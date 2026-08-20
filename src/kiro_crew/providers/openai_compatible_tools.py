"""Gated tool execution for the OpenAI-compatible provider.

Kiro Crew's security model puts the deny floor (builtin + user denied-command
rules), the sensitive-path / credential-read blocks, and the governance ceiling
all in ONE chokepoint: ``HookManager.on_tool_call``. In the ACP path, kiro-cli
executes tools and Kiro Crew only approves/rejects via that gate. An
OpenAI-compatible backend has no kiro-cli to execute tools, so the provider must
execute them itself — but it MUST route every call through the same gate first,
or the agent gains the ability to read/write its own ceiling (``~/.aws``,
``~/.ssh``, ``security_policy.json``, …). That is the entire difference between
this port and a naive monkey-patch.

This module owns the *decision* (gate) and the *execution* (running the tool
after approval). It is deliberately free of any model/transport logic so it can
be tested in isolation and reused by any non-ACP provider.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Callable

from kiro_crew.hooks import TOOL_DENY, HookManager, ToolHookResult

logger = logging.getLogger(__name__)


@dataclass
class GatedToolRequest:
    """A single tool call awaiting the gate + (if allowed) execution.

    ``request_id`` is the id surfaced to the consumer via
    ``EVENT_PERMISSION_REQUEST`` and echoed back by ``approve_tool`` /
    ``reject_tool``. The callable ``handler`` performs the actual execution and
    returns the result string.
    """

    request_id: str | int
    tool_name: str
    tool_kind: str
    raw_params: dict | None
    command: str | None
    is_shell: bool
    handler: Callable[..., Any]
    session_key: str
    agent: str
    app: str
    mcp_server_name: str
    mcp_tool_name: str
    resolved_agent: str


class GatedToolExecutor:
    """Gate then execute tool calls through Kiro Crew's own security hook.

    ``HookManager.on_tool_call`` is the single decision point for deny-floor,
    sensitive-path, credential-read, and governance checks. The executor:

    - runs the gate with the same arguments ``chat_runner._run_chat`` uses
      (L5493), so behaviour matches the ACP path exactly;
    - on ``TOOL_DENY`` returns a denial without calling the handler;
    - otherwise invokes the handler.

    It never auto-approves: it only reports the gate verdict so the caller
    (the provider) can decide how to surface approval (interactive card,
    auto-approve policy, etc.).
    """

    def __init__(self, hooks: HookManager) -> None:
        self._hooks = hooks

    def gate(self, req: GatedToolRequest) -> ToolHookResult:
        """Run ``on_tool_call`` for ``req``. Returns the verdict."""
        return self._hooks.on_tool_call(
            req.tool_name,
            session_key=req.session_key,
            agent=req.agent,
            app=req.app,
            tool_kind=req.tool_kind,
            raw_params=req.raw_params,
            command=req.command,
            is_shell=req.is_shell,
            mcp_server_name=req.mcp_server_name,
            mcp_tool_name=req.mcp_tool_name,
            resolved_agent=req.resolved_agent,
        )

    def is_denied(self, result: ToolHookResult) -> bool:
        """True when the gate denied the call (any deny flavour)."""
        return result.action == TOOL_DENY

    async def execute(self, req: GatedToolRequest) -> tuple[str, str | None]:
        """Gate then execute ``req``.

        Returns ``(result_text, deny_reason)``. On a gate denial the handler is
        NOT called and ``deny_reason`` is set; on success ``deny_reason`` is
        ``None`` and ``result_text`` holds the tool output.
        """
        verdict = self.gate(req)
        if self.is_denied(verdict):
            reason = (verdict.reason or "blocked").strip()
            logger.info(
                "Gated tool %r denied by security policy: %s",
                req.tool_name,
                reason,
            )
            return "", reason
        try:
            out = await req.handler()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - surface any tool failure to the model
            logger.exception("Gated tool %r handler failed", req.tool_name)
            return f"[Tool execution error: {exc}]", None
        return out, None
