"""Review tab: call it 'Reading time' (no 'dataset' wording) and show the hour the request carried.
Run from the repo root, after dataset_time.py. All-or-nothing."""
import pathlib
import sys

REL = "api/static/control_panel.html"
PAIRS = [
 ("<th>Reading time (dataset)</th>", "<th>Reading time</th>"),
 ("<b>Reading time</b> is the timestamp of the dataset row (the model receives only its hour).",
  "<b>Reading time</b> is the time of the reading itself; the request to the pod carries only its hour, which picks the time-window baseline."),
 ("""kv("reading time (dataset)",ctEsc(x.real&&x.real.time?x.real.time:"not recorded"));""",
  """kv("reading time",ctEsc(x.real&&x.real.time?x.real.time:"not recorded"))+kv("hour in the request",ctEsc(x.real&&x.real.time?x.real.time.slice(11,13):"not recorded"));"""),
]
p = pathlib.Path(REL)
if not p.exists():
    sys.exit(f"{REL} missing (run from the repo root)")
s = p.read_text()
bad = [f"{n} matches for {o[:60]!r}" for o, _ in PAIRS if (n := s.count(o)) != 1]
if bad:
    sys.exit("NOTHING WRITTEN:\n  " + "\n  ".join(bad))
for o, n in PAIRS:
    s = s.replace(o, n)
p.write_text(s)
print("updated", REL)
