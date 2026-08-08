"""Quests — persistence store for quests, XP, and achievements.

Ported from KPKopanion's ``adventure/engine.py`` (quest CRUD + lifecycle) and
``gamification/xp_engine.py`` (XP/level). The KPKopanion engines were
synchronous and wrote one JSON file per quest/state; this store is async and
keeps each collection in a single JSON file under the app data dir, written
atomically (``atomic_write``) so a concurrent reader never observes a torn
file.

Single-user: KPKopanion keyed everything by ``network_id``/``user_id``; Kiro
Crew is one local user, so the store holds one quest list, one XP total, and
one achievement list.

Concurrency: in-memory mutations are serialized by a ``threading.Lock`` (not an
``asyncio.Lock``) because the store is used from two contexts — the gateway's
async route handlers and the app's stdio MCP server, which runs tool calls in
worker threads that each spin up their own event loop. A ``threading.Lock``
works across both. The atomic file write happens after the lock is released, so
a slow write never blocks the event loop; ``atomic_write`` makes concurrent
writes safe (last writer wins, no torn file).
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from kiro_crew.apps.builtins.quests.models import (
    DEFAULT_QUEST_XP,
    LEVEL_TITLES,
    QUEST_STATUS_ACTIVE,
    QUEST_STATUS_COMPLETED,
    QUEST_STATUS_FAILED,
    XP_REWARDS,
    Achievement,
    Quest,
    QuestObjective,
    XPTotal,
)
from kiro_crew.atomic_write import atomic_write

logger = logging.getLogger(__name__)

QUESTS_FILE = "quests.json"
XP_FILE = "xp.json"
ACHIEVEMENTS_FILE = "achievements.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _calculate_level(total_xp: int) -> tuple[int, str]:
    """Return ``(level, title)`` for a total XP amount.

    Level is the index of the highest threshold the total reaches; title is the
    matching label. Mirrors KPKopanion's ``_calculate_level``.
    """
    level = 0
    title = LEVEL_TITLES[0][1]
    for idx, (threshold, t) in enumerate(LEVEL_TITLES):
        if total_xp >= threshold:
            level = idx
            title = t
        else:
            break
    return level, title


class QuestStore:
    """Async store for quests, XP, and achievements under the app data dir."""

    def __init__(self, data_dir: Path) -> None:
        self._data_dir = data_dir
        self._quests: dict[str, Quest] = {}
        self._xp = XPTotal()
        self._achievements: dict[str, Achievement] = {}
        self._lock = threading.Lock()
        self._load()

    # ── Paths ──────────────────────────────────────────────────────────────

    def _path(self, filename: str) -> Path:
        return self._data_dir / filename

    # ── Load / persist ────────────────────────────────────────────────────

    def _load(self) -> None:
        """Load all collections from disk. Corruption or absence yields empty
        state — the store never throws on a bad file."""
        self._quests = self._read_quests()
        self._xp = self._read_xp()
        self._achievements = self._read_achievements()

    def _read_quests(self) -> dict[str, Quest]:
        data = self._read_json(QUESTS_FILE, {})
        quests: dict[str, Quest] = {}
        for quest_id, quest_data in data.items():
            if isinstance(quest_data, dict):
                try:
                    quests[quest_id] = Quest.from_dict(dict(quest_data))
                except (TypeError, ValueError):
                    logger.warning("Skipping malformed quest %s", quest_id)
        return quests

    def _read_achievements(self) -> dict[str, Achievement]:
        data = self._read_json(ACHIEVEMENTS_FILE, {})
        achievements: dict[str, Achievement] = {}
        for ach_id, ach_data in data.items():
            if isinstance(ach_data, dict):
                try:
                    achievements[ach_id] = Achievement.from_dict(dict(ach_data))
                except (TypeError, ValueError):
                    logger.warning("Skipping malformed achievement %s", ach_id)
        return achievements

    def _read_json(self, filename: str, default: Any) -> Any:
        try:
            data = json.loads(self._path(filename).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return default
        if not isinstance(data, dict):
            return default
        return data

    def _read_xp(self) -> XPTotal:
        data = self._read_json(XP_FILE, {})
        total = int(data.get("total_xp", 0) or 0)
        level, title = _calculate_level(total)
        return XPTotal(total_xp=total, level=level, level_title=title)

    def _persist(self) -> None:
        """Write all collections atomically. Runs off the event loop."""
        self._data_dir.mkdir(parents=True, exist_ok=True)
        atomic_write(
            self._path(QUESTS_FILE),
            json.dumps(
                {qid: q.to_dict() for qid, q in self._quests.items()},
                indent=2,
                ensure_ascii=False,
            )
            + "\n",
            mode=0o600,
        )
        atomic_write(
            self._path(XP_FILE),
            json.dumps(self._xp.to_dict(), indent=2, ensure_ascii=False) + "\n",
            mode=0o600,
        )
        atomic_write(
            self._path(ACHIEVEMENTS_FILE),
            json.dumps(
                {aid: a.to_dict() for aid, a in self._achievements.items()},
                indent=2,
                ensure_ascii=False,
            )
            + "\n",
            mode=0o600,
        )

    async def _mutate(self, mutate_fn: Callable[[], Any]) -> Any:
        """Apply an in-memory mutation under the lock, then persist atomically."""
        with self._lock:
            result = mutate_fn()
        await asyncio.to_thread(self._persist)
        return result

    # ── Quest CRUD ────────────────────────────────────────────────────────

    async def create_quest(
        self,
        name: str,
        description: str = "",
        objectives: list[str] | None = None,
        xp_reward: int = DEFAULT_QUEST_XP,
    ) -> Quest:
        """Create a new quest and persist it."""
        now = _now_iso()
        quest = Quest(
            id=str(uuid.uuid4()),
            name=name,
            description=description,
            objectives=[
                QuestObjective(id=str(uuid.uuid4()), description=o) for o in (objectives or [])
            ],
            xp_reward=xp_reward,
            status=QUEST_STATUS_ACTIVE,
            created_at=now,
            updated_at=now,
        )

        def _apply() -> Quest:
            self._quests[quest.id] = quest
            return quest

        await self._mutate(_apply)
        logger.info("Created quest: %s — %s", quest.id, quest.name)
        return quest

    async def complete_objective(self, quest_id: str, objective_id: str) -> Quest | None:
        """Mark a single objective complete. Returns the quest, or None if the
        quest or objective does not exist."""

        def _apply() -> Quest | None:
            quest = self._quests.get(quest_id)
            if quest is None:
                return None
            now = _now_iso()
            for obj in quest.objectives:
                if obj.id == objective_id and not obj.completed:
                    obj.completed = True
                    obj.completed_at = now
            quest.updated_at = now
            return quest

        return await self._mutate(_apply)

    async def complete_quest(self, quest_id: str) -> tuple[Quest | None, bool]:
        """Mark a quest complete (and all its objectives).

        Returns ``(quest, newly_completed)`` — ``newly_completed`` is False when
        the quest was already complete, so the caller can avoid double-awarding
        XP. XP is awarded separately by the engine.
        """

        def _apply() -> tuple[Quest | None, bool]:
            quest = self._quests.get(quest_id)
            if quest is None:
                return None, False
            if quest.status == QUEST_STATUS_COMPLETED:
                return quest, False
            now = _now_iso()
            for obj in quest.objectives:
                if not obj.completed:
                    obj.completed = True
                    obj.completed_at = now
            quest.status = QUEST_STATUS_COMPLETED
            quest.completed_at = now
            quest.updated_at = now
            return quest, True

        return await self._mutate(_apply)

    async def get_active_quests(self) -> list[Quest]:
        """Return quests that are not completed or failed, newest first."""
        with self._lock:
            quests = [
                q
                for q in self._quests.values()
                if q.status not in (QUEST_STATUS_COMPLETED, QUEST_STATUS_FAILED)
            ]
        return sorted(quests, key=lambda q: q.created_at, reverse=True)

    async def get_completed_quests(self) -> list[Quest]:
        """Return completed quests, most recently completed first."""
        with self._lock:
            quests = [q for q in self._quests.values() if q.status == QUEST_STATUS_COMPLETED]
        return sorted(quests, key=lambda q: q.completed_at, reverse=True)

    # ── XP / level ────────────────────────────────────────────────────────

    async def award_xp(self, amount: int, category: str, reason: str) -> dict:
        """Award XP for an action and return the XP event.

        ``amount`` is the raw XP; ``category`` selects the reward table entry
        when ``amount`` is not positive. Mirrors KPKopanion's ``award_xp`` but
        with the reconciled signature ``(amount, category, reason)`` — no
        ``network_id``, single user.
        """
        xp_amount = amount if amount > 0 else XP_REWARDS.get(category, 1)
        old_level = self._xp.level

        def _apply() -> dict:
            self._xp.total_xp += xp_amount
            self._xp.level, self._xp.level_title = _calculate_level(self._xp.total_xp)
            return {
                "category": category,
                "reason": reason,
                "xp_awarded": xp_amount,
                "total_xp": self._xp.total_xp,
                "level": self._xp.level,
                "level_title": self._xp.level_title,
                "leveled_up": self._xp.level > old_level,
            }

        event = await self._mutate(_apply)
        logger.info(
            "XP awarded: +%d (%s) — level %d %s",
            xp_amount,
            category,
            self._xp.level,
            self._xp.level_title,
        )
        return event

    async def get_xp_total(self) -> XPTotal:
        """Return the current XP total and derived level."""
        with self._lock:
            return XPTotal(
                total_xp=self._xp.total_xp,
                level=self._xp.level,
                level_title=self._xp.level_title,
            )

    async def get_level(self) -> tuple[int, str]:
        """Return the current ``(level, title)``."""
        with self._lock:
            return self._xp.level, self._xp.level_title

    # ── Achievements ──────────────────────────────────────────────────────

    async def unlock_achievement(
        self, achievement_id: str, name: str, description: str, category: str
    ) -> Achievement:
        """Unlock an achievement (idempotent) and persist it."""
        now = _now_iso()

        def _apply() -> Achievement:
            existing = self._achievements.get(achievement_id)
            if existing is not None:
                return existing
            achievement = Achievement(
                id=achievement_id,
                name=name,
                description=description,
                unlocked_at=now,
                category=category,
            )
            self._achievements[achievement_id] = achievement
            return achievement

        return await self._mutate(_apply)

    async def get_achievements(self) -> list[Achievement]:
        """Return all unlocked achievements, most recently unlocked first."""
        with self._lock:
            achievements = list(self._achievements.values())
        return sorted(achievements, key=lambda a: a.unlocked_at, reverse=True)
