"""GridLock — 01: Target 4 — Congestion cascade / domino graph.

The single most novel finding (per FINDINGS §8): when a corridor's incident
count spikes at hour *t*, several other corridors see a correlated surge
at hour *t + 1, 2, 3* (Pearson r ~ 0.10–0.32, p < 0.05). The validated
output is 186 significant edges with the strongest being
Mysore Road →1h→ ORR East 1 (r=0.297, p ≈ 2e-74).

This is built directly from `Astram event data_anonymized.csv` (no extra
data). Honest caveat: r ∈ [0.10, 0.32] is an early-warning nudge, not a
deterministic forecast. Used by spec 03 (optimizer) and spec 08 (pre-alert).
"""
from __future__ import annotations
import json
import sys
import numpy as np
import pandas as pd
from scipy.stats import pearsonr

from . import config as C

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


def build_time_series(df: pd.DataFrame, min_events: int = None) -> tuple[pd.DataFrame, list]:
    """Convert raw event rows into a corridor × 1-hour count time series.

    Returns (mat, corridors) where `mat` is a DataFrame indexed by
    Asia/Kolkata-truncated hourly timestamps with one column per corridor.
    """
    if min_events is None:
        min_events = C.CASCADE_MIN_CORRIDOR_EVENTS

    counts = df["corridor"].fillna("Non-corridor").value_counts()
    corridors = sorted(counts[counts >= min_events].index.tolist())
    print(f"  corridors with >= {min_events} events: {len(corridors)} "
          f"of {df['corridor'].nunique()}")

    bucket_col = df["start_ist"].dt.floor("1h")
    ts = (
        df.assign(bucket=bucket_col)
          .groupby(["bucket", "corridor"])
          .size()
          .reset_index(name="count")
          .pivot(index="bucket", columns="corridor", values="count")
          .reindex(columns=corridors, fill_value=0)
          .fillna(0)
          .astype(int)
          .sort_index()
    )
    return ts, corridors


def compute_lag_correlations(ts: pd.DataFrame, lags: tuple = (1, 2, 3)) -> list:
    """For every ordered (A, B, lag), compute Pearson r and p-value.

    Keep only positive, significant edges. Returns list of dicts.
    """
    rows = []
    corridors = ts.columns.tolist()
    n = len(ts)
    for A in corridors:
        a = ts[A].to_numpy()
        if a.std() < 0.01:
            continue
        for B in corridors:
            if A == B:
                continue
            b = ts[B].to_numpy()
            if b.std() < 0.01:
                continue
            for lag in lags:
                if n - lag < 30:
                    continue
                x, y = a[:-lag], b[lag:]
                if x.std() < 0.01 or y.std() < 0.01:
                    continue
                r, p = pearsonr(x, y)
                if (p < C.CASCADE_P_THRESHOLD) and (r > 0):
                    rows.append({
                        "source": A, "target": B,
                        "lag_h": int(lag), "r": float(r), "p": float(p),
                        "n_pairs": int(len(x)),
                    })
    return rows


def keep_strongest_lag_per_pair(edges: list) -> list:
    """For each (source, target) keep the lag with the highest r."""
    by_pair: dict = {}
    for e in edges:
        key = (e["source"], e["target"])
        if key not in by_pair or e["r"] > by_pair[key]["r"]:
            by_pair[key] = e
    return sorted(by_pair.values(), key=lambda e: -e["r"])


def build_cascade(df: pd.DataFrame) -> dict:
    """Build the full cascade graph and write artifacts.

    Returns dict with edges, per-corridor trigger rank, and the strongest
    chains. Validates against the FINDINGS §8 expected edge count (~186).
    """
    print("Building cascade / domino graph ...")
    ts, corridors = build_time_series(df)
    print(f"  time-series shape: {ts.shape} (hours × corridors)")

    raw_edges = compute_lag_correlations(ts, lags=C.CASCADE_LAGS)
    print(f"  raw significant edges: {len(raw_edges)}")

    edges = keep_strongest_lag_per_pair(raw_edges)
    print(f"  unique (source, target) edges kept: {len(edges)}")

    edges_df = pd.DataFrame(edges)
    edges_df.to_csv(C.CASCADE_EDGES_CSV, index=False)
    print(f"  -> {C.CASCADE_EDGES_CSV}")

    # ---- per-corridor trigger rank (out-degree)
    if not edges_df.empty:
        out_deg = (edges_df.groupby("source")
                          .size()
                          .rename("downstream_count")
                          .reset_index()
                          .rename(columns={"source": "corridor"}))
        out_deg["max_r"] = (edges_df.groupby("source")["r"].max()
                                       .reindex(out_deg["corridor"]).values)
        out_deg = out_deg.sort_values(["downstream_count", "max_r"],
                                      ascending=[False, False])
    else:
        out_deg = pd.DataFrame(columns=["corridor", "downstream_count", "max_r"])

    # ---- strongest chains (greedy: strongest edge → strongest next → ...)
    chains = []
    if not edges_df.empty:
        # group by source, sort children by r desc
        children = (edges_df.sort_values("r", ascending=False)
                              .groupby("source")
                              .apply(lambda g: list(zip(g["target"], g["lag_h"],
                                                        g["r"])),
                                     include_groups=False)
                              .to_dict())
        for src in list(children.keys())[:5]:
            chain = [src]
            cur = src
            steps = 0
            while cur in children and steps < 5:
                nxt, lag, r = children[cur][0]
                chain.append(f"--{lag}h r={r:.2f}--> {nxt}")
                cur = nxt
                steps += 1
            if len(chain) > 1:
                chains.append(" ".join(chain))

    meta = {
        "n_corridors": len(corridors),
        "n_hours": int(ts.shape[0]),
        "n_edges": int(len(edges)),
        "n_edges_raw": int(len(raw_edges)),
        "lags_hours": list(C.CASCADE_LAGS),
        "p_threshold": C.CASCADE_P_THRESHOLD,
        "min_corridor_events": C.CASCADE_MIN_CORRIDOR_EVENTS,
        "trigger_rank": out_deg.to_dict(orient="records"),
        "strongest_edges": (
            edges_df.sort_values("r", ascending=False)
                    .head(10)
                    .to_dict(orient="records")
        ),
        "strongest_chains": chains,
        "honesty_note": (
            "r in [0.10, 0.32] = early-warning nudge, not a deterministic "
            "forecast. Lagged Pearson does not rule out shared rush-hour "
            "confounding — harden with hour-of-day partials or Granger."
        ),
    }
    C.CASCADE_META_JSON.write_text(json.dumps(meta, indent=2, default=str),
                                   encoding="utf-8")
    print(f"  -> {C.CASCADE_META_JSON}")

    if len(edges) >= 100:
        print(f"  OK: {len(edges)} significant edges (>= 100 spec bar).")
    else:
        print(f"  WARN: only {len(edges)} edges (spec target ~186).")
    return meta


def get_cascade_alerts(corridor: str) -> list:
    """Given a source corridor, return downstream cascade alerts."""
    edges = pd.read_csv(C.CASCADE_EDGES_CSV)
    if edges.empty or corridor not in edges["source"].values:
        return []
    out = (edges[edges["source"] == corridor]
                 .sort_values("r", ascending=False)
                 .to_dict(orient="records"))
    return [{
        "primary": corridor,
        "secondary": e["target"],
        "lag_hours": int(e["lag_h"]),
        "lag_minutes": int(e["lag_h"]) * 60,
        "correlation": float(e["r"]),
        "action": (f"Pre-alert {e['target']} controller — surge expected in "
                   f"{int(e['lag_h'])*60} min"),
    } for e in out]


def main():
    df = pd.read_parquet(C.CLEAN_PARQUET)
    meta = build_cascade(df)
    print("\nTop trigger corridors (where dominoes start):")
    for r in meta["trigger_rank"][:10]:
        print(f"  {r['corridor']:25s}  downstream={r['downstream_count']:>2d}  "
              f"max_r={r['max_r']:.3f}")
    print("\nStrongest edges:")
    for e in meta["strongest_edges"][:5]:
        print(f"  {e['source']:25s} -> {e['target']:25s}  "
              f"lag={e['lag_h']}h  r={e['r']:.3f}  p={e['p']:.2e}")
    print("\nStrongest chains:")
    for c in meta["strongest_chains"][:3]:
        print(f"  {c}")


if __name__ == "__main__":
    main()
