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
adds. Also:
  - the flags raised ONLY by the IsolationForest (what the Review tab shows as
    "safety net"): how many, how many are real anomalies (their precision), and
    which causes the real ones are;
  - the same decisions with a stricter IsolationForest cut-off (decision_function
    below 0, the trained one, then -0.02 ... -0.15): what each would give in
    precision, recall, F1, cascade recall and safety-net-only precision. It trains nothing and writes nothing under models/; the only output is
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
    cuts = (0.0, -0.02, -0.04, -0.06, -0.08, -0.10, -0.15)
    sweep = {c: {"tp": 0, "fp": 0, "fn": 0, "only_tp": 0, "only_fp": 0, "cascade_hit": 0} for c in cuts}
    only_tp_by_cause = {}
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
        score = iso.decision_function(z)             # below 0 = outlier: iso.predict(z) == -1
        i_hit = score < 0
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
        c_b = np.asarray(c_hit, bool)
        for ty in np.unique(yt[i_hit & ~c_b & y]):
            only_tp_by_cause[ty] = only_tp_by_cause.get(ty, 0) + int(np.sum(i_hit & ~c_b & y & (yt == ty)))
        for c in cuts:
            ic = score < c
            served = c_b | ic
            only = ic & ~c_b
            sw = sweep[c]
            sw["tp"] += int(np.sum(served & y)); sw["fp"] += int(np.sum(served & ~y)); sw["fn"] += int(np.sum(~served & y))
            sw["only_tp"] += int(np.sum(only & y)); sw["only_fp"] += int(np.sum(only & ~y))
            sw["cascade_hit"] += int(np.sum(served & (yt == "cascade")))
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
    s0 = sweep[0.0]
    only_prec = round(s0["only_tp"] / (s0["only_tp"] + s0["only_fp"]), 4) if s0["only_tp"] + s0["only_fp"] else 0.0
    out["safety_net_only"] = {"flags": s0["only_tp"] + s0["only_fp"], "real_anomalies": s0["only_tp"],
                              "false_alarms": s0["only_fp"], "precision": only_prec,
                              "real_anomalies_by_cause": dict(sorted(only_tp_by_cause.items(), key=lambda kv: -kv[1]))}
    print(f"\nflags raised ONLY by the IsolationForest (the Review tab's 'safety net'): {s0['only_tp'] + s0['only_fp']:,}")
    print(f"  real anomalies {s0['only_tp']:,} · false alarms {s0['only_fp']:,} · precision {only_prec}")
    print("  real anomalies by cause: " + ", ".join(f"{k} {v:,}" for k, v in out["safety_net_only"]["real_anomalies_by_cause"].items()))
    cascades = types.get("cascade", 0)
    out["isolation_forest_cutoffs"] = []
    print("\nserved decision with a stricter IsolationForest cut-off (0 = the trained one):")
    print(f"{'cut-off':>8}{'precision':>10}{'recall':>9}{'F1':>8}{'cascade recall':>16}{'net-only flags':>16}{'net-only precision':>20}")
    for c in cuts:
        sw = sweep[c]
        p, r, f = prf(sw["tp"], sw["fp"], sw["fn"])
        flags = sw["only_tp"] + sw["only_fp"]
        op = round(sw["only_tp"] / flags, 4) if flags else 0.0
        cr = round(sw["cascade_hit"] / cascades, 4) if cascades else 0.0
        out["isolation_forest_cutoffs"].append({"cutoff": c, "precision": p, "recall": r, "f1": f, "cascade_recall": cr,
                                                "safety_net_only_flags": flags, "safety_net_only_precision": op, **sw})
        print(f"{c:>8}{p:>10}{r:>9}{f:>8}{cr:>16}{flags:>16,}{op:>20}")
    if a.out:
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(json.dumps(out, indent=2))
        print(f"written to {a.out}")


if __name__ == "__main__":
    main()
