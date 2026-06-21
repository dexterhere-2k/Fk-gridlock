"""GridLock — 06: Learning loop (the live "model updates from outcomes").

Per spec 06 §"Debrief → learning loop":
  - log predicted vs actual clearance → nightly retrain → push to MLflow

In our deployment we don't ship MLflow (it would require a registry
service, the spec 00 §"locked stack" already dropped it). Instead we
implement the same loop with the SQLite ledger + a retrain trigger:

  1. /api/outcome logs predicted-vs-actual to `artifacts/ledger.sqlite3`.
  2. This script reads those outcomes and:
       a) computes the **learning signal** — how much the model has
          drifted since the last retrain (per-cause MAE, corridor
          closure accuracy, etc.)
       b) decides whether a **retrain is triggered** (configurable
          threshold on # of new outcomes + MAE drift)
       c) when triggered, re-runs `src.features` + `src.train` against
          the augmented data, with a **freeze of past-only recurrence**
          — so the new past-only features respect the "no peeking"
          rule (per 09 §"recurrence features must be past-only").
  3. Records the run in `artifacts/learning_log.json` (the artifact
     the dashboard reads to show "model last retrained on N outcomes at
     timestamp, MAE improved by X min").

This is the "make it a live feature, not a slide" promise from the spec.
"""
from __future__ import annotations
import json
import shutil
import subprocess
import sys
import time
import warnings
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from . import config as C
from .api.ledger import Ledger

warnings.filterwarnings("ignore")
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

LEARN_LOG = C.ARTIFACTS_DIR / "learning_log.json"
RETRAIN_THRESHOLD = {
    "min_new_outcomes": 25,   # need at least this many new outcomes
    "min_mae_drift_min": 5,  # retrain if mean abs error exceeds last run by this
}


def compute_learning_signal(ledger: Ledger) -> dict:
    """Look at the outcome table and produce per-cause + per-corridor signals.

    Returns a dict ready to be merged into `learning_log.json`.
    """
    outcomes = ledger.get_outcomes()
    if not outcomes:
        return {"n_outcomes": 0, "per_cause": {}, "per_corridor": {}}
    # join with predictions to get predicted P50
    rows = []
    for o in outcomes:
        if o["actual_p50_min"] is None:
            continue
        with ledger._conn() as c:
            pred_row = c.execute(
                "SELECT payload_json FROM predictions WHERE id = ?",
                (o["event_id"],)).fetchone()
        predicted_p50 = None
        predicted_closure = None
        if pred_row is not None:
            try:
                p = json.loads(pred_row["payload_json"])
                predicted_p50 = p.get("p50")
                predicted_closure = (p.get("closure_prob", 0) or 0) >= 0.5
            except Exception:
                pass
        rows.append({
            "event_id": o["event_id"],
            "actual_p50": o["actual_p50_min"],
            "actual_p90": o.get("actual_p90_min"),
            "actual_closure": bool(o.get("actual_closure")) if o.get("actual_closure") is not None else None,
            "predicted_p50": predicted_p50,
            "predicted_closure": predicted_closure,
        })
    df = pd.DataFrame(rows)
    if df.empty:
        return {"n_outcomes": 0, "per_cause": {}, "per_corridor": {}}
    df["err_min"] = (df["actual_p50"] - df["predicted_p50"]).abs()
    df["closure_correct"] = (
        (df["actual_closure"].fillna(False) == df["predicted_closure"].fillna(False))
        & df["actual_closure"].notna()
    )
    per_cause = {}
    for cause, grp in df.groupby(df["event_id"].str.split("-").str[0]):
        per_cause[str(cause)] = {
            "n": int(len(grp)),
            "p50_mae_min": round(float(grp["err_min"].mean()), 2),
            "closure_accuracy": round(float(grp["closure_correct"].mean()), 3) if len(grp) else 0.0,
        }
    # global stats
    return {
        "n_outcomes": int(len(df)),
        "global_p50_mae_min": round(float(df["err_min"].mean()), 2),
        "global_closure_accuracy": round(float(df["closure_correct"].mean()), 3),
        "per_cause": per_cause,
        "ts_min": float(df["err_min"].min()),
        "ts_max": float(df["err_min"].max()),
        "ts_median": float(df["err_min"].median()),
    }


def should_retrain(signal: dict, last_run: dict | None) -> tuple[bool, str]:
    if not last_run:
        # never retrained before
        if signal.get("n_outcomes", 0) >= RETRAIN_THRESHOLD["min_new_outcomes"]:
            return True, "first retrain (enough outcomes collected)"
        return False, f"need {RETRAIN_THRESHOLD['min_new_outcomes']} outcomes"
    new_n = signal["n_outcomes"] - last_run.get("n_outcomes_at_retrain", 0)
    drift = signal.get("global_p50_mae_min", 0) - last_run.get("mae_at_retrain", 0)
    if new_n < RETRAIN_THRESHOLD["min_new_outcomes"]:
        return False, f"only {new_n} new outcomes (need {RETRAIN_THRESHOLD['min_new_outcomes']})"
    if drift < RETRAIN_THRESHOLD["min_mae_drift_min"]:
        return False, f"MAE drift {drift:.1f}m below threshold"
    return True, f"{new_n} new outcomes + MAE drift {drift:.1f}m"


def run_retrain() -> dict:
    """Re-run features + train against the augmented data. The past-only
    recurrence rules (09 §3) keep the new model honest — the new
    "past" includes the outcomes we just logged."""
    print("  → re-running data_prep + features + train ...")
    t0 = time.time()
    for mod in ("data_prep", "features", "train", "evaluate"):
        print(f"    python -m src.{mod}")
        r = subprocess.run(
            [sys.executable, "-m", f"src.{mod}"],
            cwd=str(C.ROOT), capture_output=True, text=True, timeout=600)
        if r.returncode != 0:
            return {"status": "failed", "stage": mod,
                    "stderr": r.stderr[-800:]}
    return {"status": "ok", "elapsed_s": round(time.time() - t0, 1)}


def main():
    print("=== GridLock 06: Learning loop ===\n")
    ledger = Ledger(C.ARTIFACTS_DIR / "ledger.sqlite3")

    signal = compute_learning_signal(ledger)
    print(f"  outcomes: {signal.get('n_outcomes', 0)}")
    if signal.get("global_p50_mae_min") is not None:
        print(f"  global P50 MAE: {signal['global_p50_mae_min']}m")
        print(f"  global closure accuracy: {signal['global_closure_accuracy']:.3f}")
    print(f"  per-cause (top 5 by n):")
    for cause, stats in sorted(signal.get("per_cause", {}).items(),
                                key=lambda kv: -kv[1]["n"])[:5]:
        print(f"    {cause:<20s} n={stats['n']:>3d}  "
              f"P50 MAE={stats['p50_mae_min']:.1f}m  "
              f"closure_acc={stats['closure_accuracy']:.2f}")

    # load previous run log
    last_run = None
    if LEARN_LOG.exists():
        try:
            log = json.loads(LEARN_LOG.read_text(encoding="utf-8"))
            last_run = log.get("last_run")
        except Exception:
            last_run = None

    triggered, reason = should_retrain(signal, last_run)
    print(f"\n  retrain triggered? {triggered}  ({reason})")

    out = {
        "computed_at": datetime.now(tz=timezone.utc).isoformat(),
        "signal": signal,
        "retrain_triggered": triggered,
        "trigger_reason": reason,
    }

    if triggered:
        rt = run_retrain()
        out["retrain"] = rt
        if rt["status"] == "ok":
            out["last_run"] = {
                "ts": out["computed_at"],
                "n_outcomes_at_retrain": signal["n_outcomes"],
                "mae_at_retrain": signal.get("global_p50_mae_min"),
                "elapsed_s": rt["elapsed_s"],
            }
            print(f"  retrain SUCCEEDED in {rt['elapsed_s']}s")
        else:
            print(f"  retrain FAILED at stage '{rt.get('stage')}': "
                  f"{rt.get('stderr', '')[:200]}")
    else:
        out["last_run"] = last_run  # unchanged

    LEARN_LOG.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    print(f"\n  -> {LEARN_LOG}")


if __name__ == "__main__":
    main()
