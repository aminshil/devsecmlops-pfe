#!/bin/bash
# Validate the alerting configuration without changing anything:
#   1. promtool check rules     monitoring/alerting/alert_rules.yml
#   2. promtool test rules      monitoring/alerting/alert_rules_test.yml  (every alert fires in its scenario,
#                               stays silent otherwise)
#   3. promtool check config    monitoring/prometheus.yml  (--syntax-only off the VM: the TLS files exist only there)
#   4. amtool check-config      the Alertmanager template rendered with a placeholder chat id
# Uses promtool/amtool from PATH when present, otherwise the pinned Docker images.
set -euo pipefail
cd "$(dirname "$0")/.."
PROM_IMG=prom/prometheus:v2.53.2
AM_IMG=prom/alertmanager:v0.27.0

run_promtool() {
  if command -v promtool >/dev/null 2>&1; then promtool "$@"
  else docker run --rm --network none -v "$PWD/monitoring:/w/monitoring:ro" -w /w --entrypoint promtool "$PROM_IMG" "$@"; fi
}
run_amtool() {   # $1 = directory holding alertmanager.yml, telegram.tmpl, telegram_token
  if command -v amtool >/dev/null 2>&1; then amtool check-config "$1/alertmanager.yml"
  else docker run --rm --network none -v "$1:/etc/alertmanager:ro" --entrypoint amtool "$AM_IMG" check-config /etc/alertmanager/alertmanager.yml; fi
}

echo "== 1. rules"
run_promtool check rules monitoring/alerting/alert_rules.yml
echo "== 2. rule unit tests"
run_promtool test rules monitoring/alerting/alert_rules_test.yml
echo "== 3. prometheus.yml"
if command -v promtool >/dev/null 2>&1 && [ -f /home/pfe/.minikube/ca.crt ]; then   # full check needs the TLS files
  run_promtool check config monitoring/prometheus.yml
else
  run_promtool check config --syntax-only monitoring/prometheus.yml
fi
echo "== 4. alertmanager.yml (rendered with a placeholder chat id)"
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
if command -v amtool >/dev/null 2>&1; then dir="$tmp"; else dir=/etc/alertmanager; fi
sed -e 's/__TELEGRAM_CHAT_ID__/123456789/' -e "s#/etc/alertmanager#$dir#g" \
    monitoring/alerting/alertmanager.yml.tmpl > "$tmp/alertmanager.yml"
cp monitoring/alerting/telegram.tmpl "$tmp/"
echo placeholder > "$tmp/telegram_token"
chmod 755 "$tmp"; chmod 644 "$tmp"/*
run_amtool "$tmp"
echo "alerting configuration OK"
