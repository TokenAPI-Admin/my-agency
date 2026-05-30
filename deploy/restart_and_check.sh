#!/usr/bin/env bash
set -euo pipefail

echo "[1/3] restart workflow-ui"
sudo systemctl restart workflow-ui.service

echo "[2/3] service state"
systemctl is-active workflow-ui.service

echo "[3/3] health"
curl -fsS -m 10 http://127.0.0.1:8600/api/health
echo

