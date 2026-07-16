"""
Tunisie Telecom synthetic fleet generator (6 features, equipment-faithful).

Servers (web/app/db/cache/queue/batch/edge) expose all 6 metrics via Node Exporter.
Network appliances (router/firewall/dns/voip) are SNMP-monitored and have NO disk,
so disk_io / disk_usage / load_avg are near-zero for them — matching real hardware.

  - 11 machine types, 200 machines, day/night patterns
  - 5 anomaly types: cpu_spike, memory_leak, network_flood, disk_saturation, silent_failure
  - Correlated anomalies: router failure -> downstream machines stressed
  - 6 features: cpu (%), ram (%), network (MB/s), disk_io (%), disk_usage (%), load_avg
  - Output: timestamp, machine, type, cpu, ram, network, disk_io, disk_usage, load_avg, label
"""
import argparse
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "data" / "telecom_fleet.csv"

# (cpu),(ram),(net),(disk_io),(disk_usage_base),(load_avg), night_factor, fleet_weight
# Network gear (router/firewall/dns/voip): disk_io/disk_usage/load_avg ~0 (no disk, SNMP-only)
PROFILES = {
    "web":      ((30,  6), (50,  6), ( 80,  15), (25,  6), (40,  3), (1.2, 0.4), 0.45, 40),
    "app":      ((50,  7), (65,  7), (200,  25), (35,  7), (50,  3), (2.0, 0.5), 0.50, 35),
    "db":       ((75,  8), (80,  5), (120,  20), (80,  8), (70,  4), (4.5, 0.9), 0.60, 30),
    "cache":    ((20,  4), (88,  4), (150,  30), (30,  6), (55,  3), (0.9, 0.3), 0.40, 20),
    "queue":    ((35,  6), (55,  7), (300,  50), (55,  8), (60,  4), (1.6, 0.5), 0.35, 20),
    "batch":    ((65, 12), (60,  8), ( 90,  15), (75, 10), (65,  5), (3.8, 1.0), 0.70, 15),
    "edge":     ((25,  5), (45,  6), (250,  40), (20,  5), (35,  3), (1.0, 0.3), 0.30, 15),
    "router":   ((15,  3), (30,  4), (500,  80), (0.0, 0.3), (0.0, 0.2), (0.0, 0.05), 0.55,  8),
    "firewall": ((20,  4), (40,  5), (350,  60), (0.0, 0.3), (0.0, 0.2), (0.0, 0.05), 0.55,  7),
    "dns":      ((10,  2), (25,  3), (200,  35), (0.0, 0.3), (0.0, 0.2), (0.0, 0.05), 0.45,  5),
    "voip":     ((18,  4), (35,  5), (180,  30), (0.0, 0.3), (0.0, 0.2), (0.0, 0.05), 0.40,  5),
}

METRICS = ["cpu", "ram", "network", "disk_io", "disk_usage", "load_avg"]
NETWORK_GEAR = {"router", "firewall", "dns", "voip"}

ANOMALY_TYPES = {
    "cpu_spike":       {"cpu": 2.5, "ram": 1.1, "network": 1.0,
                        "disk_io": 1.3, "disk_usage": 1.0, "load_avg": 3.0},
    "memory_leak":     {"cpu": 1.3, "ram": 1.8, "network": 1.0,
                        "disk_io": 1.2, "disk_usage": 1.05, "load_avg": 1.6},
    "network_flood":   {"cpu": 1.2, "ram": 1.1, "network": 5.0,
                        "disk_io": 1.1, "disk_usage": 1.0, "load_avg": 1.8},
    "disk_saturation": {"cpu": 1.4, "ram": 1.2, "network": 1.1,
                        "disk_io": 3.5, "disk_usage": 1.25, "load_avg": 2.5},
    "silent_failure":  {"cpu": 0.1, "ram": 0.9, "network": 0.05,
                        "disk_io": 0.1, "disk_usage": 1.0, "load_avg": 0.1},
}


def time_factor(hour: int, night_factor: float) -> float:
    if 8 <= hour < 20:
        if hour < 10:
            return 0.6 + 0.4 * (hour - 8) / 2
        elif hour < 17:
            return 1.0
        else:
            return 1.0 - 0.5 * (hour - 17) / 3
    return night_factor


def generate_machine(name, profile_name, profile, n_minutes,
                     anomaly_ratio, rng, correlated_mask=None):
    ((cpu_mu, cpu_sig), (ram_mu, ram_sig), (net_mu, net_sig),
     (dio_mu, dio_sig), (dusage_base, dusage_sig), (load_mu, load_sig),
     night_factor, _) = profile

    is_net_gear = profile_name in NETWORK_GEAR

    start = datetime(2026, 1, 1, 0, 0, 0)
    timestamps = [start + timedelta(seconds=30*i) for i in range(n_minutes)]
    hours = np.array([t.hour for t in timestamps])
    tf = np.array([time_factor(h, night_factor) for h in hours])

    cpu = rng.normal(cpu_mu * tf, cpu_sig).clip(0, 100)
    ram = rng.normal(ram_mu * tf, ram_sig).clip(0, 100)
    net = rng.normal(net_mu * tf, net_sig).clip(0, None)
    dio = rng.normal(dio_mu * tf, dio_sig).clip(0, 100)

    if is_net_gear:
        # No disk on network appliances: flat near-zero, no drift
        dusage = np.abs(rng.normal(0, dusage_sig, n_minutes)).clip(0, 100)
    else:
        drift = np.linspace(0, 5, n_minutes)
        dusage = (dusage_base + drift + rng.normal(0, dusage_sig, n_minutes)).clip(0, 100)

    load = (load_mu * tf * (0.5 + cpu / (cpu_mu + 1e-9) * 0.5)
            + rng.normal(0, load_sig, n_minutes)).clip(0, None)

    labels = np.zeros(n_minutes, dtype=int)
    atypes = np.array([""] * n_minutes, dtype=object)

    target = int(n_minutes * anomaly_ratio)
    injected = attempts = 0
    while injected < target and attempts < 500:
        attempts += 1
        blen = int(rng.integers(10, 61))
        sidx = int(rng.integers(0, n_minutes - blen))
        if labels[sidx:sidx + blen].any():
            continue
        atype = rng.choice(list(ANOMALY_TYPES.keys()))
        # Network gear can't have disk_saturation — reroll to a network-relevant one
        if is_net_gear and atype == "disk_saturation":
            atype = rng.choice(["cpu_spike", "network_flood", "silent_failure"])
        m = ANOMALY_TYPES[atype]
        for i in range(sidx, sidx + blen):
            cpu[i] = float(np.clip(cpu_mu * tf[i] * m["cpu"]     + rng.normal(0, cpu_sig), 0, 100))
            ram[i] = float(np.clip(ram_mu * tf[i] * m["ram"]     + rng.normal(0, ram_sig), 0, 100))
            net[i] = float(np.clip(net_mu * tf[i] * m["network"] + rng.normal(0, net_sig), 0, None))
            if not is_net_gear:
                dio[i]    = float(np.clip(dio_mu * tf[i] * m["disk_io"] + rng.normal(0, dio_sig), 0, 100))
                dusage[i] = float(np.clip(dusage[i] * m["disk_usage"]   + rng.normal(0, dusage_sig), 0, 100))
                load[i]   = float(np.clip(load_mu * tf[i] * m["load_avg"] + rng.normal(0, load_sig), 0, None))
        labels[sidx:sidx + blen] = 1
        atypes[sidx:sidx + blen] = atype
        injected += blen

    if correlated_mask is not None:
        for i in np.nonzero(correlated_mask)[0]:
            if labels[i] == 0:
                net[i] = float(np.clip(net[i] * 2.0 + rng.normal(0, net_sig * 2), 0, None))
                if not is_net_gear:
                    load[i] = float(np.clip(load[i] * 1.5 + rng.normal(0, load_sig), 0, None))
                if rng.random() < 0.4:
                    labels[i] = 1
                    atypes[i] = "cascade"

    return pd.DataFrame({
        "timestamp":  [str(t) for t in timestamps],
        "machine":    name,
        "type":       profile_name,
        "cpu":        np.round(cpu, 3),
        "ram":        np.round(ram, 3),
        "network":    np.round(net, 3),
        "disk_io":    np.round(dio, 3),
        "disk_usage": np.round(dusage, 3),
        "load_avg":   np.round(load, 3),
        "label":      labels,
        "anomaly_type": atypes,
    })


def build_fleet(n_machines):
    total_w = sum(p[7] for p in PROFILES.values())
    fleet, counters = [], dict.fromkeys(PROFILES, 0)
    for pname, profile in PROFILES.items():
        count = max(1, round(n_machines * profile[7] / total_w))
        for _ in range(count):
            if len(fleet) >= n_machines:
                break
            counters[pname] += 1
            fleet.append((f"{pname}-{counters[pname]:02d}", pname, profile))
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
    n_minutes = args.days * 24 * 60 * 2   # 2x steps: 30s resolution over same time span
    fleet = build_fleet(args.machines)

    type_counts = {}
    for _, pname, _ in fleet:
        type_counts[pname] = type_counts.get(pname, 0) + 1

    print(f"\n{'='*55}")
    print(f"  Tunisie Telecom Fleet Generator (6 features)")
    print(f"{'='*55}")
    print(f"  Machines : {len(fleet)}")
    print(f"  Days     : {args.days}  ({n_minutes:,} min/machine)")
    print(f"  Features : {', '.join(METRICS)}")
    print(f"  Network gear (router/firewall/dns/voip): no disk (SNMP-only)")
    for pname in PROFILES:
        if pname in type_counts:
            print(f"    {pname:<12} {type_counts[pname]:>3} machines")

    router_masks = {}
    for name, pname, profile in fleet:
        if pname == "router":
            tmp = generate_machine(name, pname, profile, n_minutes, args.anomaly_ratio, rng)
            router_masks[name] = tmp["label"].values

    print(f"\n  Generating", end="", flush=True)
    frames, router_names = [], list(router_masks.keys())
    for idx, (name, pname, profile) in enumerate(fleet):
        corr = None
        if pname not in ("router", "firewall", "dns") and router_names:
            corr = router_masks[router_names[idx % len(router_names)]]
        frames.append(generate_machine(name, pname, profile, n_minutes,
                                        args.anomaly_ratio, rng, corr))
        if (idx + 1) % 20 == 0:
            print(".", end="", flush=True)
    print(" done")

    df = pd.concat(frames, ignore_index=True)

    print(f"\n  Total rows    : {len(df):,}")
    print(f"  Machines      : {df['machine'].nunique()}")
    print(f"  Anomaly ratio : {df['label'].mean()*100:.2f}%")
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
