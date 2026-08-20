# OpenAI-Compatible Provider — Design

**Date:** 2026-08-19 · **Branch:** `feat/openai-compatible-provider` · **Status:** approved (chat)

## Problem

Kiro Crew drives a single LLM backend: `kiro-cli` over ACP, which routes the
model through Amazon Bedrock. The operator has their own OpenAI-compatible
providers (Ollama, vLLM, LiteLLM, OpenAI, Groq, …) and does not want to use
Bedrock (rate limits / subscription). There is currently no way to point the
agent model at an OpenAI-compatible endpoint. A third-party monkey-patch
(`lenovo1996/KiroCrew-OpenAI-Compatible`) exists but **reimplements tool
execution without calling Kiro Crew's own security gate** — it breaks the
keystone invariant (agent could read `~/.aws`, `~/.ssh`, `security_policy.json`).

Goal: a **proper, additive** OpenAI-compatible provider that keeps every tool
call behind Kiro Crew's own `hooks.on_tool_call` gate, and that merges cleanly
when upstream lands its own provider abstraction (kirodotdev/KiroCrew#1693).

## Constraints that shape the design

1. **`agent.provider` stays `enum=["acp"]` (H2).** `test_harness_parity.py::test_provider_enum_is_acp_only` pins it. A second `agent.provider` value would build the factory outside `create_provider_factory` and route around every harness-parity invariant below it. Selection must ride a **separate config field + a custom `ProviderRegistry`**, never a new `agent.provider` member.
2. **H13: registration is additive at the `ProviderRegistry` seam.** `DefaultProviderRegistry.create_factory(cfg)` is identity → `cfg.create_provider_factory()`; a new registry returns the OpenAI factory when its config flag is on, else delegates to the Kiro path. The Kiro construction path gains no conditional, no new required argument, no new failure mode.
3. **Kiro Crew does NOT execute tools — kiro-cli does.** The security gate `hooks.on_tool_call` runs on the `EVENT_PERMISSION_REQUEST` branch in `chat_runner._run_chat` (L5493) and *approves or rejects only*. The workaround reimplements `execute_bash`/`read`/`write`/`edit` with raw `subprocess`/`Path` calls and never calls the gate. The port must route every tool call through the same gate before executing.
4. **Keystone.** `security_policy.json`, `profiles/`, `admission_policy.json`, `computer_use.json`, and the new personality files are on `security._SENSITIVE_HOME_DIRS`. The provider's gated executor must preserve `is_sensitive_path` / `is_sensitive_bash_command` enforcement exactly as the ACP path does.

## Decision

**A self-contained `OpenAICompatibleProvider(LLMProvider)`** that speaks
OpenAI-compatible `POST /v1/chat/completions` (SSE streaming), plus a **gated
tool executor** that calls `HookManager.on_tool_call(...)` before every tool
executes. It emits the standard `LLMEvent` stream (`text_chunk` / `tool_call` /
`permission_request` / `tool_result` / `complete`) so existing consumers
(chat_runner, session) work unchanged. Selection is via a new config section +
a custom `ProviderRegistry`; `agent.provider` enum is left as `["acp"]`.

Rejected: the `lenovo1996` monkey-patch (bypasses the security gate, reimplements
tools unsafely, loses session persistence); waiting for upstream #1693 (indefinite).

## Components

1. **`src/kiro_crew/providers/openai_compatible.py`** — `OpenAICompatibleProvider(LLMProvider)`.
   Implements the 6 abstract methods (`start`, `shutdown`, `stream`,
   `approve_tool`, `reject_tool`, `context_usage_pct`) plus relevant defaulted
   ones (`stream_command`, `cancel`, `is_alive`, `context_window_tokens`,
   `context_used_tokens`, `available_models`, `session_id`, `served_model`).
   - HTTP client for `POST /v1/chat/completions`, SSE streaming → `EVENT_TEXT_CHUNK`.
   - OpenAI function-calling → `EVENT_TOOL_CALL` + `EVENT_PERMISSION_REQUEST`.
   - Per-call in-flight map: `request_id → tool_call_id`, resolved by `approve_tool`/`reject_tool`.
   - Reasoning-effort: no-op (`get_valid_effort_levels` → `[]`) unless the model supports it.
2. **`src/kiro_crew/providers/openai_compatible_tools.py`** — the **gated executor**.
   For each tool call, invoke `HookManager.on_tool_call(...)` with the same
   arguments as `chat_runner._run_chat` (L5493): `tool_name`, `session_key`,
   `agent`, `app`, `tool_kind`, `raw_params`, `command`, `is_shell`,
   `mcp_server_name`, `mcp_tool_name`, `resolved_agent`. Deny on `TOOL_DENY`,
   else execute. Reuses Kiro Crew's existing executors (sandbox wrap, atomic
   writes) rather than reimplementing where possible.
3. **Config** — new `agent.openai_compatible` section in `config/loader.py`
   (`AgentConfig`): `{enabled: bool, base_url, api_key, model, context_window}`.
   `agent.provider` enum untouched. Regenerate `config-baseline.json` via
   `scripts/generate_config_baseline.py`.
4. **`src/kiro_crew/platform/openai_registry.py`** — `OpenAICompatibleRegistry(ProviderRegistry)`.
   `create_factory(cfg)` returns the OpenAI factory when
   `cfg.agent.openai_compatible.enabled`, else `cfg.create_provider_factory()`.
   Wired at the composition root so the Kiro path gains no conditional.
5. **Dashboard** — a Settings toggle + Model picker listing advertised models
   from the new provider (no hardcoded model ids, per `AGENTS.md` model rules).

## Testing

- `test/test_harness_parity.py` — H2 enum still `["acp"]` (existing test must stay green).
- New `test/test_openai_compatible_provider.py` — SSE→event mapping; tool call
  emits `EVENT_PERMISSION_REQUEST`; `TOOL_DENY` blocks execution; config schema.
- New `test/test_openai_compatible_tools.py` — gated executor denies
  `cat ~/.ssh/id_rsa`, `cat ~/.aws/credentials`, `security_policy.json` read;
  allows a benign `ls`.
- Live: `make dev` → chat through the OpenAI provider → bash succeeds,
  sensitive-path read denied with reason, `security_policy.json` read denied.

## Out of scope

Multimodal (image attachments); true mid-turn `steer`; session-file resume
(history is in-memory); the `claude-code` seam; changing `agent.provider` enum.
