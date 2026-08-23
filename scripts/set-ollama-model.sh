#!/usr/bin/env bash
# Switch the OpenAI-compatible (Ollama) agent model.
#
# Usage:
#   ./scripts/set-ollama-model.sh gemma4:cloud          # default / general
#   ./scripts/set-ollama-model.sh deepseek-v4-flash:cloud  # coding
#   ./scripts/set-ollama-model.sh                         # show current model
#
# Then restart the gateway (the provider/model is resolved at boot):
#   pkill -f "kirocrew gateway"; .venv/bin/kirocrew gateway
set -euo pipefail

MODEL="${1:-}"

if [ -z "$MODEL" ]; then
  kirocrew config get agent.openai_compatible.model 2>/dev/null \
    | grep -vE "deprecated|telegram" | tail -1
  echo "Tip: pass a model name to switch, e.g. ./scripts/set-ollama-model.sh deepseek-v4-flash:cloud"
  exit 0
fi

# Ensure the OpenAI-compatible provider is on.
kirocrew config set agent.openai_compatible.enabled true >/dev/null 2>&1 || true
kirocrew config set agent.openai_compatible.base_url "http://localhost:11434/v1" >/dev/null 2>&1 || true
kirocrew config set agent.openai_compatible.model "$MODEL" >/dev/null 2>&1 || true

echo "✅ model set to: $MODEL"
echo "   (Restart the gateway for it to take effect: pkill -f 'kirocrew gateway'; .venv/bin/kirocrew gateway)"
