#!/usr/bin/env bash
set -euo pipefail

echo "[1/3] restart workflow-ui"
sudo systemctl restart workflow-ui.service

echo "[2/3] service state"
systemctl is-active workflow-ui.service

echo "[3/3] health"
for i in $(seq 1 10); do
  if curl -fsS -m 10 http://127.0.0.1:8600/api/health >/tmp/workflow_ui_health.json 2>/dev/null; then
    cat /tmp/workflow_ui_health.json
    echo
    exit 0
  fi
  sleep 1
done
echo "health check failed after retries"
exit 1
echo
