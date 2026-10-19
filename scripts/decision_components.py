#!/usr/bin/env python3
"""
Read-only: what each part of the served decision contributes.

The served verdict is `classifier OR IsolationForest` (ml-model/decision.py).
This script measures, on the independent test set and at the threshold stored
in models/manifest.json, three decisions made from the SAME model outputs:

    classifier alone   P(normal) < threshold
    IsolationForest    alone
    served (OR)        either of them

with precision, recall and F1 for each, recall per cause (cascade is where the
IsolationForest matters), and the false positives that only the IsolationForest
adds. It trains nothing and writes nothing under models/; the only output is
the optional JSON file given with --out.

Usage:
  python scripts/decision_components.py
  python scripts/decision_components.py --test data/telecom_fleet_v2_test.csv --out build/decision_components.json
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "ml-model"))

import joblib  # noqa: E402
import numpy as np  # noqa: E402
import train_production as tp  # noqa: E402
from decision import classifier_vote_batch  # noqa: E402


def prf(t, f, n):
    p = t / (t + f) if t + f else 0.0
    r = t / (t + n) if t + n else 0.0
    f1 = 2 * t / (2 * t + f + n) if t + f + n else 0.0
    return round(p, 4), round(r, 4), round(f1, 4)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--test", type=Path, default=ROOT / "data" / "telecom_fleet_v2_test.csv")
    ap.add_argument("--models", type=Path, default=ROOT / "models")
    ap.add_argument("--out", type=Path, default=None)
    a = ap.parse_args()

    manifest = json.load(open(a.models / "manifest.json"))["production"]
    thr = float(manifest["decision_threshold"])
    baselines = json.load(open(a.models / tp.ARTIFACTS["baselines"]))
    clf = joblib.load(a.models / tp.ARTIFACTS["v4_model"])
    classes = list(joblib.load(a.models / tp.ARTIFACTS["v4_encoder"]).classes_)
    iso = joblib.load(a.models / tp.ARTIFACTS["iso_model"])

    test = tp.load_fleet(a.test)
    by_machine = test.groupby("machine", sort=True).indices
    machines = list(by_machine)
    names = ("classifier", "isolation_forest", "served_or")
    cnt = {n: {"tp": 0, "fp": 0, "fn": 0} for n in names}
    types, hits, only_iso_fp = {}, {n: {} for n in names}, 0
    positives = rows = 0
    step = tp.EVAL_MACHINES_PER_CHUNK
    for i in range(0, len(machines), step):
        idx = np.concatenate([by_machine[k] for k in machines[i:i + step]])
        t, z, z15 = tp.fleet_features(test.iloc[idx].copy(), baselines)
        y = t["label"].to_numpy() == 1
        yt = t["anomaly_type"].fillna("normal").to_numpy()
        c_hit, _ = classifier_vote_batch(clf.predict_proba(z15), classes, thr)
        i_hit = iso.predict(z) == -1
        pred = {"classifier": np.asarray(c_hit, bool), "isolation_forest": i_hit,
                "served_or": np.asarray(c_hit, bool) | i_hit}
        for n in names:
            p = pred[n]
            cnt[n]["tp"] += int(np.sum(p & y))
            cnt[n]["fp"] += int(np.sum(p & ~y))
            cnt[n]["fn"] += int(np.sum(~p & y))
            for ty in np.unique(yt):
                m = yt == ty
                hits[n][ty] = hits[n].get(ty, 0) + int(p[m].sum())
        for ty in np.unique(yt):
            types[ty] = types.get(ty, 0) + int((yt == ty).sum())
        only_iso_fp += int(np.sum(i_hit & ~np.asarray(c_hit, bool) & ~y))
        positives += int(y.sum())
        rows += len(y)
        print(f"  machines {i + 1}-{min(i + step, len(machines))} of {len(machines)}", file=sys.stderr)

    out = {"test_rows": rows, "anomalous_rows": positives, "threshold": thr,
           "false_positives_added_only_by_isolation_forest": only_iso_fp, "decisions": {}}
    print(f"\ntest rows {rows:,} | anomalous rows {positives:,} | threshold {thr}\n")
    print(f"{'decision':<18}{'precision':>10}{'recall':>9}{'F1':>8}")
    for n in names:
        p, r, f = prf(cnt[n]["tp"], cnt[n]["fp"], cnt[n]["fn"])
        out["decisions"][n] = {"precision": p, "recall": r, "f1": f, **cnt[n],
                               "recall_by_cause": {ty: round(hits[n][ty] / types[ty], 4)
                                                   for ty in sorted(types) if ty != "normal"}}
        print(f"{n:<18}{p:>10}{r:>9}{f:>8}")
    print("\nrecall by cause (share of rows of that cause flagged):")
    print(f"{'cause':<18}" + "".join(f"{n:>18}" for n in names))
    for ty in sorted(types):
        if ty == "normal":
            continue
        print(f"{ty:<18}" + "".join(f"{out['decisions'][n]['recall_by_cause'][ty]:>18}" for n in names))
    print(f"\nfalse positives that only the IsolationForest adds: {only_iso_fp:,}")
    if a.out:
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(json.dumps(out, indent=2))
        print(f"written to {a.out}")


if __name__ == "__main__":
    main()
