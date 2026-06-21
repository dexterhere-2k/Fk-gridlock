"""GridLock — 06: Ablation runner.

Per spec 06 §"Evaluation plan": "**Ablations** (style after vibhuti
`evaluate_ablation.py`): with/without NLP, with/without weather."

This script:
  1. Loads the engineered feature matrix.
  2. For each ablation (a feature group removed), re-trains the
     clearance quantile regressor and the closure classifier on the
     same chronological split, then evaluates on the held-out test set.
  3. Reports P50 MAE / closure ROC-AUC for each variant + the lift vs
     the full model.
  4. Persists a JSON report at `artifacts/eval_report.json` (the spec 06
     artifact consumed by the demo and the dashboard).

We intentionally reuse the EXACT hyperparameters from `train.py` (no
tuning per ablation) so the comparison is fair — the only thing that
changes is which feature groups are present.
"""
from __future__ import annotations
import json
import sys
import time
import warnings
from copy import deepcopy
from pathlib import Path

import numpy as np
import pandas as pd
import joblib
from sklearn.ensemble import GradientBoostingRegressor, GradientBoostingClassifier
from sklearn.metrics import (mean_absolute_error, roc_auc_score, average_precision_score)

from . import config as C
from .features import encode_categoricals
from .train import make_split, predict_quantile

warnings.filterwarnings("ignore")
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

OP_CAP = 6 * 60
ABLATIONS = [
    ("full_model",     []),                                   # baseline
    ("no_nlp",         ["nlp_lanes_blocked", "nlp_needs_crane_tow",
                        "nlp_weather_water", "nlp_agency_mention",
                        "nlp_kannada_cues", "nlp_severity_cue",
                        "nlp_urgency_tone", "nlp_estimated_duration_min",
                        "nlp_event_subtype_le"]),
    ("no_weather",     ["nlp_weather_water"]),
    ("no_recurrence",  ["corridor_inc_7d", "corridor_inc_30d",
                        "corridor_days_since_last", "city_inc_1d",
                        "repeat_vehicle"]),
    ("no_corridor",    ["corridor_closure_rate", "corridor_duration_mean",
                        "corridor_cause_risk", "is_non_corridor",
                        "corridor_le"]),
    ("no_target_enc",  ["cause_closure_rate", "zone_closure_rate",
                        "veh_closure_rate", "police_station_closure_rate",
                        "planned_cause_risk", "peak_cause_risk"]),
    ("no_nlp_no_weather", ["nlp_lanes_blocked", "nlp_needs_crane_tow",
                            "nlp_weather_water", "nlp_agency_mention",
                            "nlp_kannada_cues", "nlp_severity_cue",
                            "nlp_urgency_tone", "nlp_estimated_duration_min",
                            "nlp_event_subtype_le"]),
]


def _fit_clearance(X_tr, y_tr_log, X_te, y_te_unc):
    models = {}
    for q in C.QUANTILES:
        m = GradientBoostingRegressor(
            loss="quantile", alpha=q,
            n_estimators=120, max_depth=3, learning_rate=0.05,
            subsample=0.8, min_samples_leaf=15, random_state=C.SEED,
        )
        m.fit(X_tr, y_tr_log)
        models[q] = m
    p = predict_quantile(models, X_te, calibration_offsets=None, cap_min=OP_CAP)
    return {
        "p50_mae_min": float(mean_absolute_error(y_te_unc, p[0.5])),
        "p10_mae_min": float(mean_absolute_error(y_te_unc, p[0.1])),
        "p90_mae_min": float(mean_absolute_error(y_te_unc, p[0.9])),
        "p10_p90_coverage": float(((y_te_unc >= p[0.1]) & (y_te_unc <= p[0.9])).mean()),
    }


def _fit_closure(X_tr, y_tr, X_te, y_te):
    m = GradientBoostingClassifier(
        n_estimators=120, max_depth=3, learning_rate=0.05,
        subsample=0.8, random_state=C.SEED,
    )
    spw = C.CLOSURE_SCALE_POS
    m.fit(X_tr, y_tr, sample_weight=np.where(y_tr == 1, spw, 1.0))
    p_te = m.predict_proba(X_te)[:, 1]
    return {
        "roc_auc": float(roc_auc_score(y_te, p_te)),
        "pr_auc": float(average_precision_score(y_te, p_te)),
    }


def _run_ablation(df, feat_cols, drop_cols, unc, te, split_rng):
    use_cols = [c for c in feat_cols if c not in drop_cols]
    if len(use_cols) < 5:
        return None
    tr_unc = unc[unc["split_reg"] == "train"]
    va_unc = unc[unc["split_reg"] == "val"]
    te_unc = unc[unc["split_reg"] == "test"]
    ytr_log = np.log1p(tr_unc[C.T3_DURATION].clip(lower=1, upper=OP_CAP))
    yte_unc = te_unc[C.T3_DURATION].clip(lower=1, upper=OP_CAP).to_numpy()
    # ---- clearance
    t0 = time.time()
    try:
        clr = _fit_clearance(tr_unc[use_cols].astype(float), ytr_log,
                              te_unc[use_cols].astype(float), yte_unc)
    except Exception as exc:
        return {"error": f"clearance fit failed: {exc}"}
    clr["fit_time_s"] = round(time.time() - t0, 2)
    # ---- closure (chronological split)
    closure_feat_cols = [c for c in use_cols if c != "requires_road_closure"]
    t1 = time.time()
    try:
        tr2 = df[df["split"] == "train"]
        va2 = df[df["split"] == "val"]
        te2 = df[df["split"] == "test"]
        cls = _fit_closure(
            tr2[closure_feat_cols].astype(float),
            tr2[C.T1_CLOSURE].astype(int).to_numpy(),
            te2[closure_feat_cols].astype(float),
            te2[C.T1_CLOSURE].astype(int).to_numpy(),
        )
    except Exception as exc:
        cls = {"error": f"closure fit failed: {exc}"}
    cls["fit_time_s"] = round(time.time() - t1, 2)
    return {"clearance": clr, "closure": cls,
            "n_features": len(use_cols), "dropped": list(drop_cols)}


def main():
    print("=== GridLock 06: Ablation runner ===\n")
    df = pd.read_parquet(C.FEATURES_PARQUET)
    df = make_split(df)
    df, _ = encode_categoricals(df, fit=True)
    feat_cols = C.get_feature_columns(df)

    # random 70/15/15 split on uncensored (same as the main clearance eval)
    unc = df[df["is_censored"] == 0].copy()
    rng = np.random.default_rng(C.SEED)
    perm = rng.permutation(len(unc))
    unc = unc.iloc[perm].reset_index(drop=True)
    i_tr, i_va = int(len(unc) * 0.70), int(len(unc) * 0.85)
    unc["split_reg"] = "train"; unc.loc[i_tr:i_va, "split_reg"] = "val"; unc.loc[i_va:, "split_reg"] = "test"
    te = df[df["split"] == "test"]
    split_rng = rng

    baseline = _run_ablation(df, feat_cols, [], unc, te, split_rng)
    full_clearance = baseline["clearance"]
    full_closure = baseline["closure"]

    results = {"baseline": {"name": "full_model",
                              "n_features": baseline["n_features"],
                              "clearance": full_clearance,
                              "closure": full_closure}}

    print(f"  baseline: {baseline['n_features']} features  "
          f"P50 MAE={full_clearance['p50_mae_min']:.1f}m  "
          f"closure ROC-AUC={full_closure['roc_auc']:.3f}\n")

    for name, drop_cols in ABLATIONS:
        if not drop_cols:
            continue
        print(f"  running ablation: {name}  (dropping {len(drop_cols)} cols)")
        t0 = time.time()
        r = _run_ablation(df, feat_cols, drop_cols, unc, te, split_rng)
        elapsed = round(time.time() - t0, 2)
        if r is None or "error" in r:
            print(f"    → SKIPPED ({r.get('error') if r else 'too few features'})")
            results[name] = {"name": name, "error": r.get("error") if r else "too few features"}
            continue
        clr = r["clearance"]; cls = r["closure"]
        # lift vs baseline (positive = ablation helps, negative = feature group helps)
        d_mae = clr["p50_mae_min"] - full_clearance["p50_mae_min"]
        d_auc = cls["roc_auc"] - full_closure["roc_auc"]
        print(f"    → P50 MAE={clr['p50_mae_min']:.1f}m  (Δ {d_mae:+.1f}m)  "
              f"closure ROC-AUC={cls['roc_auc']:.3f}  (Δ {d_auc:+.3f})  "
              f"[{r['n_features']} feats, {elapsed}s]")
        results[name] = {"name": name, "n_features": r["n_features"],
                          "dropped": drop_cols,
                          "clearance": clr, "closure": cls,
                          "delta_mae_min": round(d_mae, 2),
                          "delta_roc_auc": round(d_auc, 4),
                          "ablation_helps": bool(d_mae < 0 or d_auc > 0),
                          "total_time_s": elapsed}

    # ---- write report
    out = C.ARTIFACTS_DIR / "eval_report.json"
    out.write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
    print(f"\n  -> {out}")

    # ---- summary table
    print("\n=== Ablation summary ===")
    print(f"  {'ablation':<22} {'n_feat':>7} {'P50 MAE':>10} {'Δ MAE':>9} "
          f"{'ROC-AUC':>9} {'Δ AUC':>8}  helps?")
    print("  " + "-" * 78)
    base = results["baseline"]
    print(f"  {base['name']:<22} {base['n_features']:>7d} "
          f"{base['clearance']['p50_mae_min']:>9.1f}m "
          f"{'—':>9} {base['closure']['roc_auc']:>9.3f} "
          f"{'—':>8}  baseline")
    for name, _ in ABLATIONS:
        if name == "full_model":
            continue
        r = results.get(name)
        if r is None or "error" in r:
            print(f"  {name:<22} {'—':>7} {'SKIPPED':>10}")
            continue
        clr = r["clearance"]; cls = r["closure"]
        helps = "yes" if r["ablation_helps"] else "no"
        print(f"  {name:<22} {r['n_features']:>7d} "
              f"{clr['p50_mae_min']:>9.1f}m {r['delta_mae_min']:>+8.1f}m "
              f"{cls['roc_auc']:>9.3f} {r['delta_roc_auc']:>+7.3f}  {helps}")


if __name__ == "__main__":
    main()
