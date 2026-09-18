#!/usr/bin/env bash
# SkullMaster iQ setup — idempotent and non-destructive.
#
# Checks prerequisites, installs Python dependencies with uv, creates .env from
# the example if absent (never overwrites an existing one), and optionally pulls
# the configured Ollama models. Re-running it is safe.
#
#   ./scripts/setup.sh
#   SKULLMASTER_SKIP_MODELS=1 ./scripts/setup.sh   # skip model downloads
#   SKULLMASTER_OCR=1 ./scripts/setup.sh           # also install OCR extras
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO"

say() { printf '%s\n' "$*"; }
have() { command -v "$1" >/dev/null 2>&1; }

say "SkullMaster iQ setup — $REPO"
say ""

# --- 1. uv -----------------------------------------------------------------
if ! have uv; then
  say "ERROR: 'uv' is required."
  say "Install it from https://docs.astral.sh/uv/getting-started/installation/ and re-run."
  exit 1
fi
say "uv: $(uv --version)"

# --- 2. Python 3.12 --------------------------------------------------------
uv python install 3.12 >/dev/null 2>&1 || true

# --- 3. dependencies -------------------------------------------------------
say "Installing dependencies (uv sync)..."
uv sync
if [ "${SKULLMASTER_OCR:-0}" = "1" ]; then
  say "Installing OCR extras (pytesseract + pillow)..."
  uv sync --extra ocr
fi

# --- 4. .env ---------------------------------------------------------------
if [ ! -f .env ]; then
  cp .env.example .env
  say "Created .env from .env.example (edit it to change models)."
else
  say ".env already exists — leaving it untouched."
fi

env_value() {  # read KEY from .env, strip inline comments/whitespace
  grep -E "^$1=" .env 2>/dev/null | head -1 | cut -d= -f2- | sed 's/[[:space:]]*#.*//; s/[[:space:]]*$//' || true
}
CHAT_MODEL="$(env_value CHAT_MODEL)"; CHAT_MODEL="${CHAT_MODEL:-qwen3:4b}"
EMBED_MODEL="$(env_value EMBED_MODEL)"; EMBED_MODEL="${EMBED_MODEL:-nomic-embed-text}"

# --- 5. Ollama models (optional) ------------------------------------------
if ! have ollama; then
  say ""
  say "NOTE: Ollama is not installed. Install it from https://ollama.com/download"
  say "      then run: ollama pull $EMBED_MODEL && ollama pull $CHAT_MODEL"
elif [ "${SKULLMASTER_SKIP_MODELS:-0}" = "1" ]; then
  say "Skipping model pull (SKULLMASTER_SKIP_MODELS=1)."
else
  do_pull=1
  if [ -t 0 ]; then
    printf 'Pull Ollama models now (%s, %s)? This can be a multi-GB download. [Y/n] ' "$CHAT_MODEL" "$EMBED_MODEL"
    read -r answer || true
    case "${answer:-y}" in [Nn]*) do_pull=0 ;; esac
  fi
  if [ "$do_pull" = "1" ]; then
    say "Pulling $EMBED_MODEL ..."
    ollama pull "$EMBED_MODEL" || say "WARN: could not pull $EMBED_MODEL (is Ollama running?)"
    say "Pulling $CHAT_MODEL ..."
    ollama pull "$CHAT_MODEL" || say "WARN: could not pull $CHAT_MODEL (is Ollama running?)"
  fi
fi

# --- 6. done ---------------------------------------------------------------
say ""
say "Setup complete. Next steps:"
say "  uv run python -m app              # then open http://127.0.0.1:8501"
say "  uv run python -m app.diagnostics  # health report"
say ""
say "Optional capabilities (app reports them as WARN when absent):"
say "  ffmpeg                     -> Video Overviews"
say "  tesseract + SKULLMASTER_OCR=1 setup -> scanned-PDF OCR"
say "  ollama pull qwen2.5vl:7b   -> diagrams/charts/images (vision)"
