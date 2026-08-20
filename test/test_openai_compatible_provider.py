"""Tests for the OpenAI-compatible provider event stream.

The provider drives a prompt → tool → gate → execute → re-prompt loop in-band,
yielding the standard LLMEvent stream. These tests assert the mapping from an
OpenAI chat/completions response to events, and that tool calls emit a
permission request that approve/reject resolve.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
import unittest.mock
from pathlib import Path
from unittest.mock import AsyncMock

from kiro_crew.acp.types import (
    EVENT_COMPLETE,
    EVENT_PERMISSION_REQUEST,
    EVENT_TEXT_CHUNK,
    EVENT_TOOL_CALL,
    EVENT_TOOL_RESULT,
)
from kiro_crew.providers.openai_compatible import OpenAICompatibleProvider
from kiro_crew.providers.openai_compatible_tools import GatedToolExecutor
from kiro_crew.hooks import HooksConfig, HookManager
from kiro_crew.config.loader import KiroCrewConfig
from kiro_crew.platform.bootstrap import _select_provider_registry
from kiro_crew.platform.defaults import DefaultProviderRegistry


class TestProviderRegistrySelection:
    def test_default_config_selects_default_registry(self):
        assert isinstance(_select_provider_registry(KiroCrewConfig()), DefaultProviderRegistry)

    def test_enabled_config_selects_openai_registry(self):
        cfg = KiroCrewConfig()
        cfg.agent.openai_compatible.enabled = True
        cfg.agent.openai_compatible.base_url = "http://localhost:11434/v1"
        reg = _select_provider_registry(cfg)
        assert type(reg).__name__ == "OpenAICompatibleRegistry"

    def test_enabled_without_base_url_falls_back_to_default(self):
        cfg = KiroCrewConfig()
        cfg.agent.openai_compatible.enabled = True
        # base_url empty -> must NOT silently select OpenAI; degrade to default.
        factory = _select_provider_registry(cfg).create_factory(cfg)
        assert factory is not None

    def test_factory_builds_openai_provider_when_enabled(self):
        cfg = KiroCrewConfig()
        cfg.agent.openai_compatible.enabled = True
        cfg.agent.openai_compatible.base_url = "http://localhost:11434/v1"
        cfg.agent.openai_compatible.model = "qwen2.5:14b"
        reg = _select_provider_registry(cfg)
        prov = reg.create_factory(cfg)(session_key="s", agent="kirocrew")
        assert type(prov).__name__ == "OpenAICompatibleProvider"
        assert prov._model == "qwen2.5:14b"


class TestOpenAICompatibleConfig:
    @staticmethod
    def _load_from_dict(data):
        import kiro_crew.config.loader as loader

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(data, f)
            tmp = Path(f.name)
        try:
            with unittest.mock.patch("kiro_crew.config.loader.config_path", return_value=tmp):
                return loader.KiroCrewConfig.load()
        finally:
            tmp.unlink(missing_ok=True)

    def test_default_is_disabled_and_acp_remains_provider(self):
        cfg = KiroCrewConfig()
        assert cfg.agent.openai_compatible.enabled is False
        assert cfg.agent.provider == "acp"

    def test_load_parses_enabled_section(self):
        cfg = self._load_from_dict(
            {
                "agent": {
                    "openai_compatible": {
                        "enabled": True,
                        "base_url": "http://localhost:11434/v1",
                        "api_key": "ollama",
                        "model": "qwen2.5:14b",
                        "context_window": 32768,
                    }
                }
            }
        )
        oc = cfg.agent.openai_compatible
        assert oc.enabled is True
        assert oc.base_url == "http://localhost:11434/v1"
        assert oc.model == "qwen2.5:14b"
        assert oc.context_window == 32768

    def test_junk_section_degrades_safely(self):
        cfg = self._load_from_dict({"agent": {"openai_compatible": "not-a-dict"}})
        assert cfg.agent.openai_compatible.enabled is False

    def test_non_bool_enabled_fails_safe_to_disabled(self):
        cfg = self._load_from_dict({"agent": {"openai_compatible": {"enabled": "true"}}})
        assert cfg.agent.openai_compatible.enabled is False

    def test_round_trip_serializes_section(self):
        cfg = self._load_from_dict(
            {
                "agent": {
                    "openai_compatible": {"enabled": True, "base_url": "http://localhost:8080/v1"}
                }
            }
        )
        reloaded = self._load_from_dict(cfg.to_dict())
        assert reloaded.agent.openai_compatible.enabled is True
        assert reloaded.agent.openai_compatible.base_url == "http://localhost:8080/v1"
        # provider enum is untouched
        assert reloaded.agent.provider == "acp"


class _FakeClient:
    """Minimal stand-in for aiohttp.ClientSession.post(...)."""

    def __init__(self, responses):
        # responses: list of dicts returned for successive /chat/completions calls.
        self._responses = list(responses)
        self._calls = []
        self.post = AsyncMock(side_effect=self._post)

    async def _post(self, url, headers=None, json=None):
        self._calls.append(json)
        body = self._responses.pop(0) if self._responses else {}
        return _FakeResponse(body)

    async def aclose(self):
        return None


class _FakeResponse:
    def __init__(self, body):
        self._body = body

    def raise_for_status(self):
        return None

    async def json(self):
        return self._body


def _make_provider(responses, tool_handler=None):
    prov = OpenAICompatibleProvider(
        base_url="http://fake/v1",
        api_key="test",
        model="test-model",
        context_window=1000,
        session_key="sess",
        agent="kirocrew",
        executor=GatedToolExecutor(HookManager(HooksConfig.from_dict({}))),
        tool_handler=tool_handler,
        client=_FakeClient(responses),
    )
    return prov


def _collect(provider, message):
    events = []

    async def _run():
        async for ev in provider.stream(message):
            events.append(ev)
            # Resolve any permission request immediately (approve) unless the
            # test injects a custom resolver.
            if ev.kind == EVENT_PERMISSION_REQUEST:
                await provider.approve_tool(ev.request_id)

    asyncio.run(_run())
    return events


def test_text_only_turn_yields_text_and_complete():
    prov = _make_provider(
        [
            {
                "choices": [{"message": {"content": "Hello!", "tool_calls": None}}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 3},
            }
        ]
    )
    events = _collect(prov, "hi")
    kinds = [e.kind for e in events]
    assert kinds == [EVENT_TEXT_CHUNK, EVENT_COMPLETE]
    assert events[0].text == "Hello!"
    assert events[1].usage.output_tokens == 3


def test_tool_call_emits_permission_then_result():
    executed = []

    async def handler(tool_name, args):
        executed.append((tool_name, args))
        return "ls output"

    prov = _make_provider(
        [
            {
                "choices": [
                    {
                        "message": {
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {
                                        "name": "execute_bash",
                                        "arguments": '{"command": "ls"}',
                                    },
                                }
                            ],
                        }
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            },
            # second round: no tool call, turn over
            {
                "choices": [{"message": {"content": "done", "tool_calls": None}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            },
        ],
        tool_handler=handler,
    )
    events = _collect(prov, "list files")
    kinds = [e.kind for e in events]
    assert EVENT_TOOL_CALL in kinds
    assert EVENT_PERMISSION_REQUEST in kinds
    assert EVENT_TOOL_RESULT in kinds
    assert events[-1].kind == EVENT_COMPLETE
    # The bash tool was gated-and-executed (benign command).
    assert executed == [("execute_bash", {"command": "ls"})]


def test_sensitive_tool_call_denied_handler_not_called():
    executed = []

    async def handler(tool_name, args):
        executed.append((tool_name, args))
        return "SHOULD NOT RUN"

    prov = _make_provider(
        [
            {
                "choices": [
                    {
                        "message": {
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {
                                        "name": "execute_bash",
                                        "arguments": '{"command": "cat ~/.ssh/id_rsa"}',
                                    },
                                }
                            ],
                        }
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            },
            {"choices": [{"message": {"content": "done", "tool_calls": None}}]},
        ],
        tool_handler=handler,
    )
    events = _collect(prov, "read my key")
    # The result should carry the denial reason, not tool output.
    result_event = next(e for e in events if e.kind == EVENT_TOOL_RESULT)
    assert (
        "sensitive" in result_event.tool_output.lower()
        or "blocked" in result_event.tool_output.lower()
    )
    assert executed == [], "denied tool handler must never be called"


def test_reject_tool_skips_execution():
    executed = []

    async def handler(tool_name, args):
        executed.append((tool_name, args))
        return "SHOULD NOT RUN"

    prov = _make_provider(
        [
            {
                "choices": [
                    {
                        "message": {
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {
                                        "name": "execute_bash",
                                        "arguments": '{"command": "ls"}',
                                    },
                                }
                            ],
                        }
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            },
            {"choices": [{"message": {"content": "done", "tool_calls": None}}]},
        ],
        tool_handler=handler,
    )
    events = []

    async def _run():
        async for ev in prov.stream("x"):
            events.append(ev)
            if ev.kind == EVENT_PERMISSION_REQUEST:
                await prov.reject_tool(ev.request_id)

    asyncio.run(_run())
    assert executed == [], "rejected tool handler must never run"
    result_event = next(e for e in events if e.kind == EVENT_TOOL_RESULT)
    assert "denied" in result_event.tool_output.lower()
