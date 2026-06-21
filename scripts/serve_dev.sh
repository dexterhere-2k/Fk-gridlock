#!/usr/bin/env bash
# GridLock — local dev runner (no Docker).
# Starts the FastAPI backend on :8000 and the Vite dev server on :5173.
#
# Usage:  ./scripts/serve_dev.sh
#         ./scripts/serve_dev.sh api        # backend only
#         ./scripts/serve_dev.sh frontend   # frontend only
set -euo pipefail
cd "$(dirname "$0")/.."

# Pick the venv python if available
if [[ -x "/home/dexter/gridlock/.venv/bin/python" ]]; then
    PY="/home/dexter/gridlock/.venv/bin/python"
elif command -v python3.11 >/dev/null 2>&1; then
    PY="python3.11"
else
    PY="python3"
fi

target="${1:-all}"

case "$target" in
    api|all)
        echo "[gridlock] starting backend on :8000 ($PY)"
        "$PY" -m uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload &
        API_PID=$!
        trap 'kill $API_PID 2>/dev/null || true' EXIT
        sleep 4
        if ! curl -fsS http://127.0.0.1:8000/api/health >/dev/null; then
            echo "[gridlock] backend not ready yet (artifact warmup)"
        fi
        ;;
esac

if [[ "$target" == "frontend" || "$target" == "all" ]]; then
    if [[ "$target" == "all" ]]; then
        sleep 2
    fi
    echo "[gridlock] starting frontend on :5173"
    (cd frontend && npm install --silent && npm run dev)
fi
