"""
Tunisie Telecom synthetic fleet generator.
Produces a realistic multi-machine CSV with:
  - 11 machine types (web, app, db, cache, queue, batch, edge,
                       router, firewall, dns, voip)
  - 200 machines by default
  - Real day/night load patterns (busy 08h-20h, quiet 20h-08h)
  - 4 anomaly types: cpu_spike, memory_leak, network_flood, silent_failure
  - Correlated anomalies: router failure -> downstream machines also stressed
  - 3 features: cpu (%), ram (%), network (MB/s)
  - Output columns: timestamp, machine, type, cpu, ram, network, label

Compatible with:
  - ml-model/preprocess.py  (build_baselines / apply_zscore)
  - api/app.py              (serving model via MODEL_NAME=telecom)
  - ml-model/benchmark.py  (--data data/telecom_fleet.csv)

Usage:
    python ml-model/generate_telecom_fleet.py
    python ml-model/generate_telecom_fleet.py --machines 200 --days 30
    python ml-model/generate_telecom_fleet.py --machines 50  --days 7  --seed 99
"""
import argparse
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "data" / "telecom_fleet.csv"

# ------------------------------------------------------------------
# Machine profiles
# Each entry: (cpu_mu, cpu_sig), (ram_mu, ram_sig), (net_mu, net_sig),
#              night_factor, fleet_weight
# night_factor: fraction of daytime load applied between 20h-08h
# fleet_weight: proportional share of the total machine count
# ------------------------------------------------------------------
PROFILES = {
    "web":      ((30,  6), (50,  6), ( 80,  15), 0.45, 40),
    "app":      ((50,  7), (65,  7), (200,  25), 0.50, 35),
    "db":       ((75,  8), (80,  5), (120,  20), 0.60, 30),
    "cache":    ((20,  4), (88,  4), (150,  30), 0.40, 20),
    "queue":    ((35,  6), (55,  7), (300,  50), 0.35, 20),
    "batch":    ((65, 12), (60,  8), ( 90,  15), 0.70, 15),
    "edge":     ((25,  5), (45,  6), (250,  40), 0.30, 15),
    "router":   ((15,  3), (30,  4), (500,  80), 0.55,  8),
    "firewall": ((20,  4), (40,  5), (350,  60), 0.55,  7),
    "dns":      ((10,  2), (25,  3), (200,  35), 0.45,  5),
    "voip":     ((18,  4), (35,  5), (180,  30), 0.40,  5),
}

METRICS = ["cpu", "ram", "network"]

# Anomaly type multipliers applied to the daytime baseline
ANOMALY_TYPES = {
    "cpu_spike":      {"cpu": 2.5, "ram": 1.1, "network": 1.0},
    "memory_leak":    {"cpu": 1.3, "ram": 1.8, "network": 1.0},
    "network_flood":  {"cpu": 1.2, "ram": 1.1, "network": 5.0},
    "silent_failure": {"cpu": 0.1, "ram": 0.9, "network": 0.05},
}


def time_factor(hour: int, night_factor: float) -> float:
    """Return load multiplier for a given hour (0-23)."""
    if 8 <= hour < 20:
        if hour < 10:                        # ramp up  08h-10h
            return 0.6 + 0.4 * (hour - 8) / 2
        elif hour < 17:                      # peak     10h-17h
            return 1.0
        else:                                # ramp down 17h-20h
            return 1.0 - 0.5 * (hour - 17) / 3
    return night_factor                      # quiet    20h-08h


def generate_machine(
    name: str,
    profile_name: str,
    profile: tuple,
    n_minutes: int,
    anomaly_ratio: float,
    rng: np.random.Generator,
    correlated_mask: np.ndarray | None = None,
) -> pd.DataFrame:
    """Generate one machine time-series with injected anomaly blocks."""
    (cpu_mu, cpu_sig), (ram_mu, ram_sig), (net_mu, net_sig), night_factor, _ = profile

    start = datetime(2026, 1, 1, 0, 0, 0)
    timestamps = [start + timedelta(minutes=i) for i in range(n_minutes)]
    hours = np.array([t.hour for t in timestamps])
    tf = np.array([time_factor(h, night_factor) for h in hours])

    # Normal baseline with day/night variation
    cpu = rng.normal(cpu_mu * tf, cpu_sig).clip(0, 100)
    ram = rng.normal(ram_mu * tf, ram_sig).clip(0, 100)
    net = rng.normal(net_mu * tf, net_sig).clip(0, None)
    labels = np.zeros(n_minutes, dtype=int)

    # Inject anomaly blocks (10-60 min each, no overlap)
    target = int(n_minutes * anomaly_ratio)
    injected = 0
    attempts = 0
    while injected < target and attempts < 500:
        attempts += 1
        blen = int(rng.integers(10, 61))
        sidx = int(rng.integers(0, n_minutes - blen))
        if labels[sidx:sidx + blen].any():
            continue
        atype = rng.choice(list(ANOMALY_TYPES.keys()))
        m = ANOMALY_TYPES[atype]
        for i in range(sidx, sidx + blen):
            cpu[i] = float(np.clip(cpu_mu * tf[i] * m["cpu"]     + rng.normal(0, cpu_sig), 0, 100))
            ram[i] = float(np.clip(ram_mu * tf[i] * m["ram"]     + rng.normal(0, ram_sig), 0, 100))
            net[i] = float(np.clip(net_mu * tf[i] * m["network"] + rng.normal(0, net_sig), 0, None))
        labels[sidx:sidx + blen] = 1
        injected += blen

    # Correlated stress from upstream router failure
    if correlated_mask is not None:
        for i in np.where(correlated_mask)[0]:
            if labels[i] == 0:
                net[i] = float(np.clip(net[i] * 2.0 + rng.normal(0, net_sig * 2), 0, None))
                if rng.random() < 0.4:          # 40% chance to become a true anomaly
                    labels[i] = 1

    return pd.DataFrame({
        "timestamp": [str(t) for t in timestamps],
        "machine":   name,
        "type":      profile_name,
        "cpu":       np.round(cpu, 3),
        "ram":       np.round(ram, 3),
        "network":   np.round(net, 3),
        "label":     labels,
    })


def build_fleet(n_machines: int) -> list[tuple]:
    """Distribute n_machines proportionally across profiles."""
    total_w = sum(p[4] for p in PROFILES.values())
    fleet, counters = [], {k: 0 for k in PROFILES}
    for pname, profile in PROFILES.items():
        count = max(1, round(n_machines * profile[4] / total_w))
        for _ in range(count):
            if len(fleet) >= n_machines:
                break
            counters[pname] += 1
            fleet.append((f"{pname}-{counters[pname]:02d}", pname, profile))
    # Fill remainder with 'web' if rounding left us short
    while len(fleet) < n_machines:
        counters["web"] += 1
        fleet.append((f"web-{counters['web']:02d}", "web", PROFILES["web"]))
    return fleet[:n_machines]


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--machines",      type=int,   default=200)
    ap.add_argument("--days",          type=int,   default=30)
    ap.add_argument("--anomaly-ratio", type=float, default=0.05)
    ap.add_argument("--seed",          type=int,   default=42)
    ap.add_argument("--output",        type=Path,  default=OUTPUT)
    args = ap.parse_args()

    rng = np.random.default_rng(args.seed)
    n_minutes = args.days * 24 * 60
    fleet = build_fleet(args.machines)

    # Count by type
    type_counts: dict[str, int] = {}
    for _, pname, _ in fleet:
        type_counts[pname] = type_counts.get(pname, 0) + 1

    print(f"\n{'='*55}")
    print(f"  Tunisie Telecom Fleet Generator")
    print(f"{'='*55}")
    print(f"  Machines : {len(fleet)}")
    print(f"  Days     : {args.days}  ({n_minutes:,} min/machine)")
    print(f"  Features : cpu, ram, network  (day/night pattern)")
    print(f"  Anomalies: cpu_spike · memory_leak · "
          f"network_flood · silent_failure")
    print(f"  Fleet breakdown:")
    for pname in PROFILES:
        if pname in type_counts:
            print(f"    {pname:<12} {type_counts[pname]:>3} machines")

    # Pre-generate router anomaly masks for downstream correlation
    router_masks: dict[str, np.ndarray] = {}
    for name, pname, profile in fleet:
        if pname == "router":
            tmp = generate_machine(name, pname, profile, n_minutes,
                                   args.anomaly_ratio, rng)
            router_masks[name] = tmp["label"].values

    # Generate all machines
    print(f"\n  Generating", end="", flush=True)
    frames, router_names = [], list(router_masks.keys())
    for idx, (name, pname, profile) in enumerate(fleet):
        corr = None
        if pname not in ("router", "firewall", "dns") and router_names:
            corr = router_masks[router_names[idx % len(router_names)]]
        frames.append(
            generate_machine(name, pname, profile, n_minutes,
                             args.anomaly_ratio, rng, corr)
        )
        if (idx + 1) % 20 == 0:
            print(".", end="", flush=True)
    print(" done")

    df = pd.concat(frames, ignore_index=True)

    print(f"\n  Results:")
    print(f"    Total rows    : {len(df):,}")
    print(f"    Machines      : {df['machine'].nunique()}")
    print(f"    Anomaly ratio : {df['label'].mean()*100:.2f}%")
    print(f"    Date range    : {df['timestamp'].min()[:10]} "
          f"→ {df['timestamp'].max()[:10]}")
    print(f"\n  Anomaly ratio by machine type:")
    for t in sorted(df["type"].unique()):
        r = df[df["type"] == t]["label"].mean()
        n = df[df["type"] == t]["machine"].nunique()
        print(f"    {t:<12} {r*100:.2f}%  ({n} machines)")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(args.output, index=False)
    size_mb = args.output.stat().st_size / 1_048_576
    print(f"\n  Saved : {args.output}  ({size_mb:.1f} MB)")
    print(f"{'='*55}\n")


if __name__ == "__main__":
    main()
