"""GridLock — 04: API contract smoke test.

Hits every endpoint (POST + GET) and asserts the response shape matches
the Pydantic schema in `src/api/schemas.py`. This is the spec 04
contract test that ensures every endpoint stays in sync with its
declared response model.

Run:
    pytest tests/test_api_contract.py -v
    # or just: python -m tests.test_api_contract
"""
from __future__ import annotations
import sys
import json
import time
from pathlib import Path

# Make `src` and project root importable
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import httpx  # noqa: E402
from pydantic import ValidationError  # noqa: E402

from src.api.schemas import (  # noqa: E402
    ClearanceRiskResponse, SimulateResponse, OptimizeResponse,
    CorridorRiskList, CascadeGraphResponse, CascadeDownstreamResponse,
    IncidentState, IncidentListResponse, DispatchResponse,
    ScheduleResponse, ExplainResponse, AccuracyResponse,
    LearningSignalResponse, RetrainResponse,
    DebriefResponse, HealthResponse,
    MapHealthResponse, GeoJsonResponse,
    ClearanceRiskRequest, SimulateRequest,
    OptimizeRequest, OptimizeEvent, OptimizeUnit, OutcomeRequest,
    DispatchRequest,
)


BASE = "http://127.0.0.1:8765"
TIMEOUT = 30.0


def _check(name: str, response_model, payload: dict) -> tuple[bool, str]:
    try:
        response_model.model_validate(payload)
        return True, f"OK {name}"
    except ValidationError as exc:
        return False, f"FAIL {name}: {exc}"


def run_checks() -> tuple[int, int]:
    """Spin the test client against a live uvicorn process. Returns (passed, total)."""
    passed = total = 0
    c = httpx.Client(base_url=BASE, timeout=TIMEOUT)

    # ---- /api/health
    r = c.get("/api/health")
    ok, msg = _check("GET /api/health", HealthResponse, r.json())
    print(msg)
    total += 1; passed += int(ok)
    assert r.status_code == 200, f"health: {r.status_code} {r.text}"

    # ---- POST /api/clearance-risk
    payload = {
        "corridor": "Mysore Road",
        "event_cause": "tree_fall",
        "veh_type": "lcv",
        "zone": "West Zone 1",
        "is_planned": False,
        "description": "huge tree fallen blocking the road crane needed",
        "datetime": "2024-04-01T05:00:00+05:30",
    }
    r = c.post("/api/clearance-risk", json=payload)
    ok, msg = _check("POST /api/clearance-risk", ClearanceRiskResponse, r.json())
    print(msg); total += 1; passed += int(ok)
    assert r.status_code == 200
    prediction_id = r.json().get("prediction_id")

    # ---- POST /api/simulate
    payload = {
        "corridor": "Tumkur Road",
        "event_cause": "vip_movement",
        "is_planned": True,
        "event_type": "planned",
        "description": "vip convoy",
        "datetime": "2024-04-01T15:00:00+05:30",
    }
    r = c.post("/api/simulate", json=payload)
    ok, msg = _check("POST /api/simulate", SimulateResponse, r.json())
    print(msg); total += 1; passed += int(ok)

    # ---- POST /api/optimize
    payload = {
        "events": [
            {"id": "E01", "corridor": "Mysore Road", "cause": "tree_fall",
             "p50_min": 60, "p90_min": 240, "closure_prob": 0.55,
             "corridor_risk": 0.42, "is_planned": False},
            {"id": "E02", "corridor": "Tumkur Road", "cause": "vip_movement",
             "p50_min": 40, "p90_min": 90, "closure_prob": 0.35,
             "corridor_risk": 0.13, "is_planned": True},
            {"id": "E03", "corridor": "ORR East 1", "cause": "accident",
             "p50_min": 50, "p90_min": 120, "closure_prob": 0.20,
             "corridor_risk": 0.27, "is_planned": False},
        ],
        "units": [
            {"id": f"U{i:03d}", "station": "Yeshwanthpura PS", "agency": "police"}
            for i in range(1, 21)
        ],
    }
    r = c.post("/api/optimize", json=payload)
    ok, msg = _check("POST /api/optimize", OptimizeResponse, r.json())
    print(msg); total += 1; passed += int(ok)

    # ---- POST /api/optimize/from-predictions
    r = c.post("/api/optimize/from-predictions", json=[
        {"corridor": "Mysore Road", "event_cause": "tree_fall",
         "description": "huge tree fallen blocking the road"},
        {"corridor": "Tumkur Road", "event_cause": "vip_movement",
         "is_planned": True, "event_type": "planned"},
    ])
    ok, msg = _check("POST /api/optimize/from-predictions", OptimizeResponse, r.json())
    print(msg); total += 1; passed += int(ok)

    # ---- GET /api/risk/corridors
    r = c.get("/api/risk/corridors")
    ok, msg = _check("GET /api/risk/corridors", CorridorRiskList, r.json())
    print(msg); total += 1; passed += int(ok)
    assert r.json()["n_corridors"] >= 20

    # ---- GET /api/cascade
    r = c.get("/api/cascade")
    ok, msg = _check("GET /api/cascade", CascadeGraphResponse, r.json())
    print(msg); total += 1; passed += int(ok)
    assert r.json()["n_edges"] >= 100

    # ---- GET /api/cascade/alerts/{corridor} (spec 08 #7)
    r = c.get("/api/cascade/alerts/Mysore%20Road")
    assert r.status_code == 200, f"cascade alerts: {r.status_code} {r.text}"
    body = r.json()
    assert body["corridor"] == "Mysore Road"
    assert body["n_alerts"] > 0, "expected cascade pre-alerts on trigger corridor"
    a = body["alerts"][0]
    assert {"primary", "secondary", "lag_minutes", "correlation",
            "cascade_risk_multiplier", "urgency", "honesty_note"} <= a.keys()
    print(f"OK GET /api/cascade/alerts/{{corridor}}  ({body['n_alerts']} alerts)")
    total += 1; passed += 1

    # ---- GET /api/cascade/{corridor}
    r = c.get("/api/cascade/Mysore Road")
    ok, msg = _check("GET /api/cascade/{corridor}", CascadeDownstreamResponse, r.json())
    print(msg); total += 1; passed += int(ok)

    # ---- POST /api/incidents (operator note only — backend auto-fills prediction)
    payload = {"id": "INC-test-1", "corridor": "Mysore Road",
               "cause": "tree_fall", "operator_note": "huge tree blocking road"}
    r = c.post("/api/incidents", json=payload)
    ok, msg = _check("POST /api/incidents", IncidentState, r.json())
    print(msg); total += 1; passed += int(ok)

    # ---- GET /api/incidents/active
    r = c.get("/api/incidents/active")
    ok, msg = _check("GET /api/incidents/active", IncidentListResponse, r.json())
    print(msg); total += 1; passed += int(ok)

    # ---- POST /api/incident/{id}/transition (valid — full 9-state machine)
    r = c.post("/api/incident/INC-test-1/transition",
               json={"target_state": "assigned"})
    ok, msg = _check("POST /api/incident/{id}/transition", IncidentState, r.json())
    print(msg); total += 1; passed += int(ok)

    # ---- POST /api/incident/{id}/transition (illegal: should 400)
    r = c.post("/api/incident/INC-test-1/transition",
               json={"target_state": "reported"})
    if r.status_code == 400:
        print("OK illegal-transition rejected (400)"); total += 1; passed += 1
    else:
        print(f"FAIL illegal-transition returned {r.status_code}"); total += 1

    # ---- POST /api/dispatch
    r = c.post("/api/dispatch", json={"incident_id": "INC-test-1"})
    ok, msg = _check("POST /api/dispatch", DispatchResponse, r.json())
    print(msg); total += 1; passed += int(ok)
    # spec 08 #3 — new dispatch shape assertions
    d = r.json()
    assert {"incident_corridor", "preferred_agency", "dispatched_unit_agency",
            "eta_source", "agency_match", "confidence",
            "alternatives", "because"} <= d.keys()
    assert d["eta_source"] in ("matrix", "haversine", "unknown")
    assert d["agency_match"] in ("exact", "police_fallback", "mismatch")
    assert d["confidence"] in ("high", "medium", "low")
    assert isinstance(d["alternatives"], list) and len(d["alternatives"]) <= 3
    print(f"     + ETA={d['estimated_eta_min']}min ({d['eta_source']}), "
          f"agency={d['agency_match']}, confidence={d['confidence']}, "
          f"{len(d['alternatives'])} alternatives")

    # ---- GET /api/schedule/{event_id}
    r = c.get("/api/schedule/PLANNED-1")
    ok, msg = _check("GET /api/schedule/{event_id}", ScheduleResponse, r.json())
    print(msg); total += 1; passed += int(ok)

    # ---- POST /api/outcome
    r = c.post("/api/outcome", json={
        "event_id": "INC-test-1", "actual_p50_min": 75.0,
        "actual_p90_min": 180.0, "actual_closure": False,
        "actual_officers_deployed": 8, "actual_barricades": 4,
        "notes": "demo outcome",
    })
    if r.status_code == 200 and r.json().get("status") == "logged":
        print("OK POST /api/outcome"); total += 1; passed += 1
    else:
        print(f"FAIL POST /api/outcome: {r.status_code} {r.text}"); total += 1

    # ---- GET /api/accuracy
    r = c.get("/api/accuracy")
    ok, msg = _check("GET /api/accuracy", AccuracyResponse, r.json())
    print(msg); total += 1; passed += int(ok)

    # ---- GET /api/learning/signal
    r = c.get("/api/learning/signal")
    ok, msg = _check("GET /api/learning/signal", LearningSignalResponse, r.json())
    print(msg); total += 1; passed += int(ok)

    # ---- GET /api/explain/{prediction_id}
    if prediction_id:
        r = c.get(f"/api/explain/{prediction_id}")
        ok, msg = _check("GET /api/explain/{prediction_id}", ExplainResponse, r.json())
        print(msg); total += 1; passed += int(ok)

    # ---- GET /api/debrief/{event_id}
    r = c.get("/api/debrief/INC-test-1")
    ok, msg = _check("GET /api/debrief/{event_id}", DebriefResponse, r.json())
    print(msg); total += 1; passed += int(ok)

    # ---- GET /api/map/health
    r = c.get("/api/map/health")
    body = r.json()
    # The `coverage` and `limitations` fields are part of the spec-07
    # honest-disclosure contract (not part of the Pydantic schema yet).
    ok = False
    try:
        MapHealthResponse.model_validate(body)
        ok = True
    except Exception:
        pass
    print(("OK " if ok else "FAIL ") + "GET /api/map/health")
    total += 1; passed += int(ok)
    # extra: the body must surface the 1/5 limitation
    if body.get("limitations") and len(body["limitations"]) >= 1:
        print(f"  + limitations disclosed: {len(body['limitations'])}")
        total += 1; passed += 1
    else:
        print(f"  - no limitations disclosed (expected ≥1, got {body.get('limitations')})")
        total += 1

    # ---- GET /api/map/risk-heatmap
    r = c.get("/api/map/risk-heatmap")
    ok, msg = _check("GET /api/map/risk-heatmap", GeoJsonResponse, r.json())
    print(msg); total += 1; passed += int(ok)

    # ---- GET /api/map/incidents
    r = c.get("/api/map/incidents?limit=20")
    ok, msg = _check("GET /api/map/incidents", GeoJsonResponse, r.json())
    print(msg); total += 1; passed += int(ok)

    # ---- GET /api/map/stations
    r = c.get("/api/map/stations")
    ok, msg = _check("GET /api/map/stations", GeoJsonResponse, r.json())
    print(msg); total += 1; passed += int(ok)

    # ---- POST /api/map/diversion
    r = c.post("/api/map/diversion",
               json={"origin_corridor": "Mysore Road",
                     "target_corridor": "Magadi Road"})
    if r.status_code == 200 and "polyline" in r.json():
        print("OK POST /api/map/diversion"); total += 1; passed += 1
    else:
        print(f"FAIL POST /api/map/diversion: {r.status_code} {r.text[:200]}"); total += 1

    # ---- POST /api/map/eta
    r = c.post("/api/map/eta",
               json={"origin_lat": 12.9858, "origin_lon": 77.5210,
                     "dest_lat": 12.9760, "dest_lon": 77.5180})
    if r.status_code == 200 and "eta_min" in r.json():
        print("OK POST /api/map/eta"); total += 1; passed += 1
    else:
        print(f"FAIL POST /api/map/eta: {r.status_code} {r.text[:200]}"); total += 1

    # ---- POST /api/map/nearest-station
    r = c.post("/api/map/nearest-station",
               json={"lat": 12.97, "lon": 77.59, "radius_m": 20000})
    if r.status_code == 200 and "results" in r.json():
        print("OK POST /api/map/nearest-station"); total += 1; passed += 1
    else:
        print(f"FAIL POST /api/map/nearest-station: {r.status_code} {r.text[:200]}"); total += 1

    print(f"\n{passed}/{total} checks passed")
    return passed, total


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--base", default=BASE,
                   help="API base URL (e.g. http://127.0.0.1:8000)")
    p.add_argument("--ready-url", default=None,
                   help="URL to poll for readiness (default: <base>/api/health)")
    p.add_argument("--timeout", type=int, default=60,
                   help="max seconds to wait for API readiness")
    args = p.parse_args()
    BASE = args.base
    TIMEOUT = args.timeout
    ready = args.ready_url or (BASE.rstrip("/") + "/api/health")
    import urllib.request
    t0 = time.time()
    while time.time() - t0 < TIMEOUT:
        try:
            urllib.request.urlopen(ready, timeout=2).read()
            break
        except Exception:
            time.sleep(0.5)
    else:
        print(f"API not ready at {ready} after {TIMEOUT}s")
        sys.exit(1)
    passed, total = run_checks()
    sys.exit(0 if passed == total else 1)
