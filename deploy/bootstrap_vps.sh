#!/usr/bin/env bash
set -euo pipefail

APP_ROOT="/home/ubuntu/video-shopping"
ENV_DIR="/etc/video-shopping"
ENV_FILE="${ENV_DIR}/workflow-ui.env"
SERVICE_FILE="/etc/systemd/system/workflow-ui.service"

echo "[1/6] install system packages"
sudo apt-get update -y
sudo apt-get install -y python3 python3-pip ffmpeg curl

echo "[2/6] create runtime directories"
mkdir -p "${APP_ROOT}/workflow_ui/runs"
mkdir -p "${APP_ROOT}/workflow_ui/content_runs"
mkdir -p "${APP_ROOT}/out"
sudo mkdir -p "${ENV_DIR}"

echo "[3/6] install python dependencies"
python3 -m pip install --upgrade pip
python3 -m pip install -r "${APP_ROOT}/requirements.txt"

echo "[4/6] install env template when missing"
if [[ ! -f "${ENV_FILE}" ]]; then
  sudo cp "${APP_ROOT}/deploy/workflow-ui.env.example" "${ENV_FILE}"
  sudo chown root:root "${ENV_FILE}"
  sudo chmod 600 "${ENV_FILE}"
  echo "created ${ENV_FILE} (please edit OPENAI_API_KEY)"
fi

echo "[5/6] install systemd service"
sudo cp "${APP_ROOT}/deploy/workflow-ui.service.template" "${SERVICE_FILE}"
sudo systemctl daemon-reload
sudo systemctl enable workflow-ui.service

echo "[6/6] restart and health-check"
sudo systemctl restart workflow-ui.service
sleep 1
systemctl is-active workflow-ui.service
curl -fsS -m 10 http://127.0.0.1:8600/api/health >/dev/null
echo "ok: workflow-ui is healthy"

