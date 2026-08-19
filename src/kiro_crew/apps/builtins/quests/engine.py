"""Quests — quest generation and completion orchestration.

Ported from KPKopanion's ``adventure/engine.py`` (``AdventureEngine``) and
``adventure/lore.py`` (narrative flavor). The engine generates quests from goal
and habit records (read from semantic memory keys by the caller) and completes
them, awarding XP through the store.

Two KPKopanion defects are fixed here:

* **XP signature mismatch.** KPKopanion's ``AdventureEngine.complete_quest``
  called ``award_xp(network_id, amount, category, reason)`` while
  ``XPEngine.award_xp`` was defined ``(user_id, category, network_id)`` — the
  two were never wired consistently. This port reconciles them to the single
  signature ``award_xp(amount, category, reason)`` (no ``network_id``: Kiro
  Crew is single-user).
* **Emoji in narrative.** KPKopanion's achievement glyphs violated Kiro Crew's
  no-emoji-in-UI rule. All narrative text is passed through ``_strip_emojis``
  so no emoji can reach the UI.
"""

from __future__ import annotations

import logging
import random
import re
from typing import Any

from kiro_crew.apps.builtins.quests.models import DEFAULT_QUEST_XP
from kiro_crew.apps.builtins.quests.store import QuestStore

logger = logging.getLogger(__name__)

#: Completion celebration text, themed. No emoji (Kiro Crew rule). Mirrors
#: KPKopanion's ``adventure/lore.py`` templates.
_COMPLETION_TEMPLATES: dict[str, list[str]] = {
    "fantasy": [
        "You have triumphed! The {title} has been conquered. The realm celebrates your valor!",
        "Quest complete! {title} — vanquished with skill and determination. Well fought!",
        "The bards shall sing of your deeds! {title} is no more. You rise in glory!",
        "Victory! {title} lies defeated at your feet. The kingdom breathes easier.",
        "You have slain the dragon of {title}! Experience and honor are yours.",
        "A great challenge overcome! {title} has fallen to your unwavering spirit.",
        "The stars align — {title} is complete. Your legend grows with each passing day.",
        "Huzzah! {title} has been mastered. The guild applauds your achievement!",
        "By blade and spell, you have conquered {title}! The elders nod in approval.",
        "The ancient prophecy spoke of one who would complete {title}. That one is you!",
        "You return to the tavern a hero. {title} is but another chapter in your saga.",
        "The dungeon of {title} has been cleared. Treasure and glory await!",
    ],
    "sci-fi": [
        "Mission accomplished. {title} has been neutralized. Command acknowledges your service.",
        "The anomaly known as {title} has been resolved. Stellar records updated.",
        "System alert: {title} complete. Your efficiency rating has been upgraded.",
        "Objective {title} secured. The federation recognizes your contribution.",
        "Operation {title} — successful. Debriefing scheduled at your convenience.",
        "The {title} threat has been eliminated. Sector secure.",
        "Protocol {title} executed flawlessly. You are a credit to the fleet.",
        "Transmission received: {title} complete. Promotion recommended.",
    ],
    "custom": [
        "Task complete! {title} has been accomplished. Well done!",
        "Success! {title} is finished. Your efforts paid off.",
        "Achievement unlocked: {title}. Keep up the great work!",
        "{title} — done and dusted. On to the next challenge!",
    ],
}

#: Default narrative theme when none is configured.
_DEFAULT_THEME = "fantasy"

#: Broad emoji range matcher. Applied defensively to any narrative text so a
#: stray glyph from a ported source can never reach the UI.
_EMOJI_RE = re.compile(
    "["
    "\U0001f000-\U0001faff"
    "\U00002600-\U000027bf"
    "\U0001f1e6-\U0001f1ff"
    "\U00002b00-\U00002bff"
    "\U0000fe0f"
    "\U00002700-\U000027bf"
    "]"
)


def _strip_emojis(text: str) -> str:
    """Remove emoji glyphs from *text* (Kiro Crew no-emoji-in-UI rule)."""
    return _EMOJI_RE.sub("", text)


def _get_attr(obj: Any, key: str, default: Any = "") -> Any:
    """Read an attribute from a dict or object."""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


class QuestEngine:
    """Generates quests from goals/habits and completes them for XP."""

    def __init__(self, store: QuestStore, theme: str = _DEFAULT_THEME) -> None:
        self._store = store
        self._theme = theme if theme in _COMPLETION_TEMPLATES else _DEFAULT_THEME

    # ── Generation ─────────────────────────────────────────────────────────

    async def generate_quests(
        self,
        goals: list[Any] | None = None,
        habits: list[Any] | None = None,
    ) -> list[Any]:
        """Generate quests from active goals and habits.

        *goals* and *habits* are records read from semantic memory keys (e.g.
        ``goal.*`` / ``habit.*``) by the caller; each may be a dict or an
        object with ``id``/``title``/``description``/``status`` (and, for
        goals, ``target_count``/``unit``/``deadline``). A quest is skipped when
        one already exists for the same source. Returns the newly created
        quests.
        """
        generated: list[Any] = []
        if goals:
            generated.extend(await self._generate_from_goals(goals))
        if habits:
            generated.extend(await self._generate_from_habits(habits))
        return generated

    async def _generate_from_goals(self, goals: list[Any]) -> list[Any]:
        generated: list[Any] = []
        active = [
            g
            for g in goals
            if (isinstance(g, dict) and g.get("status") == "active")
            or (not isinstance(g, dict) and getattr(g, "status", "") == "active")
        ]
        for goal in active:
            goal_title = _get_attr(goal, "title")
            goal_description = _get_attr(goal, "description")
            goal_target_count = int(_get_attr(goal, "target_count", 1) or 1)
            goal_unit = _get_attr(goal, "unit", "times")

            title = f"Complete: {goal_title}"
            if await self._has_quest_for(title):
                continue
            description = goal_description or f"Work toward your goal: {goal_title}"
            objectives = [
                f"Make progress on '{goal_title}' — target: {goal_target_count} {goal_unit}"
            ]
            xp_reward = max(DEFAULT_QUEST_XP, goal_target_count * 10)

            quest = await self._store.create_quest(
                name=title,
                description=description,
                objectives=objectives,
                xp_reward=xp_reward,
            )
            generated.append(quest)
        return generated

    async def _generate_from_habits(self, habits: list[Any]) -> list[Any]:
        generated: list[Any] = []
        active = [
            h
            for h in habits
            if (isinstance(h, dict) and h.get("status") == "active")
            or (not isinstance(h, dict) and getattr(h, "status", "") == "active")
        ]
        for habit in active:
            habit_name = _get_attr(habit, "name")
            habit_description = _get_attr(habit, "description")

            title = f"Maintain: {habit_name}"
            if await self._has_quest_for(title):
                continue
            description = habit_description or f"Keep up the habit: {habit_name}"
            objectives = [f"Practice '{habit_name}' — maintain your streak"]

            quest = await self._store.create_quest(
                name=title,
                description=description,
                objectives=objectives,
                xp_reward=15,
            )
            generated.append(quest)
        return generated

    async def _has_quest_for(self, title: str) -> bool:
        """True when an active quest already exists for the given source.

        The store does not track ``source_type``/``source_id`` (Kiro Crew's
        ``Quest`` model omits them), so dedup is an exact match on the generated
        title. Matching the full title (not a shared prefix) lets one quest per
        goal/habit coexist: a prefix match would skip every later goal once the
        first ``Complete: ...`` quest existed.
        """
        if not title:
            return False
        active = await self._store.get_active_quests()
        return any(q.name == title for q in active)

    # ── Completion ───────────────────────────────────────────────────────

    async def complete_quest(self, quest_id: str) -> dict:
        """Complete a quest, award its XP, and return the result.

        Returns a dict with ``quest``, ``xp_awarded``, ``flavor_text``, and
        level info. A quest that is already complete awards no XP a second
        time.
        """
        quest, newly_completed = await self._store.complete_quest(quest_id)
        if quest is None:
            return {"error": "quest_not_found", "message": "Quest not found."}

        if not newly_completed:
            return {
                "quest": quest,
                "xp_awarded": 0,
                "flavor_text": self._generate_flavor_text(quest.name),
                "level_up": False,
                "new_level": 0,
                "new_title": "",
                "message": "Quest was already completed.",
            }

        xp_awarded = quest.xp_reward
        event = await self._store.award_xp(
            xp_awarded, "quest_completed", f"Completed quest: {quest.name}"
        )
        flavor_text = self._generate_flavor_text(quest.name)

        logger.info(
            "Completed quest: %s — %s (+%d XP)",
            quest.id,
            quest.name,
            xp_awarded,
        )
        return {
            "quest": quest,
            "xp_awarded": xp_awarded,
            "flavor_text": flavor_text,
            "level_up": event["leveled_up"],
            "new_level": event["level"],
            "new_title": event["level_title"],
        }

    # ── Flavor text ───────────────────────────────────────────────────────

    def _generate_flavor_text(self, title: str) -> str:
        """Generate narrative celebration text for a completed quest."""
        templates = _COMPLETION_TEMPLATES.get(self._theme, _COMPLETION_TEMPLATES[_DEFAULT_THEME])
        template = random.choice(templates)
        return _strip_emojis(template.format(title=title))
