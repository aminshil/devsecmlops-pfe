#!/usr/bin/env python3
"""
Pin what the platform downloads to exactly what runs on the VM today, with checksums from the official
sources. Run as root on the VM, from the repository; it reads, it does not pull, build or restart anything.

  jenkins/Dockerfile          base image by digest (the local jenkins/jenkins:lts the image was built on);
                              kubectl, minikube, sonar-scanner: SHA-256 from the vendors' .sha256 files;
                              Trivy: the version the running Jenkins has, SHA-256 from Trivy's checksums.txt
  ansible/group_vars/all.yml  grafana, node-exporter, registry, minio, sonarqube: the digest of the image
                              each running container uses
  kubernetes/*.yaml,          postgres:16-alpine: the digest the cluster's PostgreSQL pod runs
  ansible/.../deployment.yaml.j2
  requirements-api.txt        numpy and scipy: the versions inside the serving image (every model pickle
                              holds numpy arrays, and they were not pinned)

All or nothing: every value is collected and checked first; if one is missing, nothing is written. Re-run
it to move a pin. Prints what it pinned.

  sudo python3 scripts/pin_versions.py
  sudo python3 scripts/pin_versions.py --dry-run
"""
import argparse
import json
import os
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCKERFILE = ROOT / "jenkins" / "Dockerfile"
GROUP_VARS = ROOT / "ansible" / "group_vars" / "all.yml"
POSTGRES_FILES = [ROOT / "kubernetes" / "deployment.yaml", ROOT / "kubernetes" / "postgres.yaml",
                  ROOT / "ansible" / "roles" / "kubernetes" / "templates" / "deployment.yaml.j2"]
REQUIREMENTS = ROOT / "requirements-api.txt"
CONTAINERS = {"img_grafana": "grafana", "img_node_exporter": "node-exporter", "img_registry": "registry",
              "img_minio": "minio", "img_sonarqube": "sonarqube"}
HEX64 = re.compile(r"^[0-9a-f]{64}$")


def sh(*cmd, env=None):
    r = subprocess.run(list(cmd), capture_output=True, text=True, env=env)
    if r.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd[:4])}...: {(r.stderr or r.stdout).strip()[:200]}")
    return r.stdout.strip()


def fetch(url):
    with urllib.request.urlopen(url, timeout=60) as r:
        return r.read().decode()


def first_hex(text, name=None):
    """The SHA-256 in a .sha256 file (the hash alone, or 'hash  name' lines)."""
    for line in text.splitlines():
        parts = line.split()
        if parts and HEX64.match(parts[0]) and (name is None or (len(parts) > 1 and parts[-1].lstrip("*") == name)):
            return parts[0]
    raise RuntimeError(f"no SHA-256{' for ' + name if name else ''} in the checksum file")


def arg(text, name):
    m = re.search(rf"^ARG {name}=(\S+)", text, re.M)
    if not m:
        raise RuntimeError(f"jenkins/Dockerfile has no ARG {name}")
    return m.group(1)


def image_ref(container):
    """repo[:version]@sha256:... of the image a running container uses."""
    configured = sh("docker", "inspect", "--format", "{{.Config.Image}}", container)
    image_id = sh("docker", "inspect", "--format", "{{.Image}}", container)
    info = json.loads(sh("docker", "image", "inspect", image_id))[0]
    repo = configured.split("@")[0]
    repo, _, tag = repo.rpartition(":") if ":" in repo.split("/")[-1] else (repo, "", "")
    digests = [d for d in info.get("RepoDigests") or [] if d.split("@")[0] == repo]
    if not digests:
        raise RuntimeError(f"{container}: no registry digest for {repo} (image built locally?)")
    version = ((info.get("Config") or {}).get("Labels") or {}).get("org.opencontainers.image.version", "")
    tag = version if re.match(r"^v?\d+(\.\d+)+", version or "") else ("" if tag in ("", "latest") else tag)
    return f"{repo}{':' + tag if tag else ''}@{digests[0].split('@')[1]}"


def collect():
    df = DOCKERFILE.read_text()
    pins = {}
    base = json.loads(sh("docker", "image", "inspect", "jenkins/jenkins:lts"))[0]
    jd = [d for d in base.get("RepoDigests") or [] if d.startswith("jenkins/jenkins@")]
    if not jd:
        raise RuntimeError("the local jenkins/jenkins:lts has no registry digest")
    jver = next((e.split("=", 1)[1] for e in base["Config"].get("Env", []) if e.startswith("JENKINS_VERSION=")), "?")
    pins["JENKINS_BASE"] = "jenkins/jenkins:lts@" + jd[0].split("@")[1]
    kv, mv, sv = arg(df, "KUBECTL_VERSION"), arg(df, "MINIKUBE_VERSION"), arg(df, "SONAR_SCANNER_VERSION")
    pins["KUBECTL_SHA256"] = first_hex(fetch(f"https://dl.k8s.io/release/{kv}/bin/linux/amd64/kubectl.sha256"))
    pins["MINIKUBE_SHA256"] = first_hex(fetch(
        f"https://storage.googleapis.com/minikube/releases/{mv}/minikube-linux-amd64.sha256"))
    pins["SONAR_SCANNER_SHA256"] = first_hex(fetch(
        "https://binaries.sonarsource.com/Distribution/sonar-scanner-cli/"
        f"sonar-scanner-cli-{sv}-linux.zip.sha256"))
    tv = re.search(r"Version:\s*v?(\d+\.\d+\.\d+)", sh("docker", "exec", "jenkins", "trivy", "--version"))
    if not tv:
        raise RuntimeError("cannot read the Trivy version of the running Jenkins")
    pins["TRIVY_VERSION"] = tv.group(1)
    pins["TRIVY_SHA256"] = first_hex(fetch(
        f"https://github.com/aquasecurity/trivy/releases/download/v{tv.group(1)}/trivy_{tv.group(1)}_checksums.txt"),
        name=f"trivy_{tv.group(1)}_Linux-64bit.tar.gz")
    images = {var: image_ref(c) for var, c in CONTAINERS.items()}
    env = {**os.environ, "KUBECONFIG": os.environ.get("KUBECONFIG", "/home/pfe/.kube/config")}
    pods = json.loads(sh("kubectl", "get", "pods", "-n", "ml-serving", "-o", "json", env=env))["items"]
    pg = {s.get("imageID", "").split("@")[-1] for p in pods
          for s in (p["status"].get("containerStatuses") or []) + (p["status"].get("initContainerStatuses") or [])
          if s.get("image", "").split("@")[0] in ("postgres:16-alpine", "docker.io/library/postgres:16-alpine")}
    pg = {d for d in pg if d.startswith("sha256:")}
    if len(pg) != 1:
        raise RuntimeError(f"expected one postgres:16-alpine digest in the cluster, found {sorted(pg) or 'none'}")
    images["postgres"] = "postgres:16-alpine@" + pg.pop()
    np_sp = sh("kubectl", "exec", "-n", "ml-serving", "deploy/anomaly-api", "-c", "api", "--", "python3", "-c",
               "import numpy, scipy; print(numpy.__version__, scipy.__version__)", env=env).split()
    if len(np_sp) != 2:
        raise RuntimeError("cannot read numpy and scipy versions in the serving pod")
    for k, v in pins.items():
        if k.endswith("SHA256") and not HEX64.match(v):
            raise RuntimeError(f"{k}: not a SHA-256: {v!r}")
    return pins, images, {"numpy": np_sp[0], "scipy": np_sp[1]}, jver


def write(pins, images, pkgs):
    df = DOCKERFILE.read_text()
    for k, v in pins.items():
        df, n = re.subn(rf"^ARG {k}=\S+$", f"ARG {k}={v}", df, flags=re.M)
        assert n == 1, k
    DOCKERFILE.write_text(df)
    gv = GROUP_VARS.read_text()
    for var in CONTAINERS:
        gv, n = re.subn(rf'^{var}: "[^"]*"$', f'{var}: "{images[var]}"', gv, flags=re.M)
        assert n == 1, var
    GROUP_VARS.write_text(gv)
    for p in POSTGRES_FILES:
        t, n = re.subn(r"image: postgres:16-alpine(@sha256:[0-9a-f]{64})?$", f"image: {images['postgres']}",
                       p.read_text(), flags=re.M)
        assert n >= 1, p
        p.write_text(t)
    req = REQUIREMENTS.read_text()
    for name, ver in pkgs.items():
        line = f"{name}=={ver}"
        if re.search(rf"^{name}==", req, re.M):
            req = re.sub(rf"^{name}==\S+.*$", line + "   # the serving image's version: every model pickle holds numpy arrays",
                         req, flags=re.M)
        else:
            req = req.rstrip("\n") + f"\n{line}   # the serving image's version: every model pickle holds numpy arrays\n"
    REQUIREMENTS.write_text(req)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="collect and print, write nothing")
    args = ap.parse_args()
    try:
        pins, images, pkgs, jver = collect()
    except (RuntimeError, OSError, ValueError, KeyError) as e:
        sys.exit(f"NOTHING WRITTEN: {e}")
    print(f"jenkins/Dockerfile (Jenkins {jver}):")
    for k, v in pins.items():
        print(f"  {k:<22} {v}")
    print("images:")
    for k, v in images.items():
        print(f"  {k:<22} {v}")
    print("requirements-api.txt:")
    for k, v in pkgs.items():
        print(f"  {k}=={v}")
    if args.dry_run:
        print("dry run: nothing written")
        return 0
    write(pins, images, pkgs)
    print("written: jenkins/Dockerfile, ansible/group_vars/all.yml, the three postgres manifests, requirements-api.txt")
    return 0


if __name__ == "__main__":
    sys.exit(main())
