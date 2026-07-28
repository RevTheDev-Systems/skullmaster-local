#!/bin/zsh
# SkullMaster iQ launcher — starts the server if needed, then opens the UI.
set -u

PROJECT_DIR="$HOME/notebooklm-local"
URL="http://127.0.0.1:8501"
UV="/opt/homebrew/bin/uv"
[[ -x "$UV" ]] || UV="$(command -v uv)"

if ! curl -s -o /dev/null --max-time 2 "$URL/healthz"; then
  cd "$PROJECT_DIR" || exit 1
  mkdir -p data
  nohup "$UV" run uvicorn app.main:app --host 127.0.0.1 --port 8501 \
    >> data/server.log 2>&1 &
  # wait up to 30s for the server to come up
  for _ in {1..60}; do
    curl -s -o /dev/null --max-time 1 "$URL/healthz" && break
    sleep 0.5
  done
fi

open "$URL"
