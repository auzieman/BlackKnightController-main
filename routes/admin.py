from flask import Blueprint, flash, render_template, request
from flask_login import current_user
from services import bkc_db
from services.access_control import register_admin_post_guard
from services.admin_history import append_admin_history, load_admin_history
from services.integration_store import load_ansible_snapshot
from services.inventory_model import build_actionable_inventory
from services.remote_admin import (
    RemoteAdminError,
    bucket_command_results,
    run_ansible_playbook,
    run_host_commands,
)
from services.rules_store import load_rules
from services.tenant_context import get_current_tenant_id
from services.template_assets import TemplateAssetError, load_template_assets, run_template_asset

admin_blueprint = Blueprint("admin", __name__)
register_admin_post_guard(admin_blueprint)


def _actionable_lookup(actionable_inventory: list[dict]) -> dict[tuple[str, str], dict]:
    lookup = {}
    for group in actionable_inventory:
        for host in group.get("hosts", []):
            lookup[(group["name"], host["name"])] = host
    return lookup


def _validate_selected_targets(selected_hosts: list[str], actionable_inventory: list[dict], group_name: str = "") -> list[tuple[str, str]]:
    if not selected_hosts:
        raise ValueError("Select at least one target host.")
    lookup = _actionable_lookup(actionable_inventory)
    validated = []
    for item in selected_hosts:
        if "|" not in item:
            raise ValueError(f"Invalid target format: {item}")
        selected_group, selected_host = item.split("|", 1)
        if group_name and selected_group != group_name:
            raise ValueError("Mixed target groups are not allowed in this action.")
        host = lookup.get((selected_group, selected_host))
        if not host:
            raise ValueError(f"Unknown target {selected_group}/{selected_host}.")
        if not host.get("ready"):
            raise ValueError(f"Target {selected_group}/{selected_host} is not ready for execution.")
        validated.append((selected_group, selected_host))
    return validated


@admin_blueprint.route("/admin", methods=["GET", "POST"])
def admin():
    rules = load_rules()
    groups = sorted(rules["groups"].items())
    actionable_inventory = build_actionable_inventory(rules)
    prefill_group = request.args.get("group", "").strip()
    prefill_host = request.args.get("host", "").strip()
    ansible_scan = load_ansible_snapshot()
    if ansible_scan is None:
        ansible_scan = {}
    history = load_admin_history()
    template_assets = load_template_assets()
    result = None
    result_buckets = None

    if request.method == "POST":
        action = request.form.get("action", "host-command")
        try:
            if action == "host-command":
                group_name = request.form.get("group", "")
                command = request.form.get("command", "").strip()
                if not command:
                    raise ValueError("Command is required.")
                targets = _validate_selected_targets(request.form.getlist("selected_hosts"), actionable_inventory, group_name=group_name)
                result = run_host_commands(
                    targets=targets,
                    command=command,
                )
                result_buckets = bucket_command_results(result)
                append_admin_history(
                    {
                        "type": "host-command",
                        "actor": current_user.id,
                        "command": command,
                        "targets": [f"{group}:{host}" for group, host in targets],
                        "bucket_count": len(result_buckets),
                    }
                )
                flash(f"Ran command across {len(result)} target(s).")
                bkc_db.append_audit(
                    int(current_user.id),
                    get_current_tenant_id(),
                    "admin.host_command",
                    "remote",
                    {"command": command, "targets": len(targets)},
                    request.remote_addr,
                )
            elif action == "ansible-playbook":
                result = run_ansible_playbook(
                    limit=request.form.get("limit", ""),
                    extra_args=request.form.get("extra_args", ""),
                    playbook_override=request.form.get("playbook", ""),
                )
                append_admin_history(
                        {
                            "type": "ansible-playbook",
                            "actor": current_user.id,
                            "command": result["command"],
                            "targets": [result["target"]],
                            "bucket_count": 1,
                        }
                )
                flash(f"Ran playbook on {result['target']} with exit status {result['exit_status']}.")
                bkc_db.append_audit(
                    int(current_user.id),
                    get_current_tenant_id(),
                    "admin.ansible_playbook",
                    "remote",
                    {"command": result.get("command"), "exit": result.get("exit_status")},
                    request.remote_addr,
                )
            elif action == "bkc-template":
                group_name = request.form.get("template_group", "")
                asset_id = request.form.get("template_asset", "")
                if not asset_id:
                    raise ValueError("Template asset is required.")
                targets = _validate_selected_targets(
                    request.form.getlist("template_selected_hosts"),
                    actionable_inventory,
                    group_name=group_name,
                )
                result = run_template_asset(targets=targets, asset_id=asset_id)
                result_buckets = bucket_command_results(result)
                append_admin_history(
                    {
                        "type": "bkc-template",
                        "actor": current_user.id,
                        "command": asset_id,
                        "targets": [f"{group}:{host}" for group, host in targets],
                        "bucket_count": len(result_buckets),
                    }
                )
                flash(f"Applied template asset across {len(result)} target(s).")
                bkc_db.append_audit(
                    int(current_user.id),
                    get_current_tenant_id(),
                    "admin.template_asset",
                    "remote",
                    {"asset_id": asset_id, "targets": len(targets)},
                    request.remote_addr,
                )
            else:
                raise ValueError("Unknown admin action.")
        except RemoteAdminError as exc:
            flash(f"Admin action failed: {exc}")
        except TemplateAssetError as exc:
            flash(f"Template action failed: {exc}")
        except ValueError as exc:
            flash(str(exc))

    return render_template(
        "admin.html.j2",
        groups=groups,
        actionable_inventory=actionable_inventory,
        prefill_group=prefill_group,
        prefill_host=prefill_host,
        ansible_scan=ansible_scan,
        history=history,
        template_assets=template_assets,
        result=result,
        result_buckets=result_buckets,
    )
