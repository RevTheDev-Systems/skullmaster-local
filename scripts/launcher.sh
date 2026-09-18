#!/usr/bin/env bash
# SkullMaster iQ launcher — starts the server if needed, then opens the UI.
#
# Cross-platform (macOS / Linux). Host and port come from app.config (env +
# .env), so the launcher, the server it starts, and the health check all agree
# on a single source of truth instead of repeating hardcoded addresses.
#
#   ./scripts/launcher.sh
#   SKULLMASTER_NO_OPEN=1 ./scripts/launcher.sh    # start but don't open a browser
#   SKULLMASTER_DRY_RUN=1 ./scripts/launcher.sh    # print resolved values, exit
set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="${SKULLMASTER_HOME:-$(dirname "$SCRIPT_DIR")}"

find_uv() {
  if command -v uv >/dev/null 2>&1; then command -v uv; return 0; fi
  for candidate in /opt/homebrew/bin/uv "$HOME/.local/bin/uv" /usr/local/bin/uv; do
    if [ -x "$candidate" ]; then printf '%s' "$candidate"; return 0; fi
  done
  return 1
}

UV="$(find_uv || true)"
if [ -z "${UV:-}" ]; then
  echo "uv is not installed. See https://docs.astral.sh/uv/getting-started/installation/" >&2
  exit 1
fi

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

if [ "${SKULLMASTER_DRY_RUN:-0}" = "1" ]; then
  echo "project: $PROJECT_DIR"
  echo "uv:      $UV"
  echo "url:     $URL"
  exit 0
fi

if ! curl -s -o /dev/null --max-time 2 "$URL/healthz"; then
  mkdir -p data
  nohup "$UV" run python -m app >> data/server.log 2>&1 &
  # wait up to 30s for the server to come up
  for _ in $(seq 1 60); do
    curl -s -o /dev/null --max-time 1 "$URL/healthz" && break
    sleep 0.5
  done
fi

if [ "${SKULLMASTER_NO_OPEN:-0}" = "1" ]; then
  echo "SkullMaster iQ is running at $URL"
  exit 0
fi

if command -v open >/dev/null 2>&1; then
  open "$URL"
elif command -v xdg-open >/dev/null 2>&1; then
  xdg-open "$URL"
else
  echo "SkullMaster iQ is running at $URL"
fi
