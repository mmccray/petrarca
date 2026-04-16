#!/usr/bin/env bash
# Research API (:8090) + Expo Metro (:8081), both background — for automation / headless dev.
# Interactive one-terminal flow: use dev-all.sh instead (Metro foreground).

set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

lsof -ti :8090 2>/dev/null | xargs kill -9 2>/dev/null || true
lsof -ti :8081 2>/dev/null | xargs kill -9 2>/dev/null || true
sleep 1

echo "==> Starting research-server on :8090 (background)…"
bash "$ROOT/scripts/run-research-server.sh" &
SRV_PID=$!
sleep 4
if ! curl -sS -m 3 "http://127.0.0.1:${RESEARCH_PORT:-8090}/health" | grep -q ok; then
  echo "ERROR: research-server did not become healthy. Killing $SRV_PID"
  kill "$SRV_PID" 2>/dev/null || true
  exit 1
fi
echo "==> API ok (pid $SRV_PID). Starting Metro on :8081 (background)…"
cd "$ROOT/app"
npx expo start --port 8081 &
echo "    Metro started in background. Stop API: kill $SRV_PID  or  lsof -ti :8090 | xargs kill -9"
echo "    Stop Metro: lsof -ti :8081 | xargs kill -9"
