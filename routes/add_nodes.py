from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user
from forms import AddNodesForm, ScanSubnetForm
from services.access_control import register_inventory_post_guard
from services.discovery import DiscoveryError, import_discovered_nodes, scan_subnet_ssh
from services.inventory_model import reconcile_rules_inventory
from services.job_queue import enqueue_job, job_queue_enabled
from services.rules_store import load_rules, save_rules
from services.tenant_context import get_current_tenant_id, get_effective_tenant_slug

add_nodes_blueprint = Blueprint("add_nodes", __name__)
register_inventory_post_guard(add_nodes_blueprint)


@add_nodes_blueprint.route("/add_nodes", methods=["GET", "POST"])
def add_nodes():
    rules = load_rules()
    add_node_form = AddNodesForm()
    scan_subnet_form = ScanSubnetForm()
    add_node_form.group.choices = [(name, name) for name in sorted(rules["groups"].keys())]
    scan_subnet_form.group.choices = add_node_form.group.choices
    scan_result = None

    if request.method == "POST":
        action = request.form.get("action", "manual")

        if action == "manual" and add_node_form.validate():
            group_name = add_node_form.group.data
            node_entries = [
                entry.strip() for entry in add_node_form.nodes.data.splitlines() if entry.strip()
            ]
            group = rules["groups"].setdefault(group_name, {"locals": {}, "nodes": {}})
            for node in node_entries:
                group["nodes"].setdefault(
                    node,
                    {
                        "user": "root",
                        "password": "",
                        "port": 22,
                        "private_key": "",
                        "provider": "manual-entry",
                        "provisioner": "cloud-init",
                        "configuration": "ansible",
                        "state": "planned",
                        "application": "bkc-managed",
                    },
                )
            save_rules(rules)
            flash(f"Added {len(node_entries)} node(s) to {group_name}.")
            return redirect(url_for("groups.hosts", group=group_name))

        if action == "scan" and scan_subnet_form.validate():
            if job_queue_enabled():
                payload = {
                    "subnet": scan_subnet_form.subnet.data,
                    "username": scan_subnet_form.username.data,
                    "password": scan_subnet_form.password.data or "",
                    "install_key": bool(scan_subnet_form.install_key.data),
                    "group_name": scan_subnet_form.group.data,
                    "tenant_slug": get_effective_tenant_slug(),
                    "tenant_id": get_current_tenant_id(),
                    "user_id": int(current_user.id),
                    "remote_ip": request.remote_addr,
                }
                job = enqueue_job(
                    "services.job_tasks.scan_subnet_job",
                    (payload,),
                    job_timeout=1200,
                    meta={
                        "user_id": payload["user_id"],
                        "tenant_slug": payload["tenant_slug"],
                        "kind": "add_nodes",
                    },
                )
                flash(f"Subnet scan queued as job {job.id}. Open Jobs to watch progress.")
                return redirect(url_for("jobs.job_status", job_id=job.id))
            try:
                scan_result = scan_subnet_ssh(
                    subnet=scan_subnet_form.subnet.data,
                    username=scan_subnet_form.username.data,
                    password=scan_subnet_form.password.data or "",
                    install_key=bool(scan_subnet_form.install_key.data),
                )
                imported = import_discovered_nodes(
                    rules,
                    group_name=scan_subnet_form.group.data,
                    scan_result=scan_result,
                )
                reconcile = reconcile_rules_inventory(rules)
                save_rules(rules)
                flash(
                    f"Scanned {scan_result['subnet']}: "
                    f"{scan_result['ssh_hosts']} SSH hosts found, {imported} imported, "
                    f"{reconcile['clusters']} related node clusters reconciled."
                )
            except DiscoveryError as exc:
                flash(f"Subnet scan failed: {exc}")

    return render_template(
        "add_nodes.html.j2",
        add_node_form=add_node_form,
        scan_subnet_form=scan_subnet_form,
        scan_result=scan_result,
    )
