"""A site can serve its own locally promoted model: the model folder is configurable.
Default (nothing set) must stay the repository's models/, so existing behaviour is unchanged."""
import importlib.util
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_root_cause():
    spec = importlib.util.spec_from_file_location("root_cause_under_test", ROOT / "ml-model" / "root_cause.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_root_cause_graph_defaults_to_repository_models(monkeypatch):
    monkeypatch.delenv("MODELS_DIR", raising=False)
    assert _load_root_cause().GRAPH_PATH == ROOT / "models" / "dependency_graph.json"


def test_root_cause_graph_follows_models_dir(monkeypatch, tmp_path):
    graph = {"depends_on": {"web-x": "router-x"}, "routers": ["router-x"], "downstream_of": {"router-x": ["web-x"]}}
    (tmp_path / "dependency_graph.json").write_text(json.dumps(graph))
    monkeypatch.setenv("MODELS_DIR", str(tmp_path))
    rc = _load_root_cause()
    assert rc.GRAPH_PATH == tmp_path / "dependency_graph.json"
    assert rc.load_graph()["routers"] == ["router-x"]


def test_dockerfile_takes_every_model_file_from_models_src():
    df = (ROOT / "Dockerfile").read_text()
    assert re.search(r"^ARG MODELS_SRC=models$", df, re.M), "default must stay models/"
    assert "COPY models/" not in df, "a model file is still copied from a fixed path"
    assert df.count("COPY ${MODELS_SRC}/") == 10


def _site_copy(tmp_path) -> Path:
    """A site's model folder: the exact files the image bakes in, copied from models/."""
    names = re.findall(r"^COPY \$\{MODELS_SRC\}/(\S+)", (ROOT / "Dockerfile").read_text(), re.M)
    site = tmp_path / "site-models"
    site.mkdir()
    for n in names:
        (site / n).write_bytes((ROOT / "models" / n).read_bytes())
    return site


def _import_app(models_dir: Path):
    import os
    import subprocess
    import sys
    env = {**os.environ, "MODELS_DIR": str(models_dir)}
    code = "import api.app as a; print('LOADED', a.MODELS_DIR, a.MODEL_LINEAGE.get('served_v4_f1'))"
    return subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, timeout=300)


def test_api_serves_a_site_model_folder(tmp_path):
    site = _site_copy(tmp_path)
    r = _import_app(site)
    assert r.returncode == 0, r.stderr[-800:]
    assert f"LOADED {site}" in r.stdout


def test_api_refuses_a_tampered_file_in_the_site_folder(tmp_path):
    site = _site_copy(tmp_path)
    with open(site / "telecom_iso_v2.pkl", "ab") as f:
        f.write(b"x")
    r = _import_app(site)
    assert r.returncode != 0
    assert "artifact integrity check failed: models/telecom_iso_v2.pkl" in r.stderr
