import json
from copy import deepcopy
from pathlib import Path

from services.secrets import decrypt_structure, encrypt_structure
from services.tenant_context import get_effective_tenant_slug

BASE_DIR = Path(__file__).resolve().parents[1]

# Legacy single-tenant files (still read for ``default`` when per-tenant files are absent).
LEGACY_INTEGRATIONS_PATH = BASE_DIR / "dictionaries" / "integrations.json"
LEGACY_PROXMOX_SNAPSHOT_PATH = BASE_DIR / "dictionaries" / "proxmox_inventory.json"
LEGACY_ANSIBLE_SNAPSHOT_PATH = BASE_DIR / "dictionaries" / "ansible_scan.json"
LEGACY_DOCKER_SNAPSHOT_PATH = BASE_DIR / "dictionaries" / "docker_scan.json"

# Deprecated module-level paths — use get_*() for tenant-aware resolution.
INTEGRATIONS_PATH = LEGACY_INTEGRATIONS_PATH
PROXMOX_SNAPSHOT_PATH = LEGACY_PROXMOX_SNAPSHOT_PATH
ANSIBLE_SNAPSHOT_PATH = LEGACY_ANSIBLE_SNAPSHOT_PATH
DOCKER_SNAPSHOT_PATH = LEGACY_DOCKER_SNAPSHOT_PATH

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
    "docker": {
        "manager_host": "",
        "manager_user": "",
        "manager_password": "",
        "stack_name": "",
    },
    "ssh": {
        "key_name": "bkc_id_rsa",
        "private_key_path": "keys/bkc_id_rsa",
        "public_key_path": "keys/bkc_id_rsa.pub",
    },
}


def _tenant_slug(slug: str | None = None) -> str:
    s = (slug or get_effective_tenant_slug()).strip().lower()
    return s or "default"


def get_integrations_path(slug: str | None = None) -> Path:
    return BASE_DIR / "dictionaries" / "tenants" / _tenant_slug(slug) / "integrations.json"


def get_proxmox_snapshot_path(slug: str | None = None) -> Path:
    return BASE_DIR / "dictionaries" / "tenants" / _tenant_slug(slug) / "proxmox_inventory.json"


def get_ansible_snapshot_path(slug: str | None = None) -> Path:
    return BASE_DIR / "dictionaries" / "tenants" / _tenant_slug(slug) / "ansible_scan.json"


def get_docker_snapshot_path(slug: str | None = None) -> Path:
    return BASE_DIR / "dictionaries" / "tenants" / _tenant_slug(slug) / "docker_scan.json"


def _merge_integrations_payload(data: dict) -> dict:
    merged = deepcopy(DEFAULT_INTEGRATIONS)
    for section, values in data.items():
        if section in merged and isinstance(values, dict):
            merged[section].update(values)
    return merged


def _load_integrations_from_path(path: Path) -> dict | None:
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as handle:
        data = decrypt_structure(json.load(handle))
    return _merge_integrations_payload(data)


def load_integrations() -> dict:
    slug = _tenant_slug()
    tenant_path = get_integrations_path(slug)
    loaded = _load_integrations_from_path(tenant_path)
    if loaded is not None:
        return loaded
    if slug == "default" and LEGACY_INTEGRATIONS_PATH.exists():
        legacy = _load_integrations_from_path(LEGACY_INTEGRATIONS_PATH)
        if legacy is not None:
            return legacy
    return deepcopy(DEFAULT_INTEGRATIONS)


def save_integrations(integrations: dict) -> None:
    path = get_integrations_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(encrypt_structure(integrations), handle, indent=2, sort_keys=True)
        handle.write("\n")


def load_snapshot(path: Path) -> dict | None:
    if not path.exists():
        return None

    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def load_proxmox_snapshot() -> dict | None:
    slug = _tenant_slug()
    p = get_proxmox_snapshot_path(slug)
    data = load_snapshot(p)
    if data is not None:
        return data
    if slug == "default":
        return load_snapshot(LEGACY_PROXMOX_SNAPSHOT_PATH)
    return None


def load_ansible_snapshot() -> dict | None:
    slug = _tenant_slug()
    p = get_ansible_snapshot_path(slug)
    data = load_snapshot(p)
    if data is not None:
        return data
    if slug == "default":
        return load_snapshot(LEGACY_ANSIBLE_SNAPSHOT_PATH)
    return None


def load_docker_snapshot() -> dict | None:
    slug = _tenant_slug()
    p = get_docker_snapshot_path(slug)
    data = load_snapshot(p)
    if data is not None:
        return data
    if slug == "default":
        return load_snapshot(LEGACY_DOCKER_SNAPSHOT_PATH)
    return None


def save_snapshot(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")


def save_proxmox_snapshot(payload: dict) -> None:
    save_snapshot(get_proxmox_snapshot_path(), payload)


def save_ansible_snapshot(payload: dict) -> None:
    save_snapshot(get_ansible_snapshot_path(), payload)


def save_docker_snapshot(payload: dict) -> None:
    save_snapshot(get_docker_snapshot_path(), payload)
