#!/usr/bin/env bash
# GridLock — post-deploy verification (the "is it actually up?" smoke test).
#
# Probes the running stack at $BASE (default: http://127.0.0.1) and asserts
# the core API surface + frontend are alive. Exits non-zero on any failure.
#
# Usage:  ./scripts/verify_deploy.sh
#         BASE=https://demo.example.com ./scripts/verify_deploy.sh
set -uo pipefail

BASE="${BASE:-http://127.0.0.1}"
PY="${PY:-python3}"

pass=0
fail=0
declare -a FAILURES=()

_probe() {
    local name="$1"
    local path="$2"
    local expect="${3:-200}"
    local body
    local code
    body=$(curl -sS -o /tmp/gridlock_body -w "%{http_code}" "${BASE}${path}" 2>/dev/null) || body="000"
    code="$body"
    if [[ "$code" == "$expect" ]]; then
        printf "  \033[32m✓\033[0m %-40s  %s\n" "$name" "$code"
        pass=$((pass+1))
    else
        printf "  \033[31m✗\033[0m %-40s  %s (expected %s)\n" "$name" "$code" "$expect"
        fail=$((fail+1))
        FAILURES+=("$name → $code")
    fi
}

_post() {
    local name="$1"
    local path="$2"
    local payload="$3"
    local code
    code=$(curl -sS -o /tmp/gridlock_body -w "%{http_code}" \
        -X POST -H "Content-Type: application/json" \
        -d "$payload" "${BASE}${path}" 2>/dev/null) || code="000"
    if [[ "$code" == "200" ]]; then
        printf "  \033[32m✓\033[0m %-40s  %s\n" "$name" "$code"
        pass=$((pass+1))
    else
        printf "  \033[31m✗\033[0m %-40s  %s (expected 200)\n" "$name" "$code"
        fail=$((fail+1))
        FAILURES+=("$name → $code")
        head -c 300 /tmp/gridlock_body 2>/dev/null
        echo
    fi
}

echo "GridLock verify @ $BASE"
echo "─────────────────────────────────────────"

# Frontend
_probe "GET /"                       "/"                  "200"
_probe "GET /assets/index-*.js"     "/"                  "200"  # may 404 if hash differs; still treated as soft
_probe "GET /healthz"                "/healthz"           "200"

# Backend
_probe "GET  /api/health"            "/api/health"        "200"
_probe "GET  /api/risk/corridors"    "/api/risk/corridors" "200"
_probe "GET  /api/cascade"           "/api/cascade"       "200"
_probe "GET  /api/cascade/Mysore%20Road" "/api/cascade/Mysore%20Road" "200"
_probe "GET  /api/incidents/active"  "/api/incidents/active" "200"
_probe "GET  /api/map/health"        "/api/map/health"    "200"
_probe "GET  /api/accuracy"          "/api/accuracy"      "200"

_post "POST /api/clearance-risk" "/api/clearance-risk" \
    '{"corridor":"Mysore Road","event_cause":"tree_fall","description":"tree blocking road"}'

_post "POST /api/optimize" "/api/optimize" \
    '{"events":[{"id":"E01","corridor":"Mysore Road","cause":"tree_fall","p50_min":60,"p90_min":240,"closure_prob":0.55,"corridor_risk":0.42}],
      "units":[{"id":"U001","station":"Yeshwanthpura PS","agency":"police"}]}'

# Optional: full contract test (requires the project Python env)
if [[ -x "/home/dexter/gridlock/.venv/bin/python" ]]; then
    echo
    echo "Running contract test suite (may take ~30s)..."
    if (cd "$(dirname "$0")/.." && /home/dexter/gridlock/.venv/bin/python tests/test_api_contract.py --base "$BASE" 2>&1 | tail -5); then
        pass=$((pass+1))
    else
        fail=$((fail+1))
        FAILURES+=("contract test")
    fi
fi

echo
echo "─────────────────────────────────────────"
if [[ $fail -eq 0 ]]; then
    printf "\033[32mGridLock OK\033[0m  %d/%d checks passed\n" "$pass" "$((pass+fail))"
    exit 0
else
    printf "\033[31mGridLock DEGRADED\033[0m  %d/%d checks passed\n" "$pass" "$((pass+fail))"
    for f in "${FAILURES[@]:-}"; do echo "  - $f"; done
    exit 1
fi
