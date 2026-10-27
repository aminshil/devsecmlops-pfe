"""Pinned downloads and images (external review, 27 October): the Jenkins image's base and tools, the
third-party images, PostgreSQL, numpy and scipy. scripts/pin_versions.py writes the values on the VM;
these tests check that it did, and that its rewriting works on the real files."""
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import pin_versions as pv  # noqa: E402

DIGEST = re.compile(r"@sha256:[0-9a-f]{64}$")


def test_the_jenkins_image_is_pinned_and_checks_every_download():
    df = (ROOT / "jenkins" / "Dockerfile").read_text()
    assert "__PIN__" not in df, "run: sudo python3 scripts/pin_versions.py (on the VM)"
    code = "\n".join(line for line in df.splitlines() if not line.lstrip().startswith("#"))
    assert not re.search(r"\|\s*(ba)?sh\b", code) and "install.sh" not in code   # no script piped into a shell
    base = re.search(r"^ARG JENKINS_BASE=(\S+)", df, re.M).group(1)
    assert DIGEST.search(base) and re.search(r"^FROM \$\{JENKINS_BASE\}$", df, re.M)
    for tool in ("KUBECTL", "MINIKUBE", "SONAR_SCANNER", "TRIVY"):
        assert re.search(rf"^ARG {tool}_SHA256=[0-9a-f]{{64}}$", df, re.M), tool
        assert f'echo "${{{tool}_SHA256}}  ' in df and "sha256sum -c -" in df, tool
    assert re.search(r"^ARG TRIVY_VERSION=\d+\.\d+\.\d+$", df, re.M)


def test_third_party_images_and_postgres_are_pinned_by_digest():
    gv = (ROOT / "ansible" / "group_vars" / "all.yml").read_text()
    for var in pv.CONTAINERS:
        ref = re.search(rf'^{var}: "([^"]*)"$', gv, re.M).group(1)
        assert DIGEST.search(ref), f"{var} = {ref}: run scripts/pin_versions.py"
    tasks = (ROOT / "ansible" / "roles" / "prerequisites" / "tasks" / "main.yml").read_text()
    assert 'image: "minio/minio"' not in tasks and 'image: "sonarqube:community"' not in tasks
    for p in pv.POSTGRES_FILES:
        for ref in re.findall(r"image: (postgres\S*)", p.read_text()):
            assert DIGEST.search(ref), f"{p.name}: {ref}"


def test_numpy_and_scipy_are_pinned_for_the_model_pickles():
    req = (ROOT / "requirements-api.txt").read_text()
    assert re.search(r"^numpy==\d+\.\d+\.\d+", req, re.M) and re.search(r"^scipy==\d+\.\d+\.\d+", req, re.M)


def test_pin_versions_rewrites_the_real_files(tmp_path, monkeypatch):
    """The rewriting works on copies of the repository's own files, placeholders or pins alike."""
    files = {"DOCKERFILE": pv.DOCKERFILE, "GROUP_VARS": pv.GROUP_VARS, "REQUIREMENTS": pv.REQUIREMENTS}
    for name, src in files.items():
        dst = tmp_path / src.name
        shutil.copy(src, dst)
        monkeypatch.setattr(pv, name, dst)
    pg = []
    for i, src in enumerate(pv.POSTGRES_FILES):
        dst = tmp_path / f"{i}_{src.name}"
        shutil.copy(src, dst)
        pg.append(dst)
    monkeypatch.setattr(pv, "POSTGRES_FILES", pg)
    h = "a" * 64
    pins = {"JENKINS_BASE": f"jenkins/jenkins:lts@sha256:{h}", "KUBECTL_SHA256": h, "MINIKUBE_SHA256": h,
            "SONAR_SCANNER_SHA256": h, "TRIVY_VERSION": "0.99.1", "TRIVY_SHA256": h}
    images = {var: f"example/{var}:1.2.3@sha256:{h}" for var in pv.CONTAINERS} | {"postgres": f"postgres:16-alpine@sha256:{h}"}
    pv.write(pins, images, {"numpy": "9.9.9", "scipy": "8.8.8"})
    pv.write(pins, images, {"numpy": "9.9.9", "scipy": "8.8.8"})          # idempotent: re-run replaces
    df = pv.DOCKERFILE.read_text()
    assert "__PIN__" not in df and f"ARG TRIVY_SHA256={h}" in df and "ARG TRIVY_VERSION=0.99.1" in df
    gv = pv.GROUP_VARS.read_text()
    assert all(f'{var}: "example/{var}:1.2.3@sha256:{h}"' in gv for var in pv.CONTAINERS)
    for p in pg:
        assert f"image: postgres:16-alpine@sha256:{h}" in p.read_text()
    req = pv.REQUIREMENTS.read_text()
    assert req.count("numpy==9.9.9") == 1 and req.count("scipy==8.8.8") == 1
    assert pv.first_hex(f"{h}  trivy_0.99.1_Linux-64bit.tar.gz\n{'b' * 64}  other", "trivy_0.99.1_Linux-64bit.tar.gz") == h
