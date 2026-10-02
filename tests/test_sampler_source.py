"""The panel's sampler must never read the test set: judged rows are used for retraining."""
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("ENABLE_OPS_UI", "1")
os.environ.setdefault("OPS_UI_PASSWORD", "test-password-0123456789")

from fastapi.testclient import TestClient  # noqa: E402
import api.app as app_module  # noqa: E402

CREDS = ("sampler-test-user", "sampler-test-password-0123456789")


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(app_module, "OPS_UI_ENABLED", True)
    monkeypatch.setattr(app_module, "_OPS_UI_USER", CREDS[0])
    monkeypatch.setattr(app_module, "_OPS_UI_PASSWORD", CREDS[1])
    return TestClient(app_module.app)


def test_the_panel_samples_from_the_operator_stream_not_the_test_set():
    assert app_module._SAMPLE_PATH.name == "telecom_fleet_v2_operator.csv"
    assert "test" not in app_module._SAMPLE_PATH.name


def test_a_missing_operator_stream_fails_closed(client, monkeypatch, tmp_path):
    monkeypatch.setattr(app_module, "_SAMPLE_PATH", tmp_path / "missing.csv")
    monkeypatch.setitem(app_module._SAMPLE_CACHE, "rows", None)
    body = client.get("/ui/sample?n=5", auth=CREDS).json()
    assert body["rows"] == [] and "operator-stream" in body["error"]
