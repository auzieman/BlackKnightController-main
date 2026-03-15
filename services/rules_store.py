import json
from copy import deepcopy
from pathlib import Path

from services.secrets import decrypt_structure, encrypt_structure


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


def get_rules_path() -> Path:
    if LOCAL_RULES_PATH.exists():
        return LOCAL_RULES_PATH
    return RULES_PATH


def get_templates_path() -> Path:
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
    LOCAL_RULES_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOCAL_RULES_PATH.open("w", encoding="utf-8") as handle:
        json.dump(encrypt_structure(rules), handle, indent=2, sort_keys=True)
        handle.write("\n")
