#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="/opt/drafthub-pi"
SERVICE_FILE="/etc/systemd/system/drafthub-pi.service"
APP_USER="drafthub"
ENABLE_NETWORKING=0
ENABLE_ONBOARD_AP=0

for arg in "$@"; do
    case "${arg}" in
        --enable-networking)
            ENABLE_NETWORKING=1
            ;;
        --enable-onboard-ap)
            ENABLE_NETWORKING=1
            ENABLE_ONBOARD_AP=1
            ;;
        *)
            echo "Unknown installer option: ${arg}" >&2
            exit 2
            ;;
    esac
done

apt-get update
apt-get install -y python3 python3-pygame ffmpeg

if [[ "${ENABLE_NETWORKING}" == "1" ]]; then
    apt-get install -y network-manager dnsmasq-base iw
fi

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

install -m 0755 "${PROJECT_DIR}/scripts/drafthub_network.py" /usr/local/sbin/drafthub-network
cat >/etc/sudoers.d/drafthub-network <<EOF
${APP_USER} ALL=(root) NOPASSWD: /usr/local/sbin/drafthub-network status, /usr/local/sbin/drafthub-network scan, /usr/local/sbin/drafthub-network configure-venue
EOF
chmod 0440 /etc/sudoers.d/drafthub-network

if [[ "${ENABLE_NETWORKING}" == "1" ]]; then
    cp "${PROJECT_DIR}/systemd/drafthub-network.service" /etc/systemd/system/drafthub-network.service
    cp "${PROJECT_DIR}/systemd/drafthub-ap-dnsmasq.service" /etc/systemd/system/drafthub-ap-dnsmasq.service

    if [[ "${ENABLE_ONBOARD_AP}" == "1" && -d /etc/netplan ]]; then
        mkdir -p /etc/drafthub/netplan-backup
        cp -an /etc/netplan/. /etc/drafthub/netplan-backup/ || true
        for netplan_file in /etc/netplan/*.yaml; do
            [[ -e "${netplan_file}" ]] || continue
            if grep -q "wifis:" "${netplan_file}"; then
                mv "${netplan_file}" "${netplan_file}.drafthub-disabled"
            fi
        done
        cat >/etc/netplan/90-drafthub-networkmanager.yaml <<'EOF'
network:
  version: 2
  renderer: NetworkManager
EOF
    fi

    if [[ "${ENABLE_ONBOARD_AP}" == "1" ]]; then
        systemctl enable NetworkManager.service
        systemctl enable drafthub-network.service drafthub-ap-dnsmasq.service
    fi
fi

systemctl daemon-reload
systemctl enable drafthub-pi.service
if [[ "${ENABLE_ONBOARD_AP}" == "1" ]]; then
    if command -v netplan >/dev/null 2>&1; then
        netplan generate
        netplan apply || true
    fi
    systemctl restart NetworkManager.service
    systemctl restart drafthub-network.service || true
    systemctl restart drafthub-ap-dnsmasq.service || true
else
    systemctl disable --now drafthub-network.service drafthub-ap-dnsmasq.service >/dev/null 2>&1 || true
    if command -v iw >/dev/null 2>&1 && iw dev dhap0 info >/dev/null 2>&1; then
        iw dev dhap0 del || true
    fi
fi
systemctl restart drafthub-pi.service

echo "DraftHub installed for ${APP_USER}. Follow logs with: journalctl -u drafthub-pi -f"
if [[ "${ENABLE_NETWORKING}" == "1" ]]; then
    echo "DraftHub network helper installed. AP services are not enabled unless --enable-onboard-ap is used."
fi
if [[ "${ENABLE_ONBOARD_AP}" == "1" ]]; then
    echo "Experimental onboard AP enabled. AP details are stored in /etc/drafthub/network.json."
fi
