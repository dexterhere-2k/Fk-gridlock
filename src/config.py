"""GridLock — 01: Central config (paths, leakage controls, feature policy, params).

The single source of truth for *what the models are allowed to see*. The
BANNED list is the most important object in the module: `priority` is 99.9%
deterministic from `corridor`, and `modified_datetime` is auto-stamped ~2h
after start. Excluding them everywhere is what separates an honest model
from a hollow demo.
"""
from __future__ import annotations
from pathlib import Path

# --------------------------------------------------------------------------- paths
ROOT = Path(__file__).resolve().parents[1]
DATA_RAW = ROOT / "data"
DATA_PROC = ROOT / "data"
ARTIFACTS_DIR = ROOT / "artifacts"

# Input CSV (Bengaluru Traffic Police ASTraM log) — see 00_MASTER §1
RAW_CSV = DATA_RAW / "Astram event data_anonymized - Astram event data_anonymizedb40ac87.csv"

# Processed parquet + artifacts produced by the pipeline
CLEAN_PARQUET = DATA_PROC / "incidents_clean.parquet"
FEATURES_PARQUET = DATA_PROC / "incidents_features.parquet"

NLP_FEATURES_PARQUET = DATA_PROC / "nlp_features.parquet"

# Model artifacts (consumed by 04_backend_api)
CLEARANCE_PKL = ARTIFACTS_DIR / "clearance_quantile.pkl"
SURVIVAL_PKL = ARTIFACTS_DIR / "survival_aft.pkl"
CLOSURE_PKL = ARTIFACTS_DIR / "closure_clf.pkl"
CONTEXT_PKL = ARTIFACTS_DIR / "context.pkl"
CORRIDOR_RISK_CSV = ARTIFACTS_DIR / "corridor_risk.csv"
CASCADE_EDGES_CSV = ARTIFACTS_DIR / "cascade_edges.csv"
CASCADE_META_JSON = ARTIFACTS_DIR / "cascade_meta.json"
METRICS_JSON = ARTIFACTS_DIR / "metrics.json"

for _d in (DATA_RAW, DATA_PROC, ARTIFACTS_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# --------------------------------------------------------------------------- seed
SEED = 42
import numpy as np
RANDOM_STATE = SEED
np.random.seed(SEED)

# --------------------------------------------------------------------------- targets
# T1: requires_road_closure (binary) — 676 positives
T1_CLOSURE = "requires_road_closure"
# T2: priority — explicitly NOT a target. BANNED. Only corridor is used downstream.
# T3: clearance / resolution time (minutes) — derived (see data_prep)
T3_DURATION = "duration_min"
TARGETS = [T1_CLOSURE, T3_DURATION]

# Bins / labels for the 4-class duration bucket (used by predict + demo)
DURATION_BUCKET_BINS = [0, 60, 180, 480, float("inf")]
DURATION_BUCKET_LABELS = ["<1h", "1-3h", "3-8h", ">8h"]

# ------------------------------------------------------------- leakage / exclusions
# Hard guardrails — enforced everywhere (features, predict, evaluation).
#  - `priority` is ~99.9% deterministic from `corridor` (6/8151 exceptions).
#  - `modified_datetime` is auto-stamped ~2h after start (system artifact).
#  - `endlat/endlon` are only populated when a closure happened (leak ~98%).
#  - `resolved_at_*`, `closed_*`, `resolved_*` are post-hoc.
#  - `*_id` columns are identifiers / book-keeping.
BANNED = {
    # target / label leakage
    "priority",
    "modified_datetime",
    "endlatitude", "endlongitude", "end_address",
    "resolved_at_address", "resolved_at_latitude", "resolved_at_longitude",
    "closed_by_id", "resolved_by_id", "closed_datetime", "resolved_datetime",
    "end_datetime", "status",
    # identifier / book-keeping
    "id", "veh_no", "kgid", "gba_identifier",
    "client_id", "created_by_id", "last_modified_by_id",
    "assigned_to_police_id", "citizen_accident_id",
    "meta_data", "map_file", "route_path",
    # free-text (handled by spec 02 separately; never a direct feature)
    "comment",
    "description", "address", "end_address",
    # raw datetime / leakage
    "start_datetime", "start_ist", "created_date",
    "authenticated", "direction",
    # raw categoricals (label-encoded versions used downstream)
    "event_type", "requires_road_closure",
    "corridor", "zone", "police_station", "veh_type", "event_cause",
    "junction",
    # duration column itself — never a direct feature (used for prior stats only)
    "duration_min", "duration_log", "is_censored",
}

# Features the spec 01 explicitly allows to be derived but NOT used as raw input.
ALLOWED_RAW = {
    "latitude", "longitude",  # for map render + corridor aggregation (sparse, not direct feat)
    "event_cause", "corridor", "zone", "police_station", "veh_type", "event_type",
    "requires_road_closure",  # target + feature-for-duration-model
}

# Features that encode corridor identity (used for the corridor-blind ablation
# during evaluation; the production model keeps them).
CORRIDOR_FEATURES = [
    "corridor_le", "is_non_corridor", "corridor_inc_7d", "corridor_inc_30d",
    "corridor_closure_rate", "corridor_days_since_last",
    "corridor_duration_mean", "corridor_cause_risk",
]

# --------------------------------------------------------------------------- features
# Numeric / temporal features produced by features.py
NUMERIC_FEATURES = [
    # temporal
    "hour", "day_of_week", "is_weekend", "month_of_year",
    "is_peak", "is_night", "hour_bucket",
    # recurrence (PAST-ONLY)
    "corridor_inc_7d", "corridor_inc_30d", "corridor_days_since_last",
    "corridor_closure_rate", "corridor_duration_mean",
    "zone_closure_rate", "cause_closure_rate", "veh_closure_rate",
    "police_station_closure_rate",
    "city_inc_1d", "repeat_vehicle",
    # spatial
    "latitude", "longitude", "dist_centroid_km",
    # flags
    "is_heavy_vehicle", "is_non_corridor", "is_planned",
    "has_description", "zone_missing",
    # interactions
    "planned_cause_risk", "peak_cause_risk", "corridor_cause_risk",
    # NLP (Tier-1, spec 02)
    "nlp_lanes_blocked", "nlp_needs_crane_tow", "nlp_weather_water",
    "nlp_event_subtype_le", "nlp_agency_mention", "nlp_kannada_cues",
    # closures (feature for duration model only)
    "requires_road_closure",
]

# Categorical columns to out-of-fold target-encode (see features.py for the
# OOF machinery). These are the columns the spec calls out explicitly.
TARGET_ENCODE_COLS = ["event_type", "event_cause", "corridor", "zone",
                      "police_station", "veh_type"]

# Label-encoded raw categoricals (fit on TRAIN only, unseen -> -1).
ENCODED_COLS = ["event_type", "event_cause", "corridor", "zone",
                "police_station", "veh_type", "nlp_event_subtype"]

# NLP feature columns produced by 02_NLP_LAYER (joined in features.py)
NLP_COLS = ["nlp_lanes_blocked", "nlp_needs_crane_tow", "nlp_weather_water",
            "nlp_event_subtype", "nlp_agency_mention", "nlp_kannada_cues",
            "nlp_vehicle_hint", "nlp_breakdown_subtype", "nlp_severity_cue",
            "nlp_urgency_tone", "nlp_estimated_duration_min"]

# ----------------------------------------------------------------- model params
# Quantile regression for clearance (Target 1) — scikit-learn GBR with quantile
# loss, three models (P10 / P50 / P90) fit on log1p(duration).
QUANTILE_PARAMS = dict(
    n_estimators=400, max_depth=3, learning_rate=0.05,
    subsample=0.8, min_samples_leaf=15, random_state=SEED,
)
QUANTILES = (0.1, 0.5, 0.9)

# Survival (Target 2) — lifelines Weibull AFT, fits on all 8,173 rows.
SURVIVAL_DURATION_CAP = 24 * 60  # 24h, in minutes
SURVIVAL_TOL = 1e-6

# Closure classifier (binary, target 1 — 11:1 imbalance).
CLOSURE_PARAMS = dict(
    n_estimators=300, max_depth=3, learning_rate=0.05,
    subsample=0.8, random_state=SEED,
)
CLOSURE_SCALE_POS = 11.1  # matches the imbalance

# Cascade / domino (Target 4) — see cascade.py
CASCADE_MIN_CORRIDOR_EVENTS = 80
CASCADE_LAGS = (1, 2, 3)  # hours
CASCADE_P_THRESHOLD = 0.05

# Cause-based closure / barricade tier lookup (per FINDINGS §6 + astram-pulse).
# Used as a rule prior alongside the learned closure classifier.
CAUSE_CLOSURE_RATE = {
    "vip_movement":      0.80,
    "public_event":      0.464,
    "protest":           0.40,
    "tree_fall":         0.394,
    "construction":      0.265,
    "procession":        0.264,
    "road_conditions":   0.118,
    "water_logging":     0.085,
    "others":            0.086,
    "vehicle_breakdown": 0.043,
    "accident":          0.030,
    "congestion":        0.015,
    "pot_holes":         0.009,
}
CAUSE_DURATION_MEDIAN = {
    "pot_holes":         12990.0,
    "road_conditions":   9236.0,
    "water_logging":     3688.0,
    "construction":      2945.0,
    "tree_fall":          732.0,
    "others":             452.0,
    "congestion":          72.0,
    "vehicle_breakdown":   41.0,
    "accident":            40.0,
    "procession":          37.0,
    "protest":             24.0,
    "vip_movement":        20.0,
    "public_event":        18.0,
}
BARRICADE_TIER = {
    "vip_movement": "HIGH", "public_event": "HIGH", "protest": "HIGH",
    "tree_fall": "HIGH", "construction": "MED", "procession": "MED",
    "road_conditions": "MED", "water_logging": "LOW", "others": "LOW",
    "vehicle_breakdown": "LOW", "accident": "LOW", "congestion": "LOW",
    "pot_holes": "LOW",
}


def feature_columns(df, corridor_blind: bool = False):
    """Return numeric/bool model features: every leakage-safe engineered column.

    The closure column is included (allowed for the duration model only). The
    spec keeps `requires_road_closure` as a feature for Target 1 (clearance)
    because the model needs to know if the incident *will* close the road
    in order to predict the additional clearance time it implies. We
    drop it for the closure model itself.
    """
    import pandas as pd
    banned = set(BANNED) | {
        "duration_min", "duration_log", "duration_raw_min", "is_censored",
        "age_of_truck", "start_imputed",  # sparse / not predictive
        # predictors produced by predict (would self-leak in training)
        "clearance_p10", "clearance_p50", "clearance_p90",
        "closure_prob_pred", "survival_median_min",
    }
    if corridor_blind:
        banned |= set(CORRIDOR_FEATURES)
    cols = []
    for c in df.columns:
        if c in banned:
            continue
        if pd.api.types.is_numeric_dtype(df[c]) or pd.api.types.is_bool_dtype(df[c]):
            cols.append(c)
    return cols


def get_feature_columns(df, corridor_blind: bool = False,
                        drop_closure_target: bool = False):
    """Feature selector used by train/evaluate. Set drop_closure_target=True
    when training the closure classifier itself (avoids trivial target leak)."""
    cols = feature_columns(df, corridor_blind=corridor_blind)
    if drop_closure_target and "requires_road_closure" in cols:
        cols = [c for c in cols if c != "requires_road_closure"]
    return cols
