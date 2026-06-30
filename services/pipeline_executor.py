from __future__ import annotations

import ipaddress
import json
import re
import shlex
import socket
import tempfile
import time
from base64 import b64decode, b64encode
from pathlib import Path
from urllib.parse import quote

from services.ansible import scan_ansible_controller
from services.ansible_inventory import parse_ansible_hosts, sync_ansible_inventory_to_rules
from services.automation_pipeline import (
    append_event,
    mark_run_active,
    mark_run_complete,
)
from services.automation_runs import get_run, load_runs, update_run, update_stage
from services.docker_swarm import scan_docker_controller, sync_docker_inventory_to_rules
from services.fresh_build_library import fresh_build_plan
from services.integration_store import (
    load_integrations,
    load_proxmox_snapshot,
    save_ansible_snapshot,
    save_docker_snapshot,
)
from services.inventory_model import reconcile_rules_inventory, resolve_group_hosts
from services.proxmox import ProxmoxClient, load_proxmox_config
from services.remote_ops import (
    download_remote_file,
    run_remote_command,
    upload_remote_bytes,
    upload_remote_file,
)
from services.rules_store import load_rules, save_rules
from services.ssh_keys import read_key_pair


class PipelineExecutionError(RuntimeError):
    pass


FEDORA_TEMPLATE_RELEASE = "44"
FEDORA_SOURCE_VMIDS = {115, 131}
K3S_CLUSTER_NAME = "k3s-lab"
K3S_NODE_PLAN = [
    {"name": "kube1.lab.auzietek.com", "short": "kube1", "role": "server"},
    {"name": "kube2.lab.auzietek.com", "short": "kube2", "role": "agent"},
]
K3S_LIVE_NODES = [
    {"name": "kube1.lab.auzietek.com", "host": "192.168.1.14", "role": "server"},
    {"name": "kube2.lab.auzietek.com", "host": "192.168.1.59", "role": "agent"},
]
K3S_NFS_MOUNTS = [
    ("192.168.1.10:/srv/nfs/swarm/shared", "/mnt/swarm/shared"),
    ("192.168.1.10:/srv/nfs/swarm/tabor-linux-forge", "/mnt/swarm/tabor-linux-forge"),
    ("192.168.1.10:/srv/nfs/swarm/AuziX", "/mnt/swarm/AuziX"),
    ("192.168.1.10:/srv/nfs/swarm/blackknightcontroller", "/mnt/swarm/blackknightcontroller"),
]
LAB_STORAGE_SWARM_HOSTS = "swarm1.lab.auzietek.com swarm2.lab.auzietek.com swarm3.lab.auzietek.com"
LAB_STORAGE_K3S_HOSTS = "192.168.1.14 192.168.1.59"
LAB_STORAGE_ALL_HOSTS = f"{LAB_STORAGE_SWARM_HOSTS} {LAB_STORAGE_K3S_HOSTS}"
LAB_STORAGE_SWARM_HOST_LIST = LAB_STORAGE_SWARM_HOSTS.split()
LAB_STORAGE_K3S_HOST_LIST = LAB_STORAGE_K3S_HOSTS.split()
LAB_STORAGE_ALL_HOST_LIST = LAB_STORAGE_ALL_HOSTS.split()
LAB_STORAGE_MIN_ROOT_BYTES = 50_000_000_000
RX_DEMO_SHARED_SOURCE = "/mnt/swarm/shared/rx-demo"
RX_DEMO_RX_UI_IMAGE = "rx-demo/rx-ui:latest"
RX_DEMO_RX_UI_TAR = "/mnt/swarm/shared/rx-demo-rx-ui-latest.tar"
RX_DEMO_K3S_DEMO_TAG = "097889a"
RX_DEMO_K3S_DEMO_REPOS = [
    "rx-ui",
    "api-gateway",
    "legacy-sync-worker",
    "read-model-projection",
    "loadgen",
]
DEMO_REGISTRY_HOST = "swarm1.lab.auzietek.com"
DEMO_REGISTRY_PORT = "5001"
DEMO_REGISTRY = f"{DEMO_REGISTRY_HOST}:{DEMO_REGISTRY_PORT}"
DEMO_REGISTRY_URL = f"http://{DEMO_REGISTRY}"
DEMO_REGISTRY_SMOKE_IMAGE = f"{DEMO_REGISTRY}/rx-demo/busybox:smoke"
DEMO_K3S_SOURCE_VMID = 131
AUZIX_VM130_HOST = "192.168.1.163"
AUZIX_VM130_SOURCE_ROOT = "/srv/nfs/swarm/AuziX/src/out/auzix-strict/AuzixRoot"
AUZIX_VM134_ID = 134
AUZIX_ARTIFACT_ROOT = "/mnt/swarm/AuziX/src"
AUZIX_ARTIFACT_HOST = "192.168.1.15"
AUZIX_VM134_ISO_NAME = "auzix-strict-desktop-vm134.iso"
AUZIX_VM134_MIN_DISK_GIB = 32
AUZIX_VM135_ID = 135
AUZIX_VM135_NAME = "Auzix-VM135"
AUZIX_VM135_ISO_NAME = "auzix-strict-desktop-vm135.iso"
AUZIX_VM135_MIN_DISK_GIB = 32


def _set_stage(run_id: str, stage_name: str, status: str, detail: str) -> None:
    update_stage(run_id, stage_name, status, detail)
    level = "error" if status == "failed" else "info"
    append_event(run_id, level, stage_name, detail)


def current_active_stage_name(run_id: str) -> str:
    run = get_run(run_id)
    if not run:
        return ""
    for stage in run.get("stages", []):
        if str(stage.get("status", "")).strip().lower() == "active":
            return str(stage.get("name", "")).strip()
    return ""


def _remote_settings() -> dict[str, str]:
    integrations = load_integrations()
    ansible = integrations["ansible"]
    docker = integrations["docker"]

    controller_host = ansible.get("controller_host", "").strip()
    controller_user = ansible.get("controller_user", "").strip()
    controller_password = ansible.get("controller_password", "").strip()
    manager_host = docker.get("manager_host", "").strip()
    manager_user = docker.get("manager_user", "").strip()
    manager_password = docker.get("manager_password", "").strip()

    if not controller_host or not controller_user:
        raise PipelineExecutionError("Ansible controller settings are incomplete.")
    if not manager_host or not manager_user:
        raise PipelineExecutionError("Docker manager settings are incomplete.")

    return {
        "controller_host": controller_host,
        "controller_user": controller_user,
        "controller_password": controller_password,
        "manager_host": manager_host,
        "manager_user": manager_user,
        "manager_password": manager_password,
    }


def _lab_storage_known_hosts_prelude(hosts: str = LAB_STORAGE_ALL_HOSTS) -> str:
    return (
        "mkdir -p /root/.ssh; chmod 700 /root/.ssh; "
        f"for host in {hosts}; do "
        "ssh-keygen -R \"$host\" >/dev/null 2>&1 || true; "
        "ssh-keyscan -H \"$host\" >> /root/.ssh/known_hosts 2>/dev/null || true; "
        "done; "
        "sort -u /root/.ssh/known_hosts -o /root/.ssh/known_hosts; "
        "chmod 600 /root/.ssh/known_hosts; "
    )


def _refresh_inventory(run_id: str) -> None:
    _set_stage(run_id, "inventory-refresh", "active", "Refreshing Docker and Ansible inventory snapshots.")

    ansible_scan = scan_ansible_controller()
    save_ansible_snapshot(ansible_scan)
    parsed_ansible = parse_ansible_hosts(ansible_scan.get("inventory_content", ""))

    docker_scan = scan_docker_controller()
    save_docker_snapshot(docker_scan)

    rules = load_rules()
    ansible_result = sync_ansible_inventory_to_rules(rules, parsed_ansible)
    docker_result = sync_docker_inventory_to_rules(rules, docker_scan)
    reconcile = reconcile_rules_inventory(rules)
    save_rules(rules)

    summary = {
        "ansible": ansible_result,
        "docker": docker_result,
        "clusters": reconcile.get("clusters"),
    }
    append_event(run_id, "info", "inventory-refresh", json.dumps(summary, sort_keys=True))
    _set_stage(run_id, "inventory-refresh", "complete", "Docker and Ansible inventory refreshed.")


WORKFLOW_DEFINITIONS = {
    "fedora-template-deploy": {
        "supports_undeploy": False,
        "stage_plan": [
            {
                "name": "source-select",
                "transport": "internal",
                "kind": "fedora-template-source-select",
                "active": "Selecting the local Fedora 44 minimal Proxmox template and target defaults for the fast VM deploy lane.",
                "complete": "Local Fedora template and Proxmox target selected.",
                "timeout": 30,
            },
            {
                "name": "proxmox-import",
                "transport": "internal",
                "kind": "fedora-template-proxmox-clone",
                "active": "Cloning the local Fedora 44 minimal template in Proxmox.",
                "complete": "Fedora template cloned in Proxmox.",
                "timeout": 2400,
            },
            {
                "name": "instance-configure",
                "transport": "internal",
                "kind": "fedora-template-configure",
                "active": "Applying cloud-init, SSH key, boot order, and guest agent settings for the cloned Fedora VM.",
                "complete": "Fedora template clone configured for first boot.",
                "timeout": 240,
            },
            {
                "name": "boot",
                "transport": "internal",
                "kind": "fedora-template-start",
                "active": "Starting the cloned Fedora VM in Proxmox.",
                "complete": "Fedora template clone start requested successfully.",
                "timeout": 120,
            },
            {
                "name": "ssh-validate",
                "transport": "internal",
                "kind": "event-note",
                "active": "Publishing the next guest-validation step for chain install or SSH-driven takeover.",
                "complete": "Guest validation note published.",
                "message": "Next step: bring the cloned Fedora guest up on the intended validation network, then drive chain install or SSH-based takeover against the fresh VM.",
            },
        ],
        "complete_message": "Fedora template deploy pipeline completed.",
    },
    "fedora-cosmic-postinstall": {
        "supports_undeploy": False,
        "stage_plan": [
            {
                "name": "target-select",
                "transport": "internal",
                "kind": "cosmic-target-select",
                "active": "Selecting the freshest reachable Fedora clone for COSMIC post-install takeover.",
                "complete": "Fedora COSMIC target selected.",
                "timeout": 120,
            },
            {
                "name": "wait-ssh",
                "transport": "internal",
                "kind": "cosmic-wait-ssh",
                "active": "Waiting for BKC SSH access on the Fedora target.",
                "complete": "Fedora target is reachable over SSH.",
                "timeout": 900,
            },
            {
                "name": "package-plan",
                "transport": "internal",
                "kind": "cosmic-package-plan",
                "active": "Preparing the unattended COSMIC package plan.",
                "complete": "COSMIC package plan prepared.",
                "timeout": 60,
            },
            {
                "name": "desktop-install",
                "transport": "internal",
                "kind": "cosmic-desktop-install",
                "active": "Installing COSMIC Desktop packages on the Fedora target.",
                "complete": "COSMIC Desktop packages installed.",
                "timeout": 5400,
            },
            {
                "name": "graphical-enable",
                "transport": "internal",
                "kind": "cosmic-graphical-enable",
                "active": "Enabling graphical boot and the COSMIC display manager.",
                "complete": "Graphical boot and display manager enabled.",
                "timeout": 300,
            },
            {
                "name": "reboot",
                "transport": "internal",
                "kind": "cosmic-reboot",
                "active": "Rebooting the Fedora target once after COSMIC setup.",
                "complete": "Fedora target reboot requested.",
                "timeout": 120,
            },
            {
                "name": "gui-validate",
                "transport": "internal",
                "kind": "cosmic-gui-validate",
                "active": "Waiting for SSH return and validating graphical target/display manager.",
                "complete": "Fedora COSMIC GUI target is online.",
                "timeout": 1200,
            },
            {
                "name": "register-resource",
                "transport": "internal",
                "kind": "cosmic-register-resource",
                "active": "Recording COSMIC desktop state in BKC inventory metadata.",
                "complete": "COSMIC desktop state registered in inventory.",
                "timeout": 120,
            },
        ],
        "complete_message": "Fedora COSMIC post-install pipeline completed.",
    },
    "k3s-fedora-cluster": {
        "supports_undeploy": False,
        "stage_plan": [
            {
                "name": "source-select",
                "transport": "internal",
                "kind": "k3s-source-select",
                "active": "Selecting the Fedora 44 Proxmox source and target defaults for the k3s lab cluster.",
                "complete": "Fedora source and Proxmox target selected for k3s.",
                "timeout": 30,
            },
            {
                "name": "clone-plan",
                "transport": "internal",
                "kind": "k3s-clone-plan",
                "active": "Planning kube1 and kube2 clone roles, names, and first-boot settings.",
                "complete": "K3s clone plan prepared.",
                "timeout": 30,
            },
            {
                "name": "proxmox-clone",
                "transport": "internal",
                "kind": "k3s-proxmox-clone",
                "active": "Cloning and cloud-init configuring the Fedora guests for kube1 and kube2.",
                "complete": "K3s Fedora guests cloned and configured.",
                "timeout": 3600,
            },
            {
                "name": "boot",
                "transport": "internal",
                "kind": "k3s-proxmox-start",
                "active": "Starting kube1 and kube2 in Proxmox.",
                "complete": "K3s Fedora guests are running.",
                "timeout": 300,
            },
            {
                "name": "discover-ssh",
                "transport": "internal",
                "kind": "k3s-discover-ssh",
                "active": "Resolving kube DNS names and waiting for BKC SSH access.",
                "complete": "K3s guests are reachable over SSH.",
                "timeout": 900,
            },
            {
                "name": "base-os-bootstrap",
                "transport": "internal",
                "kind": "k3s-base-bootstrap",
                "active": "Applying Fedora base OS prerequisites for k3s.",
                "complete": "K3s base OS prerequisites applied.",
                "timeout": 1800,
            },
            {
                "name": "install-k3s-server",
                "transport": "internal",
                "kind": "k3s-install-server",
                "active": "Installing the k3s server on kube1.",
                "complete": "K3s server installed on kube1.",
                "timeout": 1200,
            },
            {
                "name": "capture-k3s-token",
                "transport": "internal",
                "kind": "k3s-capture-token",
                "active": "Capturing the kube1 join token for the worker stage.",
                "complete": "K3s join token captured for the worker stage.",
                "timeout": 120,
            },
            {
                "name": "install-k3s-agent",
                "transport": "internal",
                "kind": "k3s-install-agent",
                "active": "Joining kube2 to the k3s cluster.",
                "complete": "Kube2 joined the k3s cluster.",
                "timeout": 1200,
            },
            {
                "name": "verify-cluster",
                "transport": "internal",
                "kind": "k3s-verify-cluster",
                "active": "Verifying both k3s nodes report Ready through kubectl.",
                "complete": "K3s cluster reports both nodes Ready.",
                "timeout": 600,
            },
            {
                "name": "register-resources",
                "transport": "internal",
                "kind": "k3s-register-resources",
                "active": "Registering the k3s cluster and node resources in BKC inventory.",
                "complete": "K3s cluster resources registered.",
                "timeout": 120,
            },
        ],
        "complete_message": "K3s Fedora cluster pipeline completed.",
    },
    "demo-swarm-image-registry": {
        "supports_undeploy": False,
        "stage_plan": [
            {
                "name": "storage-ready",
                "transport": "ssh-manager",
                "target": "manager",
                "active": "Verifying registry storage on shared swarm storage.",
                "complete": "Registry storage path is ready.",
                "command": "bash -lc 'set -euo pipefail; mkdir -p /mnt/swarm/shared/registry; test -d /mnt/swarm/shared/registry; echo registry-storage-ready'",
                "timeout": 60,
            },
            {
                "name": "deploy-registry-stack",
                "transport": "ssh-manager",
                "target": "manager",
                "active": "Deploying the local image registry stack on Docker Swarm.",
                "complete": "Swarm registry service is deployed.",
                "command": "bash -lc 'set -euo pipefail; /usr/local/bin/registry-deploy; docker service ls --filter name=registry_registry'",
                "timeout": 300,
            },
            {
                "name": "registry-health",
                "transport": "ssh-manager",
                "target": "manager",
                "active": "Checking the registry HTTP API.",
                "complete": "Registry /v2/ endpoint is healthy.",
                "command": "bash -lc 'set -euo pipefail; curl -fsS http://127.0.0.1:5001/v2/; curl -fsS http://127.0.0.1:5001/v2/_catalog'",
                "timeout": 60,
            },
            {
                "name": "k3s-dns-or-ip",
                "transport": "internal",
                "kind": "demo-registry-k3s-dns",
                "active": "Verifying k3s nodes resolve the swarm registry name.",
                "complete": "K3s nodes resolve swarm1.lab.auzietek.com.",
                "timeout": 120,
            },
            {
                "name": "k3s-containerd-trust",
                "transport": "internal",
                "kind": "demo-registry-k3s-trust",
                "active": "Configuring k3s containerd registry mirror on existing nodes.",
                "complete": "Existing k3s nodes trust the local registry mirror.",
                "timeout": 300,
            },
            {
                "name": "push-smoke-image",
                "transport": "ssh-manager",
                "target": "manager",
                "active": "Pushing a smoke image through the local registry.",
                "complete": "Smoke image is present in the registry.",
                "command": "bash -lc 'set -euo pipefail; docker pull busybox:latest >/dev/null; docker tag busybox:latest 127.0.0.1:5001/rx-demo/busybox:smoke; docker push 127.0.0.1:5001/rx-demo/busybox:smoke; curl -fsS http://127.0.0.1:5001/v2/_catalog'",
                "timeout": 300,
            },
            {
                "name": "pull-smoke-image",
                "transport": "internal",
                "kind": "demo-registry-k3s-pull",
                "active": "Pulling the smoke image from k3s containerd.",
                "complete": "Existing k3s nodes pulled the smoke image.",
                "timeout": 300,
            },
        ],
        "complete_message": "Demo swarm image registry pipeline completed.",
    },
    "demo-k3s-add-node": {
        "supports_undeploy": True,
        "stage_plan": [
            {"name": "select-target", "transport": "internal", "kind": "demo-k3s-add-node-select", "active": "Selecting the requested worker target or Fedora 44 Proxmox clone source.", "complete": "Worker target selected.", "timeout": 120},
            {"name": "clone-worker", "transport": "internal", "kind": "demo-k3s-add-node-clone", "active": "Cloning a Fedora 44 worker VM from Proxmox when no existing target host was supplied.", "complete": "Worker VM clone is ready.", "timeout": 3600},
            {"name": "boot-worker", "transport": "internal", "kind": "demo-k3s-add-node-boot", "active": "Starting the worker VM in Proxmox.", "complete": "Worker VM is running.", "timeout": 300},
            {"name": "discover-ssh", "transport": "internal", "kind": "demo-k3s-add-node-discover", "active": "Watching the cloned worker come online through Proxmox neighbor discovery before SSH takeover.", "complete": "Worker SSH is reachable.", "timeout": 900},
            {"name": "base-os-prep", "transport": "internal", "kind": "demo-k3s-add-node-base", "active": "Applying k3s worker OS prerequisites.", "complete": "Target OS prerequisites are ready.", "timeout": 1800},
            {"name": "capture-join-token", "transport": "internal", "kind": "demo-k3s-add-node-token", "active": "Capturing the kube1 k3s join token.", "complete": "Join token captured.", "timeout": 120},
            {"name": "install-k3s-agent", "transport": "internal", "kind": "demo-k3s-add-node-agent", "active": "Installing k3s-agent on the target worker.", "complete": "Target worker joined the k3s cluster.", "timeout": 1200},
            {"name": "verify-node-ready", "transport": "internal", "kind": "demo-k3s-add-node-verify", "active": "Waiting for the new worker to report Ready.", "complete": "New worker reports Ready.", "timeout": 600},
            {"name": "extend-telemetry", "transport": "internal", "kind": "demo-k3s-add-node-registry", "active": "Applying registry mirror settings to the new worker.", "complete": "New worker registry mirror is configured.", "timeout": 300},
            {"name": "register-inventory", "transport": "internal", "kind": "demo-k3s-add-node-register", "active": "Recording the new worker in BKC run metadata.", "complete": "New worker recorded in run metadata.", "timeout": 60},
        ],
        "undeploy_stage_plan": [
            {"name": "select-worker", "transport": "internal", "kind": "demo-k3s-add-node-reset-select", "active": "Selecting the demo worker to reset.", "complete": "Demo worker reset target selected.", "timeout": 60},
            {"name": "delete-k3s-node", "transport": "internal", "kind": "demo-k3s-add-node-reset-k3s", "active": "Draining and deleting the demo worker from k3s.", "complete": "Demo worker removed from k3s.", "timeout": 600},
            {"name": "destroy-worker-vm", "transport": "internal", "kind": "demo-k3s-add-node-reset-vm", "active": "Stopping and destroying the cloned Proxmox worker VM.", "complete": "Demo worker VM destroyed or confirmed absent.", "timeout": 600},
            {"name": "verify-reset", "transport": "internal", "kind": "demo-k3s-add-node-reset-verify", "active": "Verifying the demo worker is absent from k3s and Proxmox.", "complete": "Demo worker reset verified.", "timeout": 180},
        ],
        "complete_message": "Demo k3s add-node pipeline completed.",
    },
    "rx-demo-k3s-registry-preflight": {
        "supports_undeploy": False,
        "stage_plan": [
            {
                "name": "registry-reachable",
                "transport": "ssh-manager",
                "target": "manager",
                "active": "Verifying the local registry API is reachable before publishing rx-demo images.",
                "complete": "Registry API is reachable.",
                "command": "bash -lc 'set -euo pipefail; curl -fsS http://127.0.0.1:5001/v2/; curl -fsS http://127.0.0.1:5001/v2/_catalog'",
                "timeout": 60,
            },
            {
                "name": "k3s-registry-trust",
                "transport": "internal",
                "kind": "demo-registry-k3s-trust",
                "active": "Verifying k3s containerd trusts the local registry mirror.",
                "complete": "K3s registry mirror trust is configured.",
                "timeout": 300,
            },
            {
                "name": "build-and-push",
                "transport": "internal",
                "kind": "rx-demo-registry-preflight-build-push",
                "active": "Building and pushing an rx-demo preflight image tag.",
                "complete": "Rx-demo preflight image pushed to the local registry.",
                "timeout": 420,
            },
            {
                "name": "registry-catalog",
                "transport": "internal",
                "kind": "rx-demo-registry-preflight-catalog",
                "active": "Checking the registry catalog and tag list for the rx-demo preflight image.",
                "complete": "Registry catalog includes the rx-demo preflight image tag.",
                "timeout": 60,
            },
        ],
        "complete_message": "Rx-demo k3s registry preflight pipeline completed.",
    },
    "rx-demo-k3s-deploy": {
        "supports_undeploy": False,
        "stage_plan": [
            {
                "name": "k3s-ready",
                "transport": "bkc-ssh",
                "kind": "rx-demo-k3s-ready",
                "action": "k3s.nodes.ready",
                "active": "Verifying kube1 can read the k3s cluster and all nodes are Ready.",
                "complete": "K3s node readiness verified.",
                "timeout": 120,
            },
            {
                "name": "runtime-secrets",
                "transport": "bkc-ssh",
                "kind": "rx-demo-k3s-secrets",
                "action": "kubernetes.secret.ensure",
                "active": "Verifying rx-demo runtime secrets exist before deployment.",
                "complete": "Rx-demo runtime secrets are present.",
                "timeout": 60,
            },
            {
                "name": "registry-images",
                "transport": "ssh-manager",
                "kind": "rx-demo-k3s-registry-images",
                "action": "docker.image.build_push",
                "active": "Building and pushing rx-demo images to the swarm-hosted registry.",
                "complete": "Rx-demo registry images are present.",
                "timeout": 2400,
            },
            {
                "name": "apply-k3s-demo-overlay",
                "transport": "bkc-ssh",
                "kind": "rx-demo-k3s-apply-demo-overlay",
                "action": "kubectl.apply",
                "active": "Applying the rx-demo k3s-demo overlay.",
                "complete": "Rx-demo k3s-demo overlay applied.",
                "timeout": 300,
            },
            {
                "name": "rollout-app",
                "transport": "bkc-ssh",
                "kind": "rx-demo-k3s-rollout-app",
                "action": "kubectl.rollout_status",
                "active": "Waiting for rx-demo application workloads to become ready.",
                "complete": "Rx-demo application workloads are ready.",
                "timeout": 900,
            },
            {
                "name": "rollout-observability",
                "transport": "bkc-ssh",
                "kind": "rx-demo-k3s-rollout-observability",
                "action": "kubectl.rollout_status",
                "active": "Waiting for Grafana, Prometheus, Loki, and Tempo to become ready.",
                "complete": "Rx-demo observability workloads are ready.",
                "timeout": 900,
            },
            {
                "name": "smoke-api",
                "transport": "bkc-ssh",
                "kind": "rx-demo-k3s-smoke-api-full",
                "action": "http.smoke",
                "active": "Smoking rx-demo API routes through the k3s NodePort.",
                "complete": "Rx-demo API smoke checks passed.",
                "timeout": 180,
            },
            {
                "name": "smoke-ui",
                "transport": "bkc-ssh",
                "kind": "rx-demo-k3s-smoke-ui-full",
                "action": "http.smoke",
                "active": "Smoking rx-demo UI routes through the k3s NodePort.",
                "complete": "Rx-demo UI smoke checks passed.",
                "timeout": 180,
            },
            {
                "name": "telemetry-check",
                "transport": "bkc-ssh",
                "kind": "rx-demo-k3s-telemetry-check",
                "action": "prometheus.metrics.check",
                "active": "Checking rx-demo metrics and Grafana routing.",
                "complete": "Rx-demo telemetry endpoints responded.",
                "timeout": 180,
            },
            {
                "name": "access-links",
                "transport": "internal",
                "kind": "rx-demo-k3s-access-links",
                "active": "Publishing demo access links.",
                "complete": "Demo access links published.",
                "timeout": 30,
            },
        ],
        "complete_message": "Rx-demo k3s deploy pipeline completed.",
    },
    "rx-demo-k3s-undeploy": {
        "supports_undeploy": False,
        "stage_plan": [
            {
                "name": "capture-state",
                "transport": "bkc-ssh",
                "kind": "rx-demo-k3s-undeploy-capture",
                "action": "kubectl.get",
                "active": "Capturing rx-demo and demo observability state before cleanup.",
                "complete": "Pre-cleanup k3s state captured.",
                "timeout": 120,
            },
            {
                "name": "delete-overlay",
                "transport": "bkc-ssh",
                "kind": "rx-demo-k3s-undeploy-demo-observability",
                "action": "kubectl.delete",
                "active": "Removing demo-owned Grafana, Prometheus, Loki, and Tempo resources while preserving host telemetry.",
                "complete": "Demo observability resources removed.",
                "timeout": 300,
            },
            {
                "name": "delete-namespace",
                "transport": "bkc-ssh",
                "kind": "rx-demo-k3s-undeploy-namespace",
                "action": "kubectl.delete",
                "active": "Removing the rx-demo application namespace.",
                "complete": "Rx-demo namespace removed.",
                "timeout": 300,
            },
            {
                "name": "verify-removed",
                "transport": "bkc-ssh",
                "kind": "rx-demo-k3s-undeploy-verify",
                "action": "kubectl.wait_absent",
                "active": "Verifying rx-demo is absent and host telemetry remains.",
                "complete": "Cleanup verification passed.",
                "timeout": 120,
            },
            {
                "name": "registry-retained",
                "transport": "ssh-manager",
                "kind": "rx-demo-k3s-undeploy-registry",
                "action": "registry.v2.catalog",
                "active": "Confirming the local registry remains available for another demo take.",
                "complete": "Local registry remains available.",
                "timeout": 60,
            },
        ],
        "complete_message": "Rx-demo k3s cleanup pipeline completed.",
    },
    "rx-demo-redeploy-from-git-event": {
        "supports_undeploy": False,
        "stage_plan": [
            {
                "name": "git-event",
                "transport": "internal",
                "kind": "rx-demo-k3s-git-event",
                "action": "git.event.record",
                "active": "Recording the Git trigger inputs for the rx-demo redeploy.",
                "complete": "Git trigger inputs recorded.",
                "timeout": 30,
            },
            {
                "name": "sync-source-from-git",
                "transport": "ssh-manager",
                "kind": "rx-demo-k3s-sync-source-from-git",
                "action": "git.checkout",
                "active": "Updating the shared rx-demo working copy from Git.",
                "complete": "Shared rx-demo source is on the requested Git revision.",
                "timeout": 300,
            },
            {
                "name": "build-and-push",
                "transport": "ssh-manager",
                "kind": "rx-demo-k3s-redeploy-build-push",
                "action": "docker.image.build_push",
                "active": "Building and pushing commit-tagged rx-demo images.",
                "complete": "Commit-tagged rx-demo images are present in the local registry.",
                "timeout": 2400,
            },
            {
                "name": "update-images",
                "transport": "bkc-ssh",
                "kind": "rx-demo-k3s-redeploy-update-images",
                "action": "kubectl.set_image",
                "active": "Pointing k3s deployments at the commit-tagged images.",
                "complete": "K3s deployments reference the commit-tagged images.",
                "timeout": 240,
            },
            {
                "name": "rollout-app",
                "transport": "bkc-ssh",
                "kind": "rx-demo-k3s-rollout-app",
                "action": "kubectl.rollout_status",
                "active": "Waiting for the redeployed rx-demo workloads.",
                "complete": "Redeployed rx-demo workloads are ready.",
                "timeout": 900,
            },
            {
                "name": "cloudinit-node-check",
                "transport": "bkc-ssh",
                "kind": "rx-demo-k3s-cloudinit-node-check",
                "action": "k3s.node.provenance",
                "active": "Capturing k3s node and cloud-init provenance evidence.",
                "complete": "K3s node and cloud-init evidence captured.",
                "timeout": 180,
            },
            {
                "name": "visible-change-check",
                "transport": "bkc-ssh",
                "kind": "rx-demo-k3s-smoke-ui-full",
                "action": "http.content_check",
                "active": "Generating UI/API activity after the redeploy.",
                "complete": "Post-redeploy UI/API smoke activity completed.",
                "timeout": 180,
            },
            {
                "name": "telemetry-still-flowing",
                "transport": "bkc-ssh",
                "kind": "rx-demo-k3s-telemetry-check",
                "action": "prometheus.metrics.check",
                "active": "Checking telemetry endpoints after the redeploy.",
                "complete": "Telemetry endpoints responded after the redeploy.",
                "timeout": 180,
            },
            {
                "name": "loki-cloudevents-check",
                "transport": "bkc-ssh",
                "kind": "rx-demo-k3s-loki-cloudevents-check",
                "action": "loki.stream.verify",
                "active": "Querying Loki for CloudEvents audit records.",
                "complete": "Loki returned CloudEvents audit records.",
                "timeout": 300,
            },
            {
                "name": "grafana-loki-check",
                "transport": "bkc-ssh",
                "kind": "rx-demo-k3s-grafana-loki-check",
                "action": "grafana.datasource.check",
                "active": "Checking Grafana and publishing the Loki Explore query.",
                "complete": "Grafana is reachable and the Loki query is ready for the demo.",
                "timeout": 120,
            },
            {
                "name": "access-links",
                "transport": "internal",
                "kind": "rx-demo-k3s-access-links",
                "active": "Publishing demo access links.",
                "complete": "Demo access links published.",
                "timeout": 30,
            },
        ],
        "complete_message": "Rx-demo k3s Git redeploy pipeline completed.",
    },
    "fedora-workstation-spin": {
        "supports_undeploy": False,
        "stage_plan": [
            {
                "name": "repo-sync",
                "transport": "ssh-controller",
                "target": "controller",
                "active": "Checking for the staged BlackKnightController source tree on ns1 so the Fedora build kit can be rendered from the current repo state.",
                "complete": "Staged BKC source is present on ns1.",
                "command": (
                    "bash -lc '"
                    "test -d /srv/nfs/swarm/blackknightcontroller/src/.git -o -f /srv/nfs/swarm/blackknightcontroller/src/Readme.md "
                    "&& echo bkc-source-ready'"
                ),
                "timeout": 45,
            },
            {
                "name": "manifest-resolve",
                "transport": "internal",
                "kind": "fedora-build-kit",
                "active": "Generating the Fedora workstation build plan, kickstart, and package manifest for the thin MATE/Enlightenment workstation profile.",
                "complete": "Fedora workstation build kit staged on shared storage.",
                "timeout": 180,
            },
            {
                "name": "image-compose",
                "transport": "ssh-controller",
                "target": "controller",
                "active": "Summarizing the staged Fedora workstation kit for later compose and Proxmox handoff work.",
                "complete": "Fedora workstation build kit summary rendered.",
                "command": (
                    "bash -lc '"
                    "cd /srv/nfs/swarm/auzix-fedora-workstation/artifacts && "
                    "python3 - <<\"PY\"\n"
                    "import json\n"
                    "from pathlib import Path\n"
                    "plan=json.loads(Path(\"auzix-fedora-workstation-plan.json\").read_text())\n"
                    "summary={\n"
                    "  \"hostname\": plan.get(\"hostname\"),\n"
                    "  \"release\": plan.get(\"release\"),\n"
                    "  \"kickstart\": plan.get(\"kickstart_filename\"),\n"
                    "  \"boot_args\": plan.get(\"boot_args\"),\n"
                    "  \"package_manifest\": \"auzix-fedora-workstation-packages.json\"\n"
                    "}\n"
                    "Path(\"README.build.txt\").write_text(json.dumps(summary, indent=2)+\"\\n\")\n"
                    "print(json.dumps(summary, indent=2))\n"
                    "PY'"
                ),
                "timeout": 90,
            },
            {
                "name": "artifact-publish",
                "transport": "ssh-controller",
                "target": "controller",
                "active": "Verifying that the Fedora workstation build kit exists on shared storage.",
                "complete": "Fedora workstation build kit artifacts are present on shared storage.",
                "command": (
                    "bash -lc '"
                    "cd /srv/nfs/swarm/auzix-fedora-workstation/artifacts && "
                    "test -f auzix-fedora-workstation-plan.json && "
                    "test -f auzix-fedora-workstation.ks && "
                    "test -f auzix-fedora-workstation-packages.json && "
                    "ls -1'"
                ),
                "timeout": 60,
            },
        ],
        "complete_message": "Fedora workstation build kit pipeline completed.",
    },
    "wordpress-appliance-import": {
        "supports_undeploy": False,
        "stage_plan": [
            {
                "name": "source-select",
                "transport": "internal",
                "kind": "wordpress-source-select",
                "active": "Selecting a discovered WordPress-capable Proxmox template from the catalog.",
                "complete": "Selected a Proxmox template for the WordPress appliance lane.",
                "timeout": 30,
            },
            {
                "name": "proxmox-clone",
                "transport": "internal",
                "kind": "wordpress-proxmox-clone",
                "active": "Cloning the selected template through the Proxmox API.",
                "complete": "Proxmox clone completed for the WordPress appliance lane.",
                "timeout": 360,
            },
            {
                "name": "boot",
                "transport": "internal",
                "kind": "wordpress-proxmox-start",
                "active": "Starting the cloned WordPress appliance VM in Proxmox.",
                "complete": "WordPress appliance VM start requested successfully.",
                "timeout": 120,
            },
            {
                "name": "ssh-validate",
                "transport": "internal",
                "kind": "event-note",
                "active": "Publishing the next validation step for guest reachability and application checks.",
                "complete": "Guest validation note published.",
                "message": "Next step: validate the cloned appliance over SSH once the private validation network and DHCP/lease discovery path are in place.",
            },
        ],
        "complete_message": "WordPress appliance import pipeline completed.",
    },
    "lab-demo": {
        "supports_undeploy": False,
        "stage_plan": [
            {
                "name": "repo-sync",
                "transport": "ssh-manager",
                "target": "manager",
                "active": "Checking for the staged tabor-linux-forge source tree on the swarm builder host.",
                "complete": "Staged tabor-linux-forge source is present on the builder host.",
                "command": (
                    "bash -lc '"
                    "test -f /srv/stacks/tabor-linux-forge/docker-compose.yml && "
                    "test -f /mnt/swarm/tabor-linux-forge/src/Readme.md && "
                    "test -x /mnt/swarm/tabor-linux-forge/src/scripts/scaffold-auzix-strict-root.sh && "
                    "test -x /mnt/swarm/tabor-linux-forge/src/scripts/audit-auzix-strict-root.sh && "
                    "echo auzix-lab-source-ready'"
                ),
                "timeout": 45,
            },
            {
                "name": "builder-ready",
                "transport": "ssh-manager",
                "target": "manager",
                "active": "Checking the existing tabor-linux-forge container substrate.",
                "complete": "The tabor-linux-forge builder container is ready.",
                "command": (
                    "bash -lc '"
                    "cd /srv/stacks/tabor-linux-forge && "
                    "docker image inspect tabor-linux-forge-kernel --format \"{{.Id}} {{.Created}}\" || "
                    "{ echo tabor-linux-forge-kernel image missing; "
                    "docker compose -f /srv/stacks/tabor-linux-forge/docker-compose.yml build kernel-builder; } && "
                    "docker image inspect tabor-linux-forge-kernel --format \"{{.Id}} {{.Created}}\"'"
                ),
                "timeout": 60,
            },
            {
                "name": "strict-root-scaffold",
                "transport": "ssh-manager",
                "target": "manager",
                "active": "Creating the staged AuzixRoot strict filesystem skeleton inside the builder lane.",
                "complete": "AuzixRoot strict filesystem skeleton created.",
                "command": (
                    "bash -lc '"
                    "cd /srv/stacks/tabor-linux-forge && "
                    "/usr/local/bin/tabor-build ./scripts/scaffold-auzix-strict-root.sh && "
                    "find /mnt/swarm/tabor-linux-forge/src/out/auzix-strict/AuzixRoot -maxdepth 2 -type d | sort | head -80'"
                ),
                "timeout": 240,
            },
            {
                "name": "sample-payload-build",
                "transport": "ssh-manager",
                "target": "manager",
                "active": "Compiling the first native AuzixProbe package into /Programs.",
                "complete": "AuzixProbe package installed under /Programs with a compatibility export.",
                "command": (
                    "bash -lc '"
                    "cd /srv/stacks/tabor-linux-forge && "
                    "/usr/local/bin/tabor-build ./scripts/build-auzix-probe-package.sh && "
                    "/mnt/swarm/tabor-linux-forge/src/out/auzix-strict/AuzixRoot/Programs/AuzixProbe/0.1/Commands/auzix-probe && "
                    "find /mnt/swarm/tabor-linux-forge/src/out/auzix-strict/AuzixRoot/Programs/AuzixProbe -maxdepth 3 -type f -o -type l'"
                ),
                "timeout": 240,
            },
            {
                "name": "busybox-package-build",
                "transport": "ssh-manager",
                "target": "manager",
                "active": "Building static BusyBox as the first shell-capable Auzix package.",
                "complete": "BusyBox installed under /Programs with /bin compatibility applets.",
                "command": (
                    "bash -lc '"
                    "cd /srv/stacks/tabor-linux-forge && "
                    "/usr/local/bin/tabor-build ./scripts/build-auzix-busybox-package.sh && "
                    "bb=/mnt/swarm/tabor-linux-forge/src/out/auzix-strict/AuzixRoot/Programs/BusyBox/1.36.1/Commands/busybox && "
                    "\"$bb\" sh -c \"echo busybox-shell-ok\" && "
                    "\"$bb\" readlink /mnt/swarm/tabor-linux-forge/src/out/auzix-strict/AuzixRoot/bin && "
                    "\"$bb\" readlink /mnt/swarm/tabor-linux-forge/src/out/auzix-strict/AuzixRoot/System/Compatibility/bin/sh'"
                ),
                "timeout": 1800,
            },
            {
                "name": "strict-root-audit",
                "transport": "ssh-manager",
                "target": "manager",
                "active": "Auditing native top-level directories, compatibility links, and legacy path strays.",
                "complete": "AuzixRoot strict filesystem audit passed.",
                "command": (
                    "bash -lc '"
                    "cd /srv/stacks/tabor-linux-forge && "
                    "/usr/local/bin/tabor-build ./scripts/audit-auzix-strict-root.sh && "
                    "tail -80 /mnt/swarm/tabor-linux-forge/src/out/auzix-strict/audit-report.txt'"
                ),
                "timeout": 240,
            },
            {
                "name": "strict-container-build",
                "transport": "ssh-manager",
                "target": "manager",
                "active": "Importing the staged AuzixRoot as a tiny shellable container image.",
                "complete": "auzix-strict:local container image is ready for shell inspection.",
                "command": (
                    "bash -lc '"
                    "cd /mnt/swarm/tabor-linux-forge/src && "
                    "./scripts/build-auzix-strict-container.sh && "
                    "docker run --rm auzix-strict:local /Programs/BusyBox/1.36.1/Commands/busybox sh -c \"pwd; ls -1 / | head -8; /Programs/AuzixProbe/0.1/Commands/auzix-probe\"'"
                ),
                "timeout": 300,
            },
            {
                "name": "legacy-prune-test",
                "transport": "ssh-manager",
                "target": "manager",
                "active": "Removing top-level legacy links and proving native /Programs paths still run.",
                "complete": "Pruned AuzixRoot container runs without top-level /bin, /usr, /lib, /var, or /etc links.",
                "command": (
                    "bash -lc '"
                    "cd /mnt/swarm/tabor-linux-forge/src && "
                    "./scripts/test-auzix-pruned-root.sh && "
                    "docker run --rm auzix-strict:pruned /Programs/BusyBox/1.36.1/Commands/busybox sh -c \"ls -1 / | head -8; test ! -e /bin; test ! -e /usr; test ! -e /lib; /Programs/AuzixProbe/0.1/Commands/auzix-probe\"'"
                ),
                "timeout": 300,
            },
            {
                "name": "artifact-publish",
                "transport": "ssh-manager",
                "target": "manager",
                "active": "Publishing the strict-root audit report location for operators.",
                "complete": "Strict-root audit report is available on the shared tabor forge workspace.",
                "command": (
                    "bash -lc '"
                    "report=/mnt/swarm/tabor-linux-forge/src/out/auzix-strict/audit-report.txt && "
                    "test -s \"$report\" && "
                    "printf \"strict-root-report=%s\\n\" \"$report\" && "
                    "grep -F \"Auzix strict root audit: PASS\" \"$report\"'"
                ),
                "timeout": 60,
            },
            {
                "name": "dashboard-link",
                "transport": "internal",
                "kind": "event-note",
                "active": "Publishing the next Auzix lab demo handoff.",
                "complete": "Auzix lab demo handoff published.",
                "message": "Strict-root contract passed in the tabor builder substrate. Next leg can add a tiny payload recipe, then graduate to a VM image only after ldd/readelf checks stay clean.",
            },
        ],
        "complete_message": "Auzix lab demo pipeline completed.",
        "runtime_snapshot": {
            "kind": "container-prefix",
            "container_name_prefix": "tabor-linux-forge-kernel-builder-run",
            "display_name": "auzix-strict-root-builder",
        },
    },
    "tabor-build": {
        "supports_undeploy": False,
        "stage_plan": [
            {
                "name": "repo-sync",
                "transport": "ssh-controller",
                "target": "controller",
                "active": "Checking for the staged AuziX source tree on ns1.",
                "complete": "Staged AuziX source is present on ns1.",
                "command": (
                    "bash -lc '"
                    "test -f /srv/nfs/swarm/AuziX/src/README.md && "
                    "test -x /srv/nfs/swarm/AuziX/src/scripts/build-auzix-strict-all.sh && "
                    "echo auzix-source-ready'"
                ),
                "timeout": 45,
            },
            {
                "name": "builder-prepare",
                "transport": "ssh-controller",
                "target": "controller",
                "active": "Preparing the AuziX NFS workspace for the swarm builder.",
                "complete": "AuziX NFS workspace is ready for the swarm builder.",
                "command": (
                    "bash -lc '"
                    "mkdir -p /srv/nfs/swarm/AuziX/src /srv/nfs/swarm/AuziX/artifacts && "
                    "test -f /srv/nfs/swarm/AuziX/src/compose.yaml && "
                    "test -f /srv/nfs/swarm/AuziX/src/docker/builder/Dockerfile && "
                    "echo auzix-workspace-ready'"
                ),
                "timeout": 240,
            },
            {
                "name": "image-build",
                "transport": "ssh-manager",
                "target": "manager",
                "active": "Building the current AuziX artifact set on swarm1 through the staged builder container.",
                "complete": "AuziX builder finished on swarm1.",
                "command": (
                    "bash -lc '"
                    "mkdir -p /mnt/swarm/AuziX && "
                    "{ mountpoint -q /mnt/swarm/AuziX || mount -t nfs 192.168.1.10:/srv/nfs/swarm/AuziX /mnt/swarm/AuziX; } && "
                    "cd /mnt/swarm/AuziX/src && "
                    "docker compose build builder && "
                    "docker compose run --rm builder'"
                ),
                "timeout": 5400,
            },
            {
                "name": "artifact-publish",
                "transport": "ssh-manager",
                "target": "manager",
                "active": "Verifying that AuziX build artifacts landed on shared storage.",
                "complete": "Artifacts are present on the NFS-backed build share.",
                "command": (
                    "bash -lc '"
                    "first=$(find /mnt/swarm/AuziX/src/artifacts /mnt/swarm/AuziX/artifacts -maxdepth 3 -type f 2>/dev/null | head -n 1); "
                    "test -n \"$first\" || { echo no-auzix-artifacts; exit 1; }; "
                    "find /mnt/swarm/AuziX/src/artifacts /mnt/swarm/AuziX/artifacts -maxdepth 3 -type f 2>/dev/null | head -n 12'"
                ),
                "timeout": 60,
            },
        ],
        "dashboard_message": "AuziX build artifacts should now exist under /srv/nfs/swarm/AuziX on ns1 and the matching NFS mount on the swarm hosts. The later hypervisor handoff will consume the produced boot media and VM image outputs.",
        "complete_message": "AuziX image build pipeline completed.",
        "runtime_snapshot": {
            "kind": "container-prefix",
            "container_name_prefix": "tabor-linux-forge-kernel-builder-run",
            "display_name": "kernel-builder-run",
        },
    },
    "auzix-vm130-deploy": {
        "supports_undeploy": False,
        "stage_plan": [
            {
                "name": "source-verify",
                "transport": "ssh-controller",
                "target": "controller",
                "active": "Verifying the generated AuziX runtime payload on the shared controller workspace.",
                "complete": "Generated AuziX runtime payload is ready for VM130.",
                "command": (
                    "bash -lc '"
                    f"test -x {AUZIX_VM130_SOURCE_ROOT}/System/Boot/StartSequence && "
                    f"test -s {AUZIX_VM130_SOURCE_ROOT}/System/Settings/mdev.conf && "
                    f"test -x {AUZIX_VM130_SOURCE_ROOT}/Programs/Midori/11.8/Commands/midori && "
                    f"test -s {AUZIX_VM130_SOURCE_ROOT}/Programs/Midori/11.8/Resources/midori/libnssckbi.so && "
                    "test -s /srv/nfs/swarm/AuziX/src/.auzix-commit && "
                    "cat /srv/nfs/swarm/AuziX/src/.auzix-commit && "
                    "echo auzix-vm130-source-ready'"
                ),
                "timeout": 60,
            },
            {
                "name": "runtime-deploy",
                "transport": "bkc-ssh",
                "target": "vmid130",
                "kind": "auzix-vm130-deploy",
                "active": "Deploying startup permission repair and the Midori runtime wrapper to VM130.",
                "complete": "AuziX runtime payload deployed to VM130 with backups and a commit marker.",
                "timeout": 180,
            },
            {
                "name": "network-validate",
                "transport": "bkc-ssh",
                "target": "vmid130",
                "kind": "auzix-vm130-validate",
                "active": "Validating VM130 user-state ownership, DNS, HTTPS, and Midori runtime settings.",
                "complete": "VM130 browser networking and permissions contract passed.",
                "timeout": 120,
            },
        ],
        "complete_message": "AuziX VM130 deployment pipeline completed.",
    },
    "auzix-vm134-install-refresh": {
        "supports_undeploy": False,
        "stage_plan": [
            {
                "name": "source-verify",
                "transport": "ssh-controller",
                "target": "controller",
                "active": "Verifying AuziX source has installer, package, GRUB, X11 Enlightenment, and ISO build contracts.",
                "complete": "AuziX source contracts for VM134 install refresh are present.",
                "command": (
                    "bash -lc 'cd /srv/nfs/swarm/AuziX/src && "
                    "grep -Fx e182842 .auzix-commit >/dev/null && "
                    "test -x scripts/add-auzix-live-tools.sh && "
                    "test -x scripts/build-auzix-installer-package.sh && "
                    "test -x scripts/build-auzix-grub-package.sh && "
                    "test -x scripts/build-auzix-boot-iso.sh && "
                    "test -x scripts/test-auzix-installer.sh && "
                    "grep -F \"install_grub_bootloader\" scripts/add-auzix-live-tools.sh >/dev/null && "
                    "grep -F \"/Programs/Enlightenment/current/Commands/enlightenment_start\" scripts/add-auzix-live-tools.sh >/dev/null && "
                    "grep -F \"/Programs/Xorg/current/Commands/Xorg\" scripts/add-auzix-live-tools.sh >/dev/null && "
                    "grep -F \"auzix-strict-grub:\" Makefile >/dev/null && "
                    "grep -F \"auzix-strict-host-xorg:\" Makefile >/dev/null && "
                    "grep -F \"auzix-strict-host-e:\" Makefile >/dev/null && "
                    "grep -F \"auzix-install-disk\" installer/auzix-installer.lua >/dev/null && "
                    "echo auzix-vm134-source-ready'"
                ),
                "timeout": 60,
            },
            {
                "name": "installer-root-build",
                "transport": "ssh-manager",
                "target": "manager",
                "active": "Refreshing the staged strict root with live tools, Lua installer, package tools, GRUB, and the X11 Enlightenment substrate.",
                "complete": "Staged strict root contains installer, finalizer, package tools, GRUB, Xorg, and Enlightenment.",
                "command": (
                    "bash -lc 'set -e; "
                    "scratch=/var/tmp/auzix-vm134-build; "
                    "mkdir -p /mnt/swarm/AuziX && "
                    "{ mountpoint -q /mnt/swarm/AuziX || mount -t nfs "
                    "192.168.1.10:/srv/nfs/swarm/AuziX /mnt/swarm/AuziX; } && "
                    "rm -rf \"$scratch\" && mkdir -p \"$scratch\" && "
                    "rsync -a --delete --exclude out/ --exclude artifacts/ "
                    "/mnt/swarm/AuziX/src/ \"$scratch\"/ && "
                    "{ docker image inspect auzix/builder:local >/dev/null 2>&1 || "
                    "docker build --pull=false -f \"$scratch/docker/builder/Dockerfile\" "
                    "-t auzix/builder:local \"$scratch\"; } && "
                    "docker run --rm -v \"$scratch\":/workspace -w /workspace "
                    "auzix/builder:local bash -lc "
                    "'\"'\"'apt-get update >/dev/null && "
                    "apt-get install -y --no-install-recommends "
                    "grub2-common grub-pc-bin "
                    "xinit xserver-xorg-core xserver-xorg-legacy "
                    "xserver-xorg-input-libinput xserver-xorg-video-fbdev "
                    "xserver-xorg-video-vesa enlightenment terminology "
                    "lightdm lightdm-gtk-greeter dbus dbus-x11 udev acpid "
                    "pulseaudio strace xterm >/dev/null && "
                    "make auzix-strict-root "
                    "auzix-strict-busybox "
                    "auzix-strict-access "
                    "auzix-strict-live-tools "
                    "auzix-strict-package-tools "
                    "auzix-strict-installer "
                    "auzix-strict-installer-test "
                    "auzix-strict-dbus "
                    "auzix-strict-udev "
                    "auzix-strict-acpid "
                    "auzix-strict-pulseaudio "
                    "auzix-strict-strace "
                    "auzix-strict-host-xorg "
                    "auzix-strict-host-e "
                    "auzix-strict-host-terminology "
                    "auzix-strict-host-xterm "
                    "auzix-strict-lightdm "
                    "auzix-strict-display-templates "
                    "auzix-strict-user-defaults "
                    "auzix-strict-grub && "
                    "test -x out/auzix-strict/AuzixRoot/System/Tools/auzix-install-disk && "
                    "test -x out/auzix-strict/AuzixRoot/System/Tools/finalize-installed-root && "
                    "test -L out/auzix-strict/AuzixRoot/System/Tools/auzix-installer-gui && "
                    "grep -F \"auzix:x:1000:1000:\" out/auzix-strict/AuzixRoot/System/Settings/passwd >/dev/null && "
                    "grep -F \"auzix:x:1000:\" out/auzix-strict/AuzixRoot/System/Settings/group >/dev/null && "
                    "test -d out/auzix-strict/AuzixRoot/Users/auzix && "
                    "test -s out/auzix-strict/AuzixRoot/System/Settings/installer/questions.json && "
                    "test -s out/auzix-strict/AuzixRoot/System/Settings/installer/plans/default.json && "
                    "test -s out/auzix-strict/AuzixRoot/Users/auzix/.config/autostart/auzix-installer.desktop && "
                    "grep -F \"/System/Tools/launch-auzix-installer --autostart\" "
                    "out/auzix-strict/AuzixRoot/Users/auzix/.config/autostart/auzix-installer.desktop >/dev/null && "
                    "test -L out/auzix-strict/AuzixRoot/System/Tools/launch-auzix-installer && "
                    "test -s out/auzix-strict/AuzixRoot/System/Settings/display/defaults/user-defaults-note.txt && "
                    "test -L out/auzix-strict/AuzixRoot/System/Compatibility/usr/sbin/grub-install && "
                    "test -L out/auzix-strict/AuzixRoot/System/Compatibility/usr/lib/grub/i386-pc && "
                    "test -L out/auzix-strict/AuzixRoot/Programs/Xorg/current && "
                    "xorg_current=$(readlink out/auzix-strict/AuzixRoot/Programs/Xorg/current) && "
                    "test -x \"out/auzix-strict/AuzixRoot${xorg_current}/Commands/Xorg\" && "
                    "test -x \"out/auzix-strict/AuzixRoot${xorg_current}/Commands/xinit\" && "
                    "test -L out/auzix-strict/AuzixRoot/Programs/Enlightenment/current && "
                    "e_current=$(readlink out/auzix-strict/AuzixRoot/Programs/Enlightenment/current) && "
                    "test -x \"out/auzix-strict/AuzixRoot${e_current}/Commands/enlightenment_start\" && "
                    "test -L out/auzix-strict/AuzixRoot/System/Compatibility/bin/enlightenment_start && "
                    "test -L out/auzix-strict/AuzixRoot/System/Compatibility/bin/Xorg && "
                    "grub_current=$(readlink out/auzix-strict/AuzixRoot/Programs/GRUB/current) && "
                    "test -n \"$grub_current\" && "
                    "test -d \"out/auzix-strict/AuzixRoot${grub_current}/Resources/i386-pc\" && "
                    "find out/auzix-strict/AuzixRoot/System/PackageDB -maxdepth 1 "
                    "\\( -name \"AuzixInstaller-*.auzix.json\" -o -name \"GRUB-*.auzix.json\" "
                    "-o -name \"AuzixPackageTools-*.auzix.json\" -o -name \"Xorg-*.auzix.json\" "
                    "-o -name \"Enlightenment-*.auzix.json\" -o -name \"LightDM-*.auzix.json\" "
                    "-o -name \"DBus-*.auzix.json\" -o -name \"Udev-*.auzix.json\" \\) -print | sort'\"'\"''"
                ),
                "timeout": 1800,
            },
            {
                "name": "iso-build",
                "transport": "ssh-manager",
                "target": "manager",
                "active": "Building a VM134 install ISO from the refreshed strict root.",
                "complete": "VM134 install ISO and checksum are available in local build scratch.",
                "command": (
                    "bash -lc 'set -e; "
                    "scratch=/var/tmp/auzix-vm134-build; "
                    "mkdir -p /mnt/swarm/AuziX && "
                    "{ mountpoint -q /mnt/swarm/AuziX || mount -t nfs "
                    "192.168.1.10:/srv/nfs/swarm/AuziX /mnt/swarm/AuziX; } && "
                    "test -d \"$scratch/out/auzix-strict/AuzixRoot\" && "
                    "docker run --rm -v \"$scratch\":/workspace -w /workspace "
                    "auzix/builder:local bash -lc "
                    f"'\"'\"'AUZIX_ISO_NAME={AUZIX_VM134_ISO_NAME} "
                    "AUZIX_ISO_WORK_DIR=/var/tmp/auzix-iso-vm134 "
                    "AUZIX_LIVE_ROOT_MODE=iso-root "
                    "make auzix-strict-iso && "
                    f"test -s artifacts/auzix/{AUZIX_VM134_ISO_NAME} && "
                    f"test -s artifacts/auzix/{AUZIX_VM134_ISO_NAME}.sha256 && "
                    f"sha256sum -c artifacts/auzix/{AUZIX_VM134_ISO_NAME}.sha256'\"'\"''"
                ),
                "timeout": 2400,
            },
            {
                "name": "iso-publish",
                "transport": "internal",
                "kind": "auzix-vm134-iso-publish",
                "active": "Publishing the VM134 install ISO to Proxmox local ISO storage.",
                "complete": "Proxmox local ISO storage has the VM134 install media.",
                "timeout": 300,
            },
            {
                "name": "vm-target-verify",
                "transport": "internal",
                "kind": "auzix-vm134-target-verify",
                "active": "Verifying VM134 has a large disk, ISO boot media, and disk fallback boot order.",
                "complete": "VM134 target shape is ready for the live installer handoff.",
                "timeout": 120,
            },
            {
                "name": "install-handoff",
                "transport": "internal",
                "kind": "event-note",
                "active": "Publishing the VM134 install handoff.",
                "complete": "VM134 install handoff published.",
                "message": (
                    "VM134 is prepared for the guarded live installer path. Boot the ISO, run "
                    "`/System/Tools/auzix-installer-gui` or "
                    "`/System/Tools/auzix-installer tui`, choose `/dev/sda` with GRUB, and only "
                    "then let BKC add the destructive install execution stage."
                ),
            },
        ],
        "complete_message": "AuziX VM134 install refresh preflight completed.",
    },
    "auzix-vm135-fresh-install-target": {
        "supports_undeploy": False,
        "stage_plan": [
            {
                "name": "artifact-verify",
                "transport": "internal",
                "kind": "auzix-vm135-artifact-verify",
                "active": "Verifying the freshly-built AuziX install ISO artifact and checksum.",
                "complete": "AuziX install ISO artifact is ready for VM135.",
                "timeout": 120,
            },
            {
                "name": "iso-publish",
                "transport": "internal",
                "kind": "auzix-vm135-iso-publish",
                "active": "Publishing the fresh AuziX install ISO to Proxmox for VM135.",
                "complete": "Proxmox local ISO storage has the VM135 install media.",
                "timeout": 420,
            },
            {
                "name": "vm135-recreate",
                "transport": "internal",
                "kind": "auzix-vm135-recreate",
                "active": "Destroying any existing VM135 and recreating it as a fresh AuziX install target.",
                "complete": "VM135 exists with a fresh disk and ISO-first boot order.",
                "timeout": 180,
            },
            {
                "name": "vm135-start",
                "transport": "internal",
                "kind": "auzix-vm135-start",
                "active": "Starting VM135 from the fresh AuziX install ISO.",
                "complete": "VM135 is running from the fresh AuziX install media.",
                "timeout": 120,
            },
            {
                "name": "install-handoff",
                "transport": "internal",
                "kind": "event-note",
                "active": "Publishing the VM135 install handoff.",
                "complete": "VM135 install handoff published.",
                "message": (
                    "VM135 is a fresh disposable AuziX install target booting the latest ISO. "
                    "Use `/System/Tools/auzix-installer-gui` or `/System/Tools/auzix-installer tui`, "
                    "target `/dev/sda`, and keep VM134 untouched for comparison."
                ),
            },
        ],
        "complete_message": "AuziX VM135 fresh install target is running.",
    },
    "auzix-core-root-validation": {
        "supports_undeploy": False,
        "stage_plan": [
            {
                "name": "source-verify",
                "transport": "ssh-controller",
                "target": "controller",
                "active": "Verifying AuZiX core validation entry points in the staged source.",
                "complete": "AuZiX core validation source is ready.",
                "command": (
                    "bash -lc 'cd /srv/nfs/swarm/AuziX/src && "
                    "grep -Fx e182842 .auzix-commit >/dev/null && "
                    "test -x scripts/run-auzix-core-validation.sh && "
                    "test -x scripts/audit-auzix-strict-root.sh && "
                    "test -x scripts/audit-auzix-package-runtime.sh && "
                    "test -x scripts/build-auzix-strict-container.sh && "
                    "grep -F \"auzix-core-validation:\" Makefile >/dev/null && "
                    "echo auzix-core-validation-source-ready'"
                ),
                "timeout": 60,
            },
            {
                "name": "builder-prepare",
                "transport": "ssh-manager",
                "target": "manager",
                "active": "Preparing the AuZiX builder image for the core validation loop.",
                "complete": "AuZiX builder image is ready.",
                "command": (
                    "bash -lc 'set -e; "
                    "mkdir -p /mnt/swarm/AuziX && "
                    "{ mountpoint -q /mnt/swarm/AuziX || mount -t nfs "
                    "192.168.1.10:/srv/nfs/swarm/AuziX /mnt/swarm/AuziX; } && "
                    "{ docker image inspect auzix/builder:local >/dev/null 2>&1 || "
                    "docker build --pull=false -f /mnt/swarm/AuziX/src/docker/builder/Dockerfile "
                    "-t auzix/builder:local /mnt/swarm/AuziX/src; } && "
                    "docker image inspect auzix/builder:local >/dev/null && "
                    "echo auzix-core-builder-ready'"
                ),
                "timeout": 900,
            },
            {
                "name": "core-validation",
                "transport": "ssh-manager",
                "target": "manager",
                "active": "Running the cheap AuZiX root/container validation loop before ISO or VM work.",
                "complete": "AuZiX core validation loop completed.",
                "command": (
                    "bash -lc 'set -e; "
                    "scratch=/var/tmp/auzix-core-validation; "
                    "rm -rf \"$scratch\" && mkdir -p \"$scratch\" && "
                    "rsync -a --delete --exclude out/ --exclude artifacts/ "
                    "/mnt/swarm/AuziX/src/ \"$scratch\"/ && "
                    "rc=0; "
                    "docker run --rm -v \"$scratch\":/workspace -w /workspace "
                    "auzix/builder:local bash -lc "
                    "'\"'\"'apt-get update >/dev/null && "
                    "apt-get install -y --no-install-recommends "
                    "grub2-common grub-pc-bin "
                    "xinit xserver-xorg-core xserver-xorg-legacy "
                    "xserver-xorg-input-libinput xserver-xorg-video-fbdev "
                    "xserver-xorg-video-vesa enlightenment terminology "
                    "lightdm lightdm-gtk-greeter dbus dbus-x11 udev acpid "
                    "pulseaudio strace xterm >/dev/null && "
                    "AUZIX_CORE_CONTAINER=0 make auzix-core-validation'\"'\"' || rc=$?; "
                    "AUZIX_STRICT_IMAGE=auzix-strict:core-validation "
                    "\"$scratch/scripts/build-auzix-strict-container.sh\" "
                    ">>\"$scratch/out/core-validation/container-smoke.txt\" 2>&1 || rc=$?; "
                    "if docker image inspect auzix-strict:core-validation >/dev/null 2>&1; then "
                    "docker run --rm auzix-strict:core-validation "
                    "/Programs/BusyBox/1.36.1/Commands/busybox sh -c "
                    "'\"'\"'test -x /System/Tools/start-enlightenment-session && "
                    "test -e /System/Tools/launch-auzix-installer && "
                    "test -s /Users/auzix/.config/autostart/auzix-installer.desktop && "
                    "echo core-container-smoke-ok'\"'\"' "
                    ">>\"$scratch/out/core-validation/container-smoke.txt\" 2>&1 || rc=$?; "
                    "else rc=1; fi; "
                    "mkdir -p /mnt/swarm/AuziX/src/out/core-validation && "
                    "rsync -a \"$scratch/out/core-validation/\" "
                    "/mnt/swarm/AuziX/src/out/core-validation/ && "
                    "jq -e '\\'' .format == \"auzix-core-validation-report-v1\" '\\'' "
                    "/mnt/swarm/AuziX/src/out/core-validation/summary.json >/dev/null && "
                    "cat /mnt/swarm/AuziX/src/out/core-validation/summary.json && "
                    "exit \"$rc\"'"
                ),
                "timeout": 2400,
            },
            {
                "name": "prompt-report",
                "transport": "ssh-controller",
                "target": "controller",
                "active": "Publishing the bounded core validation summary and Ollama prompt paths.",
                "complete": "AuZiX core validation prompt is ready for review or worker triage.",
                "command": (
                    "bash -lc 'cd /srv/nfs/swarm/AuziX/src && "
                    "test -s out/core-validation/summary.json && "
                    "test -s out/core-validation/ollama-prompt.md && "
                    "jq -e '\"'\"'.format == \"auzix-core-validation-report-v1\"'\"'\"' "
                    "out/core-validation/summary.json >/dev/null && "
                    "printf \"summary=%s\\nprompt=%s\\n\" "
                    "\"/srv/nfs/swarm/AuziX/src/out/core-validation/summary.json\" "
                    "\"/srv/nfs/swarm/AuziX/src/out/core-validation/ollama-prompt.md\"'"
                ),
                "timeout": 60,
            },
        ],
        "complete_message": "AuZiX core root validation pipeline completed.",
    },
    "auzix-installer-foundation": {
        "supports_undeploy": False,
        "stage_plan": [
            {
                "name": "source-verify",
                "transport": "ssh-controller",
                "target": "controller",
                "active": "Verifying the staged AuziX installer source and build entry points.",
                "complete": "Staged AuziX installer source is ready.",
                "command": (
                    "bash -lc '"
                    "cd /srv/nfs/swarm/AuziX/src && "
                    "test -s installer/install-plan.schema.json && "
                    "test -s installer/questions.json && "
                    "test -s installer/auzix-installer.lua && "
                    "test -s installer/auzix-package-setup.lua && "
                    "test -x scripts/build-auzix-installer-package.sh && "
                    "test -x scripts/test-auzix-installer.sh && "
                    "echo auzix-installer-source-ready'"
                ),
                "timeout": 60,
            },
            {
                "name": "installer-build",
                "transport": "ssh-manager",
                "target": "manager",
                "active": "Packaging Lua, dialog, and the AuziX installer into the staged strict root.",
                "complete": "AuziX installer runtime and frontend contract packaged.",
                "command": (
                    "bash -lc '"
                    "mkdir -p /mnt/swarm/AuziX && "
                    "{ mountpoint -q /mnt/swarm/AuziX || mount -t nfs 192.168.1.10:/srv/nfs/swarm/AuziX /mnt/swarm/AuziX; } && "
                    "{ docker image inspect auzix/installer-builder:local >/dev/null || "
                    "docker build --pull=false -f /mnt/swarm/AuziX/src/docker/installer-builder/Dockerfile "
                    "-t auzix/installer-builder:local /mnt/swarm/AuziX/src; } && "
                    "docker run --rm -v /mnt/swarm/AuziX/src:/workspace -w /workspace "
                    "auzix/installer-builder:local bash -lc "
                    "\"./scripts/build-auzix-package-tools-package.sh && ./scripts/build-auzix-installer-package.sh\"'"
                ),
                "timeout": 900,
            },
            {
                "name": "contract-test",
                "transport": "ssh-manager",
                "target": "manager",
                "active": "Testing plan validation and guarded execution with a non-destructive fake disk executor.",
                "complete": "Installer validation and guarded execution contract passed.",
                "command": (
                    "bash -lc '"
                    "docker run --rm -v /mnt/swarm/AuziX/src:/workspace -w /workspace "
                    "auzix/installer-builder:local bash -lc "
                    "\"./scripts/test-auzix-installer.sh && "
                    "grep -F auzix-install-plan-v1 installer/plans/default.json >/dev/null && "
                    "grep -F auzix-installer-questions-v1 installer/questions.json >/dev/null\" && "
                    "echo auzix-installer-contract-pass'"
                ),
                "timeout": 180,
            },
            {
                "name": "artifact-report",
                "transport": "ssh-controller",
                "target": "controller",
                "active": "Reporting the staged installer runtime and frontend artifacts.",
                "complete": "Installer artifacts are present on the shared AuziX workspace.",
                "command": (
                    "bash -lc '"
                    "root=/srv/nfs/swarm/AuziX/src/out/auzix-strict/AuzixRoot && "
                    "test -L \"$root/Programs/Lua/current\" && "
                    "test -L \"$root/Programs/Dialog/current\" && "
                    "test -L \"$root/Programs/AuzixInstaller/current\" && "
                    "test -L \"$root/System/Tools/auzix-installer\" && "
                    "test -L \"$root/System/Tools/auzix-installer-gui\" && "
                    "find \"$root/System/Settings/installer\" -maxdepth 2 -type f -print | sort && "
                    "echo auzix-installer-artifacts-ready'"
                ),
                "timeout": 60,
            },
        ],
        "complete_message": "AuziX installer foundation pipeline completed.",
    },
    "lab-cluster-storage": {
        "supports_undeploy": False,
        "stage_plan": [
            {
                "name": "storage-preflight",
                "transport": "ssh-manager",
                "target": "manager",
                "kind": "lab-storage-preflight",
                "action": "ssh.lvm.grow_root",
                "active": "Checking root LVM layout and free extents on both clusters.",
                "complete": "All cluster guests have LVM-backed roots and sufficient capacity.",
                "command": (
                    "bash -lc 'set -e; "
                    f"{_lab_storage_known_hosts_prelude()}"
                    f"for host in {LAB_STORAGE_ALL_HOSTS}; do "
                    "ssh -o BatchMode=yes -o UserKnownHostsFile=/root/.ssh/known_hosts "
                    "-o StrictHostKeyChecking=yes root@$host "
                    "\"findmnt -n -o SOURCE /; lvs --noheadings -o lv_size; "
                    "vgs --noheadings --units g -o vg_free\"; "
                    "done'"
                ),
                "timeout": 120,
            },
            {
                "name": "swarm-grow",
                "transport": "ssh-manager",
                "target": "manager",
                "kind": "lab-storage-grow-swarm",
                "action": "ssh.lvm.grow_root",
                "active": "Growing Swarm root filesystems to 50 GiB where needed.",
                "complete": "Swarm root filesystems meet the 50 GiB target.",
                "command": (
                    "bash -lc 'set -e; "
                    f"{_lab_storage_known_hosts_prelude(LAB_STORAGE_SWARM_HOSTS)}"
                    f"for host in {LAB_STORAGE_SWARM_HOSTS}; do "
                    "ssh -o BatchMode=yes -o UserKnownHostsFile=/root/.ssh/known_hosts "
                    "-o StrictHostKeyChecking=yes root@$host '\"'\"'set -e; "
                    "lv=$(lvs --noheadings -o lv_path | xargs); "
                    "bytes=$(findmnt -bn -o SIZE /); "
                    f"[ \"$bytes\" -ge {LAB_STORAGE_MIN_ROOT_BYTES} ] || lvextend -r -L 50G \"$lv\"; "
                    "df -hT /'\"'\"'; done'"
                ),
                "timeout": 300,
            },
            {
                "name": "k3s-grow",
                "transport": "ssh-manager",
                "target": "manager",
                "kind": "lab-storage-grow-k3s",
                "action": "ssh.lvm.grow_root",
                "active": "Growing k3s root filesystems to 50 GiB where needed.",
                "complete": "k3s root filesystems meet the 50 GiB target.",
                "command": (
                    "bash -lc 'set -e; "
                    f"{_lab_storage_known_hosts_prelude(LAB_STORAGE_K3S_HOSTS)}"
                    f"for host in {LAB_STORAGE_K3S_HOSTS}; do "
                    "ssh -o BatchMode=yes -o UserKnownHostsFile=/root/.ssh/known_hosts "
                    "-o StrictHostKeyChecking=yes root@$host '\"'\"'set -e; "
                    "lv=$(lvs --noheadings -o lv_path | xargs); "
                    "bytes=$(findmnt -bn -o SIZE /); "
                    f"[ \"$bytes\" -ge {LAB_STORAGE_MIN_ROOT_BYTES} ] || lvextend -r -L 50G \"$lv\"; "
                    "df -hT /'\"'\"'; done'"
                ),
                "timeout": 300,
            },
            {
                "name": "storage-verify",
                "transport": "ssh-manager",
                "target": "manager",
                "kind": "lab-storage-verify",
                "action": "ssh.lvm.grow_root",
                "active": "Verifying root capacity and retained VG reserve.",
                "complete": "Cluster storage expansion contract passed.",
                "command": (
                    "bash -lc 'set -e; "
                    f"{_lab_storage_known_hosts_prelude()}"
                    f"for host in {LAB_STORAGE_ALL_HOSTS}; do "
                    "ssh -o BatchMode=yes -o UserKnownHostsFile=/root/.ssh/known_hosts "
                    "-o StrictHostKeyChecking=yes root@$host '\"'\"'set -e; "
                    f"bytes=$(findmnt -bn -o SIZE /); [ \"$bytes\" -ge {LAB_STORAGE_MIN_ROOT_BYTES} ]; "
                    "df -hT /; vgs --noheadings -o vg_name,vg_free'\"'\"'; done'"
                ),
                "timeout": 120,
            },
        ],
        "complete_message": "Lab cluster storage expansion pipeline completed.",
    },
    "auzix-installer-package-bot": {
        "supports_undeploy": False,
        "stage_plan": [
            {
                "name": "source-verify",
                "transport": "ssh-controller",
                "target": "controller",
                "active": "Verifying the staged installer package queue and bot entry points.",
                "complete": "Installer package queue source is ready.",
                "command": (
                    "bash -lc 'cd /srv/nfs/swarm/AuziX/src && "
                    "test -s packages/installer-ui.queue.json && "
                    "test -s packages/installer-ui.sources.json && "
                    "test -s packages/package-build-queue.schema.json && "
                    "test -x scripts/run-auzix-package-bot.sh && "
                    "test -x scripts/test-auzix-package-bot.sh && "
                    "test -x scripts/publish-auzix-package-repo.sh && "
                    "grep -Fx 0a64310 .auzix-commit >/dev/null && "
                    "echo auzix-package-bot-source-ready'"
                ),
                "timeout": 60,
            },
            {
                "name": "queue-contract",
                "transport": "ssh-manager",
                "target": "manager",
                "active": "Validating package states, script allowlisting, and required installer UI entries.",
                "complete": "Installer package queue contract passed.",
                "command": (
                    "bash -lc 'mkdir -p /mnt/swarm/AuziX && "
                    "{ mountpoint -q /mnt/swarm/AuziX || mount -t nfs "
                    "192.168.1.10:/srv/nfs/swarm/AuziX /mnt/swarm/AuziX; } && "
                    "{ docker image inspect auzix/installer-builder:local >/dev/null 2>&1 || "
                    "docker build --pull=false -f /mnt/swarm/AuziX/src/docker/installer-builder/Dockerfile "
                    "-t auzix/installer-builder:local /mnt/swarm/AuziX/src; } && "
                    "docker run --rm -v /mnt/swarm/AuziX/src:/workspace -w /workspace "
                    "auzix/installer-builder:local ./scripts/test-auzix-package-bot.sh'"
                ),
                "timeout": 900,
            },
            {
                "name": "package-build",
                "transport": "ssh-manager",
                "target": "manager",
                "active": "Building the installer UI package batch on the slow worker.",
                "complete": "Installer UI package batch completed.",
                "command": (
                    "bash -lc '{ docker image inspect auzix/builder:local >/dev/null 2>&1 || "
                    "docker build --pull=false -f /mnt/swarm/AuziX/src/docker/builder/Dockerfile "
                    "-t auzix/builder:local /mnt/swarm/AuziX/src; } && "
                    "docker run --rm "
                    "-v /mnt/swarm/AuziX/src:/workspace -w /workspace "
                    "auzix/builder:local bash -lc "
                    "'\"'\"'apt-get update >/dev/null && "
                    "apt-get install -y --no-install-recommends "
                    "xinit xserver-xorg-legacy >/dev/null && "
                    "./scripts/run-auzix-package-bot.sh "
                    "packages/installer-ui.queue.json installer-ui-core'\"'\"''"
                ),
                "timeout": 7200,
            },
            {
                "name": "artifact-report",
                "transport": "ssh-controller",
                "target": "controller",
                "active": "Verifying package receipts and the machine-readable batch report.",
                "complete": "Installer UI package receipts and report are available.",
                "command": (
                    "bash -lc 'cd /srv/nfs/swarm/AuziX/src && "
                    "report=out/package-bot/installer-ui-core.report.json && "
                    "jq -e '\"'\"'.format == \"auzix-package-build-report-v1\" "
                    "and .status == \"complete\" and (.results | length == 6)'\"'\"' \"$report\" >/dev/null && "
                    "for package in AuzixPackageTools AuzixInstaller Xorg Enlightenment Terminology LightDM; do "
                    "find out/auzix-strict/AuzixRoot/System/PackageDB -maxdepth 1 "
                    "-name \"$package-*.auzix.json\" -print -quit | grep -q .; "
                    "done && jq . \"$report\"'"
                ),
                "timeout": 120,
            },
            {
                "name": "repository-build",
                "transport": "ssh-manager",
                "target": "manager",
                "active": "Building checksummed AuziX repository archives from package receipts.",
                "complete": "AuziX repository archives and index were built.",
                "command": (
                    "bash -lc 'docker run --rm "
                    "-v /mnt/swarm/AuziX/src:/workspace -w /workspace "
                    "auzix/builder:local ./scripts/build-auzix-package-repo.sh "
                    "/workspace/out/auzix-strict/AuzixRoot'"
                ),
                "timeout": 3600,
            },
            {
                "name": "repository-publish",
                "transport": "ssh-controller",
                "target": "controller",
                "active": "Publishing verified archives and replacing the HTTP repository index.",
                "complete": "AuziX package repository was published.",
                "command": (
                    "bash -lc 'cd /srv/nfs/swarm/AuziX/src && "
                    "./scripts/publish-auzix-package-repo.sh "
                    "/srv/nfs/swarm/AuziX/src/artifacts/auzix/repo "
                    "/srv/http/auzix/repo'"
                ),
                "timeout": 1800,
            },
            {
                "name": "repository-verify",
                "transport": "ssh-controller",
                "target": "controller",
                "active": "Verifying installer packages through the served repository index.",
                "complete": "Served AuziX repository contains the installer package batch.",
                "command": (
                    "bash -lc 'set -e; "
                    "index=$(mktemp); trap '\"'\"'rm -f \"$index\"'\"'\"' EXIT; "
                    "curl -fsS http://192.168.1.10/auzix/repo/index.json -o \"$index\"; "
                    "jq -e '\"'\"'.format == \"auzix-repo-v1\"'\"'\"' \"$index\" >/dev/null; "
                    "for package in AuzixPackageTools AuzixInstaller Xorg Enlightenment Terminology LightDM; do "
                    "archive=$(jq -r --arg package \"$package\" "
                    "'\"'\"'.packages[] | select(.name == $package) | .package'\"'\"' \"$index\" | head -1); "
                    "test -n \"$archive\"; "
                    "curl -fsSI \"http://192.168.1.10/auzix/repo/packages/$archive\" >/dev/null; "
                    "done; jq '\"'\"'{created, package_count: (.packages | length)}'\"'\"' \"$index\"'"
                ),
                "timeout": 180,
            },
        ],
        "complete_message": "AuziX installer package bot built and published the repository.",
    },
    "auzix-trixie-package-intake": {
        "supports_undeploy": False,
        "stage_plan": [
            {
                "name": "source-verify",
                "transport": "ssh-controller",
                "target": "controller",
                "active": "Verifying the Trixie package profile and intake scripts.",
                "complete": "Trixie package intake source is ready.",
                "command": (
                    "bash -lc 'cd /srv/nfs/swarm/AuziX/src && "
                    "test -s profiles/packages/auzix-trixie-user-apps.packages && "
                    "test -s docker/trixie-builder/Dockerfile && "
                    "test -x scripts/run-auzix-trixie-intake.sh && "
                    "test -x scripts/test-auzix-trixie-intake.sh && "
                    "grep -Fx 0a64310 .auzix-commit >/dev/null && "
                    "./scripts/test-auzix-trixie-intake.sh'"
                ),
                "timeout": 120,
            },
            {
                "name": "builder-prepare",
                "transport": "ssh-manager",
                "target": "manager",
                "active": "Preparing the dedicated Debian Trixie package intake image.",
                "complete": "Debian Trixie package intake image is ready.",
                "command": (
                    "bash -lc 'mkdir -p /mnt/swarm/AuziX && "
                    "{ mountpoint -q /mnt/swarm/AuziX || mount -t nfs "
                    "192.168.1.10:/srv/nfs/swarm/AuziX /mnt/swarm/AuziX; } && "
                    "docker build --pull=false "
                    "-f /mnt/swarm/AuziX/src/docker/trixie-builder/Dockerfile "
                    "-t auzix/trixie-builder:local /mnt/swarm/AuziX/src'"
                ),
                "timeout": 3600,
            },
            {
                "name": "package-intake",
                "transport": "ssh-manager",
                "target": "manager",
                "active": "Attempting the Trixie user application profile sequentially.",
                "complete": "Trixie package intake attempts completed.",
                "command": (
                    "bash -lc 'docker run --rm "
                    "-v /mnt/swarm/AuziX/src:/workspace -w /workspace "
                    "auzix/trixie-builder:local bash -lc "
                    "'\"'\"'apt-get update >/dev/null && "
                    "./scripts/run-auzix-trixie-intake.sh'\"'\"''"
                ),
                "timeout": 21600,
            },
            {
                "name": "repository-build",
                "transport": "ssh-manager",
                "target": "manager",
                "active": "Rebuilding the AuziX repository with successful Trixie intake receipts.",
                "complete": "AuziX repository includes successful Trixie intake packages.",
                "command": (
                    "bash -lc 'docker run --rm "
                    "-v /mnt/swarm/AuziX/src:/workspace -w /workspace "
                    "auzix/builder:local ./scripts/build-auzix-package-repo.sh "
                    "/workspace/out/auzix-strict/AuzixRoot'"
                ),
                "timeout": 7200,
            },
            {
                "name": "repository-publish",
                "transport": "ssh-controller",
                "target": "controller",
                "active": "Publishing successful Trixie intake packages.",
                "complete": "Successful Trixie intake packages were published.",
                "command": (
                    "bash -lc 'cd /srv/nfs/swarm/AuziX/src && "
                    "./scripts/publish-auzix-package-repo.sh "
                    "/srv/nfs/swarm/AuziX/src/artifacts/auzix/repo "
                    "/srv/http/auzix/repo'"
                ),
                "timeout": 3600,
            },
            {
                "name": "repository-verify",
                "transport": "ssh-controller",
                "target": "controller",
                "active": "Verifying the Trixie intake report and served compatibility packages.",
                "complete": "Trixie intake report and published packages are available.",
                "command": (
                    "bash -lc 'cd /srv/nfs/swarm/AuziX/src && "
                    "report=out/package-bot/trixie-user-apps.report.json && "
                    "jq -e '\"'\"'.format == \"auzix-trixie-intake-report-v1\" "
                    "and .complete > 0'\"'\"' \"$report\" >/dev/null && "
                    "curl -fsS http://192.168.1.10/auzix/repo/index.json | "
                    "jq -e '\"'\"'any(.packages[]; (.name | startswith(\"Debian.\")))'\"'\"' >/dev/null && "
                    "jq '\"'\"'{status, complete, failed}'\"'\"' \"$report\"'"
                ),
                "timeout": 180,
            },
        ],
        "complete_message": "AuziX Trixie package intake completed and successful packages were published.",
    },
    "auzix-office-package-smoke": {
        "supports_undeploy": False,
        "stage_plan": [
            {
                "name": "source-verify",
                "transport": "ssh-controller",
                "target": "controller",
                "active": "Verifying the focused office package profile and smoke test.",
                "complete": "Office package smoke source is ready.",
                "command": (
                    "bash -lc 'cd /srv/nfs/swarm/AuziX/src && "
                    "test -s profiles/packages/auzix-office-smoke.packages && "
                    "test -x scripts/test-auzix-office-smoke.sh && "
                    "grep -Fx 0a64310 .auzix-commit >/dev/null && "
                    "./scripts/test-auzix-office-smoke.sh'"
                ),
                "timeout": 120,
            },
            {
                "name": "builder-prepare",
                "transport": "ssh-manager",
                "target": "manager",
                "active": "Preparing the Debian Trixie office package builder.",
                "complete": "Office package builder is ready.",
                "command": (
                    "bash -lc 'mkdir -p /mnt/swarm/AuziX && "
                    "{ mountpoint -q /mnt/swarm/AuziX || mount -t nfs "
                    "192.168.1.10:/srv/nfs/swarm/AuziX /mnt/swarm/AuziX; } && "
                    "docker build --pull=false "
                    "-f /mnt/swarm/AuziX/src/docker/trixie-builder/Dockerfile "
                    "-t auzix/trixie-builder:local /mnt/swarm/AuziX/src'"
                ),
                "timeout": 3600,
            },
            {
                "name": "package-build",
                "transport": "ssh-manager",
                "target": "manager",
                "active": "Building AbiWord and Gnumeric compatibility packages.",
                "complete": "AbiWord and Gnumeric package builds completed.",
                "command": (
                    "bash -lc 'docker run --rm "
                    "-v /mnt/swarm/AuziX/src:/workspace -w /workspace "
                    "auzix/trixie-builder:local bash -lc "
                    "'\"'\"'./scripts/run-auzix-office-smoke.sh'\"'\"''"
                ),
                "timeout": 7200,
            },
            {
                "name": "package-test",
                "transport": "ssh-manager",
                "target": "manager",
                "active": "Validating office package receipts and application payloads.",
                "complete": "Office package receipts and payloads passed.",
                "command": (
                    "bash -lc 'docker run --rm "
                    "-v /mnt/swarm/AuziX/src:/workspace -w /workspace "
                    "auzix/trixie-builder:local "
                    "bash -lc '\"'\"'./scripts/test-auzix-office-smoke.sh "
                    "/workspace/out/auzix-strict/AuzixRoot && "
                    "./scripts/audit-auzix-package-runtime.sh "
                    "/workspace/out/auzix-strict/AuzixRoot AbiWord && "
                    "./scripts/audit-auzix-package-runtime.sh "
                    "/workspace/out/auzix-strict/AuzixRoot Gnumeric'\"'\"''"
                ),
                "timeout": 300,
            },
            {
                "name": "repository-build",
                "transport": "ssh-manager",
                "target": "manager",
                "active": "Building repository archives for the office package smoke.",
                "complete": "Office package repository archives were built.",
                "command": (
                    "bash -lc 'docker run --rm "
                    "-v /mnt/swarm/AuziX/src:/workspace -w /workspace "
                    "auzix/builder:local ./scripts/build-auzix-package-repo.sh "
                    "/workspace/out/auzix-strict/AuzixRoot'"
                ),
                "timeout": 7200,
            },
            {
                "name": "repository-publish",
                "transport": "ssh-controller",
                "target": "controller",
                "active": "Incrementally publishing the office package smoke results.",
                "complete": "Office package smoke results were published.",
                "command": (
                    "bash -lc 'cd /srv/nfs/swarm/AuziX/src && "
                    "./scripts/publish-auzix-package-repo.sh "
                    "/srv/nfs/swarm/AuziX/src/artifacts/auzix/repo "
                    "/srv/http/auzix/repo'"
                ),
                "timeout": 3600,
            },
            {
                "name": "repository-verify",
                "transport": "ssh-controller",
                "target": "controller",
                "active": "Verifying served AbiWord and Gnumeric package archives.",
                "complete": "Served AbiWord and Gnumeric packages passed.",
                "command": (
                    "bash -lc 'cd /srv/nfs/swarm/AuziX/src && "
                    "report=out/package-bot/office-smoke.report.json && "
                    "jq -e '\"'\"'.status == \"complete\" and .complete == 2 and .failed == 0'\"'\"' "
                    "\"$report\" >/dev/null && "
                    "index=$(mktemp); trap '\"'\"'rm -f \"$index\"'\"'\"' EXIT; "
                    "curl -fsS http://192.168.1.10/auzix/repo/index.json >\"$index\"; "
                    "for package in AbiWord Gnumeric; do "
                    "archive=$(jq -r --arg package \"$package\" "
                    "'\"'\"'.packages[] | select(.name == $package) | .package'\"'\"' "
                    "\"$index\" | head -1); test -n \"$archive\"; "
                    "curl -fsSI \"http://192.168.1.10/auzix/repo/packages/$archive\" >/dev/null; "
                    "done; jq '\"'\"'{package_count: (.packages | length)}'\"'\"' \"$index\"'"
                ),
                "timeout": 180,
            },
        ],
        "complete_message": "AuziX AbiWord and Gnumeric package smoke completed and published.",
    },
    "monitoring-stack": {
        "supports_undeploy": True,
        "deploy_stage": "stack-deploy",
        "deploy_active": "Applying monitoring stack via ns1 Ansible controller.",
        "deploy_complete": "Monitoring stack applied.",
        "deploy_command": (
            "cd /srv/ansible && "
            "ANSIBLE_CONFIG=/srv/ansible/ansible.cfg "
            "/opt/ansible-venv/bin/ansible-playbook -i inventory/lab.yml monitoring-stack.yml"
        ),
        "health_stage": "health-check",
        "health_active": "Checking Grafana, Prometheus, Loki, and monitoring service replicas.",
        "health_complete": "Grafana, Prometheus, and Loki responded.",
        "health_command": (
            "bash -lc '"
            "for _ in $(seq 1 30); do "
            "curl -fsS http://swarm1.lab.auzietek.com:3000/login >/dev/null && "
            "curl -fsS http://swarm1.lab.auzietek.com:9090/-/healthy >/dev/null && "
            "curl -fsS http://swarm1.lab.auzietek.com:3100/ready >/dev/null && "
            "docker service ls --format \"{{.Name}} {{.Replicas}}\" | grep \"^monitoring_grafana 1/1$\" >/dev/null && "
            "docker service ls --format \"{{.Name}} {{.Replicas}}\" | grep \"^monitoring_prometheus 1/1$\" >/dev/null && "
            "docker service ls --format \"{{.Name}} {{.Replicas}}\" | grep \"^monitoring_loki 1/1$\" >/dev/null && "
            "{ echo monitoring-ok; exit 0; }; "
            "sleep 4; "
            "done; "
            "echo monitoring-health-timeout; exit 1'"
        ),
        "init_stage": "grafana-init",
        "init_active": "Importing Grafana dashboards through the API.",
        "init_complete": "Grafana dashboards imported.",
        "init_command": "/usr/local/bin/monitoring-grafana-init",
        "dashboard_message": "Grafana: http://swarm1.lab.auzietek.com:3000, Prometheus: http://swarm1.lab.auzietek.com:9090, Loki: http://swarm1.lab.auzietek.com:3100",
        "complete_message": "Monitoring pipeline completed.",
        "undeploy_stage": "stack-remove",
        "undeploy_active": "Removing monitoring stack from the swarm manager.",
        "undeploy_complete": "Monitoring stack removed.",
        "undeploy_command": (
            "bash -lc '"
            "docker stack rm monitoring >/dev/null 2>&1 || true; "
            "for _ in $(seq 1 30); do "
            "docker stack ls --format \"{{.Name}}\" | grep -qx monitoring || { echo monitoring-removed; exit 0; }; "
            "sleep 2; "
            "done; "
            "echo monitoring-removal-timeout; exit 1'"
        ),
        "absence_active": "Verifying monitoring services are no longer advertised.",
        "absence_complete": "Monitoring services are absent from the swarm.",
        "absence_command": (
            "bash -lc '"
            "docker stack ls --format \"{{.Name}}\" | grep -qx monitoring && exit 1 || true; "
            "docker service ls --format \"{{.Name}}\" | grep -q \"^monitoring_\" && exit 1 || true; "
            "echo monitoring-absent'"
        ),
        "removed_dashboard_message": "Monitoring stack removed. Grafana, Prometheus, and Loki endpoints should now be offline.",
        "runtime_snapshot": {
            "kind": "service",
            "service_filter": "^monitoring_",
            "service_names": [
                "monitoring_grafana",
                "monitoring_prometheus",
                "monitoring_loki",
                "monitoring_promtail",
            ],
        },
    },
    "microblog-publish": {
        "supports_undeploy": True,
        "deploy_stage": "stack-deploy",
        "deploy_active": "Applying micro-blog stack via ns1 Ansible controller.",
        "deploy_complete": "Micro-blog stack applied.",
        "deploy_command": (
            "cd /srv/ansible && "
            "ANSIBLE_CONFIG=/srv/ansible/ansible.cfg "
            "/opt/ansible-venv/bin/ansible-playbook -i inventory/lab.yml microblog-stack.yml"
        ),
        "health_stage": "health-check",
        "health_active": "Checking micro-blog API, UI, and compose services.",
        "health_complete": "Micro-blog API and UI responded.",
        "health_command": (
            "bash -lc '"
            "for _ in $(seq 1 30); do "
            "curl -fsS http://swarm1.lab.auzietek.com:8080/healthz >/dev/null && "
            "curl -fsS http://swarm1.lab.auzietek.com:8081/blog >/dev/null && "
            "cd /srv/stacks/micro-blog/app && "
            "docker compose ps --services --status running | grep -qx blog-api && "
            "docker compose ps --services --status running | grep -qx blog-worker && "
            "docker compose ps --services --status running | grep -qx blog-projection && "
            "docker compose ps --services --status running | grep -qx blog-ui && "
            "{ echo microblog-ok; exit 0; }; "
            "sleep 4; "
            "done; "
            "echo microblog-health-timeout; exit 1'"
        ),
        "dashboard_message": "Micro-Blog UI: http://swarm1.lab.auzietek.com:8081/blog, API: http://swarm1.lab.auzietek.com:8080/healthz, RabbitMQ: http://swarm1.lab.auzietek.com:15672",
        "complete_message": "Micro-blog pipeline completed.",
        "undeploy_stage": "stack-remove",
        "undeploy_active": "Removing micro-blog compose stack from the manager.",
        "undeploy_complete": "Micro-blog stack removed.",
        "undeploy_command": (
            "bash -lc '"
            "cd /srv/stacks/micro-blog/app && "
            "docker compose down >/dev/null 2>&1 || true; "
            "docker compose ps --services --status running | grep . && exit 1 || echo microblog-removed'"
        ),
        "absence_active": "Verifying micro-blog services are no longer running.",
        "absence_complete": "Micro-blog services are absent from the manager.",
        "absence_command": (
            "bash -lc '"
            "cd /srv/stacks/micro-blog/app && "
            "docker compose ps --services --status running | grep . && exit 1 || echo microblog-absent'"
        ),
        "removed_dashboard_message": "Micro-blog stack removed. UI and API endpoints should now be offline.",
        "runtime_snapshot": {
            "kind": "compose",
            "compose_dir": "/srv/stacks/micro-blog/app",
            "service_names": [
                "blog-api",
                "blog-worker",
                "blog-projection",
                "blog-ui",
                "rabbitmq",
                "postgres",
                "redis",
            ],
        },
    },
    "host-telemetry": {
        "supports_undeploy": False,
        "deploy_stage": "telemetry-apply",
        "deploy_active": "Applying Telegraf host telemetry via ns1 Ansible controller.",
        "deploy_complete": "Host telemetry playbook applied.",
        "deploy_command": (
            "cd /srv/ansible && "
            "ANSIBLE_CONFIG=/srv/ansible/ansible.cfg "
            "/opt/ansible-venv/bin/ansible-playbook -i inventory/lab.yml setup_monitoring.yml"
        ),
        "health_stage": "health-check",
        "health_active": "Checking Telegraf Prometheus endpoints for ns1, Proxmox, and the swarm hosts.",
        "health_complete": "Host telemetry scrape endpoints responded.",
        "health_command": (
            "bash -lc '"
            "for _ in $(seq 1 20); do "
            "curl -fsS http://192.168.1.10:9273/metrics >/dev/null && "
            "curl -fsS http://swarm1.lab.auzietek.com:9273/metrics >/dev/null && "
            "curl -fsS http://swarm2.lab.auzietek.com:9273/metrics >/dev/null && "
            "curl -fsS http://swarm3.lab.auzietek.com:9273/metrics >/dev/null && "
            "curl -fsS http://192.168.1.9:9273/metrics >/dev/null && "
            "{ echo host-telemetry-ok; exit 0; }; "
            "sleep 4; "
            "done; "
            "echo host-telemetry-timeout; exit 1'"
        ),
        "dashboard_message": "Host telemetry available in Grafana through Host Ops and Swarm Runtime dashboards. Prometheus scrape targets should include ns1, Proxmox, and swarm host telegraf endpoints.",
        "complete_message": "Host telemetry pipeline completed.",
        "runtime_snapshot": {
            "kind": "service",
            "service_filter": "^monitoring_",
            "service_names": [
                "monitoring_prometheus",
                "monitoring_grafana",
                "monitoring_loki",
            ],
        },
    },
    "k3s-host-telemetry": {
        "supports_undeploy": False,
        "stage_plan": [
            {
                "name": "verify-k3s",
                "transport": "bkc-ssh",
                "kind": "k3s-host-telemetry-verify",
                "action": "k3s.nodes.ready",
                "active": "Verifying kube1 can read the k3s cluster and both nodes are Ready.",
                "complete": "K3s node readiness verified.",
                "timeout": 120,
            },
            {
                "name": "nfs-projects",
                "transport": "bkc-ssh",
                "kind": "k3s-housekeeping-nfs",
                "action": "ssh.nfs.ensure_mounts",
                "active": "Mounting shared project NFS paths on kube1 and kube2.",
                "complete": "Shared project NFS mounts are present on both k3s nodes.",
                "timeout": 240,
            },
            {
                "name": "apply-host-telemetry",
                "transport": "bkc-ssh",
                "kind": "k3s-host-telemetry-apply",
                "action": "k3s.manifest.apply",
                "active": "Applying Telegraf and cAdvisor DaemonSets through kube1.",
                "complete": "K3s host telemetry DaemonSets are rolled out.",
                "timeout": 600,
            },
            {
                "name": "apply-loki-logs",
                "transport": "bkc-ssh",
                "kind": "k3s-housekeeping-loki-logs",
                "action": "k3s.manifest.apply",
                "active": "Deploying k3s Promtail DaemonSet for host and pod logs.",
                "complete": "K3s host and pod logs are shipping toward Loki.",
                "timeout": 300,
            },
            {
                "name": "loadgen-steady",
                "transport": "bkc-ssh",
                "kind": "k3s-housekeeping-loadgen",
                "action": "k3s.manifest.apply",
                "active": "Deploying the steady rx-demo loadgen Deployment.",
                "complete": "Steady rx-demo loadgen Deployment is available.",
                "timeout": 300,
            },
            {
                "name": "open-firewall",
                "transport": "bkc-ssh",
                "kind": "k3s-host-telemetry-firewall",
                "action": "ssh.firewall.open_ports",
                "active": "Opening Telegraf and cAdvisor scrape ports on kube1 and kube2.",
                "complete": "K3s telemetry scrape ports are open on both nodes.",
                "timeout": 180,
            },
            {
                "name": "prometheus-targets",
                "transport": "ssh-manager",
                "kind": "k3s-host-telemetry-prometheus",
                "action": "prometheus.scrape_job.ensure",
                "target": "manager",
                "active": "Adding k3s Telegraf and cAdvisor jobs to shared Prometheus.",
                "complete": "Prometheus scrape jobs for kube1/kube2 are present.",
                "timeout": 240,
            },
            {
                "name": "scrape-validate",
                "transport": "ssh-manager",
                "kind": "k3s-host-telemetry-validate",
                "action": "prometheus.targets.verify",
                "target": "manager",
                "active": "Checking Prometheus target health for kube1/kube2 host telemetry.",
                "complete": "Prometheus reports k3s Telegraf and cAdvisor targets up.",
                "timeout": 180,
            },
            {
                "name": "dashboard-link",
                "transport": "internal",
                "kind": "event-note",
                "active": "Publishing Grafana dashboard pointers for k3s host telemetry.",
                "complete": "Dashboard pointers published.",
                "message": "Grafana should now receive kube1/kube2 host metrics through job=k3s-telegraf-hosts and container metrics through job=k3s-cadvisor.",
            },
        ],
        "complete_message": "K3s host telemetry pipeline completed.",
    },
    "rx-demo-k3s-app-refresh": {
        "supports_undeploy": False,
        "stage_plan": [
            {
                "name": "verify-k3s",
                "transport": "bkc-ssh",
                "kind": "k3s-host-telemetry-verify",
                "action": "k3s.nodes.ready",
                "active": "Verifying kube1 can read the k3s cluster and both nodes are Ready.",
                "complete": "K3s node readiness verified.",
                "timeout": 120,
            },
            {
                "name": "source-check",
                "transport": "ssh-manager",
                "kind": "rx-demo-k3s-source-check",
                "action": "ssh.source.verify",
                "active": "Checking staged rx-demo source on shared storage.",
                "complete": "Staged rx-demo source is present.",
                "timeout": 60,
            },
            {
                "name": "build-rx-ui-image",
                "transport": "ssh-manager",
                "kind": "rx-demo-k3s-build-rx-ui",
                "action": "docker.image.build",
                "active": "Building the rx-ui test image on the swarm manager.",
                "complete": "rx-ui image built and exported to shared storage.",
                "timeout": 1200,
            },
            {
                "name": "import-rx-ui-image",
                "transport": "bkc-ssh",
                "kind": "rx-demo-k3s-import-rx-ui",
                "action": "k3s.image.import",
                "active": "Importing the rx-ui image into kube1 and kube2 containerd stores.",
                "complete": "rx-ui image imported on both k3s nodes.",
                "timeout": 300,
            },
            {
                "name": "apply-lab-overlay",
                "transport": "bkc-ssh",
                "kind": "rx-demo-k3s-apply-lab",
                "action": "k3s.manifest.apply",
                "active": "Applying the rx-demo lab overlay and restarting rx-ui.",
                "complete": "rx-ui rollout completed.",
                "timeout": 420,
            },
            {
                "name": "smoke-ui-routes",
                "transport": "bkc-ssh",
                "kind": "rx-demo-k3s-smoke-ui",
                "action": "http.route.smoke",
                "active": "Smoking /lookup, /approve, and /refill through the deployed UI.",
                "complete": "Routed UI smoke checks passed.",
                "timeout": 180,
            },
            {
                "name": "dashboard-link",
                "transport": "internal",
                "kind": "event-note",
                "active": "Publishing rx-demo dashboard pointers.",
                "complete": "Dashboard pointers published.",
                "message": "Rx UI route smoke checks should now produce distinct /lookup, /approve, and /refill telemetry for Grafana, Tempo, and Dynatrace-style service flow validation.",
            },
        ],
        "complete_message": "Rx demo k3s app refresh pipeline completed.",
    },
}

# Backward-compatible alias while older runs and drafts still reference the cloud-import name.
WORKFLOW_DEFINITIONS["fedora-cloud-import"] = WORKFLOW_DEFINITIONS["fedora-template-deploy"]
WORKFLOW_DEFINITIONS["rx-demo-k3s-redeploy-from-git"] = WORKFLOW_DEFINITIONS["rx-demo-redeploy-from-git-event"]


def workflow_is_supported(workflow: str) -> bool:
    return (workflow or "").strip().lower() in WORKFLOW_DEFINITIONS


def workflow_job_timeout(workflow: str, action_mode: str = "deploy") -> int:
    stage_defs = workflow_stage_definitions(workflow, action_mode=action_mode)
    if not stage_defs:
        return 900
    total = 0
    for stage in stage_defs:
        try:
            total += int(stage.get("timeout", 120))
        except (TypeError, ValueError):
            total += 120
    # Leave room for queue startup, inventory refresh, and slow remote teardown.
    return max(900, total + 600)


def workflow_stage_definitions(workflow: str, action_mode: str = "deploy") -> list[dict]:
    normalized = (workflow or "").strip().lower()
    mode = (action_mode or "deploy").strip().lower() or "deploy"
    config = WORKFLOW_DEFINITIONS.get(normalized, {})
    if not config:
        return []

    if mode == "undeploy" and config.get("supports_undeploy") and config.get("undeploy_stage_plan"):
        return [dict(stage) for stage in config["undeploy_stage_plan"]]

    if config.get("stage_plan"):
        return [dict(stage) for stage in config["stage_plan"]]

    if mode == "undeploy" and config.get("supports_undeploy"):
        return [
            {
                "name": config["undeploy_stage"],
                "transport": "ssh-manager",
                "target": "manager",
                "active": config["undeploy_active"],
                "complete": config["undeploy_complete"],
                "command": config["undeploy_command"],
                "timeout": 120,
            },
            {
                "name": "health-check",
                "transport": "ssh-manager",
                "target": "manager",
                "active": config["absence_active"],
                "complete": config["absence_complete"],
                "command": config["absence_command"],
                "timeout": 60,
            },
            {
                "name": "inventory-refresh",
                "transport": "internal",
                "kind": "inventory-refresh",
                "active": "Refreshing Docker and Ansible inventory snapshots.",
                "complete": "Docker and Ansible inventory refreshed.",
            },
            {
                "name": "dashboard-link",
                "transport": "internal",
                "kind": "event-note",
                "active": "Publishing operator note for removed endpoints.",
                "complete": "Dashboard removal note published.",
                "message": config["removed_dashboard_message"],
            },
        ]

    stage_defs = [
        {
            "name": config["deploy_stage"],
            "transport": "ssh-controller",
            "target": "controller",
            "active": config["deploy_active"],
            "complete": config["deploy_complete"],
            "command": config["deploy_command"],
            "timeout": 240,
        },
        {
            "name": config["health_stage"],
            "transport": "ssh-manager",
            "target": "manager",
            "active": config["health_active"],
            "complete": config["health_complete"],
            "command": config["health_command"],
            "timeout": 90,
        },
    ]
    init_stage = str(config.get("init_stage", "")).strip()
    init_command = str(config.get("init_command", "")).strip()
    if init_stage and init_command:
        stage_defs.append(
            {
                "name": init_stage,
                "transport": "ssh-manager",
                "target": "manager",
                "active": str(config.get("init_active", "Running initialization step.")),
                "complete": str(config.get("init_complete", "Initialization completed.")),
                "command": init_command,
                "timeout": 120,
            }
        )
    stage_defs.extend(
        [
            {
                "name": "inventory-refresh",
                "transport": "internal",
                "kind": "inventory-refresh",
                "active": "Refreshing Docker and Ansible inventory snapshots.",
                "complete": "Docker and Ansible inventory refreshed.",
            },
            {
                "name": "dashboard-link",
                "transport": "internal",
                "kind": "event-note",
                "active": "Publishing dashboard endpoints for operators.",
                "complete": "Dashboard endpoints published.",
                "message": config["dashboard_message"],
            },
        ]
    )
    return stage_defs


def workflow_supports_undeploy(workflow: str) -> bool:
    config = WORKFLOW_DEFINITIONS.get((workflow or "").strip().lower(), {})
    return bool(config.get("supports_undeploy"))


def _command_target(settings: dict[str, str], target: str) -> tuple[str, str, str]:
    normalized = (target or "manager").strip().lower()
    if normalized == "controller":
        return (
            settings["controller_host"],
            settings["controller_user"],
            settings["controller_password"],
        )
    return (
        settings["manager_host"],
        settings["manager_user"],
        settings["manager_password"],
    )


def _store_run_extra(run_id: str, payload: dict) -> None:
    def _apply(candidate: dict) -> None:
        extra = candidate.setdefault("extra", {})
        extra.update(payload)

    update_run(run_id, _apply)


def _fedora_template_source() -> dict:
    return {
        "name": "Fedora Base Template",
        "vendor": "Local Proxmox",
        "release": FEDORA_TEMPLATE_RELEASE,
        "arch": "x86_64",
        "format": "template-clone",
        "hostname": "auzix-fedora-template",
        "vm_name_prefix": "fedora-template",
        "ci_user": "root",
        "bridge": "vmbr0",
    }


def _proxmox_ssh_target(config: dict) -> tuple[str, str, str]:
    username = str(config.get("username", "")).strip() or "root@pam"
    return (
        str(config.get("host", "")).strip(),
        username.split("@", 1)[0] or "root",
        str(config.get("password", "")).strip(),
    )


def _run_proxmox_ssh_command(command: str, *, timeout: int = 120) -> str:
    host, user, password = _proxmox_ssh_target(load_proxmox_config())
    return run_remote_command(
        host=host,
        user=user,
        password=password,
        command=command,
        timeout=timeout,
    )


def _run_auzix_vm134_iso_publish(run_id: str, stage_name: str, settings: dict[str, str]) -> None:
    manager_host, manager_user, manager_password = _command_target(settings, "manager")
    proxmox_host, proxmox_user, proxmox_password = _proxmox_ssh_target(load_proxmox_config())
    source_iso = f"/var/tmp/auzix-vm134-build/artifacts/auzix/{AUZIX_VM134_ISO_NAME}"
    target_iso = f"/var/lib/vz/template/iso/{AUZIX_VM134_ISO_NAME}"

    _set_stage(run_id, stage_name, "active", "Copying VM134 ISO from manager scratch to Proxmox local ISO storage.")
    run_remote_command(
        host=manager_host,
        user=manager_user,
        password=manager_password,
        command=f"test -s {shlex.quote(source_iso)}",
        timeout=60,
    )
    with tempfile.TemporaryDirectory(prefix="bkc-auzix-vm134-") as temp_dir:
        local_iso = str(Path(temp_dir) / AUZIX_VM134_ISO_NAME)
        download_remote_file(
            host=manager_host,
            user=manager_user,
            password=manager_password,
            remote_path=source_iso,
            local_path=local_iso,
            timeout=300,
        )
        _run_proxmox_ssh_command("mkdir -p /var/lib/vz/template/iso", timeout=60)
        upload_remote_file(
            host=proxmox_host,
            user=proxmox_user,
            password=proxmox_password,
            remote_path=target_iso,
            local_path=local_iso,
            mode=0o644,
            timeout=300,
        )

    output = _run_proxmox_ssh_command(
        f"test -s {shlex.quote(target_iso)} && pvesm list local --content iso | "
        f"grep -F {shlex.quote(AUZIX_VM134_ISO_NAME)}",
        timeout=120,
    )
    _set_stage(run_id, stage_name, "complete", "Proxmox local ISO storage has the VM134 install media.")
    append_event(run_id, "info", stage_name, output[-1200:] if output else "vm134 iso published")


def _run_auzix_vm134_target_verify(run_id: str, stage_name: str) -> None:
    command = (
        "set -e; "
        f"cfg=$(qm config {AUZIX_VM134_ID}); "
        "printf \"%s\\n\" \"$cfg\"; "
        "printf \"%s\\n\" \"$cfg\" | grep -F \"name: Auzix\" >/dev/null; "
        "printf \"%s\\n\" \"$cfg\" | grep -F \"scsi0: local-lvm:\" >/dev/null; "
        "disk_gib=$(printf \"%s\\n\" \"$cfg\" | sed -n \"s/.*scsi0: .*size=\\([0-9][0-9]*\\)G.*/\\1/p\" | head -1); "
        f"test -n \"$disk_gib\" && test \"$disk_gib\" -ge {AUZIX_VM134_MIN_DISK_GIB}; "
        f"qm set {AUZIX_VM134_ID} --ide2 local:iso/{AUZIX_VM134_ISO_NAME},media=cdrom; "
        f"qm set {AUZIX_VM134_ID} --boot order=ide2\\;scsi0\\;net0; "
        f"qm config {AUZIX_VM134_ID} | grep -F \"ide2: local:iso/{AUZIX_VM134_ISO_NAME},media=cdrom\" >/dev/null; "
        f"qm config {AUZIX_VM134_ID} | grep -F \"boot: order=ide2;scsi0;net0\" >/dev/null; "
        "echo auzix-vm134-target-ready"
    )
    output = _run_proxmox_ssh_command(command, timeout=120)
    _set_stage(run_id, stage_name, "complete", "VM134 target shape is ready for the live installer handoff.")
    append_event(run_id, "info", stage_name, output[-1200:] if output else "vm134 target ready")


def _run_auzix_vm135_artifact_verify(run_id: str, stage_name: str) -> None:
    command = (
        f"test -s /var/lib/vz/template/iso/{AUZIX_VM134_ISO_NAME} && "
        f"pvesm list local --content iso | grep -F {AUZIX_VM134_ISO_NAME}"
    )
    output = _run_proxmox_ssh_command(command, timeout=120)
    _set_stage(run_id, stage_name, "complete", "AuziX install ISO artifact is ready for VM135.")
    append_event(run_id, "info", stage_name, output[-1200:] if output else "vm134 source iso ready")


def _run_auzix_vm135_iso_publish(run_id: str, stage_name: str) -> None:
    command = (
        "set -e; "
        f"test -s /var/lib/vz/template/iso/{AUZIX_VM134_ISO_NAME}; "
        f"cp -f /var/lib/vz/template/iso/{AUZIX_VM134_ISO_NAME} "
        f"/var/lib/vz/template/iso/{AUZIX_VM135_ISO_NAME}; "
        f"test -s /var/lib/vz/template/iso/{AUZIX_VM135_ISO_NAME}; "
        f"pvesm list local --content iso | grep -F {AUZIX_VM135_ISO_NAME}"
    )
    output = _run_proxmox_ssh_command(command, timeout=420)
    _set_stage(run_id, stage_name, "complete", "Proxmox local ISO storage has the VM135 install media.")
    append_event(run_id, "info", stage_name, output[-1200:] if output else "vm135 iso published")


def _run_auzix_vm135_recreate(run_id: str, stage_name: str) -> None:
    command = (
        "set -e; "
        f"if qm config {AUZIX_VM135_ID} >/dev/null 2>&1; then "
        f"qm status {AUZIX_VM135_ID} | grep -q running && qm stop {AUZIX_VM135_ID} --timeout 30 || true; "
        f"qm destroy {AUZIX_VM135_ID} --purge 1 || qm destroy {AUZIX_VM135_ID}; "
        "fi; "
        f"qm create {AUZIX_VM135_ID} --name {AUZIX_VM135_NAME} --memory 12682 --cores 4 --sockets 2 "
        "--numa 0 --ostype l26 --scsihw virtio-scsi-single "
        "--net0 virtio,bridge=vmbr0,firewall=1; "
        f"qm set {AUZIX_VM135_ID} --scsi0 local-lvm:{AUZIX_VM135_MIN_DISK_GIB},iothread=1; "
        f"qm set {AUZIX_VM135_ID} --ide2 local:iso/{AUZIX_VM135_ISO_NAME},media=cdrom; "
        f"qm set {AUZIX_VM135_ID} --boot order=ide2\\;scsi0\\;net0; "
        f"cfg=$(qm config {AUZIX_VM135_ID}); "
        "printf \"%s\\n\" \"$cfg\"; "
        f"printf \"%s\\n\" \"$cfg\" | grep -F \"name: {AUZIX_VM135_NAME}\" >/dev/null; "
        f"printf \"%s\\n\" \"$cfg\" | grep -F \"ide2: local:iso/{AUZIX_VM135_ISO_NAME},media=cdrom\" >/dev/null; "
        "disk_gib=$(printf \"%s\\n\" \"$cfg\" | sed -n \"s/.*scsi0: .*size=\\([0-9][0-9]*\\)G.*/\\1/p\" | head -1); "
        f"test -n \"$disk_gib\" && test \"$disk_gib\" -ge {AUZIX_VM135_MIN_DISK_GIB}; "
        "echo auzix-vm135-target-ready"
    )
    output = _run_proxmox_ssh_command(command, timeout=180)
    _set_stage(run_id, stage_name, "complete", "VM135 exists with a fresh disk and ISO-first boot order.")
    append_event(run_id, "info", stage_name, output[-1200:] if output else "vm135 target ready")


def _run_auzix_vm135_start(run_id: str, stage_name: str) -> None:
    command = (
        "set -e; "
        f"qm start {AUZIX_VM135_ID}; "
        "sleep 5; "
        f"qm status {AUZIX_VM135_ID} | grep -F \"status: running\"; "
        f"qm config {AUZIX_VM135_ID} | grep -F \"boot: order=ide2;scsi0;net0\" >/dev/null; "
        "echo auzix-vm135-running"
    )
    output = _run_proxmox_ssh_command(command, timeout=120)
    _set_stage(run_id, stage_name, "complete", "VM135 is running from the fresh AuziX install media.")
    append_event(run_id, "info", stage_name, output[-1200:] if output else "vm135 running")


def _fedora_root_password() -> str:
    return str(load_proxmox_config().get("password") or "").strip()


def _apply_cloudinit_config(
    client: ProxmoxClient,
    *,
    node: str,
    vmid: int,
    vm_name: str,
    cloudinit_storage: str,
    ci_user: str,
    public_key: str,
    ipconfig0: str = "ip=dhcp",
    nameserver: str = "192.168.1.10",
    searchdomain: str = "lab.auzietek.com",
) -> dict:
    encoded_public_key = quote(public_key.strip(), safe="")

    def _wait_for_config_task(result: object) -> None:
        upid = str(result or "").strip()
        if upid.startswith("UPID:"):
            task = client.wait_for_task(node, upid, timeout=180)
            exit_status = str(task.get("exitstatus", ""))
            if exit_status and exit_status != "OK":
                raise PipelineExecutionError(f"VM {vmid} config task failed: {exit_status}")

    inherited = client.vm_config(node, vmid)
    inherited_ide2 = str(inherited.get("ide2") or "")
    if inherited_ide2 and "cloudinit" not in inherited_ide2.lower():
        _wait_for_config_task(client.update_vm_config(node, vmid, delete="ide2"))
    _wait_for_config_task(
        client.update_vm_config(
            node,
            vmid,
            boot="order=scsi0",
            ide2=f"{cloudinit_storage}:cloudinit",
            ciuser=ci_user,
            ipconfig0=ipconfig0,
            sshkeys=encoded_public_key,
            agent="enabled=1",
            name=vm_name,
            nameserver=nameserver,
            searchdomain=searchdomain,
        )
    )
    config = client.vm_config(node, vmid)
    ide2 = str(config.get("ide2") or "")
    if "cloudinit" not in ide2.lower():
        raise PipelineExecutionError(f"VM {vmid} cloud-init drive was not attached; ide2 is {ide2!r}.")
    actual_ipconfig = str(config.get("ipconfig0") or "")
    if ipconfig0 and actual_ipconfig != ipconfig0:
        raise PipelineExecutionError(
            f"VM {vmid} cloud-init network config mismatch: expected {ipconfig0!r}, got {actual_ipconfig!r}."
        )
    if str(config.get("ciuser") or "") != ci_user:
        raise PipelineExecutionError(f"VM {vmid} cloud-init user was not applied.")
    return config


def _vm_primary_mac(config: dict) -> str:
    net0 = str(config.get("net0") or "")
    if "=" not in net0:
        return ""
    return net0.split("=", 1)[1].split(",", 1)[0].strip().lower()


def _is_fedora_source_record(record: dict) -> bool:
    name = str(record.get("name") or "").strip().lower()
    try:
        vmid = int(record.get("vmid") or 0)
    except (TypeError, ValueError):
        vmid = 0
    return vmid in FEDORA_SOURCE_VMIDS or name == "fc44-template" or bool(record.get("template"))


def _is_generated_fedora_clone(record: dict) -> bool:
    name = str(record.get("name") or "").strip().lower()
    return bool(re.fullmatch(r"fedora-template-\d+", name)) and not _is_fedora_source_record(record)


def _extract_neighbor_ip(output: str, mac: str) -> str:
    wanted = str(mac or "").strip().lower()
    if not wanted:
        return ""
    for line in str(output or "").splitlines():
        low = line.lower()
        if wanted not in low:
            continue
        parts = line.split()
        if not parts:
            continue
        if parts[0].count(".") == 3:
            return parts[0].strip("()")
        if parts[0].startswith("192.") and parts[0].count(".") == 3:
            return parts[0]
    return ""


def _proxmox_neighbor_ip(mac: str, *, active_prefix: str = "") -> str:
    mac = str(mac or "").strip().lower()
    if not mac:
        return ""
    config = load_proxmox_config()
    proxmox_host, proxmox_user, proxmox_password = _proxmox_ssh_target(config)
    read_cmd = "bash -lc 'cat /proc/net/arp; ip neigh show nud all || true'"
    try:
        output = run_remote_command(
            host=proxmox_host,
            user=proxmox_user,
            password=proxmox_password,
            command=read_cmd,
            timeout=20,
        )
    except Exception:
        output = ""
    found = _extract_neighbor_ip(output, mac)
    if found or not active_prefix:
        return found

    sweep_cmd = (
        "bash -lc '"
        f"prefix={shlex.quote(active_prefix)}; "
        "for i in $(seq 1 254); do ping -c1 -W1 \"$prefix.$i\" >/dev/null 2>&1 & "
        "if [ $((i % 48)) -eq 0 ]; then wait; fi; "
        "done; wait; "
        "cat /proc/net/arp; ip neigh show nud all || true'"
    )
    try:
        output = run_remote_command(
            host=proxmox_host,
            user=proxmox_user,
            password=proxmox_password,
            command=sweep_cmd,
            timeout=90,
        )
    except Exception:
        return ""
    return _extract_neighbor_ip(output, mac)


def _select_storage(client: ProxmoxClient, node: str, preferred: str) -> str:
    storages = client.list_storage(node)
    for entry in storages:
        if str(entry.get("storage", "")).strip() == preferred:
            return preferred
    for entry in storages:
        storage = str(entry.get("storage", "")).strip()
        if storage:
            return storage
    raise PipelineExecutionError(f"No Proxmox storage entries were discovered on node {node}.")


def _select_proxmox_target(client: ProxmoxClient) -> dict:
    nodes = client.nodes()
    if not nodes:
        raise PipelineExecutionError("No Proxmox nodes were discovered.")
    chosen = next((node for node in nodes if str(node.get("status", "")).lower() == "online"), nodes[0])
    node_name = str(chosen.get("node", "")).strip()
    if not node_name:
        raise PipelineExecutionError("Proxmox node metadata is missing a node name.")
    disk_storage = _select_storage(client, node_name, "local-lvm")
    cloudinit_storage = _select_storage(client, node_name, "local-lvm")
    return {
        "node": node_name,
        "disk_storage": disk_storage,
        "cloudinit_storage": cloudinit_storage,
        "bridge": "vmbr0",
    }


def _select_fedora_template() -> dict:
    candidates = []
    try:
        client = ProxmoxClient(load_proxmox_config())
        for node in client.nodes():
            node_name = str(node.get("node", "")).strip()
            if not node_name:
                continue
            for vm in client.list_qemu(node_name):
                record = dict(vm)
                record["node"] = record.get("node", node_name)
                name = str(record.get("name", "")).strip().lower()
                if record.get("template") or "fedora" in name or "fc44" in name or name.startswith("fc"):
                    candidates.append(record)
    except Exception:
        snapshot = load_proxmox_snapshot() or {}
        for template in snapshot.get("templates", []):
            record = dict(template)
            record["template"] = 1
            candidates.append(record)
        for vm in snapshot.get("virtual_machines", []):
            name = str(vm.get("name", "")).strip().lower()
            if vm.get("template") or "fedora" in name or "fc44" in name or name.startswith("fc"):
                candidates.append(dict(vm))

    ranked = []
    running_fedora_sources = []
    for template in candidates:
        name = str(template.get("name", "")).strip().lower()
        if _is_generated_fedora_clone(template):
            continue
        score = 0
        if "fc44" in name:
            score += 4
        if "fedora" in name:
            score += 3
        if "minimal" in name:
            score += 2
        if name.startswith("fc-") or name.startswith("fc"):
            score += 1
        if _is_fedora_source_record(template):
            score += 10
        if not score:
            continue
        if any(token in name for token in ("swarm", "k3s", "docker", "kube")):
            continue
        if str(template.get("status", "")).strip().lower() == "running" and not template.get("template"):
            running_fedora_sources.append(template)
            continue
        ranked.append((score, template))
    if not ranked:
        if running_fedora_sources:
            names = ", ".join(
                f"{item.get('name', 'unnamed')} (vmid {item.get('vmid', 'unknown')})"
                for item in running_fedora_sources
            )
            raise PipelineExecutionError(
                "Fedora-capable source VM is running and was not used as a clone base. "
                f"Stop or convert the source before rerunning: {names}."
            )
        raise PipelineExecutionError(
            "No Fedora-capable Proxmox source was discovered. Refusing to fall back to a generic template because that can clone stale guest identity/network settings. Refresh Proxmox inventory and mark fc44-template or another Fedora 44 VM as the source template."
        )
    preferred = next((template for score, template in ranked if score > 0 and int(template.get("vmid", 0) or 0) == 131), None)
    if preferred is not None:
        return dict(preferred)
    preferred = next((template for score, template in ranked if score > 0 and int(template.get("vmid", 0) or 0) == 115), None)
    if preferred is not None:
        return dict(preferred)
    ranked.sort(
        key=lambda item: (
            item[0],
            1 if item[1].get("template") else 0,
            1 if str(item[1].get("status", "")).strip().lower() != "running" else 0,
            int(item[1].get("vmid", 0) or 0),
            str(item[1].get("name", "")).lower(),
        ),
        reverse=True,
    )
    return dict(ranked[0][1])


def _select_wordpress_template() -> dict:
    snapshot = load_proxmox_snapshot() or {}
    templates = list(snapshot.get("templates", []))
    ranked = []
    for template in templates:
        name = str(template.get("name", "")).strip().lower()
        score = 0
        if "wordpress" in name:
            score += 3
        if "turnkey" in name:
            score += 2
        if "wp" in name:
            score += 1
        if score:
            ranked.append((score, template))
    if not ranked:
        raise PipelineExecutionError(
            "No WordPress-capable Proxmox VM template was discovered. Refresh Proxmox inventory or import a matching template first."
        )
    ranked.sort(key=lambda item: item[0], reverse=True)
    return dict(ranked[0][1])


def _run_fedora_build_kit(run_id: str, settings: dict[str, str], stage: dict) -> None:
    plan = fresh_build_plan(
        hostname="auzix-fedora-workstation.lab.auzietek.com",
        release="44",
        arch="x86_64",
        username="auzieman",
        password="changeme",
        network_mode="dhcp",
        nameserver_host="ns1.lab.auzietek.com",
    )
    package_manifest = {
        "desktop_sessions": ["mate-desktop", "enlightenment"],
        "runtime_goals": [
            "NetworkManager",
            "polkit",
            "gvfs",
            "xdg-utils",
            "openssh-server",
            "qemu-guest-agent",
        ],
        "developer_tools": ["git", "python3", "gcc", "make", "tmux", "vim"],
        "applications": ["firefox", "pluma", "mate-terminal"],
        "notes": "Thin Fedora workstation profile for later full image compose work.",
    }
    plan_json = json.dumps(plan, indent=2)
    manifest_json = json.dumps(package_manifest, indent=2)
    kickstart = str(plan.get("kickstart_content", "")).rstrip() + "\n"
    command = (
        "bash -lc 'mkdir -p /srv/nfs/swarm/auzix-fedora-workstation/artifacts && "
        "python3 - <<\"PY\"\n"
        "from pathlib import Path\n"
        f"Path('/srv/nfs/swarm/auzix-fedora-workstation/artifacts/auzix-fedora-workstation-plan.json').write_text({plan_json!r} + \"\\n\")\n"
        f"Path('/srv/nfs/swarm/auzix-fedora-workstation/artifacts/auzix-fedora-workstation-packages.json').write_text({manifest_json!r} + \"\\n\")\n"
        f"Path('/srv/nfs/swarm/auzix-fedora-workstation/artifacts/auzix-fedora-workstation.ks').write_text({kickstart!r})\n"
        "print('fedora-build-kit-ready')\n"
        "PY'"
    )
    output = run_remote_command(
        host=settings["controller_host"],
        user=settings["controller_user"],
        password=settings["controller_password"],
        command=command,
        timeout=int(stage.get("timeout", 180)),
    )
    _store_run_extra(
        run_id,
        {
            "fedora_plan_path": "/srv/nfs/swarm/auzix-fedora-workstation/artifacts/auzix-fedora-workstation-plan.json",
            "fedora_kickstart_path": "/srv/nfs/swarm/auzix-fedora-workstation/artifacts/auzix-fedora-workstation.ks",
            "fedora_manifest_path": "/srv/nfs/swarm/auzix-fedora-workstation/artifacts/auzix-fedora-workstation-packages.json",
        },
    )
    _set_stage(run_id, str(stage["name"]), "complete", str(stage.get("complete", "Stage completed.")))
    append_event(run_id, "info", str(stage["name"]), output or "fedora-build-kit-ready")


def _run_wordpress_clone(run_id: str, stage_name: str) -> None:
    template = _select_wordpress_template()
    client = ProxmoxClient(load_proxmox_config())
    source_node = str(template.get("node", "")).strip()
    source_vmid = int(template.get("vmid"))
    new_vmid = int(client.next_vmid())
    target_name = f"wordpress-{new_vmid}"
    upid = client.clone_vm(
        node=source_node,
        source_vmid=source_vmid,
        new_vmid=new_vmid,
        name=target_name,
        full=True,
    )
    task = client.wait_for_task(source_node, str(upid), timeout=300)
    exit_status = str(task.get("exitstatus", ""))
    if exit_status and exit_status != "OK":
        raise PipelineExecutionError(f"Proxmox clone failed for {target_name}: {exit_status}")
    _store_run_extra(
        run_id,
        {
            "selected_template": template,
            "wordpress_clone_node": source_node,
            "wordpress_clone_vmid": new_vmid,
            "wordpress_clone_name": target_name,
            "wordpress_clone_upid": str(upid),
        },
    )
    _set_stage(run_id, stage_name, "complete", "Proxmox clone completed for the WordPress appliance lane.")
    append_event(run_id, "info", stage_name, f"Cloned {template.get('name')} to VMID {new_vmid} as {target_name}.")


def _run_wordpress_start(run_id: str, stage_name: str) -> None:
    run = get_run(run_id) or {}
    extra = run.get("extra", {})
    node = str(extra.get("wordpress_clone_node", "")).strip()
    vmid = int(extra.get("wordpress_clone_vmid", 0))
    name = str(extra.get("wordpress_clone_name", "")).strip() or f"vm-{vmid}"
    if not node or not vmid:
        raise PipelineExecutionError("WordPress appliance clone metadata is missing. Re-run the lane from the beginning.")
    client = ProxmoxClient(load_proxmox_config())
    upid = client.start_vm(node, vmid)
    _store_run_extra(run_id, {"wordpress_start_upid": str(upid)})
    _set_stage(run_id, stage_name, "complete", "WordPress appliance VM start requested successfully.")
    append_event(run_id, "info", stage_name, f"Start requested for {name} on {node} (vmid {vmid}).")


def _run_fedora_template_select(run_id: str, stage_name: str) -> None:
    client = ProxmoxClient(load_proxmox_config())
    source = _fedora_template_source()
    target = _select_proxmox_target(client)
    template = _select_fedora_template()
    _store_run_extra(run_id, {"fedora_template_source": source, "fedora_template_target": target, "fedora_template": template})
    _set_stage(run_id, stage_name, "complete", "Local Fedora template and Proxmox target selected.")
    append_event(
        run_id,
        "info",
        stage_name,
        f"Selected template {template.get('name')} on {template.get('node')} (vmid {template.get('vmid')}) for node {target['node']}.",
    )


def _run_fedora_template_clone(run_id: str, stage_name: str) -> None:
    config = load_proxmox_config()
    client = ProxmoxClient(config)
    run = get_run(run_id) or {}
    extra = run.get("extra", {})
    source = dict(extra.get("fedora_template_source") or _fedora_template_source())
    target = dict(extra.get("fedora_template_target") or _select_proxmox_target(client))
    template = dict(extra.get("fedora_template") or _select_fedora_template())
    source_node = str(template.get("node", "")).strip()
    source_vmid = int(template.get("vmid", 0))
    if not source_node or not source_vmid:
        raise PipelineExecutionError("Fedora template metadata is incomplete. Refresh Proxmox inventory and try again.")
    new_vmid = int(client.next_vmid())
    vm_name = f"{source.get('vm_name_prefix', 'fedora-template')}-{new_vmid}"
    upid = client.clone_vm(
        node=source_node,
        source_vmid=source_vmid,
        new_vmid=new_vmid,
        name=vm_name,
        full=True,
    )
    task = client.wait_for_task(source_node, str(upid), timeout=2400)
    exit_status = str(task.get("exitstatus", ""))
    if exit_status and exit_status != "OK":
        raise PipelineExecutionError(f"Proxmox clone failed for {vm_name}: {exit_status}")
    _store_run_extra(
        run_id,
        {
            "fedora_template_vmid": new_vmid,
            "fedora_template_vm_name": vm_name,
            "fedora_template_node": source_node,
            "fedora_template_disk_storage": target["disk_storage"],
            "fedora_template_cloudinit_storage": target["cloudinit_storage"],
            "fedora_template_clone_upid": str(upid),
        },
    )
    _set_stage(run_id, stage_name, "complete", "Fedora template cloned in Proxmox.")
    append_event(run_id, "info", stage_name, f"Cloned {template.get('name')} to VMID {new_vmid} as {vm_name}.")


def _run_fedora_template_configure(run_id: str, stage_name: str) -> None:
    config = load_proxmox_config()
    run = get_run(run_id) or {}
    extra = run.get("extra", {})
    vmid = int(extra.get("fedora_template_vmid", 0))
    node = str(extra.get("fedora_template_node", "")).strip()
    cloudinit_storage = str(extra.get("fedora_template_cloudinit_storage", "")).strip()
    source = dict(extra.get("fedora_template_source") or _fedora_template_source())
    if not vmid or not node or not cloudinit_storage:
        raise PipelineExecutionError("Fedora template metadata is incomplete. Re-run the lane from the beginning.")

    ssh = load_integrations()["ssh"]
    key_info = read_key_pair(ssh["private_key_path"], ssh["public_key_path"])
    public_key = str(key_info.get("public_key", "")).strip()
    if not public_key:
        raise PipelineExecutionError("BKC SSH public key is missing. Generate or install it before cloning Fedora guests.")

    vm_name = str(extra.get("fedora_template_vm_name", f"fedora-template-{vmid}")).strip()
    client = ProxmoxClient(config)
    applied = _apply_cloudinit_config(
        client,
        node=node,
        vmid=vmid,
        vm_name=vm_name,
        cloudinit_storage=cloudinit_storage,
        ci_user=str(source.get("ci_user", "root")),
        public_key=public_key,
        ipconfig0="ip=dhcp",
    )
    _store_run_extra(run_id, {"fedora_template_mac": _vm_primary_mac(applied)})
    _set_stage(run_id, stage_name, "complete", "Fedora template clone configured for first boot.")
    append_event(
        run_id,
        "info",
        stage_name,
        f"Configured Fedora template clone {vm_name} (vmid {vmid}) with {applied.get('ide2')} and {applied.get('ipconfig0')}.",
    )


def _run_fedora_template_start(run_id: str, stage_name: str) -> None:
    config = load_proxmox_config()
    client = ProxmoxClient(config)
    run = get_run(run_id) or {}
    extra = run.get("extra", {})
    node = str(extra.get("fedora_template_node", "")).strip()
    vmid = int(extra.get("fedora_template_vmid", 0))
    name = str(extra.get("fedora_template_vm_name", "")).strip() or f"fedora-template-{vmid}"
    if not node or not vmid:
        raise PipelineExecutionError("Fedora template VM metadata is missing. Re-run the lane from the beginning.")
    upid = client.start_vm(node, vmid)
    task = client.wait_for_task(node, str(upid), timeout=120)
    exit_status = str(task.get("exitstatus", ""))
    if exit_status and exit_status != "OK":
        raise PipelineExecutionError(f"Proxmox start failed for {name}: {exit_status}")
    status = client.wait_for_vm_status(node, vmid, "running", timeout=120)
    _store_run_extra(
        run_id,
        {
            "fedora_template_start_upid": str(upid),
            "fedora_template_runtime_status": str(status.get("status", "")),
            "fedora_template_pid": status.get("pid"),
        },
    )
    _set_stage(run_id, stage_name, "complete", "Fedora template clone is running in Proxmox.")
    append_event(run_id, "info", stage_name, f"{name} reached running state on {node} (vmid {vmid}).")


def _dns_or_blank(value: str) -> str:
    target = str(value or "").strip()
    if not target:
        return ""
    try:
        return socket.gethostbyname(target)
    except OSError:
        return ""


def _route_is_ready(value: str) -> bool:
    target = str(value or "").strip()
    if not target:
        return False
    try:
        ipaddress.ip_address(target)
        return True
    except ValueError:
        return bool(_dns_or_blank(target))


def _cosmic_candidate_from_route(name: str, route: str, *, vmid: int = 0, proxmox_node: str = "", source: str = "") -> dict:
    route = str(route or "").strip()
    ip = _dns_or_blank(route) or route
    return {
        "name": str(name or route or "fedora-cosmic-target").strip(),
        "host": ip,
        "route": route or ip,
        "vmid": int(vmid or 0),
        "proxmox_node": str(proxmox_node or "").strip(),
        "source": source or "manual",
        "route_ready": _route_is_ready(route or ip),
    }


def _cosmic_route_from_vmid(client: ProxmoxClient, node: str, vmid: int, fallback: str = "") -> tuple[str, str]:
    if not node or not vmid:
        return fallback, ""
    try:
        config = client.vm_config(node, vmid)
    except Exception:
        return fallback, ""
    mac = _vm_primary_mac(config)
    ip = _proxmox_neighbor_ip(mac, active_prefix="192.168.1") if mac else ""
    return ip or fallback, mac


def _cosmic_target_from_run_extra(extra: dict) -> dict | None:
    target_host = str(extra.get("target_host") or extra.get("target_ip") or "").strip()
    target_name = str(extra.get("target_name") or extra.get("hostname") or "").strip()
    if target_host:
        return _cosmic_candidate_from_route(
            target_name or target_host,
            target_host,
            vmid=int(extra.get("target_vmid") or 0),
            proxmox_node=str(extra.get("target_node") or ""),
            source="run metadata",
        )
    return None


def _cosmic_candidates_from_runs() -> list[dict]:
    candidates = []
    client = None
    for run in load_runs()[:20]:
        workflow = str(run.get("workflow") or "").strip().lower()
        extra = run.get("extra", {}) or {}
        if workflow not in {"fedora-template-deploy", "fedora-cloud-import"}:
            continue
        name = str(extra.get("fedora_template_vm_name") or "").strip()
        vmid = int(extra.get("fedora_template_vmid") or 0)
        node = str(extra.get("fedora_template_node") or "").strip()
        route = str(extra.get("fedora_template_ip") or extra.get("target_host") or name).strip()
        if not name and not route:
            continue
        mac = str(extra.get("fedora_template_mac") or "").strip()
        if vmid and node:
            if client is None:
                client = ProxmoxClient(load_proxmox_config())
            route, discovered_mac = _cosmic_route_from_vmid(client, node, vmid, route)
            mac = mac or discovered_mac
        candidate = _cosmic_candidate_from_route(
            name or route,
            route,
            vmid=vmid,
            proxmox_node=node,
            source="recent fedora pipeline",
        )
        if mac:
            candidate["mac"] = mac
        candidates.append(candidate)
    return candidates


def _cosmic_candidates_from_rules() -> list[dict]:
    rules = load_rules()
    candidates = []
    for group_name in sorted(rules.get("groups", {})):
        for host_name, node_data, resolved in resolve_group_hosts(rules, group_name):
            name_lc = str(host_name).lower()
            config_lc = str(resolved.get("configuration") or node_data.get("configuration") or "").lower()
            if not any(token in name_lc or token in config_lc for token in ("fedora", "fc44", "cosmic")):
                continue
            route = (
                resolved.get("host")
                or resolved.get("ip")
                or resolved.get("fqdn")
                or resolved.get("hostname")
                or node_data.get("host")
                or node_data.get("ip")
                or host_name
            )
            candidates.append(
                _cosmic_candidate_from_route(
                    host_name,
                    str(route),
                    vmid=int(resolved.get("vmid") or node_data.get("vmid") or 0),
                    proxmox_node=str(resolved.get("proxmox_node") or node_data.get("proxmox_node") or ""),
                    source=f"rules:{group_name}",
                )
            )
    return candidates


def _cosmic_candidates_from_proxmox_snapshot() -> list[dict]:
    snapshot = load_proxmox_snapshot() or {}
    candidates = []
    for vm in snapshot.get("virtual_machines", []):
        name = str(vm.get("name") or "").strip()
        name_lc = name.lower()
        if _is_fedora_source_record(vm):
            continue
        if vm.get("template") or not any(token in name_lc for token in ("fedora", "fc44")):
            continue
        status = str(vm.get("status") or "").strip().lower()
        if status and status != "running":
            continue
        route = str(vm.get("ip") or vm.get("host") or vm.get("fqdn") or name).strip()
        candidates.append(
            _cosmic_candidate_from_route(
                name,
                route,
                vmid=int(vm.get("vmid") or 0),
                proxmox_node=str(vm.get("node") or ""),
                source="proxmox snapshot",
            )
        )
    return candidates


def _cosmic_candidates_from_proxmox_api() -> list[dict]:
    candidates = []
    try:
        client = ProxmoxClient(load_proxmox_config())
        for node in client.nodes():
            node_name = str(node.get("node", "")).strip()
            if not node_name:
                continue
            for vm in client.list_qemu(node_name):
                name = str(vm.get("name") or "").strip()
                name_lc = name.lower()
                if _is_fedora_source_record(vm):
                    continue
                if vm.get("template") or str(vm.get("status", "")).lower() != "running":
                    continue
                if not any(token in name_lc for token in ("fedora", "fc44")):
                    continue
                vmid = int(vm.get("vmid") or 0)
                route, mac = _cosmic_route_from_vmid(client, node_name, vmid, name)
                candidates.append(
                    _cosmic_candidate_from_route(
                        name,
                        route,
                        vmid=vmid,
                        proxmox_node=node_name,
                        source="proxmox api",
                    )
                )
                if mac:
                    candidates[-1]["mac"] = mac
    except Exception as exc:  # noqa: BLE001
        candidates.append({"error": str(exc), "source": "proxmox api"})
    return candidates


def _select_cosmic_target(run_id: str) -> dict:
    run = get_run(run_id) or {}
    extra = run.get("extra", {}) or {}
    explicit = _cosmic_target_from_run_extra(extra)
    candidates = []
    if explicit:
        candidates.append(explicit)
    candidates.extend(_cosmic_candidates_from_rules())
    candidates.extend(_cosmic_candidates_from_runs())
    candidates.extend(_cosmic_candidates_from_proxmox_snapshot())
    if not any(candidate.get("route_ready") for candidate in candidates):
        candidates.extend(_cosmic_candidates_from_proxmox_api())

    seen = set()
    usable = []
    errors = []
    for candidate in candidates:
        if candidate.get("error"):
            errors.append(f"{candidate.get('source')}: {candidate.get('error')}")
            continue
        host = str(candidate.get("host") or "").strip()
        if not host:
            continue
        if not candidate.get("route_ready") and str(candidate.get("source")) != "run metadata":
            continue
        key = (host, int(candidate.get("vmid") or 0), str(candidate.get("name") or ""))
        if key in seen:
            continue
        seen.add(key)
        usable.append(candidate)
    if not usable:
        detail = "; ".join(errors)
        raise PipelineExecutionError(
            "No reachable Fedora clone target was discovered for COSMIC post-install. "
            "Refresh inventory or create the run with target_host/target_ip metadata."
            + (f" Proxmox lookup detail: {detail}" if detail else "")
        )

    usable.sort(
        key=lambda item: (
            1 if str(item.get("source", "")).startswith("run metadata") else 0,
            1 if item.get("route_ready") else 0,
            1 if str(item.get("source", "")).startswith("rules") else 0,
            int(item.get("vmid") or 0),
            str(item.get("name") or ""),
        ),
        reverse=True,
    )
    return usable[0]


def _cosmic_target(run_id: str) -> dict:
    run = get_run(run_id) or {}
    target = dict((run.get("extra", {}) or {}).get("cosmic_target") or {})
    if not target:
        raise PipelineExecutionError("COSMIC target metadata is missing. Re-run target-select.")
    return target


def _cosmic_ssh(run_id: str, command: str, timeout: int = 120) -> str:
    target = _cosmic_target(run_id)
    password = str(target.get("password") or target.get("ssh_password") or _fedora_root_password()).strip()
    return run_remote_command(host=str(target["host"]), user="root", password=password, command=command, timeout=timeout)


def _run_cosmic_target_select(run_id: str, stage_name: str) -> None:
    target = _select_cosmic_target(run_id)
    _store_run_extra(run_id, {"cosmic_target": target})
    _set_stage(run_id, stage_name, "complete", "Fedora COSMIC target selected.")
    append_event(
        run_id,
        "info",
        stage_name,
        f"Selected {target.get('name')} at {target.get('host')} from {target.get('source')} (vmid {target.get('vmid') or 'unknown'}).",
    )


def _run_cosmic_wait_ssh(run_id: str, stage_name: str) -> None:
    deadline = time.monotonic() + 900
    last_error = ""
    while time.monotonic() < deadline:
        try:
            output = _cosmic_ssh(run_id, "bash -lc 'hostname; id -u; test -d /etc/dnf || test -d /usr/lib/sysimage/rpm'", timeout=20)
            _set_stage(run_id, stage_name, "complete", "Fedora target is reachable over SSH.")
            append_event(run_id, "info", stage_name, output or "ssh-ready")
            return
        except Exception as exc:  # noqa: BLE001
            last_error = str(exc)
            time.sleep(10)
    raise PipelineExecutionError(f"Fedora target SSH did not become ready before timeout: {last_error}")


def _run_cosmic_package_plan(run_id: str, stage_name: str) -> None:
    plan = {
        "group": "@cosmic-desktop-environment",
        "fallback_packages": [
            "cosmic-session",
            "cosmic-greeter",
            "cosmic-settings",
            "cosmic-terminal",
            "NetworkManager",
            "qemu-guest-agent",
        ],
        "display_manager": "cosmic-greeter",
        "reboot_policy": "single reboot after graphical target is enabled",
    }
    _store_run_extra(run_id, {"cosmic_package_plan": plan})
    _set_stage(run_id, stage_name, "complete", "COSMIC package plan prepared.")
    append_event(run_id, "info", stage_name, json.dumps(plan, sort_keys=True))


def _run_cosmic_desktop_install(run_id: str, stage_name: str) -> None:
    command = (
        "bash -lc 'set -euo pipefail; "
        "dnf -y makecache; "
        "if rpm -q cosmic-session >/dev/null 2>&1 && rpm -q cosmic-greeter >/dev/null 2>&1; then "
        "echo cosmic-packages-present; exit 0; "
        "fi; "
        "dnf -y install @cosmic-desktop-environment || "
        "dnf -y group install cosmic-desktop-environment || "
        "dnf -y install cosmic-session cosmic-greeter cosmic-settings cosmic-terminal NetworkManager qemu-guest-agent; "
        "dnf -y install qemu-guest-agent openssh-server; "
        "echo cosmic-packages-installed'"
    )
    output = _cosmic_ssh(run_id, command, timeout=5400)
    _set_stage(run_id, stage_name, "complete", "COSMIC Desktop packages installed.")
    append_event(run_id, "info", stage_name, output[-1200:] if output else "cosmic-packages-installed")


def _run_cosmic_graphical_enable(run_id: str, stage_name: str) -> None:
    command = (
        "bash -lc 'set -euo pipefail; "
        "systemctl enable --now NetworkManager || true; "
        "systemctl enable --now qemu-guest-agent || true; "
        "systemctl set-default graphical.target; "
        "if systemctl list-unit-files cosmic-greeter.service --no-legend 2>/dev/null | grep -q \"^cosmic-greeter.service\"; then "
        "systemctl enable cosmic-greeter.service; "
        "ln -sf /usr/lib/systemd/system/cosmic-greeter.service /etc/systemd/system/display-manager.service; "
        "elif systemctl list-unit-files gdm.service --no-legend 2>/dev/null | grep -q \"^gdm.service\"; then "
        "systemctl enable gdm.service; "
        "ln -sf /usr/lib/systemd/system/gdm.service /etc/systemd/system/display-manager.service; "
        "fi; "
        "systemctl daemon-reload; "
        "systemctl get-default; "
        "echo graphical-enabled'"
    )
    output = _cosmic_ssh(run_id, command, timeout=300)
    _set_stage(run_id, stage_name, "complete", "Graphical boot and display manager enabled.")
    append_event(run_id, "info", stage_name, output[-800:] if output else "graphical-enabled")


def _run_cosmic_reboot(run_id: str, stage_name: str) -> None:
    command = "bash -lc 'nohup sh -c \"sleep 2; systemctl reboot\" >/dev/null 2>&1 & echo reboot-requested'"
    output = _cosmic_ssh(run_id, command, timeout=60)
    _set_stage(run_id, stage_name, "complete", "Fedora target reboot requested.")
    append_event(run_id, "info", stage_name, output or "reboot-requested")


def _run_cosmic_gui_validate(run_id: str, stage_name: str) -> None:
    time.sleep(20)
    deadline = time.monotonic() + 1200
    last_error = ""
    command = (
        "bash -lc 'set -euo pipefail; "
        "test \"$(systemctl get-default)\" = graphical.target; "
        "if systemctl is-active --quiet display-manager; then dm=display-manager; "
        "elif systemctl is-active --quiet cosmic-greeter; then dm=cosmic-greeter; "
        "elif systemctl is-active --quiet gdm; then dm=gdm; "
        "else systemctl --no-pager --failed; exit 1; fi; "
        "printf \"graphical.target %s active\\n\" \"$dm\"'"
    )
    while time.monotonic() < deadline:
        try:
            output = _cosmic_ssh(run_id, command, timeout=30)
            _store_run_extra(run_id, {"cosmic_gui_status": output})
            _set_stage(run_id, stage_name, "complete", "Fedora COSMIC GUI target is online.")
            append_event(run_id, "info", stage_name, output or "cosmic-gui-online")
            return
        except Exception as exc:  # noqa: BLE001
            last_error = str(exc)
            time.sleep(15)
    raise PipelineExecutionError(f"COSMIC GUI validation did not pass before timeout: {last_error}")


def _run_cosmic_register_resource(run_id: str, stage_name: str) -> None:
    target = _cosmic_target(run_id)
    rules = load_rules()
    group = rules.setdefault("groups", {}).setdefault("fedora-cosmic", {"locals": {}, "nodes": {}})
    group.setdefault("locals", {}).update(
        {
            "provider": "proxmox",
            "resource_kind": "group",
            "configuration": "fedora-cosmic",
            "workflow": "fedora-cosmic-postinstall",
        }
    )
    ssh = load_integrations()["ssh"]
    name = str(target.get("name") or target.get("host") or "fedora-cosmic").strip()
    group.setdefault("nodes", {})[name] = {
        "provider": "proxmox",
        "resource_kind": "vm",
        "configuration": "fedora-cosmic",
        "desktop": "COSMIC",
        "vmid": int(target.get("vmid") or 0),
        "proxmox_node": str(target.get("proxmox_node") or ""),
        "host": str(target.get("host") or ""),
        "user": "root",
        "port": 22,
        "private_key": str(ssh.get("private_key_path") or ""),
        "state": "gui-online",
    }
    reconcile = reconcile_rules_inventory(rules)
    save_rules(rules)
    _set_stage(run_id, stage_name, "complete", "COSMIC desktop state registered in inventory.")
    append_event(run_id, "info", stage_name, json.dumps({"target": name, "reconcile": reconcile}, sort_keys=True))


def _k3s_plan() -> dict:
    return {
        "cluster_name": K3S_CLUSTER_NAME,
        "api_url": "https://kube1.lab.auzietek.com:6443",
        "ci_user": "root",
        "bridge": "vmbr0",
        "network_prefix": 24,
        "gateway": "192.168.1.1",
        "nameserver": "192.168.1.10",
        "searchdomain": "lab.auzietek.com",
        "nodes": [dict(node) for node in K3S_NODE_PLAN],
    }


def _run_k3s_source_select(run_id: str, stage_name: str) -> None:
    client = ProxmoxClient(load_proxmox_config())
    target = _select_proxmox_target(client)
    template = _select_fedora_template()
    plan = _k3s_plan()
    _store_run_extra(run_id, {"k3s_plan": plan, "k3s_template": template, "k3s_target": target})
    _set_stage(run_id, stage_name, "complete", "Fedora source and Proxmox target selected for k3s.")
    append_event(
        run_id,
        "info",
        stage_name,
        f"Selected {template.get('name')} on {template.get('node')} (vmid {template.get('vmid')}) for {plan['cluster_name']}.",
    )


def _run_k3s_clone_plan(run_id: str, stage_name: str) -> None:
    run = get_run(run_id) or {}
    extra = run.get("extra", {})
    plan = dict(extra.get("k3s_plan") or _k3s_plan())
    target = dict(extra.get("k3s_target") or _select_proxmox_target(ProxmoxClient(load_proxmox_config())))
    clone_plan = []
    for node in plan.get("nodes", []):
        name = str(node.get("name", "")).strip()
        expected_ip = ""
        try:
            expected_ip = socket.gethostbyname(name)
        except OSError:
            expected_ip = ""
        ipconfig0 = "ip=dhcp"
        if expected_ip:
            ipconfig0 = f"ip={expected_ip}/{int(plan.get('network_prefix') or 24)},gw={plan.get('gateway') or '192.168.1.1'}"
        clone_plan.append(
            {
                "name": name,
                "short": str(node.get("short", "")).strip(),
                "role": str(node.get("role", "")).strip(),
                "expected_ip": expected_ip,
                "cloudinit": {"ci_user": plan.get("ci_user", "root"), "ipconfig0": ipconfig0},
            }
        )
    _store_run_extra(run_id, {"k3s_plan": plan, "k3s_target": target, "k3s_clone_plan": clone_plan})
    _set_stage(run_id, stage_name, "complete", "K3s clone plan prepared.")
    append_event(run_id, "info", stage_name, json.dumps({"cluster": plan["cluster_name"], "nodes": clone_plan}, sort_keys=True))


def _k3s_configure_vm(
    *,
    proxmox_config: dict,
    node: str,
    vmid: int,
    vm_name: str,
    cloudinit_storage: str,
    public_key: str,
    ci_user: str,
    ipconfig0: str,
    nameserver: str,
    searchdomain: str,
) -> str:
    client = ProxmoxClient(proxmox_config)
    applied = _apply_cloudinit_config(
        client,
        node=node,
        vmid=vmid,
        vm_name=vm_name,
        cloudinit_storage=cloudinit_storage,
        ci_user=ci_user,
        public_key=public_key,
        ipconfig0=ipconfig0,
        nameserver=nameserver,
        searchdomain=searchdomain,
    )
    return f"k3s-vm-configured {vm_name} ide2={applied.get('ide2')} ipconfig0={applied.get('ipconfig0')}"


def _run_k3s_proxmox_clone(run_id: str, stage_name: str) -> None:
    proxmox_config = load_proxmox_config()
    client = ProxmoxClient(proxmox_config)
    run = get_run(run_id) or {}
    extra = run.get("extra", {})
    plan = dict(extra.get("k3s_plan") or _k3s_plan())
    target = dict(extra.get("k3s_target") or _select_proxmox_target(client))
    template = dict(extra.get("k3s_template") or _select_fedora_template())
    clone_plan = list(extra.get("k3s_clone_plan") or _k3s_plan()["nodes"])
    source_node = str(template.get("node", "")).strip()
    source_vmid = int(template.get("vmid", 0))
    if not source_node or not source_vmid:
        raise PipelineExecutionError("K3s Fedora source metadata is incomplete. Re-run source selection.")

    ssh = load_integrations()["ssh"]
    key_info = read_key_pair(ssh["private_key_path"], ssh["public_key_path"])
    public_key = str(key_info.get("public_key", "")).strip()
    if not public_key:
        raise PipelineExecutionError("BKC SSH public key is missing. Generate or install it before cloning k3s guests.")

    cloned = []
    for node in clone_plan:
        vm_name = str(node.get("name", "")).strip()
        role = str(node.get("role", "")).strip()
        if not vm_name or role not in {"server", "agent"}:
            raise PipelineExecutionError(f"Invalid k3s node plan entry: {node!r}")
        new_vmid = int(client.next_vmid())
        upid = client.clone_vm(
            node=source_node,
            source_vmid=source_vmid,
            new_vmid=new_vmid,
            name=vm_name,
            full=True,
        )
        task = client.wait_for_task(source_node, str(upid), timeout=2400)
        exit_status = str(task.get("exitstatus", ""))
        if exit_status and exit_status != "OK":
            raise PipelineExecutionError(f"Proxmox clone failed for {vm_name}: {exit_status}")
        _k3s_configure_vm(
            proxmox_config=proxmox_config,
            node=source_node,
            vmid=new_vmid,
            vm_name=vm_name,
            cloudinit_storage=str(target["cloudinit_storage"]),
            public_key=public_key,
            ci_user=str((node.get("cloudinit") or {}).get("ci_user") or "root"),
            ipconfig0=str((node.get("cloudinit") or {}).get("ipconfig0") or "ip=dhcp"),
            nameserver=str(plan.get("nameserver") or "192.168.1.10"),
            searchdomain=str(plan.get("searchdomain") or "lab.auzietek.com"),
        )
        vm_config = client.vm_config(source_node, new_vmid)
        cloned.append(
            {
                **dict(node),
                "vmid": new_vmid,
                "proxmox_node": source_node,
                "clone_upid": str(upid),
                "mac": _vm_primary_mac(vm_config),
            }
        )

    _store_run_extra(run_id, {"k3s_nodes": cloned})
    _set_stage(run_id, stage_name, "complete", "K3s Fedora guests cloned and configured.")
    append_event(run_id, "info", stage_name, json.dumps({"cloned": cloned}, sort_keys=True))


def _k3s_nodes(run_id: str) -> list[dict]:
    run = get_run(run_id) or {}
    nodes = list((run.get("extra", {}) or {}).get("k3s_nodes") or [])
    if not nodes:
        raise PipelineExecutionError("K3s node metadata is missing. Re-run the lane from clone planning.")
    return [dict(node) for node in nodes]


def _run_k3s_proxmox_start(run_id: str, stage_name: str) -> None:
    client = ProxmoxClient(load_proxmox_config())
    started = []
    for node in _k3s_nodes(run_id):
        proxmox_node = str(node.get("proxmox_node", "")).strip()
        vmid = int(node.get("vmid", 0))
        name = str(node.get("name", "")).strip()
        if not proxmox_node or not vmid:
            raise PipelineExecutionError(f"K3s node Proxmox metadata is incomplete for {name or node!r}.")
        upid = client.start_vm(proxmox_node, vmid)
        task = client.wait_for_task(proxmox_node, str(upid), timeout=120)
        exit_status = str(task.get("exitstatus", ""))
        if exit_status and exit_status != "OK":
            raise PipelineExecutionError(f"Proxmox start failed for {name}: {exit_status}")
        status = client.wait_for_vm_status(proxmox_node, vmid, "running", timeout=120)
        started.append({**node, "start_upid": str(upid), "status": status.get("status")})
    _store_run_extra(run_id, {"k3s_nodes": started})
    _set_stage(run_id, stage_name, "complete", "K3s Fedora guests are running.")
    append_event(run_id, "info", stage_name, json.dumps({"started": started}, sort_keys=True))


def _k3s_ssh_command(node: dict, command: str, timeout: int = 120) -> str:
    host = str(node.get("ip") or node.get("name") or "").strip()
    if not host:
        raise PipelineExecutionError(f"K3s node SSH target is missing: {node!r}")
    password = str(node.get("password") or node.get("ssh_password") or _fedora_root_password()).strip()
    return run_remote_command(host=host, user="root", password=password, command=command, timeout=timeout)


def _run_k3s_discover_ssh(run_id: str, stage_name: str) -> None:
    deadline = time.monotonic() + 900
    resolved = []
    for node in _k3s_nodes(run_id):
        name = str(node.get("name", "")).strip()
        expected_ip = str(node.get("expected_ip") or "").strip()
        mac = str(node.get("mac") or "").strip()
        ip = ""
        ready = False
        last_error = ""
        while time.monotonic() < deadline:
            candidates = []
            try:
                candidates.append(socket.gethostbyname(name))
            except Exception as exc:  # noqa: BLE001
                last_error = str(exc)
            if expected_ip:
                candidates.append(expected_ip)
            if mac:
                arp_ip = _proxmox_neighbor_ip(mac)
                if not arp_ip and time.monotonic() + 90 < deadline:
                    prefix = ".".join((expected_ip or "192.168.1.0").split(".")[:3])
                    arp_ip = _proxmox_neighbor_ip(mac, active_prefix=prefix)
                if arp_ip:
                    candidates.insert(0, arp_ip)
            for candidate in dict.fromkeys(item for item in candidates if item):
                try:
                    probe_node = {**node, "ip": candidate}
                    _k3s_ssh_command(probe_node, "true", timeout=20)
                    ip = candidate
                    ready = True
                    break
                except Exception as exc:  # noqa: BLE001
                    last_error = str(exc)
            if ready:
                break
            time.sleep(10)
        if not ready:
            raise PipelineExecutionError(f"SSH did not become ready for {name} before timeout: {last_error}")
        resolved.append({**node, "ip": ip})
    _store_run_extra(run_id, {"k3s_nodes": resolved})
    _set_stage(run_id, stage_name, "complete", "K3s guests are reachable over SSH.")
    append_event(run_id, "info", stage_name, json.dumps({"ssh_ready": resolved}, sort_keys=True))


def _run_k3s_base_bootstrap(run_id: str, stage_name: str) -> None:
    outputs = []
    ssh = load_integrations()["ssh"]
    key_info = read_key_pair(ssh["private_key_path"], ssh["public_key_path"])
    public_key = str(key_info.get("public_key") or "").strip()
    for node in _k3s_nodes(run_id):
        fqdn = str(node.get("name", "")).strip()
        key_install = ""
        if public_key:
            quoted_key = shlex.quote(public_key)
            key_install = (
                "mkdir -p /root/.ssh; chmod 700 /root/.ssh; "
                f"grep -qxF {quoted_key} /root/.ssh/authorized_keys 2>/dev/null || "
                f"printf '%s\\n' {quoted_key} >> /root/.ssh/authorized_keys; "
                "chmod 600 /root/.ssh/authorized_keys; "
            )
        command = (
            "bash -lc 'set -euo pipefail; "
            f"hostnamectl set-hostname {shlex.quote(fqdn)}; "
            f"{key_install}"
            "dnf -y install curl jq tar iptables-nft container-selinux qemu-guest-agent; "
            "systemctl enable --now qemu-guest-agent || true; "
            "swapoff -a || true; "
            "sed -ri.bkc-k3s \"/\\sswap\\s/s/^/#/\" /etc/fstab || true; "
            "modprobe br_netfilter || true; modprobe overlay || true; "
            "printf \"overlay\\nbr_netfilter\\n\" >/etc/modules-load.d/k3s.conf; "
            "printf \"net.bridge.bridge-nf-call-iptables = 1\\nnet.ipv4.ip_forward = 1\\nnet.bridge.bridge-nf-call-ip6tables = 1\\n\" >/etc/sysctl.d/90-k3s.conf; "
            "sysctl --system >/dev/null; "
            "if command -v firewall-cmd >/dev/null 2>&1; then "
            "firewall-cmd --permanent --add-port=6443/tcp || true; "
            "firewall-cmd --permanent --add-port=10250/tcp || true; "
            "firewall-cmd --permanent --add-port=8472/udp || true; "
            "firewall-cmd --reload || true; "
            "fi; "
            "echo k3s-base-ready'"
        )
        output = _k3s_ssh_command(node, command, timeout=1800)
        outputs.append({"node": fqdn, "output": output[-300:] if output else "k3s-base-ready"})
    _set_stage(run_id, stage_name, "complete", "K3s base OS prerequisites applied.")
    append_event(run_id, "info", stage_name, json.dumps(outputs, sort_keys=True))


def _k3s_node_by_role(run_id: str, role: str) -> dict:
    for node in _k3s_nodes(run_id):
        if str(node.get("role", "")).strip() == role:
            return node
    raise PipelineExecutionError(f"K3s node role '{role}' is missing from run metadata.")


def _run_k3s_install_server(run_id: str, stage_name: str) -> None:
    server = _k3s_node_by_role(run_id, "server")
    server_host = str(server.get("ip") or server.get("name") or "kube1.lab.auzietek.com").strip()
    command = (
        "bash -lc 'set -euo pipefail; "
        "if systemctl is-active --quiet k3s 2>/dev/null; then echo k3s-server-present; exit 0; fi; "
        "curl -sfL https://get.k3s.io -o /tmp/install-k3s.sh; "
        "chmod +x /tmp/install-k3s.sh; "
        "INSTALL_K3S_CHANNEL=stable /tmp/install-k3s.sh server "
        "--write-kubeconfig-mode 644 "
        "--disable traefik "
        f"--node-name {shlex.quote(str(server.get('short') or 'kube1'))} "
        f"--tls-san {shlex.quote(str(server.get('name') or 'kube1.lab.auzietek.com'))}; "
        "systemctl is-active --quiet k3s; "
        "echo k3s-server-ready'"
    )
    output = _k3s_ssh_command(server, command, timeout=1200)
    _store_run_extra(run_id, {"k3s_api_url": f"https://{server_host}:6443", "k3s_kubeconfig_path": "/etc/rancher/k3s/k3s.yaml"})
    _set_stage(run_id, stage_name, "complete", "K3s server installed on kube1.")
    append_event(run_id, "info", stage_name, output[-600:] if output else "k3s-server-ready")


def _run_k3s_capture_token(run_id: str, stage_name: str) -> None:
    server = _k3s_node_by_role(run_id, "server")
    token = _k3s_ssh_command(server, "bash -lc 'set -euo pipefail; cat /var/lib/rancher/k3s/server/node-token'", timeout=120).strip()
    if not token:
        raise PipelineExecutionError("K3s server did not return a join token.")
    _store_run_extra(run_id, {"k3s_join_token": token, "k3s_join_token_captured": True})
    _set_stage(run_id, stage_name, "complete", "K3s join token captured for the worker stage.")
    append_event(run_id, "info", stage_name, "Join token captured from kube1 and staged for kube2.")


def _run_k3s_install_agent(run_id: str, stage_name: str) -> None:
    run = get_run(run_id) or {}
    extra = run.get("extra", {}) or {}
    token = str(extra.get("k3s_join_token", "")).strip()
    if not token:
        raise PipelineExecutionError("K3s join token is missing. Re-run capture-k3s-token.")
    server = _k3s_node_by_role(run_id, "server")
    agent = _k3s_node_by_role(run_id, "agent")
    server_url = str(extra.get("k3s_api_url") or "").strip()
    if not server_url:
        server_host = str(server.get("ip") or server.get("name") or "kube1.lab.auzietek.com").strip()
        server_url = f"https://{server_host}:6443"
    command = (
        "bash -lc 'set -euo pipefail; "
        f"expected_url={shlex.quote(server_url)}; "
        "current_url=$(grep -E '^K3S_URL=' /etc/systemd/system/k3s-agent.service.env 2>/dev/null | cut -d= -f2- | tr -d '\"' || true); "
        "if systemctl is-active --quiet k3s-agent 2>/dev/null && [ \"$current_url\" = \"$expected_url\" ]; then echo k3s-agent-present; exit 0; fi; "
        "if command -v k3s-agent-uninstall.sh >/dev/null 2>&1; then /usr/local/bin/k3s-agent-uninstall.sh || true; fi; "
        "systemctl stop k3s-agent 2>/dev/null || true; "
        "rm -rf /etc/rancher/k3s /var/lib/rancher/k3s/agent /var/lib/rancher/k3s/server /var/lib/kubelet /etc/systemd/system/k3s-agent.service /etc/systemd/system/k3s-agent.service.env; "
        "curl -sfL https://get.k3s.io -o /tmp/install-k3s.sh; "
        "chmod +x /tmp/install-k3s.sh; "
        f"INSTALL_K3S_CHANNEL=stable K3S_URL={shlex.quote(server_url)} K3S_TOKEN={shlex.quote(token)} "
        "/tmp/install-k3s.sh agent "
        f"--node-name {shlex.quote(str(agent.get('short') or 'kube2'))}; "
        "systemctl is-active --quiet k3s-agent; "
        "echo k3s-agent-ready'"
    )
    output = _k3s_ssh_command(agent, command, timeout=1200)
    _store_run_extra(run_id, {"k3s_join_token": "", "k3s_join_token_used": True})
    _set_stage(run_id, stage_name, "complete", "Kube2 joined the k3s cluster.")
    append_event(run_id, "info", stage_name, output[-600:] if output else "k3s-agent-ready")


def _run_k3s_verify_cluster(run_id: str, stage_name: str) -> None:
    server = _k3s_node_by_role(run_id, "server")
    command = (
        "bash -lc 'set -euo pipefail; "
        "for _ in $(seq 1 60); do "
        "ready=$(k3s kubectl get nodes --no-headers 2>/dev/null | awk '\\''$2==\"Ready\"{c++} END{print c+0}'\\''); "
        "[ \"$ready\" -ge 2 ] && k3s kubectl get nodes -o wide && exit 0; "
        "sleep 5; "
        "done; "
        "k3s kubectl get nodes -o wide || true; "
        "exit 1'"
    )
    output = _k3s_ssh_command(server, command, timeout=600)
    _store_run_extra(run_id, {"k3s_verify_output": output[-1200:]})
    _set_stage(run_id, stage_name, "complete", "K3s cluster reports both nodes Ready.")
    append_event(run_id, "info", stage_name, output[-1200:] if output else "k3s-ready")


def _run_k3s_register_resources(run_id: str, stage_name: str) -> None:
    run = get_run(run_id) or {}
    extra = run.get("extra", {}) or {}
    nodes = _k3s_nodes(run_id)
    rules = load_rules()
    groups = rules.setdefault("groups", {})
    group = groups.setdefault(K3S_CLUSTER_NAME, {"locals": {}, "nodes": {}})
    group.setdefault("locals", {}).update(
        {
            "provider": "kubernetes",
            "resource_kind": "cluster",
            "cluster_engine": "k3s",
            "api_url": extra.get("k3s_api_url", "https://kube1.lab.auzietek.com:6443"),
            "kubeconfig_path": extra.get("k3s_kubeconfig_path", "/etc/rancher/k3s/k3s.yaml"),
            "managed_by": "blackknightcontroller",
            "workflow": "k3s-fedora-cluster",
        }
    )
    inventory = group.setdefault("nodes", {})
    ssh = load_integrations()["ssh"]
    private_key = str(ssh.get("private_key_path", "")).strip()
    for node in nodes:
        name = str(node.get("name", "")).strip()
        inventory[name] = {
            "provider": "proxmox",
            "resource_kind": "kubernetes-node",
            "configuration": "k3s",
            "cluster": K3S_CLUSTER_NAME,
            "kubernetes_role": str(node.get("role", "")),
            "vmid": int(node.get("vmid", 0)),
            "proxmox_node": str(node.get("proxmox_node", "")),
            "fqdn": name,
            "host": str(node.get("ip") or name),
            "user": "root",
            "port": 22,
            "private_key": private_key,
            "state": "ready",
        }
    reconcile = reconcile_rules_inventory(rules)
    save_rules(rules)
    _set_stage(run_id, stage_name, "complete", "K3s cluster resources registered.")
    append_event(run_id, "info", stage_name, json.dumps({"cluster": K3S_CLUSTER_NAME, "reconcile": reconcile}, sort_keys=True))


def _k3s_live_node(role: str) -> dict:
    for node in K3S_LIVE_NODES:
        if node["role"] == role:
            return node
    raise PipelineExecutionError(f"K3s live node role '{role}' is not configured.")


def _run_k3s_host_telemetry_verify(run_id: str, stage_name: str) -> None:
    server = _k3s_live_node("server")
    command = (
        "bash -lc 'set -euo pipefail; "
        "k3s kubectl get nodes -o wide; "
        "k3s kubectl wait --for=condition=Ready nodes --all --timeout=90s'"
    )
    output = run_remote_command(host=server["host"], user="root", command=command, timeout=120)
    _set_stage(run_id, stage_name, "complete", "K3s node readiness verified.")
    append_event(run_id, "info", stage_name, output[-1200:] if output else "k3s-ready")


def _encoded_file_template(name: str) -> str:
    template_path = Path(__file__).resolve().parent.parent / "file_templates" / name
    if not template_path.exists():
        raise PipelineExecutionError(f"Required file template is missing: {template_path}")
    return b64encode(template_path.read_bytes()).decode("ascii")


def _run_k3s_housekeeping_nfs(run_id: str, stage_name: str) -> None:
    mount_table = json.dumps(K3S_NFS_MOUNTS)
    mount_targets = " ".join(shlex.quote(target) for _, target in K3S_NFS_MOUNTS)
    script = "\n".join(
        [
            "set -euo pipefail",
            "dnf -y install nfs-utils >/dev/null 2>&1 || true",
            "mkdir -p /mnt/swarm/shared /mnt/swarm/tabor-linux-forge /mnt/swarm/blackknightcontroller",
            f"export BKC_K3S_NFS_MOUNTS={shlex.quote(mount_table)}",
            "python3 - <<'PY'",
            "import json, os",
            "from pathlib import Path",
            "mounts = json.loads(os.environ['BKC_K3S_NFS_MOUNTS'])",
            "path = Path('/etc/fstab')",
            "existing = path.read_text(encoding='utf-8').splitlines() if path.exists() else []",
            "targets = {target for _, target in mounts}",
            "kept = [line for line in existing if not any(f' {target} ' in f' {line} ' for target in targets)]",
            "kept.extend(f'{source} {target} nfs4 rw,sync,hard,_netdev 0 0' for source, target in mounts)",
            "path.write_text('\\n'.join(kept).rstrip() + '\\n', encoding='utf-8')",
            "PY",
            f"for target in {mount_targets}; do mount \"$target\" || true; done",
            "mount -a",
            f"for target in {mount_targets}; do findmnt -M \"$target\" -n -o TARGET,SOURCE,FSTYPE | grep -E '[[:space:]]nfs4?$'; done",
        ]
    )
    command = f"bash -lc {shlex.quote(script)}"
    results = {}
    for node in K3S_LIVE_NODES:
        results[node["name"]] = run_remote_command(host=node["host"], user="root", command=command, timeout=240)
    _set_stage(run_id, stage_name, "complete", "Shared project NFS mounts are present on both k3s nodes.")
    append_event(run_id, "info", stage_name, json.dumps(results, sort_keys=True)[-1600:])


def _run_k3s_apply_template(run_id: str, stage_name: str, template_name: str, remote_path: str, rollout_commands: str, timeout: int) -> str:
    server = _k3s_live_node("server")
    encoded = _encoded_file_template(template_name)
    command = (
        "bash -lc 'set -euo pipefail; "
        f"printf %s {shlex.quote(encoded)} | base64 -d >{shlex.quote(remote_path)}; "
        f"k3s kubectl apply -f {shlex.quote(remote_path)}; "
        f"{rollout_commands}'"
    )
    return run_remote_command(host=server["host"], user="root", command=command, timeout=timeout)


def _run_k3s_host_telemetry_apply(run_id: str, stage_name: str) -> None:
    output = _run_k3s_apply_template(
        run_id,
        stage_name,
        "k3s-host-telemetry.yaml",
        "/tmp/k3s-host-telemetry.yaml",
        "k3s kubectl -n rx-observability rollout status ds/telegraf-k3s-host --timeout=180s; "
        "k3s kubectl -n rx-observability rollout status ds/cadvisor-k3s --timeout=180s",
        600,
    )
    _set_stage(run_id, stage_name, "complete", "K3s host telemetry DaemonSets are rolled out.")
    append_event(run_id, "info", stage_name, output[-1200:] if output else "telemetry-daemonsets-ready")


def _run_k3s_housekeeping_loki_logs(run_id: str, stage_name: str) -> None:
    output = _run_k3s_apply_template(
        run_id,
        stage_name,
        "k3s-loki-logs.yaml",
        "/tmp/k3s-loki-logs.yaml",
        "k3s kubectl -n rx-observability rollout status ds/promtail-k3s --timeout=180s",
        300,
    )
    _set_stage(run_id, stage_name, "complete", "K3s host and pod logs are shipping toward Loki.")
    append_event(run_id, "info", stage_name, output[-1200:] if output else "k3s-logs-ready")


def _run_k3s_housekeeping_loadgen(run_id: str, stage_name: str) -> None:
    output = _run_k3s_apply_template(
        run_id,
        stage_name,
        "rx-loadgen-deployment.yaml",
        "/tmp/rx-loadgen-deployment.yaml",
        "k3s kubectl -n rx-demo rollout status deploy/loadgen --timeout=180s",
        300,
    )
    _set_stage(run_id, stage_name, "complete", "Steady rx-demo loadgen Deployment is available.")
    append_event(run_id, "info", stage_name, output[-1200:] if output else "loadgen-ready")


def _run_k3s_host_telemetry_firewall(run_id: str, stage_name: str) -> None:
    results = {}
    command = (
        "bash -lc 'set -euo pipefail; "
        "firewall-cmd --add-port=9273/tcp --add-port=18080/tcp --permanent 2>/dev/null || true; "
        "firewall-cmd --reload 2>/dev/null || true; "
        "curl -fsS -m 5 http://127.0.0.1:9273/metrics >/dev/null; "
        "curl -fsS -m 5 http://127.0.0.1:18080/metrics >/dev/null; "
        "echo telemetry-ports-ready'"
    )
    for node in K3S_LIVE_NODES:
        results[node["name"]] = run_remote_command(host=node["host"], user="root", command=command, timeout=180)
    _set_stage(run_id, stage_name, "complete", "K3s telemetry scrape ports are open on both nodes.")
    append_event(run_id, "info", stage_name, json.dumps(results, sort_keys=True))


def _run_k3s_host_telemetry_prometheus(run_id: str, stage_name: str, settings: dict[str, str]) -> None:
    kube1 = _k3s_live_node("server")["host"]
    kube2 = _k3s_live_node("agent")["host"]
    command = f"""
    bash -lc 'set -euo pipefail
    config=/srv/stacks/monitoring/prometheus.yml
    backup="${{config}}.bak.$(date +%Y%m%d%H%M%S)"
    cp "${{config}}" "${{backup}}"
    if ! grep -q "job_name: k3s-telegraf-hosts" "${{config}}"; then
      cat >>"${{config}}" <<EOF

  - job_name: k3s-telegraf-hosts
    static_configs:
      - targets:
          - "{kube1}:9273"
          - "{kube2}:9273"

  - job_name: k3s-cadvisor
    static_configs:
      - targets:
          - "{kube1}:18080"
          - "{kube2}:18080"
EOF
    fi
    docker service update --force monitoring_prometheus >/dev/null
    for _ in $(seq 1 30); do
      curl -fsS http://127.0.0.1:9090/-/healthy >/dev/null && break
      sleep 2
    done
    grep -n "k3s-telegraf-hosts\\|k3s-cadvisor" "${{config}}"'
    """
    output = run_remote_command(
        host=settings["manager_host"],
        user=settings["manager_user"],
        password=settings["manager_password"],
        command=command,
        timeout=240,
    )
    _set_stage(run_id, stage_name, "complete", "Prometheus scrape jobs for kube1/kube2 are present.")
    append_event(run_id, "info", stage_name, output[-1200:] if output else "prometheus-targets-ready")


def _run_k3s_host_telemetry_validate(run_id: str, stage_name: str, settings: dict[str, str]) -> None:
    command = (
        "bash -lc 'set -euo pipefail; "
        "for _ in $(seq 1 20); do "
        "targets=$(curl -fsS http://127.0.0.1:9090/api/v1/targets | "
        "jq -r '\\''[.data.activeTargets[] | select(.labels.job==\"k3s-telegraf-hosts\" or .labels.job==\"k3s-cadvisor\") | select(.health==\"up\")] | length'\\''); "
        "test \"$targets\" = \"4\" && break; "
        "sleep 3; "
        "done; "
        "curl -fsS http://127.0.0.1:9090/api/v1/targets | "
        "jq -r '\\''.data.activeTargets[] | select(.labels.job==\"k3s-telegraf-hosts\" or .labels.job==\"k3s-cadvisor\") | [.labels.job,.labels.instance,.health,.lastError] | @tsv'\\'' | sort; "
        "test \"$(curl -fsS http://127.0.0.1:9090/api/v1/targets | jq -r '\\''[.data.activeTargets[] | select(.labels.job==\"k3s-telegraf-hosts\" or .labels.job==\"k3s-cadvisor\") | select(.health==\"up\")] | length'\\'')\" = \"4\"'"
    )
    output = run_remote_command(
        host=settings["manager_host"],
        user=settings["manager_user"],
        password=settings["manager_password"],
        command=command,
        timeout=180,
    )
    _set_stage(run_id, stage_name, "complete", "Prometheus reports k3s Telegraf and cAdvisor targets up.")
    append_event(run_id, "info", stage_name, output[-1200:] if output else "k3s-scrapes-up")


def _run_rx_demo_k3s_source_check(run_id: str, stage_name: str, settings: dict[str, str]) -> None:
    script = "\n".join(
        [
            "set -euo pipefail",
            f"cd {shlex.quote(RX_DEMO_SHARED_SOURCE)}",
            "test -f rx-demo.sln",
            "test -f src/rx-ui/Rx.Ui/Dockerfile",
            "test -d k8s/overlays/lab",
            "git rev-parse --short HEAD 2>/dev/null || true",
            "printf 'rx-demo-source-ready %s\\n' \"$PWD\"",
        ]
    )
    output = run_remote_command(
        host=settings["manager_host"],
        user=settings["manager_user"],
        password=settings["manager_password"],
        command=f"bash -lc {shlex.quote(script)}",
        timeout=60,
    )
    _set_stage(run_id, stage_name, "complete", "Staged rx-demo source is present.")
    append_event(run_id, "info", stage_name, output[-1200:] if output else "rx-demo-source-ready")


def _run_rx_demo_k3s_build_rx_ui(run_id: str, stage_name: str, settings: dict[str, str]) -> None:
    script = "\n".join(
        [
            "set -euo pipefail",
            f"cd {shlex.quote(RX_DEMO_SHARED_SOURCE)}",
            "test -f src/rx-ui/Rx.Ui/Dockerfile",
            (
                "docker build "
                "-f src/rx-ui/Rx.Ui/Dockerfile "
                f"-t {shlex.quote(RX_DEMO_RX_UI_IMAGE)} "
                "--build-arg DOTNET_VERSION=10.0 ."
            ),
            f"docker image inspect {shlex.quote(RX_DEMO_RX_UI_IMAGE)} --format '{{{{.Id}}}} {{{{.Created}}}}'",
            "tmp_tar=/tmp/rx-demo-rx-ui-latest.tar",
            "rm -f \"$tmp_tar\"",
            f"docker save {shlex.quote(RX_DEMO_RX_UI_IMAGE)} -o \"$tmp_tar\"",
            f"install -m 0644 \"$tmp_tar\" {shlex.quote(RX_DEMO_RX_UI_TAR)}",
            "rm -f \"$tmp_tar\"",
            f"chmod 0644 {shlex.quote(RX_DEMO_RX_UI_TAR)}",
            f"ls -lh {shlex.quote(RX_DEMO_RX_UI_TAR)}",
        ]
    )
    output = run_remote_command(
        host=settings["manager_host"],
        user=settings["manager_user"],
        password=settings["manager_password"],
        command=f"bash -lc {shlex.quote(script)}",
        timeout=1200,
    )
    _set_stage(run_id, stage_name, "complete", "rx-ui image built and exported to shared storage.")
    append_event(run_id, "info", stage_name, output[-1600:] if output else "rx-ui-image-ready")


def _run_rx_demo_k3s_import_rx_ui(run_id: str, stage_name: str) -> None:
    script = "\n".join(
        [
            "set -euo pipefail",
            f"test -s {shlex.quote(RX_DEMO_RX_UI_TAR)}",
            f"k3s ctr images import {shlex.quote(RX_DEMO_RX_UI_TAR)}",
            "k3s ctr images ls | grep -F 'rx-demo/rx-ui'",
        ]
    )
    results = {}
    for node in K3S_LIVE_NODES:
        results[node["name"]] = run_remote_command(
            host=node["host"],
            user="root",
            command=f"bash -lc {shlex.quote(script)}",
            timeout=300,
        )[-500:]
    _set_stage(run_id, stage_name, "complete", "rx-ui image imported on both k3s nodes.")
    append_event(run_id, "info", stage_name, json.dumps(results, sort_keys=True)[-1600:])


def _run_rx_demo_k3s_apply_lab(run_id: str, stage_name: str) -> None:
    server = _k3s_live_node("server")
    script = "\n".join(
        [
            "set -euo pipefail",
            f"for _ in $(seq 1 20); do test -d {shlex.quote(RX_DEMO_SHARED_SOURCE)} && break; sleep 2; done",
            f"test -d {shlex.quote(RX_DEMO_SHARED_SOURCE)}",
            f"cd {shlex.quote(RX_DEMO_SHARED_SOURCE)}",
            "k3s kubectl apply -k k8s/overlays/lab",
            "k3s kubectl -n rx-demo rollout restart deploy/rx-ui",
            "k3s kubectl -n rx-demo rollout status deploy/rx-ui --timeout=240s",
            "k3s kubectl -n rx-demo get pods -l app.kubernetes.io/name=rx-ui -o wide",
        ]
    )
    output = run_remote_command(
        host=server["host"],
        user="root",
        command=f"bash -lc {shlex.quote(script)}",
        timeout=420,
    )
    _set_stage(run_id, stage_name, "complete", "rx-ui rollout completed.")
    append_event(run_id, "info", stage_name, output[-1600:] if output else "rx-ui-rollout-ready")


def _run_rx_demo_k3s_smoke_ui(run_id: str, stage_name: str) -> None:
    server = _k3s_live_node("server")
    script = r"""
set -euo pipefail
node_port="$(k3s kubectl -n rx-demo get svc rx-ui -o jsonpath='{.spec.ports[?(@.name=="http")].nodePort}')"
base="http://127.0.0.1:${node_port:-30080}"
rx_id="RX-BKC-SMOKE"
curl -fsS "$base/" | grep -F "Prescription Demo UI" >/dev/null
curl -fsS -H 'Content-Type: application/json' -H 'Accept: application/json' \
  -d "{\"rxId\":\"${rx_id}\"}" "$base/lookup" | grep -F '"operation":"lookup"' >/dev/null
curl -fsS -H 'Content-Type: application/json' -H 'Accept: application/json' \
  -d "{\"rxId\":\"${rx_id}\",\"approvedBy\":\"bkc.pipeline\",\"notes\":\"BKC smoke approve\"}" "$base/approve" | grep -F '"operation":"approve"' >/dev/null
curl -fsS -H 'Content-Type: application/json' -H 'Accept: application/json' \
  -d "{\"rxId\":\"${rx_id}\",\"refillCount\":1}" "$base/refill" | grep -F '"operation":"refill"' >/dev/null
printf 'rx-ui-routes-ok %s\n' "$base"
"""
    output = run_remote_command(
        host=server["host"],
        user="root",
        command=f"bash -lc {shlex.quote(script)}",
        timeout=180,
    )
    _set_stage(run_id, stage_name, "complete", "Routed UI smoke checks passed.")
    append_event(run_id, "info", stage_name, output[-1200:] if output else "rx-ui-routes-ok")


def _run_rx_demo_k3s_ready(run_id: str, stage_name: str) -> None:
    server = _k3s_live_node("server")
    script = "\n".join(
        [
            "set -euo pipefail",
            "k3s kubectl get nodes -o wide",
            "k3s kubectl wait --for=condition=Ready nodes --all --timeout=90s",
        ]
    )
    output = run_remote_command(
        host=server["host"],
        user="root",
        command=f"bash -lc {shlex.quote(script)}",
        timeout=120,
    )
    _set_stage(run_id, stage_name, "complete", "K3s node readiness verified.")
    append_event(run_id, "info", stage_name, output[-1600:] if output else "k3s-ready")


def _run_rx_demo_k3s_secrets(run_id: str, stage_name: str) -> None:
    server = _k3s_live_node("server")
    script = r"""
set -euo pipefail
k3s kubectl get namespace rx-demo >/dev/null 2>&1 || k3s kubectl create namespace rx-demo >/dev/null
sa_password="$(python3 -c 'import secrets; print("AuzixDemo9!" + secrets.token_urlsafe(18))')"
rabbit_password="$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')"
k3s kubectl -n rx-demo create secret generic rx-demo-secrets \
  --from-literal=SA_PASSWORD="$sa_password" \
  --from-literal=RABBITMQ_DEFAULT_USER="rx_demo" \
  --from-literal=RABBITMQ_DEFAULT_PASS="$rabbit_password" \
  --dry-run=client -o yaml | k3s kubectl apply -f - >/dev/null
k3s kubectl -n rx-demo get secret rx-demo-secrets -o name
"""
    output = run_remote_command(
        host=server["host"],
        user="root",
        command=f"bash -lc {shlex.quote(script)}",
        timeout=60,
    )
    _set_stage(run_id, stage_name, "complete", "Rx-demo runtime secrets are present.")
    append_event(run_id, "info", stage_name, output[-1200:] if output else "rx-demo-secrets-present")


def _run_rx_demo_k3s_registry_images(run_id: str, stage_name: str, settings: dict[str, str]) -> None:
    tag = RX_DEMO_K3S_DEMO_TAG
    script = "\n".join(
        [
            "set -euo pipefail",
            f"cd {shlex.quote(RX_DEMO_SHARED_SOURCE)}",
            "test -x tools/build-and-push.sh",
            f"TAG={shlex.quote(tag)} REGISTRY=127.0.0.1:{DEMO_REGISTRY_PORT}/rx-demo PUSH=1 tools/build-and-push.sh",
            "for repo in rx-ui api-gateway legacy-sync-worker read-model-projection loadgen; do",
            f"  curl -fsS http://127.0.0.1:{DEMO_REGISTRY_PORT}/v2/rx-demo/$repo/tags/list | grep -F {shlex.quote(tag)} >/dev/null",
            "  printf 'registry-image-ready rx-demo/%s:%s\\n' \"$repo\" " + shlex.quote(tag),
            "done",
        ]
    )
    output = run_remote_command(
        host=settings["manager_host"],
        user=settings["manager_user"],
        password=settings["manager_password"],
        command=f"bash -lc {shlex.quote(script)}",
        timeout=2400,
    )
    _set_stage(run_id, stage_name, "complete", "Rx-demo registry images are present.")
    append_event(run_id, "info", stage_name, output[-2400:] if output else f"rx-demo-images:{tag}")


def _run_rx_demo_k3s_apply_demo_overlay(run_id: str, stage_name: str) -> None:
    server = _k3s_live_node("server")
    script = "\n".join(
        [
            "set -euo pipefail",
            f"test -d {shlex.quote(RX_DEMO_SHARED_SOURCE)}",
            f"cd {shlex.quote(RX_DEMO_SHARED_SOURCE)}",
            "test -d k8s/overlays/k3s-demo",
            "k3s kubectl kustomize k8s/overlays/k3s-demo >/tmp/rx-k3s-demo.yaml",
            "wc -l /tmp/rx-k3s-demo.yaml",
            "k3s kubectl apply -k k8s/overlays/k3s-demo",
            "k3s kubectl -n rx-demo get svc -o wide",
            "k3s kubectl -n rx-observability get svc -o wide",
        ]
    )
    output = run_remote_command(
        host=server["host"],
        user="root",
        command=f"bash -lc {shlex.quote(script)}",
        timeout=300,
    )
    _set_stage(run_id, stage_name, "complete", "Rx-demo k3s-demo overlay applied.")
    append_event(run_id, "info", stage_name, output[-2400:] if output else "k3s-demo-overlay-applied")


def _run_rx_demo_k3s_rollout_app(run_id: str, stage_name: str) -> None:
    server = _k3s_live_node("server")
    deployments = "api-gateway legacy-sync-worker otel-collector rabbitmq read-model-projection redis rx-ui"
    script = "\n".join(
        [
            "set -euo pipefail",
            "k3s kubectl -n rx-demo get pods -o wide",
            f"for deploy in {deployments}; do",
            "  k3s kubectl -n rx-demo rollout status deploy/$deploy --timeout=300s",
            "done",
            "k3s kubectl -n rx-demo rollout status statefulset/mssql --timeout=300s",
            "k3s kubectl -n rx-demo get pods -o wide",
            "k3s kubectl -n rx-demo get events --sort-by=.lastTimestamp | tail -40",
        ]
    )
    output = run_remote_command(
        host=server["host"],
        user="root",
        command=f"bash -lc {shlex.quote(script)}",
        timeout=900,
    )
    _set_stage(run_id, stage_name, "complete", "Rx-demo application workloads are ready.")
    append_event(run_id, "info", stage_name, output[-3000:] if output else "rx-demo-rollout-ready")


def _run_rx_demo_k3s_rollout_observability(run_id: str, stage_name: str) -> None:
    server = _k3s_live_node("server")
    deployments = "grafana loki prometheus tempo"
    script = "\n".join(
        [
            "set -euo pipefail",
            "k3s kubectl -n rx-observability get pods -o wide",
            f"for deploy in {deployments}; do",
            "  k3s kubectl -n rx-observability rollout status deploy/$deploy --timeout=300s",
            "done",
            "k3s kubectl -n rx-observability get pods -o wide",
            "k3s kubectl -n rx-observability get events --sort-by=.lastTimestamp | tail -40",
        ]
    )
    output = run_remote_command(
        host=server["host"],
        user="root",
        command=f"bash -lc {shlex.quote(script)}",
        timeout=900,
    )
    _set_stage(run_id, stage_name, "complete", "Rx-demo observability workloads are ready.")
    append_event(run_id, "info", stage_name, output[-3000:] if output else "rx-demo-observability-ready")


def _run_rx_demo_k3s_smoke_api_full(run_id: str, stage_name: str) -> None:
    server = _k3s_live_node("server")
    script = r"""
set -euo pipefail
base="http://127.0.0.1:30081"
rx_id="RX-BKC-K3S-API"
curl -fsS "$base/healthz"
curl -fsS "$base/readyz"
curl -fsS "$base/prescriptions/${rx_id}" | grep -F "$rx_id" >/dev/null
curl -fsS -H 'Content-Type: application/json' -H 'Accept: application/json' \
  -d '{"approvedBy":"bkc.pipeline","notes":"BKC k3s API smoke"}' \
  "$base/prescriptions/${rx_id}/approve" | grep -F 'ApproveQueued' >/dev/null
curl -fsS -H 'Content-Type: application/json' -H 'Accept: application/json' \
  -d '{"refillCount":1}' \
  "$base/prescriptions/${rx_id}/refill" | grep -F 'RefillQueued' >/dev/null
printf 'rx-api-nodeport-ok %s\n' "$base"
"""
    output = run_remote_command(
        host=server["host"],
        user="root",
        command=f"bash -lc {shlex.quote(script)}",
        timeout=180,
    )
    _set_stage(run_id, stage_name, "complete", "Rx-demo API smoke checks passed.")
    append_event(run_id, "info", stage_name, output[-1200:] if output else "rx-api-nodeport-ok")


def _run_rx_demo_k3s_smoke_ui_full(run_id: str, stage_name: str) -> None:
    server = _k3s_live_node("server")
    script = r"""
set -euo pipefail
node_port="$(k3s kubectl -n rx-demo get svc rx-ui -o jsonpath='{.spec.ports[?(@.name=="http")].nodePort}')"
node_name="$(k3s kubectl -n rx-demo get endpoints rx-ui -o jsonpath='{.subsets[0].addresses[0].nodeName}')"
node_ip="$(k3s kubectl get node "$node_name" -o jsonpath='{.status.addresses[?(@.type=="InternalIP")].address}')"
base="http://${node_ip}:${node_port}"
rx_id="RX-BKC-K3S-UI"
curl -fsS "$base/" | grep -F "Prescription Demo UI" >/dev/null
curl -fsS -H 'Content-Type: application/json' -H 'Accept: application/json' \
  -d "{\"rxId\":\"${rx_id}\"}" "$base/lookup" | grep -F '"operation":"lookup"' >/dev/null
curl -fsS -H 'Content-Type: application/json' -H 'Accept: application/json' \
  -d "{\"rxId\":\"${rx_id}\",\"approvedBy\":\"bkc.pipeline\",\"notes\":\"BKC k3s demo smoke\"}" "$base/approve" | grep -F '"operation":"approve"' >/dev/null
curl -fsS -H 'Content-Type: application/json' -H 'Accept: application/json' \
  -d "{\"rxId\":\"${rx_id}\",\"refillCount\":1}" "$base/refill" | grep -F '"operation":"refill"' >/dev/null
printf 'rx-ui-nodeport-ok %s\n' "$base"
"""
    output = run_remote_command(
        host=server["host"],
        user="root",
        command=f"bash -lc {shlex.quote(script)}",
        timeout=180,
    )
    _set_stage(run_id, stage_name, "complete", "Rx-demo UI smoke checks passed.")
    append_event(run_id, "info", stage_name, output[-1200:] if output else "rx-ui-nodeport-ok")


def _run_rx_demo_k3s_telemetry_check(run_id: str, stage_name: str) -> None:
    server = _k3s_live_node("server")
    script = r"""
set -euo pipefail
node_ip_for_endpoint() {
  ns="$1"
  svc="$2"
  node_name="$(k3s kubectl -n "$ns" get endpoints "$svc" -o jsonpath='{.subsets[0].addresses[0].nodeName}' 2>/dev/null)"
  k3s kubectl get node "$node_name" -o jsonpath='{.status.addresses[?(@.type=="InternalIP")].address}'
}
node_port_for_service() {
  ns="$1"
  svc="$2"
  port_name="$3"
  k3s kubectl -n "$ns" get svc "$svc" -o jsonpath="{.spec.ports[?(@.name==\"${port_name}\")].nodePort}"
}
otel_base="http://$(node_ip_for_endpoint rx-demo otel-collector):$(node_port_for_service rx-demo otel-collector prom-metrics)"
prom_base="http://$(node_ip_for_endpoint rx-observability prometheus):$(node_port_for_service rx-observability prometheus http)"
grafana_base="http://$(node_ip_for_endpoint rx-observability grafana):$(node_port_for_service rx-observability grafana http)"
metrics_file="$(mktemp)"
trap 'rm -f "$metrics_file"' EXIT
curl -fsS "$otel_base/metrics" >"$metrics_file"
grep -m 10 -E '^(rx_|otelcol_)' "$metrics_file"
curl -fsS "$prom_base/-/ready"
curl -fsS "$grafana_base/api/health" | grep -F '"database"' >/dev/null
printf 'rx-telemetry-nodeports-ok grafana=%s prometheus=%s otel=%s\n' "$grafana_base" "$prom_base" "$otel_base"
"""
    output = run_remote_command(
        host=server["host"],
        user="root",
        command=f"bash -lc {shlex.quote(script)}",
        timeout=180,
    )
    _set_stage(run_id, stage_name, "complete", "Rx-demo telemetry endpoints responded.")
    append_event(run_id, "info", stage_name, output[-1600:] if output else "rx-telemetry-nodeports-ok")


def _run_rx_demo_k3s_access_links(run_id: str, stage_name: str) -> None:
    server = _k3s_live_node("server")
    script = r"""
set -euo pipefail
node_ip_for_endpoint() {
  ns="$1"
  svc="$2"
  node_name="$(k3s kubectl -n "$ns" get endpoints "$svc" -o jsonpath='{.subsets[0].addresses[0].nodeName}' 2>/dev/null)"
  k3s kubectl get node "$node_name" -o jsonpath='{.status.addresses[?(@.type=="InternalIP")].address}'
}
node_port_for_service() {
  ns="$1"
  svc="$2"
  port_name="$3"
  k3s kubectl -n "$ns" get svc "$svc" -o jsonpath="{.spec.ports[?(@.name==\"${port_name}\")].nodePort}"
}
printf 'rx_ui=http://%s:%s\n' "$(node_ip_for_endpoint rx-demo rx-ui)" "$(node_port_for_service rx-demo rx-ui http)"
printf 'rx_api=http://%s:%s\n' "$(node_ip_for_endpoint rx-demo api-gateway)" "$(node_port_for_service rx-demo api-gateway http)"
printf 'otel_metrics=http://%s:%s/metrics\n' "$(node_ip_for_endpoint rx-demo otel-collector)" "$(node_port_for_service rx-demo otel-collector prom-metrics)"
printf 'grafana=http://%s:%s\n' "$(node_ip_for_endpoint rx-observability grafana)" "$(node_port_for_service rx-observability grafana http)"
printf 'prometheus=http://%s:%s\n' "$(node_ip_for_endpoint rx-observability prometheus)" "$(node_port_for_service rx-observability prometheus http)"
printf 'loki=http://%s:%s\n' "$(node_ip_for_endpoint rx-observability loki)" "$(node_port_for_service rx-observability loki http)"
printf 'tempo=http://%s:%s\n' "$(node_ip_for_endpoint rx-observability tempo)" "$(node_port_for_service rx-observability tempo http)"
"""
    output = run_remote_command(
        host=server["host"],
        user="root",
        command=f"bash -lc {shlex.quote(script)}",
        timeout=120,
    )
    links = {
        "image_tag": RX_DEMO_K3S_DEMO_TAG,
    }
    for line in output.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip() and value.strip():
            links[key.strip()] = value.strip()
    _store_run_extra(run_id, {"rx_demo_k3s_links": links})
    _set_stage(run_id, stage_name, "complete", "Demo access links published.")
    append_event(run_id, "info", stage_name, json.dumps(links, sort_keys=True))


def _rx_demo_redeploy_run_context(run_id: str) -> dict[str, str]:
    run = get_run(run_id) or {}
    ref = str(run.get("ref") or "").strip()
    commit = str(run.get("commit") or "").strip()
    extra = run.get("extra") if isinstance(run.get("extra"), dict) else {}
    payload = extra.get("request_payload") if isinstance(extra.get("request_payload"), dict) else {}
    return {
        "ref": ref or str(payload.get("ref") or "").strip(),
        "commit": commit or str(payload.get("commit") or "").strip(),
        "notes": str(run.get("notes") or payload.get("notes") or "").strip(),
    }


def _run_rx_demo_k3s_git_event(run_id: str, stage_name: str) -> None:
    context = _rx_demo_redeploy_run_context(run_id)
    _set_stage(run_id, stage_name, "complete", "Git trigger inputs recorded.")
    append_event(run_id, "info", stage_name, json.dumps(context, sort_keys=True))


def _run_rx_demo_k3s_sync_source_from_git(run_id: str, stage_name: str, settings: dict[str, str]) -> None:
    context = _rx_demo_redeploy_run_context(run_id)
    ref = context["ref"]
    commit = context["commit"]
    git_work = f"{RX_DEMO_SHARED_SOURCE}/.bkc-git-work"
    script = "\n".join(
        [
            "set -euo pipefail",
            "if ! command -v git >/dev/null 2>&1; then",
            "  if command -v dnf >/dev/null 2>&1; then dnf -y install git;",
            "  elif command -v apt-get >/dev/null 2>&1; then apt-get update && apt-get install -y git;",
            "  elif command -v apk >/dev/null 2>&1; then apk add --no-cache git;",
            "  else echo 'git package manager not found' >&2; exit 1; fi",
            "fi",
            "if ! command -v rsync >/dev/null 2>&1; then",
            "  if command -v dnf >/dev/null 2>&1; then dnf -y install rsync;",
            "  elif command -v apt-get >/dev/null 2>&1; then apt-get update && apt-get install -y rsync;",
            "  elif command -v apk >/dev/null 2>&1; then apk add --no-cache rsync;",
            "  else echo 'rsync package manager not found' >&2; exit 1; fi",
            "fi",
            "git_cmd='git'",
            "if id auzieman >/dev/null 2>&1 && command -v runuser >/dev/null 2>&1; then git_cmd='runuser -u auzieman -- git'; fi",
            "if id auzieman >/dev/null 2>&1 && command -v ssh-keyscan >/dev/null 2>&1; then",
            "  install -d -m 0700 -o auzieman -g auzieman /home/auzieman/.ssh",
            "  ssh-keyscan -H github.com >>/home/auzieman/.ssh/known_hosts 2>/dev/null || true",
            "  chown auzieman:auzieman /home/auzieman/.ssh/known_hosts",
            "  chmod 0600 /home/auzieman/.ssh/known_hosts",
            "fi",
            f"live_dir={shlex.quote(RX_DEMO_SHARED_SOURCE)}",
            f"git_work={shlex.quote(git_work)}",
            "if ! test -d \"$git_work/.git\"; then",
            "  rm -rf \"$git_work\"",
            "  $git_cmd clone https://github.com/auzieman/rx-demo.git \"$git_work\"",
            "fi",
            "cd \"$git_work\"",
            "test -z \"$($git_cmd status --porcelain)\"",
            "$git_cmd fetch --prune origin",
            f"commit={shlex.quote(commit)}",
            f"ref={shlex.quote(ref)}",
            "if test -n \"$commit\"; then",
            "  $git_cmd checkout --detach \"$commit\"",
            "else",
            "  branch=\"${ref#refs/heads/}\"",
            "  test -n \"$branch\" || branch=\"$($git_cmd branch --show-current)\"",
            "  test -n \"$branch\"",
            "  $git_cmd checkout \"$branch\"",
            "  $git_cmd pull --ff-only origin \"$branch\"",
            "fi",
            "tag=\"$($git_cmd rev-parse --short HEAD)\"",
            "full_commit=\"$($git_cmd rev-parse HEAD)\"",
            "mkdir -p \"$live_dir\"",
            "rsync -a --delete --exclude='.git/' --exclude='.bkc-git-work/' \"$git_work\"/ \"$live_dir\"/",
            "cd \"$live_dir\"",
            "test -f rx-demo.sln",
            "test -x tools/build-and-push.sh",
            "printf 'rx-demo-git-ready ref=%s commit=%s tag=%s\\n' \"$ref\" \"$full_commit\" \"$tag\"",
        ]
    )
    output = run_remote_command(
        host=settings["manager_host"],
        user=settings["manager_user"],
        password=settings["manager_password"],
        command=f"bash -lc {shlex.quote(script)}",
        timeout=300,
    )
    tag = ""
    for line in output.splitlines():
        if " tag=" in line:
            tag = line.rsplit(" tag=", 1)[-1].strip()
    if not re.fullmatch(r"[0-9a-f]{7,12}", tag):
        raise PipelineExecutionError("Unable to determine rx-demo redeploy image tag from Git checkout.")
    _store_run_extra(run_id, {"rx_demo_redeploy_tag": tag})
    _set_stage(run_id, stage_name, "complete", "Shared rx-demo source is on the requested Git revision.")
    append_event(run_id, "info", stage_name, output[-1600:] if output else f"rx-demo-git-ready tag={tag}")


def _run_rx_demo_k3s_redeploy_build_push(run_id: str, stage_name: str, settings: dict[str, str]) -> None:
    script = "\n".join(
        [
            "set -euo pipefail",
            f"cd {shlex.quote(RX_DEMO_SHARED_SOURCE)}",
            "tag=\"$(git rev-parse --short HEAD)\"",
            "test -x tools/build-and-push.sh",
            f"TAG=\"$tag\" REGISTRY=127.0.0.1:{DEMO_REGISTRY_PORT}/rx-demo PUSH=1 tools/build-and-push.sh",
            "for repo in rx-ui api-gateway legacy-sync-worker read-model-projection loadgen; do",
            f"  curl -fsS http://127.0.0.1:{DEMO_REGISTRY_PORT}/v2/rx-demo/$repo/tags/list | grep -F \"$tag\" >/dev/null",
            "  printf 'registry-image-ready rx-demo/%s:%s\\n' \"$repo\" \"$tag\"",
            "done",
        ]
    )
    output = run_remote_command(
        host=settings["manager_host"],
        user=settings["manager_user"],
        password=settings["manager_password"],
        command=f"bash -lc {shlex.quote(script)}",
        timeout=2400,
    )
    _set_stage(run_id, stage_name, "complete", "Commit-tagged rx-demo images are present in the local registry.")
    append_event(run_id, "info", stage_name, output[-2400:] if output else "rx-demo-redeploy-images-ready")


def _run_rx_demo_k3s_redeploy_update_images(run_id: str, stage_name: str) -> None:
    server = _k3s_live_node("server")
    script = "\n".join(
        [
            "set -euo pipefail",
            f"cd {shlex.quote(RX_DEMO_SHARED_SOURCE)}",
            "tag=\"$(git rev-parse --short HEAD)\"",
            f"registry={shlex.quote(DEMO_REGISTRY)}/rx-demo",
            "k3s kubectl -n rx-demo set image deploy/api-gateway api-gateway=\"$registry/api-gateway:$tag\"",
            "k3s kubectl -n rx-demo set image deploy/rx-ui rx-ui=\"$registry/rx-ui:$tag\"",
            "k3s kubectl -n rx-demo set image deploy/legacy-sync-worker worker=\"$registry/legacy-sync-worker:$tag\"",
            "k3s kubectl -n rx-demo set image deploy/read-model-projection worker=\"$registry/read-model-projection:$tag\"",
            "if k3s kubectl -n rx-demo get deploy/loadgen >/dev/null 2>&1; then",
            "  k3s kubectl -n rx-demo set image deploy/loadgen loadgen=\"$registry/loadgen:$tag\"",
            "fi",
            "k3s kubectl -n rx-demo get deploy -o custom-columns=NAME:.metadata.name,IMAGE:.spec.template.spec.containers[0].image",
        ]
    )
    output = run_remote_command(
        host=server["host"],
        user="root",
        command=f"bash -lc {shlex.quote(script)}",
        timeout=240,
    )
    _set_stage(run_id, stage_name, "complete", "K3s deployments reference the commit-tagged images.")
    append_event(run_id, "info", stage_name, output[-2000:] if output else "rx-demo-images-updated")


def _run_rx_demo_k3s_cloudinit_node_check(run_id: str, stage_name: str) -> None:
    server = _k3s_live_node("server")
    script = r"""
set -euo pipefail
k3s kubectl get nodes -o wide
for node in $(k3s kubectl get nodes -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}'); do
  ip="$(k3s kubectl get node "$node" -o jsonpath='{.status.addresses[?(@.type=="InternalIP")].address}')"
  printf 'node=%s ip=%s\n' "$node" "$ip"
  ssh -o BatchMode=yes -o StrictHostKeyChecking=no -o ConnectTimeout=8 "root@$ip" \
    'set -e; hostnamectl --static 2>/dev/null || hostname; test -d /var/lib/cloud && printf "cloud-init-dir=present\n" || printf "cloud-init-dir=missing\n"; cloud-init status --long 2>/dev/null || true; test -f /etc/machine-id && cut -c1-12 /etc/machine-id'
done
"""
    output = run_remote_command(
        host=server["host"],
        user="root",
        command=f"bash -lc {shlex.quote(script)}",
        timeout=180,
    )
    _set_stage(run_id, stage_name, "complete", "K3s node and cloud-init evidence captured.")
    append_event(run_id, "info", stage_name, output[-3000:] if output else "cloudinit-node-evidence")


def _run_rx_demo_k3s_loki_cloudevents_check(run_id: str, stage_name: str) -> None:
    server = _k3s_live_node("server")
    script = r"""
set -euo pipefail
node_ip_for_endpoint() {
  ns="$1"
  svc="$2"
  node_name="$(k3s kubectl -n "$ns" get endpoints "$svc" -o jsonpath='{.subsets[0].addresses[0].nodeName}' 2>/dev/null)"
  k3s kubectl get node "$node_name" -o jsonpath='{.status.addresses[?(@.type=="InternalIP")].address}'
}
node_port_for_service() {
  ns="$1"
  svc="$2"
  port_name="$3"
  k3s kubectl -n "$ns" get svc "$svc" -o jsonpath="{.spec.ports[?(@.name==\"${port_name}\")].nodePort}"
}
api_base="http://$(node_ip_for_endpoint rx-demo api-gateway):$(node_port_for_service rx-demo api-gateway http)"
loki_base="http://$(node_ip_for_endpoint rx-observability loki):$(node_port_for_service rx-observability loki http)"
rx_id="RX-BKC-CLOUDEVENTS"
curl -fsS -H 'Content-Type: application/json' -H 'Accept: application/json' \
  -d '{"approvedBy":"bkc.pipeline","notes":"CloudEvents Loki demo"}' \
  "$api_base/prescriptions/${rx_id}/approve" | grep -F 'ApproveQueued' >/dev/null
curl -fsS -H 'Content-Type: application/json' -H 'Accept: application/json' \
  -d '{"refillCount":2}' \
  "$api_base/prescriptions/${rx_id}/refill" | grep -F 'RefillQueued' >/dev/null
query='{service_name=~"rx/.+"} |= "CloudEvent audit" |= "RX-BKC-CLOUDEVENTS"'
for _ in $(seq 1 30); do
  body="$(curl -fsS --get "$loki_base/loki/api/v1/query_range" \
    --data-urlencode "query=$query" \
    --data-urlencode "limit=20" \
    --data-urlencode "start=$(date -u -d '15 minutes ago' +%s)000000000" \
    --data-urlencode "end=$(date -u +%s)000000000")"
  if printf '%s' "$body" | grep -F 'CloudEvent audit' | grep -F 'RX-BKC-CLOUDEVENTS' >/dev/null; then
    printf 'loki-cloudevents-ok api=%s loki=%s query=%s\n' "$api_base" "$loki_base" "$query"
    printf '%s' "$body" | grep -o 'CloudEvent audit[^"]*' | head -5
    exit 0
  fi
  sleep 5
done
printf '%s\n' "$body"
exit 1
"""
    output = run_remote_command(
        host=server["host"],
        user="root",
        command=f"bash -lc {shlex.quote(script)}",
        timeout=300,
    )
    _set_stage(run_id, stage_name, "complete", "Loki returned CloudEvents audit records.")
    append_event(run_id, "info", stage_name, output[-2400:] if output else "loki-cloudevents-ok")


def _run_rx_demo_k3s_grafana_loki_check(run_id: str, stage_name: str) -> None:
    server = _k3s_live_node("server")
    script = r"""
set -euo pipefail
node_ip_for_endpoint() {
  ns="$1"
  svc="$2"
  node_name="$(k3s kubectl -n "$ns" get endpoints "$svc" -o jsonpath='{.subsets[0].addresses[0].nodeName}' 2>/dev/null)"
  k3s kubectl get node "$node_name" -o jsonpath='{.status.addresses[?(@.type=="InternalIP")].address}'
}
node_port_for_service() {
  ns="$1"
  svc="$2"
  port_name="$3"
  k3s kubectl -n "$ns" get svc "$svc" -o jsonpath="{.spec.ports[?(@.name==\"${port_name}\")].nodePort}"
}
grafana_base="http://$(node_ip_for_endpoint rx-observability grafana):$(node_port_for_service rx-observability grafana http)"
loki_base="http://$(node_ip_for_endpoint rx-observability loki):$(node_port_for_service rx-observability loki http)"
curl -fsS "$grafana_base/api/health" | grep -F '"database"' >/dev/null
curl -fsS "$loki_base/ready"
printf 'grafana-loki-ready grafana=%s loki=%s explore_query=%s\n' "$grafana_base" "$loki_base" '{service_name=~"rx/.+"} |= "CloudEvent audit"'
"""
    output = run_remote_command(
        host=server["host"],
        user="root",
        command=f"bash -lc {shlex.quote(script)}",
        timeout=120,
    )
    _set_stage(run_id, stage_name, "complete", "Grafana is reachable and the Loki query is ready for the demo.")
    append_event(run_id, "info", stage_name, output[-1200:] if output else "grafana-loki-ready")


def _run_rx_demo_k3s_undeploy_capture(run_id: str, stage_name: str) -> None:
    server = _k3s_live_node("server")
    script = r"""
set -euo pipefail
k3s kubectl -n rx-demo get all,pvc,secret,configmap -o wide || true
k3s kubectl -n rx-observability get deploy,svc,configmap,daemonset -o wide || true
"""
    output = run_remote_command(
        host=server["host"],
        user="root",
        command=f"bash -lc {shlex.quote(script)}",
        timeout=120,
    )
    _set_stage(run_id, stage_name, "complete", "Pre-cleanup k3s state captured.")
    append_event(run_id, "info", stage_name, output[-3000:] if output else "pre-cleanup-state-captured")


def _run_rx_demo_k3s_undeploy_demo_observability(run_id: str, stage_name: str) -> None:
    server = _k3s_live_node("server")
    configmaps = (
        "prometheus-k3s-config loki-config tempo-config grafana-datasources "
        "grafana-dashboard-provider rx-overview-dashboard rx-service-flow-dashboard "
        "rx-executive-health-dashboard rx-executive-flow-grafmaid-dashboard "
        "rx-grafmaid-probe-dashboard rx-traffic-map-grafmaid-dashboard rx-tempo-traces-dashboard"
    )
    script = "\n".join(
        [
            "set -euo pipefail",
            "k3s kubectl -n rx-observability delete deploy grafana loki prometheus tempo --ignore-not-found=true",
            "k3s kubectl -n rx-observability delete svc grafana loki prometheus tempo --ignore-not-found=true",
            f"k3s kubectl -n rx-observability delete configmap {configmaps} --ignore-not-found=true",
            "k3s kubectl -n rx-observability delete serviceaccount prometheus --ignore-not-found=true",
            "k3s kubectl delete clusterrole rx-demo-prometheus-discovery --ignore-not-found=true",
            "k3s kubectl delete clusterrolebinding rx-demo-prometheus-discovery --ignore-not-found=true",
            "k3s kubectl -n rx-observability get daemonset,svc -o wide || true",
        ]
    )
    output = run_remote_command(
        host=server["host"],
        user="root",
        command=f"bash -lc {shlex.quote(script)}",
        timeout=300,
    )
    _set_stage(run_id, stage_name, "complete", "Demo observability resources removed.")
    append_event(run_id, "info", stage_name, output[-3000:] if output else "demo-observability-removed")


def _run_rx_demo_k3s_undeploy_namespace(run_id: str, stage_name: str) -> None:
    server = _k3s_live_node("server")
    script = "\n".join(
        [
            "set -euo pipefail",
            "k3s kubectl delete namespace rx-demo --ignore-not-found=true --timeout=240s",
            "for _ in $(seq 1 30); do",
            "  if ! k3s kubectl get namespace rx-demo >/dev/null 2>&1; then echo rx-demo-namespace-absent; exit 0; fi",
            "  sleep 2",
            "done",
            "k3s kubectl get namespace rx-demo",
            "exit 1",
        ]
    )
    output = run_remote_command(
        host=server["host"],
        user="root",
        command=f"bash -lc {shlex.quote(script)}",
        timeout=300,
    )
    _set_stage(run_id, stage_name, "complete", "Rx-demo namespace removed.")
    append_event(run_id, "info", stage_name, output[-1600:] if output else "rx-demo-namespace-absent")


def _run_rx_demo_k3s_undeploy_verify(run_id: str, stage_name: str) -> None:
    server = _k3s_live_node("server")
    script = r"""
set -euo pipefail
if k3s kubectl get namespace rx-demo >/dev/null 2>&1; then
  echo "rx-demo namespace still exists"
  exit 1
fi
for deploy in grafana loki prometheus tempo; do
  if k3s kubectl -n rx-observability get deploy "$deploy" >/dev/null 2>&1; then
    echo "demo observability deployment still exists: $deploy"
    exit 1
  fi
done
k3s kubectl -n rx-observability get daemonset telegraf-k3s-host cadvisor-k3s -o name
printf 'rx-demo-cleanup-verified\n'
"""
    output = run_remote_command(
        host=server["host"],
        user="root",
        command=f"bash -lc {shlex.quote(script)}",
        timeout=120,
    )
    _set_stage(run_id, stage_name, "complete", "Cleanup verification passed.")
    append_event(run_id, "info", stage_name, output[-1600:] if output else "rx-demo-cleanup-verified")


def _run_rx_demo_k3s_undeploy_registry(run_id: str, stage_name: str, settings: dict[str, str]) -> None:
    command = f"bash -lc 'set -euo pipefail; curl -fsS http://127.0.0.1:{DEMO_REGISTRY_PORT}/v2/_catalog'"
    output = run_remote_command(
        host=settings["manager_host"],
        user=settings["manager_user"],
        password=settings["manager_password"],
        command=command,
        timeout=60,
    )
    _set_stage(run_id, stage_name, "complete", "Local registry remains available.")
    append_event(run_id, "info", stage_name, output[-1200:] if output else "registry-retained")


def _controller_file_b64(settings: dict[str, str], path: str) -> str:
    return run_remote_command(
        host=settings["controller_host"],
        user=settings["controller_user"],
        password=settings["controller_password"],
        command=f"base64 -w0 {shlex.quote(path)}",
        timeout=60,
    )


def _run_auzix_vm130_deploy(run_id: str, stage_name: str, settings: dict[str, str]) -> None:
    start_sequence = b64decode(
        _controller_file_b64(
            settings,
            f"{AUZIX_VM130_SOURCE_ROOT}/System/Boot/StartSequence",
        )
    )
    mdev_config = b64decode(
        _controller_file_b64(
            settings,
            f"{AUZIX_VM130_SOURCE_ROOT}/System/Settings/mdev.conf",
        )
    )
    midori_wrapper = b64decode(
        _controller_file_b64(
            settings,
            f"{AUZIX_VM130_SOURCE_ROOT}/Programs/Midori/11.8/Commands/midori",
        )
    )
    midori_nss_trust = b64decode(
        _controller_file_b64(
            settings,
            f"{AUZIX_VM130_SOURCE_ROOT}/Programs/Midori/11.8/Resources/midori/libnssckbi.so",
        )
    )
    commit = run_remote_command(
        host=settings["controller_host"],
        user=settings["controller_user"],
        password=settings["controller_password"],
        command="cat /srv/nfs/swarm/AuziX/src/.auzix-commit",
        timeout=30,
    ).strip()
    remote_payloads = {
        f"/Work/Temp/StartSequence.{commit}": start_sequence,
        f"/Work/Temp/mdev.conf.{commit}": mdev_config,
        f"/Work/Temp/midori.{commit}": midori_wrapper,
        f"/Work/Temp/libnssckbi.so.{commit}": midori_nss_trust,
    }
    run_remote_command(
        host=AUZIX_VM130_HOST,
        user="root",
        command="/Programs/BusyBox/1.36.1/Commands/busybox mkdir -p /Work/Temp /System/State/deployments",
        timeout=30,
    )
    for remote_path, content in remote_payloads.items():
        upload_remote_bytes(
            host=AUZIX_VM130_HOST,
            user="root",
            remote_path=remote_path,
            content=content,
            mode=0o755,
            timeout=60,
        )
    script = "\n".join(
        [
            "set -eu",
            "BB=/Programs/BusyBox/1.36.1/Commands/busybox",
            f'[ -f /System/Boot/StartSequence.pre-{commit} ] || "${{BB}}" cp -p /System/Boot/StartSequence /System/Boot/StartSequence.pre-{commit}',
            f'[ -f /System/Settings/mdev.conf.pre-{commit} ] || "${{BB}}" cp -p /System/Settings/mdev.conf /System/Settings/mdev.conf.pre-{commit}',
            f'[ -f /Programs/Midori/11.8/Commands/midori.pre-{commit} ] || "${{BB}}" cp -p /Programs/Midori/11.8/Commands/midori /Programs/Midori/11.8/Commands/midori.pre-{commit}',
            f'"${{BB}}" cp -f /Work/Temp/StartSequence.{commit} /System/Boot/StartSequence',
            '"${BB}" chmod 0755 /System/Boot/StartSequence',
            f'"${{BB}}" cp -f /Work/Temp/mdev.conf.{commit} /System/Settings/mdev.conf',
            '"${BB}" chmod 0644 /System/Settings/mdev.conf',
            f'"${{BB}}" cp -f /Work/Temp/midori.{commit} /Programs/Midori/11.8/Commands/midori',
            '"${BB}" chmod 0755 /Programs/Midori/11.8/Commands/midori',
            f'"${{BB}}" cp -f /Work/Temp/libnssckbi.so.{commit} /Programs/Midori/11.8/Resources/midori/libnssckbi.so',
            '"${BB}" chmod 0755 /Programs/Midori/11.8/Resources/midori/libnssckbi.so',
            '"${BB}" chmod 0666 /dev/random /dev/urandom',
            "/System/Tools/repair-e-state /Users/auzix auzix",
            '"${BB}" chown -R 1000:1000 /Users/auzix/.cache /Users/auzix/.config /Users/auzix/.local',
            '"${BB}" chmod -R u+rwX /Users/auzix/.cache /Users/auzix/.config /Users/auzix/.local',
            f"printf 'source=github.com/auzieman/AuziX\\ncommit={commit}\\ntarget=vmid130\\n' >/System/State/deployments/auzix-{commit}.txt",
            f'"${{BB}}" rm -f /Work/Temp/StartSequence.{commit} /Work/Temp/mdev.conf.{commit} /Work/Temp/midori.{commit} /Work/Temp/libnssckbi.so.{commit}',
            f"echo auzix-vm130-deployed commit={commit}",
        ]
    )
    output = run_remote_command(
        host=AUZIX_VM130_HOST,
        user="root",
        command=f"/System/Compatibility/bin/sh -c {shlex.quote(script)}",
        timeout=180,
    )
    _store_run_extra(run_id, {"target_host": AUZIX_VM130_HOST, "deployed_commit": commit})
    _set_stage(run_id, stage_name, "complete", "AuziX runtime payload deployed to VM130.")
    append_event(run_id, "info", stage_name, output[-1200:] if output else f"deployed {commit}")


def _run_auzix_vm130_validate(run_id: str, stage_name: str) -> None:
    script = "\n".join(
        [
            "set -eu",
            "BB=/Programs/BusyBox/1.36.1/Commands/busybox",
            'grep -F "chown -R 1000:1000" /System/Boot/StartSequence >/dev/null',
            'grep -E "^random[[:space:]]+0:0[[:space:]]+0666$" /System/Settings/mdev.conf >/dev/null',
            'grep -E "^urandom[[:space:]]+0:0[[:space:]]+0666$" /System/Settings/mdev.conf >/dev/null',
            'grep -F "Midori profile directories are not writable" /Programs/Midori/current/Commands/midori >/dev/null',
            'test -s /Programs/Midori/current/Resources/midori/libnssckbi.so',
            '"${BB}" su auzix -c "test -w /Users/auzix/.cache && test -w /Users/auzix/.config && test -w /Users/auzix/.local"',
            '"${BB}" su auzix -c "\\"${BB}\\" dd if=/dev/urandom of=/dev/null bs=1 count=1" >/dev/null 2>&1',
            '"${BB}" nslookup example.com >/dev/null',
            "/Programs/Curl/current/Commands/curl -fsS --max-time 15 https://example.com >/dev/null",
            "echo auzix-vm130-network-contract=pass",
        ]
    )
    output = run_remote_command(
        host=AUZIX_VM130_HOST,
        user="root",
        command=f"/System/Compatibility/bin/sh -c {shlex.quote(script)}",
        timeout=120,
    )
    _set_stage(run_id, stage_name, "complete", "VM130 browser networking and permissions contract passed.")
    append_event(run_id, "info", stage_name, output[-1200:] if output else "vm130 validation passed")


def _run_lab_storage_command(hosts: list[str], command: str, *, timeout: int = 120) -> dict[str, str]:
    results: dict[str, str] = {}
    for host in hosts:
        results[host] = run_remote_command(
            host=host,
            user="root",
            command=command,
            timeout=timeout,
        )
    return results


def _run_lab_storage_preflight(run_id: str, stage_name: str) -> None:
    command = (
        "set -e; "
        "findmnt -n -o SOURCE /; "
        "lvs --noheadings -o lv_size; "
        "vgs --noheadings --units g -o vg_free"
    )
    results = _run_lab_storage_command(LAB_STORAGE_ALL_HOST_LIST, command, timeout=120)
    _set_stage(run_id, stage_name, "complete", "All cluster guests have LVM-backed roots and sufficient capacity.")
    append_event(run_id, "info", stage_name, json.dumps(results, sort_keys=True))


def _run_lab_storage_grow(run_id: str, stage_name: str, hosts: list[str]) -> None:
    command = (
        "set -e; "
        "lv=$(lvs --noheadings -o lv_path | xargs); "
        "bytes=$(findmnt -bn -o SIZE /); "
        f"[ \"$bytes\" -ge {LAB_STORAGE_MIN_ROOT_BYTES} ] || lvextend -r -L 50G \"$lv\"; "
        "df -hT /"
    )
    results = _run_lab_storage_command(hosts, command, timeout=300)
    _set_stage(run_id, stage_name, "complete", "Root filesystems meet the 50 GiB target.")
    append_event(run_id, "info", stage_name, json.dumps(results, sort_keys=True))


def _run_lab_storage_verify(run_id: str, stage_name: str) -> None:
    command = (
        "set -e; "
        "bytes=$(findmnt -bn -o SIZE /); "
        f"[ \"$bytes\" -ge {LAB_STORAGE_MIN_ROOT_BYTES} ]; "
        "df -hT /; "
        "vgs --noheadings -o vg_name,vg_free"
    )
    results = _run_lab_storage_command(LAB_STORAGE_ALL_HOST_LIST, command, timeout=120)
    _set_stage(run_id, stage_name, "complete", "Cluster storage expansion contract passed.")
    append_event(run_id, "info", stage_name, json.dumps(results, sort_keys=True))


def _k3s_live_command(host: str, command: str, *, timeout: int = 120) -> str:
    return run_remote_command(host=host, user="root", command=command, timeout=timeout)


def _run_demo_registry_k3s_dns(run_id: str, stage_name: str) -> None:
    results = {}
    for node in K3S_LIVE_NODES:
        host = str(node["host"])
        results[host] = _k3s_live_command(host, f"getent hosts {shlex.quote(DEMO_REGISTRY_HOST)}", timeout=60)
    _set_stage(run_id, stage_name, "complete", "K3s nodes resolve swarm1.lab.auzietek.com.")
    append_event(run_id, "info", stage_name, json.dumps(results, sort_keys=True))


def _registry_yaml() -> str:
    return (
        "mirrors:\n"
        f"  \"{DEMO_REGISTRY}\":\n"
        "    endpoint:\n"
        f"      - \"{DEMO_REGISTRY_URL}\"\n"
    )


def _apply_k3s_registry_mirror(host: str, *, timeout: int = 180) -> str:
    content = _registry_yaml()
    command = (
        "python3 - <<'PY'\n"
        "from pathlib import Path\n"
        "Path('/etc/rancher/k3s').mkdir(parents=True, exist_ok=True)\n"
        f"Path('/etc/rancher/k3s/registries.yaml').write_text({content!r})\n"
        "PY\n"
        "if systemctl is-active --quiet k3s; then systemctl restart k3s; "
        "elif systemctl is-active --quiet k3s-agent; then systemctl restart k3s-agent; "
        "else echo k3s-service-not-active; exit 1; fi; "
        "echo registry-mirror-ready"
    )
    return _k3s_live_command(host, command, timeout=timeout)


def _run_demo_registry_k3s_trust(run_id: str, stage_name: str) -> None:
    results = {}
    for node in K3S_LIVE_NODES:
        host = str(node["host"])
        results[host] = _apply_k3s_registry_mirror(host, timeout=240)
    _set_stage(run_id, stage_name, "complete", "Existing k3s nodes trust the local registry mirror.")
    append_event(run_id, "info", stage_name, json.dumps(results, sort_keys=True))


def _run_demo_registry_k3s_pull(run_id: str, stage_name: str) -> None:
    results = {}
    command = f"k3s ctr images pull --plain-http {shlex.quote(DEMO_REGISTRY_SMOKE_IMAGE)}"
    for node in K3S_LIVE_NODES:
        host = str(node["host"])
        results[host] = _k3s_live_command(host, command, timeout=240)[-800:]
    _set_stage(run_id, stage_name, "complete", "Existing k3s nodes pulled the smoke image.")
    append_event(run_id, "info", stage_name, json.dumps(results, sort_keys=True))


def _run_rx_demo_registry_preflight_build_push(run_id: str, stage_name: str, settings: dict[str, str]) -> None:
    tag = run_id.split("-", 1)[0]
    local_image = f"127.0.0.1:{DEMO_REGISTRY_PORT}/rx-demo/preflight:{tag}"
    registry_image = f"{DEMO_REGISTRY}/rx-demo/preflight:{tag}"
    command = (
        "bash -lc 'set -euo pipefail; "
        f"cd {shlex.quote(RX_DEMO_SHARED_SOURCE)}; "
        "digest=$(find src k8s -type f -not -path \"*/bin/*\" -not -path \"*/obj/*\" -print0 2>/dev/null "
        "| sort -z | xargs -0 sha256sum 2>/dev/null | sha256sum | awk \"{print \\$1}\"); "
        "work=$(mktemp -d); "
        "trap \"rm -rf \\\"$work\\\"\" EXIT; "
        "cat >\"$work/Dockerfile\" <<EOF\n"
        "FROM busybox:latest\n"
        "ARG RX_DEMO_SOURCE_DIGEST\n"
        "LABEL org.opencontainers.image.title=rx-demo-preflight\n"
        "LABEL com.auzietek.rx-demo.source-digest=\\$RX_DEMO_SOURCE_DIGEST\n"
        "CMD [\"sh\", \"-c\", \"echo rx-demo-preflight\"]\n"
        "EOF\n"
        f"docker build --build-arg RX_DEMO_SOURCE_DIGEST=\"$digest\" -t {shlex.quote(local_image)} \"$work\" >/dev/null; "
        f"docker push {shlex.quote(local_image)}; "
        "printf \"image=%s\\nsource_digest=%s\\n\" "
        f"{shlex.quote(registry_image)} \"$digest\"'"
    )
    output = run_remote_command(
        host=settings["manager_host"],
        user=settings["manager_user"],
        password=settings["manager_password"],
        command=command,
        timeout=420,
    )
    source_digest = ""
    for line in output.splitlines():
        if line.startswith("source_digest="):
            source_digest = line.split("=", 1)[1].strip()
    _store_run_extra(
        run_id,
        {
            "rx_demo_preflight_image": registry_image,
            "rx_demo_preflight_tag": tag,
            "rx_demo_source_digest": source_digest,
        },
    )
    _set_stage(run_id, stage_name, "complete", "Rx-demo preflight image pushed to the local registry.")
    append_event(run_id, "info", stage_name, output[-1600:] if output else registry_image)


def _run_rx_demo_registry_preflight_catalog(run_id: str, stage_name: str, settings: dict[str, str]) -> None:
    run = get_run(run_id) or {}
    extra = run.get("extra", {}) or {}
    tag = str(extra.get("rx_demo_preflight_tag") or run_id.split("-", 1)[0]).strip()
    command = (
        "bash -lc 'set -euo pipefail; "
        "catalog=$(curl -fsS http://127.0.0.1:5001/v2/_catalog); "
        "tags=$(curl -fsS http://127.0.0.1:5001/v2/rx-demo/preflight/tags/list); "
        f"printf \"%s\" \"$tags\" | grep -F {shlex.quote(tag)} >/dev/null; "
        "printf \"catalog=%s\\ntags=%s\\n\" \"$catalog\" \"$tags\"'"
    )
    output = run_remote_command(
        host=settings["manager_host"],
        user=settings["manager_user"],
        password=settings["manager_password"],
        command=command,
        timeout=60,
    )
    _set_stage(run_id, stage_name, "complete", "Registry catalog includes the rx-demo preflight image tag.")
    append_event(run_id, "info", stage_name, output[-1200:] if output else f"rx-demo/preflight:{tag}")


def _demo_add_node_target(run_id: str) -> dict:
    run = get_run(run_id) or {}
    extra = run.get("extra", {}) or {}
    target = dict(extra.get("demo_k3s_new_worker") or {})
    if target:
        return target
    target_host = str(extra.get("target_host", "")).strip()
    if target_host in {str(node["host"]) for node in K3S_LIVE_NODES}:
        raise PipelineExecutionError(f"{target_host} is already part of the known k3s cluster.")
    target_name = str(extra.get("target_name", "")).strip() or target_host or "kube3.lab.auzietek.com"
    short = target_name.split(".", 1)[0] if target_name else target_host
    target = {"host": target_host, "name": target_name, "short": short, "role": "agent"}
    _store_run_extra(run_id, {"demo_k3s_new_worker": target})
    return target


def _demo_k3s_source_vmid(run_id: str) -> int:
    run = get_run(run_id) or {}
    extra = run.get("extra", {}) or {}
    raw = str(extra.get("source_vmid") or extra.get("template_vmid") or "").strip()
    if raw:
        try:
            return int(raw)
        except ValueError as exc:
            raise PipelineExecutionError(f"Invalid source VMID for k3s worker clone: {raw!r}") from exc
    return DEMO_K3S_SOURCE_VMID


def _select_demo_k3s_source_template(run_id: str) -> dict:
    wanted_vmid = _demo_k3s_source_vmid(run_id)
    client = ProxmoxClient(load_proxmox_config())
    for node in client.nodes():
        node_name = str(node.get("node", "")).strip()
        if not node_name:
            continue
        for vm in client.list_qemu(node_name):
            try:
                vmid = int(vm.get("vmid") or 0)
            except (TypeError, ValueError):
                vmid = 0
            if vmid == wanted_vmid:
                record = dict(vm)
                record["node"] = record.get("node", node_name)
                return record
    snapshot = load_proxmox_snapshot() or {}
    for vm in snapshot.get("virtual_machines", []):
        try:
            vmid = int(vm.get("vmid") or 0)
        except (TypeError, ValueError):
            vmid = 0
        if vmid == wanted_vmid:
            return dict(vm)
    raise PipelineExecutionError(f"Fedora 44 Proxmox source VMID {wanted_vmid} was not found in inventory.")


def _run_demo_k3s_add_node_select(run_id: str, stage_name: str) -> None:
    target = _demo_add_node_target(run_id)
    template = _select_demo_k3s_source_template(run_id)
    proxmox_target = _select_proxmox_target(ProxmoxClient(load_proxmox_config()))
    plan = _k3s_plan()
    plan["nodes"] = [
        {
            "name": target["name"],
            "short": target["short"],
            "role": "agent",
        }
    ]
    _store_run_extra(
        run_id,
        {
            "demo_k3s_new_worker": target,
            "k3s_template": template,
            "k3s_target": proxmox_target,
            "k3s_plan": plan,
        },
    )
    source = f"{template.get('name')} (vmid {template.get('vmid')})"
    _set_stage(run_id, stage_name, "complete", f"Selected {target['short']} worker from Fedora source {source}.")
    append_event(run_id, "info", stage_name, json.dumps({"target": target, "source": template}, sort_keys=True))


def _run_demo_k3s_add_node_clone(run_id: str, stage_name: str) -> None:
    target = _demo_add_node_target(run_id)
    if str(target.get("host") or "").strip():
        _set_stage(run_id, stage_name, "complete", "Existing worker host was supplied; clone step skipped.")
        append_event(run_id, "info", stage_name, json.dumps({"existing_host": target}, sort_keys=True))
        return
    run = get_run(run_id) or {}
    extra = run.get("extra", {}) or {}
    plan = dict(extra.get("k3s_plan") or _k3s_plan())
    clone_node = {
        "name": str(target["name"]),
        "short": str(target["short"]),
        "role": "agent",
        "cloudinit": {"ci_user": "root", "ipconfig0": "ip=dhcp"},
    }
    _store_run_extra(run_id, {"k3s_clone_plan": [clone_node]})
    _run_k3s_proxmox_clone(run_id, stage_name)


def _run_demo_k3s_add_node_boot(run_id: str, stage_name: str) -> None:
    target = _demo_add_node_target(run_id)
    if str(target.get("host") or "").strip():
        _set_stage(run_id, stage_name, "complete", "Existing worker host was supplied; boot step skipped.")
        append_event(run_id, "info", stage_name, json.dumps({"existing_host": target}, sort_keys=True))
        return
    _run_k3s_proxmox_start(run_id, stage_name)


def _run_demo_k3s_add_node_discover(run_id: str, stage_name: str) -> None:
    target = _demo_add_node_target(run_id)
    if str(target.get("host") or "").strip():
        _set_stage(run_id, stage_name, "complete", "Existing worker host was supplied; SSH discovery skipped.")
        append_event(run_id, "info", stage_name, json.dumps({"existing_host": target}, sort_keys=True))
        return
    _run_k3s_discover_ssh(run_id, stage_name)
    nodes = _k3s_nodes(run_id)
    if nodes:
        node = nodes[0]
        updated_target = {**target, "host": str(node.get("ip") or "").strip()}
        _store_run_extra(run_id, {"demo_k3s_new_worker": updated_target})


def _run_demo_k3s_add_node_ssh(run_id: str, stage_name: str) -> None:
    target = _demo_add_node_target(run_id)
    command = (
        "set -e; hostname; "
        "if systemctl is-active --quiet k3s 2>/dev/null || systemctl is-active --quiet k3s-agent 2>/dev/null; then "
        "echo already-k3s-node; exit 2; fi; "
        "echo ssh-ready"
    )
    output = _k3s_live_command(str(target["host"]), command, timeout=120)
    _set_stage(run_id, stage_name, "complete", "Target SSH preflight passed.")
    append_event(run_id, "info", stage_name, output[-800:] if output else "ssh-ready")


def _run_demo_k3s_add_node_base(run_id: str, stage_name: str) -> None:
    target = _demo_add_node_target(run_id)
    ssh = load_integrations()["ssh"]
    key_info = read_key_pair(ssh["private_key_path"], ssh["public_key_path"])
    public_key = str(key_info.get("public_key") or "").strip()
    key_install = ""
    if public_key:
        quoted_key = shlex.quote(public_key)
        key_install = (
            "mkdir -p /root/.ssh; chmod 700 /root/.ssh; "
            f"grep -qxF {quoted_key} /root/.ssh/authorized_keys 2>/dev/null || "
            f"printf '%s\\n' {quoted_key} >> /root/.ssh/authorized_keys; "
            "chmod 600 /root/.ssh/authorized_keys; "
        )
    command = (
        "set -euo pipefail; "
        f"hostnamectl set-hostname {shlex.quote(str(target['name']))} || true; "
        f"{key_install}"
        "dnf -y install curl jq tar iptables-nft container-selinux qemu-guest-agent; "
        "systemctl enable --now qemu-guest-agent || true; "
        "swapoff -a || true; "
        "sed -ri.bkc-k3s \"/\\sswap\\s/s/^/#/\" /etc/fstab || true; "
        "modprobe br_netfilter || true; modprobe overlay || true; "
        "printf \"overlay\\nbr_netfilter\\n\" >/etc/modules-load.d/k3s.conf; "
        "printf \"net.bridge.bridge-nf-call-iptables = 1\\nnet.ipv4.ip_forward = 1\\nnet.bridge.bridge-nf-call-ip6tables = 1\\n\" >/etc/sysctl.d/90-k3s.conf; "
        "sysctl --system >/dev/null; "
        "if command -v firewall-cmd >/dev/null 2>&1; then "
        "firewall-cmd --permanent --add-port=10250/tcp || true; "
        "firewall-cmd --permanent --add-port=8472/udp || true; "
        "firewall-cmd --reload || true; "
        "fi; "
        "echo k3s-worker-base-ready"
    )
    output = _k3s_live_command(str(target["host"]), command, timeout=1800)
    _set_stage(run_id, stage_name, "complete", "Target OS prerequisites are ready.")
    append_event(run_id, "info", stage_name, output[-1000:] if output else "k3s-worker-base-ready")


def _run_demo_k3s_add_node_token(run_id: str, stage_name: str) -> None:
    server_host = str(K3S_LIVE_NODES[0]["host"])
    token = _k3s_live_command(server_host, "cat /var/lib/rancher/k3s/server/node-token", timeout=120).strip()
    if not token:
        raise PipelineExecutionError("K3s server did not return a join token.")
    _store_run_extra(run_id, {"k3s_api_url": f"https://{server_host}:6443", "k3s_join_token": token})
    _set_stage(run_id, stage_name, "complete", "Join token captured.")
    append_event(run_id, "info", stage_name, "Join token captured from kube1.")


def _run_demo_k3s_add_node_agent(run_id: str, stage_name: str) -> None:
    target = _demo_add_node_target(run_id)
    run = get_run(run_id) or {}
    extra = run.get("extra", {}) or {}
    token = str(extra.get("k3s_join_token", "")).strip()
    server_url = str(extra.get("k3s_api_url") or f"https://{K3S_LIVE_NODES[0]['host']}:6443")
    if not token:
        raise PipelineExecutionError("K3s join token is missing.")
    server_host = str(K3S_LIVE_NODES[0]["host"])
    k3s_version = _k3s_live_command(
        server_host,
        "k3s --version | awk 'NR==1{print $3}'",
        timeout=120,
    ).strip()
    if not k3s_version:
        raise PipelineExecutionError("K3s server did not return its install version.")
    content = _registry_yaml()
    command = (
        "set -euo pipefail; "
        "mkdir -p /etc/rancher/k3s; "
        "python3 - <<'PY'\n"
        "from pathlib import Path\n"
        f"Path('/etc/rancher/k3s/registries.yaml').write_text({content!r})\n"
        "PY\n"
        "curl -sfL https://get.k3s.io -o /tmp/install-k3s.sh; "
        "chmod +x /tmp/install-k3s.sh; "
        f"INSTALL_K3S_VERSION={shlex.quote(k3s_version)} "
        f"K3S_URL={shlex.quote(server_url)} K3S_TOKEN={shlex.quote(token)} "
        "/tmp/install-k3s.sh agent "
        f"--node-name {shlex.quote(str(target['short']))}; "
        "systemctl is-active --quiet k3s-agent; "
        "echo k3s-agent-ready"
    )
    output = _k3s_live_command(str(target["host"]), command, timeout=1200)
    _store_run_extra(run_id, {"k3s_join_token": "", "k3s_join_token_used": True, "k3s_install_version": k3s_version})
    _set_stage(run_id, stage_name, "complete", "Target worker joined the k3s cluster.")
    append_event(run_id, "info", stage_name, output[-1000:] if output else "k3s-agent-ready")


def _run_demo_k3s_add_node_verify(run_id: str, stage_name: str) -> None:
    target = _demo_add_node_target(run_id)
    node_name = str(target["short"])
    quoted_node = shlex.quote(node_name)
    quoted_node_ref = shlex.quote(f"node/{node_name}")
    command = (
        "bash -lc 'set -euo pipefail; "
        "for _ in $(seq 1 90); do "
        f"k3s kubectl get node {quoted_node} >/dev/null 2>&1 && break; "
        "sleep 5; "
        "done; "
        f"k3s kubectl wait --for=condition=Ready {quoted_node_ref} --timeout=180s; "
        "k3s kubectl get nodes -o wide'"
    )
    output = _k3s_live_command(str(K3S_LIVE_NODES[0]["host"]), command, timeout=600)
    _set_stage(run_id, stage_name, "complete", "New worker reports Ready.")
    append_event(run_id, "info", stage_name, output[-1200:] if output else "node-ready")


def _run_demo_k3s_add_node_registry(run_id: str, stage_name: str) -> None:
    target = _demo_add_node_target(run_id)
    _apply_k3s_registry_mirror(str(target["host"]), timeout=240)
    output = _k3s_live_command(
        str(target["host"]),
        f"k3s ctr images pull --plain-http {shlex.quote(DEMO_REGISTRY_SMOKE_IMAGE)}",
        timeout=240,
    )
    _set_stage(run_id, stage_name, "complete", "New worker registry mirror is configured.")
    append_event(run_id, "info", stage_name, output[-1000:] if output else "registry-smoke-pulled")


def _run_demo_k3s_add_node_register(run_id: str, stage_name: str) -> None:
    target = _demo_add_node_target(run_id)
    nodes = []
    try:
        nodes = _k3s_nodes(run_id)
    except PipelineExecutionError:
        nodes = []
    node_record = next((node for node in nodes if str(node.get("short") or "") == str(target.get("short") or "")), None)
    registered = {**target}
    if node_record:
        registered.update(
            {
                "host": str(target.get("host") or node_record.get("ip") or "").strip(),
                "ip": str(node_record.get("ip") or target.get("host") or "").strip(),
                "vmid": node_record.get("vmid"),
                "proxmox_node": node_record.get("proxmox_node"),
                "mac": node_record.get("mac"),
            }
        )
    _store_run_extra(run_id, {"demo_k3s_added_worker": registered})
    summary = (
        f"Registered {registered.get('name') or registered.get('short')} "
        f"at {registered.get('host') or registered.get('ip') or 'unknown-ip'}"
    )
    if registered.get("vmid"):
        summary += f" (VMID {registered['vmid']})"
    _set_stage(run_id, stage_name, "complete", summary + ".")
    append_event(run_id, "info", stage_name, json.dumps({"added_worker": registered}, sort_keys=True))


def _demo_add_node_reset_target(run_id: str) -> dict:
    run = get_run(run_id) or {}
    extra = run.get("extra", {}) or {}
    target = dict(extra.get("demo_k3s_added_worker") or extra.get("demo_k3s_new_worker") or {})
    nodes = [dict(node) for node in extra.get("k3s_nodes") or []]
    if nodes:
        node = nodes[0]
        target = {
            **target,
            "name": target.get("name") or node.get("name"),
            "short": target.get("short") or node.get("short"),
            "host": target.get("host") or node.get("ip") or node.get("name"),
            "ip": target.get("ip") or node.get("ip"),
            "vmid": target.get("vmid") or node.get("vmid"),
            "proxmox_node": target.get("proxmox_node") or node.get("proxmox_node"),
            "mac": target.get("mac") or node.get("mac"),
        }
    if not target:
        target_name = str(extra.get("target_name", "")).strip() or "kube3.lab.auzietek.com"
        target = {"name": target_name, "short": target_name.split(".", 1)[0], "role": "agent"}
    if not str(target.get("short") or "").strip():
        name = str(target.get("name") or "").strip()
        target["short"] = name.split(".", 1)[0] if name else ""
    if not str(target.get("name") or "").strip() and str(target.get("short") or "").strip():
        target["name"] = f"{target['short']}.lab.auzietek.com"
    return target


def _find_demo_worker_vm(client: ProxmoxClient, target: dict) -> dict:
    vmid = int(target.get("vmid") or 0)
    proxmox_node = str(target.get("proxmox_node") or "").strip()
    if vmid and proxmox_node:
        return {"vmid": vmid, "node": proxmox_node, "name": target.get("name") or target.get("short")}
    names = {str(target.get("name") or "").strip(), str(target.get("short") or "").strip()}
    names = {name for name in names if name}
    for node in client.nodes():
        node_name = str(node.get("node", "")).strip()
        if not node_name:
            continue
        for vm in client.list_qemu(node_name):
            if str(vm.get("name") or "").strip() in names:
                return {**dict(vm), "node": node_name}
    return {}


def _run_demo_k3s_add_node_reset_select(run_id: str, stage_name: str) -> None:
    target = _demo_add_node_reset_target(run_id)
    client = ProxmoxClient(load_proxmox_config())
    vm = _find_demo_worker_vm(client, target)
    if vm:
        target.update({"vmid": vm.get("vmid"), "proxmox_node": vm.get("node")})
    _store_run_extra(run_id, {"demo_k3s_reset_target": target})
    _set_stage(run_id, stage_name, "complete", "Demo worker reset target selected.")
    append_event(run_id, "info", stage_name, json.dumps({"reset_target": target}, sort_keys=True))


def _run_demo_k3s_add_node_reset_k3s(run_id: str, stage_name: str) -> None:
    target = _demo_add_node_reset_target(run_id)
    node_name = str(target.get("short") or target.get("name") or "").split(".", 1)[0]
    if not node_name:
        raise PipelineExecutionError("Demo worker node name is missing.")
    quoted_node = shlex.quote(node_name)
    command = (
        "bash -lc 'set -euo pipefail; "
        f"if ! k3s kubectl get node {quoted_node} >/dev/null 2>&1; then echo node-already-absent; exit 0; fi; "
        f"k3s kubectl cordon {quoted_node} || true; "
        f"k3s kubectl drain {quoted_node} --ignore-daemonsets --delete-emptydir-data --force --timeout=180s || true; "
        f"k3s kubectl delete node {quoted_node}; "
        f"if k3s kubectl get node {quoted_node} >/dev/null 2>&1; then exit 1; fi; "
        "echo k3s-node-removed'"
    )
    output = _k3s_live_command(str(K3S_LIVE_NODES[0]["host"]), command, timeout=600)
    _set_stage(run_id, stage_name, "complete", f"Removed {node_name} from k3s or confirmed it was absent.")
    append_event(run_id, "info", stage_name, output[-1200:] if output else "k3s-node-removed")


def _run_demo_k3s_add_node_reset_vm(run_id: str, stage_name: str) -> None:
    target = _demo_add_node_reset_target(run_id)
    client = ProxmoxClient(load_proxmox_config())
    vm = _find_demo_worker_vm(client, target)
    if not vm:
        _set_stage(run_id, stage_name, "complete", "Demo worker VM is already absent.")
        append_event(run_id, "info", stage_name, json.dumps({"reset_target": target, "vm": "absent"}, sort_keys=True))
        return
    proxmox_node = str(vm.get("node") or "").strip()
    vmid = int(vm.get("vmid") or 0)
    if not proxmox_node or not vmid:
        raise PipelineExecutionError(f"Demo worker VM metadata is incomplete: {vm!r}")
    status = client.vm_status(proxmox_node, vmid)
    if str(status.get("status") or "").strip().lower() == "running":
        upid = client.stop_vm(proxmox_node, vmid, timeout=60)
        task = client.wait_for_task(proxmox_node, str(upid), timeout=180)
        exit_status = str(task.get("exitstatus", ""))
        if exit_status and exit_status != "OK":
            raise PipelineExecutionError(f"Proxmox stop failed for VMID {vmid}: {exit_status}")
        client.wait_for_vm_status(proxmox_node, vmid, "stopped", timeout=120)
    upid = client.destroy_vm(proxmox_node, vmid, purge=True)
    task = client.wait_for_task(proxmox_node, str(upid), timeout=300)
    exit_status = str(task.get("exitstatus", ""))
    if exit_status and exit_status != "OK":
        raise PipelineExecutionError(f"Proxmox destroy failed for VMID {vmid}: {exit_status}")
    _set_stage(run_id, stage_name, "complete", f"Destroyed demo worker VMID {vmid}.")
    append_event(run_id, "info", stage_name, json.dumps({"destroyed": {"node": proxmox_node, "vmid": vmid}}, sort_keys=True))


def _run_demo_k3s_add_node_reset_verify(run_id: str, stage_name: str) -> None:
    target = _demo_add_node_reset_target(run_id)
    node_name = str(target.get("short") or target.get("name") or "").split(".", 1)[0]
    quoted_node = shlex.quote(node_name)
    kubectl = _k3s_live_command(
        str(K3S_LIVE_NODES[0]["host"]),
        f"bash -lc 'if k3s kubectl get node {quoted_node} >/dev/null 2>&1; then exit 1; fi; echo k3s-node-absent'",
        timeout=120,
    )
    client = ProxmoxClient(load_proxmox_config())
    vm = _find_demo_worker_vm(client, target)
    if vm:
        raise PipelineExecutionError(f"Demo worker VM still exists: {vm}")
    _set_stage(run_id, stage_name, "complete", "Demo worker reset verified.")
    append_event(run_id, "info", stage_name, kubectl[-800:] if kubectl else "reset-verified")


def _run_stage_plan(run_id: str, workflow: str, settings: dict[str, str], *, action_mode: str = "deploy") -> None:
    config = WORKFLOW_DEFINITIONS[workflow]
    stage_plan = workflow_stage_definitions(workflow, action_mode=action_mode)
    mark_run_active(run_id, f"Running {workflow} pipeline stages.")

    for stage in stage_plan:
        stage_name = str(stage["name"])
        kind = str(stage.get("kind", "remote-command"))
        _set_stage(run_id, stage_name, "active", str(stage.get("active", f"Running {stage_name}.")))

        if kind == "inventory-refresh":
            _refresh_inventory(run_id)
            continue

        if kind == "event-note":
            message = str(stage.get("message", "")).strip()
            if message:
                append_event(run_id, "info", stage_name, message)
            _set_stage(run_id, stage_name, "complete", str(stage.get("complete", "Stage completed.")))
            continue

        if kind == "lab-storage-preflight":
            _run_lab_storage_preflight(run_id, stage_name)
            continue

        if kind == "lab-storage-grow-swarm":
            _run_lab_storage_grow(run_id, stage_name, LAB_STORAGE_SWARM_HOST_LIST)
            continue

        if kind == "lab-storage-grow-k3s":
            _run_lab_storage_grow(run_id, stage_name, LAB_STORAGE_K3S_HOST_LIST)
            continue

        if kind == "lab-storage-verify":
            _run_lab_storage_verify(run_id, stage_name)
            continue

        if kind == "demo-registry-k3s-dns":
            _run_demo_registry_k3s_dns(run_id, stage_name)
            continue

        if kind == "demo-registry-k3s-trust":
            _run_demo_registry_k3s_trust(run_id, stage_name)
            continue

        if kind == "demo-registry-k3s-pull":
            _run_demo_registry_k3s_pull(run_id, stage_name)
            continue

        if kind == "rx-demo-registry-preflight-build-push":
            _run_rx_demo_registry_preflight_build_push(run_id, stage_name, settings)
            continue

        if kind == "rx-demo-registry-preflight-catalog":
            _run_rx_demo_registry_preflight_catalog(run_id, stage_name, settings)
            continue

        if kind == "demo-k3s-add-node-select":
            _run_demo_k3s_add_node_select(run_id, stage_name)
            continue

        if kind == "demo-k3s-add-node-clone":
            _run_demo_k3s_add_node_clone(run_id, stage_name)
            continue

        if kind == "demo-k3s-add-node-boot":
            _run_demo_k3s_add_node_boot(run_id, stage_name)
            continue

        if kind == "demo-k3s-add-node-discover":
            _run_demo_k3s_add_node_discover(run_id, stage_name)
            continue

        if kind == "demo-k3s-add-node-ssh":
            _run_demo_k3s_add_node_ssh(run_id, stage_name)
            continue

        if kind == "demo-k3s-add-node-base":
            _run_demo_k3s_add_node_base(run_id, stage_name)
            continue

        if kind == "demo-k3s-add-node-token":
            _run_demo_k3s_add_node_token(run_id, stage_name)
            continue

        if kind == "demo-k3s-add-node-agent":
            _run_demo_k3s_add_node_agent(run_id, stage_name)
            continue

        if kind == "demo-k3s-add-node-verify":
            _run_demo_k3s_add_node_verify(run_id, stage_name)
            continue

        if kind == "demo-k3s-add-node-registry":
            _run_demo_k3s_add_node_registry(run_id, stage_name)
            continue

        if kind == "demo-k3s-add-node-register":
            _run_demo_k3s_add_node_register(run_id, stage_name)
            continue

        if kind == "demo-k3s-add-node-reset-select":
            _run_demo_k3s_add_node_reset_select(run_id, stage_name)
            continue

        if kind == "demo-k3s-add-node-reset-k3s":
            _run_demo_k3s_add_node_reset_k3s(run_id, stage_name)
            continue

        if kind == "demo-k3s-add-node-reset-vm":
            _run_demo_k3s_add_node_reset_vm(run_id, stage_name)
            continue

        if kind == "demo-k3s-add-node-reset-verify":
            _run_demo_k3s_add_node_reset_verify(run_id, stage_name)
            continue

        if kind == "fedora-build-kit":
            _run_fedora_build_kit(run_id, settings, stage)
            continue

        if kind == "fedora-cloud-source-select" or kind == "fedora-template-source-select":
            _run_fedora_template_select(run_id, stage_name)
            continue

        if kind == "fedora-cloud-proxmox-import" or kind == "fedora-template-proxmox-clone":
            _run_fedora_template_clone(run_id, stage_name)
            continue

        if kind == "fedora-cloud-configure" or kind == "fedora-template-configure":
            _run_fedora_template_configure(run_id, stage_name)
            continue

        if kind == "fedora-cloud-start" or kind == "fedora-template-start":
            _run_fedora_template_start(run_id, stage_name)
            continue

        if kind == "cosmic-target-select":
            _run_cosmic_target_select(run_id, stage_name)
            continue

        if kind == "cosmic-wait-ssh":
            _run_cosmic_wait_ssh(run_id, stage_name)
            continue

        if kind == "cosmic-package-plan":
            _run_cosmic_package_plan(run_id, stage_name)
            continue

        if kind == "cosmic-desktop-install":
            _run_cosmic_desktop_install(run_id, stage_name)
            continue

        if kind == "cosmic-graphical-enable":
            _run_cosmic_graphical_enable(run_id, stage_name)
            continue

        if kind == "cosmic-reboot":
            _run_cosmic_reboot(run_id, stage_name)
            continue

        if kind == "cosmic-gui-validate":
            _run_cosmic_gui_validate(run_id, stage_name)
            continue

        if kind == "cosmic-register-resource":
            _run_cosmic_register_resource(run_id, stage_name)
            continue

        if kind == "k3s-source-select":
            _run_k3s_source_select(run_id, stage_name)
            continue

        if kind == "k3s-clone-plan":
            _run_k3s_clone_plan(run_id, stage_name)
            continue

        if kind == "k3s-proxmox-clone":
            _run_k3s_proxmox_clone(run_id, stage_name)
            continue

        if kind == "k3s-proxmox-start":
            _run_k3s_proxmox_start(run_id, stage_name)
            continue

        if kind == "k3s-discover-ssh":
            _run_k3s_discover_ssh(run_id, stage_name)
            continue

        if kind == "k3s-base-bootstrap":
            _run_k3s_base_bootstrap(run_id, stage_name)
            continue

        if kind == "k3s-install-server":
            _run_k3s_install_server(run_id, stage_name)
            continue

        if kind == "k3s-capture-token":
            _run_k3s_capture_token(run_id, stage_name)
            continue

        if kind == "k3s-install-agent":
            _run_k3s_install_agent(run_id, stage_name)
            continue

        if kind == "k3s-verify-cluster":
            _run_k3s_verify_cluster(run_id, stage_name)
            continue

        if kind == "k3s-register-resources":
            _run_k3s_register_resources(run_id, stage_name)
            continue

        if kind == "k3s-host-telemetry-verify":
            _run_k3s_host_telemetry_verify(run_id, stage_name)
            continue

        if kind == "k3s-host-telemetry-apply":
            _run_k3s_host_telemetry_apply(run_id, stage_name)
            continue

        if kind == "k3s-housekeeping-nfs":
            _run_k3s_housekeeping_nfs(run_id, stage_name)
            continue

        if kind == "k3s-housekeeping-loki-logs":
            _run_k3s_housekeeping_loki_logs(run_id, stage_name)
            continue

        if kind == "k3s-housekeeping-loadgen":
            _run_k3s_housekeeping_loadgen(run_id, stage_name)
            continue

        if kind == "k3s-host-telemetry-firewall":
            _run_k3s_host_telemetry_firewall(run_id, stage_name)
            continue

        if kind == "k3s-host-telemetry-prometheus":
            _run_k3s_host_telemetry_prometheus(run_id, stage_name, settings)
            continue

        if kind == "k3s-host-telemetry-validate":
            _run_k3s_host_telemetry_validate(run_id, stage_name, settings)
            continue

        if kind == "rx-demo-k3s-source-check":
            _run_rx_demo_k3s_source_check(run_id, stage_name, settings)
            continue

        if kind == "rx-demo-k3s-build-rx-ui":
            _run_rx_demo_k3s_build_rx_ui(run_id, stage_name, settings)
            continue

        if kind == "rx-demo-k3s-import-rx-ui":
            _run_rx_demo_k3s_import_rx_ui(run_id, stage_name)
            continue

        if kind == "rx-demo-k3s-apply-lab":
            _run_rx_demo_k3s_apply_lab(run_id, stage_name)
            continue

        if kind == "rx-demo-k3s-smoke-ui":
            _run_rx_demo_k3s_smoke_ui(run_id, stage_name)
            continue

        if kind == "rx-demo-k3s-ready":
            _run_rx_demo_k3s_ready(run_id, stage_name)
            continue

        if kind == "rx-demo-k3s-secrets":
            _run_rx_demo_k3s_secrets(run_id, stage_name)
            continue

        if kind == "rx-demo-k3s-registry-images":
            _run_rx_demo_k3s_registry_images(run_id, stage_name, settings)
            continue

        if kind == "rx-demo-k3s-apply-demo-overlay":
            _run_rx_demo_k3s_apply_demo_overlay(run_id, stage_name)
            continue

        if kind == "rx-demo-k3s-rollout-app":
            _run_rx_demo_k3s_rollout_app(run_id, stage_name)
            continue

        if kind == "rx-demo-k3s-rollout-observability":
            _run_rx_demo_k3s_rollout_observability(run_id, stage_name)
            continue

        if kind == "rx-demo-k3s-smoke-api-full":
            _run_rx_demo_k3s_smoke_api_full(run_id, stage_name)
            continue

        if kind == "rx-demo-k3s-smoke-ui-full":
            _run_rx_demo_k3s_smoke_ui_full(run_id, stage_name)
            continue

        if kind == "rx-demo-k3s-telemetry-check":
            _run_rx_demo_k3s_telemetry_check(run_id, stage_name)
            continue

        if kind == "rx-demo-k3s-access-links":
            _run_rx_demo_k3s_access_links(run_id, stage_name)
            continue

        if kind == "rx-demo-k3s-git-event":
            _run_rx_demo_k3s_git_event(run_id, stage_name)
            continue

        if kind == "rx-demo-k3s-sync-source-from-git":
            _run_rx_demo_k3s_sync_source_from_git(run_id, stage_name, settings)
            continue

        if kind == "rx-demo-k3s-redeploy-build-push":
            _run_rx_demo_k3s_redeploy_build_push(run_id, stage_name, settings)
            continue

        if kind == "rx-demo-k3s-redeploy-update-images":
            _run_rx_demo_k3s_redeploy_update_images(run_id, stage_name)
            continue

        if kind == "rx-demo-k3s-cloudinit-node-check":
            _run_rx_demo_k3s_cloudinit_node_check(run_id, stage_name)
            continue

        if kind == "rx-demo-k3s-loki-cloudevents-check":
            _run_rx_demo_k3s_loki_cloudevents_check(run_id, stage_name)
            continue

        if kind == "rx-demo-k3s-grafana-loki-check":
            _run_rx_demo_k3s_grafana_loki_check(run_id, stage_name)
            continue

        if kind == "rx-demo-k3s-undeploy-capture":
            _run_rx_demo_k3s_undeploy_capture(run_id, stage_name)
            continue

        if kind == "rx-demo-k3s-undeploy-demo-observability":
            _run_rx_demo_k3s_undeploy_demo_observability(run_id, stage_name)
            continue

        if kind == "rx-demo-k3s-undeploy-namespace":
            _run_rx_demo_k3s_undeploy_namespace(run_id, stage_name)
            continue

        if kind == "rx-demo-k3s-undeploy-verify":
            _run_rx_demo_k3s_undeploy_verify(run_id, stage_name)
            continue

        if kind == "rx-demo-k3s-undeploy-registry":
            _run_rx_demo_k3s_undeploy_registry(run_id, stage_name, settings)
            continue

        if kind == "auzix-vm130-deploy":
            _run_auzix_vm130_deploy(run_id, stage_name, settings)
            continue

        if kind == "auzix-vm130-validate":
            _run_auzix_vm130_validate(run_id, stage_name)
            continue

        if kind == "auzix-vm134-iso-publish":
            _run_auzix_vm134_iso_publish(run_id, stage_name, settings)
            continue

        if kind == "auzix-vm134-target-verify":
            _run_auzix_vm134_target_verify(run_id, stage_name)
            continue

        if kind == "auzix-vm135-artifact-verify":
            _run_auzix_vm135_artifact_verify(run_id, stage_name)
            continue

        if kind == "auzix-vm135-iso-publish":
            _run_auzix_vm135_iso_publish(run_id, stage_name)
            continue

        if kind == "auzix-vm135-recreate":
            _run_auzix_vm135_recreate(run_id, stage_name)
            continue

        if kind == "auzix-vm135-start":
            _run_auzix_vm135_start(run_id, stage_name)
            continue

        if kind == "wordpress-source-select":
            template = _select_wordpress_template()
            _store_run_extra(run_id, {"selected_template": template})
            _set_stage(run_id, stage_name, "complete", str(stage.get("complete", "Stage completed.")))
            append_event(
                run_id,
                "info",
                stage_name,
                f"Selected template {template.get('name')} on {template.get('node')} (vmid {template.get('vmid')}).",
            )
            continue

        if kind == "wordpress-proxmox-clone":
            _run_wordpress_clone(run_id, stage_name)
            continue

        if kind == "wordpress-proxmox-start":
            _run_wordpress_start(run_id, stage_name)
            continue

        target_host, target_user, target_password = _command_target(settings, str(stage.get("target", "manager")))
        output = run_remote_command(
            host=target_host,
            user=target_user,
            password=target_password,
            command=str(stage["command"]),
            timeout=int(stage.get("timeout", 120)),
        )
        _set_stage(run_id, stage_name, "complete", str(stage.get("complete", "Stage completed.")))
        append_event(run_id, "info", stage_name, output[-1200:] if output else f"{stage_name} ok")

    if action_mode == "undeploy":
        mark_run_complete(run_id, f"{workflow} undeploy completed.")
    else:
        mark_run_complete(run_id, str(config.get("complete_message", f"{workflow} pipeline completed.")))


def _run_workflow_deploy(run_id: str, workflow: str, settings: dict[str, str]) -> None:
    config = WORKFLOW_DEFINITIONS[workflow]
    controller_host = settings["controller_host"]
    controller_user = settings["controller_user"]
    controller_password = settings["controller_password"]
    manager_host = settings["manager_host"]
    manager_user = settings["manager_user"]
    manager_password = settings["manager_password"]

    mark_run_active(run_id, f"Running {workflow} pipeline stages.")

    deploy_stage = config["deploy_stage"]
    _set_stage(run_id, deploy_stage, "active", config["deploy_active"])
    deploy_output = run_remote_command(
        host=controller_host,
        user=controller_user,
        password=controller_password,
        command=config["deploy_command"],
        timeout=240,
    )
    _set_stage(run_id, deploy_stage, "complete", config["deploy_complete"])
    append_event(run_id, "info", deploy_stage, deploy_output[-800:] if deploy_output else "Ansible completed.")

    health_stage = config["health_stage"]
    _set_stage(run_id, health_stage, "active", config["health_active"])
    health_output = run_remote_command(
        host=manager_host,
        user=manager_user,
        password=manager_password,
        command=config["health_command"],
        timeout=90,
    )
    _set_stage(run_id, health_stage, "complete", config["health_complete"])
    append_event(run_id, "info", health_stage, health_output or "ok")

    init_stage = str(config.get("init_stage", "")).strip()
    init_command = str(config.get("init_command", "")).strip()
    if init_stage and init_command:
        _set_stage(run_id, init_stage, "active", str(config.get("init_active", "Running initialization step.")))
        init_output = run_remote_command(
            host=manager_host,
            user=manager_user,
            password=manager_password,
            command=init_command,
            timeout=120,
        )
        _set_stage(run_id, init_stage, "complete", str(config.get("init_complete", "Initialization completed.")))
        append_event(run_id, "info", init_stage, init_output or "ok")

    _refresh_inventory(run_id)

    _set_stage(run_id, "dashboard-link", "active", "Publishing dashboard endpoints for operators.")
    append_event(run_id, "info", "dashboard-link", config["dashboard_message"])
    _set_stage(run_id, "dashboard-link", "complete", "Dashboard endpoints published.")

    mark_run_complete(run_id, config["complete_message"])


def _run_workflow_undeploy(run_id: str, workflow: str, settings: dict[str, str]) -> None:
    config = WORKFLOW_DEFINITIONS[workflow]
    manager_host = settings["manager_host"]
    manager_user = settings["manager_user"]
    manager_password = settings["manager_password"]

    mark_run_active(run_id, f"Removing {workflow} from the active lab runtime.")

    _set_stage(run_id, config["undeploy_stage"], "active", config["undeploy_active"])
    remove_output = run_remote_command(
        host=manager_host,
        user=manager_user,
        password=manager_password,
        command=config["undeploy_command"],
        timeout=120,
    )
    _set_stage(run_id, config["undeploy_stage"], "complete", config["undeploy_complete"])
    append_event(run_id, "info", config["undeploy_stage"], remove_output or "removed")

    _set_stage(run_id, "health-check", "active", config["absence_active"])
    absence_output = run_remote_command(
        host=manager_host,
        user=manager_user,
        password=manager_password,
        command=config["absence_command"],
        timeout=60,
    )
    _set_stage(run_id, "health-check", "complete", config["absence_complete"])
    append_event(run_id, "info", "health-check", absence_output or "absent")

    _refresh_inventory(run_id)

    _set_stage(run_id, "dashboard-link", "active", "Publishing operator note for removed endpoints.")
    append_event(run_id, "info", "dashboard-link", config["removed_dashboard_message"])
    _set_stage(run_id, "dashboard-link", "complete", "Dashboard removal note published.")

    mark_run_complete(run_id, f"{workflow} undeploy completed.")


def workflow_runtime_snapshot(workflow: str) -> dict | None:
    normalized = (workflow or "").strip().lower()
    config = WORKFLOW_DEFINITIONS.get(normalized)
    if not config:
        return None

    runtime = config.get("runtime_snapshot")
    if not runtime:
        return None

    settings = _remote_settings()
    manager_host = settings["manager_host"]
    manager_user = settings["manager_user"]
    manager_password = settings["manager_password"]
    kind = runtime["kind"]

    if kind == "container-prefix":
        prefix = runtime["container_name_prefix"]
        containers_cmd = (
            "bash -lc 'docker ps -a --format "
            "\"{{.Names}}|{{.Status}}|{{.Image}}\" | grep "
            f"\"^{prefix}\" || true'"
        )
        containers_output = run_remote_command(
            host=manager_host,
            user=manager_user,
            password=manager_password,
            command=containers_cmd,
            timeout=30,
        )
        services = []
        logs = []
        for raw in containers_output.splitlines():
            line = raw.strip()
            if not line:
                continue
            name, status, image = (line.split("|", 2) + ["", "", ""])[:3]
            services.append({"name": name, "replicas": status, "image": image})
            logs_cmd = (
                "bash -lc 'docker logs --tail 60 "
                f"{name} 2>&1 || true'"
            )
            content = run_remote_command(
                host=manager_host,
                user=manager_user,
                password=manager_password,
                command=logs_cmd,
                timeout=30,
            )
            if content:
                logs.append({"service": name, "content": content})
        return {"services": services, "logs": logs}

    if kind == "service":
        services_cmd = (
            "bash -lc 'docker service ls --format "
            "\"{{.Name}}|{{.Replicas}}|{{.Image}}\" | grep "
            f"\"{runtime['service_filter']}\" || true'"
        )
        services_output = run_remote_command(
            host=manager_host,
            user=manager_user,
            password=manager_password,
            command=services_cmd,
            timeout=30,
        )
        services = []
        for raw in services_output.splitlines():
            line = raw.strip()
            if not line:
                continue
            name, replicas, image = (line.split("|", 2) + ["", "", ""])[:3]
            services.append({"name": name, "replicas": replicas, "image": image})

        logs = []
        for service_name in runtime["service_names"]:
            logs_cmd = (
                "bash -lc 'docker service logs --tail 25 --timestamps "
                f"{service_name} 2>&1 || true'"
            )
            content = run_remote_command(
                host=manager_host,
                user=manager_user,
                password=manager_password,
                command=logs_cmd,
                timeout=30,
            )
            if content:
                logs.append({"service": service_name, "content": content})
        return {"services": services, "logs": logs}

    compose_dir = runtime["compose_dir"]
    services_cmd = (
        "bash -lc 'cd "
        f"{compose_dir} && "
        "docker compose ps --format json 2>/dev/null || true'"
    )
    services_output = run_remote_command(
        host=manager_host,
        user=manager_user,
        password=manager_password,
        command=services_cmd,
        timeout=30,
    )
    services = []
    for raw in services_output.splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        services.append(
            {
                "name": str(payload.get("Service") or payload.get("Name") or ""),
                "replicas": str(payload.get("State") or ""),
                "image": str(payload.get("Image") or ""),
            }
        )

    logs = []
    for service_name in runtime["service_names"]:
        logs_cmd = (
            "bash -lc 'cd "
            f"{compose_dir} && "
            "docker compose logs --tail 25 "
            f"{service_name} 2>&1 || true'"
        )
        content = run_remote_command(
            host=manager_host,
            user=manager_user,
            password=manager_password,
            command=logs_cmd,
            timeout=30,
        )
        if content:
            logs.append({"service": service_name, "content": content})
    return {"services": services, "logs": logs}


def execute_pipeline_run(run_id: str) -> dict:
    run = get_run(run_id)
    if not run:
        raise PipelineExecutionError(f"Run {run_id} not found.")

    workflow = str(run.get("workflow", "")).strip().lower()
    config = WORKFLOW_DEFINITIONS.get(workflow)
    if not config:
        raise PipelineExecutionError(f"No executor implemented for workflow '{workflow}'.")

    settings = _remote_settings()
    action_mode = str(run.get("extra", {}).get("action_mode", "deploy")).strip().lower() or "deploy"

    try:
        if config.get("stage_plan"):
            if action_mode == "undeploy" and not config.get("supports_undeploy"):
                raise PipelineExecutionError(f"Workflow '{workflow}' does not support undeploy.")
            _run_stage_plan(run_id, workflow, settings, action_mode=action_mode)
        elif action_mode == "undeploy":
            if not config.get("supports_undeploy"):
                raise PipelineExecutionError(f"Workflow '{workflow}' does not support undeploy.")
            _run_workflow_undeploy(run_id, workflow, settings)
        else:
            _run_workflow_deploy(run_id, workflow, settings)
    except PipelineExecutionError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise PipelineExecutionError(str(exc)) from exc

    completed = get_run(run_id)
    return completed or run
