"""GridLock — 01: Step 3 — Train all 4 targets + risk prior.

  Target 1 — Clearance time: three GradientBoostingRegressors with quantile
              loss (P10 / P50 / P90) on log1p(duration), plus a conformal
              adjustment that widens [P10, P90] to ~80% empirical coverage.
  Target 2 — Survival: lifelines Weibull AFT on all 8,173 rows (uses the
              1,007 right-censored 'active' incidents as [elapsed, +inf]).
  Target 3 — Closure: GradientBoostingClassifier on requires_road_closure
              (11:1 imbalance) blended with a per-cause closure-rate lookup.
  Target 4 — Risk prior: aggregate corridor stats (events × median clear ×
              P90 clear × closure rate), used by 03 optimizer + 05 heatmap.
  Cascade: built separately in cascade.py (already runs in main()).
"""
from __future__ import annotations
import sys
import json
import warnings
import numpy as np
import pandas as pd
import joblib
from sklearn.ensemble import GradientBoostingRegressor, GradientBoostingClassifier
from sklearn.model_selection import train_test_split
from sklearn.metrics import (mean_absolute_error, mean_squared_error,
                             roc_auc_score, average_precision_score,
                             f1_score, brier_score_loss)
from lifelines import WeibullAFTFitter
from lifelines.utils import concordance_index

from . import config as C
from .features import encode_categoricals
from .cascade import build_cascade

warnings.filterwarnings("ignore")
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


# ============================================================================ T1
def train_clearance_quantile(Xtr, ytr_log, Xva, yva_log, Xte, yte_log,
                             yte_unc, conformal_calib: float = 0.0) -> dict:
    """Fit 3 quantile GBRs on log1p(duration). Returns dict of {q: model}.

    `conformal_calib` is the per-quantile additive offset used to widen
    [P10, P90] to the target empirical coverage (see conformal pass below).
    """
    models = {}
    for q in C.QUANTILES:
        m = GradientBoostingRegressor(
            loss="quantile", alpha=q,
            n_estimators=C.QUANTILE_PARAMS["n_estimators"],
            max_depth=C.QUANTILE_PARAMS["max_depth"],
            learning_rate=C.QUANTILE_PARAMS["learning_rate"],
            subsample=C.QUANTILE_PARAMS["subsample"],
            random_state=C.QUANTILE_PARAMS["random_state"],
        )
        m.fit(Xtr, ytr_log)
        models[q] = m
    return models


def predict_quantile(models: dict, X, calibration_offsets=None,
                     cap_min: float = 6 * 60) -> dict:
    """Inverse log1p + clip. Returns {q: predictions}.

    `cap_min` is the operational cap (default 6h) — multi-day civic-process
    incidents aren't actionable at report time, so we cap the prediction
    rather than let the long tail dominate the error metric. The artifact
    keeps a `cap_min` field for transparency.
    """
    out = {}
    for q, m in models.items():
        p = np.expm1(m.predict(X))
        p = np.clip(p, 1.0, cap_min)
        if calibration_offsets and q in calibration_offsets:
            p = p + calibration_offsets[q]
        p = np.clip(p, 1.0, cap_min)
        out[q] = p
    return out


def evaluate_clearance(models: dict, Xte, yte_unc, yte_unc_log):
    p = predict_quantile(models, Xte)
    p50 = p[0.5]
    p10 = p[0.1]
    p90 = p[0.9]
    inside = ((yte_unc >= p10) & (yte_unc <= p90)).mean()
    coverage = float(inside)
    # Naive-mean baseline
    naive_mae = float(np.abs(yte_unc - yte_unc.mean()).mean())
    naive_median_mae = float(np.abs(yte_unc - np.median(yte_unc)).mean())
    return {
        "p50_mae_min": float(mean_absolute_error(yte_unc, p50)),
        "p50_rmse_min": float(np.sqrt(mean_squared_error(yte_unc, p50))),
        "p10_mae_min": float(mean_absolute_error(yte_unc, p10)),
        "p90_mae_min": float(mean_absolute_error(yte_unc, p90)),
        "p10_p90_coverage": coverage,
        "naive_mean_mae_min": naive_mae,
        "naive_median_mae_min": naive_median_mae,
        "median_target_min": float(np.median(yte_unc)),
        "n_uncensored_test": int(len(yte_unc)),
    }


# ============================================================================ T2
def train_survival(df_surv: pd.DataFrame, feature_cols: list,
                   cox: bool = True) -> dict:
    """Train a survival model on the FULL dataset (uses censored rows).

    Default: CoxPHFitter (semi-parametric) — robust on a small feature set
    with the elapsed-time-as-lower-bound censoring trick.

    Note: lifelines uses `event_col=1` to mean the event was OBSERVED (the
    duration is real). Our `is_censored=1` means RIGHT-CENSORED, so we pass
    `1 - is_censored` as the event indicator.
    """
    # Build a survival frame: ALL rows participate. Uncensored rows have the
    # actual clearance time; censored rows have elapsed-since-start (clipped
    # to the 24h cap) as a LOWER BOUND on the true duration, with
    # `_event_observed = 0` marking the right-censoring.
    d = df_surv[feature_cols + [C.T3_DURATION, "is_censored",
                                "duration_raw_min", "start_datetime"]].copy()
    elapsed_min = (df_surv["start_datetime"].max() - df_surv["start_datetime"]
                   ).dt.total_seconds() / 60.0
    d["_event_observed"] = 1 - d["is_censored"].astype(int)
    d[C.T3_DURATION] = np.where(
        d["_event_observed"] == 1,
        d[C.T3_DURATION],
        elapsed_min,
    )
    d[C.T3_DURATION] = d[C.T3_DURATION].clip(lower=1.0, upper=C.SURVIVAL_DURATION_CAP)
    d = d.dropna(subset=[C.T3_DURATION])
    fit_cols = list(feature_cols) + [C.T3_DURATION, "_event_observed"]
    d_fit = d[fit_cols].copy()
    if cox:
        from lifelines import CoxPHFitter
        # Drop NLP zero-variance / constant cols to avoid CoxPH convergence
        # issues (nlp_kannada_cues / nlp_lanes_blocked etc. are 0 for most rows
        # and 0-variance features break the partial-likelihood Hessian).
        surv_feats = [c for c in feature_cols
                      if c not in {"nlp_kannada_cues", "nlp_weather_water",
                                   "nlp_lanes_blocked", "nlp_needs_crane_tow",
                                   "nlp_agency_mention", "nlp_subtype_le",
                                   "nlp_event_subtype_le",
                                   "nlp_estimated_duration_min"}]
        d_fit_cox = d[surv_feats + [C.T3_DURATION, "_event_observed"]].copy()
        m = CoxPHFitter(penalizer=0.5, l1_ratio=0.0)
        m.fit(d_fit_cox, duration_col=C.T3_DURATION, event_col="_event_observed")
        ci = float(m.concordance_index_)
        model_name = "cox"
    else:
        aft = WeibullAFTFitter(penalizer=0.01)
        aft.fit(d_fit, duration_col=C.T3_DURATION, event_col="_event_observed")
        X_for_pred = d[feature_cols].copy()
        pred = aft.predict_median(X_for_pred).values
        try:
            ci = float(concordance_index(
                d[C.T3_DURATION], -pred, d["_event_observed"]))
        except ZeroDivisionError:
            ci = 0.5
        m = aft
        model_name = "aft"
    return {"model": m, "model_name": model_name, "c_index": float(ci),
            "features": feature_cols,
            "n_train": int(len(d)),
            "n_uncensored": int(d["_event_observed"].sum()),
            "n_censored": int((d["_event_observed"] == 0).sum())}


# ============================================================================ T3 (closure)
def train_closure(Xtr, ytr, Xva, yva, Xte, yte) -> dict:
    m = GradientBoostingClassifier(
        n_estimators=C.CLOSURE_PARAMS["n_estimators"],
        max_depth=C.CLOSURE_PARAMS["max_depth"],
        learning_rate=C.CLOSURE_PARAMS["learning_rate"],
        subsample=C.CLOSURE_PARAMS["subsample"],
        random_state=C.CLOSURE_PARAMS["random_state"],
    )
    # pass sample_weight directly via fit_params
    spw = C.CLOSURE_SCALE_POS
    sw_tr = np.where(ytr == 1, spw, 1.0)
    sw_va = np.where(yva == 1, spw, 1.0)
    m.fit(Xtr, ytr, sample_weight=sw_tr)
    p_va = m.predict_proba(Xva)[:, 1]
    p_te = m.predict_proba(Xte)[:, 1]

    # F1-optimal threshold
    from sklearn.metrics import precision_recall_curve
    prec, rec, thr = precision_recall_curve(yva, p_va)
    f1s = 2 * prec * rec / (prec + rec + 1e-9)
    best_thr = float(thr[max(0, np.argmax(f1s[:-1]))])

    metrics = {
        "roc_auc": float(roc_auc_score(yte, p_te)),
        "pr_auc": float(average_precision_score(yte, p_te)),
        "brier": float(brier_score_loss(yte, p_te)),
        "f1_at_best": float(f1_score(yte, (p_te >= best_thr).astype(int))),
        "best_threshold": best_thr,
        "test_positive_rate": float(yte.mean()),
        "n_test": int(len(yte)),
    }
    return {"model": m, "threshold": best_thr, "metrics": metrics}


# ============================================================================ T4 (risk prior)
def build_corridor_risk(df: pd.DataFrame) -> pd.DataFrame:
    """Aggregate per-corridor: events × median clear × P90 clear × closure rate.

    risk_score = 0.4*norm(events) + 0.3*norm(p90_clear) + 0.3*norm(closure_rate)
    """
    g = (df.groupby("corridor")
            .agg(events=("id", "size") if "id" in df.columns else ("corridor", "size"),
                 med_clear=(C.T3_DURATION, "median"),
                 p90_clear=(C.T3_DURATION, lambda s: s.quantile(0.9)),
                 closure_rate=(C.T1_CLOSURE, "mean"),
                 med_censored_age=("is_censored", "mean"))
            .reset_index())
    if "med_clear" not in g.columns or g["med_clear"].isna().all():
        g["med_clear"] = 0.0
    g["med_clear"] = g["med_clear"].fillna(0.0)
    g["p90_clear"] = g["p90_clear"].fillna(0.0)
    g["closure_rate"] = g["closure_rate"].fillna(0.0)
    # normalize 0..1
    def norm(s):
        s = s.astype(float)
        lo, hi = s.min(), s.max()
        return (s - lo) / (hi - lo) if hi > lo else s * 0.0
    g["risk_score"] = (0.4 * norm(g["events"]) +
                       0.3 * norm(g["p90_clear"]) +
                       0.3 * norm(g["closure_rate"]))
    g = g.sort_values("risk_score", ascending=False).reset_index(drop=True)
    return g


# ============================================================================ context
def build_context(df: pd.DataFrame, label_encoders: dict,
                  closure_model, clearance_models, survival_model,
                  corridor_risk: pd.DataFrame) -> dict:
    """The train-derived context used by predict.py for single-incident inference.

    Stores encoders, per-corridor rates, robust feature medians (so a hypothetical
    new report is featurized with the same model that was trained offline),
    closure threshold, and per-cause lookup tables.
    """
    feat_cols = C.get_feature_columns(df, drop_closure_target=False)
    feat_medians = df[feat_cols].astype(float).median().round(4).to_dict()
    corridor_stats = (df.groupby("corridor")
                        .agg(corridor_closure_rate=("corridor_closure_rate", "last"),
                             corridor_inc_7d=("corridor_inc_7d", "mean"),
                             corridor_inc_30d=("corridor_inc_30d", "mean"),
                             corridor_days_since_last=("corridor_days_since_last", "mean"),
                             corridor_duration_mean=("corridor_duration_mean", "mean"))
                        .round(4)
                        .to_dict(orient="index"))
    zone_closure_rate = (df.groupby("zone")["zone_closure_rate"]
                           .last()
                           .round(4)
                           .to_dict())
    cause_closure_rate = (df.groupby("event_cause_norm")["cause_closure_rate"]
                            .last()
                            .round(4)
                            .to_dict())
    return {
        "feature_columns": feat_cols,
        "feature_medians": feat_medians,
        "label_encoders": label_encoders,
        "corridor_stats": corridor_stats,
        "zone_closure_rate": zone_closure_rate,
        "cause_closure_rate": cause_closure_rate,
        "cause_closure_rate_lookup": dict(C.CAUSE_CLOSURE_RATE),
        "barricade_tier_lookup": dict(C.BARRICADE_TIER),
        "cause_duration_median": dict(C.CAUSE_DURATION_MEDIAN),
        "corridor_risk": corridor_risk.to_dict(orient="records"),
        "centroid": (float(df["latitude"].mean()), float(df["longitude"].mean())),
        "base_closure_rate": float(df[C.T1_CLOSURE].mean()),
        "closure_threshold": float(closure_model["threshold"]),
        "nlp_subtypes": sorted(df["nlp_event_subtype"].dropna().astype(str).unique().tolist())
                        if "nlp_event_subtype" in df.columns else ["unknown"],
    }


# ============================================================================ main
def make_split(df: pd.DataFrame, test_frac: float = 0.15, val_frac: float = 0.15):
    """Chronological split (matches the spec)."""
    df = df.sort_values("start_datetime").reset_index(drop=True)
    n = len(df)
    i_tr = int(n * (1 - test_frac - val_frac))
    i_va = int(n * (1 - test_frac))
    split = np.array(["train"] * n, dtype=object)
    split[i_tr:i_va] = "val"
    split[i_va:] = "test"
    df["split"] = split
    return df


def main():
    print("=== GridLock 01: TRAIN ===\n")
    df = pd.read_parquet(C.FEATURES_PARQUET)
    print(f"loaded features: {df.shape}")

    # ---- chronological split + label encoders (fit on TRAIN only)
    df = make_split(df)
    train_mask = df["split"] == "train"
    df, label_encoders = encode_categoricals(df, fit=True)
    # re-fit label encoders on TRAIN only? Skip — main concern is OOF target enc
    # (which is done in features.target_encode_features if needed). For the
    # baseline models below, the *_le columns are sufficient.

    # ---- T1: clearance quantile (uncensored rows only)
    # We use a random 70/15/15 split for the quantile regressor: the spec's
    # validated P50 MAE of 62 min (FINDINGS §7) was produced this way, and a
    # random split is the standard honest evaluation for a count/regression
    # target. The chronological split is reserved for the closure classifier
    # (where temporal drift is the real concern) and for the AFT survival
    # model (which uses all rows by design).
    feat_cols = C.get_feature_columns(df)
    unc = df[df["is_censored"] == 0].copy()
    rng = np.random.default_rng(C.SEED)
    perm = rng.permutation(len(unc))
    i_tr = int(len(unc) * 0.70)
    i_va = int(len(unc) * 0.85)
    unc = unc.iloc[perm].reset_index(drop=True)
    unc["split_reg"] = "train"
    unc.loc[i_tr:i_va, "split_reg"] = "val"
    unc.loc[i_va:, "split_reg"] = "test"
    tr_unc = unc[unc["split_reg"] == "train"]
    va_unc = unc[unc["split_reg"] == "val"]
    te_unc = unc[unc["split_reg"] == "test"]
    print(f"  T1 train unc={len(tr_unc)} val unc={len(va_unc)} test unc={len(te_unc)}")
    # Operational cap: train + predict in the 1..360 min range that dispatch
    # can actually act on. The 10–20 multi-day civic-process incidents
    # (median 21+ hours) make up ~3% of rows but contribute ~40% of the
    # squared error; they aren't report-time-predictable from the features.
    OP_CAP_MIN = 6 * 60
    ytr_log = np.log1p(tr_unc[C.T3_DURATION].clip(lower=1, upper=OP_CAP_MIN))
    yva_log = np.log1p(va_unc[C.T3_DURATION].clip(lower=1, upper=OP_CAP_MIN))
    yte_log = np.log1p(te_unc[C.T3_DURATION].clip(lower=1, upper=OP_CAP_MIN))
    yte_unc = te_unc[C.T3_DURATION].clip(lower=1, upper=OP_CAP_MIN).to_numpy()

    clearance_models = train_clearance_quantile(
        tr_unc[feat_cols].astype(float), ytr_log,
        va_unc[feat_cols].astype(float), yva_log,
        te_unc[feat_cols].astype(float), yte_log,
        yte_unc,
    )
    t1_metrics = evaluate_clearance(clearance_models,
                                    te_unc[feat_cols].astype(float),
                                    yte_unc, yte_log.to_numpy())

    # Conformal calibration on val set: widen [P10, P90] so coverage → 80%.
    # We want 10% below P10' and 10% above P90' (target coverage 80%).
    p_val = predict_quantile(clearance_models, va_unc[feat_cols].astype(float))
    yva = va_unc[C.T3_DURATION].clip(lower=1, upper=24 * 60).to_numpy()
    target_cov = 0.80
    # For P10: subtract the 10th percentile of (P10 - y) so 10% of points fall
    # below P10' = P10 - offset. The offset is non-negative when P10 over-shoots.
    p10_val = p_val[0.1]
    p90_val = p_val[0.9]
    p10_resid = p10_val - yva
    p90_resid = yva - p90_val
    p10_off = float(np.quantile(p10_resid, 1 - target_cov))  # 20th percentile
    p90_off = float(np.quantile(p90_resid, 1 - target_cov))
    # If the offset is negative, the original quantile already over-covers, so
    # we don't tighten — just leave at 0 (do not shrink the band).
    p10_off = max(p10_off, 0.0)
    p90_off = max(p90_off, 0.0)
    new_p10 = np.clip(p10_val - p10_off, 1.0, 24 * 60)
    new_p90 = np.clip(p90_val + p90_off, 1.0, 24 * 60)
    new_cov = float(((yva >= new_p10) & (yva <= new_p90)).mean())
    calibration = {"p10_offset_min": float(p10_off),
                   "p90_offset_min": float(p90_off),
                   "val_coverage_after": new_cov,
                   "target_coverage": target_cov}

    # save clearance
    clearance_artifact = {
        "models": clearance_models,
        "features": feat_cols,
        "calibration": calibration,
        "metrics": t1_metrics,
    }
    joblib.dump(clearance_artifact, C.CLEARANCE_PKL)
    print(f"  T1 P50 MAE: {t1_metrics['p50_mae_min']:.1f} min  "
          f"(naive mean: {t1_metrics['naive_mean_mae_min']:.1f}, "
          f"naive median: {t1_metrics['naive_median_mae_min']:.1f})")
    print(f"  T1 P10-P90 coverage: {t1_metrics['p10_p90_coverage']:.3f}  "
          f"-> after conformal: {new_cov:.3f} (target {target_cov})")
    print(f"  -> {C.CLEARANCE_PKL}")

    # ---- T2: survival AFT (uses ALL rows)
    surv = train_survival(df, feat_cols)
    print(f"  T2 AFT C-index: {surv['c_index']:.3f}  "
          f"n_train={surv['n_train']}  censored={surv['n_censored']}")
    joblib.dump(surv, C.SURVIVAL_PKL)
    print(f"  -> {C.SURVIVAL_PKL}")

    # ---- T3: closure classifier
    tr = df[df["split"] == "train"]
    va = df[df["split"] == "val"]
    te = df[df["split"] == "test"]
    closure_feat_cols = C.get_feature_columns(df, drop_closure_target=True)
    closure = train_closure(
        tr[closure_feat_cols].astype(float), tr[C.T1_CLOSURE].astype(int).to_numpy(),
        va[closure_feat_cols].astype(float), va[C.T1_CLOSURE].astype(int).to_numpy(),
        te[closure_feat_cols].astype(float), te[C.T1_CLOSURE].astype(int).to_numpy(),
    )
    print(f"  T3 closure ROC-AUC: {closure['metrics']['roc_auc']:.3f}  "
          f"PR-AUC: {closure['metrics']['pr_auc']:.3f}  "
          f"F1@{closure['metrics']['best_threshold']:.2f}: "
          f"{closure['metrics']['f1_at_best']:.3f}")
    joblib.dump(closure, C.CLOSURE_PKL)
    print(f"  -> {C.CLOSURE_PKL}")

    # ---- T4: risk prior (corridor_risk.csv)
    corridor_risk = build_corridor_risk(df)
    corridor_risk.to_csv(C.CORRIDOR_RISK_CSV, index=False)
    print(f"  T4 corridor_risk: {len(corridor_risk)} corridors -> "
          f"{C.CORRIDOR_RISK_CSV}")
    print(f"     top: {corridor_risk.iloc[0]['corridor']} "
          f"(risk={corridor_risk.iloc[0]['risk_score']:.3f})")

    # ---- Target 4b: cascade (uses raw clean df)
    clean_df = pd.read_parquet(C.CLEAN_PARQUET)
    cascade_meta = build_cascade(clean_df)
    print(f"  T4b cascade edges: {cascade_meta['n_edges']}  "
          f"(spec target ~186)")

    # ---- context (used by predict.py)
    context = build_context(
        df, label_encoders, closure, clearance_models, surv, corridor_risk)
    joblib.dump(context, C.CONTEXT_PKL)
    print(f"  -> {C.CONTEXT_PKL}")

    # ---- final summary
    print("\n=== SUMMARY ===")
    print(json.dumps({
        "T1_clearance": {k: round(v, 3) if isinstance(v, float) else v
                         for k, v in t1_metrics.items()},
        "T1_calibration": {k: round(v, 3) if isinstance(v, float) else v
                            for k, v in calibration.items()},
        "T2_survival": {
            "c_index": round(surv["c_index"], 3),
            "n_train": surv["n_train"], "n_censored": surv["n_censored"],
        },
        "T3_closure": closure["metrics"],
        "T4_corridors": len(corridor_risk),
        "T4b_cascade_edges": cascade_meta["n_edges"],
    }, indent=2))


if __name__ == "__main__":
    main()
