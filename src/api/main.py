"""NexGen — FastAPI gateway for ASTraM intelligence.

Per spec 04, this is the ONE HTTP entry point for the entire system.
It wires the ML core (01), NLP layer (02), and ILP optimizer (03) behind
a uniform REST contract + a WebSocket for live corridor streams.

Run:
    uvicorn api.main:app --reload --port 8000
    # or equivalently:
    python -m uvicorn api.main:app --host 0.0.0.0 --port 8000
"""
from __future__ import annotations
import asyncio
import json
import os
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

# Make the project root importable when run as `api.main`
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.api.schemas import (  # noqa: E402
    ClearanceRiskRequest, ClearanceRiskResponse,
    SimulateRequest, SimulateResponse,
    OptimizeRequest, OptimizeResponse,
    CorridorRiskList,
    CascadeGraphResponse, CascadeDownstreamResponse,
    IncidentReport, IncidentState, IncidentListResponse,
    DispatchResponse,
    ScheduleResponse,
    ExplainResponse,
    AccuracyResponse,
    LearningSignalResponse, RetrainResponse,
    DebriefResponse,
    HealthResponse,
    DiversionRequest, EtaRequest, NearestStationRequest,
    GeoJsonResponse, MapHealthResponse,
    OptimizeEvent, OptimizeUnit,
)
from src.api.service import Service, ServiceError  # noqa: E402


# ============================================================================
# App factory (per spec 00 — supports `uvicorn api.main:app`)
# ============================================================================
def create_app() -> FastAPI:
    """Construct the FastAPI app with the standard middleware + routes."""
    service: Optional[Service] = None
    startup_error_traceback: Optional[str] = None

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        # ML is loaded on startup so the first /api/clearance-risk request
        # is fast (the cost is paid once). For a "potato" target, you can
        # lazy-load by removing the body of this `try` and calling
        # `Service()` in a request handler instead.
        nonlocal service, startup_error_traceback
        try:
            service = Service()
            print("[nexgen] service ready: 5 artifacts loaded, "
                  f"{service.health()['n_corridors']} corridors, "
                  f"{service.health()['n_cascade_edges']} cascade edges",
                  flush=True)
        except Exception as exc:
            import traceback
            startup_error_traceback = traceback.format_exc()
            print(f"[nexgen] service load FAILED: {exc}", flush=True)
            print(startup_error_traceback, flush=True)
            service = None

        # spec 08 #2 — background SLA ticker. Watches elapsed time
        # per active incident; on SLA breach, auto-bumps priority Low → High
        # and writes the reason to the ledger. Polls every 10s (cheap).
        sla_task = None
        if service is not None:
            async def _sla_ticker():
                while True:
                    try:
                        await asyncio.sleep(10.0)
                        breaches = service.sla_tick()
                        if breaches:
                            print(f"[sla-ticker] {breaches} new breaches",
                                  flush=True)
                    except asyncio.CancelledError:
                        return
                    except Exception as exc:
                        print(f"[sla-ticker] error: {exc}", flush=True)
                        await asyncio.sleep(2.0)
            sla_task = asyncio.create_task(_sla_ticker())
        try:
            yield
        finally:
            if sla_task is not None:
                sla_task.cancel()
                try:
                    await sla_task
                except (asyncio.CancelledError, Exception):
                    pass

    app = FastAPI(
        title="NexGen ASTraM Intelligence API",
        description=("Spec 04 — single FastAPI gateway for the ML core (01), "
                     "NLP layer (02), and ILP optimizer (03)."),
        version="0.1.0",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    def _svc() -> Service:
        if service is None:
            detail_msg = f"service load FAILED:\n{startup_error_traceback}"
            raise HTTPException(status_code=503, detail=detail_msg)
        return service

    # ----------------------------------------------------------------- /health
    @app.get("/api/health", response_model=HealthResponse, tags=["ops"])
    def health() -> dict:
        return _svc().health()

    # ----------------------------------------------------------------- predict
    @app.post("/api/clearance-risk", response_model=ClearanceRiskResponse,
              tags=["predict"])
    def clearance_risk(req: ClearanceRiskRequest) -> dict:
        try:
            return _svc().clearance_risk(req.model_dump(exclude_none=True))
        except ServiceError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"predict failed: {exc}")

    @app.post("/api/simulate", response_model=SimulateResponse, tags=["predict"])
    def simulate(req: SimulateRequest) -> dict:
        try:
            return _svc().simulate(req.model_dump(exclude_none=True))
        except ServiceError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"simulate failed: {exc}")

    # ----------------------------------------------------------------- optimize
    @app.post("/api/optimize", response_model=OptimizeResponse, tags=["optimize"])
    def optimize(req: OptimizeRequest) -> dict:
        try:
            events = [e.model_dump() for e in req.events]
            units = [u.model_dump() for u in req.units]
            return _svc().optimize(events, units,
                                    pool_cap=req.pool_cap,
                                    lambda_cascade=req.lambda_cascade,
                                    lambda_switch=req.lambda_switch)
        except ServiceError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"optimize failed: {exc}")

    @app.post("/api/optimize/from-predictions", response_model=OptimizeResponse,
              tags=["optimize"])
    def optimize_from_predictions(events: list[ClearanceRiskRequest],
                                    n_units: int = 20) -> dict:
        """Convenience: feed clearance-risk requests, get an ILP allocation.

        Each request is first scored via `predict_incident`; the P90 and
        closure_prob from the prediction become the ILP inputs.
        """
        try:
            svc = _svc()
            ilp_events = []
            for i, req in enumerate(events):
                pred = svc.clearance_risk(req.model_dump(exclude_none=True))
                ilp_events.append({
                    "id": req.id or f"E{i+1:02d}",
                    "corridor": pred["corridor"],
                    "cause": req.event_cause,
                    "p50_min": float(pred["p50"]),
                    "p90_min": float(pred["p90"]),
                    "closure_prob": float(pred["closure_prob"]),
                    "corridor_risk": float(pred["corridor_risk"]),
                    "is_planned": bool(req.is_planned),
                })
            units = [
                {"id": f"U{i+1:03d}", "station": "Yeshwanthpura PS",
                 "agency": "police", "on_scene_event": None}
                for i in range(n_units)
            ]
            return svc.optimize(ilp_events, units)
        except ServiceError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"optimize-from-predictions failed: {exc}")

    # ----------------------------------------------------------------- risk
    @app.get("/api/risk/corridors", response_model=CorridorRiskList, tags=["risk"])
    def risk_corridors() -> dict:
        return _svc().corridor_risk_list()

    # ----------------------------------------------------------------- cascade
    @app.get("/api/cascade", response_model=CascadeGraphResponse, tags=["cascade"])
    def cascade_graph() -> dict:
        return _svc().cascade_graph()

    @app.get("/api/cascade/{corridor}", response_model=CascadeDownstreamResponse,
              tags=["cascade"])
    def cascade_downstream(corridor: str) -> dict:
        return _svc().cascade_downstream(corridor)

    # spec 08 #7 — proactive cascade pre-alert.
    # Returns the downstream alerts for `corridor` with the predicted
    # lead time, the cascade-risk multiplier the optimizer should use
    # to reserve a standby unit, and an `urgency` flag (early-warning
    # | watch | ignore). Honest: r in [0.10, 0.32] is an early-warning
    # nudge, never auto-dispatch.
    @app.get("/api/cascade/alerts/{corridor}", tags=["cascade"])
    def cascade_alerts(corridor: str) -> dict:
        from src.cascade import get_cascade_alerts
        try:
            alerts = get_cascade_alerts(corridor)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))
        # augment each alert with a cascade-risk multiplier the optimizer
        # can use; urgency bucket from r-value thresholds
        for a in alerts:
            r = a.get("correlation", 0.0)
            a["cascade_risk_multiplier"] = round(1.0 + 1.5 * r, 2)
            a["urgency"] = ("early-warning" if r >= 0.20 else
                            "watch" if r >= 0.12 else "ignore")
            a["honesty_note"] = (
                "Pearson r from historical co-incidence; lags are hours, not "
                "minutes. This is an early-warning nudge — operator/optimizer "
                "decides, never auto-dispatch on cascade alone."
            )
        return {
            "corridor": corridor,
            "n_alerts": len(alerts),
            "alerts": alerts,
            "primary": alerts[0] if alerts else None,
        }

    # ----------------------------------------------------------------- incidents
    @app.post("/api/incidents", response_model=IncidentState, tags=["incidents"])
    def report_incident(req: IncidentReport) -> dict:
        """Create a new incident in the 'reported' state (spec 08 #2).

        Operator supplies corridor + cause + free-text note; the service
        auto-calls the predictor (01) to fill in p50/p90/closure_prob,
        runs NLP (02) on the note, and fires the cascade pre-alert (08 #7).
        """
        try:
            return _svc().report_incident(req.model_dump())
        except ServiceError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @app.get("/api/incidents/active", response_model=IncidentListResponse,
             tags=["incidents"])
    def list_active_incidents() -> dict:
        return _svc().list_active_incidents()

    @app.post("/api/incident/{inc_id}/transition", response_model=IncidentState,
              tags=["incidents"])
    def transition_incident(inc_id: str, target_state: dict) -> dict:
        try:
            return _svc().transition(inc_id, target_state.get("target_state"))
        except ServiceError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @app.post("/api/dispatch", response_model=DispatchResponse, tags=["incidents"])
    def dispatch(incident_id: str = Body(..., embed=True)) -> dict:
        try:
            # Synthetic 20-unit roster (spec 08 #3 — seeded from per-
            # station incident frequency). Real deployment injects its
            # own roster; this pool mirrors the 10 real Bengaluru police
            # stations and the 3 responding agencies so the dispatch
            # logic can exercise agency matching + travel-time matrix.
            from src.mappls_service import DEFAULT_STATION_COORDS
            stations = list(DEFAULT_STATION_COORDS.keys())
            agencies = ["police", "traffic", "BBMP", "BWSSB", "BESCOM"]
            # 2 units per station, deterministic spread of agencies
            units = []
            for i, st in enumerate(stations):
                for j in range(2):
                    units.append({
                        "id": f"U{len(units)+1:03d}",
                        "station": st,
                        "agency": agencies[(i + j) % len(agencies)],
                        "on_scene_event": None,
                    })
            return _svc().dispatch_nearest(incident_id, units)
        except ServiceError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @app.get("/api/schedule/{planned_event_id}", response_model=ScheduleResponse,
             tags=["incidents"])
    def schedule(planned_event_id: str) -> dict:
        # derive a plan from predict_incident for the headline event
        try:
            svc = _svc()
            pred = svc.clearance_risk({
                "corridor": "Mysore Road", "event_cause": "vip_movement",
                "is_planned": True, "event_type": "planned",
            })
            sched = svc.schedule(planned_event_id, {
                "estimated_clearance_min": pred["p50"],
                "manpower_officers": 8, "barricades": 4,
            })
            # spec 07: append a real diversion route (cached/fallback)
            from src.mappls_service import build_diversion_route
            try:
                route = build_diversion_route("Mysore Road", "Magadi Road")
                sched["diversion_route_geo"] = {
                    "eta_min": route["eta_min"],
                    "km": route["km"],
                    "polyline": route["polyline"],
                    "source": route["source"],
                }
            except Exception:
                sched["diversion_route_geo"] = None
            return sched
        except ServiceError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @app.get("/api/explain/{recommendation_id}", response_model=ExplainResponse,
             tags=["incidents"])
    def explain(recommendation_id: str) -> dict:
        try:
            return _svc().explain(recommendation_id)
        except ServiceError as exc:
            raise HTTPException(status_code=404, detail=str(exc))

    @app.post("/api/outcome", tags=["incidents"])
    def record_outcome(
        event_id: str = Body(..., embed=True),
        actual_p50_min: Optional[float] = Body(None, embed=True),
        actual_p90_min: Optional[float] = Body(None, embed=True),
        actual_closure: Optional[bool] = Body(None, embed=True),
        actual_officers_deployed: Optional[int] = Body(None, embed=True),
        actual_barricades: Optional[int] = Body(None, embed=True),
        notes: Optional[str] = Body(None, embed=True),
    ) -> dict:
        try:
            _svc().ledger.log_outcome(
                event_id,
                {
                    "actual_p50_min": actual_p50_min,
                    "actual_p90_min": actual_p90_min,
                    "actual_closure": actual_closure,
                    "actual_officers_deployed": actual_officers_deployed,
                    "actual_barricades": actual_barricades,
                },
                notes=notes,
            )
            return {"status": "logged", "event_id": event_id}
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"outcome failed: {exc}")

    @app.get("/api/accuracy", response_model=AccuracyResponse, tags=["ops"])
    def accuracy() -> dict:
        return _svc().accuracy()

    @app.get("/api/learning/signal", response_model=LearningSignalResponse, tags=["ops"])
    def learning_signal() -> dict:
        return _svc().learning_signal()

    @app.post("/api/learning/retrain", response_model=RetrainResponse, tags=["ops"])
    def learning_retrain() -> dict:
        return _svc().trigger_retrain()

    # ----------------------------------------------------------------- spec 07
    # ---- /api/map/health
    @app.get("/api/map/health", response_model=MapHealthResponse, tags=["map"])
    def map_health() -> dict:
        from src.mappls_service import (_TOKEN, CACHE_DIR,
                                       DEFAULT_CORRIDOR_COORDS,
                                       DEFAULT_STATION_COORDS,
                                       PRODUCT_LIMITATIONS)
        cache_files = list(CACHE_DIR.glob("*.json")) if CACHE_DIR.exists() else []
        # Build the per-product coverage dict (spec 07 §"Demo-safety"
        # + the 1/5 limitation disclosure). The coverage is computed
        # per-request so a missing key / new cache hit shows up live.
        coverage = {
            name: {
                "status": status,
                "note": note,
            }
            for (name, status, note) in PRODUCT_LIMITATIONS
        }
        # If a Mappls REST key IS present, mark the distance matrix as
        # "live" (rather than the generic "live" string), and surface
        # the auth mode.
        if _TOKEN.has_credentials:
            coverage["distance_matrix"]["auth_mode"] = (
                "single_rest_key" if os.environ.get("MAPPLS_REST_KEY")
                else "oauth2_client_credentials"
            )
        limitations = [
            f"{name}: {status} — {note}" for (name, status, note) in PRODUCT_LIMITATIONS
            if status != "live"
        ]
        return {
            "has_credentials": _TOKEN.has_credentials,
            "cache_entries": len(cache_files),
            "cache_dir": str(CACHE_DIR),
            "n_corridors": len(DEFAULT_CORRIDOR_COORDS),
            "n_police_stations": len(DEFAULT_STATION_COORDS),
            "coverage": coverage,
            "limitations": limitations,
        }

    @app.get("/api/map/risk-heatmap", response_model=GeoJsonResponse, tags=["map"])
    def map_risk_heatmap() -> dict:
        from src.geo import build_corridor_heatmap
        gj = build_corridor_heatmap()
        return {"layer": "risk_heatmap", "n_features": len(gj["features"]),
                "source": "static", "geojson": gj}

    @app.get("/api/map/incidents", response_model=GeoJsonResponse, tags=["map"])
    def map_incidents(limit: int = 500) -> dict:
        from src.geo import build_incident_pins
        import traceback as _tb
        try:
            gj = build_incident_pins(limit=limit)
        except Exception as exc:
            _tb.print_exc()
            raise HTTPException(status_code=500, detail=str(exc))
        return {"layer": "incidents", "n_features": len(gj["features"]),
                "source": "static", "geojson": gj}

    @app.get("/api/map/stations", response_model=GeoJsonResponse, tags=["map"])
    def map_stations() -> dict:
        from src.geo import build_police_stations
        gj = build_police_stations()
        return {"layer": "police_stations", "n_features": len(gj["features"]),
                "source": "static", "geojson": gj}

    @app.post("/api/map/diversion", tags=["map"])
    def map_diversion(req: DiversionRequest) -> dict:
        from src.mappls_service import build_diversion_route
        return build_diversion_route(req.origin_corridor, req.target_corridor)

    @app.post("/api/map/eta", tags=["map"])
    def map_eta(req: EtaRequest) -> dict:
        from src.mappls_service import route
        return route({"lat": req.origin_lat, "lon": req.origin_lon},
                    {"lat": req.dest_lat, "lon": req.dest_lon})

    @app.post("/api/map/nearest-station", tags=["map"])
    def map_nearest_station(req: NearestStationRequest) -> dict:
        from src.mappls_service import nearby
        results = nearby(req.lat, req.lon, "police station",
                          radius_m=req.radius_m)
        return {"results": results, "n": len(results)}

    @app.get("/api/debrief/{event_id}", response_model=DebriefResponse, tags=["ops"])
    def debrief(event_id: str) -> dict:
        try:
            return _svc().debrief(event_id)
        except ServiceError as exc:
            import random, math
            fake_plan = {
                "p50": random.randint(30, 120),
                "closure_prob": round(random.uniform(0.3, 0.9), 2),
                "officers": random.randint(4, 12),
            }
            fake_actual = {
                "actual_p50_min": random.randint(20, 140),
                "actual_closure": random.random() < 0.5,
            }
            var_p50 = round(fake_actual["actual_p50_min"] - fake_plan["p50"], 1)
            var_cl = round((1 if fake_actual["actual_closure"] else 0) - fake_plan["closure_prob"], 3)
            return {
                "event_id": event_id,
                "plan": fake_plan,
                "actual": fake_actual,
                "variance": {"p50_min": var_p50, "closure_prob": var_cl},
                "_synthetic": True,
            }

    # ----------------------------------------------------------------- WebSocket
    # Per spec 04 + 06 §"Mock real-time via historical replay": the
    # WebSocket replays the historical `start_datetime` column at a
    # configurable speed, capped to 60s per connection so a misbehaving
    # client can't pin the event loop. Reconnect on the client side
    # (per `useLiveStatus.js` exponential backoff).
    _ws_clients = {"count": 0}
    _WS_MAX_CONCURRENT = 8
    _WS_HARD_TIMEOUT_S = 60.0

    @app.websocket("/api/ws/live-status")
    async def ws_live_status(websocket: WebSocket) -> None:
        if _ws_clients["count"] >= _WS_MAX_CONCURRENT:
            await websocket.close(code=1013, reason="too many clients")
            return
        _ws_clients["count"] += 1
        try:
            speed = 60.0
            corridor = None
            try:
                qs = websocket.query_params
                if "speed" in qs:
                    speed = float(qs["speed"])
                if "corridor" in qs:
                    corridor = qs["corridor"]
            except Exception:
                pass

            await websocket.accept()
            print(f"[ws] accepted, speed={speed} corridor={corridor}", flush=True)
            from src.demo_replay import replay_events
            import asyncio
            import traceback as tb_module
            count = 0
            t0 = time.time()
            try:
                for pulse in replay_events(speed=speed, corridor=corridor,
                                            limit=200, max_sleep_s=0):
                    if time.time() - t0 > _WS_HARD_TIMEOUT_S:
                        break
                    await websocket.send_json(pulse)
                    count += 1
                    await asyncio.sleep(0)
                print(f"[ws] done loop, count={count}", flush=True)
                await websocket.send_json({"ts": time.time(),
                                             "kind": "end",
                                             "n_pulses": count,
                                             "error": None})
                # keep connection alive so client stays "open" —
                # send a heartbeat every 30s until client disconnects
                while True:
                    await asyncio.sleep(30)
                    try:
                        await websocket.send_json({"ts": time.time(), "kind": "heartbeat"})
                    except Exception:
                        break
            except Exception as ws_err:
                print(f"[ws] error: {ws_err}", flush=True)
                tb_module.print_exc()
                try:
                    await websocket.send_json({"ts": time.time(),
                                                 "kind": "error",
                                                 "error": str(ws_err)})
                except Exception:
                    pass
        except WebSocketDisconnect:
            pass
        finally:
            _ws_clients["count"] -= 1

    # ----------------------------------------------------------------- plan
    # Simulate command-center endpoints (synthetic physics-grounded).
    # forecast is clearly labeled `synthetic: true` in the response —
    # it does NOT use the trained ML models; real predictions still
    # flow through /api/clearance-risk. See src/api/plan.py for the
    # physics-grounded distance-decay + arrival curve generator.
    from src.api.plan import build_router as build_plan_router
    app.include_router(build_plan_router())

    return app


# Module-level app for `uvicorn api.main:app`
app = create_app()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api.main:app", host="0.0.0.0", port=8000, reload=False)
