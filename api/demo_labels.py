"""True labels of the demo readings, kept beside the cluster's prediction log.

The cluster database never learns a reading's true label (in real operation nobody knows it). The two
demo sources do, because they replay labelled data: the control panel's Demo tab and the production
agent. They record the flagged readings here, keyed by the cluster's prediction id, so the Review tab
can show the truth next to the model's call. Each line also carries the machine and when it was recorded,
so the History tab can find the flagged readings of an anomaly episode (their reading time and true label).
Demo only: serving, training and the guardrail never read this.
"""
import fcntl
import json
import os
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MAX_BYTES = 8_000_000     # past this size the oldest lines are dropped
KEEP_LINES = 30000        # about 8 hours of the production agent's flagged readings (~60 a minute)
_LOCK = threading.Lock()   # threads of one process; the file lock below covers the other process


def labels_path() -> Path:
    return Path(os.environ.get("DEMO_LABELS_PATH") or ROOT / "data" / "demo_labels.jsonl")


def record(prediction_id, label, cause, source, reading_time=None, machine=None) -> bool:
    """Append one label. Best effort: returns False instead of raising (a label is never worth a failed request)."""
    if not prediction_id or label not in (0, 1):
        return False
    line = json.dumps({"id": str(prediction_id), "label": int(label),
                       "cause": str(cause or "")[:60], "source": str(source)[:30],
                       "time": str(reading_time or "")[:30], "machine": str(machine or "")[:64],
                       "logged": round(time.time(), 3)}) + "\n"
    path = labels_path()
    try:
        with _LOCK:
            path.parent.mkdir(parents=True, exist_ok=True)
            # The control panel and the production agent are two processes writing this file: an exclusive
            # lock on a sidecar file makes the truncation (read, then rewrite) and each append one step, so a
            # line appended by one while the other truncates is not lost.
            with open(str(path) + ".lock", "a") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                if path.exists() and path.stat().st_size > MAX_BYTES:
                    kept = path.read_text().splitlines()[-KEEP_LINES:]
                    path.write_text("\n".join(kept) + "\n")
                with path.open("a") as f:
                    f.write(line)
        return True
    except OSError:
        return False


def load() -> dict:
    """prediction id -> {"id", "label", "cause", "source", "time", "machine", "logged"} ("machine" and
    "logged" only on lines written since they were added); empty when nothing was recorded."""
    out = {}
    try:
        text = labels_path().read_text()
    except OSError:
        return out
    for line in text.splitlines():
        try:
            entry = json.loads(line)
            out[entry["id"]] = entry
        except (ValueError, KeyError, TypeError):
            continue
    return out


def for_machine(machine, since, until) -> list:
    """The labels recorded for `machine` between `since` and `until` (Unix seconds), oldest first.
    Lines written before the machine was recorded have no machine and never match."""
    rows = [e for e in load().values()
            if e.get("machine") == machine and isinstance(e.get("logged"), (int, float)) and since <= e["logged"] <= until]
    return sorted(rows, key=lambda e: e["logged"])
