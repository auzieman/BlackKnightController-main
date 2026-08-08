from __future__ import annotations

import crypt
import ipaddress
import hashlib
import json
import os
import re
import shlex
import socket
import secrets
import ssl
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from base64 import b64decode, b64encode
from pathlib import Path
from urllib.parse import quote

from services.ansible import scan_ansible_controller
from services.ansible_inventory import parse_ansible_hosts, sync_ansible_inventory_to_rules
from services.automation_pipeline import (
    append_event,
    mark_run_active,
    mark_run_complete,
    mark_run_failed,
)
from services.automation_runs import get_run, load_runs, update_run, update_stage
from services.docker_swarm import scan_docker_controller, sync_docker_inventory_to_rules
from services.fresh_build_library import fresh_build_plan
from services.integration_store import (
    load_integrations,
    load_proxmox_snapshot,
    save_ansible_snapshot,
    save_docker_snapshot,
    save_integrations,
    save_proxmox_snapshot,
)
from services.inventory_model import reconcile_rules_inventory, resolve_group_hosts
from services.kubernetes_api import kubectl_text
from services.pipeline_catalog import pipeline_by_id, resolve_pipeline_dictionary
from services.proxmox import ProxmoxClient, load_proxmox_config, summarize_inventory, sync_inventory_to_rules
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
    {"name": "kube3.lab.auzietek.com", "host": "192.168.1.239", "role": "agent"},
]
K3S_NFS_MOUNTS = [
    ("192.168.1.10:/srv/nfs/swarm/shared", "/mnt/swarm/shared"),
    ("192.168.1.10:/srv/nfs/swarm/tabor-linux-forge", "/mnt/swarm/tabor-linux-forge"),
    ("192.168.1.10:/srv/nfs/swarm/AuziX", "/mnt/swarm/AuziX"),
    ("192.168.1.10:/srv/nfs/swarm/blackknightcontroller", "/mnt/swarm/blackknightcontroller"),
]
LAB_STORAGE_SWARM_HOSTS = "swarm1.lab.auzietek.com swarm2.lab.auzietek.com swarm3.lab.auzietek.com"
LAB_STORAGE_K3S_HOSTS = "192.168.1.14 192.168.1.59 192.168.1.239"
LAB_STORAGE_ALL_HOSTS = f"{LAB_STORAGE_SWARM_HOSTS} {LAB_STORAGE_K3S_HOSTS}"
LAB_STORAGE_SWARM_HOST_LIST = LAB_STORAGE_SWARM_HOSTS.split()
LAB_STORAGE_K3S_HOST_LIST = LAB_STORAGE_K3S_HOSTS.split()
LAB_STORAGE_ALL_HOST_LIST = LAB_STORAGE_ALL_HOSTS.split()
LAB_STORAGE_MIN_ROOT_BYTES = 50_000_000_000
RX_DEMO_SHARED_SOURCE = "/mnt/swarm/shared/rx-demo"
RX_DEMO_REDEPLOY_SOURCE = "/tmp/bkc-rx-demo-git-work"
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
AUZIX_R730_BUILD_HOST = "10.20.0.130"
AUZIX_R730_BUILD_USER = "root"
AUZIX_R730_SOURCE_ROOT = "/srv/auzix/AuziX/src"
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
                "transport": "kubernetes-api",
                "kind": "rx-demo-k3s-ready",
                "action": "k3s.nodes.ready",
                "active": "Verifying kube1 can read the k3s cluster and all nodes are Ready.",
                "complete": "K3s node readiness verified.",
                "timeout": 120,
            },
            {
                "name": "runtime-secrets",
                "transport": "kubernetes-api",
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
                "transport": "kubernetes-api",
                "kind": "rx-demo-k3s-rollout-app",
                "action": "kubectl.rollout_status",
                "active": "Waiting for rx-demo application workloads to become ready.",
                "complete": "Rx-demo application workloads are ready.",
                "timeout": 900,
            },
            {
                "name": "rollout-observability",
                "transport": "kubernetes-api",
                "kind": "rx-demo-k3s-rollout-observability",
                "action": "kubectl.rollout_status",
                "active": "Waiting for Grafana, Prometheus, Loki, and Tempo to become ready.",
                "complete": "Rx-demo observability workloads are ready.",
                "timeout": 900,
            },
            {
                "name": "smoke-api",
                "transport": "kubernetes-api",
                "kind": "rx-demo-k3s-smoke-api-full",
                "action": "http.smoke",
                "active": "Smoking rx-demo API routes through the k3s NodePort.",
                "complete": "Rx-demo API smoke checks passed.",
                "timeout": 180,
            },
            {
                "name": "smoke-ui",
                "transport": "kubernetes-api",
                "kind": "rx-demo-k3s-smoke-ui-full",
                "action": "http.smoke",
                "active": "Smoking rx-demo UI routes through the k3s NodePort.",
                "complete": "Rx-demo UI smoke checks passed.",
                "timeout": 180,
            },
            {
                "name": "telemetry-check",
                "transport": "kubernetes-api",
                "kind": "rx-demo-k3s-telemetry-check",
                "action": "prometheus.metrics.check",
                "active": "Checking rx-demo metrics, Grafana routing, and dashboard panel plugins.",
                "complete": "Rx-demo telemetry endpoints and dashboard plugins responded.",
                "timeout": 180,
            },
            {
                "name": "access-links",
                "transport": "kubernetes-api",
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
                "transport": "kubernetes-api",
                "kind": "rx-demo-k3s-undeploy-capture",
                "action": "kubectl.get",
                "active": "Capturing rx-demo and demo observability state before cleanup.",
                "complete": "Pre-cleanup k3s state captured.",
                "timeout": 120,
            },
            {
                "name": "delete-overlay",
                "transport": "kubernetes-api",
                "kind": "rx-demo-k3s-undeploy-demo-observability",
                "action": "kubectl.delete",
                "active": "Removing demo-owned Grafana, Prometheus, Loki, and Tempo resources while preserving host telemetry.",
                "complete": "Demo observability resources removed.",
                "timeout": 300,
            },
            {
                "name": "delete-namespace",
                "transport": "kubernetes-api",
                "kind": "rx-demo-k3s-undeploy-namespace",
                "action": "kubectl.delete",
                "active": "Removing the rx-demo application namespace.",
                "complete": "Rx-demo namespace removed.",
                "timeout": 300,
            },
            {
                "name": "verify-removed",
                "transport": "kubernetes-api",
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
                "transport": "kubernetes-api",
                "kind": "rx-demo-k3s-redeploy-update-images",
                "action": "kubectl.set_image",
                "active": "Pointing k3s deployments at the commit-tagged images.",
                "complete": "K3s deployments reference the commit-tagged images.",
                "timeout": 240,
            },
            {
                "name": "k3s-network-ready",
                "transport": "kubernetes-api",
                "kind": "rx-demo-k3s-network-ready",
                "action": "kubernetes.endpoints.verify",
                "active": "Verifying k3s nodes and rx-demo endpoints through the Kubernetes API.",
                "complete": "K3s pod networking prerequisites are ready.",
                "timeout": 300,
            },
            {
                "name": "rollout-app",
                "transport": "kubernetes-api",
                "kind": "rx-demo-k3s-rollout-app",
                "action": "kubectl.rollout_status",
                "active": "Waiting for the redeployed rx-demo workloads.",
                "complete": "Redeployed rx-demo workloads are ready.",
                "timeout": 900,
            },
            {
                "name": "cloudinit-node-check",
                "transport": "kubernetes-api",
                "kind": "rx-demo-k3s-cloudinit-node-check",
                "action": "kubernetes.node.provenance",
                "active": "Capturing k3s node provenance evidence from the Kubernetes API.",
                "complete": "K3s node and cloud-init evidence captured.",
                "timeout": 180,
            },
            {
                "name": "visible-change-check",
                "transport": "kubernetes-api",
                "kind": "rx-demo-k3s-redeploy-visible-activity",
                "action": "http.content_check",
                "active": "Generating UI/API activity after the redeploy.",
                "complete": "Post-redeploy UI/API smoke activity completed.",
                "timeout": 180,
            },
            {
                "name": "telemetry-still-flowing",
                "transport": "kubernetes-api",
                "kind": "rx-demo-k3s-telemetry-check",
                "action": "prometheus.metrics.check",
                "active": "Checking telemetry endpoints and dashboard plugins after the redeploy.",
                "complete": "Telemetry endpoints and dashboard plugins responded after the redeploy.",
                "timeout": 180,
            },
            {
                "name": "loki-cloudevents-check",
                "transport": "kubernetes-api",
                "kind": "rx-demo-k3s-loki-cloudevents-check",
                "action": "loki.stream.verify",
                "active": "Querying Loki for CloudEvents audit records.",
                "complete": "Loki returned CloudEvents audit records.",
                "timeout": 300,
            },
            {
                "name": "grafana-loki-check",
                "transport": "kubernetes-api",
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
    "rx-demo-k3s-observability-refresh": {
        "supports_undeploy": False,
        "stage_plan": [
            {
                "name": "git-event",
                "transport": "internal",
                "kind": "rx-demo-k3s-git-event",
                "action": "git.event.record",
                "active": "Recording the Git trigger inputs for the rx-demo observability refresh.",
                "complete": "Git trigger inputs recorded.",
                "timeout": 30,
            },
            {
                "name": "sync-source-from-git",
                "transport": "ssh-manager",
                "kind": "rx-demo-k3s-sync-source-from-git",
                "action": "git.checkout",
                "active": "Updating the rx-demo working copy from Git.",
                "complete": "Rx-demo source is on the requested Git revision.",
                "timeout": 300,
            },
            {
                "name": "publish-source-to-shared",
                "transport": "ssh-manager",
                "kind": "rx-demo-k3s-publish-source-to-shared",
                "action": "git.source.publish",
                "active": "Publishing the synced rx-demo source to the shared k3s workspace.",
                "complete": "Synced rx-demo source is available to the k3s server.",
                "timeout": 180,
            },
            {
                "name": "apply-observability",
                "transport": "kubernetes-api",
                "kind": "rx-demo-k3s-apply-observability",
                "action": "kubectl.apply",
                "active": "Applying rx-demo observability manifests and provisioned Grafana dashboards.",
                "complete": "Rx-demo observability manifests and dashboards applied.",
                "timeout": 300,
            },
            {
                "name": "rollout-observability",
                "transport": "kubernetes-api",
                "kind": "rx-demo-k3s-rollout-observability",
                "action": "kubectl.rollout_status",
                "active": "Waiting for Grafana, Prometheus, Loki, and Tempo to become ready.",
                "complete": "Rx-demo observability workloads are ready.",
                "timeout": 900,
            },
            {
                "name": "telemetry-check",
                "transport": "kubernetes-api",
                "kind": "rx-demo-k3s-telemetry-check",
                "action": "prometheus.metrics.check",
                "active": "Checking rx-demo metrics, Grafana routing, and dashboard panel plugins.",
                "complete": "Rx-demo telemetry endpoints and dashboard plugins responded.",
                "timeout": 180,
            },
            {
                "name": "grafana-loki-check",
                "transport": "kubernetes-api",
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
        "complete_message": "Rx-demo k3s observability refresh pipeline completed.",
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
    "auzix-live-media-build": {
        "supports_undeploy": False,
        "stage_plan": [
            {
                "name": "preflight-pinned-recovery-inputs",
                "transport": "ssh-controller",
                "target": "controller",
                "active": "Checking the pinned AuziX recovery artifact, source commit handoff, runtime key, and R730 worker.",
                "complete": "Pinned AuziX recovery inputs and worker are ready.",
                "command": (
                    "bash -lc 'set -e; "
                    "test -x /srv/nfs/swarm/AuziX/src/scripts/run-auzix-live-recovery-r730.sh; "
                    "test -x /srv/nfs/swarm/AuziX/src/scripts/build-auzix-installer-efl-package.sh; "
                    "echo 'dbc37d309059b70cc39e37b7a5e0be7d27dae770654bf3ccf7ddf7d142c25cb6  /srv/nfs/swarm/AuziX/src/artifacts/auzix/auzix-live-theme-app-candidate.iso' | sha256sum -c -; "
                    "test -s /srv/nfs/swarm/AuziX/runtime/keys/authorized_keys; "
                    "test -s /srv/nfs/swarm/AuziX/runtime/secrets/live-root-shadow; "
                    "ssh -o BatchMode=yes root@10.20.0.130 \"docker info >/dev/null; test -d /mnt/ns1/AuziX/src\"; "
                    "echo auzix-live-media-worker-ready'"
                ),
                "timeout": 120,
            },
            {
                "name": "derive-and-validate-recovery-media",
                "transport": "ssh-controller",
                "target": "controller",
                "active": "Deriving the committed AuziX recovery SquashFS on the R730 while preserving the pinned kernel and boot map.",
                "complete": "AuziX recovery derivative passed its static payload and boot checks.",
                "command": (
                    "bash -lc 'set -e; "
                    "run_id=$(date -u +%Y%m%dT%H%M%SZ); "
                    "ssh -o BatchMode=yes root@10.20.0.130 \"AUZIX_SOURCE_COMMIT=e841dc7 AUZIX_RUN_ID=$run_id AUZIX_ISO_NAME=auzix-live-recovery-$run_id.iso /mnt/ns1/AuziX/src/scripts/run-auzix-live-recovery-r730.sh\"; "
                    "echo auzix_live_recovery_run_id=$run_id'"
                ),
                "timeout": 7200,
            },
            {
                "name": "publish-live-recovery-receipt",
                "transport": "ssh-controller",
                "target": "controller",
                "active": "Publishing the pinned-input AuziX recovery receipt and checksums for VM135 validation.",
                "complete": "AuziX recovery receipt is available for VM135 validation.",
                "command": (
                    "bash -lc 'set -e; "
                    "latest=$(ls -1t /srv/nfs/swarm/AuziX/build-receipts/live-recovery-*.receipt | head -n1); "
                    "test -s \"$latest\"; cat \"$latest\"; "
                    "grep -Fx status=pass \"$latest\"'"
                ),
                "timeout": 120,
            },
        ],
        "complete_message": "AuziX preserved live recovery build completed with a published receipt.",
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
                    "test -s .auzix-commit && "
                    "printf \"auzix_commit=%s\\n\" \"$(cat .auzix-commit)\" && "
                    "./scripts/test-auzix-trixie-intake.sh'"
                ),
                "timeout": 120,
            },
            {
                "name": "builder-prepare",
                "transport": "bkc-ssh",
                "target": "auzix-r730-build",
                "active": "Verifying the R730 AUZiX Trixie package intake image.",
                "complete": "R730 AUZiX Trixie package intake image is ready.",
                "command": (
                    "bash -lc 'set -e; "
                    f"test -s {AUZIX_R730_SOURCE_ROOT}/.auzix-commit && "
                    "docker image inspect auzix/trixie-builder:lab >/dev/null && "
                    "docker image inspect auzix/builder:lab >/dev/null && "
                    f"docker run --rm -v {AUZIX_R730_SOURCE_ROOT}:/workspace -w /workspace "
                    "auzix/trixie-builder:lab bash -lc "
                    "'\"'\"'./scripts/test-auzix-trixie-intake.sh'\"'\"''"
                ),
                "timeout": 3600,
            },
            {
                "name": "package-intake",
                "transport": "bkc-ssh",
                "target": "auzix-r730-build",
                "active": "Attempting the Trixie user application profile sequentially on the R730 AUZiX build worker.",
                "complete": "Trixie package intake attempts completed.",
                "command": (
                    "bash -lc 'docker run --rm "
                    f"-v {AUZIX_R730_SOURCE_ROOT}:/workspace -w /workspace "
                    "auzix/trixie-builder:lab bash -lc "
                    "'\"'\"'apt-get update >/dev/null && "
                    "./scripts/run-auzix-trixie-intake.sh'\"'\"''"
                ),
                "timeout": 21600,
            },
            {
                "name": "repository-build",
                "transport": "bkc-ssh",
                "target": "auzix-r730-build",
                "active": "Rebuilding the AuziX repository with successful Trixie intake receipts on the R730 AUZiX build worker.",
                "complete": "AuziX repository includes successful Trixie intake packages.",
                "command": (
                    "bash -lc 'docker run --rm "
                    f"-v {AUZIX_R730_SOURCE_ROOT}:/workspace -w /workspace "
                    "auzix/builder:lab ./scripts/build-auzix-package-repo.sh "
                    "/workspace/out/auzix-strict/AuzixRoot'"
                ),
                "timeout": 7200,
            },
            {
                "name": "repository-publish",
                "transport": "bkc-ssh",
                "target": "auzix-r730-build",
                "active": "Publishing successful Trixie intake packages.",
                "complete": "Successful Trixie intake packages were published.",
                "command": (
                    "bash -lc 'rsync -a --delete "
                    "/srv/auzix/AuziX/src/artifacts/auzix/repo/ "
                    "root@192.168.1.10:/srv/http/auzix/repo/ && "
                    "ssh root@192.168.1.10 "
                    "\"chown -R root:root /srv/http/auzix/repo && "
                    "find /srv/http/auzix/repo -type d -exec chmod 755 {} + && "
                    "find /srv/http/auzix/repo -type f -exec chmod 644 {} +\"'"
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
                    "bash -lc 'cd /srv/auzix/AuziX/src && "
                    "report=out/package-bot/trixie-user-apps.report.json && "
                    "jq -e '\"'\"'.format == \"auzix-trixie-intake-report-v1\" "
                    "and .complete > 0'\"'\"' \"$report\" >/dev/null && "
                    "curl -fsS http://192.168.1.10/auzix/repo/index.json | "
                    "jq -e '\"'\"'(.packages | length) > 80 and "
                    "any(.packages[]; .name == \"LibreOffice\") and "
                    "any(.packages[]; .name == \"Python3\") and "
                    "all(.packages[]; (.name | startswith(\"Debian.\") | not))'\"'\"' >/dev/null && "
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
WORKFLOW_DEFINITIONS["ns1-provisioning-network-prepare"] = {
    "supports_undeploy": False,
    "settings_optional": True,
    "stage_plan": [
        {
            "name": "resolve-ns1-node",
            "transport": "internal",
            "kind": "folder-pipeline-review",
            "pipeline_id": "ns1-provisioning-network-prepare",
            "active": "Reviewing ns1 target node resolution.",
            "complete": "ns1 target node resolution reviewed.",
            "timeout": 15,
        },
        {
            "name": "discover-current-network",
            "transport": "internal",
            "kind": "folder-pipeline-review",
            "pipeline_id": "ns1-provisioning-network-prepare",
            "active": "Reviewing current ns1 network discovery step.",
            "complete": "ns1 network discovery step reviewed.",
            "timeout": 30,
        },
        {
            "name": "select-provisioning-interface",
            "transport": "internal",
            "kind": "folder-pipeline-review",
            "pipeline_id": "ns1-provisioning-network-prepare",
            "active": "Reviewing provisioning interface selection.",
            "complete": "Provisioning interface selection reviewed.",
            "timeout": 15,
        },
        {
            "name": "apply-provisioning-address",
            "transport": "internal",
            "kind": "folder-pipeline-review",
            "pipeline_id": "ns1-provisioning-network-prepare",
            "active": "Reviewing provisioning address application guardrails.",
            "complete": "Provisioning address application reviewed.",
            "timeout": 60,
        },
        {
            "name": "validate-management-still-reachable",
            "transport": "internal",
            "kind": "folder-pipeline-review",
            "pipeline_id": "ns1-provisioning-network-prepare",
            "active": "Reviewing management reachability validation.",
            "complete": "Management reachability validation reviewed.",
            "timeout": 30,
        },
        {
            "name": "record-network-relationships",
            "transport": "internal",
            "kind": "folder-pipeline-review",
            "pipeline_id": "ns1-provisioning-network-prepare",
            "active": "Reviewing network relationship recording.",
            "complete": "Network relationship recording reviewed.",
            "timeout": 15,
        },
    ],
    "complete_message": "ns1 provisioning network prepare review completed.",
}
WORKFLOW_DEFINITIONS["ns1-provisioning-dhcp-prepare"] = {
    "supports_undeploy": False,
    "settings_optional": True,
    "stage_plan": [
        {
            "name": "resolve-ns1-node",
            "transport": "internal",
            "kind": "folder-pipeline-review",
            "pipeline_id": "ns1-provisioning-dhcp-prepare",
            "active": "Reviewing ns1 target node resolution.",
            "complete": "ns1 target node resolution reviewed.",
            "timeout": 15,
        },
        {
            "name": "verify-provisioning-network",
            "transport": "internal",
            "kind": "folder-pipeline-review",
            "pipeline_id": "ns1-provisioning-dhcp-prepare",
            "active": "Reviewing DHCP provisioning network boundary checks.",
            "complete": "DHCP provisioning network boundary checks reviewed.",
            "timeout": 30,
        },
        {
            "name": "ensure-dhcp-include",
            "transport": "internal",
            "kind": "folder-pipeline-review",
            "pipeline_id": "ns1-provisioning-dhcp-prepare",
            "active": "Reviewing DHCP main-config include guard.",
            "complete": "DHCP main-config include guard reviewed.",
            "timeout": 30,
        },
        {
            "name": "render-dhcp-fragment",
            "transport": "internal",
            "kind": "folder-pipeline-review",
            "pipeline_id": "ns1-provisioning-dhcp-prepare",
            "active": "Reviewing DHCP provisioning fragment rendering.",
            "complete": "DHCP provisioning fragment rendering reviewed.",
            "timeout": 15,
        },
        {
            "name": "render-dhcp-defaults",
            "transport": "internal",
            "kind": "folder-pipeline-review",
            "pipeline_id": "ns1-provisioning-dhcp-prepare",
            "active": "Reviewing DHCP interface defaults rendering.",
            "complete": "DHCP interface defaults rendering reviewed.",
            "timeout": 15,
        },
        {
            "name": "install-dhcp-package",
            "transport": "internal",
            "kind": "folder-pipeline-review",
            "pipeline_id": "ns1-provisioning-dhcp-prepare",
            "active": "Reviewing DHCP package install intent.",
            "complete": "DHCP package install intent reviewed.",
            "timeout": 180,
        },
        {
            "name": "validate-dhcp-config",
            "transport": "internal",
            "kind": "folder-pipeline-review",
            "pipeline_id": "ns1-provisioning-dhcp-prepare",
            "active": "Reviewing DHCP syntax validation command.",
            "complete": "DHCP syntax validation command reviewed.",
            "timeout": 30,
        },
        {
            "name": "keep-dhcp-disabled",
            "transport": "internal",
            "kind": "folder-pipeline-review",
            "pipeline_id": "ns1-provisioning-dhcp-prepare",
            "active": "Reviewing DHCP disabled-by-default gate.",
            "complete": "DHCP disabled-by-default gate reviewed.",
            "timeout": 30,
        },
        {
            "name": "record-dhcp-relationships",
            "transport": "internal",
            "kind": "folder-pipeline-review",
            "pipeline_id": "ns1-provisioning-dhcp-prepare",
            "active": "Reviewing DHCP relationship recording.",
            "complete": "DHCP relationship recording reviewed.",
            "timeout": 15,
        },
    ],
    "complete_message": "ns1 provisioning DHCP prepare review completed.",
}
WORKFLOW_DEFINITIONS["baremetal-bmc-discovery-prepare"] = {
    "supports_undeploy": False,
    "settings_optional": True,
    "stage_plan": [
        {
            "name": "load-bmc-discovery-intent",
            "transport": "internal",
            "kind": "folder-pipeline-review",
            "pipeline_id": "baremetal-bmc-discovery-prepare",
            "active": "Reviewing DHCP iDRAC/BMC discovery intent.",
            "complete": "DHCP iDRAC/BMC discovery intent reviewed.",
            "timeout": 15,
        },
        {
            "name": "observe-ns1-neighbors",
            "transport": "bkc-ssh",
            "kind": "bmc-discovery-neighbors",
            "pipeline_id": "baremetal-bmc-discovery-prepare",
            "active": "Collecting ns1 ARP/neighbor evidence for candidate iDRACs.",
            "complete": "ns1 ARP/neighbor evidence collected.",
            "timeout": 30,
        },
        {
            "name": "probe-redfish-roots",
            "transport": "bkc-ssh",
            "kind": "bmc-discovery-redfish",
            "pipeline_id": "baremetal-bmc-discovery-prepare",
            "active": "Probing candidate iDRAC Redfish roots from ns1.",
            "complete": "Candidate iDRAC Redfish roots probed.",
            "timeout": 45,
        },
        {
            "name": "map-bmcs-to-hosts",
            "transport": "internal",
            "kind": "folder-pipeline-review",
            "pipeline_id": "baremetal-bmc-discovery-prepare",
            "active": "Reviewing BMC to physical host mapping.",
            "complete": "BMC to physical host mapping reviewed.",
            "timeout": 15,
        },
        {
            "name": "record-discovery-handoff",
            "transport": "internal",
            "kind": "folder-pipeline-review",
            "pipeline_id": "baremetal-bmc-discovery-prepare",
            "active": "Reviewing BMC discovery handoff.",
            "complete": "BMC discovery handoff reviewed.",
            "timeout": 15,
        },
    ],
    "complete_message": "Bare metal BMC discovery preparation completed.",
}
WORKFLOW_DEFINITIONS["ns1-lan-mac-pxe-prepare"] = {
    "supports_undeploy": False,
    "settings_optional": True,
    "stage_plan": [
        {
            "name": "resolve-ns1-node",
            "transport": "internal",
            "kind": "folder-pipeline-review",
            "pipeline_id": "ns1-lan-mac-pxe-prepare",
            "active": "Resolving ns1 LAN MAC PXE target.",
            "complete": "ns1 LAN MAC PXE target reviewed.",
            "timeout": 15,
        },
        {
            "name": "validate-lan-interface",
            "transport": "bkc-ssh",
            "kind": "ns1-lan-mac-pxe-validate",
            "pipeline_id": "ns1-lan-mac-pxe-prepare",
            "active": "Validating ns1 LAN interface for MAC-only PXE.",
            "complete": "ns1 LAN interface validated.",
            "timeout": 30,
        },
        {
            "name": "ensure-dhcp-include",
            "transport": "bkc-ssh",
            "kind": "ns1-lan-mac-pxe-ensure-include",
            "pipeline_id": "ns1-lan-mac-pxe-prepare",
            "active": "Ensuring DHCP root config includes the BKC LAN MAC PXE fragment.",
            "complete": "DHCP root config includes the BKC LAN MAC PXE fragment.",
            "timeout": 30,
        },
        {
            "name": "render-lan-mac-pxe-fragment",
            "transport": "bkc-ssh",
            "kind": "ns1-lan-mac-pxe-render-fragment",
            "pipeline_id": "ns1-lan-mac-pxe-prepare",
            "active": "Rendering LAN MAC-only PXE DHCP fragment on ns1.",
            "complete": "LAN MAC-only PXE DHCP fragment rendered.",
            "timeout": 30,
        },
        {
            "name": "render-lan-dhcp-defaults",
            "transport": "bkc-ssh",
            "kind": "ns1-lan-mac-pxe-render-defaults",
            "pipeline_id": "ns1-lan-mac-pxe-prepare",
            "active": "Rendering DHCP interface defaults on ns1.",
            "complete": "DHCP interface defaults rendered.",
            "timeout": 30,
        },
        {
            "name": "validate-dhcp-config",
            "transport": "bkc-ssh",
            "kind": "ns1-lan-mac-pxe-validate-config",
            "pipeline_id": "ns1-lan-mac-pxe-prepare",
            "active": "Validating DHCP syntax for LAN MAC-only PXE.",
            "complete": "DHCP syntax validated.",
            "timeout": 30,
        },
        {
            "name": "restart-dhcp-if-enabled",
            "transport": "bkc-ssh",
            "kind": "ns1-lan-mac-pxe-restart",
            "pipeline_id": "ns1-lan-mac-pxe-prepare",
            "active": "Restarting DHCP when LAN PXE enable gate is true.",
            "complete": "DHCP restart gate evaluated.",
            "timeout": 60,
        },
        {
            "name": "record-pxe-mac-relationship",
            "transport": "internal",
            "kind": "folder-pipeline-review",
            "pipeline_id": "ns1-lan-mac-pxe-prepare",
            "active": "Recording R630 MAC PXE relationship intent.",
            "complete": "R630 MAC PXE relationship intent reviewed.",
            "timeout": 15,
        },
    ],
    "complete_message": "ns1 LAN MAC-only PXE preparation completed.",
}
WORKFLOW_DEFINITIONS["baremetal-r630-pxe-validation"] = {
    "supports_undeploy": False,
    "settings_optional": True,
    "stage_plan": [
        {
            "name": "resolve-physical-identity",
            "transport": "internal",
            "kind": "folder-pipeline-review",
            "pipeline_id": "baremetal-r630-pxe-validation",
            "active": "Reviewing first R630 physical identity resolution.",
            "complete": "First R630 physical identity resolution reviewed.",
            "timeout": 15,
        },
        {
            "name": "validate-bmc-reachability",
            "transport": "internal",
            "kind": "folder-pipeline-review",
            "pipeline_id": "baremetal-r630-pxe-validation",
            "active": "Reviewing first R630 BMC reachability evidence.",
            "complete": "First R630 BMC reachability evidence reviewed.",
            "timeout": 30,
        },
        {
            "name": "validate-provisioning-services",
            "transport": "internal",
            "kind": "folder-pipeline-review",
            "pipeline_id": "baremetal-r630-pxe-validation",
            "active": "Reviewing ns1 DHCP/PXE service evidence for the first R630.",
            "complete": "ns1 DHCP/PXE service evidence reviewed for the first R630.",
            "timeout": 30,
        },
        {
            "name": "validate-image-assets",
            "transport": "internal",
            "kind": "folder-pipeline-review",
            "pipeline_id": "baremetal-r630-pxe-validation",
            "active": "Reviewing Debian Trixie image asset validation intent.",
            "complete": "Debian Trixie image asset validation intent reviewed.",
            "timeout": 30,
        },
        {
            "name": "validate-storage-visibility",
            "transport": "internal",
            "kind": "folder-pipeline-review",
            "pipeline_id": "baremetal-r630-pxe-validation",
            "active": "Reviewing first R630 storage controller and disk visibility.",
            "complete": "First R630 storage controller and disk visibility reviewed.",
            "timeout": 60,
        },
        {
            "name": "render-boot-intent",
            "transport": "internal",
            "kind": "folder-pipeline-review",
            "pipeline_id": "baremetal-r630-pxe-validation",
            "active": "Reviewing first R630 one-shot boot intent.",
            "complete": "First R630 one-shot boot intent reviewed.",
            "timeout": 15,
        },
        {
            "name": "record-validation-evidence",
            "transport": "internal",
            "kind": "folder-pipeline-review",
            "pipeline_id": "baremetal-r630-pxe-validation",
            "active": "Reviewing first R630 provisioning evidence recording.",
            "complete": "First R630 provisioning evidence recording reviewed.",
            "timeout": 15,
        },
    ],
    "complete_message": "Bare metal R630 PXE validation review completed.",
}
WORKFLOW_DEFINITIONS["ns1-default-pxe-diagnostics"] = {
    "supports_undeploy": False,
    "settings_optional": True,
    "stage_plan": [
        {
            "name": "load-diagnostic-intent",
            "transport": "internal",
            "kind": "folder-pipeline-review",
            "pipeline_id": "ns1-default-pxe-diagnostics",
            "active": "Reviewing default PXE diagnostic intent.",
            "complete": "Default PXE diagnostic intent reviewed.",
            "timeout": 15,
        },
        {
            "name": "validate-diagnostic-boundary",
            "transport": "internal",
            "kind": "folder-pipeline-review",
            "pipeline_id": "ns1-default-pxe-diagnostics",
            "active": "Reviewing bounded PXE-only diagnostic DHCP boundary.",
            "complete": "Bounded PXE-only diagnostic DHCP boundary reviewed.",
            "timeout": 15,
        },
        {
            "name": "render-default-diagnostic-ipxe",
            "transport": "internal",
            "kind": "folder-pipeline-review",
            "pipeline_id": "ns1-default-pxe-diagnostics",
            "active": "Reviewing default diagnostic iPXE render plan.",
            "complete": "Default diagnostic iPXE render plan reviewed.",
            "timeout": 15,
        },
        {
            "name": "render-dhcp-diagnostic-fragment",
            "transport": "internal",
            "kind": "folder-pipeline-review",
            "pipeline_id": "ns1-default-pxe-diagnostics",
            "active": "Reviewing DHCP fragment render plan for default PXE diagnostics.",
            "complete": "DHCP fragment render plan for default PXE diagnostics reviewed.",
            "timeout": 15,
        },
        {
            "name": "validate-live-assets",
            "transport": "internal",
            "kind": "folder-pipeline-review",
            "pipeline_id": "ns1-default-pxe-diagnostics",
            "active": "Reviewing Debian live diagnostic asset validation plan.",
            "complete": "Debian live diagnostic asset validation plan reviewed.",
            "timeout": 60,
        },
        {
            "name": "plan-enable-lease-only-boundary",
            "transport": "internal",
            "kind": "folder-pipeline-review",
            "pipeline_id": "ns1-default-pxe-diagnostics",
            "active": "Reviewing guarded enablement of the lease-only PXE boundary.",
            "complete": "Lease-only PXE boundary enablement reviewed.",
            "timeout": 30,
        },
        {
            "name": "record-default-diagnostic-profile",
            "transport": "internal",
            "kind": "folder-pipeline-review",
            "pipeline_id": "ns1-default-pxe-diagnostics",
            "active": "Reviewing default PXE diagnostic profile relationships.",
            "complete": "Default PXE diagnostic profile relationships reviewed.",
            "timeout": 15,
        },
    ],
    "complete_message": "Default PXE diagnostics review completed.",
}
WORKFLOW_DEFINITIONS["baremetal-openstack-lab-prepare"] = {
    "supports_undeploy": False,
    "settings_optional": True,
    "stage_plan": [
        {
            "name": "load-hardware-intent",
            "transport": "internal",
            "kind": "folder-pipeline-review",
            "pipeline_id": "baremetal-openstack-lab-prepare",
            "active": "Reviewing first R630 Trixie/OpenStack hardware intent.",
            "complete": "First R630 Trixie/OpenStack hardware intent reviewed.",
            "timeout": 15,
        },
        {
            "name": "register-physical-nodes",
            "transport": "internal",
            "kind": "folder-pipeline-review",
            "pipeline_id": "baremetal-openstack-lab-prepare",
            "active": "Reviewing first R630 physical node registration.",
            "complete": "First R630 physical node registration reviewed.",
            "timeout": 30,
        },
        {
            "name": "validate-bmc-access",
            "transport": "internal",
            "kind": "folder-pipeline-review",
            "pipeline_id": "baremetal-openstack-lab-prepare",
            "active": "Reviewing first R630 BMC access validation.",
            "complete": "First R630 BMC access validation reviewed.",
            "timeout": 60,
        },
        {
            "name": "validate-provisioning-services",
            "transport": "internal",
            "kind": "folder-pipeline-review",
            "pipeline_id": "baremetal-openstack-lab-prepare",
            "active": "Reviewing first R630 provisioning service validation.",
            "complete": "First R630 provisioning service validation reviewed.",
            "timeout": 45,
        },
        {
            "name": "plan-openstack-edge-network",
            "transport": "internal",
            "kind": "folder-pipeline-review",
            "pipeline_id": "baremetal-openstack-lab-prepare",
            "active": "Reviewing OpenStack lab edge network plan.",
            "complete": "OpenStack lab edge network plan reviewed.",
            "timeout": 30,
        },
        {
            "name": "validate-base-os-image",
            "transport": "internal",
            "kind": "folder-pipeline-review",
            "pipeline_id": "baremetal-openstack-lab-prepare",
            "active": "Reviewing Debian Trixie base OS image validation.",
            "complete": "Debian Trixie base OS image validation reviewed.",
            "timeout": 45,
        },
        {
            "name": "select-storage-profile",
            "transport": "internal",
            "kind": "folder-pipeline-review",
            "pipeline_id": "baremetal-openstack-lab-prepare",
            "active": "Reviewing first R630 OpenStack storage profile selection.",
            "complete": "First R630 OpenStack storage profile selection reviewed.",
            "timeout": 60,
        },
        {
            "name": "render-openstack-base-boot-intent",
            "transport": "internal",
            "kind": "openstack-base-boot-render",
            "pipeline_id": "baremetal-openstack-lab-prepare",
            "active": "Rendering first R630 Trixie iPXE and preseed assets on ns1.",
            "complete": "First R630 Trixie iPXE and preseed assets rendered.",
            "timeout": 30,
        },
        {
            "name": "plan-base-os-install",
            "transport": "internal",
            "kind": "folder-pipeline-review",
            "pipeline_id": "baremetal-openstack-lab-prepare",
            "active": "Reviewing first R630 base OS install gate.",
            "complete": "First R630 base OS install gate reviewed.",
            "timeout": 30,
        },
        {
            "name": "plan-firstboot-enrollment",
            "transport": "internal",
            "kind": "folder-pipeline-review",
            "pipeline_id": "baremetal-openstack-lab-prepare",
            "active": "Reviewing first R630 BKC SSH enrollment plan.",
            "complete": "First R630 BKC SSH enrollment plan reviewed.",
            "timeout": 30,
        },
        {
            "name": "validate-openstack-host-baseline",
            "transport": "internal",
            "kind": "openstack-base-boot-validate",
            "pipeline_id": "baremetal-openstack-lab-prepare",
            "active": "Validating first R630 PXE boot assets from ns1.",
            "complete": "First R630 PXE boot assets validated.",
            "timeout": 120,
        },
        {
            "name": "prepare-trixie-neutron-hosts",
            "transport": "internal",
            "kind": "folder-pipeline-review",
            "pipeline_id": "baremetal-openstack-lab-prepare",
            "active": "Reviewing Trixie OpenStack host prep handoff.",
            "complete": "Trixie OpenStack host prep handoff reviewed.",
            "timeout": 60,
        },
        {
            "name": "plan-openstack-installer-handoff",
            "transport": "internal",
            "kind": "folder-pipeline-review",
            "pipeline_id": "baremetal-openstack-lab-prepare",
            "active": "Reviewing OpenStack installer provider handoff.",
            "complete": "OpenStack installer provider handoff reviewed.",
            "timeout": 60,
        },
    ],
    "complete_message": "Bare metal OpenStack lab prepare review completed.",
}
WORKFLOW_DEFINITIONS["trixie-openstack-host-prepare"] = {
    "supports_undeploy": False,
    "settings_optional": True,
    "stage_plan": [
        {
            "name": "load-openstack-host-intent",
            "transport": "internal",
            "kind": "folder-pipeline-review",
            "pipeline_id": "trixie-openstack-host-prepare",
            "active": "Reviewing Trixie OpenStack host intent.",
            "complete": "Trixie OpenStack host intent reviewed.",
            "timeout": 15,
        },
        {
            "name": "validate-firstboot-login",
            "transport": "bkc-ssh",
            "kind": "openstack-host-firstboot-login",
            "pipeline_id": "trixie-openstack-host-prepare",
            "active": "Validating first-boot BKC SSH and demo login expectations.",
            "complete": "First-boot BKC SSH and demo login expectations validated.",
            "timeout": 120,
        },
        {
            "name": "normalize-firstboot-baseline",
            "transport": "bkc-ssh",
            "kind": "openstack-host-base-normalize",
            "pipeline_id": "trixie-openstack-host-prepare",
            "active": "Normalizing first-boot hostname, admin sudo, tools, and BKC marker.",
            "complete": "First-boot hostname, admin sudo, tools, and BKC marker normalized.",
            "timeout": 600,
        },
        {
            "name": "validate-network-sides",
            "transport": "bkc-ssh",
            "kind": "openstack-host-network-sides",
            "pipeline_id": "trixie-openstack-host-prepare",
            "active": "Validating current bootstrap network and planned OpenStack side networks.",
            "complete": "Current bootstrap network and planned OpenStack side networks validated.",
            "timeout": 120,
        },
        {
            "name": "prepare-neutron-host-packages",
            "transport": "bkc-ssh",
            "kind": "openstack-host-package-prepare",
            "pipeline_id": "trixie-openstack-host-prepare",
            "active": "Preparing Trixie host packages for OpenStack Neutron/OVS.",
            "complete": "Trixie host package preparation completed.",
            "timeout": 1200,
        },
        {
            "name": "validate-neutron-host-readiness",
            "transport": "bkc-ssh",
            "kind": "openstack-host-neutron-validate",
            "pipeline_id": "trixie-openstack-host-prepare",
            "active": "Validating Trixie OpenStack Neutron/OVS readiness.",
            "complete": "Trixie OpenStack Neutron/OVS readiness validated.",
            "timeout": 180,
        },
        {
            "name": "cache-openstack-image-assets",
            "transport": "bkc-ssh",
            "kind": "openstack-host-image-cache",
            "pipeline_id": "trixie-openstack-host-prepare",
            "active": "Caching OpenStack QCOW/image assets on the prepared host.",
            "complete": "OpenStack QCOW/image assets cached on the prepared host.",
            "timeout": 1800,
        },
        {
            "name": "validate-openstack-web-target",
            "transport": "internal",
            "kind": "folder-pipeline-review",
            "pipeline_id": "trixie-openstack-host-prepare",
            "active": "Reviewing OpenStack web/API validation target.",
            "complete": "OpenStack web/API validation target reviewed.",
            "timeout": 30,
        },
        {
            "name": "record-openstack-network-profile",
            "transport": "internal",
            "kind": "folder-pipeline-review",
            "pipeline_id": "trixie-openstack-host-prepare",
            "active": "Reviewing OpenStack network profile relationship recording.",
            "complete": "OpenStack network profile relationship recording reviewed.",
            "timeout": 15,
        },
    ],
    "complete_message": "Trixie OpenStack host preparation completed.",
}
WORKFLOW_DEFINITIONS["openstack-lab-seed-and-validate"] = {
    "supports_undeploy": False,
    "settings_optional": True,
    "stage_plan": [
        {
            "name": "load-openstack-seed-intent",
            "transport": "internal",
            "kind": "folder-pipeline-review",
            "pipeline_id": "openstack-lab-seed-and-validate",
            "active": "Reviewing OpenStack seed and validation intent.",
            "complete": "OpenStack seed and validation intent reviewed.",
            "timeout": 15,
        },
        {
            "name": "validate-openstack-api-access",
            "transport": "openstack-api",
            "kind": "folder-pipeline-review",
            "pipeline_id": "openstack-lab-seed-and-validate",
            "active": "Reviewing OpenStack API credential validation.",
            "complete": "OpenStack API credential validation reviewed.",
            "timeout": 120,
        },
        {
            "name": "validate-horizon-dashboard",
            "transport": "http",
            "kind": "folder-pipeline-review",
            "pipeline_id": "openstack-lab-seed-and-validate",
            "active": "Reviewing Horizon dashboard validation.",
            "complete": "Horizon dashboard validation reviewed.",
            "timeout": 60,
        },
        {
            "name": "seed-project-and-user",
            "transport": "openstack-api",
            "kind": "folder-pipeline-review",
            "pipeline_id": "openstack-lab-seed-and-validate",
            "active": "Reviewing OpenStack project and user seed.",
            "complete": "OpenStack project and user seed reviewed.",
            "timeout": 180,
        },
        {
            "name": "seed-networks",
            "transport": "openstack-api",
            "kind": "folder-pipeline-review",
            "pipeline_id": "openstack-lab-seed-and-validate",
            "active": "Reviewing OpenStack provider and tenant network seed.",
            "complete": "OpenStack provider and tenant network seed reviewed.",
            "timeout": 240,
        },
        {
            "name": "seed-image-flavor-keypair-security",
            "transport": "openstack-api",
            "kind": "folder-pipeline-review",
            "pipeline_id": "openstack-lab-seed-and-validate",
            "active": "Reviewing OpenStack image, flavor, keypair, and security group seed.",
            "complete": "OpenStack image, flavor, keypair, and security group seed reviewed.",
            "timeout": 600,
        },
        {
            "name": "launch-smoke-instance",
            "transport": "openstack-api",
            "kind": "folder-pipeline-review",
            "pipeline_id": "openstack-lab-seed-and-validate",
            "active": "Reviewing OpenStack smoke instance launch.",
            "complete": "OpenStack smoke instance launch reviewed.",
            "timeout": 600,
        },
        {
            "name": "validate-smoke-instance",
            "transport": "openstack-api",
            "kind": "folder-pipeline-review",
            "pipeline_id": "openstack-lab-seed-and-validate",
            "active": "Reviewing OpenStack smoke instance validation.",
            "complete": "OpenStack smoke instance validation reviewed.",
            "timeout": 300,
        },
        {
            "name": "record-bkc-openstack-ownership",
            "transport": "internal",
            "kind": "folder-pipeline-review",
            "pipeline_id": "openstack-lab-seed-and-validate",
            "active": "Reviewing BKC OpenStack ownership relationships.",
            "complete": "BKC OpenStack ownership relationships reviewed.",
            "timeout": 30,
        },
    ],
    "complete_message": "OpenStack lab seed and validation review completed.",
}
WORKFLOW_DEFINITIONS["openstack-kolla-single-node-install"] = {
    "supports_undeploy": False,
    "settings_optional": True,
    "stage_plan": [
        {
            "name": "load-kolla-install-intent",
            "transport": "internal",
            "kind": "folder-pipeline-review",
            "pipeline_id": "openstack-kolla-single-node-install",
            "active": "Reviewing Kolla single-node install intent.",
            "complete": "Kolla single-node install intent reviewed.",
            "timeout": 15,
        },
        {
            "name": "validate-kolla-host-readiness",
            "transport": "bkc-ssh",
            "kind": "openstack-kolla-phase",
            "pipeline_id": "openstack-kolla-single-node-install",
            "phase": "preflight",
            "active": "Validating host readiness for a real Kolla install.",
            "complete": "Host readiness for Kolla validated.",
            "timeout": 180,
        },
        {
            "name": "prepare-kolla-dependencies",
            "transport": "bkc-ssh",
            "kind": "openstack-kolla-phase",
            "pipeline_id": "openstack-kolla-single-node-install",
            "phase": "dependencies",
            "operation_modes": ["prepare"],
            "active": "Installing Kolla host dependencies.",
            "complete": "Kolla host dependencies installed.",
            "timeout": 1800,
        },
        {
            "name": "install-kolla-ansible",
            "transport": "bkc-ssh",
            "kind": "openstack-kolla-phase",
            "pipeline_id": "openstack-kolla-single-node-install",
            "phase": "install",
            "operation_modes": ["prepare"],
            "active": "Installing Kolla Ansible into the BKC venv.",
            "complete": "Kolla Ansible installed into the BKC venv.",
            "timeout": 1800,
        },
        {
            "name": "render-kolla-configuration",
            "transport": "bkc-ssh",
            "kind": "openstack-kolla-phase",
            "pipeline_id": "openstack-kolla-single-node-install",
            "phase": "configure",
            "operation_modes": ["precheck"],
            "active": "Rendering Kolla single-node configuration.",
            "complete": "Kolla single-node configuration rendered.",
            "timeout": 600,
        },
        {
            "name": "kolla-bootstrap-servers",
            "transport": "bkc-ssh",
            "kind": "openstack-kolla-phase",
            "pipeline_id": "openstack-kolla-single-node-install",
            "phase": "bootstrap",
            "operation_modes": ["precheck"],
            "active": "Running kolla-ansible bootstrap-servers.",
            "complete": "Kolla bootstrap-servers completed.",
            "timeout": 2400,
        },
        {
            "name": "kolla-prechecks",
            "transport": "bkc-ssh",
            "kind": "openstack-kolla-phase",
            "pipeline_id": "openstack-kolla-single-node-install",
            "phase": "prechecks",
            "operation_modes": ["precheck"],
            "active": "Running Kolla prechecks.",
            "complete": "Kolla prechecks completed.",
            "timeout": 2400,
        },
        {
            "name": "kolla-deploy",
            "transport": "bkc-ssh",
            "kind": "openstack-kolla-phase",
            "pipeline_id": "openstack-kolla-single-node-install",
            "phase": "deploy",
            "operation_modes": ["deploy"],
            "active": "Deploying OpenStack with Kolla.",
            "complete": "OpenStack deploy completed.",
            "timeout": 7200,
        },
        {
            "name": "kolla-post-deploy",
            "transport": "bkc-ssh",
            "kind": "openstack-kolla-phase",
            "pipeline_id": "openstack-kolla-single-node-install",
            "phase": "postdeploy",
            "operation_modes": ["validate"],
            "active": "Running Kolla post-deploy credential export.",
            "complete": "Kolla post-deploy credential export completed.",
            "timeout": 900,
        },
        {
            "name": "validate-horizon-keystone",
            "transport": "bkc-ssh",
            "kind": "openstack-kolla-phase",
            "pipeline_id": "openstack-kolla-single-node-install",
            "phase": "validate",
            "operation_modes": ["validate"],
            "active": "Validating Horizon and Keystone on the OpenStack host.",
            "complete": "Horizon and Keystone validation completed.",
            "timeout": 300,
        },
        {
            "name": "record-kolla-handoff",
            "transport": "internal",
            "kind": "folder-pipeline-review",
            "pipeline_id": "openstack-kolla-single-node-install",
            "operation_modes": ["validate"],
            "active": "Recording BKC to OpenStack ownership handoff.",
            "complete": "BKC to OpenStack ownership handoff recorded.",
            "timeout": 30,
        },
    ],
    "complete_message": "OpenStack Kolla single-node install pipeline completed.",
}
WORKFLOW_DEFINITIONS["baremetal-vmware-trial-prepare"] = {
    "supports_undeploy": False,
    "settings_optional": True,
    "stage_plan": [
        {
            "name": "load-hardware-intent",
            "transport": "internal",
            "kind": "folder-pipeline-review",
            "pipeline_id": "baremetal-vmware-trial-prepare",
            "active": "Reviewing experimental ESXi hardware intent.",
            "complete": "Experimental ESXi hardware intent reviewed.",
            "timeout": 15,
        },
        {
            "name": "register-physical-nodes",
            "transport": "internal",
            "kind": "folder-pipeline-review",
            "pipeline_id": "baremetal-vmware-trial-prepare",
            "active": "Reviewing experimental ESXi physical node registration.",
            "complete": "Experimental ESXi physical node registration reviewed.",
            "timeout": 30,
        },
        {
            "name": "validate-bmc-access",
            "transport": "internal",
            "kind": "folder-pipeline-review",
            "pipeline_id": "baremetal-vmware-trial-prepare",
            "active": "Reviewing experimental ESXi BMC access validation.",
            "complete": "Experimental ESXi BMC access validation reviewed.",
            "timeout": 60,
        },
        {
            "name": "validate-provisioning-services",
            "transport": "internal",
            "kind": "folder-pipeline-review",
            "pipeline_id": "baremetal-vmware-trial-prepare",
            "active": "Reviewing experimental ESXi provisioning services.",
            "complete": "Experimental ESXi provisioning services reviewed.",
            "timeout": 45,
        },
        {
            "name": "validate-operator-supplied-media",
            "transport": "internal",
            "kind": "folder-pipeline-review",
            "pipeline_id": "baremetal-vmware-trial-prepare",
            "active": "Reviewing operator-supplied VMware installer media.",
            "complete": "Operator-supplied VMware installer media reviewed.",
            "timeout": 45,
        },
        {
            "name": "render-vmware-kickstart-intent",
            "transport": "internal",
            "kind": "vmware-esxi-boot-assets-render",
            "pipeline_id": "baremetal-vmware-trial-prepare",
            "active": "Rendering experimental ESXi boot.cfg, ks.cfg, and firstboot intent when enabled.",
            "complete": "Experimental ESXi boot.cfg, ks.cfg, and firstboot intent rendered or reviewed.",
            "timeout": 30,
        },
        {
            "name": "plan-stage-vmware-installer-media",
            "transport": "bkc-ssh",
            "kind": "vmware-esxi-media-stage",
            "pipeline_id": "baremetal-vmware-trial-prepare",
            "active": "Validating and publishing operator-supplied ESXi media on ns1.",
            "complete": "ESXi media, vendor boot configuration, and HTTP assets validated on ns1.",
            "timeout": 900,
        },
        {
            "name": "plan-esxi-install",
            "transport": "pxe+http+redfish",
            "kind": "vmware-esxi-iso-handoff",
            "pipeline_id": "baremetal-vmware-trial-prepare",
            "active": "Booting Server2 from the intact vendor ESXi ISO and waiting for operator handoff.",
            "complete": "Server2 reached the intact ESXi ISO handoff boundary.",
            "timeout": 1800,
        },
        {
            "name": "validate-vmware-firstboot",
            "transport": "internal",
            "kind": "folder-pipeline-review",
            "pipeline_id": "baremetal-vmware-trial-prepare",
            "active": "Reviewing experimental ESXi first boot validation.",
            "complete": "Experimental ESXi first boot validation reviewed.",
            "timeout": 120,
        },
        {
            "name": "validate-esxi-web-target",
            "transport": "internal",
            "kind": "folder-pipeline-review",
            "pipeline_id": "baremetal-vmware-trial-prepare",
            "active": "Reviewing ESXi web/API validation target.",
            "complete": "ESXi web/API validation target reviewed.",
            "timeout": 30,
        },
        {
            "name": "plan-vcenter-registration",
            "transport": "internal",
            "kind": "folder-pipeline-review",
            "pipeline_id": "baremetal-vmware-trial-prepare",
            "active": "Reviewing optional vCenter registration plan.",
            "complete": "Optional vCenter registration plan reviewed.",
            "timeout": 60,
        },
    ],
    "complete_message": "Experimental bare metal VMware trial prepare review completed.",
}
WORKFLOW_DEFINITIONS["baremetal-proxmox-trial-prepare"] = {
    "supports_undeploy": False,
    "settings_optional": True,
    "stage_plan": [
        {
            "name": "load-proxmox-intent",
            "transport": "internal",
            "kind": "folder-pipeline-review",
            "pipeline_id": "baremetal-proxmox-trial-prepare",
            "active": "Reviewing Server2 Proxmox installation intent.",
            "complete": "Server2 Proxmox installation intent reviewed.",
            "timeout": 15,
        },
        {
            "name": "validate-unattended-media",
            "transport": "bkc-ssh",
            "kind": "folder-pipeline-review",
            "pipeline_id": "baremetal-proxmox-trial-prepare",
            "active": "Reviewing the checksum-gated Proxmox unattended media.",
            "complete": "Proxmox unattended media validation intent reviewed.",
            "timeout": 180,
        },
        {
            "name": "arm-server2-one-shot-pxe",
            "transport": "bkc-ssh",
            "kind": "folder-pipeline-review",
            "pipeline_id": "baremetal-proxmox-trial-prepare",
            "active": "Evaluating the guarded Server2 one-shot PXE arm.",
            "complete": "Server2 one-shot PXE arm gate evaluated.",
            "timeout": 180,
        },
        {
            "name": "plan-server2-install",
            "transport": "internal",
            "kind": "folder-pipeline-review",
            "pipeline_id": "baremetal-proxmox-trial-prepare",
            "active": "Reviewing the destructive Server2 install boundary.",
            "complete": "Destructive Server2 install boundary reviewed.",
            "timeout": 30,
        },
        {
            "name": "validate-proxmox-management",
            "transport": "internal",
            "kind": "folder-pipeline-review",
            "pipeline_id": "baremetal-proxmox-trial-prepare",
            "active": "Reviewing Proxmox management validation targets.",
            "complete": "Proxmox management validation targets reviewed.",
            "timeout": 30,
        },
        {
            "name": "disarm-server2-one-shot-pxe",
            "transport": "bkc-ssh",
            "kind": "folder-pipeline-review",
            "pipeline_id": "baremetal-proxmox-trial-prepare",
            "active": "Reviewing the mandatory post-install PXE disarm boundary.",
            "complete": "Post-install PXE disarm boundary reviewed.",
            "timeout": 60,
        },
    ],
    "complete_message": "Server2 Proxmox filming preflight completed.",
}

# Filming workflows deliberately use separate IDs from the older review recipes.
# Every stage below either changes the target or validates live evidence.
WORKFLOW_DEFINITIONS["baremetal-openstack-lab-deploy"] = {
    "supports_undeploy": False, "settings_optional": True,
    "stage_plan": [
        {"name": "destructive-one-shot-trixie", "kind": "video-openstack-one-shot", "active": "Erasing all Server1 disks and performing one unattended Trixie PXE transaction.", "timeout": 4200},
    ], "complete_message": "Server1 all-drive destructive Trixie PXE installation completed and SSH-validated.",
}
WORKFLOW_DEFINITIONS["baremetal-proxmox-deploy"] = {
    "supports_undeploy": False, "settings_optional": True,
    "stage_plan": [
        {"name": "validate-unattended-media", "kind": "video-proxmox-media", "active": "Validating the checksum-pinned Proxmox auto-install media and PXE initrd.", "timeout": 300},
        {"name": "arm-and-boot-server2", "kind": "video-proxmox-boot", "active": "Arming Server2 one-shot PXE and requesting the destructive install.", "timeout": 180},
        {"name": "wipe-install-observe-and-disarm", "kind": "video-proxmox-handoff", "active": "Running the destructive Proxmox install, observing payload handoff, then disarming DHCP PXE.", "timeout": 1800},
        {"name": "validate-proxmox-firstboot", "kind": "video-proxmox-validate", "active": "Waiting for disk boot and validating Proxmox API, SSH, and KVM.", "timeout": 3600},
    ], "complete_message": "Server2 bare-metal Proxmox deployment completed and validated.",
}
WORKFLOW_DEFINITIONS["native-openstack-all-in-one"] = {
    "supports_undeploy": False, "settings_optional": True,
    "stage_plan": [
        {"name": "install-native-openstack", "kind": "video-openstack-install", "active": "Installing Keystone, Horizon, Glance, Placement, Nova, and Neutron on Server1.", "timeout": 7200},
        {"name": "validate-openstack-services", "kind": "video-openstack-validate", "active": "Validating OpenStack APIs and Horizon on Server1.", "timeout": 300},
    ], "complete_message": "Server1 native OpenStack services and Horizon completed and validated.",
}
WORKFLOW_DEFINITIONS["lab-dual-platform-seed-validate"] = {
    "supports_undeploy": False, "settings_optional": True,
    "stage_plan": [
        {"name": "seed-openstack-resources", "kind": "video-seed-openstack", "active": "Seeding real OpenStack project, network, image, flavor, and smoke server.", "timeout": 1800},
        {"name": "seed-proxmox-base-guests", "kind": "video-seed-proxmox", "active": "Migrating the proven Trixie VM from Proxmox .9 and creating both base guests.", "timeout": 3600},
        {"name": "validate-both-platforms", "kind": "video-seed-validate", "active": "Validating OpenStack, Proxmox guests, KVM, and edge dashboards.", "timeout": 600},
    ], "complete_message": "Both lab platforms were seeded with real resources and validated.",
}
WORKFLOW_DEFINITIONS["openstack-local-ai-openwebui-preflight"] = {
    "supports_undeploy": False, "settings_optional": True,
    "stage_plan": [
        {"name": "preflight-server1-ai-capacity", "kind": "video-local-ai-capacity", "active": "Measuring Server1/OpenStack capacity, egress, and container/runtime state for the local-AI bonus lane.", "timeout": 300},
        {"name": "ensure-openstack-ai-vm", "kind": "video-local-ai-vm", "active": "Ensuring the cattle OpenStack AI VM flavor, security group, keypair, and server exist.", "timeout": 1800},
        {"name": "install-ollama-baremetal", "kind": "video-local-ai-ollama", "active": "Installing and validating Ollama as a Server1 systemd service.", "timeout": 1800},
        {"name": "deploy-openwebui-container", "kind": "video-local-ai-openwebui", "active": "Installing Podman if needed and running OpenWebUI against Server1 Ollama.", "timeout": 1800},
        {"name": "validate-local-ai-stack", "kind": "video-local-ai-validate", "active": "Validating Ollama API, OpenWebUI HTTP, and local-AI service receipts.", "timeout": 300},
        {"name": "record-ollama-openwebui-fragments", "kind": "video-local-ai-fragments", "active": "Recording the Ollama, OpenWebUI, benchmark, and Cytoscape layout fragments for promotion review.", "timeout": 60},
    ], "complete_message": "Local-AI/OpenWebUI candidate preflight completed.",
}
WORKFLOW_DEFINITIONS["openstack-docker-swarm-seed"] = {
    "supports_undeploy": False, "settings_optional": True,
    "stage_plan": [
        {"name": "preflight-openstack-swarm-base", "kind": "video-openstack-swarm-preflight", "active": "Validating OpenStack API, Debian cloud image, lab network, keypair source, and optional Fedora metadata.", "timeout": 300},
        {"name": "ensure-openstack-swarm-vms", "kind": "video-openstack-swarm-vms", "active": "Ensuring the five Debian OpenStack VMs for the cattle Docker Swarm exist and are ACTIVE.", "timeout": 1800},
        {"name": "bootstrap-openstack-docker-swarm", "kind": "video-openstack-swarm-bootstrap", "active": "Installing Docker, initializing manager 1, joining manager 2, and joining three workers.", "timeout": 2400},
        {"name": "validate-openstack-docker-swarm", "kind": "video-openstack-swarm-validate", "active": "Validating SSH, Docker, swarm membership, and OpenStack server state.", "timeout": 600},
        {"name": "record-openstack-swarm-fragments", "kind": "video-openstack-swarm-fragments", "active": "Recording known-good OpenStack swarm image, VM, SSH, and bootstrap fragments.", "timeout": 60},
    ], "complete_message": "OpenStack-hosted three-node Docker Swarm seed completed and validated.",
}
WORKFLOW_DEFINITIONS["openstack-bkc-compose-deploy"] = {
    "supports_undeploy": False, "settings_optional": True,
    "stage_plan": [
        {"name": "preflight-openstack-bkc-vm", "kind": "openstack-bkc-compose-preflight", "active": "Validating SSH, OS tools, ns1 runtime export, and Docker Compose readiness on the OpenStack BKC VM.", "timeout": 180},
        {"name": "stage-openstack-bkc-runtime", "kind": "openstack-bkc-compose-runtime", "active": "Preparing /srv/bkc, mounting ns1 runtime, and ensuring mutable runtime subfolders exist.", "timeout": 300},
        {"name": "render-openstack-bkc-compose", "kind": "openstack-bkc-compose-render", "active": "Rendering the target .env and Docker Compose file from known-good templates.", "timeout": 180},
        {"name": "deploy-openstack-bkc-compose", "kind": "openstack-bkc-compose-up", "active": "Updating source and running docker compose up -d --build on the OpenStack BKC VM.", "timeout": 1800},
        {"name": "validate-openstack-bkc", "kind": "openstack-bkc-compose-validate", "active": "Validating the new BKC /ready endpoint and container state.", "timeout": 300},
        {"name": "publish-openstack-bkc-edge-pointer", "kind": "openstack-bkc-compose-edge-pointer", "active": "Publishing the intended edge pointer for the OpenStack-side BKC.", "timeout": 60},
    ], "complete_message": "OpenStack-side BKC Docker Compose deployment completed and validated.",
}
WORKFLOW_DEFINITIONS["openstack-bkc-swarm-promote"] = {
    "supports_undeploy": False,
    "settings_optional": True,
    "stage_plan": [
        {
            "name": "promote-openstack-bkc-swarm-services",
            "kind": "openstack-bkc-swarm-promote-script",
            "active": "Promoting the canonical OpenStack-hosted BKC Swarm services through Portainer to the selected validated image.",
            "timeout": 900,
        },
        {
            "name": "record-openstack-bkc-promotion-fragment",
            "kind": "event-note",
            "message": "Known-good guardrail: bkc.lab.auzietek.com points at the OpenStack bkc-alt services; edge BKC is utility/fallback.",
            "complete": "OpenStack BKC promotion fragment recorded.",
            "timeout": 30,
        },
    ],
    "complete_message": "OpenStack BKC Swarm promotion completed.",
}
WORKFLOW_DEFINITIONS["esxi-docker-swarm-seed"] = {
    "supports_undeploy": False, "settings_optional": True,
    "stage_plan": [
        {"name": "preflight-esxi-api", "kind": "video-esxi-api-preflight", "active": "Validating ESXi API login, datastore visibility, network names, and current inventory.", "timeout": 120},
        {"name": "validate-known-good-base", "kind": "video-esxi-swarm-inventory", "active": "Validating the known-good bkc-trixie-base and target guest inventory.", "timeout": 120},
        {"name": "clone-esxi-swarm-vms", "kind": "video-esxi-swarm-shells", "active": "Ensuring ESXi Docker Swarm VM clones exist with the known-good VMX shape.", "timeout": 900},
        {"name": "pin-esxi-swarm-dhcp", "kind": "video-esxi-swarm-dhcp", "active": "Discovering generated MACs and pinning ns1 DHCP reservations for 10.20.0.121-.125.", "timeout": 180},
        {"name": "configure-esxi-docker-swarm", "kind": "video-esxi-swarm-bootstrap", "active": "Installing Docker and converging the two-manager/three-worker ESXi swarm.", "timeout": 2400},
        {"name": "validate-esxi-guest-inventory", "kind": "video-esxi-swarm-inventory", "active": "Validating ESXi inventory for imported/created guest VMs.", "timeout": 120},
        {"name": "record-esxi-swarm-fragments", "kind": "video-esxi-swarm-fragments", "active": "Recording ESXi clone/config/bootstrap known-good fragments.", "timeout": 60},
    ], "complete_message": "ESXi Docker Swarm seed converged and validated.",
}
WORKFLOW_DEFINITIONS["micro-blog-swarm-compose"] = {
    "supports_undeploy": False, "settings_optional": True,
    "stage_plan": [
        {"name": "preflight-micro-blog-source", "kind": "micro-blog-source-preflight", "active": "Validating the NFS-staged micro-blog source bundle and required Dockerfiles.", "timeout": 120},
        {"name": "preflight-lab-registry", "kind": "micro-blog-registry-preflight", "active": "Validating lab registry reachability from the target swarm manager.", "timeout": 120},
        {"name": "preflight-target-swarm", "kind": "micro-blog-target-swarm-preflight", "active": "Validating ESXi Docker Swarm manager reachability, node shape, and worker labels.", "timeout": 180},
        {"name": "build-and-push-micro-blog-images", "kind": "micro-blog-build-push", "active": "Building and pushing micro-blog app images from the NFS-staged source bundle.", "timeout": 1200},
        {"name": "render-micro-blog-swarm-stack", "kind": "micro-blog-render-stack", "active": "Rendering the Swarm-safe micro-blog stack file on the target manager.", "timeout": 180},
        {"name": "stage-micro-blog-stack-on-manager", "kind": "micro-blog-stage-stack", "active": "Staging .env, collector config, content path, and stack bundle on the target manager.", "timeout": 180},
        {"name": "deploy-micro-blog-stack", "kind": "micro-blog-deploy-stack", "active": "Deploying the micro-blog stack through Docker Swarm.", "timeout": 900},
        {"name": "validate-micro-blog-rollout", "kind": "micro-blog-validate-rollout", "active": "Validating micro-blog service replicas and HTTP health endpoints.", "timeout": 360},
        {"name": "optional-seed-micro-blog-lab-journal", "kind": "micro-blog-lab-journal-note", "active": "Recording the optional markdown lab-journal seed path for follow-up.", "timeout": 60},
        {"name": "optional-wire-micro-blog-telemetry", "kind": "micro-blog-telemetry-note", "active": "Recording the optional telemetry backhaul choice for follow-up.", "timeout": 60},
        {"name": "publish-micro-blog-edge-pointer", "kind": "micro-blog-edge-note", "active": "Recording the intended edge pointer for the micro-blog deployment.", "timeout": 60},
        {"name": "record-micro-blog-known-good-fragment", "kind": "micro-blog-fragment-note", "active": "Recording the known-good micro-blog canary fragment.", "timeout": 60},
    ], "complete_message": "Micro-blog Docker Swarm canary completed and validated.",
}
WORKFLOW_DEFINITIONS["micro-blog-esxi-lab-canary-refresh"] = {
    "supports_undeploy": False,
    "settings_optional": True,
    "stage_plan": [
        {
            "name": "refresh-esxi-lab-canary-from-repo",
            "kind": "micro-blog-esxi-lab-canary-refresh-script",
            "active": "Building a fresh micro-blog UI image from the pinned repo commit, pushing through the lab registry, updating the ESXi Swarm service, syncing content, and validating public proof strings.",
            "timeout": 2400,
        },
        {
            "name": "record-micro-blog-known-good-fragment",
            "kind": "micro-blog-fragment-note",
            "active": "Recording the known-good micro-blog ESXi canary refresh fragment.",
            "timeout": 60,
        },
    ],
    "complete_message": "Micro-blog ESXi lab canary refresh completed and validated.",
}
WORKFLOW_DEFINITIONS["micro-blog-lab-content-canary"] = {
    "supports_undeploy": False,
    "settings_optional": True,
    "stage_plan": [
        {
            "name": "sync-content-files-to-lab",
            "kind": "micro-blog-content-sync-only",
            "active": "Copying the staged micro-blog content tree to the ESXi lab swarm nodes without rebuilding or restarting the app runtime.",
            "timeout": 600,
        },
        {
            "name": "run-filesystem-sync-api",
            "kind": "micro-blog-filesystem-sync-api",
            "active": "Calling the micro-blog filesystem sync API so copied markdown/assets become published content.",
            "timeout": 180,
        },
        {
            "name": "validate-lab-content-routes",
            "kind": "micro-blog-content-proof",
            "active": "Validating the lab edge routes and expected proof strings after content sync.",
            "timeout": 240,
        },
        {
            "name": "record-content-refresh-fragment",
            "kind": "micro-blog-content-fragment-note",
            "active": "Recording the content-only refresh receipt and guardrail.",
            "timeout": 60,
        },
    ],
    "complete_message": "Micro-blog lab content canary synced and validated without a runtime redeploy.",
}
WORKFLOW_DEFINITIONS["auzietek-beta-preview-deploy"] = {
    "supports_undeploy": False,
    "settings_optional": True,
    "stage_plan": [
        {
            "name": "preflight-source-and-remote-runtime",
            "kind": "micro-blog-public-ui-preflight",
            "active": "Validating lab-build source, IONOS Compose runtime, and remote .env before a UI/runtime refresh.",
            "timeout": 180,
        },
        {
            "name": "sync-source-preserving-remote-secrets",
            "kind": "micro-blog-public-ui-source-sync",
            "active": "Syncing micro-blog source to IONOS while preserving .env, docker-compose.yml, and deployment-local state.",
            "timeout": 600,
        },
        {
            "name": "build-ui-image",
            "kind": "micro-blog-public-ui-build",
            "active": "Building the public blog-ui image from the refreshed source on the IONOS Compose host.",
            "timeout": 900,
        },
        {
            "name": "restart-ui-service",
            "kind": "micro-blog-public-ui-up",
            "active": "Recreating only the blog-ui service so route/theme code updates take effect.",
            "timeout": 300,
        },
        {
            "name": "smoke-public-preview-urls",
            "kind": "micro-blog-public-ui-smoke",
            "active": "Validating public lane URLs and featured article routing after the UI refresh.",
            "timeout": 240,
        },
        {
            "name": "record-deploy-known-good-fragment",
            "kind": "micro-blog-public-ui-fragment-note",
            "active": "Recording the UI/runtime deploy receipt and .env preservation guardrail.",
            "timeout": 60,
        },
    ],
    "complete_message": "Micro-blog public UI/runtime refresh completed with deployment secrets preserved.",
}
WORKFLOW_DEFINITIONS["auzietek-public-article-publish"] = {
    "supports_undeploy": False,
    "settings_optional": True,
    "stage_plan": [
        {
            "name": "backup-live-site-before-public-promotion",
            "kind": "micro-blog-public-backup",
            "active": "Backing up the IONOS Compose micro-blog content, database, compose file, and redacted environment keys before public promotion.",
            "timeout": 900,
        },
        {
            "name": "sync-approved-content-to-public-compose-volume",
            "kind": "micro-blog-public-content-rsync",
            "active": "Syncing approved micro-blog content/media into the production Compose content volume without rebuilding or restarting the runtime.",
            "timeout": 600,
        },
        {
            "name": "import-public-filesystem-content",
            "kind": "micro-blog-public-filesystem-sync-api",
            "active": "Calling the production blog-api filesystem sync endpoint on localhost so the copied Markdown/assets become public content.",
            "timeout": 300,
        },
        {
            "name": "validate-public-compose-services",
            "kind": "micro-blog-public-compose-proof",
            "active": "Validating production Compose services and public lane URLs after content promotion.",
            "timeout": 240,
        },
        {
            "name": "record-article-publish-fragment",
            "kind": "micro-blog-public-fragment-note",
            "active": "Recording the content-only public promotion receipt and guardrail.",
            "timeout": 60,
        },
    ],
    "complete_message": "Micro-blog public content promotion completed without a runtime redeploy.",
}
WORKFLOW_DEFINITIONS["baremetal-lab-reset"] = {
    "supports_undeploy": False,
    "settings_optional": True,
    "stage_plan": [
        {
            "name": "load-reset-scope",
            "transport": "internal",
            "kind": "folder-pipeline-review",
            "pipeline_id": "baremetal-lab-reset",
            "active": "Reviewing physical lab reset scope.",
            "complete": "Physical lab reset scope reviewed.",
            "timeout": 15,
        },
        {
            "name": "archive-current-evidence",
            "transport": "internal",
            "kind": "folder-pipeline-review",
            "pipeline_id": "baremetal-lab-reset",
            "active": "Reviewing physical lab evidence archive plan.",
            "complete": "Physical lab evidence archive plan reviewed.",
            "timeout": 30,
        },
        {
            "name": "plan-pxe-state-clear",
            "transport": "internal",
            "kind": "folder-pipeline-review",
            "pipeline_id": "baremetal-lab-reset",
            "active": "Reviewing physical lab PXE state cleanup plan.",
            "complete": "Physical lab PXE state cleanup plan reviewed.",
            "timeout": 30,
        },
        {
            "name": "plan-unattended-profile-restore",
            "transport": "internal",
            "kind": "folder-pipeline-review",
            "pipeline_id": "baremetal-lab-reset",
            "active": "Reviewing unattended PXE profile restore plan.",
            "complete": "Unattended PXE profile restore plan reviewed.",
            "timeout": 30,
        },
        {
            "name": "plan-power-reset",
            "transport": "internal",
            "kind": "baremetal-bmc-power-reset",
            "pipeline_id": "baremetal-lab-reset",
            "active": "Evaluating guarded physical lab BMC power reset.",
            "complete": "Physical lab BMC power reset gate evaluated.",
            "timeout": 60,
        },
        {
            "name": "plan-disk-wipe",
            "transport": "internal",
            "kind": "folder-pipeline-review",
            "pipeline_id": "baremetal-lab-reset",
            "active": "Reviewing physical lab disk wipe gate.",
            "complete": "Physical lab disk wipe gate reviewed.",
            "timeout": 60,
        },
        {
            "name": "plan-rerun-sequence",
            "transport": "internal",
            "kind": "folder-pipeline-review",
            "pipeline_id": "baremetal-lab-reset",
            "active": "Reviewing physical lab rerun sequence.",
            "complete": "Physical lab rerun sequence reviewed.",
            "timeout": 30,
        },
        {
            "name": "verify-rerun-boundary",
            "transport": "internal",
            "kind": "folder-pipeline-review",
            "pipeline_id": "baremetal-lab-reset",
            "active": "Reviewing physical lab rerun boundary.",
            "complete": "Physical lab rerun boundary reviewed.",
            "timeout": 30,
        },
    ],
    "complete_message": "Bare metal lab reset review completed.",
}
WORKFLOW_DEFINITIONS["ns1-trixie-pxe-smoke"] = {
    "supports_undeploy": False,
    "settings_optional": True,
    "stage_plan": [
        {
            "name": "resolve-provisioning-context",
            "transport": "internal",
            "kind": "folder-pipeline-review",
            "pipeline_id": "ns1-trixie-pxe-smoke",
            "active": "Reviewing Trixie provisioning context.",
            "complete": "Trixie provisioning context reviewed.",
            "timeout": 15,
        },
        {
            "name": "verify-ns1-pxe-prereqs",
            "transport": "internal",
            "kind": "trixie-pxe-prereqs",
            "pipeline_id": "ns1-trixie-pxe-smoke",
            "active": "Validating ns1 PXE prerequisite checks.",
            "complete": "ns1 PXE prerequisite checks passed.",
            "timeout": 45,
        },
        {
            "name": "fetch-trixie-netboot",
            "transport": "internal",
            "kind": "trixie-netboot-fetch",
            "pipeline_id": "ns1-trixie-pxe-smoke",
            "active": "Caching Debian Trixie netboot assets on ns1.",
            "complete": "Debian Trixie netboot assets cached.",
            "timeout": 300,
        },
        {
            "name": "render-ipxe-entry",
            "transport": "internal",
            "kind": "trixie-ipxe-render",
            "pipeline_id": "ns1-trixie-pxe-smoke",
            "active": "Rendering Trixie iPXE entry onto ns1.",
            "complete": "Trixie iPXE entry rendered.",
            "timeout": 15,
        },
        {
            "name": "render-preseed-profile",
            "transport": "internal",
            "kind": "trixie-preseed-render",
            "pipeline_id": "ns1-trixie-pxe-smoke",
            "active": "Rendering Trixie preseed profile onto ns1.",
            "complete": "Trixie preseed profile rendered.",
            "timeout": 15,
        },
        {
            "name": "prepare-vm132-pxe-target",
            "transport": "internal",
            "kind": "trixie-vm-prepare",
            "pipeline_id": "ns1-trixie-pxe-smoke",
            "active": "Recreating VMID 132 as a two-NIC PXE target.",
            "complete": "VMID 132 PXE target prepared.",
            "timeout": 300,
        },
        {
            "name": "pxe-boot-vm132",
            "transport": "internal",
            "kind": "trixie-vm-boot",
            "pipeline_id": "ns1-trixie-pxe-smoke",
            "active": "Starting VMID 132 from PXE boot order.",
            "complete": "VMID 132 PXE boot requested.",
            "timeout": 120,
        },
        {
            "name": "observe-installer-handoff",
            "transport": "internal",
            "kind": "trixie-vm-observe",
            "pipeline_id": "ns1-trixie-pxe-smoke",
            "active": "Observing VMID 132 Proxmox state after PXE boot.",
            "complete": "VMID 132 Proxmox state observed.",
            "timeout": 180,
        },
        {
            "name": "post-boot-recollect",
            "transport": "internal",
            "kind": "event-note",
            "pipeline_id": "ns1-trixie-pxe-smoke",
            "active": "Recording post-boot recollection handoff.",
            "complete": "Post-boot recollection handoff recorded.",
            "message": "Post-boot facter recollection remains the next phase after Debian finishes installing and management SSH is reachable.",
            "timeout": 120,
        },
        {
            "name": "record-trixie-relationships",
            "transport": "internal",
            "kind": "folder-pipeline-review",
            "pipeline_id": "ns1-trixie-pxe-smoke",
            "active": "Reviewing Trixie PXE relationship recording.",
            "complete": "Trixie PXE relationship recording reviewed.",
            "timeout": 15,
        },
    ],
    "complete_message": "ns1 Trixie PXE smoke review completed.",
}
WORKFLOW_DEFINITIONS["windows10-reference-discover"] = {
    "supports_undeploy": False,
    "settings_optional": True,
    "stage_plan": [
        {
            "name": "verify-iso",
            "transport": "internal",
            "kind": "windows10-verify-iso",
            "pipeline_id": "windows10-reference-discover",
            "active": "Hashing the Windows 10 ISO source.",
            "complete": "Windows 10 ISO source verified.",
            "timeout": 120,
        },
        {
            "name": "inspect-vm113",
            "transport": "internal",
            "kind": "windows10-inspect-vm",
            "pipeline_id": "windows10-reference-discover",
            "active": "Inspecting VMID 113 in Proxmox.",
            "complete": "VMID 113 inspected.",
            "timeout": 60,
        },
        {
            "name": "validate-openssh",
            "transport": "internal",
            "kind": "windows10-validate-openssh",
            "pipeline_id": "windows10-reference-discover",
            "active": "Validating Windows OpenSSH key access.",
            "complete": "Windows OpenSSH key access validated.",
            "timeout": 60,
        },
        {
            "name": "stage-firstboot-artifacts",
            "transport": "internal",
            "kind": "windows10-stage-artifacts",
            "pipeline_id": "windows10-reference-discover",
            "active": "Checking Windows firstboot artifact files.",
            "complete": "Windows firstboot artifacts checked.",
            "timeout": 15,
        },
        {
            "name": "record-windows-relationships",
            "transport": "internal",
            "kind": "folder-pipeline-review",
            "pipeline_id": "windows10-reference-discover",
            "active": "Reviewing Windows reference relationships.",
            "complete": "Windows reference relationships reviewed.",
            "timeout": 15,
        },
    ],
    "complete_message": "Windows 10 reference discovery completed.",
}
WORKFLOW_DEFINITIONS["windows10-pxe-smoke"] = {
    "supports_undeploy": False,
    "settings_optional": True,
    "stage_plan": [
        {
            "name": "resolve-windows-pxe-context",
            "transport": "internal",
            "kind": "folder-pipeline-review",
            "pipeline_id": "windows10-pxe-smoke",
            "active": "Reviewing Windows PXE context.",
            "complete": "Windows PXE context reviewed.",
            "timeout": 15,
        },
        {
            "name": "verify-ns1-pxe-prereqs",
            "transport": "internal",
            "kind": "windows10-pxe-prereqs",
            "pipeline_id": "windows10-pxe-smoke",
            "active": "Validating ns1 PXE prerequisite checks for Windows.",
            "complete": "ns1 PXE prerequisite checks passed.",
            "timeout": 45,
        },
        {
            "name": "verify-windows-iso",
            "transport": "internal",
            "kind": "windows10-pxe-verify-iso",
            "pipeline_id": "windows10-pxe-smoke",
            "active": "Verifying Windows ISO in Proxmox storage.",
            "complete": "Windows ISO verified.",
            "timeout": 60,
        },
        {
            "name": "fetch-wimboot",
            "transport": "internal",
            "kind": "windows10-wimboot-fetch",
            "pipeline_id": "windows10-pxe-smoke",
            "active": "Caching wimboot on ns1.",
            "complete": "wimboot cached on ns1.",
            "timeout": 120,
        },
        {
            "name": "stage-windows-install-media",
            "transport": "internal",
            "kind": "windows10-winpe-stage",
            "pipeline_id": "windows10-pxe-smoke",
            "active": "Extracting Windows install media from the Proxmox ISO.",
            "complete": "Windows install media staged on ns1.",
            "timeout": 1800,
        },
        {
            "name": "render-windows-ipxe",
            "transport": "internal",
            "kind": "windows10-ipxe-render",
            "pipeline_id": "windows10-pxe-smoke",
            "active": "Rendering Windows iPXE entry onto ns1.",
            "complete": "Windows iPXE entry rendered.",
            "timeout": 15,
        },
        {
            "name": "configure-windows-media-share",
            "transport": "internal",
            "kind": "windows10-media-share",
            "pipeline_id": "windows10-pxe-smoke",
            "active": "Configuring read-only SMB share for Windows install media.",
            "complete": "Windows install media SMB share configured.",
            "timeout": 120,
        },
        {
            "name": "render-unattend-firstboot",
            "transport": "internal",
            "kind": "windows10-unattend-render",
            "pipeline_id": "windows10-pxe-smoke",
            "active": "Rendering Windows unattended and firstboot assets.",
            "complete": "Windows unattended and firstboot assets rendered.",
            "timeout": 30,
        },
        {
            "name": "render-dhcp-windows-route",
            "transport": "internal",
            "kind": "windows10-dhcp-route-render",
            "pipeline_id": "windows10-pxe-smoke",
            "active": "Rendering fixed-MAC DHCP route for Windows PXE.",
            "complete": "Windows DHCP route rendered.",
            "timeout": 60,
        },
        {
            "name": "prepare-vm136-pxe-target",
            "transport": "internal",
            "kind": "windows10-vm-prepare",
            "pipeline_id": "windows10-pxe-smoke",
            "active": "Recreating VMID 136 as a Windows PXE target.",
            "complete": "VMID 136 Windows PXE target prepared.",
            "timeout": 300,
        },
        {
            "name": "pxe-boot-vm136",
            "transport": "internal",
            "kind": "windows10-vm-boot",
            "pipeline_id": "windows10-pxe-smoke",
            "active": "Starting VMID 136 from PXE boot order.",
            "complete": "VMID 136 PXE boot requested.",
            "timeout": 120,
        },
        {
            "name": "observe-winpe-handoff",
            "transport": "internal",
            "kind": "windows10-vm-observe",
            "pipeline_id": "windows10-pxe-smoke",
            "active": "Observing VMID 136 Proxmox state after PXE boot.",
            "complete": "VMID 136 Proxmox state observed.",
            "timeout": 180,
        },
        {
            "name": "post-install-ssh-check",
            "transport": "internal",
            "kind": "event-note",
            "pipeline_id": "windows10-pxe-smoke",
            "active": "Recording Windows post-install SSH handoff.",
            "complete": "Windows post-install SSH handoff recorded.",
            "message": "Windows setup and firstboot bootstrap must complete before SSH-driven personalization.",
            "timeout": 120,
        },
        {
            "name": "record-windows-pxe-relationships",
            "transport": "internal",
            "kind": "folder-pipeline-review",
            "pipeline_id": "windows10-pxe-smoke",
            "active": "Reviewing Windows PXE relationship recording.",
            "complete": "Windows PXE relationship recording reviewed.",
            "timeout": 15,
        },
    ],
    "complete_message": "Windows 10 PXE smoke completed.",
}
WORKFLOW_DEFINITIONS["trixie-workstation-personalize"] = {
    "supports_undeploy": False,
    "settings_optional": True,
    "stage_plan": [
        {
            "name": "discover-installed-trixie",
            "transport": "internal",
            "kind": "trixie-personalize-discover",
            "pipeline_id": "trixie-workstation-personalize",
            "active": "Verifying installed Trixie guest reachability through qemu-guest-agent.",
            "complete": "Installed Trixie guest is reachable.",
            "timeout": 60,
        },
        {
            "name": "normalize-local-login",
            "transport": "internal",
            "kind": "trixie-personalize-login",
            "pipeline_id": "trixie-workstation-personalize",
            "active": "Normalizing the Trixie local workstation login.",
            "complete": "Trixie local workstation login normalized.",
            "timeout": 60,
        },
        {
            "name": "publish-demo-checkpoints",
            "transport": "internal",
            "kind": "trixie-personalize-checkpoints",
            "pipeline_id": "trixie-workstation-personalize",
            "active": "Publishing FooBar demo checkpoint links on the Trixie desktop.",
            "complete": "FooBar demo checkpoint links published on Trixie.",
            "timeout": 180,
        },
        {
            "name": "install-workstation-packages",
            "transport": "internal",
            "kind": "trixie-personalize-packages",
            "pipeline_id": "trixie-workstation-personalize",
            "active": "Installing Trixie workstation package profile.",
            "complete": "Trixie workstation packages installed.",
            "timeout": 3600,
        },
        {
            "name": "install-vscode-if-enabled",
            "transport": "internal",
            "kind": "trixie-personalize-vscode",
            "pipeline_id": "trixie-workstation-personalize",
            "active": "Installing VS Code on Trixie if enabled.",
            "complete": "VS Code stage completed.",
            "timeout": 900,
        },
        {
            "name": "install-rustdesk-if-configured",
            "transport": "internal",
            "kind": "trixie-personalize-rustdesk",
            "pipeline_id": "trixie-workstation-personalize",
            "active": "Installing RustDesk on Trixie if configured.",
            "complete": "RustDesk stage completed.",
            "timeout": 900,
        },
        {
            "name": "enable-graphical-services",
            "transport": "internal",
            "kind": "trixie-personalize-services",
            "pipeline_id": "trixie-workstation-personalize",
            "active": "Enabling Trixie graphical and remoting services.",
            "complete": "Trixie graphical and remoting services enabled.",
            "timeout": 120,
        },
        {
            "name": "verify-trixie-personality",
            "transport": "internal",
            "kind": "trixie-personalize-verify",
            "pipeline_id": "trixie-workstation-personalize",
            "active": "Verifying Trixie workstation personality.",
            "complete": "Trixie workstation personality verified.",
            "timeout": 120,
        },
        {
            "name": "record-trixie-personality",
            "transport": "internal",
            "kind": "folder-pipeline-review",
            "pipeline_id": "trixie-workstation-personalize",
            "active": "Reviewing Trixie workstation relationship recording.",
            "complete": "Trixie workstation relationship recording reviewed.",
            "timeout": 15,
        },
    ],
    "complete_message": "Trixie workstation personalization completed.",
}
WORKFLOW_DEFINITIONS["windows10-workstation-personalize"] = {
    "supports_undeploy": False,
    "settings_optional": True,
    "stage_plan": [
        {
            "name": "discover-installed-windows",
            "transport": "internal",
            "kind": "windows10-personalize-discover",
            "pipeline_id": "windows10-workstation-personalize",
            "active": "Verifying installed Windows guest SSH reachability.",
            "complete": "Installed Windows guest is reachable.",
            "timeout": 120,
        },
        {
            "name": "normalize-local-login",
            "transport": "internal",
            "kind": "windows10-personalize-login",
            "pipeline_id": "windows10-workstation-personalize",
            "active": "Normalizing the Windows local workstation login.",
            "complete": "Windows local workstation login normalized.",
            "timeout": 60,
        },
        {
            "name": "publish-demo-checkpoints",
            "transport": "internal",
            "kind": "windows10-personalize-checkpoints",
            "pipeline_id": "windows10-workstation-personalize",
            "active": "Publishing FooBar demo checkpoint links on the Windows desktop.",
            "complete": "FooBar demo checkpoint links published on Windows.",
            "timeout": 180,
        },
        {
            "name": "ensure-chocolatey",
            "transport": "internal",
            "kind": "windows10-personalize-chocolatey",
            "pipeline_id": "windows10-workstation-personalize",
            "active": "Installing Chocolatey if missing.",
            "complete": "Chocolatey is available.",
            "timeout": 600,
        },
        {
            "name": "install-workstation-packages",
            "transport": "internal",
            "kind": "windows10-personalize-packages",
            "pipeline_id": "windows10-workstation-personalize",
            "active": "Installing Windows workstation package profile.",
            "complete": "Windows workstation packages installed.",
            "timeout": 1800,
        },
        {
            "name": "verify-windows-personality",
            "transport": "internal",
            "kind": "windows10-personalize-verify",
            "pipeline_id": "windows10-workstation-personalize",
            "active": "Verifying Windows workstation personality.",
            "complete": "Windows workstation personality verified.",
            "timeout": 120,
        },
        {
            "name": "record-windows-personality",
            "transport": "internal",
            "kind": "folder-pipeline-review",
            "pipeline_id": "windows10-workstation-personalize",
            "active": "Reviewing Windows workstation relationship recording.",
            "complete": "Windows workstation relationship recording reviewed.",
            "timeout": 15,
        },
    ],
    "complete_message": "Windows 10 workstation personalization completed.",
}
WORKFLOW_DEFINITIONS["windows10-winpe-builder"] = {
    "supports_undeploy": False,
    "settings_optional": True,
    "stage_plan": [
        {
            "name": "resolve-builder-context",
            "transport": "internal",
            "kind": "folder-pipeline-review",
            "pipeline_id": "windows10-winpe-builder",
            "active": "Reviewing Windows WinPE builder context.",
            "complete": "Windows WinPE builder context reviewed.",
            "timeout": 15,
        },
        {
            "name": "inspect-vm113",
            "transport": "internal",
            "kind": "windows10-builder-inspect-vm",
            "pipeline_id": "windows10-winpe-builder",
            "active": "Inspecting VMID 113 in Proxmox.",
            "complete": "VMID 113 inspected.",
            "timeout": 60,
        },
        {
            "name": "validate-builder-ssh",
            "transport": "internal",
            "kind": "windows10-builder-ssh",
            "pipeline_id": "windows10-winpe-builder",
            "active": "Validating Windows builder OpenSSH access.",
            "complete": "Windows builder OpenSSH access validated.",
            "timeout": 60,
        },
        {
            "name": "inspect-adk-tooling",
            "transport": "internal",
            "kind": "windows10-adk-inspect",
            "pipeline_id": "windows10-winpe-builder",
            "active": "Inspecting Windows ADK and WinPE tooling.",
            "complete": "Windows ADK tooling inspected.",
            "timeout": 60,
        },
        {
            "name": "stage-builder-scripts",
            "transport": "internal",
            "kind": "windows10-builder-stage-scripts",
            "pipeline_id": "windows10-winpe-builder",
            "active": "Staging WinPE builder scripts on VMID 113.",
            "complete": "WinPE builder scripts staged.",
            "timeout": 60,
        },
        {
            "name": "install-adk-if-enabled",
            "transport": "internal",
            "kind": "windows10-adk-install",
            "pipeline_id": "windows10-winpe-builder",
            "active": "Checking ADK install gate.",
            "complete": "ADK install gate checked.",
            "timeout": 900,
        },
        {
            "name": "build-winpe-if-enabled",
            "transport": "internal",
            "kind": "windows10-winpe-build",
            "pipeline_id": "windows10-winpe-builder",
            "active": "Checking WinPE build gate.",
            "complete": "WinPE build gate checked.",
            "timeout": 900,
        },
        {
            "name": "publish-winpe-if-enabled",
            "transport": "internal",
            "kind": "windows10-winpe-publish",
            "pipeline_id": "windows10-winpe-builder",
            "active": "Checking WinPE publish gate.",
            "complete": "WinPE publish gate checked.",
            "timeout": 600,
        },
        {
            "name": "record-winpe-builder-relationships",
            "transport": "internal",
            "kind": "folder-pipeline-review",
            "pipeline_id": "windows10-winpe-builder",
            "active": "Reviewing WinPE builder relationship recording.",
            "complete": "WinPE builder relationships reviewed.",
            "timeout": 15,
        },
    ],
    "complete_message": "Windows 10 WinPE builder preparation completed.",
}

WORKFLOW_DEFINITIONS["small-office-foobar-reference"] = {
    "supports_undeploy": False,
    "settings_optional": True,
    "stage_plan": [
        {
            "name": "load-recipe-intent",
            "transport": "internal",
            "kind": "folder-pipeline-review",
            "pipeline_id": "small-office-foobar-reference",
            "active": "Reviewing foo.bar recipe intent.",
            "complete": "FooBar recipe intent reviewed.",
            "timeout": 15,
        },
        {
            "name": "plan-identity-storage",
            "transport": "internal",
            "kind": "folder-pipeline-review",
            "pipeline_id": "small-office-foobar-reference",
            "active": "Reviewing identity and shared-home service plan.",
            "complete": "Identity and shared-home plan reviewed.",
            "timeout": 30,
        },
        {
            "name": "plan-crm-intranet",
            "transport": "internal",
            "kind": "folder-pipeline-review",
            "pipeline_id": "small-office-foobar-reference",
            "active": "Reviewing CRM/intranet service plan.",
            "complete": "CRM/intranet service plan reviewed.",
            "timeout": 30,
        },
        {
            "name": "provision-linux-developer-workstations",
            "transport": "internal",
            "kind": "folder-pipeline-review",
            "pipeline_id": "small-office-foobar-reference",
            "active": "Reviewing Linux developer workstation PXE plan.",
            "complete": "Linux developer workstation PXE plan reviewed.",
            "timeout": 60,
        },
        {
            "name": "provision-windows-helpdesk-workstations",
            "transport": "internal",
            "kind": "folder-pipeline-review",
            "pipeline_id": "small-office-foobar-reference",
            "active": "Reviewing Windows helpdesk workstation PXE plan.",
            "complete": "Windows helpdesk workstation PXE plan reviewed.",
            "timeout": 60,
        },
        {
            "name": "personalize-workstations",
            "transport": "internal",
            "kind": "folder-pipeline-review",
            "pipeline_id": "small-office-foobar-reference",
            "active": "Reviewing workstation personalization plan.",
            "complete": "Workstation personalization plan reviewed.",
            "timeout": 60,
        },
        {
            "name": "validate-small-office",
            "transport": "internal",
            "kind": "folder-pipeline-review",
            "pipeline_id": "small-office-foobar-reference",
            "active": "Reviewing small-office validation evidence plan.",
            "complete": "Small-office validation evidence plan reviewed.",
            "timeout": 120,
        },
        {
            "name": "render-demo-lifecycle",
            "transport": "internal",
            "kind": "folder-pipeline-review",
            "pipeline_id": "small-office-foobar-reference",
            "active": "Reviewing Grafana/Mermaid lifecycle render plan.",
            "complete": "Grafana/Mermaid lifecycle render plan reviewed.",
            "timeout": 15,
        },
    ],
    "complete_message": "Small Office FooBar reference review completed.",
}

WORKFLOW_DEFINITIONS["small-office-foobar-reset"] = {
    "supports_undeploy": False,
    "settings_optional": True,
    "stage_plan": [
        {
            "name": "load-reset-scope",
            "transport": "internal",
            "kind": "folder-pipeline-review",
            "pipeline_id": "small-office-foobar-reset",
            "active": "Reviewing foo.bar reset scope.",
            "complete": "FooBar reset scope reviewed.",
            "timeout": 15,
        },
        {
            "name": "plan-demo-vm-removal",
            "transport": "internal",
            "kind": "folder-pipeline-review",
            "pipeline_id": "small-office-foobar-reset",
            "active": "Reviewing demo VM removal plan.",
            "complete": "Demo VM removal plan reviewed.",
            "timeout": 30,
        },
        {
            "name": "plan-pxe-route-cleanup",
            "transport": "internal",
            "kind": "folder-pipeline-review",
            "pipeline_id": "small-office-foobar-reset",
            "active": "Reviewing PXE route cleanup plan.",
            "complete": "PXE route cleanup plan reviewed.",
            "timeout": 30,
        },
        {
            "name": "plan-evidence-archive",
            "transport": "internal",
            "kind": "folder-pipeline-review",
            "pipeline_id": "small-office-foobar-reset",
            "active": "Reviewing validation evidence archive plan.",
            "complete": "Validation evidence archive plan reviewed.",
            "timeout": 30,
        },
        {
            "name": "verify-reset-boundary",
            "transport": "internal",
            "kind": "folder-pipeline-review",
            "pipeline_id": "small-office-foobar-reset",
            "active": "Reviewing reset boundary validation.",
            "complete": "Reset boundary validation reviewed.",
            "timeout": 30,
        },
    ],
    "complete_message": "Small Office FooBar reset review completed.",
}

WORKFLOW_DEFINITIONS["small-office-foobar-app-vms"] = {
    "supports_undeploy": False,
    "settings_optional": True,
    "stage_plan": [
        {
            "name": "load-app-vm-plan",
            "transport": "internal",
            "kind": "folder-pipeline-review",
            "pipeline_id": "small-office-foobar-app-vms",
            "active": "Reviewing foo.bar application VM plan.",
            "complete": "FooBar application VM plan reviewed.",
            "timeout": 15,
        },
        {
            "name": "select-trixie-source",
            "transport": "bkc-proxmox",
            "kind": "foobar-app-source-select",
            "pipeline_id": "small-office-foobar-app-vms",
            "active": "Selecting the prepared Trixie source VM.",
            "complete": "Prepared Trixie source VM selected.",
            "timeout": 60,
        },
        {
            "name": "clone-app-vms",
            "transport": "bkc-proxmox",
            "kind": "foobar-app-vm-clone",
            "pipeline_id": "small-office-foobar-app-vms",
            "active": "Cloning SuiteCRM and Kanboard VM shells from Trixie.",
            "complete": "SuiteCRM and Kanboard VM shells cloned.",
            "timeout": 2400,
        },
        {
            "name": "boot-app-vms",
            "transport": "bkc-proxmox",
            "kind": "foobar-app-vm-boot",
            "pipeline_id": "small-office-foobar-app-vms",
            "active": "Starting SuiteCRM and Kanboard VM shells.",
            "complete": "SuiteCRM and Kanboard VM shells are running.",
            "timeout": 300,
        },
        {
            "name": "record-app-relationships",
            "transport": "internal",
            "kind": "foobar-app-relationships",
            "pipeline_id": "small-office-foobar-app-vms",
            "active": "Recording foo.bar application VM relationships.",
            "complete": "FooBar application VM relationships recorded.",
            "timeout": 30,
        },
    ],
    "complete_message": "Small Office FooBar application VM pipeline completed.",
}

WORKFLOW_DEFINITIONS["small-office-foobar-services"] = {
    "supports_undeploy": False,
    "settings_optional": True,
    "stage_plan": [
        {
            "name": "load-service-plan",
            "transport": "internal",
            "kind": "folder-pipeline-review",
            "pipeline_id": "small-office-foobar-services",
            "active": "Reviewing foo.bar service provisioning plan.",
            "complete": "FooBar service provisioning plan reviewed.",
            "timeout": 15,
        },
        {
            "name": "ensure-identity-vm",
            "transport": "bkc-proxmox",
            "kind": "foobar-service-identity-vm",
            "pipeline_id": "small-office-foobar-services",
            "active": "Ensuring the foo.bar identity VM exists.",
            "complete": "FooBar identity VM is ready to boot.",
            "timeout": 2400,
        },
        {
            "name": "wait-service-guests",
            "transport": "bkc-proxmox",
            "kind": "foobar-service-guest-wait",
            "pipeline_id": "small-office-foobar-services",
            "active": "Waiting for foo.bar service guests to accept BKC guest commands.",
            "complete": "FooBar service guests are command-ready.",
            "timeout": 600,
        },
        {
            "name": "configure-demo-lan",
            "transport": "bkc-proxmox",
            "kind": "foobar-service-demo-lan",
            "pipeline_id": "small-office-foobar-services",
            "active": "Configuring browser-reachable foo.bar service interfaces.",
            "complete": "FooBar demo LAN service interfaces configured.",
            "timeout": 300,
        },
        {
            "name": "install-identity-packages",
            "transport": "bkc-proxmox",
            "kind": "foobar-service-identity-packages",
            "pipeline_id": "small-office-foobar-services",
            "active": "Installing foo.bar LDAP, Samba, and web packages.",
            "complete": "FooBar identity packages installed.",
            "timeout": 2400,
        },
        {
            "name": "seed-ldap-directory",
            "transport": "bkc-proxmox",
            "kind": "foobar-service-ldap-seed",
            "pipeline_id": "small-office-foobar-services",
            "active": "Applying foo.bar LDAP base, groups, and users.",
            "complete": "FooBar LDAP directory seeded.",
            "timeout": 300,
        },
        {
            "name": "configure-samba-homes",
            "transport": "bkc-proxmox",
            "kind": "foobar-service-samba-homes",
            "pipeline_id": "small-office-foobar-services",
            "active": "Configuring foo.bar Samba shared homes.",
            "complete": "FooBar Samba shared homes configured.",
            "timeout": 300,
        },
        {
            "name": "publish-identity-portal",
            "transport": "bkc-proxmox",
            "kind": "foobar-service-identity-portal",
            "pipeline_id": "small-office-foobar-services",
            "active": "Publishing foo.bar identity portal placeholder.",
            "complete": "FooBar identity portal published.",
            "timeout": 180,
        },
        {
            "name": "provision-suitecrm-service",
            "transport": "bkc-proxmox",
            "kind": "foobar-service-suitecrm-provision",
            "pipeline_id": "small-office-foobar-services",
            "active": "Installing the foo.bar SuiteCRM service.",
            "complete": "FooBar SuiteCRM service installed.",
            "timeout": 1800,
        },
        {
            "name": "provision-kanboard-service",
            "transport": "bkc-proxmox",
            "kind": "foobar-service-kanboard-provision",
            "pipeline_id": "small-office-foobar-services",
            "active": "Installing the foo.bar Kanboard ticket service.",
            "complete": "FooBar Kanboard ticket service installed.",
            "timeout": 1800,
        },
        {
            "name": "validate-foobar-services",
            "transport": "bkc-proxmox",
            "kind": "foobar-service-validate",
            "pipeline_id": "small-office-foobar-services",
            "active": "Validating foo.bar identity, CRM, and ticket services.",
            "complete": "FooBar services validated.",
            "timeout": 300,
        },
        {
            "name": "record-service-relationships",
            "transport": "internal",
            "kind": "foobar-service-relationships",
            "pipeline_id": "small-office-foobar-services",
            "active": "Recording foo.bar service relationships.",
            "complete": "FooBar service relationships recorded.",
            "timeout": 30,
        },
    ],
    "complete_message": "Small Office FooBar service provisioning pipeline completed.",
}

WORKFLOW_DEFINITIONS["auzix-package-repo-stripped-iso"] = {
    "supports_undeploy": False,
    "settings_optional": True,
    "stage_plan": [
        {
            "name": "validate-package-intents",
            "transport": "local",
            "kind": "local-command",
            "cwd": "/workspace/AuziX",
            "active": "Validating AUZiX package intent JSON before repository work.",
            "complete": "AUZiX package intent JSON is syntactically valid.",
            "timeout": 120,
            "command": "python3 -m json.tool packages/extended-ports.manifest.json >/dev/null && python3 -m json.tool packages/oci-and-python.queue.json >/dev/null && python3 -m json.tool packages/flatpak-desktop.queue.json >/dev/null && python3 -m json.tool packages/desktop-control-and-userapps.queue.json >/dev/null && python3 -m json.tool packages/desktop-userapps.sources.json >/dev/null && python3 -m json.tool packages/auzix-control-panel.intent.json >/dev/null",
        },
        {
            "name": "build-package-repository",
            "transport": "local",
            "kind": "local-command",
            "cwd": "/workspace/AuziX",
            "active": "Building Flatpak first-pass packages, then the AUZiX package repository from strict-root receipts.",
            "complete": "AUZiX package repository was built from strict-root receipts.",
            "timeout": 1800,
            "command": "mkdir -p out/package-repo-stripped-iso && { chmod +x scripts/build-auzix-abiword-package.sh scripts/build-auzix-gnumeric-package.sh; ./scripts/run-auzix-package-bot.sh packages/desktop-control-and-userapps.queue.json native-dev-and-debug-tools out/auzix-strict/AuzixRoot packages/desktop-userapps.sources.json; ./scripts/run-auzix-package-bot.sh packages/desktop-control-and-userapps.queue.json native-internet-and-creative-apps out/auzix-strict/AuzixRoot packages/desktop-userapps.sources.json; ./scripts/build-auzix-flatpak-runtime-slice.sh out/auzix-strict/AuzixRoot && ./scripts/build-auzix-package-repo.sh out/auzix-strict/AuzixRoot; } >out/package-repo-stripped-iso/package-repo-build.log 2>&1 && jq -r '\"packages=\" + ((.packages | length) | tostring)' artifacts/auzix/repo/index.json",
        },
        {
            "name": "strict-root-no-classic-dir-audit",
            "transport": "local",
            "kind": "local-command",
            "cwd": "/workspace/AuziX",
            "active": "Running strict-root audit with classic top-level directories treated as invalid.",
            "complete": "Strict-root audit report captured for Ollama review.",
            "timeout": 2400,
            "command": "mkdir -p out/package-repo-stripped-iso; AUZIX_LEGACY_POLICY=invalid ./scripts/audit-auzix-strict-root.sh out/auzix-strict/AuzixRoot out/package-repo-stripped-iso/strict-root-audit.txt >out/package-repo-stripped-iso/strict-root-audit.stdout 2>&1 || true; tail -n 40 out/package-repo-stripped-iso/strict-root-audit.txt",
        },
        {
            "name": "ollama-review-receipts",
            "transport": "local",
            "kind": "local-command",
            "cwd": "/workspace/AuziX",
            "active": "Asking Ollama to review package repository and strict-root receipts.",
            "complete": "Ollama receipt review completed or was recorded as unavailable.",
            "timeout": 420,
            "command": "python3 - <<'PY'\nimport json, pathlib, subprocess, urllib.request\nroot = pathlib.Path('.')\nout = root / 'out/package-repo-stripped-iso'\nout.mkdir(parents=True, exist_ok=True)\nindex = root / 'artifacts/auzix/repo/index.json'\naudit = out / 'strict-root-audit.txt'\nbuild_log = out / 'package-repo-build.log'\nintent_files = [\n    'packages/extended-ports.manifest.json',\n    'packages/oci-and-python.queue.json',\n    'packages/flatpak-desktop.queue.json',\n    'packages/desktop-control-and-userapps.queue.json',\n    'packages/auzix-control-panel.intent.json',\n    'packages/userspace-tools.queue.json',\n]\n\ndef cmd(args):\n    try:\n        return subprocess.check_output(args, text=True, stderr=subprocess.DEVNULL).strip()\n    except Exception:\n        return 'unknown'\n\ndef tail(path, n=80):\n    if not path.exists():\n        return []\n    return path.read_text(errors='replace').splitlines()[-n:]\n\ndef package_names_from_intent(path):\n    if not path.exists():\n        return []\n    try:\n        data = json.loads(path.read_text())\n    except Exception:\n        return []\n    names = []\n    for key in ('packages', 'items', 'intents'):\n        for item in data.get(key, []) if isinstance(data.get(key), list) else []:\n            if isinstance(item, dict):\n                name = item.get('name') or item.get('package') or item.get('id')\n                if name:\n                    names.append(str(name))\n    for batch in data.get('batches', []) if isinstance(data.get('batches'), list) else []:\n        for item in batch.get('packages', []) if isinstance(batch, dict) else []:\n            if isinstance(item, dict):\n                name = item.get('name') or item.get('package') or item.get('id')\n            else:\n                name = item\n            if name:\n                names.append(str(name))\n    return names[:80]\n\nsummary = []\nsummary.append('pipeline_id=auzix-package-repo-stripped-iso')\nsummary.append('goal=build/publish AUZiX package repo candidates, then validate install on disposable vmid135 before filming')\nsummary.append('input_policy=receipts-and-intents-only-no-secrets')\nsummary.append('git_branch=' + cmd(['git', 'branch', '--show-current']))\nsummary.append('git_commit=' + cmd(['git', 'rev-parse', '--short', 'HEAD']))\nsummary.append('git_dirty_count=' + cmd(['sh', '-lc', 'git status --porcelain 2>/dev/null | wc -l | tr -d \" \"']))\nsummary.append('receipt_paths=artifacts/auzix/repo/index.json,out/package-repo-stripped-iso/package-repo-build.log,out/package-repo-stripped-iso/strict-root-audit.txt,out/package-repo-stripped-iso/ollama-review.md')\nsummary.append('related_intent_files=' + ', '.join(path for path in intent_files if (root / path).exists()))\nfor path_text in intent_files:\n    names = package_names_from_intent(root / path_text)\n    if names:\n        summary.append(f'intent_packages[{path_text}]=' + ', '.join(names[:60]))\nif index.exists():\n    data = json.loads(index.read_text())\n    summary.append(f\"repo_package_count={len(data.get('packages', []))}\")\n    summary.append('repo_packages=' + ', '.join(pkg.get('name', '') for pkg in data.get('packages', [])[:80]))\nif audit.exists():\n    lines = audit.read_text(errors='replace').splitlines()\n    summary.append('strict_root_audit_selected_lines=')\n    summary.extend([line for line in lines if line.startswith(('FAIL:', 'WARN:', 'PASS: no undeclared', 'PASS:', 'Identity baseline', 'Runtime network'))][:120])\nif build_log.exists():\n    summary.append('package_build_log_tail=')\n    summary.extend(tail(build_log, 80))\nsummary.append('next_gate=install selected packages on vmid135; if stable, consider stripped installer ISO reroll; keep classic paths only as declared break-fix debt')\nprompt = 'Review this AUZiX package pipeline context. Return: 1) film-ready summary, 2) real blockers, 3) likely missing package/dependency candidates, 4) smallest next VM135 install validation steps. Do not ask for secrets.\\n\\n' + '\\n'.join(summary)\nreport = out / 'ollama-review.md'\ntry:\n    req = urllib.request.Request('http://10.20.0.130:11434/api/generate', data=json.dumps({'model':'qwen2.5-coder:1.5b','prompt':prompt,'stream':False}).encode(), headers={'Content-Type':'application/json'})\n    with urllib.request.urlopen(req, timeout=300) as resp:\n        payload = json.loads(resp.read().decode())\n    text = payload.get('response') or json.dumps(payload, indent=2)\nexcept Exception as exc:\n    text = f'Ollama review unavailable: {exc}\\n\\nReceipt summary retained locally.\\n\\n' + '\\n'.join(summary[:220])\nreport.write_text(text + '\\n')\n(out / 'ollama-review-input.txt').write_text(prompt + '\\n')\nprint(text[-4000:])\nPY",
        },
        {
            "name": "record-proof-handoff",
            "transport": "local",
            "kind": "local-command",
            "cwd": "/workspace/AuziX",
            "active": "Recording proof handoff paths for the next stripped ISO pass.",
            "complete": "AUZiX package repository and strict-root proof receipts are ready for review.",
            "timeout": 120,
            "command": "python3 - <<'PY'\nimport json, pathlib, time\nout = pathlib.Path('out/package-repo-stripped-iso')\nout.mkdir(parents=True, exist_ok=True)\npaths = ['artifacts/auzix/repo/index.json','out/package-repo-stripped-iso/package-repo-build.log','out/package-repo-stripped-iso/strict-root-audit.txt','out/package-repo-stripped-iso/ollama-review.md']\nreceipt = {'format':'auzix-package-repo-stripped-iso-proof-v1','created':time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),'paths':paths,'next_gate':'remove or quarantine classic top-level compatibility links before stripped ISO rebuild'}\n(out / 'proof-handoff.json').write_text(json.dumps(receipt, indent=2) + '\\n')\nprint(json.dumps(receipt, indent=2))\nPY",
        },
    ],
    "complete_message": "AUZiX package repo deploy + stripped ISO proof pipeline completed.",
}


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
    if normalized == "auzix-r730-build":
        return (
            AUZIX_R730_BUILD_HOST,
            AUZIX_R730_BUILD_USER,
            "",
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
    output = "\n".join(
        [
            kubectl_text(["get", "nodes", "-o", "wide"], timeout=120),
            kubectl_text(["wait", "--for=condition=Ready", "nodes", "--all", "--timeout=90s"], timeout=120),
        ]
    )
    _set_stage(run_id, stage_name, "complete", "K3s node readiness verified.")
    append_event(run_id, "info", stage_name, (output[-1600:] if output else "k3s-ready") + "\ntransport=kubernetes-api")


def _ensure_rx_demo_runtime_secrets() -> str:
    namespace = kubectl_text(["get", "namespace", "rx-demo"], timeout=60, check=False)
    outputs = []
    if "not found" in namespace.lower():
        outputs.append(kubectl_text(["create", "namespace", "rx-demo"], timeout=60))
    sa_password = "AuzixDemo9!" + secrets.token_urlsafe(18)
    rabbit_password = secrets.token_urlsafe(24)
    secret_yaml = kubectl_text(
        [
            "-n",
            "rx-demo",
            "create",
            "secret",
            "generic",
            "rx-demo-secrets",
            f"--from-literal=SA_PASSWORD={sa_password}",
            "--from-literal=RABBITMQ_DEFAULT_USER=rx_demo",
            f"--from-literal=RABBITMQ_DEFAULT_PASS={rabbit_password}",
            "--dry-run=client",
            "-o",
            "yaml",
        ],
        timeout=60,
    )
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".yaml", delete=True) as handle:
        handle.write(secret_yaml)
        handle.flush()
        outputs.append(kubectl_text(["apply", "-f", handle.name], timeout=60))
    outputs.append(kubectl_text(["-n", "rx-demo", "get", "secret", "rx-demo-secrets", "-o", "name"], timeout=60))
    return "\n".join(outputs)


def _run_rx_demo_k3s_secrets(run_id: str, stage_name: str) -> None:
    output = _ensure_rx_demo_runtime_secrets()
    _set_stage(run_id, stage_name, "complete", "Rx-demo runtime secrets are present.")
    append_event(run_id, "info", stage_name, (output[-1200:] if output else "rx-demo-secrets-present") + "\ntransport=kubernetes-api")


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
    deployments = ["api-gateway", "legacy-sync-worker", "loadgen", "otel-collector", "rabbitmq", "read-model-projection", "redis", "rx-ui"]
    outputs = [kubectl_text(["-n", "rx-demo", "get", "pods", "-o", "wide"], timeout=120, check=False)]
    for deploy in deployments:
        outputs.append(kubectl_text(["-n", "rx-demo", "rollout", "restart", f"deploy/{deploy}"], timeout=120))
    for deploy in deployments:
        outputs.append(kubectl_text(["-n", "rx-demo", "rollout", "status", f"deploy/{deploy}", "--timeout=300s"], timeout=360))
    outputs.append(kubectl_text(["-n", "rx-demo", "rollout", "status", "statefulset/mssql", "--timeout=300s"], timeout=360))
    outputs.append(kubectl_text(["-n", "rx-demo", "get", "pods", "-o", "wide"], timeout=120))
    outputs.append(kubectl_text(["-n", "rx-demo", "get", "events", "--sort-by=.lastTimestamp"], timeout=120, check=False))
    output = "\n".join(item for item in outputs if item)
    _set_stage(run_id, stage_name, "complete", "Rx-demo application workloads are ready.")
    append_event(run_id, "info", stage_name, (output[-3000:] if output else "rx-demo-rollout-ready") + "\ntransport=kubernetes-api")


def _run_rx_demo_k3s_network_ready(run_id: str, stage_name: str) -> None:
    _set_stage(run_id, stage_name, "active", "Verifying k3s pod networking and firewalld allowances.")
    outputs = [
        kubectl_text(["get", "nodes", "-o", "wide"], timeout=120),
        kubectl_text(["wait", "--for=condition=Ready", "nodes", "--all", "--timeout=90s"], timeout=120),
        kubectl_text(["-n", "rx-demo", "get", "endpoints", "rabbitmq", "otel-collector", "api-gateway", "rx-ui", "-o", "wide"], timeout=120),
        kubectl_text(["-n", "rx-demo", "get", "networkpolicy", "-o", "wide"], timeout=120, check=False),
    ]
    _set_stage(run_id, stage_name, "complete", "K3s pod networking prerequisites are ready.")
    append_event(run_id, "info", stage_name, ("\n".join(outputs))[-3600:] + "\ntransport=kubernetes-api")


def _run_rx_demo_k3s_rollout_observability(run_id: str, stage_name: str) -> None:
    deployments = ["grafana", "loki", "prometheus", "tempo"]
    outputs = [kubectl_text(["-n", "rx-observability", "get", "pods", "-o", "wide"], timeout=120, check=False)]
    for deploy in deployments:
        outputs.append(kubectl_text(["-n", "rx-observability", "rollout", "status", f"deploy/{deploy}", "--timeout=300s"], timeout=360))
    outputs.append(kubectl_text(["-n", "rx-observability", "get", "pods", "-o", "wide"], timeout=120))
    outputs.append(kubectl_text(["-n", "rx-observability", "get", "events", "--sort-by=.lastTimestamp"], timeout=120, check=False))
    output = "\n".join(item for item in outputs if item)
    _set_stage(run_id, stage_name, "complete", "Rx-demo observability workloads are ready.")
    append_event(run_id, "info", stage_name, (output[-3000:] if output else "rx-demo-observability-ready") + "\ntransport=kubernetes-api")


def _run_rx_demo_k3s_apply_observability(run_id: str, stage_name: str) -> None:
    context = _rx_demo_redeploy_run_context(run_id)
    ref = context["ref"]
    commit = context["commit"]
    github_ref = commit or ref.removeprefix("refs/heads/") or "main"
    api_url = (
        "https://api.github.com/repos/auzieman/rx-demo/contents/"
        "k8s/observability/grafana/rx-traffic-map-grafmaid-dashboard.json?"
        f"ref={urllib.parse.quote(github_ref, safe='')}"
    )
    with urllib.request.urlopen(api_url, timeout=30) as response:  # noqa: S310 - public GitHub content
        payload = json.loads(response.read().decode("utf-8"))
    content = b64decode(str(payload.get("content") or "").encode("ascii")).decode("utf-8")
    json.loads(content)

    outputs = [f"dashboard-source-ref={github_ref}"]
    with tempfile.TemporaryDirectory(prefix="rx-observability-dashboard-") as tmp:
        dashboard_path = Path(tmp) / "rx-traffic-map-grafmaid-dashboard.json"
        dashboard_path.write_text(content, encoding="utf-8")
        configmap_yaml = kubectl_text(
            [
                "-n",
                "rx-observability",
                "create",
                "configmap",
                "rx-traffic-map-grafmaid-dashboard",
                f"--from-file=rx-traffic-map-grafmaid-dashboard.json={dashboard_path}",
                "--dry-run=client",
                "-o",
                "yaml",
            ],
            timeout=60,
        )
        configmap_path = Path(tmp) / "rx-traffic-map-grafmaid-dashboard.configmap.yaml"
        configmap_path.write_text(configmap_yaml, encoding="utf-8")
        outputs.append(kubectl_text(["apply", "-f", str(configmap_path)], timeout=60))
    outputs.append(kubectl_text(["-n", "rx-observability", "rollout", "restart", "deploy/grafana"], timeout=120))
    outputs.append(kubectl_text(["-n", "rx-observability", "rollout", "status", "deploy/grafana", "--timeout=240s"], timeout=300))
    outputs.append(kubectl_text(["-n", "rx-observability", "get", "configmap", "rx-traffic-map-grafmaid-dashboard", "-o", "name"], timeout=60))
    outputs.append(kubectl_text(["-n", "rx-observability", "get", "pods", "-l", "app=grafana", "-o", "wide"], timeout=120))
    output = "\n".join(item for item in outputs if item)
    _set_stage(run_id, stage_name, "complete", "Rx-demo observability manifests and dashboards applied.")
    append_event(run_id, "info", stage_name, (output[-2400:] if output else "rx-demo-observability-applied") + "\ntransport=kubernetes-api")


def _run_rx_demo_k3s_smoke_api_full(run_id: str, stage_name: str) -> None:
    base = _k8s_nodeport_base("rx-demo", "api-gateway", "http")
    rx_id = "RX-BKC-K3S-API"
    _http_text(f"{base}/healthz")
    _http_text(f"{base}/readyz")
    _retry_http_contains(f"{base}/prescriptions/{rx_id}", rx_id, attempts=30, delay=3)
    _retry_http_contains(
        f"{base}/prescriptions/{rx_id}/approve",
        "ApproveQueued",
        method="POST",
        payload={"approvedBy": "bkc.pipeline", "notes": "BKC k3s API smoke"},
        attempts=30,
        delay=3,
    )
    _retry_http_contains(
        f"{base}/prescriptions/{rx_id}/refill",
        "RefillQueued",
        method="POST",
        payload={"refillCount": 1},
        attempts=30,
        delay=3,
    )
    output = f"rx-api-nodeport-ok {base}\ntransport=kubernetes-api"
    _set_stage(run_id, stage_name, "complete", "Rx-demo API smoke checks passed.")
    append_event(run_id, "info", stage_name, output[-1200:] if output else "rx-api-nodeport-ok")


def _run_rx_demo_k3s_smoke_ui_full(run_id: str, stage_name: str) -> None:
    base = _k8s_nodeport_base("rx-demo", "rx-ui", "http")
    rx_id = "RX-BKC-K3S-UI"
    _retry_http_contains(f"{base}/", "Prescription Demo UI", attempts=30, delay=3)
    _retry_http_contains(f"{base}/lookup", '"operation":"lookup"', method="POST", payload={"rxId": rx_id}, attempts=30, delay=3)
    _retry_http_contains(
        f"{base}/approve",
        '"operation":"approve"',
        method="POST",
        payload={"rxId": rx_id, "approvedBy": "bkc.pipeline", "notes": "BKC k3s demo smoke"},
        attempts=30,
        delay=3,
    )
    _retry_http_contains(
        f"{base}/refill",
        '"operation":"refill"',
        method="POST",
        payload={"rxId": rx_id, "refillCount": 1},
        attempts=30,
        delay=3,
    )
    output = f"rx-ui-nodeport-ok {base}\ntransport=kubernetes-api"
    _set_stage(run_id, stage_name, "complete", "Rx-demo UI smoke checks passed.")
    append_event(run_id, "info", stage_name, output[-1200:] if output else "rx-ui-nodeport-ok")


def _run_rx_demo_k3s_telemetry_check(run_id: str, stage_name: str) -> None:
    otel_base = _k8s_nodeport_base("rx-demo", "otel-collector", "prom-metrics")
    prom_base = _k8s_nodeport_base("rx-observability", "prometheus", "http")
    grafana_base = _k8s_nodeport_base("rx-observability", "grafana", "http")
    metrics = _http_text(f"{otel_base}/metrics", timeout=10)
    if not any(line.startswith(("rx_", "otelcol_")) for line in metrics.splitlines()):
        raise PipelineExecutionError("OTel metrics endpoint did not expose rx_ or otelcol_ metrics.")
    _http_text(f"{prom_base}/-/ready", timeout=10)
    grafana_health = _http_text(f"{grafana_base}/api/health", timeout=10)
    if '"database"' not in grafana_health:
        raise PipelineExecutionError(f"Grafana health response did not include database status: {grafana_health}")
    kubectl_text(
        ["-n", "rx-observability", "exec", "deploy/grafana", "--", "test", "-d", "/var/lib/grafana/plugins/neildengg-grafmaid-panel"],
        timeout=120,
    )
    output = "\n".join(
        [
            f"rx-telemetry-nodeports-ok grafana={grafana_base} prometheus={prom_base} otel={otel_base}",
            "grafana-plugin-ok plugin=neildengg-grafmaid-panel",
            "transport=kubernetes-api",
        ]
    )
    _set_stage(run_id, stage_name, "complete", "Rx-demo telemetry endpoints responded.")
    append_event(run_id, "info", stage_name, output[-1600:] if output else "rx-telemetry-nodeports-ok")


def _run_rx_demo_k3s_access_links(run_id: str, stage_name: str) -> None:
    run = get_run(run_id) or {}
    extra = run.get("extra") if isinstance(run.get("extra"), dict) else {}
    image_tag = str(extra.get("rx_demo_redeploy_tag") or RX_DEMO_K3S_DEMO_TAG).strip()
    links = {
        "image_tag": image_tag,
        "rx_ui": _k8s_nodeport_base("rx-demo", "rx-ui", "http"),
        "rx_api": _k8s_nodeport_base("rx-demo", "api-gateway", "http"),
        "otel_metrics": f"{_k8s_nodeport_base('rx-demo', 'otel-collector', 'prom-metrics')}/metrics",
        "grafana": _k8s_nodeport_base("rx-observability", "grafana", "http"),
        "prometheus": _k8s_nodeport_base("rx-observability", "prometheus", "http"),
        "loki": _k8s_nodeport_base("rx-observability", "loki", "http"),
        "tempo": _k8s_nodeport_base("rx-observability", "tempo", "http"),
    }
    _store_run_extra(run_id, {"rx_demo_k3s_links": links})
    _set_stage(run_id, stage_name, "complete", "Demo access links published.")
    append_event(run_id, "info", stage_name, json.dumps({**links, "transport": "kubernetes-api"}, sort_keys=True))


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
    git_work = RX_DEMO_REDEPLOY_SOURCE
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
            f"git_work={shlex.quote(git_work)}",
            "if id auzieman >/dev/null 2>&1 && test -e \"$git_work\"; then",
            "  chown -R auzieman:auzieman \"$git_work\"",
            "fi",
            "if ! test -d \"$git_work/.git\"; then",
            "  rm -rf \"$git_work\"",
            "  $git_cmd clone https://github.com/auzieman/rx-demo.git \"$git_work\"",
            "fi",
            "cd \"$git_work\"",
            "if test -n \"$($git_cmd status --porcelain)\"; then",
            "  $git_cmd status --short",
            "  $git_cmd reset --hard",
            "  $git_cmd clean -fdx",
            "fi",
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
            "test -f rx-demo.sln",
            "test -x tools/build-and-push.sh",
            "printf '%s\\n' \"$tag\" > .bkc-source-tag",
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
    _set_stage(run_id, stage_name, "complete", "Local rx-demo redeploy source is on the requested Git revision.")
    append_event(run_id, "info", stage_name, output[-1600:] if output else f"rx-demo-git-ready tag={tag}")


def _run_rx_demo_k3s_redeploy_build_push(run_id: str, stage_name: str, settings: dict[str, str]) -> None:
    script = "\n".join(
        [
            "set -euo pipefail",
            f"cd {shlex.quote(RX_DEMO_REDEPLOY_SOURCE)}",
            "tag=\"$(cat .bkc-source-tag)\"",
            "test -n \"$tag\"",
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


def _run_rx_demo_k3s_publish_source_to_shared(run_id: str, stage_name: str, settings: dict[str, str]) -> None:
    _set_stage(run_id, stage_name, "complete", "Shared source publish skipped; k3s will apply observability directly from Git.")
    append_event(run_id, "info", stage_name, "rx-demo-observability-source-direct-git\ntransport=internal")


def _rx_demo_redeploy_tag(run_id: str) -> str:
    run = get_run(run_id) or {}
    extra = run.get("extra") if isinstance(run.get("extra"), dict) else {}
    tag = str(extra.get("rx_demo_redeploy_tag") or "").strip()
    if not re.fullmatch(r"[0-9a-f]{7,12}", tag):
        raise PipelineExecutionError("Unable to determine rx-demo redeploy image tag from run state.")
    return tag


def _k8s_jsonpath(args: list[str], expression: str, *, timeout: int = 60) -> str:
    return kubectl_text([*args, "-o", f"jsonpath={expression}"], timeout=timeout).strip()


def _k8s_node_ip_for_endpoint(namespace: str, service: str) -> str:
    node_name = _k8s_jsonpath(
        ["-n", namespace, "get", "endpoints", service],
        "{.subsets[0].addresses[0].nodeName}",
    )
    if not node_name:
        raise PipelineExecutionError(f"No endpoint node found for {namespace}/{service}.")
    node_ip = _k8s_jsonpath(
        ["get", "node", node_name],
        '{.status.addresses[?(@.type=="InternalIP")].address}',
    )
    if not node_ip:
        raise PipelineExecutionError(f"No InternalIP found for Kubernetes node {node_name}.")
    return node_ip


def _k8s_nodeport(namespace: str, service: str, port_name: str) -> str:
    port = _k8s_jsonpath(
        ["-n", namespace, "get", "svc", service],
        f'{{.spec.ports[?(@.name=="{port_name}")].nodePort}}',
    )
    if not port:
        raise PipelineExecutionError(f"No NodePort named {port_name} found for {namespace}/{service}.")
    return port


def _k8s_nodeport_base(namespace: str, service: str, port_name: str) -> str:
    return f"http://{_k8s_node_ip_for_endpoint(namespace, service)}:{_k8s_nodeport(namespace, service, port_name)}"


def _http_text(url: str, *, method: str = "GET", payload: dict | None = None, timeout: int = 10) -> str:
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - lab endpoint validation
            return response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise PipelineExecutionError(f"HTTP {exc.code} from {url}: {body}") from exc
    except urllib.error.URLError as exc:
        raise PipelineExecutionError(f"HTTP request failed for {url}: {exc}") from exc


def _retry_http_contains(url: str, text: str, *, method: str = "GET", payload: dict | None = None, attempts: int = 30, delay: int = 3) -> str:
    last = ""
    for _ in range(attempts):
        try:
            last = _http_text(url, method=method, payload=payload)
            if text in last:
                return last
        except PipelineExecutionError as exc:
            last = str(exc)
        time.sleep(delay)
    if text not in last:
        raise PipelineExecutionError(f"Expected {text!r} from {url}. Last response: {last[-800:]}")
    return last


def _ensure_rx_demo_overlay_present_for_redeploy() -> str:
    namespace = kubectl_text(["get", "namespace", "rx-demo"], timeout=60, check=False)
    api_deploy = kubectl_text(["-n", "rx-demo", "get", "deploy/api-gateway"], timeout=60, check=False)
    if "not found" not in namespace.lower() and "not found" not in api_deploy.lower():
        return "rx-demo-overlay-present"

    secret_output = _ensure_rx_demo_runtime_secrets()
    server = _k3s_live_node("server")
    script = "\n".join(
        [
            "set -euo pipefail",
            f"test -d {shlex.quote(RX_DEMO_SHARED_SOURCE)}",
            f"cd {shlex.quote(RX_DEMO_SHARED_SOURCE)}",
            "test -d k8s/overlays/k3s-demo",
            "k3s kubectl apply -k k8s/overlays/k3s-demo",
            "k3s kubectl -n rx-demo get deploy api-gateway rx-ui legacy-sync-worker read-model-projection -o wide",
            "k3s kubectl -n rx-observability get deploy grafana loki prometheus tempo -o wide",
        ]
    )
    output = run_remote_command(
        host=server["host"],
        user="root",
        command=f"bash -lc {shlex.quote(script)}",
        timeout=300,
    )
    return "\n".join(
        [
            "rx-demo-overlay-created-for-redeploy",
            secret_output[-800:] if secret_output else "rx-demo-secrets-present",
            output[-1800:] if output else "k3s-demo-overlay-applied",
        ]
    )


def _run_rx_demo_k3s_redeploy_update_images(run_id: str, stage_name: str) -> None:
    tag = _rx_demo_redeploy_tag(run_id)
    registry = f"{DEMO_REGISTRY}/rx-demo"
    outputs = [
        _ensure_rx_demo_overlay_present_for_redeploy(),
        kubectl_text(["-n", "rx-demo", "set", "image", "deploy/api-gateway", f"api-gateway={registry}/api-gateway:{tag}"], timeout=240),
        kubectl_text(["-n", "rx-demo", "set", "image", "deploy/rx-ui", f"rx-ui={registry}/rx-ui:{tag}"], timeout=240),
        kubectl_text(
            ["-n", "rx-demo", "set", "image", "deploy/legacy-sync-worker", f"worker={registry}/legacy-sync-worker:{tag}"],
            timeout=240,
        ),
        kubectl_text(
            ["-n", "rx-demo", "set", "image", "deploy/read-model-projection", f"worker={registry}/read-model-projection:{tag}"],
            timeout=240,
        ),
    ]
    loadgen = kubectl_text(["-n", "rx-demo", "get", "deploy/loadgen"], timeout=60, check=False)
    if "not found" not in loadgen.lower():
        outputs.append(kubectl_text(["-n", "rx-demo", "set", "image", "deploy/loadgen", f"loadgen={registry}/loadgen:{tag}"], timeout=240))
    outputs.append(
        kubectl_text(
            ["-n", "rx-demo", "get", "deploy", "-o", "custom-columns=NAME:.metadata.name,IMAGE:.spec.template.spec.containers[0].image"],
            timeout=120,
        )
    )
    output = "\n".join(outputs)
    _set_stage(run_id, stage_name, "complete", "K3s deployments reference the commit-tagged images.")
    append_event(run_id, "info", stage_name, (output[-2000:] if output else "rx-demo-images-updated") + "\ntransport=kubernetes-api")


def _run_rx_demo_k3s_cloudinit_node_check(run_id: str, stage_name: str) -> None:
    output = "\n".join(
        [
            kubectl_text(["get", "nodes", "-o", "wide"], timeout=120),
            kubectl_text(
                [
                    "get",
                    "nodes",
                    "-o",
                    "custom-columns=NAME:.metadata.name,INTERNAL_IP:.status.addresses[?(@.type==\"InternalIP\")].address,OS:.status.nodeInfo.osImage,KERNEL:.status.nodeInfo.kernelVersion,KUBELET:.status.nodeInfo.kubeletVersion,RUNTIME:.status.nodeInfo.containerRuntimeVersion",
                ],
                timeout=120,
            ),
            kubectl_text(["get", "nodes", "--show-labels"], timeout=120),
        ]
    )
    _set_stage(run_id, stage_name, "complete", "K3s node and cloud-init evidence captured.")
    append_event(run_id, "info", stage_name, (output[-3000:] if output else "k3s-node-evidence") + "\ntransport=kubernetes-api")


def _run_rx_demo_k3s_loki_cloudevents_check(run_id: str, stage_name: str) -> None:
    api_base = _k8s_nodeport_base("rx-demo", "api-gateway", "http")
    loki_base = _k8s_nodeport_base("rx-observability", "loki", "http")
    rx_id = "RX-BKC-CLOUDEVENTS"
    _retry_http_contains(
        f"{api_base}/prescriptions/{rx_id}/approve",
        "ApproveQueued",
        method="POST",
        payload={"approvedBy": "bkc.pipeline", "notes": "CloudEvents Loki demo"},
        attempts=10,
    )
    _retry_http_contains(
        f"{api_base}/prescriptions/{rx_id}/refill",
        "RefillQueued",
        method="POST",
        payload={"refillCount": 2},
        attempts=10,
    )
    query = '{service_name=~"rx/.+"} |= "CloudEvent audit" |= "RX-BKC-CLOUDEVENTS"'
    body = ""
    for _ in range(30):
        params = urllib.parse.urlencode(
            {
                "query": query,
                "limit": "20",
                "start": f"{int(time.time() - 900)}000000000",
                "end": f"{int(time.time())}000000000",
            }
        )
        body = _http_text(f"{loki_base}/loki/api/v1/query_range?{params}", timeout=10)
        if "CloudEvent audit" in body and rx_id in body:
            break
        time.sleep(5)
    if "CloudEvent audit" not in body or rx_id not in body:
        raise PipelineExecutionError(f"Loki did not return CloudEvents audit records for {rx_id}: {body[-1200:]}")
    output = f"loki-cloudevents-ok api={api_base} loki={loki_base} query={query}\ntransport=kubernetes-api"
    _set_stage(run_id, stage_name, "complete", "Loki returned CloudEvents audit records.")
    append_event(run_id, "info", stage_name, output[-2400:] if output else "loki-cloudevents-ok")


def _run_rx_demo_k3s_redeploy_visible_activity(run_id: str, stage_name: str) -> None:
    ui_base = _k8s_nodeport_base("rx-demo", "rx-ui", "http")
    api_base = _k8s_nodeport_base("rx-demo", "api-gateway", "http")
    rx_id = "RX-BKC-REDEPLOY"
    kubectl_text(["-n", "rx-demo", "rollout", "restart", "deployment", "rabbitmq"], timeout=120)
    kubectl_text(["-n", "rx-demo", "rollout", "status", "deployment", "rabbitmq", "--timeout=180s"], timeout=240)
    _retry_http_contains(f"{ui_base}/", "Prescription Demo UI", attempts=30, delay=3)
    _http_text(f"{api_base}/healthz")
    _retry_http_contains(f"{api_base}/readyz", '"rabbitmq":"ok"', attempts=40, delay=3)
    _retry_http_contains(f"{api_base}/prescriptions/{rx_id}", rx_id, attempts=40, delay=3)
    _retry_http_contains(
        f"{api_base}/prescriptions/{rx_id}/approve",
        "ApproveQueued",
        method="POST",
        payload={"approvedBy": "bkc.pipeline", "notes": "BKC redeploy visible activity"},
        attempts=40,
        delay=3,
    )
    _retry_http_contains(
        f"{api_base}/prescriptions/{rx_id}/refill",
        "RefillQueued",
        method="POST",
        payload={"refillCount": 1},
        attempts=40,
        delay=3,
    )
    output = f"rx-redeploy-visible-activity-ok ui={ui_base} api={api_base} rx_id={rx_id}\ntransport=kubernetes-api"
    _set_stage(run_id, stage_name, "complete", "Post-redeploy UI/API smoke activity completed.")
    append_event(run_id, "info", stage_name, output[-1600:] if output else "rx-redeploy-visible-activity-ok")


def _run_rx_demo_k3s_grafana_loki_check(run_id: str, stage_name: str) -> None:
    grafana_base = _k8s_nodeport_base("rx-observability", "grafana", "http")
    loki_base = _k8s_nodeport_base("rx-observability", "loki", "http")
    grafana_health = _http_text(f"{grafana_base}/api/health")
    if '"database"' not in grafana_health:
        raise PipelineExecutionError(f"Grafana health response did not include database status: {grafana_health}")
    loki_ready = _http_text(f"{loki_base}/ready")
    plugin_check = kubectl_text(
        ["-n", "rx-observability", "exec", "deploy/grafana", "--", "test", "-d", "/var/lib/grafana/plugins/neildengg-grafmaid-panel"],
        timeout=120,
    )
    output = "\n".join(
        [
            loki_ready,
            plugin_check,
            "grafana-plugin-ok plugin=neildengg-grafmaid-panel",
            f"grafana-loki-ready grafana={grafana_base} loki={loki_base} explore_query={{service_name=~\"rx/.+\"}} |= \"CloudEvent audit\"",
            "transport=kubernetes-api",
        ]
    )
    _set_stage(run_id, stage_name, "complete", "Grafana is reachable and the Loki query is ready for the demo.")
    append_event(run_id, "info", stage_name, output[-1200:] if output else "grafana-loki-ready")


def _run_rx_demo_k3s_undeploy_capture(run_id: str, stage_name: str) -> None:
    output = "\n\n".join(
        [
            kubectl_text(["-n", "rx-demo", "get", "all,pvc,secret,configmap", "-o", "wide"], timeout=120, check=False),
            kubectl_text(
                ["-n", "rx-observability", "get", "deploy,svc,configmap,daemonset", "-o", "wide"],
                timeout=120,
                check=False,
            ),
        ]
    )
    _set_stage(run_id, stage_name, "complete", "Pre-cleanup k3s state captured.")
    append_event(run_id, "info", stage_name, (output[-3000:] if output else "pre-cleanup-state-captured") + "\ntransport=kubernetes-api")


def _run_rx_demo_k3s_undeploy_demo_observability(run_id: str, stage_name: str) -> None:
    configmaps = [
        "prometheus-k3s-config",
        "loki-config",
        "tempo-config",
        "grafana-datasources",
        "grafana-dashboard-provider",
        "rx-overview-dashboard",
        "rx-service-flow-dashboard",
        "rx-executive-health-dashboard",
        "rx-executive-flow-grafmaid-dashboard",
        "rx-grafmaid-probe-dashboard",
        "rx-traffic-map-grafmaid-dashboard",
        "rx-tempo-traces-dashboard",
    ]
    outputs = [
        kubectl_text(
            ["-n", "rx-observability", "delete", "deploy", "grafana", "loki", "prometheus", "tempo", "--ignore-not-found=true"],
            timeout=300,
        ),
        kubectl_text(
            ["-n", "rx-observability", "delete", "svc", "grafana", "loki", "prometheus", "tempo", "--ignore-not-found=true"],
            timeout=120,
        ),
        kubectl_text(["-n", "rx-observability", "delete", "configmap", *configmaps, "--ignore-not-found=true"], timeout=120),
        kubectl_text(["-n", "rx-observability", "delete", "serviceaccount", "prometheus", "--ignore-not-found=true"], timeout=120),
        kubectl_text(["delete", "clusterrole", "rx-demo-prometheus-discovery", "--ignore-not-found=true"], timeout=120),
        kubectl_text(["delete", "clusterrolebinding", "rx-demo-prometheus-discovery", "--ignore-not-found=true"], timeout=120),
        kubectl_text(["-n", "rx-observability", "get", "daemonset,svc", "-o", "wide"], timeout=120, check=False),
    ]
    output = "\n".join(item for item in outputs if item)
    _set_stage(run_id, stage_name, "complete", "Demo observability resources removed.")
    append_event(run_id, "info", stage_name, (output[-3000:] if output else "demo-observability-removed") + "\ntransport=kubernetes-api")


def _run_rx_demo_k3s_undeploy_namespace(run_id: str, stage_name: str) -> None:
    output = kubectl_text(["delete", "namespace", "rx-demo", "--ignore-not-found=true", "--timeout=240s"], timeout=300)
    wait_output = kubectl_text(
        ["wait", "--for=delete", "namespace/rx-demo", "--timeout=240s"],
        timeout=300,
        check=False,
    )
    if "not found" not in wait_output.lower() and "deleted" not in wait_output.lower():
        namespace_check = kubectl_text(["get", "namespace", "rx-demo"], timeout=60, check=False)
        if "not found" not in namespace_check.lower():
            raise PipelineExecutionError(namespace_check or wait_output or "rx-demo namespace still exists")
    output = "\n".join(item for item in (output, wait_output, "rx-demo-namespace-absent") if item)
    _set_stage(run_id, stage_name, "complete", "Rx-demo namespace removed.")
    append_event(run_id, "info", stage_name, output[-1600:] + "\ntransport=kubernetes-api")


def _run_rx_demo_k3s_undeploy_verify(run_id: str, stage_name: str) -> None:
    namespace_check = kubectl_text(["get", "namespace", "rx-demo"], timeout=60, check=False)
    if "not found" not in namespace_check.lower():
        raise PipelineExecutionError(namespace_check or "rx-demo namespace still exists")
    for deploy in ("grafana", "loki", "prometheus", "tempo"):
        deploy_check = kubectl_text(["-n", "rx-observability", "get", "deploy", deploy], timeout=60, check=False)
        if "not found" not in deploy_check.lower():
            raise PipelineExecutionError(f"demo observability deployment still exists: {deploy}\n{deploy_check}")
    daemonsets = kubectl_text(
        ["-n", "rx-observability", "get", "daemonset", "telegraf-k3s-host", "cadvisor-k3s", "-o", "name"],
        timeout=120,
    )
    output = "\n".join([namespace_check, daemonsets, "rx-demo-cleanup-verified", "transport=kubernetes-api"])
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


def _run_folder_pipeline_review_stage(run_id: str, stage: dict) -> None:
    stage_name = str(stage["name"])
    pipeline_id = str(stage.get("pipeline_id") or "").strip()
    pipeline = pipeline_by_id(pipeline_id)
    if not pipeline:
        raise PipelineExecutionError(f"Pipeline '{pipeline_id}' is not available in the catalog.")

    resolved = resolve_pipeline_dictionary(pipeline, run_inputs=_run_request_inputs(run_id))
    missing = list(resolved.get("missing", []))
    if missing:
        raise PipelineExecutionError(f"Pipeline '{pipeline_id}' is missing required dictionary values: {', '.join(missing)}")

    stage_spec = next(
        (
            item
            for item in pipeline.get("stages", [])
            if str(item.get("id") or item.get("name") or "").strip() == stage_name
        ),
        {},
    )
    item_spec = next(
        (
            item
            for item in pipeline.get("items", [])
            if str(item.get("id") or item.get("name") or "").strip() == stage_name
        ),
        {},
    )
    evidence = {
        "pipeline_id": pipeline_id,
        "pipeline_name": pipeline.get("name"),
        "source_type": pipeline.get("source_type"),
        "source_layers": [layer.get("source_type") for layer in pipeline.get("source_layers", [])],
        "stage": {
            "id": stage_spec.get("id") or stage_name,
            "action": stage_spec.get("action") or item_spec.get("action"),
            "transport": stage_spec.get("transport"),
            "risk": stage_spec.get("risk") or item_spec.get("risk"),
            "with": stage_spec.get("with", {}),
            "produces": stage_spec.get("produces", []),
        },
        "dictionary": {
            "keys": sorted(str(key) for key in resolved.get("values", {}).keys()),
            "target_host": resolved.get("values", {}).get("target_host"),
            "provisioning_interface": resolved.get("values", {}).get("provisioning_interface"),
            "enable_dhcp_service": resolved.get("values", {}).get("enable_dhcp_service"),
            "dhcp_range_start": resolved.get("values", {}).get("dhcp_range_start"),
            "dhcp_range_end": resolved.get("values", {}).get("dhcp_range_end"),
            "boot_image_name": resolved.get("values", {}).get("boot_image_name"),
            "netboot_root": resolved.get("values", {}).get("netboot_root"),
            "target_vmid": resolved.get("values", {}).get("target_vmid"),
            "target_vm_name": resolved.get("values", {}).get("target_vm_name"),
            "target_vm_boot_nic": resolved.get("values", {}).get("target_vm_boot_nic"),
            "target_vm_boot_bridge": resolved.get("values", {}).get("target_vm_boot_bridge"),
            "target_vm_management_nic": resolved.get("values", {}).get("target_vm_management_nic"),
            "target_vm_management_bridge": resolved.get("values", {}).get("target_vm_management_bridge"),
            "enable_vm_create": resolved.get("values", {}).get("enable_vm_create"),
            "enable_pxe_boot": resolved.get("values", {}).get("enable_pxe_boot"),
            "enable_install": resolved.get("values", {}).get("enable_install"),
        },
        "mode": "review-only",
    }
    append_event(run_id, "info", stage_name, json.dumps(evidence, sort_keys=True))
    _set_stage(run_id, stage_name, "complete", str(stage.get("complete", "Folder pipeline review stage completed.")))


def _folder_pipeline_context(pipeline_id: str, run_inputs: dict | None = None) -> tuple[dict, dict, dict]:
    pipeline = pipeline_by_id(pipeline_id)
    if not pipeline:
        raise PipelineExecutionError(f"Pipeline '{pipeline_id}' is not available in the catalog.")
    resolved = resolve_pipeline_dictionary(pipeline, run_inputs=run_inputs)
    missing = list(resolved.get("missing", []))
    if missing:
        raise PipelineExecutionError(f"Pipeline '{pipeline_id}' is missing required dictionary values: {', '.join(missing)}")
    return pipeline, resolved, dict(resolved.get("values", {}))


def _repo_pipeline_folder(pipeline: dict) -> Path:
    for layer in pipeline.get("source_layers", []):
        if str(layer.get("source_type", "")).strip() == "repo-folder":
            folder = Path(str(layer.get("source_folder") or "")).resolve()
            if folder.exists():
                return folder
    source_path = Path(str(pipeline.get("source_path") or "")).resolve()
    return source_path.parent if source_path.exists() else Path.cwd()


def _template_value(values: dict, key: str) -> object:
    current: object = values
    for part in key.split("."):
        if isinstance(current, dict):
            current = current.get(part, "")
        else:
            return ""
    return current


def _render_pipeline_template(template_text: str, values: dict) -> str:
    def replace(match: re.Match[str]) -> str:
        _scope = match.group(1)
        key = match.group(2)
        value = _template_value(values, key)
        if isinstance(value, (dict, list)):
            return json.dumps(value, sort_keys=True)
        return str(value)

    return re.sub(r"\$\{(dictionary|inputs)\.([A-Za-z0-9_.]+)\}", replace, template_text)


def _run_ns1_command(values: dict, command: str, *, timeout: int = 120) -> str:
    return run_remote_command(
        host=str(values.get("target_host") or "").strip(),
        user="root",
        command=command,
        timeout=timeout,
    )


def _bmc_discovery_context() -> tuple[dict, dict, dict]:
    return _folder_pipeline_context("baremetal-bmc-discovery-prepare")


def _run_bmc_observer_command(values: dict, command: str, *, timeout: int = 120) -> str:
    return run_remote_command(
        host=str(values.get("observer_host") or "").strip(),
        user="root",
        command=command,
        timeout=timeout,
    )


def _run_bmc_discovery_neighbors(run_id: str, stage_name: str) -> None:
    _, _, values = _bmc_discovery_context()
    interface = shlex.quote(str(values.get("observer_interface") or "ens18"))
    candidates = values.get("candidate_bmcs") if isinstance(values.get("candidate_bmcs"), list) else []
    expected = []
    for item in candidates:
        if isinstance(item, dict):
            address = str(item.get("expected_address") or "").strip()
            mac = str(item.get("observed_mac") or "").strip().lower()
            if address:
                expected.append(address)
            if mac:
                expected.append(mac)
    pattern = "|".join(re.escape(item) for item in expected) or "a^"
    command = (
        "set -e; "
        f"echo interface={interface}; "
        f"ip -br addr show {interface}; "
        "printf '\\n-- neighbors --\\n'; "
        "ip neigh show nud all | sort; "
        "printf '\\n-- expected matches --\\n'; "
        f"ip neigh show nud all | sort | grep -Ei {shlex.quote(pattern)} || true"
    )
    output = _run_bmc_observer_command(values, command, timeout=30)
    _store_run_extra(run_id, {"bmc_neighbor_evidence": output})
    _set_stage(run_id, stage_name, "complete", "ns1 neighbor evidence collected for candidate iDRACs.")
    append_event(run_id, "info", stage_name, output[-3000:] if output else "no neighbor output")


def _run_bmc_discovery_redfish(run_id: str, stage_name: str) -> None:
    _, _, values = _bmc_discovery_context()
    path = str(values.get("redfish_path") or "/redfish/v1/").strip() or "/redfish/v1/"
    candidates = values.get("candidate_bmcs") if isinstance(values.get("candidate_bmcs"), list) else []
    if not candidates:
        raise PipelineExecutionError("candidate_bmcs is required for Redfish probing.")
    probes = []
    for item in candidates:
        if not isinstance(item, dict):
            continue
        address = str(item.get("expected_address") or "").strip()
        if not address:
            continue
        probes.append(
            "printf '%s ' "
            + shlex.quote(address)
            + "; curl -kfsS --connect-timeout 3 -o /dev/null -w '%{http_code}\\n' "
            + shlex.quote(f"https://{address}{path}")
            + " || true"
        )
    command = "set -e; " + "; ".join(probes)
    output = _run_bmc_observer_command(values, command, timeout=45)
    statuses = {}
    for line in output.splitlines():
        parts = line.split()
        if len(parts) >= 2:
            statuses[parts[0]] = parts[1]
    failures = {address: status for address, status in statuses.items() if status != "200"}
    if failures:
        raise PipelineExecutionError(f"Redfish root probe failed: {failures}")
    _store_run_extra(run_id, {"bmc_redfish_status": statuses})
    _set_stage(run_id, stage_name, "complete", "Candidate iDRAC Redfish roots responded with HTTP 200.")
    append_event(run_id, "info", stage_name, json.dumps(statuses, sort_keys=True))


def _ns1_lan_mac_pxe_context(run_inputs: dict | None = None) -> tuple[dict, dict, dict]:
    return _folder_pipeline_context("ns1-lan-mac-pxe-prepare", run_inputs)


def _run_ns1_lan_mac_validate(run_id: str, stage_name: str) -> None:
    _, _, values = _ns1_lan_mac_pxe_context(_run_request_inputs(run_id))
    interface = str(values.get("lan_interface") or "").strip()
    router = str(values.get("lan_router") or "").strip()
    if not interface or not router:
        raise PipelineExecutionError("LAN MAC PXE validation requires lan_interface and lan_router.")
    command = (
        "set -e; "
        f"ip -br addr show {shlex.quote(interface)}; "
        f"ip route get {shlex.quote(router)}"
    )
    output = _run_ns1_command(values, command, timeout=30)
    _set_stage(run_id, stage_name, "complete", "ns1 LAN interface is present for MAC-only PXE.")
    append_event(run_id, "info", stage_name, output[-1600:] if output else "lan-interface-ok")


def _run_ns1_lan_mac_ensure_include(run_id: str, stage_name: str) -> None:
    _, _, values = _ns1_lan_mac_pxe_context(_run_request_inputs(run_id))
    config_path = str(values.get("dhcp_config_path") or "").strip()
    include_line = str(values.get("dhcp_include_line") or "").strip()
    if not config_path.startswith("/etc/dhcp/") or not include_line:
        raise PipelineExecutionError("Refusing to update DHCP include outside /etc/dhcp.")
    command = (
        "set -e; "
        f"touch {shlex.quote(config_path)}; "
        f"grep -Fxq {shlex.quote(include_line)} {shlex.quote(config_path)} || "
        f"(cp -a {shlex.quote(config_path)} {shlex.quote(config_path + '.bkc-backup')} && "
        f"printf '\\n%s\\n' {shlex.quote(include_line)} >> {shlex.quote(config_path)}); "
        f"grep -Fx {shlex.quote(include_line)} {shlex.quote(config_path)}"
    )
    output = _run_ns1_command(values, command, timeout=30)
    _set_stage(run_id, stage_name, "complete", "DHCP root config includes the BKC LAN MAC PXE fragment.")
    append_event(run_id, "info", stage_name, output[-1200:] if output else include_line)


def _run_ns1_lan_mac_upload_template(run_id: str, stage_name: str, template_name: str, target_key: str) -> None:
    pipeline, _, values = _ns1_lan_mac_pxe_context(_run_request_inputs(run_id))
    template_path = _repo_pipeline_folder(pipeline) / "templates" / template_name
    target_path = str(values.get(target_key) or "").strip()
    if not template_path.exists():
        raise PipelineExecutionError(f"Template not found: {template_path}")
    if not target_path.startswith("/etc/dhcp/") and not target_path.startswith("/etc/default/"):
        raise PipelineExecutionError(f"Refusing to write template outside DHCP paths: {target_path}")
    content = _render_pipeline_template(template_path.read_text(encoding="utf-8"), values).encode("utf-8")
    _run_ns1_command(values, f"mkdir -p {shlex.quote(str(Path(target_path).parent))}", timeout=30)
    upload_remote_bytes(
        host=str(values.get("target_host") or "").strip(),
        user="root",
        remote_path=target_path,
        content=content,
        mode=0o644,
        timeout=60,
    )
    output = _run_ns1_command(values, f"test -s {shlex.quote(target_path)} && sed -n '1,120p' {shlex.quote(target_path)}", timeout=30)
    append_event(run_id, "info", stage_name, output[-2400:] if output else target_path)


def _run_ns1_lan_mac_render_fragment(run_id: str, stage_name: str) -> None:
    _run_ns1_lan_mac_upload_template(run_id, stage_name, "dhcpd-lan-mac-pxe.conf.tpl", "dhcp_fragment_path")
    _set_stage(run_id, stage_name, "complete", "LAN MAC-only PXE DHCP fragment rendered on ns1.")


def _openstack_bkc_compose_context(run_id: str) -> tuple[dict, dict, dict]:
    return _folder_pipeline_context("openstack-bkc-compose-deploy", _run_request_inputs(run_id))


def _openstack_bkc_target(values: dict) -> tuple[str, str, str]:
    host = str(values.get("target_host") or "").strip()
    user = str(values.get("target_user") or "root").strip() or "root"
    password = str(values.get("target_password") or "")
    if not host:
        raise PipelineExecutionError("openstack-bkc-compose-deploy requires target_host.")
    return host, user, password


def _run_openstack_bkc_command(values: dict, command: str, *, timeout: int = 120) -> str:
    host, user, password = _openstack_bkc_target(values)
    return run_remote_command(host=host, user=user, password=password, command=command, timeout=timeout)


def _openstack_bkc_quote_values(values: dict) -> dict[str, str]:
    keys = [
        "target_root",
        "runtime_mount",
        "runtime_export",
        "repo_url",
        "repo_branch",
        "compose_path",
        "env_path",
        "bkc_internal_url",
    ]
    return {key: shlex.quote(str(values.get(key) or "")) for key in keys}


def _run_openstack_bkc_compose_preflight(run_id: str, stage_name: str) -> None:
    _, _, values = _openstack_bkc_compose_context(run_id)
    q = _openstack_bkc_quote_values(values)
    command = (
        "set -e; "
        "printf 'host='; hostname; "
        "printf 'kernel='; uname -sr; "
        "printf 'ip='; hostname -I || true; "
        "command -v apt-get >/dev/null; "
        "getent hosts ns1.example.local >/dev/null 2>&1 || true; "
        f"showmount -e {shlex.quote(str(values.get('runtime_export') or '').split(':', 1)[0])} >/tmp/bkc-nfs-showmount.txt 2>&1 || true; "
        "printf '\\n-- docker --\\n'; docker --version 2>/dev/null || true; "
        "printf '\\n-- compose --\\n'; docker compose version 2>/dev/null || true; "
        "printf '\\n-- nfs export --\\n'; sed -n '1,80p' /tmp/bkc-nfs-showmount.txt || true; "
        f"printf '\\nplanned_runtime=%s\\n' {q['runtime_mount']}; "
        f"printf 'planned_repo=%s branch=%s\\n' {q['repo_url']} {q['repo_branch']}"
    )
    output = _run_openstack_bkc_command(values, command, timeout=120)
    append_event(run_id, "info", stage_name, output[-3000:] if output else "openstack-bkc-preflight-ok")
    _set_stage(run_id, stage_name, "complete", "OpenStack BKC VM responded over SSH and basic deploy prerequisites were inspected.")


def _run_openstack_bkc_compose_runtime(run_id: str, stage_name: str) -> None:
    _, _, values = _openstack_bkc_compose_context(run_id)
    q = _openstack_bkc_quote_values(values)
    runtime_mount = str(values.get("runtime_mount") or "/srv/bkc/runtime")
    runtime_export = str(values.get("runtime_export") or "")
    if not runtime_mount.startswith("/srv/bkc/"):
        raise PipelineExecutionError(f"Refusing runtime mount outside /srv/bkc: {runtime_mount}")
    if ":" not in runtime_export:
        raise PipelineExecutionError("runtime_export must be an NFS server:path value.")
    fstab_line = f"{runtime_export} {runtime_mount} nfs defaults,_netdev,nofail 0 0"
    command = (
        "set -e; "
        "export DEBIAN_FRONTEND=noninteractive; "
        "apt-get update; "
        "apt-get install -y --no-install-recommends git curl ca-certificates nfs-common docker.io docker-compose-plugin; "
        "systemctl enable --now docker; "
        f"mkdir -p {q['target_root']} {q['runtime_mount']}; "
        f"grep -Fxq {shlex.quote(fstab_line)} /etc/fstab || printf '%s\\n' {shlex.quote(fstab_line)} >> /etc/fstab; "
        f"mountpoint -q {q['runtime_mount']} || mount {q['runtime_mount']}; "
        f"mkdir -p {q['runtime_mount']}/dictionaries {q['runtime_mount']}/file_templates {q['runtime_mount']}/keys {q['runtime_mount']}/pipelines {q['runtime_mount']}/redis; "
        f"chmod 700 {q['runtime_mount']}/keys; "
        f"if [ ! -d {q['target_root']}/source/.git ]; then git clone {q['repo_url']} {q['target_root']}/source; fi; "
        f"cd {q['target_root']}/source; git fetch --all --prune; git switch {q['repo_branch']}; git pull --ff-only; "
        f"test -d {q['runtime_mount']}/pipelines; mountpoint {q['runtime_mount']} || true; "
        "docker --version; docker compose version"
    )
    output = _run_openstack_bkc_command(values, command, timeout=900)
    append_event(run_id, "info", stage_name, output[-4000:] if output else "openstack-bkc-runtime-ready")
    _set_stage(run_id, stage_name, "complete", "Target runtime, ns1 NFS mount, Docker, Compose plugin, and source checkout are ready.")


def _run_openstack_bkc_compose_render(run_id: str, stage_name: str) -> None:
    pipeline, _, values = _openstack_bkc_compose_context(run_id)
    folder = _repo_pipeline_folder(pipeline)
    compose_template = folder / "templates" / "bkc-compose.yml.tpl"
    env_template = folder / "templates" / "bkc.env.tpl"
    if not compose_template.exists() or not env_template.exists():
        raise PipelineExecutionError("OpenStack BKC compose templates are missing.")

    compose_path = str(values.get("compose_path") or "/srv/bkc/compose.yml")
    env_path = str(values.get("env_path") or "/srv/bkc/.env")
    for path in (compose_path, env_path):
        if not path.startswith("/srv/bkc/"):
            raise PipelineExecutionError(f"Refusing to write OpenStack BKC artifact outside /srv/bkc: {path}")

    compose_content = _render_pipeline_template(compose_template.read_text(encoding="utf-8"), values).encode("utf-8")
    env_content = _render_pipeline_template(env_template.read_text(encoding="utf-8"), values).encode("utf-8")
    host, user, password = _openstack_bkc_target(values)
    _run_openstack_bkc_command(values, "mkdir -p /srv/bkc", timeout=30)
    upload_remote_bytes(host=host, user=user, password=password, remote_path=compose_path, content=compose_content, mode=0o644, timeout=60)
    upload_remote_bytes(host=host, user=user, password=password, remote_path=env_path, content=env_content, mode=0o600, timeout=60)
    output = _run_openstack_bkc_command(
        values,
        f"set -e; ls -l {shlex.quote(compose_path)} {shlex.quote(env_path)}; sed -n '1,160p' {shlex.quote(compose_path)}",
        timeout=30,
    )
    append_event(run_id, "info", stage_name, output[-3000:] if output else "compose-and-env-rendered")
    _set_stage(run_id, stage_name, "complete", "Docker Compose file and target-local .env were rendered on the OpenStack BKC VM.")


def _run_openstack_bkc_compose_up(run_id: str, stage_name: str) -> None:
    _, _, values = _openstack_bkc_compose_context(run_id)
    q = _openstack_bkc_quote_values(values)
    command = (
        "set -e; "
        f"cd {q['target_root']}/source; "
        "git fetch --all --prune; "
        f"git switch {q['repo_branch']}; "
        "git pull --ff-only; "
        f"cd {q['target_root']}; "
        f"docker compose --env-file {q['env_path']} -f {q['compose_path']} up -d --build; "
        f"docker compose -f {q['compose_path']} ps"
    )
    output = _run_openstack_bkc_command(values, command, timeout=1800)
    append_event(run_id, "info", stage_name, output[-5000:] if output else "docker-compose-up-complete")
    _set_stage(run_id, stage_name, "complete", "BKC web, worker, and Redis Compose services were deployed on the OpenStack VM.")


def _run_openstack_bkc_compose_validate(run_id: str, stage_name: str) -> None:
    _, _, values = _openstack_bkc_compose_context(run_id)
    q = _openstack_bkc_quote_values(values)
    command = (
        "set -e; "
        "for i in $(seq 1 30); do "
        f"code=$(curl -fsS -o /tmp/bkc-ready.out -w '%{{http_code}}' {q['bkc_internal_url']}/ready 2>/tmp/bkc-ready.err || true); "
        "[ \"$code\" = 200 ] && break; "
        "sleep 5; "
        "done; "
        "cat /tmp/bkc-ready.out 2>/dev/null || true; "
        "printf '\\nhttp_code=%s\\n' \"$code\"; "
        "[ \"$code\" = 200 ]; "
        f"cd {q['target_root']}; docker compose -f {q['compose_path']} ps"
    )
    output = _run_openstack_bkc_command(values, command, timeout=240)
    _store_run_extra(run_id, {"openstack_bkc_backend": f"http://{values.get('target_host')}:{values.get('bkc_public_port', 5000)}"})
    append_event(run_id, "info", stage_name, output[-5000:] if output else "new-bkc-ready")
    _set_stage(run_id, stage_name, "complete", "The OpenStack-side BKC /ready endpoint returned HTTP 200.")


def _run_openstack_bkc_edge_pointer(run_id: str, stage_name: str) -> None:
    _, _, values = _openstack_bkc_compose_context(run_id)
    backend = f"http://{values.get('target_host')}:{values.get('bkc_public_port', 5000)}"
    payload = {
        "label": str(values.get("bkc_edge_label") or "BlackKnightController — OpenStack"),
        "mode": str(values.get("edge_pointer_mode") or "record-only"),
        "edge_mode": str(values.get("bkc_edge_mode") or "proxy-or-nat"),
        "edge_public_port": values.get("bkc_edge_public_port"),
        "enabled": _truthy(values.get("enable_edge_pointer")),
        "edge_url": str(values.get("bkc_edge_url") or ""),
        "backend": backend,
        "notes": str(values.get("edge_notes") or ""),
    }
    _store_run_extra(run_id, {"openstack_bkc_edge_pointer": payload})
    append_event(run_id, "info", stage_name, json.dumps(payload, sort_keys=True))
    _set_stage(run_id, stage_name, "complete", f"{payload['label']} edge pointer recorded: {payload['edge_url']} -> {backend}")


def _run_openstack_bkc_swarm_promote(run_id: str, stage_name: str) -> None:
    pipeline_id = "openstack-bkc-swarm-promote"
    pipeline, _, values = _folder_pipeline_context(pipeline_id, _run_request_inputs(run_id))
    pipeline_folder = _repo_pipeline_folder(pipeline)
    script_path = pipeline_folder / "scripts" / "promote-openstack-bkc-image.sh"
    if not script_path.exists():
        raise PipelineExecutionError(f"OpenStack BKC promote helper script is missing: {script_path}")

    enable_promote = values.get("enable_promote")
    if enable_promote is not None and not _truthy(enable_promote):
        _set_stage(run_id, stage_name, "complete", "Promotion disabled by enable_promote=false; no service update requested.")
        return

    image = str(values.get("image") or "").strip()
    if not image:
        raise PipelineExecutionError("Promotion image is required.")

    env = os.environ.copy()
    env["BKC_PROMOTE_IMAGE"] = image
    env["PORTAINER_URL"] = str(values.get("portainer_url") or "https://127.0.0.1:9443")
    env["PORTAINER_USER"] = str(values.get("portainer_user") or "admin")
    env["PORTAINER_ENDPOINT_ID"] = str(values.get("portainer_endpoint_id") or 6)
    services = values.get("services")
    if isinstance(services, list) and services:
        env["BKC_PROMOTE_SERVICES"] = ",".join(str(item).strip() for item in services if str(item).strip())
    elif str(values.get("services") or "").strip():
        env["BKC_PROMOTE_SERVICES"] = str(values.get("services"))
    if str(values.get("portainer_password") or "").strip() and not env.get("PORTAINER_PASSWORD"):
        env["PORTAINER_PASSWORD"] = str(values.get("portainer_password"))

    append_event(
        run_id,
        "info",
        stage_name,
        json.dumps(
            {
                "pipeline_id": pipeline_id,
                "image": image,
                "portainer_url": env["PORTAINER_URL"],
                "endpoint_id": env["PORTAINER_ENDPOINT_ID"],
                "services": env.get("BKC_PROMOTE_SERVICES", "bkc-alt_bkc,bkc-alt_worker,bkc-alt_slow-worker"),
                "edge_url": values.get("edge_url"),
            },
            sort_keys=True,
        ),
    )
    try:
        completed = subprocess.run(
            [str(script_path)],
            check=False,
            capture_output=True,
            text=True,
            timeout=int(values.get("script_timeout_seconds") or 900),
            cwd=str(pipeline_folder),
            env=env,
        )
    except subprocess.TimeoutExpired as exc:
        raise PipelineExecutionError(f"OpenStack BKC Swarm promotion timed out after {exc.timeout}s") from exc
    except Exception as exc:  # noqa: BLE001
        raise PipelineExecutionError(f"OpenStack BKC Swarm promotion failed to start: {exc}") from exc

    output = "\n".join(part for part in [completed.stdout, completed.stderr] if part).strip()
    if output:
        append_event(run_id, "info", stage_name, output[-12000:])
    _store_run_extra(
        run_id,
        {
            "openstack_bkc_swarm_promote": {
                "image": image,
                "returncode": completed.returncode,
                "services": env.get("BKC_PROMOTE_SERVICES", "bkc-alt_bkc,bkc-alt_worker,bkc-alt_slow-worker"),
            }
        },
    )
    if completed.returncode != 0:
        raise PipelineExecutionError(output[-4000:] or f"OpenStack BKC Swarm promotion exited {completed.returncode}")
    _set_stage(run_id, stage_name, "complete", f"OpenStack BKC Swarm services promoted to {image}.")


def _run_ns1_lan_mac_render_defaults(run_id: str, stage_name: str) -> None:
    _run_ns1_lan_mac_upload_template(run_id, stage_name, "isc-dhcp-server-lan.defaults.tpl", "dhcp_defaults_path")
    _set_stage(run_id, stage_name, "complete", "DHCP interface defaults rendered on ns1.")


def _run_ns1_lan_mac_validate_config(run_id: str, stage_name: str) -> None:
    _, _, values = _ns1_lan_mac_pxe_context(_run_request_inputs(run_id))
    config_path = str(values.get("dhcp_config_path") or "").strip()
    output = _run_ns1_command(values, f"dhcpd -t -cf {shlex.quote(config_path)}", timeout=30)
    _set_stage(run_id, stage_name, "complete", "DHCP config syntax is valid.")
    append_event(run_id, "info", stage_name, output[-1600:] if output else "dhcpd-config-ok")


def _run_ns1_lan_mac_restart(run_id: str, stage_name: str) -> None:
    _, _, values = _ns1_lan_mac_pxe_context(_run_request_inputs(run_id))
    if not _truthy(values.get("enable_lan_pxe_service")):
        _set_stage(run_id, stage_name, "complete", "DHCP restart skipped because enable_lan_pxe_service is not true.")
        return
    service = str(values.get("dhcp_service_name") or "isc-dhcp-server").strip()
    command = (
        "set -e; "
        f"svc={shlex.quote(service)}; "
        "systemctl list-unit-files \"$svc.service\" >/dev/null 2>&1 || svc=dhcpd; "
        "systemctl restart \"$svc.service\"; "
        "systemctl is-active \"$svc.service\""
    )
    output = _run_ns1_command(values, command, timeout=60)
    _set_stage(run_id, stage_name, "complete", "DHCP service restarted with LAN MAC-only PXE enabled.")
    append_event(run_id, "info", stage_name, output[-1200:] if output else "dhcp-service-active")


def _run_trixie_pxe_prereqs(run_id: str, stage_name: str) -> None:
    _, _, values = _folder_pipeline_context("ns1-trixie-pxe-smoke")
    interface = shlex.quote(str(values.get("provisioning_interface") or ""))
    cidr = shlex.quote(str(values.get("provisioning_network_cidr") or ""))
    command = (
        "set -e; "
        f"ip link show {interface} >/dev/null; "
        f"ip -o addr show dev {interface} | grep -F {shlex.quote(str(values.get('pxe_http_host') or ''))} >/dev/null; "
        f"case {cidr} in 10.*/*|172.16.*/*|172.17.*/*|172.18.*/*|172.19.*/*|172.20.*/*|172.21.*/*|172.22.*/*|172.23.*/*|172.24.*/*|172.25.*/*|172.26.*/*|172.27.*/*|172.28.*/*|172.29.*/*|172.30.*/*|172.31.*/*) ;; *) exit 12 ;; esac; "
        "echo trixie-pxe-prereqs-ok"
    )
    output = _run_ns1_command(values, command, timeout=60)
    _set_stage(run_id, stage_name, "complete", "ns1 PXE prerequisites are present.")
    append_event(run_id, "info", stage_name, output[-1200:] if output else "trixie-pxe-prereqs-ok")


def _run_trixie_netboot_fetch(run_id: str, stage_name: str) -> None:
    _, _, values = _folder_pipeline_context("ns1-trixie-pxe-smoke")
    source = str(values.get("netboot_source_url") or "").rstrip("/") + "/"
    root = str(values.get("netboot_root") or "")
    command = (
        "set -e; "
        f"mkdir -p {shlex.quote(root)}; "
        f"curl -fsSL -o {shlex.quote(str(values.get('netboot_kernel_path') or ''))} {shlex.quote(source + 'linux')}; "
        f"curl -fsSL -o {shlex.quote(str(values.get('netboot_initrd_path') or ''))} {shlex.quote(source + 'initrd.gz')}; "
        f"test -s {shlex.quote(str(values.get('netboot_kernel_path') or ''))}; "
        f"test -s {shlex.quote(str(values.get('netboot_initrd_path') or ''))}; "
        f"ls -lh {shlex.quote(root)}"
    )
    output = _run_ns1_command(values, command, timeout=300)
    _set_stage(run_id, stage_name, "complete", "Debian Trixie netboot assets are cached on ns1.")
    append_event(run_id, "info", stage_name, output[-1600:] if output else "trixie netboot cached")


def _openstack_lab_context(run_inputs: dict | None = None) -> tuple[dict, dict, dict]:
    return _folder_pipeline_context("baremetal-openstack-lab-prepare", run_inputs)


def _openstack_lab_upload_template(
    run_id: str,
    stage_name: str,
    template_name: str,
    target_key: str,
    values: dict,
    *,
    mode: int = 0o644,
) -> None:
    pipeline, _, _ = _openstack_lab_context()
    template_path = _repo_pipeline_folder(pipeline) / "templates" / template_name
    if not template_path.exists():
        raise PipelineExecutionError(f"Template not found: {template_path}")
    target_path = str(values.get(target_key) or "").strip()
    if not target_path.startswith("/srv/") and not target_path.startswith("/etc/dhcp/"):
        raise PipelineExecutionError(f"Refusing to write template outside approved paths: {target_path}")
    content = _render_pipeline_template(template_path.read_text(encoding="utf-8"), values).encode("utf-8")
    _run_ns1_command(values, f"mkdir -p {shlex.quote(str(Path(target_path).parent))}", timeout=60)
    upload_remote_bytes(
        host=str(values.get("target_host") or "").strip(),
        user="root",
        remote_path=target_path,
        content=content,
        mode=mode,
        timeout=60,
    )
    output = _run_ns1_command(values, f"test -s {shlex.quote(target_path)} && ls -l {shlex.quote(target_path)}", timeout=60)
    append_event(run_id, "info", stage_name, output[-1200:] if output else target_path)


def _run_openstack_base_boot_render(run_id: str, stage_name: str) -> None:
    _, _, values = _openstack_lab_context(_run_request_inputs(run_id))
    render_values = dict(values)
    if not str(render_values.get("target_ssh_authorized_key") or "").strip():
        integrations = load_integrations()
        ssh = integrations["ssh"]
        render_values["target_ssh_authorized_key"] = read_key_pair(
            ssh["private_key_path"],
            ssh["public_key_path"],
        ).get("public_key", "")
    if not str(render_values.get("target_ssh_authorized_key") or "").startswith("ssh-"):
        raise PipelineExecutionError("BKC SSH public key is missing or invalid.")

    _openstack_lab_upload_template(
        run_id,
        stage_name,
        "r630-openstack-01-trixie.ipxe.tpl",
        "physical_ipxe_script_path",
        render_values,
    )
    _openstack_lab_upload_template(
        run_id,
        stage_name,
        "r630-openstack-01-trixie-preseed.cfg.tpl",
        "physical_preseed_path",
        render_values,
    )
    guard_enabled = _truthy(render_values.get("enable_pxe_guard")) or _truthy(render_values.get("physical_pxe_guard_enabled"))
    guard_path = str(render_values.get("physical_ipxe_guard_path") or render_values.get("physical_ipxe_script_path") or "").strip()
    if guard_enabled and guard_path:
        guard = (
            "#!ipxe\n"
            "# BKC guard: first R630 already entered Debian installer. Boot local disk on accidental PXE retry.\n"
            "sanboot --no-describe --drive 0x80 || exit\n"
        )
        upload_remote_bytes(
            host=str(render_values.get("target_host") or "").strip(),
            user="root",
            remote_path=guard_path,
            content=guard.encode("utf-8"),
            mode=0o644,
            timeout=60,
        )
        append_event(run_id, "info", stage_name, f"Installed physical local-disk PXE guard at {guard_path}.")
        _set_stage(run_id, stage_name, "complete", "First R630 Trixie preseed rendered and local-disk PXE guard installed on ns1.")
        return
    _set_stage(run_id, stage_name, "complete", "First R630 Trixie installer iPXE and preseed rendered on ns1.")


def _run_openstack_base_boot_validate(run_id: str, stage_name: str) -> None:
    _, _, values = _openstack_lab_context(_run_request_inputs(run_id))
    ipxe_path = str(values.get("physical_ipxe_script_path") or "").strip()
    preseed_path = str(values.get("physical_preseed_path") or "").strip()
    http_host = str(values.get("physical_pxe_http_host") or "").strip()
    preseed_url = str(values.get("physical_preseed_url") or "").strip()
    if not ipxe_path or not preseed_path or not http_host or not preseed_url:
        raise PipelineExecutionError("OpenStack PXE validation requires iPXE path, preseed path, HTTP host, and preseed URL.")
    pxe_expectation = "sanboot --no-describe --drive 0x80" if (
        _truthy(values.get("enable_pxe_guard")) or _truthy(values.get("physical_pxe_guard_enabled"))
    ) else "preseed/url="
    command = (
        "set -e; "
        f"test -s {shlex.quote(ipxe_path)}; "
        f"test -s {shlex.quote(preseed_path)}; "
        f"curl -fsSI --connect-timeout 5 {shlex.quote('http://' + http_host + '/pxe/debian-trixie.ipxe')} >/tmp/bkc-r630-ipxe.headers; "
        f"curl -fsSI --connect-timeout 5 {shlex.quote(preseed_url)} >/tmp/bkc-r630-preseed.headers; "
        f"grep -F {shlex.quote(str(values.get('physical_install_hostname') or 'r630-openstack-01'))} {shlex.quote(preseed_path)} >/dev/null; "
        f"grep -F {shlex.quote(pxe_expectation)} {shlex.quote(ipxe_path)} >/dev/null; "
        f"grep -F '/var/lib/bkc/base-provisioning.json' {shlex.quote(preseed_path)} >/dev/null; "
        f"printf 'ipxe=%s\\npreseed=%s\\n' {shlex.quote(ipxe_path)} {shlex.quote(preseed_path)}; "
        "cat /tmp/bkc-r630-ipxe.headers /tmp/bkc-r630-preseed.headers"
    )
    output = _run_ns1_command(values, command, timeout=60)
    _set_stage(run_id, stage_name, "complete", "First R630 PXE boot assets are reachable over ns1 HTTP.")
    append_event(run_id, "info", stage_name, output[-2000:] if output else "openstack pxe assets validated")


def _truthy(value: object) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "on", "enable", "enabled"}


def _run_request_inputs(run_id: str) -> dict:
    run = get_run(run_id) or {}
    extra = run.get("extra") if isinstance(run.get("extra"), dict) else {}
    payload = extra.get("request_payload") if isinstance(extra.get("request_payload"), dict) else {}
    extra_inputs = extra.get("inputs") if isinstance(extra.get("inputs"), dict) else {}
    inputs = payload.get("inputs") if isinstance(payload.get("inputs"), dict) else {}
    merged = dict(extra_inputs)
    merged.update(inputs)
    ignored_payload_keys = {"repo", "workflow", "ref", "commit", "notes", "inputs"}
    for key, value in payload.items():
        if key in ignored_payload_keys or key in merged:
            continue
        if isinstance(value, (str, int, float, bool)) or value is None:
            merged[key] = value
    for key in (
        "target_node_id",
        "enable_power_actions",
        "enable_one_time_pxe",
        "enable_disk_wipe",
        "reset_type",
    ):
        if key in payload and key not in merged:
            merged[key] = payload[key]
    return merged


def _secret_ref_key(secret_ref: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", secret_ref.replace("secret:", "")).strip("_").upper()


def _resolve_bmc_credentials(secret_ref: str) -> tuple[str, str]:
    key = _secret_ref_key(secret_ref)
    combined = os.environ.get(f"BKC_SECRET_{key}", "").strip()
    username = os.environ.get(f"BKC_SECRET_{key}_USERNAME", "").strip()
    password = os.environ.get(f"BKC_SECRET_{key}_PASSWORD", "").strip()
    if combined:
        if combined.startswith("{"):
            try:
                payload = json.loads(combined)
                username = str(payload.get("username") or username).strip()
                password = str(payload.get("password") or password).strip()
            except json.JSONDecodeError as exc:
                raise PipelineExecutionError(f"BMC secret {secret_ref} is not valid JSON.") from exc
        elif ":" in combined and not password:
            username, password = combined.split(":", 1)

    secret_dir = Path(os.environ.get("BKC_BMC_SECRET_DIR", "/run/secrets/bkc")).resolve()
    rel = secret_ref.replace("secret:", "")
    candidates = [
        secret_dir / f"{key}.json",
        secret_dir / f"{key}.txt",
        secret_dir.joinpath(*rel.split("/")).with_suffix(".json"),
        secret_dir.joinpath(*rel.split("/")).with_suffix(".txt"),
    ]
    for path in candidates:
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8").strip()
        if path.suffix == ".json" or text.startswith("{"):
            try:
                payload = json.loads(text)
            except json.JSONDecodeError as exc:
                raise PipelineExecutionError(f"BMC secret file {path} is not valid JSON.") from exc
            username = str(payload.get("username") or username).strip()
            password = str(payload.get("password") or password).strip()
        elif ":" in text:
            username, password = text.split(":", 1)
            username = username.strip()
            password = password.strip()
        elif text and not password:
            password = text

    username = username or "root"
    if not password:
        raise PipelineExecutionError(
            f"BMC credential {secret_ref} is missing. Set BKC_SECRET_{key}_PASSWORD or provide a runtime secret file."
        )
    return username, password


def _redfish_request(address: str, path: str, username: str, password: str, *, method: str = "GET", payload: dict | None = None, timeout: int = 20) -> tuple[int, str]:
    url = f"https://{address}{path if path.startswith('/') else '/' + path}"
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(url, data=body, method=method)
    request.add_header("Accept", "application/json")
    if body is not None:
        request.add_header("Content-Type", "application/json")
    token = b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
    request.add_header("Authorization", f"Basic {token}")
    context = ssl._create_unverified_context()
    try:
        context.set_ciphers("DEFAULT:@SECLEVEL=1")
    except ssl.SSLError:
        pass
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
            return int(response.status), response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:500]
        raise PipelineExecutionError(f"Redfish {method} {path} failed with HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise PipelineExecutionError(f"Redfish {method} {path} failed: {exc}") from exc


def _redfish_request_via_ns1(
    values: dict,
    address: str,
    path: str,
    username: str,
    password: str,
    *,
    method: str = "GET",
    payload: dict | None = None,
    timeout: int = 20,
) -> tuple[int, str]:
    """Reach the isolated iDRAC network through BKC SSH on ns1."""
    url = f"https://{address}{path if path.startswith('/') else '/' + path}"
    parts = [
        "curl", "-ksS", "--max-time", str(timeout), "-X", method,
        "-u", f"{username}:{password}", "-H", "Accept: application/json",
    ]
    if payload is not None:
        parts.extend(["-H", "Content-Type: application/json", "--data-binary", json.dumps(payload)])
    parts.extend(["-w", "\\n__BKC_HTTP_STATUS__:%{http_code}", url])
    ns1_host = str(values.get("target_host") or values.get("provisioning_ssh_host") or "").strip()
    if not ns1_host:
        raise PipelineExecutionError("NS1 SSH host is missing for isolated Redfish transport.")
    output = run_remote_command(
        host=ns1_host,
        user="root",
        command=" ".join(shlex.quote(part) for part in parts),
        timeout=timeout + 10,
    )
    marker = "\n__BKC_HTTP_STATUS__:"
    if marker not in output:
        raise PipelineExecutionError(f"Redfish {method} {path} through ns1 returned no HTTP status.")
    body, raw_status = output.rsplit(marker, 1)
    try:
        status = int(raw_status.strip())
    except ValueError as exc:
        raise PipelineExecutionError(f"Redfish {method} {path} through ns1 returned an invalid status.") from exc
    if status < 200 or status >= 300:
        raise PipelineExecutionError(f"Redfish {method} {path} through ns1 failed with HTTP {status}: {body[:500]}")
    return status, body


def _run_baremetal_bmc_power_reset(run_id: str, stage_name: str) -> None:
    _, _, values = _folder_pipeline_context("baremetal-lab-reset", _run_request_inputs(run_id))
    enabled = _truthy(values.get("enable_power_actions"))
    target_node_id = str(values.get("target_node_id") or "").strip()
    if not enabled:
        _set_stage(run_id, stage_name, "complete", "BMC power reset skipped because enable_power_actions is not true.")
        return
    if not target_node_id:
        raise PipelineExecutionError("target_node_id is required when enable_power_actions=true.")

    hosts = values.get("hosts") if isinstance(values.get("hosts"), list) else []
    target = next((host for host in hosts if str(host.get("node_id") or "") == target_node_id), None)
    if not target:
        raise PipelineExecutionError(f"Target node {target_node_id} is not in the bare metal reset scope.")
    address = str(target.get("bmc_observed_address") or "").strip()
    secret_ref = str(target.get("bmc_credential_ref") or "").strip()
    if not address or not secret_ref:
        raise PipelineExecutionError("Target BMC address and credential ref are required for power reset.")

    username, password = _resolve_bmc_credentials(secret_ref)
    status, body = _redfish_request(address, "/redfish/v1/Systems", username, password)
    systems = json.loads(body or "{}")
    members = systems.get("Members") if isinstance(systems, dict) else []
    if not members:
        raise PipelineExecutionError("Redfish Systems collection did not return any members.")
    system_path = str(members[0].get("@odata.id") or "").strip()
    if not system_path:
        raise PipelineExecutionError("Redfish system member is missing @odata.id.")

    _, system_body = _redfish_request(address, system_path, username, password)
    system = json.loads(system_body or "{}")
    if _truthy(values.get("enable_one_time_pxe")):
        _redfish_request(
            address,
            system_path,
            username,
            password,
            method="PATCH",
            payload={
                "Boot": {
                    "BootSourceOverrideEnabled": "Once",
                    "BootSourceOverrideTarget": "Pxe",
                }
            },
        )
        append_event(run_id, "info", stage_name, f"One-time PXE boot override requested for {target_node_id}.")
    actions = system.get("Actions") if isinstance(system, dict) else {}
    reset_action = actions.get("#ComputerSystem.Reset") if isinstance(actions, dict) else {}
    reset_target = str(reset_action.get("target") or f"{system_path}/Actions/ComputerSystem.Reset").strip()
    reset_type = str(values.get("reset_type") or "ForceRestart").strip() or "ForceRestart"
    _redfish_request(address, reset_target, username, password, method="POST", payload={"ResetType": reset_type})

    _store_run_extra(
        run_id,
        {
            "bmc_power_reset": {
                "target_node_id": target_node_id,
                "bmc_address": address,
                "redfish_system": system_path,
                "reset_type": reset_type,
                "one_time_pxe": _truthy(values.get("enable_one_time_pxe")),
            }
        },
    )
    _set_stage(run_id, stage_name, "complete", f"BMC power reset requested for {target_node_id} using Redfish {reset_type}.")


def _vmware_trial_context(run_inputs: dict | None = None) -> tuple[dict, dict, dict]:
    return _folder_pipeline_context("baremetal-vmware-trial-prepare", run_inputs)


def _vmware_trial_upload_template(
    run_id: str,
    stage_name: str,
    template_name: str,
    target_key: str,
    values: dict,
    *,
    mode: int = 0o644,
) -> None:
    pipeline, _, _ = _vmware_trial_context()
    template_path = _repo_pipeline_folder(pipeline) / "templates" / template_name
    if not template_path.exists():
        raise PipelineExecutionError(f"Template not found: {template_path}")
    target_path = str(values.get(target_key) or "").strip()
    if not target_path.startswith("/srv/pxe/"):
        raise PipelineExecutionError(f"Refusing to write VMware PXE asset outside /srv/pxe: {target_path}")
    content = _render_pipeline_template(template_path.read_text(encoding="utf-8"), values).encode("utf-8")
    _run_ns1_command(values, f"mkdir -p {shlex.quote(str(Path(target_path).parent))}", timeout=60)
    upload_remote_bytes(
        host=str(values.get("target_host") or "").strip(),
        user="root",
        remote_path=target_path,
        content=content,
        mode=mode,
        timeout=60,
    )
    output = _run_ns1_command(values, f"test -s {shlex.quote(target_path)} && ls -l {shlex.quote(target_path)}", timeout=60)
    append_event(run_id, "info", stage_name, output[-1200:] if output else target_path)


def _run_vmware_esxi_boot_assets_render(run_id: str, stage_name: str) -> None:
    _, _, values = _vmware_trial_context(_run_request_inputs(run_id))
    if not _truthy(values.get("enable_vmware_asset_render")):
        _set_stage(run_id, stage_name, "complete", "Reviewed ESXi boot asset render intent; enable_vmware_asset_render is false.")
        append_event(run_id, "info", stage_name, "VMware ESXi boot asset writes remain gated.")
        return

    render_values = dict(values)
    if not str(render_values.get("vmware_authorized_key") or "").strip():
        integrations = load_integrations()
        ssh = integrations["ssh"]
        render_values["vmware_authorized_key"] = read_key_pair(
            ssh["private_key_path"],
            ssh["public_key_path"],
        ).get("public_key", "")
    if not str(render_values.get("vmware_authorized_key") or "").startswith("ssh-"):
        raise PipelineExecutionError("BKC SSH public key is missing or invalid for VMware firstboot.")

    _vmware_trial_upload_template(
        run_id,
        stage_name,
        "esxi-ks.cfg.tpl",
        "vmware_ks_path",
        render_values,
    )
    _vmware_trial_upload_template(
        run_id,
        stage_name,
        "bkc-esxi-firstboot.sh.tpl",
        "vmware_firstboot_path",
        render_values,
        mode=0o755,
    )
    command = (
        "set -e; "
        f"grep -F 'vim-cmd hostsvc/enable_ssh' {shlex.quote(str(render_values.get('vmware_ks_path') or ''))} >/dev/null; "
        f"grep -F 'system account add --id={shlex.quote(str(render_values.get('vmware_admin_username') or 'admin'))}' {shlex.quote(str(render_values.get('vmware_ks_path') or ''))} >/dev/null; "
        f"grep -F '/scratch/bkc/firstboot.json' {shlex.quote(str(render_values.get('vmware_ks_path') or ''))} >/dev/null; "
        f"test -x {shlex.quote(str(render_values.get('vmware_firstboot_path') or ''))}; "
        "echo vmware-esxi-boot-assets-rendered"
    )
    output = _run_ns1_command(render_values, command, timeout=60)
    _set_stage(run_id, stage_name, "complete", "Experimental ESXi boot.cfg, ks.cfg, and firstboot assets rendered on ns1.")
    append_event(run_id, "info", stage_name, output[-1200:] if output else "vmware-esxi-boot-assets-rendered")


def _run_vmware_esxi_media_stage(run_id: str, stage_name: str) -> None:
    _, _, values = _vmware_trial_context(_run_request_inputs(run_id))
    if not _truthy(values.get("enable_vmware_media_stage")):
        _set_stage(run_id, stage_name, "complete", "Validated ESXi media staging intent; enable_vmware_media_stage is false.")
        append_event(run_id, "info", stage_name, "VMware ESXi media writes remain gated.")
        return

    iso = str(values.get("vmware_iso_stage_path") or "").strip()
    expected = str((values.get("vmware_operator_media") or {}).get("sha256") or "").strip().lower()
    iso_url = f"http://10.20.0.10/pxe/vmware/esxi-8u3e/{Path(iso).name}"
    if not iso.startswith("/srv/pxe/") or not expected:
        raise PipelineExecutionError("ESXi staged ISO path and checksum are required.")

    command = (
        "set -e; "
        f"iso={shlex.quote(iso)}; expected={shlex.quote(expected)}; "
        "test -s \"$iso\"; actual=$(sha256sum \"$iso\" | cut -d' ' -f1); test \"$actual\" = \"$expected\"; "
        "dhcpd -t -cf /etc/dhcp/dhcpd.conf >/dev/null; "
        f"curl -fsSI {shlex.quote(iso_url)}; "
        "echo vmware-esxi-intact-iso-ready"
    )
    output = _run_ns1_command(values, command, timeout=900)
    _set_stage(run_id, stage_name, "complete", "Untouched ESXi ISO checksum and HTTP delivery validated on ns1.")
    append_event(run_id, "info", stage_name, output[-1600:] if output else "vmware-esxi-intact-iso-ready")


def _run_vmware_esxi_iso_handoff(run_id: str, stage_name: str) -> None:
    inputs = _run_request_inputs(run_id)
    if not _truthy(inputs.get("enable_destructive_install")):
        _set_stage(run_id, stage_name, "complete", "Reviewed intact ESXi ISO handoff; destructive gate is disabled.")
        return
    pipeline, _, values = _vmware_trial_context(inputs)
    host = values["physical_hosts"][0]
    mac = str(host["provisioning_mac"])
    lease = str(values["installer_lease_address"])
    iso = str(values["vmware_iso_stage_path"])
    iso_url = f"http://10.20.0.10/pxe/vmware/esxi-8u3e/{Path(iso).name}"
    ns1 = str(values["target_host"])
    baseline = int(run_remote_command(host=ns1, user="root", command="wc -l </var/log/nginx/access.log", timeout=20).strip())

    arm = _repo_pipeline_folder(pipeline) / "scripts/arm-esxi-one-shot-pxe.sh"
    remote_arm = "/tmp/bkc-arm-esxi-one-shot-pxe.sh"
    upload_remote_bytes(host=ns1, user="root", remote_path=remote_arm, content=arm.read_bytes(), mode=0o700, timeout=60)
    armed = run_remote_command(
        host=ns1,
        user="root",
        command=f"{remote_arm} {shlex.quote(mac)} {shlex.quote(lease)} {shlex.quote(iso_url)}",
        timeout=120,
    )
    bmc_address = _video_bmc_pxe_reset(values)
    append_event(run_id, "warning", stage_name, armed[-1200:] + f"\niDRAC={bmc_address}")

    evidence = ""
    deadline = time.time() + 1200
    iso_path = urllib.parse.urlparse(iso_url).path
    while time.time() < deadline:
        evidence = run_remote_command(
            host=ns1,
            user="root",
            command=(
                f"tail -n +{baseline + 1} /var/log/nginx/access.log | "
                f"grep -F {shlex.quote(lease)} | grep -F {shlex.quote(iso_path)} | tail -1 || true"
            ),
            timeout=20,
        )
        if evidence and (' 200 ' in evidence or ' 206 ' in evidence):
            break
        time.sleep(10)
    if not evidence:
        raise PipelineExecutionError("No fresh HTTP read of the intact ESXi ISO was observed.")

    disarm = _repo_pipeline_folder(pipeline) / "scripts/disarm-esxi-one-shot-pxe.sh"
    remote_disarm = "/tmp/bkc-disarm-esxi-one-shot-pxe.sh"
    upload_remote_bytes(host=ns1, user="root", remote_path=remote_disarm, content=disarm.read_bytes(), mode=0o700, timeout=60)
    disarmed = run_remote_command(host=ns1, user="root", command=remote_disarm, timeout=90)

    username, password = _resolve_bmc_credentials(str(host.get("bmc_credential_ref") or ""))
    _, body = _redfish_request_via_ns1(values, bmc_address, "/redfish/v1/Systems", username, password)
    members = json.loads(body or "{}").get("Members", [])
    if not members:
        raise PipelineExecutionError("Server2 Redfish system disappeared during ESXi ISO handoff.")
    _redfish_request_via_ns1(
        values,
        bmc_address,
        str(members[0]["@odata.id"]),
        username,
        password,
        method="PATCH",
        payload={"Boot": {"BootSourceOverrideEnabled": "Once", "BootSourceOverrideTarget": "Hdd"}},
    )
    append_event(run_id, "info", stage_name, evidence[-1200:] + "\n" + disarmed[-800:] + "\nserver2_next_boot=Hdd")
    _set_stage(run_id, stage_name, "complete", "Intact ESXi ISO boot observed; PXE disarmed and operator installer handoff ready.")


def _esxi_seed_context(run_id: str) -> tuple[dict, dict]:
    _, _, values = _folder_pipeline_context("esxi-docker-swarm-seed", _run_request_inputs(run_id))
    return get_run(run_id) or {}, values


def _esxi_seed_password(values: dict) -> str:
    return _secret_ref_literal(values.get("esxi_password_demo"), default="changeme123@44")


def _esxi_seed_nodes(values: dict) -> list[dict]:
    nodes = values.get("swarm_nodes")
    if not isinstance(nodes, list) or not nodes:
        raise PipelineExecutionError("ESXi swarm_nodes must define at least one VM.")
    cleaned: list[dict] = []
    for raw in nodes:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name") or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,62}", name):
            raise PipelineExecutionError(f"Unsafe ESXi VM name: {name!r}")
        cleaned.append(
            {
                "name": name,
                "role": str(raw.get("role") or "worker").strip(),
                "memory_mb": int(raw.get("memory_mb") or 2048),
                "num_cpus": int(raw.get("num_cpus") or 2),
                "disk_gb": int(raw.get("disk_gb") or 32),
            }
        )
    if not cleaned:
        raise PipelineExecutionError("ESXi swarm_nodes did not contain any valid VM records.")
    return cleaned


def _esxi_inventory(values: dict) -> dict:
    from services import vsphere

    return vsphere.inventory(
        host=str(values.get("esxi_management_host") or "10.20.0.114"),
        username=str(values.get("esxi_username") or "root"),
        password=_esxi_seed_password(values),
        verify_ssl=_truthy(values.get("esxi_validate_certs")),
    )


def _video_esxi_api_preflight(run_id: str, stage_name: str) -> None:
    _, values = _esxi_seed_context(run_id)
    inventory = _esxi_inventory(values)
    datastore = str(values.get("esxi_datastore") or "datastore1")
    network = str(values.get("esxi_network") or "VM Network")
    if not any(item.get("name") == datastore and item.get("accessible") for item in inventory.get("datastores", [])):
        raise PipelineExecutionError(f"ESXi datastore is not accessible: {datastore}")
    if not any(item.get("name") == network for item in inventory.get("networks", [])):
        raise PipelineExecutionError(f"ESXi network is not present: {network}")
    _store_run_extra(run_id, {"esxi_inventory_preflight": inventory})
    append_event(run_id, "info", stage_name, json.dumps(inventory, indent=2, sort_keys=True)[-5000:])
    _set_stage(run_id, stage_name, "complete", "ESXi API login, datastore, network, and inventory validated.")


def _video_esxi_enable_ssh(run_id: str, stage_name: str) -> None:
    _, values = _esxi_seed_context(run_id)
    from services import vsphere

    host = str(values.get("esxi_management_host") or "10.20.0.114")
    user = str(values.get("esxi_username") or "root")
    password = _esxi_seed_password(values)
    try:
        result = vsphere.configure_service(
            host=host,
            username=user,
            password=password,
            service_key="TSM-SSH",
            running=True,
            policy="on",
            verify_ssl=_truthy(values.get("esxi_validate_certs")),
        )
    except vsphere.VsphereRestrictedError as exc:
        try:
            probe = run_remote_command(host=host, user=user, password=password, command="vim-cmd hostsvc/hostsummary | head -20", timeout=15)
        except Exception as ssh_exc:  # noqa: BLE001
            raise PipelineExecutionError(
                "ESXi API inventory works, but this ESXi license/version blocked enabling SSH over API. "
                "Enable Host > Manage > Services > TSM-SSH from the ESXi Host Client, then rerun 50B. "
                f"API detail: {exc}; SSH probe: {ssh_exc}"
            ) from exc
        result = {"key": "TSM-SSH", "running": True, "policy": "operator-enabled", "probe": probe[:500]}
    append_event(run_id, "info", stage_name, json.dumps(result, sort_keys=True))
    _set_stage(run_id, stage_name, "complete", "ESXi SSH service is available for BKC population.")


def _video_esxi_swarm_shells(run_id: str, stage_name: str) -> None:
    pipeline, _, values = _folder_pipeline_context("esxi-docker-swarm-seed", _run_request_inputs(run_id))
    host = str(values.get("esxi_management_host") or "10.20.0.114")
    user = str(values.get("esxi_username") or "root")
    password = _esxi_seed_password(values)
    script = (_repo_pipeline_folder(pipeline) / "scripts" / "create-esxi-swarm-vms.sh").read_text()
    command = (
        "set -e; "
        "cat >/tmp/create-esxi-swarm-vms.sh <<'BKC_ESXI_CREATE'\n"
        f"{script}\n"
        "BKC_ESXI_CREATE\n"
        "chmod +x /tmp/create-esxi-swarm-vms.sh; "
        "/bin/sh /tmp/create-esxi-swarm-vms.sh"
    )
    out = run_remote_command(host=host, user=user, password=password, command=command, timeout=900)
    _store_run_extra(run_id, {"esxi_swarm_clone": out[-6000:]})
    append_event(run_id, "info", stage_name, out[-6000:])
    _set_stage(run_id, stage_name, "complete", "ESXi Docker Swarm VM clones exist with the known-good SATA/vmxnet3 shape.")


def _video_esxi_swarm_dhcp(run_id: str, stage_name: str) -> None:
    pipeline, _, values = _folder_pipeline_context("esxi-docker-swarm-seed", _run_request_inputs(run_id))
    host = str(values.get("esxi_management_host") or "10.20.0.114")
    user = str(values.get("esxi_username") or "root")
    password = _esxi_seed_password(values)
    nodes = _esxi_seed_nodes(values)
    macs: dict[str, str] = {}
    for node in nodes:
        name = node["name"]
        command = f"grep '^ethernet0.generatedAddress' /vmfs/volumes/datastore1/{shlex.quote(name)}/{shlex.quote(name)}.vmx || true"
        out = run_remote_command(host=host, user=user, password=password, command=command, timeout=30)
        match = re.search(r'"(([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2})"', out)
        if not match:
            raise PipelineExecutionError(f"Could not discover generated MAC for ESXi VM {name}.")
        macs[name] = match.group(1).lower()
    env_map = {
        "esxi-swarm-mgr-01": "ESXI_SWARM_MGR_01_MAC",
        "esxi-swarm-mgr-02": "ESXI_SWARM_MGR_02_MAC",
        "esxi-swarm-worker-01": "ESXI_SWARM_WORKER_01_MAC",
        "esxi-swarm-worker-02": "ESXI_SWARM_WORKER_02_MAC",
        "esxi-swarm-worker-03": "ESXI_SWARM_WORKER_03_MAC",
    }
    script = (_repo_pipeline_folder(pipeline) / "scripts" / "render-esxi-swarm-dhcp-reservations.sh").read_text()
    env = " ".join(f"{env_map[name]}={shlex.quote(mac)}" for name, mac in macs.items() if name in env_map)
    ns1_command = (
        "set -e; "
        "cat >/tmp/render-esxi-swarm-dhcp-reservations.sh <<'BKC_ESXI_DHCP'\n"
        f"{script}\n"
        "BKC_ESXI_DHCP\n"
        "chmod +x /tmp/render-esxi-swarm-dhcp-reservations.sh; "
        f"{env} /tmp/render-esxi-swarm-dhcp-reservations.sh; "
        "cat /etc/dhcp/dhcpd.d/bkc-esxi-swarm-guests.conf"
    )
    out = run_remote_command(host="10.20.0.10", user="root", command=ns1_command, timeout=180)
    _store_run_extra(run_id, {"esxi_swarm_dhcp_macs": macs})
    append_event(run_id, "info", stage_name, json.dumps(macs, indent=2, sort_keys=True) + "\n" + out[-3000:])
    _set_stage(run_id, stage_name, "complete", "ns1 DHCP reservations pinned ESXi Swarm generated MACs to 10.20.0.121-.125.")


def _video_esxi_swarm_bootstrap(run_id: str, stage_name: str) -> None:
    pipeline, _, _ = _folder_pipeline_context("esxi-docker-swarm-seed", _run_request_inputs(run_id))
    script_path = _repo_pipeline_folder(pipeline) / "scripts" / "bootstrap-esxi-docker-swarm.py"
    namespace = {"__name__": "__bkc_esxi_swarm_bootstrap__"}
    import contextlib
    import io

    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        exec(compile(script_path.read_text(), str(script_path), "exec"), namespace)
        namespace["main"]()
    text = output.getvalue()
    _store_run_extra(run_id, {"esxi_swarm_bootstrap": text[-6000:]})
    append_event(run_id, "info", stage_name, text[-6000:])
    _set_stage(run_id, stage_name, "complete", "ESXi Docker Swarm converged with two managers and three workers.")


def _video_esxi_swarm_inventory(run_id: str, stage_name: str) -> None:
    _, values = _esxi_seed_context(run_id)
    inventory = _esxi_inventory(values)
    template_name = str(values.get("esxi_template_name") or "bkc-trixie-base")
    expected = {node["name"] for node in _esxi_seed_nodes(values)}
    present = {str(vm.get("name")) for vm in inventory.get("vms", [])}
    if template_name not in present:
        raise PipelineExecutionError(f"ESXi inventory is missing imported base VM/template: {template_name}")
    missing = sorted(expected - present)
    if missing:
        append_event(run_id, "warning", stage_name, "ESXi swarm guest clone stage is not complete yet; missing VM(s): " + ", ".join(missing))
    _store_run_extra(run_id, {"esxi_inventory_after_population": inventory})
    append_event(run_id, "info", stage_name, json.dumps(inventory.get("vms", []), indent=2, sort_keys=True))
    detail = f"ESXi inventory contains imported base {template_name}."
    if not missing:
        detail += " All target swarm VMs are present."
    else:
        detail += f" Clone/bootstrap remains next; {len(missing)} target VM(s) are not present yet."
    _set_stage(run_id, stage_name, "complete", detail)


def _video_esxi_swarm_fragments(run_id: str, stage_name: str) -> None:
    fragments = {
        "esxi.edge.tls-passthrough": {
            "rating": "known-good",
            "contract": "Use https://swarm1.lab.auzietek.com:8443/ui/ for raw TLS passthrough to the ESXi Host Client; :8087 is only the HTTP reverse proxy path.",
        },
        "esxi.seed.first-population": {
            "rating": "candidate-known-good",
            "contract": "50B validates ESXi API access, enables TSM-SSH, and registers three named Debian VM shells with thin disks on datastore1.",
        },
        "esxi.seed.remaining-gap": {
            "rating": "planned",
            "contract": "Next iteration should import or install a real Debian Trixie base, then clone/customize guests and run the Docker Swarm bootstrap.",
        },
    }
    _store_run_extra(run_id, {"esxi_swarm_fragments": fragments})


def _micro_blog_context(run_id: str) -> tuple[dict, dict, dict]:
    return _folder_pipeline_context("micro-blog-swarm-compose", _run_request_inputs(run_id))


def _micro_blog_content_context(run_id: str) -> tuple[dict, dict, dict]:
    return _folder_pipeline_context("micro-blog-lab-content-canary", _run_request_inputs(run_id))


def _micro_blog_public_context(run_id: str) -> tuple[dict, dict, dict]:
    return _folder_pipeline_context("auzietek-public-article-publish", _run_request_inputs(run_id))


def _micro_blog_public_ui_context(run_id: str) -> tuple[dict, dict, dict]:
    return _folder_pipeline_context("auzietek-beta-preview-deploy", _run_request_inputs(run_id))


def _micro_blog_public_ui_values(run_id: str) -> dict:
    _, _, values = _micro_blog_public_ui_context(run_id)
    return values


def _micro_blog_remote(run_id: str, command: str, *, timeout: int = 300, host_override: str = "") -> str:
    _, _, values = _micro_blog_context(run_id)
    host = str(host_override or values.get("target_manager_host") or "10.20.0.121").strip()
    user = str(values.get("target_user") or "admin-deploy").strip()
    password = str(values.get("target_password") or "").strip()
    if not host or not user:
        raise PipelineExecutionError("micro-blog target_manager_host and target_user are required.")
    route_mode = str(values.get("ssh_route_mode") or "auto").strip().lower()
    if route_mode in {"lab-direct", "direct"}:
        return run_remote_command(host=host, user=user, password=password, command=command, timeout=timeout)
    if route_mode == "auto":
        try:
            return run_remote_command(host=host, user=user, password=password, command=command, timeout=timeout)
        except Exception as direct_exc:  # noqa: BLE001
            append_event(run_id, "warning", "micro-blog-ssh-route", f"Direct SSH failed; falling back to ns1 jump: {direct_exc}")
    if route_mode in {"auto", "ns1-jump", "jump", "proxyjump"}:
        integrations = load_integrations()
        ssh = integrations["ssh"]
        key_info = read_key_pair(ssh["private_key_path"], ssh["public_key_path"])
        private_key = str(key_info["private_key_path"])
        jump = str(values.get("ssh_jump_host") or "root@192.168.1.10").strip()
        ssh_args = [
            "ssh",
            "-i",
            private_key,
            "-o",
            "IdentitiesOnly=yes",
            "-o",
            "StrictHostKeyChecking=no",
            "-o",
            "UserKnownHostsFile=/tmp/bkc_micro_blog_known_hosts",
            "-o",
            f"ProxyCommand=ssh -i {shlex.quote(private_key)} -o IdentitiesOnly=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/tmp/bkc_micro_blog_jump_known_hosts -W %h:%p {shlex.quote(jump)}",
            f"{user}@{host}",
            command,
        ]
        try:
            completed = subprocess.run(
                ssh_args,
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except Exception as exc:  # noqa: BLE001
            raise PipelineExecutionError(f"micro-blog SSH jump execution failed: {exc}") from exc
        output = (completed.stdout or "").strip()
        error = (completed.stderr or "").strip()
        if completed.returncode != 0:
            raise PipelineExecutionError(error or output or f"micro-blog SSH jump exited {completed.returncode}")
        return output
    raise PipelineExecutionError(f"Unsupported micro-blog ssh_route_mode: {route_mode}")


def _micro_blog_paths(values: dict) -> dict[str, str]:
    mountpoint = str(values.get("nfs_mountpoint") or "/mnt/swarm").rstrip("/")
    source_path = str(values.get("source_path") or f"{mountpoint}/shared/micro-blog/src-candidate").rstrip("/")
    stage_path = str(values.get("target_stage_path") or "/srv/micro-blog-stack").rstrip("/")
    content_path = str(values.get("target_content_path") or "/srv/micro-blog/content").rstrip("/")
    return {
        "mountpoint": mountpoint,
        "source_path": source_path,
        "stage_path": stage_path,
        "content_path": content_path,
        "stack_file": f"{stage_path}/micro-blog.stack.yml",
    }


def _micro_blog_ensure_nfs_script(values: dict) -> str:
    nfs_export = str(values.get("nfs_export") or "10.20.0.10:/srv/nfs/swarm")
    paths = _micro_blog_paths(values)
    return f"""
set -euo pipefail
if ! command -v mountpoint >/dev/null 2>&1; then
  sudo apt-get update -y >/dev/null
  sudo apt-get install -y util-linux >/dev/null
fi
if ! command -v mount.nfs >/dev/null 2>&1; then
  sudo apt-get update -y >/dev/null
  sudo apt-get install -y nfs-common >/dev/null
fi
sudo mkdir -p {shlex.quote(paths["mountpoint"])}
if ! mountpoint -q {shlex.quote(paths["mountpoint"])}; then
  sudo mount -t nfs {shlex.quote(nfs_export)} {shlex.quote(paths["mountpoint"])}
fi
test -d {shlex.quote(paths["source_path"])}
test -f {shlex.quote(paths["source_path"] + "/docker-compose.yml")}
test -f {shlex.quote(paths["source_path"] + "/src/api/Dockerfile")}
test -f {shlex.quote(paths["source_path"] + "/src/worker/Dockerfile")}
test -f {shlex.quote(paths["source_path"] + "/src/projection/Dockerfile")}
test -f {shlex.quote(paths["source_path"] + "/src/ui/Dockerfile")}
"""


def _run_micro_blog_source_preflight(run_id: str, stage_name: str) -> None:
    _, _, values = _micro_blog_context(run_id)
    out = _micro_blog_remote(
        run_id,
        _micro_blog_ensure_nfs_script(values)
        + f"\nfind {shlex.quote(_micro_blog_paths(values)['source_path'])} -maxdepth 2 -type f | sort | sed -n '1,80p'\n",
        timeout=240,
    )
    append_event(run_id, "info", stage_name, out[-6000:])
    _set_stage(run_id, stage_name, "complete", "NFS-staged micro-blog source bundle is visible from the target swarm manager.")


def _run_micro_blog_registry_preflight(run_id: str, stage_name: str) -> None:
    _, _, values = _micro_blog_context(run_id)
    registry = str(values.get("registry_host") or "swarm1.lab.auzietek.com:5001").strip()
    insecure_registries = values.get("insecure_registry_hosts")
    if not isinstance(insecure_registries, list) or not insecure_registries:
        insecure_registries = [registry]
    target_hosts = values.get("target_node_hosts")
    if not isinstance(target_hosts, list) or not target_hosts:
        target_hosts = [str(values.get("target_manager_host") or "10.20.0.121")]
    registry_json = json.dumps([str(item) for item in insecure_registries if str(item).strip()])
    for target_host in [str(item).strip() for item in target_hosts if str(item).strip()]:
        configure = f"""
set -euo pipefail
sudo mkdir -p /etc/docker
if [ -f /etc/docker/daemon.json ] && [ ! -f /etc/docker/daemon.json.bkc-pre-micro-blog ]; then
  sudo cp /etc/docker/daemon.json /etc/docker/daemon.json.bkc-pre-micro-blog
fi
sudo python3 - <<'PY'
import json
from pathlib import Path
p = Path('/etc/docker/daemon.json')
data = {{}}
if p.exists() and p.read_text().strip():
    data = json.loads(p.read_text())
regs = data.setdefault('insecure-registries', [])
for reg in {registry_json!r}:
    if reg not in regs:
        regs.append(reg)
p.write_text(json.dumps(data, indent=2, sort_keys=True) + '\\n')
PY
sudo systemctl restart docker
"""
        _micro_blog_remote(run_id, configure, timeout=180, host_override=target_host)
    command = f"""
set -euo pipefail
curl -fsS --max-time 8 http://{shlex.quote(registry)}/v2/ >/dev/null
echo registry-ok {shlex.quote(registry)}
"""
    out = _micro_blog_remote(run_id, command, timeout=60)
    append_event(run_id, "info", stage_name, out)
    _set_stage(run_id, stage_name, "complete", f"Lab registry is reachable from target manager: {registry}.")


def _run_micro_blog_target_swarm_preflight(run_id: str, stage_name: str) -> None:
    _, _, values = _micro_blog_context(run_id)
    expected_workers = int(values.get("expected_worker_count") or 3)
    expected_managers = int(values.get("expected_manager_count") or 2)
    label = str(values.get("placement_label") or "bkc.workload=app")
    command = f"""
set -euo pipefail
docker_cmd=docker
if ! docker info >/dev/null 2>&1; then docker_cmd="sudo docker"; fi
$docker_cmd info --format 'swarm={{{{.Swarm.LocalNodeState}}}} node={{{{.Swarm.NodeID}}}}'
$docker_cmd node ls
managers="$($docker_cmd node ls --filter role=manager --format '{{{{.Hostname}}}}' | wc -l)"
workers="$($docker_cmd node ls --filter role=worker --format '{{{{.Hostname}}}}' | wc -l)"
test "$managers" -ge {expected_managers}
test "$workers" -ge {expected_workers}
key={shlex.quote(label.split('=', 1)[0])}
value={shlex.quote(label.split('=', 1)[1] if '=' in label else '')}
if [ -n "$value" ]; then
  for n in $($docker_cmd node ls --filter role=worker --format '{{{{.Hostname}}}}'); do
    current="$($docker_cmd node inspect "$n" --format '{{{{ index .Spec.Labels "'$key'" }}}}' 2>/dev/null || true)"
    if [ "$current" != "$value" ]; then
      $docker_cmd node update --label-add "$key=$value" "$n" >/dev/null
    fi
  done
  labeled="$($docker_cmd node ls --format '{{{{.Hostname}}}}' | while read -r n; do $docker_cmd node inspect "$n" --format '{{{{ index .Spec.Labels "'$key'" }}}}' 2>/dev/null; done | grep -Fx "$value" | wc -l)"
  test "$labeled" -ge {expected_workers}
  echo labeled-workers=$labeled
fi
"""
    out = _micro_blog_remote(run_id, command, timeout=180)
    append_event(run_id, "info", stage_name, out[-6000:])
    _set_stage(run_id, stage_name, "complete", "Target Docker Swarm manager, node count, and placement labels validated.")


def _run_micro_blog_build_push(run_id: str, stage_name: str) -> None:
    _, _, values = _micro_blog_context(run_id)
    paths = _micro_blog_paths(values)
    registry = str(values.get("registry_host") or "swarm1.lab.auzietek.com:5001").strip()
    tag = str(values.get("image_tag") or "candidate").strip()
    command = _micro_blog_ensure_nfs_script(values) + f"""
set -euo pipefail
cd {shlex.quote(paths["source_path"])}
docker_cmd=docker
if ! docker info >/dev/null 2>&1; then docker_cmd="sudo docker"; fi
for item in \\
  blog-api:src/api/Dockerfile \\
  blog-worker:src/worker/Dockerfile \\
  blog-projection:src/projection/Dockerfile \\
  blog-ui:src/ui/Dockerfile
do
  svc="${{item%%:*}}"
  dockerfile="${{item#*:}}"
  image={shlex.quote(registry)}/micro-blog/${{svc}}:{shlex.quote(tag)}
  echo "building $image"
  $docker_cmd build -t "$image" -f "$dockerfile" .
  echo "pushing $image"
  $docker_cmd push "$image"
done
"""
    out = _micro_blog_remote(run_id, command, timeout=1500)
    append_event(run_id, "info", stage_name, out[-8000:])
    _set_stage(run_id, stage_name, "complete", "Micro-blog app images built and pushed to the lab registry.")


def _micro_blog_stack_yaml(values: dict) -> str:
    registry = str(values.get("registry_host") or "swarm1.lab.auzietek.com:5001").strip()
    tag = str(values.get("image_tag") or "candidate").strip()
    ui_port = int(values.get("publish_ui_port") or 18081)
    api_port = int(values.get("publish_api_port") or 18080)
    paths = _micro_blog_paths(values)
    content_path = paths["content_path"]
    return f"""networks:
  app_net:
    driver: overlay
    attachable: true

volumes:
  rabbitmq-data:
  redis-data:
  postgres-data:

configs:
  otel_collector_local:
    file: ./collector/otel-collector-local.yaml

services:
  rabbitmq:
    image: rabbitmq:3.12-management
    environment:
      RABBITMQ_DEFAULT_USER: "${{RABBITMQ_DEFAULT_USER:-guest}}"
      RABBITMQ_DEFAULT_PASS: "${{RABBITMQ_DEFAULT_PASS:-guest}}"
    volumes:
      - rabbitmq-data:/var/lib/rabbitmq
    networks: [app_net]
    deploy:
      placement:
        constraints: ["node.labels.bkc.workload == app"]

  redis:
    image: redis:7-alpine
    command: ["redis-server", "--appendonly", "yes"]
    volumes:
      - redis-data:/data
    networks: [app_net]
    deploy:
      placement:
        constraints: ["node.labels.bkc.workload == app"]

  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_DB: "microblog"
      POSTGRES_USER: "blog"
      POSTGRES_PASSWORD: "${{POSTGRES_PASSWORD:-Str0ngP@ssword!}}"
    volumes:
      - postgres-data:/var/lib/postgresql/data
    networks: [app_net]
    deploy:
      placement:
        constraints: ["node.labels.bkc.workload == app"]

  otel-collector:
    image: otel/opentelemetry-collector-contrib:0.103.0
    command: ["--config=/etc/otel-collector.yaml"]
    configs:
      - source: otel_collector_local
        target: /etc/otel-collector.yaml
    ports:
      - "4317:4317"
      - "4318:4318"
      - "9464:9464"
    networks:
      app_net:
        aliases: [otel-collector]
    deploy:
      placement:
        constraints: ["node.labels.bkc.workload == app"]

  blog-api:
    image: {registry}/micro-blog/blog-api:{tag}
    env_file: .env
    environment:
      DATABASE_URL: "dbname=microblog user=blog password=${{POSTGRES_PASSWORD:-Str0ngP@ssword!}} host=postgres port=5432"
      REDIS_URL: "redis://redis:6379/0"
      RABBITMQ_URL: "amqp://${{RABBITMQ_DEFAULT_USER:-guest}}:${{RABBITMQ_DEFAULT_PASS:-guest}}@rabbitmq:5672/%2F"
      OTEL_EXPORTER_OTLP_ENDPOINT: "http://${{OTEL_COLLECTOR_SERVICE:-otel-collector}}:4318"
      OTEL_ENVIRONMENT: "${{OTEL_ENVIRONMENT:-lab}}"
      SERVICE_NAMESPACE: "microblog"
      ADMIN_EMAIL: "${{ADMIN_EMAIL:-admin@example.invalid}}"
      CONTENT_IMPORT_ROOT: "/content"
      CONTENT_PUBLIC_BASE: "/content-files"
      AUTO_IMPORT_FILESYSTEM_ON_BOOT: "${{AUTO_IMPORT_FILESYSTEM_ON_BOOT:-false}}"
    volumes:
      - {content_path}:/content
    ports:
      - "{api_port}:8080"
    networks: [app_net]
    deploy:
      replicas: 1
      placement:
        constraints: ["node.labels.bkc.workload == app"]

  blog-worker:
    image: {registry}/micro-blog/blog-worker:{tag}
    env_file: .env
    environment:
      DATABASE_URL: "dbname=microblog user=blog password=${{POSTGRES_PASSWORD:-Str0ngP@ssword!}} host=postgres port=5432"
      RABBITMQ_URL: "amqp://${{RABBITMQ_DEFAULT_USER:-guest}}:${{RABBITMQ_DEFAULT_PASS:-guest}}@rabbitmq:5672/%2F"
      OTEL_EXPORTER_OTLP_ENDPOINT: "http://${{OTEL_COLLECTOR_SERVICE:-otel-collector}}:4318"
      OTEL_ENVIRONMENT: "${{OTEL_ENVIRONMENT:-lab}}"
      SERVICE_NAMESPACE: "microblog"
    networks: [app_net]
    deploy:
      replicas: 1
      placement:
        constraints: ["node.labels.bkc.workload == app"]

  blog-projection:
    image: {registry}/micro-blog/blog-projection:{tag}
    env_file: .env
    environment:
      DATABASE_URL: "dbname=microblog user=blog password=${{POSTGRES_PASSWORD:-Str0ngP@ssword!}} host=postgres port=5432"
      REDIS_URL: "redis://redis:6379/0"
      RABBITMQ_URL: "amqp://${{RABBITMQ_DEFAULT_USER:-guest}}:${{RABBITMQ_DEFAULT_PASS:-guest}}@rabbitmq:5672/%2F"
      OTEL_EXPORTER_OTLP_ENDPOINT: "http://${{OTEL_COLLECTOR_SERVICE:-otel-collector}}:4318"
      OTEL_ENVIRONMENT: "${{OTEL_ENVIRONMENT:-lab}}"
      SERVICE_NAMESPACE: "microblog"
    networks: [app_net]
    deploy:
      replicas: 1
      placement:
        constraints: ["node.labels.bkc.workload == app"]

  blog-ui:
    image: {registry}/micro-blog/blog-ui:{tag}
    env_file: .env
    environment:
      BLOG_API_BASE_URL: "http://blog-api:8080"
      OTEL_EXPORTER_OTLP_ENDPOINT: "http://${{OTEL_COLLECTOR_SERVICE:-otel-collector}}:4318"
      OTEL_ENVIRONMENT: "${{OTEL_ENVIRONMENT:-lab}}"
      SERVICE_NAMESPACE: "microblog"
      ADMIN_EMAIL: "${{ADMIN_EMAIL:-admin@example.invalid}}"
      ADMIN_ACCESS_CODE: "${{ADMIN_ACCESS_CODE:-local-admin}}"
      FLASK_SECRET_KEY: "${{FLASK_SECRET_KEY:-change-me-before-deploy}}"
      GOOGLE_CLIENT_ID: "${{GOOGLE_CLIENT_ID:-}}"
      GOOGLE_CLIENT_SECRET: "${{GOOGLE_CLIENT_SECRET:-}}"
      DEFAULT_THEME_VARIANT: "${{DEFAULT_THEME_VARIANT:-midnight}}"
      CONTENT_IMPORT_ROOT: "/content"
    volumes:
      - {content_path}:/content:ro
    ports:
      - "{ui_port}:8080"
    networks: [app_net]
    deploy:
      replicas: 1
      placement:
        constraints: ["node.labels.bkc.workload == app"]
"""


def _run_micro_blog_render_stack(run_id: str, stage_name: str) -> None:
    _, _, values = _micro_blog_context(run_id)
    paths = _micro_blog_paths(values)
    stack_yaml = _micro_blog_stack_yaml(values)
    command = f"""
set -euo pipefail
sudo mkdir -p {shlex.quote(paths["stage_path"] + "/collector")} {shlex.quote(paths["content_path"])}
sudo tee {shlex.quote(paths["stack_file"])} >/dev/null <<'BKC_MICROBLOG_STACK'
{stack_yaml}
BKC_MICROBLOG_STACK
sudo cp {shlex.quote(paths["source_path"] + "/collector/otel-collector-local.yaml")} {shlex.quote(paths["stage_path"] + "/collector/otel-collector-local.yaml")}
sudo chmod 0644 {shlex.quote(paths["stack_file"])} {shlex.quote(paths["stage_path"] + "/collector/otel-collector-local.yaml")}
sed -n '1,80p' {shlex.quote(paths["stack_file"])}
"""
    out = _micro_blog_remote(run_id, command, timeout=180)
    append_event(run_id, "info", stage_name, out[-6000:])
    _set_stage(run_id, stage_name, "complete", "Swarm-safe micro-blog stack rendered on target manager.")


def _run_micro_blog_stage_stack(run_id: str, stage_name: str) -> None:
    _, _, values = _micro_blog_context(run_id)
    paths = _micro_blog_paths(values)
    target_hosts = values.get("target_node_hosts")
    if not isinstance(target_hosts, list) or not target_hosts:
        target_hosts = [str(values.get("target_manager_host") or "10.20.0.121")]
    for target_host in [str(item).strip() for item in target_hosts if str(item).strip()]:
        _micro_blog_remote(
            run_id,
            f"set -euo pipefail; sudo mkdir -p {shlex.quote(paths['content_path'])}; sudo chmod 0755 /srv/micro-blog {shlex.quote(paths['content_path'])}",
            timeout=60,
            host_override=target_host,
        )
    command = f"""
set -euo pipefail
sudo mkdir -p {shlex.quote(paths["stage_path"])} {shlex.quote(paths["content_path"])}
sudo chown {shlex.quote(str(values.get("target_user") or "admin-deploy"))}:{shlex.quote(str(values.get("target_user") or "admin-deploy"))} {shlex.quote(paths["stage_path"])}
if [ ! -f {shlex.quote(paths["stage_path"] + "/.env")} ]; then
  sudo cp {shlex.quote(paths["source_path"] + "/.env.sample")} {shlex.quote(paths["stage_path"] + "/.env")}
  sudo sed -i 's/^SITE_URL=.*/SITE_URL=http:\\/\\/localhost:{int(values.get("publish_ui_port") or 18081)}/' {shlex.quote(paths["stage_path"] + "/.env")}
  sudo sed -i 's/^OTEL_ENVIRONMENT=.*/OTEL_ENVIRONMENT=lab/' {shlex.quote(paths["stage_path"] + "/.env")}
fi
sudo chown {shlex.quote(str(values.get("target_user") or "admin-deploy"))}:{shlex.quote(str(values.get("target_user") or "admin-deploy"))} {shlex.quote(paths["stage_path"] + "/.env")}
sudo chmod 0640 {shlex.quote(paths["stage_path"] + "/.env")}
sudo test -f {shlex.quote(paths["stage_path"] + "/.env")}
sudo test -f {shlex.quote(paths["stage_path"] + "/collector/otel-collector-local.yaml")}
sudo test -f {shlex.quote(paths["stack_file"])}
echo staged={shlex.quote(paths["stage_path"])}
"""
    out = _micro_blog_remote(run_id, command, timeout=180)
    append_event(run_id, "info", stage_name, out)
    _set_stage(run_id, stage_name, "complete", "Micro-blog stack bundle, .env, collector config, and content path staged.")


def _run_micro_blog_deploy_stack(run_id: str, stage_name: str) -> None:
    _, _, values = _micro_blog_context(run_id)
    paths = _micro_blog_paths(values)
    stack = str(values.get("stack_name") or "micro-blog")
    command = f"""
set -euo pipefail
cd {shlex.quote(paths["stage_path"])}
docker_cmd=docker
if ! docker info >/dev/null 2>&1; then docker_cmd="sudo docker"; fi
python3 - <<'PY'
from pathlib import Path
import shlex
lines = []
for raw in Path('.env').read_text().splitlines():
    line = raw.strip()
    if not line or line.startswith('#') or '=' not in line:
        continue
    key, value = line.split('=', 1)
    key = key.strip()
    if not key:
        continue
    lines.append(f"export {{key}}={{shlex.quote(value.strip())}}")
Path('.env.export').write_text("\\n".join(lines) + "\\n")
PY
. ./.env.export
$docker_cmd stack deploy -c {shlex.quote(paths["stack_file"])} {shlex.quote(stack)}
$docker_cmd stack services {shlex.quote(stack)}
"""
    out = _micro_blog_remote(run_id, command, timeout=900)
    append_event(run_id, "info", stage_name, out[-6000:])
    _set_stage(run_id, stage_name, "complete", "Micro-blog stack deploy requested through Docker Swarm.")


def _run_micro_blog_validate_rollout(run_id: str, stage_name: str) -> None:
    _, _, values = _micro_blog_context(run_id)
    stack = str(values.get("stack_name") or "micro-blog")
    ui_port = int(values.get("publish_ui_port") or 18081)
    api_port = int(values.get("publish_api_port") or 18080)
    command = f"""
set -euo pipefail
docker_cmd=docker
if ! docker info >/dev/null 2>&1; then docker_cmd="sudo docker"; fi
deadline=$((SECONDS+300))
while true; do
  not_ready="$($docker_cmd stack services {shlex.quote(stack)} --format '{{{{.Name}}}} {{{{.Replicas}}}}' | awk -F'[ /]+' '$2 != $3 {{print}}' || true)"
  if [ -z "$not_ready" ]; then break; fi
  if [ "$SECONDS" -ge "$deadline" ]; then
    $docker_cmd stack services {shlex.quote(stack)}
    $docker_cmd stack ps {shlex.quote(stack)} --no-trunc
    echo "$not_ready"
    exit 1
  fi
  sleep 5
done
curl -fsS http://127.0.0.1:{api_port}/healthz >/dev/null
curl -fsS http://127.0.0.1:{api_port}/readyz >/dev/null
curl -fsS http://127.0.0.1:{ui_port}/healthz >/dev/null
curl -fsS http://127.0.0.1:{ui_port}/ >/dev/null
$docker_cmd stack services {shlex.quote(stack)}
"""
    out = _micro_blog_remote(run_id, command, timeout=360)
    _store_run_extra(run_id, {"micro_blog_validation": out[-6000:]})
    append_event(run_id, "info", stage_name, out[-6000:])
    _set_stage(run_id, stage_name, "complete", "Micro-blog replicas and HTTP health endpoints validated.")


def _run_micro_blog_note(run_id: str, stage_name: str, detail: str, payload: dict | None = None) -> None:
    if payload:
        append_event(run_id, "info", stage_name, json.dumps(payload, sort_keys=True))
    _set_stage(run_id, stage_name, "complete", detail)


def _run_micro_blog_content_sync_only(run_id: str, stage_name: str) -> None:
    _, _, values = _micro_blog_content_context(run_id)
    build_host = str(values.get("build_host") or "10.20.0.233").strip()
    build_user = str(values.get("build_user") or "admin-deploy").strip()
    source_content_path = str(values.get("source_content_path") or "/var/lib/bkc-builds/src/micro-blog/content").rstrip("/")
    target_content_path = str(values.get("target_content_path") or "/srv/micro-blog/content").rstrip("/")
    target_user = str(values.get("target_user") or "admin-deploy").strip()
    target_hosts = values.get("target_node_hosts")
    if not isinstance(target_hosts, list) or not target_hosts:
        target_hosts = [str(values.get("target_manager_host") or "10.20.0.121")]
    target_hosts = [str(item).strip() for item in target_hosts if str(item).strip()]
    if not build_host or not build_user or not target_hosts:
        raise PipelineExecutionError("micro-blog content sync requires build_host/build_user and target_node_hosts.")

    host_lines = "\n".join(shlex.quote(host) for host in target_hosts)
    command = f"""
set -euo pipefail
source_content_path={shlex.quote(source_content_path)}
target_content_path={shlex.quote(target_content_path)}
target_user={shlex.quote(target_user)}
known_hosts=/tmp/bkc-micro-blog-content-known-hosts
test -d "$source_content_path"
find "$source_content_path" -maxdepth 2 -type f | sort | sed -n '1,60p'
while IFS= read -r host; do
  [ -n "$host" ] || continue
  echo "+ content sync $host:$target_content_path"
  tar -C "$source_content_path" -cf - . |
    ssh -o BatchMode=yes -o ConnectTimeout=10 -o StrictHostKeyChecking=no -o UserKnownHostsFile="$known_hosts" "$target_user@$host" \
      "sudo mkdir -p '$target_content_path' && sudo tar -C '$target_content_path' -xf - && sudo chown -R root:root '$target_content_path'"
done <<'BKC_TARGET_HOSTS'
{host_lines}
BKC_TARGET_HOSTS
echo content-sync-complete nodes={len(target_hosts)} source="$source_content_path" target="$target_content_path"
"""
    out = run_remote_command(host=build_host, user=build_user, command=command, timeout=600)
    _store_run_extra(
        run_id,
        {
            "micro_blog_content_sync": {
                "mode": "copy-update-no-delete",
                "build_host": build_host,
                "source_content_path": source_content_path,
                "target_content_path": target_content_path,
                "target_node_hosts": target_hosts,
            }
        },
    )
    append_event(run_id, "info", stage_name, out[-6000:])
    _set_stage(run_id, stage_name, "complete", "Micro-blog content files copied to lab swarm node content paths without runtime redeploy.")


def _run_micro_blog_filesystem_sync_api(run_id: str, stage_name: str) -> None:
    _, _, values = _micro_blog_content_context(run_id)
    manager_host = str(values.get("target_manager_host") or "10.20.0.121").strip()
    target_user = str(values.get("target_user") or "admin-deploy").strip()
    api_url = str(values.get("api_internal_url") or "http://127.0.0.1:18080").strip()
    content_subdir = str(values.get("filesystem_sync_subdir") or "posts/public-lanes").strip()
    sync_mode = str(values.get("sync_mode") or "update").strip()
    status = str(values.get("status") or "published").strip()
    theme_variant = str(values.get("theme_variant") or "midnight").strip()
    payload_base = json.dumps(
        {
            "root_path": "/content",
            "content_subdir": content_subdir,
            "sync_mode": sync_mode,
            "status": status,
            "theme_variant": theme_variant,
        },
        sort_keys=True,
    )
    command = f"""
set -euo pipefail
api_url={shlex.quote(api_url)}
payload_base={shlex.quote(payload_base)}
env_file=/srv/micro-blog-stack/.env
admin_email=""
if [ -f "$env_file" ]; then
  admin_email="$(grep -E '^ADMIN_EMAIL=' "$env_file" | tail -n 1 | cut -d= -f2- || true)"
fi
admin_email="${{admin_email:-admin@example.invalid}}"
payload="$(ADMIN_EMAIL="$admin_email" python3 - "$payload_base" <<'PY'
import json
import os
import sys
payload = json.loads(sys.argv[1])
payload["admin_email"] = os.environ["ADMIN_EMAIL"]
print(json.dumps(payload, sort_keys=True))
PY
)"
curl -fsS -X POST "$api_url/admin/bootstrap/filesystem-sync" \
  -H 'Content-Type: application/json' \
  --data "$payload"
"""
    out = run_remote_command(host=manager_host, user=target_user, command=command, timeout=180)
    _store_run_extra(
        run_id,
        {
            "micro_blog_filesystem_sync": {
                "api_url": api_url,
                "content_subdir": content_subdir,
                "sync_mode": sync_mode,
                "status": status,
                "theme_variant": theme_variant,
            }
        },
    )
    append_event(run_id, "info", stage_name, out[-4000:])
    _set_stage(run_id, stage_name, "complete", "Micro-blog filesystem sync API accepted the content refresh request.")


def _run_micro_blog_content_proof(run_id: str, stage_name: str) -> None:
    _, _, values = _micro_blog_content_context(run_id)
    lab_url = str(values.get("lab_url") or values.get("edge_url") or "http://swarm1.lab.auzietek.com:8091").rstrip("/")
    smoke_urls = values.get("lab_smoke_urls") or values.get("public_smoke_urls") or []
    proof_strings = values.get("proof_strings") or []
    checked: list[dict] = []

    for url in [str(item).strip() for item in smoke_urls if str(item).strip()]:
        with urllib.request.urlopen(url, timeout=20) as response:
            status = int(getattr(response, "status", 200))
            response.read(2048)
        if status >= 400:
            raise PipelineExecutionError(f"Smoke URL returned HTTP {status}: {url}")
        checked.append({"url": url, "status": status})

    for proof in proof_strings:
        if not isinstance(proof, dict):
            continue
        path = str(proof.get("path") or "").strip()
        needle = str(proof.get("contains") or "").strip()
        if not path or not needle:
            continue
        url = path if path.startswith("http://") or path.startswith("https://") else f"{lab_url}{path}"
        with urllib.request.urlopen(url, timeout=20) as response:
            body = response.read().decode("utf-8", errors="replace")
            status = int(getattr(response, "status", 200))
        if status >= 400:
            raise PipelineExecutionError(f"Proof URL returned HTTP {status}: {url}")
        if needle not in body:
            raise PipelineExecutionError(f"Missing proof string {needle!r} at {url}")
        checked.append({"url": url, "contains": needle, "status": status})

    _store_run_extra(run_id, {"micro_blog_content_proof": checked})
    append_event(run_id, "info", stage_name, json.dumps(checked, sort_keys=True)[-6000:])
    _set_stage(run_id, stage_name, "complete", f"Validated {len(checked)} micro-blog lab route/proof checks.")


def _run_micro_blog_content_fragment_note(run_id: str, stage_name: str) -> None:
    _, _, values = _micro_blog_content_context(run_id)
    payload = {
        "rating": "candidate-known-good",
        "contract": "Content-only micro-blog refresh copies the mounted content tree, calls /admin/bootstrap/filesystem-sync, validates proof strings, and does not rebuild images or update Swarm services.",
        "source_content_path": values.get("source_content_path") or "/var/lib/bkc-builds/src/micro-blog/content",
        "target_content_path": values.get("target_content_path") or "/srv/micro-blog/content",
        "sync_mode": values.get("sync_mode") or "update",
        "lab_url": values.get("lab_url") or "http://swarm1.lab.auzietek.com:8091",
    }
    _run_micro_blog_note(run_id, stage_name, "Recorded content-only micro-blog refresh fragment.", payload)


def _micro_blog_public_values(run_id: str) -> dict:
    _, _, values = _micro_blog_public_context(run_id)
    return values


def _micro_blog_public_host(values: dict) -> tuple[str, str]:
    host = str(values.get("public_remote_host") or values.get("remote_host") or "74.208.45.165").strip()
    user = str(values.get("public_remote_user") or values.get("remote_user") or "root").strip()
    if not host or not user:
        raise PipelineExecutionError("Public micro-blog promotion requires public_remote_host and public_remote_user.")
    return host, user


def _micro_blog_public_app_path(values: dict) -> str:
    return str(values.get("public_remote_app_path") or "/svc/micro-blog").rstrip("/")


def _micro_blog_public_ui_paths(values: dict) -> dict[str, str]:
    remote_path = str(values.get("remote_path") or values.get("public_remote_app_path") or "/svc/micro-blog").rstrip("/")
    source_path = str(values.get("source_path") or "/var/lib/bkc-builds/src/micro-blog").rstrip("/")
    build_host = str(values.get("build_host") or "10.20.0.233").strip()
    build_user = str(values.get("build_user") or "admin-deploy").strip()
    remote_host = str(values.get("remote_host") or values.get("public_remote_host") or "74.208.45.165").strip()
    remote_user = str(values.get("remote_user") or values.get("public_remote_user") or "root").strip()
    compose_service = str(values.get("compose_service") or "blog-ui").strip()
    return {
        "remote_path": remote_path,
        "source_path": source_path,
        "build_host": build_host,
        "build_user": build_user,
        "remote_host": remote_host,
        "remote_user": remote_user,
        "compose_service": compose_service,
    }


def _bool_value(value: object, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on", "enabled"}


def _run_micro_blog_public_ui_preflight(run_id: str, stage_name: str) -> None:
    values = _micro_blog_public_ui_values(run_id)
    paths = _micro_blog_public_ui_paths(values)
    if not paths["remote_host"] or not paths["remote_user"]:
        raise PipelineExecutionError("Public UI deploy requires remote_host/remote_user.")
    source_out = "source_sync=disabled; using source already staged on remote Compose host"
    if _bool_value(values.get("sync_source"), False):
        if not paths["build_host"] or not paths["build_user"]:
            raise PipelineExecutionError("Public UI deploy requires build_host/build_user when sync_source is enabled.")
        source_check = f"""
set -euo pipefail
source_path={shlex.quote(paths["source_path"])}
test -d "$source_path"
test -f "$source_path/src/ui/app.py"
test -f "$source_path/src/ui/Dockerfile"
test -f "$source_path/src/ui/templates/public_index.html"
printf 'source_path=%s\\n' "$source_path"
find "$source_path/src/ui" -maxdepth 2 -type f | wc -l
"""
        source_out = run_remote_command(host=paths["build_host"], user=paths["build_user"], command=source_check, timeout=180)
    remote_check = f"""
set -euo pipefail
cd {shlex.quote(paths["remote_path"])}
test -f .env
test -f docker-compose.yml
test -f src/ui/app.py
test -f src/ui/Dockerfile
docker compose ps --format '{{{{.Service}}}} {{{{.State}}}}' | sort
"""
    remote_out = run_remote_command(host=paths["remote_host"], user=paths["remote_user"], command=remote_check, timeout=180)
    append_event(run_id, "info", stage_name, (source_out + "\n" + remote_out)[-6000:])
    _set_stage(run_id, stage_name, "complete", "Lab-build source and IONOS Compose runtime are ready; remote .env exists.")


def _run_micro_blog_public_ui_source_sync(run_id: str, stage_name: str) -> None:
    values = _micro_blog_public_ui_values(run_id)
    paths = _micro_blog_public_ui_paths(values)
    if not _bool_value(values.get("sync_source"), False):
        note = "Source sync disabled for this run; using source already staged at the IONOS Compose path."
        _store_run_extra(run_id, {"micro_blog_public_ui_source_sync": {"mode": "remote-source-already-staged", "remote_path": paths["remote_path"]}})
        append_event(run_id, "info", stage_name, note)
        _set_stage(run_id, stage_name, "complete", note)
        return
    excludes = values.get("rsync_excludes") or [".git/", ".env", "docker-compose.yml", "__pycache__/", ".pytest_cache/"]
    exclude_args = " ".join(f"--exclude {shlex.quote(str(item))}" for item in excludes)
    target = f"{paths['remote_user']}@{paths['remote_host']}:{paths['remote_path'].rstrip('/')}/"
    command = f"""
set -euo pipefail
source_path={shlex.quote(paths["source_path"])}
target={shlex.quote(target)}
test -d "$source_path"
rsync -az --delete --itemize-changes {exclude_args} "$source_path"/ "$target"
ssh {shlex.quote(paths["remote_user"] + "@" + paths["remote_host"])} 'test -s {shlex.quote(paths["remote_path"] + "/.env")} && test -f {shlex.quote(paths["remote_path"] + "/docker-compose.yml")}'
"""
    out = run_remote_command(host=paths["build_host"], user=paths["build_user"], command=command, timeout=600)
    _store_run_extra(
        run_id,
        {
            "micro_blog_public_ui_source_sync": {
                "mode": "rsync-delete-with-deployment-excludes",
                "source_host": paths["build_host"],
                "source_path": paths["source_path"],
                "remote_host": paths["remote_host"],
                "remote_path": paths["remote_path"],
                "preserved": [".env", "docker-compose.yml"],
            }
        },
    )
    append_event(run_id, "info", stage_name, out[-8000:])
    _set_stage(run_id, stage_name, "complete", "Micro-blog source synced to IONOS while preserving .env and docker-compose.yml.")


def _run_micro_blog_public_ui_build(run_id: str, stage_name: str) -> None:
    values = _micro_blog_public_ui_values(run_id)
    paths = _micro_blog_public_ui_paths(values)
    command = f"""
set -euo pipefail
cd {shlex.quote(paths["remote_path"])}
test -s .env
docker compose build {shlex.quote(paths["compose_service"])}
"""
    out = run_remote_command(host=paths["remote_host"], user=paths["remote_user"], command=command, timeout=900)
    append_event(run_id, "info", stage_name, out[-8000:])
    _set_stage(run_id, stage_name, "complete", f"Built Compose service {paths['compose_service']} from refreshed source.")


def _run_micro_blog_public_ui_up(run_id: str, stage_name: str) -> None:
    values = _micro_blog_public_ui_values(run_id)
    paths = _micro_blog_public_ui_paths(values)
    command = f"""
set -euo pipefail
cd {shlex.quote(paths["remote_path"])}
test -s .env
docker compose up -d {shlex.quote(paths["compose_service"])}
docker compose ps {shlex.quote(paths["compose_service"])}
"""
    out = run_remote_command(host=paths["remote_host"], user=paths["remote_user"], command=command, timeout=300)
    append_event(run_id, "info", stage_name, out[-6000:])
    _set_stage(run_id, stage_name, "complete", f"Recreated Compose service {paths['compose_service']} without touching unrelated services.")


def _run_micro_blog_public_ui_smoke(run_id: str, stage_name: str) -> None:
    values = _micro_blog_public_ui_values(run_id)
    smoke_urls = values.get("public_smoke_urls") or [
        "https://auzietek.com/",
        "https://www.blackknightcontroller.com/",
        "https://blackknightcontroller.com/blog?lane=blackknight",
        "https://linux-users.auzietek.com/blog",
        "https://retro-users.auzietek.com/blog",
    ]
    checks: list[dict] = []
    for url in [str(item).strip() for item in smoke_urls if str(item).strip()]:
        request = urllib.request.Request(url, headers={"User-Agent": "BlackKnightController-ui-deploy-smoke/1.0"})
        try:
            with urllib.request.urlopen(request, timeout=20) as response:  # noqa: S310 - operator-supplied smoke URLs
                body = response.read(250000).decode("utf-8", errors="replace")
                title_match = re.search(r"<title>(.*?)</title>", body, flags=re.I | re.S)
                title = re.sub(r"\s+", " ", title_match.group(1)).strip() if title_match else ""
                checks.append({"url": url, "status": response.status, "title": title})
        except Exception as exc:  # noqa: BLE001
            raise PipelineExecutionError(f"Public UI smoke failed for {url}: {exc}") from exc
    _store_run_extra(run_id, {"micro_blog_public_ui_smoke": checks})
    append_event(run_id, "info", stage_name, json.dumps(checks, indent=2, sort_keys=True)[-6000:])
    _set_stage(run_id, stage_name, "complete", f"Smoked {len(checks)} public UI URLs after runtime refresh.")


def _run_micro_blog_public_ui_fragment_note(run_id: str, stage_name: str) -> None:
    values = _micro_blog_public_ui_values(run_id)
    paths = _micro_blog_public_ui_paths(values)
    fragment = {
        "id": f"micro-blog-public-ui-refresh-{run_id}",
        "rating": "known-good",
        "contract": "Runtime/theme refresh preserves /svc/micro-blog/.env and docker-compose.yml, rebuilds only blog-ui, and smokes public hostnames.",
        "remote_host": paths["remote_host"],
        "remote_path": paths["remote_path"],
        "compose_service": paths["compose_service"],
    }
    _store_run_extra(run_id, {"micro_blog_public_ui_fragment": fragment})
    append_event(run_id, "info", stage_name, json.dumps(fragment, indent=2, sort_keys=True))
    _set_stage(run_id, stage_name, "complete", "Recorded public UI/runtime refresh guardrail fragment.")


def _run_micro_blog_public_backup(run_id: str, stage_name: str) -> None:
    values = _micro_blog_public_values(run_id)
    host, user = _micro_blog_public_host(values)
    app_path = _micro_blog_public_app_path(values)
    backup_root = str(values.get("backup_root") or "/srv/archive/backups").rstrip("/")
    command = f"""
set -euo pipefail
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
dest={shlex.quote(backup_root)}/micro-blog-content-promote-"$stamp"
mkdir -p "$dest"
cd {shlex.quote(app_path)}
cp docker-compose.yml "$dest/docker-compose.yml"
if [ -f .env ]; then
  awk -F= '/^[A-Za-z_][A-Za-z0-9_]*=/ {{print $1"=<redacted>"}}' .env > "$dest/env.keys.redacted"
fi
tar -czf "$dest/content-before.tgz" content 2>/dev/null || true
docker compose exec -T postgres pg_dump -U blog microblog > "$dest/postgres-microblog.sql"
sha256sum "$dest"/* > "$dest/SHA256SUMS"
echo "$dest"
ls -lh "$dest"
"""
    out = run_remote_command(host=host, user=user, command=command, timeout=900)
    backup_path = next((line.strip() for line in out.splitlines() if line.strip().startswith(backup_root + "/")), "")
    _store_run_extra(run_id, {"micro_blog_public_backup": {"host": host, "path": backup_path}})
    append_event(run_id, "info", stage_name, out[-6000:])
    _set_stage(run_id, stage_name, "complete", f"Production micro-blog backup captured{': ' + backup_path if backup_path else ''}.")


def _run_micro_blog_public_content_rsync(run_id: str, stage_name: str) -> None:
    values = _micro_blog_public_values(run_id)
    host, user = _micro_blog_public_host(values)
    build_host = str(values.get("build_host") or "10.20.0.233").strip()
    build_user = str(values.get("build_user") or "admin-deploy").strip()
    source_content_path = str(values.get("source_content_path") or "/var/lib/bkc-builds/src/micro-blog/content").rstrip("/")
    target_content_path = str(values.get("public_remote_content_path") or "/svc/micro-blog/content").rstrip("/")
    if not _bool_value(values.get("sync_content"), True):
        command = f"""
set -euo pipefail
test -d {shlex.quote(target_content_path)}
find {shlex.quote(target_content_path)} -type f | wc -l
"""
        out = run_remote_command(host=host, user=user, command=command, timeout=180)
        _store_run_extra(
            run_id,
            {
                "micro_blog_public_content_sync": {
                    "mode": "remote-content-already-staged",
                    "public_host": host,
                    "target_content_path": target_content_path,
                }
            },
        )
        append_event(run_id, "info", stage_name, out[-4000:])
        _set_stage(run_id, stage_name, "complete", "Content sync disabled for this run; using content already staged in the production Compose content volume.")
        return
    if not build_host or not build_user:
        raise PipelineExecutionError("Public content promotion requires build_host/build_user so rsync runs from lab-build.")
    command = f"""
set -euo pipefail
source_content_path={shlex.quote(source_content_path)}
target={shlex.quote(f"{user}@{host}:{target_content_path}/")}
test -d "$source_content_path"
find "$source_content_path" -type f | wc -l
rsync -az --itemize-changes --exclude __pycache__ --exclude .pytest_cache "$source_content_path"/ "$target"
"""
    out = run_remote_command(host=build_host, user=build_user, command=command, timeout=600)
    _store_run_extra(
        run_id,
        {
            "micro_blog_public_content_sync": {
                "mode": "rsync-update-no-delete",
                "build_host": build_host,
                "public_host": host,
                "source_content_path": source_content_path,
                "target_content_path": target_content_path,
            }
        },
    )
    append_event(run_id, "info", stage_name, out[-8000:])
    _set_stage(run_id, stage_name, "complete", "Approved micro-blog content synced to the production Compose content volume without deleting existing files.")


def _run_micro_blog_public_filesystem_sync_api(run_id: str, stage_name: str) -> None:
    values = _micro_blog_public_values(run_id)
    host, user = _micro_blog_public_host(values)
    app_path = _micro_blog_public_app_path(values)
    api_url = str(values.get("public_api_internal_url") or "http://127.0.0.1:18080").rstrip("/")
    endpoint = str(values.get("public_api_sync_endpoint") or "/admin/bootstrap/filesystem-sync")
    sync_mode = str(values.get("sync_mode") or "update").strip()
    status = str(values.get("status") or "published").strip()
    theme_variant = str(values.get("theme_variant") or "midnight").strip()
    content_subdir = str(values.get("filesystem_sync_subdir") or "").strip()
    payload_base = json.dumps(
        {
            "root_path": "/content",
            "content_subdir": content_subdir,
            "sync_mode": sync_mode,
            "status": status,
            "theme_variant": theme_variant,
        },
        sort_keys=True,
    )
    admin_command = f"""
set -euo pipefail
cd {shlex.quote(app_path)}
python3 -c "from pathlib import Path; print(next(line.split('=', 1)[1].strip().strip(chr(34)+chr(39)) for line in Path('.env').read_text().splitlines() if line.startswith('ADMIN_EMAIL=')))"
"""
    admin_email = run_remote_command(host=host, user=user, command=admin_command, timeout=60).strip()
    if not admin_email:
        raise PipelineExecutionError("ADMIN_EMAIL not found in production .env")
    payload = json.loads(payload_base)
    payload["admin_email"] = admin_email
    payload_json = json.dumps(payload, sort_keys=True)
    command = f"""
set -euo pipefail
cd {shlex.quote(app_path)}
curl -fsS -X POST {shlex.quote(api_url + endpoint)} \
  -H 'Content-Type: application/json' \
  --data {shlex.quote(payload_json)}
"""
    out = run_remote_command(host=host, user=user, command=command, timeout=300)
    _store_run_extra(run_id, {"micro_blog_public_filesystem_sync": {"api_url": api_url + endpoint, "sync_mode": sync_mode}})
    append_event(run_id, "info", stage_name, out[-6000:])
    _set_stage(run_id, stage_name, "complete", "Production filesystem sync API accepted the content promotion request.")


def _run_micro_blog_public_compose_proof(run_id: str, stage_name: str) -> None:
    values = _micro_blog_public_values(run_id)
    host, user = _micro_blog_public_host(values)
    app_path = _micro_blog_public_app_path(values)
    smoke_urls = values.get("public_smoke_urls") or []
    checked: list[dict] = []
    command = f"""
set -euo pipefail
cd {shlex.quote(app_path)}
docker compose ps --format '{{{{.Service}}}} {{{{.State}}}}' | sort
bad="$(docker compose ps --format '{{{{.Service}}}} {{{{.State}}}}' | awk '$2 != "running" {{print}}' || true)"
test -z "$bad"
"""
    out = run_remote_command(host=host, user=user, command=command, timeout=180)
    append_event(run_id, "info", stage_name, out[-4000:])
    for url in [str(item).strip() for item in smoke_urls if str(item).strip()]:
        with urllib.request.urlopen(url, timeout=20) as response:
            status_code = int(getattr(response, "status", 200))
            response.read(4096)
        if status_code >= 400:
            raise PipelineExecutionError(f"Public smoke URL returned HTTP {status_code}: {url}")
        checked.append({"url": url, "status": status_code})
    _store_run_extra(run_id, {"micro_blog_public_proof": checked})
    append_event(run_id, "info", stage_name, json.dumps(checked, sort_keys=True)[-6000:])
    _set_stage(run_id, stage_name, "complete", f"Production Compose services and {len(checked)} public URLs validated.")


def _run_micro_blog_public_fragment_note(run_id: str, stage_name: str) -> None:
    values = _micro_blog_public_values(run_id)
    payload = {
        "rating": "candidate-known-good",
        "contract": "Public micro-blog promotion is content-only for the IONOS Docker Compose runtime: backup, rsync /svc/micro-blog/content, call /admin/bootstrap/filesystem-sync, validate Compose and public URLs. Do not rebuild or restart runtime unless Pipeline 10 was deliberately selected.",
        "public_remote_host": values.get("public_remote_host") or "74.208.45.165",
        "public_remote_content_path": values.get("public_remote_content_path") or "/svc/micro-blog/content",
        "sync_mode": values.get("sync_mode") or "update",
    }
    _run_micro_blog_note(run_id, stage_name, "Recorded content-only public promotion fragment.", payload)


def _run_micro_blog_esxi_lab_canary_refresh(run_id: str, stage_name: str) -> None:
    pipeline_id = "micro-blog-esxi-lab-canary-refresh"
    pipeline, _, values = _folder_pipeline_context(pipeline_id, _run_request_inputs(run_id))
    pipeline_folder = _repo_pipeline_folder(pipeline)
    script_path = pipeline_folder / "scripts" / "refresh-ui-canary.sh"
    if not script_path.exists():
        raise PipelineExecutionError(f"Refresh helper script is missing: {script_path}")

    source_path_raw = str(values.get("source_path") or "/home/auzieman/Projects/micro-blog").strip()
    source_path = Path(source_path_raw).expanduser()
    source_commit = str(values.get("source_commit") or "").strip()
    tag_prefix = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(values.get("image_tag_prefix") or "lab-canary")).strip("-")
    if not tag_prefix:
        tag_prefix = "lab-canary"
    commit_part = re.sub(r"[^A-Za-z0-9_.-]+", "-", source_commit or "local").strip("-")[:24] or "local"
    run_part = re.sub(r"[^A-Za-z0-9_.-]+", "-", run_id).strip("-")[:12] or "run"
    image_tag = str(values.get("image_tag") or f"{tag_prefix}-{commit_part}-{run_part}").strip()

    content_overlay = str(values.get("content_overlay_dir") or "").strip()
    build_host = str(values.get("build_host") or "").strip()
    build_user = str(values.get("build_user") or "admin-deploy").strip()
    build_script_path = str(values.get("build_script_path") or f"/tmp/bkc-{pipeline_id}-{run_part}.sh").strip()

    append_event(
        run_id,
        "info",
        stage_name,
        json.dumps(
            {
                "pipeline_id": pipeline_id,
                "source_path": str(source_path),
                "source_branch": values.get("source_branch"),
                "source_commit": source_commit,
                "image_tag": image_tag,
                "edge_url": values.get("edge_url") or "http://swarm1.lab.auzietek.com:8091",
                "build_host": build_host or "local",
            },
            sort_keys=True,
        ),
    )

    if build_host:
        upload_remote_bytes(
            host=build_host,
            user=build_user,
            remote_path=build_script_path,
            content=script_path.read_bytes(),
            mode=0o700,
            timeout=60,
        )
        args = [build_script_path, source_path_raw, image_tag]
        if content_overlay:
            args.append(content_overlay)
        prefix = ""
        if str(values.get("target_password") or "").strip():
            prefix = f"BKC_ESXI_SWARM_PASSWORD={shlex.quote(str(values.get('target_password')))} "
        command = prefix + " ".join(shlex.quote(arg) for arg in args)
        try:
            output = run_remote_command(
                host=build_host,
                user=build_user,
                command=command,
                timeout=int(values.get("script_timeout_seconds") or 2400),
            )
        except Exception as exc:  # noqa: BLE001
            raise PipelineExecutionError(f"micro-blog ESXi canary refresh failed on build host {build_host}: {exc}") from exc
        returncode = 0
    else:
        source_path = source_path.resolve()
        if not source_path.exists():
            raise PipelineExecutionError(f"micro-blog source path is missing: {source_path}")
        command = [str(script_path), str(source_path), image_tag]
        if content_overlay:
            overlay_path = Path(content_overlay).expanduser().resolve()
            if not overlay_path.exists():
                raise PipelineExecutionError(f"Content overlay path is missing: {overlay_path}")
            command.append(str(overlay_path))

        env = os.environ.copy()
        if str(values.get("target_password") or "").strip() and not env.get("BKC_ESXI_SWARM_PASSWORD"):
            env["BKC_ESXI_SWARM_PASSWORD"] = str(values.get("target_password"))
        try:
            completed = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=int(values.get("script_timeout_seconds") or 2400),
                cwd=str(source_path),
                env=env,
            )
        except subprocess.TimeoutExpired as exc:
            raise PipelineExecutionError(f"micro-blog ESXi canary refresh timed out after {exc.timeout}s") from exc
        except Exception as exc:  # noqa: BLE001
            raise PipelineExecutionError(f"micro-blog ESXi canary refresh failed to start: {exc}") from exc

        output = "\n".join(part for part in [completed.stdout, completed.stderr] if part).strip()
        returncode = completed.returncode

    if output:
        append_event(run_id, "info", stage_name, output[-12000:])
    _store_run_extra(
        run_id,
        {
            "micro_blog_esxi_lab_canary_refresh": {
                "source_path": source_path_raw,
                "source_commit": source_commit,
                "image_tag": image_tag,
                "build_host": build_host or "local",
                "returncode": returncode,
            }
        },
    )
    _set_stage(run_id, stage_name, "complete", f"Micro-blog ESXi lab canary refreshed and validated with image tag {image_tag}.")


def _run_trixie_template_upload(run_id: str, stage_name: str, template_name: str, target_key: str, mode: int = 0o644) -> None:
    pipeline, _, values = _folder_pipeline_context("ns1-trixie-pxe-smoke")
    template_path = _repo_pipeline_folder(pipeline) / "templates" / template_name
    if not template_path.exists():
        raise PipelineExecutionError(f"Template not found: {template_path}")
    target_path = str(values.get(target_key) or "").strip()
    if not target_path.startswith("/srv/"):
        raise PipelineExecutionError(f"Refusing to write template outside /srv: {target_path}")
    render_values = dict(values)
    if template_name == "trixie-smoke-preseed.cfg.tpl":
        password = str(values.get("target_install_password") or "").strip() or f"Bkc-{secrets.token_urlsafe(18)}A1!"
        if not str(render_values.get("target_user_password_crypted") or "").strip():
            render_values["target_user_password_crypted"] = crypt.crypt(password, crypt.mksalt(crypt.METHOD_SHA512))
        if not str(render_values.get("target_root_password_crypted") or "").strip():
            render_values["target_root_password_crypted"] = render_values["target_user_password_crypted"]
        if not str(render_values.get("target_ssh_authorized_key") or "").strip():
            integrations = load_integrations()
            ssh = integrations["ssh"]
            render_values["target_ssh_authorized_key"] = read_key_pair(
                ssh["private_key_path"],
                ssh["public_key_path"],
            ).get("public_key", "")
        if not str(render_values.get("target_ssh_authorized_key") or "").startswith("ssh-"):
            raise PipelineExecutionError("BKC SSH public key is missing or invalid.")
        credentials_path = f"/root/bkc-vm{int(values.get('target_vmid') or 132)}-trixie-credentials.txt"
        credentials = (
            f"host={values.get('target_install_hostname')}.{values.get('target_install_domain')}\n"
            f"user={values.get('target_install_user')}\n"
            f"password={password}\n"
        )
        upload_remote_bytes(
            host=str(values.get("target_host") or "").strip(),
            user="root",
            remote_path=credentials_path,
            content=credentials.encode("utf-8"),
            mode=0o600,
            timeout=60,
        )
        append_event(run_id, "info", stage_name, json.dumps({"credentials_path": credentials_path, "user": values.get("target_install_user")}, sort_keys=True))

    content = _render_pipeline_template(template_path.read_text(encoding="utf-8"), render_values).encode("utf-8")
    parent = str(Path(target_path).parent)
    _run_ns1_command(values, f"mkdir -p {shlex.quote(parent)}", timeout=60)
    upload_remote_bytes(
        host=str(values.get("target_host") or "").strip(),
        user="root",
        remote_path=target_path,
        content=content,
        mode=mode,
        timeout=60,
    )
    output = _run_ns1_command(values, f"test -s {shlex.quote(target_path)} && ls -l {shlex.quote(target_path)}", timeout=60)
    _set_stage(run_id, stage_name, "complete", f"Rendered {template_name} to ns1.")
    append_event(run_id, "info", stage_name, output[-1200:] if output else target_path)


def _run_trixie_vm_prepare(run_id: str, stage_name: str) -> None:
    _, _, values = _folder_pipeline_context("ns1-trixie-pxe-smoke")
    vmid = int(values.get("target_vmid") or 132)
    name = shlex.quote(str(values.get("target_vm_name") or f"trixie-smoke-{vmid}"))
    memory = int(values.get("target_vm_memory_mb") or 4096)
    cores = int(values.get("target_vm_cores") or 2)
    disk_gb = int(values.get("target_vm_disk_gb") or 32)
    storage = shlex.quote(str(values.get("target_vm_storage") or "local-lvm"))
    boot_bridge = shlex.quote(str(values.get("target_vm_boot_bridge") or "vmbr1"))
    management_bridge = shlex.quote(str(values.get("target_vm_management_bridge") or "vmbr0"))
    command = (
        "set -e; "
        f"if qm config {vmid} >/dev/null 2>&1; then "
        f"qm status {vmid} | grep -q running && qm stop {vmid} --timeout 30 || true; "
        f"qm destroy {vmid} --purge 1 || qm destroy {vmid}; "
        "fi; "
        f"qm create {vmid} --name {name} --memory {memory} --cores {cores} --sockets 1 "
        "--numa 0 --ostype l26 --scsihw virtio-scsi-single --agent enabled=1 --serial0 socket "
        f"--net0 virtio,bridge={boot_bridge},firewall=1 "
        f"--net1 virtio,bridge={management_bridge},firewall=1; "
        f"qm set {vmid} --scsi0 {storage}:{disk_gb},iothread=1; "
        f"qm set {vmid} --boot order=net0\\;scsi0; "
        f"cfg=$(qm config {vmid}); printf \"%s\\n\" \"$cfg\"; "
        "printf \"%s\\n\" \"$cfg\" | grep -F \"net0:\" >/dev/null; "
        "printf \"%s\\n\" \"$cfg\" | grep -F \"net1:\" >/dev/null; "
        "printf \"%s\\n\" \"$cfg\" | grep -F \"boot: order=net0;scsi0\" >/dev/null; "
        f"echo trixie-vm{vmid}-prepared"
    )
    output = _run_proxmox_ssh_command(command, timeout=240)
    _set_stage(run_id, stage_name, "complete", f"VMID {vmid} exists with PXE and management NICs.")
    append_event(run_id, "info", stage_name, output[-1800:] if output else f"vm{vmid} prepared")


def _run_trixie_vm_boot(run_id: str, stage_name: str) -> None:
    _, _, values = _folder_pipeline_context("ns1-trixie-pxe-smoke")
    vmid = int(values.get("target_vmid") or 132)
    command = (
        "set -e; "
        f"qm set {vmid} --boot order=net0\\;scsi0; "
        f"qm status {vmid} | grep -q running || qm start {vmid}; "
        "sleep 5; "
        f"qm set {vmid} --boot order=scsi0\\;net0; "
        f"qm status {vmid}; "
        f"qm config {vmid} | grep -F \"boot: order=scsi0;net0\" >/dev/null; "
        f"echo trixie-vm{vmid}-pxe-boot-requested-disk-first-next"
    )
    output = _run_proxmox_ssh_command(command, timeout=120)
    guard = (
        "#!ipxe\n"
        f"# BKC guard: VMID {vmid} already entered Debian installer. Boot local disk on accidental PXE retry.\n"
        "sanboot --no-describe --drive 0x80 || exit\n"
    )
    ipxe_script = str(values.get("ipxe_script_path") or "").strip()
    if ipxe_script:
        upload_remote_bytes(
            host=str(values.get("target_host") or "").strip(),
            user="root",
            remote_path=ipxe_script,
            content=guard.encode("utf-8"),
            mode=0o644,
            timeout=60,
        )
        append_event(run_id, "info", stage_name, f"Installed local-disk PXE guard at {ipxe_script}.")
    _set_stage(run_id, stage_name, "complete", f"VMID {vmid} PXE boot requested; next boot is disk-first.")
    append_event(run_id, "info", stage_name, output[-1200:] if output else f"vm{vmid} booted")


def _run_trixie_vm_observe(run_id: str, stage_name: str) -> None:
    _, _, values = _folder_pipeline_context("ns1-trixie-pxe-smoke")
    vmid = int(values.get("target_vmid") or 132)
    output = _run_proxmox_ssh_command(f"qm status {vmid}; qm config {vmid} | sed -n '1,80p'", timeout=60)
    _set_stage(run_id, stage_name, "complete", f"VMID {vmid} is observable in Proxmox.")
    append_event(run_id, "info", stage_name, output[-1600:] if output else f"vm{vmid} observable")


def _run_windows10_verify_iso(run_id: str, stage_name: str) -> None:
    _, _, values = _folder_pipeline_context("windows10-reference-discover")
    iso_path = Path(str(values.get("windows_iso_path") or "")).expanduser()
    if iso_path.exists():
        digest = hashlib.sha256()
        with iso_path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        summary = {
            "source": "local-path",
            "path": str(iso_path),
            "name": values.get("windows_iso_name"),
            "size": iso_path.stat().st_size,
            "sha256": digest.hexdigest(),
        }
        _set_stage(run_id, stage_name, "complete", "Windows ISO exists locally and was hashed.")
        append_event(run_id, "info", stage_name, json.dumps(summary, sort_keys=True))
        return

    proxmox_volume = str(values.get("proxmox_iso_volume") or "").strip()
    if not proxmox_volume:
        raise PipelineExecutionError(f"Windows ISO is missing: {iso_path}")

    storage = proxmox_volume.split(":", 1)[0] if ":" in proxmox_volume else "local"
    command = (
        f"pvesm list {shlex.quote(storage)} --content iso | "
        f"awk -v vol={shlex.quote(proxmox_volume)} '$1 == vol {{found=1; print}} END {{exit found ? 0 : 1}}'"
    )
    output = _run_proxmox_ssh_command(command, timeout=60).strip()
    summary = {
        "source": "proxmox-storage",
        "local_path": str(iso_path),
        "name": values.get("windows_iso_name"),
        "proxmox_volume": proxmox_volume,
        "proxmox_inventory": output,
    }
    _set_stage(run_id, stage_name, "complete", "Windows ISO exists in Proxmox storage.")
    append_event(run_id, "info", stage_name, json.dumps(summary, sort_keys=True))


def _run_windows10_inspect_vm(run_id: str, stage_name: str) -> None:
    _, _, values = _folder_pipeline_context("windows10-reference-discover")
    vmid = int(values.get("reference_vmid") or 113)
    output = _run_proxmox_ssh_command(f"qm status {vmid}; qm config {vmid} | sed -n '1,120p'", timeout=60)
    _set_stage(run_id, stage_name, "complete", f"VMID {vmid} Proxmox shape inspected.")
    append_event(run_id, "info", stage_name, output[-2000:] if output else f"vm{vmid} inspected")


def _windows_ssh_command(values: dict, command: str, *, timeout: int = 60) -> str:
    host = str(values.get("reference_vm_ip") or "").strip()
    user = str(values.get("reference_vm_user") or "depadmin").strip()
    return run_remote_command(
        host=host,
        user=user,
        command=command,
        timeout=timeout,
    )


def _run_windows10_validate_openssh(run_id: str, stage_name: str) -> None:
    _, _, values = _folder_pipeline_context("windows10-reference-discover")
    output = _windows_ssh_command(values, "hostname", timeout=60)
    _set_stage(run_id, stage_name, "complete", "Windows OpenSSH key access works from BKC.")
    append_event(run_id, "info", stage_name, output[-1200:] if output else "windows ssh ready")


def _run_windows10_stage_artifacts(run_id: str, stage_name: str) -> None:
    pipeline, _, values = _folder_pipeline_context("windows10-reference-discover")
    folder = _repo_pipeline_folder(pipeline)
    artifacts = {}
    for key in ("bootstrap_script", "diet_script"):
        rel_path = str(values.get(key) or "").strip()
        path = folder / rel_path
        if not path.exists():
            raise PipelineExecutionError(f"Windows artifact is missing: {path}")
        artifacts[key] = {"path": str(path), "size": path.stat().st_size}
    _set_stage(run_id, stage_name, "complete", "Windows firstboot artifacts are present.")
    append_event(run_id, "info", stage_name, json.dumps(artifacts, sort_keys=True))


def _windows10_pxe_context() -> tuple[dict, dict, dict]:
    return _folder_pipeline_context("windows10-pxe-smoke")


def _run_windows10_pxe_prereqs(run_id: str, stage_name: str) -> None:
    _, _, values = _windows10_pxe_context()
    interface = shlex.quote(str(values.get("provisioning_interface") or ""))
    cidr = shlex.quote(str(values.get("provisioning_network_cidr") or ""))
    command = (
        "set -e; "
        f"ip link show {interface} >/dev/null; "
        f"ip -o addr show dev {interface} | grep -F {shlex.quote(str(values.get('pxe_http_host') or ''))} >/dev/null; "
        f"case {cidr} in 10.*/*|172.16.*/*|172.17.*/*|172.18.*/*|172.19.*/*|172.20.*/*|172.21.*/*|172.22.*/*|172.23.*/*|172.24.*/*|172.25.*/*|172.26.*/*|172.27.*/*|172.28.*/*|172.29.*/*|172.30.*/*|172.31.*/*) ;; *) exit 12 ;; esac; "
        "systemctl is-active --quiet dhcpd; "
        "systemctl is-active --quiet nginx; "
        "systemctl is-active --quiet tftp.socket; "
        "echo windows10-pxe-prereqs-ok"
    )
    output = _run_ns1_command(values, command, timeout=60)
    _set_stage(run_id, stage_name, "complete", "ns1 Windows PXE prerequisites are present.")
    append_event(run_id, "info", stage_name, output[-1200:] if output else "windows10-pxe-prereqs-ok")


def _run_windows10_pxe_verify_iso(run_id: str, stage_name: str) -> None:
    _, _, values = _windows10_pxe_context()
    proxmox_volume = str(values.get("proxmox_iso_volume") or "").strip()
    if not proxmox_volume:
        raise PipelineExecutionError("Windows PXE ISO volume is missing.")
    storage = proxmox_volume.split(":", 1)[0] if ":" in proxmox_volume else "local"
    command = (
        f"pvesm list {shlex.quote(storage)} --content iso | "
        f"awk -v vol={shlex.quote(proxmox_volume)} '$1 == vol {{found=1; print}} END {{exit found ? 0 : 1}}'"
    )
    output = _run_proxmox_ssh_command(command, timeout=60).strip()
    summary = {
        "source": "proxmox-storage",
        "name": values.get("windows_iso_name"),
        "proxmox_volume": proxmox_volume,
        "proxmox_inventory": output,
    }
    _set_stage(run_id, stage_name, "complete", "Windows ISO exists in Proxmox storage.")
    append_event(run_id, "info", stage_name, json.dumps(summary, sort_keys=True))


def _run_windows10_wimboot_fetch(run_id: str, stage_name: str) -> None:
    _, _, values = _windows10_pxe_context()
    target = str(values.get("wimboot_path") or "").strip()
    if not target.startswith("/srv/"):
        raise PipelineExecutionError(f"Refusing to write wimboot outside /srv: {target}")
    command = (
        "set -e; "
        f"mkdir -p {shlex.quote(str(Path(target).parent))}; "
        f"if ! test -s {shlex.quote(target)}; then "
        f"curl -fsSL -o {shlex.quote(target)} {shlex.quote(str(values.get('wimboot_url') or ''))}; "
        "fi; "
        f"chmod 0644 {shlex.quote(target)}; "
        f"test -s {shlex.quote(target)}; "
        f"ls -lh {shlex.quote(target)}"
    )
    output = _run_ns1_command(values, command, timeout=180)
    _set_stage(run_id, stage_name, "complete", "wimboot is cached on ns1.")
    append_event(run_id, "info", stage_name, output[-1200:] if output else target)


def _proxmox_iso_file_path(volume: str) -> str:
    storage, _, image = volume.partition(":")
    if storage != "local" or not image.startswith("iso/"):
        raise PipelineExecutionError(f"Unsupported Windows ISO volume for file extraction: {volume}")
    return "/var/lib/vz/template/" + image


def _run_windows10_winpe_stage(run_id: str, stage_name: str) -> None:
    _, _, values = _windows10_pxe_context()
    proxmox_volume = str(values.get("proxmox_iso_volume") or "").strip()
    iso_path = _proxmox_iso_file_path(proxmox_volume)
    target_root = str(values.get("windows_netboot_root") or "").strip()
    media_root = str(values.get("windows_media_root") or "").strip()
    if not target_root.startswith("/srv/"):
        raise PipelineExecutionError(f"Refusing to stage WinPE files outside /srv: {target_root}")
    if not media_root.startswith("/srv/"):
        raise PipelineExecutionError(f"Refusing to stage Windows media outside /srv: {media_root}")

    reuse_command = (
        f"test -s {shlex.quote(media_root + '/setup.exe')} && "
        f"(test -s {shlex.quote(media_root + '/sources/install.wim')} || "
        f"test -s {shlex.quote(media_root + '/sources/install.esd')})"
    )
    media_reused = False
    try:
        _run_ns1_command(values, reuse_command, timeout=60)
        media_reused = True
    except Exception:
        media_reused = False

    host, user, password = _proxmox_ssh_target(load_proxmox_config())
    copied_files = 0
    copied_bytes = 0
    mount_dir = f"/mnt/bkc-winiso-{run_id[:8]}"
    if not media_reused:
        integrations = load_integrations()
        ssh = integrations["ssh"]
        key_info = read_key_pair(ssh["private_key_path"], ssh["public_key_path"])
        private_key_path = str(key_info["private_key_path"])
        if not private_key_path or not Path(private_key_path).exists():
            raise PipelineExecutionError("BKC SSH private key is missing; cannot stream Windows media to ns1.")
        proxmox_key_path = f"/var/tmp/bkc-ns1-copy-{run_id[:8]}"
        upload_remote_file(
            host=host,
            user=user,
            password=password,
            remote_path=proxmox_key_path,
            local_path=private_key_path,
            mode=0o600,
            timeout=60,
        )
        ns1_host = str(values.get("target_host") or "").strip()
        transfer_command = (
            "set -e; "
            f"umount {shlex.quote(mount_dir)} >/dev/null 2>&1 || true; "
            f"rm -rf {shlex.quote(mount_dir)}; mkdir -p {shlex.quote(mount_dir)}; "
            f"mount -o loop,ro {shlex.quote(iso_path)} {shlex.quote(mount_dir)}; "
            f"test -s {shlex.quote(mount_dir + '/setup.exe')}; "
            f"files=$(find {shlex.quote(mount_dir)} -type f | wc -l); "
            f"bytes=$(du -sb {shlex.quote(mount_dir)} | awk '{{print $1}}'); "
            f"ssh -i {shlex.quote(proxmox_key_path)} -o BatchMode=yes -o StrictHostKeyChecking=no root@{shlex.quote(ns1_host)} "
            f"{shlex.quote('rm -rf ' + shlex.quote(media_root) + ' && mkdir -p ' + shlex.quote(media_root))}; "
            f"tar -C {shlex.quote(mount_dir)} -cf - . | "
            f"ssh -i {shlex.quote(proxmox_key_path)} -o BatchMode=yes -o StrictHostKeyChecking=no root@{shlex.quote(ns1_host)} "
            f"{shlex.quote('tar -C ' + shlex.quote(media_root) + ' -xf -')}; "
            f"ssh -i {shlex.quote(proxmox_key_path)} -o BatchMode=yes -o StrictHostKeyChecking=no root@{shlex.quote(ns1_host)} "
            f"{shlex.quote('test -s ' + shlex.quote(media_root + '/setup.exe') + ' && (test -s ' + shlex.quote(media_root + '/sources/install.wim') + ' || test -s ' + shlex.quote(media_root + '/sources/install.esd') + ')')}; "
            "echo copied_files=$files copied_bytes=$bytes"
        )
        try:
            transfer_output = run_remote_command(
                host=host,
                user=user,
                password=password,
                command=transfer_command,
                timeout=1800,
            )
            match_files = re.search(r"copied_files=(\d+)", transfer_output)
            match_bytes = re.search(r"copied_bytes=(\d+)", transfer_output)
            copied_files = int(match_files.group(1)) if match_files else 0
            copied_bytes = int(match_bytes.group(1)) if match_bytes else 0
        finally:
            run_remote_command(
                host=host,
                user=user,
                password=password,
                command=(
                    f"rm -f {shlex.quote(proxmox_key_path)}; "
                    f"umount {shlex.quote(mount_dir)} >/dev/null 2>&1 || true; "
                    f"rmdir {shlex.quote(mount_dir)} >/dev/null 2>&1 || true"
                ),
                timeout=60,
            )

    derive_command = (
        "set -e; "
        f"mkdir -p {shlex.quote(target_root)}; "
        f"cp {shlex.quote(media_root + '/bootmgr')} {shlex.quote(target_root + '/bootmgr')}; "
        f"cp {shlex.quote(media_root + '/boot/bcd')} {shlex.quote(target_root + '/BCD')}; "
        f"cp {shlex.quote(media_root + '/boot/boot.sdi')} {shlex.quote(target_root + '/boot.sdi')}; "
        f"cp {shlex.quote(media_root + '/sources/boot.wim')} {shlex.quote(target_root + '/boot.wim')}; "
        f"test -s {shlex.quote(media_root + '/setup.exe')}; "
        f"(test -s {shlex.quote(media_root + '/sources/install.wim')} || test -s {shlex.quote(media_root + '/sources/install.esd')}); "
        f"ls -lh {shlex.quote(target_root)}; "
        f"ls -lh {shlex.quote(media_root + '/setup.exe')} {shlex.quote(media_root + '/sources/install.wim')} {shlex.quote(media_root + '/sources/install.esd')} 2>/dev/null || true"
    )
    verify = _run_ns1_command(values, derive_command, timeout=120)
    _set_stage(run_id, stage_name, "complete", "Windows install media and WinPE boot files are staged on ns1.")
    append_event(
        run_id,
        "info",
        stage_name,
        json.dumps(
            {
                "copied_bytes": copied_bytes,
                "copied_files": copied_files,
                "media_reused": media_reused,
                "media_root": media_root,
                "ns1": verify[-1600:],
            },
            sort_keys=True,
        ),
    )


def _windows10_pxe_upload_template(
    run_id: str,
    stage_name: str,
    template_name: str,
    target_key: str,
    values: dict | None = None,
    *,
    mode: int = 0o644,
) -> None:
    pipeline, _, base_values = _windows10_pxe_context()
    render_values = dict(base_values)
    if values:
        render_values.update(values)
    template_path = _repo_pipeline_folder(pipeline) / "templates" / template_name
    if not template_path.exists():
        raise PipelineExecutionError(f"Template not found: {template_path}")
    target_path = str(render_values.get(target_key) or "").strip()
    if not target_path.startswith("/srv/") and not target_path.startswith("/etc/dhcp/"):
        raise PipelineExecutionError(f"Refusing to write template outside approved paths: {target_path}")
    content = _render_pipeline_template(template_path.read_text(encoding="utf-8"), render_values).encode("utf-8")
    _run_ns1_command(render_values, f"mkdir -p {shlex.quote(str(Path(target_path).parent))}", timeout=60)
    upload_remote_bytes(
        host=str(render_values.get("target_host") or "").strip(),
        user="root",
        remote_path=target_path,
        content=content,
        mode=mode,
        timeout=60,
    )
    output = _run_ns1_command(render_values, f"test -s {shlex.quote(target_path)} && ls -l {shlex.quote(target_path)}", timeout=60)
    _set_stage(run_id, stage_name, "complete", f"Rendered {template_name} to ns1.")
    append_event(run_id, "info", stage_name, output[-1200:] if output else target_path)


def _run_windows10_unattend_render(run_id: str, stage_name: str) -> None:
    _, _, values = _windows10_pxe_context()
    password = str(values.get("target_admin_password") or "").strip() or f"Bkc-{secrets.token_urlsafe(18)}A1!"
    public_key = str(values.get("target_ssh_authorized_key") or "").strip()
    if not public_key:
        integrations = load_integrations()
        ssh = integrations["ssh"]
        public_key = read_key_pair(ssh["private_key_path"], ssh["public_key_path"]).get("public_key", "")
    if not public_key.startswith("ssh-"):
        raise PipelineExecutionError("BKC SSH public key is missing or invalid.")
    render_values = {
        **values,
        "target_admin_password": password,
        "target_ssh_authorized_key": public_key,
    }
    _windows10_pxe_upload_template(
        run_id,
        stage_name,
        "Autounattend.xml.tpl",
        "windows_unattend_path",
        render_values,
        mode=0o644,
    )
    _windows10_pxe_upload_template(
        run_id,
        stage_name,
        "bkc-firstboot.ps1.tpl",
        "windows_firstboot_path",
        render_values,
        mode=0o644,
    )
    for template_name, value_key in (
        ("winpeshl.ini.tpl", "windows_winpe_shell_path"),
        ("startnet.cmd.tpl", "windows_winpe_startnet_path"),
        ("bkc-winpe-setup.cmd.tpl", "windows_winpe_setup_path"),
    ):
        _windows10_pxe_upload_template(
            run_id,
            stage_name,
            template_name,
            value_key,
            render_values,
            mode=0o644,
        )
    oem_script_root = f"{values.get('windows_media_root')}/sources/$OEM$/$$/Setup/Scripts"
    _run_ns1_command(render_values, f"mkdir -p {shlex.quote(oem_script_root)}", timeout=60)
    for template_name, output_name in (
        ("bkc-firstboot.ps1.tpl", "bkc-firstboot.ps1"),
        ("SetupComplete.cmd.tpl", "SetupComplete.cmd"),
    ):
        pipeline, _, _ = _windows10_pxe_context()
        template_path = _repo_pipeline_folder(pipeline) / "templates" / template_name
        content = _render_pipeline_template(template_path.read_text(encoding="utf-8"), render_values).encode("utf-8")
        upload_remote_bytes(
            host=str(values.get("target_host") or "").strip(),
            user="root",
            remote_path=f"{oem_script_root}/{output_name}",
            content=content,
            mode=0o644,
            timeout=60,
        )
    append_event(run_id, "info", stage_name, f"Staged Windows SetupComplete assets under {oem_script_root}.")
    credentials_path = f"/root/bkc-vm{int(values.get('target_vmid') or 136)}-credentials.txt"
    credentials = (
        f"host={values.get('target_install_hostname')}.{values.get('target_install_domain')}\n"
        f"user={values.get('target_admin_user')}\n"
        f"password={password}\n"
    )
    upload_remote_bytes(
        host=str(values.get("target_host") or "").strip(),
        user="root",
        remote_path=credentials_path,
        content=credentials.encode("utf-8"),
        mode=0o600,
        timeout=60,
    )
    _set_stage(run_id, stage_name, "complete", "Windows unattended assets rendered and credentials staged on ns1.")
    append_event(run_id, "info", stage_name, json.dumps({"credentials_path": credentials_path, "user": values.get("target_admin_user")}, sort_keys=True))


def _run_windows10_dhcp_route_render(run_id: str, stage_name: str) -> None:
    _, _, values = _windows10_pxe_context()
    _windows10_pxe_upload_template(
        run_id,
        stage_name,
        "bkc-provisioning-windows-route.conf.tpl",
        "dhcp_fragment_path",
        values,
        mode=0o644,
    )
    output = _run_ns1_command(
        values,
        "dhcpd -t -cf /etc/dhcp/dhcpd.conf >/dev/null && systemctl restart dhcpd && systemctl is-active dhcpd",
        timeout=60,
    )
    append_event(run_id, "info", stage_name, output[-1200:] if output else "dhcpd active")


def _run_windows10_ipxe_render(run_id: str, stage_name: str) -> None:
    _, _, values = _windows10_pxe_context()
    _windows10_pxe_upload_template(run_id, stage_name, "windows10.ipxe.tpl", "windows_ipxe_script_path", values)


def _run_windows10_media_share(run_id: str, stage_name: str) -> None:
    _, _, values = _windows10_pxe_context()
    media_root = str(values.get("windows_media_root") or "").strip()
    share = str(values.get("windows_media_share") or "win10media").strip()
    conf_path = str(values.get("samba_media_conf_path") or "/etc/samba/smb.conf.d/bkc-windows-media.conf").strip()
    if not media_root.startswith("/srv/"):
        raise PipelineExecutionError(f"Refusing to share Windows media outside /srv: {media_root}")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", share):
        raise PipelineExecutionError(f"Invalid Samba share name: {share}")
    if not conf_path.startswith("/etc/samba/"):
        raise PipelineExecutionError(f"Refusing to write Samba config outside /etc/samba: {conf_path}")

    _run_ns1_command(values, f"mkdir -p {shlex.quote(str(Path(conf_path).parent))}", timeout=60)
    share_conf = (
        f"[{share}]\n"
        f"    path = {media_root}\n"
        "    read only = yes\n"
        "    guest ok = yes\n"
        "    guest only = yes\n"
        "    browsable = yes\n"
        "    force user = nobody\n"
    )
    upload_remote_bytes(
        host=str(values.get("target_host") or "").strip(),
        user="root",
        remote_path=conf_path,
        content=share_conf.encode("utf-8"),
        mode=0o644,
        timeout=60,
    )
    command = (
        "set -e; "
        "grep -q '^\\s*map to guest = Bad User' /etc/samba/smb.conf || "
        "sed -i '/^\\[global\\]/a\\    map to guest = Bad User' /etc/samba/smb.conf; "
        "sed -i '/^# BKC WINDOWS MEDIA BEGIN$/,/^# BKC WINDOWS MEDIA END$/d' /etc/samba/smb.conf; "
        "printf '\\n# BKC WINDOWS MEDIA BEGIN\\n' >> /etc/samba/smb.conf; "
        f"cat {shlex.quote(conf_path)} >> /etc/samba/smb.conf; "
        "printf '# BKC WINDOWS MEDIA END\\n' >> /etc/samba/smb.conf; "
        f"test -s {shlex.quote(media_root + '/setup.exe')}; "
        f"(test -s {shlex.quote(media_root + '/sources/install.wim')} || test -s {shlex.quote(media_root + '/sources/install.esd')}); "
        f"chcon -t samba_share_t {shlex.quote(media_root)} {shlex.quote(media_root + '/setup.exe')} {shlex.quote(media_root + '/sources')} >/dev/null 2>&1 || true; "
        "timeout 20s firewall-cmd --add-service=samba >/dev/null 2>&1 || true; "
        "timeout 20s firewall-cmd --add-service=samba --permanent >/dev/null 2>&1 || true; "
        "timeout 30s systemctl enable --now smb >/dev/null; "
        "timeout 30s systemctl restart smb; "
        "timeout 20s testparm -s >/tmp/bkc-testparm.out; "
        "systemctl is-active smb; "
        f"timeout 20s smbclient -N -L //127.0.0.1 | grep -F {shlex.quote(share)} >/dev/null; "
        f"timeout 20s smbclient -N //127.0.0.1/{shlex.quote(share)} -c 'ls setup.exe' >/dev/null; "
        "cat /tmp/bkc-testparm.out | sed -n '1,120p'"
    )
    output = _run_ns1_command(values, command, timeout=120)
    _set_stage(run_id, stage_name, "complete", f"Windows media share //{values.get('pxe_http_host')}/{share} is ready.")
    append_event(run_id, "info", stage_name, output[-1800:] if output else f"{share} ready")


def _run_windows10_vm_prepare(run_id: str, stage_name: str) -> None:
    _, _, values = _windows10_pxe_context()
    vmid = int(values.get("target_vmid") or 136)
    name = str(values.get("target_vm_name") or f"win10-pxe-smoke-{vmid}")
    q_name = shlex.quote(name)
    memory = int(values.get("target_vm_memory_mb") or 8192)
    cores = int(values.get("target_vm_cores") or 4)
    disk_gb = int(values.get("target_vm_disk_gb") or 64)
    storage = shlex.quote(str(values.get("target_vm_storage") or "local-lvm"))
    disk_bus = str(values.get("target_vm_disk_bus") or "sata0").strip()
    if disk_bus != "sata0":
        raise PipelineExecutionError(f"Windows PXE smoke currently supports only sata0 disk bus, got {disk_bus}.")
    boot_bridge = shlex.quote(str(values.get("target_vm_boot_bridge") or "vmbr20"))
    management_bridge = shlex.quote(str(values.get("target_vm_management_bridge") or "vmbr0"))
    mac = str(values.get("target_vm_mac") or "").strip()
    if not re.fullmatch(r"[0-9A-Fa-f]{2}(:[0-9A-Fa-f]{2}){5}", mac):
        raise PipelineExecutionError(f"Invalid Windows PXE target MAC: {mac}")
    q_mac = shlex.quote(mac.upper())
    command = (
        "set -e; "
        f"if qm config {vmid} >/dev/null 2>&1; then "
        f"current=$(qm config {vmid} | awk -F': ' '$1 == \"name\" {{print $2}}'); "
        f"test \"$current\" = {shlex.quote(name)} || (echo \"Refusing to replace VMID {vmid} named $current\" >&2; exit 22); "
        f"qm status {vmid} | grep -q running && qm stop {vmid} --timeout 30 || true; "
        f"qm destroy {vmid} --purge 1 || qm destroy {vmid}; "
        "fi; "
        f"qm create {vmid} --name {q_name} --memory {memory} --cores {cores} --sockets 1 "
        "--numa 0 --ostype win10 --bios seabios "
        f"--net0 e1000={q_mac},bridge={boot_bridge},firewall=1 "
        f"--net1 e1000,bridge={management_bridge},firewall=1; "
        f"qm set {vmid} --{disk_bus} {storage}:{disk_gb}; "
        f"qm set {vmid} --boot order=net0\\;{disk_bus}; "
        f"cfg=$(qm config {vmid}); printf \"%s\\n\" \"$cfg\"; "
        "printf \"%s\\n\" \"$cfg\" | grep -F \"net0:\" >/dev/null; "
        "printf \"%s\\n\" \"$cfg\" | grep -F \"net1:\" >/dev/null; "
        f"printf \"%s\\n\" \"$cfg\" | grep -F \"{disk_bus}:\" >/dev/null; "
        f"printf \"%s\\n\" \"$cfg\" | grep -F \"boot: order=net0;{disk_bus}\" >/dev/null; "
        f"echo windows10-vm{vmid}-prepared"
    )
    output = _run_proxmox_ssh_command(command, timeout=240)
    _set_stage(run_id, stage_name, "complete", f"VMID {vmid} exists with Windows PXE and management NICs.")
    append_event(run_id, "info", stage_name, output[-1800:] if output else f"vm{vmid} prepared")


def _run_windows10_vm_boot(run_id: str, stage_name: str) -> None:
    _, _, values = _windows10_pxe_context()
    vmid = int(values.get("target_vmid") or 136)
    disk_bus = str(values.get("target_vm_disk_bus") or "sata0").strip()
    if disk_bus != "sata0":
        raise PipelineExecutionError(f"Windows PXE smoke currently supports only sata0 disk bus, got {disk_bus}.")
    command = (
        "set -e; "
        f"qm set {vmid} --boot order=net0\\;{disk_bus}; "
        f"qm status {vmid} | grep -q running || qm start {vmid}; "
        "sleep 5; "
        f"qm set {vmid} --boot order={disk_bus}\\;net0; "
        f"qm status {vmid}; "
        f"qm config {vmid} | grep -F \"boot: order={disk_bus};net0\" >/dev/null; "
        f"echo windows10-vm{vmid}-pxe-boot-requested-disk-first-next"
    )
    output = _run_proxmox_ssh_command(command, timeout=120)
    guard = (
        "#!ipxe\n"
        f"# BKC guard: VMID {vmid} already entered Windows setup. Boot local disk on accidental PXE retry.\n"
        "sanboot --no-describe --drive 0x80 || exit\n"
    )
    windows_ipxe = str(values.get("windows_ipxe_script_path") or "").strip()
    if windows_ipxe:
        upload_remote_bytes(
            host=str(values.get("target_host") or "").strip(),
            user="root",
            remote_path=windows_ipxe,
            content=guard.encode("utf-8"),
            mode=0o644,
            timeout=60,
        )
        append_event(run_id, "info", stage_name, f"Installed local-disk PXE guard at {windows_ipxe}.")
    _set_stage(run_id, stage_name, "complete", f"VMID {vmid} PXE boot requested; next boot is disk-first.")
    append_event(run_id, "info", stage_name, output[-1200:] if output else f"vm{vmid} booted")


def _run_windows10_vm_observe(run_id: str, stage_name: str) -> None:
    _, _, values = _windows10_pxe_context()
    vmid = int(values.get("target_vmid") or 136)
    output = _run_proxmox_ssh_command(f"qm status {vmid}; qm config {vmid} | sed -n '1,100p'", timeout=60)
    _set_stage(run_id, stage_name, "complete", f"VMID {vmid} is observable in Proxmox.")
    append_event(run_id, "info", stage_name, output[-1600:] if output else f"vm{vmid} observable")


def _run_windows10_post_install_ssh(run_id: str, stage_name: str) -> None:
    _, _, values = _windows10_pxe_context()
    if str(values.get("enable_post_install_ssh_check")).strip().lower() != "true":
        _set_stage(run_id, stage_name, "complete", "Skipped post-install SSH check until Windows bootstrap reachability is explicit.")
        append_event(run_id, "info", stage_name, "Windows net-install handoff is allowed to continue asynchronously; SSH validation remains opt-in.")
        return
    host = f"{values.get('target_install_hostname')}.{values.get('target_install_domain')}"
    output = run_remote_command(
        host=host,
        user=str(values.get("post_boot_probe_user") or values.get("target_admin_user") or "depadmin"),
        command="hostname",
        timeout=120,
    )
    _set_stage(run_id, stage_name, "complete", "Windows post-install SSH access works from BKC.")
    append_event(run_id, "info", stage_name, output[-1200:] if output else "windows ssh ready")


def _flatten_package_values(*groups: object) -> list[str]:
    packages: list[str] = []
    for group in groups:
        if isinstance(group, list):
            for item in group:
                if isinstance(item, str) and item.strip():
                    packages.append(item.strip())
        elif isinstance(group, str) and group.strip():
            packages.extend(part.strip() for part in group.replace(",", " ").split() if part.strip())
    return packages


def _trixie_personalize_context() -> tuple[dict, dict, dict]:
    return _folder_pipeline_context("trixie-workstation-personalize")


def _pipeline_value_truthy(values: dict, key: str) -> bool:
    return str(values.get(key) or "").strip().lower() in {"1", "true", "yes", "on"}


def _trixie_guest_exec(values: dict, command: str, *, timeout: int = 120) -> str:
    vmid = int(values.get("target_vmid") or 132)
    host_timeout = max(10, int(timeout) - 5)
    encoded_command = b64encode(command.encode("utf-8")).decode("ascii")
    wrapper = f"printf %s {shlex.quote(encoded_command)} | base64 -d | /bin/sh"
    remote = f"timeout {host_timeout}s qm guest exec {vmid} -- /bin/sh -c {shlex.quote(wrapper)}"
    output = _run_proxmox_ssh_command(remote, timeout=timeout)
    try:
        payload = json.loads(output)
    except json.JSONDecodeError:
        return output
    pid = payload.get("pid")
    if pid is not None and "exitcode" not in payload:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            status_output = _run_proxmox_ssh_command(
                f"timeout 10s qm guest exec-status {vmid} {int(pid)}",
                timeout=15,
            )
            try:
                status = json.loads(status_output)
            except json.JSONDecodeError as exc:
                raise PipelineExecutionError(f"Could not parse guest exec-status for VMID {vmid}: {status_output}") from exc
            if status.get("exited"):
                payload = status
                break
            time.sleep(2)
        else:
            raise PipelineExecutionError(f"guest command on VMID {vmid} did not exit within {timeout} seconds")
    out = str(payload.get("out-data") or "")
    err = str(payload.get("err-data") or "")
    exit_code = int(payload.get("exitcode") or 0)
    combined = "\n".join(part for part in (out.rstrip(), err.rstrip()) if part)
    if exit_code != 0:
        raise PipelineExecutionError(combined or f"guest command exited {exit_code}")
    return combined


def _run_trixie_personalize_discover(run_id: str, stage_name: str) -> None:
    _, _, values = _trixie_personalize_context()
    output = _trixie_guest_exec(
        values,
        "hostname; ip -brief addr; systemctl is-active qemu-guest-agent ssh",
        timeout=60,
    )
    _set_stage(run_id, stage_name, "complete", "Installed Trixie guest is reachable through qemu-guest-agent.")
    append_event(run_id, "info", stage_name, output[-1600:] if output else "trixie guest ready")


def _run_trixie_personalize_login(run_id: str, stage_name: str) -> None:
    _, _, values = _trixie_personalize_context()
    install_user = str(values.get("target_install_user") or "auzieman").strip()
    install_password = str(values.get("target_install_password") or "").strip()
    if not install_user:
        raise PipelineExecutionError("target_install_user is required for Trixie login normalization.")
    if not install_password:
        _set_stage(run_id, stage_name, "complete", "Skipped password normalization because target_install_password is blank.")
        append_event(run_id, "info", stage_name, "Trixie local password normalization is opt-in.")
        return
    command = (
        "set -e; "
        f"user={shlex.quote(install_user)}; "
        "getent passwd \"$user\" >/dev/null; "
        f"printf '%s\n' {shlex.quote(f'{install_user}:{install_password}')} | chpasswd; "
        "passwd -S \"$user\" | awk '{print $1, $2, $3}'; "
        "getent passwd \"$user\""
    )
    output = _trixie_guest_exec(values, command, timeout=60)
    _set_stage(run_id, stage_name, "complete", f"Trixie local login normalized for {install_user}.")
    append_event(run_id, "info", stage_name, output[-1200:] if output else f"{install_user} password normalized")


def _service_checkpoint_urls(values: dict) -> list[dict]:
    checkpoints = values.get("service_checkpoints")
    if not isinstance(checkpoints, list) or not checkpoints:
        raise PipelineExecutionError("service_checkpoints must be configured for demo checkpoint publishing.")
    preferred_prefix = str(values.get("demo_lan_prefix") or "192.168.1.").strip()
    require_demo_lan = _pipeline_value_truthy(values, "require_demo_lan_urls")
    urls = []
    for raw in checkpoints:
        if not isinstance(raw, dict):
            raise PipelineExecutionError("Each service checkpoint must be an object.")
        label = str(raw.get("label") or "").strip()
        vmid = int(raw.get("vmid") or 0)
        path = str(raw.get("path") or "/").strip() or "/"
        if not path.startswith("/"):
            path = f"/{path}"
        if not label or not vmid:
            raise PipelineExecutionError("Each service checkpoint requires label and vmid.")
        output = _foobar_guest_exec(
            vmid,
            "ip -4 -o addr show | awk '{split($4,a,\"/\"); print a[1]}'",
            timeout=60,
        ).strip()
        candidates = output.split()
        ip = next((candidate for candidate in candidates if preferred_prefix and candidate.startswith(preferred_prefix)), "")
        if not ip and not require_demo_lan:
            ip = candidates[0] if candidates else ""
        if not ip:
            detail = f" with prefix {preferred_prefix}" if preferred_prefix else ""
            raise PipelineExecutionError(f"Could not resolve browser-reachable IP{detail} for service checkpoint {label} VMID {vmid}.")
        urls.append({"label": label, "vmid": vmid, "ip": ip, "url": f"http://{ip}{path}"})
    return urls


def _checkpoint_text(urls: list[dict]) -> str:
    lines = [
        "FooBar Small Office Demo Checkpoints",
        "",
        "Use these IP-based URLs from lab workstations; local DNS may not resolve foo.bar names.",
        "",
    ]
    lines.extend(f"{item['label']}: {item['url']}" for item in urls)
    lines.extend(["", "Generated by BlackKnightController."])
    return "\n".join(lines)


def _run_trixie_personalize_checkpoints(run_id: str, stage_name: str) -> None:
    _, _, values = _trixie_personalize_context()
    urls = _service_checkpoint_urls(values)
    text = _checkpoint_text(urls)
    install_user = str(values.get("target_install_user") or "auzieman").strip()
    quoted_text = shlex.quote(text)
    command = (
        "set -e; "
        f"user={shlex.quote(install_user)}; "
        "home=$(getent passwd \"$user\" | cut -d: -f6); "
        "test -n \"$home\"; "
        "install -d -m 0755 \"$home/Desktop\"; "
        f"printf '%s\n' {quoted_text} > \"$home/Desktop/FooBar Demo Checkpoints.txt\"; "
        "chown \"$user:$user\" \"$home/Desktop/FooBar Demo Checkpoints.txt\"; "
        "cat \"$home/Desktop/FooBar Demo Checkpoints.txt\"; "
        + " ".join(f"curl -fsS {shlex.quote(item['url'])} >/dev/null;" for item in urls)
    )
    output = _trixie_guest_exec(values, command, timeout=180)
    _store_run_extra(run_id, {"foobar_demo_checkpoints": urls})
    _set_stage(run_id, stage_name, "complete", "FooBar demo checkpoint links published on Trixie.")
    append_event(run_id, "info", stage_name, output[-2000:] if output else json.dumps(urls, sort_keys=True))


def _run_trixie_personalize_packages(run_id: str, stage_name: str) -> None:
    _, _, values = _trixie_personalize_context()
    if not _pipeline_value_truthy(values, "enable_full_personalization"):
        _set_stage(run_id, stage_name, "complete", "Skipped full Trixie package profile because enable_full_personalization is false.")
        append_event(run_id, "info", stage_name, "Lightweight demo checkpoint mode is active.")
        return
    packages = _flatten_package_values(values.get("desktop_packages"), values.get("developer_packages"))
    if not packages:
        raise PipelineExecutionError("No Trixie workstation packages are configured.")
    package_args = " ".join(shlex.quote(package) for package in packages)
    command = (
        "export DEBIAN_FRONTEND=noninteractive; "
        "apt-get -o DPkg::Lock::Timeout=600 update; "
        f"apt-get -o DPkg::Lock::Timeout=600 install -y {package_args}"
    )
    output = _trixie_guest_exec(values, command, timeout=3600)
    _set_stage(run_id, stage_name, "complete", "Trixie workstation package profile installed.")
    append_event(run_id, "info", stage_name, output[-2400:] if output else "trixie packages installed")


def _run_trixie_personalize_vscode(run_id: str, stage_name: str) -> None:
    _, _, values = _trixie_personalize_context()
    if not _pipeline_value_truthy(values, "enable_full_personalization") or str(values.get("enable_vscode")).strip().lower() != "true":
        _set_stage(run_id, stage_name, "complete", "Skipped VS Code because enable_vscode is false.")
        append_event(run_id, "info", stage_name, "VS Code install remains opt-in.")
        return
    command = (
        "set -e; export DEBIAN_FRONTEND=noninteractive; "
        "install -d -m 0755 /etc/apt/keyrings; "
        "wget -qO- https://packages.microsoft.com/keys/microsoft.asc | gpg --dearmor > /etc/apt/keyrings/packages.microsoft.gpg; "
        "chmod 0644 /etc/apt/keyrings/packages.microsoft.gpg; "
        "printf '%s\n' 'deb [arch=amd64 signed-by=/etc/apt/keyrings/packages.microsoft.gpg] https://packages.microsoft.com/repos/code stable main' > /etc/apt/sources.list.d/vscode.list; "
        "apt-get -o DPkg::Lock::Timeout=600 update; "
        "apt-get -o DPkg::Lock::Timeout=600 install -y code"
    )
    output = _trixie_guest_exec(values, command, timeout=900)
    _set_stage(run_id, stage_name, "complete", "VS Code installed on Trixie.")
    append_event(run_id, "info", stage_name, output[-2000:] if output else "vscode installed")


def _run_trixie_personalize_rustdesk(run_id: str, stage_name: str) -> None:
    _, _, values = _trixie_personalize_context()
    enabled = _pipeline_value_truthy(values, "enable_full_personalization") and str(values.get("enable_rustdesk")).strip().lower() == "true"
    url = str(values.get("rustdesk_deb_url") or "").strip()
    if not enabled or not url:
        _set_stage(run_id, stage_name, "complete", "Skipped RustDesk because enable_rustdesk is false or rustdesk_deb_url is blank.")
        append_event(run_id, "info", stage_name, "RustDesk Linux install requires an explicit .deb URL.")
        return
    command = (
        "set -e; export DEBIAN_FRONTEND=noninteractive; "
        f"wget -O /tmp/bkc-rustdesk.deb {shlex.quote(url)}; "
        "apt-get -o DPkg::Lock::Timeout=600 install -y /tmp/bkc-rustdesk.deb"
    )
    output = _trixie_guest_exec(values, command, timeout=900)
    _set_stage(run_id, stage_name, "complete", "RustDesk installed on Trixie.")
    append_event(run_id, "info", stage_name, output[-2000:] if output else "rustdesk installed")


def _run_trixie_personalize_services(run_id: str, stage_name: str) -> None:
    _, _, values = _trixie_personalize_context()
    target = str(values.get("graphical_target") or "graphical.target").strip()
    command = (
        f"systemctl set-default {shlex.quote(target)}; "
        "systemctl enable --now ssh qemu-guest-agent; "
        "systemctl enable --now lightdm 2>/dev/null || true; "
        "systemctl is-enabled ssh qemu-guest-agent; "
        "systemctl is-active lightdm 2>/dev/null || true; "
        "systemctl get-default"
    )
    output = _trixie_guest_exec(values, command, timeout=120)
    _set_stage(run_id, stage_name, "complete", "Trixie graphical and remoting services are enabled.")
    append_event(run_id, "info", stage_name, output[-1600:] if output else "trixie services enabled")


def _run_trixie_personalize_verify(run_id: str, stage_name: str) -> None:
    _, _, values = _trixie_personalize_context()
    command = (
        "dpkg-query -W libreoffice mate-desktop-environment-core enlightenment 2>/dev/null; "
        "command -v code >/dev/null 2>&1 && code --version | head -n 1 || true; "
        "systemctl is-active ssh qemu-guest-agent; "
        "systemctl get-default"
    )
    output = _trixie_guest_exec(values, command, timeout=120)
    _set_stage(run_id, stage_name, "complete", "Trixie workstation personality verified.")
    append_event(run_id, "info", stage_name, output[-2000:] if output else "trixie workstation verified")


def _windows10_personalize_context() -> tuple[dict, dict, dict]:
    return _folder_pipeline_context("windows10-workstation-personalize")


def _windows10_personalize_powershell(values: dict, script: str, *, timeout: int = 120) -> str:
    host = str(values.get("target_host") or "").strip()
    user = str(values.get("target_admin_user") or "depadmin").strip()
    if not host:
        raise PipelineExecutionError("Windows personalization target_host is required.")
    encoded = b64encode(script.encode("utf-16le")).decode("ascii")
    command = f"powershell.exe -NoProfile -ExecutionPolicy Bypass -EncodedCommand {encoded}"
    return run_remote_command(host=host, user=user, command=command, timeout=timeout)


def _run_windows10_personalize_discover(run_id: str, stage_name: str) -> None:
    _, _, values = _windows10_personalize_context()
    host = str(values.get("target_host") or "").strip()
    user = str(values.get("target_admin_user") or "depadmin").strip()
    append_event(run_id, "info", stage_name, f"Resolved Windows personalization target {user}@{host}.")
    output = _windows10_personalize_powershell(
        values,
        "hostname; Get-Service sshd | Select-Object Name,Status,StartType | Format-List",
        timeout=120,
    )
    _set_stage(run_id, stage_name, "complete", "Installed Windows guest is reachable over BKC SSH.")
    append_event(run_id, "info", stage_name, output[-1600:] if output else "windows ssh ready")


def _run_windows10_personalize_login(run_id: str, stage_name: str) -> None:
    _, _, values = _windows10_personalize_context()
    admin_user = str(values.get("target_admin_user") or "depadmin").strip()
    demo_password = str(values.get("target_demo_password") or "").strip()
    if not admin_user:
        raise PipelineExecutionError("target_admin_user is required for Windows login normalization.")
    if not demo_password:
        _set_stage(run_id, stage_name, "complete", "Skipped password normalization because target_demo_password is blank.")
        append_event(run_id, "info", stage_name, "Windows local password normalization is opt-in.")
        return
    escaped_user = admin_user.replace("'", "''")
    escaped_password = demo_password.replace("'", "''")
    script = (
        "$ErrorActionPreference = 'Stop'; "
        f"net user '{escaped_user}' '{escaped_password}'; "
        "Write-Output 'windows-local-login-normalized'; "
        "whoami"
    )
    output = _windows10_personalize_powershell(values, script, timeout=60)
    _set_stage(run_id, stage_name, "complete", f"Windows local login normalized for {admin_user}.")
    append_event(run_id, "info", stage_name, output[-1200:] if output else f"{admin_user} password normalized")


def _run_windows10_personalize_checkpoints(run_id: str, stage_name: str) -> None:
    _, _, values = _windows10_personalize_context()
    urls = _service_checkpoint_urls(values)
    text = _checkpoint_text(urls)
    ps_urls = "@(" + ",".join("'" + item["url"].replace("'", "''") + "'" for item in urls) + ")"
    script = (
        "$ErrorActionPreference = 'Stop'; "
        "$desktop = [Environment]::GetFolderPath('Desktop'); "
        "if (-not $desktop) { $desktop = Join-Path $env:USERPROFILE 'Desktop' }; "
        "New-Item -ItemType Directory -Force -Path $desktop | Out-Null; "
        f"@'\n{text}\n'@ | Set-Content -Encoding UTF8 -Path (Join-Path $desktop 'FooBar Demo Checkpoints.txt'); "
        f"$urls = {ps_urls}; "
        "foreach ($url in $urls) { Invoke-WebRequest -UseBasicParsing -Uri $url -TimeoutSec 15 | Out-Null }; "
        "Get-Content (Join-Path $desktop 'FooBar Demo Checkpoints.txt')"
    )
    output = _windows10_personalize_powershell(values, script, timeout=180)
    _store_run_extra(run_id, {"foobar_demo_checkpoints": urls})
    _set_stage(run_id, stage_name, "complete", "FooBar demo checkpoint links published on Windows.")
    append_event(run_id, "info", stage_name, output[-2000:] if output else json.dumps(urls, sort_keys=True))


def _run_windows10_personalize_chocolatey(run_id: str, stage_name: str) -> None:
    _, _, values = _windows10_personalize_context()
    if not _pipeline_value_truthy(values, "enable_full_personalization") or str(values.get("enable_chocolatey")).strip().lower() != "true":
        _set_stage(run_id, stage_name, "complete", "Skipped Chocolatey because enable_chocolatey is false.")
        append_event(run_id, "info", stage_name, "Chocolatey remains opt-in.")
        return
    script = (
        "$ErrorActionPreference = 'Stop'; "
        "$choco = 'C:\\ProgramData\\chocolatey\\bin\\choco.exe'; "
        "if ((Test-Path 'C:\\ProgramData\\chocolatey') -and -not (Test-Path $choco)) { Remove-Item -Recurse -Force 'C:\\ProgramData\\chocolatey' }; "
        "if (-not (Test-Path $choco)) { "
        "Set-ExecutionPolicy Bypass -Scope Process -Force; "
        "[System.Net.ServicePointManager]::SecurityProtocol = [System.Net.ServicePointManager]::SecurityProtocol -bor 3072; "
        "iex ((New-Object System.Net.WebClient).DownloadString('https://community.chocolatey.org/install.ps1')); "
        "} "
        "if (-not (Test-Path $choco)) { $cmd = Get-Command choco.exe -ErrorAction SilentlyContinue; if ($cmd) { $choco = $cmd.Source } } "
        "if (-not (Test-Path $choco)) { throw 'Chocolatey install did not produce choco.exe.' } "
        "& $choco --version"
    )
    output = _windows10_personalize_powershell(values, script, timeout=600)
    _set_stage(run_id, stage_name, "complete", "Chocolatey is available on Windows.")
    append_event(run_id, "info", stage_name, output[-1600:] if output else "chocolatey ready")


def _run_windows10_personalize_packages(run_id: str, stage_name: str) -> None:
    _, _, values = _windows10_personalize_context()
    if not _pipeline_value_truthy(values, "enable_full_personalization"):
        _set_stage(run_id, stage_name, "complete", "Skipped full Windows package profile because enable_full_personalization is false.")
        append_event(run_id, "info", stage_name, "Lightweight demo checkpoint mode is active.")
        return
    packages = _flatten_package_values(values.get("package_names"))
    if not packages:
        raise PipelineExecutionError("No Windows workstation packages are configured.")
    package_args = " ".join(packages)
    script = (
        "$ErrorActionPreference = 'Stop'; "
        "$choco = 'C:\\ProgramData\\chocolatey\\bin\\choco.exe'; "
        "if (-not (Test-Path $choco)) { $cmd = Get-Command choco.exe -ErrorAction SilentlyContinue; if ($cmd) { $choco = $cmd.Source } } "
        "if (-not (Test-Path $choco)) { throw 'Chocolatey is not installed.' } "
        f"& $choco install {package_args} -y --no-progress --limit-output"
    )
    output = _windows10_personalize_powershell(values, script, timeout=1800)
    _set_stage(run_id, stage_name, "complete", "Windows workstation package profile installed.")
    append_event(run_id, "info", stage_name, output[-2400:] if output else "windows packages installed")


def _run_windows10_personalize_verify(run_id: str, stage_name: str) -> None:
    _, _, values = _windows10_personalize_context()
    if _pipeline_value_truthy(values, "enable_full_personalization"):
        script = (
            "$ErrorActionPreference = 'Stop'; "
            "$ProgressPreference = 'SilentlyContinue'; "
            "$choco = 'C:\\ProgramData\\chocolatey\\bin\\choco.exe'; "
            "$expected = @( "
            "'C:\\Program Files\\LibreOffice\\program\\soffice.exe', "
            "'C:\\Program Files\\Microsoft VS Code\\Code.exe', "
            "'C:\\Program Files\\RustDesk\\RustDesk.exe', "
            "'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe' "
            "); "
            "$missing = @($expected | Where-Object { -not (Test-Path $_) }); "
            "if ($missing.Count -gt 0) { throw ('Missing expected workstation apps: ' + ($missing -join ', ')) }; "
            "hostname; "
            "if (Test-Path $choco) { & $choco list --local-only --limit-output | Out-String | Write-Output }; "
            "$expected | ForEach-Object { Write-Output ('present: ' + $_) }; "
            "Get-Service sshd | Select-Object Name,Status,StartType | Format-List; "
            "Write-Output 'Windows workstation package verification passed.'"
        )
    else:
        script = (
            "$ErrorActionPreference = 'Stop'; "
            "hostname; "
            "Test-Path (Join-Path ([Environment]::GetFolderPath('Desktop')) 'FooBar Demo Checkpoints.txt'); "
            "Get-Service sshd | Select-Object Name,Status,StartType | Format-List"
        )
    output = _windows10_personalize_powershell(values, script, timeout=120)
    _set_stage(run_id, stage_name, "complete", "Windows workstation personality verified.")
    append_event(run_id, "info", stage_name, output[-2400:] if output else "windows workstation verified")


def _windows10_builder_context() -> tuple[dict, dict, dict]:
    return _folder_pipeline_context("windows10-winpe-builder")


def _windows10_builder_command(values: dict, command: str, *, timeout: int = 60) -> str:
    return run_remote_command(
        host=str(values.get("builder_vm_ip") or "").strip(),
        user=str(values.get("builder_vm_user") or "depadmin").strip(),
        command=command,
        timeout=timeout,
    )


def _run_windows10_builder_inspect_vm(run_id: str, stage_name: str) -> None:
    _, _, values = _windows10_builder_context()
    vmid = int(values.get("builder_vmid") or 113)
    output = _run_proxmox_ssh_command(f"qm status {vmid}; qm config {vmid} | sed -n '1,120p'", timeout=60)
    _set_stage(run_id, stage_name, "complete", f"VMID {vmid} Proxmox shape inspected.")
    append_event(run_id, "info", stage_name, output[-2000:] if output else f"vm{vmid} inspected")


def _run_windows10_builder_ssh(run_id: str, stage_name: str) -> None:
    _, _, values = _windows10_builder_context()
    output = _windows10_builder_command(values, "hostname && whoami", timeout=60)
    _set_stage(run_id, stage_name, "complete", "Windows builder SSH access works from BKC.")
    append_event(run_id, "info", stage_name, output[-1200:] if output else "windows builder ssh ready")


def _run_windows10_adk_inspect(run_id: str, stage_name: str) -> None:
    _, _, values = _windows10_builder_context()
    command = r'''powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Continue'; $adkRoot='${ADK_ROOT}'; $paths=@($adkRoot, 'C:\Program Files (x86)\Windows Kits\10\Assessment and Deployment Kit\Windows Preinstallation Environment', 'C:\Program Files (x86)\Windows Kits\10\Windows Preinstallation Environment'); $result=[ordered]@{ computer=$env:COMPUTERNAME; adk_root_exists=(Test-Path $adkRoot); dism=(Get-Command dism.exe -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source -First 1); copype=($paths | ForEach-Object { Join-Path $_ 'copype.cmd' } | Where-Object { Test-Path $_ } | Select-Object -First 1); makewinpemedia=($paths | ForEach-Object { Join-Path $_ 'MakeWinPEMedia.cmd' } | Where-Object { Test-Path $_ } | Select-Object -First 1) }; $result | ConvertTo-Json -Compress"'''
    command = command.replace("${ADK_ROOT}", str(values.get("adk_root") or ""))
    output = _windows10_builder_command(values, command, timeout=60)
    _set_stage(run_id, stage_name, "complete", "Windows ADK and WinPE tooling inspected.")
    append_event(run_id, "info", stage_name, output[-1600:] if output else "adk inspected")


def _windows_path_join(base: str, leaf: str) -> str:
    return base.rstrip("\\/") + "\\" + leaf.lstrip("\\/")


def _run_windows10_builder_stage_scripts(run_id: str, stage_name: str) -> None:
    pipeline, _, values = _windows10_builder_context()
    folder = _repo_pipeline_folder(pipeline)
    artifact_root = str(values.get("artifact_root") or r"C:\BKC\WinPE")
    host = str(values.get("builder_vm_ip") or "").strip()
    user = str(values.get("builder_vm_user") or "depadmin").strip()
    _windows10_builder_command(
        values,
        f'powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "New-Item -ItemType Directory -Force {json.dumps(artifact_root)} | Out-Null"',
        timeout=60,
    )
    staged = {}
    for key in ("build_script", "publish_script"):
        rel_path = str(values.get(key) or "").strip()
        source = folder / rel_path
        if not source.exists():
            raise PipelineExecutionError(f"Windows builder script is missing: {source}")
        target = _windows_path_join(artifact_root, Path(rel_path).name)
        upload_remote_file(
            host=host,
            user=user,
            remote_path=target,
            local_path=str(source),
            mode=0o644,
            timeout=60,
        )
        staged[key] = target
    verify = _windows10_builder_command(
        values,
        f'powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "Get-ChildItem {json.dumps(artifact_root)} | Select-Object Name,Length | ConvertTo-Json -Compress"',
        timeout=60,
    )
    _set_stage(run_id, stage_name, "complete", "Windows builder scripts are staged on VMID 113.")
    append_event(run_id, "info", stage_name, json.dumps({"staged": staged, "verify": verify}, sort_keys=True))


def _run_windows10_adk_install(run_id: str, stage_name: str) -> None:
    _, _, values = _windows10_builder_context()
    if str(values.get("enable_adk_install")).strip().lower() != "true":
        _set_stage(run_id, stage_name, "complete", "Skipped ADK install because enable_adk_install is false.")
        append_event(run_id, "info", stage_name, "ADK install remains opt-in.")
        return
    raise PipelineExecutionError("ADK install is not implemented yet; keep enable_adk_install false until installer URLs and silent flags are added.")


def _run_windows10_winpe_build(run_id: str, stage_name: str) -> None:
    _, _, values = _windows10_builder_context()
    if str(values.get("enable_build")).strip().lower() != "true":
        _set_stage(run_id, stage_name, "complete", "Skipped WinPE build because enable_build is false.")
        append_event(run_id, "info", stage_name, "WinPE build remains opt-in until ADK tooling is verified.")
        return
    script = _windows_path_join(str(values.get("artifact_root") or r"C:\BKC\WinPE"), Path(str(values.get("build_script") or "build-bkc-winpe.ps1")).name)
    command = f'powershell.exe -NoProfile -ExecutionPolicy Bypass -File {json.dumps(script)}'
    output = _windows10_builder_command(values, command, timeout=900)
    _set_stage(run_id, stage_name, "complete", "BKC WinPE artifact built on VMID 113.")
    append_event(run_id, "info", stage_name, output[-2000:] if output else "winpe built")


def _run_windows10_winpe_publish(run_id: str, stage_name: str) -> None:
    _, _, values = _windows10_builder_context()
    if str(values.get("enable_publish")).strip().lower() != "true":
        _set_stage(run_id, stage_name, "complete", "Skipped WinPE publish because enable_publish is false.")
        append_event(run_id, "info", stage_name, "WinPE publish remains opt-in until build artifacts are verified.")
        return
    script = _windows_path_join(str(values.get("artifact_root") or r"C:\BKC\WinPE"), Path(str(values.get("publish_script") or "publish-bkc-winpe.ps1")).name)
    command = f'powershell.exe -NoProfile -ExecutionPolicy Bypass -File {json.dumps(script)}'
    output = _windows10_builder_command(values, command, timeout=600)
    _set_stage(run_id, stage_name, "complete", "BKC WinPE artifact publish contract executed.")
    append_event(run_id, "info", stage_name, output[-2000:] if output else "winpe publish checked")


def _foobar_app_vm_context() -> tuple[dict, dict, dict]:
    return _folder_pipeline_context("small-office-foobar-app-vms")


def _foobar_app_targets(values: dict) -> list[dict]:
    targets = values.get("target_vms")
    if not isinstance(targets, list) or not targets:
        raise PipelineExecutionError("small-office-foobar-app-vms requires target_vms in defaults.json.")
    normalized = []
    for raw in targets:
        if not isinstance(raw, dict):
            raise PipelineExecutionError("Each foo.bar app VM target must be an object.")
        vmid = int(raw.get("vmid") or 0)
        name = str(raw.get("name") or "").strip()
        if not vmid or not name:
            raise PipelineExecutionError("Each foo.bar app VM target requires vmid and name.")
        target = dict(raw)
        target["vmid"] = vmid
        target["name"] = name
        normalized.append(target)
    return normalized


def _foobar_truthy(value: object) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _find_proxmox_vm_node(client: ProxmoxClient, vmid: int, preferred_node: str = "") -> tuple[str, dict | None]:
    preferred = str(preferred_node or "").strip()
    if preferred:
        try:
            return preferred, client.vm_config(preferred, vmid)
        except Exception:
            pass
    for node in client.nodes():
        node_name = str(node.get("node") or "").strip()
        if not node_name:
            continue
        try:
            return node_name, client.vm_config(node_name, vmid)
        except Exception:
            continue
    return preferred, None


def _run_foobar_app_source_select(run_id: str, stage_name: str) -> None:
    _, _, values = _foobar_app_vm_context()
    client = ProxmoxClient(load_proxmox_config())
    source_vmid = int(values.get("source_template_vmid") or 0)
    source_node_hint = str(values.get("source_template_node") or "").strip()
    expected_name = str(values.get("source_template_name") or "").strip()
    if not source_vmid:
        raise PipelineExecutionError("source_template_vmid is required.")

    source_node, config = _find_proxmox_vm_node(client, source_vmid, source_node_hint)
    if not config:
        raise PipelineExecutionError(f"Trixie source VMID {source_vmid} was not found in Proxmox.")
    actual_name = str(config.get("name") or "").strip()
    if expected_name and actual_name and actual_name != expected_name:
        raise PipelineExecutionError(
            f"Refusing to use VMID {source_vmid}: expected {expected_name!r}, found {actual_name!r}."
        )
    status = client.vm_status(source_node, source_vmid)
    is_template = str(config.get("template") or "0") == "1"
    current_status = str(status.get("status") or "").strip().lower()
    if current_status == "running" and not is_template:
        raise PipelineExecutionError(
            f"Source VMID {source_vmid} is running. Shut it down or convert it to a template before cloning app VMs."
        )

    _store_run_extra(
        run_id,
        {
            "foobar_app_source": {
                "node": source_node,
                "vmid": source_vmid,
                "name": actual_name or expected_name,
                "status": current_status,
                "template": is_template,
            }
        },
    )
    _set_stage(run_id, stage_name, "complete", f"Selected Trixie source VMID {source_vmid} on {source_node}.")
    append_event(
        run_id,
        "info",
        stage_name,
        json.dumps({"source_node": source_node, "vmid": source_vmid, "status": current_status, "template": is_template}, sort_keys=True),
    )


def _run_foobar_app_vm_clone(run_id: str, stage_name: str) -> None:
    _, _, values = _foobar_app_vm_context()
    client = ProxmoxClient(load_proxmox_config())
    run = get_run(run_id) or {}
    extra = run.get("extra") if isinstance(run.get("extra"), dict) else {}
    source = dict(extra.get("foobar_app_source") or {})
    source_node = str(source.get("node") or values.get("source_template_node") or "").strip()
    source_vmid = int(source.get("vmid") or values.get("source_template_vmid") or 0)
    if not source_node or not source_vmid:
        raise PipelineExecutionError("FooBar app VM source metadata is missing. Re-run select-trixie-source.")
    targets = _foobar_app_targets(values)
    enable_replace = _foobar_truthy(values.get("enable_replace"))
    cloned = []

    for target in targets:
        vmid = int(target["vmid"])
        name = str(target["name"])
        existing_node, existing_config = _find_proxmox_vm_node(client, vmid, source_node)
        if existing_config:
            existing_name = str(existing_config.get("name") or "").strip()
            if existing_name != name:
                raise PipelineExecutionError(
                    f"Refusing to replace VMID {vmid}: expected {name!r}, found {existing_name!r}."
                )
            if not enable_replace:
                cloned.append({"node": existing_node or source_node, "vmid": vmid, "name": name, "status": "existing"})
                continue
            status = client.vm_status(existing_node or source_node, vmid)
            if str(status.get("status") or "").strip().lower() == "running":
                stop_upid = client.stop_vm(existing_node or source_node, vmid, timeout=60)
                client.wait_for_task(existing_node or source_node, str(stop_upid), timeout=180)
            destroy_upid = client.destroy_vm(existing_node or source_node, vmid, purge=True)
            client.wait_for_task(existing_node or source_node, str(destroy_upid), timeout=300)

        upid = client.clone_vm(
            node=source_node,
            source_vmid=source_vmid,
            new_vmid=vmid,
            name=name,
            full=True,
        )
        task = client.wait_for_task(source_node, str(upid), timeout=2400)
        exit_status = str(task.get("exitstatus") or "")
        if exit_status and exit_status != "OK":
            raise PipelineExecutionError(f"Proxmox clone failed for {name}: {exit_status}")
        cloned.append(
            {
                "node": source_node,
                "vmid": vmid,
                "name": name,
                "role": target.get("role"),
                "application": target.get("application"),
                "status": "cloned",
                "upid": str(upid),
            }
        )

    _store_run_extra(run_id, {"foobar_app_vms": cloned})
    _set_stage(run_id, stage_name, "complete", "FooBar SuiteCRM and Kanboard VM shells cloned.")
    append_event(run_id, "info", stage_name, json.dumps(cloned, sort_keys=True))


def _run_foobar_app_vm_boot(run_id: str, stage_name: str) -> None:
    run = get_run(run_id) or {}
    extra = run.get("extra") if isinstance(run.get("extra"), dict) else {}
    targets = list(extra.get("foobar_app_vms") or [])
    if not targets:
        _, _, values = _foobar_app_vm_context()
        source_node = str((extra.get("foobar_app_source") or {}).get("node") or values.get("source_template_node") or "").strip()
        targets = [{"node": source_node, **target} for target in _foobar_app_targets(values)]
    client = ProxmoxClient(load_proxmox_config())
    booted = []
    for target in targets:
        node = str(target.get("node") or "").strip()
        vmid = int(target.get("vmid") or 0)
        name = str(target.get("name") or f"vm-{vmid}").strip()
        if not node or not vmid:
            raise PipelineExecutionError("FooBar app VM boot target metadata is incomplete.")
        status = client.vm_status(node, vmid)
        if str(status.get("status") or "").strip().lower() != "running":
            upid = client.start_vm(node, vmid)
            task = client.wait_for_task(node, str(upid), timeout=180)
            exit_status = str(task.get("exitstatus") or "")
            if exit_status and exit_status != "OK":
                raise PipelineExecutionError(f"Proxmox start failed for {name}: {exit_status}")
        running = client.wait_for_vm_status(node, vmid, "running", timeout=120)
        booted.append({"node": node, "vmid": vmid, "name": name, "status": running.get("status", "running")})
    _store_run_extra(run_id, {"foobar_app_vms_running": booted})
    _set_stage(run_id, stage_name, "complete", "FooBar application VM shells are running.")
    append_event(run_id, "info", stage_name, json.dumps(booted, sort_keys=True))


def _run_foobar_app_relationships(run_id: str, stage_name: str) -> None:
    pipeline, _, values = _foobar_app_vm_context()
    run = get_run(run_id) or {}
    extra = run.get("extra") if isinstance(run.get("extra"), dict) else {}
    payload = {
        "pipeline_id": pipeline.get("id"),
        "tenant": values.get("tenant_slug"),
        "source": extra.get("foobar_app_source"),
        "vms": extra.get("foobar_app_vms_running") or extra.get("foobar_app_vms") or _foobar_app_targets(values),
        "handoff": "small-office-foobar-app-install",
    }
    append_event(run_id, "info", stage_name, json.dumps(payload, sort_keys=True))
    _set_stage(run_id, stage_name, "complete", "FooBar SuiteCRM and Kanboard VM relationships recorded.")


def _foobar_services_context() -> tuple[dict, dict, dict]:
    return _folder_pipeline_context("small-office-foobar-services")


def _foobar_service_identity_target(values: dict) -> dict:
    target = values.get("identity_vm")
    if not isinstance(target, dict):
        raise PipelineExecutionError("small-office-foobar-services requires identity_vm in defaults.json.")
    vmid = int(target.get("vmid") or 0)
    name = str(target.get("name") or "").strip()
    if not vmid or not name:
        raise PipelineExecutionError("identity_vm requires vmid and name.")
    normalized = dict(target)
    normalized["vmid"] = vmid
    normalized["name"] = name
    return normalized


def _foobar_service_app_targets(values: dict) -> list[dict]:
    targets = values.get("application_vms")
    if not isinstance(targets, list) or not targets:
        raise PipelineExecutionError("small-office-foobar-services requires application_vms in defaults.json.")
    normalized = []
    for raw in targets:
        if not isinstance(raw, dict):
            raise PipelineExecutionError("Each foo.bar service application target must be an object.")
        vmid = int(raw.get("vmid") or 0)
        name = str(raw.get("name") or "").strip()
        role = str(raw.get("role") or "").strip()
        if not vmid or not name or not role:
            raise PipelineExecutionError("Each foo.bar service application target requires vmid, name, and role.")
        target = dict(raw)
        target["vmid"] = vmid
        target["name"] = name
        target["role"] = role
        normalized.append(target)
    return normalized


def _foobar_service_targets(values: dict) -> list[dict]:
    return [_foobar_service_identity_target(values), *_foobar_service_app_targets(values)]


def _foobar_guest_exec(vmid: int, command: str, *, timeout: int = 120) -> str:
    host_timeout = max(10, int(timeout) - 5)
    encoded_command = b64encode(command.encode("utf-8")).decode("ascii")
    wrapper = f"printf %s {shlex.quote(encoded_command)} | base64 -d | /bin/sh"
    remote = f"timeout {host_timeout}s qm guest exec {int(vmid)} -- /bin/sh -c {shlex.quote(wrapper)}"
    output = _run_proxmox_ssh_command(remote, timeout=timeout)
    try:
        payload = json.loads(output)
    except json.JSONDecodeError:
        return output
    pid = payload.get("pid")
    if pid is not None and "exitcode" not in payload:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            status_output = _run_proxmox_ssh_command(
                f"timeout 10s qm guest exec-status {int(vmid)} {int(pid)}",
                timeout=15,
            )
            try:
                status = json.loads(status_output)
            except json.JSONDecodeError as exc:
                raise PipelineExecutionError(f"Could not parse guest exec-status for VMID {vmid}: {status_output}") from exc
            if status.get("exited"):
                payload = status
                break
            time.sleep(2)
        else:
            raise PipelineExecutionError(f"guest command on VMID {vmid} did not exit within {timeout} seconds")
    out = str(payload.get("out-data") or "")
    err = str(payload.get("err-data") or "")
    exit_code = int(payload.get("exitcode") or 0)
    combined = "\n".join(part for part in (out.rstrip(), err.rstrip()) if part)
    if exit_code != 0:
        raise PipelineExecutionError(combined or f"guest command exited {exit_code}")
    return combined


def _foobar_wait_guest(vmid: int, name: str, *, attempts: int = 30, sleep_seconds: int = 10) -> str:
    last_error = ""
    for _ in range(attempts):
        try:
            return _foobar_guest_exec(vmid, "hostname; systemctl is-active qemu-guest-agent || true", timeout=60)
        except Exception as exc:
            last_error = str(exc)
            time.sleep(sleep_seconds)
    raise PipelineExecutionError(f"{name} VMID {vmid} did not become guest-command ready: {last_error}")


def _run_foobar_service_identity_vm(run_id: str, stage_name: str) -> None:
    _, _, values = _foobar_services_context()
    client = ProxmoxClient(load_proxmox_config())
    source_vmid = int(values.get("source_template_vmid") or 0)
    source_node_hint = str(values.get("source_template_node") or "").strip()
    expected_source_name = str(values.get("source_template_name") or "").strip()
    target = _foobar_service_identity_target(values)
    target_vmid = int(target["vmid"])
    target_name = str(target["name"])
    if not source_vmid:
        raise PipelineExecutionError("source_template_vmid is required for identity VM clone.")

    source_node, source_config = _find_proxmox_vm_node(client, source_vmid, source_node_hint)
    if not source_config:
        raise PipelineExecutionError(f"Trixie source VMID {source_vmid} was not found in Proxmox.")
    actual_source_name = str(source_config.get("name") or "").strip()
    if expected_source_name and actual_source_name and actual_source_name != expected_source_name:
        raise PipelineExecutionError(
            f"Refusing to use VMID {source_vmid}: expected {expected_source_name!r}, found {actual_source_name!r}."
        )

    existing_node, existing_config = _find_proxmox_vm_node(client, target_vmid, source_node)
    enable_replace = _foobar_truthy(values.get("enable_replace_identity"))
    if existing_config:
        existing_name = str(existing_config.get("name") or "").strip()
        if existing_name != target_name:
            raise PipelineExecutionError(
                f"Refusing to replace VMID {target_vmid}: expected {target_name!r}, found {existing_name!r}."
            )
        if enable_replace:
            status = client.vm_status(existing_node or source_node, target_vmid)
            if str(status.get("status") or "").strip().lower() == "running":
                stop_upid = client.stop_vm(existing_node or source_node, target_vmid, timeout=60)
                client.wait_for_task(existing_node or source_node, str(stop_upid), timeout=180)
            destroy_upid = client.destroy_vm(existing_node or source_node, target_vmid, purge=True)
            client.wait_for_task(existing_node or source_node, str(destroy_upid), timeout=300)
        else:
            _store_run_extra(run_id, {"foobar_identity_vm": {"node": existing_node or source_node, **target, "status": "existing"}})
            _set_stage(run_id, stage_name, "complete", f"Identity VMID {target_vmid} already exists.")
            return

    source_status = client.vm_status(source_node, source_vmid)
    source_running = str(source_status.get("status") or "").strip().lower() == "running"
    if source_running:
        if not _foobar_truthy(values.get("stop_source_for_identity_clone")):
            raise PipelineExecutionError(f"Source VMID {source_vmid} is running and stop_source_for_identity_clone is false.")
        stop_upid = client.stop_vm(source_node, source_vmid, timeout=60)
        client.wait_for_task(source_node, str(stop_upid), timeout=180)
        client.wait_for_vm_status(source_node, source_vmid, "stopped", timeout=120)

    upid = client.clone_vm(node=source_node, source_vmid=source_vmid, new_vmid=target_vmid, name=target_name, full=True)
    task = client.wait_for_task(source_node, str(upid), timeout=2400)
    exit_status = str(task.get("exitstatus") or "")
    if exit_status and exit_status != "OK":
        raise PipelineExecutionError(f"Proxmox clone failed for {target_name}: {exit_status}")
    start_upid = client.start_vm(source_node, target_vmid)
    client.wait_for_task(source_node, str(start_upid), timeout=180)
    client.wait_for_vm_status(source_node, target_vmid, "running", timeout=120)
    identity = {"node": source_node, **target, "status": "running", "upid": str(upid)}
    _store_run_extra(run_id, {"foobar_identity_vm": identity})
    append_event(run_id, "info", stage_name, json.dumps(identity, sort_keys=True))
    _set_stage(run_id, stage_name, "complete", f"Identity VM {target_name} cloned and started.")


def _run_foobar_service_guest_wait(run_id: str, stage_name: str) -> None:
    _, _, values = _foobar_services_context()
    client = ProxmoxClient(load_proxmox_config())
    ready = []
    for target in _foobar_service_targets(values):
        vmid = int(target["vmid"])
        name = str(target["name"])
        node, config = _find_proxmox_vm_node(client, vmid, str(values.get("source_template_node") or ""))
        if not config or not node:
            raise PipelineExecutionError(f"{name} VMID {vmid} was not found in Proxmox.")
        status = client.vm_status(node, vmid)
        if str(status.get("status") or "").strip().lower() != "running":
            upid = client.start_vm(node, vmid)
            client.wait_for_task(node, str(upid), timeout=180)
            client.wait_for_vm_status(node, vmid, "running", timeout=120)
        append_event(run_id, "info", stage_name, f"Waiting for guest-command readiness on {name} VMID {vmid}.")
        output = _foobar_wait_guest(vmid, name)
        append_event(run_id, "info", stage_name, f"{name} VMID {vmid} accepted BKC guest commands.")
        ready.append({"node": node, "vmid": vmid, "name": name, "output": output[-300:]})
    _store_run_extra(run_id, {"foobar_service_guests_ready": ready})
    append_event(run_id, "info", stage_name, json.dumps(ready, sort_keys=True))
    _set_stage(run_id, stage_name, "complete", "FooBar service guests are command-ready.")


def _run_foobar_service_demo_lan(run_id: str, stage_name: str) -> None:
    _, _, values = _foobar_services_context()
    interface = str(values.get("demo_lan_interface") or "ens19").strip()
    prefix = str(values.get("demo_lan_prefix") or "192.168.1.").strip()
    if not interface:
        raise PipelineExecutionError("demo_lan_interface is required.")
    evidence = []
    for target in _foobar_service_targets(values):
        vmid = int(target["vmid"])
        name = str(target["name"])
        command = (
            "set -e; "
            f"iface={shlex.quote(interface)}; "
            "ip link show \"$iface\" >/dev/null; "
            "ip link set \"$iface\" up; "
            "command -v dhclient >/dev/null || { export DEBIAN_FRONTEND=noninteractive; apt-get -o DPkg::Lock::Timeout=600 update; apt-get -o DPkg::Lock::Timeout=600 install -y isc-dhcp-client; }; "
            "dhclient -1 -v \"$iface\" 2>/tmp/bkc-demo-lan-dhclient.log || cat /tmp/bkc-demo-lan-dhclient.log; "
            "ip -4 -o addr show dev \"$iface\" | awk '{split($4,a,\"/\"); print a[1]}'"
        )
        output = _foobar_guest_exec(vmid, command, timeout=240)
        ips = [line.strip() for line in output.splitlines() if line.strip() and line.strip()[0].isdigit()]
        selected = next((ip for ip in ips if not prefix or ip.startswith(prefix)), ips[-1] if ips else "")
        if not selected:
            raise PipelineExecutionError(f"{name} VMID {vmid} did not receive a demo LAN IPv4 address on {interface}.")
        evidence.append({"vmid": vmid, "name": name, "interface": interface, "ip": selected})
    _store_run_extra(run_id, {"foobar_demo_lan_ips": evidence})
    append_event(run_id, "info", stage_name, json.dumps(evidence, sort_keys=True))
    _set_stage(run_id, stage_name, "complete", "FooBar service demo LAN interfaces configured.")


def _foobar_usernames(values: dict) -> list[str]:
    users = values.get("users")
    if not isinstance(users, list):
        return []
    return [str(user.get("username") or "").strip() for user in users if isinstance(user, dict) and str(user.get("username") or "").strip()]


def _foobar_identity_values(values: dict) -> tuple[dict, int, str, str, str, list[str]]:
    target = _foobar_service_identity_target(values)
    vmid = int(target["vmid"])
    hostname = str(target.get("hostname") or target["name"]).split(".", 1)[0]
    domain = str(values.get("domain") or "foo.bar").strip()
    password = str(values.get("default_password") or "changeme123").strip()
    users = _foobar_usernames(values)
    return target, vmid, hostname, domain, password, users


def _run_foobar_service_identity_packages(run_id: str, stage_name: str) -> None:
    _, _, values = _foobar_services_context()
    _, vmid, hostname, domain, password, _ = _foobar_identity_values(values)
    slapd_seed = "\n".join(
        [
            "slapd slapd/no_configuration boolean false",
            f"slapd slapd/domain string {domain}",
            "slapd shared/organization string FooBar",
            f"slapd slapd/password1 password {password}",
            f"slapd slapd/password2 password {password}",
            "slapd slapd/backend select MDB",
            "slapd slapd/purge_database boolean true",
            "slapd slapd/move_old_database boolean true",
            "slapd slapd/allow_ldap_v2 boolean false",
        ]
    )
    command = (
        "set -e; export DEBIAN_FRONTEND=noninteractive; "
        f"hostnamectl set-hostname {shlex.quote(hostname)}; "
        f"printf '%s\n' {shlex.quote(slapd_seed)} | debconf-set-selections; "
        "apt-get -o DPkg::Lock::Timeout=600 update; "
        "apt-get -o DPkg::Lock::Timeout=600 install -y slapd ldap-utils samba apache2 php php-ldap libapache2-mod-php phpldapadmin python3 curl; "
        "systemctl enable --now slapd; "
        "systemctl is-active slapd; "
        "ldapsearch -x -H ldap://localhost -b dc=foo,dc=bar -s base dn"
    )
    output = _foobar_guest_exec(vmid, command, timeout=2400)
    _set_stage(run_id, stage_name, "complete", "FooBar identity packages installed.")
    append_event(run_id, "info", stage_name, output[-2400:] if output else "identity packages installed")


def _run_foobar_service_ldap_seed(run_id: str, stage_name: str) -> None:
    _, _, values = _foobar_services_context()
    _, vmid, _, _, password, users = _foobar_identity_values(values)
    user_ldif_parts = []
    uid_base = 10000
    for offset, username in enumerate(users, start=1):
        cn = username.replace(".", " ").title()
        user_ldif_parts.append(
            "\n".join(
                [
                    f"dn: uid={username},ou=People,dc=foo,dc=bar",
                    "objectClass: inetOrgPerson",
                    "objectClass: posixAccount",
                    f"cn: {cn}",
                    f"sn: {cn.split()[-1]}",
                    f"uid: {username}",
                    f"uidNumber: {uid_base + offset}",
                    "gidNumber: 10000",
                    f"homeDirectory: /srv/foobar/homes/{username}",
                    "loginShell: /bin/bash",
                    "userPassword: ${USER_PASSWORD_HASH}",
                ]
            )
        )
    user_ldif = "\n\n".join(user_ldif_parts)
    command = (
        "set -e; "
        f"USER_PASSWORD_HASH=$(slappasswd -s {shlex.quote(password)}); "
        "cat >/tmp/bkc-foobar-base.ldif <<'LDIF'\n"
        "dn: ou=People,dc=foo,dc=bar\n"
        "objectClass: organizationalUnit\n"
        "ou: People\n\n"
        "dn: ou=Groups,dc=foo,dc=bar\n"
        "objectClass: organizationalUnit\n"
        "ou: Groups\n\n"
        "dn: cn=foobar_users,ou=Groups,dc=foo,dc=bar\n"
        "objectClass: posixGroup\n"
        "cn: foobar_users\n"
        "gidNumber: 10000\n"
        "LDIF\n"
        "sed \"s|${USER_PASSWORD_HASH}|$USER_PASSWORD_HASH|g\" >/tmp/bkc-foobar-users.ldif <<'LDIF'\n"
        f"{user_ldif}\n"
        "LDIF\n"
        "ldapsearch -x -D cn=admin,dc=foo,dc=bar -w "
        f"{shlex.quote(password)} "
        "-b ou=People,dc=foo,dc=bar -s base dn >/dev/null 2>&1 || "
        f"ldapadd -x -D cn=admin,dc=foo,dc=bar -w {shlex.quote(password)} -f /tmp/bkc-foobar-base.ldif; "
        "while IFS= read -r dn; do "
        "actual_dn=${dn#dn: }; "
        f"ldapsearch -x -D cn=admin,dc=foo,dc=bar -w {shlex.quote(password)} -b \"$actual_dn\" -s base dn >/dev/null 2>&1 || "
        "awk -v start=\"$dn\" 'BEGIN{p=0} $0==start{p=1} p{print} p && $0==\"\"{exit}' /tmp/bkc-foobar-users.ldif | "
        f"ldapadd -x -D cn=admin,dc=foo,dc=bar -w {shlex.quote(password)}; "
        "done <<'DNS'\n"
        + "\n".join(f"dn: uid={username},ou=People,dc=foo,dc=bar" for username in users)
        + "\nDNS\n"
        f"ldapsearch -x -D cn=admin,dc=foo,dc=bar -w {shlex.quote(password)} -b ou=People,dc=foo,dc=bar uid | sed -n '1,80p'"
    )
    output = _foobar_guest_exec(vmid, command, timeout=300)
    _set_stage(run_id, stage_name, "complete", "FooBar LDAP directory seeded.")
    append_event(run_id, "info", stage_name, output[-2400:] if output else "ldap directory seeded")


def _run_foobar_service_samba_homes(run_id: str, stage_name: str) -> None:
    _, _, values = _foobar_services_context()
    _, vmid, _, _, _, users = _foobar_identity_values(values)
    user_dirs = " ".join(shlex.quote(f"/srv/foobar/homes/{username}") for username in users)
    command = (
        "set -e; "
        "install -d -m 0755 /srv/foobar/homes; "
        f"install -d -m 0750 {user_dirs}; "
        "chown -R root:root /srv/foobar; "
        "cp /etc/samba/smb.conf /etc/samba/smb.conf.bkc-pre-foobar 2>/dev/null || true; "
        "sed -i '/# BKC FooBar homes start/,/# BKC FooBar homes end/d' /etc/samba/smb.conf; "
        "cat >>/etc/samba/smb.conf <<'SMB'\n"
        "# BKC FooBar homes start\n"
        "[foobar-homes]\n"
        "   path = /srv/foobar/homes\n"
        "   browseable = yes\n"
        "   read only = no\n"
        "   guest ok = yes\n"
        "   force user = root\n"
        "# BKC FooBar homes end\n"
        "SMB\n"
        "systemctl enable --now smbd; "
        "systemctl restart smbd; "
        "systemctl is-active smbd; "
        "testparm -s >/tmp/bkc-foobar-testparm.out"
    )
    output = _foobar_guest_exec(vmid, command, timeout=300)
    _set_stage(run_id, stage_name, "complete", "FooBar Samba shared homes configured.")
    append_event(run_id, "info", stage_name, output[-1800:] if output else "samba homes configured")


def _run_foobar_service_identity_portal(run_id: str, stage_name: str) -> None:
    _, _, values = _foobar_services_context()
    _, vmid, _, _, _, users = _foobar_identity_values(values)
    base_dn = str(values.get("ldap_base_dn") or "dc=foo,dc=bar").strip()
    admin_dn = str(values.get("ldap_admin_dn") or "cn=admin,dc=foo,dc=bar").strip()
    user_labels = ", ".join(users)
    command = (
        "set -e; "
        "test -f /etc/phpldapadmin/config.php; "
        "cp /etc/phpldapadmin/config.php /etc/phpldapadmin/config.php.bkc-pre-foobar 2>/dev/null || true; "
        "python3 - <<'PY'\n"
        "from pathlib import Path\n"
        "path = Path('/etc/phpldapadmin/config.php')\n"
        "text = path.read_text()\n"
        "marker = '// bkc_foobar_configured'\n"
        "if marker not in text:\n"
        "    block = \"\\n\".join([\n"
        "        marker,\n"
        "        \"$servers->setValue('server','host','127.0.0.1');\",\n"
        f"        \"$servers->setValue('server','base',array({base_dn!r}));\",\n"
        f"        \"$servers->setValue('login','bind_id',{admin_dn!r});\",\n"
        "        \"$config->custom->appearance['hide_template_warning'] = true;\",\n"
        "    ]) + \"\\n\"\n"
        "    stripped = text.rstrip()\n"
        "    if stripped.endswith('?>'):\n"
        "        text = stripped[:-2].rstrip() + \"\\n\" + block + \"?>\\n\"\n"
        "    else:\n"
        "        text = text.rstrip() + \"\\n\" + block\n"
        "path.write_text(text)\n"
        "PY\n"
        f"printf '%s\\n' '<!doctype html><title>FooBar Identity</title><h1>FooBar Identity</h1><p>OpenLDAP, Samba homes, and phpLDAPadmin are provisioned.</p><p>Users: {user_labels}</p><p>Admin DN: {admin_dn}</p><p><a href=\"/phpldapadmin/\">Open phpLDAPadmin</a></p>' > /var/www/html/index.html; "
        "systemctl enable --now apache2; "
        "systemctl restart apache2; "
        "systemctl is-active apache2; "
        "curl -fsS http://localhost/phpldapadmin/ >/tmp/bkc-foobar-identity.html; "
        "grep -Ei 'phpLDAPadmin|Authenticate|Login|Username' /tmp/bkc-foobar-identity.html | head -n 5"
    )
    output = _foobar_guest_exec(vmid, command, timeout=180)
    _set_stage(run_id, stage_name, "complete", "FooBar phpLDAPadmin portal published.")
    append_event(run_id, "info", stage_name, output[-1800:] if output else "phpLDAPadmin portal published")


def _foobar_app_target_by_role(values: dict, role: str) -> dict:
    for target in _foobar_service_app_targets(values):
        if str(target.get("role") or "") == role:
            return target
    raise PipelineExecutionError(f"No foo.bar service target found for role {role!r}.")


def _run_foobar_service_app_provision(run_id: str, stage_name: str, *, role: str) -> None:
    _, _, values = _foobar_services_context()
    target = _foobar_app_target_by_role(values, role)
    vmid = int(target["vmid"])
    hostname = str(target.get("hostname") or target["name"]).split(".", 1)[0]
    app = str(target.get("application") or role).strip()
    endpoint_path = str(target.get("endpoint_path") or f"/{app}/").strip()
    packages = "apache2 php libapache2-mod-php curl"
    if role == "crm":
        packages += " mariadb-server"
        title = "FooBar SuiteCRM"
        body = "CRM placeholder endpoint for helpdesk customer records."
    else:
        packages += " sqlite3"
        title = "FooBar Kanboard"
        body = "Ticket board placeholder endpoint for helpdesk case work."
    web_dir = f"/var/www/html/{app}"
    command = (
        "set -e; export DEBIAN_FRONTEND=noninteractive; "
        f"hostnamectl set-hostname {shlex.quote(hostname)}; "
        "apt-get -o DPkg::Lock::Timeout=600 update; "
        f"apt-get -o DPkg::Lock::Timeout=600 install -y {packages}; "
        f"install -d -m 0755 {shlex.quote(web_dir)}; "
        f"printf '%s\\n' '<!doctype html><title>{title}</title><h1>{title}</h1><p>{body}</p><p>BKC service lane: {stage_name}</p>' > {shlex.quote(web_dir + '/index.html')}; "
        f"printf '%s\\n' '<!doctype html><title>{title}</title><h1>{title}</h1><p>{body}</p><p>Endpoint: {endpoint_path}</p>' > /var/www/html/index.html; "
        "systemctl enable --now apache2; "
        "systemctl restart apache2; "
        "systemctl is-active apache2; "
        f"curl -fsS http://localhost{shlex.quote(endpoint_path)} >/tmp/bkc-foobar-{role}.html"
    )
    output = _foobar_guest_exec(vmid, command, timeout=1800)
    _set_stage(run_id, stage_name, "complete", f"FooBar {app} intranet endpoint provisioned.")
    append_event(run_id, "info", stage_name, output[-2400:] if output else f"{app} endpoint provisioned")


def _run_foobar_service_suitecrm(run_id: str, stage_name: str) -> None:
    _, _, values = _foobar_services_context()
    target = _foobar_app_target_by_role(values, "crm")
    vmid = int(target["vmid"])
    hostname = str(target.get("hostname") or target["name"]).split(".", 1)[0]
    version = str(values.get("suitecrm_version") or "7.15.1").strip().lstrip("v")
    db_password = str(values.get("default_password") or "changeme123").strip()
    sql_password = db_password.replace("'", "''")
    if not version:
        raise PipelineExecutionError("suitecrm_version is required.")
    archive_url = f"https://github.com/SuiteCRM/SuiteCRM/releases/download/v{version}/SuiteCRM-{version}.zip"
    command = (
        "set -e; export DEBIAN_FRONTEND=noninteractive; "
        f"hostnamectl set-hostname {shlex.quote(hostname)}; "
        "apt-get -o DPkg::Lock::Timeout=600 update; "
        "apt-get -o DPkg::Lock::Timeout=600 install -y "
        "apache2 mariadb-server php libapache2-mod-php php-mysql php-curl php-xml php-mbstring "
        "php-zip php-gd php-ldap php-intl php-soap unzip curl wget ca-certificates; "
        "a2enmod rewrite >/dev/null 2>&1 || true; "
        "for php_conf_dir in /etc/php/*/apache2/conf.d; do "
        "test -d \"$php_conf_dir\" || continue; "
        "cat >\"$php_conf_dir/99-bkc-suitecrm.ini\" <<'PHPINI'\n"
        "upload_max_filesize = 64M\n"
        "post_max_size = 64M\n"
        "memory_limit = 512M\n"
        "max_execution_time = 300\n"
        "max_input_time = 300\n"
        "PHPINI\n"
        "done; "
        "systemctl enable --now mariadb apache2; "
        "cat >/tmp/bkc-suitecrm.sql <<'SQL'\n"
        "CREATE DATABASE IF NOT EXISTS suitecrm CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;\n"
        f"CREATE USER IF NOT EXISTS 'suitecrm'@'localhost' IDENTIFIED BY '{sql_password}';\n"
        "GRANT ALL PRIVILEGES ON suitecrm.* TO 'suitecrm'@'localhost';\n"
        "FLUSH PRIVILEGES;\n"
        "SQL\n"
        "mysql </tmp/bkc-suitecrm.sql; "
        "tmp=$(mktemp -d); trap 'rm -rf \"$tmp\"' EXIT; "
        f"wget -qO \"$tmp/suitecrm.zip\" {shlex.quote(archive_url)}; "
        "rm -rf /var/www/html/suitecrm; "
        "install -d -m 0755 /var/www/html/suitecrm; "
        "unzip -q \"$tmp/suitecrm.zip\" -d \"$tmp/suitecrm-src\"; "
        "src=$(find \"$tmp/suitecrm-src\" -mindepth 1 -maxdepth 1 -type d | head -n 1); "
        "cp -a \"$src\"/. /var/www/html/suitecrm/; "
        "install -d -m 0775 /var/www/html/suitecrm/cache /var/www/html/suitecrm/custom "
        "/var/www/html/suitecrm/modules /var/www/html/suitecrm/upload; "
        "chown -R www-data:www-data /var/www/html/suitecrm; "
        "find /var/www/html/suitecrm -type d -exec chmod 0755 {} +; "
        "find /var/www/html/suitecrm -type f -exec chmod 0644 {} +; "
        "chmod -R u+rwX,g+rwX /var/www/html/suitecrm/cache /var/www/html/suitecrm/custom "
        "/var/www/html/suitecrm/modules /var/www/html/suitecrm/upload; "
        "cat >/etc/apache2/conf-available/bkc-suitecrm.conf <<'APACHE'\n"
        "<Directory /var/www/html/suitecrm>\n"
        "    AllowOverride All\n"
        "    Require all granted\n"
        "</Directory>\n"
        "APACHE\n"
        "a2enconf bkc-suitecrm >/dev/null 2>&1 || true; "
        "printf '%s\\n' '<!doctype html><title>FooBar CRM</title><h1>FooBar CRM</h1><p>SuiteCRM is staged at <a href=\"/suitecrm/\">/suitecrm/</a>.</p><p>Installer database: localhost / suitecrm / suitecrm / changeme123</p><p>Demo admin convention: admin / changeme123</p>' > /var/www/html/index.html; "
        "systemctl restart apache2; "
        "systemctl is-active apache2 mariadb; "
        "curl -fsS http://localhost/suitecrm/ >/tmp/bkc-validate-suitecrm.html; "
        "grep -Ei 'SuiteCRM|Install|Setup|Login' /tmp/bkc-validate-suitecrm.html | head -n 8; "
        f"printf '%s\\n' 'suitecrm_version={version}'"
    )
    output = _foobar_guest_exec(vmid, command, timeout=1800)
    _set_stage(run_id, stage_name, "complete", f"FooBar SuiteCRM v{version} staged.")
    append_event(run_id, "info", stage_name, output[-2400:] if output else f"suitecrm v{version} staged")


def _run_foobar_service_kanboard(run_id: str, stage_name: str) -> None:
    _, _, values = _foobar_services_context()
    target = _foobar_app_target_by_role(values, "tickets")
    vmid = int(target["vmid"])
    hostname = str(target.get("hostname") or target["name"]).split(".", 1)[0]
    version = str(values.get("kanboard_version") or "1.2.52").strip().lstrip("v")
    if not version:
        raise PipelineExecutionError("kanboard_version is required.")
    archive_url = f"https://github.com/kanboard/kanboard/archive/refs/tags/v{version}.tar.gz"
    command = (
        "set -e; export DEBIAN_FRONTEND=noninteractive; "
        f"hostnamectl set-hostname {shlex.quote(hostname)}; "
        "apt-get -o DPkg::Lock::Timeout=600 update; "
        "apt-get -o DPkg::Lock::Timeout=600 install -y "
        "apache2 libapache2-mod-php php php-cli php-sqlite3 php-mbstring php-xml php-gd php-curl php-zip "
        "sqlite3 curl wget ca-certificates tar; "
        "tmp=$(mktemp -d); trap 'rm -rf \"$tmp\"' EXIT; "
        f"wget -qO \"$tmp/kanboard.tar.gz\" {shlex.quote(archive_url)}; "
        "rm -rf /var/www/html/kanboard; "
        "install -d -m 0755 /var/www/html/kanboard; "
        "tar -xzf \"$tmp/kanboard.tar.gz\" -C /var/www/html/kanboard --strip-components=1; "
        "install -d -m 0775 /var/www/html/kanboard/data /var/www/html/kanboard/plugins; "
        "chown -R www-data:www-data /var/www/html/kanboard/data /var/www/html/kanboard/plugins; "
        "find /var/www/html/kanboard -type d -exec chmod 0755 {} +; "
        "find /var/www/html/kanboard -type f -exec chmod 0644 {} +; "
        "chmod -R u+rwX,g+rwX /var/www/html/kanboard/data /var/www/html/kanboard/plugins; "
        "printf '%s\\n' '<!doctype html><title>FooBar Tickets</title><h1>FooBar Tickets</h1><p>Kanboard is installed at <a href=\"/kanboard/\">/kanboard/</a>.</p><p>Initial login: admin / admin</p>' > /var/www/html/index.html; "
        "systemctl enable --now apache2; "
        "systemctl restart apache2; "
        "systemctl is-active apache2; "
        "curl -fsS http://localhost/kanboard/ >/tmp/bkc-validate-kanboard.html; "
        "grep -Ei 'Kanboard|Username|Password' /tmp/bkc-validate-kanboard.html | head -n 5; "
        f"printf '%s\\n' 'kanboard_version={version}'"
    )
    output = _foobar_guest_exec(vmid, command, timeout=1800)
    _set_stage(run_id, stage_name, "complete", f"FooBar Kanboard v{version} installed.")
    append_event(run_id, "info", stage_name, output[-2400:] if output else f"kanboard v{version} installed")


def _run_foobar_service_validate(run_id: str, stage_name: str) -> None:
    _, _, values = _foobar_services_context()
    identity = _foobar_service_identity_target(values)
    crm = _foobar_app_target_by_role(values, "crm")
    tickets = _foobar_app_target_by_role(values, "tickets")
    checks = [
        (
            identity,
            "set -e; systemctl is-active slapd apache2 smbd; test -d /srv/foobar/homes/joe.user; test -f /etc/phpldapadmin/config.php; curl -fsS http://localhost/phpldapadmin/ >/tmp/bkc-validate-identity.html; grep -Ei 'phpLDAPadmin|Authenticate|Login|Username' /tmp/bkc-validate-identity.html | head -n 5",
        ),
        (
            crm,
            "set -e; systemctl is-active apache2 mariadb; test -d /var/www/html/suitecrm/cache; curl -fsS http://localhost/suitecrm/ >/tmp/bkc-validate-suitecrm.html; grep -Ei 'SuiteCRM|Install|Setup|Login' /tmp/bkc-validate-suitecrm.html | head -n 8",
        ),
        (
            tickets,
            "set -e; systemctl is-active apache2; test -d /var/www/html/kanboard/data; curl -fsS http://localhost/kanboard/ >/tmp/bkc-validate-kanboard.html; grep -Ei 'Kanboard|Username|Password' /tmp/bkc-validate-kanboard.html | head -n 5",
        ),
    ]
    evidence = []
    for target, command in checks:
        output = _foobar_guest_exec(int(target["vmid"]), command, timeout=120)
        evidence.append({"vmid": target["vmid"], "name": target["name"], "evidence": output[-700:]})
    _store_run_extra(run_id, {"foobar_service_evidence": evidence})
    append_event(run_id, "info", stage_name, json.dumps(evidence, sort_keys=True))
    _set_stage(run_id, stage_name, "complete", "FooBar services validated.")


def _run_foobar_service_relationships(run_id: str, stage_name: str) -> None:
    pipeline, _, values = _foobar_services_context()
    payload = {
        "pipeline_id": pipeline.get("id"),
        "tenant": values.get("tenant_slug"),
        "identity": _foobar_service_identity_target(values),
        "applications": _foobar_service_app_targets(values),
        "users": _foobar_usernames(values),
        "handoff": "small-office-foobar-workstation-personalize",
    }
    append_event(run_id, "info", stage_name, json.dumps(payload, sort_keys=True))
    _set_stage(run_id, stage_name, "complete", "FooBar service relationships recorded.")


def _openstack_host_context(run_inputs: dict | None = None) -> tuple[dict, dict, dict]:
    return _folder_pipeline_context("trixie-openstack-host-prepare", run_inputs)


def _openstack_target_hosts(values: dict) -> list[str]:
    hosts = values.get("target_hosts")
    if isinstance(hosts, list):
        normalized = [str(host).strip() for host in hosts if str(host).strip()]
    else:
        normalized = [part.strip() for part in str(hosts or "").replace(",", " ").split() if part.strip()]
    if not normalized:
        raise PipelineExecutionError("trixie-openstack-host-prepare requires at least one target host.")
    return normalized


def _openstack_ssh_user(values: dict) -> str:
    login = values.get("target_login") if isinstance(values.get("target_login"), dict) else {}
    return str(login.get("automation_user") or "root").strip() or "root"


def _openstack_run_host_command(values: dict, host: str, command: str, *, timeout: int = 120) -> str:
    user = _openstack_ssh_user(values)
    return run_remote_command(host=host, user=user, command=command, timeout=timeout)


def _run_openstack_host_firstboot_login(run_id: str, stage_name: str) -> None:
    _, _, values = _openstack_host_context(_run_request_inputs(run_id))
    login = values.get("target_login") if isinstance(values.get("target_login"), dict) else {}
    expected_user = str(login.get("user") or "auzieman").strip() or "auzieman"
    results: dict[str, str] = {}
    command = (
        "set -e; "
        "printf 'hostname='; hostname; "
        f"id {shlex.quote(expected_user)}; "
        "systemctl is-active ssh; "
        "if [ \"$(id -u)\" -eq 0 ]; then echo root-automation-ok; else sudo -n true && echo sudo-nopasswd-ok; fi; "
        "ip -brief addr"
    )
    for host in _openstack_target_hosts(values):
        results[host] = _openstack_run_host_command(values, host, command, timeout=120)
    _store_run_extra(run_id, {"openstack_firstboot_login": results})
    _set_stage(run_id, stage_name, "complete", "OpenStack host first-boot login validated through BKC SSH.")
    append_event(run_id, "info", stage_name, json.dumps(results, sort_keys=True)[-3000:])


def _run_openstack_host_base_normalize(run_id: str, stage_name: str) -> None:
    _, _, values = _openstack_host_context(_run_request_inputs(run_id))
    if not _pipeline_value_truthy(values, "enable_base_normalize"):
        _set_stage(run_id, stage_name, "complete", "Skipped first-boot baseline normalization because enable_base_normalize is false.")
        append_event(run_id, "info", stage_name, "OpenStack first-boot baseline normalization skipped by input.")
        return

    login = values.get("target_login") if isinstance(values.get("target_login"), dict) else {}
    admin_user = str(login.get("user") or "auzieman").strip() or "auzieman"
    expected_hostname = str(values.get("expected_hostname") or "r630-openstack-01").strip() or "r630-openstack-01"
    packages = " ".join(_flatten_package_values(values.get("base_normalize_packages")))
    if not packages:
        packages = "sudo facter curl git ca-certificates lldpd net-tools iputils-arping dnsutils tcpdump pciutils usbutils lshw nmap"
    sudoers_path = f"/etc/sudoers.d/90-bkc-{admin_user}"
    marker_json = json.dumps(
        {
            "admin_user": admin_user,
            "expected_hostname": expected_hostname,
            "host_prepare_pipeline": "trixie-openstack-host-prepare",
            "node_id": "node:physical_machine:r630-openstack-01",
            "profile": "openstack-base-os",
        },
        sort_keys=True,
    )
    command = (
        "set -e; "
        "export DEBIAN_FRONTEND=noninteractive; "
        "apt-get update >/dev/null; "
        f"apt-get install -y --no-install-recommends {packages}; "
        f"hostnamectl set-hostname {shlex.quote(expected_hostname)}; "
        f"printf '%s\\n' {shlex.quote(expected_hostname)} > /etc/hostname; "
        f"cp /etc/hosts /etc/hosts.bkc-before-openstack-normalize; "
        f"grep -v -F {shlex.quote(expected_hostname)} /etc/hosts.bkc-before-openstack-normalize > /etc/hosts; "
        f"printf '%s\\n' {shlex.quote('127.0.1.1 ' + expected_hostname)} >> /etc/hosts; "
        f"id {shlex.quote(admin_user)} >/dev/null; "
        f"usermod -aG sudo {shlex.quote(admin_user)}; "
        f"printf '%s\\n' {shlex.quote(admin_user + ' ALL=(ALL) NOPASSWD:ALL')} > {shlex.quote(sudoers_path)}; "
        f"printf '%s\\n' {shlex.quote('Defaults:' + admin_user + ' !requiretty')} >> {shlex.quote(sudoers_path)}; "
        f"chmod 440 {shlex.quote(sudoers_path)}; "
        "install -d -m 0755 /var/lib/bkc; "
        f"printf '%s\\n' {shlex.quote(marker_json)} > /var/lib/bkc/openstack-firstboot-baseline.json; "
        "systemctl enable --now ssh >/dev/null; "
        "systemctl enable --now lldpd >/dev/null 2>&1 || true; "
        "printf 'hostname='; hostname; "
        f"sudo -n -u {shlex.quote(admin_user)} sudo -n true; "
        "command -v ifconfig; command -v nmap; "
        "ip -brief addr; "
        "printf '\\n-- lldp --\\n'; timeout 8 lldpctl 2>/dev/null || true"
    )
    results: dict[str, str] = {}
    for host in _openstack_target_hosts(values):
        results[host] = _openstack_run_host_command(values, host, command, timeout=600)
    _store_run_extra(run_id, {"openstack_firstboot_normalize": results})
    _set_stage(run_id, stage_name, "complete", "OpenStack host first-boot baseline normalized.")
    append_event(run_id, "info", stage_name, json.dumps(results, sort_keys=True)[-4000:])


def _run_openstack_host_network_sides(run_id: str, stage_name: str) -> None:
    _, _, values = _openstack_host_context(_run_request_inputs(run_id))
    context = values.get("physical_network_context") if isinstance(values.get("physical_network_context"), dict) else {}
    results: dict[str, dict] = {}
    command = "set -e; ip -brief addr; printf '\\n-- routes --\\n'; ip route"
    for host in _openstack_target_hosts(values):
        output = _openstack_run_host_command(values, host, command, timeout=120)
        results[host] = {
            "observed": output,
            "current_bootstrap": context.get("current_bootstrap", {}),
            "planned_management": context.get("planned_management", {}),
            "planned_services": context.get("planned_services", {}),
        }
    _store_run_extra(run_id, {"openstack_network_sides": results})
    _set_stage(run_id, stage_name, "complete", "OpenStack host network sides validated against current bootstrap and planned lab networks.")
    append_event(run_id, "info", stage_name, json.dumps(results, sort_keys=True)[-4000:])


def _openstack_pipeline_file(values: dict, key: str) -> Path:
    pipeline, _, _ = _openstack_host_context()
    folder = _repo_pipeline_folder(pipeline)
    rel_path = str(values.get(key) or "").strip()
    if not rel_path:
        raise PipelineExecutionError(f"{key} is required.")
    path = (folder / rel_path).resolve()
    if not path.exists() or folder not in path.parents:
        raise PipelineExecutionError(f"Invalid pipeline file for {key}: {path}")
    return path


def _run_openstack_host_package_prepare(run_id: str, stage_name: str) -> None:
    _, _, values = _openstack_host_context(_run_request_inputs(run_id))
    if not _pipeline_value_truthy(values, "enable_package_install"):
        _set_stage(run_id, stage_name, "complete", "Skipped OpenStack package prep because enable_package_install is false.")
        append_event(run_id, "info", stage_name, "OpenStack package installation remains gated for tonight's first-boot validation.")
        return
    script_path = _openstack_pipeline_file(values, "prepare_script")
    package_values = values.get("openstack_host_packages")
    packages = " ".join(_flatten_package_values(package_values))
    environment = {
        "BKC_OPENSTACK_PACKAGES": packages,
        "BKC_NEUTRON_MODE": str(values.get("openstack_network_mode") or "ovs"),
        "BKC_NEUTRON_EXTERNAL_BRIDGE": str(values.get("neutron_external_bridge") or "br-ex"),
        "BKC_NEUTRON_PHYSNET": str(values.get("neutron_physnet") or "physnet1"),
        "BKC_ENABLE_NETWORK_CONFIG": "true" if _pipeline_value_truthy(values, "enable_network_config") else "false",
        "BKC_OPENSTACK_SMOKE_MODE": "true" if _pipeline_value_truthy(values, "smoke_mode") else "false",
    }
    env_prefix = " ".join(f"{key}={shlex.quote(value)}" for key, value in environment.items())
    results: dict[str, str] = {}
    for host in _openstack_target_hosts(values):
        remote_path = "/tmp/bkc-prepare-openstack-neutron-host.sh"
        upload_remote_bytes(
            host=host,
            user=_openstack_ssh_user(values),
            remote_path=remote_path,
            content=script_path.read_bytes(),
            mode=0o700,
            timeout=60,
        )
        results[host] = _openstack_run_host_command(
            values,
            host,
            f"set -e; {env_prefix} {remote_path}",
            timeout=1200,
        )
    _store_run_extra(run_id, {"openstack_package_prepare": results})
    _set_stage(run_id, stage_name, "complete", "OpenStack host packages prepared.")
    append_event(run_id, "info", stage_name, json.dumps(results, sort_keys=True)[-4000:])


def _run_openstack_host_neutron_validate(run_id: str, stage_name: str) -> None:
    _, _, values = _openstack_host_context(_run_request_inputs(run_id))
    if not _pipeline_value_truthy(values, "enable_package_install"):
        _set_stage(run_id, stage_name, "complete", "Skipped Neutron readiness validation because package prep is gated off.")
        append_event(run_id, "info", stage_name, "Neutron readiness validation waits for enable_package_install=true.")
        return
    script_path = _openstack_pipeline_file(values, "validate_script")
    environment = {
        "BKC_NEUTRON_MODE": str(values.get("openstack_network_mode") or "ovs"),
        "BKC_NEUTRON_EXTERNAL_BRIDGE": str(values.get("neutron_external_bridge") or "br-ex"),
        "BKC_NEUTRON_PHYSNET": str(values.get("neutron_physnet") or "physnet1"),
        "BKC_OPENSTACK_SMOKE_MODE": "true" if _pipeline_value_truthy(values, "smoke_mode") else "false",
    }
    env_prefix = " ".join(f"{key}={shlex.quote(value)}" for key, value in environment.items())
    results: dict[str, str] = {}
    for host in _openstack_target_hosts(values):
        remote_path = "/tmp/bkc-validate-openstack-neutron-host.sh"
        upload_remote_bytes(
            host=host,
            user=_openstack_ssh_user(values),
            remote_path=remote_path,
            content=script_path.read_bytes(),
            mode=0o700,
            timeout=60,
        )
        results[host] = _openstack_run_host_command(
            values,
            host,
            f"set -e; {env_prefix} {remote_path}",
            timeout=180,
        )
    _store_run_extra(run_id, {"openstack_neutron_validate": results})
    _set_stage(run_id, stage_name, "complete", "OpenStack Neutron host readiness validated.")
    append_event(run_id, "info", stage_name, json.dumps(results, sort_keys=True)[-4000:])


def _run_openstack_host_image_cache(run_id: str, stage_name: str) -> None:
    _, _, values = _openstack_host_context(_run_request_inputs(run_id))
    if not _pipeline_value_truthy(values, "enable_image_cache"):
        _set_stage(run_id, stage_name, "complete", "Skipped OpenStack image cache because enable_image_cache is false.")
        append_event(run_id, "info", stage_name, "OpenStack image cache remains gated until explicitly enabled.")
        return

    cache_dir = str(values.get("image_cache_dir") or "/var/lib/bkc/openstack-images").strip()
    if not cache_dir.startswith("/var/lib/bkc/"):
        raise PipelineExecutionError(f"Refusing to cache OpenStack images outside /var/lib/bkc: {cache_dir}")
    assets = values.get("openstack_image_assets")
    if not isinstance(assets, list) or not assets:
        raise PipelineExecutionError("openstack_image_assets must contain at least one asset.")

    asset_lines: list[str] = []
    for asset in assets:
        if not isinstance(asset, dict):
            continue
        name = str(asset.get("name") or "").strip()
        url = str(asset.get("url") or "").strip()
        filename = str(asset.get("filename") or Path(urllib.parse.urlparse(url).path).name).strip()
        image_format = str(asset.get("format") or "qcow2").strip()
        min_bytes = int(asset.get("min_bytes") or 1)
        if not name or not url or not filename:
            raise PipelineExecutionError(f"Invalid OpenStack image asset: {asset}")
        if "/" in filename or filename in {".", ".."}:
            raise PipelineExecutionError(f"Invalid OpenStack image filename: {filename}")
        asset_lines.append("\t".join([name, url, filename, image_format, str(min_bytes)]))

    if not asset_lines:
        raise PipelineExecutionError("No valid OpenStack image assets were provided.")

    asset_table = "\n".join(asset_lines)
    command = (
        "set -euo pipefail; "
        f"cache_dir={shlex.quote(cache_dir)}; "
        "install -d -m 0755 \"$cache_dir\"; "
        "manifest=\"$cache_dir/manifest.jsonl\"; : > \"$manifest\"; "
        "while IFS=$'\\t' read -r name url filename image_format min_bytes; do "
        "  [ -n \"$name\" ] || continue; "
        "  dest=\"$cache_dir/$filename\"; tmp=\"$dest.tmp\"; "
        "  if [ ! -s \"$dest\" ] || [ \"$(stat -c %s \"$dest\")\" -lt \"$min_bytes\" ]; then "
        "    rm -f \"$tmp\"; "
        "    curl -fL --retry 3 --retry-delay 3 --connect-timeout 20 -o \"$tmp\" \"$url\"; "
        "    mv \"$tmp\" \"$dest\"; "
        "  fi; "
        "  size=$(stat -c %s \"$dest\"); "
        "  if [ \"$size\" -lt \"$min_bytes\" ]; then echo \"asset too small: $filename $size < $min_bytes\" >&2; exit 13; fi; "
        "  sha=$(sha256sum \"$dest\" | awk '{print $1}'); "
        "  printf '{\"name\":\"%s\",\"filename\":\"%s\",\"format\":\"%s\",\"bytes\":%s,\"sha256\":\"%s\",\"url\":\"%s\"}\\n' "
        "    \"$name\" \"$filename\" \"$image_format\" \"$size\" \"$sha\" \"$url\" >> \"$manifest\"; "
        "done <<'BKC_OPENSTACK_IMAGES'\n"
        f"{asset_table}\n"
        "BKC_OPENSTACK_IMAGES\n"
        "ls -lh \"$cache_dir\"; printf '\\n-- manifest --\\n'; cat \"$manifest\""
    )

    results: dict[str, str] = {}
    for host in _openstack_target_hosts(values):
        results[host] = _openstack_run_host_command(values, host, command, timeout=1800)
    _store_run_extra(run_id, {"openstack_image_cache": results})
    _set_stage(run_id, stage_name, "complete", "OpenStack QCOW/image assets cached on the prepared host.")
    append_event(run_id, "info", stage_name, json.dumps(results, sort_keys=True)[-5000:])


def _openstack_kolla_context(run_inputs: dict | None = None) -> tuple[dict, dict, dict]:
    return _folder_pipeline_context("openstack-kolla-single-node-install", run_inputs)


def _openstack_kolla_pipeline_file(values: dict, key: str) -> Path:
    pipeline, _, _ = _openstack_kolla_context()
    folder = _repo_pipeline_folder(pipeline)
    rel_path = str(values.get(key) or "").strip()
    if not rel_path:
        raise PipelineExecutionError(f"{key} is required.")
    path = (folder / rel_path).resolve()
    if not path.exists() or folder not in path.parents:
        raise PipelineExecutionError(f"Invalid pipeline file for {key}: {path}")
    return path


def _secret_ref_literal(value: object, *, default: str = "") -> str:
    text = str(value or "").strip()
    if text.startswith("demo-value:"):
        return text.split(":", 1)[1]
    return text or default


def _openstack_kolla_operation_mode(run_id: str) -> str:
    _, _, values = _openstack_kolla_context(_run_request_inputs(run_id))
    operation_mode = str(values.get("operation_mode") or "review").strip().lower()
    valid_modes = {"review", "prepare", "precheck", "deploy", "validate"}
    if operation_mode not in valid_modes:
        raise PipelineExecutionError(
            f"Invalid Kolla operation_mode {operation_mode!r}; expected one of {sorted(valid_modes)}."
        )
    return operation_mode


def _run_openstack_kolla_phase(run_id: str, stage: dict) -> None:
    stage_name = str(stage["name"])
    phase = str(stage.get("phase") or "").strip()
    if not phase:
        raise PipelineExecutionError(f"{stage_name} is missing a Kolla phase.")

    _, _, values = _openstack_kolla_context(_run_request_inputs(run_id))
    operation_mode = _openstack_kolla_operation_mode(run_id)
    operation_modes = {str(item).strip().lower() for item in stage.get("operation_modes", []) if str(item).strip()}
    if operation_modes and operation_mode not in operation_modes:
        detail = f"Skipped Kolla phase {phase}; operation_mode is {operation_mode}."
        _set_stage(run_id, stage_name, "complete", detail)
        append_event(run_id, "info", stage_name, detail)
        return

    script_path = _openstack_kolla_pipeline_file(values, "install_script")
    admin_password = _secret_ref_literal(values.get("keystone_admin_password_ref"), default="changeme123")
    environment = {
        "BKC_KOLLA_PHASE": phase,
        "BKC_KOLLA_WORKSPACE": str(values.get("kolla_workspace") or "/opt/bkc/kolla"),
        "BKC_KOLLA_VENV": str(values.get("kolla_venv") or "/opt/bkc/kolla-venv"),
        "BKC_KOLLA_CONFIG_DIR": str(values.get("kolla_config_dir") or "/etc/kolla"),
        "BKC_EXPECTED_HOSTNAME": str(values.get("expected_hostname") or "r630-openstack-01"),
        "BKC_KOLLA_SOURCE_REF": str(values.get("kolla_source_ref") or "stable/2026.1"),
        "BKC_KOLLA_BASE_DISTRO": str(values.get("kolla_base_distro") or "debian"),
        "BKC_KOLLA_INSTALL_TYPE": str(values.get("kolla_install_type") or "source"),
        "BKC_OPENSTACK_RELEASE": str(values.get("openstack_release") or "2026.1"),
        "BKC_KOLLA_USE_TEST_IMAGES": "yes" if _pipeline_value_truthy(values, "use_test_images") else "no",
        "BKC_KOLLA_PRIMARY_INTERFACE": str(values.get("primary_interface") or "eno1"),
        "BKC_KOLLA_NETWORK_INTERFACE": str(values.get("network_interface") or "bkc-mgmt0"),
        "BKC_KOLLA_MANAGEMENT_ADDRESS": str(values.get("management_address") or "10.20.0.31/24"),
        "BKC_KOLLA_EXTERNAL_INTERFACE": str(values.get("neutron_external_interface") or "eno2"),
        "BKC_KOLLA_INTERNAL_VIP": str(values.get("kolla_internal_vip_address") or "10.20.0.30"),
        "BKC_KOLLA_ENABLE_HAPROXY": "yes" if _pipeline_value_truthy(values, "enable_haproxy") else "no",
        "BKC_KOLLA_ENABLE_CINDER": "yes" if _pipeline_value_truthy(values, "enable_cinder") else "no",
        "BKC_KOLLA_ENABLE_PROVIDER_NETWORKS": "yes"
        if _pipeline_value_truthy(values, "enable_neutron_provider_networks")
        else "no",
        "BKC_KEYSTONE_ADMIN_PASSWORD": admin_password,
        "BKC_HORIZON_URL": str(values.get("horizon_url") or "http://192.168.1.242/horizon/"),
        "BKC_KEYSTONE_URL": str(values.get("keystone_url") or "http://192.168.1.242:5000/v3"),
    }
    env_prefix = " ".join(f"{key}={shlex.quote(value)}" for key, value in environment.items())
    results: dict[str, str] = {}
    for host in _openstack_target_hosts(values):
        remote_path = f"/tmp/bkc-kolla-single-node-{phase}.sh"
        upload_remote_bytes(
            host=host,
            user=_openstack_ssh_user(values),
            remote_path=remote_path,
            content=script_path.read_bytes(),
            mode=0o700,
            timeout=60,
        )
        results[host] = _openstack_run_host_command(
            values,
            host,
            f"set -e; {env_prefix} {remote_path}",
            timeout=int(stage.get("timeout") or 1200),
        )
    _store_run_extra(run_id, {f"openstack_kolla_{phase}": results})
    _set_stage(run_id, stage_name, "complete", str(stage.get("complete") or f"Kolla phase {phase} completed."))
    append_event(run_id, "info", stage_name, json.dumps(results, sort_keys=True)[-6000:])


def _video_context(pipeline_id: str, run_id: str) -> tuple[dict, dict]:
    pipeline, _, values = _folder_pipeline_context(pipeline_id, _run_request_inputs(run_id))
    return pipeline, values


def _require_video_gates(run_id: str, *names: str) -> dict:
    inputs = _run_request_inputs(run_id)
    missing = [name for name in names if not _truthy(inputs.get(name))]
    if missing:
        raise PipelineExecutionError("Real filming workflow requires enabled gate(s): " + ", ".join(missing))
    return inputs


def _upload_and_run_pipeline_script(pipeline: dict, values: dict, script_name: str, *, host: str, env: dict | None = None, timeout: int = 600) -> str:
    script = _repo_pipeline_folder(pipeline) / "scripts" / script_name
    if not script.is_file():
        raise PipelineExecutionError(f"Pipeline script is missing: {script}")
    content = script.read_bytes()
    readiness = script.parent / "00-bkc-readiness.sh"
    if readiness.is_file() and script.name != readiness.name:
        content = readiness.read_bytes() + b"\n" + content
    remote = f"/tmp/bkc-{re.sub(r'[^A-Za-z0-9_.-]', '-', script_name)}"
    upload_remote_bytes(host=host, user="root", remote_path=remote, content=content, mode=0o700, timeout=90)
    exports = " ".join(f"{key}={shlex.quote(str(value))}" for key, value in (env or {}).items())
    return run_remote_command(host=host, user="root", command=f"set -e; {exports} {shlex.quote(remote)}", timeout=timeout)


def _video_bmc_pxe_reset(values: dict) -> str:
    hosts = values.get("physical_hosts") if isinstance(values.get("physical_hosts"), list) else []
    if not hosts:
        raise PipelineExecutionError("Physical host/BMC definition is missing.")
    host = hosts[0]
    address = str(host.get("bmc_observed_address") or "").strip()
    username, password = _resolve_bmc_credentials(str(host.get("bmc_credential_ref") or ""))
    _, body = _redfish_request_via_ns1(values, address, "/redfish/v1/Systems", username, password)
    members = json.loads(body or "{}").get("Members", [])
    if not members:
        raise PipelineExecutionError(f"No Redfish system found on {address}.")
    system_path = str(members[0].get("@odata.id") or "")
    _, body = _redfish_request_via_ns1(values, address, system_path, username, password)
    system = json.loads(body or "{}")
    _redfish_request_via_ns1(values, address, system_path, username, password, method="PATCH", payload={"Boot": {"BootSourceOverrideEnabled": "Once", "BootSourceOverrideTarget": "Pxe"}})
    reset = system.get("Actions", {}).get("#ComputerSystem.Reset", {}).get("target") or f"{system_path}/Actions/ComputerSystem.Reset"
    def reset_and_verify(reset_type: str, expected_state: str) -> None:
        try:
            _redfish_request_via_ns1(
                values,
                address,
                reset,
                username,
                password,
                method="POST",
                payload={"ResetType": reset_type},
            )
        except PipelineExecutionError as exc:
            # iDRAC8 may close the HTTP connection while applying a reset. The
            # state transition below is the authoritative acknowledgement.
            if "returned no HTTP status" not in str(exc):
                raise
        deadline = time.time() + 120
        last_state = ""
        while time.time() < deadline:
            try:
                _, state_body = _redfish_request_via_ns1(
                    values, address, system_path, username, password, timeout=15
                )
                last_state = str(json.loads(state_body or "{}").get("PowerState") or "")
                if last_state.lower() == expected_state.lower():
                    return
            except PipelineExecutionError:
                pass
            time.sleep(5)
        raise PipelineExecutionError(
            f"iDRAC reset {reset_type} did not reach PowerState={expected_state}; last state was {last_state or 'unavailable'}."
        )

    # iDRAC 8 can acknowledge ForceRestart without actually leaving the running
    # OS. A positive off/on transition is slower but gives the PXE-once override
    # a deterministic cold-boot boundary for filming and unattended installs.
    if str(system.get("PowerState") or "").lower() == "on":
        reset_and_verify("ForceOff", "Off")
    reset_and_verify("On", "On")
    return address


def _video_bmc_ipmi_pxe_reset_via_ns1(values: dict, address: str, username: str, password: str) -> str:
    """Fallback for older iDRACs whose Redfish HTTPS stack is not responding."""
    quoted = {
        "address": shlex.quote(address),
        "username": shlex.quote(username),
        "password": shlex.quote(password),
    }
    command = (
        "set -e; "
        "command -v ipmitool >/dev/null; "
        f"ipmitool -I lanplus -H {quoted['address']} -U {quoted['username']} -P {quoted['password']} chassis bootdev pxe options=efiboot; "
        f"state=$(ipmitool -I lanplus -H {quoted['address']} -U {quoted['username']} -P {quoted['password']} chassis power status || true); "
        "case \"$state\" in *on*) "
        f"ipmitool -I lanplus -H {quoted['address']} -U {quoted['username']} -P {quoted['password']} chassis power cycle; "
        ";; *) "
        f"ipmitool -I lanplus -H {quoted['address']} -U {quoted['username']} -P {quoted['password']} chassis power on; "
        ";; esac; "
        "printf 'ipmi_pxe_reset=%s\\n' \"$state\""
    )
    return _run_ns1_command(values, command, timeout=60)


def _video_openstack_pxe(run_id: str, stage_name: str) -> None:
    _require_video_gates(run_id, "enable_destructive_install")
    _run_openstack_base_boot_render(run_id, stage_name)
    _run_openstack_base_boot_validate(run_id, stage_name)
    _, values = _video_context("baremetal-openstack-lab-prepare", run_id)
    broad = str(values.get("dhcp_broad_fragment_path") or "/etc/dhcp/dhcpd.d/bkc-provisioning.conf")
    output = _run_ns1_command(
        values,
        f"test -f {shlex.quote(broad)}; ! grep -Eq '^[[:space:]]*(filename|next-server|option[[:space:]]+bootfile-name)[[:space:]]' {shlex.quote(broad)}; "
        f"! grep -Eq '^[[:space:]]*(filename|next-server|option[[:space:]]+bootfile-name)[[:space:]]' {shlex.quote(str(values['dhcp_fragment_path']))}; "
        f"dhcpd -t -cf {shlex.quote(str(values['dhcp_main_path']))}; echo broad-pxe-default=disarmed",
        timeout=60,
    )
    append_event(run_id, "info", stage_name, output[-800:])


def _video_openstack_boot(run_id: str, stage_name: str) -> None:
    _require_video_gates(run_id, "enable_destructive_install")
    _, values = _video_context("baremetal-openstack-lab-prepare", run_id)
    bmc_definition = values["physical_hosts"][0]
    bmc_address = str(bmc_definition["bmc_observed_address"])
    bmc_user, bmc_password = _resolve_bmc_credentials(str(bmc_definition["bmc_credential_ref"]))
    try:
        _, bios_body = _redfish_request_via_ns1(
            values,
            bmc_address,
            "/redfish/v1/Systems/System.Embedded.1/Bios",
            bmc_user,
            bmc_password,
        )
        bios = json.loads(bios_body or "{}")
        actual_boot_mode = str((bios.get("Attributes") or {}).get("BootMode") or "").strip()
        required_boot_mode = str(values.get("required_boot_mode") or "Uefi").strip()
        if actual_boot_mode.lower() != required_boot_mode.lower():
            raise PipelineExecutionError(
                f"Server1 firmware drift: BootMode is {actual_boot_mode or 'unknown'}, "
                f"but pipeline 10 requires {required_boot_mode}. Correct iDRAC BIOS settings before arming PXE."
            )
        append_event(run_id, "info", stage_name, f"server1_firmware_boot_mode={actual_boot_mode}")
    except PipelineExecutionError as exc:
        append_event(
            run_id,
            "warning",
            stage_name,
            f"Server1 Redfish BIOS guard unavailable; continuing with IPMI PXE fallback after DHCP validation: {exc}",
        )
    mac = str(values["physical_hosts"][0]["provisioning_mac"])
    lease, http_host = str(values["installer_lease_address"]), str(values["physical_pxe_http_host"])
    fragment, main = str(values["dhcp_fragment_path"]), str(values["dhcp_main_path"])
    content = f'''# BKC Server1 one-shot Debian lane. Exact LOM only.
option architecture-type code 93 = unsigned integer 16;
host r630-openstack-01-pxe {{
  hardware ethernet {mac};
  fixed-address {lease};
  next-server {http_host};
  if exists user-class and option user-class = "iPXE" {{
    filename "http://{http_host}/pxe/debian-trixie.ipxe";
  }} elsif option architecture-type = 00:07 {{
    filename "ipxe-snponly-x86_64.efi";
    option bootfile-name "ipxe-snponly-x86_64.efi";
  }} elsif option architecture-type = 00:09 {{
    filename "ipxe-snponly-x86_64.efi";
    option bootfile-name "ipxe-snponly-x86_64.efi";
  }} else {{
    filename "undionly.kpxe";
    option bootfile-name "undionly.kpxe";
  }}
}}
'''
    upload_remote_bytes(host=str(values["target_host"]), user="root", remote_path=fragment, content=content.encode(), mode=0o644, timeout=60)
    include_line = f'include "{fragment}";'
    command = f"set -e; grep -Fqx {shlex.quote(include_line)} {shlex.quote(main)} || printf '%s\\n' {shlex.quote(include_line)} >> {shlex.quote(main)}; dhcpd -t -cf {shlex.quote(main)}; systemctl restart dhcpd"
    _run_ns1_command(values, command, timeout=60)
    baseline_text = _run_ns1_command(values, "wc -l </var/log/nginx/access.log", timeout=20)
    try:
        baseline = int(baseline_text.strip())
    except ValueError as exc:
        raise PipelineExecutionError(f"Could not record Server1 Nginx log baseline: {baseline_text}") from exc
    _store_run_extra(run_id, {"openstack_nginx_log_baseline": baseline})

    try:
        try:
            bmc = _video_bmc_pxe_reset(values)
        except PipelineExecutionError as exc:
            append_event(run_id, "warning", stage_name, f"Server1 Redfish PXE reset unavailable; using IPMI fallback: {exc}")
            bmc = _video_bmc_ipmi_pxe_reset_via_ns1(values, bmc_address, bmc_user, bmc_password)
    except Exception:
        # Arming DHCP precedes the Redfish reset.  If iDRAC is unavailable,
        # restore the exact-MAC fragment to lease-only so a later manual boot
        # cannot accidentally enter an unobserved destructive install.
        lease_only = (
            "# BKC Server1 persistent lease-only identity. No PXE boot options.\n"
            "host r630-openstack-01-lease {\n"
            f"  hardware ethernet {mac};\n"
            f"  fixed-address {lease};\n"
            "}\n"
        )
        upload_remote_bytes(
            host=str(values["target_host"]),
            user="root",
            remote_path=fragment,
            content=lease_only.encode(),
            mode=0o644,
            timeout=60,
        )
        _run_ns1_command(
            values,
            f"dhcpd -t -cf {shlex.quote(main)}; systemctl restart dhcpd",
            timeout=60,
        )
        raise
    append_event(run_id, "warning", stage_name, f"Server1 one-shot PXE cold boot requested through iDRAC {bmc}; MAC {mac}.")
    _set_stage(run_id, stage_name, "complete", "Server1 booted directly into unattended Debian Installer; Partman owns the declared disk replacement.")


def _wait_ssh(host: str, *, timeout: int, command: str = "true") -> str:
    deadline, last = time.time() + timeout, ""
    while time.time() < deadline:
        try:
            return run_remote_command(host=host, user="root", command=command, timeout=20)
        except Exception as exc:  # noqa: BLE001
            last = str(exc)
            time.sleep(15)
    raise PipelineExecutionError(f"Timed out waiting for root SSH on {host}: {last}")


def _video_openstack_firstboot(run_id: str, stage_name: str) -> None:
    _, values = _video_context("baremetal-openstack-lab-prepare", run_id)
    host = str(values["installer_lease_address"])
    run = get_run(run_id) or {}
    extra = run.get("extra") if isinstance(run.get("extra"), dict) else {}
    baseline = int(extra.get("openstack_nginx_log_baseline") or 0)
    if baseline < 1:
        raise PipelineExecutionError("Server1 Nginx log baseline is missing; refusing stale installer evidence.")
    preseed_path = urllib.parse.urlparse(str(values["physical_preseed_url"])).path
    deadline, evidence = time.time() + 720, ""
    while time.time() < deadline:
        evidence = _run_ns1_command(
            values,
            f"tail -n +{baseline + 1} /var/log/nginx/access.log | "
            f"grep -F {shlex.quote(host)} | grep -F {shlex.quote(preseed_path)} | tail -1 || true",
            timeout=20,
        )
        if evidence and (" 200 " in evidence or '\" 200 ' in evidence):
            break
        time.sleep(10)
    if not evidence:
        raise PipelineExecutionError("Fresh Server1 Debian preseed handoff was not observed in the NS1 HTTP log.")
    fragment = str(values["dhcp_fragment_path"])
    mac = str(values["physical_hosts"][0]["provisioning_mac"])
    lease = str(values["installer_lease_address"])
    lease_only = (
        "# BKC Server1 persistent lease-only identity. No PXE boot options.\n"
        "host r630-openstack-01-lease {\n"
        f"  hardware ethernet {mac};\n"
        f"  fixed-address {lease};\n"
        "}\n"
    )
    upload_remote_bytes(
        host=str(values["target_host"]),
        user="root",
        remote_path=fragment,
        content=lease_only.encode(),
        mode=0o644,
        timeout=60,
    )
    _run_ns1_command(
        values,
        f"! grep -Eq '^[[:space:]]*(filename|next-server|option[[:space:]]+bootfile-name)[[:space:]]' {shlex.quote(fragment)}; "
        f"dhcpd -t -cf {shlex.quote(str(values['dhcp_main_path']))}; systemctl restart dhcpd",
        timeout=60,
    )
    # The R630 keeps its legacy NIC ahead of "Hard drive C:" in the permanent
    # boot list.  A consumed one-shot PXE override therefore is not enough:
    # explicitly hand the installer's reboot to disk after removing PXE from
    # DHCP.  Future provisioning runs still work because _video_bmc_pxe_reset
    # applies a fresh one-shot PXE override.
    bmc = values["physical_hosts"][0]
    address = str(bmc.get("bmc_observed_address") or "").strip()
    username, password = _resolve_bmc_credentials(str(bmc.get("bmc_credential_ref") or ""))
    disk_handoff = "next boot pinned to Hdd"
    try:
        _, systems_body = _redfish_request_via_ns1(values, address, "/redfish/v1/Systems", username, password)
        members = json.loads(systems_body or "{}").get("Members", [])
        if not members:
            raise PipelineExecutionError(f"No Redfish system found while setting Server1 disk boot at {address}.")
        system_path = str(members[0].get("@odata.id") or "")
        _redfish_request_via_ns1(
            values,
            address,
            system_path,
            username,
            password,
            method="PATCH",
            payload={"Boot": {"BootSourceOverrideEnabled": "Once", "BootSourceOverrideTarget": "Hdd"}},
        )
    except Exception as exc:  # DHCP is already safe; disk fallback can proceed without iDRAC.
        disk_handoff = f"iDRAC disk override unavailable ({exc}); lease-only DHCP permits firmware disk fallback"
        append_event(run_id, "warning", stage_name, disk_handoff)
    append_event(
        run_id,
        "info",
        stage_name,
        f"Fresh preseed handoff observed; Server1 PXE disarmed, lease-only {lease} retained, and {disk_handoff}: {evidence[-1000:]}",
    )
    output = _wait_ssh(host, timeout=3300, command="hostname; test -s /var/lib/bkc/base-provisioning.json; cat /var/lib/bkc/base-provisioning.json")
    append_event(run_id, "info", stage_name, output[-1800:])
    _set_stage(run_id, stage_name, "complete", f"Trixie first boot enrolled over BKC SSH at {host}; Server1 PXE disarmed.")


def _video_openstack_one_shot(run_id: str, stage_name: str) -> None:
    """Own the complete destructive Server1 base-OS transaction as one stage."""
    _require_video_gates(run_id, "enable_destructive_install")
    _video_openstack_pxe(run_id, stage_name)
    _set_stage(run_id, stage_name, "active", "PXE assets validated; cold-booting Server1 into the unattended installer.")
    _video_openstack_boot(run_id, stage_name)
    _set_stage(run_id, stage_name, "active", "Server1 entered one-shot PXE; waiting for destructive Trixie install and SSH first boot.")
    _video_openstack_firstboot(run_id, stage_name)


def _video_openstack_install(run_id: str, stage_name: str) -> None:
    _require_video_gates(run_id, "enable_openstack_install")
    _, values = _video_context("baremetal-openstack-lab-prepare", run_id)
    host = str(values["installer_lease_address"])
    public_address = str(values.get("openstack_public_address") or host)
    management_address = str(values.get("openstack_management_address") or host)
    management_cidr = str(values.get("openstack_management_cidr") or f"{management_address}/24")
    internal_vip = str(values.get("openstack_internal_vip") or management_address)
    management_gateway = str(values.get("openstack_management_gateway") or values.get("proxmox_gateway") or "10.20.0.9")
    native = pipeline_by_id("native-openstack-all-in-one")
    if not native:
        raise PipelineExecutionError("Native OpenStack pipeline is missing.")
    env = {
        "BKC_OPENSTACK_PUBLIC_ADDRESS": public_address,
        "BKC_OPENSTACK_MANAGEMENT_ADDRESS": management_address,
        "BKC_OPENSTACK_MANAGEMENT_CIDR": management_cidr,
        "BKC_OPENSTACK_INTERNAL_VIP": internal_vip,
        "BKC_OPENSTACK_MANAGEMENT_GATEWAY": management_gateway,
        "BKC_OPENSTACK_NOVNC_BASE_URL": str(values.get("openstack_novnc_base_url") or "http://swarm1.lab.auzietek.com:8089/vnc_auto.html"),
        "BKC_OPENSTACK_LAB_PASSWORD": str(values.get("target_install_password") or "changeme123"),
    }
    for name in ("01-foundation-keystone-horizon.sh", "02-glance-placement.sh", "03-nova.sh", "04-neutron-ovs.sh"):
        output = _upload_and_run_pipeline_script(native, {}, name, host=host, env=env, timeout=2400)
        append_event(run_id, "info", stage_name, f"{name}: {output[-1600:]}")
    _set_stage(run_id, stage_name, "complete", "All four native OpenStack service phases completed.")


def _video_openstack_validate(run_id: str, stage_name: str) -> None:
    _, values = _video_context("baremetal-openstack-lab-prepare", run_id)
    host = str(values["installer_lease_address"])
    public_address = str(values.get("openstack_public_address") or host)
    management_address = str(values.get("openstack_management_address") or host)
    command = (
        ". /root/admin-openrc; "
        "openstack token issue -f value -c id; "
        "openstack compute service list; "
        "openstack network agent list; "
        f"curl -fsS -o /dev/null {shlex.quote(f'http://{public_address}/horizon/')}; "
        f"curl -fsS -o /dev/null {shlex.quote(f'http://{management_address}:5000/v3')}"
    )
    output = run_remote_command(host=host, user="root", command=command, timeout=180)
    append_event(run_id, "info", stage_name, output[-3000:])
    _set_stage(run_id, stage_name, "complete", "Keystone, Horizon, Nova, Glance, and Neutron responded successfully.")


def _proxmox_env(values: dict) -> dict:
    return {"BKC_PROXMOX_MAC": values["provisioning_mac"], "BKC_PROXMOX_LEASE": values["installer_lease_address"], "BKC_PROXMOX_HTTP_HOST": values["provisioning_host"], "BKC_PROXMOX_ISO_PATH": values["installer_iso_path"], "BKC_PROXMOX_ISO_URL": values["installer_iso_url"], "BKC_PROXMOX_ISO_SHA256": values["installer_iso_sha256"], "BKC_PROXMOX_IPXE_PATH": values["ipxe_script_path"], "BKC_PROXMOX_DHCP_FRAGMENT": values["dhcp_fragment_path"], "BKC_PROXMOX_DHCP_MAIN": values["dhcp_main_path"]}


def _video_proxmox_media(run_id: str, stage_name: str) -> None:
    _require_video_gates(run_id, "enable_pxe_arm", "enable_destructive_install")
    _, values = _video_context("baremetal-proxmox-trial-prepare", run_id)
    ssh = load_integrations()["ssh"]
    public_key = str(read_key_pair(ssh["private_key_path"], ssh["public_key_path"]).get("public_key") or "").strip()
    if not public_key.startswith("ssh-"):
        raise PipelineExecutionError("BKC SSH public key is missing; refusing a Proxmox install that cannot be validated.")
    iso = str(values["installer_iso_path"])
    iso_url = str(values["installer_iso_url"])
    pxe_dir = str(Path(iso).parent / "pxeboot")
    fragment = str(values["dhcp_fragment_path"])
    broad = str(
        values.get("dhcp_broad_fragment_path")
        or "/etc/dhcp/dhcpd.d/bkc-provisioning.conf"
    )
    main = str(values["dhcp_main_path"])
    answer_checks = {
        f'fqdn = "{values["proxmox_hostname"]}"',
        'root-password = "changem123"',
        f'cidr = "{values["proxmox_management_address"]}/24"',
        f'dns = "{values["proxmox_dns"]}"',
        f'gateway = "{values["proxmox_gateway"]}"',
        'disk-list = ["sda"]',
        f'filter.ID_NET_NAME_MAC = "*{str(values["provisioning_mac"]).replace(":", "")}"',
    }
    grep_checks = " ".join(
        f"grep -Fqx {shlex.quote(item)} \"$mnt/answer.toml\";" for item in sorted(answer_checks)
    )
    command = (
        "set -e; "
        f"printf '%s  %s\\n' {shlex.quote(str(values['installer_iso_sha256']))} {shlex.quote(iso)} | sha256sum -c -; "
        f"test -s {shlex.quote(pxe_dir + '/linux26')}; test -s {shlex.quote(pxe_dir + '/initrd')}; "
        f"test ! -s {shlex.quote(fragment)}; "
        f"! grep -Eq '^[[:space:]]*(filename|next-server|option[[:space:]]+bootfile-name)[[:space:]]' {shlex.quote(broad)}; "
        f"dhcpd -t -cf {shlex.quote(main)}; "
        "mnt=$(mktemp -d); cleanup() { mountpoint -q \"$mnt\" && umount \"$mnt\" || true; rmdir \"$mnt\" 2>/dev/null || true; }; trap cleanup EXIT; "
        f"mount -o loop,ro {shlex.quote(iso)} \"$mnt\"; test -e \"$mnt/auto-installer-capable\"; test -s \"$mnt/answer.toml\"; "
        f"{grep_checks} grep -Fq {shlex.quote(public_key)} \"$mnt/answer.toml\"; "
        f"curl -fsSI {shlex.quote(iso_url)}; "
        f"for asset in linux26 initrd; do expected=$(stat -c %s {shlex.quote(pxe_dir)}/$asset); "
        f"actual=$(curl -fsSI {shlex.quote('http://' + str(values['provisioning_host']) + '/pxe/proxmox/9.2-1/pxeboot')}/$asset | "
        "awk 'BEGIN { IGNORECASE=1 } /^Content-Length:/ { gsub(\"\\r\", \"\", $2); print $2 }' | tail -1); "
        "test \"$actual\" = \"$expected\"; done; "
        "echo bkc-proxmox-preflight=ready"
    )
    out = run_remote_command(host=str(values["provisioning_ssh_host"]), user="root", command=command, timeout=300)
    hosts = values.get("physical_hosts") if isinstance(values.get("physical_hosts"), list) else []
    if not hosts:
        raise PipelineExecutionError("Server2 BMC definition is missing.")
    bmc = hosts[0]
    address = str(bmc.get("bmc_observed_address") or "").strip()
    username, password = _resolve_bmc_credentials(str(bmc.get("bmc_credential_ref") or ""))
    status, body = _redfish_request_via_ns1(values, address, "/redfish/v1/Systems", username, password)
    if status < 200 or status >= 300 or not json.loads(body or "{}").get("Members"):
        raise PipelineExecutionError(f"Server2 iDRAC Redfish preflight failed at {address}.")
    append_event(run_id, "info", stage_name, out[-2000:])
    _set_stage(run_id, stage_name, "complete", "Proxmox ISO/answer intent, current BKC SSH key, PXE payloads, disarmed DHCP, and Server2 iDRAC validated.")


def _video_proxmox_boot(run_id: str, stage_name: str) -> None:
    _require_video_gates(run_id, "enable_pxe_arm", "enable_destructive_install")
    pipeline, values = _video_context("baremetal-proxmox-trial-prepare", run_id)
    ns1 = str(values["provisioning_ssh_host"])
    baseline_text = run_remote_command(
        host=ns1,
        user="root",
        command="wc -l </var/log/nginx/access.log",
        timeout=20,
    )
    try:
        baseline = int(baseline_text.strip())
    except ValueError as exc:
        raise PipelineExecutionError(f"Could not record NS1 Nginx log baseline: {baseline_text}") from exc
    _store_run_extra(run_id, {"proxmox_nginx_log_baseline": baseline})
    out = _upload_and_run_pipeline_script(pipeline, values, "arm-proxmox-one-shot-pxe.sh", host=str(values["provisioning_ssh_host"]), env=_proxmox_env(values), timeout=300)
    bmc = _video_bmc_pxe_reset(values)
    append_event(run_id, "warning", stage_name, out[-1600:] + f"\niDRAC={bmc}")
    _set_stage(run_id, stage_name, "complete", "Server2 PXE armed and one-time PXE reboot requested.")


def _video_proxmox_handoff(run_id: str, stage_name: str) -> None:
    pipeline, values = _video_context("baremetal-proxmox-trial-prepare", run_id)
    ns1 = str(values["provisioning_ssh_host"])
    initrd = "/pxe/proxmox/9.2-1/pxeboot/initrd"
    installer_lease = str(values["installer_lease_address"])
    run = get_run(run_id) or {}
    extra = run.get("extra") if isinstance(run.get("extra"), dict) else {}
    baseline = int(extra.get("proxmox_nginx_log_baseline") or 0)
    if baseline < 1:
        raise PipelineExecutionError("Proxmox Nginx log baseline is missing; refusing to use stale PXE evidence.")
    deadline, evidence = time.time() + 1500, ""
    while time.time() < deadline:
        evidence = run_remote_command(
            host=ns1,
            user="root",
            command=(
                f"tail -n +{baseline + 1} /var/log/nginx/access.log | "
                f"grep -F {shlex.quote(installer_lease)} | "
                f"grep -F {shlex.quote(initrd)} | tail -1 || true"
            ),
            timeout=20,
        )
        if evidence and (" 200 " in evidence or '" 200 ' in evidence):
            break
        time.sleep(10)
    if not evidence:
        raise PipelineExecutionError("Proxmox installer initrd handoff was not observed in the HTTP access log.")
    out = _upload_and_run_pipeline_script(pipeline, values, "disarm-proxmox-one-shot-pxe.sh", host=ns1, env=_proxmox_env(values), timeout=90)
    hosts = values.get("physical_hosts") if isinstance(values.get("physical_hosts"), list) else []
    if not hosts:
        raise PipelineExecutionError("Server2 BMC definition is missing during PXE handoff.")
    bmc = hosts[0]
    address = str(bmc.get("bmc_observed_address") or "").strip()
    username, password = _resolve_bmc_credentials(str(bmc.get("bmc_credential_ref") or ""))
    _, body = _redfish_request_via_ns1(values, address, "/redfish/v1/Systems", username, password)
    members = json.loads(body or "{}").get("Members", [])
    if not members:
        raise PipelineExecutionError(f"No Redfish system found while setting Server2 disk boot at {address}.")
    system_path = str(members[0].get("@odata.id") or "")
    _redfish_request_via_ns1(
        values,
        address,
        system_path,
        username,
        password,
        method="PATCH",
        payload={"Boot": {"BootSourceOverrideEnabled": "Once", "BootSourceOverrideTarget": "Hdd"}},
    )
    append_event(
        run_id,
        "info",
        stage_name,
        evidence[-1200:] + "\n" + out[-800:] + "\nserver2_next_boot=Hdd",
    )
    _set_stage(
        run_id,
        stage_name,
        "complete",
        "Installer payload was delivered, DHCP PXE was disarmed, and Server2 next boot was pinned to disk.",
    )


def _video_proxmox_validate(run_id: str, stage_name: str) -> None:
    _, values = _video_context("baremetal-proxmox-trial-prepare", run_id)
    host = str(values["proxmox_management_address"])
    root_user = str(values.get("proxmox_root_user") or "root@pam")
    root_password = str(values.get("proxmox_root_password") or "changeme123")
    command = (
        "set -e; "
        f"printf '%s\\n' {shlex.quote('root:' + root_password)} | chpasswd; "
        "hostname; pveversion; test -c /dev/kvm; pvesm status; "
        "curl -kfsS -o /dev/null https://127.0.0.1:8006/; "
        "response=$(curl -kfsS --data-urlencode "
        f"username={shlex.quote(root_user)} --data-urlencode password={shlex.quote(root_password)} "
        "https://127.0.0.1:8006/api2/json/access/ticket); "
        "printf '%s' \"$response\" | grep -q ticket; echo proxmox_root_pam_login=valid"
    )
    out = _wait_ssh(host, timeout=3300, command=command)
    append_event(run_id, "info", stage_name, out[-2500:])
    _set_stage(run_id, stage_name, "complete", f"Proxmox disk boot, SSH, root@pam login, API, storage, and KVM validated at {host}.")


def _video_local_ai_context(run_id: str) -> tuple[dict, dict]:
    return _video_context("openstack-local-ai-openwebui-preflight", run_id)


def _video_local_ai_capacity(run_id: str, stage_name: str) -> None:
    _, values = _video_local_ai_context(run_id)
    host = str(values.get("openstack_host") or "10.20.0.240")
    openrc = shlex.quote(str(values.get("admin_openrc") or "/root/admin-openrc"))
    command = f'''
set -euo pipefail
printf 'host='; hostname -f || hostname
printf 'kernel='; uname -r
printf 'uptime='; uptime -p
printf 'disk_root='; df -h / | tail -1
printf 'mem='; free -h | awk '/Mem:/ {{print $2" total "$7" available"}}'
printf 'cpu_count='; nproc
printf 'virt_flags='; (grep -m1 -oE 'vmx|svm' /proc/cpuinfo || true) | head -1
printf 'docker='; command -v docker || true
printf 'podman='; command -v podman || true
printf 'ollama='; command -v ollama || true
printf 'egress_debian='; curl -fsSI --max-time 8 https://deb.debian.org/debian/ >/dev/null && echo ok || echo fail
printf 'egress_ollama='; curl -fsSI --max-time 8 https://ollama.com/ >/dev/null && echo ok || echo fail
. {openrc}
printf 'openstack_token_bytes='; openstack token issue -f value -c id >/tmp/bkc-local-ai-token && wc -c </tmp/bkc-local-ai-token
printf 'hypervisors\\n'; openstack hypervisor list -f value || true
printf 'servers\\n'; openstack server list -f value -c Name -c Status -c Networks || true
printf 'flavors\\n'; openstack flavor list -f value -c Name -c RAM -c VCPUs -c Disk || true
printf 'images\\n'; openstack image list -f value -c Name -c Status || true
printf 'networks\\n'; openstack network list -f value -c Name -c Subnets || true
'''
    out = run_remote_command(host=host, user="root", command=command, timeout=180)
    append_event(run_id, "info", stage_name, out[-4000:])
    if "egress_debian=ok" not in out or "egress_ollama=ok" not in out:
        raise PipelineExecutionError("Server1 local-AI preflight needs Debian and Ollama egress before install stages are enabled.")
    if "debian-13-genericcloud active" not in out:
        raise PipelineExecutionError("OpenStack Debian 13 generic cloud image is missing or inactive.")
    if "lab-internal" not in out:
        raise PipelineExecutionError("OpenStack lab-internal network is missing.")
    _store_run_extra(run_id, {"local_ai_capacity": out[-4000:]})
    _set_stage(run_id, stage_name, "complete", "Server1/OpenStack capacity, egress, Debian image, and lab-internal network are ready for local-AI preflight.")


def _video_local_ai_vm(run_id: str, stage_name: str) -> None:
    inputs = _require_video_gates(run_id, "enable_openstack_ai_vm")
    _, values = _video_local_ai_context(run_id)
    host = str(values.get("openstack_host") or "10.20.0.240")
    openrc = shlex.quote(str(values.get("admin_openrc") or "/root/admin-openrc"))
    flavor = values.get("ai_flavor") if isinstance(values.get("ai_flavor"), dict) else {}
    secgroup = values.get("ai_security_group") if isinstance(values.get("ai_security_group"), dict) else {}
    server = values.get("ai_server") if isinstance(values.get("ai_server"), dict) else {}
    flavor_name = str(flavor.get("name") or "bkc.ai.small")
    image_name = str(values.get("ai_image") or "debian-13-genericcloud")
    network_name = str(values.get("ai_network") or "lab-internal")
    key_name = str(values.get("ai_keypair") or "bkc-demo-key")
    server_name = str(server.get("name") or "bkc-local-ai-01")
    admin_user = str(server.get("user") or "admin-deploy")
    console_password = str(server.get("console_password") or "changeme123")
    ssh_password_auth = _truthy(server.get("ssh_password_auth", True))
    replace_server = _truthy(inputs.get("enable_replace_ai_vm"))
    secgroup_name = str(secgroup.get("name") or "bkc-local-ai-allow")
    ssh = load_integrations()["ssh"]
    public_key = str(read_key_pair(ssh["private_key_path"], ssh["public_key_path"]).get("public_key") or "").strip()
    if not public_key.startswith("ssh-"):
        raise PipelineExecutionError("BKC SSH public key is unavailable for OpenStack cloud-init/keypair injection.")

    secgroup_commands = [
        f"openstack security group show {shlex.quote(secgroup_name)} >/dev/null 2>&1 || openstack security group create {shlex.quote(secgroup_name)} >/dev/null"
    ]
    for rule in secgroup.get("rules", []):
        if not isinstance(rule, dict):
            continue
        proto = str(rule.get("protocol") or "").strip()
        remote = str(rule.get("remote_ip_prefix") or "10.20.0.0/24").strip()
        if proto == "icmp":
            secgroup_commands.append(f"openstack security group rule create --proto icmp --remote-ip {shlex.quote(remote)} {shlex.quote(secgroup_name)} >/dev/null 2>&1 || true")
        elif proto == "tcp":
            port = int(rule.get("dst_port") or 22)
            secgroup_commands.append(f"openstack security group rule create --proto tcp --dst-port {port} --remote-ip {shlex.quote(remote)} {shlex.quote(secgroup_name)} >/dev/null 2>&1 || true")

    cloud_init = f"""#cloud-config
users:
  - name: {admin_user}
    groups: sudo
    shell: /bin/bash
    lock_passwd: false
    plain_text_passwd: {console_password}
    sudo: ALL=(ALL) NOPASSWD:ALL
    ssh_authorized_keys:
      - {public_key}
chpasswd:
  expire: false
ssh_pwauth: {str(ssh_password_auth).lower()}
disable_root: true
package_update: true
packages:
  - ca-certificates
  - curl
  - qemu-guest-agent
runcmd:
  - systemctl enable --now qemu-guest-agent || true
  - mkdir -p /var/lib/bkc
  - echo local-ai-firstboot > /var/lib/bkc/local-ai-firstboot.txt
"""
    encoded = b64encode(cloud_init.encode()).decode()
    command = f'''
set -euo pipefail
. {openrc}
openstack image show {shlex.quote(image_name)} >/dev/null
openstack network show {shlex.quote(network_name)} >/dev/null
openstack flavor show {shlex.quote(flavor_name)} >/dev/null 2>&1 || openstack flavor create --ram {int(flavor.get("ram_mb") or 8192)} --disk {int(flavor.get("disk_gb") or 40)} --vcpus {int(flavor.get("vcpus") or 4)} {shlex.quote(flavor_name)}
key_tmp=$(mktemp)
printf '%s\\n' {shlex.quote(public_key)} > "$key_tmp"
openstack keypair show {shlex.quote(key_name)} >/dev/null 2>&1 || openstack keypair create --public-key "$key_tmp" {shlex.quote(key_name)} >/dev/null
rm -f "$key_tmp"
{chr(10).join(secgroup_commands)}
user_data=$(mktemp)
printf '%s' {shlex.quote(encoded)} | base64 -d > "$user_data"
if [ {shlex.quote("1" if replace_server else "0")} = "1" ] && openstack server show {shlex.quote(server_name)} >/dev/null 2>&1; then
  openstack server delete {shlex.quote(server_name)}
  deadline=$((SECONDS+300))
  while [ "$SECONDS" -lt "$deadline" ]; do
    openstack server show {shlex.quote(server_name)} >/dev/null 2>&1 || break
    sleep 5
  done
fi
if ! openstack server show {shlex.quote(server_name)} >/dev/null 2>&1; then
  openstack server create --image {shlex.quote(image_name)} --flavor {shlex.quote(flavor_name)} --network {shlex.quote(network_name)} --key-name {shlex.quote(key_name)} --security-group {shlex.quote(secgroup_name)} --user-data "$user_data" {shlex.quote(server_name)} >/dev/null
fi
rm -f "$user_data"
deadline=$((SECONDS+900))
status=""
while [ "$SECONDS" -lt "$deadline" ]; do
  status=$(openstack server show {shlex.quote(server_name)} -f value -c status 2>/dev/null || true)
  [ "$status" = "ACTIVE" ] && break
  sleep 10
done
[ "$status" = "ACTIVE" ]
openstack server show {shlex.quote(server_name)} -f json
'''
    out = run_remote_command(host=host, user="root", command=command, timeout=1200)
    append_event(run_id, "info", stage_name, out[-4000:])
    _store_run_extra(run_id, {"local_ai_vm": out[-4000:]})
    _set_stage(run_id, stage_name, "complete", f"OpenStack AI VM {server_name} is ACTIVE with flavor {flavor_name} on {network_name}.")


def _video_local_ai_ollama(run_id: str, stage_name: str) -> None:
    _require_video_gates(run_id, "enable_baremetal_ollama")
    _, values = _video_local_ai_context(run_id)
    host = str(values.get("openstack_host") or "10.20.0.240")
    ollama = values.get("ollama") if isinstance(values.get("ollama"), dict) else {}
    bind = str(ollama.get("bind") or "127.0.0.1:11434")
    model_values = ollama.get("models") if isinstance(ollama.get("models"), list) else []
    models = [str(item).strip() for item in model_values if str(item).strip()]
    legacy_model = str(ollama.get("model") or "").strip()
    if legacy_model and legacy_model not in models:
        models.insert(0, legacy_model)
    model_storage = str(ollama.get("model_storage") or "/var/lib/ollama")
    model_pull = ""
    if models:
        model_pull = "\n".join(
            f"ollama list | awk '{{print $1}}' | grep -Fx {shlex.quote(model)} >/dev/null || ollama pull {shlex.quote(model)}"
            for model in models
        )
    command = f'''
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y ca-certificates curl
if ! command -v ollama >/dev/null 2>&1; then
  curl -fsSL https://ollama.com/install.sh | sh
fi
install -d -o ollama -g ollama -m 0755 {shlex.quote(model_storage)}
install -d -m 0755 /etc/systemd/system/ollama.service.d
cat >/etc/systemd/system/ollama.service.d/bkc.conf <<'EOF'
[Service]
Environment="OLLAMA_HOST={bind}"
Environment="OLLAMA_MODELS={model_storage}"
EOF
systemctl daemon-reload
systemctl reset-failed ollama >/dev/null 2>&1 || true
systemctl enable --now ollama
systemctl restart ollama
deadline=$((SECONDS+180))
until curl -fsS http://{bind}/api/tags >/dev/null; do
  [ "$SECONDS" -lt "$deadline" ] || exit 1
  sleep 3
done
{model_pull}
ollama --version
curl -fsS http://{bind}/api/tags
'''
    out = run_remote_command(host=host, user="root", command=command, timeout=1800)
    append_event(run_id, "info", stage_name, out[-5000:])
    _store_run_extra(run_id, {"local_ai_ollama": out[-5000:]})
    model_detail = f" with models {', '.join(models)}" if models else ""
    _set_stage(run_id, stage_name, "complete", f"Ollama is running on Server1 at {bind}{model_detail}.")


def _video_local_ai_openwebui(run_id: str, stage_name: str) -> None:
    _require_video_gates(run_id, "enable_openwebui_container")
    _, values = _video_local_ai_context(run_id)
    host = str(values.get("openstack_host") or "10.20.0.240")
    openwebui = values.get("openwebui") if isinstance(values.get("openwebui"), dict) else {}
    image = str(openwebui.get("image") or "ghcr.io/open-webui/open-webui:main")
    listen = str(openwebui.get("listen") or "0.0.0.0:8080")
    public_url = str(openwebui.get("public_url") or "http://swarm1.lab.auzietek.com:8088").rstrip("/")
    ollama_base_url = str(openwebui.get("ollama_base_url") or "http://127.0.0.1:11434")
    listen_port = int(listen.rsplit(":", 1)[-1])
    command = f'''
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y ca-certificates curl podman
install -d -m 0755 /var/lib/open-webui
podman rm -f bkc-openwebui >/dev/null 2>&1 || true
podman pull {shlex.quote(image)}
podman run -d --name bkc-openwebui --replace --restart=always \\
  --network host \\
  -e PORT={listen_port} \\
  -e WEBUI_URL={shlex.quote(public_url)} \\
  -e CORS_ALLOW_ORIGIN={shlex.quote(public_url)} \\
  -e FORWARDED_ALLOW_IPS='*' \\
  -e OLLAMA_BASE_URL={shlex.quote(ollama_base_url)} \\
  -v /var/lib/open-webui:/app/backend/data:Z \\
  {shlex.quote(image)}
deadline=$((SECONDS+300))
until curl -fsS http://127.0.0.1:{listen_port}/ >/dev/null; do
  [ "$SECONDS" -lt "$deadline" ] || {{ podman logs --tail 80 bkc-openwebui || true; exit 1; }}
  sleep 5
done
podman ps --filter name=bkc-openwebui --format '{{{{.Names}}}} {{{{.Status}}}} {{{{.Ports}}}}'
curl -fsSI http://127.0.0.1:{listen_port}/ | sed -n '1,8p'
'''
    out = run_remote_command(host=host, user="root", command=command, timeout=1800)
    append_event(run_id, "info", stage_name, out[-5000:])
    _store_run_extra(run_id, {"local_ai_openwebui": out[-5000:]})
    _set_stage(run_id, stage_name, "complete", f"OpenWebUI container is running on Server1 port {listen_port}.")


def _video_local_ai_validate(run_id: str, stage_name: str) -> None:
    _, values = _video_local_ai_context(run_id)
    host = str(values.get("openstack_host") or "10.20.0.240")
    ollama = values.get("ollama") if isinstance(values.get("ollama"), dict) else {}
    openwebui = values.get("openwebui") if isinstance(values.get("openwebui"), dict) else {}
    bind = str(ollama.get("bind") or "127.0.0.1:11434")
    listen = str(openwebui.get("listen") or "0.0.0.0:8080")
    listen_port = int(listen.rsplit(":", 1)[-1])
    command = f'''
set -euo pipefail
systemctl is-active --quiet ollama
curl -fsS http://{bind}/api/tags
podman inspect bkc-openwebui --format '{{{{.State.Status}}}}'
curl -fsSI http://127.0.0.1:{listen_port}/ | sed -n '1,8p'
ss -ltnp | grep -E '(:11434|:{listen_port})'
'''
    out = run_remote_command(host=host, user="root", command=command, timeout=180)
    append_event(run_id, "info", stage_name, out[-4000:])
    _store_run_extra(run_id, {"local_ai_validate": out[-4000:]})
    _set_stage(run_id, stage_name, "complete", "Ollama API, OpenWebUI HTTP, container state, and listening sockets validated on Server1.")


def _video_local_ai_fragments(run_id: str, stage_name: str) -> None:
    _, values = _video_local_ai_context(run_id)
    fragments = {
        "ollama.native.ensure": {
            "rating": "candidate",
            "target": "server1 bare metal or bkc-local-ai-01 VM",
            "guard": "requires explicit enable_baremetal_ollama or VM SSH validation",
            "validation": "ollama --version; curl /api/tags; model digest recorded",
        },
        "openwebui.container.ensure": {
            "rating": "candidate",
            "image": (values.get("openwebui") or {}).get("image") if isinstance(values.get("openwebui"), dict) else "",
            "guard": "container install disabled until Ollama health is proven",
            "validation": "HTTP 200 on OpenWebUI and Ollama base URL configured",
        },
        "cytoscape.layout.request": {
            "rating": "planned",
            "guard": "model output is proposal-only; validator owns accepted graph positions",
            "validation": "known IDs only, complete node coverage, finite bounded coordinates",
        },
    }
    append_event(run_id, "info", stage_name, json.dumps(fragments, indent=2, sort_keys=True))
    _store_run_extra(run_id, {"local_ai_fragments": fragments})
    _set_stage(run_id, stage_name, "complete", "Local-AI and graph-layout candidate fragments recorded for 40 VIDEO promotion review.")


def _video_openstack_swarm_context(run_id: str) -> tuple[dict, dict]:
    return _video_context("openstack-docker-swarm-seed", run_id)


def _video_openstack_swarm_preflight(run_id: str, stage_name: str) -> None:
    _, values = _video_openstack_swarm_context(run_id)
    host = str(values.get("openstack_host") or "10.20.0.240")
    openrc = shlex.quote(str(values.get("admin_openrc") or "/root/admin-openrc"))
    image_name = shlex.quote(str(values.get("base_image") or "debian-13-genericcloud"))
    network_name = shlex.quote(str(values.get("network") or "lab-internal"))
    ssh = load_integrations()["ssh"]
    public_key = str(read_key_pair(ssh["private_key_path"], ssh["public_key_path"]).get("public_key") or "").strip()
    if not public_key.startswith("ssh-"):
        raise PipelineExecutionError("BKC SSH public key is unavailable for OpenStack swarm cloud-init/keypair injection.")
    fedora = values.get("fedora_image") if isinstance(values.get("fedora_image"), dict) else {}
    fedora_note = "disabled"
    if _truthy(values.get("enable_fedora_image_import", False)):
        fedora_note = str(fedora.get("name") or "fedora-cloud-candidate")
    command = f'''
set -euo pipefail
. {openrc}
openstack token issue -f value -c id >/tmp/bkc-openstack-swarm-token
printf 'token_bytes='; wc -c </tmp/bkc-openstack-swarm-token
    openstack image show {image_name} -f value -c status
    openstack network show {network_name} -f value -c name
    openstack hypervisor list -f value || true
    printf 'swarm_target=%s managers / %s workers\\n' \
      {sum(1 for node in (values.get("nodes") if isinstance(values.get("nodes"), list) else []) if isinstance(node, dict) and str(node.get("role") or "").strip().lower() == "manager")} \
      {sum(1 for node in (values.get("nodes") if isinstance(values.get("nodes"), list) else []) if isinstance(node, dict) and str(node.get("role") or "").strip().lower() != "manager")}
    printf 'servers\\n'; openstack server list -f value -c Name -c Status -c Networks || true
    printf 'fedora_import=%s\\n' {shlex.quote(fedora_note)}
'''
    out = run_remote_command(host=host, user="root", command=command, timeout=180)
    append_event(run_id, "info", stage_name, out[-4000:])
    if "active" not in out.lower():
        raise PipelineExecutionError("OpenStack swarm preflight did not prove an active base image.")
    _store_run_extra(run_id, {"openstack_swarm_preflight": out[-4000:]})
    _set_stage(run_id, stage_name, "complete", "OpenStack API, Debian base image, lab network, and BKC SSH key are ready for the three-node swarm.")


def _video_openstack_swarm_vms(run_id: str, stage_name: str) -> None:
    inputs = _require_video_gates(run_id, "enable_openstack_swarm_vms")
    _, values = _video_openstack_swarm_context(run_id)
    host = str(values.get("openstack_host") or "10.20.0.240")
    openrc = shlex.quote(str(values.get("admin_openrc") or "/root/admin-openrc"))
    flavor = values.get("flavor") if isinstance(values.get("flavor"), dict) else {}
    manager_flavor = values.get("manager_flavor") if isinstance(values.get("manager_flavor"), dict) else {}
    secgroup = values.get("security_group") if isinstance(values.get("security_group"), dict) else {}
    service_interface = values.get("service_interface") if isinstance(values.get("service_interface"), dict) else {}
    nodes = values.get("nodes") if isinstance(values.get("nodes"), list) else []
    manager_count = sum(1 for node in nodes if isinstance(node, dict) and str(node.get("role") or "").strip().lower() == "manager")
    worker_count = sum(1 for node in nodes if isinstance(node, dict) and str(node.get("role") or "").strip().lower() != "manager")
    if manager_count < 1 or worker_count < 1:
        raise PipelineExecutionError("OpenStack swarm seed requires at least one manager and one worker definition.")
    flavor_name = str(flavor.get("name") or "bkc.swarm.small")
    manager_flavor_name = str(manager_flavor.get("name") or flavor_name)
    image_name = str(values.get("base_image") or "debian-13-genericcloud")
    network_name = str(values.get("network") or "lab-internal")
    key_name = str(values.get("keypair") or "bkc-demo-key")
    admin_user = str(values.get("admin_user") or "admin-deploy")
    console_password = str(values.get("console_password") or "changeme123")
    secgroup_name = str(secgroup.get("name") or "bkc-openstack-swarm-allow")
    service_server = str(service_interface.get("server") or "bkc-swarm-mgr-01")
    service_network = str(service_interface.get("network") or "lab-service-provider")
    service_subnet = str(service_interface.get("subnet") or "lab-service-provider-v4")
    service_port = str(service_interface.get("port") or "bkc-swarm-mgr-01-service")
    service_address = str(service_interface.get("address") or "10.20.0.230")
    service_prefix = int(service_interface.get("prefix_length") or 24)
    service_mac = str(service_interface.get("mac_address") or "fa:16:3e:6f:2c:6f")
    service_operator_route = str(service_interface.get("operator_route") or "10.20.0.10/32")
    replace_vms = _truthy(inputs.get("enable_replace_swarm_vms"))
    ssh = load_integrations()["ssh"]
    public_key = str(read_key_pair(ssh["private_key_path"], ssh["public_key_path"]).get("public_key") or "").strip()
    if not public_key.startswith("ssh-"):
        raise PipelineExecutionError("BKC SSH public key is unavailable for OpenStack swarm cloud-init/keypair injection.")
    secgroup_commands = [
        f"openstack security group show {shlex.quote(secgroup_name)} >/dev/null 2>&1 || openstack security group create {shlex.quote(secgroup_name)} >/dev/null"
    ]
    for rule in secgroup.get("rules", []):
        if not isinstance(rule, dict):
            continue
        proto = str(rule.get("protocol") or "").strip()
        remote = str(rule.get("remote_ip_prefix") or "172.24.10.0/24").strip()
        if proto == "icmp":
            secgroup_commands.append(f"openstack security group rule create --proto icmp --remote-ip {shlex.quote(remote)} {shlex.quote(secgroup_name)} >/dev/null 2>&1 || true")
        elif proto in {"tcp", "udp"}:
            port = int(rule.get("dst_port") or 22)
            secgroup_commands.append(f"openstack security group rule create --proto {proto} --dst-port {port} --remote-ip {shlex.quote(remote)} {shlex.quote(secgroup_name)} >/dev/null 2>&1 || true")
    cloud_init = f"""#cloud-config
users:
  - name: {admin_user}
    groups: sudo
    shell: /bin/bash
    lock_passwd: false
    plain_text_passwd: {console_password}
    sudo: ALL=(ALL) NOPASSWD:ALL
    ssh_authorized_keys:
      - {public_key}
chpasswd:
  expire: false
ssh_pwauth: true
disable_root: true
package_update: true
packages:
  - ca-certificates
  - curl
  - openssh-server
  - qemu-guest-agent
runcmd:
  - systemctl enable --now qemu-guest-agent || true
  - mkdir -p /var/lib/bkc
  - echo openstack-docker-swarm-base > /var/lib/bkc/role.txt
  - [sh, -c, 'if [ "$(hostname -s)" = "{service_server}" ]; then printf "%s\\n" "[Match]" "MACAddress={service_mac}" "" "[Network]" "Address={service_address}/{service_prefix}" "LinkLocalAddressing=no" "IPv6AcceptRA=no" "" "[Route]" "Destination={service_operator_route}" "Scope=link" > /etc/systemd/network/20-bkc-service.network; networkctl reload; for n in /sys/class/net/*; do grep -qi "{service_mac}" "$n/address" && networkctl reconfigure "${{n##*/}}"; done; fi']
"""
    encoded = b64encode(cloud_init.encode()).decode()
    node_names = " ".join(shlex.quote(str(node.get("name") or "")) for node in nodes if isinstance(node, dict))
    command = f'''
set -euo pipefail
. {openrc}
openstack image show {shlex.quote(image_name)} >/dev/null
openstack network show {shlex.quote(network_name)} >/dev/null
openstack flavor show {shlex.quote(flavor_name)} >/dev/null 2>&1 || openstack flavor create --ram {int(flavor.get("ram_mb") or 2048)} --disk {int(flavor.get("disk_gb") or 20)} --vcpus {int(flavor.get("vcpus") or 2)} {shlex.quote(flavor_name)}
openstack flavor show {shlex.quote(manager_flavor_name)} >/dev/null 2>&1 || openstack flavor create --ram {int(manager_flavor.get("ram_mb") or flavor.get("ram_mb") or 4096)} --disk {int(manager_flavor.get("disk_gb") or flavor.get("disk_gb") or 30)} --vcpus {int(manager_flavor.get("vcpus") or flavor.get("vcpus") or 2)} {shlex.quote(manager_flavor_name)}
key_tmp=$(mktemp)
printf '%s\\n' {shlex.quote(public_key)} > "$key_tmp"
openstack keypair show {shlex.quote(key_name)} >/dev/null 2>&1 || openstack keypair create --public-key "$key_tmp" {shlex.quote(key_name)} >/dev/null
rm -f "$key_tmp"
{chr(10).join(secgroup_commands)}
openstack network show {shlex.quote(service_network)} >/dev/null
openstack subnet show {shlex.quote(service_subnet)} >/dev/null
openstack port show {shlex.quote(service_port)} >/dev/null 2>&1 || openstack port create --network {shlex.quote(service_network)} --fixed-ip subnet={shlex.quote(service_subnet)},ip-address={shlex.quote(service_address)} --mac-address {shlex.quote(service_mac)} --security-group {shlex.quote(secgroup_name)} {shlex.quote(service_port)} >/dev/null
user_data=$(mktemp)
printf '%s' {shlex.quote(encoded)} | base64 -d > "$user_data"
for name in {node_names}; do
  [ -n "$name" ] || continue
  node_flavor={shlex.quote(flavor_name)}
  case "$name" in
    *mgr*|*manager*) node_flavor={shlex.quote(manager_flavor_name)} ;;
  esac
  if [ {shlex.quote("1" if replace_vms else "0")} = "1" ] && openstack server show "$name" >/dev/null 2>&1; then
    openstack server delete "$name"
    deadline=$((SECONDS+300))
    while [ "$SECONDS" -lt "$deadline" ]; do openstack server show "$name" >/dev/null 2>&1 || break; sleep 5; done
  fi
  network_args=(--network {shlex.quote(network_name)})
  if [ "$name" = {shlex.quote(service_server)} ]; then network_args+=(--port {shlex.quote(service_port)}); fi
  openstack server show "$name" >/dev/null 2>&1 || openstack server create --image {shlex.quote(image_name)} --flavor "$node_flavor" "${{network_args[@]}}" --key-name {shlex.quote(key_name)} --security-group {shlex.quote(secgroup_name)} --user-data "$user_data" --config-drive true "$name" >/dev/null
done
rm -f "$user_data"
deadline=$((SECONDS+1200))
while [ "$SECONDS" -lt "$deadline" ]; do
  pending=0
  for name in {node_names}; do
    status=$(openstack server show "$name" -f value -c status 2>/dev/null || echo MISSING)
    [ "$status" = ACTIVE ] || pending=1
  done
  [ "$pending" = 0 ] && break
  sleep 10
done
openstack server list --name '^bkc-swarm-' -f table
'''
    out = run_remote_command(host=host, user="root", command=command, timeout=1500)
    append_event(run_id, "info", stage_name, out[-6000:])
    _store_run_extra(run_id, {"openstack_swarm_vms": out[-6000:]})
    _set_stage(run_id, stage_name, "complete", f"OpenStack swarm VMs are ACTIVE on {network_name}: {manager_count} manager(s), {worker_count} worker(s).")


def _openstack_swarm_remote_script(values: dict, *, remote_key_path: str, validate_only: bool = False) -> str:
    nodes = values.get("nodes") if isinstance(values.get("nodes"), list) else []
    admin_user = str(values.get("admin_user") or "admin-deploy")
    openrc = shlex.quote(str(values.get("admin_openrc") or "/root/admin-openrc"))
    docker_static_url = str(values.get("docker_static_url") or "https://download.docker.com/linux/static/stable/x86_64/docker-28.3.3.tgz")
    docker_archive_name = Path(urllib.parse.urlparse(docker_static_url).path).name or "docker.tgz"
    node_names = " ".join(shlex.quote(str(node.get("name") or "")) for node in nodes if isinstance(node, dict))
    bootstrap = "true" if validate_only else r'''
docker_archive=/var/lib/bkc/openstack-swarm/__DOCKER_ARCHIVE_NAME__
install -d -m 0755 /var/lib/bkc/openstack-swarm
if ! getent hosts download.docker.com >/dev/null 2>&1; then
  printf '%s\n' 'nameserver 10.20.0.10' 'nameserver 1.1.1.1' >/etc/resolv.conf
fi
test -s "$docker_archive" || curl -fL --retry 3 -o "$docker_archive" __DOCKER_STATIC_URL__
test -s "$docker_archive"
install_docker() {
  ip="$1"
  "${ssh_cmd[@]}" "${ssh_opts[@]}" "$admin_user@$ip" 'mkdir -p /tmp/bkc-docker'
  ip netns exec "$tenant_netns" scp -i "$remote_key" -o IdentitiesOnly=yes -o BatchMode=yes -o StrictHostKeyChecking=no "$docker_archive" "$admin_user@$ip:/tmp/bkc-docker/docker.tgz"
  "${ssh_cmd[@]}" "${ssh_opts[@]}" "$admin_user@$ip" 'set -euo pipefail
    sudo tar -C /usr/local/bin -xzf /tmp/bkc-docker/docker.tgz --strip-components=1
    sudo groupadd -f docker
    sudo usermod -aG docker "$USER" || true
    sudo install -d -m 0755 /etc/docker /var/lib/docker
    sudo tee /etc/systemd/system/docker.service >/dev/null <<'"'"'EOF'"'"'
[Unit]
Description=Docker Application Container Engine
Documentation=https://docs.docker.com
After=network-online.target firewalld.service containerd.service
Wants=network-online.target

[Service]
Type=notify
ExecStart=/usr/local/bin/dockerd --host=unix:///var/run/docker.sock
ExecReload=/bin/kill -s HUP $MAINPID
TimeoutStartSec=0
RestartSec=2
Restart=always
LimitNOFILE=infinity
LimitNPROC=infinity
LimitCORE=infinity
TasksMax=infinity
Delegate=yes
KillMode=process
OOMScoreAdjust=-500

[Install]
WantedBy=multi-user.target
EOF
    sudo systemctl daemon-reload
    sudo systemctl enable --now docker
    sudo docker info >/dev/null
    sudo docker version
  '
}
for ip in "${ips[@]}"; do install_docker "$ip"; done
manager="${ips[0]}"
"${ssh_cmd[@]}" "${ssh_opts[@]}" "$admin_user@$manager" "sudo docker swarm leave --force >/dev/null 2>&1 || true; sudo docker swarm init --advertise-addr $manager >/dev/null"
token=$("${ssh_cmd[@]}" "${ssh_opts[@]}" "$admin_user@$manager" 'sudo docker swarm join-token -q worker')
for ip in "${ips[@]:1}"; do
  "${ssh_cmd[@]}" "${ssh_opts[@]}" "$admin_user@$ip" "sudo docker swarm leave --force >/dev/null 2>&1 || true; sudo docker swarm join --token $token $manager:2377 >/dev/null"
done
'''.replace("__DOCKER_ARCHIVE_NAME__", shlex.quote(docker_archive_name)).replace("__DOCKER_STATIC_URL__", shlex.quote(docker_static_url))
    return f'''
set -euo pipefail
. {openrc}
admin_user={shlex.quote(admin_user)}
remote_key={shlex.quote(remote_key_path)}
test -s "$remote_key"
chmod 0600 "$remote_key"
tenant_netns=""
for candidate_ns in $(ip netns list | awk '{{print $1}}'); do
  if ip netns exec "$candidate_ns" ip -4 addr show | grep -q '172\\.24\\.10\\.'; then
    tenant_netns="$candidate_ns"
    break
  fi
done
[ -n "$tenant_netns" ] || {{ echo "missing_tenant_netns=172.24.10.0/24"; exit 1; }}
ssh_cmd=(ip netns exec "$tenant_netns" ssh)
ssh_opts=(-i "$remote_key" -o IdentitiesOnly=yes -o BatchMode=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/root/.ssh/known_hosts)
printf 'tenant_netns=%s\\n' "$tenant_netns"
names=({node_names})
ips=()
for name in "${{names[@]}}"; do
  ip=$(openstack server show "$name" -f json | python3 -c 'import json,re,sys; data=json.load(sys.stdin); nets=str(data.get("addresses","")); m=re.search(r"\\b(?:172\\.24\\.10|10\\.20\\.0)\\.\\d+\\b", nets); print(m.group(0) if m else "")')
  [ -n "$ip" ] || {{ echo "missing_ip=$name"; exit 1; }}
  ips+=("$ip")
done
for ip in "${{ips[@]}}"; do
  deadline=$((SECONDS+900))
  while true; do
    ssh_probe=$("${{ssh_cmd[@]}}" "${{ssh_opts[@]}}" -o ConnectTimeout=8 "$admin_user@$ip" 'cloud-init status --wait >/dev/null 2>&1 || true; hostname; true' 2>&1) && break
    printf 'ssh_probe_failed=%s %s\\n' "$ip" "$ssh_probe"
    if printf '%s\\n' "$ssh_probe" | grep -qi 'Permission denied'; then
      exit 1
    fi
    [ "$SECONDS" -lt "$deadline" ] || {{ echo "ssh_timeout=$ip"; exit 1; }}
    sleep 10
  done
done
{bootstrap}
manager="${{ips[0]}}"
"${{ssh_cmd[@]}}" "${{ssh_opts[@]}}" "$admin_user@$manager" 'sudo docker node ls'
printf 'openstack_swarm_ips=%s\\n' "${{ips[*]}}"
'''


def _video_openstack_swarm_bootstrap(run_id: str, stage_name: str) -> None:
    _require_video_gates(run_id, "enable_bootstrap_swarm")
    _, values = _video_openstack_swarm_context(run_id)
    host = str(values.get("openstack_host") or "10.20.0.240")
    ssh = load_integrations()["ssh"]
    key_info = read_key_pair(ssh["private_key_path"], ssh["public_key_path"])
    private_key_path = str(key_info["private_key_path"])
    if not private_key_path or not Path(private_key_path).exists():
        raise PipelineExecutionError("BKC SSH private key is missing; cannot bootstrap OpenStack swarm from Server1.")
    remote_key_path = f"/var/tmp/bkc-openstack-swarm-{run_id[:8]}"
    upload_remote_file(host=host, user="root", remote_path=remote_key_path, local_path=private_key_path, mode=0o600, timeout=60)
    out = run_remote_command(host=host, user="root", command=_openstack_swarm_remote_script(values, remote_key_path=remote_key_path), timeout=2400)
    append_event(run_id, "info", stage_name, out[-6000:])
    _store_run_extra(run_id, {"openstack_swarm_bootstrap": out[-6000:]})
    _set_stage(run_id, stage_name, "complete", "Docker is installed and the three OpenStack VMs have formed a swarm.")


def _video_openstack_swarm_validate(run_id: str, stage_name: str) -> None:
    _, values = _video_openstack_swarm_context(run_id)
    host = str(values.get("openstack_host") or "10.20.0.240")
    ssh = load_integrations()["ssh"]
    key_info = read_key_pair(ssh["private_key_path"], ssh["public_key_path"])
    private_key_path = str(key_info["private_key_path"])
    if not private_key_path or not Path(private_key_path).exists():
        raise PipelineExecutionError("BKC SSH private key is missing; cannot validate OpenStack swarm from Server1.")
    remote_key_path = f"/var/tmp/bkc-openstack-swarm-{run_id[:8]}"
    upload_remote_file(host=host, user="root", remote_path=remote_key_path, local_path=private_key_path, mode=0o600, timeout=60)
    out = run_remote_command(host=host, user="root", command=_openstack_swarm_remote_script(values, remote_key_path=remote_key_path, validate_only=True), timeout=900)
    append_event(run_id, "info", stage_name, out[-6000:])
    if out.count(" Ready ") < 3 and out.count(" Ready") < 3:
        raise PipelineExecutionError("Docker swarm validation did not report three Ready nodes.")
    _store_run_extra(run_id, {"openstack_swarm_validate": out[-6000:]})
    _set_stage(run_id, stage_name, "complete", "OpenStack-hosted Docker Swarm reports three Ready nodes.")


def _video_openstack_swarm_fragments(run_id: str, stage_name: str) -> None:
    _, values = _video_openstack_swarm_context(run_id)
    fragments = {
        "openstack.swarm.debian-base": {
            "rating": "candidate-known-good",
            "image": values.get("base_image") or "debian-13-genericcloud",
            "contract": "Debian cloud-init VM must be created with config-drive enabled and must reach SSH before Docker bootstrap starts.",
        },
        "openstack.swarm.security-group": {
            "rating": "candidate",
            "contract": "Allow SSH from management and Docker Swarm ports 2377/tcp, 7946/tcp+udp, 4789/udp inside tenant CIDR.",
        },
        "openstack.swarm.bootstrap": {
            "rating": "candidate",
            "contract": "Stage the official Docker static archive from Server1, install dockerd with systemd on each VM, swarm init on node1, join node2/node3, validate docker node ls.",
        },
        "fedora.cloud-image": {
            "rating": "planned",
            "contract": "Keep optional until an official Fedora cloud image URL is selected and cached.",
        },
    }
    append_event(run_id, "info", stage_name, json.dumps(fragments, indent=2, sort_keys=True))
    _store_run_extra(run_id, {"openstack_swarm_fragments": fragments})
    _set_stage(run_id, stage_name, "complete", "OpenStack Docker Swarm candidate fragments recorded for future reuse and anti-regression context.")


def _video_seed_openstack(run_id: str, stage_name: str) -> None:
    _require_video_gates(run_id, "enable_openstack_seed", "enable_smoke_instance")
    _, values = _video_context("openstack-lab-seed-and-validate", run_id)
    host = str(values.get("openstack_host") or "10.20.0.240")
    project = str(values.get("seed_project") or "bkc-demo")
    user = str(values.get("seed_user") or "bkc-demo-admin")
    password = _secret_ref_literal(values.get("seed_user_password_ref"), default="changeme123")
    flavor = values.get("demo_flavor") if isinstance(values.get("demo_flavor"), dict) else {}
    image = values.get("demo_image") if isinstance(values.get("demo_image"), dict) else {}
    keypair = values.get("demo_keypair") if isinstance(values.get("demo_keypair"), dict) else {}
    security_group = values.get("demo_security_group") if isinstance(values.get("demo_security_group"), dict) else {}
    provider = values.get("provider_network") if isinstance(values.get("provider_network"), dict) else {}
    network = values.get("self_service_network") if isinstance(values.get("self_service_network"), dict) else {}
    smoke = values.get("smoke_instance") if isinstance(values.get("smoke_instance"), dict) else {}
    image_name = str(image.get("name") or smoke.get("image") or "cirros-bkc-smoke")
    image_url = str(image.get("url") or "https://download.cirros-cloud.net/0.6.3/cirros-0.6.3-x86_64-disk.img")
    image_filename = Path(urllib.parse.urlparse(image_url).path).name or f"{image_name}.qcow2"
    ssh = load_integrations()["ssh"]
    public_key = str(read_key_pair(ssh["private_key_path"], ssh["public_key_path"]).get("public_key") or "").strip()
    key_name = str(keypair.get("name") or smoke.get("key_name") or "bkc-demo-key")
    secgroup_name = str(security_group.get("name") or "bkc-demo-allow-ssh-icmp")
    secgroup_rules = security_group.get("rules") if isinstance(security_group.get("rules"), list) else []
    secgroup_commands: list[str] = [
        f"openstack security group show {shlex.quote(secgroup_name)} >/dev/null 2>&1 || openstack security group create {shlex.quote(secgroup_name)} >/dev/null"
    ]
    for rule in secgroup_rules:
        if not isinstance(rule, dict):
            continue
        proto = str(rule.get("protocol") or "").strip()
        remote = str(rule.get("remote_ip_prefix") or "0.0.0.0/0").strip()
        if proto == "icmp":
            secgroup_commands.append(
                f"openstack security group rule create --proto icmp --remote-ip {shlex.quote(remote)} {shlex.quote(secgroup_name)} >/dev/null 2>&1 || true"
            )
        elif proto == "tcp":
            port = int(rule.get("dst_port") or 22)
            secgroup_commands.append(
                f"openstack security group rule create --proto tcp --dst-port {port} --remote-ip {shlex.quote(remote)} {shlex.quote(secgroup_name)} >/dev/null 2>&1 || true"
            )
    keypair_command = "true"
    if public_key.startswith("ssh-"):
        keypair_command = (
            f"openstack keypair show {shlex.quote(key_name)} >/dev/null 2>&1 || "
            "{ "
            f"key_tmp=$(mktemp); printf '%s\\n' {shlex.quote(public_key)} > \"$key_tmp\"; "
            f"openstack keypair create --public-key \"$key_tmp\" {shlex.quote(key_name)} >/dev/null; "
            "rm -f \"$key_tmp\"; "
            "}"
        )
    server_key_arg = f"--key-name {shlex.quote(key_name)}" if public_key.startswith("ssh-") else ""
    provider_pools = provider.get("allocation_pools") if isinstance(provider.get("allocation_pools"), list) else []
    provider_pool_args = " ".join(
        f"--allocation-pool start={shlex.quote(str(pool.get('start') or ''))},end={shlex.quote(str(pool.get('end') or ''))}"
        for pool in provider_pools
        if isinstance(pool, dict) and pool.get("start") and pool.get("end")
    )
    if not provider_pool_args:
        provider_pool_args = (
            f"--allocation-pool start={shlex.quote(str(provider.get('allocation_pool_start') or '10.20.0.232'))},"
            f"end={shlex.quote(str(provider.get('allocation_pool_end') or '10.20.0.239'))}"
        )
    script = f'''set -euo pipefail
. /root/admin-openrc
install -d -m 0755 /var/lib/bkc/openstack-images
image_file=/var/lib/bkc/openstack-images/{shlex.quote(image_filename)}
test -s "$image_file" || curl -fL --retry 3 -o "$image_file" {shlex.quote(image_url)}
openstack project show {shlex.quote(project)} >/dev/null 2>&1 || openstack project create --domain {shlex.quote(str(values.get("seed_domain") or "Default"))} {shlex.quote(project)}
openstack user show {shlex.quote(user)} >/dev/null 2>&1 || openstack user create --domain {shlex.quote(str(values.get("seed_domain") or "Default"))} --password {shlex.quote(password)} {shlex.quote(user)}
openstack role add --project {shlex.quote(project)} --user {shlex.quote(user)} member || true
openstack flavor show {shlex.quote(str(flavor.get("name") or "bkc.nano"))} >/dev/null 2>&1 || openstack flavor create --ram {int(flavor.get("ram_mb") or 512)} --disk {int(flavor.get("disk_gb") or 1)} --vcpus {int(flavor.get("vcpus") or 1)} {shlex.quote(str(flavor.get("name") or "bkc.nano"))}
openstack network show {shlex.quote(str(provider.get("name") or "lab-service-provider"))} >/dev/null 2>&1 || openstack network create --external --share --provider-network-type {shlex.quote(str(provider.get("type") or "flat"))} --provider-physical-network {shlex.quote(str(provider.get("physical_network") or "provider"))} {shlex.quote(str(provider.get("name") or "lab-service-provider"))}
openstack subnet show {shlex.quote(str(provider.get("subnet_name") or "lab-service-provider-v4"))} >/dev/null 2>&1 || openstack subnet create --network {shlex.quote(str(provider.get("name") or "lab-service-provider"))} --subnet-range {shlex.quote(str(provider.get("cidr") or "10.20.0.0/24"))} --gateway {shlex.quote(str(provider.get("gateway") or "10.20.0.10"))} --no-dhcp {provider_pool_args} {shlex.quote(str(provider.get("subnet_name") or "lab-service-provider-v4"))}
openstack subnet set --no-allocation-pool {provider_pool_args} {shlex.quote(str(provider.get("subnet_name") or "lab-service-provider-v4"))}
openstack network show {shlex.quote(str(network.get("name") or "tenant-demo-net"))} >/dev/null 2>&1 || openstack network create {shlex.quote(str(network.get("name") or "tenant-demo-net"))}
openstack subnet show {shlex.quote(str(network.get("subnet_name") or "tenant-demo-subnet"))} >/dev/null 2>&1 || openstack subnet create --network {shlex.quote(str(network.get("name") or "tenant-demo-net"))} --subnet-range {shlex.quote(str(network.get("cidr") or "172.16.10.0/24"))} {shlex.quote(str(network.get("subnet_name") or "tenant-demo-subnet"))}
{keypair_command}
{chr(10).join(secgroup_commands)}
openstack image show {shlex.quote(image_name)} >/dev/null 2>&1 || openstack image create --disk-format {shlex.quote(str(image.get("disk_format") or "qcow2"))} --container-format {shlex.quote(str(image.get("container_format") or "bare"))} --public --file "$image_file" {shlex.quote(image_name)}
openstack server show {shlex.quote(str(smoke.get("name") or "bkc-openstack-smoke-01"))} >/dev/null 2>&1 || openstack server create --image {shlex.quote(image_name)} --flavor {shlex.quote(str(flavor.get("name") or "bkc.nano"))} --network {shlex.quote(str(smoke.get("network") or network.get("name") or "lab-internal"))} {server_key_arg} --security-group {shlex.quote(secgroup_name)} {shlex.quote(str(smoke.get("name") or "bkc-openstack-smoke-01"))}
openstack server list
'''
    out = run_remote_command(host=host, user="root", command=script, timeout=1200)
    append_event(run_id, "info", stage_name, out[-3000:])
    _set_stage(run_id, stage_name, "complete", "Real OpenStack identity, network, image, flavor, and smoke server resources are present.")


def _video_seed_proxmox(run_id: str, stage_name: str) -> None:
    _require_video_gates(run_id, "enable_proxmox_seed")
    _, values = _video_context("openstack-lab-seed-and-validate", run_id)
    source, target = str(values["proxmox_source_host"]), str(values["proxmox_target_host"])
    if "proxmox_source_password" in values:
        source_password = str(values.get("proxmox_source_password") or "").strip()
    else:
        source_password = str(load_proxmox_config().get("password") or "").strip()
    target_password = str(values.get("proxmox_target_password") or "changeme123").strip()
    pub = run_remote_command(host=source, user="root", password=source_password, command="set -e; test -s /root/.ssh/bkc-migrate || ssh-keygen -q -t ed25519 -N '' -f /root/.ssh/bkc-migrate; cat /root/.ssh/bkc-migrate.pub", timeout=30).strip()
    run_remote_command(host=target, user="root", password=target_password, command=f"mkdir -p /root/.ssh; touch /root/.ssh/authorized_keys; grep -Fqx {shlex.quote(pub)} /root/.ssh/authorized_keys || printf '%s\\n' {shlex.quote(pub)} >> /root/.ssh/authorized_keys", timeout=30)
    source_vmid = int(values["proxmox_source_vmid"])
    target_q = shlex.quote(target)
    migrate = (
        "set -euo pipefail; "
        f"target={target_q}; "
        "ssh_opts='-i /root/.ssh/bkc-migrate -o StrictHostKeyChecking=no'; "
        "if ssh $ssh_opts root@$target \"qm config 201 2>/dev/null | grep -Eq '^(scsi|virtio|sata|ide)[0-9]:'\"; then "
        "  echo vm201=already-restored; "
        "else "
        "  ssh $ssh_opts root@$target 'qm unlock 201 >/dev/null 2>&1 || true; qm destroy 201 --purge >/dev/null 2>&1 || true'; "
        f"  vzdump {source_vmid} --mode stop --compress 0 --stdout | ssh $ssh_opts root@$target 'qmrestore - 201 --storage local-lvm'; "
        "fi"
    )
    out = run_remote_command(host=source, user="root", password=source_password, command=migrate, timeout=3300)
    configure = "set -e; qm set 201 --name ns1-trixie-base --memory 2048 --cores 2 --delete net1 >/dev/null 2>&1 || true; qm set 201 --net0 virtio,bridge=vmbr0; qm status 202 >/dev/null 2>&1 || qm clone 201 202 --name swarm1-trixie-base --full --storage local-lvm; qm set 202 --memory 4096 --cores 2 --net0 virtio,bridge=vmbr0; qm start 201 || true; qm start 202 || true; qm list"
    out += "\n" + run_remote_command(host=target, user="root", password=target_password, command=configure, timeout=1200)
    append_event(run_id, "info", stage_name, out[-3500:])
    _set_stage(run_id, stage_name, "complete", "Proxmox VM132 migrated to VM201 and cloned to VM202 with vmbr0 networking.")


def _video_seed_validate(run_id: str, stage_name: str) -> None:
    _, values = _video_context("openstack-lab-seed-and-validate", run_id)
    prox_host = str(values["proxmox_target_host"])
    prox_password = str(values.get("proxmox_target_password") or "changeme123")
    prox_user = str(values.get("proxmox_root_user") or "root@pam")
    prox_api = str(values.get("proxmox_management_url") or f"https://{prox_host}:8006/").rstrip("/")
    if not prox_api.endswith("/api2/json"):
        prox_api = prox_api.rstrip("/") + "/api2/json"
    prox = run_remote_command(host=prox_host, user="root", password=prox_password, command="test -c /dev/kvm; qm status 201 | grep -F running; qm status 202 | grep -F running; curl -kfsS -o /dev/null https://127.0.0.1:8006/; qm list", timeout=120)
    integrations = load_integrations()
    integrations["proxmox"].update({
        "api_url": prox_api,
        "username": prox_user,
        "password": prox_password,
        "token_name": "",
        "token_value": "",
        "verify_ssl": False,
    })
    save_integrations(integrations)
    inventory = summarize_inventory(ProxmoxClient(load_proxmox_config()))
    save_proxmox_snapshot(inventory)
    rules = load_rules()
    sync_result = sync_inventory_to_rules(rules, inventory)
    save_rules(rules)
    cloud_host = str(values.get("openstack_host") or "10.20.0.240")
    smoke = values.get("smoke_instance") if isinstance(values.get("smoke_instance"), dict) else {}
    smoke_name = str(smoke.get("name") or "bkc-openstack-smoke-01")
    cloud = run_remote_command(host=cloud_host, user="root", command=f". /root/admin-openrc; openstack token issue -f value -c id; openstack server show {shlex.quote(smoke_name)} -f value -c status", timeout=120)
    urls = values.get("edge_validation_urls") if isinstance(values.get("edge_validation_urls"), list) else []
    edge = "\n".join(f"{url}={urllib.request.urlopen(url, timeout=15).status}" for url in urls)
    append_event(run_id, "info", stage_name, (cloud + "\n" + prox + "\n" + edge + f"\nproxmox_inventory_sync={sync_result}")[-4000:])
    _set_stage(run_id, stage_name, "complete", "Both hypervisors, seeded guests, OpenStack API, BKC Proxmox inventory, and edge dashboards validated.")


def _run_stage_plan(run_id: str, workflow: str, settings: dict[str, str], *, action_mode: str = "deploy") -> None:
    config = WORKFLOW_DEFINITIONS[workflow]
    stage_plan = workflow_stage_definitions(workflow, action_mode=action_mode)
    run = get_run(run_id) or {}
    extra = run.get("extra") if isinstance(run.get("extra"), dict) else {}
    skip_completed_stages = bool(extra.get("skip_completed_stages"))
    completed_stage_names = {
        str(stage.get("name", ""))
        for stage in run.get("stages", [])
        if str(stage.get("status", "")).strip().lower() == "complete"
    }
    mark_run_active(run_id, f"Running {workflow} pipeline stages.")

    for stage in stage_plan:
        stage_name = str(stage["name"])
        if skip_completed_stages and stage_name in completed_stage_names:
            append_event(run_id, "info", stage_name, "Skipping previously completed stage for review-phase resume.")
            continue
        if workflow == "openstack-kolla-single-node-install":
            operation_modes = {
                str(item).strip().lower()
                for item in stage.get("operation_modes", [])
                if str(item).strip()
            }
            operation_mode = _openstack_kolla_operation_mode(run_id)
            if operation_modes and operation_mode not in operation_modes:
                detail = f"Skipped stage; operation_mode is {operation_mode}."
                _set_stage(run_id, stage_name, "complete", detail)
                append_event(run_id, "info", stage_name, detail)
                continue
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

        if kind == "local-command":
            command = str(stage.get("command", "")).strip()
            if not command:
                raise PipelineExecutionError(f"Stage {stage_name} is missing a local command.")
            cwd = str(stage.get("cwd", "")).strip() or None
            timeout = int(stage.get("timeout", stage.get("timeout_seconds", 120)))
            result = subprocess.run(
                command,
                shell=True,
                cwd=cwd,
                text=True,
                capture_output=True,
                timeout=timeout,
                check=False,
            )
            output = "\n".join(part for part in [result.stdout, result.stderr] if part)
            if output:
                append_event(run_id, "info", stage_name, output[-6000:])
            if result.returncode != 0:
                raise PipelineExecutionError(
                    f"Stage {stage_name} failed with exit code {result.returncode}."
                )
            _set_stage(run_id, stage_name, "complete", str(stage.get("complete", "Stage completed.")))
            continue

        if kind == "folder-pipeline-review":
            _run_folder_pipeline_review_stage(run_id, stage)
            continue

        video_runners = {
            "video-openstack-one-shot": _video_openstack_one_shot,
            "video-openstack-pxe": _video_openstack_pxe,
            "video-openstack-boot": _video_openstack_boot,
            "video-openstack-firstboot": _video_openstack_firstboot,
            "video-openstack-install": _video_openstack_install,
            "video-openstack-validate": _video_openstack_validate,
            "video-proxmox-media": _video_proxmox_media,
            "video-proxmox-boot": _video_proxmox_boot,
            "video-proxmox-handoff": _video_proxmox_handoff,
            "video-proxmox-validate": _video_proxmox_validate,
            "video-seed-openstack": _video_seed_openstack,
            "video-seed-proxmox": _video_seed_proxmox,
            "video-seed-validate": _video_seed_validate,
            "video-local-ai-capacity": _video_local_ai_capacity,
            "video-local-ai-vm": _video_local_ai_vm,
            "video-local-ai-ollama": _video_local_ai_ollama,
            "video-local-ai-openwebui": _video_local_ai_openwebui,
            "video-local-ai-validate": _video_local_ai_validate,
            "video-local-ai-fragments": _video_local_ai_fragments,
            "video-openstack-swarm-preflight": _video_openstack_swarm_preflight,
            "video-openstack-swarm-vms": _video_openstack_swarm_vms,
            "video-openstack-swarm-bootstrap": _video_openstack_swarm_bootstrap,
            "video-openstack-swarm-validate": _video_openstack_swarm_validate,
            "video-openstack-swarm-fragments": _video_openstack_swarm_fragments,
        }
        if kind in video_runners:
            video_runners[kind](run_id, stage_name)
            continue

        if kind == "bmc-discovery-neighbors":
            _run_bmc_discovery_neighbors(run_id, stage_name)
            continue

        if kind == "bmc-discovery-redfish":
            _run_bmc_discovery_redfish(run_id, stage_name)
            continue

        if kind == "ns1-lan-mac-pxe-validate":
            _run_ns1_lan_mac_validate(run_id, stage_name)
            continue

        if kind == "ns1-lan-mac-pxe-ensure-include":
            _run_ns1_lan_mac_ensure_include(run_id, stage_name)
            continue

        if kind == "ns1-lan-mac-pxe-render-fragment":
            _run_ns1_lan_mac_render_fragment(run_id, stage_name)
            continue

        if kind == "ns1-lan-mac-pxe-render-defaults":
            _run_ns1_lan_mac_render_defaults(run_id, stage_name)
            continue

        if kind == "ns1-lan-mac-pxe-validate-config":
            _run_ns1_lan_mac_validate_config(run_id, stage_name)
            continue

        if kind == "ns1-lan-mac-pxe-restart":
            _run_ns1_lan_mac_restart(run_id, stage_name)
            continue

        if kind == "openstack-host-firstboot-login":
            _run_openstack_host_firstboot_login(run_id, stage_name)
            continue

        if kind == "openstack-host-base-normalize":
            _run_openstack_host_base_normalize(run_id, stage_name)
            continue

        if kind == "openstack-host-network-sides":
            _run_openstack_host_network_sides(run_id, stage_name)
            continue

        if kind == "openstack-host-package-prepare":
            _run_openstack_host_package_prepare(run_id, stage_name)
            continue

        if kind == "openstack-host-neutron-validate":
            _run_openstack_host_neutron_validate(run_id, stage_name)
            continue

        if kind == "openstack-host-image-cache":
            _run_openstack_host_image_cache(run_id, stage_name)
            continue

        if kind == "openstack-kolla-phase":
            _run_openstack_kolla_phase(run_id, stage)
            continue

        if kind == "openstack-base-boot-render":
            _run_openstack_base_boot_render(run_id, stage_name)
            continue

        if kind == "openstack-base-boot-validate":
            _run_openstack_base_boot_validate(run_id, stage_name)
            continue

        if kind == "openstack-bkc-compose-preflight":
            _run_openstack_bkc_compose_preflight(run_id, stage_name)
            continue

        if kind == "openstack-bkc-compose-runtime":
            _run_openstack_bkc_compose_runtime(run_id, stage_name)
            continue

        if kind == "openstack-bkc-compose-render":
            _run_openstack_bkc_compose_render(run_id, stage_name)
            continue

        if kind == "openstack-bkc-compose-up":
            _run_openstack_bkc_compose_up(run_id, stage_name)
            continue

        if kind == "openstack-bkc-compose-validate":
            _run_openstack_bkc_compose_validate(run_id, stage_name)
            continue

        if kind == "openstack-bkc-compose-edge-pointer":
            _run_openstack_bkc_edge_pointer(run_id, stage_name)
            continue

        if kind == "openstack-bkc-swarm-promote-script":
            _run_openstack_bkc_swarm_promote(run_id, stage_name)
            continue

        if kind == "baremetal-bmc-power-reset":
            _run_baremetal_bmc_power_reset(run_id, stage_name)
            continue

        if kind == "vmware-esxi-boot-assets-render":
            _run_vmware_esxi_boot_assets_render(run_id, stage_name)
            continue

        if kind == "vmware-esxi-media-stage":
            _run_vmware_esxi_media_stage(run_id, stage_name)
            continue

        if kind == "vmware-esxi-iso-handoff":
            _run_vmware_esxi_iso_handoff(run_id, stage_name)
            continue

        if kind == "video-esxi-api-preflight":
            _video_esxi_api_preflight(run_id, stage_name)
            continue

        if kind == "video-esxi-enable-ssh":
            _video_esxi_enable_ssh(run_id, stage_name)
            continue

        if kind == "video-esxi-swarm-shells":
            _video_esxi_swarm_shells(run_id, stage_name)
            continue

        if kind == "video-esxi-swarm-dhcp":
            _video_esxi_swarm_dhcp(run_id, stage_name)
            continue

        if kind == "video-esxi-swarm-bootstrap":
            _video_esxi_swarm_bootstrap(run_id, stage_name)
            continue

        if kind == "video-esxi-swarm-inventory":
            _video_esxi_swarm_inventory(run_id, stage_name)
            continue

        if kind == "video-esxi-swarm-fragments":
            _video_esxi_swarm_fragments(run_id, stage_name)
            continue

        if kind == "micro-blog-source-preflight":
            _run_micro_blog_source_preflight(run_id, stage_name)
            continue

        if kind == "micro-blog-registry-preflight":
            _run_micro_blog_registry_preflight(run_id, stage_name)
            continue

        if kind == "micro-blog-target-swarm-preflight":
            _run_micro_blog_target_swarm_preflight(run_id, stage_name)
            continue

        if kind == "micro-blog-build-push":
            _run_micro_blog_build_push(run_id, stage_name)
            continue

        if kind == "micro-blog-render-stack":
            _run_micro_blog_render_stack(run_id, stage_name)
            continue

        if kind == "micro-blog-stage-stack":
            _run_micro_blog_stage_stack(run_id, stage_name)
            continue

        if kind == "micro-blog-deploy-stack":
            _run_micro_blog_deploy_stack(run_id, stage_name)
            continue

        if kind == "micro-blog-validate-rollout":
            _run_micro_blog_validate_rollout(run_id, stage_name)
            continue

        if kind == "micro-blog-esxi-lab-canary-refresh-script":
            _run_micro_blog_esxi_lab_canary_refresh(run_id, stage_name)
            continue

        if kind == "micro-blog-content-sync-only":
            _run_micro_blog_content_sync_only(run_id, stage_name)
            continue

        if kind == "micro-blog-filesystem-sync-api":
            _run_micro_blog_filesystem_sync_api(run_id, stage_name)
            continue

        if kind == "micro-blog-content-proof":
            _run_micro_blog_content_proof(run_id, stage_name)
            continue

        if kind == "micro-blog-content-fragment-note":
            _run_micro_blog_content_fragment_note(run_id, stage_name)
            continue

        if kind == "micro-blog-public-ui-preflight":
            _run_micro_blog_public_ui_preflight(run_id, stage_name)
            continue

        if kind == "micro-blog-public-ui-source-sync":
            _run_micro_blog_public_ui_source_sync(run_id, stage_name)
            continue

        if kind == "micro-blog-public-ui-build":
            _run_micro_blog_public_ui_build(run_id, stage_name)
            continue

        if kind == "micro-blog-public-ui-up":
            _run_micro_blog_public_ui_up(run_id, stage_name)
            continue

        if kind == "micro-blog-public-ui-smoke":
            _run_micro_blog_public_ui_smoke(run_id, stage_name)
            continue

        if kind == "micro-blog-public-ui-fragment-note":
            _run_micro_blog_public_ui_fragment_note(run_id, stage_name)
            continue

        if kind == "micro-blog-public-backup":
            _run_micro_blog_public_backup(run_id, stage_name)
            continue

        if kind == "micro-blog-public-content-rsync":
            _run_micro_blog_public_content_rsync(run_id, stage_name)
            continue

        if kind == "micro-blog-public-filesystem-sync-api":
            _run_micro_blog_public_filesystem_sync_api(run_id, stage_name)
            continue

        if kind == "micro-blog-public-compose-proof":
            _run_micro_blog_public_compose_proof(run_id, stage_name)
            continue

        if kind == "micro-blog-public-fragment-note":
            _run_micro_blog_public_fragment_note(run_id, stage_name)
            continue

        if kind == "micro-blog-lab-journal-note":
            _run_micro_blog_note(
                run_id,
                stage_name,
                "Lab journal seeding is optional for this canary; markdown source is staged separately before import/bootstrap.",
                {"optional": True, "content_path": "/srv/micro-blog/content", "import_mode": "filesystem-sync"},
            )
            continue

        if kind == "micro-blog-telemetry-note":
            _run_micro_blog_note(
                run_id,
                stage_name,
                "Telemetry backhaul is optional for this canary; the deployed OTEL collector exposes :9464 metrics and accepts OTLP on :4317/:4318.",
                {"optional": True, "preferred_mode": "main-lab-scrape-or-forward"},
            )
            continue

        if kind == "micro-blog-edge-note":
            _run_micro_blog_note(
                run_id,
                stage_name,
                "Edge pointer recorded; add or update lab-edge once the internal health URL is validated.",
                {"edge_port": 8091, "target_port": 18081, "label": "Micro Blog — ESXi Swarm"},
            )
            continue

        if kind == "micro-blog-fragment-note":
            _run_micro_blog_note(
                run_id,
                stage_name,
                "Known-good fragment: micro-blog is the small Docker Swarm canary; rx-demo remains the heavier observability workload.",
                {"rating": "candidate-known-good", "tool_choice": "hammer-before-wrench"},
            )
            continue

        if kind == "trixie-pxe-prereqs":
            _run_trixie_pxe_prereqs(run_id, stage_name)
            continue

        if kind == "trixie-netboot-fetch":
            _run_trixie_netboot_fetch(run_id, stage_name)
            continue

        if kind == "trixie-ipxe-render":
            _run_trixie_template_upload(
                run_id,
                stage_name,
                "debian-trixie.ipxe.tpl",
                "ipxe_script_path",
                mode=0o644,
            )
            continue

        if kind == "trixie-preseed-render":
            _run_trixie_template_upload(
                run_id,
                stage_name,
                "trixie-smoke-preseed.cfg.tpl",
                "preseed_path",
                mode=0o644,
            )
            continue

        if kind == "trixie-vm-prepare":
            _run_trixie_vm_prepare(run_id, stage_name)
            continue

        if kind == "trixie-vm-boot":
            _run_trixie_vm_boot(run_id, stage_name)
            continue

        if kind == "trixie-vm-observe":
            _run_trixie_vm_observe(run_id, stage_name)
            continue

        if kind == "windows10-verify-iso":
            _run_windows10_verify_iso(run_id, stage_name)
            continue

        if kind == "windows10-inspect-vm":
            _run_windows10_inspect_vm(run_id, stage_name)
            continue

        if kind == "windows10-validate-openssh":
            _run_windows10_validate_openssh(run_id, stage_name)
            continue

        if kind == "windows10-stage-artifacts":
            _run_windows10_stage_artifacts(run_id, stage_name)
            continue

        if kind == "windows10-pxe-prereqs":
            _run_windows10_pxe_prereqs(run_id, stage_name)
            continue

        if kind == "windows10-pxe-verify-iso":
            _run_windows10_pxe_verify_iso(run_id, stage_name)
            continue

        if kind == "windows10-wimboot-fetch":
            _run_windows10_wimboot_fetch(run_id, stage_name)
            continue

        if kind == "windows10-winpe-stage":
            _run_windows10_winpe_stage(run_id, stage_name)
            continue

        if kind == "windows10-ipxe-render":
            _run_windows10_ipxe_render(run_id, stage_name)
            continue

        if kind == "windows10-media-share":
            _run_windows10_media_share(run_id, stage_name)
            continue

        if kind == "windows10-unattend-render":
            _run_windows10_unattend_render(run_id, stage_name)
            continue

        if kind == "windows10-dhcp-route-render":
            _run_windows10_dhcp_route_render(run_id, stage_name)
            continue

        if kind == "windows10-vm-prepare":
            _run_windows10_vm_prepare(run_id, stage_name)
            continue

        if kind == "windows10-vm-boot":
            _run_windows10_vm_boot(run_id, stage_name)
            continue

        if kind == "windows10-vm-observe":
            _run_windows10_vm_observe(run_id, stage_name)
            continue

        if kind == "windows10-post-install-ssh":
            _run_windows10_post_install_ssh(run_id, stage_name)
            continue

        if kind == "foobar-app-source-select":
            _run_foobar_app_source_select(run_id, stage_name)
            continue

        if kind == "foobar-app-vm-clone":
            _run_foobar_app_vm_clone(run_id, stage_name)
            continue

        if kind == "foobar-app-vm-boot":
            _run_foobar_app_vm_boot(run_id, stage_name)
            continue

        if kind == "foobar-app-relationships":
            _run_foobar_app_relationships(run_id, stage_name)
            continue

        if kind == "foobar-service-identity-vm":
            _run_foobar_service_identity_vm(run_id, stage_name)
            continue

        if kind == "foobar-service-guest-wait":
            _run_foobar_service_guest_wait(run_id, stage_name)
            continue

        if kind == "foobar-service-demo-lan":
            _run_foobar_service_demo_lan(run_id, stage_name)
            continue

        if kind == "foobar-service-identity-packages":
            _run_foobar_service_identity_packages(run_id, stage_name)
            continue

        if kind == "foobar-service-ldap-seed":
            _run_foobar_service_ldap_seed(run_id, stage_name)
            continue

        if kind == "foobar-service-samba-homes":
            _run_foobar_service_samba_homes(run_id, stage_name)
            continue

        if kind == "foobar-service-identity-portal":
            _run_foobar_service_identity_portal(run_id, stage_name)
            continue

        if kind == "foobar-service-suitecrm-provision":
            _run_foobar_service_suitecrm(run_id, stage_name)
            continue

        if kind == "foobar-service-kanboard-provision":
            _run_foobar_service_kanboard(run_id, stage_name)
            continue

        if kind == "foobar-service-validate":
            _run_foobar_service_validate(run_id, stage_name)
            continue

        if kind == "foobar-service-relationships":
            _run_foobar_service_relationships(run_id, stage_name)
            continue

        if kind == "trixie-personalize-discover":
            _run_trixie_personalize_discover(run_id, stage_name)
            continue

        if kind == "trixie-personalize-login":
            _run_trixie_personalize_login(run_id, stage_name)
            continue

        if kind == "trixie-personalize-checkpoints":
            _run_trixie_personalize_checkpoints(run_id, stage_name)
            continue

        if kind == "trixie-personalize-packages":
            _run_trixie_personalize_packages(run_id, stage_name)
            continue

        if kind == "trixie-personalize-vscode":
            _run_trixie_personalize_vscode(run_id, stage_name)
            continue

        if kind == "trixie-personalize-rustdesk":
            _run_trixie_personalize_rustdesk(run_id, stage_name)
            continue

        if kind == "trixie-personalize-services":
            _run_trixie_personalize_services(run_id, stage_name)
            continue

        if kind == "trixie-personalize-verify":
            _run_trixie_personalize_verify(run_id, stage_name)
            continue

        if kind == "windows10-personalize-discover":
            _run_windows10_personalize_discover(run_id, stage_name)
            continue

        if kind == "windows10-personalize-login":
            _run_windows10_personalize_login(run_id, stage_name)
            continue

        if kind == "windows10-personalize-checkpoints":
            _run_windows10_personalize_checkpoints(run_id, stage_name)
            continue

        if kind == "windows10-personalize-chocolatey":
            _run_windows10_personalize_chocolatey(run_id, stage_name)
            continue

        if kind == "windows10-personalize-packages":
            _run_windows10_personalize_packages(run_id, stage_name)
            continue

        if kind == "windows10-personalize-verify":
            _run_windows10_personalize_verify(run_id, stage_name)
            continue

        if kind == "windows10-builder-inspect-vm":
            _run_windows10_builder_inspect_vm(run_id, stage_name)
            continue

        if kind == "windows10-builder-ssh":
            _run_windows10_builder_ssh(run_id, stage_name)
            continue

        if kind == "windows10-adk-inspect":
            _run_windows10_adk_inspect(run_id, stage_name)
            continue

        if kind == "windows10-builder-stage-scripts":
            _run_windows10_builder_stage_scripts(run_id, stage_name)
            continue

        if kind == "windows10-adk-install":
            _run_windows10_adk_install(run_id, stage_name)
            continue

        if kind == "windows10-winpe-build":
            _run_windows10_winpe_build(run_id, stage_name)
            continue

        if kind == "windows10-winpe-publish":
            _run_windows10_winpe_publish(run_id, stage_name)
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

        if kind == "rx-demo-k3s-apply-observability":
            _run_rx_demo_k3s_apply_observability(run_id, stage_name)
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

        if kind == "rx-demo-k3s-publish-source-to-shared":
            _run_rx_demo_k3s_publish_source_to_shared(run_id, stage_name, settings)
            continue

        if kind == "rx-demo-k3s-redeploy-update-images":
            _run_rx_demo_k3s_redeploy_update_images(run_id, stage_name)
            continue

        if kind == "rx-demo-k3s-network-ready":
            _run_rx_demo_k3s_network_ready(run_id, stage_name)
            continue

        if kind == "rx-demo-k3s-cloudinit-node-check":
            _run_rx_demo_k3s_cloudinit_node_check(run_id, stage_name)
            continue

        if kind == "rx-demo-k3s-loki-cloudevents-check":
            _run_rx_demo_k3s_loki_cloudevents_check(run_id, stage_name)
            continue

        if kind == "rx-demo-k3s-redeploy-visible-activity":
            _run_rx_demo_k3s_redeploy_visible_activity(run_id, stage_name)
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

    settings = {} if config.get("settings_optional") else _remote_settings()
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

    settings = {} if config.get("settings_optional") else _remote_settings()
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
    except PipelineExecutionError as exc:
        failed_run = get_run(run_id) or {}
        active_stage = next(
            (
                str(stage.get("name") or "")
                for stage in failed_run.get("stages", [])
                if str(stage.get("status") or "").lower() == "active"
            ),
            "",
        )
        mark_run_failed(run_id, str(exc), active_stage)
        raise
    except Exception as exc:  # noqa: BLE001
        failed_run = get_run(run_id) or {}
        active_stage = next(
            (
                str(stage.get("name") or "")
                for stage in failed_run.get("stages", [])
                if str(stage.get("status") or "").lower() == "active"
            ),
            "",
        )
        mark_run_failed(run_id, str(exc), active_stage)
        raise PipelineExecutionError(str(exc)) from exc

    completed = get_run(run_id)
    return completed or run
