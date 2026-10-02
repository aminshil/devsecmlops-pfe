"""The feedback exporter, with kubectl replaced by a stub: it must work for a relative --out-dir
(as Jenkins passes it), for one outside the repository, and report an empty table or a kubectl
failure instead of crashing. Found by a real Jenkins run: relative_to(ROOT) crashed on both paths."""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HEADER = ("id,timestamp,machine,machine_type,time_window,raw_metrics_json,history_json,model_version,"
          "final_is_anomaly,final_cause,operator_verdict,verdict_timestamp")


def _run(tmp_path, args, body, code=0):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    stub = bin_dir / "kubectl"
    stub.write_text("#!/bin/sh\n" + body + f"exit {code}\n")
    stub.chmod(0o755)
    env = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"}
    return subprocess.run([sys.executable, str(ROOT / "scripts" / "export_feedback_dataset.py"), *args],
                          cwd=tmp_path, env=env, capture_output=True, text=True)


TWO_ROWS = (f"printf '%s\\n' '{HEADER}' "
            "'a,t,web-01,web,afternoon,{},,telecom_v4_rolling,1,memory_leak,true_positive,t' "
            "'b,t,db-01,db,afternoon,{},,telecom_v4_rolling,0,,true_negative,t'\n")


def test_export_with_a_relative_out_dir(tmp_path):
    r = _run(tmp_path, ["--out-dir", "build/feedback"], TWO_ROWS)
    assert r.returncode == 0, r.stderr
    assert "rows=2" in r.stdout
    csvs = list((tmp_path / "build" / "feedback").glob("feedback_*.csv"))
    assert len(csvs) == 1 and len(list((tmp_path / "build" / "feedback").glob("*.sha256"))) == 1


def test_export_with_an_out_dir_outside_the_repository(tmp_path):
    r = _run(tmp_path, ["--out-dir", str(tmp_path / "elsewhere")], TWO_ROWS)
    assert r.returncode == 0, r.stderr
    assert "rows=2" in r.stdout and list((tmp_path / "elsewhere").glob("feedback_*.csv"))


def test_export_reports_an_empty_table(tmp_path):
    r = _run(tmp_path, ["--out-dir", "build/feedback"], f"printf '%s\\n' '{HEADER}'\n")
    assert r.returncode == 0 and "nothing exported" in r.stdout
    assert not (tmp_path / "build").exists()


def test_export_fails_when_kubectl_fails(tmp_path):
    r = _run(tmp_path, ["--out-dir", "build/feedback"], "echo 'pod not found' >&2\n", code=1)
    assert r.returncode == 1 and "pod not found" in r.stderr
