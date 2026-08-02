from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from datetime import datetime, timezone
from time import monotonic
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

_RESOURCE_GRAPH_CACHE: dict[str, tuple[float, dict]] = {}

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


def _run_timestamp(run: dict) -> datetime:
    value = str(run.get("updated_at") or run.get("created_at") or "").strip()
    if value.endswith("Z"):
        value = f"{value[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _resource_sort_key(resource: dict) -> tuple:
    kind = str(resource.get("kind", ""))
    if kind in {"pipeline", "action"}:
        latest = resource.get("facts", {}).get("latest run at") or resource.get("facts", {}).get("updated") or ""
        return (
            kind not in {"pipeline", "action"},
            -_run_timestamp({"updated_at": latest}).timestamp(),
            resource["name"].lower(),
        )
    return (False, 0, resource["name"].lower())


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


def _pipeline_target_resource(target: str) -> dict | None:
    value = str(target or "").strip()
    if not value:
        return None
    if value.startswith("repo:"):
        repo_name = value.split(":", 1)[1].strip()
        if not repo_name:
            return None
        return {
            "id": f"repo:{repo_name}",
            "kind": "repo",
            "name": repo_name,
            "state": "referenced",
            "summary": "Repository referenced by a pipeline target.",
            "sources": ["pipeline target"],
            "facts": {"target": value},
            "actions": [{"label": "Open pipelines", "href": "/pipelines"}],
        }
    if value.startswith("service:"):
        service_name = value.split(":", 1)[1].strip()
        if not service_name:
            return None
        return {
            "id": f"container:{service_name}",
            "kind": "container",
            "name": service_name,
            "state": "referenced",
            "summary": "Service referenced by a pipeline target.",
            "sources": ["pipeline target"],
            "facts": {"target": value, "provider": "service"},
            "actions": _resource_action_links("container"),
        }
    if value.startswith("node:"):
        parts = value.split(":", 2)
        if len(parts) != 3:
            return None
        _, resource_kind, resource_name = [part.strip() for part in parts]
        if resource_kind not in RESOURCE_KIND_META or not resource_name:
            return None
        return {
            "id": f"{resource_kind}:{resource_name}",
            "kind": resource_kind,
            "name": resource_name,
            "state": "referenced",
            "summary": "Resource referenced by a pipeline target.",
            "sources": ["pipeline target"],
            "facts": {"target": value, "provider": "pipeline"},
            "actions": _resource_action_links(resource_kind),
        }
    return None


def _pipeline_stage_service_targets(pipeline: dict) -> list[str]:
    services: list[str] = []
    for stage in pipeline.get("stages", []):
        if not isinstance(stage, dict):
            continue
        values = stage.get("with") if isinstance(stage.get("with"), dict) else {}
        for key in ("service", "services"):
            value = values.get(key)
            candidates = value if isinstance(value, list) else [value]
            for candidate in candidates:
                service = str(candidate or "").strip()
                if not service or "${" in service:
                    continue
                if service not in services:
                    services.append(service)
    return services


def _pipeline_service_resource(service_name: str) -> dict | None:
    service = str(service_name or "").strip()
    if not service:
        return None
    return {
        "id": f"container:{service}",
        "kind": "container",
        "name": service,
        "state": "referenced",
        "summary": "Service referenced by pipeline stage metadata.",
        "sources": ["pipeline stage"],
        "facts": {"provider": "service", "service": service},
        "actions": _resource_action_links("container"),
    }


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
        proxmox_snapshot = indexes.get("proxmox") if isinstance(indexes.get("proxmox"), dict) else {}
        refresh_status = str(proxmox_snapshot.get("refresh_status") or "").strip()
        last_seen = (
            proxmox_snapshot.get("last_refreshed_at")
            or proxmox_snapshot.get("last_refresh_attempt_at")
            or proxmox_snapshot.get("captured_at")
            or proxmox_snapshot.get("generated_at")
            or "snapshot"
        )
        facts.update(
            {
                "vmid": proxmox_item.get("vmid", vmid),
                "proxmox node": proxmox_item.get("node", "unset"),
                "status": proxmox_item.get("status", "unset"),
                "inventory source": "proxmox snapshot",
                "last seen": last_seen,
            }
        )
        if refresh_status:
            facts["snapshot refresh"] = refresh_status
        if refresh_status == "unreachable":
            facts["refresh error"] = proxmox_snapshot.get("refresh_error", "unreachable")
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
            endpoint=proxmox_snapshot.get("configured_endpoint"),
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

    tenant_runs = sorted(
        [run for run in load_runs() if run.get("tenant_slug") == tenant_slug],
        key=_run_timestamp,
        reverse=True,
    )
    latest_runs_by_pipeline: dict[str, dict] = {}
    latest_runs_by_workflow: dict[str, dict] = {}
    for run in tenant_runs:
        workflow = str(run.get("workflow") or "")
        pipeline_key = str(run.get("extra", {}).get("pipeline_id") or "")
        if pipeline_key:
            latest_runs_by_pipeline.setdefault(pipeline_key, run)
        if workflow:
            latest_runs_by_workflow.setdefault(workflow, run)

    for pipeline in demo_pipelines():
        pipeline_id = f"pipeline:{pipeline['id']}"
        latest_run = latest_runs_by_pipeline.get(str(pipeline["id"])) or latest_runs_by_workflow.get(
            str(pipeline.get("workflow", ""))
        )
        facts = {
            "workflow": pipeline.get("workflow", "unset"),
            "repo": pipeline.get("repo", "unset"),
            "stages": str(len(pipeline.get("stages", []))),
        }
        if latest_run:
            facts.update(
                {
                    "latest run": latest_run.get("id") or "unset",
                    "latest status": latest_run.get("status") or "unset",
                    "latest run at": latest_run.get("updated_at") or latest_run.get("created_at") or "unset",
                }
            )
        _add_resource(
            graph,
            {
                "id": pipeline_id,
                "kind": "pipeline",
                "name": pipeline["name"],
                "state": latest_run.get("status", "editable" if pipeline.get("editable") else "defined")
                if latest_run
                else "editable"
                if pipeline.get("editable")
                else "defined",
                "summary": pipeline.get("description", ""),
                "sources": ["pipeline catalog"],
                "facts": facts,
                "actions": _pipeline_actions(pipeline),
                "raw": {
                    "stages": pipeline.get("stages", []),
                    "notes": pipeline.get("notes", ""),
                    "targets": pipeline.get("targets", {}),
                },
            },
        )
        for target in (pipeline.get("targets") or {}).values():
            target_resource = _pipeline_target_resource(str(target))
            if not target_resource:
                continue
            existing = _add_resource(graph, target_resource)
            _add_relationship(graph, pipeline_id, "targets", existing["id"], "Pipeline target metadata.")
        for service in _pipeline_stage_service_targets(pipeline):
            service_resource = _pipeline_service_resource(service)
            if not service_resource:
                continue
            existing = _add_resource(graph, service_resource)
            _add_relationship(graph, pipeline_id, "targets", existing["id"], "Pipeline stage service metadata.")
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

    for run in tenant_runs[:20]:
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
        pipeline_key = str(run.get("extra", {}).get("pipeline_id") or workflow)
        pipeline_id = f"pipeline:{pipeline_key}"
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
            "resources": sorted(items, key=_resource_sort_key),
        }
        for kind, items in sorted(
            resources_by_kind.items(),
            key=lambda item: (RESOURCE_KIND_META.get(item[0], {}).get("order", 999), item[0]),
        )
    ]
    graph["resources"] = sorted(
        graph["resources"],
        key=lambda item: (RESOURCE_KIND_META.get(item["kind"], {}).get("order", 999), *_resource_sort_key(item)),
    )
    return graph


def cached_resource_graph(ttl_seconds: float = 12.0) -> dict:
    """Return a short-lived per-tenant resource graph snapshot for UI views.

    The graph is assembled from rules, integration snapshots, runs, and pipeline
    catalog metadata. That is perfect for correctness, but unnecessarily chatty
    when a human is clicking around the UI. A small in-process TTL keeps views
    responsive while preserving the "live enough" feel for lab operations.
    """

    tenant_slug = get_effective_tenant_slug()
    now = monotonic()
    cache_key = f"tenant:{tenant_slug}"
    cached = _RESOURCE_GRAPH_CACHE.get(cache_key)
    if cached and now - cached[0] <= ttl_seconds:
        return deepcopy(cached[1])
    graph = build_resource_graph()
    _RESOURCE_GRAPH_CACHE[cache_key] = (now, deepcopy(graph))
    return graph


def related_to(graph: dict, resource_id: str) -> list[dict]:
    return [
        relationship
        for relationship in graph["relationships"]
        if relationship["source_id"] == resource_id or relationship["target_id"] == resource_id
    ]


CYTOSCAPE_NODE_TYPES = {"cluster", "host", "vm", "container", "pipeline"}


def _cytoscape_status(state: str, kind: str = "") -> str:
    normalized = str(state or "").strip().lower()
    resource_kind = str(kind or "").strip().lower()
    if normalized in {
        "",
        "unknown",
        "known",
        "defined",
        "referenced",
        "stopped",
        "inactive",
        "off",
        "powered_off",
        "legacy",
        "template",
        "retired",
        "offline",
        "stale",
    }:
        return "inactive"
    if normalized in {"failed", "failure", "error", "blocked", "needs setup", "unreachable"}:
        return "failed"
    if resource_kind in {"host", "vm", "container", "cluster"} and normalized in {
        "running",
        "active",
        "ready",
        "healthy",
        "up",
        "configured",
    }:
        return "success"
    if normalized in {"running", "active", "queued", "planned", "in_progress", "pending"}:
        return "running"
    if normalized in {"success", "healthy", "ready", "complete", "completed", "proven", "configured", "up"}:
        return "success"
    return "inactive"


def _cytoscape_edge_type(relation_type: str) -> str:
    normalized = str(relation_type or "").strip().lower()
    if normalized == "authenticates":
        return "ssh"
    if normalized in {"composes", "created"}:
        return "pipeline_flow"
    return "dependency"


def _ensure_cytoscape_parent_host(nodes_by_id: dict[str, dict], host_name: str) -> str:
    label = str(host_name or "").strip()
    parent_id = f"host:{label}"
    if parent_id not in nodes_by_id:
        nodes_by_id[parent_id] = {
            "data": {
                "id": parent_id,
                "label": label,
                "type": "host",
                "status": "success",
            }
        }
    return parent_id


def _cytoscape_stage_id(pipeline_id: str, stage: str, index: int) -> str:
    clean = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in str(stage).strip().lower())
    clean = "-".join(part for part in clean.split("-") if part)
    return f"stage:{pipeline_id.removeprefix('pipeline:')}:{index + 1:02d}:{clean or 'stage'}"


def _cytoscape_pipeline_group_id(pipeline_id: str) -> str:
    return f"pipeline-group:{pipeline_id.removeprefix('pipeline:')}"


def _cytoscape_stage_lane(stage: str, index: int) -> int:
    normalized = str(stage or "").strip().lower()
    if any(token in normalized for token in ("install", "package", "chocolatey", "vscode", "rustdesk", "chrome")):
        return -1 if index % 2 == 0 else 1
    if any(token in normalized for token in ("verify", "validate", "check", "smoke")):
        return 1
    if any(token in normalized for token in ("record", "link", "inventory", "relationship")):
        return 2
    return 0


def cytoscape_elements_from_resource_graph(graph: dict) -> dict:
    """Return Cytoscape.js elements split into nodes and edges.

    The output intentionally keeps a small public contract:
    ``nodes[].data`` has ``id``, ``label``, ``type``, ``status``, and optional
    ``parent``. ``edges[].data`` has ``id``, ``source``, ``target``, and
    normalized ``type``.
    """

    nodes_by_id: dict[str, dict] = {}
    parent_by_child: dict[str, str] = {}

    for resource in graph.get("resources", []):
        kind = str(resource.get("kind") or "").strip().lower()
        if kind not in CYTOSCAPE_NODE_TYPES:
            continue
        resource_id = str(resource.get("id") or "").strip()
        if not resource_id:
            continue
        facts = resource.get("facts") if isinstance(resource.get("facts"), dict) else {}
        state_source = facts.get("status") if kind in {"host", "vm", "container"} else resource.get("state")
        data = {
            "id": resource_id,
            "label": str(resource.get("name") or resource_id),
            "type": kind,
            "status": _cytoscape_status(str(state_source or resource.get("state") or ""), kind),
        }
        if kind == "pipeline":
            data.update({"storyRank": 0, "storyLane": 0, "layoutRole": "pipeline"})
            raw = resource.get("raw") if isinstance(resource.get("raw"), dict) else {}
            stages = raw.get("stages") if isinstance(raw.get("stages"), list) else []
            if stages:
                group_id = _cytoscape_pipeline_group_id(resource_id)
                nodes_by_id[group_id] = {
                    "data": {
                        "id": group_id,
                        "label": str(resource.get("name") or resource_id),
                        "type": "pipeline_group",
                        "status": data["status"],
                        "layoutRole": "pipeline_group",
                    }
                }
                data["parent"] = group_id
        if kind in {"vm", "container"}:
            proxmox_node = str(facts.get("proxmox node") or "").strip()
            if proxmox_node and proxmox_node != "unset":
                data["parent"] = _ensure_cytoscape_parent_host(nodes_by_id, proxmox_node)
        nodes_by_id[resource_id] = {"data": data}
        if kind == "pipeline":
            raw = resource.get("raw") if isinstance(resource.get("raw"), dict) else {}
            stages = raw.get("stages") if isinstance(raw.get("stages"), list) else []
            for index, stage in enumerate(stages):
                if isinstance(stage, dict):
                    stage_name = str(stage.get("name") or stage.get("id") or "").strip()
                else:
                    stage_name = str(stage).strip()
                if not stage_name:
                    continue
                stage_id = _cytoscape_stage_id(resource_id, stage_name, index)
                nodes_by_id[stage_id] = {
                    "data": {
                        "id": stage_id,
                        "label": stage_name,
                        "type": "stage",
                        "status": data["status"],
                        "parentPipeline": resource_id,
                        "storyRank": index + 1,
                        "storyLane": _cytoscape_stage_lane(stage_name, index),
                        "layoutRole": "stage",
                    }
                }
                if data.get("parent"):
                    nodes_by_id[stage_id]["data"]["parent"] = data["parent"]

    def _short_label(node: dict) -> str:
        label = str(node.get("data", {}).get("label") or "").strip().lower()
        return label.split(".", 1)[0]

    pve_id = "host:pve"
    active_swarm_shorts = {
        _short_label(node)
        for node in nodes_by_id.values()
        if str(node.get("data", {}).get("type") or "") == "vm"
        and str(node.get("data", {}).get("status") or "") == "running"
        and _short_label(node).startswith("swarm")
    }
    for node_id, node in list(nodes_by_id.items()):
        data = node.get("data", {})
        if str(data.get("type") or "") != "host":
            continue
        short = _short_label(node)
        if short.startswith("swarm") and short in active_swarm_shorts:
            nodes_by_id.pop(node_id, None)

    swarm_nodes = [
        node
        for node in nodes_by_id.values()
        if str(node.get("data", {}).get("type") or "") in {"host", "vm"}
        and str(node.get("data", {}).get("label") or "").lower().startswith("swarm")
        and str(node.get("data", {}).get("status") or "") != "inactive"
    ]
    inactive_swarm_nodes = [
        node
        for node in nodes_by_id.values()
        if str(node.get("data", {}).get("type") or "") in {"host", "vm"}
        and str(node.get("data", {}).get("label") or "").lower().startswith("swarm")
        and str(node.get("data", {}).get("status") or "") == "inactive"
    ]
    if swarm_nodes:
        cluster_id = "cluster:docker-swarm"
        cluster_status = "running" if any(node["data"].get("status") == "running" for node in swarm_nodes) else "success"
        nodes_by_id[cluster_id] = {
            "data": {
                "id": cluster_id,
                "label": "Docker Swarm",
                "type": "cluster",
                "status": cluster_status,
                "layoutRole": "cluster",
            }
        }
        if pve_id in nodes_by_id:
            nodes_by_id[cluster_id]["data"]["parent"] = pve_id
        for node in swarm_nodes:
            node["data"]["parent"] = cluster_id
        if inactive_swarm_nodes:
            legacy_id = "cluster:legacy-proxmox-swarm"
            nodes_by_id[legacy_id] = {
                "data": {
                    "id": legacy_id,
                    "label": "Legacy / powered off",
                    "type": "cluster",
                    "status": "inactive",
                    "layoutRole": "legacy_cluster",
                }
            }
            if pve_id in nodes_by_id:
                nodes_by_id[legacy_id]["data"]["parent"] = pve_id
            for node in inactive_swarm_nodes:
                node["data"]["parent"] = legacy_id
        for node in nodes_by_id.values():
            data = node.get("data", {})
            if data.get("type") != "container":
                continue
            label = str(data.get("label") or "").lower()
            if label.startswith("blackknight") or label.startswith("registry") or label.startswith("monitoring_"):
                manager = next(
                    (
                        swarm_node
                        for swarm_node in swarm_nodes
                        if str(swarm_node.get("data", {}).get("label") or "").lower().startswith("swarm1.")
                    ),
                    swarm_nodes[0],
                )
                data["parent"] = manager["data"]["id"]

    k3s_nodes = [
        node
        for node in nodes_by_id.values()
        if str(node.get("data", {}).get("type") or "") in {"host", "vm"}
        and any(token in str(node.get("data", {}).get("label") or "").lower() for token in ("k3s", "kube"))
    ]
    k3s_services = [
        node
        for node in nodes_by_id.values()
        if str(node.get("data", {}).get("type") or "") == "container"
        and "/" in str(node.get("data", {}).get("label") or "")
    ]
    if k3s_nodes or k3s_services:
        cluster_id = "cluster:k3s"
        cluster_status = "running" if any(node["data"].get("status") == "running" for node in k3s_nodes) else "success"
        nodes_by_id[cluster_id] = {
            "data": {
                "id": cluster_id,
                "label": "K3s / Kubernetes",
                "type": "cluster",
                "status": cluster_status,
                "layoutRole": "cluster",
            }
        }
        if pve_id in nodes_by_id:
            nodes_by_id[cluster_id]["data"]["parent"] = pve_id
        for node in k3s_nodes + k3s_services:
            node["data"]["parent"] = cluster_id

    for relationship in graph.get("relationships", []):
        source_id = str(relationship.get("source_id") or "").strip()
        target_id = str(relationship.get("target_id") or "").strip()
        if not source_id or not target_id:
            continue
        source = graph.get("resources_by_id", {}).get(source_id, {})
        target = graph.get("resources_by_id", {}).get(target_id, {})
        if (
            str(source.get("kind") or "").strip().lower() == "host"
            and str(target.get("kind") or "").strip().lower() in {"vm", "container"}
            and target_id in nodes_by_id
        ):
            parent_by_child.setdefault(target_id, source_id)

    for child_id, parent_id in parent_by_child.items():
        if parent_id in nodes_by_id and child_id in nodes_by_id:
            nodes_by_id[child_id]["data"].setdefault("parent", parent_id)

    edges = []
    seen_edges = set()
    for node in nodes_by_id.values():
        data = node.get("data", {})
        if data.get("type") != "stage":
            continue
        stage_id = str(data.get("id") or "")
        parent_pipeline = str(data.get("parentPipeline") or "")
        rank = int(data.get("storyRank") or 0)
        source_id = parent_pipeline
        if rank > 1:
            source_id = ""
            prefix = f"stage:{parent_pipeline.removeprefix('pipeline:')}:{rank - 1:02d}:"
            for candidate in nodes_by_id:
                if candidate.startswith(prefix):
                    source_id = candidate
                    break
        if not source_id or source_id not in nodes_by_id:
            continue
        edge_id = f"edge:{source_id}:pipeline_flow:{stage_id}"
        if edge_id in seen_edges:
            continue
        seen_edges.add(edge_id)
        edges.append(
            {
                "data": {
                    "id": edge_id,
                    "source": source_id,
                    "target": stage_id,
                    "type": "pipeline_flow",
                }
            }
        )
    for node in nodes_by_id.values():
        data = node.get("data", {})
        parent_id = str(data.get("parent") or "").strip()
        child_id = str(data.get("id") or "").strip()
        if not parent_id or not child_id or parent_id not in nodes_by_id:
            continue
        edge_id = f"edge:{parent_id}:dependency:{child_id}"
        if edge_id in seen_edges:
            continue
        seen_edges.add(edge_id)
        edges.append(
            {
                "data": {
                    "id": edge_id,
                    "source": parent_id,
                    "target": child_id,
                    "type": "dependency",
                }
            }
        )

    for relationship in graph.get("relationships", []):
        source_id = str(relationship.get("source_id") or "").strip()
        target_id = str(relationship.get("target_id") or "").strip()
        if source_id not in nodes_by_id or target_id not in nodes_by_id:
            continue
        edge_type = _cytoscape_edge_type(str(relationship.get("type") or "dependency"))
        edge_id = f"edge:{source_id}:{edge_type}:{target_id}"
        if edge_id in seen_edges:
            continue
        seen_edges.add(edge_id)
        edges.append(
            {
                "data": {
                    "id": edge_id,
                    "source": source_id,
                    "target": target_id,
                    "type": edge_type,
                }
            }
        )

    nodes = sorted(nodes_by_id.values(), key=lambda item: (item["data"].get("type", ""), item["data"].get("label", "")))
    edges = sorted(edges, key=lambda item: item["data"]["id"])
    return {"nodes": nodes, "edges": edges}


def apply_cytoscape_positions(elements: dict, positions: dict[str, dict[str, float]]) -> dict:
    """Attach saved Cytoscape positions without changing the public data contract."""

    if not positions:
        return elements
    for node in elements.get("nodes", []):
        node_id = str(node.get("data", {}).get("id") or "")
        position = positions.get(node_id)
        if position is None:
            continue
        try:
            node["position"] = {"x": float(position["x"]), "y": float(position["y"])}
        except (KeyError, TypeError, ValueError):
            continue
    return elements
