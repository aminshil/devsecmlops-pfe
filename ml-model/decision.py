"""
Serving decision rule, shared by the API (api/app.py) and the training /
evaluation pipeline (ml-model/train_production.py), so that offline
evaluation measures exactly the rule production applies.

Final verdict = classifier vote OR IsolationForest vote.

Classifier vote, single-threshold mode (default):
    anomalous if P(normal) < threshold; cause = most probable non-normal class.
Classifier vote, per-class mode (per_class thresholds given):
    anomalous if any non-normal class reaches its own threshold; cause = the
    class exceeding its threshold by the largest margin.
"""
from __future__ import annotations

import numpy as np

NORMAL = "normal"
SAFETY_NET_CAUSE = "unknown (flagged by safety-net model only)"


def classifier_vote(p_normal: float, non_normal: list[tuple[str, float]],
                    threshold: float, per_class: dict | None = None) -> tuple[bool, str]:
    """Scalar form used per request by the API."""
    if per_class is not None:
        exceed = [(cls, prob, prob - per_class.get(cls, 1.01))
                  for cls, prob in non_normal
                  if prob >= per_class.get(cls, 1.01)]
        if exceed:
            return True, max(exceed, key=lambda x: x[2])[0]
        return False, NORMAL
    if p_normal < threshold:
        return True, max(non_normal, key=lambda x: x[1])[0]
    return False, NORMAL


def classifier_vote_batch(proba: np.ndarray, classes: list[str], threshold: float,
                          per_class: dict | None = None) -> tuple[np.ndarray, np.ndarray]:
    """Vectorised form used for offline evaluation. Row-for-row identical to
    classifier_vote (checked by tests/test_decision.py)."""
    classes = list(classes)
    n_idx = classes.index(NORMAL)
    other = [i for i in range(len(classes)) if i != n_idx]
    other_names = np.array([classes[i] for i in other], dtype=object)
    p_other = proba[:, other]
    if per_class is not None:
        thr = np.array([per_class.get(classes[i], 1.01) for i in other])
        margin = p_other - thr
        hit = (p_other >= thr).any(axis=1)
        cause = other_names[np.argmax(np.where(p_other >= thr, margin, -np.inf), axis=1)]
    else:
        hit = proba[:, n_idx] < threshold
        cause = other_names[np.argmax(p_other, axis=1)]
    return hit, np.where(hit, cause, NORMAL)


def final_verdict_batch(clf_hit: np.ndarray, clf_cause: np.ndarray,
                        iso_hit: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    is_anom = clf_hit | iso_hit
    cause = np.where(clf_hit, clf_cause, np.where(iso_hit, SAFETY_NET_CAUSE, NORMAL))
    return is_anom, cause
