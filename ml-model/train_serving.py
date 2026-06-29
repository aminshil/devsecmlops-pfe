"""
Dynamic serving artifact generator — per-machine z-score only.

We tested adding machine-type one-hot and time features on top of z-score
(see benchmark with --time, and the test serving model). Measured result:
type+time degraded the detection signal because Isolation Forest spent its
splits on the easy type one-hot columns instead of the z-score values, and
the per-machine baseline already implicitly encodes machine type. The shipped
model therefore uses per-machine z-score alone — the measured-best config.

Outputs:
  models/serving_model.pkl
  models/serving_baselines.json
"""
import argparse
import sys
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest
from sklearn.metrics import f1_score
from sklearn.model_selection import train_test_split

sys.path.insert(0, str(Path(__file__).resolve().parent))
from preprocess import build_baselines, apply_zscore, save_baselines

ROOT = Path(__file__).resolve().parent.parent
MODELS = ROOT / "models"

PROFILES = {
    "web":   {"cpu": (30, 5),  "ram": (50, 5),  "network": (80,  15), "desc": "Web server, idles low"},
    "app":   {"cpu": (50, 6),  "ram": (65, 6),  "network": (200, 25), "desc": "Application, mid-load"},
    "db":    {"cpu": (75, 7),  "ram": (80, 5),  "network": (120, 20), "desc": "Database, runs hot"},
    "cache": {"cpu": (20, 4),  "ram": (88, 4),  "network": (150, 30), "desc": "Cache, RAM-heavy"},
    "queue": {"cpu": (35, 6),  "ram": (55, 7),  "network": (300, 50), "desc": "Message queue, bursty"},
    "batch": {"cpu": (65, 12), "ram": (60, 8),  "network": (90,  15), "desc": "Batch worker, spiky"},
    "edge":  {"cpu": (25, 5),  "ram": (45, 6),  "network": (250, 40), "desc": "Edge node, low compute"},
}
METRICS = ["cpu", "ram", "network"]


def list_profiles():
    print("Available machine profiles:\n")
    for name, p in PROFILES.items():
        print(f"  {name:<8} cpu={p['cpu'][0]:>3}+-{p['cpu'][1]:<2}  "
              f"ram={p['ram'][0]:>3}+-{p['ram'][1]:<2}  "
              f"net={p['network'][0]:>3}+-{p['network'][1]:<2}  {p['desc']}")


def generate_machine(name, profile, n_rows, anom_ratio, rng):
    n_an = int(n_rows * anom_ratio); n_no = n_rows - n_an
    cmu, csig = profile["cpu"]; rmu, rsig = profile["ram"]; nmu, nsig = profile["network"]
    cpu = np.concatenate([rng.normal(cmu, csig, n_no),
                          rng.normal(cmu + 25, max(csig * 0.6, 3), n_an)]).clip(0, 100)
    ram = np.concatenate([rng.normal(rmu, rsig, n_no),
                          rng.normal(min(rmu + 18, 98), max(rsig * 0.6, 2), n_an)]).clip(0, 100)
    net = np.concatenate([rng.normal(nmu, nsig, n_no),
                          rng.normal(nmu * 3, nsig * 2, n_an)]).clip(0, None)
    lab = np.concatenate([np.zeros(n_no, int), np.ones(n_an, int)])
    idx = rng.permutation(n_rows)
    return pd.DataFrame({"machine": name, "cpu": cpu[idx], "ram": ram[idx],
                         "network": net[idx], "label": lab[idx]})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n-machines", type=int, default=30)
    ap.add_argument("--rows-per-machine", type=int, default=1500)
    ap.add_argument("--anomaly-ratio", type=float, default=0.05)
    ap.add_argument("--profiles", default="all")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--list-profiles", action="store_true")
    args = ap.parse_args()

    if args.list_profiles:
        list_profiles(); return

    pool = (list(PROFILES) if args.profiles == "all"
            else [p.strip() for p in args.profiles.split(",")])
    for p in pool:
        if p not in PROFILES:
            raise SystemExit(f"Unknown profile {p!r}.")

    rng = np.random.default_rng(args.seed)
    counters, frames, fleet = {p: 0 for p in pool}, [], []
    for i in range(args.n_machines):
        profile_name = pool[i % len(pool)]
        counters[profile_name] += 1
        machine_name = f"{profile_name}-{counters[profile_name]:02d}"
        fleet.append((machine_name, profile_name))
        frames.append(generate_machine(machine_name, PROFILES[profile_name],
                                       args.rows_per_machine, args.anomaly_ratio, rng))
    df = pd.concat(frames, ignore_index=True)

    print(f"\nGenerated fleet: {args.n_machines} machines, {len(df):,} rows")
    print(f"Profile counts: {pd.Series([p for _, p in fleet]).value_counts().to_dict()}\n")

    df_tr, df_te = train_test_split(df, test_size=0.3, random_state=args.seed,
                                    stratify=df["label"])

    baselines = build_baselines(df_tr, METRICS)
    model = IsolationForest(contamination=args.anomaly_ratio, n_estimators=100,
                            random_state=args.seed, n_jobs=-1)
    model.fit(apply_zscore(df_tr, baselines, METRICS))

    pred = (model.predict(apply_zscore(df_te, baselines, METRICS)) == -1).astype(int)
    print(f"Serving model F1 (test): {f1_score(df_te['label'], pred):.3f}")

    MODELS.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, MODELS / "serving_model.pkl")
    save_baselines(baselines, MODELS / "serving_baselines.json")

    print(f"\nFleet sample (first 6):")
    for name, profile in fleet[:6]:
        mu = baselines[name]
        print(f"  {name:<10} ({profile:<6})  cpu mu={mu['cpu'][0]:>5.1f}  "
              f"ram mu={mu['ram'][0]:>5.1f}  net mu={mu['network'][0]:>6.1f}")
    if len(fleet) > 6:
        print(f"  ... and {len(fleet) - 6} more")
    print(f"\nSaved: {MODELS / 'serving_model.pkl'}")
    print(f"Saved: {MODELS / 'serving_baselines.json'}")


if __name__ == "__main__":
    main()
