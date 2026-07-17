"""
Tunisie Telecom Synthetic Fleet Generator v2

Designed for:
- Isolation Forest
- Autoencoder
- LSTM Autoencoder

Improvements:
- gradual failures
- realistic telecom incidents
- temporal degradation
- response time
- packet loss
- equipment-aware behavior

Output:
data/telecom_fleet_v2.csv
"""

import argparse
from pathlib import Path
from datetime import datetime, timedelta

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "data" / "telecom_fleet_v2.csv"


PROFILES = {

    # cpu, ram, network, disk_io, disk_usage, load, latency
    "web":
        (35, 55, 100, 30, 40, 1.2, 40),

    "app":
        (50, 65, 220, 40, 50, 2.0, 50),

    "db":
        (70, 80, 130, 75, 70, 4.5, 70),

    "cache":
        (25, 85, 180, 30, 55, 1.0, 30),

    "queue":
        (40, 60, 300, 50, 60, 1.8, 60),

    "batch":
        (65, 65, 100, 80, 65, 3.5, 80),

    "edge":
        (30, 45, 250, 20, 35, 1.0, 35),

    "router":
        (20, 35, 500, 0, 0, 0, 20),

    "firewall":
        (25, 40, 350, 0, 0, 0, 25),

    "dns":
        (15, 30, 200, 0, 0, 0, 15),

    "voip":
        (20, 35, 180, 0, 0, 0, 20),
}


NETWORK_DEVICES = {
    "router",
    "firewall",
    "dns",
    "voip"
}


ANOMALIES = [
    "cpu_spike",
    "memory_leak",
    "network_flood",
    "disk_saturation",
    "silent_failure"
]


def workload(hour):

    if 8 <= hour <= 18:
        return 1.0

    if 6 <= hour < 8:
        return 0.7

    return 0.45



def inject_anomaly(
        df,
        start,
        length,
        anomaly,
        rng
):

    end = min(
        start + length,
        len(df)
    )

    indexes = np.arange(start, end)


    progress = np.linspace(
        0,
        1,
        len(indexes)
    )


    if anomaly == "cpu_spike":

        increase = (
            20 +
            45 * progress
        )

        df.loc[indexes, "cpu"] += increase


        df.loc[indexes, "load_avg"] *= (
            1 + progress * 2
        )


    elif anomaly == "memory_leak":

        increase = (
            5 +
            45 * progress
        )

        df.loc[indexes, "ram"] += increase


        df.loc[indexes, "load_avg"] *= (
            1 + progress
        )


    elif anomaly == "network_flood":

        multiplier = (
            1 +
            6 * progress
        )

        df.loc[indexes, "network"] *= multiplier


        df.loc[indexes, "response_time"] *= (
            1 + progress * 3
        )


        df.loc[indexes, "packet_loss"] += (
            progress * 20
        )


    elif anomaly == "disk_saturation":

        df.loc[indexes, "disk_io"] += (
            20 +
            60 * progress
        )


        df.loc[indexes, "disk_usage"] += (
            5 +
            20 * progress
        )


        df.loc[indexes, "response_time"] *= (
            1 + progress * 2
        )


    elif anomaly == "silent_failure":

        df.loc[indexes, "cpu"] *= (
            1-progress*0.8
        )

        df.loc[indexes, "network"] *= (
            1-progress*0.95
        )

        df.loc[indexes, "response_time"] *= (
            1 + progress*20
        )

        df.loc[indexes, "packet_loss"] += (
            progress*80
        )


    df.loc[indexes, "label"] = 1

    df.loc[indexes, "anomaly_type"] = anomaly



def generate_machine(
        name,
        mtype,
        days,
        anomaly_ratio,
        rng
):

    base = PROFILES[mtype]

    samples = days * 24 * 60


    start = datetime(
        2026,
        1,
        1
    )


    timestamps = [
        start + timedelta(minutes=i)
        for i in range(samples)
    ]


    hours = np.array(
        [x.hour for x in timestamps]
    )


    factor = np.array(
        [
            workload(h)
            for h in hours
        ]
    )


    cpu_mu, ram_mu, net_mu, disk_mu, disk_usage_mu, load_mu, latency_mu = base



    df = pd.DataFrame({

        "timestamp": timestamps,

        "machine": name,

        "type": mtype,


        "cpu":
            rng.normal(
                cpu_mu*factor,
                5,
                samples
            ),


        "ram":
            rng.normal(
                ram_mu*factor,
                5,
                samples
            ),


        "network":
            rng.normal(
                net_mu*factor,
                20,
                samples
            ),


        "disk_io":
            rng.normal(
                disk_mu*factor,
                5,
                samples
            ),


        "disk_usage":
            rng.normal(
                disk_usage_mu,
                3,
                samples
            ),


        "load_avg":
            rng.normal(
                load_mu*factor,
                .5,
                samples
            ),


        "response_time":
            rng.normal(
                latency_mu,
                5,
                samples
            ),


        "packet_loss":
            rng.normal(
                0,
                0.2,
                samples
            ),


        "label":0,


        "anomaly_type":
            "normal"

    })


    if mtype in NETWORK_DEVICES:

        df["disk_io"] = 0
        df["disk_usage"] = 0
        df["load_avg"] = 0



    anomaly_points = int(
        samples *
        anomaly_ratio
    )


    injected = 0


    while injected < anomaly_points:

        length = rng.integers(
            20,
            120
        )

        start = rng.integers(
            0,
            samples-length
        )


        anomaly = rng.choice(
            ANOMALIES
        )


        if (
            mtype in NETWORK_DEVICES
            and anomaly == "disk_saturation"
        ):
            continue


        inject_anomaly(
            df,
            start,
            length,
            anomaly,
            rng
        )


        injected += length



    df["cpu"] = df["cpu"].clip(0,100)
    df["ram"] = df["ram"].clip(0,100)
    df["disk_io"] = df["disk_io"].clip(0,100)
    df["disk_usage"] = df["disk_usage"].clip(0,100)

    df["network"] = df["network"].clip(0)
    df["response_time"] = df["response_time"].clip(0)
    df["packet_loss"] = df["packet_loss"].clip(0,100)


    return df



def main():

    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--machines",
        default=200,
        type=int
    )

    parser.add_argument(
        "--days",
        default=30,
        type=int
    )

    parser.add_argument(
        "--anomaly-ratio",
        default=0.02,
        type=float
    )

    args = parser.parse_args()


    rng = np.random.default_rng(42)


    machines=[]


    types=list(PROFILES.keys())


    for i in range(args.machines):

        t = types[
            i % len(types)
        ]

        machines.append(
            generate_machine(
                f"{t}-{i:03d}",
                t,
                args.days,
                args.anomaly_ratio,
                rng
            )
        )


    df=pd.concat(
        machines,
        ignore_index=True
    )


    df.to_csv(
        OUTPUT,
        index=False
    )


    print(df.head())

    print(
        "\nSaved:",
        OUTPUT
    )

    print(
        "Anomaly ratio:",
        df.label.mean()
    )



if __name__=="__main__":
    main()
