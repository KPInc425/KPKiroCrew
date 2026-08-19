"""Quests — data model for the gamification builtin app.

Ported from KPKopanion's ``adventure/engine.py`` and
``gamification/xp_engine.py``. The KPKopanion ``Quest``/``QuestObjective``
dataclasses lived in ``engine.py`` (there was no ``adventure/models.py``), so
this module is built from those definitions. The ``network_id`` dimension is
dropped: Kiro Crew is a single-user agent, so XP and quests are keyed to the
one local user.

Emoji are deliberately absent. KPKopanion's achievement definitions carried
emoji glyphs (trophy, target, flame, ...) that violate Kiro Crew's
no-emoji-in-UI rule; they are stripped here and the ``Achievement`` model has
no emoji field.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

#: XP awarded per activity category. ``quest_completed`` is the default reward
#: for finishing a quest; the rest mirror KPKopanion's ``XP_REWARDS``.
XP_REWARDS: dict[str, int] = {
    "goal_completed": 50,
    "journal_entry": 10,
    "daily_login": 5,
    "streak_day": 20,
    "reminder_completed": 15,
    "habit_logged": 5,
    "helping_other": 30,
    "skill_practice": 25,
    "conversation": 1,
    "quest_completed": 25,
}

#: Level thresholds and their titles, ascending. A player's level is the index
#: of the highest threshold their total XP reaches; the title is the matching
#: label. Mirrors KPKopanion's ``LEVEL_TITLES``.
LEVEL_TITLES: list[tuple[int, str]] = [
    (0, "Newcomer"),
    (100, "Learner"),
    (250, "Contributor"),
    (500, "Achiever"),
    (1000, "Expert"),
    (2000, "Master"),
    (3500, "Legend"),
    (5000, "Champion"),
]

#: Default XP reward for a manually created quest with no explicit reward.
DEFAULT_QUEST_XP = 25

#: Quest lifecycle states.
QUEST_STATUS_AVAILABLE = "available"
QUEST_STATUS_ACTIVE = "active"
QUEST_STATUS_COMPLETED = "completed"
QUEST_STATUS_FAILED = "failed"


@dataclass
class QuestObjective:
    """A single objective within a quest."""

    id: str
    description: str
    completed: bool = False
    completed_at: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "description": self.description,
            "completed": self.completed,
            "completed_at": self.completed_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> QuestObjective:
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class Quest:
    """A quest — a themed task with objectives and an XP reward."""

    id: str
    name: str
    description: str = ""
    objectives: list[QuestObjective] = field(default_factory=list)
    xp_reward: int = DEFAULT_QUEST_XP
    status: str = QUEST_STATUS_AVAILABLE
    created_at: str = ""
    updated_at: str = ""
    completed_at: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "objectives": [o.to_dict() for o in self.objectives],
            "xp_reward": self.xp_reward,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "completed_at": self.completed_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Quest:
        objectives_data = data.get("objectives", [])
        quest = cls(
            **{k: v for k, v in data.items() if k in cls.__dataclass_fields__ and k != "objectives"}
        )
        quest.objectives = [QuestObjective.from_dict(o) for o in objectives_data]
        return quest


@dataclass
class XPTotal:
    """The player's running XP total and derived level."""

    total_xp: int = 0
    level: int = 0
    level_title: str = LEVEL_TITLES[0][1]

    def to_dict(self) -> dict:
        return {
            "total_xp": self.total_xp,
            "level": self.level,
            "level_title": self.level_title,
        }

    @classmethod
    def from_dict(cls, data: dict) -> XPTotal:
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class Achievement:
    """A milestone the player has unlocked."""

    id: str
    name: str
    description: str = ""
    unlocked_at: str = ""
    category: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "unlocked_at": self.unlocked_at,
            "category": self.category,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Achievement:
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
