import json
from pathlib import PurePosixPath

try:
    from jinja2 import Environment, FileSystemLoader
except ImportError:  # pragma: no cover - runtime dependent
    Environment = None
    FileSystemLoader = None

from services.inventory_model import merge_host_config
from services.rules_store import BASE_DIR, get_templates_path, load_rules

GROUP_VARS_PATH = BASE_DIR / "dictionaries" / "group_vars.json"
GLOBAL_VARS_PATH = BASE_DIR / "dictionaries" / "globals.json"
ENV_VARS_PATH = BASE_DIR / "dictionaries" / "env_vars.json"


class TemplateAssetError(RuntimeError):
    pass


def _load_json(path):
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def load_template_assets() -> list[dict]:
    payload = _load_json(GROUP_VARS_PATH)
    assets = []
    for group in payload.get("groups", []):
        group_name = group.get("name", "")
        group_vars = group.get("group_vars", {})
        for template in group.get("templates", []):
            template_file = template.get("template", "")
            template_name = template.get("name", "") or PurePosixPath(template_file).stem
            assets.append(
                {
                    "id": f"{group_name}:{template_name}",
                    "group": group_name,
                    "name": template_name,
                    "template_file": PurePosixPath(template_file).name or template_name,
                    "output": template.get("output", ""),
                    "os": template.get("os", "linux"),
                    "auth_mode": template.get("auth_mode", "ssh"),
                    "vars": template.get("vars", {}),
                    "group_vars": group_vars,
                }
            )
    return assets


def _asset_by_id(asset_id: str) -> dict:
    for asset in load_template_assets():
        if asset["id"] == asset_id:
            return asset
    raise TemplateAssetError(f"Unknown template asset {asset_id}.")


def _render_asset(asset: dict, group_name: str, host_name: str) -> str:
    if Environment is None or FileSystemLoader is None:
        raise TemplateAssetError("jinja2 is required to render template assets. Install requirements.txt first.")
    rules = load_rules()
    group = rules["groups"].get(group_name)
    if not group:
        raise TemplateAssetError(f"Unknown target group {group_name}.")
    node = group.get("nodes", {}).get(host_name)
    if not node:
        raise TemplateAssetError(f"Unknown target host {host_name}.")

    globals_payload = _load_json(GLOBAL_VARS_PATH)
    env_payload = _load_json(ENV_VARS_PATH)
    resolved = merge_host_config(rules.get("globals", {}), group, node)

    template_vars = {}
    template_vars.update(globals_payload.get("deployment_vars", {}))
    template_vars.update(env_payload)
    template_vars.update(group.get("locals", {}))
    template_vars.update(asset.get("group_vars", {}))
    template_vars.update(resolved)
    template_vars.update(asset.get("vars", {}))
    template_vars["host_name"] = host_name

    env = Environment(loader=FileSystemLoader(str(get_templates_path())))
    template = env.get_template(asset["template_file"])
    return template.render(template_vars)


def run_template_asset(targets: list[tuple[str, str]], asset_id: str) -> list[dict]:
    from services.remote_admin import RemoteAdminError, connect_host, run_client_command

    asset = _asset_by_id(asset_id)
    if asset.get("auth_mode") != "ssh":
        raise TemplateAssetError(f"Template asset {asset_id} uses unsupported auth mode {asset.get('auth_mode')}.")

    results = []
    for group_name, host_name in targets:
        try:
            rendered = _render_asset(asset, group_name, host_name)
            client, _, auth_method = connect_host(group_name, host_name)
            try:
                remote_dir = str(PurePosixPath(asset["output"]).parent)
                if remote_dir and remote_dir != ".":
                    run_client_command(client, f"mkdir -p '{remote_dir}'")
                sftp = client.open_sftp()
                try:
                    with sftp.file(asset["output"], "w") as handle:
                        handle.write(rendered)
                finally:
                    sftp.close()
            finally:
                client.close()

            results.append(
                {
                    "target": host_name,
                    "group": group_name,
                    "command": f"render {asset['template_file']} -> {asset['output']}",
                    "auth_method": auth_method,
                    "stdout": f"Wrote {asset['template_file']} to {asset['output']}",
                    "stderr": "",
                    "exit_status": 0,
                }
            )
        except (TemplateAssetError, RemoteAdminError, OSError) as exc:
            results.append(
                {
                    "target": host_name,
                    "group": group_name,
                    "command": f"render {asset['template_file']} -> {asset['output']}",
                    "auth_method": "failed",
                    "stdout": "",
                    "stderr": str(exc),
                    "exit_status": -1,
                }
            )
    return results
