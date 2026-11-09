"""The local model store: a site promotes, serves and rolls back its own model without Git."""
import hashlib
import importlib.util
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("model_store", ROOT / "scripts" / "model_store.py")
ms = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ms)


def _repo_models(tmp_path) -> Path:
    src = tmp_path / "repo-models"
    src.mkdir()
    for n in ms.FILES:
        (src / n).write_bytes((ROOT / "models" / n).read_bytes())
    return src


def _candidate(from_dir: Path, tmp_path, tag: str) -> Path:
    """A different 'trained' model: one artifact changed and the manifest updated, as a promotion does."""
    cand = tmp_path / f"cand-{tag}"
    cand.mkdir()
    for n in ms.FILES:
        (cand / n).write_bytes((from_dir / n).read_bytes())
    with open(cand / "telecom_iso_v2.pkl", "ab") as f:
        f.write(tag.encode())
    m = json.loads((cand / "manifest.json").read_text())
    m["production"]["artifacts"]["models/telecom_iso_v2.pkl"] = hashlib.sha256((cand / "telecom_iso_v2.pkl").read_bytes()).hexdigest()
    m["production"]["mlflow_run_id"] = f"run-{tag}"
    (cand / "manifest.json").write_text(json.dumps(m))
    return cand


def _current_run(store: Path, site: str) -> str:
    return json.loads((store / site / "current" / "manifest.json").read_text())["production"]["mlflow_run_id"]


def test_files_are_exactly_what_the_dockerfile_bakes_in():
    baked = re.findall(r"^COPY \$\{MODELS_SRC\}/(\S+)", (ROOT / "Dockerfile").read_text(), re.M)
    assert sorted(baked) == sorted(ms.FILES)


def test_init_then_stage_copies_the_site_model(tmp_path):
    store = tmp_path / "store"
    src = _repo_models(tmp_path)
    assert ms.main(["init", "--site", "default", "--store", str(store), "--from", str(src)]) == 0
    assert ms.main(["exists", "--site", "default", "--store", str(store)]) == 0
    assert ms.main(["exists", "--site", "sfax", "--store", str(store)]) == 1
    assert ms.main(["stage", "--site", "default", "--store", str(store), "--to", str(tmp_path / "staging")]) == 0
    for n in ms.FILES:
        assert (tmp_path / "staging" / n).read_bytes() == (src / n).read_bytes()
    # a second init never overwrites a site's current model
    other = _candidate(src, tmp_path, "x")
    assert ms.main(["init", "--site", "default", "--store", str(store), "--from", str(other)]) == 0
    assert _current_run(store, "default") == json.loads((src / "manifest.json").read_text())["production"]["mlflow_run_id"]


def test_activate_keeps_the_previous_model_and_rollback_restores_it(tmp_path):
    store = tmp_path / "store"
    src = _repo_models(tmp_path)
    ms.main(["init", "--site", "lab", "--store", str(store), "--from", str(src)])
    first = _current_run(store, "lab")
    a = _candidate(src, tmp_path, "a")
    b = _candidate(src, tmp_path, "b")
    assert ms.main(["activate", "--site", "lab", "--store", str(store), "--from", str(a), "--build", "20"]) == 0
    assert ms.main(["activate", "--site", "lab", "--store", str(store), "--from", str(b), "--build", "21"]) == 0
    assert _current_run(store, "lab") == "run-b"
    assert len(list((store / "lab" / "versions").iterdir())) == 2
    assert ms.main(["rollback", "--site", "lab", "--store", str(store)]) == 0
    assert _current_run(store, "lab") == "run-a"
    assert ms.main(["rollback", "--site", "lab", "--store", str(store)]) == 0      # repeat: one further back
    assert _current_run(store, "lab") == first
    assert ms.main(["rollback", "--site", "lab", "--store", str(store)]) == 1      # nothing older left
    assert len(list((store / "lab" / "rolled-back").iterdir())) == 2              # rolled-back models are kept
    events = [json.loads(line)["event"] for line in (store / "lab" / "log.jsonl").read_text().splitlines()]
    assert events == ["init", "activate", "activate", "rollback", "rollback"]


def test_sites_are_independent(tmp_path):
    store = tmp_path / "store"
    src = _repo_models(tmp_path)
    ms.main(["init", "--site", "carthage", "--store", str(store), "--from", str(src)])
    ms.main(["init", "--site", "sfax", "--store", str(store), "--from", str(src)])
    ms.main(["activate", "--site", "sfax", "--store", str(store), "--from", str(_candidate(src, tmp_path, "s")), "--build", "7"])
    assert _current_run(store, "sfax") == "run-s"
    assert _current_run(store, "carthage") != "run-s"


def test_a_tampered_file_is_refused_everywhere(tmp_path, capsys):
    store = tmp_path / "store"
    src = _repo_models(tmp_path)
    ms.main(["init", "--site", "default", "--store", str(store), "--from", str(src)])
    bad = _candidate(src, tmp_path, "bad")
    with open(bad / "telecom_xgb_v4_rolling.pkl", "ab") as f:          # changed after the manifest was written
        f.write(b"x")
    assert ms.main(["activate", "--site", "default", "--store", str(store), "--from", str(bad), "--build", "9"]) == 1
    assert "SHA-256 differs" in capsys.readouterr().out
    assert not (store / "default" / ".incoming").exists() or not (store / "default" / "versions").exists()
    with open(store / "default" / "current" / "telecom_iso_v2.pkl", "ab") as f:
        f.write(b"x")
    assert ms.main(["export", "--site", "default", "--store", str(store), "--to", str(tmp_path / "out")]) == 1


@pytest.mark.parametrize("site", ["../etc", "Sfax", "a/b", "", "x" * 40])
def test_site_names_cannot_escape_the_store(tmp_path, site):
    assert ms.main(["status", "--site", site, "--store", str(tmp_path)]) == 1
