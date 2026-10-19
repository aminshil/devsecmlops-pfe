#!/usr/bin/env python3
"""Sync the docs with the audit pass. Run from the repo root. All-or-nothing: every
replacement must match exactly once, otherwise nothing is written."""
import pathlib, sys

EDITS = {
"README.md": [
 ("| Tests | 120 passing |", "| Tests | 132 passing |"),
 ("pytest, 120 tests, coverage report", "pytest, 132 tests, coverage report"),
 ("**120 tests** (up from 15)", "**132 tests** (up from 15)"),
 ("- `test_api.py` (27 tests) — every endpoint, input validation (NaN/inf\n  rejection, machine length,",
  "- `test_api.py` (38 tests) — every endpoint, input validation (NaN/inf\n  rejection, physical-range rejection (cpu=150 is a 422), the opt-in feedback token, machine length,"),
],
"docs/DEMO_SCRIPT.md": [
 ("all 11 CI stages green, 120 tests", "all 11 CI stages green, 132 tests"),
 ("stage 1b (120 tests)", "stage 1b (132 tests)"),
],
"docs/INFRASTRUCTURE_SETUP.md": [
 ("expect: 120 passed", "expect: 132 passed"),
 ("If `120 passed` does not appear", "If `132 passed` does not appear"),
 ("export MINIO_ROOT_PASSWORD='choose-12-or-more-chars'\nexport GRAFANA_ADMIN_PASSWORD='choose-a-password'\nexport OPS_UI_USER='operator'\nexport OPS_UI_PASSWORD='choose-a-password'\nEOF",
  "export MINIO_ROOT_PASSWORD='choose-12-or-more-chars'\nexport GRAFANA_ADMIN_PASSWORD='choose-12-or-more-chars'\nexport OPS_UI_USER='operator'\nexport OPS_UI_PASSWORD='choose-12-or-more-chars'\n# optional, NOT wired into Ansible, leave it out for the demo. To protect POST /feedback:\n#   kubectl set env deployment/anomaly-api FEEDBACK_TOKEN=<value> -n ml-serving\n# and give the same variable to the control panel and scripts/feedback_demo.py, otherwise their verdicts get a 401\n# export FEEDBACK_TOKEN='choose-a-long-random-value'\nEOF"),
 ("Nothing secret is stored in the repository. The playbook reads passwords from the environment and refuses to run if they are missing or too short.",
  "Nothing secret is stored in the repository. The playbook reads passwords from the environment and refuses to run if they are missing or shorter than 12 characters (MinIO, Grafana and the Operations UI)."),
 ("ansible-playbook -i inventory.ini site.yml --syntax-check\n```\n\n### 5a.",
  "ansible-playbook -i inventory.ini site.yml --syntax-check\n```\n\nOptional dry run (changes nothing; the bucket task and all installs are skipped). Use throwaway values of 12+ characters, **never run the real playbook with them**: MinIO would be recreated with the wrong credentials.\n\n```bash\nMINIO_ROOT_USER=x MINIO_ROOT_PASSWORD=placeholder-12345 GRAFANA_ADMIN_PASSWORD=placeholder-12345 OPS_UI_USER=x OPS_UI_PASSWORD=placeholder-12345 ansible-playbook -i inventory.ini site.yml --check\n```\n\nExpect `failed=0`. Some `changed` lines are normal (apt, pip, and the MinIO and Grafana containers, which differ from your real values).\n\n### 5a."),
 ("which fails unless the alert threshold is read from the manifest.\n",
  "which fails unless the alert threshold is read from the manifest. If that smoke test fails, the pipeline rolls the deployment back to the previous revision on its own (`kubectl rollout undo`).\n"),
 ("| Never run on a clean second machine | True. Treat this guide as a careful reading of the code, not a tested procedure |",
  "| The MLflow artifact bucket was never created, and `boto3` was not declared for the host | Fixed: `scripts/ensure_minio_bucket.py` (called by the `prerequisites` role; skipped in `--check`) and `boto3` in `requirements.txt` |\n| `ansible-playbook --check` skipped the read-only probes, so it guessed | Fixed: the probes run for real in `--check`. The dry run passes on the original VM (`failed=0`) |\n| `group_vars/production_secrets.yml` is not in the repository | By design (it holds a secret). The demo profile does not need it; the production profile stops with a clear message until you create it with `ansible-vault create` (section 4) |\n| A new `docker` group membership does not apply to a running play | The play uses a local connection and cannot refresh it. On a non-root account, log out and in (or `newgrp docker`) after the first Docker install and re-run |\n| Never run on a clean second machine | True. Treat this guide as a careful reading of the code, not a tested procedure |"),
],
}

root = pathlib.Path(".")
new = {}
for f, pairs in EDITS.items():
    s = (root / f).read_text()
    for old, rep in pairs:
        n = s.count(old)
        if n != 1:
            sys.exit(f"NOT WRITTEN: {f}: {n} matches for {old[:60]!r}")
        s = s.replace(old, rep)
    new[f] = s
for f, s in new.items():
    (root / f).write_text(s)
    print("updated", f)
