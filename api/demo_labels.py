"""True labels of the demo readings, kept beside the cluster's prediction log.

The cluster database never learns a reading's true label (in real operation nobody knows it). The two
demo sources do, because they replay labelled data: the control panel's Demo tab and the production
agent. They record the flagged readings here, keyed by the cluster's prediction id, so the Review tab
can show the truth next to the model's call. Demo only: serving, training and the guardrail never read this.
"""
import json
import os
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MAX_BYTES = 4_000_000     # past this size the oldest lines are dropped
KEEP_LINES = 5000
_LOCK = threading.Lock()


def labels_path() -> Path:
    return Path(os.environ.get("DEMO_LABELS_PATH") or ROOT / "data" / "demo_labels.jsonl")


def record(prediction_id, label, cause, source, reading_time=None) -> bool:
    """Append one label. Best effort: returns False instead of raising (a label is never worth a failed request)."""
    if not prediction_id or label not in (0, 1):
        return False
    line = json.dumps({"id": str(prediction_id), "label": int(label),
                       "cause": str(cause or "")[:60], "source": str(source)[:30],
                       "time": str(reading_time or "")[:30]}) + "\n"
    path = labels_path()
    try:
        with _LOCK:
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists() and path.stat().st_size > MAX_BYTES:
                kept = path.read_text().splitlines()[-KEEP_LINES:]
                path.write_text("\n".join(kept) + "\n")
            with path.open("a") as f:
                f.write(line)
        return True
    except OSError:
        return False


def load() -> dict:
    """prediction id -> {"id", "label", "cause", "source"}; empty when nothing was recorded."""
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
