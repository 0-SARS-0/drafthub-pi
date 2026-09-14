#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="/opt/drafthub-pi"
SERVICE_FILE="/etc/systemd/system/drafthub-pi.service"
APP_USER="drafthub"

apt-get update
apt-get install -y python3 python3-pygame ffmpeg

if ! id -u "${APP_USER}" >/dev/null 2>&1; then
    useradd --system --home-dir /var/lib/drafthub --shell /usr/sbin/nologin "${APP_USER}"
fi

for group in video render input; do
    if getent group "${group}" >/dev/null; then
        usermod -aG "${group}" "${APP_USER}"
    fi
done

mkdir -p /var/lib/drafthub/media /var/lib/drafthub/state
chown -R "${APP_USER}:${APP_USER}" /var/lib/drafthub
cp "${PROJECT_DIR}/systemd/drafthub-pi.service" "${SERVICE_FILE}"

systemctl daemon-reload
systemctl enable drafthub-pi.service
systemctl restart drafthub-pi.service

echo "DraftHub installed for ${APP_USER}. Follow logs with: journalctl -u drafthub-pi -f"
