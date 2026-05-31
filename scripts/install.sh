#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="/opt/drafthub-pi"
SERVICE_FILE="/etc/systemd/system/drafthub-pi.service"

apt-get update
apt-get install -y python3 python3-pygame

mkdir -p /var/lib/drafthub/media /var/lib/drafthub/state
cp "${PROJECT_DIR}/systemd/drafthub-pi.service" "${SERVICE_FILE}"

systemctl daemon-reload
systemctl enable drafthub-pi.service
systemctl restart drafthub-pi.service

echo "DraftHub Pi installed. Follow logs with: journalctl -u drafthub-pi -f"

