# Windows Helper

A tiny, security-hardened MCP server that runs natively on Windows and exposes
an **allowlisted** set of Windows capabilities to KiroCrew running in WSL2.

KiroCrew runs inside a WSL2 (Linux) sandbox and cannot natively drive Windows
apps (toasts, Outlook, clipboard, app launching). This helper is a small bridge
that runs on the Windows host and talks to KiroCrew over localhost HTTP/SSE.

## What it is

- A minimal MCP server (`POST /mcp`) implementing `initialize`, `tools/list`,
  and `tools/call`.
- A fixed, allowlisted tool set — a tool not on the list returns HTTP 403,
  never a 200.
- A health endpoint (`GET /health`) for connectivity checks.

## Security model

This process is the **trust boundary** between the WSL2 sandbox (untrusted by
design) and full Windows access. It is hardened accordingly:

| Requirement | How it is met |
|---|---|
| Loopback only | Binds `127.0.0.1` only; refuses to start on any other host |
| Token auth | Every endpoint except `/health` requires `Authorization: Bearer <token>`; compared with `hmac.compare_digest` (constant time) |
| Allowlist, not denylist | `windows_open_app` only launches names in `allowed_apps`; `windows_read_folder` only lists folders under `allowed_folders`; a tool not on the allowlist returns HTTP 403 |
| No arbitrary shell | PowerShell is invoked only with fixed scripts; user values are passed as base64 so they can never be executed as code |
| No arbitrary file I/O | Only folder listings under scoped paths; no file contents |
| No GUI automation | Not exposed at all |
| No self-modification | The config/token file is never exposed as a tool |
| Rate limiting | 30 requests per 5 seconds per IP |
| Request size limit | 10 MB (configurable via `max_file_size_mb`) |

Run it as a **low-privilege Windows user**. The token is the only thing
standing between WSL2 and Windows, so keep it secret and long.

## Requirements

- Windows 10/11 with PowerShell 5.1+ (built in).
- Python 3.10+.
- `aiohttp` (see `requirements.txt`).

## Setup

1. Copy the example config and set a strong random token:

   ```powershell
   copy config.example.json config.json
   ```

   Edit `config.json` and replace `token` with a long random secret (e.g.
   `python -c "import secrets; print(secrets.token_urlsafe(48))"`).

2. Install the dependency:

   ```powershell
   pip install -r requirements.txt
   ```

3. Start the server:

   ```powershell
   python server.py
   ```

   It prints the listening URL. The config path can be overridden with
   `--config <path>` or the `WINDOWS_HELPER_CONFIG` environment variable.

## Configuration

| Key | Meaning |
|---|---|
| `host` | Bind address. Must be a loopback address (`127.0.0.1`). |
| `port` | TCP port to listen on. |
| `token` | Shared secret required on every request except `/health`. |
| `allowed_apps` | App names `windows_open_app` may launch. |
| `allowed_folders` | Folder roots `windows_read_folder` may list, relative to the user's home directory. |
| `max_file_size_mb` | Maximum request body size in MB. |

## Tools

| Tool | Parameters | Returns |
|---|---|---|
| `windows_notify` | `title` (str, req), `message` (str, req) | `{ok, method}` |
| `windows_open_app` | `app` (str, req, must be in `allowed_apps`) | `{ok, app}` |
| `windows_read_folder` | `folder` (str, req, must be under `allowed_folders`) | `{folder, files[]}` |
| `windows_clipboard_get` | — | `{text}` |
| `windows_clipboard_set` | `text` (str, req, max 10000 chars) | `{ok}` |
| `windows_system_status` | — | `{battery_pct, on_ac_power, uptime_hours}` |
| `windows_calendar_list` | `days` (int, opt, default 7) | `{events[]}` |
| `windows_calendar_create` | `title` (str, req), `start` (ISO str, req), `duration_minutes` (int, opt, default 30), `location` (str, opt) | `{ok}` |

## How KiroCrew connects

KiroCrew (in WSL2) reaches the helper over the WSL2 localhost forwarding
bridge. From WSL2, `localhost` on the Windows host is reachable directly, so
the MCP endpoint is:

```
http://127.0.0.1:8765/mcp
```

Every request must carry the shared token:

```
Authorization: Bearer <token>
```

KiroCrew registers this as an MCP server (HTTP transport) pointing at the URL
above, with the token supplied as the bearer credential. The helper exposes
only the allowlisted tools, so KiroCrew's agent can call `windows_notify`,
`windows_open_app`, and the rest — but nothing else.

## Notes

- The directory name `windows-helper` contains a hyphen, so it is not a valid
  Python module name. Run it with `python server.py` (or rename the directory
  to `windows_helper` to use `python -m windows_helper`).
- Calendar tools require Microsoft Outlook to be installed and configured; they
  are best-effort and return an error when Outlook is unavailable.
- Toast notifications use BurntToast when installed and fall back to a Windows
  balloon tip otherwise.
