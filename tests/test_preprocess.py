"""
Tests for the core baseline + z-score pipeline (ml-model/preprocess.py).
Covers the production logic: per-machine/window baselines, the 4-level
fallback chain, and z-score correctness. No F1/accuracy assertions here
-- structural correctness only. Model accuracy is validated separately
via the experiment scripts and live K8s tests (see scripts/).
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "ml-model"))

from preprocess import (build_baselines, apply_zscore, add_window_column, get_stats,
                        hour_to_window, legacy_hour_to_window, window_function)


@pytest.fixture
def sample_df():
    return pd.DataFrame({
        "timestamp": [
            "2026-01-01 02:00:00", "2026-01-01 02:01:00",
            "2026-01-01 14:00:00", "2026-01-01 14:01:00",
            "2026-01-01 02:00:00", "2026-01-01 02:01:00",
        ],
        "machine": ["web-01", "web-01", "web-01", "web-01", "web-02", "web-02"],
        "type": ["web", "web", "web", "web", "web", "web"],
        "cpu": [10.0, 20.0, 50.0, 60.0, 15.0, 25.0],
        "label": [0, 0, 0, 0, 0, 0],
    })


def test_add_window_column_buckets_correctly(sample_df):
    df = add_window_column(sample_df)
    assert list(df["window"]) == ["h02", "h02", "h14", "h14", "h02", "h02"]


def test_build_baselines_creates_expected_keys(sample_df):
    df = add_window_column(sample_df)
    baselines = build_baselines(df, ["cpu"])
    assert "web-01|h02" in baselines
    assert "web-01|h14" in baselines
    assert "web-02|h02" in baselines
    assert "__global__" in baselines
    assert "__feature_order__" in baselines


def test_build_baselines_values_are_correct(sample_df):
    df = add_window_column(sample_df)
    baselines = build_baselines(df, ["cpu"])
    mean, std = baselines["web-01|h02"]["cpu"]
    assert mean == pytest.approx(15.0)


def test_fallback_chain_machine_window_first(sample_df):
    df = add_window_column(sample_df)
    baselines = build_baselines(df, ["cpu"])
    stats, level = get_stats(baselines, "web-01", window="h02")
    assert level == "machine+window"


def test_fallback_chain_falls_back_when_window_unseen(sample_df):
    df = add_window_column(sample_df)
    baselines = build_baselines(df, ["cpu"])
    stats, level = get_stats(baselines, "web-01", window="h20")
    assert level in ("machine", "type", "global")


def test_fallback_chain_unknown_machine_uses_global(sample_df):
    df = add_window_column(sample_df)
    baselines = build_baselines(df, ["cpu"])
    stats, level = get_stats(baselines, "totally-unknown-machine-999", window="h02")
    assert level == "global"


def test_apply_zscore_known_value(sample_df):
    df = add_window_column(sample_df)
    baselines = build_baselines(df, ["cpu"])
    z = apply_zscore(df.iloc[[0]], baselines, ["cpu"])
    # web-01 h02: mean=15, std=5 -> z for cpu=10 is (10-15)/5 = -1.0
    assert z["cpu"].iloc[0] == pytest.approx(-1.0, abs=0.01)


def test_std_floor_prevents_division_by_zero():
    df = pd.DataFrame({
        "timestamp": ["2026-01-01 02:00:00"] * 3,
        "machine": ["flat-01"] * 3,
        "type": ["web"] * 3,
        "cpu": [50.0, 50.0, 50.0],
        "label": [0, 0, 0],
    })
    df = add_window_column(df)
    baselines = build_baselines(df, ["cpu"])
    z = apply_zscore(df, baselines, ["cpu"])
    assert np.isfinite(z["cpu"]).all()


def test_hour_to_window_is_one_window_per_hour():
    assert [hour_to_window(h) for h in (0, 9, 23)] == ["h00", "h09", "h23"]
    assert len({hour_to_window(h) for h in range(24)}) == 24


def test_baselines_record_the_windows_they_contain(sample_df):
    baselines = build_baselines(add_window_column(sample_df), ["cpu"])
    assert baselines["__windows__"] == ["h02", "h14"]
    assert window_function(baselines) is hour_to_window   # a file may miss hours it never saw


def test_window_function_reads_legacy_files():
    """Files built before 7 November 2026 have the 4 windows; they keep their own mapping."""
    legacy = {"__has_windows__": True, "__windows__": ["night", "morning", "afternoon", "evening"]}
    assert window_function(legacy) is legacy_hour_to_window
    assert [legacy_hour_to_window(h) for h in (5, 6, 12, 18)] == ["night", "morning", "afternoon", "evening"]
    assert window_function({"__has_windows__": False}) is None
    assert window_function({"__has_windows__": True, "__windows__": ["all"]}) is None


def test_apply_zscore_scores_a_frame_against_a_legacy_file(sample_df):
    """A frame windowed per hour, scored against a 4-window file (the production model during the
    switch): the window is recomputed from 'hour' with the file's own mapping, so the
    machine+window baseline is used, not the all-day fallback."""
    df = add_window_column(sample_df)                       # windows h02 / h14
    legacy_df = df.assign(window=df["hour"].map(legacy_hour_to_window))
    legacy = build_baselines(legacy_df, ["cpu"])            # keys web-01|night, web-01|afternoon
    assert set(legacy["__windows__"]) == {"night", "afternoon"}
    z_from_hourly_frame = apply_zscore(df, legacy, ["cpu"])
    z_from_legacy_frame = apply_zscore(legacy_df, legacy, ["cpu"])
    assert z_from_hourly_frame["cpu"].tolist() == pytest.approx(z_from_legacy_frame["cpu"].tolist())
    assert z_from_hourly_frame["cpu"].iloc[0] == pytest.approx(-1.0, abs=0.01)   # night mean 15, std 5
