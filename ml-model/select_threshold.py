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
threshold chosen by the rules in config/model_policy.json (F-beta with a
beta that weighs recall above precision, because in network operations a
missed incident costs more than an unnecessary check; optionally the highest
precision that still reaches a recall target).

With --record, the choice and its evidence (validation-set fingerprint, beta,
the full precision/recall/F curve, and the SHA-256 of the artifacts it was
computed for) are written to models/manifest.json under
production.threshold_selection, and production.decision_threshold is set.

Ownership: train_production.py --promote is the only path that creates a new production
model and its threshold. This tool only re-selects the threshold for the model files in models/ (the record
stores the SHA-256 of the files on disk; it does not check that they are the promoted
ones, so approval stays a matter of process) and clears the recorded evaluation, so the CI gate
fails until --evaluate-production has redone it. Enforced: --record refuses any --models-dir
other than models/ (a read-only run on another directory still prints the curve).
The test-set evaluation must then be recorded at that threshold:

    python ml-model/train_production.py --evaluate-production

(train_production.py uses production.decision_threshold by default). The serving
API reads the same production.decision_threshold from the manifest, so no
deployment file carries a copy -- scripts/verify_model_manifest.py fails the CI
build if one reappears.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "ml-model"))
import train_production as tp  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--val", type=Path, default=ROOT / "data/telecom_fleet_v2_val.csv")
    ap.add_argument("--models-dir", type=Path, default=tp.MODELS)
    ap.add_argument("--beta", type=float, default=None,
                    help="F-beta to maximise; default: threshold.beta from config/model_policy.json")
    ap.add_argument("--min-recall", type=float, default=None,
                    help="highest precision whose recall is at least this; default: "
                         "threshold.min_recall from config/model_policy.json")
    ap.add_argument("--train", type=Path, default=tp.ROOT / "data/telecom_fleet_v2_labeled.csv",
                    help="training fleet; --val must not be this file")
    ap.add_argument("--test", type=Path, default=tp.ROOT / "data/telecom_fleet_v2_test.csv",
                    help="test fleet; --val must not be this file")
    ap.add_argument("--record", action="store_true", help="write the choice into the manifest")
    a = ap.parse_args(argv)

    if a.record and a.models_dir.resolve() != tp.MODELS.resolve():
        tp.log("FATAL: --record writes threshold evidence, so it is only allowed for the production "
               f"artifacts ({tp.MODELS}); a new model gets its threshold from train_production.py --promote")
        return 2

    if not a.val.exists():
        tp.log(f"FATAL: missing validation fleet {a.val} (see the generation command in --help)")
        return 2
    fp, problem = tp.validation_fingerprint(a.val, tp.fingerprint({"train": a.train, "test": a.test}))
    if problem:
        tp.log(f"FATAL: {problem}")
        return 2
    fleet = tp.load_fleet(a.val)
    fp["rows"] = int(len(fleet))
    tp.log(f"validation fleet {fp['file']}: {fp['rows']:,} rows, sha256 {fp['sha256'][:12]}")

    selection = tp.select_candidate_threshold(a.models_dir, fleet, fp, a.beta, a.min_recall)
    key = f"f{selection['beta']:g}"
    curve, best = selection["curve"], selection["selected"]
    print(f"\n{'threshold':>9} {'precision':>9} {'recall':>7} {'F1':>7} {key.upper():>7}")
    for r in curve:
        mark = "  <- selected" if r is best else ""
        print(f"{r['threshold']:>9.2f} {r['precision']:>9.4f} {r['recall']:>7.4f} "
              f"{r['f1']:>7.4f} {r[key]:>7.4f}{mark}")
    tp.log(f"selected threshold {best['threshold']} by rule '{selection['rule']}' "
           f"(recall {best['recall']}, precision {best['precision']}, {key.upper()} {best[key]}) "
           f"on the validation fleet")

    if a.record:
        manifest = tp.read_manifest(a.models_dir)
        prod = manifest.setdefault("production", {})
        prod["decision_threshold"] = best["threshold"]
        prod["threshold_selection"] = selection
        prod.pop("evaluation", None)   # recorded at the old threshold: must be redone
        tp.write_manifest(a.models_dir, manifest)
        tp.log("manifest updated: decision_threshold set, previous test evaluation cleared "
               "-> run: python ml-model/train_production.py --evaluate-production")
    return 0


if __name__ == "__main__":
    sys.exit(main())
