"""Feedback loop — samples ~10% of interactions and nudges the behavior dials.

Port of KPKopanion's ``self_evolution/feedback.py``. The agent asks "was that
helpful?" occasionally, records the rating via the ``personality_feedback`` MCP
tool, and ``run_feedback_adjustment`` runs the dial-adjustment heuristic
(``dials.apply_feedback``) out-of-band. Wire it as a cron script (see
``config/crons/personality_feedback.py``) so it runs on a schedule rather than
in-band during a conversation.

Security invariant: the feedback store lives on the keystone floor
(``security._CREW_SECRET_LEAVES``), so the agent can neither read nor write its
own ratings. The backend opens the file directly, never through
``is_sensitive_path``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
from dataclasses import asdict, dataclass
from pathlib import Path
from time import time

from kiro_crew.atomic_write import atomic_write
from kiro_crew.personality.dials import (
    ADJUSTMENT_THRESHOLD,
    apply_feedback,
    load_dials,
    save_dials,
)

logger = logging.getLogger(__name__)

#: File name (a keystone leaf) under the crew data home.
FEEDBACK_FILE_NAME = "personality_feedback.json"

#: ~10% of interactions get a feedback prompt.
FEEDBACK_SAMPLE_RATE = 0.10

#: Minimum interactions between feedback prompts (never twice in a row).
MIN_INTERACTIONS_BETWEEN = 5

#: How many recent ratings the trend comparison uses.
TREND_WINDOW = 5


@dataclass
class FeedbackEntry:
    """A single recorded rating."""

    session_key: str
    timestamp: float
    rating: int  # 1-5
    note: str = ""


class FeedbackCollector:
    """Collects and persists user feedback ratings.

    Sampling is deterministic-friendly: the caller may inject a seeded
    ``random.Random`` for reproducible tests.
    """

    def __init__(self, config_dir: Path, rng: random.Random | None = None):
        self._path = config_dir / FEEDBACK_FILE_NAME
        self._rng = rng or random
        self._entries: list[FeedbackEntry] = []
        self._interaction_count = 0
        self._last_feedback_interaction = 0
        self._load()

    def should_ask_for_feedback(self) -> bool:
        """Return True ~10% of the time, never within ``MIN_INTERACTIONS_BETWEEN``.

        Each call counts one interaction. A feedback prompt is only offered when
        sampling fires AND enough interactions have passed since the last
        request, so the agent never asks twice in a row.
        """
        self._interaction_count += 1
        if self._interaction_count - self._last_feedback_interaction < MIN_INTERACTIONS_BETWEEN:
            return False
        return self._rng.random() < FEEDBACK_SAMPLE_RATE

    async def record_feedback(self, session_key: str, rating: int, note: str = "") -> None:
        """Append a rating and persist atomically, off the event loop.

        The rating is clamped to 1-5 so a malformed tool call cannot corrupt
        the store. The atomic write is offloaded so a slow disk never blocks
        the gateway loop.
        """
        clamped = max(1, min(5, int(rating)))
        self._entries.append(
            FeedbackEntry(
                session_key=session_key,
                timestamp=time(),
                rating=clamped,
                note=note,
            )
        )
        self._last_feedback_interaction = self._interaction_count
        await asyncio.to_thread(self._save)

    def get_recent_feedback(self, count: int = 10) -> list[FeedbackEntry]:
        """Return the ``count`` most recent entries (newest first)."""
        return list(reversed(self._entries[-count:]))

    def get_feedback_summary(self) -> dict:
        """Aggregate stats: average rating, count, and a simple trend.

        Trend compares the most recent ``TREND_WINDOW`` ratings against the
        overall average so the operator can see at a glance whether satisfaction
        is moving.
        """
        if not self._entries:
            return {"count": 0, "avg_rating": 0.0, "trend": "neutral"}
        ratings = [e.rating for e in self._entries]
        avg = sum(ratings) / len(ratings)
        recent = ratings[-TREND_WINDOW:]
        recent_avg = sum(recent) / len(recent)
        if recent_avg < avg - 0.5:
            trend = "declining"
        elif recent_avg > avg + 0.5:
            trend = "improving"
        else:
            trend = "stable"
        return {"count": len(ratings), "avg_rating": round(avg, 2), "trend": trend}

    def _save(self) -> None:
        """Persist the store atomically (owner-only mode)."""
        payload = {
            "interaction_count": self._interaction_count,
            "last_feedback_interaction": self._last_feedback_interaction,
            "entries": [asdict(e) for e in self._entries],
        }
        atomic_write(self._path, json.dumps(payload, indent=2), mode=0o600)

    def _load(self) -> None:
        """Load the store, failing safe to an empty store on any error."""
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            self._interaction_count = int(data.get("interaction_count", 0))
            self._last_feedback_interaction = int(data.get("last_feedback_interaction", 0))
            for e in data.get("entries", []):
                self._entries.append(FeedbackEntry(**e))
        except Exception as e:
            logger.warning("Failed to load feedback store %s: %s", self._path, e)


#: How many recent ratings ``run_feedback_adjustment`` considers.
RECENT_FEEDBACK_WINDOW = 10

#: Average rating below which the dials are considered worth adjusting.
ADJUSTMENT_AVG_THRESHOLD = 3.0


def run_feedback_adjustment(config_dir: Path) -> None:
    """Run the out-of-band dial-adjustment heuristic from stored feedback.

    Loads the feedback store, and when at least ``ADJUSTMENT_THRESHOLD`` recent
    ratings average below ``ADJUSTMENT_AVG_THRESHOLD``, nudges the behavior dials
    via ``dials.apply_feedback`` and persists them. A no-op when there is not
    enough (or not negative enough) feedback, so it is safe to run on a schedule.

    Intended to be wired as a cron script (see ``config/crons/personality_feedback.py``)
    so the adjustment happens out-of-band, never during a conversation.
    """
    collector = FeedbackCollector(config_dir)
    ratings = [e.rating for e in collector.get_recent_feedback(RECENT_FEEDBACK_WINDOW)]
    if len(ratings) < ADJUSTMENT_THRESHOLD:
        return
    avg = sum(ratings) / len(ratings)
    if avg >= ADJUSTMENT_AVG_THRESHOLD:
        return
    dials = load_dials(config_dir)
    adjusted = apply_feedback(dials, ratings)
    if adjusted != dials:
        save_dials(config_dir, adjusted)
        logger.info("Adjusted behavior dials from feedback (avg rating %.2f)", avg)
