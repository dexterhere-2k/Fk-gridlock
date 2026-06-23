"""NexGen — 01: Step 2 — Leakage-safe feature engineering.

Everything here is computable *the moment an incident is reported*. Recurrence
and corridor-history features are strictly PAST-ONLY: for each row they look
only at incidents that started earlier, via time-ordered searchsorted /
cumulative means. Categorical encoders are fit on TRAIN only (unseen -> -1).
Out-of-fold target encoding is used for high-cardinality categoricals
(K=5) — the within-fold mean is itself a leak, so we cycle folds.

Banned end-point / resolution columns are never touched.
"""
from __future__ import annotations
import sys
import json
import numpy as np
import pandas as pd

from . import config as C

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

NS_PER_DAY = 86_400 * 1_000_000_000
HEAVY_VEH = {"heavy_vehicle", "truck", "lcv", "tanker", "container"}


# ---------------------------------------------------------------- past-only helpers
def _past_window_count(times_ns, group_idx_list, window_days):
    """For each row: # of prior incidents in the same group within `window_days`."""
    win = int(window_days * NS_PER_DAY)
    out = np.zeros(len(times_ns))
    for idx in group_idx_list:
        ts = times_ns[idx]                              # ascending (df is time-sorted)
        left = np.searchsorted(ts, ts - win, side="left")
        out[idx] = np.arange(len(ts)) - left            # rows in [t-win, t)
    return out


def _past_rate(labels, group_idx_list, base):
    """For each row: mean of the label over prior incidents in the same group."""
    out = np.full(len(labels), base, dtype=float)
    for idx in group_idx_list:
        lab = labels[idx].astype(float)
        prior_sum = np.cumsum(lab) - lab
        prior_cnt = np.arange(len(lab))
        with np.errstate(invalid="ignore", divide="ignore"):
            rate = np.where(prior_cnt > 0, prior_sum / np.maximum(prior_cnt, 1), base)
        out[idx] = rate
    return out


def _days_since_last(times_ns, group_idx_list, fill=999.0):
    out = np.full(len(times_ns), fill, dtype=float)
    for idx in group_idx_list:
        ts = times_ns[idx]
        if len(ts) > 1:
            d = np.empty(len(ts))
            d[0] = fill
            d[1:] = (ts[1:] - ts[:-1]) / NS_PER_DAY
            out[idx] = d
    return out


def _group_idx(series):
    """Ordered list of positional-index arrays, one per group (time-sorted)."""
    return [np.asarray(v) for v in series.reset_index(drop=True).groupby(
        series.values, sort=False).groups.values()]


def _past_mean_cont(values, group_idx_list, base):
    """Past-only mean of a continuous column; NaN rows (censored) are excluded."""
    v = np.where(np.isnan(values), 0.0, np.array(values, dtype=float))
    valid = (~np.isnan(values)).astype(float)
    out = np.full(len(values), base, dtype=float)
    for idx in group_idx_list:
        vv, vld = v[idx], valid[idx]
        prior_sum = np.cumsum(vv * vld) - vv * vld
        prior_cnt = np.cumsum(vld) - vld
        with np.errstate(invalid="ignore", divide="ignore"):
            rate = np.where(prior_cnt > 0, prior_sum / np.maximum(prior_cnt, 1), base)
        out[idx] = rate
    return out


def _haversine_km(lat1, lon1, lat2, lon2):
    R = 6371.0
    p1, p2 = np.radians(lat1), np.radians(lat2)
    dp, dl = np.radians(lat2 - lat1), np.radians(lon2 - lon1)
    a = np.sin(dp / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dl / 2) ** 2
    return 2 * R * np.arcsin(np.sqrt(a))


# ------------------------------------------------------------- OOF target encoding
def _oof_target_encode(train_vals, train_y, test_vals, k=5, smoothing=20.0,
                       prior=None):
    """K-fold OOF target encoding for a single categorical column.

    Returns (train_oof, test_enc, mapping_for_test).
    """
    rng = np.random.default_rng(C.SEED)
    train_idx = np.arange(len(train_vals))
    folds = np.array_split(rng.permutation(train_idx), k)
    oof = np.full(len(train_vals), np.nan, dtype=float)
    global_mean = float(np.nanmean(train_y)) if prior is None else prior
    for f in folds:
        tr_idx = np.array([i for i in train_idx if i not in set(f)])
        agg = pd.DataFrame({"x": train_vals[tr_idx], "y": train_y[tr_idx]}) \
                .groupby("x")["y"].agg(["mean", "count"])
        # smoothed: (count*mean + smoothing*global)/(count + smoothing)
        agg["enc"] = (agg["count"] * agg["mean"] + smoothing * global_mean) \
                      / (agg["count"] + smoothing)
        m = agg["enc"].to_dict()
        for i in f:
            v = train_vals[i]
            oof[i] = m.get(v, global_mean)
    # Final test/holdout mapping fit on all of TRAIN
    agg = pd.DataFrame({"x": train_vals, "y": train_y}) \
            .groupby("x")["y"].agg(["mean", "count"])
    agg["enc"] = (agg["count"] * agg["mean"] + smoothing * global_mean) \
                  / (agg["count"] + smoothing)
    test_enc = pd.Series(test_vals).map(agg["enc"].to_dict()).fillna(global_mean).to_numpy()
    return oof, test_enc, agg["enc"].to_dict(), global_mean


# ----------------------------------------------------------------- main builder
def build_features(df: pd.DataFrame) -> pd.DataFrame:
    df = df.sort_values("start_datetime").reset_index(drop=True)
    t = df["start_ist"]

    # ---- temporal
    df["hour"] = t.dt.hour
    df["day_of_week"] = t.dt.dayofweek
    df["is_weekend"] = (df["day_of_week"] >= 5).astype(int)
    df["month_of_year"] = t.dt.month
    # refined peak: closure spikes at 11-12 (midday) and 16-19 (evening) per EDA
    df["is_peak"] = df["hour"].isin([11, 12, 16, 17, 18, 19]).astype(int)
    df["is_night"] = df["hour"].isin([0, 1, 2, 3, 4, 5]).astype(int)
    df["hour_bucket"] = pd.cut(df["hour"], [-1, 5, 11, 16, 20, 24],
                               labels=[0, 1, 2, 3, 4]).astype(int)

    # ---- recurrence (PAST-ONLY)
    times = df["start_datetime"].astype("int64").to_numpy()
    base_t1 = float(df[C.T1_CLOSURE].mean())
    t1 = df[C.T1_CLOSURE].fillna(0).to_numpy()

    corr_groups = _group_idx(df["corridor"].fillna("NA"))
    df["corridor_inc_7d"] = _past_window_count(times, corr_groups, 7)
    df["corridor_inc_30d"] = _past_window_count(times, corr_groups, 30)
    df["corridor_days_since_last"] = _days_since_last(times, corr_groups)
    df["corridor_closure_rate"] = _past_rate(t1, corr_groups, base_t1)

    zone_groups = _group_idx(df["zone"].fillna("UNKNOWN"))
    df["zone_closure_rate"] = _past_rate(t1, zone_groups, base_t1)

    cause_groups = _group_idx(df["event_cause_norm"].fillna("unknown"))
    df["cause_closure_rate"] = _past_rate(t1, cause_groups, base_t1)

    veh_groups = _group_idx(df["veh_type"].fillna("unknown"))
    df["veh_closure_rate"] = _past_rate(t1, veh_groups, base_t1)

    ps_groups = _group_idx(df["police_station"].fillna("UNKNOWN"))
    df["police_station_closure_rate"] = _past_rate(t1, ps_groups, base_t1)

    # past-only mean resolution time per group (censored rows excluded).
    dur_vals = np.where(df["is_censored"].to_numpy() == 0,
                        df[C.T3_DURATION].to_numpy(dtype=float), np.nan)
    global_dur_mean = float(np.nanmean(dur_vals))
    df["corridor_duration_mean"] = _past_mean_cont(dur_vals, corr_groups, global_dur_mean)

    # city-wide load in the prior 24h (global, past-only)
    left = np.searchsorted(times, times - NS_PER_DAY, side="left")
    df["city_inc_1d"] = np.arange(len(times)) - left

    # repeat-vehicle (anonymised veh_no seen before)
    veh_prior = df.groupby("veh_no").cumcount()
    df["repeat_vehicle"] = ((df["veh_no"].notna()) & (veh_prior > 0)).astype(int)

    # ---- spatial
    clat, clon = df["latitude"].mean(), df["longitude"].mean()
    df["dist_centroid_km"] = _haversine_km(df["latitude"], df["longitude"], clat, clon)

    # ---- flags
    df["is_heavy_vehicle"] = df["veh_type"].isin(HEAVY_VEH).astype(int)
    df["is_non_corridor"] = (df["corridor"] == "Non-corridor").astype(int)
    df["is_planned"] = (df["event_type"] == "planned").astype(int)
    df["has_description"] = df["description"].notna().astype(int)
    df["zone_missing"] = (df["zone"] == "Unknown").astype(int)

    # interactions: lets the model separate high-risk planned causes (vip/event/
    # protest, ~40-80% closure) from low-risk planned (construction, ~27%) without
    # a two-level tree split. Also added for corridor and peak hour.
    df["planned_cause_risk"] = df["is_planned"] * df["cause_closure_rate"]
    df["peak_cause_risk"] = df["is_peak"] * df["cause_closure_rate"]
    df["corridor_cause_risk"] = df["corridor_closure_rate"] * df["cause_closure_rate"]

    # ---- NLP feature join (spec 02 — rule-based, optional)
    nlp_path = C.NLP_FEATURES_PARQUET
    if nlp_path.exists():
        nlp = pd.read_parquet(nlp_path)
        # align on id (or index)
        if "id" in nlp.columns and "id" in df.columns:
            nlp_keep = [c for c in nlp.columns
                        if c.startswith("nlp_") and c != "id"]
            df = df.merge(nlp[["id"] + nlp_keep], on="id", how="left")
        else:
            nlp_keep = [c for c in nlp.columns if c.startswith("nlp_")]
            df[nlp_keep] = nlp[nlp_keep].to_numpy()
        # fill missing nlp columns with 0 / "unknown"
        for col in C.NLP_COLS:
            if col not in df.columns:
                df[col] = 0 if col != "nlp_event_subtype" else "unknown"
            else:
                if col == "nlp_event_subtype":
                    df[col] = df[col].fillna("unknown")
                else:
                    df[col] = df[col].fillna(0)
        print(f"joined NLP features from {nlp_path}")
    else:
        # no NLP yet — fill with safe defaults so downstream doesn't break
        defaults = {
            "nlp_lanes_blocked": 0, "nlp_needs_crane_tow": 0,
            "nlp_weather_water": 0, "nlp_agency_mention": 0,
            "nlp_kannada_cues": 0, "nlp_subtype_le": -1,
            "nlp_estimated_duration_min": 0.0,
        }
        for k, v in defaults.items():
            df[k] = v
        df["nlp_event_subtype"] = "unknown"

    return df


def encode_categoricals(df: pd.DataFrame, encoders: dict | None = None,
                        fit: bool = True) -> tuple[pd.DataFrame, dict]:
    """Label-encode raw categoricals (fit on TRAIN, unseen -> -1).

    Out-of-fold target encoding for the same columns is added in
    `target_encode_features` after the chronological split is known.
    """
    df = df.copy()
    if encoders is None:
        encoders = {}
    for col in C.ENCODED_COLS:
        if col not in df.columns:
            continue
        key = f"{col}_le"
        if fit:
            cats = sorted({str(v) for v in df[col].dropna().unique()})
            encoders[col] = {c: i for i, c in enumerate(cats)}
        mapping = encoders.get(col, {})
        df[key] = df[col].astype("object").map(
            lambda x: mapping.get(str(x), -1)).astype(int)
    return df, encoders


def target_encode_features(train: pd.DataFrame, test: pd.DataFrame,
                           target_col: str, cols: list[str],
                           encoders: dict | None = None, k: int = 5,
                           smoothing: float = 20.0) -> tuple:
    """OOF target encoding for high-cardinality categoricals on (train, test).

    Returns (train_out, test_out, encoders) where each *_out has the new
    `f"{col}_te"` columns appended.
    """
    if encoders is None:
        encoders = {}
    for col in cols:
        if col not in train.columns:
            continue
        oof, test_enc, mapping, prior = _oof_target_encode(
            train[col].fillna("NA").astype(str).to_numpy(),
            train[target_col].fillna(0).astype(float).to_numpy(),
            test[col].fillna("NA").astype(str).to_numpy(),
            k=k, smoothing=smoothing,
        )
        train[f"{col}_te"] = oof
        test[f"{col}_te"] = test_enc
        encoders[col] = {"mapping": mapping, "prior": float(prior),
                         "smoothing": smoothing, "k": k}
    return train, test, encoders


def main():
    df = pd.read_parquet(C.CLEAN_PARQUET)
    feats = build_features(df)
    feats.to_parquet(C.FEATURES_PARQUET, index=False)
    print(f"features shape: {feats.shape}  ->  {C.FEATURES_PARQUET}")

    # encode categoricals (default fit-all so the parquet has _le columns
    # for downstream consumers; train.py re-fits on TRAIN-only split)
    feats, encoders = encode_categoricals(feats, fit=True)
    feats.to_parquet(C.FEATURES_PARQUET, index=False)

    # show the model feature list
    numeric_feats = C.get_feature_columns(feats)
    print(f"\nnumeric/bool feature columns ({len(numeric_feats)}):")
    for c in numeric_feats:
        print(f"  - {c}")
    # leakage guard
    banned_present = [c for c in numeric_feats if c in C.BANNED]
    assert not banned_present, f"LEAKAGE: banned cols in features: {banned_present}"
    print("\nOK: features built, no banned leakage columns in numeric feature set.")


if __name__ == "__main__":
    main()
