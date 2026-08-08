# Integration Roadmap — KPKopanion personality → KiroCrew

**Goal:** Port KPKopanion's three genuinely novel features (adaptive personality,
people relationship graph, gamification) into KiroCrew, contributing them
upstream so they become part of the app itself — while keeping the fork in
sync with upstream KiroCrew updates.

**Source:** `E:\Programming\AI\KPKopanion` (the three features to port)
**Target:** your KiroCrew fork in WSL2 (`~/projects/KPKiroCrew`)
**Security gate:** everything lands behind KiroCrew's keystone + governance;
nothing weakens the security model.

---

## 0. The development model: fork + upstream PRs

### Git setup

1. **Fork** `github.com/kirodotdev/KiroCrew` on GitHub (your account).
2. **Clone your fork** into WSL2's Linux filesystem (NOT `/mnt/c`):
   ```bash
   cd projects
   git clone https://github.com/<you>/KiroCrew.git
   cd KPKiroCrew
   git remote add upstream https://github.com/kirodotdev/KiroCrew.git
   ```
3. **Editable install** (run from source so your changes take effect):
   ```bash
   make build                          # npm build + editable pip install
   PYTHONPATH=src python -m kiro_crew gateway
   ```
4. **Keep in sync** — before any feature work, rebase on upstream:
   ```bash
   git fetch upstream
   git rebase upstream/main           # or merge, your preference
   ```
5. **PR features upstream** — each phase below is a PR candidate. If accepted,
   it's in upstream and your fork stays clean. If not accepted (or while
   waiting), it lives in your fork and you maintain only the merge.

### Why fork, not clone-with-local-edits

| Approach | Get updates | Your features in the app | Maintainability |
|----------|-------------|--------------------------|-----------------|
| Clone + local edits (no fork) | Manual, painful | No PR path | Diverges over time |
| **Fork + upstream PRs** | `git rebase upstream/main` | Yes — features land in upstream | Stays clean if PR'd |
| Separate side project | N/A | Not in KiroCrew | Two systems to run |

**The answer to "am I forking or just building on my clone?" is: forking.**
A fork on GitHub is the only model that lets you both pull upstream updates
trivially AND contribute your features back so they become part of KiroCrew
itself. A bare local clone has no PR path and diverges.

### How to keep features merge-friendly

- Build features as **builtin skills/apps**, not core edits, wherever
  possible. KiroCrew's App Kit and skill system are designed extension points —
  they don't touch core behavior, so upstream merges stay clean.
- The **one core change** this roadmap needs (a `people.*` memory prefix) is
  a one-line data addition in `_BUILTIN_PREFIXES`. Tiny, reviewable, PR-able.
- Each feature on its own branch → its own PR → small, reviewable, lands
  independently.

### Upstream contribution bar (from AGENTS.md)

Every PR must pass the gate:
```bash
black src/kiro_crew test && isort src/kiro_crew test
flake8 src/kiro_crew test && mypy src/kiro_crew
python -m pytest
```
Plus: update the spec in `docs/system-specs/modules/` in the same commit;
add the doc to its directory README; run `scripts/docs-lint.sh`. Code style:
100-char lines, `from __future__ import annotations`, `logging.getLogger`,
async I/O, no hardcoded model ids, no emojis in UI (use `lucide-react`).

---

## 1. Phase 0 — Environment (do this first)

### In WSL2
- [ ] Fork + clone (above)
- [ ] `make build` + `kirocrew setup` + `kirocrew doctor` (confirm sandbox
      active — this is the whole point of WSL2)
- [ ] `kiro-cli login`
- [ ] Create `~/.kiro/crew/prompt.md` with a placeholder persona block (this is
      the user prompt override — KiroCrew reads it instead of the shipped
      `config/prompt.md`). This is where your JARVIS voice lives.

### On Windows
- [ ] Build the tiny Windows helper MCP server (separate small project, not in
      the KiroCrew repo) — allowlisted tools, token auth, low-privilege. This
      is the bridge for toasts/apps/Outlook/clipboard.

**Exit criteria:** `kirocrew gateway` runs in WSL2, dashboard reachable at
`localhost:5476`, sandbox confirmed active, helper responds to a ping.

---

## 2. Phase 1 — Adaptive Personality (highest value, do this first)

This is the gem: a closed loop where "was that helpful?" feedback nudges
personality sliders, which feed the system prompt. KiroCrew has no
personality system in core. ~550 lines of source to port.

### What it is

- **Behavior dials** — 8 tunable knobs (warmth, humor, formality, initiative,
  verbosity, ...) persisted as JSON, injected into the prompt as a
  natural-language block. Self-adjusts when the user corrects ("too formal"
  → decreases formality).
- **Feedback loop** — samples ~10% of interactions, asks "was that
  helpful?", stores ratings, nudges the dials after repeated negative
  feedback. The only genuinely adaptive personality mechanism in either
  codebase.

### KPKopanion source → KiroCrew target

| KPKopanion file | Lines | KiroCrew home | How |
|---|---|---|---|
| `ethics/behavior_dials.py` | 256 | New: `src/kiro_crew/personality/dials.py` | Port the dials dataclass + self-adjustment; persist to `~/.kiro/crew/personality_dials.json` (this MUST be added to `_SENSITIVE_HOME_DIRS` — see security note) |
| `self_evolution/feedback.py` | 297 | New: `src/kiro_crew/personality/feedback.py` | Port the feedback sampler + slider nudging; wire to `autonudge` or a cron lane |
| `personality.py` | 162 | `~/.kiro/crew/prompt.md` (user override) | The slider→text mapping becomes a prompt block you write by hand; the dials module injects the current values |
| `classification/classifier.py` | 397 | New: `src/kiro_crew/personality/tone.py` | Optional — per-turn tone injection. Can defer; the dials + feedback are the core value |

### Core changes needed

1. **Prompt assembly** — `agent.py` `_prompt_path()` already reads
   `~/.kiro/crew/prompt.md`. Add a small step that interpolates the current
   dial values into a `{personality_block}` placeholder before sending to
   kiro-cli. ~20 lines in `agent.py` or a `context_blocks.py` addition.
2. **Keystone** — add `personality_dials.json` and `personality_feedback.json`
   to `_SENSITIVE_HOME_DIRS` in `security.py` so the agent can't rewrite its
   own personality to be more permissive. This is the critical security
   invariant — without it, a prompt-injected agent could nudge its own
   warmth up and its caution down. ~2 lines.
3. **Feedback MCP tool** — add `personality_feedback` to `mcp_core.py` (the
   tool the agent calls to record a user's "was that helpful?" rating). Must
   use `_resolve_session_key_strict` (mutates session state). ~50 lines.

### How it runs

- Dials are loaded at session start, injected into the prompt.
- Feedback is collected via an MCP tool call (the agent asks, the user
  answers, the agent records it) or a dashboard button.
- A cron job (or autonudge lane) runs the dial-adjustment heuristic nightly.

### Upstream PR-ability

**Good candidate.** KiroCrew already has the `mochi` app showing its house
style for persona/mood. A core "adaptive personality" layer is a natural
extension. The PR would be: the `personality/` module + the prompt
interpolation + the keystone addition + the MCP tool + tests + a spec doc.
Frame it as "operator-tunable personality with self-correction" — matches
KiroCrew's operator-is-trusted, agent-is-not philosophy.

### Security checklist for this phase
- [ ] `personality_dials.json` in `_SENSITIVE_HOME_DIRS` (read+write blocked)
- [ ] `personality_feedback.json` in `_SENSITIVE_HOME_DIRS`
- [ ] Feedback MCP tool uses `_resolve_session_key_strict`
- [ ] Dial adjustment runs as a cron (out-of-band executor, Plane C) — verify
      it goes through governance if you want the operator to be able to
      deny `personality.adjust` via a scope
- [ ] No model id hardcoded in the feedback sampler

### Effort
~2-3 focused sessions. The port is mechanical (JSON-backed, low coupling);
the integration (prompt interpolation, keystone, MCP tool) is the real work.

---

## 3. Phase 2 — People Relationship Graph

Structured people memory: registry, dossiers, relationship map, sentiment
timeline. KiroCrew has free-form `user.*` facts but no schema. ~1,164 lines.

### What it is

- **Person registry** — aliases, attributes per person.
- **Dossier** — compiled context injected into the prompt when a person is
  mentioned.
- **Relationship map** — edges between people (family, coworker, friend).
- **Sentiment timeline** — mood over time derived from journal/mentions.

### KPKopanion source → KiroCrew target

| KPKopanion file | Lines | KiroCrew home | How |
|---|---|---|---|
| `people/registry.py` | ~300 | KiroCrew semantic memory: add `people.*` prefix | The registry becomes `people.<name>.name`, `people.<name>.aliases`, `people.<name>.attributes` semantic keys. No new table — use the existing `vector_memory.py` semantic store. |
| `people/dossier.py` | ~250 | New: `src/kiro_crew/builtin_skills/people-memory/SKILL.md` | A skill that compiles a person's semantic facts into a prompt block when their name is detected in the conversation. |
| `people/relationship_map.py` | ~300 | Semantic keys: `people.<name>.relationships` | Edges as a JSON value under the person's key. A skill query resolves the graph. |
| `people/sentiment_timeline.py` | ~300 | Defer or simplify | KPKopanion's sentiment is keyword-based. Could be rebuilt as episodic-memory queries + a periodic cron that tags mentions. Lower priority. |

### Core changes needed

1. **`people.*` prefix** — add `"people"` to `_BUILTIN_PREFIXES` in
   `vector_memory.py`. This is the one core change — a one-line data
   addition, not an evaluator edit. Makes `people.*` keys first-class
   semantic memory.
2. **Builtin skill** — `src/kiro_crew/builtin_skills/people-memory/SKILL.md`
   with frontmatter (`always: true`, `triggers: ["person mentioned"]`). The
   skill body tells the agent how to maintain people facts: when you learn
   something about a person, write `people.<name>.<fact>`. When a person is
   mentioned, retrieve their dossier.
3. **MCP tools** (optional) — `people_lookup`, `people_add_fact`,
   `people_list` in `mcp_core.py`, backed by semantic memory queries. Or
   let the agent use the existing `learn_add` tool with `people.*` keys —
   no new tool needed for the MVP.

### How it runs

- The agent uses `learn_add` (existing MCP tool) to store `people.*` facts.
- The `people-memory` skill (always-on) tells the agent the schema and when
  to retrieve dossiers.
- Semantic memory's existing vector search surfaces relevant people facts
  when a name is mentioned.

### Upstream PR-ability

**Strong candidate.** A `people.*` prefix + a builtin skill is exactly the
kind of small, additive change KiroCrew accepts. The prefix is a data
change; the skill is markdown. Low risk, high value for a personal agent.

### Security checklist
- [ ] `people.*` keys go through the existing semantic-memory injection
      screening (prompt-injection checks already exist in `vector_memory.py`)
- [ ] No new filesystem paths — people data lives in the existing SQLite
      semantic store, which is already keystone-protected
- [ ] The skill is in `builtin_skills/` (the only bundled path)

### Effort
~1-2 sessions. The MVP (registry + dossier + skill) is small; the prefix
addition is trivial. Sentiment timeline is optional and can be deferred.

---

## 4. Phase 3 — Gamification (optional, lower priority)

Quests/XP/achievements from goals and habits. KiroCrew has nothing in core
(only streaks in the `mochi` app). ~1,484 lines. Product value, not
intelligence.

### What it is

- Quests generated from goals/habits (e.g. "drink water 7 days" → a quest).
- XP, levels, achievements.
- Narrative flavor (fantasy/sci-fi theme text).

### KPKopanion source → KiroCrew target

| KPKopanion file | Lines | KiroCrew home | How |
|---|---|---|---|
| `adventure/engine.py` | ~700 | New builtin app: `src/kiro_crew/apps/builtins/quests/` | Model after `mochi` — an app with a manifest, a store, and a dashboard widget. Quests are generated by a cron job from goal/habit data. |
| `gamification/` | ~470 | Same app: `quests/score.py`, `quests/levels.py` | XP/level logic, deterministic. |
| `adventure/models.py` | ~65 | `quests/models.py` | Dataclasses for Quest, Achievement. |

### Core changes needed

**None.** This is purely additive — a new builtin app. Apps don't touch
core; they get their own manifest, store, and MCP tools via the App Kit.

### How it runs

- A cron job runs the quest generator daily, reading goals/habits (which
  you'd track via the people/life features, or via memory `goal.*` keys).
- XP is awarded via an MCP tool the agent calls when you complete a quest.
- The dashboard widget shows your level/quests (the App Kit renders widgets).

### Upstream PR-ability

**Possible, but frame carefully.** Gamification is a product opinion. KiroCrew's
maintainers may not want it in core. The `mochi` app is the template — if you
build it as an opt-in builtin app (`defaultEnabled: false`), it's a cleaner
PR. If they decline, it lives in your fork.

### Security checklist
- [ ] App manifest `permissions` are advisory but keep them minimal
- [ ] `apps_allow_third_party` stays `false` (your app is builtin, not third-party)
- [ ] Quest data store goes under `~/.kiro/crew/apps/quests/` (already
      keystone-protected as part of the data home)
- [ ] No shell execution in the quest generator (it's pure Python + memory)

### Effort
~2-3 sessions. More UI work than the other two (dashboard widget, app
manifest). Defer until Phases 1-2 are done and you're using the assistant
daily.

---

## 5. Phase 4 — Windows Helper (separate project, not in the fork)

This is the bridge for native Windows capabilities. It is NOT part of the
KiroCrew fork — it's a small standalone project on Windows.

### What it is

A tiny MCP server over HTTP/SSE, running natively on Windows, exposing an
allowlisted tool set to KiroCrew in WSL2 via localhost.

### Tools to expose (allowlist, not denylist)

- `windows_notify` — toast notification
- `windows_open_app` — launch an allowlisted app
- `windows_read_folder` — read a scoped folder (e.g. `Documents\Kiro`)
- `windows_clipboard_get` / `windows_clipboard_set`
- `windows_calendar_list` / `windows_calendar_create` — Outlook integration
- `windows_system_status` — battery, network, running apps

### What it must NOT expose

- ❌ Arbitrary shell / `cmd` execution
- ❌ Arbitrary file read/write (scoped paths only)
- ❌ GUI automation (defer — that's the computer-use risk surface)
- ❌ Self-modification of its config or token

### Security model

- Binds `127.0.0.1` only
- Requires a shared-secret token (only KiroCrew in WSL2 knows it)
- Runs as a low-privilege Windows user
- Tool set is a fixed allowlist — anything not on it returns 403

### Effort
~1-2 sessions. Small surface; the security discipline (allowlist, low-
privilege, token) is the work, not the code volume.

---

## 6. Summary: what goes where

| Thing | Lives in | Why |
|---|---|---|
| KiroCrew core + brain | Your fork in WSL2 (`~/projects/KPKiroCrew`), synced to upstream | Mature, secure, gets updates |
| Adaptive personality | `src/kiro_crew/personality/` in the fork → PR upstream | Core feature, keystone-protected |
| People graph | `people.*` prefix (core) + `builtin_skills/people-memory/` → PR upstream | Small core change + skill |
| Gamification | `apps/builtins/quests/` → PR upstream (opt-in) | Additive app |
| Windows helper | Separate small project on Windows | Native bridge, not part of KiroCrew |
| KPKopanion runtime | **Retired** — reference only | Replaced by the above |

## 7. What you keep from KPKopanion (as reference, not running code)

- `self_evolution/feedback.py` + `ethics/behavior_dials.py` — port to Phase 1
- `people/` — port to Phase 2
- `adventure/` + `gamification/` — port to Phase 3
- `personality.py` — the slider→text mapping informs your `prompt.md`
- Everything else is redundant with KiroCrew or shallow (dreams, intel LLM
  stub, mind engine) — do not port.

## 8. Suggested order

1. **Phase 0** — fork, clone, WSL2, sandbox check, helper ping (the foundation)
2. **Phase 1** — adaptive personality (highest value, smallest, most novel)
3. **Phase 2** — people graph (small, high value for "intimate with my life")
4. **Phase 4** — Windows helper (unblocks native Windows integration)
5. **Phase 3** — gamification (optional, do it if you want the quest feel)

Each phase is independently shippable and independently PR-able upstream.

---

## 9. The one thing to internalize

The keystone invariant from the audit applies to every feature you port:
**the agent must not be able to modify the data that controls its own
behavior.** For the personality dials, that means `personality_dials.json`
goes in `_SENSITIVE_HOME_DIRS` — same as `security_policy.json`. If you skip
that, a prompt-injected agent can nudge its own warmth up and its caution
down, and you've recreated the KPKopanion "self-set booleans" problem inside
KiroCrew. The port is only safe if the dials are operator-controlled, not
agent-controlled.
