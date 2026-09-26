#!/usr/bin/env python3
"""
Live validation of the Kubernetes deployment on the INDEPENDENT test set.

Data: data/telecom_fleet_v2_test.csv (generator seed 123) -- never used for
training (the models are trained on the seed-42 file). The first --days days
are taken; every machine is checked every --every-hours hours, so all 200
machines and all four time windows are covered.

Each request carries the machine's 10 preceding readings as `history`,
exactly what a monitoring agent would hold, so the deployment answers with
the v4 model; the script reports which model actually served each request
instead of assuming it. Requests go through the NodePort, like any external
client, and are answered by whichever pod the Service picks.

Reported: served F1 / precision / recall, per-cause recall, cause accuracy,
latency percentiles, and the served-model breakdown. Results are also
written as JSON (--out) so the numbers quoted in the README can be traced
to a file.

Usage:
  python scripts/live_k8s_validation.py
  python scripts/live_k8s_validation.py --days 14 --every-hours 4 --out build/live_validation.json
"""
import argparse
import hashlib
import json
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd
import requests
from sklearn.metrics import f1_score, precision_score, recall_score

ROOT = Path(__file__).resolve().parent.parent
FEATURES = ["cpu", "ram", "network", "disk_io", "disk_usage", "load_avg"]
HISTORY_KEYS = ["cpu", "ram", "load_avg"]
WINDOW = 10


def load_slice(path: Path, days: int) -> pd.DataFrame:
    cols = ["timestamp", "machine", "type", *FEATURES, "label", "anomaly_type"]
    parts, start = [], None
    for chunk in pd.read_csv(path, usecols=cols, parse_dates=["timestamp"], chunksize=1_000_000):
        start = chunk["timestamp"].min() if start is None else min(start, chunk["timestamp"].min())
        parts.append(chunk[chunk["timestamp"] < start + pd.Timedelta(days=days)])
    df = pd.concat(parts, ignore_index=True)
    return df.sort_values(["machine", "timestamp"]).reset_index(drop=True)


def build_requests(df: pd.DataFrame, every_hours: int) -> list[dict]:
    out = []
    for _machine, grp in df.groupby("machine", sort=False):
        grp = grp.reset_index(drop=True)
        ts = grp["timestamp"]
        picks = grp.index[(ts.dt.hour % every_hours == 0) & (ts.dt.minute == 0)
                          & (ts.dt.second == 0) & (grp.index >= WINDOW - 1)]
        for i in picks:
            row = grp.iloc[i]
            win = grp.iloc[i - WINDOW + 1:i + 1]
            out.append({
                "payload": {
                    "machine": row["machine"], "machine_type": row["type"],
                    "hour": int(row["timestamp"].hour),
                    "metrics": {c: float(row[c]) for c in FEATURES},
                    "history": {c: [float(v) for v in win[c]] for c in HISTORY_KEYS},
                },
                "true_label": int(row["label"]),
                "true_cause": row["anomaly_type"] if isinstance(row["anomaly_type"], str) else "normal",
            })
    return out


def call(url: str, item: dict) -> dict:
    t0 = time.time()
    try:
        r = requests.post(url + "/predict", json=item["payload"], timeout=15)
        lat = time.time() - t0
        if r.status_code != 200:
            return {"error": r.status_code, "latency": lat}
        return {**r.json(), "latency": lat,
                "true_label": item["true_label"], "true_cause": item["true_cause"]}
    except requests.RequestException as e:
        return {"error": str(e), "latency": time.time() - t0}


def summarise(ok: list[dict]) -> dict:
    y_true = [r["true_label"] for r in ok]
    y_pred = [r["is_anomaly"] for r in ok]
    per_cause = defaultdict(lambda: [0, 0])
    for r in ok:
        if r["true_label"] == 1:
            per_cause[r["true_cause"]][1] += 1
            per_cause[r["true_cause"]][0] += r["is_anomaly"]
    tp_named = [r for r in ok if r["true_label"] == 1 and r["is_anomaly"] == 1
                and r["true_cause"] != "cascade"
                and (r.get("xgb_vote") or {}).get("is_anomaly") == 1]
    lat = sorted(r["latency"] for r in ok)
    return {
        "requests": len(ok),
        "anomalous_readings": int(sum(y_true)),
        "served_by_model": dict(Counter(r.get("model") for r in ok)),
        "f1": round(f1_score(y_true, y_pred), 4),
        "precision": round(precision_score(y_true, y_pred), 4),
        "recall": round(recall_score(y_true, y_pred), 4),
        "per_cause_recall": {c: round(h / t, 4) for c, (h, t) in sorted(per_cause.items())},
        "cause_accuracy_on_detected": round(
            sum(r.get("likely_cause") == r["true_cause"] for r in tp_named) / max(len(tp_named), 1), 4),
        "latency_ms": {"p50": round(lat[len(lat) // 2] * 1000, 1),
                       "p95": round(lat[int(len(lat) * 0.95)] * 1000, 1),
                       "max": round(lat[-1] * 1000, 1)},
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default="http://192.168.49.2:30080")
    ap.add_argument("--data", type=Path, default=ROOT / "data" / "telecom_fleet_v2_test.csv")
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--every-hours", type=int, default=4)
    ap.add_argument("--workers", type=int, default=20)
    ap.add_argument("--out", type=Path, default=ROOT / "build" / "live_validation.json")
    a = ap.parse_args()

    digest = hashlib.sha256(a.data.read_bytes()).hexdigest()
    print(f"Independent test set {a.data} (sha256 {digest[:12]}), first {a.days} days")
    df = load_slice(a.data, a.days)
    items = build_requests(df, a.every_hours)
    print(f"{len(items):,} requests ({df['machine'].nunique()} machines, every {a.every_hours} h, "
          f"each with its 10 preceding readings as history) -> {a.url}")

    t0 = time.time()
    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        results = list(ex.map(lambda it: call(a.url, it), items))
    elapsed = time.time() - t0
    ok = [r for r in results if "error" not in r]
    errors = [r for r in results if "error" in r]
    print(f"done in {elapsed:.0f}s ({len(results) / elapsed:.1f} req/s); errors: {len(errors)}")
    if not ok:
        raise SystemExit("no successful responses")

    summary = summarise(ok) | {
        "test_set": {"file": str(a.data.relative_to(ROOT)) if a.data.is_relative_to(ROOT) else str(a.data),
                     "sha256": digest},
        "days": a.days, "every_hours": a.every_hours, "errors": len(errors),
        "url": a.url, "finished_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    print(json.dumps(summary, indent=2))
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(summary, indent=2))
    print(f"written to {a.out}")


if __name__ == "__main__":
    main()
