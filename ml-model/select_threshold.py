#!/usr/bin/env python3
"""
Choose the serving decision threshold on an INDEPENDENT validation fleet.

The threshold is a model setting, so it must be chosen on data that is
neither the training set (seed 42) nor the test set used to report results
(seed 123) -- choosing it by looking at test results would inflate the
reported score. The validation fleet comes from the same generator with its
own seed:

    python ml-model/generate_telecom_fleet.py --machines 200 --days 14 \
        --seed 7 --output data/telecom_fleet_v2_val.csv

Method: the production models' outputs are computed once on the validation
fleet; the served decision (classifier vote OR IsolationForest vote,
ml-model/decision.py) is applied at every threshold of the grid, and the
threshold maximising F-beta is selected. beta = 2 by default: recall counts
twice as much as precision, because in network operations a missed incident
costs more than an unnecessary check.

With --record, the choice and its evidence (validation-set fingerprint, beta,
the full precision/recall/F curve) are written to models/manifest.json under
production.threshold_selection, and production.decision_threshold is set.
The test-set evaluation must then be recorded at that threshold:

    python ml-model/train_production.py --evaluate-production

(train_production.py uses production.decision_threshold by default), and the
deployment files must carry the same value -- scripts/verify_model_manifest.py
fails the CI build if they do not.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np
from sklearn.metrics import fbeta_score, f1_score, precision_score, recall_score

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "ml-model"))
import decision  # noqa: E402
import train_production as tp  # noqa: E402

GRID = [round(t, 2) for t in np.arange(0.05, 0.951, 0.05)]


def served_curve(models_dir: Path, fleet, grid, beta: float) -> list[dict]:
    """Served-decision metrics at each threshold, from one pass of the models."""
    b = json.load(open(models_dir / tp.ARTIFACTS["baselines"]))
    t, z, z15 = tp.fleet_features(fleet, b)
    y = t["label"].to_numpy()
    clf = joblib.load(models_dir / tp.ARTIFACTS["v4_model"])
    enc = joblib.load(models_dir / tp.ARTIFACTS["v4_encoder"])
    iso = joblib.load(models_dir / tp.ARTIFACTS["iso_model"])
    proba = clf.predict_proba(z15)
    iso_hit = iso.predict(z) == -1
    classes = list(enc.classes_)
    curve = []
    for thr in grid:
        hit, cause = decision.classifier_vote_batch(proba, classes, thr)
        pred, _ = decision.final_verdict_batch(hit, cause, iso_hit)
        curve.append({
            "threshold": thr,
            "precision": round(float(precision_score(y, pred, zero_division=0)), 4),
            "recall": round(float(recall_score(y, pred, zero_division=0)), 4),
            "f1": round(float(f1_score(y, pred, zero_division=0)), 4),
            f"f{beta:g}": round(float(fbeta_score(y, pred, beta=beta, zero_division=0)), 4),
        })
    return curve


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--val", type=Path, default=ROOT / "data/telecom_fleet_v2_val.csv")
    ap.add_argument("--models-dir", type=Path, default=tp.MODELS)
    ap.add_argument("--beta", type=float, default=2.0)
    ap.add_argument("--record", action="store_true", help="write the choice into the manifest")
    a = ap.parse_args(argv)

    if not a.val.exists():
        tp.log(f"FATAL: missing validation fleet {a.val} (see the generation command in --help)")
        return 2
    fp = tp.fingerprint({"val": a.val})["val"]
    fleet = tp.load_fleet(a.val)
    fp["rows"] = int(len(fleet))
    tp.log(f"validation fleet {fp['file']}: {fp['rows']:,} rows, sha256 {fp['sha256'][:12]}")

    key = f"f{a.beta:g}"
    curve = served_curve(a.models_dir, fleet, GRID, a.beta)
    best = max(curve, key=lambda r: (r[key], r["recall"]))
    print(f"\n{'threshold':>9} {'precision':>9} {'recall':>7} {'F1':>7} {key.upper():>7}")
    for r in curve:
        mark = "  <- selected" if r is best else ""
        print(f"{r['threshold']:>9.2f} {r['precision']:>9.4f} {r['recall']:>7.4f} "
              f"{r['f1']:>7.4f} {r[key]:>7.4f}{mark}")
    tp.log(f"selected threshold {best['threshold']} (max {key.upper()} = {best[key]}) on the validation fleet")

    if a.record:
        manifest = tp.read_manifest(a.models_dir)
        prod = manifest.setdefault("production", {})
        prod["decision_threshold"] = best["threshold"]
        prod["threshold_selection"] = {
            "selected_at": datetime.now(timezone.utc).isoformat(),
            "method": f"maximise F{a.beta:g} of the served decision (classifier OR "
                      f"IsolationForest) on an independent validation fleet",
            "validation_set": fp,
            "beta": a.beta,
            "selected": best,
            "curve": curve,
        }
        prod.pop("evaluation", None)   # recorded at the old threshold: must be redone
        tp.write_manifest(a.models_dir, manifest)
        tp.log("manifest updated: decision_threshold set, previous test evaluation cleared "
               "-> run: python ml-model/train_production.py --evaluate-production")
    return 0


if __name__ == "__main__":
    sys.exit(main())
