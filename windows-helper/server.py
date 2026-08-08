"""Windows helper MCP server.

A tiny, security-hardened HTTP/SSE bridge that exposes an allowlisted set of
Windows capabilities to KiroCrew running in WSL2. This process is the trust
boundary between the WSL2 sandbox (untrusted by design) and full Windows
access, so every request is gated by:

- binding to a loopback address only (never 0.0.0.0),
- a shared-secret Bearer token on every endpoint except ``/health``,
- per-IP rate limiting,
- a request size cap.

The MCP surface is a fixed allowlist defined in ``tools.py``; anything not on
it returns a JSON-RPC error, never a 200.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from aiohttp import web

from auth import extract_bearer, token_matches
from tools import ToolError, get_tool, list_tools

logger = logging.getLogger(__name__)

# MCP protocol version this server speaks.
MCP_PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "windows-helper"
SERVER_VERSION = "0.1.0"

# Rate limiting: at most this many requests per IP within the window.
RATE_LIMIT = 30
RATE_WINDOW_SECONDS = 5.0

# The example placeholder token must never be accepted as a real secret.
PLACEHOLDER_TOKEN = "CHANGE_ME_TO_A_LONG_RANDOM_SECRET"

# Loopback hosts we are willing to bind to. Anything else is refused at startup.
LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")


@dataclass
class Config:
    """Server configuration loaded from a JSON file."""

    host: str
    port: int
    token: str
    allowed_apps: list[str] = field(default_factory=list)
    allowed_folders: list[str] = field(default_factory=list)
    max_file_size_mb: int = 10


class RateLimiter:
    """In-memory per-IP request counter with a sliding time window.

    This is a coarse throttle, not a hard security boundary: it exists to blunt
    brute-force attempts against the token and to keep a misbehaving caller
    from saturating the process. State is intentionally not persisted.
    """

    def __init__(self, limit: int, window_seconds: float) -> None:
        self.limit = limit
        self.window = window_seconds
        self._hits: dict[str, deque[float]] = {}

    def allow(self, ip: str) -> bool:
        now = time.monotonic()
        hits = self._hits.setdefault(ip, deque())
        while hits and now - hits[0] > self.window:
            hits.popleft()
        if len(hits) >= self.limit:
            return False
        hits.append(now)
        return True


def load_config(path: Path) -> Config:
    """Load and parse the config JSON file."""
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    return Config(
        host=str(data.get("host", "127.0.0.1")),
        port=int(data.get("port", 8765)),
        token=str(data.get("token", "")),
        allowed_apps=[str(a) for a in data.get("allowed_apps", [])],
        allowed_folders=[str(f) for f in data.get("allowed_folders", [])],
        max_file_size_mb=int(data.get("max_file_size_mb", 10)),
    )


def validate_config(config: Config) -> None:
    """Refuse to start on an unsafe configuration.

    Binding to anything but loopback would expose the token-gated surface to
    the network, and an empty or placeholder token would make the trust
    boundary trivially bypassable. Both are fail-closed at startup.
    """
    if config.host not in LOOPBACK_HOSTS:
        raise SystemExit(
            f"Refusing to start: host '{config.host}' is not a loopback address. "
            "Bind to 127.0.0.1 only."
        )
    if not config.token or config.token == PLACEHOLDER_TOKEN:
        raise SystemExit(
            "Refusing to start: set a strong random token in the config file."
        )


def _rpc_error(rid: Any, code: int, message: str) -> dict:
    """Build a JSON-RPC 2.0 error response."""
    return {
        "jsonrpc": "2.0",
        "id": rid,
        "error": {"code": code, "message": message},
    }


def create_app(config: Config) -> web.Application:
    """Build the aiohttp application with all security middleware wired in."""
    limiter = RateLimiter(RATE_LIMIT, RATE_WINDOW_SECONDS)
    max_bytes = config.max_file_size_mb * 1024 * 1024

    @web.middleware
    async def security_middleware(request: web.Request, handler: Any) -> web.StreamResponse:
        if not limiter.allow(request.remote or "unknown"):
            return web.json_response({"error": "rate_limited"}, status=429)
        if request.path != "/health":
            provided = extract_bearer(request.headers.get("Authorization", ""))
            if not token_matches(config.token, provided):
                return web.json_response({"error": "unauthorized"}, status=403)
        return await handler(request)

    async def handle_health(request: web.Request) -> web.Response:
        return web.json_response({"status": "ok"})

    async def handle_mcp(request: web.Request) -> web.Response:
        # Read the body with a hard cap so a huge payload cannot exhaust memory
        # even if the client omits or lies about Content-Length.
        raw = await request.content.read(max_bytes + 1)
        if len(raw) > max_bytes:
            return web.json_response({"error": "request too large"}, status=413)
        try:
            body = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return web.json_response(_rpc_error(None, -32700, "parse error"), status=400)
        if not isinstance(body, dict) or body.get("jsonrpc") != "2.0":
            return web.json_response(_rpc_error(None, -32600, "invalid request"), status=400)

        rid = body.get("id")
        method = body.get("method")
        params = body.get("params") or {}

        if method == "initialize":
            result = {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            }
            return web.json_response({"jsonrpc": "2.0", "id": rid, "result": result})

        if method == "tools/list":
            return web.json_response(
                {"jsonrpc": "2.0", "id": rid, "result": {"tools": list_tools()}}
            )

        if method == "tools/call":
            name = params.get("name")
            arguments = params.get("arguments") or {}
            tool = get_tool(name) if isinstance(name, str) else None
            if tool is None:
                # A tool not on the allowlist is an error, never a 200. HTTP 403
                # matches the roadmap's "anything not on it returns 403" and keeps
                # the allowlist as a hard gate rather than a soft JSON-RPC error.
                return web.json_response(
                    _rpc_error(rid, -32601, f"tool '{name}' is not allowlisted"),
                    status=403,
                )
            try:
                result = await tool.handler(arguments, config)
            except ToolError as exc:
                return web.json_response(_rpc_error(rid, -32000, str(exc)))
            return web.json_response(
                {
                    "jsonrpc": "2.0",
                    "id": rid,
                    "result": {
                        "content": [{"type": "text", "text": json.dumps(result)}]
                    },
                }
            )

        return web.json_response(
            _rpc_error(rid, -32601, f"method '{method}' not found")
        )

    app = web.Application(middlewares=[security_middleware])
    app.router.add_get("/health", handle_health)
    app.router.add_post("/mcp", handle_mcp)
    return app


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Windows helper MCP server")
    parser.add_argument(
        "--config",
        default=None,
        help="path to the config JSON file (default: config.json next to this file)",
    )
    return parser.parse_args(argv)


def _resolve_config_path(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit)
    env = os.environ.get("WINDOWS_HELPER_CONFIG")
    if env:
        return Path(env)
    return Path(__file__).resolve().parent / "config.json"


def main(argv: list[str] | None = None) -> None:
    """Load config, validate it, and run the server."""
    logging.basicConfig(level=logging.INFO)
    args = _parse_args(argv)
    config = load_config(_resolve_config_path(args.config))
    validate_config(config)
    app = create_app(config)
    print(f"Windows helper listening on http://{config.host}:{config.port}")
    print("REMINDER: keep the token secret; only KiroCrew in WSL2 should know it.")
    web.run_app(app, host=config.host, port=config.port)


if __name__ == "__main__":
    main()
