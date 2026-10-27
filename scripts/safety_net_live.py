#!/usr/bin/env python3
"""
Live check of who raises the alerts and how often they are right, on the cluster's own predictions.

Every reading the production agent (or the Demo tab) sends and the cluster flags gets its true label
recorded (api/demo_labels.py, about the last 8 hours). This script joins those labels with the cluster's
prediction log (GET /predictions/by-machine on the pods) and splits the flagged readings by who raised them:

  classifier   XGBoost P(normal) below the served threshold (it names a cause)
  safety net   only the IsolationForest fired (cause "unknown (flagged by safety-net model only)")

For each: how many flags, how many were real anomalies (precision), and the real causes of the hits and of
the false alarms. Read-only; run on the VM, where the label store and the NodePort are.

  python3 scripts/safety_net_live.py
  python3 scripts/safety_net_live.py --out models/results/safety_net_live_YYYYMMDD.json
"""
import argparse
import datetime as dt
import json
import sys
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from api import demo_labels  # noqa: E402

SAFETY_NET = "unknown (flagged by safety-net model only)"


def logged_predictions(cluster, machine, start, end):
    q = urllib.parse.urlencode({"machine": machine, "start": start, "end": end, "limit": 2000})
    with urllib.request.urlopen(f"{cluster}/predictions/by-machine?{q}", timeout=30) as r:
        return json.load(r)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cluster", default="http://192.168.49.2:30080", help="the pods' NodePort")
    ap.add_argument("--out", type=Path, default=ROOT / "build" / "safety_net_live.json")
    args = ap.parse_args()
    labels = [e for e in demo_labels.load().values() if e.get("machine") and isinstance(e.get("logged"), (int, float))]
    if not labels:
        sys.exit(f"no labelled reading with a machine in {demo_labels.labels_path()}: is the production agent running?")
    by_machine = {}
    for e in labels:
        by_machine.setdefault(e["machine"], []).append(e)
    found, truncated = {}, 0
    for machine, rows in sorted(by_machine.items()):
        lo, hi = min(r["logged"] for r in rows) - 60, max(r["logged"] for r in rows) + 60
        while hi - lo > 0:            # the route returns at most 2000 rows: walk the window in slices
            part = logged_predictions(args.cluster, machine, lo, min(hi, lo + 4 * 3600))
            for p in part["predictions"]:
                found[p["id"]] = p
            truncated += bool(part["truncated"])
            lo += 4 * 3600
    stats = {k: {"flags": 0, "real": 0, "false_alarms": 0, "real_causes": Counter(), "false_alarm_causes_named": Counter()}
             for k in ("classifier", "safety_net")}
    unmatched = 0
    for e in labels:
        p = found.get(e["id"])
        if not p or p.get("final_is_anomaly") != 1:
            unmatched += 1
            continue
        who = "safety_net" if p.get("final_cause") == SAFETY_NET else "classifier"
        s = stats[who]
        s["flags"] += 1
        if e["label"] == 1:
            s["real"] += 1
            s["real_causes"][e.get("cause") or "?"] += 1
        else:
            s["false_alarms"] += 1
            s["false_alarm_causes_named"][p.get("final_cause") or "?"] += 1
    span = (min(e["logged"] for e in labels), max(e["logged"] for e in labels))
    out = {"finished_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "labels_from": dt.datetime.fromtimestamp(span[0], dt.timezone.utc).isoformat(),
           "labels_to": dt.datetime.fromtimestamp(span[1], dt.timezone.utc).isoformat(),
           "labelled_flags": len(labels), "not_found_in_the_log": unmatched, "truncated_queries": truncated, "by_flagger": {}}
    total = sum(s["flags"] for s in stats.values()) or 1
    print(f"labelled flagged readings {len(labels):,} ({out['labels_from'][:16]} → {out['labels_to'][:16]} UTC), "
          f"{unmatched:,} not found in the cluster log\n")
    print(f"{'raised by':<12}{'flags':>8}{'share':>8}{'real':>8}{'false alarms':>14}{'precision':>11}")
    for who, s in stats.items():
        prec = round(s["real"] / s["flags"], 4) if s["flags"] else None
        out["by_flagger"][who] = {"flags": s["flags"], "share": round(s["flags"] / total, 4), "real": s["real"],
                                  "false_alarms": s["false_alarms"], "precision": prec,
                                  "real_causes": dict(s["real_causes"].most_common()),
                                  "false_alarm_named_causes": dict(s["false_alarm_causes_named"].most_common())}
        print(f"{who:<12}{s['flags']:>8,}{s['flags'] / total:>8.1%}{s['real']:>8,}{s['false_alarms']:>14,}{prec if prec is not None else '-':>11}")
    for who in stats:
        print(f"\n{who}: real causes of its hits: " + (", ".join(f"{k} {v}" for k, v in out["by_flagger"][who]["real_causes"].items()) or "-"))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out, indent=2) + "\n")
    print(f"\nwritten to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
