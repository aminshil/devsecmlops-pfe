#!/usr/bin/env python3
"""
Watchdog for the alerting chain itself: Prometheus cannot alert about itself, and nothing reaches
Telegram while Alertmanager is down. This process runs outside both (systemd service
prometheus-watchdog, installed by scripts/setup_alerting.sh) and checks them every few seconds.

  Prometheus down (no answer to /-/healthy, twice in a row)
      -> alert PrometheusDown (critical) posted straight to Alertmanager -> Telegram, refreshed while it
         lasts; resolved when Prometheus answers again (Alertmanager sends the resolved message)
  Alertmanager down (twice in a row)
      -> a message sent straight to Telegram (Bot API), and another when it is back; a Prometheus outage
         while Alertmanager is down also goes straight to Telegram

The bot token is read from the root-only file setup_alerting.sh wrote, inside this process: it is never on
a command line, in the environment or in a log line. Every state change is appended to --state (JSON), which
scripts/watchdog_test.py reads to time the alerts.

  sudo python3 scripts/prometheus_watchdog.py                 # what the service runs
  sudo python3 scripts/prometheus_watchdog.py --once          # one check, print the result
"""
import argparse
import datetime as dt
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

TOKEN_FILE = Path("/etc/devsecmlops/alertmanager/telegram_token")
ENV_FILE = Path("/etc/devsecmlops/alerting.env")
TELEGRAM = "https://api.telegram.org"
ALERT_NAME = "PrometheusDown"


def iso(t):
    return dt.datetime.fromtimestamp(t, dt.timezone.utc).isoformat()


def http_ok(url, timeout=3):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status == 200
    except (OSError, ValueError):
        return False


class Watchdog:
    """Two targets, each DOWN after `failures` failed checks in a row and UP again on the first success.
    The side effects (checking, posting to Alertmanager, sending to Telegram) are passed in, so the state
    machine is tested without a network (tests/test_prometheus_watchdog.py)."""

    def __init__(self, check, post_alert, telegram, log, failures=2, refresh=30.0, alert_ttl=180.0):
        self.check, self.post_alert, self.telegram, self.log = check, post_alert, telegram, log
        self.failures, self.refresh, self.alert_ttl = failures, refresh, alert_ttl
        self.fails = {"prometheus": 0, "alertmanager": 0}
        self.down = {"prometheus": False, "alertmanager": False}
        self.down_since = {"prometheus": None, "alertmanager": None}
        self.last_post = None             # None: post at the next step
        self.alert_in_am = False          # Alertmanager holds a firing PrometheusDown from us

    def _alert(self, now, firing):
        since = self.down_since["prometheus"] or now
        return [{"labels": {"alertname": ALERT_NAME, "severity": "critical", "job": "prometheus-watchdog"},
                 "annotations": {
                     "summary": "Prometheus is down: no alert rule is evaluated",
                     "description": "Prometheus (:9090) has not answered /-/healthy since "
                                    + dt.datetime.fromtimestamp(since).strftime("%H:%M:%S")
                                    + "; reported by prometheus-watchdog, outside Prometheus."},
                 "startsAt": iso(since), "endsAt": iso(now + self.alert_ttl if firing else now)}]

    def _to_am(self, now, firing):
        """Post the alert to Alertmanager; False if Alertmanager did not take it."""
        try:
            self.post_alert(self._alert(now, firing))
        except Exception as e:  # noqa: BLE001  any failure means: Alertmanager did not take it
            self.log("prometheus", "alertmanager refused the alert", type(e).__name__)
            return False
        self.last_post, self.alert_in_am = now, firing
        return True

    def _direct(self, target, text):
        try:
            self.telegram(text)
            self.log(target, "sent to Telegram directly", text.split("\n")[0])
        except Exception as e:  # noqa: BLE001
            self.log(target, "Telegram send failed", type(e).__name__)

    def step(self, now):
        went_down, came_back = [], []
        for target in ("alertmanager", "prometheus"):
            ok = self.check(target)
            self.fails[target] = 0 if ok else self.fails[target] + 1
            if not ok and not self.down[target] and self.fails[target] >= self.failures:
                went_down.append(target)
            elif ok and self.down[target]:
                came_back.append(target)
        for target in came_back:
            self.down[target] = False
            self.log(target, "up", f"down for {now - self.down_since[target]:.0f} s")
        for target in went_down:
            self.down[target], self.down_since[target] = True, now
            self.log(target, "down", f"after {self.failures} failed checks")
        both = {"prometheus", "alertmanager"}
        if set(went_down) == both:
            self._direct("both", "🔴 <b>Prometheus and Alertmanager are down</b>: no alert can fire or be delivered")
        elif set(came_back) == both:
            if self.alert_in_am and self._to_am(now, False):
                self.log("prometheus", "alert resolved in Alertmanager", ALERT_NAME)
            self.alert_in_am = False
            self._direct("both", "✅ <b>Prometheus and Alertmanager are back</b>: alerts fire and are delivered again")
        else:
            for target in went_down:
                self._went_down(target, now)
            for target in came_back:
                self._came_back(target, now)
        if self.down["prometheus"] and not self.down["alertmanager"] and (self.last_post is None or now - self.last_post >= self.refresh):
            first = not self.alert_in_am
            if self._to_am(now, True) and first:
                self.log("prometheus", "alert posted to Alertmanager", ALERT_NAME)

    def _went_down(self, target, now):
        if target == "prometheus":
            if self.down["alertmanager"]:
                self._direct(target, "🔴 <b>Prometheus is down</b> (Alertmanager too): no alert can fire or be delivered")
            self.last_post = None             # Alertmanager up: step() posts PrometheusDown right after this
        else:
            extra = " (Prometheus too)" if self.down["prometheus"] else ""
            self._direct(target, f"🔴 <b>Alertmanager is down</b>{extra}: no alert reaches Telegram until it is back")

    def _came_back(self, target, now):
        if target == "prometheus":
            if self.alert_in_am and not self.down["alertmanager"]:
                if self._to_am(now, False):
                    self.log("prometheus", "alert resolved in Alertmanager", ALERT_NAME)
            elif self.down["alertmanager"]:
                self._direct(target, "✅ <b>Prometheus is back</b> (Alertmanager is still down)")
            self.alert_in_am = False
        else:
            self._direct(target, "✅ <b>Alertmanager is back</b>: alerts are delivered again")
            if self.down["prometheus"]:
                self.last_post = None         # post PrometheusDown now that someone can deliver it


def telegram_sender(token_file=TOKEN_FILE, env_file=ENV_FILE, api=TELEGRAM):
    def send(text):
        token = token_file.read_text().strip()
        chat = next(line.split("=", 1)[1].strip() for line in env_file.read_text().splitlines()
                    if line.startswith("TELEGRAM_CHAT_ID="))
        body = json.dumps({"chat_id": chat, "text": text + "\nDevSecMLOps · prometheus-watchdog",
                           "parse_mode": "HTML"}).encode()
        req = urllib.request.Request(f"{api}/bot{token}/sendMessage", data=body,
                                     headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                r.read()
        except urllib.error.HTTPError as e:      # the message names the status only, never the URL
            raise RuntimeError(f"Telegram answered {e.code}") from None
        except OSError as e:
            raise RuntimeError(f"Telegram unreachable ({type(e).__name__})") from None
    return send


def alert_poster(am):
    def post(alerts):
        req = urllib.request.Request(am + "/api/v2/alerts", data=json.dumps(alerts).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=5) as r:
            r.read()
    return post


def state_logger(path: Path, keep=200):
    def log(target, event, detail=""):
        entry = {"t": round(time.time(), 2), "time": time.strftime("%H:%M:%S"), "target": target,
                 "event": event, "detail": detail}
        print(f"{entry['time']} {target}: {event} {detail}".rstrip(), flush=True)
        try:
            old = json.loads(path.read_text()) if path.exists() else []
        except ValueError:
            old = []
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps((old + [entry])[-keep:], indent=1) + "\n")
    return log


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--prometheus", default="http://localhost:9090")
    ap.add_argument("--alertmanager", default="http://localhost:9093")
    ap.add_argument("--interval", type=float, default=5.0, help="seconds between checks")
    ap.add_argument("--failures", type=int, default=2, help="failed checks in a row before 'down'")
    ap.add_argument("--state", type=Path, default=Path("/run/prometheus-watchdog/events.json"))
    ap.add_argument("--once", action="store_true", help="check once, print, and exit")
    ap.add_argument("--token-file", type=Path, default=TOKEN_FILE, help=argparse.SUPPRESS)    # for tests
    ap.add_argument("--env-file", type=Path, default=ENV_FILE, help=argparse.SUPPRESS)
    ap.add_argument("--telegram-api", default=TELEGRAM, help=argparse.SUPPRESS)
    args = ap.parse_args()
    urls = {"prometheus": args.prometheus.rstrip("/") + "/-/healthy",
            "alertmanager": args.alertmanager.rstrip("/") + "/-/healthy"}
    if args.once:
        res = {k: http_ok(u) for k, u in urls.items()}
        print(json.dumps(res))
        return 0 if all(res.values()) else 1
    wd = Watchdog(check=lambda t: http_ok(urls[t]), post_alert=alert_poster(args.alertmanager.rstrip("/")),
                  telegram=telegram_sender(args.token_file, args.env_file, args.telegram_api), log=state_logger(args.state), failures=args.failures)
    wd.log("watchdog", "started", f"every {args.interval:g} s, down after {args.failures} failed checks")
    while True:
        start = time.time()
        wd.step(start)
        time.sleep(max(0.0, args.interval - (time.time() - start)))


if __name__ == "__main__":
    sys.exit(main())
