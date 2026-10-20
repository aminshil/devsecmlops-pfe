#!/usr/bin/env python3
"""Does a router's OWN anomaly label line up with its dependents failing?

Measures, per router, over every timestamp in the dataset:
  * its own anomaly rate
  * the correlation between its own label and the share of its dependents that are anomalous
  * during "cascade events" (a large share of its dependents anomalous at once), how often the
    router itself is anomalous - compared with its base rate

If the router's own label carried the cascade, the correlation would be strongly positive and the
router would almost always be anomalous during its own cascades. Read-only.

    python3 scripts/cascade_check.py
    python3 scripts/cascade_check.py --data data/telecom_fleet_v2_test.csv --share 0.3
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, default=ROOT / "data" / "telecom_fleet_v2_labeled.csv")
    ap.add_argument("--graph", type=Path, default=ROOT / "models" / "dependency_graph.json")
    ap.add_argument("--share", type=float, default=0.25,
                    help="a cascade event = at least this share of a router's dependents anomalous")
    args = ap.parse_args()
    import pandas as pd

    if not args.data.exists():
        print(f"missing {args.data} - run where the datasets are", file=sys.stderr)
        return 1
    graph = json.loads(args.graph.read_text())
    depends_on, routers = graph["depends_on"], list(graph["routers"])
    n_dep = {r: len(graph["downstream_of"].get(r, [])) for r in routers}

    dep_hits, own = {}, {}            # (ts, router) -> anomalous dependents ; (ts, router) -> own label
    rows = 0
    for chunk in pd.read_csv(args.data, usecols=["timestamp", "machine", "label"], chunksize=1_000_000):
        rows += len(chunk)
        chunk["label"] = chunk["label"].astype(int)
        is_router = chunk["machine"].isin(routers)
        r = chunk.loc[is_router]
        for (ts, m), v in r.groupby(["timestamp", "machine"])["label"].max().items():
            own[(ts, m)] = int(v)
        d = chunk.loc[~is_router].copy()
        d["router"] = d["machine"].map(depends_on)
        d = d.dropna(subset=["router"])
        for (ts, rt), v in d.groupby(["timestamp", "router"])["label"].sum().items():
            dep_hits[(ts, rt)] = dep_hits.get((ts, rt), 0) + int(v)
        print(f"  ...{rows:,} rows", end="\r", flush=True)
    print(f"  read {rows:,} rows      ")

    if not own or not dep_hits:
        print("no router rows or no dependent rows found", file=sys.stderr)
        return 1

    print(f"\n{'router':>10} | {'own rate':>8} | {'corr(own, dep share)':>20} | "
          f"{'cascade events':>14} | {'router anomalous then':>21}")
    print("-" * 88)
    summary = []
    for rt in routers:
        ts_keys = [k[0] for k in dep_hits if k[1] == rt]
        if not ts_keys:
            continue
        share = pd.Series({t: dep_hits[(t, rt)] / max(n_dep[rt], 1) for t in ts_keys})
        mine = pd.Series({t: own.get((t, rt), 0) for t in ts_keys})
        corr = mine.corr(share)
        ev = share >= args.share
        n_ev = int(ev.sum())
        hit = float(mine[ev].mean()) if n_ev else float("nan")
        base = float(mine.mean())
        summary.append((rt, base, corr, n_ev, hit))
        print(f"{rt:>10} | {base:>8.4f} | {corr:>20.4f} | {n_ev:>14,} | "
              f"{(f'{hit:.4f}' if n_ev else 'n/a'):>21}")

    corrs = [c for _, _, c, _, _ in summary if c == c]
    hits = [(h, n) for _, _, _, n, h in summary if n and h == h]
    base_all = sum(b for _, b, _, _, _ in summary) / len(summary)
    print()
    print(f"mean correlation across routers: {sum(corrs) / len(corrs):+.4f}" if corrs else "no correlation")
    if hits:
        w = sum(h * n for h, n in hits) / sum(n for _, n in hits)
        print(f"during cascade events the router is anomalous {w:.4f} of the time; "
              f"its base rate is {base_all:.4f}")
        print(f"ratio {w / base_all:.2f}x  (1.0x = the cascade tells you nothing about the router)")
    print()
    print("Near-zero correlation and a ~1x ratio mean the router's own readings do not mark the")
    print("cascades it causes: the generator draws each router twice - once for the mask that drives")
    print("its dependents, once for its own rows - so the two are independent.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
