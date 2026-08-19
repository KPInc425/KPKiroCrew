"""Tests for the adaptive personality module (dials + feedback loop)."""

from __future__ import annotations

import random
from pathlib import Path

import pytest

from kiro_crew.personality.dials import (
    BehaviorDials,
    adjust_dial,
    apply_feedback,
    load_dials,
    render_personality_block,
    save_dials,
)
from kiro_crew.personality.feedback import (
    FeedbackCollector,
    run_feedback_adjustment,
)


class TestBehaviorDials:
    def test_behavior_dials_defaults(self) -> None:
        dials = BehaviorDials()
        for field in (
            "warmth",
            "humor",
            "formality",
            "initiative",
            "verbosity",
            "patience",
            "curiosity",
            "conciseness",
        ):
            assert getattr(dials, field) == 3

    def test_load_dials_missing_file(self, tmp_path: Path) -> None:
        dials = load_dials(tmp_path)
        assert dials == BehaviorDials()

    def test_load_dials_corrupt_file(self, tmp_path: Path) -> None:
        (tmp_path / "personality_dials.json").write_text("{not valid json", encoding="utf-8")
        dials = load_dials(tmp_path)
        assert dials == BehaviorDials()

    def test_save_load_roundtrip(self, tmp_path: Path) -> None:
        dials = BehaviorDials(warmth=5, humor=1, initiative=4)
        save_dials(tmp_path, dials)
        loaded = load_dials(tmp_path)
        assert loaded == dials

    def test_adjust_dial_clamp(self) -> None:
        dials = BehaviorDials(warmth=1, humor=5)
        assert adjust_dial(dials, "warmth", -1).warmth == 1
        assert adjust_dial(dials, "humor", +1).humor == 5
        assert adjust_dial(dials, "warmth", +1).warmth == 2

    def test_adjust_dial_unknown_is_noop(self) -> None:
        dials = BehaviorDials()
        assert adjust_dial(dials, "bogus", -1) == dials


class TestApplyFeedback:
    def test_apply_feedback_negative(self) -> None:
        # Five strongly-negative ratings: warmth drops to the floor, initiative
        # gets a single nudge (not one per rating) because the average is < 2.
        dials = apply_feedback(BehaviorDials(), [1, 1, 1, 1, 1])
        assert dials.warmth == 1
        assert dials.initiative == 2

    def test_apply_feedback_mild_negative_does_not_touch_warmth(self) -> None:
        # A rating of 2 is mildly negative: it must not adjust warmth, and an
        # average of exactly 2 is not below 2, so initiative is not nudged.
        dials = apply_feedback(BehaviorDials(), [2, 2, 2, 2, 2])
        assert dials.warmth == 3
        assert dials.initiative == 3

    def test_apply_feedback_positive(self) -> None:
        # Positive ratings reward warmth, capped at 5.
        dials = apply_feedback(BehaviorDials(), [4, 4, 4, 4, 4])
        assert dials.warmth == 5
        assert dials.initiative == 3

    def test_apply_feedback_empty_is_noop(self) -> None:
        assert apply_feedback(BehaviorDials(), []) == BehaviorDials()


class TestRenderPersonalityBlock:
    def test_render_personality_block(self) -> None:
        block = render_personality_block(BehaviorDials())
        assert block
        assert "## Companion Personality" in block
        assert "Warmth" in block

    def test_render_personality_block_extremes(self) -> None:
        low = render_personality_block(
            BehaviorDials(
                warmth=1,
                humor=1,
                formality=1,
                initiative=1,
                verbosity=1,
                patience=1,
                curiosity=1,
                conciseness=1,
            )
        )
        high = render_personality_block(
            BehaviorDials(
                warmth=5,
                humor=5,
                formality=5,
                initiative=5,
                verbosity=5,
                patience=5,
                curiosity=5,
                conciseness=5,
            )
        )
        assert low
        assert high
        assert low != high


class TestFeedbackCollector:
    @pytest.mark.asyncio
    async def test_feedback_collector_record(self, tmp_path: Path) -> None:
        collector = FeedbackCollector(tmp_path)
        await collector.record_feedback("s1", 4, "good")
        await collector.record_feedback("s1", 2)
        recent = collector.get_recent_feedback()
        assert len(recent) == 2
        assert recent[0].rating == 2
        assert recent[1].rating == 4

    @pytest.mark.asyncio
    async def test_feedback_collector_recent(self, tmp_path: Path) -> None:
        collector = FeedbackCollector(tmp_path)
        for i in range(12):
            await collector.record_feedback("s1", (i % 5) + 1)
        recent = collector.get_recent_feedback(10)
        assert len(recent) == 10
        # Newest first: the last recorded rating is (11 % 5) + 1 == 2.
        assert recent[0].rating == 2

    @pytest.mark.asyncio
    async def test_feedback_collector_summary(self, tmp_path: Path) -> None:
        collector = FeedbackCollector(tmp_path)
        for rating in (1, 2, 3, 4, 5):
            await collector.record_feedback("s1", rating)
        summary = collector.get_feedback_summary()
        assert summary["count"] == 5
        assert summary["avg_rating"] == 3.0
        assert summary["trend"] == "stable"

    def test_should_ask_for_feedback_deterministic(self) -> None:
        def drive() -> list[bool]:
            collector = FeedbackCollector(Path("unused"), rng=random.Random(42))
            return [collector.should_ask_for_feedback() for _ in range(20)]

        assert drive() == drive()
        assert all(isinstance(v, bool) for v in drive())


class TestRunFeedbackAdjustment:
    @pytest.mark.asyncio
    async def test_run_feedback_adjustment(self, tmp_path: Path) -> None:
        collector = FeedbackCollector(tmp_path)
        for _ in range(5):
            await collector.record_feedback("s1", 1)
        run_feedback_adjustment(tmp_path)
        dials = load_dials(tmp_path)
        assert dials.warmth == 1
        assert dials.initiative == 2

    @pytest.mark.asyncio
    async def test_run_feedback_adjustment_not_enough(self, tmp_path: Path) -> None:
        collector = FeedbackCollector(tmp_path)
        for _ in range(3):
            await collector.record_feedback("s1", 1)
        run_feedback_adjustment(tmp_path)
        # Not enough ratings: dials file is never created.
        assert not (tmp_path / "personality_dials.json").exists()

    @pytest.mark.asyncio
    async def test_run_feedback_adjustment_positive_noop(self, tmp_path: Path) -> None:
        collector = FeedbackCollector(tmp_path)
        for _ in range(5):
            await collector.record_feedback("s1", 5)
        run_feedback_adjustment(tmp_path)
        assert not (tmp_path / "personality_dials.json").exists()
