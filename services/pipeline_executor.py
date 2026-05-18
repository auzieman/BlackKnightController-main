from __future__ import annotations

import ipaddress
import json
import re
import shlex
import socket
import time
from base64 import b64encode
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
from services.remote_ops import run_remote_command
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
    ("192.168.1.10:/srv/nfs/swarm/blackknightcontroller", "/mnt/swarm/blackknightcontroller"),
]


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
    "tabor-build": {
        "supports_undeploy": False,
        "stage_plan": [
            {
                "name": "repo-sync",
                "transport": "ssh-controller",
                "target": "controller",
                "active": "Checking for the staged Auzix builder source tree on ns1.",
                "complete": "Staged Auzix builder source is present on ns1.",
                "command": (
                    "bash -lc '"
                    "test -d /srv/nfs/swarm/tabor-linux-forge/src/.git -o -f /srv/nfs/swarm/tabor-linux-forge/src/Readme.md "
                    "&& echo tabor-source-ready'"
                ),
                "timeout": 45,
            },
            {
                "name": "builder-prepare",
                "transport": "ssh-controller",
                "target": "controller",
                "active": "Rendering the swarm image-builder lane for tabor-linux-forge through Ansible.",
                "complete": "Swarm image-builder lane is rendered and validated.",
                "command": (
                    "cd /srv/ansible && "
                    "ANSIBLE_CONFIG=/srv/ansible/ansible.cfg "
                    "/opt/ansible-venv/bin/ansible-playbook -i inventory/lab.yml tabor-linux-forge-builder.yml"
                ),
                "timeout": 240,
            },
            {
                "name": "image-build",
                "transport": "ssh-manager",
                "target": "manager",
                "active": "Building the current Auzix artifact set on swarm1 through the staged builder container.",
                "complete": "Auzix builder finished on swarm1.",
                "command": (
                    "bash -lc '"
                    "cd /srv/stacks/tabor-linux-forge && "
                    "docker compose -f docker-compose.yml build kernel-builder && "
                    "/usr/local/bin/tabor-build ./scripts/fetch-linux.sh profiles/kernel/upstream-6.6-lts.env && "
                    "/usr/local/bin/tabor-build ./scripts/apply-patches.sh && "
                    "/usr/local/bin/tabor-build ./scripts/configure-tabor.sh && "
                    "/usr/local/bin/tabor-build ./scripts/build-kernel.sh && "
                    "/usr/local/bin/tabor-build ./scripts/package-kernel.sh'"
                ),
                "timeout": 5400,
            },
            {
                "name": "artifact-publish",
                "transport": "ssh-manager",
                "target": "manager",
                "active": "Verifying that Auzix build artifacts landed on shared storage.",
                "complete": "Artifacts are present on the NFS-backed build share.",
                "command": (
                    "bash -lc '"
                    "first=$(find /mnt/swarm/tabor-linux-forge/artifacts -maxdepth 2 -type f | head -n 1); "
                    "test -n \"$first\" || { echo no-tabor-artifacts; exit 1; }; "
                    "find /mnt/swarm/tabor-linux-forge/artifacts -maxdepth 2 -type f | head -n 12'"
                ),
                "timeout": 60,
            },
        ],
        "dashboard_message": "Auzix build artifacts should now exist under /srv/nfs/swarm/tabor-linux-forge/artifacts on ns1 and the matching NFS mount on the swarm hosts. The later hypervisor handoff will consume the produced boot media and VM image outputs.",
        "complete_message": "Auzix image build pipeline completed.",
        "runtime_snapshot": {
            "kind": "container-prefix",
            "container_name_prefix": "tabor-linux-forge-kernel-builder-run",
            "display_name": "kernel-builder-run",
        },
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
}

# Backward-compatible alias while older runs and drafts still reference the cloud-import name.
WORKFLOW_DEFINITIONS["fedora-cloud-import"] = WORKFLOW_DEFINITIONS["fedora-template-deploy"]


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
