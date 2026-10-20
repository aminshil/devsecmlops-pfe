#!/usr/bin/env python3
"""
Realistic fleet generator (v3). All data is simulated; nothing here is Tunisie Telecom data.

Same fleet, profiles, calendar and output columns as generate_telecom_fleet.py (v2), but router
failures are modelled the way they happen in production networks. Sources are in the README
("Failure model v3").

  * One incident, recorded once. A router incident drives the router's own rows AND every
    dependent's rows from the same schedule. (v2 drew each router twice, so its own label was
    unrelated to the cascade it caused: README, Decision 9b.)
  * Three router incident modes:
      gray           degraded, silent packet loss. The router keeps answering and its counters do
                     not show the drops; its forwarded throughput falls (median traffic during a
                     failure is ~65% of normal, Gill et al. SIGCOMM'11). Dependents lose throughput
                     too, because TCP backs off under loss (bandwidth ~ 1/sqrt(p), Mathis et al.
                     CCR'97), and hold more in-flight work: a little more CPU, RAM and load.
                     Gray failure = differential observability (Huang et al. HotOS'17).
      down           crash or lost uplink. The router and everything behind it go silent at once:
                     the central monitor cannot reach them (reachable=0) and host-local agents record
                     idle machines. Monitoring tools mark the children UNREACHABLE and alert only on
                     the parent (Nagios parent/child reachability).
      control_plane  the router's management CPU spikes but forwarding (data plane) is unaffected,
                     so nothing propagates. A busy router with healthy dependents is normal.
  * Propagation takes time: dependents degrade 30 s to 3 min after the router and recover with a
    short tail (cascades build up over "a couple of minutes", Google SRE book ch. 22). A hard
    failure cuts connectivity within a reading.
  * Impact depends on the machine: chatty or latency-sensitive tiers (web, app, cache, edge, voip)
    are hit harder than batch, and impact scales with current traffic, so the same gray failure
    hurts fewer machines at 3 AM. A dependent is labelled anomalous only if its impact is material:
    deterministic, no coin flip (v2 labelled the same degraded state anomalous 40% of the time).
  * Durations are short-tailed: median 5 minutes, a few up to an hour (most link failures are
    resolved within five minutes, Gill et al. 2011).
  * Rates are compressed: a top-of-rack device fails < 5% per year in production (Gill et al.),
    far too rarely to learn from, so incidents are injected much more often, as fault-injection
    benchmarks do. The incident log written next to the CSV is the ground truth for root cause.

Output columns: the v2 columns (timestamp, machine, type, 6 metrics, label, anomaly_type) plus
root_cause (machine that caused the reading's anomaly, "" if normal) and reachable (0 while the
central monitor cannot reach the machine). The v2 pipeline selects columns by name, so it can
read this file; new anomaly types (router_gray, router_down, cascade_gray, cascade_unreachable)
would become new classes if it were retrained on it.

    python ml-model/generate_telecom_fleet_v3.py --days 30 --seed 42 --output data/telecom_fleet_v3.csv
"""
import argparse
import json
import math
import sys
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import generate_telecom_fleet as v2  # noqa: E402  (profiles, anomaly signatures, calendar, fleet)

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "data" / "telecom_fleet_v3.csv"
GRAPH = ROOT / "models" / "dependency_graph.json"
START = datetime(2026, 1, 1, 0, 0, 0)
STEP_S = 30
INDEPENDENT = {"router", "firewall", "dns"}            # on no router (as in v2 and the graph)

# How hard a degraded upstream hits each tier: chatty and latency-sensitive tiers most, batch least.
SENSITIVITY = {"web": 1.0, "app": 1.0, "cache": 1.0, "edge": 1.0, "db": 0.9, "queue": 0.8,
               "voip": 1.2, "batch": 0.4}
MATERIAL = 0.35          # impact below this is within normal noise: the reading stays labelled 0
MODES = ("gray", "down", "control_plane")
CALM_GAP = 12            # >= 6 min between two incidents of one router (tails, reconnection)


def base_signals(profile_name, profile, n, tf, rng):
    """Normal behaviour, exactly as in v2 (day/night factor, per-metric noise, disk drift)."""
    ((cpu_mu, cpu_sig), (ram_mu, ram_sig), (net_mu, net_sig),
     (dio_mu, dio_sig), (dusage_base, dusage_sig), (load_mu, load_sig), _, _) = profile
    cpu = rng.normal(cpu_mu * tf, cpu_sig).clip(0, 100)
    ram = rng.normal(ram_mu * tf, ram_sig).clip(0, 100)
    net = rng.normal(net_mu * tf, net_sig).clip(0, None)
    dio = rng.normal(dio_mu * tf, dio_sig).clip(0, 100)
    if profile_name in v2.NETWORK_GEAR:
        dusage = np.abs(rng.normal(0, dusage_sig, n)).clip(0, 100)
    else:
        dusage = (dusage_base + np.linspace(0, 5, n) + rng.normal(0, dusage_sig, n)).clip(0, 100)
    load = (load_mu * tf * (0.5 + cpu / (cpu_mu + 1e-9) * 0.5) + rng.normal(0, load_sig, n)).clip(0, None)
    return {"cpu": cpu, "ram": ram, "network": net, "disk_io": dio, "disk_usage": dusage, "load_avg": load}


def inject_local_faults(m, profile_name, profile, n, tf, ratio, labels, atypes, roots, name, rng):
    """The five machine-local causes, as in v2: 5-30 min windows until `ratio` of the timeline."""
    ((cpu_mu, cpu_sig), (ram_mu, ram_sig), (net_mu, net_sig),
     (dio_mu, dio_sig), (_, dusage_sig), (load_mu, load_sig), _, _) = profile
    is_gear = profile_name in v2.NETWORK_GEAR
    target, injected, attempts = int(n * ratio), 0, 0
    while injected < target and attempts < 500:
        attempts += 1
        blen = int(rng.integers(10, 61))
        s = int(rng.integers(0, n - blen))
        if labels[s:s + blen].any():
            continue
        atype = str(rng.choice(list(v2.ANOMALY_TYPES)))
        if is_gear and atype == "disk_saturation":
            atype = str(rng.choice(["cpu_spike", "network_flood", "silent_failure"]))
        k = v2.ANOMALY_TYPES[atype]
        sl = slice(s, s + blen)
        t = tf[sl]
        m["cpu"][sl] = np.clip(cpu_mu * t * k["cpu"] + rng.normal(0, cpu_sig, blen), 0, 100)
        m["ram"][sl] = np.clip(ram_mu * t * k["ram"] + rng.normal(0, ram_sig, blen), 0, 100)
        m["network"][sl] = np.clip(net_mu * t * k["network"] + rng.normal(0, net_sig, blen), 0, None)
        if not is_gear:
            m["disk_io"][sl] = np.clip(dio_mu * t * k["disk_io"] + rng.normal(0, dio_sig, blen), 0, 100)
            m["disk_usage"][sl] = np.clip(m["disk_usage"][sl] * k["disk_usage"]
                                          + rng.normal(0, dusage_sig, blen), 0, 100)
            m["load_avg"][sl] = np.clip(load_mu * t * k["load_avg"] + rng.normal(0, load_sig, blen), 0, None)
        labels[sl], atypes[sl], roots[sl] = 1, atype, name
        injected += blen


def router_incidents(n, ratio, shares, rng):
    """Non-overlapping incidents covering about `ratio` of one router's timeline."""
    target, covered, attempts = int(n * ratio), 0, 0
    busy = np.zeros(n, dtype=bool)
    out = []
    while covered < target and attempts < 20000:
        attempts += 1
        dur = int(np.clip(round(rng.lognormal(math.log(10), 0.8)), 4, 120))   # median 5 min, tail 1 h
        if n - dur <= 0:
            break
        s = int(rng.integers(0, n - dur))
        e = s + dur
        if busy[max(0, s - CALM_GAP):min(n, e + CALM_GAP)].any():
            continue
        busy[s:e] = True
        mode = str(rng.choice(MODES, p=shares))
        out.append({"start": s, "end": e, "mode": mode,
                    "severity": round(float(rng.uniform(0.35, 1.0)), 3) if mode == "gray" else 1.0})
        covered += dur
    return sorted(out, key=lambda x: x["start"])


def apply_router_own(m, incidents, labels, atypes, roots, reach, name, rng):
    """What the router's own monitoring records during each of its incidents."""
    for inc in incidents:
        sl = slice(inc["start"], inc["end"])
        k = inc["end"] - inc["start"]
        if inc["mode"] == "gray":
            sev = inc["severity"]
            m["network"][sl] *= (1 - 0.5 * sev) * rng.normal(1, 0.03, k)   # forwarded traffic falls
            m["cpu"][sl] = np.clip(m["cpu"][sl] * (1 + 0.2 * sev), 0, 100)
            atypes[sl] = "router_gray"
        elif inc["mode"] == "down":
            m["network"][sl] = np.abs(rng.normal(0, 2.0, k))                 # nothing forwarded
            m["cpu"][sl] *= 0.6
            reach[sl] = 0
            atypes[sl] = "router_down"
        else:                                                                 # control plane only
            m["cpu"][sl] = np.clip(m["cpu"][sl] * 2.4 + rng.normal(0, 2, k), 0, 100)
            atypes[sl] = "cpu_spike"
        labels[sl], roots[sl] = 1, name


def apply_dependent(m, profile_name, n, tf, incidents, router, labels, atypes, roots, reach, rng):
    """What a dependent records while its router is degraded or down (control-plane events do not
    reach it). Machine-local faults already in progress keep their own label and signature."""
    is_gear = profile_name in v2.NETWORK_GEAR
    sens = SENSITIVITY.get(profile_name, 1.0)
    for inc in incidents:
        if inc["mode"] == "control_plane":
            continue
        gray = inc["mode"] == "gray"
        lag = int(rng.integers(1, 7)) if gray else int(rng.integers(0, 2))    # 30 s - 3 min / instant
        ramp = int(rng.integers(2, 5)) if gray else 1
        tail = int(rng.integers(1, 4)) if gray else int(rng.integers(0, 3))
        a, b = inc["start"] + lag, min(n, inc["end"] + tail)
        for i in range(a, b):
            if labels[i]:
                if not gray:
                    reach[i] = 0                     # a local fault behind a dead router is unreachable too
                continue
            r = min(1.0, (i - a + 1) / ramp)
            if i >= inc["end"]:
                r *= (b - i) / (tail + 1)            # recovery tail
            if gray:
                imp = inc["severity"] * sens * tf[i] * r      # less traffic, fewer losses to suffer
                m["network"][i] *= 1 - 0.45 * imp             # TCP backs off under loss
                m["cpu"][i] = min(100.0, m["cpu"][i] * (1 + 0.2 * imp))   # retries, timeouts
                if not is_gear:
                    m["ram"][i] = min(100.0, m["ram"][i] * (1 + 0.1 * imp))   # in-flight work queues
                    m["load_avg"][i] *= 1 + 0.25 * imp                    # busier workers
                    m["disk_io"][i] *= 1 - 0.15 * imp                     # fewer requests completed
                if imp >= MATERIAL:
                    labels[i], atypes[i], roots[i] = 1, "cascade_gray", router
            else:
                m["network"][i] = abs(float(rng.normal(0, 0.5)))          # cut off
                m["cpu"][i] *= 0.25
                if not is_gear:
                    m["load_avg"][i] *= 0.3
                    m["disk_io"][i] *= 0.3
                reach[i] = 0
                labels[i], atypes[i], roots[i] = 1, "cascade_unreachable", router


def topology(fleet):
    """machine -> router, with the same rule v2 uses (it is what models/dependency_graph.json holds)."""
    routers = [n for n, p, _ in fleet if p == "router"]
    dep = {}
    for idx, (n, p, _) in enumerate(fleet):
        if p not in INDEPENDENT and routers:
            dep[n] = routers[idx % len(routers)]
    return routers, dep


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--machines", type=int, default=200)
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--anomaly-ratio", type=float, default=0.05, help="machine-local faults, share of time")
    ap.add_argument("--router-fault-ratio", type=float, default=0.03,
                    help="share of each router's timeline spent in an incident")
    ap.add_argument("--gray-share", type=float, default=0.55)
    ap.add_argument("--down-share", type=float, default=0.30,
                    help="the rest of the incidents are control-plane only")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--output", type=Path, default=OUTPUT)
    args = ap.parse_args()

    ctrl = 1.0 - args.gray_share - args.down_share
    if ctrl < 0:
        ap.error("--gray-share + --down-share must not exceed 1")
    shares = [args.gray_share, args.down_share, ctrl]
    n = args.days * 24 * 60 * 2
    fleet = v2.build_fleet(args.machines)
    routers, depends_on = topology(fleet)
    if GRAPH.exists() and args.machines == 200:
        g = json.loads(GRAPH.read_text())
        if g.get("depends_on") != depends_on:
            print("WARNING: topology differs from models/dependency_graph.json", file=sys.stderr)

    times = [START + timedelta(seconds=STEP_S * i) for i in range(n)]
    stamps = np.array([str(t) for t in times], dtype=object)
    hours = np.array([t.hour for t in times])
    tf_cache = {}

    seeds = np.random.SeedSequence(args.seed).spawn(len(fleet) + len(routers) + 1)
    sched_rng = np.random.default_rng(seeds[-1])
    incidents = {r: router_incidents(n, args.router_fault_ratio, shares, sched_rng) for r in routers}

    out = args.output
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        out.unlink()
    totals, by_type = {"rows": 0, "anom": 0}, {}
    print(f"generating {len(fleet)} machines x {n:,} readings (30 s, {args.days} days) -> {out}")
    for idx, (name, pname, profile) in enumerate(fleet):
        rng = np.random.default_rng(seeds[idx])
        if pname not in tf_cache:
            tf_cache[pname] = np.array([v2.time_factor(h, profile[6]) for h in hours])
        tf = tf_cache[pname]
        m = base_signals(pname, profile, n, tf, rng)
        labels = np.zeros(n, dtype=np.int8)
        atypes = np.full(n, "", dtype=object)
        roots = np.full(n, "", dtype=object)
        reach = np.ones(n, dtype=np.int8)
        if pname == "router":
            apply_router_own(m, incidents[name], labels, atypes, roots, reach, name, rng)
        else:
            inject_local_faults(m, pname, profile, n, tf, args.anomaly_ratio, labels, atypes, roots, name, rng)
            if name in depends_on:
                apply_dependent(m, pname, n, tf, incidents[depends_on[name]], depends_on[name],
                                labels, atypes, roots, reach, rng)
        df = pd.DataFrame({"timestamp": stamps, "machine": name, "type": pname,
                           **{k: np.round(m[k], 3) for k in v2.METRICS},
                           "label": labels, "anomaly_type": atypes,
                           "root_cause": roots, "reachable": reach})
        df.to_csv(out, mode="a", header=(idx == 0), index=False)
        totals["rows"] += n
        totals["anom"] += int(labels.sum())
        for t, c in zip(*np.unique(atypes[labels == 1], return_counts=True)):
            by_type[t] = by_type.get(t, 0) + int(c)
        if (idx + 1) % 20 == 0:
            print(f"  {idx + 1}/{len(fleet)} machines", flush=True)

    log = {"generator": "generate_telecom_fleet_v3.py", "seed": args.seed, "start": str(START),
           "step_seconds": STEP_S, "readings_per_machine": n,
           "params": {k: getattr(args, k) for k in ("machines", "days", "anomaly_ratio",
                                                     "router_fault_ratio", "gray_share", "down_share")},
           "depends_on": depends_on,
           "incidents": {r: [{**i, "start_ts": stamps[i["start"]],
                              "end_ts": stamps[min(i["end"], n - 1)]} for i in incs]
                         for r, incs in incidents.items()}}
    log_path = out.with_suffix(".incidents.json")
    log_path.write_text(json.dumps(log, indent=1))

    print(f"\nrows {totals['rows']:,}   anomaly rate {totals['anom'] / totals['rows']:.4f}")
    for t in sorted(by_type, key=lambda x: -by_type[x]):
        print(f"  {t:<20} {by_type[t]:>10,}")
    modes = [i["mode"] for incs in incidents.values() for i in incs]
    print(f"router incidents: {len(modes)}  "
          + "  ".join(f"{md} {modes.count(md)}" for md in MODES))
    print(f"wrote {out}\nwrote {log_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
