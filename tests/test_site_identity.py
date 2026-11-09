"""Each installation knows its site: Ansible's site_name reaches Jenkins as PFE_SITE, and both jobs
use it when their SITE parameter is left empty (so a plain "Build" on a site never bakes in another
site's model). A site's datasets under data/sites/ are never committed."""
import re
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
RESOLVE = "def site = params.SITE?.trim() ?: (env.PFE_SITE?.trim() ?: 'default')"


def _site_re():
    src = (ROOT / "scripts" / "model_store.py").read_text()
    return re.search(r'SITE_RE = re\.compile\(r"([^"]+)"\)', src).group(1)


def test_both_jobs_default_to_the_installation_site():
    for name in ("Jenkinsfile", "Jenkinsfile.model"):
        text = (ROOT / name).read_text()
        assert text.count(RESOLVE) == 1, name
        param = re.search(r"string\(name: 'SITE', defaultValue: '([^']*)'", text)
        assert param and param.group(1) == "", name
        assert "params.SITE ?: 'default'" not in text, name


def test_ansible_passes_the_site_to_jenkins_and_checks_it():
    all_vars = yaml.safe_load((ROOT / "ansible" / "group_vars" / "all.yml").read_text())
    assert "lookup('ansible.builtin.env', 'PFE_SITE')" in all_vars["site_name"]
    assert "default('default', true)" in all_vars["site_name"]

    tasks = yaml.safe_load((ROOT / "ansible" / "roles" / "prerequisites" / "tasks" / "main.yml").read_text())
    jenkins = [t for t in tasks if t.get("name") == "Ensure Jenkins (CI/CD) is running"]
    assert len(jenkins) == 1
    assert jenkins[0]["community.docker.docker_container"]["env"] == {"PFE_SITE": "{{ site_name }}"}

    site = (ROOT / "ansible" / "site.yml").read_text()
    assert f"site_name is match('^{_site_re()}$')" in site


def test_site_datasets_are_never_committed():
    out = subprocess.run(["git", "check-ignore", "-q", "data/sites/sfax/telecom_fleet_v2_labeled.csv"],
                         cwd=ROOT, capture_output=True)
    assert out.returncode == 0
