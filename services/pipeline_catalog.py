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
        "description": "Run the strict AuzixRoot filesystem contract demo through the tabor-linux-forge builder lane, then publish the audit output for the later image and VM boot-test legs.",
        "stages": [
            "repo-sync",
            "builder-ready",
            "strict-root-scaffold",
            "sample-payload-build",
            "busybox-package-build",
            "strict-root-audit",
            "strict-container-build",
            "legacy-prune-test",
            "artifact-publish",
            "dashboard-link",
        ],
        "actions": [
            "repo.source.verify",
            "docker.builder.prepare",
            "auzix.root.scaffold",
            "auzix.package.build",
            "auzix.busybox.build",
            "auzix.root.audit",
            "docker.image.import_root",
            "auzix.legacy_links.prune_test",
            "artifact.report.publish",
        ],
        "notes": "Primary Auzix demo lane. It deliberately proves the strict root contract in the tabor container/build substrate before attempting a full image or hypervisor handoff.",
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
                "summary": "Watch swarm node pressure and shared-storage backed runtime behavior during the Auzix strict-root path.",
                "url": "http://swarm1.lab.auzietek.com:3000",
            },
        ],
        "tags": ["auzix", "tabor", "strict-root", "demo"],
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
        "id": "demo-swarm-image-registry",
        "name": "Demo: Swarm Image Registry",
        "repo": "BlackKnightController",
        "workflow": "demo-swarm-image-registry",
        "description": "Deploy the demo registry stack, validate push and pull behavior, and prepare k3s nodes to trust the swarm-hosted image source.",
        "stages": [
            "storage-ready",
            "deploy-registry-stack",
            "registry-health",
            "k3s-dns-or-ip",
            "k3s-containerd-trust",
            "push-smoke-image",
            "pull-smoke-image",
        ],
        "actions": [
            "docker.registry.probe",
            "docker.image.push",
            "k3s.registry.trust",
        ],
        "notes": "Demo step 1. Use this before the rx-demo deploy lane when the cluster image source needs to be proven on camera.",
        "editable": True,
        "links": [
            {"label": "BlackKnightController", "url": "http://swarm1.lab.auzietek.com:5000"},
            {"label": "Registry", "url": "http://swarm1.lab.auzietek.com:5001/v2/"},
            {"label": "Portainer", "url": "https://swarm1.lab.auzietek.com:9443"},
        ],
        "dashboards": [
            {
                "name": "Pipeline Control",
                "summary": "Watch registry deploy, push, and k3s trust stages from the BKC pipeline surface.",
                "url": "http://swarm1.lab.auzietek.com:5000/pipelines?pipeline=demo-swarm-image-registry",
            },
        ],
        "tags": ["demo", "registry", "swarm", "k3s", "deploy"],
    },
    {
        "id": "rx-demo-k3s-deploy",
        "name": "Demo: Rx Demo K3s Deploy",
        "repo": "rx-demo",
        "workflow": "rx-demo-k3s-deploy",
        "description": "Build rx-demo images, apply the k3s demo overlay, roll out the app and observability stack, smoke the UI/API, and publish access links.",
        "stages": [
            "k3s-ready",
            "runtime-secrets",
            "registry-images",
            "apply-k3s-demo-overlay",
            "rollout-app",
            "rollout-observability",
            "smoke-api",
            "smoke-ui",
            "telemetry-check",
            "access-links",
        ],
        "actions": [
            "k3s.nodes.ready",
            "docker.images.build_push",
            "kubectl.manifest.apply",
            "kubectl.rollout.wait",
            "http.smoke",
            "prometheus.metrics.check",
        ],
        "notes": "Demo step 2. This is the full rx-demo bring-up lane and now validates the Grafmaid panel plugin as part of telemetry checks.",
        "editable": True,
        "links": [
            {"label": "Rx UI", "url": "http://192.168.1.239:30080"},
            {"label": "Rx API", "url": "http://192.168.1.239:30081"},
            {"label": "Grafana", "url": "http://192.168.1.239:30300"},
            {"label": "Prometheus", "url": "http://192.168.1.239:30090"},
        ],
        "dashboards": [
            {
                "name": "Rx Traffic Map",
                "summary": "Grafmaid-backed service map for the rx-demo flow.",
                "url": "http://192.168.1.239:30300/d/rx-traffic-map/rx-traffic-map",
            },
            {
                "name": "Rx CloudEvents Audit",
                "summary": "Known transaction audit trail through Loki.",
                "url": "http://192.168.1.239:30300/d/rx-cloudevents/rx-cloudevents-audit",
            },
        ],
        "tags": ["demo", "k3s", "rx-demo", "deploy", "grafana"],
    },
    {
        "id": "rx-demo-k3s-redeploy-from-git",
        "name": "Demo: Rx Demo Redeploy From Git",
        "repo": "rx-demo",
        "workflow": "rx-demo-k3s-redeploy-from-git",
        "description": "Record a Git-triggered rx-demo redeploy, build and push commit-tagged images, update k3s, and prove CloudEvents still arrive in Loki/Grafana.",
        "stages": [
            "git-event",
            "sync-source-from-git",
            "build-and-push",
            "update-images",
            "k3s-network-ready",
            "rollout-app",
            "cloudinit-node-check",
            "visible-change-check",
            "telemetry-still-flowing",
            "loki-cloudevents-check",
            "grafana-loki-check",
            "access-links",
        ],
        "actions": [
            "git.source.sync",
            "docker.images.build_push",
            "kubectl.image.update",
            "kubectl.rollout.wait",
            "loki.query.verify",
            "grafana.datasource.verify",
        ],
        "notes": "Demo step 3. Use this for the source-to-runtime story after the full stack is already online.",
        "editable": True,
        "links": [
            {"label": "Rx UI", "url": "http://192.168.1.239:30080"},
            {"label": "Grafana", "url": "http://192.168.1.239:30300"},
            {"label": "Pipelines", "url": "http://swarm1.lab.auzietek.com:5000/pipelines?pipeline=rx-demo-k3s-redeploy-from-git"},
        ],
        "dashboards": [
            {
                "name": "Rx CloudEvents Audit",
                "summary": "Shows the known transaction generated by the redeploy validation lane.",
                "url": "http://192.168.1.239:30300/d/rx-cloudevents/rx-cloudevents-audit",
            },
        ],
        "tags": ["demo", "git", "k3s", "rx-demo", "redeploy"],
    },
    {
        "id": "rx-demo-k3s-observability-refresh",
        "name": "Demo: Rx Demo Observability Refresh",
        "repo": "rx-demo",
        "workflow": "rx-demo-k3s-observability-refresh",
        "description": "Sync rx-demo from Git, reapply the k3s observability manifests, restart Grafana, and validate dashboard/plugin health without rebuilding application images.",
        "stages": [
            "git-event",
            "sync-source-from-git",
            "publish-source-to-shared",
            "apply-observability",
            "rollout-observability",
            "telemetry-check",
            "grafana-loki-check",
            "access-links",
        ],
        "actions": [
            "git.source.sync",
            "kubectl.manifest.apply",
            "kubectl.rollout.wait",
            "prometheus.metrics.check",
            "grafana.datasource.verify",
        ],
        "notes": "Use this for dashboard and observability manifest updates when the deployed app images are already acceptable for the recording.",
        "editable": True,
        "links": [
            {"label": "Rx UI", "url": "http://192.168.1.239:30080"},
            {"label": "Grafana", "url": "http://192.168.1.239:30300"},
            {"label": "Traffic Map", "url": "http://192.168.1.239:30300/d/rx-traffic-map/rx-traffic-map"},
            {"label": "Pipelines", "url": "http://swarm1.lab.auzietek.com:5000/pipelines?pipeline=rx-demo-k3s-observability-refresh"},
        ],
        "dashboards": [
            {
                "name": "Rx Traffic Map",
                "summary": "Grafmaid-backed service map showing browser events, rx-ui, API, RabbitMQ, workers, Redis, and observability flow.",
                "url": "http://192.168.1.239:30300/d/rx-traffic-map/rx-traffic-map",
            },
            {
                "name": "Rx CloudEvents Audit",
                "summary": "Known transaction audit trail through Loki.",
                "url": "http://192.168.1.239:30300/d/rx-cloudevents/rx-cloudevents-audit",
            },
        ],
        "tags": ["demo", "git", "k3s", "rx-demo", "grafana", "observability"],
    },
    {
        "id": "rx-demo-k3s-undeploy",
        "name": "Demo: Rx Demo K3s Undeploy",
        "repo": "rx-demo",
        "workflow": "rx-demo-k3s-undeploy",
        "description": "Capture rx-demo state, remove demo-owned app and observability resources, verify cleanup, and keep registry artifacts intact.",
        "stages": [
            "capture-state",
            "delete-overlay",
            "delete-namespace",
            "verify-removed",
            "registry-retained",
        ],
        "actions": [
            "kubectl.get",
            "kubectl.delete",
            "kubectl.verify_absent",
            "docker.registry.probe",
        ],
        "notes": "Demo step 4. Use this from the latest rx-demo run's Undeploy button or queue it directly to reset the rehearsal environment.",
        "editable": True,
        "links": [
            {"label": "Pipelines", "url": "http://swarm1.lab.auzietek.com:5000/pipelines?pipeline=rx-demo-k3s-undeploy"},
            {"label": "Registry", "url": "http://swarm1.lab.auzietek.com:5001/v2/"},
        ],
        "dashboards": [
            {
                "name": "Pipeline Control",
                "summary": "Follow cleanup evidence and verify the demo namespaces were removed.",
                "url": "http://swarm1.lab.auzietek.com:5000/pipelines?pipeline=rx-demo-k3s-undeploy",
            },
        ],
        "tags": ["demo", "cleanup", "k3s", "rx-demo", "undeploy"],
    },
    {
        "id": "rx-demo-k3s-app-refresh",
        "name": "Demo: Rx Demo App Refresh",
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
            {"label": "Rx UI", "url": "http://192.168.1.239:30080"},
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
        "name": "AuziX Image Build",
        "repo": "AuziX",
        "workflow": "tabor-build",
        "description": "Use the staged AuziX source on ns1, prepare the swarm builder, construct the current AuziX build artifacts, and verify they landed on shared storage.",
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
        "id": "auzix-installer-package-bot",
        "name": "AuziX Installer Package Bot",
        "repo": "AuziX",
        "workflow": "auzix-installer-package-bot",
        "description": "Build and publish the installer UI package batch sequentially on the bounded BKC slow worker.",
        "resource_class": "slow",
        "stages": [
            "source-verify",
            "queue-contract",
            "package-build",
            "artifact-report",
            "repository-build",
            "repository-publish",
            "repository-verify",
        ],
        "notes": "Consumes the installer queue and source catalog, runs only allowlisted package scripts, stops on failure, builds checksummed AuziX archives, and publishes index.json last.",
        "editable": True,
        "links": [
            {"label": "BlackKnightController", "url": "http://swarm1.lab.auzietek.com:5000"},
            {"label": "Package Bot Runs", "url": "http://swarm1.lab.auzietek.com:5000/pipelines?pipeline=auzix-installer-package-bot"},
        ],
        "tags": ["auzix", "installer", "packages", "build", "slow-worker"],
    },
    {
        "id": "auzix-trixie-package-intake",
        "name": "AuziX Trixie Package Intake",
        "repo": "AuziX",
        "workflow": "auzix-trixie-package-intake",
        "description": "Attempt the vmid132-derived Trixie application list sequentially and publish successful AuziX compatibility packages.",
        "resource_class": "slow",
        "stages": [
            "source-verify",
            "builder-prepare",
            "package-intake",
            "repository-build",
            "repository-publish",
            "repository-verify",
        ],
        "notes": "Uses a Debian Trixie builder, continues after individual package failures, records a JSON report, and publishes successful Debian.<name> compatibility packages.",
        "editable": True,
        "links": [
            {"label": "BlackKnightController", "url": "http://swarm1.lab.auzietek.com:5000"},
            {"label": "AuziX Repository", "url": "http://192.168.1.10/auzix/repo/"},
        ],
        "tags": ["auzix", "trixie", "applications", "packages", "slow-worker"],
    },
    {
        "id": "auzix-office-package-smoke",
        "name": "AuziX Office Package Smoke",
        "repo": "AuziX",
        "workflow": "auzix-office-package-smoke",
        "description": "Build, validate, and publish focused AbiWord and Gnumeric compatibility packages.",
        "resource_class": "slow",
        "stages": [
            "source-verify",
            "builder-prepare",
            "package-build",
            "package-test",
            "repository-build",
            "repository-publish",
            "repository-verify",
        ],
        "notes": "Uses a dedicated two-package profile and report, then verifies both payloads and served checksummed archives.",
        "editable": True,
        "links": [
            {"label": "BlackKnightController", "url": "http://swarm1.lab.auzietek.com:5000"},
            {"label": "AuziX Repository", "url": "http://192.168.1.10/auzix/repo/"},
        ],
        "tags": ["auzix", "trixie", "office", "packages", "smoke", "slow-worker"],
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
    override = os.environ.get("BKC_PIPELINE_DEFINITIONS_PATH", "").strip()
    if override:
        return Path(override).expanduser()
    return BASE_DIR / "dictionaries" / "pipeline_definitions.local.json"


def _pipeline_folders_path() -> Path:
    override = os.environ.get("BKC_PIPELINE_FOLDERS_PATH", "").strip()
    if override:
        return Path(override).expanduser()
    return BASE_DIR / "dictionaries" / "pipelines"


def _repo_pipeline_folders_path() -> Path:
    override = os.environ.get("BKC_REPO_PIPELINE_FOLDERS_PATH", "").strip()
    if override:
        return Path(override).expanduser()
    return BASE_DIR / "pipelines"


def _source_layer(source_type: str, path: Path, folder: Path) -> dict:
    return {
        "source_type": source_type,
        "source_path": str(path),
        "source_folder": str(folder),
    }


def _load_json_file(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return {}
    return dict(payload) if isinstance(payload, dict) else {}


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


def _load_pipeline_items(folder: Path, source_type: str) -> list[dict]:
    items_dir = folder / "items"
    if not items_dir.is_dir():
        return []
    items: list[dict] = []
    for path in sorted(items_dir.glob("*.json")):
        try:
            with path.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        item = dict(payload)
        item.setdefault("id", path.stem)
        item.setdefault("source_type", source_type)
        item.setdefault("source_path", str(path))
        item.setdefault("source_folder", str(folder))
        items.append(item)
    return items


def _load_folder_pipelines(root: Path, source_type: str) -> list[dict]:
    if not root.is_dir():
        return []
    pipelines: list[dict] = []
    for folder in sorted(path for path in root.iterdir() if path.is_dir()):
        path = folder / "pipeline.json"
        dictionary_path = folder / "dictionary.json"
        if not path.exists() and not (source_type == "runtime-folder" and dictionary_path.exists()):
            continue
        try:
            with (path if path.exists() else dictionary_path).open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        if not path.exists():
            pipeline_id = str(payload.get("pipeline_id") or payload.get("id") or "").strip()
            if not pipeline_id:
                continue
            pipeline = {
                "id": pipeline_id,
                "dictionary": dict(payload),
            }
            path = dictionary_path
        elif not payload.get("id"):
            continue
        else:
            pipeline = dict(payload)
        if pipeline.get("id") and not pipeline.get("workflow"):
            pipeline["workflow"] = str(pipeline["id"])
        if pipeline.get("summary") and not pipeline.get("description"):
            pipeline["description"] = str(pipeline["summary"])
        if not pipeline.get("repo"):
            pipeline["repo"] = "BlackKnightController"
        pipeline.setdefault("source_type", source_type)
        pipeline.setdefault("source_path", str(path))
        pipeline.setdefault("source_folder", str(folder))
        pipeline.setdefault("source_layers", [_source_layer(source_type, path, folder)])
        items = _load_pipeline_items(folder, source_type)
        if items:
            pipeline["items"] = items
        pipelines.append(pipeline)
    return pipelines


def _load_repo_folder_pipelines() -> list[dict]:
    return _load_folder_pipelines(_repo_pipeline_folders_path(), "repo-folder")


def _load_runtime_folder_pipelines() -> list[dict]:
    return _load_folder_pipelines(_pipeline_folders_path(), "runtime-folder")


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
        if key in {"actions", "dashboards", "gates", "items", "links", "stages", "tags"} and isinstance(value, list):
            merged[key] = deepcopy(value)
        elif key == "source_layers" and isinstance(value, list):
            existing_layers = list(merged.get("source_layers", []))
            for layer in value:
                if layer not in existing_layers:
                    existing_layers.append(deepcopy(layer))
            merged["source_layers"] = existing_layers
        else:
            merged[key] = value
    return merged


def demo_pipelines() -> list[dict]:
    overrides = _load_overrides()
    repo_folder_pipelines = {pipeline["id"]: pipeline for pipeline in _load_repo_folder_pipelines()}
    runtime_folder_pipelines = {pipeline["id"]: pipeline for pipeline in _load_runtime_folder_pipelines()}
    builtins = []
    for pipeline in BUILTIN_PIPELINES:
        merged = _merge_pipeline(pipeline, {"source_type": "legacy-catalog", "source_layers": []})
        repo_folder_pipeline = repo_folder_pipelines.pop(pipeline["id"], None)
        if repo_folder_pipeline:
            merged = _merge_pipeline(merged, repo_folder_pipeline)
        if pipeline["id"] in overrides:
            override = deepcopy(overrides[pipeline["id"]])
            override.setdefault("source_type", "local-override")
            merged = _merge_pipeline(merged, override)
        runtime_folder_pipeline = runtime_folder_pipelines.pop(pipeline["id"], None)
        if runtime_folder_pipeline:
            merged = _merge_pipeline(merged, runtime_folder_pipeline)
        builtins.append(merged)
    customs = [dict(item) for item in _load_custom_pipelines()]
    for custom in customs:
        custom.setdefault("source_type", "custom")
        custom.setdefault("source_layers", [])

    folder_only = []
    for pipeline_id, pipeline in sorted(repo_folder_pipelines.items()):
        runtime_folder_pipeline = runtime_folder_pipelines.pop(pipeline_id, None)
        if runtime_folder_pipeline:
            pipeline = _merge_pipeline(pipeline, runtime_folder_pipeline)
        folder_only.append(pipeline)

    return builtins + customs + folder_only + list(runtime_folder_pipelines.values())


def pipeline_by_id(pipeline_id: str) -> dict | None:
    for pipeline in demo_pipelines():
        if pipeline["id"] == pipeline_id:
            return pipeline
    return None


def resolve_pipeline_dictionary(pipeline: dict | None, run_inputs: dict | None = None) -> dict:
    if not pipeline:
        return {"values": {}, "layers": [], "missing": []}

    values: dict = {}
    layers: list[dict] = []

    for layer in pipeline.get("source_layers", []):
        source_type = str(layer.get("source_type", "")).strip()
        folder = Path(str(layer.get("source_folder", "")))
        if source_type == "repo-folder":
            defaults_path = folder / "defaults.json"
            defaults = _load_json_file(defaults_path)
            if defaults:
                values.update(defaults)
                layers.append(
                    {
                        "name": "repo defaults",
                        "source_type": source_type,
                        "source_path": str(defaults_path),
                        "keys": sorted(defaults.keys()),
                    }
                )
        if source_type == "runtime-folder":
            dictionary_path = folder / "dictionary.json"
            dictionary = _load_json_file(dictionary_path)
            if dictionary:
                values.update(dictionary)
                layers.append(
                    {
                        "name": "runtime dictionary",
                        "source_type": source_type,
                        "source_path": str(dictionary_path),
                        "keys": sorted(dictionary.keys()),
                    }
                )

    embedded_dictionary = pipeline.get("dictionary")
    if isinstance(embedded_dictionary, dict):
        embedded = dict(embedded_dictionary)
        if embedded:
            values.update(embedded)
            source_path = str(pipeline.get("source_path") or "")
            if not any(layer.get("source_path") == source_path for layer in layers):
                layers.append(
                    {
                        "name": "embedded dictionary",
                        "source_type": str(pipeline.get("source_type") or "pipeline"),
                        "source_path": source_path,
                        "keys": sorted(embedded.keys()),
                    }
                )

    inputs = dict(run_inputs or {})
    if inputs:
        values.update(inputs)
        layers.append(
            {
                "name": "run inputs",
                "source_type": "run-inputs",
                "source_path": "",
                "keys": sorted(inputs.keys()),
            }
        )

    missing = []
    for name, spec in dict(pipeline.get("inputs", {})).items():
        if not isinstance(spec, dict) or not spec.get("required"):
            continue
        if values.get(name) in ("", None, [], {}):
            missing.append(name)

    return {"values": values, "layers": layers, "missing": sorted(missing)}


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
