"""
Worked example: raw reading -> per-machine per-window z-score -> IsolationForest verdict.
Loads the REAL 6-feature v2.3.0 serving artifacts (telecom_serving_*).
"""
import json
from pathlib import Path
import joblib
import pandas as pd
from rich.console import Console
from rich.table import Table
from rich import box

ROOT      = Path(__file__).resolve().parent.parent
model     = joblib.load(ROOT / "models" / "telecom_serving_model.pkl")
baselines = json.load(open(ROOT / "models" / "telecom_serving_baselines.json"))

FEATURES    = baselines["__feature_order__"]
HAS_WINDOWS = baselines.get("__has_windows__", False)
console = Console()


def hour_to_window(h):
    if h < 6:  return "night"
    if h < 12: return "morning"
    if h < 18: return "afternoon"
    return "evening"


def get_stats(machine, hour):
    if hour is not None and HAS_WINDOWS:
        wkey = f"{machine}|{hour_to_window(hour)}"
        if wkey in baselines:
            return baselines[wkey], f"machine+window ({hour_to_window(hour)})"
    if machine in baselines:
        return baselines[machine], "machine (all-day)"
    return baselines["__global__"], "global"


def demo(machine, hour, reading, label=None):
    stats, level = get_stats(machine, hour)
    header = f"Machine: [bold cyan]{machine}[/bold cyan]"
    if hour is not None:
        header += f"   Hour: [bold yellow]{hour:02d}:00[/bold yellow]"
    header += f"   Baseline: [dim]{level}[/dim]"
    if label:
        header += f"   [bold magenta]-- {label} --[/bold magenta]"
    console.print(f"\n{header}")
    console.print(f"[dim]raw reading:[/dim] {reading}")

    tbl = Table(box=box.SIMPLE, show_edge=False, pad_edge=False)
    tbl.add_column("metric", style="white", no_wrap=True)
    tbl.add_column("raw",    style="white", justify="right")
    tbl.add_column("mean",   style="cyan",  justify="right")
    tbl.add_column("std",    style="cyan",  justify="right")
    tbl.add_column("z = (raw - mean)/std", style="bold", justify="right")

    z_vals = []
    for f in FEATURES:
        raw    = reading[f]
        mu, sd = stats[f]
        z      = (raw - mu) / sd
        z_vals.append(z)
        abs_z = abs(z)
        if abs_z >= 3:
            color = "red"
        elif abs_z >= 2:
            color = "yellow"
        else:
            color = "green"
        tbl.add_row(f, f"{raw:.1f}", f"{mu:.2f}", f"{sd:.2f}",
                    f"[{color}]{z:+.2f}[/{color}]")
    console.print(tbl)

    X       = pd.DataFrame([z_vals], columns=FEATURES)
    is_anom = int(model.predict(X)[0] == -1)
    score   = float(-model.score_samples(X)[0])
    verdict = "[bold red]ANOMALY[/bold red]" if is_anom else "[bold green]normal[/bold green]"
    zvec    = "[" + ", ".join(f"{v:+.2f}" for v in z_vals) + "]"
    console.print(f"[dim]z-vector -> IsolationForest:[/dim] {zvec}")
    console.print(f"[dim]verdict:[/dim] {verdict}   [dim](anomaly_score {score:.3f})[/dim]")


console.print("\n[bold]Part 1 - Per-machine z-score: same raw value, different verdict[/bold]")
console.print("[dim]Same cpu=78. On db-01 (idles high) normal. On web-01 (idles low) anomaly.[/dim]")

demo("db-01", 14,
     {"cpu": 78, "ram": 80, "network": 120, "disk_io": 80, "disk_usage": 72, "load_avg": 4.5},
     label="db-01 cpu=78 is NORMAL for db")

demo("web-01", 14,
     {"cpu": 90, "ram": 75, "network": 260, "disk_io": 55, "disk_usage": 55, "load_avg": 4.5},
     label="web-01 sustained high load = ANOMALY for web")

console.print("\n[bold]Part 2 - The 3am problem: same values, different time, different verdict[/bold]")
console.print("[dim]Identical raw reading at hour=14 (afternoon) vs hour=3 (night) on web-01.[/dim]")

demo("web-01", 3,
     {"cpu": 30, "ram": 50, "network": 80, "disk_io": 25, "disk_usage": 40, "load_avg": 1.2},
     label="web-01 at 3am")

demo("web-01", 14,
     {"cpu": 30, "ram": 50, "network": 80, "disk_io": 25, "disk_usage": 40, "load_avg": 1.2},
     label="web-01 at 2pm (same reading)")

console.print("\n[bold]Part 3 - Big multivariate anomaly (all features spike)[/bold]")

demo("app-01", 14,
     {"cpu": 95, "ram": 92, "network": 900, "disk_io": 90, "disk_usage": 85, "load_avg": 12},
     label="app-01 full incident")
