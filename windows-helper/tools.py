"""Allowlisted tool implementations for the Windows helper.

Every tool here is a fixed, allowlisted capability. There is deliberately no
generic "run a command" or "read a file" tool: the only way to reach Windows
is through one of these narrow functions, each of which validates its inputs
against the config allowlist before doing anything.

Security invariants enforced here:

- ``windows_open_app`` only launches names present in ``allowed_apps``.
- ``windows_read_folder`` only lists directories under ``allowed_folders`` and
  never returns file contents.
- No tool accepts a free-form command string. PowerShell is invoked only with
  fixed scripts; user-supplied values are passed in as base64 so they can never
  be interpreted as PowerShell code.
- Clipboard text is capped at 10000 characters.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

# Hard cap on clipboard text length, in characters.
MAX_CLIPBOARD_CHARS = 10000

# Outlook folder constant for the default Calendar folder (olFolderCalendar).
_OUTLOOK_CALENDAR_FOLDER = 9
# Outlook item type constant for an appointment (olAppointmentItem).
_OUTLOOK_APPOINTMENT_ITEM = 1

# Default timeout for PowerShell calls, in seconds. Calendar/Outlook COM can be
# slow to start, so it gets a longer budget.
_PS_TIMEOUT_SECONDS = 30.0
_PS_TIMEOUT_OUTLOOK_SECONDS = 60.0


class ToolError(Exception):
    """Raised when a tool call fails validation or execution.

    The server converts this into a JSON-RPC error response so the caller sees
    a structured failure rather than a 200 with a misleading payload.
    """


@dataclass
class Tool:
    """A single allowlisted capability: its schema plus its handler.

    ``handler`` receives the validated arguments and the loaded config so it can
    enforce allowlists (apps, folders) at call time.
    """

    name: str
    description: str
    input_schema: dict
    handler: Callable[[dict, Any], Awaitable[dict]]


# ---------------------------------------------------------------------------
# PowerShell helpers
# ---------------------------------------------------------------------------


def _ps_b64(value: str) -> str:
    """Encode a string as base64 UTF-16LE for safe embedding in PowerShell.

    PowerShell's ``[System.Text.Encoding]::Unicode`` is UTF-16LE, so this is
    the exact inverse of the decode used inside every script. Passing user data
    this way means quotes, semicolons, and other PowerShell metacharacters in
    the value can never be executed as code.
    """
    return base64.b64encode(value.encode("utf-16-le")).decode("ascii")


def _run_powershell(script: str, timeout: float = _PS_TIMEOUT_SECONDS) -> str:
    """Run a fixed PowerShell script and return its trimmed stdout.

    The script is passed via ``-EncodedCommand`` (base64 UTF-16LE), which avoids
    all command-line quoting problems. Raises ``ToolError`` on any failure so
    callers never have to inspect return codes themselves.
    """
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except FileNotFoundError:
        raise ToolError("PowerShell is not available on this system")
    except subprocess.TimeoutExpired:
        raise ToolError("PowerShell call timed out")
    if proc.returncode != 0:
        logger.warning("PowerShell failed: %s", proc.stderr.strip())
        raise ToolError("PowerShell command failed")
    return proc.stdout.strip()


# ---------------------------------------------------------------------------
# Path scoping helpers
# ---------------------------------------------------------------------------


def _resolve_path(folder: str) -> Path:
    """Resolve a folder to an absolute, symlink-free path.

    Relative paths are resolved against the user's home directory, matching how
    ``allowed_folders`` like ``Documents/Kiro`` are interpreted. ``resolve()``
    collapses ``..`` and follows symlinks, so a traversal attempt cannot hide
    behind a relative segment.
    """
    path = Path(folder)
    if not path.is_absolute():
        path = Path.home() / path
    return path.resolve()


def _is_within_allowed(requested: Path, allowed: list[Path]) -> bool:
    """Return True only when ``requested`` is an allowed folder or a descendant.

    ``is_relative_to`` is the safe containment check: it rejects siblings and
    parents, so ``../Documents/Kiro`` cannot escape the scoped root.
    """
    for root in allowed:
        if requested == root or requested.is_relative_to(root):
            return True
    return False


# ---------------------------------------------------------------------------
# Tool handlers
# ---------------------------------------------------------------------------


async def _windows_notify(args: dict, config: Any) -> dict:
    title = args.get("title")
    message = args.get("message")
    if not title or not message:
        raise ToolError("title and message are required")
    script = f"""
$ErrorActionPreference = 'SilentlyContinue'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$title = [System.Text.Encoding]::Unicode.GetString([Convert]::FromBase64String('{_ps_b64(title)}'))
$message = [System.Text.Encoding]::Unicode.GetString([Convert]::FromBase64String('{_ps_b64(message)}'))
$method = 'balloon'
if (Get-Module -ListAvailable -Name BurntToast) {{
    New-BurntToastNotification -Text $title, $message
    $method = 'burnttoast'
}} else {{
    Add-Type -AssemblyName System.Windows.Forms
    Add-Type -AssemblyName System.Drawing
    $notify = New-Object System.Windows.Forms.NotifyIcon
    $notify.Icon = [System.Drawing.SystemIcons]::Information
    $notify.Visible = $true
    $notify.BalloonTipTitle = $title
    $notify.BalloonTipText = $message
    $notify.ShowBalloonTip(5000)
    Start-Sleep -Milliseconds 200
    $notify.Dispose()
}}
Write-Output $method
"""
    method = _run_powershell(script)
    return {"ok": True, "method": method or "balloon"}


async def _windows_open_app(args: dict, config: Any) -> dict:
    app = args.get("app")
    if not app:
        raise ToolError("app is required")
    if app not in config.allowed_apps:
        raise ToolError(f"app '{app}' is not in the allowlist")
    # The app name comes from the fixed allowlist, so it cannot carry shell
    # metacharacters. ``start`` treats the first quoted argument as the window
    # title, hence the empty string.
    subprocess.Popen(["cmd", "/c", "start", "", app])
    return {"ok": True, "app": app}


async def _windows_read_folder(args: dict, config: Any) -> dict:
    folder = args.get("folder")
    if not folder:
        raise ToolError("folder is required")
    requested = _resolve_path(folder)
    allowed = [_resolve_path(f) for f in config.allowed_folders]
    if not _is_within_allowed(requested, allowed):
        raise ToolError("folder is not within an allowed folder")
    if not requested.is_dir():
        raise ToolError("folder does not exist")
    names = sorted(entry.name for entry in requested.iterdir())
    return {"folder": str(requested), "files": names}


async def _windows_clipboard_get(args: dict, config: Any) -> dict:
    script = """
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$text = Get-Clipboard -Raw
[Convert]::ToBase64String([System.Text.Encoding]::Unicode.GetBytes($text))
"""
    b64 = _run_powershell(script)
    try:
        text = base64.b64decode(b64).decode("utf-16-le")
    except (ValueError, UnicodeDecodeError):
        raise ToolError("failed to decode clipboard contents")
    return {"text": text}


async def _windows_clipboard_set(args: dict, config: Any) -> dict:
    text = args.get("text")
    if text is None:
        raise ToolError("text is required")
    if len(text) > MAX_CLIPBOARD_CHARS:
        raise ToolError(f"text exceeds {MAX_CLIPBOARD_CHARS} characters")
    script = f"""
$ErrorActionPreference = 'Stop'
$text = [System.Text.Encoding]::Unicode.GetString([Convert]::FromBase64String('{_ps_b64(text)}'))
Set-Clipboard -Value $text
"""
    _run_powershell(script)
    return {"ok": True}


async def _windows_system_status(args: dict, config: Any) -> dict:
    script = """
$ErrorActionPreference = 'SilentlyContinue'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$battery = Get-CimInstance Win32_Battery
$os = Get-CimInstance Win32_OperatingSystem
$uptime = (Get-Date) - $os.LastBootUpTime
$result = [ordered]@{
    battery_pct = if ($battery) { [int]$battery.EstimatedChargeRemaining } else { $null }
    on_ac_power = if ($battery) { ($battery.BatteryStatus -eq 2) } else { $true }
    uptime_hours = [math]::Round($uptime.TotalHours, 2)
}
$result | ConvertTo-Json -Compress
"""
    output = _run_powershell(script)
    try:
        return json.loads(output or "{}")
    except json.JSONDecodeError:
        raise ToolError("failed to parse system status")


async def _windows_calendar_list(args: dict, config: Any) -> dict:
    try:
        days = int(args.get("days", 7))
    except (TypeError, ValueError):
        raise ToolError("days must be an integer")
    if days < 1 or days > 365:
        raise ToolError("days must be between 1 and 365")
    script = f"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$days = {days}
$outlook = New-Object -ComObject Outlook.Application
$namespace = $outlook.GetNamespace('MAPI')
$calendar = $namespace.GetDefaultFolder({_OUTLOOK_CALENDAR_FOLDER})
$start = (Get-Date).Date
$end = $start.AddDays($days)
$filter = "[Start] >= '" + $start.ToString('MM/dd/yyyy HH:mm') + "' AND [Start] < '" + $end.ToString('MM/dd/yyyy HH:mm') + "'"
$items = $calendar.Items
$items.Sort('[Start]')
$restricted = $items.Restrict($filter)
$events = @()
foreach ($item in $restricted) {{
    $events += [ordered]@{{
        title = $item.Subject
        start = $item.Start.ToString('o')
        end = $item.End.ToString('o')
    }}
}}
$events | ConvertTo-Json -Compress -AsArray
"""
    output = _run_powershell(script, timeout=_PS_TIMEOUT_OUTLOOK_SECONDS)
    try:
        events = json.loads(output or "[]")
    except json.JSONDecodeError:
        events = []
    return {"events": events}


async def _windows_calendar_create(args: dict, config: Any) -> dict:
    title = args.get("title")
    start = args.get("start")
    if not title or not start:
        raise ToolError("title and start are required")
    try:
        datetime.fromisoformat(start)
    except ValueError:
        raise ToolError("start must be an ISO 8601 datetime string")
    try:
        duration = int(args.get("duration_minutes", 30))
    except (TypeError, ValueError):
        raise ToolError("duration_minutes must be an integer")
    if duration < 1 or duration > 1440:
        raise ToolError("duration_minutes must be between 1 and 1440")
    location = args.get("location") or ""
    script = f"""
$ErrorActionPreference = 'Stop'
$title = [System.Text.Encoding]::Unicode.GetString([Convert]::FromBase64String('{_ps_b64(title)}'))
$location = [System.Text.Encoding]::Unicode.GetString([Convert]::FromBase64String('{_ps_b64(location)}'))
$startStr = [System.Text.Encoding]::Unicode.GetString([Convert]::FromBase64String('{_ps_b64(start)}'))
$start = [datetime]::Parse($startStr, [System.Globalization.CultureInfo]::InvariantCulture, [System.Globalization.DateTimeStyles]::RoundtripKind)
$duration = {duration}
$outlook = New-Object -ComObject Outlook.Application
$namespace = $outlook.GetNamespace('MAPI')
$calendar = $namespace.GetDefaultFolder({_OUTLOOK_CALENDAR_FOLDER})
$appt = $calendar.Items.Add({_OUTLOOK_APPOINTMENT_ITEM})
$appt.Subject = $title
$appt.Start = $start
$appt.Duration = $duration
if ($location) {{ $appt.Location = $location }}
$appt.Save()
Write-Output 'created'
"""
    _run_powershell(script, timeout=_PS_TIMEOUT_OUTLOOK_SECONDS)
    return {"ok": True}


# ---------------------------------------------------------------------------
# Tool registry
# ---------------------------------------------------------------------------

TOOLS: list[Tool] = [
    Tool(
        name="windows_notify",
        description="Show a Windows toast notification.",
        input_schema={
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "message": {"type": "string"},
            },
            "required": ["title", "message"],
        },
        handler=_windows_notify,
    ),
    Tool(
        name="windows_open_app",
        description="Launch an allowlisted Windows application.",
        input_schema={
            "type": "object",
            "properties": {
                "app": {"type": "string"},
            },
            "required": ["app"],
        },
        handler=_windows_open_app,
    ),
    Tool(
        name="windows_read_folder",
        description="List file names in a folder scoped under an allowed folder.",
        input_schema={
            "type": "object",
            "properties": {
                "folder": {"type": "string"},
            },
            "required": ["folder"],
        },
        handler=_windows_read_folder,
    ),
    Tool(
        name="windows_clipboard_get",
        description="Read the current clipboard text.",
        input_schema={"type": "object", "properties": {}},
        handler=_windows_clipboard_get,
    ),
    Tool(
        name="windows_clipboard_set",
        description="Set the clipboard text.",
        input_schema={
            "type": "object",
            "properties": {
                "text": {"type": "string"},
            },
            "required": ["text"],
        },
        handler=_windows_clipboard_set,
    ),
    Tool(
        name="windows_system_status",
        description="Report battery, AC power, and uptime.",
        input_schema={"type": "object", "properties": {}},
        handler=_windows_system_status,
    ),
    Tool(
        name="windows_calendar_list",
        description="List upcoming Outlook calendar events.",
        input_schema={
            "type": "object",
            "properties": {
                "days": {"type": "integer", "minimum": 1, "maximum": 365},
            },
        },
        handler=_windows_calendar_list,
    ),
    Tool(
        name="windows_calendar_create",
        description="Create an Outlook calendar event.",
        input_schema={
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "start": {"type": "string", "format": "date-time"},
                "duration_minutes": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 1440,
                },
                "location": {"type": "string"},
            },
            "required": ["title", "start"],
        },
        handler=_windows_calendar_create,
    ),
]


def get_tool(name: str) -> Tool | None:
    """Return the tool with ``name``, or None if it is not allowlisted."""
    for tool in TOOLS:
        if tool.name == name:
            return tool
    return None


def list_tools() -> list[dict]:
    """Return the MCP ``tools/list`` payload for the allowlisted tools."""
    return [
        {
            "name": tool.name,
            "description": tool.description,
            "inputSchema": tool.input_schema,
        }
        for tool in TOOLS
    ]
