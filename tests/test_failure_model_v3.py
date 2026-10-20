"""The v3 failure model: one incident drives the router and its dependents, gray / down / control-plane
behave as documented, labels follow impact (no coin flip), and the realism report finds nothing
unexplained. Runs the generator on a small fleet."""
import json
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
GEN = ROOT / "ml-model" / "generate_telecom_fleet_v3.py"
REPORT = ROOT / "scripts" / "failure_model_report.py"


@pytest.fixture(scope="module")
def fleet(tmp_path_factory):
    out = tmp_path_factory.mktemp("v3") / "fleet.csv"
    subprocess.run([sys.executable, str(GEN), "--machines", "40", "--days", "1", "--seed", "5",
                    "--router-fault-ratio", "0.08", "--output", str(out)],
                   check=True, capture_output=True, text=True)
    df = pd.read_csv(out, keep_default_na=False)
    log = json.loads(out.with_suffix(".incidents.json").read_text())
    return out, df, log


def _rows(df, machine):
    return df[df["machine"] == machine].reset_index(drop=True)


def test_output_keeps_the_v2_columns_and_adds_root_cause_and_reachability(fleet):
    _, df, log = fleet
    for col in ("timestamp", "machine", "type", "cpu", "ram", "network", "disk_io", "disk_usage",
                "load_avg", "label", "anomaly_type", "root_cause", "reachable"):
        assert col in df.columns
    assert set(df["reachable"].unique()) <= {0, 1}
    assert (df.loc[df["label"] == 0, "root_cause"] == "").all()
    assert (df.loc[df["label"] == 1, "root_cause"] != "").all()
    assert log["incidents"], "the small fleet must contain at least one router incident"


def test_the_router_is_labelled_during_every_one_of_its_incidents(fleet):
    _, df, log = fleet
    expected = {"gray": "router_gray", "down": "router_down", "control_plane": "cpu_spike"}
    for router, incs in log["incidents"].items():
        r = _rows(df, router)
        for inc in incs:
            win = r.iloc[inc["start"]:inc["end"]]
            assert (win["label"] == 1).all() and (win["root_cause"] == router).all()
            assert (win["anomaly_type"] == expected[inc["mode"]]).all()


def test_a_hard_down_router_makes_every_dependent_unreachable(fleet):
    _, df, log = fleet
    for router, incs in log["incidents"].items():
        deps = [m for m, r in log["depends_on"].items() if r == router]
        for inc in (i for i in incs if i["mode"] == "down"):
            for m in deps:
                win = _rows(df, m).iloc[inc["start"] + 1:inc["end"]]   # lag is at most one reading
                assert (win["reachable"] == 0).all(), (router, m, inc)


def test_control_plane_events_do_not_propagate(fleet):
    _, df, log = fleet
    for router, incs in log["incidents"].items():
        deps = [m for m, r in log["depends_on"].items() if r == router]
        for inc in (i for i in incs if i["mode"] == "control_plane"):
            for m in deps:
                win = _rows(df, m).iloc[inc["start"]:inc["end"] + 4]
                assert not (win["root_cause"] == router).any()


def test_no_cascade_label_exists_without_a_router_incident(fleet):
    _, df, log = fleet
    for router, incs in log["incidents"].items():
        allowed = set()
        for inc in incs:
            if inc["mode"] != "control_plane":
                allowed.update(range(inc["start"], inc["end"] + 4))     # lag + recovery tail
        deps = [m for m, r in log["depends_on"].items() if r == router]
        for m in deps:
            r = _rows(df, m)
            idx = set(r.index[r["root_cause"] == router])
            assert idx <= allowed, (m, sorted(idx - allowed)[:5])


def test_the_generator_is_deterministic(fleet, tmp_path):
    out, df, _ = fleet
    again = tmp_path / "again.csv"
    subprocess.run([sys.executable, str(GEN), "--machines", "40", "--days", "1", "--seed", "5",
                    "--router-fault-ratio", "0.08", "--output", str(again)],
                   check=True, capture_output=True, text=True)
    assert again.read_bytes() == out.read_bytes()


def test_the_rack_rule_names_every_hard_down_router(fleet):
    """Hard-down detection comes from reachability, not from the model, so it must be complete
    whatever artifacts score the readings (here the committed production ones)."""
    out, _, log = fleet
    if not any(i["mode"] == "down" for incs in log["incidents"].values() for i in incs):
        pytest.skip("no hard-down incident in this small fleet")
    rep = subprocess.run([sys.executable, str(ROOT / "scripts" / "rack_rule_eval.py"), "--data", str(out),
                          "--models", str(ROOT / "models")], check=True, capture_output=True, text=True).stdout
    line = next(ln for ln in rep.splitlines() if "per incident, down" in ln)
    assert "(100%)" in line, line


def test_the_realism_report_leaves_nothing_unexplained(fleet):
    out, _, _ = fleet
    rep = subprocess.run([sys.executable, str(REPORT), "--data", str(out), "--many", "3"],
                         check=True, capture_output=True, text=True).stdout
    line = next(ln for ln in rep.splitlines() if "unreachable dependents outside" in ln)
    assert line.strip().endswith(": 0")
