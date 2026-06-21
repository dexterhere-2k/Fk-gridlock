"""GridLock — 06: Master eval orchestrator.

Runs every evaluation step (DoD metrics, ablations, calibration,
before/after ILP, learning signal) and merges them into one
`artifacts/eval_report.json` — the spec 06 §"Evaluation plan" artifact
that the dashboard + demo read.

This is the single entry point judges use to verify the system.
"""
from __future__ import annotations
import json
import sys
from pathlib import Path

from . import config as C

REPORT = C.ARTIFACTS_DIR / "eval_report.json"


def main():
    print("=" * 64)
    print("  GridLock — 06: master eval orchestrator")
    print("=" * 64)
    report = {}

    # ---- 1. DoD metrics (already in artifacts/metrics.json)
    metrics_path = C.METRACTS_DIR if hasattr(C, "METRACTS_DIR") else C.ARTIFACTS_DIR
    metrics_path = C.ARTIFACTS_DIR / "metrics.json"
    if metrics_path.exists():
        report["dod_metrics"] = json.loads(metrics_path.read_text(encoding="utf-8"))
        print(f"  ✓ DoD metrics: {metrics_path.name} loaded")
    else:
        print(f"  ⚠ {metrics_path} not found; run src.evaluate first")

    # ---- 2. Ablations
    abl_path = C.ARTIFACTS_DIR / "eval_report.json"
    # this would re-run ablations; we read the previous run if it exists
    # (since re-running takes ~3 min). For the demo we use the cached
    # version produced by `python -m src.evaluate_ablation`.
    if abl_path.exists():
        # don't overwrite the ablation result with ourselves; we just
        # copy into the master report under a different key
        with open(abl_path, encoding="utf-8") as f:
            existing = json.load(f)
        # existing is the ablation report; merge in if not already present
        if "ablations" not in report:
            report["ablations"] = existing
        print(f"  ✓ ablations: {abl_path.name} loaded")

    # ---- 3. Calibration
    cal_path = C.ARTIFACTS_DIR / "calibration.json"
    if cal_path.exists():
        report["calibration"] = json.loads(cal_path.read_text(encoding="utf-8"))
        print(f"  ✓ calibration: {cal_path.name} loaded")

    # ---- 4. Before/after ILP (from artifacts/demo_allocation.json)
    ilp_path = C.ARTIFACTS_DIR / "demo_allocation.json"
    if ilp_path.exists():
        ilp = json.loads(ilp_path.read_text(encoding="utf-8"))
        report["ilp_before_after"] = {
            "tight_scenario": {
                "naive_score": ilp.get("tight_scenario", {}).get("naive_score"),
                "ilp_score": ilp.get("tight_scenario", {}).get("ilp_score"),
                "improvement_pct": ilp.get("tight_scenario", {}).get("improvement_pct"),
            },
            "realistic_scenario": {
                "naive_score": ilp.get("realistic_scenario", {}).get("naive_score"),
                "ilp_score": ilp.get("realistic_scenario", {}).get("ilp_score"),
                "improvement_pct": ilp.get("realistic_scenario", {}).get("improvement_pct"),
            },
            "plenty_scenario_solve_time_s":
                ilp.get("plenty_scenario", {}).get("solve_time_s"),
        }
        print(f"  ✓ ILP before/after: {ilp_path.name} loaded")

    # ---- 5. Learning signal
    learn_path = C.ARTIFACTS_DIR / "learning_log.json"
    if learn_path.exists():
        report["learning_signal"] = json.loads(learn_path.read_text(encoding="utf-8"))
        print(f"  ✓ learning signal: {learn_path.name} loaded")

    # ---- 6. Definition of Done (master rollup)
    report["definition_of_done"] = _compute_master_dod(report)

    # write
    REPORT.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(f"\n  -> {REPORT}")

    # summary
    print(f"\n=== Master eval summary ===")
    dod = report.get("definition_of_done", {})
    for k, v in dod.items():
        mark = "✓" if v else "✗"
        print(f"  {mark} {k}")


def _compute_master_dod(report: dict) -> dict:
    dod = {}
    m = report.get("dod_metrics", {})
    t1 = m.get("T1_clearance", {})
    t2 = m.get("T2_survival", {})
    t3 = m.get("T3_closure", {})
    t4 = m.get("T4_risk_and_cascade", {})
    dod["T1_p50_mae_under_70"] = t1.get("p50_mae_min", 999) < 70
    dod["T2_survival_cindex_above_05"] = t2.get("c_index", 0) > 0.5
    dod["T3_closure_roc_auc_at_least_075"] = t3.get("roc_auc", 0) >= 0.75
    dod["T4b_cascade_at_least_100"] = t4.get("cascade_n_edges", 0) >= 100
    dod["T1_coverage_above_70pct"] = t1.get("p10_p90_coverage", 0) >= 0.70
    abl = report.get("ablations", {}).get("no_nlp", {})
    dod["nlp_ablation_helps_closure"] = abl.get("delta_roc_auc", 0) < 0
    ilp_ba = report.get("ilp_before_after", {}).get("tight_scenario", {})
    dod["ilp_beats_naive_on_tight"] = (ilp_ba.get("improvement_pct", 0) or 0) > 0
    ilp_p = report.get("ilp_before_after", {}).get("plenty_scenario_solve_time_s", 99)
    dod["ilp_under_1s_on_3_events_40_officers"] = (ilp_p or 99) < 1.0
    cal = report.get("calibration", {})
    dod["corridor_risk_calibrated_to_actual"] = (
        cal.get("risk_vs_actual_pearson", 0) > 0.5)
    dod["all_pass"] = all(v for v in dod.values())
    return dod


if __name__ == "__main__":
    main()
