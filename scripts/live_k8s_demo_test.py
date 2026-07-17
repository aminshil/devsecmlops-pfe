"""
Two-week live demo: every machine (200), checked every 4 hours, across
14 consecutive days -- full coverage of all machines and all time-of-day
windows (night/morning/afternoon/evening), sent through the LIVE K8s
deployment.
"""
import requests
import pandas as pd
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from sklearn.metrics import f1_score, precision_score, recall_score
from collections import defaultdict

MINIKUBE_IP = "192.168.49.2"
BASE_URL = f"http://{MINIKUBE_IP}:30080"
FEATURES = ["cpu", "ram", "network", "disk_io", "disk_usage", "load_avg"]

print("Loading full dataset (seed=42, same as training)...")
df = pd.read_csv("data/telecom_fleet_two_weeks_demo.csv", parse_dates=["timestamp"])
print(f"  Total rows: {len(df):,}")

# First 14 days only
cutoff = df["timestamp"].min() + pd.Timedelta(days=14)
df = df[df["timestamp"] < cutoff]
print(f"  First 14 days: {len(df):,} rows | Anomaly ratio: {df.label.mean()*100:.2f}%")

# Every 4 hours per machine: keep rows where hour % 4 == 0 and minute == 0
df["hour"] = df["timestamp"].dt.hour
df["minute"] = df["timestamp"].dt.minute
sample = df[(df["hour"] % 4 == 0) & (df["minute"] == 0)].reset_index(drop=True)
print(f"  Sampled (every 4h per machine): {len(sample):,} rows "
      f"({sample.label.sum()} anomalous, {(sample.label==0).sum()} normal)")

def call_predict(row):
    payload = {
        "machine": row["machine"],
        "hour": int(row["hour"]),
        "metrics": {c: float(row[c]) for c in FEATURES}
    }
    t0 = time.time()
    try:
        resp = requests.post(f"{BASE_URL}/predict", json=payload, timeout=10)
        latency = time.time() - t0
        if resp.status_code != 200:
            return {"error": resp.status_code, "latency": latency}
        result = resp.json()
        result["latency"] = latency
        result["true_label"] = int(row["label"])
        result["true_cause"] = row.get("anomaly_type", "") or "normal"
        result["day"] = row["timestamp"].date().isoformat()
        return result
    except Exception as e:
        return {"error": str(e), "latency": time.time() - t0}

print(f"\nSending {len(sample):,} requests to LIVE K8s API ({BASE_URL}), 20 workers...")
t_start = time.time()
results = []
with ThreadPoolExecutor(max_workers=20) as executor:
    futures = [executor.submit(call_predict, row) for _, row in sample.iterrows()]
    for i, future in enumerate(as_completed(futures)):
        results.append(future.result())
        if (i + 1) % 2000 == 0:
            elapsed = time.time() - t_start
            rate = (i + 1) / elapsed
            eta = (len(sample) - (i + 1)) / rate
            print(f"  {i+1:,}/{len(sample):,} done  ({rate:.1f} req/s, ETA {eta:.0f}s)...")

total_time = time.time() - t_start
print(f"\nDone in {total_time:.1f}s ({total_time/60:.1f} min) at {len(sample)/total_time:.1f} req/s")

errors = [r for r in results if "error" in r]
ok = [r for r in results if "error" not in r]
print(f"Errors: {len(errors)}/{len(results)}")
if errors[:3]:
    print("Sample errors:", errors[:3])

if ok:
    latencies = sorted([r["latency"] for r in ok])
    print(f"\nLatency (ms): p50={latencies[len(latencies)//2]*1000:.1f}  "
          f"p95={latencies[int(len(latencies)*0.95)]*1000:.1f}  "
          f"max={latencies[-1]*1000:.1f}")

    y_true = [r["true_label"] for r in ok]
    y_pred = [r["is_anomaly"] for r in ok]
    f1 = f1_score(y_true, y_pred)
    prec = precision_score(y_true, y_pred)
    rec = recall_score(y_true, y_pred)
    print(f"\n=== TWO-WEEK LIVE DEMO RESULTS (200 machines, every 4h, 14 days) ===")
    print(f"F1={f1:.3f}  Precision={prec:.3f}  Recall={rec:.3f}")

    print(f"\nPer-cause recall:")
    by_cause = defaultdict(lambda: [0, 0])
    for r in ok:
        if r["true_label"] == 1:
            by_cause[r["true_cause"]][1] += 1
            if r["is_anomaly"] == 1:
                by_cause[r["true_cause"]][0] += 1
    for cause, (hit, total) in sorted(by_cause.items()):
        print(f"  {cause:<18} {hit}/{total} = {hit/total*100:.1f}%")

    tp = [r for r in ok if r["true_label"] == 1 and r["is_anomaly"] == 1]
    correct_cause = sum(1 for r in tp if r.get("likely_cause") == r["true_cause"])
    print(f"\nCause accuracy: {correct_cause}/{len(tp)} = {correct_cause/len(tp)*100:.1f}%")

    print(f"\nDaily breakdown (anomalies detected per day, first 14 days):")
    by_day = defaultdict(lambda: [0, 0])
    for r in ok:
        if r["true_label"] == 1:
            by_day[r["day"]][1] += 1
            if r["is_anomaly"] == 1:
                by_day[r["day"]][0] += 1
    for day, (hit, total) in sorted(by_day.items()):
        print(f"  {day}: {hit}/{total} detected ({hit/total*100:.0f}%)")
