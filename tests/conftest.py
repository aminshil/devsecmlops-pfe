"""Shared fixtures: tiny synthetic fleets from the real generator, so the
training pipeline and the experiment console are exercised end to end on
data with the exact production schema."""
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _generate(out: Path, seed: int) -> Path:
    subprocess.run([sys.executable, str(ROOT / "ml-model" / "generate_telecom_fleet.py"),
                    "--machines", "20", "--days", "1", "--seed", str(seed),
                    "--output", str(out)], check=True, capture_output=True)
    return out


@pytest.fixture(scope="session")
def tiny_fleet(tmp_path_factory):
    d = tmp_path_factory.mktemp("fleet")
    return {"train": _generate(d / "telecom_fleet_v2_labeled.csv", 42),
            "test": _generate(d / "telecom_fleet_v2_test.csv", 123),
            "dir": d}
