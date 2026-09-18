#!/bin/zsh
# SkullMaster iQ launcher — starts the server if needed, then opens the UI.
#
# Host and port come from app.config (env + .env), so the launcher, the server
# it starts, and the health check all agree on a single source of truth instead
# of repeating hardcoded 127.0.0.1:8501 values.
set -u

# Resolve the repo root without assuming a fixed absolute location:
# `SKULLMASTER_HOME` overrides; otherwise use the parent of this script's own
# directory. This lets the repo live anywhere (and be renamed) without editing.
SCRIPT_DIR="${0:A:h}"
PROJECT_DIR="${SKULLMASTER_HOME:-${SCRIPT_DIR:h}}"
UV="/opt/homebrew/bin/uv"
[[ -x "$UV" ]] || UV="$(command -v uv)"

cd "$PROJECT_DIR" || exit 1

read -r BIND_HOST BIND_PORT < <("$UV" run python -c \
  'from app.config import HOST, PORT; print(HOST, PORT)' 2>/dev/null) || true
BIND_HOST="${BIND_HOST:-127.0.0.1}"
BIND_PORT="${BIND_PORT:-8501}"

# 0.0.0.0/:: mean "all interfaces" for the server, but the browser must use a
# loopback address.
case "$BIND_HOST" in
  0.0.0.0|::|"") OPEN_HOST="127.0.0.1" ;;
  *) OPEN_HOST="$BIND_HOST" ;;
esac
URL="http://${OPEN_HOST}:${BIND_PORT}"

if ! curl -s -o /dev/null --max-time 2 "$URL/healthz"; then
  mkdir -p data
  nohup "$UV" run python -m app >> data/server.log 2>&1 &
  # wait up to 30s for the server to come up
  for _ in {1..60}; do
    curl -s -o /dev/null --max-time 1 "$URL/healthz" && break
    sleep 0.5
  done
fi

open "$URL"
