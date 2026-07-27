from __future__ import annotations

import json

from flask import Blueprint, render_template
from services.automation_runs import load_runs
from services.integration_store import (
    load_ansible_snapshot,
    load_docker_snapshot,
    load_integrations,
    load_kubernetes_snapshot,
    load_proxmox_snapshot,
)
from services.pipeline_catalog import demo_pipelines
from services.resource_graph import cached_resource_graph, cytoscape_elements_from_resource_graph

beta_ui_blueprint = Blueprint("beta_ui", __name__)


def _status_counts(resources: list[dict]) -> dict[str, int]:
    counts = {"running": 0, "success": 0, "warning": 0, "failed": 0, "unknown": 0}
    for resource in resources:
        state = str(resource.get("state") or resource.get("status") or "unknown").strip().lower()
        if state in counts:
            counts[state] += 1
        elif state in {"active", "ok", "healthy", "up"}:
            counts["running"] += 1
        elif state in {"error", "down", "unreachable", "offline"}:
            counts["failed"] += 1
        elif state in {"stale", "last-known", "degraded"}:
            counts["warning"] += 1
        else:
            counts["unknown"] += 1
    return counts


def _pipeline_cards() -> list[dict]:
    runs = load_runs()
    latest_by_pipeline: dict[str, dict] = {}
    for run in runs:
        pipeline_id = str((run.get("extra") or {}).get("pipeline_id") or run.get("workflow") or "").strip()
        if pipeline_id and pipeline_id not in latest_by_pipeline:
            latest_by_pipeline[pipeline_id] = run

    cards = []
    for pipeline in demo_pipelines():
        latest = latest_by_pipeline.get(str(pipeline.get("id") or ""))
        stages = list(pipeline.get("stages") or [])
        if not stages:
            stages = [{"id": name, "action": name} for name in list(pipeline.get("actions") or [])]
        cards.append(
            {
                "id": pipeline.get("id"),
                "name": pipeline.get("name") or pipeline.get("id"),
                "summary": pipeline.get("summary") or pipeline.get("description") or "",
                "status": pipeline.get("status") or "catalog",
                "tags": list(pipeline.get("tags") or [])[:6],
                "stage_count": len(stages),
                "stages": [
                    {
                        "id": stage.get("id") or stage.get("name") or f"stage-{index + 1}",
                        "action": stage.get("action") or stage.get("kind") or stage.get("name") or "",
                        "transport": stage.get("transport") or "",
                        "risk": stage.get("risk") or "",
                    }
                    for index, stage in enumerate(stages[:8])
                    if isinstance(stage, dict)
                ],
                "latest_run_status": latest.get("status") if latest else "not-run",
            }
        )
    return cards


def _integration_cards() -> list[dict]:
    integrations = load_integrations()
    snapshots = {
        "Proxmox": load_proxmox_snapshot(),
        "Docker Swarm": load_docker_snapshot(),
        "Kubernetes": load_kubernetes_snapshot(),
        "Ansible": load_ansible_snapshot(),
    }
    configured = {
        "Proxmox": bool((integrations.get("proxmox") or {}).get("api_url")),
        "Docker Swarm": bool((integrations.get("docker") or {}).get("manager_host")),
        "Kubernetes": bool((integrations.get("kubernetes") or {}).get("api_url") or (integrations.get("kubernetes") or {}).get("kubeconfig_path")),
        "Ansible": bool((integrations.get("ansible") or {}).get("controller_host")),
        "OpenStack": True,
        "IPMI / Redfish": True,
        "N2024 Switch": True,
        "IPFire Candidate": False,
    }
    cards = []
    for name, is_configured in configured.items():
        snapshot = snapshots.get(name) or {}
        refresh_status = str(snapshot.get("refresh_status") or "").strip().lower() if isinstance(snapshot, dict) else ""
        health = "snapshot" if snapshot else ("ready" if is_configured else "planned")
        detail = "Last inventory snapshot available." if snapshot else "Uses existing BKC pipeline/SSH contracts."
        if refresh_status == "unreachable":
            health = "stale"
            detail = f"Snapshot preserved; latest refresh could not reach {snapshot.get('configured_endpoint') or 'configured endpoint'}."
        elif refresh_status == "ok":
            detail = f"Inventory refreshed at {snapshot.get('last_refreshed_at') or 'latest run'}."
        cards.append(
            {
                "name": name,
                "state": "configured" if is_configured else "candidate",
                "health": health,
                "detail": detail,
            }
        )
    return cards


def _fabric_cards() -> list[dict]:
    return [
        {
            "id": "edge:internet-cloud",
            "label": "Internet / Cloud",
            "kind": "internet",
            "state": "external",
            "primary": "public routing",
            "secondary": "outside of lab control",
            "accent": "sky",
            "actions": ["dns", "tls", "vpn"],
            "facts": {
                "role": "external network layer between the home edge and hosted remote sites",
                "note": "keeps Spectrum separate from IONOS/public VPS hosting",
            },
        },
        {
            "id": "edge:spectrum",
            "label": "Spectrum Router",
            "kind": "unmanaged-wan",
            "state": "unmanaged",
            "primary": "192.168.1.1-ish",
            "secondary": "consumer router / upstream DHCP",
            "accent": "slate",
            "actions": ["observe", "avoid dhcp conflict"],
            "facts": {
                "ownership": "uncontrolled upstream",
                "role": "outside network gravity / internet edge",
            },
        },
        {
            "id": "site:ionos",
            "label": "IONOS Remote Site",
            "kind": "remote-site",
            "state": "managed",
            "primary": "auzietek.com / beta / Kanboard",
            "secondary": "public VPS pair",
            "accent": "indigo",
            "actions": ["ssh", "dns api", "cert rotation"],
            "facts": {
                "provider": "IONOS",
                "site": "auzietek-public",
                "role": "remote public hosting and future VPN endpoint",
            },
        },
        {
            "id": "host:ionos-auzietek-01",
            "label": "ionos-auzietek-01",
            "kind": "vps",
            "state": "running",
            "primary": "74.208.45.165",
            "secondary": "public edge / Drupal / beta / Kanboard",
            "accent": "indigo",
            "actions": ["ssh", "nginx", "docker"],
            "facts": {
                "ip": "74.208.45.165",
                "role": "current public front door and Docker swarm manager",
                "services": "Drupal, beta micro-blog, Kanboard, Grafana, Gogs archive route",
            },
        },
        {
            "id": "host:ionos-auzietek-02",
            "label": "ionos-auzietek-02",
            "kind": "vps",
            "state": "warning",
            "primary": "74.208.45.164",
            "secondary": "worker / disk pressure",
            "accent": "amber",
            "actions": ["ssh", "inventory", "cleanup"],
            "facts": {
                "ip": "74.208.45.164",
                "role": "secondary IONOS VPS / old worker",
                "note": "candidate for consolidation after beta migration and backups",
            },
        },
        {
            "id": "edge:ipfire",
            "label": "IPFire Edge",
            "kind": "firewall",
            "state": "candidate",
            "primary": "GREEN 10.20.0.254",
            "secondary": "RED 192.168.1.82",
            "accent": "red",
            "actions": ["ssh", "web", "nat draft"],
            "facts": {
                "fqdn": "ipfire.lab.auzietek.com",
                "green": "10.20.0.254/24",
                "red": "192.168.1.82/24",
                "note": "Temporary RED admin allow for filming from 192.168.1.90",
            },
        },
        {
            "id": "fabric:n2024",
            "label": "N2024 Switch",
            "kind": "switch",
            "state": "managed",
            "primary": "10.20.0.100",
            "secondary": "serial via Server1",
            "accent": "teal",
            "actions": ["ports", "mac table", "vlans"],
            "facts": {
                "hostname": "n2024-lab",
                "management_ip": "10.20.0.100",
                "role": "lab fabric evidence source",
            },
        },
        {
            "id": "control:ipmi",
            "label": "IPMI / iDRAC",
            "kind": "bmc",
            "state": "ready",
            "primary": "Server1 + Server2",
            "secondary": "power / boot / firmware",
            "accent": "violet",
            "actions": ["power", "boot order", "bios notes"],
            "facts": {
                "server1_bmc": "10.20.0.119",
                "server2_bmc": "10.20.0.102",
                "route": "BKC -> ns1 -> Redfish",
            },
        },
        {
            "id": "core:ns1",
            "label": "ns1 Core",
            "kind": "dns-dhcp-nfs",
            "state": "owner",
            "primary": "10.20.0.10",
            "secondary": "DHCP / DNS / PXE / NFS / NAT",
            "accent": "green",
            "actions": ["leases", "nfs", "pxe"],
            "facts": {
                "domain": "lab.auzietek.com",
                "ownership": "active DHCP/DNS/PXE/NAT until IPFire cutover",
            },
        },
        {
            "id": "platform:openstack",
            "label": "OpenStack",
            "kind": "cloud",
            "state": "running",
            "primary": "Server1",
            "secondary": "Horizon + API + local AI",
            "accent": "orange",
            "actions": ["horizon", "instances", "api"],
            "facts": {
                "host": "10.20.0.240",
                "role": "future BKC home / local AI lane",
            },
        },
        {
            "id": "platform:hypervisors",
            "label": "Hypervisors",
            "kind": "platform",
            "state": "mixed",
            "primary": "Proxmox + ESXi",
            "secondary": ".9 edge + Server2 lab",
            "accent": "blue",
            "actions": ["inventory", "vm list", "edge urls"],
            "facts": {
                "proxmox": "192.168.1.9",
                "esxi": "10.20.0.114",
            },
        },
    ]


def _pipeline_story_elements(pipeline_cards: list[dict]) -> tuple[list[dict], list[dict]]:
    """Curated beta pipeline lane.

    The full catalog is intentionally too much for the hero graph. This lane is
    the "video story": destructive bare metal, OpenStack/Proxmox bring-up, seed
    validation, and ESXi/OpenStack swarm follow-ons connected back to the
    hardware/platform objects they affect.
    """

    selected_ids = [
        "baremetal-lab-reset",
        "baremetal-openstack-lab-prepare",
        "native-openstack-all-in-one",
        "baremetal-proxmox-trial-prepare",
        "openstack-lab-seed-and-validate",
        "openstack-docker-swarm-seed",
        "baremetal-vmware-trial-prepare",
        "esxi-docker-swarm-seed",
    ]
    target_map = {
        "baremetal-lab-reset": ["host:server1", "host:server2", "control:ipmi", "core:ns1"],
        "baremetal-openstack-lab-prepare": ["host:server1", "core:ns1", "control:ipmi"],
        "native-openstack-all-in-one": ["host:server1", "platform:openstack"],
        "baremetal-proxmox-trial-prepare": ["host:server2", "platform:hypervisors", "core:ns1"],
        "openstack-lab-seed-and-validate": ["platform:openstack", "platform:hypervisors", "host:pve1"],
        "openstack-docker-swarm-seed": ["host:server1", "platform:openstack", "vm:openstack-manager-01"],
        "baremetal-vmware-trial-prepare": ["host:server2", "control:ipmi", "core:ns1"],
        "esxi-docker-swarm-seed": ["host:server2", "vm:esxi-swarm-mgr-01", "vm:esxi-swarm-worker-03"],
    }
    cards_by_id = {str(card.get("id") or ""): card for card in pipeline_cards}
    nodes: list[dict] = []
    edges: list[dict] = []
    start_x = 118
    start_y = 535
    gap_x = 178
    for index, pipeline_id in enumerate(selected_ids):
        card = cards_by_id.get(pipeline_id)
        if not card:
            continue
        node_id = f"pipeline:{pipeline_id}"
        label = str(card.get("name") or pipeline_id).replace(" — ", "\n", 1)
        nodes.append(
            {
                "data": {
                    "id": node_id,
                    "label": label,
                    "type": "pipeline",
                    "kind": "video-pipeline",
                    "status": str(card.get("latest_run_status") or card.get("status") or "catalog"),
                    "summary": card.get("summary") or "",
                    "stage_count": card.get("stage_count"),
                    "layoutRole": "pipeline_story",
                },
                "classes": "pipeline-story hidden-pipeline",
                "position": {"x": start_x + index * gap_x, "y": start_y},
            }
        )
        prior_id = node_id
        for stage_index, stage in enumerate(list(card.get("stages") or [])[:4]):
            stage_id = f"beta-stage:{pipeline_id}:{stage_index + 1:02d}"
            stage_label = str(stage.get("id") or stage.get("action") or f"stage-{stage_index + 1}")
            nodes.append(
                {
                    "data": {
                        "id": stage_id,
                        "label": stage_label,
                        "type": "stage",
                        "kind": "pipeline-step",
                        "status": str(card.get("latest_run_status") or "defined"),
                        "pipeline": pipeline_id,
                        "action": stage.get("action") or "",
                        "transport": stage.get("transport") or "",
                        "risk": stage.get("risk") or "",
                        "layoutRole": "pipeline_story_stage",
                    },
                    "classes": "pipeline-story hidden-pipeline",
                    "position": {"x": start_x + index * gap_x, "y": start_y + 92 + stage_index * 58},
                }
            )
            edges.append(
                {
                    "data": {
                        "id": f"edge:{prior_id}:pipeline_flow:{stage_id}",
                        "source": prior_id,
                        "target": stage_id,
                        "type": "pipeline_flow",
                        "label": "then",
                    },
                    "classes": "pipeline-story hidden-pipeline",
                }
            )
            prior_id = stage_id
        for target_id in target_map.get(pipeline_id, []):
            edges.append(
                {
                    "data": {
                        "id": f"edge:{node_id}:affects:{target_id}",
                        "source": node_id,
                        "target": target_id,
                        "type": "affects",
                        "label": "affects",
                    },
                    "classes": "pipeline-story hidden-pipeline",
                }
            )
    return nodes, edges


def _beta_graph_elements(elements: dict, fabric_cards: list[dict], pipeline_cards: list[dict]) -> dict:
    """Return a beta-friendly graph.

    The full resource graph is excellent as data, but too dense for the beta
    hero canvas because pipeline stages alone can add hundreds of compound
    nodes. Keep the canvas focused on infrastructure and put pipelines in the
    workbench below.
    """

    allowed_types = {"host", "vm", "container", "cluster"}
    nodes = []
    kept_ids: set[str] = set()
    for node in elements.get("nodes", []):
        data = node.get("data") if isinstance(node, dict) else {}
        node_type = str(data.get("type") or "").strip().lower()
        node_id = str(data.get("id") or "").strip()
        label = str(data.get("label") or "")
        if not node_id:
            continue
        if node_type not in allowed_types:
            continue
        if label.endswith(":") and node_id.startswith("host:"):
            continue
        kept_ids.add(node_id)
        nodes.append({"data": {k: v for k, v in data.items() if k != "parent"}})

    fabric_node_map = {
        "edge:internet-cloud": {"label": "Internet\nCloud", "type": "cloud", "status": "external"},
        "edge:spectrum": {"label": "Spectrum", "type": "isp", "status": "unmanaged"},
        "site:ionos": {"label": "IONOS\nRemote Site", "type": "remote-site", "status": "running"},
        "host:ionos-auzietek-01": {"label": "IONOS 01\n74.208.45.165", "type": "host", "status": "running"},
        "host:ionos-auzietek-02": {"label": "IONOS 02\n74.208.45.164", "type": "host", "status": "warning"},
        "edge:ipfire": {"label": "IPFire", "type": "firewall", "status": "candidate"},
        "fabric:n2024": {"label": "N2024", "type": "switch", "status": "running"},
        "control:ipmi": {"label": "iDRAC", "type": "bmc", "status": "running"},
        "core:ns1": {"label": "ns1", "type": "host", "status": "running"},
        "platform:openstack": {"label": "OpenStack", "type": "cluster", "status": "running"},
        "platform:hypervisors": {"label": "Hypervisors", "type": "cluster", "status": "running"},
    }
    for card in fabric_cards:
        node_id = str(card.get("id") or "")
        data = fabric_node_map.get(node_id)
        if not data:
            continue
        kept_ids.add(node_id)
        positions = {
            "edge:internet-cloud": {"x": 70, "y": 120},
            "edge:spectrum": {"x": 80, "y": 280},
            "edge:ipfire": {"x": 250, "y": 280},
            "fabric:n2024": {"x": 430, "y": 280},
            "control:ipmi": {"x": 620, "y": 160},
            "core:ns1": {"x": 620, "y": 380},
            "platform:openstack": {"x": 850, "y": 160},
            "platform:hypervisors": {"x": 850, "y": 380},
            "site:ionos": {"x": 250, "y": 60},
            "host:ionos-auzietek-01": {"x": 450, "y": 35},
            "host:ionos-auzietek-02": {"x": 450, "y": 115},
        }
        node = {"data": {"id": node_id, **data}}
        if node_id in positions:
            node["position"] = positions[node_id]
            node["locked"] = True
        nodes.append(node)

    pipeline_nodes, pipeline_edges = _pipeline_story_elements(pipeline_cards)
    for node in pipeline_nodes:
        node_id = str((node.get("data") or {}).get("id") or "")
        if node_id:
            kept_ids.add(node_id)
            nodes.append(node)

    edge_specs = [
        ("edge:internet-cloud", "edge:spectrum", "wan"),
        ("edge:internet-cloud", "site:ionos", "public_route"),
        ("site:ionos", "host:ionos-auzietek-01", "hosts"),
        ("site:ionos", "host:ionos-auzietek-02", "hosts"),
        ("edge:spectrum", "edge:ipfire", "wan"),
        ("edge:ipfire", "core:ns1", "protects"),
        ("edge:ipfire", "fabric:n2024", "connected_to"),
        ("fabric:n2024", "control:ipmi", "observes"),
        ("fabric:n2024", "platform:openstack", "connects"),
        ("fabric:n2024", "platform:hypervisors", "connects"),
        ("core:ns1", "platform:openstack", "provides_services"),
        ("core:ns1", "platform:hypervisors", "provides_services"),
    ]
    edges = [
        {"data": {"id": f"edge:{source}:{relation}:{target}", "source": source, "target": target, "type": relation}}
        for source, target, relation in edge_specs
        if source in kept_ids and target in kept_ids
    ]
    edges.extend(
        edge
        for edge in pipeline_edges
        if str((edge.get("data") or {}).get("source") or "") in kept_ids
        and str((edge.get("data") or {}).get("target") or "") in kept_ids
    )

    for edge in elements.get("edges", []):
        data = edge.get("data") if isinstance(edge, dict) else {}
        source = str(data.get("source") or "")
        target = str(data.get("target") or "")
        edge_type = str(data.get("type") or "")
        if source in kept_ids and target in kept_ids and edge_type != "pipeline_flow":
            edges.append({"data": data})

    return {"nodes": nodes[:140], "edges": edges[:220]}


@beta_ui_blueprint.get("/beta")
def beta_home():
    graph = cached_resource_graph(ttl_seconds=10)
    resources = list(graph.get("resources", []))
    fabric_cards = _fabric_cards()
    pipeline_cards = _pipeline_cards()
    elements = _beta_graph_elements(cytoscape_elements_from_resource_graph(graph), fabric_cards, pipeline_cards)
    return render_template(
        "beta/home.html.j2",
        graph=graph,
        cytoscape_elements_json=json.dumps(elements, sort_keys=True),
        pipeline_cards_json=json.dumps(pipeline_cards[:18], sort_keys=True),
        sample_contract_json=json.dumps(
            {
                "view": "beta-workbench",
                "intent": "graph-linked operations",
                "actions": ["inspect", "edit", "run", "validate", "open evidence"],
                "guardrail": "production UI remains unchanged",
            },
            indent=2,
            sort_keys=True,
        ),
        status_counts=_status_counts(resources),
        resource_count=len(resources),
        relationship_count=len(graph.get("relationships", [])),
        pipeline_count=len(pipeline_cards),
        active_pipeline_count=sum(1 for card in pipeline_cards if str(card.get("latest_run_status")).lower() in {"active", "running", "queued"}),
        pipeline_cards=pipeline_cards[:18],
        integration_cards=_integration_cards(),
        fabric_cards=fabric_cards,
        fabric_cards_json=json.dumps(fabric_cards, sort_keys=True),
    )
