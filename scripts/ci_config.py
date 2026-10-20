#!/usr/bin/env python3
"""
Record (export) and re-create (restore) the hand-made CI settings as files in git.

Jenkins jobs, the plugin list and the SonarQube project/quality-gate settings live in Docker
volumes and were created through the web UIs. This tool reads them over their REST APIs and
writes plain files under ci-config/, so a rebuilt machine can get them back and a reviewer can
see them. It never touches your running Jenkins or SonarQube when exporting.

  export (read-only):
    JENKINS_USER=admin JENKINS_TOKEN=<api token> SONAR_TOKEN=<user token> \
      python scripts/ci_config.py export
  restore (writes to the target servers; asks for --yes):
    JENKINS_USER=... JENKINS_TOKEN=... SONAR_TOKEN=... python scripts/ci_config.py restore --yes

Credentials (the GitHub token, the Sonar token) are NOT exported: Jenkins keeps them encrypted
and they must be re-entered by hand. Job XML is scanned and any <secret>/<password> element is
blanked before it is written, and the count is reported.

What it does not do: install plugins (it writes ci-config/jenkins/plugins.txt; install with
`jenkins-plugin-cli -f plugins.txt` inside the Jenkins image), create credentials, or configure
the Jenkins-to-SonarQube server entry. Those remain manual steps in docs/INFRASTRUCTURE_SETUP.md.
"""
import argparse
import base64
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "ci-config"
JOBS = ["devsecmlops-pipeline", "devsecmlops-model"]
SONAR_PROJECT = "devsecmlops-pfe"
SECRET_ELEMENT = re.compile(r"<(secret|password|privateKey|passphrase)(\s[^>]*)?>.*?</\1>", re.S | re.I)


class Api:
    def __init__(self, base: str, user: str, token: str):
        self.base = base.rstrip("/")
        self.auth = "Basic " + base64.b64encode(f"{user}:{token}".encode()).decode()

    def call(self, method: str, path: str, body: bytes | None = None, headers: dict | None = None):
        req = urllib.request.Request(self.base + path, data=body, method=method,
                                     headers={"Authorization": self.auth, **(headers or {})})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def get_json(self, path: str):
        status, data = self.call("GET", path)
        if status != 200:
            raise RuntimeError(f"GET {self.base}{path} -> HTTP {status}")
        return json.loads(data)


def redact(xml: str) -> tuple[str, int]:
    out, n = SECRET_ELEMENT.subn(lambda m: f"<{m.group(1)}></{m.group(1)}>", xml)
    return out, n


def export_jenkins(j: Api, jobs: list[str], out: Path) -> None:
    (out / "jobs").mkdir(parents=True, exist_ok=True)
    for name in jobs:
        status, data = j.call("GET", f"/job/{urllib.parse.quote(name)}/config.xml")
        if status != 200:
            print(f"  jenkins job {name}: HTTP {status}, skipped", file=sys.stderr)
            continue
        xml, n = redact(data.decode())
        (out / "jobs" / f"{name}.xml").write_text(xml)
        print(f"  jenkins job {name}: written ({n} secret element(s) blanked)")
    plugins = j.get_json("/pluginManager/api/json?depth=1")["plugins"]
    lines = sorted(f"{p['shortName']}:{p['version']}" for p in plugins if p.get("enabled", True))
    (out / "plugins.txt").write_text("\n".join(lines) + "\n")
    print(f"  jenkins plugins: {len(lines)} written to plugins.txt")


def export_sonar(s: Api, project: str, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    wanted = {
        "quality_gates.json": "/api/qualitygates/list",
        "project.json": "/api/projects/search?projects=" + urllib.parse.quote(project),
        "project_gate.json": "/api/qualitygates/get_by_project?project=" + urllib.parse.quote(project),
        "webhooks.json": "/api/webhooks/list",
        "quality_profiles.json": "/api/qualityprofiles/search",
    }
    for fname, path in wanted.items():
        try:
            (out / fname).write_text(json.dumps(s.get_json(path), indent=2, sort_keys=True) + "\n")
            print(f"  sonarqube {fname}: written")
        except RuntimeError as e:
            print(f"  sonarqube {fname}: {e}", file=sys.stderr)


def restore_jenkins(j: Api, src: Path) -> None:
    status, data = j.call("GET", "/crumbIssuer/api/json")
    crumb = {}
    if status == 200:
        c = json.loads(data)
        crumb = {c["crumbRequestField"]: c["crumb"]}
    for xml_file in sorted((src / "jobs").glob("*.xml")):
        name = xml_file.stem
        body = xml_file.read_bytes()
        hdr = {"Content-Type": "application/xml", **crumb}
        exists = j.call("GET", f"/job/{urllib.parse.quote(name)}/config.xml")[0] == 200
        path = (f"/job/{urllib.parse.quote(name)}/config.xml" if exists
                else f"/createItem?name={urllib.parse.quote(name)}")
        status, resp = j.call("POST", path, body, hdr)
        print(f"  jenkins job {name}: {'updated' if exists else 'created'} -> HTTP {status}")
        if status >= 300:
            print("    " + resp.decode(errors="replace")[:200], file=sys.stderr)


def restore_sonar(s: Api, project: str, src: Path) -> None:
    q = urllib.parse.urlencode({"project": project, "name": project})
    status, resp = s.call("POST", f"/api/projects/create?{q}")
    print(f"  sonarqube project {project}: HTTP {status}"
          + (" (already exists is fine)" if status == 400 else ""))
    hooks = src / "webhooks.json"
    if hooks.exists():
        for w in json.loads(hooks.read_text()).get("webhooks", []):
            q = urllib.parse.urlencode({"name": w["name"], "url": w["url"]})
            status, _ = s.call("POST", f"/api/webhooks/create?{q}")
            print(f"  sonarqube webhook {w['name']}: HTTP {status}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", choices=["export", "restore"])
    ap.add_argument("--jenkins", default=os.environ.get("JENKINS_URL", "http://localhost:8080"))
    ap.add_argument("--sonar", default=os.environ.get("SONAR_URL", "http://localhost:9000"))
    ap.add_argument("--jobs", nargs="*", default=JOBS)
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--yes", action="store_true", help="required for restore")
    a = ap.parse_args(argv)

    ju, jt = os.environ.get("JENKINS_USER"), os.environ.get("JENKINS_TOKEN")
    st = os.environ.get("SONAR_TOKEN")
    if not (ju and jt and st):
        print("set JENKINS_USER, JENKINS_TOKEN and SONAR_TOKEN in the environment", file=sys.stderr)
        return 2
    jenkins, sonar = Api(a.jenkins, ju, jt), Api(a.sonar, st, "")
    if a.action == "export":
        print(f"exporting to {a.out}")
        export_jenkins(jenkins, a.jobs, a.out / "jenkins")
        export_sonar(sonar, SONAR_PROJECT, a.out / "sonarqube")
        return 0
    if not a.yes:
        print("restore writes to Jenkins and SonarQube; re-run with --yes", file=sys.stderr)
        return 2
    print(f"restoring from {a.out}")
    restore_jenkins(jenkins, a.out / "jenkins")
    restore_sonar(sonar, SONAR_PROJECT, a.out / "sonarqube")
    print("now install the plugins (ci-config/jenkins/plugins.txt) and re-enter the credentials by hand")
    return 0


if __name__ == "__main__":
    sys.exit(main())
