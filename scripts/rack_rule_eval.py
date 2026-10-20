#!/usr/bin/env python3
"""Does the Fleet tab's rack-level rule catch router incidents that per-machine detection misses?

Runs a trained artifact set on every reading of a v3 fleet (generate_telecom_fleet_v3.py) with the
served decision (classifier OR IsolationForest, v4 path), then applies the exact rule the Fleet tab
uses (api/fleet_rules.py) at every 30-second step to every router's rack. A reading whose machine
is unreachable (reachable=0) is treated as "not reporting", as the live agent sends nothing for it.
Scored against the incident log written next to the CSV:

  * per gray / hard-down incident: is the router named as suspect at some point, and how fast
  * per machine: share of the gray-cascade readings the served decision flags on its own
  * false alarms: router-steps outside any incident (and its recovery tail) where the rule names it

    python3 scripts/rack_rule_eval.py --data data/telecom_fleet_v3_test.csv --models models
    python3 scripts/rack_rule_eval.py --data X.csv --models build/candidate --threshold 0.13
"""
import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "ml-model"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, required=True, help="a v3 fleet CSV (with its .incidents.json)")
    ap.add_argument("--models", type=Path, default=ROOT / "models", help="artifact directory")
    ap.add_argument("--threshold", type=float, default=None,
                    help="decision threshold (default: production.decision_threshold in <models>/manifest.json)")
    ap.add_argument("--tail", type=int, default=4, help="readings after an incident counted as its recovery")
    args = ap.parse_args()

    import joblib
    import pandas as pd
    import decision
    import train_production as tp
    from api import fleet_rules

    log_path = args.data.with_suffix(".incidents.json")
    if not args.data.exists() or not log_path.exists():
        print(f"need {args.data} and {log_path}", file=sys.stderr)
        return 1
    log = json.loads(log_path.read_text())
    thr = args.threshold
    if thr is None:
        man = json.loads((args.models / "manifest.json").read_text())
        thr = float(man["production"]["decision_threshold"])
    n, step = log["readings_per_machine"], log["step_seconds"]
    t0 = datetime.fromisoformat(log["start"])
    dep_of = log["depends_on"]
    routers = sorted(log["incidents"])
    r_idx = {r: k for k, r in enumerate(routers)}
    n_dep = {r: sum(1 for v in dep_of.values() if v == r) for r in routers}

    print(f"loading {args.data} ...")
    fleet = tp.load_fleet(args.data)
    fleet["reachable"] = pd.read_csv(args.data, usecols=["reachable"])["reachable"].to_numpy()
    b = json.load(open(args.models / tp.ARTIFACTS["baselines"]))
    m = {k: joblib.load(args.models / v) for k, v in tp.ARTIFACTS.items() if v.endswith(".pkl")}
    classes = list(m["v4_encoder"].classes_)

    pred_dep = np.zeros((len(routers), n), dtype=np.int16)    # flagged AND reporting dependents
    live_dep = np.zeros((len(routers), n), dtype=np.int16)
    stale_dep = np.zeros((len(routers), n), dtype=np.int16)
    hub_bad = np.zeros((len(routers), n), dtype=bool)
    gray_rows = gray_hit = 0
    rows_by_machine = fleet.groupby("machine", sort=True).indices
    machines = list(rows_by_machine)
    for i in range(0, len(machines), tp.EVAL_MACHINES_PER_CHUNK):
        idx = np.concatenate([rows_by_machine[k] for k in machines[i:i + tp.EVAL_MACHINES_PER_CHUNK]])
        t, z, z15 = tp.fleet_features(fleet.iloc[idx].copy(), b)
        iso_hit = m["iso_model"].predict(z) == -1
        hit, cause = decision.classifier_vote_batch(m["v4_model"].predict_proba(z15), classes, thr)
        pred, _ = decision.final_verdict_batch(hit, cause, iso_hit)
        ts = ((pd.to_datetime(t["timestamp"]) - t0).dt.total_seconds() // step).astype(int).to_numpy()
        mach = t["machine"].to_numpy()
        reach = t["reachable"].to_numpy() == 1
        atype = t["anomaly_type"].fillna("").to_numpy()
        g = atype == "cascade_gray"
        gray_rows += int(g.sum())
        gray_hit += int((pred & g).sum())
        for r in routers:
            sel = mach == r
            if sel.any():
                hub_bad[r_idx[r], ts[sel]] = pred[sel] & reach[sel]
        dr = np.array([dep_of.get(x, "") for x in mach], dtype=object)
        for r in routers:
            sel = dr == r
            if not sel.any():
                continue
            k = r_idx[r]
            np.add.at(live_dep[k], ts[sel & reach], 1)
            np.add.at(stale_dep[k], ts[sel & ~reach], 1)
            np.add.at(pred_dep[k], ts[sel & reach & pred], 1)
        print(f"  scored machines {i + 1}-{min(i + tp.EVAL_MACHINES_PER_CHUNK, len(machines))} of {len(machines)}",
              flush=True)

    state = np.empty((len(routers), n), dtype=object)
    for r in routers:
        k = r_idx[r]
        for s in range(n):
            state[k, s] = fleet_rules.classify(n_dep[r], int(stale_dep[k, s]), int(live_dep[k, s]),
                                               int(pred_dep[k, s]), bool(hub_bad[k, s]))
    flagging = np.isin(state, list(fleet_rules.FLAGGING))

    covered = np.zeros((len(routers), n), dtype=bool)
    results = {"gray": [], "down": []}
    for r in routers:
        k = r_idx[r]
        for inc in log["incidents"][r]:
            s, e = inc["start"], min(n, inc["end"] + args.tail)
            covered[k, s:e] = True
            if inc["mode"] in results:
                hits = np.flatnonzero(flagging[k, s:e])
                results[inc["mode"]].append(int(hits[0]) if len(hits) else None)

    print(f"\nthreshold {thr}, rule: {fleet_rules.SUSPECT_SHARE:.0%} of reporting dependents, at least "
          f"{fleet_rules.MIN_GROUP} machines")
    if gray_rows:
        print(f"per machine: the served decision flags {gray_hit / gray_rows:.1%} of gray-cascade readings "
              f"({gray_hit:,} of {gray_rows:,})")
    for mode, lags in results.items():
        if not lags:
            continue
        found = [x for x in lags if x is not None]
        med = float(np.median(found)) * step if found else float("nan")
        print(f"per incident, {mode:<4}: router named as suspect in {len(found)} of {len(lags)} "
              f"({len(found) / len(lags):.0%}); median {med:.0f} s after the incident starts")
    outside = int((~covered).sum())
    fa = int((flagging & ~covered).sum())
    days = outside * step / 86400
    print(f"false alarms: {fa:,} router-steps flagged outside any incident, of {outside:,} "
          f"({fa / max(outside, 1):.3%}; {fa / max(days, 1e-9):.1f} per router-day)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
