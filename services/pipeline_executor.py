from __future__ import annotations

import json
import shlex
import socket
import time

from services.ansible import scan_ansible_controller
from services.ansible_inventory import parse_ansible_hosts, sync_ansible_inventory_to_rules
from services.automation_pipeline import (
    append_event,
    mark_run_active,
    mark_run_complete,
    mark_run_failed,
)
from services.automation_runs import get_run, update_run, update_stage
from services.docker_swarm import scan_docker_controller, sync_docker_inventory_to_rules
from services.fresh_build_library import fresh_build_plan
from services.integration_store import load_integrations, load_proxmox_snapshot, save_ansible_snapshot, save_docker_snapshot
from services.inventory_model import reconcile_rules_inventory
from services.proxmox import ProxmoxClient, load_proxmox_config
from services.remote_ops import run_remote_command
from services.rules_store import load_rules, save_rules
from services.ssh_keys import read_key_pair


class PipelineExecutionError(RuntimeError):
    pass


FEDORA_TEMPLATE_RELEASE = "44"
K3S_CLUSTER_NAME = "k3s-lab"
K3S_NODE_PLAN = [
    {"name": "kube1.lab.auzietek.com", "short": "kube1", "role": "server"},
    {"name": "kube2.lab.auzietek.com", "short": "kube2", "role": "agent"},
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
        score = 0
        if "fc44" in name:
            score += 4
        if "fedora" in name:
            score += 3
        if "minimal" in name:
            score += 2
        if name.startswith("fc-") or name.startswith("fc"):
            score += 1
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

    proxmox_host, proxmox_user, proxmox_password = _proxmox_ssh_target(config)
    key_path = f"/tmp/bkc-fedora-template-{vmid}.pub"
    vm_name = str(extra.get("fedora_template_vm_name", f"fedora-template-{vmid}")).strip()
    command = (
        "bash -lc 'set -euo pipefail; "
        f"vmid={vmid}; "
        f"cloudinit_storage={cloudinit_storage!r}; "
        f"ci_user={source.get('ci_user', 'auzieman')!r}; "
        f"hostname={vm_name!r}; "
        f"key_path={key_path!r}; "
        f"pubkey={public_key!r}; "
        "printf \"%s\\n\" \"$pubkey\" > \"$key_path\"; "
        "qm set \"$vmid\" --boot order=scsi0; "
        "qm set \"$vmid\" --ide2 \"$cloudinit_storage:cloudinit\"; "
        "qm set \"$vmid\" --ciuser \"$ci_user\" --ipconfig0 ip=dhcp --sshkey \"$key_path\"; "
        "qm set \"$vmid\" --agent enabled=1; "
        "qm set \"$vmid\" --name \"$hostname\" --nameserver 192.168.1.10 --searchdomain lab.auzietek.com; "
        "echo fedora-template-configured'"
    )
    output = run_remote_command(
        host=proxmox_host,
        user=proxmox_user,
        password=proxmox_password,
        command=command,
        timeout=240,
    )
    _set_stage(run_id, stage_name, "complete", "Fedora template clone configured for first boot.")
    append_event(run_id, "info", stage_name, output or f"Configured Fedora template clone {vm_name} (vmid {vmid}).")


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
    vmid: int,
    vm_name: str,
    cloudinit_storage: str,
    public_key: str,
    ci_user: str,
    ipconfig0: str,
    nameserver: str,
    searchdomain: str,
) -> str:
    proxmox_host, proxmox_user, proxmox_password = _proxmox_ssh_target(proxmox_config)
    key_path = f"/tmp/bkc-k3s-{vmid}.pub"
    command = (
        "bash -lc 'set -euo pipefail; "
        f"vmid={vmid}; "
        f"cloudinit_storage={shlex.quote(cloudinit_storage)}; "
        f"ci_user={shlex.quote(ci_user)}; "
        f"hostname={shlex.quote(vm_name)}; "
        f"key_path={shlex.quote(key_path)}; "
        f"pubkey={shlex.quote(public_key)}; "
        f"ipconfig0={shlex.quote(ipconfig0)}; "
        f"nameserver={shlex.quote(nameserver)}; "
        f"searchdomain={shlex.quote(searchdomain)}; "
        "printf \"%s\\n\" \"$pubkey\" > \"$key_path\"; "
        "qm set \"$vmid\" --boot order=scsi0; "
        "qm set \"$vmid\" --ide2 \"$cloudinit_storage:cloudinit\"; "
        "qm set \"$vmid\" --ciuser \"$ci_user\" --ipconfig0 \"$ipconfig0\" --sshkey \"$key_path\"; "
        "qm set \"$vmid\" --agent enabled=1; "
        "qm set \"$vmid\" --name \"$hostname\" --nameserver \"$nameserver\" --searchdomain \"$searchdomain\"; "
        "echo k3s-vm-configured'"
    )
    return run_remote_command(
        host=proxmox_host,
        user=proxmox_user,
        password=proxmox_password,
        command=command,
        timeout=240,
    )


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
            vmid=new_vmid,
            vm_name=vm_name,
            cloudinit_storage=str(target["cloudinit_storage"]),
            public_key=public_key,
            ci_user=str((node.get("cloudinit") or {}).get("ci_user") or "root"),
            ipconfig0=str((node.get("cloudinit") or {}).get("ipconfig0") or "ip=dhcp"),
            nameserver=str(plan.get("nameserver") or "192.168.1.10"),
            searchdomain=str(plan.get("searchdomain") or "lab.auzietek.com"),
        )
        cloned.append({**dict(node), "vmid": new_vmid, "proxmox_node": source_node, "clone_upid": str(upid)})

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
    return run_remote_command(host=host, user="root", password="", command=command, timeout=timeout)


def _run_k3s_discover_ssh(run_id: str, stage_name: str) -> None:
    deadline = time.monotonic() + 900
    resolved = []
    for node in _k3s_nodes(run_id):
        name = str(node.get("name", "")).strip()
        ip = ""
        ready = False
        last_error = ""
        while time.monotonic() < deadline:
            try:
                ip = socket.gethostbyname(name)
                probe_node = {**node, "ip": ip}
                _k3s_ssh_command(probe_node, "true", timeout=20)
                ready = True
                break
            except Exception as exc:  # noqa: BLE001
                last_error = str(exc)
                time.sleep(10)
        if not ready:
            raise PipelineExecutionError(f"SSH did not become ready for {name} before timeout: {last_error}")
        resolved.append({**node, "ip": ip})
    _store_run_extra(run_id, {"k3s_nodes": resolved})
    _set_stage(run_id, stage_name, "complete", "K3s guests are reachable over SSH.")
    append_event(run_id, "info", stage_name, json.dumps({"ssh_ready": resolved}, sort_keys=True))


def _run_k3s_base_bootstrap(run_id: str, stage_name: str) -> None:
    outputs = []
    for node in _k3s_nodes(run_id):
        fqdn = str(node.get("name", "")).strip()
        command = (
            "bash -lc 'set -euo pipefail; "
            f"hostnamectl set-hostname {shlex.quote(fqdn)}; "
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
    command = (
        "bash -lc 'set -euo pipefail; "
        "if systemctl is-active --quiet k3s 2>/dev/null; then echo k3s-server-present; exit 0; fi; "
        "curl -sfL https://get.k3s.io | "
        "INSTALL_K3S_CHANNEL=stable sh -s - server "
        "--write-kubeconfig-mode 644 "
        "--disable traefik "
        f"--node-name {shlex.quote(str(server.get('short') or 'kube1'))} "
        f"--tls-san {shlex.quote(str(server.get('name') or 'kube1.lab.auzietek.com'))}; "
        "systemctl is-active --quiet k3s; "
        "echo k3s-server-ready'"
    )
    output = _k3s_ssh_command(server, command, timeout=1200)
    _store_run_extra(run_id, {"k3s_api_url": "https://kube1.lab.auzietek.com:6443", "k3s_kubeconfig_path": "/etc/rancher/k3s/k3s.yaml"})
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
    token = str((run.get("extra", {}) or {}).get("k3s_join_token", "")).strip()
    if not token:
        raise PipelineExecutionError("K3s join token is missing. Re-run capture-k3s-token.")
    agent = _k3s_node_by_role(run_id, "agent")
    command = (
        "bash -lc 'set -euo pipefail; "
        "if systemctl is-active --quiet k3s-agent 2>/dev/null; then echo k3s-agent-present; exit 0; fi; "
        "curl -sfL https://get.k3s.io | "
        f"INSTALL_K3S_CHANNEL=stable K3S_URL={shlex.quote('https://kube1.lab.auzietek.com:6443')} K3S_TOKEN={shlex.quote(token)} "
        "sh -s - agent "
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
