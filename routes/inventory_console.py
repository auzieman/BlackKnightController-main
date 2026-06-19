from flask import Blueprint, render_template, request
from services.inventory_model import build_actionable_inventory, resolve_group_hosts
from services.rules_store import load_rules

inventory_console_blueprint = Blueprint("inventory_console", __name__)


@inventory_console_blueprint.route("/inventory", methods=["GET"])
def inventory_console():
    rules = load_rules()
    tab = request.args.get("tab", "inventory").strip().lower()
    if tab not in {"inventory", "launch"}:
        tab = "inventory"
    selected_group = request.args.get("group", "").strip()
    search = request.args.get("q", "").strip().lower()
    sort_key = request.args.get("sort", "node").strip().lower()
    if sort_key not in {"node", "provider", "state", "os"}:
        sort_key = "node"

    memberships = {}
    group_rows = []
    for group_name in sorted(rules.get("groups", {}).keys()):
        resolved_hosts = resolve_group_hosts(rules, group_name)
        host_rows = []
        for host_name, node_data, resolved in resolved_hosts:
            memberships.setdefault(host_name, set()).add(group_name)
            host_rows.append(
                {
                    "name": host_name,
                    "provider": resolved.get("provider", "") or node_data.get("provider", ""),
                    "route": resolved.get("ip", "") or resolved.get("fqdn", "") or resolved.get("hostname", ""),
                    "state": resolved.get("state", "") or node_data.get("state", ""),
                    "os_name": resolved.get("os_name", ""),
                    "services": resolved.get("services_detected", []),
                    "user": resolved.get("user", ""),
                }
            )
        group_rows.append(
            {
                "name": group_name,
                "locals": rules["groups"][group_name].get("locals", {}),
                "hosts": host_rows,
                "host_count": len(host_rows),
            }
        )

    if not selected_group and group_rows:
        selected_group = group_rows[0]["name"]

    shared_hosts = {
        host_name: sorted(groups)
        for host_name, groups in memberships.items()
        if len(groups) > 1
    }

    selected_group_row = next((group for group in group_rows if group["name"] == selected_group), None)
    filtered_hosts = list(selected_group_row["hosts"]) if selected_group_row else []
    if search:
        filtered_hosts = [
            host
            for host in filtered_hosts
            if search in host["name"].lower()
            or search in (host["provider"] or "").lower()
            or search in (host["route"] or "").lower()
            or search in (host["os_name"] or "").lower()
            or any(search in service.lower() for service in host["services"])
        ]

    sort_map = {
        "node": lambda host: host["name"].lower(),
        "provider": lambda host: (host["provider"] or "").lower(),
        "state": lambda host: (host["state"] or "").lower(),
        "os": lambda host: (host["os_name"] or "").lower(),
    }
    filtered_hosts = sorted(filtered_hosts, key=sort_map[sort_key])

    selected_actionable = next(
        (group for group in build_actionable_inventory(rules) if group["name"] == selected_group),
        {"name": selected_group, "hosts": []},
    )
    filtered_launch_hosts = list(selected_actionable["hosts"])
    if search:
        filtered_launch_hosts = [
            host
            for host in filtered_launch_hosts
            if search in host["name"].lower()
            or search in (host["route_target"] or "").lower()
            or search in (host["reason"] or "").lower()
            or search in (host["resolved"].get("user", "") or "").lower()
        ]

    return render_template(
        "inventory_console.html.j2",
        tab=tab,
        group_rows=group_rows,
        actionable_inventory=build_actionable_inventory(rules),
        selected_actionable=selected_actionable,
        selected_group=selected_group,
        selected_group_row=selected_group_row,
        filtered_hosts=filtered_hosts,
        filtered_launch_hosts=filtered_launch_hosts,
        search=search,
        sort_key=sort_key,
        shared_hosts=shared_hosts,
    )
