#!/usr/bin/env python3
"""
Read-only: would the safety net be better if it had to be confirmed by the machine's router?

The served verdict is `classifier OR IsolationForest` (ml-model/decision.py). Measured live on 27 October
(scripts/safety_net_live.py), the alerts raised only by the IsolationForest were right 17% of the time, and
96% of the right ones were cascades: a machine stressed because ITS ROUTER failed. This script measures, on
the independent test set and at the served threshold, the rule

    router-confirmed:  classifier vote
                       OR (IsolationForest vote AND the machine's router is flagged at that moment)

where the router comes from models/dependency_graph.json (a router has no router: its own IsolationForest
vote stands) and "flagged at that moment" is the router's served verdict on a reading at the same time or
up to --window-s seconds before (what a pod could know when the machine's reading arrives). Next to it:
the classifier alone, today's served OR, and the same rule with the router's TRUE label (an upper bound
no deployment can reach). For each: precision, recall, F1, F-beta (beta and the minimum recall from
config/model_policy.json),
cascade recall, false alarms, and the precision of the alerts raised only by the IsolationForest.

It also checks that the graph matches the data: the share of cascade readings whose graph router is truly
anomalous at that time (the generator builds cascades from the router's anomalies, so it should be ~1).
Trains nothing, writes nothing under models/; the only output is the optional --out JSON.

  python3 scripts/safety_net_router_rule.py --out models/results/safety_net_router_rule_YYYYMMDD.json
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "ml-model"))

import joblib  # noqa: E402
import pandas as pd  # noqa: E402
import numpy as np  # noqa: E402
import train_production as tp  # noqa: E402
from decision import classifier_vote_batch  # noqa: E402

NAMES = ("classifier", "served_or", "router_confirmed", "router_confirmed_truth")


def scores(c, beta):
    t, f, n = c["tp"], c["fp"], c["fn"]
    p = t / (t + f) if t + f else 0.0
    r = t / (t + n) if t + n else 0.0
    f1 = 2 * p * r / (p + r) if p + r else 0.0
    b2 = beta * beta
    fb = (1 + b2) * p * r / (b2 * p + r) if p + r else 0.0
    return round(p, 4), round(r, 4), round(f1, 4), round(fb, 4)


def verdicts(frame, baselines, clf, classes, iso, thr):
    """Served parts for one chunk of whole machines: the frame (sorted), classifier vote, IsolationForest vote."""
    t, z, z15 = tp.fleet_features(frame.copy(), baselines)
    c_hit, _ = classifier_vote_batch(clf.predict_proba(z15), classes, thr)
    return t, np.asarray(c_hit, bool), iso.predict(z) == -1


def flagged_at(times_ns, router_times, router_flags, window_ns):
    """For each time: was the router flagged on a reading in [time - window, time]?"""
    flagged_times = router_times[router_flags]
    if not len(flagged_times):
        return np.zeros(len(times_ns), bool)
    idx = np.searchsorted(flagged_times, times_ns, side="right") - 1      # last flagged reading at or before
    ok = idx >= 0
    out = np.zeros(len(times_ns), bool)
    out[ok] = times_ns[ok] - flagged_times[idx[ok]] <= window_ns
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--test", type=Path, default=ROOT / "data" / "telecom_fleet_v2_test.csv")
    ap.add_argument("--models", type=Path, default=ROOT / "models")
    ap.add_argument("--window-s", type=float, default=60.0, help="how far back a router's alert still counts")
    ap.add_argument("--out", type=Path, default=None)
    a = ap.parse_args()

    manifest = json.load(open(a.models / "manifest.json"))["production"]
    thr = float(manifest["decision_threshold"])
    policy = json.load(open(ROOT / "config" / "model_policy.json"))["threshold"]
    beta, min_recall = float(policy["beta"]), float(policy["min_recall"])
    baselines = json.load(open(a.models / tp.ARTIFACTS["baselines"]))
    clf = joblib.load(a.models / tp.ARTIFACTS["v4_model"])
    classes = list(joblib.load(a.models / tp.ARTIFACTS["v4_encoder"]).classes_)
    iso = joblib.load(a.models / tp.ARTIFACTS["iso_model"])
    graph = json.load(open(a.models / "dependency_graph.json"))
    upstream = graph["depends_on"]
    window_ns = int(a.window_s * 1e9)

    test = tp.load_fleet(a.test)
    by_machine = test.groupby("machine", sort=True).indices
    # pass 1: each router's served verdict and true label over time
    routers = {}
    for r in sorted(set(upstream.values()) | set(graph.get("routers", []))):
        if r not in by_machine:
            continue
        t, c_hit, i_hit = verdicts(test.iloc[by_machine[r]], baselines, clf, classes, iso, thr)
        ts = pd.to_datetime(t["timestamp"]).to_numpy().astype("int64")
        routers[r] = (ts, c_hit | i_hit, t["label"].to_numpy() == 1)
        print(f"  router {r}: {int((c_hit | i_hit).sum()):,} flagged of {len(ts):,}", file=sys.stderr)

    cnt = {n: {"tp": 0, "fp": 0, "fn": 0, "only_iso_tp": 0, "only_iso_fp": 0, "cascade_hit": 0} for n in NAMES}
    cascades = cascade_router_true = rows = positives = 0
    machines = list(by_machine)
    step = tp.EVAL_MACHINES_PER_CHUNK
    for i in range(0, len(machines), step):
        idx = np.concatenate([by_machine[k] for k in machines[i:i + step]])
        t, c_hit, i_hit = verdicts(test.iloc[idx], baselines, clf, classes, iso, thr)
        y = t["label"].to_numpy() == 1
        yt = t["anomaly_type"].fillna("normal").to_numpy()
        ts = pd.to_datetime(t["timestamp"]).to_numpy().astype("int64")
        m = t["machine"].to_numpy()
        r_flag, r_true = np.zeros(len(t), bool), np.zeros(len(t), bool)
        for mach in np.unique(m):
            rows_m = m == mach
            r = upstream.get(mach)
            if r is None or r == mach or r not in routers:
                r_flag[rows_m] = r_true[rows_m] = True        # no router above it: its own IsolationForest vote stands
                continue
            rts, rflag, rtrue = routers[r]
            r_flag[rows_m] = flagged_at(ts[rows_m], rts, rflag, window_ns)
            r_true[rows_m] = flagged_at(ts[rows_m], rts, rtrue, window_ns)
        decided = {"classifier": c_hit, "served_or": c_hit | i_hit,
                   "router_confirmed": c_hit | (i_hit & r_flag), "router_confirmed_truth": c_hit | (i_hit & r_true)}
        for n, p in decided.items():
            only = p & ~c_hit
            cn = cnt[n]
            cn["tp"] += int(np.sum(p & y)); cn["fp"] += int(np.sum(p & ~y)); cn["fn"] += int(np.sum(~p & y))
            cn["only_iso_tp"] += int(np.sum(only & y)); cn["only_iso_fp"] += int(np.sum(only & ~y))
            cn["cascade_hit"] += int(np.sum(p & (yt == "cascade")))
        is_cascade = yt == "cascade"
        cascades += int(is_cascade.sum())
        cascade_router_true += int(np.sum(is_cascade & r_true))
        rows += len(t)
        positives += int(y.sum())
        print(f"  machines {i + 1}-{min(i + step, len(machines))} of {len(machines)}", file=sys.stderr)

    graph_match = round(cascade_router_true / cascades, 4) if cascades else None
    out = {"test_rows": rows, "anomalous_rows": positives, "threshold": thr, "beta": beta, "min_recall": min_recall,
           "window_s": a.window_s,
           "cascade_rows": cascades, "graph_matches_data": graph_match, "decisions": {}}
    print(f"\ntest rows {rows:,} | anomalous {positives:,} | threshold {thr} | router window {a.window_s:g} s")
    print(f"graph check: {graph_match} of cascade readings have their graph router truly anomalous at that time\n")
    print(f"{'decision':<24}{'precision':>10}{'recall':>9}{'F1':>8}{'F' + str(int(beta)):>8}{'cascade':>9}"
          f"{'false alarms':>14}{'IF-only flags':>15}{'IF-only prec.':>15}")
    for n in NAMES:
        c = cnt[n]
        p, r, f1, fb = scores(c, beta)
        flags = c["only_iso_tp"] + c["only_iso_fp"]
        op = round(c["only_iso_tp"] / flags, 4) if flags else 0.0
        cr = round(c["cascade_hit"] / cascades, 4) if cascades else 0.0
        out["decisions"][n] = {"precision": p, "recall": r, "f1": f1, "f_beta": fb, "cascade_recall": cr,
                               "false_alarms": c["fp"], "isolation_forest_only_flags": flags,
                               "isolation_forest_only_precision": op, **c}
        out["decisions"][n]["meets_min_recall"] = r >= min_recall
        print(f"{n:<24}{p:>10}{r:>9}{f1:>8}{fb:>8}{cr:>9}{c['fp']:>14,}{flags:>15,}{op:>15}")
    print(f"(the policy asks recall >= {min_recall} and picks by F{beta:g}: config/model_policy.json)")
    if a.out:
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(json.dumps(out, indent=2) + "\n")
        print(f"\nwritten to {a.out}")


if __name__ == "__main__":
    main()
