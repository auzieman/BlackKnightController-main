import json
import os
from copy import deepcopy
from pathlib import Path

from services.secrets import decrypt_structure, encrypt_structure
from services.tenant_context import get_effective_tenant_slug

BASE_DIR = Path(__file__).resolve().parents[1]
RULES_PATH = BASE_DIR / "dictionaries" / "rules.json"
LOCAL_RULES_PATH = BASE_DIR / "dictionaries" / "rules.local.json"
TEMPLATES_PATH = BASE_DIR / "file_templates"

DEFAULT_RULES = {
    "globals": {
        "env": "lab",
        "datacenter": "homelab",
        "release": "draft",
    },
    "groups": {},
}


def _tenant_rules_path(slug: str) -> Path:
    return BASE_DIR / "dictionaries" / "tenants" / slug / "rules.local.json"


def get_rules_path() -> Path:
    """
    Tenant-scoped inventory file. Legacy fallbacks apply only for slug ``default``.
    """
    slug = get_effective_tenant_slug()
    tenant_path = _tenant_rules_path(slug)
    if tenant_path.exists():
        return tenant_path
    if slug == "default":
        if LOCAL_RULES_PATH.exists():
            return LOCAL_RULES_PATH
        return RULES_PATH
    return tenant_path


def get_templates_path() -> Path:
    slug = get_effective_tenant_slug()
    tenant_templates = BASE_DIR / "dictionaries" / "tenants" / slug / "file_templates"
    if tenant_templates.is_dir():
        return tenant_templates
    return TEMPLATES_PATH


def load_rules() -> dict:
    rules_path = get_rules_path()
    if not rules_path.exists():
        return deepcopy(DEFAULT_RULES)

    with rules_path.open("r", encoding="utf-8") as handle:
        data = decrypt_structure(json.load(handle))

    if "groups" not in data:
        data["groups"] = {}
    if "globals" not in data:
        data["globals"] = deepcopy(DEFAULT_RULES["globals"])

    return data


def save_rules(rules: dict) -> None:
    slug = get_effective_tenant_slug()
    path = _tenant_rules_path(slug)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(encrypt_structure(rules), handle, indent=2, sort_keys=True)
        handle.write("\n")
    # Optional one-way hint for operators migrating from legacy layout
    if slug == "default" and os.environ.get("BKC_REMOVE_LEGACY_RULES_LOCAL", "").lower() in ("1", "true", "yes"):
        try:
            if LOCAL_RULES_PATH.exists() and LOCAL_RULES_PATH.resolve() != path.resolve():
                LOCAL_RULES_PATH.unlink()
        except OSError:
            pass
