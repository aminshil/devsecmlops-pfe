#!/bin/bash
# Install alerting on the VM: Prometheus rules -> Alertmanager -> Telegram, plus HTTP probes of every
# platform layer. Run as root from the repository, on the VM:
#
#   sudo bash scripts/setup_alerting.sh            # asks for the bot token once, finds the chat id
#   sudo bash scripts/setup_alerting.sh --test     # only send a test alert through the whole chain
#
# What it does, in order (it stops at the first failure, before changing anything if a check fails):
#   1. bot token: read once without echo, checked with Telegram's getMe, stored root-only in
#      /etc/devsecmlops/alertmanager/telegram_token (never in the repository, never printed);
#      chat id: found from the message you send to the bot (getUpdates), stored in alerting.env
#   2. scripts/check_alerting.sh: rules, rule unit tests, prometheus.yml, alertmanager.yml
#   3. Alertmanager (127.0.0.1:9093) and the blackbox exporter (:9115) as containers that restart with Docker
#   4. Prometheus restarted (its config, monitoring/prometheus.yml, now loads the rules)
#   5. the watchdog (prometheus-watchdog.service): reports Prometheus or Alertmanager down, from outside both
#   6. verification: rules loaded, Alertmanager connected, each layer's probe result, the watchdog, a test alert
set -euo pipefail
cd "$(dirname "$0")/.."
REPO="$PWD"
CONF=/etc/devsecmlops/alertmanager
ENVF=/etc/devsecmlops/alerting.env
AM_IMG=prom/alertmanager:v0.28.1
BB_IMG=prom/blackbox-exporter:v0.25.0
TG=https://api.telegram.org

[ "$(id -u)" = 0 ] || { echo "run as root (sudo)"; exit 1; }
for c in docker curl python3 systemctl; do command -v "$c" >/dev/null || { echo "missing: $c"; exit 1; }; done

json() { python3 -c "import sys,json; d=json.load(sys.stdin); print(eval(sys.argv[1]))" "$1"; }
# Telegram call with the token passed on stdin (curl --config -), never on the command line or in ps output
tg() { printf 'url = "%s/bot%s/%s"\n' "$TG" "$1" "$2" | curl -s --max-time 10 --config -; }

send_test_alert() {
  python3 - <<'EOF'
import datetime, json, urllib.request
now = datetime.datetime.now(datetime.timezone.utc)
alert = [{"labels": {"alertname": "AlertingTest", "severity": "info"},
          "annotations": {"summary": "test alert: Prometheus -> Alertmanager -> Telegram works",
                          "description": "Sent by scripts/setup_alerting.sh; it resolves by itself in 2 minutes."},
          "startsAt": now.isoformat(), "endsAt": (now + datetime.timedelta(minutes=2)).isoformat()}]
req = urllib.request.Request("http://localhost:9093/api/v2/alerts", data=json.dumps(alert).encode(),
                             headers={"Content-Type": "application/json"}, method="POST")
print("test alert accepted by Alertmanager:", urllib.request.urlopen(req, timeout=10).status == 200)
EOF
  echo "it should reach Telegram within a few seconds"
}

if [ "${1:-}" = "--test" ]; then send_test_alert; exit 0; fi

echo "== 1. Telegram"
mkdir -p "$CONF"; chmod 755 "$CONF"
if [ ! -s "$CONF/telegram_token" ]; then
  read -rsp "Telegram bot token (from @BotFather, not shown): " TOKEN; echo
  ok=$(tg "$TOKEN" getMe | json 'd.get("ok")') || ok=False
  [ "$ok" = True ] || { echo "Telegram refused this token (getMe failed): nothing written"; exit 1; }
  umask 077; printf '%s' "$TOKEN" > "$CONF/telegram_token"; umask 022
  chown 65534:65534 "$CONF/telegram_token"; chmod 400 "$CONF/telegram_token"   # Alertmanager runs as nobody
  unset TOKEN
  echo "token valid and stored root-only"
fi
TOKEN_FILE="$CONF/telegram_token"
# shellcheck source=/dev/null
CHAT_ID=$( [ -f "$ENVF" ] && . "$ENVF" && echo "${TELEGRAM_CHAT_ID:-}" || true )
if [ -z "$CHAT_ID" ]; then
  name=$(tg "$(cat "$TOKEN_FILE")" getMe | json 'd["result"]["username"]')
  read -rp "Open Telegram, send any message to @$name, then press Enter here..." _
  CHAT_ID=$(tg "$(cat "$TOKEN_FILE")" getUpdates | \
            json '([u["message"]["chat"]["id"] for u in d.get("result", []) if "message" in u] or [""])[-1]')
  [ -n "$CHAT_ID" ] || { echo "no message found: send one to @$name and run the script again"; exit 1; }
  printf 'TELEGRAM_CHAT_ID=%s\n' "$CHAT_ID" > "$ENVF"; chmod 600 "$ENVF"
fi
echo "chat id: $CHAT_ID"

echo "== 2. checks (nothing installed if one fails)"
bash scripts/check_alerting.sh

echo "== 3. Alertmanager and blackbox exporter"
sed "s/__TELEGRAM_CHAT_ID__/$CHAT_ID/" monitoring/alerting/alertmanager.yml.tmpl > "$CONF/alertmanager.yml"
cp monitoring/alerting/telegram.tmpl "$CONF/telegram.tmpl"
chmod 644 "$CONF/alertmanager.yml" "$CONF/telegram.tmpl"
docker rm -f alertmanager blackbox-exporter >/dev/null 2>&1 || true
docker run -d --name blackbox-exporter --restart unless-stopped --network host \
  -v "$REPO/monitoring/alerting/blackbox.yml:/etc/blackbox_exporter/config.yml:ro" "$BB_IMG" >/dev/null
# Alertmanager answers on 127.0.0.1 only: its API takes silences with no authentication, so from the LAN
# anyone could mute every alert. Prometheus, the watchdog and the panel (its Mute) are on this host.
docker run -d --name alertmanager --restart unless-stopped --network host \
  -v "$CONF:/etc/alertmanager:ro" -v alertmanager-data:/alertmanager "$AM_IMG" \
  --config.file=/etc/alertmanager/alertmanager.yml --storage.path=/alertmanager \
  --web.listen-address=127.0.0.1:9093 --cluster.listen-address= >/dev/null
for _ in $(seq 1 30); do curl -sf localhost:9093/-/ready >/dev/null && curl -sf localhost:9115/-/healthy >/dev/null && break; sleep 2; done
curl -sf localhost:9093/-/ready >/dev/null || { echo "Alertmanager not ready:"; docker logs --tail 20 alertmanager; exit 1; }
curl -sf localhost:9115/-/healthy >/dev/null || { echo "blackbox exporter not ready:"; docker logs --tail 20 blackbox-exporter; exit 1; }
echo "Alertmanager 127.0.0.1:9093 (this host only) and blackbox exporter :9115 up"

echo "== 4. Prometheus"
systemctl restart prometheus
for _ in $(seq 1 30); do curl -sf localhost:9090/-/ready >/dev/null && break; sleep 2; done
curl -sf localhost:9090/-/ready >/dev/null || { echo "Prometheus not ready:"; journalctl -u prometheus -n 20 --no-pager; exit 1; }

echo "== 5. watchdog (Prometheus or Alertmanager down -> Telegram, from outside both)"
cat > /etc/systemd/system/prometheus-watchdog.service <<UNIT
[Unit]
Description=Watchdog: reports Prometheus or Alertmanager down on Telegram (scripts/prometheus_watchdog.py)
After=network-online.target docker.service

[Service]
ExecStart=/usr/bin/python3 $REPO/scripts/prometheus_watchdog.py
Restart=always
RestartSec=5
RuntimeDirectory=prometheus-watchdog
RuntimeDirectoryPreserve=yes

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable --now prometheus-watchdog >/dev/null 2>&1
systemctl restart prometheus-watchdog
sleep 2
systemctl is-active --quiet prometheus-watchdog || { echo "watchdog not running:"; journalctl -u prometheus-watchdog -n 20 --no-pager; exit 1; }
echo "prometheus-watchdog running: $(python3 scripts/prometheus_watchdog.py --once)"

echo "== 6. verification"
sleep 20   # one scrape and one rule evaluation
rules=$(curl -s localhost:9090/api/v1/rules | json 'sum(len(g["rules"]) for g in d["data"]["groups"])')
ams=$(curl -s localhost:9090/api/v1/alertmanagers | json 'len(d["data"]["activeAlertmanagers"])')
expected=$(grep -cE '^\s*- (alert|record):' monitoring/alerting/alert_rules.yml)   # every rule in the file
echo "rules loaded: $rules (expected $expected)   alertmanagers connected: $ams (expected 1)"
echo "layer probes (1 = up):"
curl -sG localhost:9090/api/v1/query --data-urlencode 'query=probe_success{job="blackbox-layers"}' | \
  python3 -c "import sys,json; [print(f'   {r[\"metric\"][\"layer\"]:<32} {r[\"value\"][1]}') for r in json.load(sys.stdin)['data']['result']]"
send_test_alert
echo "done. Rules and states: http://<vm-ip>:9090/alerts   Alertmanager: on this host only (ssh -L 9093:127.0.0.1:9093 to open its UI)"
