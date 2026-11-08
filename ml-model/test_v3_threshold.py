#!/usr/bin/env python3
"""
Would the v3 path pass the guardrail with its OWN threshold?

Why: the model job selects ONE threshold on the validation fleet, for the v4 served decision
(served_curve in train_production.py), and the guardrail and the API apply it to v3 too. The
per-hour candidate of 8 November 2026 (threshold 0.66) was rejected on two v3 causes only
(silent_failure 0.9205 -> 0.8827, cascade 0.3103 -> 0.2700 on validation). This script scores
a candidate's v3 path (v3 classifier OR IsolationForest) at every threshold of the policy grid
on the validation fleet, with the pipeline's own scoring code (fleet_features, _accumulate,
_finalize), and applies the guardrail rules (config/model_policy.json) against production at
its own threshold:
  - which v3 thresholds would pass every v3 rule, with v4 kept at the candidate's threshold;
  - which threshold the policy's own rule (highest precision with recall >= min_recall) picks
    for the v3 served decision.
Nothing is trained or promoted; production files, the manifest and MLflow are not touched.

Usage:
    python3 ml-model/test_v3_threshold.py --candidate DIR --cand-threshold 0.66 --val VAL.csv --out OUT.json
"""
import argparse
import json
import sys
import time
from pathlib import Path

import joblib
import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import model_policy  # noqa: E402
import train_production as tp  # noqa: E402


def fbeta(p: float, r: float, beta: float) -> float:
    b2 = beta ** 2
    return round((1 + b2) * p * r / (b2 * p + r), 4) if (b2 * p + r) else 0.0


def score(art_dir: Path, val, v3_thresholds: list[float], v4_threshold: float) -> dict:
    """One pass over the fleet: v3 at each threshold in v3_thresholds, v4 at v4_threshold."""
    b = json.load(open(art_dir / tp.ARTIFACTS["baselines"]))
    m = {k: joblib.load(art_dir / v) for k, v in tp.ARTIFACTS.items() if v.endswith(".pkl")}
    v3c, v4c = list(m["v3_encoder"].classes_), list(m["v4_encoder"].classes_)
    acc3 = {t: tp._new_counts() for t in v3_thresholds}
    acc4 = tp._new_counts()
    rows = val.groupby("machine", sort=True).indices
    machines = list(rows)
    for i in range(0, len(machines), tp.EVAL_MACHINES_PER_CHUNK):
        idx = np.concatenate([rows[k] for k in machines[i:i + tp.EVAL_MACHINES_PER_CHUNK]])
        t, z, z15 = tp.fleet_features(val.iloc[idx].copy(), b)
        yb, yt = t["label"].to_numpy(), t["anomaly_type"].fillna("normal").to_numpy()
        iso_hit = m["iso_model"].predict(z) == -1
        p3 = m["v3_model"].predict_proba(z)
        for th in v3_thresholds:
            tp._accumulate(acc3[th], p3, v3c, th, iso_hit, yb, yt)
        tp._accumulate(acc4, m["v4_model"].predict_proba(z15), v4c, v4_threshold, iso_hit, yb, yt)
        tp.log(f"  {art_dir.name}: machines {i + 1}-{min(i + tp.EVAL_MACHINES_PER_CHUNK, len(machines))} of {len(machines)}")
    return {"v3": {th: tp._finalize(c) for th, c in acc3.items()}, "v4": tp._finalize(acc4)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--candidate", type=Path, required=True, help="directory with the candidate's 6 files")
    ap.add_argument("--cand-threshold", type=float, required=True, help="the candidate's selected threshold")
    ap.add_argument("--production", type=Path, default=tp.MODELS)
    ap.add_argument("--prod-threshold", type=float, default=None,
                    help="default: production.decision_threshold in the manifest")
    ap.add_argument("--val", type=Path, default=tp.DEFAULT_VAL)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args(argv)

    policy = model_policy.load_policy()
    grid = model_policy.threshold_grid(policy)
    g, beta, min_recall = policy["guardrail"], policy["threshold"]["beta"], policy["threshold"]["min_recall"]
    prod_th = args.prod_threshold
    if prod_th is None:
        prod_th = (tp.read_manifest(args.production).get("production") or {}).get("decision_threshold")
    t0 = time.time()
    val = tp.load_fleet(args.val)
    tp.log(f"validation fleet {args.val} ({len(val):,} rows, sha256 {tp.sha256(args.val)[:12]})")
    prod = score(args.production, val, [prod_th], prod_th)
    cand = score(args.candidate, val, grid, args.cand_threshold)
    p3, p4 = prod["v3"][prod_th], prod["v4"]

    _, v4_reasons = tp.guardrail({"v4": cand["v4"], "v3": p3}, {"v4": p4, "v3": p3},
                                 g["max_f1_drop"], g["max_recall_drop"])
    rows = []
    for th in grid:
        c3 = cand["v3"][th]
        ok, reasons = tp.guardrail({"v4": p4, "v3": c3}, {"v4": p4, "v3": p3},
                                   g["max_f1_drop"], g["max_recall_drop"])
        rows.append({"threshold": th, "passes_v3_rules": ok, "reasons": reasons,
                     "f1": c3["f1"], "precision": c3["precision"], "recall": c3["recall"],
                     f"f{beta:g}": fbeta(c3["precision"], c3["recall"], beta),
                     "per_cause_recall": c3["per_cause_recall"]})
    curve = [{"threshold": r["threshold"], "precision": r["precision"], "recall": r["recall"],
              "f1": r["f1"], f"f{beta:g}": r[f"f{beta:g}"]} for r in rows]
    own, rule = model_policy.choose_threshold(curve, beta, min_recall)
    passing = [r["threshold"] for r in rows if r["passes_v3_rules"]]
    record = {
        "script": "ml-model/test_v3_threshold.py", "git_commit": tp.git_commit(),
        "validation": {"file": str(args.val), "sha256": tp.sha256(args.val), "rows": len(val)},
        "production": {"dir": str(args.production), "threshold": prod_th, "v3": p3, "v4": p4},
        "candidate": {"dir": str(args.candidate), "threshold": args.cand_threshold, "v4": cand["v4"],
                      "v4_guardrail_reasons": v4_reasons},
        "v3_own_threshold_by_policy_rule": {"rule": rule, "selected": own},
        "v3_thresholds_passing_every_v3_rule": passing,
        "v3_curve": rows,
        "seconds": round(time.time() - t0),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(record, indent=2) + "\n")

    print(f"\nproduction (threshold {prod_th}): v3 F1 {p3['f1']}  per cause {p3['per_cause_recall']}")
    print(f"candidate v4 at {args.cand_threshold}: F1 {cand['v4']['f1']}; v4 guardrail reasons: {v4_reasons or 'none'}")
    print("\ncandidate v3   thr   F1      P       R       silent_failure  cascade  passes")
    for r in rows:
        pc = r["per_cause_recall"]
        print(f"               {r['threshold']:<5} {r['f1']:<7} {r['precision']:<7} {r['recall']:<7} "
              f"{pc.get('silent_failure', float('nan')):<15} {pc.get('cascade', float('nan')):<8} "
              f"{'yes' if r['passes_v3_rules'] else 'no: ' + '; '.join(r['reasons'])}")
    print(f"\nv3 thresholds passing every v3 rule: {passing or 'none'}")
    print(f"v3's own threshold by the policy rule ({rule}): {own}")
    tp.log(f"written {args.out}")
    return 0


if __name__ == "__main__":
    np.seterr(all="ignore")
    sys.exit(main())
