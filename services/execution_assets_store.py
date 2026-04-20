import json
from pathlib import Path

from services.integration_store import ANSIBLE_SNAPSHOT_PATH, load_snapshot
from services.rules_store import BASE_DIR
from services.template_assets import load_template_assets


EXECUTION_ASSETS_PATH = BASE_DIR / "dictionaries" / "execution_assets.json"


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {"assets": []}
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def load_execution_assets() -> list[dict]:
    payload = _load_json(EXECUTION_ASSETS_PATH)
    assets = list(payload.get("assets", []))
    seen_ids = {asset.get("id") for asset in assets}

    for asset in load_template_assets():
        asset_id = asset["id"]
        if asset_id in seen_ids:
            continue
        assets.append(
            {
                "id": asset_id,
                "type": "bkc-template",
                "name": asset["name"],
                "label": f"{asset['group']} / {asset['name']}",
                "group": asset["group"],
                "template_file": asset["template_file"],
                "output": asset["output"],
                "description": f"Render {asset['template_file']} to {asset['output']}",
            }
        )
        seen_ids.add(asset_id)

    ansible_scan = load_snapshot(ANSIBLE_SNAPSHOT_PATH) or {}
    for playbook in ansible_scan.get("playbooks", []):
        asset_id = f"ansible:{playbook}"
        if asset_id in seen_ids:
            continue
        assets.append(
            {
                "id": asset_id,
                "type": "ansible-playbook",
                "name": Path(playbook).name,
                "label": f"ansible / {Path(playbook).name}",
                "playbook": playbook,
                "description": f"Run playbook {playbook} on the configured controller.",
            }
        )
        seen_ids.add(asset_id)

    for asset in [
        {
            "id": "probe:ssh-fingerprint",
            "type": "light-probe",
            "name": "ssh-fingerprint",
            "label": "probe / ssh fingerprint",
            "description": "Check for TCP/22 and inspect the SSH banner to infer a likely Linux/OpenSSH target.",
        },
        {
            "id": "firstboot:network-personalize",
            "type": "firstboot",
            "name": "network-personalize",
            "label": "firstboot / network personalize",
            "description": "Apply hostname, IP, gateway, and DNS settings after clone or first boot.",
        },
    ]:
        if asset["id"] not in seen_ids:
            assets.append(asset)
            seen_ids.add(asset["id"])

    return sorted(assets, key=lambda item: (item.get("type", ""), item.get("label", item.get("name", ""))))
