#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import secrets
import socket
import subprocess
import sys
from pathlib import Path


CONFIG_DIR = Path("/etc/drafthub")
CONFIG_PATH = CONFIG_DIR / "network.json"
DNSMASQ_CONFIG_PATH = CONFIG_DIR / "dnsmasq-ap.conf"
AP_CONNECTION = "DraftHub Management AP"
VENUE_CONNECTION = "DraftHub Venue Wi-Fi"
AP_INTERFACE = "dhap0"
ALLOW_ONBOARD_AP_ENV = "DRAFTHUB_ALLOW_ONBOARD_AP"
DEFAULT_AP_ADDRESS = "10.77.50.1"
DEFAULT_AP_RANGE = "10.77.50.20,10.77.50.120,12h"


def run(command: list[str], *, check: bool = True, input_text: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=check, input=input_text, capture_output=True, text=True)


def command_exists(command: str) -> bool:
    paths = os.environ.get("PATH", "").split(os.pathsep) + ["/usr/sbin", "/sbin"]
    return any((Path(path) / command).exists() for path in paths)


def output_json(value: object) -> None:
    print(json.dumps(value, separators=(",", ":")))


def nmcli(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return run(["nmcli", *args], check=check)


def iw(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    iw_path = "/usr/sbin/iw" if Path("/usr/sbin/iw").exists() else "iw"
    return run([iw_path, *args], check=check)


def read_config() -> dict[str, object]:
    if not CONFIG_PATH.exists():
        return {}
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def write_config(config: dict[str, object]) -> None:
    CONFIG_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(config, indent=2), encoding="utf-8")
    CONFIG_PATH.chmod(0o600)


def parse_iw_dev() -> dict[str, object]:
    result = iw("dev", check=False)
    interfaces: list[dict[str, str]] = []
    current_phy = ""
    current: dict[str, str] | None = None
    for raw_line in result.stdout.splitlines():
        line = raw_line.strip()
        if line.startswith("phy#"):
            current_phy = line.removeprefix("phy#")
        elif line.startswith("Interface "):
            if current:
                interfaces.append(current)
            current = {"name": line.split(maxsplit=1)[1], "phy": current_phy}
        elif current and line.startswith("type "):
            current["type"] = line.split(maxsplit=1)[1]
        elif current and line.startswith("ssid "):
            current["ssid"] = line.split(maxsplit=1)[1]
        elif current and line.startswith("addr "):
            current["mac"] = line.split(maxsplit=1)[1]
    if current:
        interfaces.append(current)
    managed = next((item for item in interfaces if item.get("type") == "managed"), None)
    ap = next((item for item in interfaces if item.get("name") == AP_INTERFACE), None)
    return {"interfaces": interfaces, "managed": managed, "ap": ap}


def read_mac(interface: str | None) -> str:
    if not interface:
        return ""
    path = Path("/sys/class/net") / interface / "address"
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def normalize_mac(mac: str) -> str:
    return "".join(part for part in mac.upper() if part in "0123456789ABCDEF")


def device_identity(managed_interface: str | None) -> dict[str, object]:
    mac = read_mac(managed_interface).replace(":", "").upper()
    normalized = normalize_mac(mac)
    suffix = normalized[-4:] if len(normalized) >= 4 else secrets.token_hex(2).upper()
    subnet_index = 20 + (int(suffix[-2:], 16) % 180)
    return {
        "device_id": suffix,
        "device_name": f"DraftHub-{suffix}",
        "ap_ssid": f"DraftHub-{suffix}",
        "ap_address": f"10.77.{subnet_index}.1",
        "ap_range": f"10.77.{subnet_index}.20,10.77.{subnet_index}.120,12h",
        "ap_subnet": f"10.77.{subnet_index}.0/24",
    }


def get_device_config(managed_interface: str | None) -> dict[str, str]:
    config = read_config()
    defaults = device_identity(managed_interface)
    changed = False
    for key, value in defaults.items():
        if not config.get(key):
            config[key] = value
            changed = True
    if not config.get("ap_ssid"):
        config["ap_ssid"] = defaults["ap_ssid"]
        changed = True
    if not config.get("ap_password"):
        config["ap_password"] = secrets.token_urlsafe(12)[:16]
        changed = True
    if changed:
        write_config(config)
    return {
        "device_id": str(config["device_id"]),
        "device_name": str(config["device_name"]),
        "ssid": str(config["ap_ssid"]),
        "password": str(config["ap_password"]),
        "ap_address": str(config.get("ap_address", DEFAULT_AP_ADDRESS)),
        "ap_range": str(config.get("ap_range", DEFAULT_AP_RANGE)),
        "ap_subnet": str(config.get("ap_subnet", "10.77.50.0/24")),
    }


def wifi_capability() -> dict[str, object]:
    if not command_exists("iw"):
        return {"available": False, "reason": "iw is not installed"}
    result = iw("list", check=False)
    text = result.stdout
    supports_managed = "* managed" in text
    supports_ap = "* AP" in text
    combo = "valid interface combinations:" in text and "managed" in text and "AP" in text
    return {
        "available": result.returncode == 0,
        "supports_managed": supports_managed,
        "supports_ap": supports_ap,
        "supports_concurrent_ap_sta": supports_managed and supports_ap and combo,
    }


def ensure_root() -> None:
    if os.geteuid() != 0:
        raise SystemExit("This command must run as root")


def ensure_ap_interface(phy: str) -> None:
    if Path(f"/sys/class/net/{AP_INTERFACE}").exists():
        return
    iw("phy", f"phy{phy}", "interface", "add", AP_INTERFACE, "type", "__ap")


def ensure_dnsmasq_config(ap_config: dict[str, str]) -> None:
    CONFIG_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    ap_address = ap_config["ap_address"]
    DNSMASQ_CONFIG_PATH.write_text(
        "\n".join(
            [
                f"interface={AP_INTERFACE}",
                "bind-interfaces",
                "domain-needed",
                "bogus-priv",
                f"dhcp-range={ap_config['ap_range']}",
                f"dhcp-option=3,{ap_address}",
                f"dhcp-option=6,{ap_address}",
                f"address=/#/{ap_address}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    DNSMASQ_CONFIG_PATH.chmod(0o644)


def ensure_ap() -> None:
    ensure_root()
    if os.environ.get(ALLOW_ONBOARD_AP_ENV) != "1":
        output_json(
            {
                "ok": False,
                "reason": (
                    "Onboard AP mode is disabled because the Radxa Zero 3W aic8800 "
                    "driver has proven unreliable for concurrent AP + STA on this image. "
                    "Use a dedicated USB Wi-Fi adapter for the DraftHub AP, or set "
                    f"{ALLOW_ONBOARD_AP_ENV}=1 for experimental testing."
                ),
            }
        )
        return
    if not command_exists("nmcli"):
        raise SystemExit("NetworkManager/nmcli is not installed")
    device_info = parse_iw_dev()
    managed = device_info.get("managed")
    if not isinstance(managed, dict):
        raise SystemExit("No managed Wi-Fi interface found")
    phy = str(managed.get("phy", "0"))
    capability = wifi_capability()
    if not capability.get("supports_concurrent_ap_sta"):
        raise SystemExit("Wi-Fi driver does not report concurrent AP + STA support")
    ensure_ap_interface(phy)
    ap_config = get_device_config(str(managed.get("name")))
    ensure_dnsmasq_config(ap_config)
    if nmcli("-t", "-f", "NAME", "con", "show", AP_CONNECTION, check=False).returncode:
        nmcli("con", "add", "type", "wifi", "ifname", AP_INTERFACE, "con-name", AP_CONNECTION, "ssid", ap_config["ssid"])
    nmcli(
        "con",
        "modify",
        AP_CONNECTION,
        "connection.autoconnect",
        "yes",
        "connection.autoconnect-priority",
        "-100",
        "802-11-wireless.mode",
        "ap",
        "802-11-wireless.band",
        "bg",
        "802-11-wireless-security.key-mgmt",
        "wpa-psk",
        "802-11-wireless-security.psk",
        ap_config["password"],
        "ipv4.method",
        "manual",
        "ipv4.addresses",
        f"{ap_config['ap_address']}/24",
        "ipv4.never-default",
        "yes",
        "ipv4.route-metric",
        "900",
        "ipv6.method",
        "disabled",
    )
    nmcli("con", "up", AP_CONNECTION, check=False)
    output_json(
        {
            "ok": True,
            "device_id": ap_config["device_id"],
            "device_name": ap_config["device_name"],
            "ap_ssid": ap_config["ssid"],
            "ap_interface": AP_INTERFACE,
            "ap_address": ap_config["ap_address"],
            "ap_subnet": ap_config["ap_subnet"],
        }
    )


def active_connections() -> list[dict[str, str]]:
    result = nmcli("-t", "-f", "NAME,TYPE,DEVICE", "con", "show", "--active", check=False)
    rows = []
    for line in result.stdout.splitlines():
        name, kind, device = (line.split(":") + ["", "", ""])[:3]
        rows.append({"name": name, "type": kind, "device": device})
    return rows


def internet_ok() -> bool:
    try:
        with socket.create_connection(("1.1.1.1", 53), timeout=2):
            return True
    except OSError:
        return False


def local_addresses() -> list[dict[str, str]]:
    result = run(["ip", "-j", "-4", "addr", "show"], check=False)
    try:
        devices = json.loads(result.stdout or "[]")
    except json.JSONDecodeError:
        return []
    addresses: list[dict[str, str]] = []
    for device in devices:
        ifname = str(device.get("ifname", ""))
        for info in device.get("addr_info", []):
            address = str(info.get("local", ""))
            if address and not address.startswith("127."):
                addresses.append({"interface": ifname, "address": address})
    return addresses


def status() -> None:
    device_info = parse_iw_dev() if command_exists("iw") else {"interfaces": [], "managed": None, "ap": None}
    managed = device_info.get("managed")
    managed_name = str(managed.get("name")) if isinstance(managed, dict) else None
    ap_config = get_device_config(managed_name) if os.geteuid() == 0 else read_config()
    route = run(["ip", "route", "show", "default"], check=False).stdout.strip()
    output_json(
        {
            "device_id": ap_config.get("device_id"),
            "device_name": ap_config.get("device_name"),
            "hostname": socket.gethostname(),
            "network_manager": command_exists("nmcli"),
            "dnsmasq": Path("/usr/sbin/dnsmasq").exists() or command_exists("dnsmasq"),
            "capability": wifi_capability(),
            "onboard_ap_enabled": os.environ.get(ALLOW_ONBOARD_AP_ENV) == "1",
            "managed_interface": managed_name,
            "ap_interface": AP_INTERFACE if Path(f"/sys/class/net/{AP_INTERFACE}").exists() else None,
            "ap_ssid": ap_config.get("ssid") or ap_config.get("ap_ssid"),
            "ap_address": ap_config.get("ap_address", DEFAULT_AP_ADDRESS),
            "ap_subnet": ap_config.get("ap_subnet", "10.77.50.0/24"),
            "venue_ssid": managed.get("ssid") if isinstance(managed, dict) else None,
            "active_connections": active_connections() if command_exists("nmcli") else [],
            "local_addresses": local_addresses(),
            "default_route": route,
            "internet": internet_ok(),
        }
    )


def scan() -> None:
    if not command_exists("nmcli"):
        raise SystemExit("NetworkManager/nmcli is not installed")
    device_info = parse_iw_dev()
    managed = device_info.get("managed")
    if not isinstance(managed, dict):
        raise SystemExit("No managed Wi-Fi interface found")
    iface = str(managed["name"])
    nmcli("dev", "wifi", "rescan", "ifname", iface, check=False)
    result = nmcli("-t", "-f", "SSID,SIGNAL,SECURITY", "dev", "wifi", "list", "ifname", iface, check=False)
    networks: dict[str, dict[str, object]] = {}
    for line in result.stdout.splitlines():
        parts = line.split(":")
        if len(parts) < 3 or not parts[0]:
            continue
        ssid, signal, security = parts[0], parts[1], ":".join(parts[2:])
        current = networks.get(ssid)
        item = {"ssid": ssid, "signal": int(signal or 0), "security": security}
        if current is None or int(current["signal"]) < item["signal"]:
            networks[ssid] = item
    output_json({"networks": sorted(networks.values(), key=lambda item: int(item["signal"]), reverse=True)})


def configure_venue() -> None:
    ensure_root()
    payload = json.loads(sys.stdin.read() or "{}")
    ssid = str(payload.get("ssid", "")).strip()
    password = str(payload.get("password", ""))
    if not ssid:
        raise SystemExit("Venue SSID is required")
    if not command_exists("nmcli"):
        raise SystemExit("NetworkManager/nmcli is not installed")
    device_info = parse_iw_dev()
    managed = device_info.get("managed")
    if not isinstance(managed, dict):
        raise SystemExit("No managed Wi-Fi interface found")
    iface = str(managed["name"])
    if nmcli("-t", "-f", "NAME", "con", "show", VENUE_CONNECTION, check=False).returncode:
        nmcli("con", "add", "type", "wifi", "ifname", iface, "con-name", VENUE_CONNECTION, "ssid", ssid)
    nmcli(
        "con",
        "modify",
        VENUE_CONNECTION,
        "connection.interface-name",
        iface,
        "connection.autoconnect",
        "yes",
        "connection.autoconnect-priority",
        "20",
        "802-11-wireless.ssid",
        ssid,
        "ipv4.method",
        "auto",
        "ipv4.route-metric",
        "100",
        "ipv6.method",
        "auto",
    )
    if password:
        nmcli(
            "con",
            "modify",
            VENUE_CONNECTION,
            "802-11-wireless-security.key-mgmt",
            "wpa-psk",
            "802-11-wireless-security.psk",
            password,
        )
    else:
        nmcli("con", "modify", VENUE_CONNECTION, "remove", "802-11-wireless-security.key-mgmt", check=False)
    result = nmcli("con", "up", VENUE_CONNECTION, check=False)
    output_json({"ok": result.returncode == 0, "ssid": ssid, "message": "Connected" if result.returncode == 0 else result.stderr.strip()})


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("status", "scan", "configure-venue", "ensure-ap"))
    args = parser.parse_args()
    if args.command == "status":
        status()
    elif args.command == "scan":
        scan()
    elif args.command == "configure-venue":
        configure_venue()
    elif args.command == "ensure-ap":
        ensure_ap()


if __name__ == "__main__":
    main()
