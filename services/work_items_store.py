import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from services.rules_store import BASE_DIR

WORK_ITEMS_PATH = BASE_DIR / "dictionaries" / "workflow_items.json"
LOCAL_WORK_ITEMS_PATH = BASE_DIR / "dictionaries" / "workflow_items.local.json"

DEFAULT_WORK_ITEMS = {"items": []}


def _path() -> Path:
    if LOCAL_WORK_ITEMS_PATH.exists():
        return LOCAL_WORK_ITEMS_PATH
    return WORK_ITEMS_PATH


def load_work_items() -> list[dict]:
    path = _path()
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    return list(payload.get("items", []))


def save_work_items(items: list[dict]) -> None:
    LOCAL_WORK_ITEMS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOCAL_WORK_ITEMS_PATH.open("w", encoding="utf-8") as handle:
        json.dump({"items": items}, handle, indent=2, sort_keys=True)
        handle.write("\n")


def create_work_item(
    title: str,
    stage: str,
    group_name: str,
    target_host: str,
    execution_asset_id: str = "",
    source_group: str = "",
    source_host: str = "",
    network_mode: str = "existing-lan-dhcp",
    requested_ip: str = "",
    gateway: str = "",
    dns_servers: str = "",
    firstboot_asset_id: str = "",
    validation_profile: str = "",
    notes: str = "",
) -> dict:
    return {
        "id": str(uuid4()),
        "title": title.strip(),
        "stage": stage.strip(),
        "group": group_name.strip(),
        "target_host": target_host.strip(),
        "source_group": source_group.strip(),
        "source_host": source_host.strip(),
        "execution_asset_id": execution_asset_id.strip(),
        "network_mode": network_mode.strip() or "existing-lan-dhcp",
        "requested_ip": requested_ip.strip(),
        "gateway": gateway.strip(),
        "dns_servers": dns_servers.strip(),
        "firstboot_asset_id": firstboot_asset_id.strip(),
        "validation_profile": validation_profile.strip(),
        "notes": notes.strip(),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "planned",
    }
