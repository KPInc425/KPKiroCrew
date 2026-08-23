"""OpenAI-compatible LLM provider for Kiro Crew.

A self-contained ``LLMProvider`` that speaks OpenAI-compatible
``POST /v1/chat/completions`` (SSE streaming), letting Kiro Crew drive any
endpoint that implements that spec — Ollama, vLLM, LiteLLM, Together, Groq,
OpenRouter, Azure OpenAI, a self-hosted proxy, etc. — instead of kiro-cli /
Bedrock.

Two things make this a *proper* port rather than a monkey-patch:

1. **The security gate stays in Kiro Crew.** Every tool call passes through the
   SAME ``HookManager.on_tool_call`` gate the ACP path uses (see
   ``openai_compatible_tools.GatedToolExecutor``), so denied-command rules,
   sensitive-path / credential-read blocks, and the governance ceiling apply
   exactly as they do to kiro-cli. A provider that executes tools without this
   gate lets the agent read/write its own ceiling.

2. **Selection is additive at the ``ProviderRegistry`` seam.** ``agent.provider``
   stays ``enum=["acp"]`` (harness-parity H2). This provider is chosen by a
   separate config section + a custom ``ProviderRegistry``, so the Kiro path
   gains no conditional (H13) and upstream provider work merges cleanly.

The provider drives its own prompt → tool → execute → re-prompt loop in-band:
``stream()`` yields ``EVENT_PERMISSION_REQUEST`` for each tool call and awaits
the approval future that ``approve_tool`` / ``reject_tool`` resolve. This mirrors
how ``chat_runner._run_chat`` consumes the ACP stream, where approval is driven
concurrently while the generator is suspended at ``yield``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import AsyncIterator, Callable
from typing import Any

import aiohttp

from kiro_crew.acp.types import TurnUsage
from kiro_crew.providers.base import (
    EVENT_COMPLETE,
    EVENT_PERMISSION_REQUEST,
    EVENT_TEXT_CHUNK,
    EVENT_TOOL_CALL,
    EVENT_TOOL_RESULT,
    LLMProvider,
)
from kiro_crew.providers.base import LLMEvent
from kiro_crew.providers.openai_compatible_tools import (
    GatedToolExecutor,
    GatedToolRequest,
)

logger = logging.getLogger(__name__)


class OpenAICompatibleProvider(LLMProvider):
    """LLMProvider backed by any OpenAI-compatible chat/completions endpoint."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        context_window: int = 0,
        session_key: str | None = None,
        agent: str | None = None,
        cwd: str = "",
        executor: GatedToolExecutor | None = None,
        tool_handler: Callable[..., Any] | None = None,
        client: aiohttp.ClientSession | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model
        self._context_window = context_window
        self._session_key = session_key or ""
        self._agent = agent or ""
        self._cwd = cwd
        self._executor = executor
        self._tool_handler = tool_handler
        # Owned client created in start(); injectable for tests.
        self._client = client
        self._owned_client = client is None

        self._messages: list[dict[str, Any]] = []
        # request_id -> asyncio.Future(bool) resolved by approve/reject.
        self._pending: dict[str | int, asyncio.Future] = {}
        self._next_request_id = 0
        self._last_pct = 0.0
        self._input_tokens = 0
        self._output_tokens = 0
        self._started = False

    # ── lifecycle ──

    async def start(self) -> None:
        if self._started:
            return
        if self._client is None:
            timeout = aiohttp.ClientTimeout(total=60.0, sock_read=300.0)
            self._client = aiohttp.ClientSession(timeout=timeout)
        self._started = True

    async def shutdown(self) -> None:
        if self._owned_client and self._client is not None:
            await self._client.close()
            self._client = None
        self._started = False

    def is_alive(self) -> bool:
        return self._started

    # ── model/context surface ──

    @property
    def served_model(self) -> str:
        return self._model

    def context_usage_pct(self) -> float:
        return self._last_pct

    def context_window_tokens(self) -> int:
        # Model-aware window: the configured ``context_window`` is a fallback,
        # but these are cloud models whose real window far exceeds a local
        # default. Resolve from the known-model map first so the dashboard meter
        # and compaction heuristics use the actual window per model, then fall
        # back to the operator's configured value.
        return _model_window(self._model) or self._context_window

    def context_used_tokens(self) -> int:
        return self._input_tokens + self._output_tokens

    def available_models(self) -> list[dict[str, str]]:
        return [{"modelId": self._model, "name": self._model}]

    # ── permission resolution ──

    async def approve_tool(self, request_id: str | int, *, always: bool = False) -> None:
        fut = self._pending.pop(request_id, None)
        if fut is not None and not fut.done():
            fut.set_result(True)

    async def reject_tool(self, request_id: str | int) -> None:
        fut = self._pending.pop(request_id, None)
        if fut is not None and not fut.done():
            fut.set_result(False)

    # ── tool-call bookkeeping ──

    def _request_id(self) -> int:
        self._next_request_id += 1
        return self._next_request_id

    def _register_tool_use(self, tool_call_id: str) -> tuple[int, asyncio.Future]:
        rid = self._request_id()
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[rid] = fut
        return rid, fut

    # ── main turn ──

    async def stream(self, message: str) -> AsyncIterator[LLMEvent]:
        self._messages.append({"role": "user", "content": message})
        while True:
            data = await self._chat_completion(self._messages)
            if data is None:
                yield self._complete_event()
                return
            content = data.get("content") or ""
            if content:
                self._messages.append({"role": "assistant", "content": content})
                yield LLMEvent(kind=EVENT_TEXT_CHUNK, text=content)

            tool_calls = data.get("tool_calls") or []
            if not tool_calls:
                # No tool call -> turn over.
                yield self._complete_event()
                return

            # One or more tool calls this round. Emit them, gate each, execute,
            # then loop back to re-prompt with the results appended.
            for tc in tool_calls:
                fn = tc.get("function") or {}
                tool_name = fn.get("name") or ""
                args_raw = fn.get("arguments") or ""
                try:
                    args = json.loads(args_raw) if args_raw else {}
                except (ValueError, TypeError):
                    args = {}
                tool_call_id = tc.get("id") or f"call-{uuid.uuid4().hex[:8]}"

                yield LLMEvent(
                    kind=EVENT_TOOL_CALL,
                    tool_call_id=tool_call_id,
                    title=tool_name,
                    tool_kind=tool_name,
                    raw_tool_params=args,
                    tool_input=json.dumps(args) if args else "",
                )

                rid, fut = self._register_tool_use(tool_call_id)
                yield LLMEvent(
                    kind=EVENT_PERMISSION_REQUEST,
                    request_id=rid,
                    title=tool_name,
                    tool_kind=tool_name,
                    raw_tool_params=args,
                    tool_input=json.dumps(args) if args else "",
                    is_shell=_is_shell_name(tool_name),
                )

                allowed = await fut
                if not allowed:
                    self._messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tool_call_id,
                            "content": "Tool use was denied by the user or security policy.",
                        }
                    )
                    yield LLMEvent(
                        kind=EVENT_TOOL_RESULT,
                        tool_call_id=tool_call_id,
                        title=tool_name,
                        tool_output="Tool use denied.",
                        tool_final=True,
                    )
                    continue

                result_text = await self._run_gated(tool_name, args, rid)
                self._messages.append(
                    {"role": "tool", "tool_call_id": tool_call_id, "content": result_text}
                )
                yield LLMEvent(
                    kind=EVENT_TOOL_RESULT,
                    tool_call_id=tool_call_id,
                    title=tool_name,
                    tool_output=result_text,
                    tool_final=True,
                )

    async def _run_gated(self, tool_name: str, args: dict, request_id: int) -> str:
        """Execute ``tool_name`` through the gated executor (if configured)."""
        executor = self._executor
        handler = self._tool_handler
        if executor is None or handler is None:
            return f"[Tool {tool_name!r} not configured; nothing executed.]"
        command = args.get("command") if _is_shell_name(tool_name) else None
        req = GatedToolRequest(
            request_id=request_id,
            tool_name=tool_name,
            # The gate's write-protection (filesystem.write scope) keys on
            # ``tool_kind == "edit"``, so a write/edit call must report that
            # kind (matching the ACP path) rather than the bare tool name.
            tool_kind=_tool_kind_for(tool_name),
            raw_params=args,
            command=command,
            is_shell=_is_shell_name(tool_name),
            handler=lambda: handler(tool_name, args),
            session_key=self._session_key,
            agent=self._agent,
            app="",
            mcp_server_name="",
            mcp_tool_name="",
            resolved_agent=self._agent,
        )
        result_text, deny_reason = await executor.execute(req)
        if deny_reason:
            return f"Blocked by security policy: {deny_reason}"
        return result_text

    async def _chat_completion(self, messages: list[dict[str, Any]]) -> dict[str, Any] | None:
        """POST /v1/chat/completions (non-streaming for simplicity/safety).

        Returns a normalized dict with ``content`` and ``tool_calls``, or None
        on a request that produced no turn data.
        """
        if self._client is None:
            return None
        url = f"{self._base_url}/chat/completions"
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "stream": False,
        }
        tool_specs = _tool_specs()
        if tool_specs:
            payload["tools"] = tool_specs
        try:
            resp = await self._client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
            body = await resp.json()
            # aiohttp responses must be released; fakes may not expose it.
            release = getattr(resp, "release", None)
            if callable(release):
                release()
        except Exception as exc:  # noqa: BLE001 - surface a readable turn error
            logger.exception("chat/completions failed")
            return {"content": f"[Provider error: {exc}]", "tool_calls": []}

        choice = (body.get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        usage = body.get("usage") or {}
        self._input_tokens = int(usage.get("prompt_tokens") or 0)
        self._output_tokens = int(usage.get("completion_tokens") or 0)
        window = self.context_window_tokens()
        if window:
            total = self._input_tokens + self._output_tokens
            self._last_pct = round(min(1.0, total / window) * 100, 1)
        return {
            "content": msg.get("content") or "",
            "tool_calls": msg.get("tool_calls") or [],
        }

    def _complete_event(self) -> LLMEvent:
        return LLMEvent(
            kind=EVENT_COMPLETE,
            usage=TurnUsage(
                input_tokens=self._input_tokens,
                output_tokens=self._output_tokens,
            ),
        )


def _is_shell_name(tool_name: str) -> bool:
    """Best-effort shell detection for OpenAI tool names.

    kiro-cli's ACP path carries an authoritative ``kind=="execute"`` flag; the
    OpenAI function name is the only signal here, so map the conventional bash
    tool names onto ``is_shell``. Unknown names default to non-shell, which the
    gate's deny-by-default backstop covers only when ``command`` is also absent.
    """
    return tool_name in ("execute_bash", "bash", "shell", "terminal")


def _tool_kind_for(tool_name: str) -> str:
    """Map a tool name onto the ACP semantic kind the gate's scopes expect.

    The write-protection scope (``filesystem.write``) keys on
    ``tool_kind == "edit"``, so write/edit calls report ``"edit"``; every other
    tool reports its bare name (which the gate treats as a generic kind).
    """
    if tool_name in ("write", "edit"):
        return "edit"
    return tool_name


def _model_window(model: str) -> int:
    """Context window in tokens for a known model, or 0 if unknown.

    Cloud models (served through the local Ollama proxy) carry large windows;
    local models are smaller. Return 0 for an unknown model so callers fall back
    to the operator's configured ``context_window``. A single config value cannot
    serve both a 256K and a 1M model correctly, so the known-model map wins when
    present.
    """
    m = (model or "").lower()
    if "gemma4" in m and "e4b" not in m:
        return 256 * 1024
    if "deepseek-v4-flash" in m:
        return 1_000_000
    if "deepseek-v4-pro" in m:
        return 1_000_000
    if "kimi-k2.7" in m or "kimi-k2.6" in m or "kimi-k3" in m:
        return 256 * 1024
    if "glm-5" in m:
        return 256 * 1024
    if "qwen3.5" in m:
        return 256 * 1024
    return 0


def _tool_specs() -> list[dict[str, Any]]:
    """Declare the tool set to the model as OpenAI function-calling specs.

    The gated executor runs only tools whose handler is registered (see
    ``openai_registry._run_file_op``). Keep the set aligned with those handlers.
    """
    return [
        {
            "type": "function",
            "function": {
                "name": "execute_bash",
                "description": "Run a shell command and return its output.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "command": {"type": "string", "description": "Shell command to run"}
                    },
                    "required": ["command"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "read",
                "description": "Read a file with line numbers (offset/limit optional).",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Absolute file path"},
                        "offset": {"type": "integer", "description": "1-based start line"},
                        "limit": {"type": "integer", "description": "Max lines"},
                    },
                    "required": ["path"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "write",
                "description": "Write content to a file, creating parent dirs.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Absolute file path"},
                        "content": {"type": "string", "description": "File content"},
                    },
                    "required": ["path", "content"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "edit",
                "description": "Replace text in a file (old_string -> new_string).",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Absolute file path"},
                        "old_string": {"type": "string", "description": "Text to find"},
                        "new_string": {"type": "string", "description": "Replacement text"},
                        "replace_all": {
                            "type": "boolean",
                            "description": "Replace all occurrences",
                        },
                    },
                    "required": ["path", "old_string", "new_string"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "list_dir",
                "description": "List directory entries with sizes.",
                "parameters": {
                    "type": "object",
                    "properties": {"path": {"type": "string", "description": "Directory path"}},
                    "required": ["path"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "glob",
                "description": "Find files by glob pattern under a base path.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Base directory"},
                        "pattern": {"type": "string", "description": "Glob pattern, e.g. **/*.py"},
                    },
                    "required": ["pattern"],
                },
            },
        },
    ]
