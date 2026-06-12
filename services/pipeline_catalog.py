from __future__ import annotations

import json
import os
import tempfile
from copy import deepcopy
from pathlib import Path
from uuid import uuid4

from services.rules_store import BASE_DIR

BUILTIN_PIPELINES = [
    {
        "id": "auzix-lab-demo",
        "name": "Auzix Lab Demo",
        "repo": "tabor-linux-forge",
        "workflow": "lab-demo",
        "description": "Build the current Auzix artifacts, stage monitoring visibility, then hand off to the later hypervisor import and VM boot-test leg.",
        "stages": [
            "repo-sync",
            "image-build",
            "monitoring-deploy",
            "microblog-publish",
            "hypervisor-handoff",
        ],
        "notes": "Primary demo lane for the full lab loop. This remains planned until the composite executor is wired.",
        "editable": True,
        "links": [
            {"label": "BlackKnightController", "url": "http://swarm1.lab.auzietek.com:5000"},
            {"label": "Grafana", "url": "http://swarm1.lab.auzietek.com:3000"},
            {"label": "Portainer", "url": "https://swarm1.lab.auzietek.com:9443"},
            {"label": "Proxmox", "url": "https://192.168.1.9:8006"},
        ],
        "dashboards": [
            {
                "name": "Pipeline Control",
                "summary": "Use Grafana to watch queue pressure, stage timing, and failed run count as the full lab loop grows.",
                "url": "http://swarm1.lab.auzietek.com:3000",
            },
            {
                "name": "Lab Infra",
                "summary": "Watch swarm node pressure, restarts, and shared-storage backed runtime behavior during the Auzix path.",
                "url": "http://swarm1.lab.auzietek.com:3000",
            },
        ],
    },
    {
        "id": "wordpress-appliance",
        "name": "WordPress Appliance Import",
        "repo": "candidate-import",
        "workflow": "wordpress-appliance-import",
        "description": "Clone and boot a discovered WordPress-capable Proxmox template, then leave a clear handoff point for guest validation and application tuning.",
        "stages": [
            "source-select",
            "proxmox-clone",
            "boot",
            "ssh-validate",
        ],
        "notes": "First real appliance lane. It expects a discovered Proxmox VM template whose name contains wordpress or turnkey.",
        "editable": True,
        "links": [
            {"label": "BlackKnightController", "url": "http://swarm1.lab.auzietek.com:5000"},
            {"label": "Proxmox", "url": "https://192.168.1.9:8006"},
            {"label": "Grafana", "url": "http://swarm1.lab.auzietek.com:3000"},
        ],
        "dashboards": [
            {
                "name": "Pipeline Control",
                "summary": "Watch the appliance import lane while the hypervisor tier becomes part of normal pipeline execution.",
                "url": "http://swarm1.lab.auzietek.com:3000",
            },
        ],
        "tags": ["candidate", "hypervisor"],
    },
    {
        "id": "fedora-template-deploy",
        "name": "Fedora Template Deploy",
        "repo": "proxmox-template-deploy",
        "workflow": "fedora-template-deploy",
        "description": "Clone a known local Fedora 44 minimal Proxmox template, apply first-boot cloud-init settings, boot it, and leave a clean handoff for chain install or SSH-driven takeover.",
        "stages": [
            "source-select",
            "proxmox-import",
            "instance-configure",
            "boot",
            "ssh-validate",
        ],
        "notes": "Fast hypervisor test lane for the local Fedora template path. Use this to prove clone, boot, and later takeover logic without waiting on full image composition.",
        "editable": True,
        "links": [
            {"label": "BlackKnightController", "url": "http://swarm1.lab.auzietek.com:5000"},
            {"label": "Proxmox", "url": "https://192.168.1.9:8006"},
            {"label": "Grafana", "url": "http://swarm1.lab.auzietek.com:3000"},
        ],
        "dashboards": [
            {
                "name": "Pipeline Control",
                "summary": "Use the fast Fedora template lane to validate Proxmox operations and later SSH guest checks without waiting on the full image build path.",
                "url": "http://swarm1.lab.auzietek.com:3000",
            },
        ],
        "tags": ["candidate", "hypervisor", "deploy"],
    },
    {
        "id": "fedora-cosmic-postinstall",
        "name": "Fedora COSMIC Post Install",
        "repo": "proxmox-template-deploy",
        "workflow": "fedora-cosmic-postinstall",
        "description": "Take over a freshly cloned Fedora VM over BKC SSH, install COSMIC Desktop unattended, enable graphical boot, reboot once, and verify the display manager is online.",
        "stages": [
            "target-select",
            "wait-ssh",
            "package-plan",
            "desktop-install",
            "graphical-enable",
            "reboot",
            "gui-validate",
            "register-resource",
        ],
        "notes": "Second-stage VM customization lane. It deliberately avoids firstboot scripts so a broken desktop setup cannot put the installer back into a boot loop.",
        "editable": True,
        "links": [
            {"label": "BlackKnightController", "url": "http://swarm1.lab.auzietek.com:5000"},
            {"label": "Proxmox", "url": "https://192.168.1.9:8006"},
            {"label": "Fedora COSMIC", "url": "https://fedoraproject.org/spins/cosmic"},
        ],
        "dashboards": [
            {
                "name": "Pipeline Control",
                "summary": "Follow SSH takeover, package install, reboot, and graphical target validation from BKC.",
                "url": "http://swarm1.lab.auzietek.com:5000/pipelines",
            },
        ],
        "tags": ["fedora", "cosmic", "desktop", "ssh", "postinstall"],
    },
    {
        "id": "k3s-fedora-cluster",
        "name": "K3s Fedora Cluster",
        "repo": "proxmox-template-deploy",
        "workflow": "k3s-fedora-cluster",
        "description": "Clone two Fedora 44 guests, bootstrap k3s with BKC SSH, pass the kube1 join token into kube2, verify node readiness, and register the cluster in inventory.",
        "stages": [
            "source-select",
            "clone-plan",
            "proxmox-clone",
            "boot",
            "discover-ssh",
            "base-os-bootstrap",
            "install-k3s-server",
            "capture-k3s-token",
            "install-k3s-agent",
            "verify-cluster",
            "register-resources",
        ],
        "notes": "BKC-native SSH example for cluster orchestration. It expects kube1.lab.auzietek.com and kube2.lab.auzietek.com to resolve after the Proxmox guests boot.",
        "editable": True,
        "links": [
            {"label": "BlackKnightController", "url": "http://swarm1.lab.auzietek.com:5000"},
            {"label": "Proxmox", "url": "https://192.168.1.9:8006"},
            {"label": "K3s API", "url": "https://kube1.lab.auzietek.com:6443"},
            {"label": "Portainer", "url": "https://swarm1.lab.auzietek.com:9443"},
            {"label": "Grafana", "url": "http://swarm1.lab.auzietek.com:3000"},
        ],
        "dashboards": [
            {
                "name": "Pipeline Control",
                "summary": "Follow clone, SSH bootstrap, k3s install, and readiness stages from the normal BKC pipeline surface.",
                "url": "http://swarm1.lab.auzietek.com:5000/pipelines",
            },
            {
                "name": "Cluster API",
                "summary": "Future API integration point for reading Kubernetes nodes, pods, services, and events through kube1.",
                "url": "https://kube1.lab.auzietek.com:6443",
            },
            {
                "name": "Portainer",
                "summary": "Existing Portainer CE control plane for Docker Swarm today and the k3s environment once the agent or kubeconfig is registered.",
                "url": "https://swarm1.lab.auzietek.com:9443",
            },
        ],
        "tags": ["kubernetes", "k3s", "hypervisor", "deploy", "ssh"],
    },
    {
        "id": "k3s-host-telemetry",
        "name": "K3s Lab Housekeeping",
        "repo": "rx-demo",
        "workflow": "k3s-host-telemetry",
        "description": "Mount shared project storage, deploy Telegraf/cAdvisor, push k3s host and pod logs to Loki, keep loadgen running, update Prometheus, and verify Grafana has k3s signals.",
        "stages": [
            "verify-k3s",
            "nfs-projects",
            "apply-host-telemetry",
            "apply-loki-logs",
            "loadgen-steady",
            "open-firewall",
            "prometheus-targets",
            "scrape-validate",
            "dashboard-link",
        ],
        "actions": [
            "k3s.nodes.ready",
            "ssh.nfs.ensure_mounts",
            "k3s.manifest.apply",
            "ssh.firewall.open_ports",
            "prometheus.scrape_job.ensure",
            "prometheus.targets.verify",
        ],
        "notes": "BKC-native SSH lane for kube1/kube2 housekeeping after the k3s app stack is online.",
        "editable": True,
        "links": [
            {"label": "BlackKnightController", "url": "http://swarm1.lab.auzietek.com:5000"},
            {"label": "Prometheus Targets", "url": "http://swarm1.lab.auzietek.com:9090/targets"},
            {"label": "Grafana", "url": "http://swarm1.lab.auzietek.com:3000"},
        ],
        "dashboards": [
            {
                "name": "Host Ops",
                "summary": "Confirm kube1 and kube2 host CPU, memory, disk, and network telemetry from Telegraf.",
                "url": "http://swarm1.lab.auzietek.com:3000/d/host-ops/host-ops",
            },
            {
                "name": "Container Overview - Telegraf",
                "summary": "Use cAdvisor and Telegraf-backed runtime panels to inspect k3s container pressure.",
                "url": "http://swarm1.lab.auzietek.com:3000/d/auzix-container-telegraf/container-overview-telegraf-auzix-lab",
            },
            {
                "name": "Loki k3s Logs",
                "summary": "Inspect k3s host and pod logs with job=k3s-hostlogs and job=k3s-pods labels.",
                "url": "http://swarm1.lab.auzietek.com:3000/explore",
            },
        ],
        "tags": ["kubernetes", "k3s", "monitoring", "storage", "ssh"],
    },
    {
        "id": "rx-demo-k3s-app-refresh",
        "name": "Rx Demo K3s App Refresh",
        "repo": "rx-demo",
        "workflow": "rx-demo-k3s-app-refresh",
        "description": "Build the staged rx-demo UI image on the swarm manager, import it into both k3s nodes, apply the lab overlay, restart rx-ui, and smoke the routed UI actions.",
        "stages": [
            "verify-k3s",
            "source-check",
            "build-rx-ui-image",
            "import-rx-ui-image",
            "apply-lab-overlay",
            "smoke-ui-routes",
            "dashboard-link",
        ],
        "actions": [
            "k3s.nodes.ready",
            "docker.image.build",
            "k3s.image.import",
            "k3s.manifest.apply",
            "http.route.smoke",
        ],
        "notes": "This lane expects the working rx-demo tree to be staged at /mnt/swarm/shared/rx-demo so BKC can deploy the test build without ad-hoc kube1 commands.",
        "editable": True,
        "links": [
            {"label": "BlackKnightController", "url": "http://swarm1.lab.auzietek.com:5000"},
            {"label": "Rx UI", "url": "http://kube1.lab.auzietek.com:30080"},
            {"label": "Grafana", "url": "http://swarm1.lab.auzietek.com:3000"},
        ],
        "dashboards": [
            {
                "name": "Rx Executive Health",
                "summary": "Watch route-level UI activity and component health after the k3s refresh.",
                "url": "http://swarm1.lab.auzietek.com:3000/d/rx-executive-health/rx-demo-executive-health",
            },
            {
                "name": "Tempo Traces",
                "summary": "Inspect browser-to-UI-to-API route traces after the routed action smoke checks run.",
                "url": "http://swarm1.lab.auzietek.com:3000/d/rx-tempo-traces/rx-tempo-traces",
            },
        ],
        "tags": ["kubernetes", "k3s", "rx-demo", "deploy", "ssh"],
    },
    {
        "id": "tabor-build",
        "name": "Auzix Image Build",
        "repo": "tabor-linux-forge",
        "workflow": "tabor-build",
        "description": "Use the staged tabor-linux-forge source on ns1, prepare the swarm builder, construct the current Auzix build artifacts, and verify they landed on shared storage.",
        "stages": [
            "repo-sync",
            "builder-prepare",
            "image-build",
            "artifact-publish",
        ],
        "notes": "Current build lane for the custom image builder. The end state is two artifacts: boot media and a runnable VM image, with later Proxmox handoff layered on top.",
        "editable": True,
        "links": [
            {"label": "BlackKnightController", "url": "http://swarm1.lab.auzietek.com:5000"},
            {"label": "Grafana", "url": "http://swarm1.lab.auzietek.com:3000"},
            {"label": "Portainer", "url": "https://swarm1.lab.auzietek.com:9443"},
            {"label": "Proxmox", "url": "https://192.168.1.9:8006"},
        ],
        "dashboards": [
            {
                "name": "Host Ops",
                "summary": "Watch swarm and ns1 resource pressure while the builder container runs.",
                "url": "http://swarm1.lab.auzietek.com:3000",
            },
            {
                "name": "Container Overview - Telegraf",
                "summary": "Inspect the builder container and related service activity while image and media artifacts are produced.",
                "url": "http://swarm1.lab.auzietek.com:3000/d/auzix-container-telegraf/container-overview-telegraf-auzix-lab",
            },
        ],
    },
    {
        "id": "auzix-vm130-deploy",
        "name": "AuziX VM130 Deploy",
        "repo": "AuziX",
        "workflow": "auzix-vm130-deploy",
        "description": "Deploy the generated AuziX runtime startup and Midori wrapper to the installed VMID 130 guest, then verify browser networking and user-state permissions.",
        "stages": [
            "source-verify",
            "runtime-deploy",
            "network-validate",
        ],
        "notes": "Repeatable SSH deployment for root@192.168.1.163. The lane consumes the generated AuzixRoot on the shared AuziX build workspace and records the deployed Git commit in /System/State/deployments.",
        "editable": True,
        "links": [
            {"label": "BlackKnightController", "url": "http://swarm1.lab.auzietek.com:5000"},
            {"label": "Proxmox", "url": "https://192.168.1.9:8006"},
        ],
        "dashboards": [
            {
                "name": "Pipeline Control",
                "summary": "Track the VM130 payload copy and browser-network validation as separate rerunnable stages.",
                "url": "http://swarm1.lab.auzietek.com:5000/pipelines",
            },
        ],
        "tags": ["auzix", "vm130", "deploy", "ssh"],
    },
    {
        "id": "auzix-installer-foundation",
        "name": "AuziX Installer Foundation",
        "repo": "AuziX",
        "workflow": "auzix-installer-foundation",
        "description": "Build and validate the Lua installer engine, dialog TUI, JSON plan contract, and graphical frontend protocol from the staged AuziX source.",
        "resource_class": "slow",
        "stages": [
            "source-verify",
            "installer-build",
            "contract-test",
            "artifact-report",
        ],
        "notes": "Non-destructive installer lane. It packages Lua and dialog, validates guarded plan execution with a fake executor, and reports the staged installer artifacts without running auzix-install-disk or changing VM130.",
        "editable": True,
        "links": [
            {"label": "BlackKnightController", "url": "http://swarm1.lab.auzietek.com:5000"},
            {"label": "AuziX Pipelines", "url": "http://swarm1.lab.auzietek.com:5000/pipelines?pipeline=auzix-installer-foundation"},
        ],
        "dashboards": [
            {
                "name": "Pipeline Control",
                "summary": "Track installer package construction and the guarded execution contract on the shared build workspace.",
                "url": "http://swarm1.lab.auzietek.com:5000/pipelines",
            },
        ],
        "tags": ["auzix", "installer", "lua", "dialog", "build"],
    },
    {
        "id": "lab-cluster-storage",
        "name": "Lab Cluster Storage",
        "repo": "BlackKnightController",
        "workflow": "lab-cluster-storage",
        "description": "Preflight, grow, and verify the LVM-backed root filesystems on the Swarm and k3s guests.",
        "stages": ["storage-preflight", "swarm-grow", "k3s-grow", "storage-verify"],
        "actions": ["ssh.lvm.grow_root"],
        "notes": "Idempotent guest-side growth to 50 GiB. The current 60 GiB virtual disks retain about 8 GiB free in each volume group.",
        "editable": True,
        "links": [
            {"label": "BlackKnightController", "url": "http://swarm1.lab.auzietek.com:5000"},
            {"label": "Proxmox", "url": "https://192.168.1.9:8006"},
        ],
        "tags": ["lab", "storage", "swarm", "k3s", "lvm"],
    },
    {
        "id": "monitoring-stack",
        "name": "Monitoring Bring-Up",
        "repo": "lab/ns1/ansible",
        "workflow": "monitoring-stack",
        "description": "Deploy and validate Grafana, Prometheus, Loki, and supporting exporters on the swarm.",
        "stages": [
            "stack-render",
            "stack-deploy",
            "health-check",
            "grafana-init",
            "inventory-refresh",
            "dashboard-link",
        ],
        "notes": "Useful while the observability layer is still being tuned.",
        "editable": True,
        "links": [
            {"label": "Prometheus", "url": "http://swarm1.lab.auzietek.com:9090"},
            {"label": "Loki", "url": "http://swarm1.lab.auzietek.com:3100"},
            {"label": "Grafana", "url": "http://swarm1.lab.auzietek.com:3000"},
            {"label": "Portainer", "url": "https://swarm1.lab.auzietek.com:9443"},
        ],
        "dashboards": [
            {
                "name": "Monitoring Tier",
                "summary": "Verify Grafana, Prometheus, Loki, and exporter health after BKC finishes the bring-up lane.",
                "url": "http://swarm1.lab.auzietek.com:3000",
            },
            {
                "name": "Runtime Convergence",
                "summary": "Inspect service replicas and runtime signals when stack deploy completes but the operator still needs depth.",
                "url": "http://swarm1.lab.auzietek.com:3000",
            },
        ],
    },
    {
        "id": "auzix-fedora-workstation",
        "name": "Auzix Fedora Workstation",
        "repo": "tabor-linux-forge",
        "workflow": "fedora-workstation-spin",
        "description": "Generate and stage the Fedora workstation build kit on shared storage: kickstart, manifest, and build plan for the later full compose and Proxmox handoff.",
        "stages": [
            "repo-sync",
            "manifest-resolve",
            "image-compose",
            "artifact-publish",
            "hypervisor-handoff",
        ],
        "notes": "First real version stages a build kit on NFS rather than pretending the full workstation image compose already exists.",
        "editable": True,
        "links": [
            {"label": "BlackKnightController", "url": "http://swarm1.lab.auzietek.com:5000"},
            {"label": "Grafana", "url": "http://swarm1.lab.auzietek.com:3000"},
            {"label": "Portainer", "url": "https://swarm1.lab.auzietek.com:9443"},
            {"label": "Proxmox", "url": "https://192.168.1.9:8006"},
        ],
        "dashboards": [
            {
                "name": "Pipeline Control",
                "summary": "Use the existing pipeline dashboard to compare the lighter Fedora workstation lane against the heavier Auzix builder path.",
                "url": "http://swarm1.lab.auzietek.com:3000",
            },
            {
                "name": "Host Ops",
                "summary": "Watch builder-node pressure while the workstation image lane is taking shape.",
                "url": "http://swarm1.lab.auzietek.com:3000",
            },
        ],
    },
    {
        "id": "microblog-publish",
        "name": "Micro-Blog Publish",
        "repo": "micro-blog",
        "workflow": "microblog-publish",
        "description": "Deploy and validate the micro-blog narrative surface on the lab manager, then refresh inventory and publish operator links.",
        "stages": [
            "repo-sync",
            "stack-deploy",
            "health-check",
            "inventory-refresh",
            "dashboard-link",
        ],
        "notes": "Narrative layer for markdown, project docs, and later Mermaid-backed lab state views.",
        "editable": True,
        "links": [
            {"label": "Micro-Blog UI", "url": "http://swarm1.lab.auzietek.com:8081/blog"},
            {"label": "Micro-Blog API", "url": "http://swarm1.lab.auzietek.com:8080/healthz"},
            {"label": "Grafana", "url": "http://swarm1.lab.auzietek.com:3000"},
            {"label": "Loki", "url": "http://swarm1.lab.auzietek.com:3100"},
        ],
        "dashboards": [
            {
                "name": "Micro-Blog Service Health",
                "summary": "Track API, worker, and projection stability while the content lane is being hardened.",
                "url": "http://swarm1.lab.auzietek.com:3000",
            },
            {
                "name": "Filesystem Publish Flow",
                "summary": "Use Grafana and Loki to inspect filesystem sync, queue handling, and publish events for markdown-backed content.",
                "url": "http://swarm1.lab.auzietek.com:3000",
            },
        ],
    },
    {
        "id": "host-telemetry",
        "name": "Host Telemetry",
        "repo": "lab/ns1/ansible",
        "workflow": "host-telemetry",
        "description": "Apply Telegraf to ns1, Proxmox, and the swarm hosts, then verify Prometheus scrape endpoints and refresh inventory.",
        "stages": [
            "telemetry-apply",
            "health-check",
            "inventory-refresh",
            "dashboard-link",
        ],
        "notes": "Useful before heavy builds so host CPU, memory, disk, and system behavior stay visible.",
        "editable": True,
        "links": [
            {"label": "Grafana", "url": "http://swarm1.lab.auzietek.com:3000"},
            {"label": "Prometheus", "url": "http://swarm1.lab.auzietek.com:9090"},
            {"label": "Loki", "url": "http://swarm1.lab.auzietek.com:3100"},
        ],
        "dashboards": [
            {
                "name": "Host Ops",
                "summary": "Use the host telemetry lane to keep an eye on CPU, memory, disk, and load across ns1, Proxmox, and the swarm nodes.",
                "url": "http://swarm1.lab.auzietek.com:3000",
            },
            {
                "name": "Swarm Runtime",
                "summary": "Pair the host telemetry lane with container and node signals while heavier lanes such as tabor come online.",
                "url": "http://swarm1.lab.auzietek.com:3000",
            },
        ],
    },
]


def _definitions_path() -> Path:
    return BASE_DIR / "dictionaries" / "pipeline_definitions.local.json"


def _load_catalog_state() -> dict:
    path = _definitions_path()
    if not path.exists():
        return {"pipelines": {}, "custom_pipelines": []}
    try:
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return {"pipelines": {}, "custom_pipelines": []}
    return {
        "pipelines": dict(payload.get("pipelines", {})),
        "custom_pipelines": list(payload.get("custom_pipelines", [])),
    }


def _load_overrides() -> dict[str, dict]:
    return _load_catalog_state()["pipelines"]


def _load_custom_pipelines() -> list[dict]:
    return _load_catalog_state()["custom_pipelines"]


def _save_catalog_state(state: dict) -> None:
    path = _definitions_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        {
            "pipelines": state.get("pipelines", {}),
            "custom_pipelines": state.get("custom_pipelines", []),
        },
        indent=2,
        sort_keys=True,
    ) + "\n"
    fd, tmp_name = tempfile.mkstemp(prefix=".pipeline_definitions.", suffix=".json", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    finally:
        try:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)
        except OSError:
            pass


def _save_overrides(overrides: dict[str, dict]) -> None:
    state = _load_catalog_state()
    state["pipelines"] = overrides
    _save_catalog_state(state)


def _merge_pipeline(base: dict, override: dict) -> dict:
    merged = deepcopy(base)
    for key, value in override.items():
        if key in {"actions", "links", "dashboards", "stages"} and isinstance(value, list):
            merged[key] = deepcopy(value)
        else:
            merged[key] = value
    return merged


def demo_pipelines() -> list[dict]:
    overrides = _load_overrides()
    builtins = [_merge_pipeline(pipeline, overrides.get(pipeline["id"], {})) for pipeline in BUILTIN_PIPELINES]
    customs = [dict(item) for item in _load_custom_pipelines()]
    return builtins + customs


def pipeline_by_id(pipeline_id: str) -> dict | None:
    for pipeline in demo_pipelines():
        if pipeline["id"] == pipeline_id:
            return pipeline
    return None


def save_pipeline_override(
    pipeline_id: str,
    *,
    name: str,
    repo: str,
    description: str,
    notes: str,
    stages: list[str],
    links: list[dict],
    dashboards: list[dict],
) -> dict:
    base = next((item for item in BUILTIN_PIPELINES if item["id"] == pipeline_id), None)
    state = _load_catalog_state()
    if not base:
        custom_items = state.get("custom_pipelines", [])
        idx = next((i for i, item in enumerate(custom_items) if item.get("id") == pipeline_id), None)
        if idx is None:
            raise KeyError(pipeline_id)
        current = dict(custom_items[idx])
        current.update(
            {
                "name": name.strip() or current.get("name", pipeline_id),
                "repo": repo.strip() or current.get("repo", ""),
                "description": description.strip() or current.get("description", ""),
                "notes": notes.strip(),
                "stages": stages or list(current.get("stages", [])),
                "links": links,
                "dashboards": dashboards,
            }
        )
        custom_items[idx] = current
        state["custom_pipelines"] = custom_items
        _save_catalog_state(state)
        return pipeline_by_id(pipeline_id) or current

    existing = state.get("pipelines", {}).get(pipeline_id, {})
    override = {
        "name": name.strip() or base["name"],
        "repo": repo.strip() or base["repo"],
        "description": description.strip() or base["description"],
        "notes": notes.strip(),
        "stages": stages or list(base.get("stages", [])),
        "links": links,
        "dashboards": dashboards,
        "stage_overrides": deepcopy(existing.get("stage_overrides", {})),
    }
    overrides = state.get("pipelines", {})
    overrides[pipeline_id] = override
    state["pipelines"] = overrides
    _save_catalog_state(state)
    return pipeline_by_id(pipeline_id) or _merge_pipeline(base, override)


def stage_override(pipeline_id: str, stage_name: str) -> dict:
    pipeline = pipeline_by_id(pipeline_id) or {}
    stage_map = dict(pipeline.get("stage_overrides", {}))
    return dict(stage_map.get(stage_name, {}))


def save_stage_override(
    pipeline_id: str,
    stage_name: str,
    *,
    display_name: str,
    operator_notes: str,
    draft_definition: str,
) -> dict:
    base = next((item for item in BUILTIN_PIPELINES if item["id"] == pipeline_id), None)
    if not base:
        state = _load_catalog_state()
        custom_items = state.get("custom_pipelines", [])
        idx = next((i for i, item in enumerate(custom_items) if item.get("id") == pipeline_id), None)
        if idx is None:
            raise KeyError(pipeline_id)
        current = dict(custom_items[idx])
        stage_map = dict(current.get("stage_overrides", {}))
        stage_map[stage_name] = {
            "display_name": display_name.strip(),
            "operator_notes": operator_notes.strip(),
            "draft_definition": draft_definition.rstrip(),
        }
        current["stage_overrides"] = stage_map
        custom_items[idx] = current
        state["custom_pipelines"] = custom_items
        _save_catalog_state(state)
        return stage_override(pipeline_id, stage_name)

    overrides = _load_overrides()
    pipeline_override = dict(overrides.get(pipeline_id, {}))
    stage_map = dict(pipeline_override.get("stage_overrides", {}))
    stage_map[stage_name] = {
        "display_name": display_name.strip(),
        "operator_notes": operator_notes.strip(),
        "draft_definition": draft_definition.rstrip(),
    }
    pipeline_override["stage_overrides"] = stage_map
    pipeline_override.setdefault("name", base["name"])
    pipeline_override.setdefault("repo", base["repo"])
    pipeline_override.setdefault("description", base["description"])
    pipeline_override.setdefault("notes", pipeline_override.get("notes", ""))
    pipeline_override.setdefault("stages", list(base.get("stages", [])))
    pipeline_override.setdefault("links", deepcopy(base.get("links", [])))
    pipeline_override.setdefault("dashboards", deepcopy(base.get("dashboards", [])))
    overrides[pipeline_id] = pipeline_override
    _save_overrides(overrides)
    return stage_override(pipeline_id, stage_name)


def create_custom_pipeline(payload: dict) -> dict:
    state = _load_catalog_state()
    custom_items = state.get("custom_pipelines", [])
    custom_id = payload.get("id") or f"custom-{uuid4().hex[:10]}"
    entry = {
        "id": custom_id,
        "name": payload.get("name", custom_id),
        "repo": payload.get("repo", ""),
        "workflow": payload.get("workflow", "candidate-import"),
        "description": payload.get("description", ""),
        "stages": list(payload.get("stages", [])),
        "notes": payload.get("notes", ""),
        "editable": True,
        "links": list(payload.get("links", [])),
        "dashboards": list(payload.get("dashboards", [])),
        "tags": list(payload.get("tags", [])),
        "candidate": dict(payload.get("candidate", {})),
    }
    custom_items = [item for item in custom_items if item.get("id") != custom_id]
    custom_items.append(entry)
    state["custom_pipelines"] = custom_items
    _save_catalog_state(state)
    return entry
