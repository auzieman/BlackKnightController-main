from collections import defaultdict

from flask import Blueprint, render_template, request
from services.inventory_model import build_actionable_inventory, resolve_group_hosts
from services.resource_graph import RESOURCE_KIND_META, cached_resource_graph, related_to
from services.rules_store import load_rules

inventory_console_blueprint = Blueprint("inventory_console", __name__)


def _resource_matches_search(resource: dict, query: str) -> bool:
    if not query:
        return True
    haystack = [
        resource.get("id", ""),
        resource.get("name", ""),
        resource.get("kind", ""),
        resource.get("state", ""),
        resource.get("summary", ""),
        *[str(value) for value in (resource.get("facts") or {}).values()],
    ]
    for section in (resource.get("sections") or {}).values():
        if isinstance(section, dict):
            haystack.extend(str(value) for value in section.values())
    return query in " ".join(haystack).lower()


def _resource_cmdb_rows(resource_graph: dict) -> tuple[list[dict], dict]:
    resources = list(resource_graph.get("resources") or [])
    by_name: dict[str, list[dict]] = defaultdict(list)
    rows: list[dict] = []
    for resource in resources:
        facts = resource.get("facts") or {}
        sections = resource.get("sections") or {}
        name = str(resource.get("name") or resource.get("id") or "").strip()
        stable_id = str(resource.get("id") or "").strip()
        kind = str(resource.get("kind") or "unknown").strip()
        state = str(resource.get("state") or "unknown").strip().lower()
        summary = str(resource.get("summary") or "")
        source_system = str(facts.get("source") or facts.get("inventory source") or facts.get("provider") or facts.get("platform") or "BKC graph")
        owner = str(facts.get("owner") or facts.get("ownership") or facts.get("owning_group") or "")
        parent = str(facts.get("parent") or facts.get("parent_host") or facts.get("host") or facts.get("platform") or "")
        lifecycle = "stale" if state in {"stale", "offline", "inactive"} or "stale" in summary.lower() else ("active" if state in {"running", "ready", "managed", "owner", "configured", "success"} else ("experimental" if state in {"candidate", "planned"} else "unknown"))
        health = "healthy" if state in {"running", "ready", "managed", "owner", "configured", "success"} else ("degraded" if state in {"warning", "stale", "candidate"} else ("offline" if state in {"offline", "failed", "down", "unreachable"} else "unknown"))
        normalized_name = name.lower().removesuffix(".lab.auzietek.com").removesuffix(":")
        by_name[normalized_name].append(resource)
        findings = []
        if not stable_id or ":" not in stable_id:
            findings.append("missing stable namespace")
        if not owner:
            findings.append("missing owner")
        if kind in {"host", "vm", "container", "service"} and not parent and kind != "host":
            findings.append("missing parent")
        if lifecycle == "stale" and health != "offline":
            findings.append("lifecycle/health review")
        rows.append({
            "id": stable_id,
            "name": name,
            "canonical_name": normalized_name or name,
            "type": kind,
            "subtype": facts.get("role") or facts.get("platform") or resource.get("state") or "",
            "source_system": source_system,
            "owner": owner or "review",
            "parent": parent or "review",
            "lifecycle": lifecycle,
            "health": health,
            "summary": summary,
            "findings": findings,
            "relationship_count": len(resource_graph.get("relationships_by_resource", {}).get(stable_id, [])) if isinstance(resource_graph.get("relationships_by_resource"), dict) else 0,
        })
    duplicate_keys = {key for key, values in by_name.items() if key and len(values) > 1}
    for row in rows:
        if row["canonical_name"] in duplicate_keys:
            row["findings"].append("duplicate identity candidate")
    summary = {
        "resources_reviewed": len(rows),
        "duplicate_candidates": sum(1 for row in rows if "duplicate identity candidate" in row["findings"]),
        "stale_candidates": sum(1 for row in rows if row["lifecycle"] == "stale"),
        "missing_owner": sum(1 for row in rows if "missing owner" in row["findings"]),
        "missing_parent": sum(1 for row in rows if "missing parent" in row["findings"]),
    }
    return rows, summary


@inventory_console_blueprint.route("/inventory", methods=["GET"])
def inventory_console():
    rules = load_rules()
    resource_graph = cached_resource_graph(ttl_seconds=10)
    tab = request.args.get("tab", "inventory").strip().lower()
    if tab not in {"inventory", "launch", "cmdb"}:
        tab = "inventory"
    selected_group = request.args.get("group", "").strip()
    selected_resource_id = request.args.get("resource", "").strip()
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

    visible_resource_tree = []
    for tree_group in resource_graph["tree"]:
        resources = [
            resource
            for resource in tree_group["resources"]
            if _resource_matches_search(resource, search)
        ]
        if resources:
            visible_resource_tree.append({**tree_group, "resources": resources})
    visible_resources = [resource for group in visible_resource_tree for resource in group["resources"]]

    if not selected_group and group_rows:
        selected_group = group_rows[0]["name"]
    if not selected_resource_id and selected_group:
        selected_resource_id = f"group:{selected_group}"
    if selected_resource_id not in resource_graph["resources_by_id"]:
        selected_resource_id = visible_resources[0]["id"] if visible_resources else (resource_graph["resources"][0]["id"] if resource_graph["resources"] else "")
    selected_resource = resource_graph["resources_by_id"].get(selected_resource_id)

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
    cmdb_rows, cmdb_summary = _resource_cmdb_rows(resource_graph)
    if search:
        cmdb_rows = [
            row for row in cmdb_rows
            if search in " ".join(str(value) for value in row.values()).lower()
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
        cmdb_rows=cmdb_rows,
        cmdb_summary=cmdb_summary,
        search=search,
        sort_key=sort_key,
        shared_hosts=shared_hosts,
        resource_graph=resource_graph,
        visible_resource_tree=visible_resource_tree,
        visible_resource_count=len(visible_resources),
        resource_kind_meta=RESOURCE_KIND_META,
        selected_resource=selected_resource,
        selected_resource_id=selected_resource_id,
        selected_relationships=related_to(resource_graph, selected_resource_id) if selected_resource else [],
    )
