# Kiro Crew — Getting Started Cheat Sheet

Quick reference for the setup on this machine (WSL2 + Windows-hosted Ollama +
Runware MCP + OpenAI-compatible provider). This documents how it works and the
commands you'll reach for.

## The stack

- **Agent model (chat):** OpenAI-compatible provider → your Windows-hosted Ollama
  (`http://localhost:11434/v1`). Default model `gemma4:cloud`. Swap per task
  (e.g. `deepseek-v4-flash:cloud` for coding).
- **Images / video / audio / 3D:** Runware MCP server (`@runware/mcp`) with the
  outcome skills loaded. Ask in plain language; the agent picks the skill +
  model.
- **Personality / quests / people-memory:** your fork's features, active.
- **Gateway:** `kirocrew gateway` → dashboard at `http://127.0.0.1:5476`.

## Starting the gateway

```bash
# from the repo root (/home/kpinc/projects/KPKiroCrew)
.venv/bin/kirocrew gateway
# opens the dashboard in your browser; add --no-open to skip
```

## Switching the Ollama model

> The provider is already enabled. You usually only change the **model**.

```bash
kirocrew config set agent.openai_compatible.model gemma4:cloud        # general
kirocrew config set agent.openai_compatible.model deepseek-v4-flash:cloud  # coding
```

Restart the gateway after a model change (the provider is selected at boot).

Useful models on this host (from `curl http://localhost:11434/v1/models`):

| Model | Use for |
|---|---|
| `gemma4:cloud` | Default — general assistant |
| `deepseek-v4-flash:cloud` | Fast coding |
| `deepseek-v4-pro:cloud` | Heavier coding/reasoning |
| `kimi-k2.6:cloud` / `kimi-k2.7-code:cloud` | Long-context / coding |
| `qwen3.5:latest` | Local (no cloud) fallback |
| `nomic-embed-text:latest` | Embeddings (local) |

## Turning the OpenAI provider off (back to Bedrock)

```bash
kirocrew config set agent.openai_compatible.enabled false
# restart gateway
```

## Runware (image/video/audio)

Just ask, e.g. "make a launch video from this product photo", "restore this
photo", "generate a voiceover". The agent:
1. Loads the matching outcome skill (e.g. `product-ad-video`, `voiceover`)
2. Picks a live model via `runware-models`
3. Calls the Runware MCP `run` tool and returns the result.

Skills live in `~/.kiro/crew/skills/` (33 Runware skills installed).

> The `@runware/mcp` npm package has an upstream ESM bug; we use a patched copy
> at `/home/kpinc/.runware-mcp/patched/`. Config: `~/.kiro/settings/mcp.json`.

## Useful files

| File | What it is |
|---|---|
| `~/.kiro/settings/mcp.json` | Runware MCP server + API key (600 perms) |
| `~/.kiro/crew/config.json` | Agent config (model, provider, approval) |
| `~/.kiro/crew/prompt.md` | (moved aside) personality — now shipped `config/prompt.md` |
| `~/.kiro/crew/skills/` | All skills incl. the 33 Runway ones |
| `/home/kpinc/.runware-mcp/patched/` | Patched `@runware/mcp` bridge |
