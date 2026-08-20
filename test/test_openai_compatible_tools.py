"""Tests for the gated tool executor used by the OpenAI-compatible provider.

The gate is Kiro Crew's own ``HookManager.on_tool_call`` — the same chokepoint
the ACP path uses. These tests assert the security-critical property: a tool
whose command reads a sensitive path is DENIED and the handler is never called.
"""

from __future__ import annotations

import asyncio

from kiro_crew.hooks import HooksConfig, HookManager, TOOL_DENY
from kiro_crew.providers.openai_compatible_tools import (
    GatedToolExecutor,
    GatedToolRequest,
)


class TestGatedToolExecutor:
    def _executor(self) -> GatedToolExecutor:
        return GatedToolExecutor(HookManager(HooksConfig.from_dict({})))

    def _req(self, tool_name="execute_bash", args=None, command=None, **kw):
        args = args or {}
        return GatedToolRequest(
            request_id=1,
            tool_name=tool_name,
            tool_kind=kw.get("tool_kind", tool_name),
            raw_params=args,
            command=command,
            is_shell=kw.get("is_shell", command is not None),
            handler=kw.get("handler"),
            session_key=kw.get("session_key", ""),
            agent=kw.get("agent", ""),
            app=kw.get("app", ""),
            mcp_server_name=kw.get("mcp_server_name", ""),
            mcp_tool_name=kw.get("mcp_tool_name", ""),
            resolved_agent=kw.get("resolved_agent", ""),
        )

    @staticmethod
    def _run(coro):
        return asyncio.run(coro)

    def test_benign_bash_is_executed(self):
        called = []

        async def handler():
            called.append(True)
            return "ok"

        req = self._req(command="ls /tmp", handler=handler)
        result, deny = self._run(self._executor().execute(req))
        assert deny is None
        assert result == "ok"
        assert called == [True]

    def test_sensitive_path_read_is_denied_and_not_executed(self):
        called = []

        async def handler():
            called.append(True)
            return "SHOULD NOT RUN"

        # cat ~/.ssh/id_rsa is a sensitive-path read the gate must block.
        req = self._req(
            command="cat ~/.ssh/id_rsa",
            args={"command": "cat ~/.ssh/id_rsa"},
            handler=handler,
        )
        result, deny = self._run(self._executor().execute(req))
        assert deny is not None, "a sensitive-path read must be denied"
        assert "sensitive" in deny.lower() or "blocked" in deny.lower()
        assert called == [], "denied tool handler must never execute"

    def test_aws_credentials_read_is_denied(self):
        called = []

        async def handler():
            called.append(True)
            return "SHOULD NOT RUN"

        req = self._req(
            command="cat ~/.aws/credentials",
            args={"command": "cat ~/.aws/credentials"},
            handler=handler,
        )
        result, deny = self._run(self._executor().execute(req))
        assert deny is not None
        assert called == []

    def test_keystone_policy_read_is_denied(self):
        called = []

        async def handler():
            called.append(True)
            return "SHOULD NOT RUN"

        req = self._req(
            command="cat ~/.kiro/crew/security_policy.json",
            args={"command": "cat ~/.kiro/crew/security_policy.json"},
            handler=handler,
        )
        result, deny = self._run(self._executor().execute(req))
        assert deny is not None
        assert called == []

    def test_deny_is_surfaceable(self):
        req = self._req(command="cat ~/.ssh/id_rsa")
        verdict = self._executor().gate(req)
        assert verdict.action == TOOL_DENY
        assert self._executor().is_denied(verdict) is True
