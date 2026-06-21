"""GridLock — 06: Historical replay (time-travel demo feed).

Per spec 06 §"Legit external data → Mock real-time via historical
replay": the live WebSocket should replay the historical `start_datetime`
column at a configurable speed, so the demo looks live but is fully
reproducible. This module produces the replayed event stream that the
backend's `/api/ws/live-status` serves.

Usage:
  # CLI dump — print the first 20 replayed events
  python -m src.demo_replay --speed 60 --limit 20

  # Imported in src/api/main.py's WebSocket handler.
  from src.demo_replay import replay_events
  for event in replay_events(speed=60, on='corridor_pulse'):
      await ws.send_json(event)
"""
from __future__ import annotations
import random
import sys
import time
from datetime import datetime, timezone
from typing import Optional

import numpy as np
import pandas as pd

from . import config as C

# Replay speed: 60 means 1 minute of wall-clock per 1 second of data.
DEFAULT_SPEED = 60.0
# Which corridor to center the replay on (None = round-robin all)
DEFAULT_CORRIDOR = None

# Module-level cache of corridor → risk_score so we don't re-read the
# CSV on every pulse. This makes the synthetic `risk_score` field
# reflect the real spec 01 corridor prior (so values vary across
# corridors, instead of all reading 0.30/0.80).
_CORRIDOR_RISK: dict[str, float] = {}


def _load_corridor_risk() -> dict[str, float]:
    """Read `artifacts/corridor_risk.csv` once and return
    {corridor: risk_score}. Falls back to {} if the file is missing.
    """
    if _CORRIDOR_RISK:
        return _CORRIDOR_RISK
    try:
        df = pd.read_csv(C.CORRIDOR_RISK_CSV)
        _CORRIDOR_RISK.update(dict(zip(df["corridor"].astype(str),
                                       df["risk_score"].astype(float))))
    except Exception:
        pass
    return _CORRIDOR_RISK


def _load_event_log() -> pd.DataFrame:
    """Return the historical incident log sorted by start_datetime."""
    df = pd.read_parquet(C.CLEAN_PARQUET)
    df = df.dropna(subset=["start_datetime"])
    df = df.sort_values("start_datetime").reset_index(drop=True)
    return df


def _event_to_pulse(row: pd.Series, replay_ts: float) -> dict:
    """Convert a historical row into a WebSocket-style pulse message.

    The synthetic "kind" cycles through three flavors so the UI shows
    a realistic mix of pulses:
      - corridor_pulse: a new incident on a corridor
      - corridor_resolved: the incident was closed/resolved
      - cascade_alert: a high-r cascade edge fires

    `risk_score` is derived from the real spec 01 corridor prior
    (corridor_risk.csv) so the demo shows meaningful variation
    between corridors (instead of a synthetic 0.30/0.80 floor).
    `eta_min` is floored at 5 and gets ±15% jitter so the dashboard
    doesn't show "eta 0m" for fast-cleared events.
    """
    corridor = str(row.get("corridor") or "Non-corridor")
    cause = str(row.get("event_cause") or "incident")
    is_closed = str(row.get("status", "")) == "closed"
    # use a deterministic per-row hash to choose the kind
    h = abs(hash((row.name, corridor, cause))) % 3
    if is_closed and h == 1:
        kind = "corridor_resolved"
    elif h == 2 and not is_closed:
        kind = "cascade_alert"
    else:
        kind = "corridor_pulse"
    p50 = row.get(C.T3_DURATION)
    p50_finite = (p50 is not None and not pd.isna(p50) and float(p50) > 0)
    p50_min = float(p50) if p50_finite else 0.0
    # eta: historical p50 × ±15% jitter, floored at 5 min so fast-cleared
    # events don't render as "eta 0m" (which looks broken in the UI).
    if p50_finite:
        eta_min = int(round(max(5, p50_min * (0.85 + random.random() * 0.3))))
    else:
        eta_min = 5  # default for rows with no recorded duration
    # risk_score: real spec 01 corridor prior + closure bonus + ETA bonus
    # (so the same corridor reads consistently across pulses but the
    # value also reflects this specific event's severity).
    base_risk = _load_corridor_risk().get(corridor, 0.30)
    closure_bonus = 0.20 if bool(row.get(C.T1_CLOSURE, 0)) else 0.0
    eta_bonus = min(0.15, 0.03 * (eta_min // 30))  # up to +0.15 for slow
    risk_score = round(min(1.0, max(0.0, base_risk + closure_bonus + eta_bonus)), 2)
    return {
        "ts": replay_ts,
        "kind": kind,
        "corridor": corridor,
        "corridor_risk": round(base_risk, 2),
        "cause": cause,
        "event_id": str(row.get("id", row.name)),
        "p50_min": round(p50_min, 1),
        # ETA = median clearance in minutes (same as the T3 prediction);
        # rendered as a friendly "eta N min" pill in the LiveView pulse.
        "eta_min": eta_min,
        "closure": bool(row.get(C.T1_CLOSURE, 0)),
        # risk score: real corridor prior + per-event deltas (see above)
        "risk_score": risk_score,
    }


def replay_events(
    speed: float = DEFAULT_SPEED,
    corridor: Optional[str] = DEFAULT_CORRIDOR,
    limit: Optional[int] = None,
    since: Optional[str] = None,
    yield_seconds: bool = False,
    max_sleep_s: float = 0.5,
):
    """Generator: yields replayed events at `speed`× real time.

    `speed=60`  → each historical second = 1/60s wall-clock = 1 minute/sec
    `speed=1.0` → real time (one historical second per second of wall-clock)
    `corridor`  → filter to one corridor (None = all)
    `limit`     → max events to yield (None = all 8,000)
    `since`     → ISO date string — only events after this (useful for the
                 demo to start "now" and walk forward)
    `yield_seconds` → also yield the inter-event sleep time (for tests)
    `max_sleep_s`   → cap the wall-clock sleep between pulses (default
                       0.5s) so a 40-day data gap doesn't pause the demo
    """
    df = _load_event_log()
    if corridor is not None:
        df = df[df["corridor"].fillna("Non-corridor") == corridor]
    if since is not None:
        cutoff = pd.to_datetime(since, utc=True)
        df = df[df["start_datetime"] >= cutoff]
    if df.empty:
        return
    df = df.reset_index(drop=True)
    if limit is not None:
        df = df.head(limit)
    prev_ts = df["start_datetime"].iloc[0]
    replay_start = time.time()
    for _, row in df.iterrows():
        cur_ts = row["start_datetime"]
        # sleep the right amount of wall-clock (capped so a 40-day gap
        # between rare incidents doesn't stall the demo)
        delta_s = (cur_ts - prev_ts).total_seconds() / max(1e-9, speed)
        delta_s = min(delta_s, max_sleep_s)
        if yield_seconds:
            yield delta_s
        else:
            if delta_s > 0:
                time.sleep(delta_s)
        replay_ts = time.time()
        yield _event_to_pulse(row, replay_ts)
        prev_ts = cur_ts


def main():
    import argparse
    p = argparse.ArgumentParser(description="Re-emit the historical event log as a live feed")
    p.add_argument("--speed", type=float, default=DEFAULT_SPEED,
                   help="Replay speed multiplier (default 60 = 1 min/sec)")
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--corridor", default=None)
    args = p.parse_args()
    print(f"=== Replay at {args.speed}× speed, limit {args.limit} ===\n")
    for i, pulse in enumerate(replay_events(
            speed=args.speed, corridor=args.corridor, limit=args.limit)):
        ts = datetime.fromtimestamp(pulse["ts"], tz=timezone.utc).isoformat(timespec="seconds")
        print(f"  {i:>3d}  {pulse['kind']:>18s}  {pulse['corridor']:>22s}  "
              f"{pulse['cause']:>18s}  closure={pulse['closure']}")


if __name__ == "__main__":
    main()
