import json
from copy import deepcopy
from pathlib import Path

from services.secrets import decrypt_structure, encrypt_structure


BASE_DIR = Path(__file__).resolve().parents[1]
INTEGRATIONS_PATH = BASE_DIR / "dictionaries" / "integrations.json"
PROXMOX_SNAPSHOT_PATH = BASE_DIR / "dictionaries" / "proxmox_inventory.json"
ANSIBLE_SNAPSHOT_PATH = BASE_DIR / "dictionaries" / "ansible_scan.json"

DEFAULT_INTEGRATIONS = {
    "proxmox": {
        "api_url": "",
        "token_name": "",
        "token_value": "",
        "username": "",
        "password": "",
        "verify_ssl": False,
    },
    "ansible": {
        "controller_host": "",
        "controller_user": "",
        "controller_password": "",
        "playbook": "",
        "inventory_path": "",
        "config_root": "/etc/ansible",
    },
    "ssh": {
        "key_name": "bkc_id_rsa",
        "private_key_path": "keys/bkc_id_rsa",
        "public_key_path": "keys/bkc_id_rsa.pub",
    },
}


def load_integrations() -> dict:
    if not INTEGRATIONS_PATH.exists():
        return deepcopy(DEFAULT_INTEGRATIONS)

    with INTEGRATIONS_PATH.open("r", encoding="utf-8") as handle:
        data = decrypt_structure(json.load(handle))

    merged = deepcopy(DEFAULT_INTEGRATIONS)
    for section, values in data.items():
        if section in merged and isinstance(values, dict):
            merged[section].update(values)
    return merged


def save_integrations(integrations: dict) -> None:
    INTEGRATIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with INTEGRATIONS_PATH.open("w", encoding="utf-8") as handle:
        json.dump(encrypt_structure(integrations), handle, indent=2, sort_keys=True)
        handle.write("\n")


def load_snapshot(path: Path) -> dict | None:
    if not path.exists():
        return None

    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def save_snapshot(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
