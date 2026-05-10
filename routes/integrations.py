from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user
from services import bkc_db
from services.access_control import register_integrations_post_guard
from services.ansible import AnsibleScanError, scan_ansible_controller
from services.ansible_inventory import parse_ansible_hosts, sync_ansible_inventory_to_rules
from services.docker_swarm import DockerScanError, scan_docker_controller, sync_docker_inventory_to_rules
from services.integration_store import (
    load_ansible_snapshot,
    load_docker_snapshot,
    load_integrations,
    load_proxmox_snapshot,
    save_ansible_snapshot,
    save_docker_snapshot,
    save_integrations,
    save_proxmox_snapshot,
)
from services.inventory_model import reconcile_rules_inventory
from services.job_queue import enqueue_job, job_queue_enabled
from services.proxmox import (
    ProxmoxAPIError,
    ProxmoxClient,
    ProxmoxConfigError,
    load_proxmox_config,
    summarize_inventory,
    sync_inventory_to_rules,
)
from services.rules_store import load_rules, save_rules
from services.ssh_keys import ensure_key_pair, read_key_pair
from services.tenant_context import get_current_tenant_id, get_effective_tenant_slug

integrations_blueprint = Blueprint("integrations", __name__)
register_integrations_post_guard(integrations_blueprint)

_INTEGRATION_ASYNC_JOBS = {
    "pull-proxmox-inventory": "services.job_tasks.pull_proxmox_inventory_job",
    "sync-proxmox-inventory": "services.job_tasks.sync_proxmox_inventory_job",
    "scan-ansible": "services.job_tasks.scan_ansible_job",
    "sync-ansible-inventory": "services.job_tasks.sync_ansible_inventory_job",
    "scan-docker": "services.job_tasks.scan_docker_job",
    "sync-docker-inventory": "services.job_tasks.sync_docker_inventory_job",
}


def _try_enqueue_integration_job(action: str):
    if not job_queue_enabled():
        return None
    fn = _INTEGRATION_ASYNC_JOBS.get(action)
    if not fn:
        return None
    tenant_slug = get_effective_tenant_slug()
    tenant_id = get_current_tenant_id()
    user_id = int(current_user.id)
    job = enqueue_job(
        fn,
        (tenant_slug, tenant_id, user_id, request.remote_addr),
        job_timeout=900,
        meta={
            "user_id": user_id,
            "tenant_slug": tenant_slug,
            "action": action,
            "kind": "integrations",
        },
    )
    flash(f"Queued background job {job.id} ({action}). Open Jobs to watch progress.")
    return redirect(url_for("jobs.job_status", job_id=job.id))


def _clean(value: str) -> str:
    return value.strip()


def _normalize_proxmox_token_name(username: str, token_name: str) -> str:
    cleaned = token_name.strip()
    if "!" in cleaned:
        cleaned = cleaned.split("!", 1)[1]
    if cleaned.startswith(f"{username}!"):
        cleaned = cleaned.split("!", 1)[1]
    return cleaned


@integrations_blueprint.route("/integrations", methods=["GET", "POST"])
def integrations():
    integrations = load_integrations()
    integrations["proxmox"]["token_name"] = _normalize_proxmox_token_name(
        integrations["proxmox"].get("username", ""),
        integrations["proxmox"].get("token_name", ""),
    )
    proxmox_inventory = load_proxmox_snapshot()
    ansible_scan = load_ansible_snapshot()
    docker_scan = load_docker_snapshot()

    if request.method == "POST":
        action = request.form.get("action", "save")

        if action == "save-proxmox":
            username = _clean(request.form.get("username", ""))
            integrations["proxmox"] = {
                "api_url": _clean(request.form.get("api_url", "")),
                "token_name": _normalize_proxmox_token_name(
                    username,
                    _clean(request.form.get("token_name", "")),
                ),
                "token_value": _clean(request.form.get("token_value", "")),
                "username": username,
                "password": _clean(request.form.get("password", "")),
                "verify_ssl": request.form.get("verify_ssl") == "on",
            }
            save_integrations(integrations)
            flash("Saved Proxmox settings.")
            bkc_db.append_audit(
                int(current_user.id),
                get_current_tenant_id(),
                "integrations.save_proxmox",
                "integrations",
                {},
                request.remote_addr,
            )
            return redirect(url_for("integrations.integrations"))

        if action == "save-ansible":
            integrations["ansible"] = {
                "controller_host": _clean(request.form.get("controller_host", "")),
                "controller_user": _clean(request.form.get("controller_user", "")),
                "controller_password": _clean(request.form.get("controller_password", "")),
                "playbook": _clean(request.form.get("playbook", "")),
                "inventory_path": _clean(request.form.get("inventory_path", "")),
                "config_root": _clean(request.form.get("config_root", "")) or "/etc/ansible",
            }
            save_integrations(integrations)
            flash("Saved Ansible settings.")
            bkc_db.append_audit(
                int(current_user.id),
                get_current_tenant_id(),
                "integrations.save_ansible",
                "integrations",
                {},
                request.remote_addr,
            )
            return redirect(url_for("integrations.integrations"))

        if action == "save-docker":
            integrations["docker"] = {
                "manager_host": _clean(request.form.get("manager_host", "")),
                "manager_user": _clean(request.form.get("manager_user", "")),
                "manager_password": _clean(request.form.get("manager_password", "")),
                "stack_name": _clean(request.form.get("stack_name", "")),
            }
            save_integrations(integrations)
            flash("Saved Docker Swarm settings.")
            bkc_db.append_audit(
                int(current_user.id),
                get_current_tenant_id(),
                "integrations.save_docker",
                "integrations",
                {},
                request.remote_addr,
            )
            return redirect(url_for("integrations.integrations"))

        if action == "save-ssh":
            key_name = _clean(request.form.get("key_name", "")) or "bkc_id_rsa"
            integrations["ssh"] = {
                "key_name": key_name,
                "private_key_path": f"keys/{key_name}",
                "public_key_path": f"keys/{key_name}.pub",
            }
            save_integrations(integrations)
            flash("Saved SSH key settings.")
            bkc_db.append_audit(
                int(current_user.id),
                get_current_tenant_id(),
                "integrations.save_ssh",
                "integrations",
                {},
                request.remote_addr,
            )
            return redirect(url_for("integrations.integrations"))

        if action == "test-proxmox":
            try:
                version = ProxmoxClient(load_proxmox_config()).version()
                flash(
                    f"Connected to Proxmox {version.get('version', 'unknown')} "
                    f"({version.get('release', 'unknown release')})."
                )
            except (ProxmoxConfigError, ProxmoxAPIError) as exc:
                flash(f"Proxmox test failed: {exc}")
            return redirect(url_for("integrations.integrations"))

        if action == "pull-proxmox-inventory":
            queued = _try_enqueue_integration_job(action)
            if queued is not None:
                return queued
            try:
                proxmox_inventory = summarize_inventory(ProxmoxClient(load_proxmox_config()))
                save_proxmox_snapshot(proxmox_inventory)
                flash(
                    f"Fetched Proxmox inventory: "
                    f"{len(proxmox_inventory['nodes'])} nodes, "
                    f"{len(proxmox_inventory['virtual_machines'])} VMs, "
                    f"{len(proxmox_inventory['containers'])} containers."
                )
                if (
                    len(proxmox_inventory["nodes"]) > 0
                    and len(proxmox_inventory["virtual_machines"]) == 0
                    and len(proxmox_inventory["containers"]) == 0
                ):
                    flash(
                        "Proxmox returned nodes but no guests. This usually means the API token can connect "
                        "but lacks VM/CT audit permissions on the node or pool."
                    )
            except (ProxmoxConfigError, ProxmoxAPIError) as exc:
                flash(f"Proxmox inventory pull failed: {exc}")

        if action == "sync-proxmox-inventory":
            queued = _try_enqueue_integration_job(action)
            if queued is not None:
                return queued
            try:
                proxmox_inventory = summarize_inventory(ProxmoxClient(load_proxmox_config()))
                save_proxmox_snapshot(proxmox_inventory)
                rules = load_rules()
                result = sync_inventory_to_rules(rules, proxmox_inventory)
                reconcile = reconcile_rules_inventory(rules)
                save_rules(rules)
                flash(
                    f"Synced Proxmox inventory into BKC: "
                    f"{result['groups']} groups created, "
                    f"{result['created_nodes']} nodes created, "
                    f"{result['updated_nodes']} nodes updated, "
                    f"{reconcile['clusters']} related node clusters reconciled."
                )
                if (
                    len(proxmox_inventory["nodes"]) > 0
                    and len(proxmox_inventory["virtual_machines"]) == 0
                    and len(proxmox_inventory["containers"]) == 0
                ):
                    flash(
                        "Proxmox sync found zero guests. Check token permissions such as VM.Audit on the relevant node, pool, or /vms path."
                    )
            except (ProxmoxConfigError, ProxmoxAPIError) as exc:
                flash(f"Proxmox sync failed: {exc}")

        if action == "scan-ansible":
            queued = _try_enqueue_integration_job(action)
            if queued is not None:
                return queued
            try:
                ansible_scan = scan_ansible_controller()
                save_ansible_snapshot(ansible_scan)
                flash(
                    f"Scanned Ansible controller: "
                    f"{len(ansible_scan['playbooks'])} playbooks found."
                )
            except AnsibleScanError as exc:
                flash(f"Ansible scan failed: {exc}")

        if action == "sync-ansible-inventory":
            queued = _try_enqueue_integration_job(action)
            if queued is not None:
                return queued
            try:
                ansible_scan = scan_ansible_controller()
                save_ansible_snapshot(ansible_scan)
                parsed = parse_ansible_hosts(ansible_scan["inventory_content"])
                rules = load_rules()
                result = sync_ansible_inventory_to_rules(rules, parsed)
                reconcile = reconcile_rules_inventory(rules)
                save_rules(rules)
                flash(
                    f"Synced Ansible inventory into BKC: "
                    f"{result['groups']} groups created, "
                    f"{result['created_nodes']} nodes created, "
                    f"{result['updated_nodes']} nodes updated, "
                    f"{reconcile['clusters']} related node clusters reconciled."
                )
            except AnsibleScanError as exc:
                flash(f"Ansible inventory sync failed: {exc}")

        if action == "scan-docker":
            queued = _try_enqueue_integration_job(action)
            if queued is not None:
                return queued
            try:
                docker_scan = scan_docker_controller()
                save_docker_snapshot(docker_scan)
                flash(
                    f"Scanned Docker Swarm: "
                    f"{len(docker_scan['nodes'])} nodes, "
                    f"{len(docker_scan['stacks'])} stacks, "
                    f"{len(docker_scan['services'])} services."
                )
            except DockerScanError as exc:
                flash(f"Docker scan failed: {exc}")

        if action == "sync-docker-inventory":
            queued = _try_enqueue_integration_job(action)
            if queued is not None:
                return queued
            try:
                docker_scan = scan_docker_controller()
                save_docker_snapshot(docker_scan)
                rules = load_rules()
                result = sync_docker_inventory_to_rules(rules, docker_scan)
                reconcile = reconcile_rules_inventory(rules)
                save_rules(rules)
                flash(
                    f"Synced Docker inventory into BKC: "
                    f"{result['groups']} groups created, "
                    f"{result['created_nodes']} nodes created, "
                    f"{result['updated_nodes']} nodes updated, "
                    f"{reconcile['clusters']} related node clusters reconciled."
                )
            except DockerScanError as exc:
                flash(f"Docker inventory sync failed: {exc}")

        if action == "generate-ssh-key":
            key_info = ensure_key_pair(
                integrations["ssh"]["private_key_path"],
                integrations["ssh"]["public_key_path"],
            )
            flash(f"SSH key ready at {key_info['private_key_path']}.")
            return redirect(url_for("integrations.integrations"))

    ssh_key = read_key_pair(
        integrations["ssh"]["private_key_path"],
        integrations["ssh"]["public_key_path"],
    )

    return render_template(
        "integrations.html.j2",
        integrations=integrations,
        ssh_key=ssh_key,
        proxmox_inventory=proxmox_inventory,
        ansible_scan=ansible_scan,
        docker_scan=docker_scan,
    )
