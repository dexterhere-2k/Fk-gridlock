"""GridLock — 02: ablation — clearance with vs without NLP features.

Per spec 02 §5: "Train clearance model with vs without NLP features; report
MAE delta (expect a small but real gain on the rows that have rich text)."

The ablation is the proof that the spec 02 layer actually adds value. It runs
on the same random 70/15/15 split as the main clearance model, using the
same hyperparameters, and reports:
  - P50 MAE without NLP features
  - P50 MAE with NLP features
  - MAE delta (positive = NLP helps)
  - Per-coverage-group breakdown (rows with text vs rows without)

Output: appended to `artifacts/metrics.json` under the key `nlp_ablation`.
"""
from __future__ import annotations
import json
import sys
import warnings
import numpy as np
import pandas as pd
import joblib
from sklearn.ensemble import GradientBoostingRegressor
from sklearn.metrics import mean_absolute_error

from . import config as C
from .features import encode_categoricals
from .train import make_split, predict_quantile

warnings.filterwarnings("ignore")
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

OP_CAP_MIN = 6 * 60


def _fit_clearance(df, feat_cols, log_targets, cap=OP_CAP_MIN):
    """Fit 3 quantile GBRs and return the model dict (same as train.py)."""
    models = {}
    for q in C.QUANTILES:
        m = GradientBoostingRegressor(
            loss="quantile", alpha=q,
            n_estimators=C.QUANTILE_PARAMS["n_estimators"],
            max_depth=C.QUANTILE_PARAMS["max_depth"],
            learning_rate=C.QUANTILE_PARAMS["learning_rate"],
            subsample=C.QUANTILE_PARAMS["subsample"],
            min_samples_leaf=C.QUANTILE_PARAMS.get("min_samples_leaf", 15),
            random_state=C.QUANTILE_PARAMS["random_state"],
        )
        m.fit(df[feat_cols].astype(float), log_targets)
        models[q] = m
    return models


def _evaluate_p50(models, X, y, cap=OP_CAP_MIN):
    p = predict_quantile(models, X, calibration_offsets=None, cap_min=cap)
    return float(mean_absolute_error(y, p[0.5]))


def main():
    print("=== GridLock 02: NLP ablation ===\n")
    df = pd.read_parquet(C.FEATURES_PARQUET)
    df = make_split(df)
    df, _ = encode_categoricals(df, fit=True)

    # the 4 NLP features that spec 02 §5 singles out
    NLP_FEATS = [
        "nlp_lanes_blocked", "nlp_needs_crane_tow",
        "nlp_weather_water", "nlp_event_subtype_le",
    ]
    base_feats = C.get_feature_columns(df)

    # Sanity: ensure the NLP features are present (not banned)
    for f in NLP_FEATS:
        assert f in base_feats, f"missing NLP feature in column list: {f}"
    no_nlp_feats = [f for f in base_feats if f not in NLP_FEATS]
    print(f"  with-NLP feats: {len(base_feats)}    no-NLP feats: {len(no_nlp_feats)}")

    # random 70/15/15 split on uncensored (same as main clearance)
    unc = df[df["is_censored"] == 0].copy()
    rng = np.random.default_rng(C.SEED)
    perm = rng.permutation(len(unc))
    unc = unc.iloc[perm].reset_index(drop=True)
    i_tr = int(len(unc) * 0.70)
    i_va = int(len(unc) * 0.85)
    tr_unc = unc.iloc[:i_tr]
    va_unc = unc.iloc[i_tr:i_va]
    te_unc = unc.iloc[i_va:]

    ytr_log = np.log1p(tr_unc[C.T3_DURATION].clip(lower=1, upper=OP_CAP_MIN))
    yte_unc = te_unc[C.T3_DURATION].clip(lower=1, upper=OP_CAP_MIN).to_numpy()

    # ---- train both variants
    print("  fitting model WITHOUT NLP features ...")
    m_no = _fit_clearance(tr_unc, no_nlp_feats, ytr_log)
    p50_no = _evaluate_p50(m_no, te_unc[no_nlp_feats].astype(float), yte_unc)
    print(f"  -> P50 MAE no-NLP: {p50_no:.2f} min")

    print("  fitting model WITH NLP features ...")
    m_yes = _fit_clearance(tr_unc, base_feats, ytr_log)
    p50_yes = _evaluate_p50(m_yes, te_unc[base_feats].astype(float), yte_unc)
    print(f"  -> P50 MAE with-NLP: {p50_yes:.2f} min")

    delta = p50_no - p50_yes
    print(f"  MAE delta (no-NLP − with-NLP): {delta:+.2f} min "
          f"({'NLP helps' if delta > 0 else 'NLP does not help'})")

    # ---- breakdown by text-presence
    te_unc = te_unc.copy()
    te_unc["_has_text"] = (te_unc["description"].fillna("").str.len() > 0).to_numpy() \
        if "description" in te_unc.columns else True
    if "_has_text" not in te_unc.columns:
        te_unc["_has_text"] = True

    p_no_full = predict_quantile(m_no, te_unc[no_nlp_feats].astype(float))
    p_yes_full = predict_quantile(m_yes, te_unc[base_feats].astype(float))
    grp_results = {}
    for label, mask in [("with_text", te_unc["_has_text"]),
                         ("no_text", ~te_unc["_has_text"])]:
        if mask.sum() == 0:
            continue
        mae_no = float(mean_absolute_error(yte_unc[mask], p_no_full[0.5][mask]))
        mae_yes = float(mean_absolute_error(yte_unc[mask], p_yes_full[0.5][mask]))
        grp_results[label] = {
            "n": int(mask.sum()),
            "p50_mae_no_nlp_min": round(mae_no, 2),
            "p50_mae_with_nlp_min": round(mae_yes, 2),
            "delta_min": round(mae_no - mae_yes, 2),
        }
        print(f"  {label:>8s} (n={mask.sum():>4d}): no-NLP {mae_no:.1f}  "
              f"with-NLP {mae_yes:.1f}  delta {mae_no - mae_yes:+.1f}")

    # ---- persist into metrics.json
    metrics = json.loads(C.METRICS_JSON.read_text(encoding="utf-8"))
    metrics["nlp_ablation"] = {
        "p50_mae_no_nlp_min": round(p50_no, 2),
        "p50_mae_with_nlp_min": round(p50_yes, 2),
        "delta_min": round(delta, 2),
        "n_features_no_nlp": len(no_nlp_feats),
        "n_features_with_nlp": len(base_feats),
        "nlp_features": NLP_FEATS,
        "by_text_presence": grp_results,
        "note": ("Coverage caveat: 17% of rows have no `description`; NLP features "
                 "are null-imputed there and the model falls back to structured "
                 "signal. The honest comparison is the per-group breakdown above."),
    }
    C.METRICS_JSON.write_text(json.dumps(metrics, indent=2, default=str),
                              encoding="utf-8")
    print(f"\n  -> updated {C.METRICS_JSON}")


if __name__ == "__main__":
    main()
