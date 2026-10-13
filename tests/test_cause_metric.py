"""api_anomaly_cause_p: one series per currently-anomalous machine, named by cause."""
from types import SimpleNamespace

from prometheus_client import REGISTRY

from api import app as A


def _series(machine):
    out = {}
    for fam in REGISTRY.collect():
        if fam.name == "api_anomaly_cause_p":
            for s in fam.samples:
                if s.labels.get("machine") == machine:
                    out[s.labels["cause"]] = s.value
    return out


def _rec(machine, anomalous, cause=None, p=0.9):
    A._record_prediction_metrics(
        SimpleNamespace(machine=machine, metrics={}),
        {"machine_type": "web", "is_anomaly": 1 if anomalous else 0,
         "likely_cause": cause, "xgb_vote": {"p_anomaly": p}})


def test_cause_series_follows_the_latest_verdict():
    m = next(iter(A._KNOWN_MACHINES))
    _rec(m, True, "memory_leak", 0.93)
    assert _series(m) == {"memory_leak": 0.93}
    _rec(m, True, "cpu_spike", 0.71)
    assert _series(m) == {"cpu_spike": 0.71}
    _rec(m, False, "normal", 0.05)
    assert _series(m) == {}


def test_anomaly_without_cause_is_labelled_unexplained():
    m = next(iter(A._KNOWN_MACHINES))
    _rec(m, True, None, 0.4)
    assert _series(m) == {"unexplained": 0.4}
    _rec(m, False)
    assert _series(m) == {}


def test_unknown_machine_names_do_not_create_series():
    evil = "attacker-" + "x" * 20
    _rec(evil, True, "network_flood")
    assert _series(evil) == {}
    assert _series("unknown") == {"network_flood": 0.9}
    _rec(evil, False)
    assert _series("unknown") == {}
