"""Quests — backend routes (browser-facing, same-origin authed).

Registered at gateway startup via the manifest's ``backend.routes`` field, the
same pattern as mochi and issue-radar. Handlers reach the store/engine through
a lazy singleton rooted at the app data dir.

Endpoints:

  GET  /api/apps/quests/quests                          -> {"quests": [...]}
  GET  /api/apps/quests/quests/completed                -> {"quests": [...]}
  POST /api/apps/quests/quests/{id}/complete             -> completion result
  POST /api/apps/quests/quests/{id}/objectives/{obj}/complete -> {"quest": ...}
  GET  /api/apps/quests/xp                              -> {"total_xp", "level", "level_title"}
  GET  /api/apps/quests/achievements                     -> {"achievements": [...]}

Deny-by-default: every route 403s while the app is disabled (routes are
registered once at gateway startup, so a default-disabled app would otherwise
stay callable). There is no long-lived runtime to 503 on — the store is
stateless on disk — so the guard only checks the enabled flag.
"""

from __future__ import annotations

import asyncio
import logging
from functools import wraps
from typing import Any, Awaitable, Callable

from aiohttp import web

from kiro_crew.apps.builtins.quests.engine import QuestEngine
from kiro_crew.apps.builtins.quests.store import QuestStore
from kiro_crew.apps.manager import is_app_enabled

logger = logging.getLogger(__name__)

APP_NAME = "quests"
_BASE = f"/api/apps/{APP_NAME}"

Handler = Callable[[web.Request], Awaitable[web.Response]]

_store_instance: QuestStore | None = None
_engine_instance: QuestEngine | None = None


def _store() -> QuestStore:
    """Lazy singleton store rooted at the app data dir.

    The import is function-local to avoid pulling the app-manager graph into
    the package import (the package ``__init__`` eagerly imports this module to
    expose ``register_routes``).
    """
    global _store_instance
    if _store_instance is None:
        from kiro_crew.apps.manager import app_data_dir

        _store_instance = QuestStore(app_data_dir(APP_NAME))
    return _store_instance


def _engine() -> QuestEngine:
    global _engine_instance
    if _engine_instance is None:
        _engine_instance = QuestEngine(_store())
    return _engine_instance


def _require_enabled(handler: Handler) -> Handler:
    """403 while disabled. ``is_app_enabled`` is a sync installed.json read —
    run it off the event loop (same as mochi's guard)."""

    @wraps(handler)
    async def _wrapped(request: web.Request) -> web.Response:
        if not await asyncio.to_thread(is_app_enabled, APP_NAME):
            return web.json_response(
                {"error": "quests is disabled", "code": "app_disabled"}, status=403
            )
        return await handler(request)

    return _wrapped


def _quest_to_dict(quest: Any) -> dict:
    return {
        "id": quest.id,
        "name": quest.name,
        "description": quest.description,
        "objectives": [
            {
                "id": o.id,
                "description": o.description,
                "completed": o.completed,
                "completed_at": o.completed_at,
            }
            for o in quest.objectives
        ],
        "xp_reward": quest.xp_reward,
        "status": quest.status,
        "created_at": quest.created_at,
        "updated_at": quest.updated_at,
        "completed_at": quest.completed_at,
    }


async def _handle_quests_get(request: web.Request) -> web.Response:
    quests = await _store().get_active_quests()
    return web.json_response({"quests": [_quest_to_dict(q) for q in quests]})


async def _handle_quests_completed_get(request: web.Request) -> web.Response:
    quests = await _store().get_completed_quests()
    return web.json_response({"quests": [_quest_to_dict(q) for q in quests]})


async def _handle_quest_complete(request: web.Request) -> web.Response:
    quest_id = request.match_info["id"]
    result = await _engine().complete_quest(quest_id)
    if "error" in result:
        return web.json_response(
            {"error": result["error"], "message": result["message"]}, status=404
        )
    payload = {
        "quest": _quest_to_dict(result["quest"]),
        "xp_awarded": result["xp_awarded"],
        "flavor_text": result["flavor_text"],
        "level_up": result["level_up"],
        "new_level": result["new_level"],
        "new_title": result["new_title"],
    }
    if "message" in result:
        payload["message"] = result["message"]
    return web.json_response(payload)


async def _handle_objective_complete(request: web.Request) -> web.Response:
    quest_id = request.match_info["id"]
    objective_id = request.match_info["obj_id"]
    quest = await _store().complete_objective(quest_id, objective_id)
    if quest is None:
        return web.json_response(
            {"error": "quest_not_found", "message": "Quest not found."}, status=404
        )
    return web.json_response({"quest": _quest_to_dict(quest)})


async def _handle_xp_get(request: web.Request) -> web.Response:
    xp = await _store().get_xp_total()
    return web.json_response(
        {
            "total_xp": xp.total_xp,
            "level": xp.level,
            "level_title": xp.level_title,
        }
    )


async def _handle_achievements_get(request: web.Request) -> web.Response:
    achievements = await _store().get_achievements()
    return web.json_response(
        {
            "achievements": [
                {
                    "id": a.id,
                    "name": a.name,
                    "description": a.description,
                    "unlocked_at": a.unlocked_at,
                    "category": a.category,
                }
                for a in achievements
            ]
        }
    )


def register_routes(app: web.Application) -> None:
    """Register on the gateway's aiohttp Application (single-arg convention,
    same as every builtin — see mochi/backend/routes.py)."""
    app.router.add_get(f"{_BASE}/quests", _require_enabled(_handle_quests_get))
    app.router.add_get(f"{_BASE}/quests/completed", _require_enabled(_handle_quests_completed_get))
    app.router.add_post(f"{_BASE}/quests/{{id}}/complete", _require_enabled(_handle_quest_complete))
    app.router.add_post(
        f"{_BASE}/quests/{{id}}/objectives/{{obj_id}}/complete",
        _require_enabled(_handle_objective_complete),
    )
    app.router.add_get(f"{_BASE}/xp", _require_enabled(_handle_xp_get))
    app.router.add_get(f"{_BASE}/achievements", _require_enabled(_handle_achievements_get))
