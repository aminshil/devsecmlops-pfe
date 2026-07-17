"""
Slices the first 14 days out of the full 30-day training dataset
(telecom_fleet_v2_labeled.csv, seed=42) and saves it as a standalone
file for live-deployment demo/testing purposes.

This is NOT a fresh generation -- it's the exact first two weeks of
the actual training data, so results against it are directly
comparable to (and consistent with) the full 30-day evaluation.

Usage:
    python scripts/make_two_week_demo_dataset.py
"""
import pandas as pd
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "data" / "telecom_fleet_v2_labeled.csv"
OUTPUT = ROOT / "data" / "telecom_fleet_two_weeks_demo.csv"

print(f"Loading {SOURCE} ...")
df = pd.read_csv(SOURCE, parse_dates=["timestamp"])

cutoff = df["timestamp"].min() + pd.Timedelta(days=14)
two_weeks = df[df["timestamp"] < cutoff].copy()

print(f"Two-week slice: {len(two_weeks):,} rows")
print(f"Date range: {two_weeks.timestamp.min()} to {two_weeks.timestamp.max()}")
print(f"Anomaly ratio: {two_weeks.label.mean()*100:.2f}%")

OUTPUT.parent.mkdir(parents=True, exist_ok=True)
two_weeks.to_csv(OUTPUT, index=False)
print(f"\nSaved: {OUTPUT}")
