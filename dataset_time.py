"""Review tab: show the dataset timestamp of each demo reading next to the time the pod logged it.
Run from the repo root (after review_details.py). All-or-nothing."""
import pathlib
import sys

EDITS = {
"api/demo_labels.py": [
 ("def record(prediction_id, label, cause, source) -> bool:", "def record(prediction_id, label, cause, source, reading_time=None) -> bool:"),
 ("""                       "cause": str(cause or "")[:60], "source": str(source)[:30]}) + "\\n\"""",
  """                       "cause": str(cause or "")[:60], "source": str(source)[:30],
                       "time": str(reading_time or "")[:30]}) + "\\n\""""),
],
"api/app.py": [
 ("""                "hour": _hr,
                "metrics": {""", """                "hour": _hr,
                "timestamp": _ts,
                "metrics": {"""),
 ("    true_cause: str | None = None\n", "    true_cause: str | None = None\n    true_time: str | None = None      # the dataset row's own timestamp\n"),
 ("exclude={\"true_label\", \"true_cause\"}", "exclude={\"true_label\", \"true_cause\", \"true_time\"}"),
 ("body.true_label, body.true_cause, \"demo tab\")", "body.true_label, body.true_cause, \"demo tab\", body.true_time)"),
 ("""        "real": ({"label": real.get("label"), "cause": real.get("cause") or "", "source": real.get("source") or ""}""",
  """        "real": ({"label": real.get("label"), "cause": real.get("cause") or "", "source": real.get("source") or "",
                  "time": real.get("time") or ""}"""),
],
"monitoring/production_agent.py": [
 ("""demo_labels.record(result["prediction_id"], label, cause, "production agent")""",
  """demo_labels.record(result["prediction_id"], label, cause, "production agent", row.get("timestamp"))"""),
],
"api/static/control_panel.html": [
 ("Press <b>Details</b> to see why",
  "<b>Logged</b> is when the pod stored the prediction (UTC, 1 hour behind Tunisia); <b>Reading time</b> is the timestamp of the dataset row (the model receives only its hour). Press <b>Details</b> to see why"),
 ("<th>When</th><th>Machine</th><th>Model</th><th>Cause given</th><th>P(normal)</th><th>Real label (demo)</th>",
  "<th>Logged (UTC)</th><th>Reading time (dataset)</th><th>Machine</th><th>Model</th><th>Cause given</th><th>P(normal)</th><th>Real label (demo)</th>"),
 ("""ctEsc(x.timestamp)+'</td><td class="mono">'+ctEsc(x.machine)""",
  """ctEsc(x.timestamp)+'</td><td class="mono">'+ctEsc(x.real&&x.real.time?x.real.time:"—")+'</td><td class="mono">'+ctEsc(x.machine)"""),
 ("""<td colspan="8"></td>""", """<td colspan="9"></td>"""),
 ("""    why+=kv("model",ctEsc(x.model_version))""",
  """    why+=kv("logged at (UTC)",ctEsc(x.timestamp))+kv("reading time (dataset)",ctEsc(x.real&&x.real.time?x.real.time:"not recorded"));
    why+=kv("model",ctEsc(x.model_version))"""),
 ("{true_label:isAnom?1:0,true_cause:s.true_cause}", "{true_label:isAnom?1:0,true_cause:s.true_cause,true_time:s.timestamp}"),
],
"tests/test_review_details.py": [
 ("""    demo_labels.record(PID, 1, "cpu_spike", "demo tab")
    cluster.answers[("GET", "/predictions/recent?limit=1000")] = {"predictions": [_logged(PID)]}""",
  """    demo_labels.record(PID, 1, "cpu_spike", "demo tab", "2026-01-01 03:00:00")
    cluster.answers[("GET", "/predictions/recent?limit=1000")] = {"predictions": [_logged(PID)]}"""),
 ("""assert row["real"] == {"label": 1, "cause": "cpu_spike", "source": "demo tab"}""",
  """assert row["real"] == {"label": 1, "cause": "cpu_spike", "source": "demo tab", "time": "2026-01-01 03:00:00"}"""),
 ("""\"true_label": 1, "true_cause": "cpu_spike"}""", """\"true_label": 1, "true_cause": "cpu_spike", "true_time": "2026-01-01 03:00:00"}"""),
 ("""    assert demo_labels.load()[PID]["cause"] == "cpu_spike"\n""",
  """    assert demo_labels.load()[PID]["cause"] == "cpu_spike" and demo_labels.load()[PID]["time"] == "2026-01-01 03:00:00"\n"""),
 ("""and got[PID]["source"] == "production agent"\n""", """and got[PID]["source"] == "production agent"\n    assert got[PID]["time"] == "2026-01-01 03:00:00"\n"""),
 ("class _Resp:", '''def test_sample_rows_carry_the_dataset_timestamp(client, monkeypatch):
    cur = {"machine": "web-01", "type": "web", "timestamp": "2026-01-01 03:00:30", "label": "1", "anomaly_type": "cpu_spike",
           **{c: "1.0" for c in ("cpu", "ram", "network", "disk_io", "disk_usage", "load_avg")}}
    hist = [{"cpu": "1", "ram": "1", "load_avg": "1"}] * 10
    monkeypatch.setattr(app_module, "_load_sample_pool", lambda: [{"cur": cur, "hist": hist}])
    row = client.get("/ui/sample?n=1", auth=CREDS).json()["rows"][0]
    assert row["timestamp"] == "2026-01-01 03:00:30" and row["hour"] == 3


class _Resp:'''),
],
}

problems, new_text = [], {}
for rel, pairs in EDITS.items():
    p = pathlib.Path(rel)
    if not p.exists():
        problems.append(f"{rel} missing (run from the repo root, after review_details.py)")
        continue
    s = p.read_text()
    for old, new in pairs:
        n = s.count(old)
        if n != 1:
            problems.append(f"{rel}: {n} matches for {old[:60]!r}")
        else:
            s = s.replace(old, new)
    new_text[rel] = s
if problems:
    sys.exit("NOTHING WRITTEN:\n  " + "\n  ".join(problems))
for rel, s in new_text.items():
    pathlib.Path(rel).write_text(s)
    print("updated", rel)
