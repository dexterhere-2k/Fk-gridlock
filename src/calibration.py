"""GridLock — 06: Calibration curves + risk-prior vs actual analysis.

Per spec 06 §"Evaluation plan":
  - "Risk prior: calibration plot vs actual closure rates."

Produces the data for a reliability diagram (predicted probability vs
observed frequency) for the closure classifier, and per-corridor
risk-score-vs-actual-closure-rate comparison for the corridor risk prior.
Output: `artifacts/calibration.json` (the frontend can render it
without re-running the analysis).
"""
from __future__ import annotations
import json
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import joblib
from sklearn.calibration import calibration_curve

from . import config as C
from .features import encode_categoricals
from .train import make_split

warnings.filterwarnings("ignore")
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


def _calibration_curve_data(p_pred: np.ndarray, y_true: np.ndarray,
                             n_bins: int = 10) -> dict:
    """Brier-style reliability diagram data (predicted vs observed)."""
    bin_edges = np.linspace(0.0, 1.0, n_bins + 1)
    rows = []
    n_total = len(p_pred)
    n_pos = int(y_true.sum())
    for i in range(n_bins):
        lo, hi = bin_edges[i], bin_edges[i + 1]
        mask = (p_pred >= lo) & (p_pred < hi if i < n_bins - 1 else p_pred <= hi)
        if mask.sum() < 5:
            continue
        rows.append({
            "bin_lo": round(float(lo), 3),
            "bin_hi": round(float(hi), 3),
            "n": int(mask.sum()),
            "frac_predicted": round(float(p_pred[mask].mean()), 4),
            "frac_observed": round(float(y_true[mask].mean()), 4),
            "gap": round(float(y_true[mask].mean() - p_pred[mask].mean()), 4),
        })
    # Brier score (MSE between predicted and observed)
    brier = float(((p_pred - y_true) ** 2).mean())
    # Expected Calibration Error (ECE): |gap| weighted by bin size
    ece = float(sum(abs(r["gap"]) * r["n"] for r in rows) / max(1, n_total))
    return {"bins": rows, "n_total": n_total, "n_positive": n_pos,
            "brier_score": round(brier, 4), "expected_calibration_error": round(ece, 4),
            "positive_rate": round(float(y_true.mean()), 4)}


def _corridor_risk_calibration(corridor_risk: pd.DataFrame,
                                 df: pd.DataFrame) -> dict:
    """For each corridor, compare the risk_score (0..1) to the actual
    closure_rate and the actual median clearance (operational sanity)."""
    df = df.copy()
    df["corridor"] = df["corridor"].fillna("Non-corridor")
    out = []
    for _, row in corridor_risk.iterrows():
        c = row["corridor"]
        sub = df[df["corridor"] == c]
        if len(sub) < 5:
            continue
        actual_closure = float(sub["requires_road_closure"].mean())
        out.append({
            "corridor": c,
            "n_events": int(len(sub)),
            "risk_score": round(float(row["risk_score"]), 3),
            "predicted_risk_rank": int(row.name) + 1,
            "actual_closure_rate": round(actual_closure, 3),
            "actual_med_clear_min": round(float(sub["duration_min"].median() or 0), 1),
            "actual_p90_clear_min": round(
                float(sub["duration_min"].quantile(0.9) or 0), 1),
        })
    out.sort(key=lambda r: r["risk_score"], reverse=True)
    return {"corridors": out}


def main():
    print("=== GridLock 06: Calibration analysis ===\n")
    df = pd.read_parquet(C.FEATURES_PARQUET)
    df = make_split(df)
    df, _ = encode_categoricals(df, fit=True)
    closure = joblib.load(C.CLOSURE_PKL)
    feat_cols = C.get_feature_columns(df, drop_closure_target=True)
    te = df[df["split"] == "test"]
    X = te[feat_cols].astype(float)
    y = te[C.T1_CLOSURE].astype(int).to_numpy()
    p = closure["model"].predict_proba(X)[:, 1]
    cal = _calibration_curve_data(p, y, n_bins=10)
    print(f"  closure calibration (test set, n={cal['n_total']}, "
          f"positives={cal['n_positive']}):")
    print(f"    Brier score:        {cal['brier_score']:.4f}")
    print(f"    ECE:                {cal['expected_calibration_error']:.4f}")
    print(f"    Positive rate:      {cal['positive_rate']:.4f}")
    for b in cal["bins"]:
        flag = "  ⚠" if abs(b["gap"]) > 0.05 else "   "
        print(f"    {flag} [{b['bin_lo']:.2f}-{b['bin_hi']:.2f}]  "
              f"n={b['n']:>3d}  pred={b['frac_predicted']:.3f}  "
              f"obs={b['frac_observed']:.3f}  gap={b['gap']:+.3f}")

    # ---- corridor risk prior vs actual
    corridor_risk = pd.read_csv(C.CORRIDOR_RISK_CSV)
    cr = _corridor_risk_calibration(corridor_risk, df)
    print(f"\n  corridor risk prior vs actual closure rate (top 10 by risk):")
    for r in cr["corridors"][:10]:
        print(f"    {r['corridor']:>30s}  risk={r['risk_score']:.3f}  "
              f"actual_closure={r['actual_closure_rate']:.3f}  "
              f"n={r['n_events']:>5d}  med_clear={r['actual_med_clear_min']:.0f}m")

    # ---- Pearson correlation: risk_score vs actual_closure_rate (across corridors)
    risk_vals = [r["risk_score"] for r in cr["corridors"]]
    actual_vals = [r["actual_closure_rate"] for r in cr["corridors"]]
    if len(risk_vals) > 2:
        corr = float(np.corrcoef(risk_vals, actual_vals)[0, 1])
    else:
        corr = 0.0
    print(f"\n  Pearson(risk_score, actual_closure_rate): {corr:.3f}")

    # ---- write report
    out = C.ARTIFACTS_DIR / "calibration.json"
    out.write_text(json.dumps({
        "closure_calibration": cal,
        "corridor_risk_calibration": cr,
        "risk_vs_actual_pearson": corr,
    }, indent=2, default=str), encoding="utf-8")
    print(f"\n  -> {out}")


if __name__ == "__main__":
    main()
