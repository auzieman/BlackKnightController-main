from flask import Blueprint, flash, redirect, render_template, request, url_for

from services.ansible import AnsibleScanError, scan_ansible_controller
from services.ansible_inventory import parse_ansible_hosts, sync_ansible_inventory_to_rules
from services.integration_store import (
    ANSIBLE_SNAPSHOT_PATH,
    PROXMOX_SNAPSHOT_PATH,
    load_integrations,
    load_snapshot,
    save_integrations,
    save_snapshot,
)
from services.inventory_model import reconcile_rules_inventory
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


integrations_blueprint = Blueprint("integrations", __name__)


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
    proxmox_inventory = load_snapshot(PROXMOX_SNAPSHOT_PATH)
    ansible_scan = load_snapshot(ANSIBLE_SNAPSHOT_PATH)

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
            try:
                proxmox_inventory = summarize_inventory(ProxmoxClient(load_proxmox_config()))
                save_snapshot(PROXMOX_SNAPSHOT_PATH, proxmox_inventory)
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
            try:
                proxmox_inventory = summarize_inventory(ProxmoxClient(load_proxmox_config()))
                save_snapshot(PROXMOX_SNAPSHOT_PATH, proxmox_inventory)
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
            try:
                ansible_scan = scan_ansible_controller()
                save_snapshot(ANSIBLE_SNAPSHOT_PATH, ansible_scan)
                flash(
                    f"Scanned Ansible controller: "
                    f"{len(ansible_scan['playbooks'])} playbooks found."
                )
            except AnsibleScanError as exc:
                flash(f"Ansible scan failed: {exc}")

        if action == "sync-ansible-inventory":
            try:
                ansible_scan = scan_ansible_controller()
                save_snapshot(ANSIBLE_SNAPSHOT_PATH, ansible_scan)
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
    )
