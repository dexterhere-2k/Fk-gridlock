"""NexGen — 01: Step 1 — Load, clean, derive targets, save chronological parquet.

Key decisions (per 00_MASTER + 09_DATA_DICTIONARY):
  * Duration = (resolved_datetime ∥ closed_datetime) − start_datetime, in
    minutes. NEVER uses `modified_datetime` (auto-stamped ~2h after start).
  * Cap at 24h, drop ≤ 0; uncensored rows with a value = 2,520 (matches spec).
  * Censoring: `status == 'active'` is right-censored (lower-bound = elapsed
    since start). Used by the AFT survival model on all 8,173 rows.
  * `priority` is dropped as a feature (99.9% leak from `corridor`).
  * Rows sorted by start_datetime — backbone for past-only recurrence + split.
"""
from __future__ import annotations
import sys
import numpy as np
import pandas as pd

from . import config as C

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

DATETIME_COLS = [
    "start_datetime", "end_datetime", "modified_datetime", "created_date",
    "closed_datetime", "resolved_datetime",
]

# Normalize casing / spelling variants observed in event_cause
CAUSE_FIXES = {
    "debris": "debris",
    "fog / low visibility": "fog_low_visibility",
    "processesion": "procession",   # common typo in dataset
}


def _norm_cause(x):
    if pd.isna(x):
        return np.nan
    s = str(x).strip().lower()
    return CAUSE_FIXES.get(s, s)


def load_raw() -> pd.DataFrame:
    df = pd.read_csv(C.RAW_CSV, low_memory=False)
    for c in DATETIME_COLS:
        if c in df.columns:
            df[c] = pd.to_datetime(df[c], errors="coerce", utc=True)
    return df


def clean(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    # ---- event_cause normalization
    df["event_cause_norm"] = df["event_cause"].map(_norm_cause)

    # ---- T1: requires_road_closure -> 1/0
    df[C.T1_CLOSURE] = df["requires_road_closure"].astype(int)

    # ---- impute start_datetime from created_date where missing (planned events)
    df["start_imputed"] = df["start_datetime"].isna().astype(int)
    df["start_datetime"] = df["start_datetime"].fillna(df["created_date"])
    df = df[df["start_datetime"].notna()].copy()
    df["start_ist"] = df["start_datetime"].dt.tz_convert("Asia/Kolkata")

    # ---- T3: resolution time (minutes) + censoring info for AFT
    end_ts = df["resolved_datetime"].fillna(df["closed_datetime"])
    delta = (end_ts - df["start_datetime"]).dt.total_seconds() / 60.0
    df["duration_raw_min"] = delta
    # censoring flag: any row WITHOUT a valid 1..1440-min duration is right-
    # censored for the AFT model. This includes:
    #   (a) status == 'active' (still open at last observation),
    #   (b) status in {closed, resolved} but no end_ts / non-positive duration
    #       (we know it ended, but we don't know when — same statistical situation).
    # Both contribute to the ~69% right-censored share called out in the spec.
    uncensored_ok = (delta >= 1) & (delta <= 24 * 60)
    df["is_censored"] = (~uncensored_ok).astype(int)
    df[C.T3_DURATION] = np.where(uncensored_ok, delta, np.nan)
    # log1p version used by the quantile regressor
    df["duration_log"] = np.log1p(df[C.T3_DURATION])

    # ---- normalize corridor / zone nulls (Non-corridor is a valid value)
    df["corridor"] = df["corridor"].fillna("Non-corridor")
    df["zone"] = df["zone"].fillna("Unknown")
    df["police_station"] = df["police_station"].fillna("Unknown")
    df["veh_type"] = df["veh_type"].fillna("unknown")
    df["event_type"] = df["event_type"].fillna("unplanned")

    # ---- drop fully-null / unusable bookkeeping columns
    null_rate = df.isna().mean()
    drop_null = [c for c in df.columns
                 if null_rate.get(c, 0) >= 0.999
                 and c not in (C.T3_DURATION, "duration_raw_min", "duration_log")]
    df = df.drop(columns=drop_null, errors="ignore")

    # ---- chronological order (backbone for split + past-only recurrence)
    df = df.sort_values("start_datetime").reset_index(drop=True)
    return df


def main():
    raw = load_raw()
    print(f"raw shape: {raw.shape}")
    df = clean(raw)

    df.to_parquet(C.CLEAN_PARQUET, index=False)
    print(f"clean shape: {df.shape}  ->  {C.CLEAN_PARQUET}")

    # ---- sanity report (spec 00 §4 acceptance)
    n = len(df)
    t1 = df[C.T1_CLOSURE].mean()
    res_uncens = df.loc[df["is_censored"] == 0, C.T3_DURATION]
    print("\n=== target sanity ===")
    print(f"T1 requires_road_closure: {t1*100:.1f}% positive (expect ~8.3%)")
    print(f"T3 rows with value      : {df[C.T3_DURATION].notna().sum()} "
          f"(censored/active={int((df['is_censored']==1).sum())}, "
          f"uncensored={int(res_uncens.notna().sum())})")
    print(f"T3 uncensored median min: {res_uncens.median():.1f}  "
          f"(spec target ~46)")
    print(f"start imputed rows      : {int(df['start_imputed'].sum())}")
    print(f"date range              : {df['start_datetime'].min()} -> "
          f"{df['start_datetime'].max()}")
    print(f"event_type counts       : {df['event_type'].value_counts().to_dict()}")
    assert 0.05 < t1 < 0.12, "T1 positive rate off — check mapping"
    assert df[C.T1_CLOSURE].isna().sum() == 0, "T1 has NaNs"
    # spec exact: 2,520 labeled durations
    assert 2400 <= df[C.T3_DURATION].notna().sum() <= 2700, \
        f"T3 labeled count {df[C.T3_DURATION].notna().sum()} off spec (~2,520)"
    print("\nOK: data_prep complete.")


if __name__ == "__main__":
    main()
