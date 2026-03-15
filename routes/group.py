from flask import Blueprint, abort, flash, redirect, render_template, request, url_for

from services.inventory_model import reconcile_rules_inventory, resolve_group_hosts
from services.inventory_probe import probe_hosts
from services.rules_store import load_rules, save_rules
from services.workflow import build_stage_columns, parse_workflow_stages


groups = Blueprint("groups", __name__)


@groups.route("/group", methods=["GET"])
def list_groups():
    rules = load_rules()
    return render_template(
        "list_groups.html.j2",
        groups=sorted(rules["groups"].items()),
    )


@groups.route("/group/<group>/hosts", methods=["GET", "POST"])
def hosts(group):
    rules = load_rules()
    group_data = rules["groups"].get(group)
    if group_data is None:
        abort(404)

    probe_results = []
    if request.method == "POST":
        action = request.form.get("action", "")
        if action == "reconcile":
            result = reconcile_rules_inventory(rules)
            save_rules(rules)
            flash(
                f"Reconciled {result['nodes']} node records across {result['clusters']} linked inventory clusters."
            )
            return redirect(url_for("groups.hosts", group=group))
        if action == "probe":
            selected_hosts = request.form.getlist("selected_hosts")
            if selected_hosts:
                targets = [(group, host_name) for host_name in selected_hosts]
            else:
                targets = [(group, host_name) for host_name in sorted(group_data.get("nodes", {}).keys())]
            probe_results = probe_hosts(rules, targets)
            save_rules(rules)
            successful = len([result for result in probe_results if result.get("status") == "ok"])
            flash(f"Probed {successful} of {len(probe_results)} selected host(s).")
            group_data = rules["groups"].get(group)

    workflow = group_data.get("locals", {}).get("workflow", "build -> provision -> configure -> deploy")
    resolved_hosts = resolve_group_hosts(rules, group)
    hosts = [(host_name, node_data) for host_name, node_data, _ in resolved_hosts]

    return render_template(
        "hosts.html.j2",
        group=group,
        group_data=group_data,
        hosts=hosts,
        resolved_hosts=resolved_hosts,
        probe_results=probe_results,
        workflow_stages=parse_workflow_stages(workflow),
        workflow_columns=build_stage_columns(hosts, workflow),
    )


@groups.route("/group/<group>/host/<host>/edit", methods=["GET", "POST"])
def edit_host(group, host):
    rules = load_rules()
    group_data = rules["groups"].get(group)
    if group_data is None or host not in group_data.get("nodes", {}):
        abort(404)

    host_data = group_data["nodes"][host]

    if request.method == "POST":
        host_data["user"] = request.form["user"].strip()
        host_data["port"] = int(request.form["port"])
        host_data["private_key"] = request.form["private_key"].strip()
        host_data["provider"] = request.form["provider"].strip()
        host_data["provisioner"] = request.form["provisioner"].strip()
        host_data["configuration"] = request.form["configuration"].strip()
        host_data["state"] = request.form["state"].strip()
        host_data["application"] = request.form["application"].strip()
        save_rules(rules)
        return redirect(url_for("groups.hosts", group=group))

    return render_template(
        "edit_host.html.j2",
        group=group,
        host=host,
        host_data=host_data,
    )


@groups.route("/group/<group>/edit", methods=["GET", "POST"])
def edit_group(group):
    rules = load_rules()
    group_data = rules["groups"].get(group)
    if group_data is None:
        abort(404)

    locals_data = group_data.setdefault("locals", {})

    if request.method == "POST":
        locals_data["env"] = request.form["env"].strip()
        locals_data["datacenter"] = request.form["datacenter"].strip()
        locals_data["release"] = request.form["release"].strip()
        locals_data["workflow"] = request.form["workflow"].strip()
        save_rules(rules)
        return redirect(url_for("groups.hosts", group=group))

    return render_template(
        "edit_group.html.j2",
        group=group,
        locals_data=locals_data,
    )
