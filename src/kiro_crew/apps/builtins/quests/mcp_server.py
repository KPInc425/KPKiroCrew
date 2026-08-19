"""Quests — the app's own MCP server, over stdio.

The tools its agents call to read and mutate quest/XP/achievement state. Like
mochi's MCP server, this is a stdio server that talks to the same JSON files
the gateway's route handlers own (the store), so a write here is visible to the
dashboard and vice versa. Writes are atomic (``atomic_write``), so a concurrent
reader never observes a torn file.

Identity: mutating tools (``quest_complete``, ``quest_objective_complete``,
``quest_create``) resolve the caller with ``_resolve_session_key_strict`` and
fail CLOSED when no verified identity exists — a subagent under the parent's
process tree must not be able to PID-walk into the parent's identity and mutate
quest state on the parent's behalf. Read-only tools (``quest_list``,
``xp_status``, ``achievements_list``) do not need an identity.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any

from kiro_crew.apps.builtins.quests.engine import QuestEngine
from kiro_crew.apps.builtins.quests.store import QuestStore
from kiro_crew.mcp_shared import call_tool_with_logging, run_mcp_stdio_loop
from kiro_crew.validation import ValidationError, validate_mcp_tool_arguments

logger = logging.getLogger(__name__)

SERVER_NAME = "quests"
SERVER_VERSION = "1.0.0"


def _data_dir() -> Path:
    """The app data dir — the same one the gateway's route handlers use."""
    from kiro_crew.apps.manager import app_data_dir

    return app_data_dir(SERVER_NAME)


def _store() -> QuestStore:
    return QuestStore(_data_dir())


def _engine() -> QuestEngine:
    return QuestEngine(_store())


def _run(coro: Any) -> Any:
    """Run an async store/engine call to completion.

    Tool calls run in a worker thread with no running event loop, so a fresh
    loop per call is safe.
    """
    return asyncio.run(coro)


def _ok(payload: Any) -> str:
    return json.dumps(payload, indent=2)


def _err(tool: str, exc: Exception) -> str:
    """Tool errors are returned as text, matching the original's shape.

    The "Error:" prefix is load-bearing: ``call_tool_with_logging`` derives the
    SEL outcome from it, so without it a failed call would be audited as
    "completed".
    """
    logger.warning("[quests-mcp] %s failed: %s", tool, exc)
    return f"Error: {tool} failed: {exc}"


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


# ── Tool handlers ─────────────────────────────────────────────────────────


def _tool_quest_list(args: dict[str, Any]) -> str:
    quests = _run(_store().get_active_quests())
    return _ok({"quests": [_quest_to_dict(q) for q in quests]})


def _tool_quest_complete(args: dict[str, Any]) -> str:
    _require_session("quest_complete")
    result = _run(_engine().complete_quest(str(args["quest_id"])))
    if "error" in result:
        return f"Error: {result['message']}"
    return _ok(
        {
            "quest": _quest_to_dict(result["quest"]),
            "xp_awarded": result["xp_awarded"],
            "flavor_text": result["flavor_text"],
            "level_up": result["level_up"],
            "new_level": result["new_level"],
            "new_title": result["new_title"],
        }
    )


def _tool_quest_objective_complete(args: dict[str, Any]) -> str:
    _require_session("quest_objective_complete")
    quest = _run(_store().complete_objective(str(args["quest_id"]), str(args["objective_id"])))
    if quest is None:
        return "Error: quest or objective not found"
    return _ok({"quest": _quest_to_dict(quest)})


def _tool_xp_status(args: dict[str, Any]) -> str:
    xp = _run(_store().get_xp_total())
    return _ok(
        {
            "total_xp": xp.total_xp,
            "level": xp.level,
            "level_title": xp.level_title,
        }
    )


def _tool_achievements_list(args: dict[str, Any]) -> str:
    achievements = _run(_store().get_achievements())
    return _ok(
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


def _tool_quest_create(args: dict[str, Any]) -> str:
    _require_session("quest_create")
    quest = _run(
        _store().create_quest(
            name=str(args["name"]),
            description=str(args.get("description", "")),
            objectives=[str(o) for o in args.get("objectives", [])],
            xp_reward=int(args.get("xp_reward", 25)),
        )
    )
    return _ok({"quest": _quest_to_dict(quest)})


def _require_session(tool: str) -> None:
    """Fail closed when no verified session identity is available.

    The ``mcp_core`` import is deferred to call time: ``mcp_core`` pulls in a
    large module graph (artifacts, hooks, validation) that has a pre-existing
    circular import when loaded first, and this server is a short-lived stdio
    process that should not pay that graph on startup. The same deferral the
    mochi server uses for ``app_data_dir``.
    """
    from kiro_crew.mcp_core import _resolve_session_key_strict

    if not _resolve_session_key_strict():
        raise RuntimeError(f"{tool} requires a verified session identity")


# ── Tool schemas ───────────────────────────────────────────────────────────


def _list_tools() -> list[dict[str, Any]]:
    return [
        {
            "name": "quest_list",
            "description": "List the player's active (not completed or failed) quests.",
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "quest_complete",
            "description": "Complete a quest by id, awarding its XP reward.",
            "inputSchema": {
                "type": "object",
                "properties": {"quest_id": {"type": "string"}},
                "required": ["quest_id"],
            },
        },
        {
            "name": "quest_objective_complete",
            "description": "Mark a single objective of a quest complete.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "quest_id": {"type": "string"},
                    "objective_id": {"type": "string"},
                },
                "required": ["quest_id", "objective_id"],
            },
        },
        {
            "name": "xp_status",
            "description": "Return the player's total XP, level, and level title.",
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "achievements_list",
            "description": "List the player's unlocked achievements.",
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "quest_create",
            "description": "Create a new quest with objectives and an XP reward.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "description": {"type": "string"},
                    "objectives": {"type": "array", "items": {"type": "string"}},
                    "xp_reward": {"type": "integer"},
                },
                "required": ["name"],
            },
        },
    ]


_DISPATCH: dict[str, Any] = {
    "quest_list": _tool_quest_list,
    "quest_complete": _tool_quest_complete,
    "quest_objective_complete": _tool_quest_objective_complete,
    "xp_status": _tool_xp_status,
    "achievements_list": _tool_achievements_list,
    "quest_create": _tool_quest_create,
}


def _validate_args(name: str, raw_args: dict[str, Any]) -> dict[str, Any]:
    """Check arguments against the declared inputSchema before dispatch.

    Fail-closed: unknown keys and undeclared schemas reject (see
    ``validate_mcp_tool_arguments``), so every accepted argument is one the
    schema names.
    """
    args = raw_args or {}
    try:
        validate_mcp_tool_arguments(args, _INPUT_SCHEMAS.get(name))
    except ValidationError as exc:
        raise ValidationError(f"{name} failed", str(exc)) from exc
    return args


def _call_tool_inner(name: str, args: dict[str, Any]) -> str:
    fn = _DISPATCH.get(name)
    if fn is None:
        return f"Error: Unknown tool: {name}"
    try:
        return fn(args or {})
    except Exception as exc:  # noqa: BLE001 — a tool error must not kill the server
        return _err(name, exc)


def _call_tool(name: str, args: dict[str, Any]) -> str:
    """Dispatch one tool call, audited.

    Routed through the shared helper so every outcome lands in the SEL audit
    log. The session key is the app's own namespace (the strict resolver is
    applied per-mutating-tool inside the handlers).
    """
    return call_tool_with_logging(
        name,
        args,
        _validate_args,
        _call_tool_inner,
        session_key=f"mcp_{SERVER_NAME}",
        downstream_service=f"kirocrew-{SERVER_NAME}",
    )


#: name -> declared inputSchema, built once from the same list ``tools/list``
#: advertises so the schema enforced is byte-identical to the schema published.
_INPUT_SCHEMAS: dict[str, Any] = {t["name"]: t.get("inputSchema") for t in _list_tools()}


def run_mcp_server() -> None:
    """Run the stdio MCP server. Entry point for ``kirocrew app mcp quests``."""
    # Keep logging off stdout — stdout is the JSON-RPC channel.
    logging.basicConfig(level=os.environ.get("KIROCREW_LOG_LEVEL", "WARNING"), stream=None)
    run_mcp_stdio_loop(SERVER_NAME, SERVER_VERSION, _list_tools, _call_tool)
