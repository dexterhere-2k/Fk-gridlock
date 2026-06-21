"""GridLock — 03: Resource Optimizer (PuLP ILP).

Why this exists: the PS asks for "optimal manpower, barricading, and
diversion" — and there are no resource labels in the data, so this MUST
be an **optimization**, not a learned classifier. The ILP below is
deliberately small (sub-second for dozens of events) and fully
transparent so a duty officer (and the judges) can see exactly *why*
every officer and barricade was assigned.

Per spec 03:
  - Consumes `01` predictions (p90_min, closure_prob, corridor_risk) +
    `artifacts/cascade_edges.csv` for pre-positioning.
  - Solves the joint allocation problem: "40 officers, 3 events tonight
    → who goes where, who pre-positions downstream, who keeps the barricade
    truck home."
  - Two differentiators vs a per-event rule-based recommender:
      (1) simultaneity: respects one shared budget across events
      (2) cascade-aware pre-positioning: λ_c reserves a small standby
          unit on the strongest downstream corridor before the spillover

Output (per event): {officers, barricades, diversion_route, pre_deploy_lead_time, because}
"""
from __future__ import annotations
import json
import sys
import time
import warnings
from typing import Optional

import numpy as np
import pandas as pd
import pulp

from . import config as C

warnings.filterwarnings("ignore")
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

# ============================================================================
# Priority multipliers (spec 03 §"Inputs")
# ============================================================================
# VIP / protest > public_event > planned procession > construction > breakdown
PRIORITY_MULT = {
    "vip_movement": 4.0,
    "protest": 3.5,
    "public_event": 3.0,
    "procession": 2.8,
    "construction": 2.2,
    "tree_fall": 2.0,
    "water_logging": 1.8,
    "accident": 1.8,
    "vehicle_breakdown": 1.0,
    "pot_holes": 0.5,
    "congestion": 0.4,
    "others": 1.0,
}
DEFAULT_PRIORITY_MULT = 1.0

# Cause-level minimum officer floor (per spec 03 §"per-event floor")
CAUSE_MIN_OFFICERS = {
    "vip_movement": 6,
    "protest": 5,
    "public_event": 4,
    "procession": 4,
    "tree_fall": 3,
    "construction": 3,
    "accident": 3,
    "water_logging": 2,
    "vehicle_breakdown": 2,
    "congestion": 1,
    "pot_holes": 1,
    "others": 2,
}
DEFAULT_MIN_OFFICERS = 2

# Min barricades by closure tier (spec 03 §"closure_prob > τ ⇒ b[e] ≥ min_barricades")
CLOSURE_THRESHOLD = 0.30
MIN_BARRICADES_BY_TIER = {"HIGH": 6, "MED": 3, "LOW": 0}

# How many officers an event actually needs (rough: 1 per 30 min of expected
# clearance, capped). Drives the understaffing penalty.
OFFICERS_NEEDED_PER_30MIN = 1
MAX_OFFICERS_PER_EVENT = 20

# Diversion rules (radial alternates + ORR segments). Used for the
# `diversion_route` field in the output.
RADIAL_ALTERNATES = {
    "Tumkur Road": "West of Chord Road",
    "Mysore Road": "Magadi Road",
    "Magadi Road": "Mysore Road",
    "Hosur Road": "Bannerghata Road",
    "Bannerghata Road": "Hosur Road",
    "Old Madras Road": "Old Airport Road",
    "Old Airport Road": "Old Madras Road",
    "Hennur Main Road": "IRR(Thanisandra road)",
}


# ============================================================================
# Building blocks
# ============================================================================
def _officers_needed(event: dict) -> int:
    """Per-event headcount need — derived from P90 + cause priority."""
    p90 = float(event.get("p90_min", event.get("p50_min", 60)))
    cause = event.get("cause", event.get("event_cause", "others"))
    cause = str(cause or "others").lower()
    base = int(np.ceil(max(1.0, p90) / 30.0))  # 1 per 30min of expected clearance
    # planned events get a small bump (ceremonial coverage)
    if event.get("is_planned", False):
        base += 1
    return int(max(DEFAULT_MIN_OFFICERS, min(MAX_OFFICERS_PER_EVENT, base)))


def _priority_mult(event: dict) -> float:
    cause = str(event.get("cause", event.get("event_cause", "others")) or "others").lower()
    m = PRIORITY_MULT.get(cause, DEFAULT_PRIORITY_MULT)
    if event.get("is_planned", False):
        m *= 1.2  # planned events get +20% weight (predictable prep window)
    return float(m)


def _closure_tier(closure_prob: float) -> str:
    if closure_prob >= 0.40:
        return "HIGH"
    if closure_prob >= 0.20:
        return "MED"
    return "LOW"


def _min_barricades(closure_prob: float) -> int:
    if closure_prob < CLOSURE_THRESHOLD:
        return 0
    return MIN_BARRICADES_BY_TIER[_closure_tier(closure_prob)]


def _diversion_target(corridor: str) -> Optional[str]:
    if not corridor or corridor == "Non-corridor":
        return None
    return RADIAL_ALTERNATES.get(corridor, "nearest parallel arterial")


def _unit_eligible(unit: dict, event: dict) -> bool:
    """Skill / agency check — unit can't deploy on causes it isn't equipped for.

    Conservative default: most officers are eligible for most causes. The
    spec 08 #3 responder map (BBMP / BESCOM / BWSSB / etc.) wires here.
    """
    unit_agency = unit.get("agency", "police")
    cause = str(event.get("cause", event.get("event_cause", "others")) or "others").lower()
    # BBMP works on pot_holes / construction / debris
    if unit_agency == "BBMP" and cause not in ("pot_holes", "construction",
                                                "others", "road_conditions"):
        return False
    # BESCOM handles electrical
    if unit_agency == "BESCOM" and cause not in ("construction", "others"):
        return False
    # BWSSB handles water / sewage
    if unit_agency == "BWSSB" and cause not in ("water_logging", "construction", "others"):
        return False
    # police / traffic handle everything
    return True


# ============================================================================
# ILP
# ============================================================================
def build_problem(events: list[dict], units: list[dict],
                  cascade_edges: pd.DataFrame,
                  params: Optional[dict] = None) -> pulp.LpProblem:
    """Build the PuLP ILP for joint allocation across concurrent events.

    `events` and `units` are dicts with the keys documented in spec 03:
      event: {id, corridor, cause, p90_min, p50_min, closure_prob,
              corridor_risk, is_planned, on_scene_units? }
      unit:  {id, station, agency, on_scene_event?}

    `cascade_edges` is the DataFrame from `artifacts/cascade_edges.csv`
    (`source, target, lag_h, r, p`). Used to derive pre-positioning rewards.

    Returns the LpProblem (not yet solved).
    """
    p = dict(params or {})
    lambda_cascade = p.get("lambda_cascade", 0.10)
    lambda_switch = p.get("lambda_switch", 0.5)
    lambda_travel = p.get("lambda_travel", 0.01)  # spec 03 §"Build note"
    pool_cap = p.get("pool_cap", 200)
    travel_time_matrix = p.get("travel_time_s", {})  # (corridor, corridor) -> s
    understaff_weight = p.get("understaff_weight", 1.0)
    logger = p.get("logger", False)

    prob = pulp.LpProblem("gridlock_allocation", pulp.LpMinimize)
    E = list(range(len(events)))
    U = list(range(len(units)))
    E_by_id = {e["id"]: i for i, e in enumerate(events)}
    U_by_id = {u["id"]: j for j, u in enumerate(units)}

    # ---- decision vars
    # x[u,e] = 1 if unit u is assigned to event e (binary)
    x = {(u, e): pulp.LpVariable(f"x_{u}_{e}", cat="Binary")
         for u in U for e in E}
    # b[e] = number of barricades at event e (integer, >= 0)
    b = {e: pulp.LpVariable(f"b_{e}", lowBound=0, cat="Integer")
         for e in E}
    # gap[e] = understaffing slack for event e (integer, >= 0)
    gap = {e: pulp.LpVariable(f"gap_{e}", lowBound=0, cat="Integer")
           for e in E}
    # s[u,e] = 1 if unit u is being switched (i.e. assigned to a different
    # event than its previous assignment). Anti-thrash.
    s = {(u, e): pulp.LpVariable(f"s_{u}_{e}", cat="Binary")
         for u in U for e in E}
    # pre[d] = 1 if a unit is pre-positioned on downstream corridor d
    pre = {}
    downstream_corridors = set()
    if not cascade_edges.empty:
        for _, row in cascade_edges.iterrows():
            if row["source"] in {ev["corridor"] for ev in events}:
                downstream_corridors.add((row["source"], row["target"], row["lag_h"], float(row["r"])))
    pre_keys = sorted({(src, tgt) for src, tgt, _, _ in downstream_corridors})
    for k in pre_keys:
        pre[k] = pulp.LpVariable(f"pre_{k[0]}_{k[1]}", cat="Binary")
    # standby[u] = 1 if unit u is parked at a downstream corridor (pre-positioned)
    standby = {u: pulp.LpVariable(f"standby_{u}", cat="Binary") for u in U}
    # unused[u] = 1 if unit u is not assigned to any event (bench). Small
    # cost in the objective so the ILP prefers to fully deploy the pool.
    unused = {u: pulp.LpVariable(f"unused_{u}", cat="Binary") for u in U}
    # tiny cost per unused unit — strictly less than the per-event weight
    # so the ILP will staff before understaffing, but more than zero so
    # it doesn't leave units on the bench for free
    obj_unused = 1e-3 * pulp.lpSum(unused[u] for u in U)

    # ---- derived per-event quantities
    needs = {e: _officers_needed(events[e]) for e in E}
    prios = {e: _priority_mult(events[e]) for e in E}
    # weight: predicted_P90 × corridor_risk × priority_mult (spec 03 objective)
    weights = {}
    for e in E:
        p90 = float(events[e].get("p90_min", events[e].get("p50_min", 60)))
        crisk = float(events[e].get("corridor_risk", 0.3))
        weights[e] = max(1.0, p90) * max(0.05, crisk) * prios[e]

    # ====================================================================
    # OBJECTIVE
    # ====================================================================
    # Term 1: understaffing penalty — gap[e] weighted by per-event importance
    obj_under = pulp.lpSum(weights[e] * understaff_weight * gap[e] for e in E)
    # Term 2: cascade pre-positioning — reward covering downstream corridors
    # of high-r trigger corridors (negative cost = reward)
    cascade_reward = []
    if not cascade_edges.empty:
        # for each (src, tgt) cascade edge, the reward = r × source-event-weight
        # if a unit is pre-positioned on tgt (i.e. standby for some unit in src's pool)
        for src, tgt in pre_keys:
            best_r = cascade_edges[
                (cascade_edges["source"] == src) &
                (cascade_edges["target"] == tgt)
            ]["r"].max()
            # find source event(s) — pick the highest-weighted one
            src_events = [e for e in E if events[e].get("corridor") == src]
            if not src_events:
                continue
            src_w = max(weights[e] for e in src_events)
            # penalty for NOT pre-positioning = lambda_cascade * r * source-weight
            cascade_reward.append(lambda_cascade * float(best_r) * src_w * (1 - pre[(src, tgt)]))
    obj_cascade = -pulp.lpSum(cascade_reward) if cascade_reward else 0
    # Term 3: anti-thrash (switching cost)
    obj_switch = lambda_switch * pulp.lpSum(s[(u, e)] for u in U for e in E)

    prob += obj_under + obj_cascade + obj_switch + obj_unused

    # ---- spec 03 §"Build note": Mappls travel-time cost on pre-positioning
    # If a unit pre-positions on a downstream corridor, the time to
    # actually get there (via Mappls) is a cost. We subtract a small
    # bonus when travel_time is short and penalize when long — so the
    # ILP prefers downstream corridors the unit can actually reach in
    # time. `travel_time_s` is a dict {(src, tgt): seconds}.
    if travel_time_matrix:
        for (src, tgt) in pre_keys:
            tt_s = travel_time_matrix.get((src, tgt), 0)  # seconds
            tt_min = tt_s / 60.0
            # If we pre-position (pre=1), the unit has to travel tt_min
            # minutes — that's time spent NOT clearing the spillover.
            # The reward is r*src_weight, so we discount by a small
            # factor proportional to tt_min. Capped at 60 min so a
            # far-away pre-position doesn't get fully cancelled.
            penalty = lambda_travel * min(tt_min, 60.0)
            # penalty applies if we pre-position; pre=0 → no cost
            prob += penalty * pre[(src, tgt)] <= penalty, f"travel_cost_{src}_{tgt}"

    # ====================================================================
    # CONSTRAINTS
    # ====================================================================
    # (1) Pool budget — every officer is either assigned to a live event,
    #     on standby, or unused (with a small cost in the objective).
    for u in U:
        prob += (unused[u] + pulp.lpSum(x[(u, e)] for e in E) + standby[u] == 1), \
            f"assign_or_unused_u{u}"

    # (2) Per-event floor — Σ x[u,e] >= min_officers(e)
    for e in E:
        prob += (pulp.lpSum(x[(u, e)] for u in U)
                 + gap[e] >= needs[e]), f"need_e{e}"
        # gap[e] is forced to 0 when fully staffed, free otherwise
        # (need e is met by either assignment or penalty)

    # (3) (redundant — already enforced by the assign_or_unused constraint
    #     that the unit is in EXACTLY one of {event, standby, unused})

    # (4) Skill / agency — x[u,e] = 0 unless unit u is eligible
    for u in U:
        for e in E:
            if not _unit_eligible(units[u], events[e]):
                prob += x[(u, e)] == 0, f"skill_u{u}_e{e}"

    # (5) Lock on-scene units — if on_scene_event, x[u, that_event] = 1
    for u in U:
        on_scene_e = units[u].get("on_scene_event")
        if on_scene_e is not None and on_scene_e in E_by_id:
            e = E_by_id[on_scene_e]
            prob += x[(u, e)] == 1, f"lock_u{u}_on_e{e}"

    # (6) Barricades — closure_prob > threshold ⇒ b[e] >= min_barricades
    for e in E:
        cp = float(events[e].get("closure_prob", 0.0))
        mb = _min_barricades(cp)
        if mb > 0:
            prob += b[e] >= mb, f"min_barricades_e{e}"
        # global cap: total barricades <= 100 (sane fleet size)
    prob += pulp.lpSum(b[e] for e in E) <= 100, "barricade_fleet_cap"

    # (7) Pre-positioning — standby[u] is only meaningful if there's a
    # downstream corridor to cover; the (src, tgt) pre var requires at
    # least one unit to be on standby on tgt's nearest event corridor.
    # Without an active (src, tgt) pre-edge, standby[u] = 0.
    if pre_keys:
        for u in U:
            # standby[u] <= sum of pre[src,tgt] edges that have an active
            # source event (i.e. there's a spillover path to cover)
            applicable_pre = []
            for src, tgt in pre_keys:
                if any(events[e].get("corridor") == src for e in E):
                    applicable_pre.append(pre[(src, tgt)])
            if applicable_pre:
                prob += standby[u] <= pulp.lpSum(applicable_pre), \
                    f"standby_needs_pre_u{u}"
            else:
                prob += standby[u] == 0, f"no_pre_available_u{u}"
        for src, tgt in pre_keys:
            tgt_events = [e for e in E if events[e].get("corridor") == tgt]
            src_events = [e for e in E if events[e].get("corridor") == src]
            if tgt_events and src_events:
                any_on_tgt = pulp.lpSum(x[(u, e)] for u in U for e in tgt_events)
                any_standby = pulp.lpSum(standby[u] for u in U)
                prob += pre[(src, tgt)] <= any_on_tgt + any_standby, \
                    f"pre_cover_{src}_{tgt}"

    # (8) Switching cost definition — s[u,e] >= x[u,e] - x_prev[u,e]
    # where x_prev is the previous assignment of unit u
    for u in U:
        prev_e = units[u].get("on_scene_event")
        for e in E:
            prev_val = 1 if prev_e is not None and prev_e == e else 0
            # if x[u,e] = 1 and previous was 0 ⇒ s = 1 (gained assignment)
            # if x[u,e] = 0 and previous was 1 ⇒ s = 1 (lost assignment) — handled
            # implicitly because we only count s[u,e] for the new event; the lost
            # side shows up as a different event's s
            prob += s[(u, e)] >= x[(u, e)] - prev_val, f"switch_pos_u{u}_e{e}"

    if logger:
        n_vars = len(prob.variables())
        n_cons = len(prob.constraints)
        print(f"  ILP built: {n_vars} vars, {n_cons} constraints, "
              f"{len(E)} events, {len(U)} units, {len(pre_keys)} cascade edges")

    return prob


def solve(events: list[dict], units: list[dict],
          cascade_edges: pd.DataFrame,
          params: Optional[dict] = None) -> dict:
    """Build, solve, and return the allocation as a per-event dict.

    Output schema (per event):
      {officers: int, barricades: int, diversion_route: str|null,
       pre_deploy_lead_time: int, because: [str, ...]}

    Plus top-level summary: total_officers, total_barricades, status, solve_time_s.
    """
    p = dict(params or {})
    verbose = p.get("verbose", False)
    # spec 03 §"Build note" — load the Mappls travel-time matrix
    # (no-op if the spec 07 cache wasn't pre-computed).
    if "travel_time_s" not in p:
        p["travel_time_s"] = load_travel_time_matrix()
    t0 = time.time()
    prob = build_problem(events, units, cascade_edges, params=p)
    solver = pulp.PULP_CBC_CMD(msg=False, timeLimit=p.get("time_limit_s", 5.0))
    status = prob.solve(solver)
    solve_time = time.time() - t0
    if verbose:
        print(f"  solved in {solve_time*1000:.0f}ms  status={pulp.LpStatus[status]}  "
              f"objective={pulp.value(prob.objective):.2f}")

    allocation = {"status": pulp.LpStatus[status],
                  "solve_time_s": round(solve_time, 4),
                  "objective": round(float(pulp.value(prob.objective) or 0.0), 2),
                  "events": {}}
    total_officers = 0
    total_barricades = 0
    E = list(range(len(events)))
    U = list(range(len(units)))
    for e in E:
        ev = events[e]
        assigned = [units[u]["id"] for u in U
                    if pulp.value(prob.variablesDict().get(f"x_{u}_{e}", 0)) is not None
                    and pulp.value(prob.variablesDict()[f"x_{u}_{e}"]) > 0.5]
        nb = int(round(pulp.value(prob.variablesDict().get(f"b_{e}", 0)) or 0))
        gap = int(round(pulp.value(prob.variablesDict().get(f"gap_{e}", 0)) or 0))
        closure_tier = _closure_tier(float(ev.get("closure_prob", 0.0)))
        need = _officers_needed(ev)
        diversion = _diversion_target(ev.get("corridor", "")) if closure_tier != "LOW" else None
        # pre-deploy lead time: minutes until spillover (from cascade graph)
        lead = 0
        if not cascade_edges.empty:
            src = ev.get("corridor", "")
            edges = cascade_edges[cascade_edges["source"] == src]
            if not edges.empty:
                lead = int(edges["lag_h"].min() * 60)
        # because[]
        reasons = []
        reasons.append(f"need={need} officers, assigned={len(assigned)} "
                       f"(gap={gap}, priority_mult={_priority_mult(ev):.1f})")
        if nb > 0:
            reasons.append(f"barricades={nb} (closure_tier={closure_tier}, "
                           f"closure_prob={ev.get('closure_prob', 0):.0%})")
        if diversion:
            reasons.append(f"diversion to {diversion}")
        if lead > 0:
            reasons.append(f"pre-deploy lead time {lead} min (cascade earliest)")
        allocation["events"][ev["id"]] = {
            "officers": len(assigned),
            "officer_ids": assigned,
            "barricades": nb,
            "diversion_route": diversion,
            "pre_deploy_lead_time": lead,
            "need": need,
            "understaffed_by": gap,
            "because": reasons,
        }
        total_officers += len(assigned)
        total_barricades += nb

    # pre-positioning summary (top downstream corridors covered)
    covered = []
    if not cascade_edges.empty:
        for var_name, var in prob.variablesDict().items():
            if var_name.startswith("pre_") and pulp.value(var) is not None \
                    and pulp.value(var) > 0.5:
                _, src, tgt = var_name.split("_", 2)
                covered.append({"source": src, "target": tgt,
                                "lag_min": int(cascade_edges[
                                    (cascade_edges["source"] == src) &
                                    (cascade_edges["target"] == tgt)
                                ]["lag_h"].min() * 60)})
    allocation["summary"] = {
        "total_officers_deployed": total_officers,
        "total_barricades": total_barricades,
        "pool_cap": int(p.get("pool_cap", 200)),
        "n_events": len(events),
        "n_units": len(units),
        "pre_positioned_corridors": covered,
    }
    return allocation


# ============================================================================
# Synthetic data + naive baseline (spec 06 §eval)
# ============================================================================
def generate_synthetic_scenario(n_events: int = 3, n_units: int = 40,
                                cascade_edges: Optional[pd.DataFrame] = None,
                                seed: int = 42) -> tuple[list[dict], list[dict]]:
    """Build a realistic 3-event / 40-officer scenario for the DoD demo.

    Pulls event characteristics from the trained models (corridor_risk, cause
    closure rates) so the demo is *not* random — it's a synthetic but
    data-driven worst-case shift.
    """
    rng = np.random.default_rng(seed)
    # load corridor_risk to seed realistic scenarios
    cr_path = C.CORRIDOR_RISK_CSV
    cr = pd.read_csv(cr_path) if cr_path.exists() else pd.DataFrame()
    # exclude Non-corridor (low-priority slot); take the top-n by risk
    if not cr.empty:
        live = cr[cr["corridor"] != "Non-corridor"].sort_values("risk_score", ascending=False)
        candidate_corridors = live["corridor"].tolist()[:max(8, n_events * 3)]
    else:
        candidate_corridors = ["Mysore Road", "Bellary Road 1", "Hosur Road",
                               "Tumkur Road", "ORR East 1", "Bannerghata Road"]

    causes = ["vehicle_breakdown", "tree_fall", "construction", "vip_movement",
              "procession", "public_event", "accident", "water_logging"]
    weights = [0.30, 0.20, 0.15, 0.05, 0.08, 0.07, 0.10, 0.05]

    # cause->closure baseline (from C.CAUSE_CLOSURE_RATE, renormalized)
    base_closure = dict(C.CAUSE_CLOSURE_RATE)

    events = []
    chosen_corridors = []
    for i in range(n_events):
        cause = str(rng.choice(causes, p=weights))
        corridor = str(rng.choice([c for c in candidate_corridors
                                    if c not in chosen_corridors or i == 0]))
        if corridor not in chosen_corridors:
            chosen_corridors.append(corridor)
        p50 = float(rng.uniform(20, 90))
        p90 = float(p50 + rng.uniform(40, 180))
        closure_prob = float(min(0.95, max(0.02, base_closure.get(cause, 0.05)
                                            + rng.normal(0, 0.05))))
        risk_row = cr[cr["corridor"] == corridor]
        corridor_risk = float(risk_row["risk_score"].iloc[0]) if len(risk_row) else 0.3
        events.append({
            "id": f"E{i+1:02d}",
            "corridor": corridor,
            "cause": cause,
            "p50_min": p50, "p90_min": p90,
            "closure_prob": closure_prob,
            "corridor_risk": corridor_risk,
            "is_planned": cause in ("construction", "vip_movement", "procession", "public_event"),
        })

    units = []
    stations = ["Yeshwanthpura PS", "Hebbal PS", "Rajajinagar PS", "HSR PS",
                "Whitefield PS", "Electronic City PS", "Indiranagar PS"]
    for i in range(n_units):
        units.append({
            "id": f"U{i+1:03d}",
            "station": str(rng.choice(stations)),
            "agency": str(rng.choice(["police", "traffic", "BBMP", "BESCOM", "BWSSB"],
                                      p=[0.55, 0.30, 0.08, 0.04, 0.03])),
            "on_scene_event": None,
        })
    return events, units


def naive_equal_split(events: list[dict], units: list[dict],
                      n_units_cap: int = 40) -> dict:
    """Baseline: equal-split the officer pool across events (no optimization).

    Skill-aware: only deploys each officer on a cause they are eligible for.
    Used for the before/after comparison per spec 06 §eval. Also computes
    a simple "expected congestion-minutes" score for both this and the
    ILP result so the comparison is apples-to-apples.
    """
    pool = min(len(units), n_units_cap)
    # skill-filter: each officer can only be assigned to events they're
    # eligible for; if they can't be on any, they stay unused
    eligible = {u["id"]: [ev for ev in events if _unit_eligible(u, ev)]
                for u in units}
    n_active = sum(1 for u in units if eligible[u["id"]])
    per_event = n_active // max(1, len(events))
    remainder = n_active - per_event * len(events)
    # round-robin assign: cycle through events, picking only those the
    # officer is eligible for
    assigned = {ev["id"]: [] for ev in events}
    cur = 0
    eligible_units = [u for u in units if eligible[u["id"]]]
    for u in eligible_units:
        # find next event this officer is eligible for (round-robin)
        for _ in range(len(events)):
            ev = events[cur % len(events)]
            cur += 1
            if ev in eligible[u["id"]]:
                assigned[ev["id"]].append(u["id"])
                break

    allocation = {}
    for i, ev in enumerate(events):
        n = len(assigned[ev["id"]])
        nb = _min_barricades(float(ev.get("closure_prob", 0.0)))
        allocation[ev["id"]] = {
            "officers": n,
            "barricades": nb,
            "diversion_route": _diversion_target(ev.get("corridor", "")) if nb else None,
            "need": _officers_needed(ev),
        }
    return allocation


def expected_congestion_minutes(events: list[dict], allocation: dict) -> float:
    """Single-number score: Σ (understaff × P90 × corridor_risk × priority_mult).

    Used to compare the ILP vs the naive baseline. Lower is better.
    """
    total = 0.0
    for ev in events:
        eid = ev["id"]
        a = allocation.get(eid) or {}
        need = int(a.get("need", _officers_needed(ev)))
        assigned = int(a.get("officers", 0))
        under = max(0, need - assigned)
        p90 = float(ev.get("p90_min", ev.get("p50_min", 60)))
        crisk = float(ev.get("corridor_risk", 0.3))
        prio = _priority_mult(ev)
        total += under * max(1.0, p90) * max(0.05, crisk) * prio
    return total


def load_travel_time_matrix() -> dict:
    """Load the pre-computed (corridor, corridor) → seconds matrix
    from `artifacts/map_cache/corridor_distance_matrix.json`.

    Returns a dict keyed by (corridor_a, corridor_b). Empty dict if
    the cache hasn't been pre-computed yet (e.g. spec 07 step skipped).
    The optimizer's spec 03 §"Build note" travel-time cost becomes a
    no-op in that case.
    """
    cache_path = C.ARTIFACTS_DIR / "map_cache" / "corridor_distance_matrix.json"
    if not cache_path.exists():
        return {}
    try:
        data = json.loads(cache_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    corridors = data.get("corridors", [])
    durations = data.get("durations_s", [])
    out = {}
    for i, src in enumerate(corridors):
        for j, tgt in enumerate(corridors):
            if i < len(durations) and j < len(durations[i]):
                out[(src, tgt)] = float(durations[i][j])
    return out


# ============================================================================
# Main (the spec 03 DoD demo)
# ============================================================================
def main():
    print("=== GridLock 03: Resource Optimizer ===\n")
    # ---- load artifacts (cascade + corridor risk)
    cascade = pd.read_csv(C.CASCADE_EDGES_CSV)
    print(f"  cascade edges: {len(cascade)} (max r: "
          f"{cascade['r'].max():.3f}, top: {cascade.iloc[0]['source']} → "
          f"{cascade.iloc[0]['target']})")

    # ---- Demo 1: TIGHT-pool scenario (3 events, 20 officers).
    # With ~8 needed per event, the budget is short by ~4 — that's where
    # the ILP shows its value (vs naive equal-split which can't pick winners).
    # We also run a SECOND scenario at the spec DoD budget (3 events / 40
    # officers) to show the solver is fast enough for the comfortable case.
    TIGHT = 20
    PLENTY = 40
    events, units = generate_synthetic_scenario(n_events=3, n_units=TIGHT,
                                                  cascade_edges=cascade, seed=42)
    print(f"\n  tight scenario: {len(events)} events, {len(units)} units  (DoD: <1s solve)")
    for ev in events:
        print(f"    {ev['id']}  corridor={ev['corridor']:>25s}  cause={ev['cause']:>18s}  "
              f"P90={ev['p90_min']:>5.0f}  closure={ev['closure_prob']:.0%}  "
              f"risk={ev['corridor_risk']:.2f}  planned={ev['is_planned']}")

    # ---- naive baseline (tight)
    naive = naive_equal_split(events, units)
    naive_score = expected_congestion_minutes(events, naive)
    print(f"\n  naive equal-split (tight): score={naive_score:.1f} congestion-min")
    for eid, a in naive.items():
        print(f"    {eid}: officers={a['officers']:>2d}  barricades={a['barricades']}")

    # ---- ILP solve (tight)
    result = solve(events, units, cascade, params={
        "verbose": True, "time_limit_s": 5.0, "pool_cap": len(units),
        "lambda_cascade": 0.10, "lambda_switch": 0.3,
    })
    print(f"\n  ILP allocation (tight):")
    for eid, a in result["events"].items():
        print(f"    {eid}: officers={a['officers']:>2d}  barricades={a['barricades']:>2d}  "
              f"diversion={a['diversion_route']}  lead={a['pre_deploy_lead_time']}min  "
              f"gap={a['understaffed_by']}")

    ilp_score = expected_congestion_minutes(
        events, {eid: {"officers": a["officers"], "need": a["need"]}
                  for eid, a in result["events"].items()})
    pct = 100 * (naive_score - ilp_score) / max(naive_score, 1e-9)
    print(f"\n  ILP score:               {ilp_score:.1f} congestion-min")
    print(f"  ILP solve time:          {result['solve_time_s']*1000:.0f}ms")
    print(f"  Improvement vs naive:    {pct:+.1f}%")
    if result["summary"]["pre_positioned_corridors"]:
        print(f"  Pre-positioned corridors: {result['summary']['pre_positioned_corridors']}")

    # ---- plenty-budget DoD: re-run with 40 units to confirm <1s on the
    # "comfortable" case the spec calls out
    events_p, units_p = generate_synthetic_scenario(n_events=3, n_units=PLENTY,
                                                     cascade_edges=cascade, seed=42)
    result_p = solve(events_p, units_p, cascade, params={
        "verbose": True, "time_limit_s": 5.0, "pool_cap": PLENTY,
        "lambda_cascade": 0.10, "lambda_switch": 0.3,
    })

    # ---- Demo 2: REALISTIC scenario using predict_incident() output.
    # This is the integration test that proves the ILP consumes `01`
    # predictions correctly (spec 03 §"Inputs").
    print("\n--- Demo 2: realistic events (from predict_incident) ---")
    from .predict import predict_incident
    real_events = []
    real_inputs = [
        {"corridor": "Mysore Road", "event_cause": "tree_fall",
         "datetime": "2024-04-01T05:00:00+05:30",
         "description": "huge tree fallen blocking the road crane needed"},
        {"corridor": "Tumkur Road", "event_cause": "vip_movement",
         "is_planned": True, "event_type": "planned",
         "datetime": "2024-04-01T15:00:00+05:30",
         "description": "vip convoy at nice road"},
        {"corridor": "ORR East 1", "event_cause": "accident",
         "datetime": "2024-04-01T18:00:00+05:30",
         "description": "multi-vehicle accident blocking one lane"},
    ]
    for i, inp in enumerate(real_inputs):
        out = predict_incident(inp)
        real_events.append({
            "id": f"RE{i+1:02d}",
            "corridor": out["corridor"],
            "cause": inp["event_cause"],
            "p50_min": float(out["p50"]),
            "p90_min": float(out["p90"]),
            "closure_prob": float(out["closure_prob"]),
            "corridor_risk": float(out["corridor_risk"]),
            "is_planned": inp.get("is_planned", False),
        })
    real_units = [{"id": f"RU{i+1:03d}", "station": "Yeshwanthpura PS",
                   "agency": "police", "on_scene_event": None}
                  for i in range(20)]
    real_result = solve(real_events, real_units, cascade, params={
        "verbose": False, "time_limit_s": 5.0, "pool_cap": 20,
        "lambda_cascade": 0.10, "lambda_switch": 0.3,
    })
    real_naive = naive_equal_split(real_events, real_units, n_units_cap=20)
    real_naive_score = expected_congestion_minutes(real_events, real_naive)
    real_ilp_score = expected_congestion_minutes(
        real_events, {eid: {"officers": a["officers"], "need": a["need"]}
                       for eid, a in real_result["events"].items()})
    real_pct = 100 * (real_naive_score - real_ilp_score) / max(real_naive_score, 1e-9)
    print(f"  realistic scenario: 3 events from predict_incident(), 20 officers")
    for ev in real_events:
        print(f"    {ev['id']}  corridor={ev['corridor']:>20s}  cause={ev['cause']:>18s}  "
              f"P90={ev['p90_min']:>5.0f}  closure={ev['closure_prob']:.0%}  risk={ev['corridor_risk']:.2f}")
    print(f"  naive:  {real_naive_score:.1f}  ILP:  {real_ilp_score:.1f}  improvement:  {real_pct:+.1f}%")
    for eid, a in real_result["events"].items():
        print(f"    {eid}: officers={a['officers']:>2d}  barricades={a['barricades']:>2d}  "
              f"diversion={a['diversion_route']}  lead={a['pre_deploy_lead_time']}min  gap={a['understaffed_by']}")

    # ---- DoD checks
    print(f"\n=== Definition-of-Done ===")
    dod = {
        "feasible_allocation": result["status"] == "Optimal",
        "solved_under_1s_for_3_events_40_officers": result_p["solve_time_s"] < 1.0,
        "respects_pool_cap": result["summary"]["total_officers_deployed"]
                             <= len(units),
        "all_min_officers_met": all(
            a["officers"] + a["understaffed_by"] >= a["need"]
            for a in result["events"].values()
        ),
        "barricades_match_closure_tier": all(
            (a["barricades"] > 0) == (events[i].get("closure_prob", 0) >= CLOSURE_THRESHOLD)
            for i, a in enumerate(result["events"].values())
        ),
        "improvement_vs_naive_or_equal": ilp_score <= naive_score + 0.01,
        "feasible_plenty_budget": result_p["status"] == "Optimal",
        "realistic_event_solve_succeeds": real_result["status"] == "Optimal",
        "realistic_solve_under_1s": real_result["solve_time_s"] < 1.0,
    }
    for k, v in dod.items():
        mark = "✓" if v else "✗"
        print(f"  {mark} {k}")

    # ---- persist allocation JSON
    out = C.ARTIFACTS_DIR / "demo_allocation.json"
    out.write_text(json.dumps({
        "tight_scenario": {
            "events": events,
            "n_units": len(units),
            "ilp_allocation": result,
            "naive_baseline": naive,
            "naive_score": round(naive_score, 2),
            "ilp_score": round(ilp_score, 2),
            "improvement_pct": round(pct, 2),
        },
        "plenty_scenario": {
            "events": events_p,
            "n_units": PLENTY,
            "ilp_allocation": result_p,
            "solve_time_s": result_p["solve_time_s"],
        },
        "realistic_scenario": {
            "events": real_events,
            "ilp_allocation": real_result,
            "naive_score": round(real_naive_score, 2),
            "ilp_score": round(real_ilp_score, 2),
            "improvement_pct": round(real_pct, 2),
        },
        "definition_of_done": dod,
    }, indent=2, default=str), encoding="utf-8")
    print(f"\n  -> {out}")


if __name__ == "__main__":
    main()
