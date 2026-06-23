#!/bin/sh
# GridLock — single-container entrypoint for Heroku (Eco dyno).
#
# Starts uvicorn (FastAPI backend) in the background, waits for its
# /api/health endpoint, then execs nginx in the foreground. Heroku's
# dyno manager requires the named process (`web`) to be the foreground
# process, so nginx has to be PID 1 from Heroku's perspective.
#
# Why background uvicorn + foreground nginx:
#   - nginx serves the SPA on $PORT (Heroku-assigned) and reverse-proxies
#     /api/* + WebSocket to uvicorn on 127.0.0.1:8000
#   - if we ran nginx in the background, the dyno would think the
#     process exited and restart the container
set -eu

# Recover artifacts if mounted volume is empty (e.g. Azure Container App File Share mount)
if [ -d "/app/artifacts_bak" ]; then
    echo "[entrypoint] Checking artifacts in /app/artifacts..."
    mkdir -p /app/artifacts
    if [ ! -f "/app/artifacts/context.pkl" ]; then
        echo "[entrypoint] Mounted volume is missing artifacts. Copying from backup..."
        cp -rp /app/artifacts_bak/* /app/artifacts/
        echo "[entrypoint] Artifacts restored successfully."
    else
        echo "[entrypoint] Artifacts already exist in /app/artifacts."
    fi
fi

# Default PORT (Heroku always sets this; the default is for local Docker use)
: "${PORT:=80}"

# Substitute ${PORT} in the nginx config template into the live config.
# Heroku's env has many vars; envsubst is the standard way to template
# a single one without leaking the rest into the file.
envsubst '${PORT}' < /etc/nginx/conf.d/gridlock.conf.template \
    > /etc/nginx/conf.d/default.conf

# Optional: log level from env
: "${GRIDLOCK_LOG_LEVEL:=info}"

echo "[entrypoint] starting uvicorn on 127.0.0.1:8000 (log_level=$GRIDLOCK_LOG_LEVEL)"
uvicorn api.main:app \
    --host 127.0.0.1 \
    --port 8000 \
    --log-level "$GRIDLOCK_LOG_LEVEL" \
    --workers 1 \
    > /tmp/uvicorn.log 2>&1 &
UVICORN_PID=$!

# Wait for the API to be ready (max ~30s)
echo "[entrypoint] waiting for /api/health ..."
for i in $(seq 1 60); do
    if wget -q -O- http://127.0.0.1:8000/api/health >/dev/null 2>&1; then
        echo "[entrypoint] uvicorn ready after ${i} polls"
        break
    fi
    if ! kill -0 "$UVICORN_PID" 2>/dev/null; then
        echo "[entrypoint] FATAL: uvicorn died, dumping log:"
        cat /tmp/uvicorn.log
        exit 1
    fi
    sleep 0.5
done

# Final check — if it still isn't ready, fail loud
if ! wget -q -O- http://127.0.0.1:8000/api/health >/dev/null 2>&1; then
    echo "[entrypoint] FATAL: uvicorn did not become ready in 30s"
    cat /tmp/uvicorn.log
    exit 1
fi

# Hand the dyno over to nginx (PID 1) — uvicorn keeps running in the background
echo "[entrypoint] exec nginx in foreground (pid=1 from here)"
exec nginx -g 'daemon off;'
