# KiroCrew ← KPKopanion Integration Baseline

**Phase 0 deliverable.** This document records the exact insertion points in
KiroCrew that later phases will modify, the structure of each KPKopanion source
module to be ported, the mochi app pattern, the MCP tool registration pattern,
and the prompt loading path. Subsequent phase agents should read this instead of
re-discovering the codebase.

Source of truth for the plan: `INTEGRATION_ROADMAP.md`.

---

## 1. Directory verification

All four target directories exist:

| Directory | Status |
|---|---|
| `KiroCrew/src/kiro_crew/` | ✅ backend package |
| `KiroCrew/src/kiro_crew/builtin_skills/` | ✅ 16 bundled skills |
| `KiroCrew/src/kiro_crew/apps/builtins/` | ✅ 20 builtin apps (incl. `mochi`) |
| `KiroCrew/src/kiro_crew/config/` | ✅ config files |

**Note:** KPKopanion lives at `E:/Programming/AI/KPKopanion` — OUTSIDE the
project root. The file tools (`read_file`/`list_directory`) are scoped to
`KiroCrew/`, so KPKopanion sources must be read via the terminal
(`cat`/`sed` on `E:/Programming/AI/KPKopanion/...`).

---

## 2. KiroCrew insertion points

### 2a. `src/kiro_crew/security.py` — keystone sensitive paths

**`_SENSITIVE_HOME_DIRS`** — `list[str]` of `$HOME`-relative path strings.
Defined at **L4036–4079**. Entries are plain strings like `".aws"`,
`".ssh"`, `".local/share/kiro-cli"`. The list is later extended by
`_CREW_SECRET_LEAVES` (see below). **Insertion point for Phase 1:** add
`personality_dials.json` and `personality_feedback.json` here (or, better, as
leaves — see below).

**`_CREW_HOME_PREFIXES`** — `tuple[str, ...] = (".kiro/crew", ".kirocrew")` at
**L4118**. Every leaf is expanded under BOTH prefixes so a secret is gated
whether the data home is the current `~/.kiro/crew` or the legacy `~/.kirocrew`.

**`_CREW_SECRET_LEAVES`** — `list[str]` at **L4119–4240**. These are file
*leaves* (relative to the crew data home), e.g. `".env"`,
`"security_policy.json"`, `"profiles"`, `"computer_use.json"`,
`"denied_commands.json"`, `"token_signing.key"`. **This is the recommended
insertion point for Phase 1** — add `"personality_dials.json"` and
`"personality_feedback.json"` to this list so they are gated under BOTH
`~/.kiro/crew` and `~/.kirocrew`. The list is consumed at **L4241–4243**:

```python
_SENSITIVE_HOME_DIRS += [
    f"{prefix}/{leaf}" for prefix in _CREW_HOME_PREFIXES for leaf in _CREW_SECRET_LEAVES
]
```

**`is_sensitive_path(path_str: str, base_dir: str | None = None) -> bool`** at
**L4764–4773**. Read+write gate over `_SENSITIVE_HOME_DIRS`. Used across every
file-access surface. `is_sensitive_write_path` (L4824) adds
`_WRITE_PROTECTED_HOME_PATHS` (write-only). `sensitive_home_dirs()` (L4830)
returns a read-only tuple view.

**Security invariant (do NOT weaken):** the agent must not be able to read OR
write the files that control its own behavior. Adding the personality files to
`_CREW_SECRET_LEAVES` is what makes the dials operator-controlled. The app's
own backend opens these files directly (NOT via `is_sensitive_path`), so real
functionality is unaffected.

### 2b. `src/kiro_crew/vector_memory.py` — semantic memory prefixes

**`_BUILTIN_PREFIXES`** — `list[str]` at **L156–161**:

```python
_BUILTIN_PREFIXES = [
    "pref.*",
    "project.*",
    "user.*",
    "lesson.*",
]
```

**Insertion point for Phase 2:** add `"people.*"` here. This is the one-line
core change the roadmap calls for. The list is copied into
`VectorMemoryStore._prefixes` in `__init__` (**L363–373**), which also accepts
`extra_prefixes`.

**Key structure:** semantic keys are dot-separated (`pref.general`,
`project.<name>.tool`, `user.*`, `lesson.*`). `_KEY_PATTERN` enforces the
format; `_validate_key` (L480) rejects `..` and over-long keys.

**Semantic CRUD functions** (class `VectorMemoryStore`):
- `get_semantic(key)` — **L547**
- `get_all_semantic(limit, offset)` — **L554**
- `set_semantic(key, value, confidence, source)` — **L571** (full validation pipeline; returns `(code, msg)` or `None`)
- `set_semantic_if_absent(...)` — **L597**
- `_write_semantic(...)` — **L633** (conflict resolution + upsert)
- `delete_semantic(key, source)` — **L741**
- `search_semantic(prefix)` — **L821** (by key prefix)
- `get_semantic_context(query_text, cap)` — **L830** (prompt injection, hybrid retrieval)
- `validate_semantic(...)` — **L494** (allowlist check via `_matches_allowlist` L490)

**Validation:** `validate_semantic` rejects keys not matching an allowed prefix
(`SemanticRejectCode.ALLOWLIST`), low-confidence writes, oversized values, and
prompt-injection content (`_contains_injection`). People data written as
`people.*` keys automatically inherits this screening — no new security work.

### 2c. `src/kiro_crew/agent.py` + `src/kiro_crew/context.py` — prompt loading & assembly

**`agent.py` prompt path resolution:**
- `_shipped_prompt()` — **L229** (returns `config/prompt.md`, prefers project-dir override)
- `_user_prompt_path()` — **L250** (returns `~/.kiro/crew/prompt.md`)
- `_prompt_path(mode="")` — **L649–671**. Returns the user prompt if it exists, else the shipped prompt. `mode="orchestrator"` selects the orchestrator prompt. **This is the override mechanism** — a user `~/.kiro/crew/prompt.md` wins over the shipped `config/prompt.md`.
- `build_agent_config` sets `config["prompt"] = f"file://{_prompt_path()}"` at **L1508**; `_refresh_dynamic_fields` at **L1545**.

**`context.py` — the ACTUAL runtime prompt assembly** (this is where the
personality block must be injected, NOT `context_blocks.py`):

> ⚠️ **Correction to the task brief:** `context_blocks.py` is a *classifier* that
> attributes assembled-prompt characters to blocks (markers like `[Memory`,
> `[Semantic Memory`, `[Skills:]`). It does NOT assemble the prompt. The
> assembler is `ContextBuilder` in `context.py`.

- `ContextBuilder._substitute_bot_name(prompt)` — **L1367** (replaces `{bot_name}`)
- `ContextBuilder._resolve_prompt_templates(prompt, session_key)` — **L1371–1521**. **THE insertion point for Phase 1.** This is where `{{MAX_SUBAGENTS}}` (L1381), `{{VERBOSITY_BLOCK}}` (L1485), and `{{WIDGET_BLOCK}}` (L1491/1521) are resolved. Add a `{personality_block}` resolution here, reading the dials from `~/.kiro/crew/personality_dials.json` (opened directly, not via `is_sensitive_path`).
- `ContextBuilder._load_agent_prompt(agent)` — **L1523** (custom agents)
- `ContextBuilder.build_session_context(...)` — **L1558–1959**. Assembles session-start context blocks (critical rules, date, agent identity, user profile, workspace, docs, skills, memory, lessons) into `parts: list[str]` and joins them. A personality block could also be appended here.
- `ContextBuilder.build_message(...)` — **L1959+**. Loads the agent prompt at **L2034–2053** (`_prompt_path(mode=mode).read_text()`), then at **L2054–2059**:
  ```python
  if agent_prompt:
      agent_prompt = self._resolve_prompt_templates(agent_prompt, session_key or "")
      agent_prompt = self._substitute_bot_name(agent_prompt)
      parts.append(f"[AGENT SYSTEM PROMPT]\n{agent_prompt}\n[END AGENT SYSTEM PROMPT]\n\n")
  ```
  The prompt is then sent to the ACP backend as part of the assembled message.

**`context_blocks.py`** — if a new `[PERSONALITY]` block is added to the
assembly, add a matching `("personality", r"\[PERSONALITY")` entry to
`_MARKERS` at **L33–66** so the classifier attributes it correctly (otherwise it
lands in `unclassified`).

### 2d. `src/kiro_crew/mcp_core.py` — MCP tool registration

**Registration pattern** (two parts):
1. **Schema + description** in `_list_tools()` — returns `list[dict]` with
   `name`, `description`, `inputSchema`. Example `learn_add` at **L597–640**.
   Schemas are defined as `*_SCHEMA` objects (e.g. `LEARN_ADD_SCHEMA`, imported
   at L103) and validated with `validate_tool_args(args, SCHEMA)`.
2. **Handler** in `_call_tool_inner()` — `if name == "learn_add":` at **L3976**.
   The handler validates args, enforces governance, calls a gateway endpoint via
   `_post("/api/lessons", payload)`, and returns a string result.

**`_resolve_session_key_strict()`** — **L2478–2535**. Resolves session identity
refusing PID-walked/unsigned identities. Accepts (0) gateway-injected per-call
`kirocrew.caller` context, (1) `KIROCREW_SESSION_KEY` env var, (2)
`KIROCREW_HOST_PID` → `session_pid_<pid>.txt` only when the HMAC sidecar
verifies. Returns `""` when only the `/proc` ancestor walk would match.

**Mutating-tool pattern (STRICT resolver):** `monitor_start` (L5454),
`monitor_update` (L5502), `autonudge_stop` (L5397), `set_project` all call
`_resolve_session_key_strict()` because they mutate persistent state and a
subagent must not PID-walk into the parent's identity. **The new
`personality_feedback` tool MUST follow this pattern** (per roadmap §2 security
checklist). Note: `learn_add` (L3976) uses the *lenient* `_resolve_session_key()`
— do not copy that for a mutating personality tool.

**Server entry:** `run_mcp_core_server` at **L6321** calls
`run_mcp_stdio_loop("kirocrew-core", "1.0.0", _list_tools, _call_tool, ...)`.

### 2e. `src/kiro_crew/config/prompt.md` — default system prompt

Read in full. Structure: `Output Format` (L3), `KiroCrew Capabilities` (L19),
`Subagent Orchestration` (L29), `Rules` (L66), `Wait & Webhook Tools` (L86),
`Browser` (L131), `Computer Use` (L161). Uses placeholders `{bot_name}` (L1),
`{{MAX_SUBAGENTS}}` (L59), `{{VERBOSITY_BLOCK}}`, `{{WIDGET_BLOCK}}`.

**`{personality_block}` insertion point:** a natural spot is after the `## Rules`
section (L66) or near the top after the identity line (L1), following the
existing placeholder convention. The reference template
`config/prompt-personality.md` documents the intended block.

---

## 3. KPKopanion source modules to port

All under `E:/Programming/AI/KPKopanion/runtime/app/`.

### Phase 1 — Adaptive Personality

**`ethics/behavior_dials.py`** (256 lines) → `src/kiro_crew/personality/dials.py`
- `DialDefinition` dataclass: `dial_id, name, description, default_value=3, min_value=1, max_value=5, category, emoji`. `to_prompt_fragment(value)` → `"- {emoji} {name}: {value}/5 ({description})"`.
- `BUILT_IN_DIALS`: 8 dials — formality, verbosity, humor, empathy, proactivity, encouragement, coaching, creativity.
- `BehaviorDials` class: per-user + global values; `get_value/set_value/get_all_values/build_prompt_section`; `adjust_from_correction(dial_id, direction)` (self-adjust ±1, clamped 1–5); `process_correction_message` (uses classifier); `load/save` to `data/ethics/dials.json`.
- **Port notes:** emojis in `DialDefinition.emoji` violate KiroCrew's no-emoji-in-UI rule — drop or replace. Persistence must move to `~/.kiro/crew/personality_dials.json` (keystone-protected). `process_correction_message` imports the classifier — keep that coupling or drop it.

**`self_evolution/feedback.py`** (297 lines) → `src/kiro_crew/personality/feedback.py`
- Constants: `FEEDBACK_SAMPLE_RATE=0.10`, `ADJUSTMENT_THRESHOLD=5`, `NUDGE_AMOUNT=0.05`.
- `FeedbackEntry` dataclass: `timestamp, session_id, interaction_summary, rating (None/True/False), personality_at_time, adjusted`.
- `FeedbackCollector`: `should_ask_for_feedback` (~10% sampling, never twice/session, ≥5 interactions apart); `record_interaction` (returns the "was that helpful?" prompt); `record_feedback`; `get_stats`; `get_adjustment_suggestions`; `_maybe_adjust_personality` (nudges warmth/initiative down after ≥5 negative); persistence to `data/feedback/feedback.json`.
- **Port notes:** the "was that helpful?" string is the feedback-loop trigger. Persistence → `~/.kiro/crew/personality_feedback.json`. The adjustment heuristic should run as a cron/autonudge lane (out-of-band), not in-band.

**`personality.py`** (162 lines) → informs `~/.kiro/crew/prompt.md` (hand-written block)
- `PersonalityConfig` (warmth/humor/formality/initiative/verbosity, 0.0–1.0 sliders).
- `build_personality_prompt(personality)` — slider→natural-language mapping with 4 tiers per slider. This is the *text* the dials module injects; the roadmap says write it by hand into the prompt block.

**`classification/classifier.py`** (397 lines) → `src/kiro_crew/personality/tone.py` (optional)
- `IntentCategory` enum (question/request/emotional/reflection/command/greeting/small_talk/feedback/unknown).
- `IntentResult`, `ResponseStyle` dataclasses.
- `IntentClassifier.classify(message)` — regex pattern matching (emotional, correction, command, question, greeting, reflection, feedback, small-talk). `_detect_correction` maps words → dials (e.g. "formal"→formality, "verbose"→verbosity). `get_response_style` returns per-intent modifiers.
- **Port notes:** optional/deferrable per roadmap. Pure regex, no model id.

### Phase 2 — People Relationship Graph

**`people/registry.py`** (~300 lines) → semantic memory `people.*` keys
- `Person` dataclass: `id, name, aliases, relationship, bio, attributes, tags, last_interaction, interaction_count, created_at, updated_at`.
- `PersonRegistry`: JSON persistence per network; CRUD; `resolve_alias` (exact/alias/substring); `search`; `extract_from_text` (heuristic person extraction); `record_interaction`.
- **Port notes:** roadmap maps this to `people.<name>.name`, `people.<name>.aliases`, `people.<name>.attributes` semantic keys — no new table, use `vector_memory.py` `set_semantic`/`get_semantic`.

**`people/dossier.py`** (~250 lines) → `builtin_skills/people-memory/SKILL.md`
- `PersonDossier` dataclass; `DossierManager.generate_dossier` (compiles facts, relationship summary, sentiment from journal); `generate_summary` ("what do you know about X?"); `generate_context_blob` (compact `[Person: X] | Alias: ... | Rel: ... | Facts: ...` for prompt injection).

**`people/relationship_map.py`** (~300 lines) → `people.<name>.relationships` semantic keys
- `RELATIONSHIP_TYPES` tuple (25 types); `Relationship` dataclass (person_a/b, type, label, strength, notes); `RelationshipGraph` (nodes/edges); `RelationshipMap` CRUD, `find_relationship`, `get_relationships_for_person`, `build_graph`, `build_family_tree`.

**`people/sentiment_timeline.py`** (~300 lines) → defer/simplify
- `EMOTION_LEXICON` (word→score map); `SentimentPoint`, `PersonSentiment` dataclasses; `SentimentTimeline.analyze` (scores journal entries mentioning a person, blends with mood, detects trend); `detect_mentions`; `_score_text`/`_score_to_label`/`_detect_trend`.
- **Port notes:** keyword-based; roadmap says rebuild as episodic-memory queries + periodic cron, or defer. Emojis in labels violate the no-emoji rule.

### Phase 3 — Gamification

**`adventure/engine.py`** (~700 lines) → `apps/builtins/quests/`
- `QuestObjective`, `Quest` dataclasses (id, title, description, objectives, xp_reward, status, deadline, participants, source_type/id, flavor_text, timestamps).
- `AdventureEngine`: JSON persistence; CRUD; lifecycle (`start_quest`, `complete_quest` → awards XP + flavor text, `fail_quest`); generation (`generate_from_goals`, `generate_from_habits`, `generate_quests`); `_generate_flavor_text`; `get_stats`.
- **Port notes:** `complete_quest` calls `self._xp_engine.award_xp(...)` with signature `(network_id, amount, category, reason)` — NOTE this differs from `xp_engine.award_xp(user_id, category, network_id)`; the two are not wired consistently in KPKopanion. The port must reconcile this.

**`adventure/models.py`** — ⚠️ **DOES NOT EXIST.** The task brief references it,
but the `adventure/` dir contains only `engine.py`, `lore.py`, `minds.py`.
`Quest`/`QuestObjective` are defined in `engine.py`. The roadmap's
`quests/models.py` should be built from `engine.py`'s dataclasses.

**`adventure/lore.py`** — thematic flavor: `THEME_TEMPLATES` (fantasy/sci-fi
completion text, quest prefixes, level titles); `AdventureLore` class.

**`adventure/minds.py`** — `QuestSuggestion`, `DetectedPattern` dataclasses;
`AdventureMinds` (pattern-based quest suggestions). Lower priority.

**`gamification/xp_engine.py`** (~470 lines) → `quests/score.py`, `quests/levels.py`
- `XP_REWARDS` dict (goal_completed=50, journal_entry=10, daily_login=5, streak_day=20, reminder_completed=15, habit_logged=5, helping_other=30, skill_practice=25, conversation=1).
- `LEVEL_TITLES` list of `(threshold, title)`.
- `XPState` dataclass; `XPEngine`: `award_xp(user_id, category, network_id)`, `update_streak`, `get_state`, `_calculate_level`, JSON persistence.

**`gamification/skill_tracker.py`** — `Skill` dataclass; `SkillTracker` (add_skill, log_practice with streak logic).

**`gamification/family_achievements.py`** — `Achievement`, `AchievementProgress` dataclasses; `BUILT_IN_ACHIEVEMENTS` (8); `FamilyAchievements` (record_progress → unlock detection).

---

## 4. The mochi app pattern (template for Phase 3 `quests` app)

`src/kiro_crew/apps/builtins/mochi/` is the house-style companion app. Structure:

- **`app.json`** — manifest: `name`, `version`, `displayName`, `description`, `author: "kirocrew"`, `tags`, `highlights`, `defaultEnabled: false` (opt-in), `platform`, `permissions` (api routes, storage, events, cron, spawn), `ui.pages` (route/label/icon), `backend.hooks` (`on_startup`/`on_shutdown`), `backend.routes` (`backend.routes:register_routes`), `agents` (agent JSON files), `skills`, `mcpServers` (`kirocrew app mcp <name>`).
- **`__init__.py`** — re-exports `register_routes` from `backend/routes.py` (required: `dashboard/server.py` checks `hasattr(_mod, "register_routes")`).
- **`backend/routes.py`** — aiohttp routes under `/api/apps/mochi/*`, same-origin authed, deny-by-default (403 when disabled, 503 when runtime down).
- **`soul_loader.py`** — persona system: `DEFAULT_SOUL` + `BUILTIN_PERSONAS` (per appearance pack) + `SoulLoader` (config-over-default). `render_agent_prompt(pet_name, persona)` builds the identity header + packaged behaviour prompt; `write_agent_prompts(data_dir, ...)` renders into the app data dir with `atomic_write(..., mode=0o600)`.
- **`stats_service.py`** — streaks/milestones: `CompanionStats` dict, `create_default_stats`, `merge_stats`, `parse_stats_json` (corruption → defaults, never throws), `_synchronized` (RLock decorator), `StatsService` (load/reset/tick/flush/save, `on_milestone` callback, `record_*` methods). This is the closest existing analog to gamification XP/level logic.
- **`mcp_server.py`** — the app's own stdio MCP server (`run_mcp_stdio_loop`), tools confined to the app's data dir, atomic file writes.
- **`agents/mochi.json`** — agent config: `name`, `description`, `model: "auto"`, `tools` (incl. `@mochi:mochi`), `includeMcpJson: false`.

**Phase 3 guidance:** build `apps/builtins/quests/` modeled on mochi — manifest
(`defaultEnabled: false`), a store (XP/level/quest persistence under
`~/.kiro/crew/apps/quests/`, already keystone-protected), a cron-driven quest
generator, an MCP tool to award XP, and a dashboard widget. No shell execution
in the generator (pure Python + memory).

---

## 5. MCP tool registration pattern (summary)

To add a new MCP tool (e.g. `personality_feedback` in Phase 1):

1. Define a `*_SCHEMA` (see `LEARN_ADD_SCHEMA` import at `mcp_core.py` L103) and
   validate with `validate_tool_args(args, SCHEMA)`.
2. Register in `_list_tools()` (L217+) — add a dict with `name`, `description`,
   `inputSchema` (see `learn_add` at L597–640).
3. Add a handler in `_call_tool_inner()` — `if name == "personality_feedback":`
   (see `learn_add` at L3976). For a MUTATING tool, call
   `_resolve_session_key_strict()` first and fail closed if it returns `""`.
4. Persist via a gateway endpoint (`_post("/api/...", payload)`) or directly to
   the keystone-protected file (opened directly, not via `is_sensitive_path`).
5. Return a string result (or `"Error: ..."`).

---

## 6. Prompt loading path (summary)

```
agent._prompt_path(mode)  [agent.py L649]
  → user ~/.kiro/crew/prompt.md if it exists, else shipped config/prompt.md
  → (mode="orchestrator" → prompt-orchestrator.md)
context.ContextBuilder.build_message  [context.py L1959]
  → reads prompt via _prompt_path (L2034/2049)
  → _resolve_prompt_templates(prompt, session_key)  [L1371]  ← ADD {personality_block} HERE
  → _substitute_bot_name(prompt)  [L1367]
  → wraps in "[AGENT SYSTEM PROMPT] ... [END AGENT SYSTEM PROMPT]"
  → build_session_context(...)  [L1558] appends memory/skills/lessons blocks
  → assembled message sent to ACP backend (kiro-cli)
```

---

## 7. Deliverables created in Phase 0

- `src/kiro_crew/config/prompt-personality.md` — reference personality prompt
  template with `{personality_block}` placeholder, JARVIS-like persona, and the
  "was that helpful?" feedback-loop instruction.
- `INTEGRATION_BASELINE.md` — this document.

## 8. Issues / blockers

1. **KPKopanion is outside the project root** — must be read via terminal, not
   the file tools. Not a blocker, but a workflow constraint.
2. **`adventure/models.py` does not exist** — `Quest`/`QuestObjective` live in
   `adventure/engine.py`. The roadmap's `quests/models.py` should be built from
   `engine.py`.
3. **XP engine signature mismatch** — `engine.complete_quest` calls
   `xp_engine.award_xp(network_id, amount, category, reason)` but
   `xp_engine.award_xp(user_id, category, network_id)` is defined differently.
   The port must reconcile these.
4. **Emojis in KPKopanion sources** — `DialDefinition.emoji`, sentiment labels,
   and achievement emojis violate KiroCrew's no-emoji-in-UI rule. Must be
   stripped/replaced during port.
5. **`context_blocks.py` is a classifier, not the assembler** — the personality
   block must be injected in `context.py` `_resolve_prompt_templates`, and a
   matching `_MARKERS` entry added to `context_blocks.py` for correct
   attribution.
6. **`learn_add` uses the lenient resolver** — do NOT copy that for the new
   mutating `personality_feedback` tool; use `_resolve_session_key_strict()`.
