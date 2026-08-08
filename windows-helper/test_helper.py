"""Tests for the Windows helper MCP server (auth, allowlists, rate limiting)."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

import pytest
import server
import tools
from aiohttp import test_utils
from server import Config, create_app

TOKEN = "test-secret-token"


@asynccontextmanager
async def _client(app: object) -> AsyncIterator[test_utils.TestClient]:
    """Wrap an app in a TestServer + TestClient (aiohttp 3.14 requires it)."""
    test_server = test_utils.TestServer(app)
    client = test_utils.TestClient(test_server)
    async with client:
        yield client


def _make_config(**overrides: object) -> Config:
    defaults: dict[str, object] = {
        "host": "127.0.0.1",
        "port": 8765,
        "token": TOKEN,
        "allowed_apps": ["notepad.exe"],
        "allowed_folders": ["Documents/Kiro"],
    }
    defaults.update(overrides)
    return Config(**defaults)  # type: ignore[arg-type]


def _mcp_payload(method: str, params: dict | None = None) -> dict:
    return {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}


class TestTokenAuth:
    async def _post(
        self, client: test_utils.TestClient, token: str | None
    ) -> test_utils.TestResponse:
        headers = {}
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        return await client.post("/mcp", json=_mcp_payload("tools/list"), headers=headers)

    @pytest.mark.asyncio
    async def test_token_auth_missing(self) -> None:
        app = create_app(_make_config())
        async with _client(app) as client:
            resp = await self._post(client, None)
            assert resp.status == 403

    @pytest.mark.asyncio
    async def test_token_auth_wrong(self) -> None:
        app = create_app(_make_config())
        async with _client(app) as client:
            resp = await self._post(client, "wrong-token")
            assert resp.status == 403

    @pytest.mark.asyncio
    async def test_token_auth_correct(self) -> None:
        app = create_app(_make_config())
        async with _client(app) as client:
            resp = await self._post(client, TOKEN)
            assert resp.status == 200


class TestHealth:
    @pytest.mark.asyncio
    async def test_health_no_auth(self) -> None:
        app = create_app(_make_config())
        async with _client(app) as client:
            resp = await client.get("/health")
            assert resp.status == 200
            assert (await resp.json())["status"] == "ok"


class TestToolAllowlist:
    async def _call(
        self, client: test_utils.TestClient, name: str, arguments: dict
    ) -> test_utils.TestResponse:
        return await client.post(
            "/mcp",
            json=_mcp_payload("tools/call", {"name": name, "arguments": arguments}),
            headers={"Authorization": f"Bearer {TOKEN}"},
        )

    @pytest.mark.asyncio
    async def test_non_allowlisted_tool_403(self) -> None:
        app = create_app(_make_config())
        async with _client(app) as client:
            resp = await self._call(client, "windows_rm_rf", {})
            assert resp.status == 403

    @pytest.mark.asyncio
    async def test_path_traversal_blocked(self) -> None:
        app = create_app(_make_config())
        async with _client(app) as client:
            resp = await self._call(client, "windows_read_folder", {"folder": "../../etc/passwd"})
            body = await resp.json()
            assert body["error"]["code"] == -32000

    @pytest.mark.asyncio
    async def test_open_app_allowlist(self) -> None:
        app = create_app(_make_config())
        async with _client(app) as client:
            resp = await self._call(client, "windows_open_app", {"app": "evil.exe"})
            body = await resp.json()
            assert body["error"]["code"] == -32000

    @pytest.mark.asyncio
    async def test_open_app_allowlisted(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[list[str]] = []

        def fake_popen(argv: list[str], **kwargs: object) -> object:
            calls.append(argv)
            return object()

        monkeypatch.setattr(tools.subprocess, "Popen", fake_popen)
        app = create_app(_make_config())
        async with _client(app) as client:
            resp = await self._call(client, "windows_open_app", {"app": "notepad.exe"})
            assert resp.status == 200
            assert calls


class TestRateLimiting:
    @pytest.mark.asyncio
    async def test_rate_limiting(self) -> None:
        app = create_app(_make_config())
        async with _client(app) as client:
            statuses = []
            for _ in range(server.RATE_LIMIT + 1):
                resp = await client.get("/health")
                statuses.append(resp.status)
            assert statuses[-1] == 429
