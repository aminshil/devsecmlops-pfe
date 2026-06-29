"""
Worked example: raw reading -> per-metric z-score -> Isolation Forest verdict.
Loads the REAL serving artifacts and prints every step in a colored table,
so the transform is visible end to end.
"""
import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from rich.console import Console
from rich.table import Table
from rich import box

ROOT = Path(__file__).resolve().parent.parent
model = joblib.load(ROOT / "models" / "serving_model.pkl")
baselines = json.load(open(ROOT / "models" / "serving_baselines.json"))
FEATURES = ["cpu", "ram", "network"]
console = Console()

examples = [
    ("db-01",  {"cpu": 78, "ram": 80, "network": 120}),
    ("web-01", {"cpu": 78, "ram": 50, "network": 80}),
    ("app-01", {"cpu": 95, "ram": 92, "network": 900}),
]

for machine, reading in examples:
    stats = baselines.get(machine, baselines["__global__"])
    console.print(f"\n[bold white]Machine:[/bold white] [bold cyan]{machine}[/bold cyan]"
                  f"   [dim]raw reading:[/dim] {reading}")

    table = Table(box=box.ROUNDED, show_lines=False, expand=False)
    table.add_column("metric", style="bold")
    table.add_column("raw", justify="right")
    table.add_column("mean (mu)", justify="right", style="dim")
    table.add_column("std (sigma)", justify="right", style="dim")
    table.add_column("z = (raw - mu)/sigma", justify="right", style="bold")

    z = []
    for col in FEATURES:
        mu, sigma = stats[col]
        zi = (reading[col] - mu) / sigma
        z.append(zi)
        z_color = "red" if abs(zi) > 3 else ("yellow" if abs(zi) > 1.5 else "green")
        table.add_row(
            col,
            f"{reading[col]:.1f}",
            f"{mu:.2f}",
            f"{sigma:.2f}",
            f"[{z_color}]{zi:+.2f}[/{z_color}]",
        )
    console.print(table)

    X = pd.DataFrame([z], columns=FEATURES)
    is_anom = model.predict(X)[0] == -1
    score = -model.score_samples(X)[0]
    verdict_str = ("[bold red]ANOMALY[/bold red]" if is_anom
                   else "[bold green]normal[/bold green]")
    z_vec = "[" + ", ".join(f"{v:+.2f}" for v in z) + "]"
    console.print(f"  [dim]z-vector into IsolationForest:[/dim] {z_vec}")
    console.print(f"  [dim]->[/dim] verdict: {verdict_str}   "
                  f"[dim](anomaly_score {score:.3f})[/dim]")

console.print()
