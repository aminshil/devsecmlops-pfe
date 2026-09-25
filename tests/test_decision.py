"""The vectorised decision rule used for offline evaluation must agree
row-for-row with the scalar rule the API applies per request."""
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ml-model"))
import decision  # noqa: E402

CLASSES = ["cpu_spike", "disk_saturation", "memory_leak", "network_flood", "normal", "silent_failure"]


@pytest.mark.parametrize("per_class", [None, {"cpu_spike": 0.3, "memory_leak": 0.2,
                                               "network_flood": 0.4, "disk_saturation": 0.5,
                                               "silent_failure": 0.35}])
@pytest.mark.parametrize("threshold", [0.5, 0.85])
def test_batch_matches_scalar(threshold, per_class):
    rng = np.random.default_rng(1)
    proba = rng.dirichlet(np.ones(len(CLASSES)) * 0.6, size=500)
    hit, cause = decision.classifier_vote_batch(proba, CLASSES, threshold, per_class)
    n_idx = CLASSES.index("normal")
    for row, h, c in zip(proba, hit, cause):
        non_normal = [(CLASSES[i], row[i]) for i in range(len(CLASSES)) if i != n_idx]
        sh, sc = decision.classifier_vote(float(row[n_idx]), non_normal, threshold, per_class)
        assert (bool(h), str(c)) == (sh, sc)


def test_final_verdict_or_gate():
    clf_hit = np.array([True, False, False])
    clf_cause = np.array(["cpu_spike", "normal", "normal"], dtype=object)
    iso_hit = np.array([False, True, False])
    is_anom, cause = decision.final_verdict_batch(clf_hit, clf_cause, iso_hit)
    assert list(is_anom) == [True, True, False]
    assert list(cause) == ["cpu_spike", decision.SAFETY_NET_CAUSE, "normal"]
