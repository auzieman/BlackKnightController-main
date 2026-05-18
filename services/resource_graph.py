from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from urllib.parse import urlparse

from services.action_catalog import actions_for_kind, list_actions
from services.automation_runs import load_runs
from services.integration_store import (
    load_ansible_snapshot,
    load_docker_snapshot,
    load_integrations,
    load_proxmox_snapshot,
)
from services.inventory_model import resolve_group_hosts
from services.pipeline_catalog import demo_pipelines
from services.rules_store import load_rules
from services.tenant_context import get_effective_tenant_slug

RESOURCE_KIND_META = {
    "api": {"label": "APIs", "short": "API", "order": 10},
    "cluster": {"label": "Clusters", "short": "K8S", "order": 20},
    "group": {"label": "Groups", "short": "GRP", "order": 21},
    "host": {"label": "Hosts", "short": "HST", "order": 30},
    "vm": {"label": "VMs", "short": "VM", "order": 31},
    "container": {"label": "Containers", "short": "CTR", "order": 32},
    "kubernetes-node": {"label": "Kubernetes Nodes", "short": "KND", "order": 33},
    "repo": {"label": "Repositories", "short": "GIT", "order": 40},
    "pipeline": {"label": "Pipelines", "short": "PLN", "order": 50},
    "action": {"label": "Actions", "short": "ACT", "order": 60},
    "credential": {"label": "Credentials", "short": "KEY", "order": 70},
}

RELATIONSHIP_CONSTRAINTS = [
    {"source": "group", "type": "contains", "target": "host"},
    {"source": "group", "type": "contains", "target": "vm"},
    {"source": "group", "type": "contains", "target": "container"},
    {"source": "cluster", "type": "contains", "target": "kubernetes-node"},
    {"source": "api", "type": "discovers", "target": "host"},
    {"source": "api", "type": "discovers", "target": "vm"},
    {"source": "api", "type": "discovers", "target": "container"},
    {"source": "pipeline", "type": "uses", "target": "repo"},
    {"source": "pipeline", "type": "targets", "target": "group"},
    {"source": "action", "type": "runs_on", "target": "host"},
    {"source": "action", "type": "runs_on", "target": "vm"},
    {"source": "action", "type": "runs_on", "target": "kubernetes-node"},
    {"source": "action", "type": "pulls", "target": "repo"},
    {"source": "pipeline", "type": "composes", "target": "action"},
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
        existing.setdefault("sections", {}).update(resource.get("sections", {}))
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
        "sections": dict(resource.get("sections", {})),
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
    resource_kind = str(resolved.get("resource_kind") or node_data.get("resource_kind") or "").lower()
    if resource_kind in RESOURCE_KIND_META:
        return resource_kind
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


def _resource_action_links(kind: str, existing: list[dict] | None = None) -> list[dict]:
    actions = list(existing or [])
    for action in actions_for_kind(kind):
        actions.append(
            {
                "label": action["label"],
                "href": f"/resources?kind=action&resource=action:{action['id']}",
            }
        )
    return actions


def _fmt_bytes(value) -> str:
    try:
        size = float(value or 0)
    except (TypeError, ValueError):
        return "unset"
    units = ["B", "KiB", "MiB", "GiB", "TiB"]
    for unit in units:
        if abs(size) < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(size)} {unit}"
            return f"{size:.1f} {unit}"
        size /= 1024
    return "unset"


def _fmt_percent(value) -> str:
    try:
        return f"{float(value or 0) * 100:.1f}%"
    except (TypeError, ValueError):
        return "unset"


def _fmt_uptime(seconds) -> str:
    try:
        total = int(seconds or 0)
    except (TypeError, ValueError):
        return "unset"
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, _ = divmod(rem, 60)
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def _kv(**items) -> dict:
    return {key: ("unset" if value in (None, "") else str(value)) for key, value in items.items()}


def _snapshot_indexes() -> dict:
    proxmox = load_proxmox_snapshot() or {}
    docker = load_docker_snapshot() or {}
    ansible = load_ansible_snapshot() or {}

    proxmox_by_name = {}
    proxmox_by_vmid = {}
    for item in list(proxmox.get("virtual_machines", [])) + list(proxmox.get("containers", [])):
        name = str(item.get("name") or "").strip()
        vmid = str(item.get("vmid") or "").strip()
        if name:
            proxmox_by_name[name.lower()] = item
        if vmid:
            proxmox_by_vmid[vmid] = item

    docker_nodes = {
        str(item.get("Hostname") or item.get("Name") or "").strip().lower(): item
        for item in docker.get("nodes", [])
        if item.get("Hostname") or item.get("Name")
    }
    docker_services = {
        str(item.get("Name") or "").strip().lower(): item
        for item in docker.get("services", [])
        if item.get("Name")
    }

    return {
        "proxmox": proxmox,
        "proxmox_by_name": proxmox_by_name,
        "proxmox_by_vmid": proxmox_by_vmid,
        "docker_nodes": docker_nodes,
        "docker_services": docker_services,
        "ansible": ansible,
    }


def _operational_sections(kind: str, host_name: str, node_data: dict, resolved: dict, indexes: dict) -> tuple[dict, dict]:
    facts = {}
    sections = {}
    proxmox_item = None
    vmid = str(resolved.get("vmid") or node_data.get("vmid") or "").strip()
    if vmid:
        proxmox_item = indexes["proxmox_by_vmid"].get(vmid)
    if proxmox_item is None:
        proxmox_item = indexes["proxmox_by_name"].get(host_name.lower())

    if proxmox_item:
        facts.update(
            {
                "vmid": proxmox_item.get("vmid", vmid),
                "proxmox node": proxmox_item.get("node", "unset"),
                "status": proxmox_item.get("status", "unset"),
            }
        )
        sections["compute"] = _kv(
            cpus=proxmox_item.get("cpus"),
            memory=f"{_fmt_bytes(proxmox_item.get('mem'))} / {_fmt_bytes(proxmox_item.get('maxmem'))}",
            cpu_used=_fmt_percent(proxmox_item.get("cpu")),
            uptime=_fmt_uptime(proxmox_item.get("uptime")),
            pid=proxmox_item.get("pid"),
        )
        sections["storage"] = _kv(
            disk=f"{_fmt_bytes(proxmox_item.get('disk'))} / {_fmt_bytes(proxmox_item.get('maxdisk'))}",
            disk_read=_fmt_bytes(proxmox_item.get("diskread")),
            disk_write=_fmt_bytes(proxmox_item.get("diskwrite")),
        )
        sections["network"] = _kv(
            route=resolved.get("ip") or resolved.get("fqdn") or resolved.get("hostname"),
            net_in=_fmt_bytes(proxmox_item.get("netin")),
            net_out=_fmt_bytes(proxmox_item.get("netout")),
        )
        sections["management"] = _kv(
            provider="proxmox",
            resource_type=proxmox_item.get("type"),
            user=resolved.get("user") or node_data.get("user"),
            provisioner=resolved.get("provisioner") or node_data.get("provisioner"),
        )

    docker_node = indexes["docker_nodes"].get(host_name.lower())
    docker_service = indexes["docker_services"].get(host_name.lower())
    if docker_node:
        facts.update({"docker role": docker_node.get("ManagerStatus") or "worker", "engine": docker_node.get("EngineVersion")})
        sections.setdefault("compute", {}).update(_kv(engine=docker_node.get("EngineVersion"), availability=docker_node.get("Availability")))
        sections["management"] = {
            **sections.get("management", {}),
            **_kv(provider="docker-swarm", node_id=docker_node.get("ID"), tls=docker_node.get("TLSStatus"), role=docker_node.get("ManagerStatus") or "worker"),
        }
    if docker_service:
        facts.update({"replicas": docker_service.get("Replicas"), "image": docker_service.get("Image")})
        sections["service"] = _kv(
            image=docker_service.get("Image"),
            replicas=docker_service.get("Replicas"),
            mode=docker_service.get("Mode"),
            ports=docker_service.get("Ports"),
        )

    ansible = indexes.get("ansible") or {}
    inventory_path = ansible.get("inventory_path")
    if inventory_path and kind in {"host", "vm", "container"}:
        sections["automation"] = _kv(
            ansible_controller=ansible.get("controller_host"),
            inventory=inventory_path,
            playbooks=len(ansible.get("playbooks", [])),
        )

    return facts, sections


def build_resource_graph() -> dict:
    graph = _blank_graph()
    rules = load_rules()
    tenant_slug = get_effective_tenant_slug()
    snapshot_indexes = _snapshot_indexes()

    integrations = load_integrations()
    for action in list_actions():
        action_id = f"action:{action['id']}"
        target_kinds = ", ".join(action.get("target_kinds", [])) or "unset"
        _add_resource(
            graph,
            {
                "id": action_id,
                "kind": "action",
                "name": action["label"],
                "state": action.get("status", "planned"),
                "summary": action.get("summary", ""),
                "sources": ["action catalog"],
                "facts": {
                    "action id": action["id"],
                    "kind": action.get("kind", "unset"),
                    "risk": action.get("risk", "unset"),
                    "credential": action.get("credential_scope", "unset"),
                    "targets": target_kinds,
                },
                "sections": {
                    "inputs": action.get("inputs", {}),
                    "validations": {str(index + 1): value for index, value in enumerate(action.get("validations", []))},
                    "produces": {str(index + 1): value for index, value in enumerate(action.get("produces", []))},
                },
                "actions": [],
                "raw": action,
            },
        )

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
                "actions": _resource_action_links("api", [{"label": "Open integrations", "href": "/integrations"}]),
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
        hosts = resolve_group_hosts(rules, group_name)
        locals_meta = group_data.get("locals", {})
        group_kind = str(locals_meta.get("resource_kind") or "").strip().lower()
        if group_kind not in RESOURCE_KIND_META:
            group_kind = "group"
        group_id = f"{group_kind}:{group_name}"
        _add_resource(
            graph,
            {
                "id": group_id,
                "kind": group_kind,
                "name": group_name,
                "state": locals_meta.get("state", "defined"),
                "summary": f"{len(hosts)} resources in this inventory group.",
                "sources": ["rules"],
                "facts": {
                    "environment": locals_meta.get("env") or rules.get("globals", {}).get("env", "unset"),
                    "datacenter": locals_meta.get("datacenter") or rules.get("globals", {}).get("datacenter", "unset"),
                    "workflow": locals_meta.get("workflow", "unset"),
                    "engine": locals_meta.get("cluster_engine", "unset"),
                    "api": locals_meta.get("api_url", "unset"),
                },
                "actions": _resource_action_links(
                    group_kind,
                    [
                        {"label": "Open group", "href": f"/group/{group_name}/hosts"},
                        {"label": "Edit group", "href": f"/group/{group_name}/edit"},
                    ],
                ),
                "raw": {"locals": locals_meta},
            },
        )
        for host_name, node_data, resolved in hosts:
            kind = _node_kind(node_data, resolved)
            host_id = f"{kind}:{host_name}"
            provider = resolved.get("provider") or node_data.get("provider") or "manual"
            route = resolved.get("ip") or resolved.get("fqdn") or resolved.get("hostname") or ""
            operational_facts, sections = _operational_sections(kind, host_name, node_data, resolved, snapshot_indexes)
            facts = {
                "provider": provider,
                "route": route or "unset",
                "os": resolved.get("os_name") or "unset",
                "user": resolved.get("user") or "unset",
            }
            facts.update(operational_facts)
            _add_resource(
                graph,
                {
                    "id": host_id,
                    "kind": kind,
                    "name": host_name,
                    "state": resolved.get("state") or node_data.get("state") or "known",
                    "summary": f"{provider} resource" + (f" reachable at {route}." if route else "."),
                    "sources": ["rules"],
                    "facts": facts,
                    "sections": sections,
                    "actions": _resource_action_links(
                        kind,
                        [
                            {"label": "Deploy", "href": f"/deploy/{group_name}/{host_name}"},
                            {"label": "Admin", "href": f"/admin?group={group_name}&host={host_name}"},
                        ],
                    ),
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
        for action_name in pipeline.get("actions", []):
            action_id = f"action:{action_name}"
            if action_id in graph["resources_by_id"]:
                _add_relationship(graph, pipeline_id, "composes", action_id, "Pipeline stage uses this action definition.")
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
