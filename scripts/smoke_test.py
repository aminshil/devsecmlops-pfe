#!/usr/bin/env python3
"""
Functional smoke test of a running API instance (standard library only, so
it can run inside the serving pod as well as from Jenkins).

Checks: /health is ok and reports the expected version; a prediction without
history is served by v3 and one with a full history window by v4; /metrics
exposes the prediction counter; the operations UI is NOT exposed.
Exit 0 = all checks pass, 1 = a check failed.
"""
import argparse
import json
import sys
import time
import urllib.error
import urllib.request

READING = {"machine": "web-01", "hour": 14,
           "metrics": {"cpu": 30, "ram": 50, "network": 80,
                       "disk_io": 25, "disk_usage": 40, "load_avg": 1.2}}
HISTORY = {"cpu": [30.0] * 10, "ram": [50.0] * 10, "load_avg": [1.2] * 10}


def call(url, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def check(ok, msg):
    print(("PASS " if ok else "FAIL ") + msg)
    if not ok:
        sys.exit(1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--expect-version", default=None)
    ap.add_argument("--wait", type=int, default=60)
    a = ap.parse_args()
    base = a.url.rstrip("/")

    deadline = time.time() + a.wait
    health = None
    while time.time() < deadline:
        try:
            status, body = call(base + "/health")
            if status == 200:
                health = json.loads(body)
                break
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(2)
    check(health is not None and health.get("status") == "ok", f"/health ok ({base})")
    if a.expect_version:
        check(health.get("version") == a.expect_version,
              f"version {health.get('version')} == {a.expect_version}")

    status, body = call(base + "/predict", READING)
    v3 = json.loads(body) if status == 200 else {}
    check(status == 200 and v3.get("model") == "telecom_v3", f"/predict without history -> v3 ({status})")

    status, body = call(base + "/predict", {**READING, "history": HISTORY})
    v4 = json.loads(body) if status == 200 else {}
    check(status == 200 and v4.get("model") == "telecom_v4_rolling", f"/predict with history -> v4 ({status})")

    status, body = call(base + "/metrics")
    check(status == 200 and "api_predictions_total" in body, "/metrics exposes api_predictions_total")

    status, _ = call(base + "/ui/status")
    check(status == 404, f"operations UI not exposed (/ui/status -> {status})")
    print("smoke test passed")


if __name__ == "__main__":
    main()
