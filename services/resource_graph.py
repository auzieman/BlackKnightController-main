from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from urllib.parse import urlparse

from services.automation_runs import load_runs
from services.integration_store import load_integrations
from services.inventory_model import resolve_group_hosts
from services.pipeline_catalog import demo_pipelines
from services.rules_store import load_rules
from services.tenant_context import get_effective_tenant_slug


RESOURCE_KIND_META = {
    "api": {"label": "APIs", "short": "API", "order": 10},
    "group": {"label": "Groups", "short": "GRP", "order": 20},
    "host": {"label": "Hosts", "short": "HST", "order": 30},
    "vm": {"label": "VMs", "short": "VM", "order": 31},
    "container": {"label": "Containers", "short": "CTR", "order": 32},
    "repo": {"label": "Repositories", "short": "GIT", "order": 40},
    "pipeline": {"label": "Pipelines", "short": "PLN", "order": 50},
    "action": {"label": "Actions", "short": "ACT", "order": 60},
    "credential": {"label": "Credentials", "short": "KEY", "order": 70},
}

RELATIONSHIP_CONSTRAINTS = [
    {"source": "group", "type": "contains", "target": "host"},
    {"source": "group", "type": "contains", "target": "vm"},
    {"source": "group", "type": "contains", "target": "container"},
    {"source": "api", "type": "discovers", "target": "host"},
    {"source": "api", "type": "discovers", "target": "vm"},
    {"source": "api", "type": "discovers", "target": "container"},
    {"source": "pipeline", "type": "uses", "target": "repo"},
    {"source": "pipeline", "type": "targets", "target": "group"},
    {"source": "action", "type": "runs_on", "target": "host"},
    {"source": "action", "type": "pulls", "target": "repo"},
    {"source": "credential", "type": "authenticates", "target": "api"},
    {"source": "credential", "type": "authenticates", "target": "host"},
]


def _blank_graph() -> dict:
    return {
        "resources": [],
        "resources_by_id": {},
        "relationships": [],
        "tree": [],
        "counts": {},
        "constraints": deepcopy(RELATIONSHIP_CONSTRAINTS),
    }


def _add_resource(graph: dict, resource: dict) -> dict:
    existing = graph["resources_by_id"].get(resource["id"])
    if existing:
        existing.setdefault("sources", [])
        for source in resource.get("sources", []):
            if source not in existing["sources"]:
                existing["sources"].append(source)
        existing.setdefault("facts", {}).update(resource.get("facts", {}))
        existing.setdefault("actions", []).extend(resource.get("actions", []))
        return existing

    normalized = {
        "id": resource["id"],
        "kind": resource["kind"],
        "name": resource["name"],
        "state": resource.get("state", "known"),
        "summary": resource.get("summary", ""),
        "sources": list(resource.get("sources", [])),
        "facts": dict(resource.get("facts", {})),
        "actions": list(resource.get("actions", [])),
        "raw": dict(resource.get("raw", {})),
    }
    graph["resources_by_id"][normalized["id"]] = normalized
    graph["resources"].append(normalized)
    return normalized


def _add_relationship(graph: dict, source_id: str, relation_type: str, target_id: str, summary: str = "") -> None:
    if source_id not in graph["resources_by_id"] or target_id not in graph["resources_by_id"]:
        return
    key = (source_id, relation_type, target_id)
    for relationship in graph["relationships"]:
        if (relationship["source_id"], relationship["type"], relationship["target_id"]) == key:
            return
    source = graph["resources_by_id"][source_id]
    target = graph["resources_by_id"][target_id]
    graph["relationships"].append(
        {
            "source_id": source_id,
            "source_label": source["name"],
            "source_kind": source["kind"],
            "type": relation_type,
            "target_id": target_id,
            "target_label": target["name"],
            "target_kind": target["kind"],
            "summary": summary,
        }
    )


def _node_kind(node_data: dict, resolved: dict) -> str:
    provider = str(resolved.get("provider") or node_data.get("provider") or "").lower()
    node_type = str(resolved.get("type") or node_data.get("type") or node_data.get("resource_type") or "").lower()
    if provider in {"proxmox", "qemu"} or node_type in {"vm", "qemu"}:
        return "vm"
    if provider in {"lxc", "docker"} or node_type in {"container", "lxc"}:
        return "container"
    return "host"


def _integration_configured(name: str, config: dict) -> bool:
    if name == "proxmox":
        return bool(config.get("api_url") and (config.get("token_name") or config.get("username")))
    if name == "ansible":
        return bool(config.get("controller_host") or config.get("inventory_path"))
    if name == "docker":
        return bool(config.get("manager_host") or config.get("stack_name"))
    if name == "ssh":
        return bool(config.get("private_key_path") or config.get("public_key_path"))
    return any(bool(value) for value in config.values())


def _host_from_url(url: str) -> str:
    parsed = urlparse(url)
    return parsed.netloc or parsed.path


def _pipeline_actions(pipeline: dict) -> list[dict]:
    return [
        {"label": "Edit pipeline", "href": f"/pipelines/{pipeline['id']}/edit"},
        {"label": "Open pipelines", "href": "/pipelines"},
    ]


def build_resource_graph() -> dict:
    graph = _blank_graph()
    rules = load_rules()
    tenant_slug = get_effective_tenant_slug()

    integrations = load_integrations()
    for name, config in sorted(integrations.items()):
        configured = _integration_configured(name, config)
        api_id = f"api:{name}"
        facts = {"configured": "yes" if configured else "no"}
        if name == "proxmox" and config.get("api_url"):
            facts["endpoint"] = _host_from_url(config["api_url"])
        if name == "ansible" and config.get("inventory_path"):
            facts["inventory"] = config["inventory_path"]
        if name == "ssh":
            facts["key path"] = config.get("public_key_path") or config.get("private_key_path") or "unset"
        _add_resource(
            graph,
            {
                "id": api_id,
                "kind": "api",
                "name": name.title(),
                "state": "configured" if configured else "needs setup",
                "summary": "Integration endpoint available for inventory and actions." if configured else "Integration exists but is not fully configured.",
                "sources": ["integrations"],
                "facts": facts,
                "actions": [{"label": "Open integrations", "href": "/integrations"}],
            },
        )
        if name == "ssh":
            key_id = "credential:ssh-default"
            _add_resource(
                graph,
                {
                    "id": key_id,
                    "kind": "credential",
                    "name": config.get("key_name") or "Default SSH key",
                    "state": "configured" if configured else "needs setup",
                    "summary": "Default SSH credential used by direct SSH operations.",
                    "sources": ["integrations"],
                    "facts": {"public key": config.get("public_key_path") or "unset"},
                    "actions": [{"label": "Open integrations", "href": "/integrations"}],
                },
            )
            _add_relationship(graph, key_id, "authenticates", api_id, "SSH mode uses this key material.")

    for group_name, group_data in sorted(rules.get("groups", {}).items()):
        group_id = f"group:{group_name}"
        hosts = resolve_group_hosts(rules, group_name)
        locals_meta = group_data.get("locals", {})
        _add_resource(
            graph,
            {
                "id": group_id,
                "kind": "group",
                "name": group_name,
                "state": locals_meta.get("state", "defined"),
                "summary": f"{len(hosts)} resources in this inventory group.",
                "sources": ["rules"],
                "facts": {
                    "environment": locals_meta.get("env") or rules.get("globals", {}).get("env", "unset"),
                    "datacenter": locals_meta.get("datacenter") or rules.get("globals", {}).get("datacenter", "unset"),
                    "workflow": locals_meta.get("workflow", "unset"),
                },
                "actions": [
                    {"label": "Open group", "href": f"/group/{group_name}/hosts"},
                    {"label": "Edit group", "href": f"/group/{group_name}/edit"},
                ],
                "raw": {"locals": locals_meta},
            },
        )
        for host_name, node_data, resolved in hosts:
            kind = _node_kind(node_data, resolved)
            host_id = f"{kind}:{host_name}"
            provider = resolved.get("provider") or node_data.get("provider") or "manual"
            route = resolved.get("ip") or resolved.get("fqdn") or resolved.get("hostname") or ""
            _add_resource(
                graph,
                {
                    "id": host_id,
                    "kind": kind,
                    "name": host_name,
                    "state": resolved.get("state") or node_data.get("state") or "known",
                    "summary": f"{provider} resource" + (f" reachable at {route}." if route else "."),
                    "sources": ["rules"],
                    "facts": {
                        "provider": provider,
                        "route": route or "unset",
                        "os": resolved.get("os_name") or "unset",
                        "user": resolved.get("user") or "unset",
                    },
                    "actions": [
                        {"label": "Deploy", "href": f"/deploy/{group_name}/{host_name}"},
                        {"label": "Admin", "href": f"/admin?group={group_name}&host={host_name}"},
                    ],
                    "raw": {"resolved": resolved},
                },
            )
            _add_relationship(graph, group_id, "contains", host_id, "Inventory membership.")
            provider_id = f"api:{str(provider).lower()}"
            if provider_id in graph["resources_by_id"]:
                _add_relationship(graph, provider_id, "discovers", host_id, "Provider-backed resource.")
            if "api:ssh" in graph["resources_by_id"]:
                _add_relationship(graph, "credential:ssh-default", "authenticates", host_id, "SSH operations can target this resource.")

    for pipeline in demo_pipelines():
        pipeline_id = f"pipeline:{pipeline['id']}"
        _add_resource(
            graph,
            {
                "id": pipeline_id,
                "kind": "pipeline",
                "name": pipeline["name"],
                "state": "editable" if pipeline.get("editable") else "defined",
                "summary": pipeline.get("description", ""),
                "sources": ["pipeline catalog"],
                "facts": {
                    "workflow": pipeline.get("workflow", "unset"),
                    "repo": pipeline.get("repo", "unset"),
                    "stages": str(len(pipeline.get("stages", []))),
                },
                "actions": _pipeline_actions(pipeline),
                "raw": {"stages": pipeline.get("stages", []), "notes": pipeline.get("notes", "")},
            },
        )
        repo = str(pipeline.get("repo", "")).strip()
        if repo:
            repo_id = f"repo:{repo}"
            _add_resource(
                graph,
                {
                    "id": repo_id,
                    "kind": "repo",
                    "name": repo,
                    "state": "referenced",
                    "summary": "Repository path referenced by one or more pipelines.",
                    "sources": ["pipeline catalog"],
                    "facts": {"referenced by": pipeline["name"]},
                    "actions": [{"label": "Open pipelines", "href": "/pipelines"}],
                },
            )
            _add_relationship(graph, pipeline_id, "uses", repo_id, "Pipeline source or working tree.")

    for run in load_runs()[:20]:
        if run.get("tenant_slug") != tenant_slug:
            continue
        action_id = f"action:{run.get('id')}"
        workflow = str(run.get("workflow") or "workflow")
        _add_resource(
            graph,
            {
                "id": action_id,
                "kind": "action",
                "name": workflow,
                "state": run.get("status", "planned"),
                "summary": run.get("notes") or f"{workflow} run created from {run.get('trigger_source', 'api')}.",
                "sources": ["automation runs"],
                "facts": {
                    "repo": run.get("repo") or "unset",
                    "requested by": run.get("requested_by") or "unset",
                    "updated": run.get("updated_at") or "unset",
                },
                "actions": [{"label": "Open run", "href": f"/pipelines/{run.get('id')}"}],
                "raw": {"stages": run.get("stages", [])},
            },
        )
        pipeline_id = f"pipeline:{workflow}"
        if pipeline_id in graph["resources_by_id"]:
            _add_relationship(graph, pipeline_id, "created", action_id, "Recent execution.")
        repo = str(run.get("repo") or "").strip()
        repo_id = f"repo:{repo}"
        if repo and repo_id in graph["resources_by_id"]:
            _add_relationship(graph, action_id, "pulls", repo_id, "Run source repository.")

    resources_by_kind = defaultdict(list)
    for resource in graph["resources"]:
        resources_by_kind[resource["kind"]].append(resource)
    graph["counts"] = {kind: len(items) for kind, items in resources_by_kind.items()}
    graph["tree"] = [
        {
            "kind": kind,
            "meta": RESOURCE_KIND_META.get(kind, {"label": kind.title(), "short": kind[:3].upper(), "order": 999}),
            "resources": sorted(items, key=lambda item: item["name"].lower()),
        }
        for kind, items in sorted(
            resources_by_kind.items(),
            key=lambda item: (RESOURCE_KIND_META.get(item[0], {}).get("order", 999), item[0]),
        )
    ]
    graph["resources"] = sorted(
        graph["resources"],
        key=lambda item: (RESOURCE_KIND_META.get(item["kind"], {}).get("order", 999), item["name"].lower()),
    )
    return graph


def related_to(graph: dict, resource_id: str) -> list[dict]:
    return [
        relationship
        for relationship in graph["relationships"]
        if relationship["source_id"] == resource_id or relationship["target_id"] == resource_id
    ]
