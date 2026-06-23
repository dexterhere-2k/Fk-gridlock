"""NexGen — 06: 4-minute demo script (per spec 06 §"Demo script").

Walks the audience through every major component of the system in
exactly the cadence called out in the spec. Each step is a self-contained
call to the live API + a print of the result + a `time.sleep(0.3)`
between calls so the script reads as a paced narration.

The script is **runnable** — `python -m src.demo_script` — and produces
`artifacts/demo_run.json` with every step's result, so a judge can
re-run it offline and compare numbers.

Step plan (per spec 06):
  0:00 — REFRAME: the honesty slide (no severity label, no leakage).
  0:45 — UNPLANNED: clearance-risk on a real event (with Kannada demo).
  1:45 — CONCURRENT: ILP allocation across 3 events.
  2:45 — PLANNED: time-phased schedule + diversion route.
  3:30 — DEBRIEF: log outcomes + show learning loop signal.
  3:50 — HONEST CLOSE: what production needs (ASTraM cameras + Mappls).
"""
from __future__ import annotations
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

from . import config as C

API = "http://127.0.0.1:8000"
RUN_OUT = C.ARTIFACTS_DIR / "demo_run.json"


def _step(label: str, fn, budget_s: float = 60.0):
    t0 = time.time()
    print(f"\n  ┌─ {label}")
    try:
        out = fn()
    except Exception as exc:
        out = {"error": str(exc)}
        print(f"  │  ⚠ error: {exc}")
    dt = time.time() - t0
    print(f"  └─ done in {dt:.1f}s")
    time.sleep(0.3)
    return out


def _post(path: str, body: dict) -> dict:
    r = httpx.post(f"{API}{path}", json=body, timeout=30.0)
    r.raise_for_status()
    return r.json()


def _get(path: str) -> dict:
    r = httpx.get(f"{API}{path}", timeout=30.0)
    r.raise_for_status()
    return r.json()


def step0_reframe():
    """0:00 — the honesty slide."""
    print("  │  Finding #1: this dataset is an INCIDENT LOG, not a")
    print("  │  congestion-impact dataset. There is no severity label,")
    print("  │  no closure ground truth, no queue length. We predict the")
    print("  │  ONE real label — clearance time — with calibrated uncertainty.")
    print("  │")
    print("  │  Finding #2: 'priority' column is 99.9% deterministic from")
    print("  │  'corridor' — banned everywhere. 6 exceptions in 8,151 rows.")
    print("  │")
    print("  │  Finding #3: only 31% of rows have a valid duration. The")
    print("  │  other 69% are right-censored → we use CoxPH survival on")
    print("  │  ALL 8,171 rows instead of throwing them away.")
    return {"step": "reframe", "claims": ["no_severity_label",
                                            "priority_is_corridor",
                                            "31%_label_coverage",
                                            "69%_survival_censored"]}


def step1_unplanned():
    """0:45 — clearance-risk on a tree fall (the Kannada demo)."""
    # English case
    out_en = _post("/api/clearance-risk", {
        "corridor": "Bellary Road 1", "event_cause": "tree_fall",
        "description": "huge tree fallen blocking the road crane needed",
        "datetime": "2024-04-01T05:00:00+05:30",
    })
    print(f"  │  Bellary Road 1 / tree_fall (English):")
    print(f"  │    P10={out_en['p10']}m  P50={out_en['p50']}m  P90={out_en['p90']}m")
    print(f"  │    closure_prob={out_en['closure_prob']:.0%}  "
          f"tier={out_en['closure_tier']}  confidence={out_en['confidence']}")
    if out_en.get("nlp_cues", {}).get("needs_crane_tow"):
        print(f"  │    ✓ NLP parsed: 'needs crane/tow'")

    # Kannada case — the spec 02 §2b showpiece
    out_kn = _post("/api/clearance-risk", {
        "corridor": "Tumkur Road", "event_cause": "vehicle_breakdown",
        "description": "ನಮಸ್ತೆ ಸರ್ ಬಸ್ ಆಫ್ ರೋಡ್ ಆಗಿರುತ್ತದೆ ಕ್ರೇನ್ ಬೇಕು ಒಂದು ಲೇನ್ ಬ್ಲಾಕ್",
        "datetime": "2024-04-01T20:00:00+05:30",
    })
    print(f"  │  Tumkur Road / vehicle_breakdown (Kannada):")
    print(f"  │    P10={out_kn['p10']}m  P50={out_kn['p50']}m  P90={out_kn['p90']}m")
    kn = out_kn.get("nlp_cues", {})
    print(f"  │    ✓ Kannada parsed: lanes_blocked={kn.get('lanes_blocked')}, "
          f"needs_crane={kn.get('needs_crane_tow')}, "
          f"kannada_cues={kn.get('kannada_cues')}")
    print(f"  │    because: {out_kn['because'][-1] if out_kn.get('because') else '—'}")

    return {"step": "unplanned", "en": out_en, "kn": out_kn}


def step2_concurrent():
    """1:45 — ILP allocation across 3 concurrent events (the wow)."""
    events = [
        {"id": "DEMO-E1", "corridor": "Mysore Road", "cause": "tree_fall",
         "p50_min": 60, "p90_min": 240, "closure_prob": 0.55,
         "corridor_risk": 0.42, "is_planned": False},
        {"id": "DEMO-E2", "corridor": "Tumkur Road", "cause": "vip_movement",
         "p50_min": 40, "p90_min": 90, "closure_prob": 0.35,
         "corridor_risk": 0.13, "is_planned": True},
        {"id": "DEMO-E3", "corridor": "ORR East 1", "cause": "accident",
         "p50_min": 50, "p90_min": 120, "closure_prob": 0.20,
         "corridor_risk": 0.27, "is_planned": False},
    ]
    units = [
        {"id": f"U{i:03d}", "station": "Yeshwanthpura PS", "agency": "police"}
        for i in range(1, 21)
    ]
    out = _post("/api/optimize", {"events": events, "units": units, "pool_cap": 20})
    print(f"  │  3 concurrent events, 20 officers (tight budget):")
    print(f"  │    ILP status: {out['status']}  solved in {out['solve_time_s']*1000:.0f}ms")
    for eid, a in out["events"].items():
        print(f"  │    {eid}: officers={a['officers']:>2d}  "
              f"barricades={a['barricades']:>2d}  "
              f"diversion={a['diversion_route'] or '—'}")
    if out["summary"].get("pre_positioned_corridors"):
        print(f"  │    ⚠ cascade pre-positioned: "
              f"{len(out['summary']['pre_positioned_corridors'])} corridors")
    return {"step": "concurrent", "events": events,
            "units": len(units), "result": out}


def step3_planned():
    """2:45 — planned-event mode: time-phased schedule."""
    out = _get("/api/schedule/PLANNED-DEMO")
    print(f"  │  schedule for PLANNED-DEMO:")
    for s in out["slots"]:
        print(f"  │    T{s['time_offset_min']:>+5d}m  {s['action']:<22s}  "
              f"×{s['units']}  ({s['reason']})")
    return {"step": "planned", "result": out}


def step4_debrief():
    """3:30 — log outcomes + show learning loop signal."""
    # log 3 sample outcomes
    for event_id, actual_p50, closure in [
        ("DEMO-E1", 78, True),     # tree fall took longer + closed road
        ("DEMO-E2", 35, False),    # VIP convoy cleared quickly
        ("DEMO-E3", 65, True),     # accident on ORR East 1
    ]:
        r = httpx.post(f"{API}/api/outcome", json={
            "event_id": event_id,
            "actual_p50_min": actual_p50,
            "actual_closure": closure,
            "actual_officers_deployed": 8,
            "actual_barricades": 4,
            "notes": f"demo: {event_id} actually took {actual_p50}min",
        }, timeout=10.0)
        r.raise_for_status()
        print(f"  │  logged outcome for {event_id}: actual_p50={actual_p50}m")
    # get the accuracy readout
    acc = _get("/api/accuracy")
    print(f"  │  learning loop signal: {acc['n_outcomes']} outcomes logged, "
          f"P50 MAE = {acc['p50_mae_min']}m, "
          f"closure acc = {acc['closure_accuracy']:.2f}")
    return {"step": "debrief", "outcomes_logged": 3, "accuracy": acc}


def step5_close():
    """3:50 — honest close (what production needs)."""
    print("  │  In production, NexGen needs:")
    print("  │  • ASTraM's 9,000 cameras + congestion feed (real-time)")
    print("  │  • Mappls live ETA + diversion routing (spec 07)")
    print("  │  • Festival / holiday calendar (rain → water_logging)")
    print("  │  • Active RL agent trained against a validated sim (spec 03 §RL)")
    print("  │")
    print("  │  What's real TODAY: the 5-view SPA, the ILP allocation,")
    print("  │  the survival model, the NLP layer, the cascade pre-alert,")
    print("  │  and the live learning loop. All on a single Python backend")
    print("  │  + a SQLite file + a Vite SPA. Runs on the spec's potato PC.")
    return {"step": "close"}


def main():
    print("=" * 64)
    print("  NexGen — 4-minute demo script (per spec 06)")
    print(f"  API base: {API}")
    print(f"  run at:   {datetime.now(tz=timezone.utc).isoformat(timespec='seconds')}")
    print("=" * 64)
    # pre-flight: API reachable?
    try:
        _get("/api/health")
    except Exception as exc:
        print(f"  ⚠ API not reachable at {API} — start uvicorn first.")
        print(f"    {exc}")
        sys.exit(1)

    results = {}
    results["step0_reframe"]   = _step("STEP 0:00  REFRAME (honesty slide)",      step0_reframe)
    results["step1_unplanned"] = _step("STEP 0:45  UNPLANNED INCIDENT (predict)",   step1_unplanned)
    results["step2_concurrent"]= _step("STEP 1:45  CONCURRENT EVENTS (ILP)",        step2_concurrent)
    results["step3_planned"]   = _step("STEP 2:45  PLANNED MODE (schedule)",        step3_planned)
    results["step4_debrief"]   = _step("STEP 3:30  DEBRIEF (learning loop)",        step4_debrief)
    results["step5_close"]     = _step("STEP 3:50  HONEST CLOSE",                   step5_close)

    RUN_OUT.write_text(json.dumps({
        "ran_at": datetime.now(tz=timezone.utc).isoformat(),
        "api_base": API,
        "results": results,
    }, indent=2, default=str), encoding="utf-8")
    print(f"\n  full transcript -> {RUN_OUT}")


if __name__ == "__main__":
    main()
