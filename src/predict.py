"""GridLock — 01: predict.py — single-incident inference glue (used by 04 API).

Contract (per 00_MASTER §4 + 01_DATA_ML_CORE §"predict.py contract"):

  predict_incident(event: dict) -> dict

  Returns:
    {
      p10, p50, p90 : int   # clearance minutes, calibrated
      closure_prob  : float # P(road closure)
      closure_tier  : str   # 'HIGH' | 'MED' | 'LOW'
      corridor_risk : float # 0..1 from corridor_risk.csv
      cascade_downstream: [{corridor, lag_h, r}, ...]   # pre-alert list
      confidence    : 'high' | 'low'   # drives the confidence-gated UI
      because       : [str, ...]   # short text explanations
      survival_median_min : float  # Cox/AFT median survival time
    }

The function loads artifacts once, then featurizes the input using the same
past-only / label-encoder logic as features.py. NLP features default to 0
unless `nlp` is provided in the event dict (spec 02 produces them separately).
"""
from __future__ import annotations
import functools
import json
import sys
from datetime import datetime, timezone
import numpy as np
import pandas as pd
import joblib

from . import config as C

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

OP_CAP_MIN = 6 * 60  # matches train.py operational cap


@functools.lru_cache(maxsize=1)
def load_artifacts():
    return {
        "clearance": joblib.load(C.CLEARANCE_PKL),
        "survival": joblib.load(C.SURVIVAL_PKL),
        "closure": joblib.load(C.CLOSURE_PKL),
        "context": joblib.load(C.CONTEXT_PKL),
        "corridor_risk": pd.read_csv(C.CORRIDOR_RISK_CSV),
        "cascade_edges": pd.read_csv(C.CASCADE_EDGES_CSV),
    }


def _enc(context, col, val):
    return context["label_encoders"].get(col, {}).get(str(val), -1)


def _hour_bucket(h):
    return 0 if h <= 5 else 1 if h <= 11 else 2 if h <= 16 else 3 if h <= 20 else 4


def featurize_event(event: dict, context: dict) -> tuple[pd.DataFrame, dict]:
    """Build a 1-row feature DataFrame for an incoming event."""
    feat_cols = context["feature_columns"]
    feat_medians = context["feature_medians"]
    row = dict(feat_medians)  # robust defaults from train medians

    # ---- parse datetime
    dt_raw = event.get("datetime") or event.get("start_datetime")
    if isinstance(dt_raw, str):
        dt = pd.to_datetime(dt_raw, utc=True, errors="coerce")
        if pd.isna(dt):
            dt = pd.Timestamp.utcnow().tz_localize("UTC")
    elif isinstance(dt_raw, (datetime, pd.Timestamp)):
        dt = dt_raw
        if dt.tzinfo is None:
            dt = dt.tz_localize("UTC")
    else:
        # pandas >= 2 returns a tz-aware Timestamp; use the .now(tz=) form
        # which works on all versions.
        dt = pd.Timestamp.now(tz="UTC")
    dt_ist = dt.tz_convert("Asia/Kolkata")
    h = int(dt_ist.hour)
    dow = int(dt_ist.dayofweek)
    row.update({
        "hour": h, "day_of_week": dow,
        "is_weekend": int(dow >= 5), "month_of_year": int(dt_ist.month),
        "is_peak": int(h in (11, 12, 16, 17, 18, 19)),
        "is_night": int(h <= 5), "hour_bucket": _hour_bucket(h),
    })

    # ---- corridor stats
    corridor = str(event.get("corridor") or "Non-corridor")
    cs = context["corridor_stats"].get(corridor, {})
    base_cr = context["base_closure_rate"]
    row.update({
        "corridor_le": _enc(context, "corridor", corridor),
        "is_non_corridor": int(corridor == "Non-corridor"),
        "corridor_closure_rate": float(cs.get("corridor_closure_rate", base_cr)),
        "corridor_inc_7d": float(cs.get("corridor_inc_7d", 0.0)),
        "corridor_inc_30d": float(cs.get("corridor_inc_30d", 0.0)),
        "corridor_days_since_last": float(cs.get("corridor_days_since_last", 30.0)),
        "corridor_duration_mean": float(cs.get("corridor_duration_mean", 60.0)),
    })

    # ---- zone / cause / veh / police
    zone = str(event.get("zone") or "Unknown")
    cause = str(event.get("event_cause") or "unknown")
    veh = str(event.get("veh_type") or "unknown")
    ps = str(event.get("police_station") or "Unknown")
    row.update({
        "zone_le": _enc(context, "zone", zone),
        "is_planned": int(event.get("is_planned", False) or
                          str(event.get("event_type", "unplanned")) == "planned"),
        "event_cause_le": _enc(context, "event_cause", cause),
        "police_station_le": _enc(context, "police_station", ps),
        "veh_type_le": _enc(context, "veh_type", veh),
        "event_type_le": _enc(context, "event_type",
                              "planned" if event.get("is_planned") else "unplanned"),
        "zone_closure_rate": float(context["zone_closure_rate"].get(zone, base_cr)),
        "cause_closure_rate": float(context["cause_closure_rate"].get(cause, base_cr)),
        "veh_closure_rate": float(context["cause_closure_rate"].get(veh, base_cr)),
        "police_station_closure_rate": float(base_cr),  # not in context; fall back
        "zone_missing": int(zone == "Unknown"),
        "is_heavy_vehicle": int(veh in {"heavy_vehicle", "truck", "lcv", "tanker", "container"}),
        "has_description": int(bool(event.get("description"))),
    })

    # ---- lat / lon
    lat = event.get("latitude")
    lon = event.get("longitude")
    clat, clon = context["centroid"]
    try:
        row["latitude"] = float(lat) if lat is not None else clat
        row["longitude"] = float(lon) if lon is not None else clon
    except (TypeError, ValueError):
        row["latitude"], row["longitude"] = clat, clon
    # haversine to centroid
    p1, p2 = np.radians(row["latitude"]), np.radians(clat)
    dp = np.radians(clat - row["latitude"])
    dl = np.radians(clon - row["longitude"])
    a = np.sin(dp / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dl / 2) ** 2
    row["dist_centroid_km"] = float(2 * 6371.0 * np.arcsin(np.sqrt(a)))

    # ---- interaction terms
    is_planned_val = row["is_planned"]
    cause_cr = row["cause_closure_rate"]
    corr_cr = row["corridor_closure_rate"]
    row["planned_cause_risk"] = is_planned_val * cause_cr
    row["peak_cause_risk"] = row["is_peak"] * cause_cr
    row["corridor_cause_risk"] = corr_cr * cause_cr

    # ---- recurrence / time-aware defaults for the no-history case
    row["city_inc_1d"] = float(context.get("city_inc_1d_median", row.get("city_inc_1d", 0)))
    row["repeat_vehicle"] = 0

    # ---- NLP features (Tier-1 rule-based, runs inline unless pre-extracted).
    # Accept either a pre-extracted dict (from spec 02) or a raw description
    # string — if a string is given (or a `description` field is present in
    # `event`), run Tier-1 inline so the caller doesn't need a separate NLP
    # step. The whole Tier-1 path is pure-Python and runs in <1ms.
    nlp = event.get("nlp")
    if not nlp and event.get("description"):
        nlp = event["description"]
    if isinstance(nlp, str):
        from .nlp_extract import rule_extract as _rule_extract
        nlp = _rule_extract(nlp)
    nlp = nlp or {}
    # accept both `lanes_blocked` and `nlp_lanes_blocked` keys (the latter is
    # the output of Tier-1; the former is the structured-LLM convention).
    def _n(k_short, k_long, default=None):
        return nlp.get(k_short, nlp.get(k_long, default))
    nlp_defaults = {
        "nlp_lanes_blocked": int(_n("lanes_blocked", "nlp_lanes_blocked", 0)),
        "nlp_needs_crane_tow": int(_n("needs_crane_tow", "nlp_needs_crane_tow", 0)),
        "nlp_weather_water": int(_n("weather_water", "nlp_weather_water", 0)),
        "nlp_agency_mention": int(bool(_n("agency_mention", "nlp_agency_mention", 0))),
        "nlp_kannada_cues": int(bool(_n("kannada_cues", "nlp_kannada_cues", 0))),
        "nlp_severity_cue": int(_n("severity_cue", "nlp_severity_cue", 3)),
        "nlp_urgency_tone": int(_n("urgency_tone", "nlp_urgency_tone", 0)),
        "nlp_estimated_duration_min": float(_n("estimated_duration_min",
                                                 "nlp_estimated_duration_min", 0.0)),
        "nlp_event_subtype": str(_n("event_subtype", "nlp_event_subtype", "unknown")),
    }
    nlp_defaults["nlp_event_subtype_le"] = _enc(context, "nlp_event_subtype",
                                                  nlp_defaults["nlp_event_subtype"])
    row.update({k: v for k, v in nlp_defaults.items() if k in feat_cols})
    # also keep the raw subtype on the row (not a model feature, but surfaced
    # in the response payload).
    row["nlp_event_subtype"] = nlp_defaults["nlp_event_subtype"]

    # ---- closure (used as a feature for the duration model only)
    row["requires_road_closure"] = int(event.get("requires_road_closure", 0))

    # ---- assemble single-row DataFrame in the expected column order
    X = pd.DataFrame([{f: row.get(f, 0.0) for f in feat_cols}]).astype(float)
    return X, row


def _predict_clearance(art, X) -> dict:
    models = art["clearance"]["models"]
    cal = art["clearance"].get("calibration", {})
    p10_off = cal.get("p10_offset_min", 0.0)
    p90_off = cal.get("p90_offset_min", 0.0)
    out = {}
    for q, m in models.items():
        v = float(np.expm1(m.predict(X)[0]))
        v = max(1.0, min(v, OP_CAP_MIN))
        if q == 0.1:
            v = max(1.0, min(v - p10_off, OP_CAP_MIN))
        elif q == 0.9:
            v = max(1.0, min(v + p90_off, OP_CAP_MIN))
        out[q] = v
    return {"p10": out[0.1], "p50": out[0.5], "p90": out[0.9]}


def _predict_closure(art, X) -> dict:
    clf = art["closure"]["model"]
    thr = art["closure"]["threshold"]
    p = float(clf.predict_proba(X)[:, 1][0])
    # Blend with cause lookup for a more robust tier (per 01 spec)
    cause = X["event_cause_le"].iloc[0]
    cause_lookup = art["context"]["cause_closure_rate_lookup"]
    # Reverse-lookup the cause string from label encoder
    le = art["context"]["label_encoders"].get("event_cause", {})
    inv = {v: k for k, v in le.items()}
    cause_str = inv.get(int(cause), "")
    lookup_rate = float(cause_lookup.get(cause_str, 0.05))
    # blend: weighted average (0.7 model, 0.3 lookup)
    blended = 0.7 * p + 0.3 * lookup_rate
    # tier from blended
    if blended >= 0.30:
        tier = "HIGH"
    elif blended >= 0.15:
        tier = "MED"
    else:
        tier = "LOW"
    return {"closure_prob": blended, "closure_tier": tier, "ml_prob": p,
            "lookup_rate": lookup_rate, "threshold": float(thr)}


def _predict_survival_median(art, X) -> float:
    surv = art["survival"]
    name = surv.get("model_name", "aft")
    try:
        if name == "aft":
            return float(surv["model"].predict_median(X).iloc[0])
        else:
            # CoxPH: predict_survival_function then take median
            sf = surv["model"].predict_survival_function(X)
            # median is the time where S(t) crosses 0.5
            median_t = []
            for col in sf.columns:
                row = sf[col]
                below = row[row <= 0.5]
                median_t.append(float(below.index[0]) if len(below) else float(row.index[-1]))
            return float(median_t[0])
    except Exception:
        return float(OP_CAP_MIN)


def _corridor_risk(art, corridor: str) -> float:
    df = art["corridor_risk"]
    if corridor in df["corridor"].values:
        return float(df.loc[df["corridor"] == corridor, "risk_score"].iloc[0])
    return 0.0


def _cascade_downstream(art, corridor: str, top_n: int = 5) -> list:
    edges = art["cascade_edges"]
    if edges.empty or corridor not in edges["source"].values:
        return []
    out = (edges[edges["source"] == corridor]
                 .sort_values("r", ascending=False)
                 .head(top_n)
                 .to_dict(orient="records"))
    return [{
        "corridor": e["target"],
        "lag_h": int(e["lag_h"]),
        "lag_min": int(e["lag_h"]) * 60,
        "r": float(e["r"]),
    } for e in out]


def _confidence_band(clearance, n_train_for_corridor) -> str:
    band = clearance["p90"] - clearance["p10"]
    if band < 90 and n_train_for_corridor >= 50:
        return "high"
    if band < 180 and n_train_for_corridor >= 20:
        return "medium"
    return "low"


def _because(clearance, closure, corridor_risk, cascade, nlp_cues=None) -> list:
    """Generate the human-readable `because` payload for the UI.

    `nlp_cues` (optional) is a dict of the parsed NLP features for this event
    (from spec 02). When present, the explanations mention what the note said —
    e.g. "+18 min: note says 'needs crane' + 'one lane blocked' (parsed from
    Kannada)." This is the spec 02 §4 explainability hook.
    """
    reasons = []
    if closure["closure_tier"] == "HIGH":
        reasons.append(f"Closure likely ({closure['closure_prob']:.0%}) — "
                       f"tier HIGH per cause/corridor blend")
    elif closure["closure_tier"] == "MED":
        reasons.append(f"Closure possible ({closure['closure_prob']:.0%}) — tier MED")
    if corridor_risk >= 0.30:
        reasons.append(f"Corridor has high historical risk score ({corridor_risk:.2f})")
    if cascade:
        primary = cascade[0]
        reasons.append(f"Cascade: expect surge at {primary['corridor']} in "
                       f"{primary['lag_min']} min (r={primary['r']:.2f})")
    # ---- NLP cues (spec 02 §4 — explainability hook)
    if nlp_cues:
        nlp_bits = []
        if nlp_cues.get("nlp_needs_crane_tow"):
            nlp_bits.append("'needs crane/tow'")
        if nlp_cues.get("nlp_lanes_blocked"):
            nlp_bits.append("'lane(s) blocked'")
        if nlp_cues.get("nlp_weather_water"):
            nlp_bits.append("'water/rain'")
        if nlp_cues.get("nlp_agency_mention"):
            nlp_bits.append("agency named")
        if nlp_bits:
            lang = " (parsed from Kannada)" if nlp_cues.get("nlp_kannada_cues") else ""
            reasons.append(f"Note mentions {', '.join(nlp_bits)}{lang}")
        if nlp_cues.get("nlp_estimated_duration_min", 0) > 0:
            reasons.append(f"Note states ~{int(nlp_cues['nlp_estimated_duration_min'])} "
                           "min to clear")
    if clearance["p50"] > 90:
        reasons.append(f"Median clearance ~{clearance['p50']:.0f} min — "
                       "longer than typical")
    elif clearance["p50"] < 30:
        reasons.append(f"Median clearance ~{clearance['p50']:.0f} min — quick turnaround")
    return reasons


def predict_incident(event: dict) -> dict:
    """The single entry point used by 04 backend API.

    `event` may include:
      - corridor, zone, event_cause, veh_type, police_station, event_type
      - latitude, longitude
      - is_planned (bool) — or event_type=='planned'
      - requires_road_closure (0/1, optional — passed through)
      - description (str, optional — used by Tier-1 NLP if `nlp` not given)
      - nlp (dict or str, optional) — pre-extracted NLP cues from spec 02,
        or a raw description string (Tier-1 runs inline)
      - datetime / start_datetime (str or datetime)
    """
    art = load_artifacts()
    context = art["context"]
    X, row = featurize_event(event, context)

    clearance = _predict_clearance(art, X)
    closure = _predict_closure(art, X)
    survival_med = _predict_survival_median(art, X)
    corridor = str(event.get("corridor") or "Non-corridor")
    risk = _corridor_risk(art, corridor)
    cascade = _cascade_downstream(art, corridor)
    # n_train_for_corridor: from corridor_stats context
    cs = context["corridor_stats"].get(corridor, {})
    n_for_corr = int(cs.get("corridor_inc_30d", 0) + cs.get("corridor_inc_7d", 0))
    confidence = _confidence_band(clearance, n_for_corr)

    return {
        # Clearance quantile (calibrated, capped at 6h operational range)
        "p10": int(round(clearance["p10"])),
        "p50": int(round(clearance["p50"])),
        "p90": int(round(clearance["p90"])),
        # Closure
        "closure_prob": round(closure["closure_prob"], 3),
        "closure_tier": closure["closure_tier"],
        "closure_ml_prob": round(closure["ml_prob"], 3),
        "closure_lookup_rate": round(closure["lookup_rate"], 3),
        # Survival (uses censored rows)
        "survival_median_min": round(survival_med, 1),
        # Corridor / cascade
        "corridor_risk": round(risk, 3),
        "corridor": corridor,
        "cascade_downstream": cascade,
        # NLP cues surfaced to the UI (the spec 02 §4 explainability hook)
        "nlp_cues": {
            "lanes_blocked": bool(row.get("nlp_lanes_blocked", 0)),
            "needs_crane_tow": bool(row.get("nlp_needs_crane_tow", 0)),
            "weather_water": bool(row.get("nlp_weather_water", 0)),
            "agency_mention": bool(row.get("nlp_agency_mention", 0)),
            "kannada_cues": bool(row.get("nlp_kannada_cues", 0)),
            "event_subtype": str(row.get("nlp_event_subtype", "unknown")),
            "urgency_tone": int(row.get("nlp_urgency_tone", 0)),
            "estimated_duration_min": float(row.get("nlp_estimated_duration_min", 0.0)),
        },
        # UI gating
        "confidence": confidence,
        "because": _because(clearance, closure, risk, cascade, nlp_cues={
            "nlp_lanes_blocked": row.get("nlp_lanes_blocked", 0),
            "nlp_needs_crane_tow": row.get("nlp_needs_crane_tow", 0),
            "nlp_weather_water": row.get("nlp_weather_water", 0),
            "nlp_agency_mention": row.get("nlp_agency_mention", 0),
            "nlp_kannada_cues": row.get("nlp_kannada_cues", 0),
            "nlp_estimated_duration_min": row.get("nlp_estimated_duration_min", 0),
        }),
    }


def main():
    # --- canonical demo cases (deterministic, no LLM, no network)
    print("=== GridLock 01+02: predict_incident demo ===\n")
    cases = [
        {"corridor": "Mysore Road", "event_cause": "vehicle_breakdown",
         "veh_type": "lcv", "zone": "West Zone 1",
         "police_station": "Yeshwanthpura PS",
         "datetime": "2024-04-01T18:00:00+05:30",
         "description": "lcv breakdown near yeshwantpur"},
        {"corridor": "Bellary Road 1", "event_cause": "tree_fall",
         "zone": "Hebbal", "police_station": "Hebbal PS",
         "datetime": "2024-04-01T05:00:00+05:30",
         "description": "huge tree fallen blocking the road crane needed"},
        {"corridor": "Hosur Road", "event_cause": "construction",
         "is_planned": True, "event_type": "planned",
         "datetime": "2024-04-01T14:00:00+05:30",
         "description": "BWSSB work in progress, 1 lane blocked"},
        {"corridor": "Mysore Road", "event_cause": "vip_movement",
         "is_planned": True, "event_type": "planned",
         "datetime": "2024-04-01T15:00:00+05:30",
         "description": "vip convoy passing through"},
        # The Kannada demo (spec 02 §2b) — Tier-1 NLP must parse this
        {"corridor": "Tumkur Road", "event_cause": "vehicle_breakdown",
         "datetime": "2024-04-01T20:00:00+05:30",
         "description": "ನಮಸ್ತೆ ಸರ್ ಬಸ್ ಆಫ್ ರೋಡ್ ಆಗಿರುತ್ತದೆ ಕ್ರೇನ್ ಬೇಕು ಒಂದು ಲೇನ್ ಬ್ಲಾಕ್"},
    ]
    for c in cases:
        out = predict_incident(c)
        cues = out.get("nlp_cues", {})
        nlp_str = ""
        if any(cues.get(k) for k in ("lanes_blocked", "needs_crane_tow",
                                     "weather_water", "agency_mention")):
            bits = []
            if cues.get("needs_crane_tow"): bits.append("crane")
            if cues.get("lanes_blocked"): bits.append("lanes-blocked")
            if cues.get("weather_water"): bits.append("water")
            if cues.get("agency_mention"): bits.append("agency")
            nlp_str = f"  NLP=[{','.join(bits)}|kan={cues.get('kannada_cues')}]"
        print(f"  corridor={out['corridor']:>20s}  cause={c['event_cause']:>20s}  "
              f"P50={out['p50']:>3d}min  closure={out['closure_tier']:>4s} "
              f"({out['closure_prob']:.0%})  conf={out['confidence']}{nlp_str}")
        for r in out["because"]:
            print(f"      - {r}")
        if out["cascade_downstream"]:
            c0 = out["cascade_downstream"][0]
            print(f"      cascade: -> {c0['corridor']} in {c0['lag_min']}min "
                  f"(r={c0['r']:.2f})")


if __name__ == "__main__":
    main()
