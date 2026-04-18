from flask import Blueprint, flash, render_template, request
from flask_login import current_user
from services import bkc_db
from services.access_control import register_admin_post_guard
from services.admin_history import append_admin_history, load_admin_history
from services.integration_store import load_ansible_snapshot
from services.remote_admin import (
    RemoteAdminError,
    bucket_command_results,
    run_ansible_playbook,
    run_host_commands,
)
from services.rules_store import load_rules
from services.tenant_context import get_current_tenant_id

admin_blueprint = Blueprint("admin", __name__)
register_admin_post_guard(admin_blueprint)


@admin_blueprint.route("/admin", methods=["GET", "POST"])
def admin():
    rules = load_rules()
    groups = sorted(rules["groups"].items())
    ansible_scan = load_ansible_snapshot()
    if ansible_scan is None:
        ansible_scan = {}
    history = load_admin_history()
    result = None
    result_buckets = None

    if request.method == "POST":
        action = request.form.get("action", "host-command")
        try:
            if action == "host-command":
                group_name = request.form.get("group", "")
                selected_hosts = request.form.getlist("selected_hosts")
                if selected_hosts:
                    targets = [tuple(item.split("|", 1)) for item in selected_hosts if "|" in item]
                else:
                    targets = [
                        (group_name, host_name)
                        for host_name in sorted(rules["groups"].get(group_name, {}).get("nodes", {}).keys())
                    ]
                command = request.form.get("command", "")
                result = run_host_commands(
                    targets=targets,
                    command=command,
                )
                result_buckets = bucket_command_results(result)
                append_admin_history(
                    {
                        "type": "host-command",
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
        except RemoteAdminError as exc:
            flash(f"Admin action failed: {exc}")

    return render_template(
        "admin.html.j2",
        groups=groups,
        ansible_scan=ansible_scan,
        history=history,
        result=result,
        result_buckets=result_buckets,
    )
