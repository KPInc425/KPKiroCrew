"""ProviderRegistry seam for the optional OpenAI-compatible backend.

Kiro Crew ships Kiro-CLI-ACP only (``agent.provider`` is pinned to ``acp``,
harness-parity H2). This registry lets an operator select an OpenAI-compatible
endpoint through a SEPARATE config section (``agent.openai_compatible``)
without touching ``agent.provider`` or the Kiro construction path (H13):

- When ``agent.openai_compatible.enabled`` is false (default), ``create_factory``
  delegates to ``DefaultProviderRegistry`` — the public Kiro path is untouched.
- When enabled, it returns a factory that builds ``OpenAICompatibleProvider``
  wired to a ``GatedToolExecutor`` so every tool call still passes through
  Kiro Crew's own security gate.

The provider is additive at this seam: when upstream lands its own provider
abstraction (kirodotdev/KiroCrew#1693), this registry is the single file to
reconcile against it, and the provider/tool modules merge untouched.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable

from kiro_crew.platform.defaults import DefaultProviderRegistry

logger = logging.getLogger(__name__)


class OpenAICompatibleRegistry(DefaultProviderRegistry):
    """DefaultProviderRegistry that swaps in the OpenAI factory when enabled."""

    def create_factory(self, cfg: Any) -> Callable[..., Any]:
        oc = getattr(getattr(cfg, "agent", None), "openai_compatible", None)
        if oc is not None and oc.enabled:
            if not oc.base_url:
                logger.warning(
                    "agent.openai_compatible.enabled is true but base_url is empty; "
                    "falling back to the default (kiro-cli / ACP) provider."
                )
                return super().create_factory(cfg)
            return self._openai_factory(cfg, oc)
        return super().create_factory(cfg)

    def _openai_factory(self, cfg: Any, oc: Any) -> Callable[..., Any]:
        """Build the OpenAI-compatible provider factory from *cfg*."""

        def _factory(
            session_key: str | None = None,
            agent: str | None = None,
            cwd: str | None = None,
            model_override: str | None = None,
            **_kwargs: object,
        ) -> Any:
            from kiro_crew.hooks import HookManager, hooks_config_from_config_dict
            from kiro_crew.providers.openai_compatible import OpenAICompatibleProvider
            from kiro_crew.providers.openai_compatible_tools import GatedToolExecutor

            model = model_override or oc.model or ""
            executor = GatedToolExecutor(
                HookManager(hooks_config_from_config_dict(_hooks_section(cfg)))
            )
            return OpenAICompatibleProvider(
                base_url=oc.base_url,
                api_key=oc.api_key,
                model=model,
                context_window=oc.context_window,
                session_key=session_key,
                agent=agent or "",
                cwd=cwd or "",
                executor=executor,
                tool_handler=_default_tool_handler(),
            )

        return _factory


def _hooks_section(cfg: Any) -> dict:
    """The config.json ``hooks`` section for building the gate's HooksConfig."""
    data = getattr(cfg, "to_dict", None)
    if data is None:
        return {}
    try:
        return data().get("hooks", {}) or {}
    except Exception:  # noqa: BLE001 - a broken hooks section must not crash selection
        return {}


def _default_tool_handler() -> Callable[..., Any]:
    """Return the default gated tool handler (execute_bash).

    Extend here as more tools are added. Each handler is invoked by
    ``GatedToolExecutor`` only AFTER ``hooks.on_tool_call`` permits it, so a
    denied command (sensitive path, credential read, governance ceiling) never
    reaches execution.
    """
    from kiro_crew.platform_compat import trusted_system_bin

    bash = trusted_system_bin("bash")

    async def _run(tool_name: str, args: dict) -> str:
        if tool_name == "execute_bash":
            cmd = args.get("command") or ""
            if not cmd:
                return "[Error: command required]"
            # Route the spawn through Kiro Crew's sandbox/limits rather than a
            # bare subprocess call, keeping resource ceilings on the agent.
            # ``popen_limited`` returns a synchronous Popen, so its
            # ``communicate()`` must run off the event loop.
            proc = _popen_limited(cmd, bash)
            stdout, stderr = await asyncio.to_thread(proc.communicate)
            out = stdout.decode(errors="replace")
            err = stderr.decode(errors="replace")
            if err:
                out += f"\n[stderr]\n{err}"
            if proc.returncode:
                out += f"\n[exit code: {proc.returncode}]"
            return out
        if tool_name in ("read", "write", "edit", "list_dir", "glob"):
            return await _run_file_op(tool_name, args)
        return f"[Tool {tool_name!r} not implemented]"

    return _run


async def _run_file_op(tool_name: str, args: dict) -> str:
    """Execute a gated file operation (read/write/edit/list_dir/glob).

    The ``GatedToolExecutor`` has already run ``HookManager.on_tool_call`` on
    these args BEFORE this runs, so the sensitive-path / write-protected-config
    checks have passed. These handlers just perform the operation; they do NOT
    re-implement the security decision.
    """
    from pathlib import Path

    from kiro_crew.atomic_write import atomic_write

    p = args.get("path") or args.get("file_path") or ""
    path = Path(p).expanduser() if isinstance(p, str) and p else None

    if tool_name == "read":
        if path is None or not path.exists():
            return f"[Error: file not found: {path or '(empty path)'}]"
        if path.is_dir():
            return f"[Error: {path} is a directory]"
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:  # noqa: BLE001
            return f"[Error reading {path}: {exc}]"
        lines = content.splitlines(keepends=True)
        start = max(0, int(args.get("offset", 1)) - 1)
        limit = args.get("limit")
        if limit:
            lines = lines[start : start + int(limit)]
        else:
            lines = lines[start:]
        return (
            "\n".join(f"{i + start + 1:>6}\t{l.rstrip()}" for i, l in enumerate(lines))
            or "[empty file]"
        )

    if tool_name in ("write", "edit"):
        if path is None:
            return "[Error: path required]"
        content = args.get("content") or ""
        try:
            if tool_name == "edit":
                if not path.exists():
                    return f"[Error: file not found: {path}]"
                old = args.get("old_string") or ""
                if not old:
                    return "[Error: old_string required]"
                current = path.read_text(encoding="utf-8", errors="replace")
                count = current.count(old)
                if count == 0:
                    return f"[Error: old_string not found in {path}]"
                replace_all = args.get("replace_all", False)
                if count > 1 and not replace_all:
                    return f"[Error: found {count} matches — use replace_all or more context]"
                content = current.replace(old, args.get("new_string", ""), -1 if replace_all else 1)
            path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write(path, content)
            return f"[Wrote {len(content)} bytes to {path}]"
        except Exception as exc:  # noqa: BLE001
            return f"[Error writing {path}: {exc}]"

    if tool_name == "list_dir":
        if path is None or not path.exists():
            return f"[Error: path not found: {path or '(empty)'}]"
        if not path.is_dir():
            return f"[Error: {path} is not a directory]"
        try:
            entries = sorted(path.iterdir(), key=lambda x: (x.is_file(), x.name.lower()))
            lines = []
            for e in entries:
                if e.is_dir():
                    lines.append(f"DIR  {e.name}/")
                else:
                    sz = e.stat().st_size
                    sz_s = f"{sz}B" if sz < 1024 else f"{sz / 1024:.1f}KB"
                    lines.append(f"FILE {e.name}  ({sz_s})")
            return "\n".join(lines) or "[empty directory]"
        except Exception as exc:  # noqa: BLE001
            return f"[Error listing {path}: {exc}]"

    if tool_name == "glob":
        import glob as _glob_mod

        pattern = args.get("pattern") or ""
        base = Path(args.get("path") or ".").expanduser()
        if not pattern:
            return "[Error: pattern required]"
        if not base.exists():
            return f"[Error: directory not found: {base}]"
        matches = sorted(_glob_mod.glob(str(base / pattern), recursive=True))
        return "\n".join(matches[:200]) if matches else f"[No files matching '{pattern}']"

    return f"[Tool {tool_name!r} not implemented]"


def _popen_limited(cmd: str, bash: str | None):
    """Build a bounded shell process for a gated bash command."""
    import shlex
    import subprocess

    from kiro_crew.sandbox import popen_limited

    if bash:
        argv = [bash, "-c", cmd]
    else:
        argv = shlex.split(cmd)
    return popen_limited(
        argv,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
