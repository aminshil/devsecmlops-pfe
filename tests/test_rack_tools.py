"""The offline rack tools: the vectorised rule equals the API's rule, the curve comparison reads both
curves correctly, and the window sweep runs end to end on a small v3 fleet."""
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from api import fleet_rules  # noqa: E402


def test_classify_arrays_matches_classify_element_for_element():
    rng = np.random.default_rng(3)
    n = 5000
    seen = rng.integers(0, 25, n)
    stale = np.minimum(rng.integers(0, 25, n), seen)
    live = seen - stale
    bad = np.minimum(rng.integers(0, 25, n), live)
    hub = rng.random(n) < 0.3
    for share, group in ((0.4, 4), (0.2, 3)):
        vec = fleet_rules.classify_arrays(seen, stale, live, bad, hub, share, group)
        ref = [fleet_rules.classify(int(a), int(b), int(c), int(d), bool(e), share, group)
               for a, b, c, d, e in zip(seen, stale, live, bad, hub)]
        assert list(vec) == ref


def _curve(points):
    return [{"threshold": round(0.05 + 0.01 * i, 2), "precision": p, "recall": r, "f1": 2 * p * r / (p + r)}
            for i, (p, r) in enumerate(points)]


def test_compare_curves_finds_where_the_candidate_overtakes(tmp_path):
    prod = _curve([(0.76, 0.80 + 0.001 * i) for i in range(90)] + [(0.55, 0.89)] * 5)
    cand = _curve([(0.72, 0.81 + 0.0013 * i) for i in range(95)])
    (tmp_path / "prod.json").write_text(json.dumps(prod))
    (tmp_path / "report.json").write_text(json.dumps({"threshold_selection": {"curve": cand},
                                                      "params": {"threshold": 0.32}}))
    out = subprocess.run([sys.executable, str(ROOT / "scripts" / "compare_curves.py"), "--reuse",
                          "--report", str(tmp_path / "report.json"), "--out", str(tmp_path / "prod.json")],
                         check=True, capture_output=True, text=True).stdout
    assert "best F1 on each curve" in out
    # production stays at P 0.76 up to recall 0.889, then drops to 0.55: the candidate (0.72) wins from 0.89
    assert "the candidate becomes the more precise detector from recall 0.89" in out
    assert "at recall >= 0.80: production P 0.7600" in out


def test_window_sweep_runs_and_names_every_hard_down_router(tmp_path):
    data = tmp_path / "fleet.csv"
    subprocess.run([sys.executable, str(ROOT / "ml-model" / "generate_telecom_fleet_v3.py"), "--machines", "40",
                    "--days", "1", "--seed", "5", "--router-fault-ratio", "0.08", "--output", str(data)],
                   check=True, capture_output=True, text=True)
    log = json.loads(data.with_suffix(".incidents.json").read_text())
    if not any(i["mode"] == "down" for incs in log["incidents"].values() for i in incs):
        pytest.skip("no hard-down incident in this small fleet")
    out = subprocess.run([sys.executable, str(ROOT / "scripts" / "rack_rule_sweep.py"), "--data", str(data),
                          "--models", str(ROOT / "models"), "--windows", "1,4", "--shares", "0.4"],
                         check=True, capture_output=True, text=True).stdout
    rows = [ln for ln in out.splitlines() if " s " in ln and "%" in ln and "|" in ln]
    assert len(rows) == 2 and "<- shipped" in rows[0]
    for row in rows:
        assert "100%" in row.split("|")[2], row
