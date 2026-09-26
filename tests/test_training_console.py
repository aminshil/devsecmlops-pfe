"""Experiment console (api/training.py): dataset whitelist, representative
sampling, like-for-like comparison with the production IsolationForest."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import api.training as training  # noqa: E402


@pytest.fixture
def data_dir(tiny_fleet, monkeypatch):
    monkeypatch.setattr(training, "DATA_DIR", tiny_fleet["dir"])
    monkeypatch.setattr(training, "EVAL_ROWS", 5000)
    return tiny_fleet["dir"]


@pytest.mark.parametrize("name", ["../VERSION", "/etc/passwd", "..", "missing.csv",
                                  "telecom_fleet_v2_labeled.csv/../../x"])
def test_dataset_outside_whitelist_is_refused(data_dir, name):
    with pytest.raises(ValueError):
        training._resolve_dataset(name)


def test_sample_is_spread_over_the_whole_file(data_dir):
    df, note = training._load_sampled("telecom_fleet_v2_labeled.csv", 3000)
    assert 2000 < len(df) < 4000
    assert df["machine"].nunique() == 20          # first-N-rows would cover one machine
    assert "random sample" in note


def test_candidate_compared_with_production_on_same_rows(data_dir):
    from preprocess import add_window_column
    df, _ = training._load_sampled("telecom_fleet_v2_labeled.csv", 0)
    df = add_window_column(df)
    _, _, cand, prod, n_train, n_eval = training._train_isoforest(
        df, ["cpu", "ram", "network", "disk_io", "disk_usage", "load_avg"],
        {"contamination": 0.07, "n_estimators": 50})
    assert n_train == len(df) and n_eval > 0
    for m in (cand, prod):
        assert 0.0 <= m["f1"] <= 1.0

    training._RUNS.append({"run_id": "t1", "metrics": cand, "production_metrics": prod,
                           "n_test": n_eval})
    res = training.verify_run("t1")
    assert res["ok"] and res["production_f1"] == prod["f1"]
    assert "model pipeline" in res["reason"] or "production keeps" in res["reason"]


def test_verify_unknown_run():
    assert training.verify_run("does-not-exist")["ok"] is False
