"""Tests for the people relationship graph (semantic memory ``people.*`` keys)."""

from __future__ import annotations

from pathlib import Path

from kiro_crew.validation import (
    PEOPLE_ADD_FACT_SCHEMA,
    PEOPLE_LIST_SCHEMA,
    PEOPLE_LOOKUP_SCHEMA,
)
from kiro_crew.vector_memory import _BUILTIN_PREFIXES, SemanticRejectCode, VectorMemoryStore


class TestPeoplePrefix:
    def test_people_prefix_in_builtin_prefixes(self) -> None:
        assert "people.*" in _BUILTIN_PREFIXES


class TestPeopleValidation:
    def test_validate_semantic_people_key(self, tmp_path: Path) -> None:
        store = VectorMemoryStore(db_path=tmp_path / "mem.db")
        store.init()
        result = store.validate_semantic("people.alice.name", "Alice", 0.9, "user_explicit")
        assert result is None

    def test_validate_semantic_rejects_non_people(self, tmp_path: Path) -> None:
        store = VectorMemoryStore(db_path=tmp_path / "mem.db")
        store.init()
        result = store.validate_semantic("random.key", "x", 0.9, "user_explicit")
        assert result is not None
        assert result[0] == SemanticRejectCode.ALLOWLIST


class TestPeopleSchemas:
    def test_people_add_fact_schema(self) -> None:
        names = {f.name for f in PEOPLE_ADD_FACT_SCHEMA.fields}
        assert names == {"name", "attribute", "value"}

    def test_people_lookup_schema(self) -> None:
        names = {f.name for f in PEOPLE_LOOKUP_SCHEMA.fields}
        assert names == {"name"}

    def test_people_list_schema(self) -> None:
        assert PEOPLE_LIST_SCHEMA.fields == []
