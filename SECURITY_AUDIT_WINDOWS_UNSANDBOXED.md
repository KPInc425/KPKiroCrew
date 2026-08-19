# KiroCrew Security Audit — Unsandboxed Execution on Windows

**Audited:** `E:\Programming\AI\KiroCrew`
**Date:** 2026-08-07
**Scope:** Full read-only security audit to evaluate whether opting into
`agent.sandbox_allow_unsandboxed_exec` on Windows is safe for a fully
automated assistant.
**Method:** Five parallel code-level audits (security core, execution layer,
computer use, documentation, MCP/subagent/cron/workflows) plus direct
verification of the three most critical findings.

---

## TL;DR — Recommendation

**Do not opt into unsandboxed execution on Windows as your daily driver yet.**

The security architecture is genuinely well-engineered — defense-in-depth,
a tamper-evident audit log, a keystone the agent cannot touch, and unusually
honest documentation. But on Windows specifically, the OS sandbox layer does
not exist, so opting in means the agent runs with your **full user privileges
and no credential isolation**, protected only by in-process regex gates that
have at least one reachable bypass (hooks.json) and a Windows-specific blind
spot (backslash paths).

If you want to run it today, run it under **WSL2** (where the Linux namespace
sandbox exists) or in a **dedicated low-privilege VM**, not as your primary
Windows user. See [Hardening Recommendations](#hardening-recommendations).

---

## 1. How the Security Model Is Designed

KiroCrew is a six-layer defense-in-depth system. The structural insight is
that **the PreToolUse gate is KiroCrew's own gate, not the agent's** — denied
commands and governance are evaluated in `hooks.py`, never written into the
kiro-cli agent JSON, so the agent cannot edit its own deny list.

| Layer | Control | Always-on? | Works on Windows? |
|-------|---------|------------|-------------------|
| 5 | SEL audit (HMAC-chained, tamper-evident) | Yes | Yes |
| 4 | Output redaction (credentials + exfil URLs + streaming) | Yes | Yes |
| 3 | MCP input validation (typed schemas, length caps) | Yes | Yes |
| 2 | Command gate (139 denied rules + sensitive-bash + exfil shapes) | Yes | **Partial** (see §4) |
| 1 | Filesystem gate (resolved-path read block + wider write block) | Yes | Yes |
| 0 | OS sandbox (Linux namespace / macOS Seatbelt) | **Optional** | **Does not exist** |

**The keystone** is the load-bearing invariant: `security_policy.json`,
`profiles/`, `admission_policy.json`, `denied_commands.json`, the SEL HMAC
key, `token_signing.key`, `.env`, `computer_use.json`, and `run/` are all on
`security._SENSITIVE_HOME_DIRS` — read+write blocked from every agent tool
and shell form. This is what makes the ceiling **un-disableable from inside
the agent**. Verified present and correct.

**Approval modes:**
- `approval_mode` defaults to `"auto"` — meaning fully automated is the
  default posture once the agent can execute at all.
- `dangerously_skip_permissions` is the only non-expiring grant
  (config-file-only, no dashboard toggle).
- YOLO overrides are time-limited (default 6h, hard cap 24h), fail-closed on
  audit failure.

---

## 2. What "Unsandboxed Execution" Means on Windows

On Windows, `detect_backend()` always returns `"none"` — there are no user
namespaces (Linux) and no `sandbox-exec` (macOS). The default is
**fail-closed**: the agent subprocess refuses to spawn unless you set:

```json
{ "agent": { "sandbox_allow_unsandboxed_exec": true } }
```

The docs are explicit about what this means:

> "Setting it means agent subprocesses run with your own user privileges,
> which is the same posture as running the tool yourself in a shell."

**Concretely, with the opt-in:**
- The kiro-cli subprocess has **full filesystem access** — `~/.aws`,
  `~/.ssh`, `~/.gnupg`, your entire home directory are readable.
- **No credential isolation** — the env scrub runs at the parent level
  only; the sandbox's per-credential masking is gone.
- **No resource limits** — `apply_resource_limits` is a no-op on Windows
  (no `resource` module). No FD cap, no fork-bomb mitigation, no OOM bias.
- The only remaining boundary is **in-process regex gates** running as your
  user — bypassable by a sufficiently creative agent, as documented below.

---

## 3. Critical Findings

### 3.1 [CRITICAL] `hooks.json` is off the sensitive-path floor — arbitrary code execution

**Severity:** Critical (sandbox escape / persistence)
**Status:** Verified — `hooks.json` and `ScriptHookStore` appear **zero times**
in `security.py`.

`ScriptHookStore` (`hooks.py` L2539) persists script hooks to
`~/.kiro/crew/hooks.json`, and `run_script_hook` (L2364) executes
`hook.command` via `cmd /c` (Windows) or `/bin/sh -c` (POSIX) with the
gateway's full privileges.

But `hooks.json` is **not** in `_SENSITIVE_HOME_DIRS`, not in
`_WRITE_PROTECTED_HOME_PATHS`, not in `_WRITE_PROTECTED_BASH_LEAVES`. A
prompt-injected agent could:

1. Write `~/.kiro/crew/hooks.json` (via the file-edit tool or shell — neither
   checks this path as sensitive).
2. Register a `PreToolUse` script hook with arbitrary `hook.command`.
3. On the next gateway restart, every tool call triggers that command.

The `capabilities.script_hooks` governance gate is default-OFF but
**fail-open on error** and permits on a standalone host. On Windows there is
no sandbox/cgroup to bound the hook. This is a reachable arbitrary-code-
execution path that survives reboots.

**This is the single highest-value fix:** add `hooks.json` and the script-hook
store to the keystone floor.

### 3.2 [HIGH] `@kirocrew-core` MCP tools bypass the hooks gate

**Status:** Verified — `@kirocrew-core` is in both `tools` and `allowedTools`
in `config/defaults.json` (L17-21, L36-40).

`@kirocrew-core` is a **server-level** auto-approve grant — every tool it
serves (`spawn_run`, `spawn_continue`, `learn_add`, `send_message`,
`file_send`, `send_notification`, …) is auto-approved by kiro-cli and
**never reaches `hooks.on_tool_call`**. Their security rests only on internal
governance checks inside `mcp_core.py` — which check capability scopes but
do **not** include the always-on sensitive-path floor, the exfiltration deny,
or the denied-command rules.

Code confirms this explicitly (`agent.py` L449-454, `mcp_core.py` L2544,
`apps/bridges.py` L429-433). The gateway does strip client-forged caller
identity and inject its own, so the *identity* is trustworthy — but the
*tool dispatch* bypasses the main security gate.

**Windows impact:** higher, because there is no OS sandbox backstop behind
these weaker internal checks.

### 3.3 [HIGH] Windows backslash paths evade the sensitive-path shell matcher

**Status:** Verified — `_build_sensitive_regex` (security.py L4448-4457)
builds `sensitive_path = rf"{home_alts}/(?:{dirs_pattern})(?:/|\s|$|['\"])"`
using **forward slashes only**. `home_alts` includes `Path.home()`, `~`,
`$HOME`, `/home/<user>`, `/Users/<user>` — none with backslash separators.

On Windows, a native `cmd /c` command like:
```
type C:\Users\kingp\.aws\credentials
```
does **not** match the shell-path regex (no forward slashes). The file-*tool*
path is still protected (it resolves the path through Python's
`os.path.join` which handles Windows separators), but the **shell path**
— the one that matters for unsandboxed execution — has a real gap.

Git-bash style (`C:/Users/...`) *is* caught. The gap is specifically native
backslash paths in `cmd /c` commands, which is exactly how script hooks run
on Windows.

### 3.4 [HIGH] Cron and workflow auto-approve paths drop key security layers

- **Cron jobs with `approval_mode="auto"`** run with `ToolApprovalPolicy.AUTO_APPROVE`,
  which runs only the always-enforced deny floor (sensitive path, denied
  commands) and **skips** the governance ceiling, the exfiltration deny, and
  the write-protected-config deny.
- **Workflow agent steps** run with `AUTO_APPROVE` and **no `HookManager`
  at all** — same reduced floor, plus no governance ceiling.
- **Subagents** with `parent_policy="auto"` bypass interactive approval and
  inherit the `@kirocrew-core` auto-approve (finding 3.2).

Since `approval_mode` defaults to `"auto"`, these are the default paths, not
opt-ins. On Windows with no sandbox, the reduced floor is the only thing
standing between an auto-approved cron job and your filesystem.

### 3.5 [MEDIUM] No resource-exhaustion protection on Windows

`apply_resource_limits` (security.py L7267) returns a no-op when `resource`
is unavailable (L7302-7304). The cgroup v2 fork-bomb/memory bound is
Linux-only. On Windows, a compromised or runaway agent has **no FD-leak,
fork-bomb, or memory-exhaustion mitigation**. For unsandboxed execution,
this is a real DoS vector against the host.

### 3.6 [MEDIUM] Shell-startup-file persistence vector

`~/.bashrc`/`~/.zshrc` (and on Windows, PowerShell profile/startup) are
**not** on the sensitive-path floor. An agent that writes to your shell
startup files gains persistence — every new shell runs the planted command.
Mitigated only by the approval gate and destructive-command rules, both of
which are weakened in the auto-approve paths above.

### 3.7 [LOW] Governance degrades fail-open on unexpected errors

`_governance_denial` (hooks.py L955-966) returns `None` (permit) on any
non-`PlatformCompositionError` exception. A transient profile-load glitch
silently drops the operator's policy narrowing for that call. The built-in
deny floor still runs, so this is a narrowing bypass, not a full one — but
it is a documented soft fail-open on a security decision.

---

## 4. What's Done Well

These are genuine strengths, not checkbox items:

- **The keystone invariant is real and well-engineered.** The agent cannot
  read or write `security_policy.json`, `profiles/`, `admission_policy.json`,
  `denied_commands.json`, `token_signing.key`, `sel_hmac.key`,
  `computer_use.json`, or `run/`. Symlink/casefold/`KIROCREW_HOME`-re-
  anchoring hardening is present. This is the one control that survives even
  full auto-approve.
- **SEL audit is fail-closed for high-stakes paths.** Safety-override
  activation, exception grants, and internal sensitive reads all refuse to
  proceed if the audit write fails. The log is HMAC-SHA256-chained and
  tamper-evident.
- **ReDoS defense is layered.** Built-in rules use a linear fragment matcher;
  user regexes are structurally gated and length-bounded.
- **Process spawning is solid.** List-argv only, no `shell=True` in live
  paths, trusted-binary pinning (`_resolve_kiro_bin`), env scrubbing, and
  resource-limit shim with `-I -S` to skip `sitecustomize`.
- **`crash_guard` is safe** — logging only, no restart, no escalation.
- **Computer use is inert on Windows** — `WindowsBackend` refuses every
  action; the macOS driver's security model (keystone opt-in, `auto`→`global`
  protection, secure-field floor) is well-engineered but moot on Windows.
- **Gateway socket identity works on Windows** — peer-principal verification
  via SID comparison, not just PID.
- **The documentation is unusually honest.** Every gap above is documented
  somewhere in the tree. The code comments admit residuals rather than
  hiding them.

---

## 5. Comparison to KPKopanion

Your KPKopanion project prioritizes **privacy-first, local processing,
consent-gated power, and self-healing**. KiroCrew aligns on several axes:

| Value | KiroCrew | KPKopanion |
|-------|----------|------------|
| Local-first processing | Yes (in-process embeddings, local MCP) | Yes (core principle) |
| Consent-gated execution | Yes (approval modes, deny floor) | Yes (consent-gated power) |
| Memory across sessions | Yes (vector memory, skills) | Yes (memory deepens) |
| Self-healing | Partial (crash_guard logs, doctor CLI) | Yes (core principle) |
| Privacy — data egress | **No default network egress control** | Yes (zero-knowledge relay) |
| Operator vs agent trust split | Yes (keystone) | Yes (consent boundary) |
| Windows-native support | Partial (many features POSIX-only) | N/A (your project) |

The biggest philosophical gap: KPKopanion is **privacy-first with zero-
knowledge relay**, while KiroCrew has **no default network egress control**
and routes through the KiroACP provider (kiro-cli, an external subprocess).
On Windows with no sandbox, a compromised agent can post non-credential
data to arbitrary hosts. This is a real divergence from KPKopanion's values.

---

## 6. Hardening Recommendations

If you decide to run KiroCrew on Windows despite the findings:

### Minimum bar before opting in

1. **Run under WSL2, not native Windows.** The Linux namespace sandbox
   exists there, credential isolation works, and resource limits apply.
   This is the single highest-value change.
2. **If you must run native:** use a dedicated low-privilege Windows user
   account. Put `~/.aws`, `~/.ssh`, and the Kiro data home outside that
   account's reachable paths. Treat the hooks gate as defense-in-depth,
   not as a boundary.
3. **Never set `dangerously_skip_permissions`** on an unsandboxed Windows
   host. Use time-limited YOLO only.

### Config hardening

4. **Remove `@kirocrew-core` from `allowedTools`** in your agent JSON, or
   narrow it to specific tools. This forces kirocrew-core MCP calls through
   the hooks gate. (This is a tradeoff — some autonomous features may break.)
5. **Set `approval_mode` to something other than `"auto"`** for cron jobs
   and workflows if you can tolerate the friction. `"auto"` is the default
   and drops the governance ceiling + exfiltration deny.
6. **Audit cron job `approval_mode` settings** — treat `"auto"` as a
   high-privilege setting.
7. **Keep `apps_allow_third_party` at its default `false`** unless you
   specifically need third-party apps.

### Code-level fixes (would require contributing upstream)

8. **Add `hooks.json` to `_SENSITIVE_HOME_DIRS`** — the most critical fix.
   Without this, the keystone has a hole.
9. **Make `_build_sensitive_regex` Windows-separator-aware** — add
   backslash alternatives to the home-anchored path matcher, or normalize
   backslashes to forward slashes before matching.
10. **Add Windows resource limits** — at minimum, a job-object-based memory
    and process-count cap for the agent subprocess.

### Operational

11. **Review the SEL audit log regularly.** It is the tamper-evident trail
    for all auto-approve paths. There is no UI dashboard for it yet —
    queries are API-only.
12. **Do not expose the gateway to a network** without authentication. The
    docs require auth on all non-localhost surfaces; respect that.
13. **Keep the desktop installer's limitations in mind** — it is unsigned
    (SmartScreen interstitial) and has no auto-update yet.

---

## 7. Final Evaluation

| Dimension | Rating | Notes |
|-----------|--------|-------|
| Security architecture design | **Strong** | Defense-in-depth, keystone, fail-closed defaults |
| Documentation honesty | **Excellent** | Gaps are documented, not hidden |
| Windows support maturity | **Immature** | No sandbox, no resource limits, shell-path gap |
| Safe for unsandboxed daily driver on Windows | **No** | Too many gaps without the OS sandbox backstop |
| Safe under WSL2 | **Probably yes** | With the config hardening above |
| Safe in a dedicated VM | **Probably yes** | With the config hardening above |

**Bottom line:** The security *design* is better than most agent frameworks
I've audited. The problem is that on Windows, the design's outermost layer
(the OS sandbox) doesn't exist, and the inner layers have at least one
reachable bypass (`hooks.json`) and a platform-specific blind spot
(backslash paths). Until those are fixed, unsandboxed Windows execution
concentrates too much autonomous power in one unconfined process running as
your primary user.

If KiroCrew's feature set is what you want, the safest path is to run it
under WSL2 with the config hardening above, and consider contributing the
`hooks.json` keystone fix upstream — it would benefit every Windows user.

---

*This audit was read-only. No project files were modified except this
report. Findings were verified by direct code inspection where cited.*