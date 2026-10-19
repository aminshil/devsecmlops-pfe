"""
Model policy: how the model is trained and how its alert threshold is chosen.

Everything a person may want to change lives in ONE file, config/model_policy.json.
This module only reads and validates it, and holds the (pure, unit-tested) rule that
picks a threshold from a precision/recall curve. Used by train_production.py and
select_threshold.py; the serving API does not import it -- it reads the threshold the
pipeline recorded in models/manifest.json.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
POLICY_PATH = ROOT / "config" / "model_policy.json"

DEFAULTS = {
    "training": {"normal_sample": None},
    "threshold": {"beta": 2.0, "min_recall": None,
                  "grid": {"start": 0.05, "stop": 0.95, "step": 0.05}},
    "guardrail": {"max_f1_drop": 0.0, "max_recall_drop": 0.02},
}


def _merge(base: dict, over: dict) -> dict:
    out = {}
    for k, v in base.items():
        if isinstance(v, dict):
            out[k] = _merge(v, over.get(k) or {})
        else:
            out[k] = over.get(k, v)
    return out


def validate(p: dict) -> dict:
    ns = p["training"]["normal_sample"]
    if ns is not None and (not isinstance(ns, int) or isinstance(ns, bool) or ns < 1):
        raise ValueError("model_policy: training.normal_sample must be null (all rows) or a positive integer")
    t = p["threshold"]
    if not isinstance(t["beta"], (int, float)) or isinstance(t["beta"], bool) or t["beta"] <= 0:
        raise ValueError("model_policy: threshold.beta must be a positive number")
    mr = t["min_recall"]
    if mr is not None and (not isinstance(mr, (int, float)) or isinstance(mr, bool) or not 0 < mr <= 1):
        raise ValueError("model_policy: threshold.min_recall must be null or a number in (0, 1]")
    g = t["grid"]
    if not (0 < g["start"] < g["stop"] < 1.0 + 1e-9 and g["step"] > 0):
        raise ValueError("model_policy: threshold.grid needs 0 < start < stop <= 1 and step > 0")
    for k in ("max_f1_drop", "max_recall_drop"):
        v = p["guardrail"][k]
        if not isinstance(v, (int, float)) or isinstance(v, bool) or not 0 <= v < 1:
            raise ValueError(f"model_policy: guardrail.{k} must be a number in [0, 1)")
    return p


def load_policy(path: Path | None = None) -> dict:
    """The policy file merged over the defaults and validated. A missing file means the
    defaults (the behaviour before the policy file existed); a malformed one is an error."""
    path = Path(path) if path else POLICY_PATH
    raw = json.loads(path.read_text()) if path.exists() else {}
    return validate(_merge(DEFAULTS, raw))


def threshold_grid(policy: dict) -> list[float]:
    g = policy["threshold"]["grid"]
    n = int(round((g["stop"] - g["start"]) / g["step"]))
    return [round(g["start"] + i * g["step"], 4) for i in range(n + 1) if g["start"] + i * g["step"] <= g["stop"] + 1e-9]


def choose_threshold(curve: list[dict], beta: float, min_recall: float | None = None) -> tuple[dict, str]:
    """Pick the threshold from a validation curve (rows with threshold, precision, recall, f1,
    f<beta>). Returns (row, rule) where rule says which branch decided:
      fbeta                  maximise F-beta (ties: higher recall)
      min_recall             highest precision among rows with recall >= min_recall
      min_recall_unreachable no row reaches min_recall: take the highest recall"""
    if min_recall is not None:
        ok = [r for r in curve if r["recall"] >= min_recall]
        if ok:
            return max(ok, key=lambda r: (r["precision"], r["recall"])), "min_recall"
        return max(curve, key=lambda r: (r["recall"], r["precision"])), "min_recall_unreachable"
    key = f"f{beta:g}"
    return max(curve, key=lambda r: (r[key], r["recall"])), "fbeta"
