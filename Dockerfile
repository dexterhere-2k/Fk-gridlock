# =============================================================================
# GridLock — multi-stage Dockerfile
#
# Stage 1: frontend  — Vite build of the React SPA → /app/dist
# Stage 2: api       — Python 3.11 runtime with ML artifacts
# Stage 3: frontend  — nginx serving the SPA + reverse-proxying /api/*
#
# Build:   docker build -t gridlock .
# Targets: --target api        (backend only, for dev)
#           --target frontend  (default; SPA + nginx)
# =============================================================================

# ============================================================================
# Stage 1 — Frontend build
# ============================================================================
FROM node:20-alpine AS frontend-build
WORKDIR /build

# Install deps first for better layer caching
COPY frontend/package.json frontend/package-lock.json* ./frontend/
RUN cd frontend && npm ci --no-audit --no-fund || npm install --no-audit --no-fund

# Build
COPY frontend/ ./frontend/
RUN cd frontend && npm run build

# ============================================================================
# Stage 2 — Backend runtime (also a build target)
# ============================================================================
FROM python:3.11-slim AS api

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# OS deps: build-essential is only needed for some wheels; libgomp for sklearn
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        libgomp1 \
        curl \
    && rm -rf /var/lib/apt/lists/*

# Python deps
COPY requirements.txt ./
RUN pip install -r requirements.txt

# Application code + trained artifacts + data
COPY src/        ./src/
COPY api/        ./api/
COPY tests/      ./tests/
COPY data/       ./data/
COPY artifacts/  ./artifacts/
COPY scripts/    ./scripts/
COPY README.md   ./
COPY requirements.txt ./

# Make the project root importable
ENV PYTHONPATH=/app

# Pre-warm the artifacts so the first request is fast (5s → ~0.5s).
# Best-effort: if it fails, the API still works (artifacts are loaded on lifespan).
RUN python -c "from src.api.service import Service; s = Service(); print('warmup ok', s.health())" \
    || echo "warmup skipped (artifacts will load on first request)"

EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=5s --start-period=15s --retries=5 \
    CMD python -c "import httpx; r = httpx.get('http://127.0.0.1:8000/api/health', timeout=4); r.raise_for_status()" \
    || exit 1

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]

# ============================================================================
# Stage 3 — Frontend (nginx + reverse proxy to api service)
# ============================================================================
FROM nginx:1.27-alpine AS frontend

# nginx config (reverse-proxies /api/* → http://api:8000)
RUN rm -f /etc/nginx/conf.d/default.conf
COPY nginx.conf /etc/nginx/conf.d/gridlock.conf

# Built SPA from stage 1
COPY --from=frontend-build /build/frontend/dist /usr/share/nginx/html

EXPOSE 80
HEALTHCHECK --interval=15s --timeout=4s --retries=3 \
    CMD wget -q -O- http://127.0.0.1/healthz || exit 1
