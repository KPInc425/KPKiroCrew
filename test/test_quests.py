"""Tests for the quests gamification builtin app."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from kiro_crew.apps.builtins.quests.engine import QuestEngine
from kiro_crew.apps.builtins.quests.models import Quest, QuestObjective
from kiro_crew.apps.builtins.quests.store import QuestStore, _calculate_level


class TestQuestModel:
    def test_quest_model_roundtrip(self) -> None:
        quest = Quest(
            id="q1",
            name="Test",
            description="desc",
            objectives=[QuestObjective(id="o1", description="do it")],
            xp_reward=50,
            status="active",
        )
        restored = Quest.from_dict(quest.to_dict())
        assert restored == quest

    def test_quest_objective_roundtrip(self) -> None:
        obj = QuestObjective(id="o1", description="do it", completed=True, completed_at="now")
        assert QuestObjective.from_dict(obj.to_dict()) == obj


class TestQuestStore:
    @pytest.mark.asyncio
    async def test_quest_store_create_get(self, tmp_path: Path) -> None:
        store = QuestStore(tmp_path)
        quest = await store.create_quest("Test", objectives=["do it"])
        active = await store.get_active_quests()
        assert any(q.id == quest.id for q in active)

    @pytest.mark.asyncio
    async def test_quest_objective_complete(self, tmp_path: Path) -> None:
        store = QuestStore(tmp_path)
        quest = await store.create_quest("Test", objectives=["do it"])
        obj = quest.objectives[0]
        updated = await store.complete_objective(quest.id, obj.id)
        assert updated is not None
        assert updated.objectives[0].completed is True

    @pytest.mark.asyncio
    async def test_quest_complete_awards_xp(self, tmp_path: Path) -> None:
        store = QuestStore(tmp_path)
        engine = QuestEngine(store)
        quest = await store.create_quest("Test", xp_reward=25)
        result = await engine.complete_quest(quest.id)
        assert result["xp_awarded"] == 25
        xp = await store.get_xp_total()
        assert xp.total_xp == 25

    @pytest.mark.asyncio
    async def test_quest_complete_no_double_xp(self, tmp_path: Path) -> None:
        store = QuestStore(tmp_path)
        engine = QuestEngine(store)
        quest = await store.create_quest("Test", xp_reward=25)
        await engine.complete_quest(quest.id)
        result = await engine.complete_quest(quest.id)
        assert result["xp_awarded"] == 0
        xp = await store.get_xp_total()
        assert xp.total_xp == 25

    @pytest.mark.asyncio
    async def test_quest_store_atomic_write(self, tmp_path: Path) -> None:
        store = QuestStore(tmp_path)
        await store.create_quest("Test")
        quests_file = tmp_path / "quests.json"
        assert quests_file.exists()
        data = json.loads(quests_file.read_text(encoding="utf-8"))
        assert any(q["name"] == "Test" for q in data.values())
        # Atomic write leaves no temp files behind.
        assert not list(tmp_path.glob("*.tmp"))

    @pytest.mark.asyncio
    async def test_achievement_unlock(self, tmp_path: Path) -> None:
        store = QuestStore(tmp_path)
        ach = await store.unlock_achievement("a1", "First", "desc", "general")
        assert ach.id == "a1"
        assert len(await store.get_achievements()) == 1
        # Idempotent: unlocking the same achievement again does not duplicate it.
        await store.unlock_achievement("a1", "First", "desc", "general")
        assert len(await store.get_achievements()) == 1


class TestXpLevels:
    def test_xp_level_calculation(self) -> None:
        assert _calculate_level(0) == (0, "Newcomer")
        assert _calculate_level(100) == (1, "Learner")
        assert _calculate_level(250) == (2, "Contributor")
        assert _calculate_level(5000) == (7, "Champion")


class TestQuestEngine:
    @pytest.mark.asyncio
    async def test_quest_engine_generate(self, tmp_path: Path) -> None:
        store = QuestStore(tmp_path)
        engine = QuestEngine(store)
        goals = [
            {
                "id": "g1",
                "title": "Learn Python",
                "description": "Study",
                "status": "active",
                "target_count": 3,
                "unit": "hours",
            }
        ]
        quests = await engine.generate_quests(goals=goals)
        assert len(quests) == 1
        assert quests[0].name == "Complete: Learn Python"

    @pytest.mark.asyncio
    async def test_quest_engine_dedup(self, tmp_path: Path) -> None:
        store = QuestStore(tmp_path)
        engine = QuestEngine(store)
        goals = [{"id": "g1", "title": "Learn Python", "status": "active"}]
        first = await engine.generate_quests(goals=goals)
        assert len(first) == 1
        second = await engine.generate_quests(goals=goals)
        assert second == []
