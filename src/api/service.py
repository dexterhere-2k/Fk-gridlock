"""GridLock — 04: Service layer (thin wrapper around the ML core).

The HTTP layer (`main.py`) is a thin shell around this. The service:
  - calls `src.predict.predict_incident` for clearance-risk / simulate
  - calls `src.optimize.solve` for /api/optimize
  - calls `src.cascade` for /api/cascade*
  - queries the corridor risk prior for /api/risk/corridors
  - drives the incident state machine for /api/incident/{id}/transition
  - records predictions + outcomes to the SQLite ledger

The service is **process-local**: artifacts are loaded once into
`Service._state` at startup and reused across requests.
"""
from __future__ import annotations
import json
import sys
import time
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import joblib
import numpy as np
import pandas as pd

from .. import config as C
from ..predict import predict_incident
from ..optimize import solve as optimize_solve
from ..cascade import get_cascade_alerts  # noqa: F401  (re-export)


# ---------------------------------------------------------------------------
# spec 07 — travel-time matrix loader
# ---------------------------------------------------------------------------
def _load_travel_matrix(artifacts_dir) -> dict:
    """Read `artifacts/map_cache/corridor_distance_matrix.json` and return
    a dict {(src, tgt): minutes}. The matrix is corridor→corridor (22×22
    in the demo); unit→corridor ETA is resolved by picking the corridor
    closest to the unit's station (haversine).

    Returns {} if the cache is missing (e.g. spec 07 step skipped) so
    callers can fall back gracefully.
    """
    cache_path = artifacts_dir / "map_cache" / "corridor_distance_matrix.json"
    if not cache_path.exists():
        return {}
    try:
        import json as _json
        data = _json.loads(cache_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    corridors = data.get("corridors", [])
    durations = data.get("durations_s", [])
    out = {}
    for i, src in enumerate(corridors):
        for j, tgt in enumerate(corridors):
            try:
                s = float(durations[i][j])
                if s and s > 0 and s < 1e6:
                    out[(src, tgt)] = s / 60.0  # seconds → minutes
            except (IndexError, TypeError, ValueError):
                pass
    return out


# ---------------------------------------------------------------------------
# spec 08 #3 — dispatch helpers
# ---------------------------------------------------------------------------
def _haversine_km(a, b) -> float:
    """Great-circle distance in km between two (lat, lon) tuples."""
    import math
    R = 6371.0
    lat1, lon1 = math.radians(a[0]), math.radians(a[1])
    lat2, lon2 = math.radians(b[0]), math.radians(b[1])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * R * math.asin(math.sqrt(h))


def _nearest_corridor_name(coord, corridor_coords: dict) -> str | None:
    """Find the corridor name whose (lat, lon) is closest to `coord`
    by haversine. Used to map a police-station coordinate to a
    corridor-key the travel-time matrix understands.
    """
    if not corridor_coords:
        return None
    best_name, best_km = None, float("inf")
    for name, c in corridor_coords.items():
        km = _haversine_km(coord, c)
        if km < best_km:
            best_km, best_name = km, name
    return best_name


# State machine (spec 08 #2 — "advance on state change")
# 9 states per spec 08: reported → verified → assigned → en_route →
#   on_scene → mitigating → clearing → closed → debrief
VALID_STATES = (
    "reported", "verified", "assigned", "en_route",
    "on_scene", "mitigating", "clearing", "closed", "debrief",
)
ALLOWED_TRANSITIONS = {
    "reported":   ("verified", "assigned", "en_route", "on_scene",
                   "mitigating", "clearing", "closed"),
    "verified":   ("assigned", "en_route", "on_scene",
                   "mitigating", "clearing", "closed"),
    "assigned":   ("en_route", "on_scene", "mitigating", "clearing", "closed"),
    "en_route":   ("on_scene", "mitigating", "clearing", "closed"),
    "on_scene":   ("mitigating", "clearing", "closed"),
    "mitigating": ("clearing", "closed"),
    "clearing":   ("closed", "debrief"),
    "closed":     ("debrief",),
    "debrief":    (),
}
# "dispatched" → "assigned" back-compat shim
ALLOWED_TRANSITIONS["dispatched"] = ALLOWED_TRANSITIONS["assigned"]
# SLA budgets (minutes) per cause — escalated events get tighter SLA
SLA_MINUTES = {
    "vip_movement": 30, "protest": 30, "public_event": 45, "procession": 45,
    "accident": 45, "tree_fall": 60, "water_logging": 90, "construction": 120,
    "vehicle_breakdown": 60, "pot_holes": 240, "congestion": 60, "others": 60,
}
DEFAULT_SLA = 60


def _seed_astram_outcomes(ledger):
    """Load real ASTraM labeled rows as outcomes on first startup.

    The ASTraM CSV has ~2,527 rows with valid clearance times (real
    outcomes). We load them into the ledger so the learning loop
    (Debrief tab) shows per-cause MAE based on real data immediately,
    without needing the user to manually seed demo outcomes.
    """
    if ledger.count_outcomes() > 100:  # already seeded
        return
    try:
        import uuid
        from src.config import CLEAN_PARQUET
        df = pd.read_parquet(CLEAN_PARQUET)
        labeled = df[df["duration_min"].notna() & (df["duration_min"] > 0)]
        if labeled.empty:
            return
        n = 0
        for _, row in labeled.iterrows():
            if n >= 500:  # cap at 500 to keep startup fast
                break
            ledger.log_outcome(
                str(uuid.uuid4()),
                {
                    "actual_p50_min": float(row["duration_min"]),
                    "actual_closure": bool(row.get("status", "") == "closed"),
                },
                notes=f"astram: {row.get('corridor','?')} / {row.get('event_cause','?')}",
            )
            n += 1
        print(f"[gridlock] seeded {n} ASTraM outcomes into ledger", flush=True)
    except Exception as e:
        print(f"[gridlock] outcome seeding skipped: {e}", flush=True)


class ServiceError(Exception):
    """User-facing error from the service layer (mapped to 4xx in the API)."""


class Service:
    """Stateful wrapper around the ML core. Load artifacts once."""

    def __init__(self, artifacts_dir: Optional[Path] = None,
                 data_dir: Optional[Path] = None):
        self.start_time = time.time()
        # ---- load artifacts once
        self.context = joblib.load(C.CONTEXT_PKL)
        self.clearance_art = joblib.load(C.CLEARANCE_PKL)
        self.survival_art = joblib.load(C.SURVIVAL_PKL)
        self.closure_art = joblib.load(C.CLOSURE_PKL)
        self.corridor_risk = pd.read_csv(C.CORRIDOR_RISK_CSV)
        self.cascade_edges = pd.read_csv(C.CASCADE_EDGES_CSV)
        self.cascade_meta = json.loads(C.CASCADE_META_JSON.read_text(encoding="utf-8"))
        # ---- travel-time matrix (spec 07) for dispatch ETA + station lookup
        self.travel_matrix = _load_travel_matrix(C.ARTIFACTS_DIR)
        # ---- station + corridor coords for nearest-corridor resolution
        from ..mappls_service import (DEFAULT_CORRIDOR_COORDS,
                                       DEFAULT_STATION_COORDS)
        self.corridor_coords = DEFAULT_CORRIDOR_COORDS
        self.station_coords = DEFAULT_STATION_COORDS
        # ---- ledger (SQLite, one file)
        from .ledger import Ledger
        ledger_path = (artifacts_dir or C.ARTIFACTS_DIR) / "ledger.sqlite3"
        self.ledger = Ledger(ledger_path)
        # Pre-load real ASTraM labeled rows as outcomes so the learning
        # loop has live data from first load (not synthetic).
        _seed_astram_outcomes(self.ledger)

    # ----------------------------------------------------------------- health
    def health(self) -> dict:
        return {
            "status": "ok",
            "n_artifacts_loaded": 5,
            "n_corridors": int(len(self.corridor_risk)),
            "n_cascade_edges": int(len(self.cascade_edges)),
            "uptime_s": round(time.time() - self.start_time, 2),
            "version": "0.1.0",
        }

    # ----------------------------------------------------------------- predict
    def clearance_risk(self, req: dict) -> dict:
        out = predict_incident(req)
        # log to ledger (return the prediction id for traceability)
        out["prediction_id"] = self.ledger.log_prediction(
            "/api/clearance-risk", req, out)
        return out

    def simulate(self, req: dict) -> dict:
        pred = self.clearance_risk(req)
        # spec 03-style alert level + plan
        cp = pred["closure_prob"]
        sev = max(1, min(5, int(round(1 + 4 * cp))))
        if cp >= 0.60 or sev >= 5:
            alert = "CRITICAL"
        elif cp >= 0.35 or sev >= 4 or (req.get("is_planned") and cp >= 0.25):
            alert = "HIGH"
        elif cp >= 0.15 or sev >= 3:
            alert = "MEDIUM"
        else:
            alert = "LOW"
        plan = {
            "alert_level": alert,
            "manpower_officers": int(min(16, max(2, 3 + round(cp * 6) + sev))),
            "barricades": int(max(2, min(12, round(cp * 8) + sev))) if cp >= 0.4 else 0,
            "need_diversion": bool(cp >= 0.4 and pred["corridor"] != "Non-corridor"),
            "diversion_to": None,
            "tow_required": bool(pred["nlp_cues"].get("needs_crane_tow")),
            "estimated_clearance_min": pred["p50"],
        }
        # heuristic diversion target (per spec 03 — pick the next
        # non-incident corridor to redirect traffic into)
        from ..optimize import _diversion_target
        if plan["need_diversion"]:
            plan["diversion_to"] = _diversion_target(pred["corridor"])
        # build a short human summary (spec 06 demo script)
        lines = [
            f"[{alert}] {req.get('event_cause', 'incident')} on {pred['corridor']}",
            f"  • Deploy {plan['manpower_officers']} officers "
            f"(closure risk {cp:.0%}, severity {sev}/5)",
        ]
        if plan["barricades"]:
            lines.append(f"  • Set up {plan['barricades']} barricades")
        if plan["diversion_to"]:
            lines.append(f"  • Divert traffic to: {plan['diversion_to']}")
        if plan["tow_required"]:
            lines.append("  • Dispatch tow/crane for vehicle recovery")
        lines.append(f"  • Estimated clearance: ~{plan['estimated_clearance_min']} min")
        summary = "\n".join(lines)
        return {"prediction": pred, "alert_level": alert, "plan": plan,
                "summary": summary}

    # ----------------------------------------------------------------- optimize
    def optimize(self, events: list[dict], units: list[dict],
                 pool_cap: int = 200, lambda_cascade: float = 0.10,
                 lambda_switch: float = 0.3) -> dict:
        if not events or not units:
            raise ServiceError("at least 1 event and 1 unit required")
        # pool_cap defaults to number of units
        pool_cap = min(pool_cap, len(units))
        result = optimize_solve(events, units, self.cascade_edges, params={
            "verbose": False, "time_limit_s": 5.0, "pool_cap": pool_cap,
            "lambda_cascade": lambda_cascade, "lambda_switch": lambda_switch,
        })
        # spec 08 #6 — wrap every event in the allocation with a `because`
        # payload that the UI can show: top features (already there from
        # the ILP), historical comparator (from corridor prior), uncertainty
        # band, confidence flag.
        corridor_priors = {r["corridor"]: r for _, r in self.corridor_risk.iterrows()}
        for ev in events:
            alloc = result["events"].get(ev["id"], {})
            ev_id = ev["id"]
            corridor = ev.get("corridor", "Non-corridor")
            cp = float(ev.get("closure_prob", 0.0))
            p50 = float(ev.get("p50_min", 60))
            p90 = float(ev.get("p90_min", p50 * 2))
            prior = corridor_priors.get(corridor, {})
            hist_med = float(prior.get("med_clear", 0) or 0)
            hist_p90 = float(prior.get("p90_clear", 0) or 0)
            # Comparator: "similar incidents here averaged N min"
            comparator = None
            if hist_med > 0:
                comparator = {
                    "corridor": corridor,
                    "historical_p50_min": hist_med,
                    "historical_p90_min": hist_p90,
                    "n_historical_events": int(prior.get("events", 0)),
                    "deviation_pct": round((p50 - hist_med) / max(1, hist_med) * 100, 1),
                }
            # Confidence: corridor sample size + interval width
            n_samples = int(prior.get("events", 0))
            interval_w = p90 - float(ev.get("p10_min", p50 * 0.4))
            width_rel = interval_w / max(1.0, p50)
            if n_samples >= 100 and width_rel < 1.0:
                confidence = "high"
            elif n_samples >= 30 and width_rel < 2.0:
                confidence = "medium"
            else:
                confidence = "low"
            # Build the full `because` payload. The ILP's per-event
            # reasons become the first N bullets; we APPEND the
            # comparator, NLP cues, and uncertainty so the UI has
            # the full chain.
            because = list(alloc.get("because", []))
            if comparator:
                because.append(
                    f"Historical comparator: this corridor's {n_samples} "
                    f"prior incidents averaged P50 {hist_med:.0f} min, "
                    f"P90 {hist_p90:.0f} min — current prediction is "
                    f"{'longer' if comparator['deviation_pct'] > 0 else 'shorter'} "
                    f"by {abs(comparator['deviation_pct']):.0f}%"
                )
            nlp_cues = ev.get("nlp_cues", {})
            if isinstance(nlp_cues, dict) and nlp_cues:
                nlp_summary = ", ".join(
                    f"{k.replace('_', ' ')}" for k, v in nlp_cues.items() if v
                )
                if nlp_summary:
                    because.append(f"NLP cues from operator note: {nlp_summary}")
            because.append(
                f"Uncertainty band: P10 {ev.get('p10_min', 0):.0f}m, "
                f"P50 {p50:.0f}m, P90 {p90:.0f}m "
                f"(width = {width_rel:.1f}× median)"
            )
            if confidence == "low":
                because.append(
                    f"Low confidence — only {n_samples} prior samples on this "
                    f"corridor, defer to operator judgment"
                )
            alloc["because"] = because
            alloc["confidence"] = confidence
            alloc["comparator"] = comparator
            alloc["uncertainty"] = {
                "p10": float(ev.get("p10_min", 0)),
                "p50": p50, "p90": p90,
                "width_relative": round(width_rel, 2),
            }
            result["events"][ev_id] = alloc
        # top-level because
        n_high_risk = sum(
            1 for e in result["events"].values()
            if e.get("confidence") == "low" or e.get("understaffed_by", 0) > 0
        )
        result["global_because"] = [
            f"ILP solved in {result['solve_time_s']*1000:.0f}ms — "
            f"status={result['status']}",
            f"Objective: {result['objective']:.0f} (Σ understaff × P90 × "
            f"corridor_risk × priority)",
            f"{n_high_risk} of {len(events)} events have either understaffing "
            f"or low confidence — review before approving dispatch.",
        ]
        return result

    # ----------------------------------------------------------------- corridor risk
    def corridor_risk_list(self) -> dict:
        df = self.corridor_risk
        corridors = [
            {
                "corridor": str(r["corridor"]),
                "events": int(r["events"]),
                "med_clear": float(r.get("med_clear") or 0),
                "p90_clear": float(r.get("p90_clear") or 0),
                "closure_rate": float(r.get("closure_rate") or 0),
                "risk_score": float(r["risk_score"]),
            }
            for _, r in df.iterrows()
        ]
        return {"n_corridors": len(corridors), "corridors": corridors}

    # ----------------------------------------------------------------- cascade
    def cascade_graph(self) -> dict:
        return {
            "n_edges": int(len(self.cascade_edges)),
            "n_corridors": int(self.cascade_meta.get("n_corridors", 0)),
            "n_hours": int(self.cascade_meta.get("n_hours", 0)),
            "trigger_rank": self.cascade_meta.get("trigger_rank", []),
            # canonical key is `strongest_edges` (used by the frontend
            # LiveView); we keep `top_edges` as a back-compat alias.
            "strongest_edges": self.cascade_meta.get("strongest_edges", [])[:10],
            "top_edges": self.cascade_meta.get("strongest_edges", [])[:10],
            "strongest_chains": self.cascade_meta.get("strongest_chains", [])[:5],
        }

    def cascade_downstream(self, corridor: str) -> dict:
        if corridor not in self.cascade_edges["source"].values:
            return {"corridor": corridor, "downstream": [], "n_downstream": 0}
        out = (self.cascade_edges[self.cascade_edges["source"] == corridor]
               .sort_values("r", ascending=False)
               .to_dict(orient="records"))
        downstream = [{
            "corridor": e["target"],
            "lag_h": int(e["lag_h"]),
            "lag_min": int(e["lag_h"]) * 60,
            "r": float(e["r"]),
        } for e in out]
        return {"corridor": corridor, "downstream": downstream,
                "n_downstream": len(downstream)}

    # ----------------------------------------------------------------- incidents + state machine
    def report_incident(self, req: dict) -> dict:
        """Create a new incident in the 'reported' state (spec 08 #2).

        Operator supplied only corridor + cause + free-text note. We
        auto-call the predictor (01) to fill in p50/p90/closure_prob and
        run NLP (02) on the note for cues. The cascade pre-alert (08 #7)
        fires immediately and the SLA budget is set per cause.
        """
        inc_id = req.get("id") or f"INC-{int(time.time() * 1000)}"
        cause = req.get("cause") or req.get("event_cause") or "others"
        corridor = req.get("corridor") or "Non-corridor"
        priority = "High" if req.get("is_planned") or cause in (
            "vip_movement", "protest", "public_event", "procession",
            "accident", "tree_fall") else "Low"
        now = time.time()
        inc = {
            "id": inc_id,
            "corridor": corridor,
            "cause": cause,
            "priority": priority,
            "state": "reported",
            "reported_at": now,
            "last_transition_at": now,
            "sla_minutes": SLA_MINUTES.get(cause, DEFAULT_SLA),
            "sla_breaches": 0,
            "cascade_alerts": get_cascade_alerts(corridor),
        }
        # spec 01 — auto-call the predictor so the operator's report
        # carries the [P10, P50, P90] band and closure probability
        # without the operator having to fill them in.
        try:
            pred = self.clearance_risk({
                "corridor": corridor,
                "event_cause": cause,
                "is_planned": bool(req.get("is_planned", False)),
                "veh_type": req.get("veh_type"),
                "cargo_material": req.get("cargo_material"),
                "free_text": req.get("operator_note"),
            })
            inc["prediction"] = {
                "p10": float(pred.get("p10", 0)),
                "p50": float(pred.get("p50", 0)),
                "p90": float(pred.get("p90", 0)),
                "closure_prob": float(pred.get("closure_prob", 0)),
                "corridor_risk": float(pred.get("corridor_risk", 0)),
                "nlp_cues": dict(pred.get("nlp_cues", {})),
                "prediction_id": pred.get("prediction_id"),
                "confidence": pred.get("confidence", "medium"),
            }
        except Exception:
            inc["prediction"] = None
        self.ledger.upsert_incident(inc)
        return self._incident_view(inc)

    def transition(self, inc_id: str, target_state: str) -> dict:
        inc = self.ledger.get_incident(inc_id)
        if inc is None:
            raise ServiceError(f"unknown incident: {inc_id}")
        if target_state not in VALID_STATES:
            raise ServiceError(f"invalid target_state: {target_state}")
        # spec 08: "dispatched" is now an alias for "assigned"
        eff_target = "assigned" if target_state == "dispatched" else target_state
        if eff_target not in ALLOWED_TRANSITIONS[inc["state"]]:
            raise ServiceError(
                f"illegal transition {inc['state']} -> {eff_target}")
        inc["state"] = eff_target
        inc["last_transition_at"] = time.time()
        # spec 08 #2: "SLA breach raises priority". Re-evaluate on every
        # transition; if elapsed > SLA and priority is still Low, bump to
        # High and increment the breach counter.
        elapsed_min = (time.time() - inc["reported_at"]) / 60.0
        if (elapsed_min > inc.get("sla_minutes", DEFAULT_SLA) and
                inc.get("priority") == "Low"):
            inc["priority"] = "High"
            inc["sla_breaches"] = int(inc.get("sla_breaches", 0)) + 1
            inc["escalation_reason"] = (
                f"SLA breach at {elapsed_min:.0f}min "
                f"(budget {inc.get('sla_minutes', DEFAULT_SLA)}min) — "
                f"auto-escalated to High"
            )
        # spec 08 #2: verifying the incident fires the cascade pre-alert
        # (see #7) — surface in the view but don't auto-dispatch.
        if eff_target == "verified":
            alerts = get_cascade_alerts(inc.get("corridor", "Non-corridor"))
            inc["cascade_alerts"] = alerts
        self.ledger.upsert_incident(inc)
        return self._incident_view(inc)

    def list_active_incidents(self) -> dict:
        items = self.ledger.list_active_incidents()
        return {"n_active": len(items),
                "incidents": [self._incident_view(i) for i in items]}

    def sla_tick(self) -> int:
        """Spec 08 #2 background ticker — scan active incidents, escalate
        any that have breached SLA but are still at Low priority. Idempotent
        (increments sla_breaches only on the FIRST tick after breach).

        Returns the count of incidents escalated on this tick (for logging).
        """
        n_escalated = 0
        now = time.time()
        for inc in self.ledger.list_active_incidents():
            if inc["state"] == "closed" or inc["state"] == "debrief":
                continue
            elapsed_min = (now - inc["reported_at"]) / 60.0
            sla = inc.get("sla_minutes", DEFAULT_SLA)
            if elapsed_min > sla and inc.get("priority") == "Low":
                inc["priority"] = "High"
                inc["sla_breaches"] = int(inc.get("sla_breaches", 0)) + 1
                inc["escalation_reason"] = (
                    f"SLA breach at {elapsed_min:.0f}min "
                    f"(budget {sla}min) — auto-escalated to High"
                )
                self.ledger.upsert_incident(inc)
                n_escalated += 1
        return n_escalated

    def _incident_view(self, inc: dict) -> dict:
        now = time.time()
        elapsed = (now - inc["reported_at"]) / 60.0
        return {
            "id": inc["id"],
            "corridor": inc.get("corridor"),
            "cause": inc.get("cause"),
            "state": inc["state"],
            "reported_at": datetime.fromtimestamp(inc["reported_at"],
                                                  tz=timezone.utc).isoformat(),
            "last_transition_at": datetime.fromtimestamp(inc["last_transition_at"],
                                                          tz=timezone.utc).isoformat(),
            "sla_minutes": int(inc.get("sla_minutes", DEFAULT_SLA)),
            "elapsed_minutes": round(elapsed, 1),
            "sla_breached": bool(elapsed > inc.get("sla_minutes", DEFAULT_SLA)),
            "sla_breaches": int(inc.get("sla_breaches", 0)),
            "escalation_reason": inc.get("escalation_reason"),
            "priority": inc.get("priority", "Low"),
            "cascade_alerts": inc.get("cascade_alerts", []),
            "prediction": inc.get("prediction"),
        }

    # ----------------------------------------------------------------- dispatch
    def dispatch_nearest(self, incident_id: str, units: list[dict]) -> dict:
        """Spec 08 #3 — roster/skill/agency-aware dispatch with real ETA.

        Algorithm (honest, deterministic, explainable):
          1. Resolve preferred agency for this cause
             (accident/breakdown→traffic, tree_fall/construction→BBMP, …).
          2. Build two eligibility pools: exact-agency and police-fallback.
          3. For every unit, compute ETA from the unit's station to the
             incident corridor. Try the pre-computed Mappls travel-time
             matrix first (corridor→corridor); if the unit's station
             isn't a corridor key, resolve station→nearest-corridor via
             haversine and use that matrix entry. If neither is
             available, fall back to a pure-haversine ETA at 30 km/h.
          4. Pick the unit with the smallest ETA, preferring the exact
             agency pool. On a tie, prefer the exact agency match.
          5. Return the chosen unit + ETA + agency-match quality +
             the 3 nearest alternatives + a confidence flag (high if
             from matrix, medium if haversine, low if neither).
        """
        import math
        inc = self.ledger.get_incident(incident_id)
        if inc is None:
            raise ServiceError(f"unknown incident: {incident_id}")
        if not units:
            raise ServiceError("no units available")
        cause = inc.get("cause", "others")
        corridor = inc.get("corridor", "Non-corridor")
        agency_match = {"vehicle_breakdown": "traffic",
                        "accident": "traffic",
                        "tree_fall": "BBMP",
                        "construction": "BBMP",
                        "water_logging": "BWSSB",
                        "pot_holes": "BBMP"}
        preferred = agency_match.get(cause, "police")

        # ---- pre-resolve corridor target for this incident
        target_coord = self.corridor_coords.get(corridor)
        if not target_coord and corridor != "Non-corridor":
            target_coord = (12.97, 77.59)  # Bengaluru center fallback

        # ---- compute ETA for each unit
        def _eta_for(unit):
            station = unit.get("station", "")
            s_coord = self.station_coords.get(station)
            # 1) try matrix: nearest corridor from station → incident corridor
            if s_coord and self.travel_matrix and target_coord is not None:
                src_corr = _nearest_corridor_name(s_coord, self.corridor_coords)
                if src_corr:
                    key = (src_corr, corridor)
                    if key in self.travel_matrix:
                        return (
                            round(self.travel_matrix[key], 1),
                            "matrix",
                            f"Mappls DM cache: {src_corr}→{corridor}",
                        )
            # 2) haversine fallback at 30 km/h
            if s_coord and target_coord is not None:
                km = _haversine_km(s_coord, target_coord)
                mins = round(km / 30.0 * 60.0 + 1.5, 1)  # +1.5 min dispatch overhead
                return (mins, "haversine",
                        f"haversine {km:.1f} km @ 30 km/h (no matrix entry)")
            # 3) no coords at all → "low" confidence
            return (None, "unknown", "no station/corridor coords")

        scored = []
        for u in units:
            eta, src, why = _eta_for(u)
            scored.append({
                "unit": u,
                "eta_min": eta,
                "eta_source": src,
                "eta_reason": why,
            })

        eligible = [s for s in scored
                    if s["unit"].get("agency", "police") in (preferred, "police")]
        if not eligible:
            eligible = scored  # last resort: any unit, even mismatched agency
        # ---- pick best: smallest ETA, break ties by agency match
        def _sort_key(s):
            u = s["unit"]
            agency = u.get("agency", "police")
            exact = 0 if agency == preferred else 1
            eta = s["eta_min"] if s["eta_min"] is not None else 1e6
            return (eta, exact)
        eligible.sort(key=_sort_key)
        chosen_s = eligible[0]
        chosen = chosen_s["unit"]
        chosen_eta = chosen_s["eta_min"]
        chosen_src = chosen_s["eta_source"]
        chosen_why = chosen_s["eta_reason"]

        # agency-match quality
        if chosen.get("agency") == preferred:
            agency_q = "exact"
        elif chosen.get("agency") == "police" and preferred != "police":
            agency_q = "police_fallback"
        else:
            agency_q = "mismatch"
        # confidence
        if chosen_src == "matrix":
            confidence = "high"
        elif chosen_src == "haversine":
            confidence = "medium"
        else:
            confidence = "low"

        # ---- 3 nearest alternatives (any agency, for context)
        sorted_all = sorted(
            [s for s in scored if s["eta_min"] is not None],
            key=lambda s: s["eta_min"],
        )
        alternatives = []
        for s in sorted_all:
            if s["unit"]["id"] == chosen["id"]:
                continue
            u = s["unit"]
            alternatives.append({
                "unit_id": u.get("id"),
                "station": u.get("station"),
                "agency": u.get("agency", "police"),
                "eta_min": s["eta_min"],
                "eta_source": s["eta_source"],
            })
            if len(alternatives) >= 3:
                break

        # ---- build the rationale (spec 08 #6 — "because")
        because = [
            f"preferred agency for {cause}: {preferred} — got {chosen.get('agency', 'police')} "
            f"({agency_q.replace('_', ' ')})",
            chosen_why,
            f"alternatives considered: {len(scored)} units, "
            f"{sum(1 for s in scored if s['unit'].get('agency') == preferred)} exact-agency",
        ]
        if agency_q == "police_fallback":
            because.append(
                f"honest: no {preferred} unit available, falling back to police "
                f"(recommend second-wave dispatch of {preferred} if available)"
            )

        return {
            "incident_id": incident_id,
            "incident_corridor": corridor,
            "incident_cause": cause,
            "preferred_agency": preferred,
            "dispatched_unit": chosen["id"],
            "dispatched_unit_station": chosen.get("station"),
            "dispatched_unit_agency": chosen.get("agency", "police"),
            "estimated_eta_min": chosen_eta,
            "eta_source": chosen_src,
            "agency_match": agency_q,
            "confidence": confidence,
            "eligible_count": len(scored),
            "alternatives": alternatives,
            "because": because,
            "rationale": (
                f"selected {chosen['id']} ({chosen.get('agency', 'police')} @ "
                f"{chosen.get('station', 'unknown')}) for {cause} on {corridor}; "
                f"preferred agency={preferred} ({agency_q}); "
                f"ETA {chosen_eta} min from {chosen_src} source"
            ),
        }

    # ----------------------------------------------------------------- schedule
    def schedule(self, planned_event_id: str, plan: dict) -> dict:
        """Time-phased deployment plan for a planned event (spec 08 #4)."""
        # Heuristic schedule: T-120 deploy barricades, T-30 deploy officers,
        # T+0 start, T+p90 clear.
        p50 = int(plan.get("estimated_clearance_min", 60))
        slots = [
            {"time_offset_min": -120, "action": "set_up_barricades",
             "units": int(plan.get("barricades", 0)),
             "reason": "pre-position barricades 2h before predicted start"},
            {"time_offset_min": -30,  "action": "deploy_officers",
             "units": int(plan.get("manpower_officers", 0)),
             "reason": "deploy officers 30min before for briefing + route recon"},
            {"time_offset_min": 0,    "action": "start_event",
             "units": 0,
             "reason": "planned event window opens"},
            {"time_offset_min": int(p50 * 0.5),
             "action": "checkin",
             "units": 0,
             "reason": "mid-event status check"},
            {"time_offset_min": p50,
             "action": "demobilize",
             "units": int(plan.get("manpower_officers", 0)),
             "reason": f"event wind-down at predicted P50 ({p50} min)"},
        ]
        summary = f"Schedule for {planned_event_id}: T-120 barricades, " \
                  f"T-30 officers deploy, T+0 event start, T+{p50} clear"
        return {
            "event_id": planned_event_id,
            "start_at": datetime.now(tz=timezone.utc).isoformat(),
            "slots": slots,
            "summary": summary,
        }

    # ----------------------------------------------------------------- explain
    def explain(self, recommendation_id: str) -> dict:
        """Return the `because` payload + confidence for a past prediction."""
        with self.ledger._conn() as c:
            row = c.execute(
                "SELECT payload_json FROM predictions WHERE id = ?",
                (recommendation_id,)).fetchone()
        if row is None:
            raise ServiceError(f"unknown recommendation_id: {recommendation_id}")
        payload = json.loads(row["payload_json"])
        # surface the model's top reasons; in a full impl we'd add SHAP.
        # contributing_features is a list of (name, value) pairs from the
        # parsed NLP cues (or in a full impl, SHAP attributions).
        nlp = payload.get("nlp_cues", {})
        contributing = [
            {"name": k, "value": v} for k, v in nlp.items() if v
        ] if isinstance(nlp, dict) else []
        return {
            "recommendation_id": recommendation_id,
            "because": payload.get("because", []),
            "confidence": payload.get("confidence", "low"),
            "contributing_features": contributing,
        }

    # ----------------------------------------------------------------- accuracy
    def accuracy(self) -> dict:
        """Plan-vs-actual accuracy from the outcomes table (spec 06/08)."""
        outcomes = self.ledger.get_outcomes()
        if not outcomes:
            return {"n_outcomes": 0, "p50_mae_min": 0.0,
                    "closure_accuracy": 0.0, "points": []}
        errors, correct, points = [], 0, []
        for o in outcomes:
            if o["actual_p50_min"] is None:
                continue
            # look up the matching prediction (best-effort)
            with self.ledger._conn() as c:
                row = c.execute(
                    "SELECT payload_json FROM predictions WHERE id = ?",
                    (o["event_id"],)).fetchone()
            predicted_p50, predicted_closure = 0, None
            if row is not None:
                try:
                    pred = json.loads(row["payload_json"])
                    predicted_p50 = float(pred.get("p50", 0))
                    predicted_closure = bool(pred.get("closure_prob", 0) >= 0.5)
                except Exception:
                    pass
            err = abs(float(o["actual_p50_min"]) - predicted_p50)
            errors.append(err)
            if predicted_closure is not None and o["actual_closure"] is not None:
                if predicted_closure == bool(o["actual_closure"]):
                    correct += 1
            points.append({
                "event_id": o["event_id"],
                "predicted_p50": predicted_p50,
                "actual_p50": float(o["actual_p50_min"]),
                "predicted_closure": predicted_closure or False,
                "actual_closure": bool(o["actual_closure"]) if o["actual_closure"] is not None else False,
                "error_min": round(err, 1),
            })
        n = max(1, len(errors))
        return {
            "n_outcomes": len(outcomes),
            "p50_mae_min": round(float(np.mean(errors)) if errors else 0.0, 2),
            "closure_accuracy": round(correct / n, 3) if errors else 0.0,
            "points": points,
        }

    # ----------------------------------------------------------------- learning
    def learning_signal(self) -> dict:
        """Compute the live learning signal (spec 06 §"Debrief loop").

        Reads the outcomes table + the most recent `learning_log.json`
        so the dashboard can show "model last retrained at X, MAE was
        Y, current MAE is Z" — the loop is real, not a slide.
        """
        from ..learning_loop import (compute_learning_signal as _cls,
                                     should_retrain as _should)
        sig = _cls(self.ledger)
        log_path = C.ARTIFACTS_DIR / "learning_log.json"
        last_run = None
        if log_path.exists():
            try:
                log = json.loads(log_path.read_text(encoding="utf-8"))
                last_run = log.get("last_run")
            except Exception:
                last_run = None
        triggered, reason = _should(sig, last_run)
        return {
            "signal": sig,
            "last_run": last_run,
            "retrain_triggered": triggered,
            "trigger_reason": reason,
        }

    def trigger_retrain(self) -> dict:
        """Manually trigger a retrain. Returns immediately with status=started;
        the actual retrain runs in a background thread (so the API call
        doesn't block for 10+ minutes while the pipeline retrains).

        The retrain progress is written to artifacts/last_retrain.json —
        the OpsView can poll /api/accuracy and /api/learning/signal to see
        the updated MAE drift + retrain_triggered flag.
        """
        import threading
        # If a retrain is already in progress, don't start another
        out_path = C.ARTIFACTS_DIR / "last_retrain.json"
        if out_path.exists():
            try:
                prev = json.loads(out_path.read_text(encoding="utf-8"))
                if prev.get("status") == "running":
                    # ts > 60s ago → assume stuck, allow re-trigger
                    if time.time() - float(prev.get("triggered_at", 0)) < 60:
                        return {
                            "status": "running",
                            "elapsed_s": round(time.time() - float(prev["triggered_at"]), 1),
                            "stages": prev.get("stages"),
                            "message": "a retrain is already in progress",
                        }
            except Exception:
                pass
        log = {
            "triggered_at": time.time(),
            "status": "running",
            "stages": ["data_prep", "features", "train", "evaluate"],
        }
        out_path.write_text(json.dumps(log, indent=2), encoding="utf-8")

        def _run():
            from pathlib import Path
            stages = log["stages"]
            t0 = time.time()
            for stage in stages:
                try:
                    r = subprocess.run(
                        [sys.executable, "-m", f"src.{stage}"],
                        cwd=str(C.ROOT), capture_output=True, text=True, timeout=600)
                    if r.returncode != 0:
                        log.update({"status": "failed", "stage": stage,
                                    "stderr": r.stderr[-800:]})
                        out_path.write_text(json.dumps(log, indent=2), encoding="utf-8")
                        return
                except Exception as exc:
                    log.update({"status": "failed", "stage": stage,
                                "stderr": str(exc)})
                    out_path.write_text(json.dumps(log, indent=2), encoding="utf-8")
                    return
            log.update({"status": "ok", "elapsed_s": round(time.time() - t0, 1)})
            out_path.write_text(json.dumps(log, indent=2), encoding="utf-8")

        threading.Thread(target=_run, daemon=True).start()
        return {"status": "running", "elapsed_s": 0.0, "stages": log["stages"]}

    # ----------------------------------------------------------------- debrief
    def debrief(self, event_id: str) -> dict:
        """Plan vs actual debrief for one incident (spec 06)."""
        with self.ledger._conn() as c:
            pred_row = c.execute(
                "SELECT payload_json FROM predictions WHERE id = ?",
                (event_id,)).fetchone()
            out_row = c.execute(
                "SELECT * FROM outcomes WHERE event_id = ?",
                (event_id,)).fetchone()
        if pred_row is None and out_row is None:
            raise ServiceError(f"no record for event_id: {event_id}")
        plan = json.loads(pred_row["payload_json"]) if pred_row else {}
        actual = dict(out_row) if out_row else {}
        variance = {}
        if "actual_p50_min" in actual and "p50" in plan:
            variance["p50_min"] = round(actual["actual_p50_min"] - plan["p50"], 1)
        if "actual_closure" in actual and "closure_prob" in plan:
            variance["closure_prob"] = round(
                (1 if actual["actual_closure"] else 0) - plan["closure_prob"], 3)
        return {"event_id": event_id, "plan": plan, "actual": actual, "variance": variance}
