#!/usr/bin/env python3
"""Sweep the Fleet tab's rack rule: evidence window x share of the rack, on a v3 fleet.

Scores every reading once with a trained artifact set (served decision, v4 path), then for each
window W (a machine counts as flagged if it was flagged at least once in its last W readings) and
each share threshold, applies the Fleet tab's rule (api/fleet_rules.classify_arrays, the same rule
as the API) at every 30-second step and scores it against the incident log:

  gray / hard-down incidents in which the router is named as suspect, how fast, and the false
  alarms outside any incident. The shipped setting is window 30 s, share 40%.

    python3 scripts/rack_rule_sweep.py --data data/telecom_fleet_v3_test.csv --models models
    python3 scripts/rack_rule_sweep.py --data X.csv --models build/v3/candidate --threshold 0.32
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


def windowed(a, w):
    """True where the machine was flagged at least once in its last w readings."""
    if w <= 1:
        return a
    c = np.concatenate([[0], np.cumsum(a.astype(np.int32))])
    idx = np.arange(1, len(a) + 1)
    return (c[idx] - c[np.maximum(idx - w, 0)]) > 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, required=True)
    ap.add_argument("--models", type=Path, default=ROOT / "models")
    ap.add_argument("--threshold", type=float, default=None,
                    help="default: production.decision_threshold in <models>/manifest.json")
    ap.add_argument("--windows", default="1,4,6,8", help="readings (30 s each)")
    ap.add_argument("--shares", default="0.2,0.25,0.3,0.4")
    ap.add_argument("--tail", type=int, default=4)
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
        thr = float(json.loads((args.models / "manifest.json").read_text())["production"]["decision_threshold"])
    n, step, t0 = log["readings_per_machine"], log["step_seconds"], datetime.fromisoformat(log["start"])
    dep_of, routers = log["depends_on"], sorted(log["incidents"])

    print(f"loading {args.data} ...", flush=True)
    fleet = tp.load_fleet(args.data)
    fleet["reachable"] = pd.read_csv(args.data, usecols=["reachable"])["reachable"].to_numpy()
    b = json.load(open(args.models / tp.ARTIFACTS["baselines"]))
    m = {k: joblib.load(args.models / v) for k, v in tp.ARTIFACTS.items() if v.endswith(".pkl")}
    classes = list(m["v4_encoder"].classes_)
    pred_m, reach_m = {}, {}
    by_machine = fleet.groupby("machine", sort=True).indices
    names = list(by_machine)
    for i in range(0, len(names), tp.EVAL_MACHINES_PER_CHUNK):
        idx = np.concatenate([by_machine[k] for k in names[i:i + tp.EVAL_MACHINES_PER_CHUNK]])
        t, z, z15 = tp.fleet_features(fleet.iloc[idx].copy(), b)
        iso = m["iso_model"].predict(z) == -1
        hit, cause = decision.classifier_vote_batch(m["v4_model"].predict_proba(z15), classes, thr)
        pred, _ = decision.final_verdict_batch(hit, cause, iso)
        ts = ((pd.to_datetime(t["timestamp"]) - t0).dt.total_seconds() // step).astype(int).to_numpy()
        mach, reach = t["machine"].to_numpy(), t["reachable"].to_numpy() == 1
        for name in np.unique(mach):
            sel = mach == name
            p = np.zeros(n, dtype=bool)
            r = np.ones(n, dtype=bool)
            p[ts[sel]] = np.asarray(pred)[sel]
            r[ts[sel]] = reach[sel]
            pred_m[name], reach_m[name] = p, r
        print(f"  scored machines {i + 1}-{min(i + tp.EVAL_MACHINES_PER_CHUNK, len(names))} of {len(names)}", flush=True)

    covered, incs = {}, {r: log["incidents"][r] for r in routers}
    for r in routers:
        c = np.zeros(n, dtype=bool)
        for inc in incs[r]:
            c[inc["start"]:min(n, inc["end"] + args.tail)] = True
        covered[r] = c
    deps = {r: [d for d, rr in dep_of.items() if rr == r and d in pred_m] for r in routers}
    shipped = (1, fleet_rules.SUSPECT_SHARE)

    print(f"\nthreshold {thr}; a router is named when the rule says {sorted(fleet_rules.FLAGGING)}")
    print(f"{'window':>8} {'share':>6} | {'gray named':>14} {'median lag':>11} | {'down named':>14} | "
          f"{'false alarms / router-day':>26}")
    for w in (int(x) for x in args.windows.split(",")):
        flagged = {k: windowed(v & reach_m[k], w) for k, v in pred_m.items()}
        for share in (float(x) for x in args.shares.split(",")):
            g = [0, 0]; d = [0, 0]; fa = out = 0; lags = []
            for r in routers:
                live = np.sum([reach_m[x] for x in deps[r]], axis=0)
                stale = len(deps[r]) - live
                bad = np.sum([flagged[x] & reach_m[x] for x in deps[r]], axis=0)
                hub = flagged.get(r, np.zeros(n, bool)) & reach_m.get(r, np.ones(n, bool))
                st = fleet_rules.classify_arrays(len(deps[r]), stale, live, bad, hub, share, fleet_rules.MIN_GROUP)
                named = np.isin(st, list(fleet_rules.FLAGGING))
                for inc in incs[r]:
                    hits = np.flatnonzero(named[inc["start"]:min(n, inc["end"] + args.tail)])
                    if inc["mode"] == "gray":
                        g[1] += 1
                        if len(hits):
                            g[0] += 1
                            lags.append(int(hits[0]) * step)
                    elif inc["mode"] == "down":
                        d[1] += 1
                        d[0] += int(len(hits) > 0)
                fa += int((named & ~covered[r]).sum())
                out += int((~covered[r]).sum())
            lag = f"{np.median(lags):.0f} s" if lags else "-"
            tag = "  <- shipped" if (w, share) == shipped else ""
            print(f"{w * step:>6} s {share:>6.0%} | {g[0]:>5}/{g[1]:<4} {g[0] / max(g[1], 1):>4.0%} {lag:>11} | "
                  f"{d[0]:>5}/{d[1]:<4} {d[0] / max(d[1], 1):>4.0%} | {fa / max(out * step / 86400, 1e-9):>26.1f}{tag}",
                  flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
