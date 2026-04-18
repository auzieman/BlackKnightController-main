from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user
from services import bkc_db
from services.access_control import register_proxmox_ops_post_guard
from services.inventory_model import reconcile_rules_inventory
from services.proxmox import (
    ProxmoxAPIError,
    ProxmoxClient,
    ProxmoxConfigError,
    build_catalog,
    load_proxmox_config,
    summarize_inventory,
    sync_inventory_to_rules,
)
from services.rules_store import load_rules, save_rules
from services.tenant_context import get_current_tenant_id

proxmox_ops_blueprint = Blueprint("proxmox_ops", __name__)
register_proxmox_ops_post_guard(proxmox_ops_blueprint)


@proxmox_ops_blueprint.route("/proxmox", methods=["GET", "POST"])
def proxmox_ops():
    catalog = None

    if request.method == "POST":
        action = request.form.get("action", "")
        try:
            client = ProxmoxClient(load_proxmox_config())

            if action == "refresh":
                catalog = build_catalog(client)
                flash(
                    f"Loaded Proxmox catalog: "
                    f"{len(catalog['templates'])} VM templates, "
                    f"{len(catalog['containers'])} containers, "
                    f"{len(catalog['iso_images'])} ISOs."
                )

            elif action == "clone-qemu":
                source_node = request.form.get("source_node", "").strip()
                source_vmid = int(request.form.get("source_vmid", "0"))
                new_vmid = int(request.form.get("new_vmid", "0"))
                name = request.form.get("name", "").strip()

                if not source_node or not source_vmid or not new_vmid or not name:
                    raise ProxmoxConfigError("Source node, source VMID, new VMID, and target name are required.")

                client.clone_vm(
                    node=source_node,
                    source_vmid=source_vmid,
                    new_vmid=new_vmid,
                    name=name,
                    full=request.form.get("full_clone") == "on",
                )

                inventory = summarize_inventory(client)
                rules = load_rules()
                sync_result = sync_inventory_to_rules(rules, inventory)
                reconcile_result = reconcile_rules_inventory(rules)
                save_rules(rules)
                flash(
                    f"Submitted VM clone for {name} from {source_vmid} on {source_node}. "
                    f"Inventory sync created {sync_result['created_nodes']} node(s) and reconciled "
                    f"{reconcile_result['clusters']} cluster(s)."
                )
                bkc_db.append_audit(
                    int(current_user.id),
                    get_current_tenant_id(),
                    "proxmox.clone_qemu",
                    "hypervisor",
                    {"name": name, "source_vmid": source_vmid, "new_vmid": new_vmid},
                    request.remote_addr,
                )
                return redirect(url_for("proxmox_ops.proxmox_ops"))

            elif action == "clone-lxc":
                source_node = request.form.get("source_node", "").strip()
                source_vmid = int(request.form.get("source_vmid", "0"))
                new_vmid = int(request.form.get("new_vmid", "0"))
                hostname = request.form.get("name", "").strip()

                if not source_node or not source_vmid or not new_vmid or not hostname:
                    raise ProxmoxConfigError("Source node, source CTID, new CTID, and hostname are required.")

                client.clone_lxc(
                    node=source_node,
                    source_vmid=source_vmid,
                    new_vmid=new_vmid,
                    hostname=hostname,
                    full=request.form.get("full_clone") == "on",
                )

                inventory = summarize_inventory(client)
                rules = load_rules()
                sync_result = sync_inventory_to_rules(rules, inventory)
                reconcile_result = reconcile_rules_inventory(rules)
                save_rules(rules)
                flash(
                    f"Submitted LXC clone for {hostname} from {source_vmid} on {source_node}. "
                    f"Inventory sync created {sync_result['created_nodes']} node(s) and reconciled "
                    f"{reconcile_result['clusters']} cluster(s)."
                )
                bkc_db.append_audit(
                    int(current_user.id),
                    get_current_tenant_id(),
                    "proxmox.clone_lxc",
                    "hypervisor",
                    {"hostname": hostname, "source_vmid": source_vmid, "new_vmid": new_vmid},
                    request.remote_addr,
                )
                return redirect(url_for("proxmox_ops.proxmox_ops"))

        except (ProxmoxConfigError, ProxmoxAPIError, ValueError) as exc:
            flash(f"Proxmox operation failed: {exc}")

    if catalog is None:
        try:
            catalog = build_catalog(ProxmoxClient(load_proxmox_config()))
        except (ProxmoxConfigError, ProxmoxAPIError) as exc:
            flash(f"Proxmox catalog unavailable: {exc}")
            catalog = {
                "nodes": [],
                "virtual_machines": [],
                "templates": [],
                "containers": [],
                "storage": [],
                "iso_images": [],
                "container_templates": [],
                "next_vmid": None,
                "request_trace": [],
            }

    return render_template("proxmox_ops.html.j2", catalog=catalog)
