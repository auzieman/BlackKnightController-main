from __future__ import annotations

import json
import os
import tempfile
import fcntl
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from services.rules_store import BASE_DIR

DEFAULT_RUNS = {"runs": []}


def _runtime_root() -> Path:
    override = os.environ.get("BKC_RUNTIME_ROOT", "").strip()
    if override:
        return Path(override)
    return BASE_DIR / "dictionaries"


def _path() -> Path:
    return _runtime_root() / "automation_runs.local.json"


def _lock_path() -> Path:
    return _runtime_root() / ".automation_runs.lock"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def load_runs() -> list[dict]:
    path = _path()
    if not path.exists():
        return []
    try:
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except json.JSONDecodeError:
        return []
    return list(payload.get("runs", []))


def save_runs(runs: list[dict]) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({"runs": runs}, indent=2, sort_keys=True) + "\n"
    fd, tmp_name = tempfile.mkstemp(prefix=".automation_runs.", suffix=".json", dir=str(path.parent))
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


def default_stages(workflow: str, extra: dict | None = None) -> list[str]:
    normalized = (workflow or "auzix-test-loop").strip().lower()
    action_mode = str((extra or {}).get("action_mode", "")).strip().lower()
    if normalized == "tabor-build":
        return ["repo-sync", "builder-prepare", "image-build", "artifact-publish"]
    if normalized == "auzix-vm130-deploy":
        return ["source-verify", "runtime-deploy", "network-validate"]
    if normalized == "auzix-vm134-install-refresh":
        return [
            "source-verify",
            "installer-root-build",
            "iso-build",
            "iso-publish",
            "vm-target-verify",
            "install-handoff",
        ]
    if normalized == "auzix-vm135-fresh-install-target":
        return [
            "artifact-verify",
            "iso-publish",
            "vm135-recreate",
            "vm135-start",
            "install-handoff",
        ]
    if normalized == "auzix-core-root-validation":
        return ["source-verify", "builder-prepare", "core-validation", "prompt-report"]
    if normalized == "auzix-installer-foundation":
        return ["source-verify", "installer-build", "contract-test", "artifact-report"]
    if normalized == "auzix-installer-package-bot":
        return [
            "source-verify",
            "queue-contract",
            "package-build",
            "artifact-report",
            "repository-build",
            "repository-publish",
            "repository-verify",
        ]
    if normalized == "auzix-trixie-package-intake":
        return [
            "source-verify",
            "builder-prepare",
            "package-intake",
            "repository-build",
            "repository-publish",
            "repository-verify",
        ]
    if normalized == "auzix-office-package-smoke":
        return [
            "source-verify",
            "builder-prepare",
            "package-build",
            "package-test",
            "repository-build",
            "repository-publish",
            "repository-verify",
        ]
    if normalized == "lab-cluster-storage":
        return ["storage-preflight", "swarm-grow", "k3s-grow", "storage-verify"]
    if normalized == "fedora-workstation-spin":
        return ["repo-sync", "manifest-resolve", "image-compose", "artifact-publish"]
    if normalized in {"fedora-cloud-import", "fedora-template-deploy"}:
        return ["source-select", "proxmox-import", "instance-configure", "boot", "ssh-validate"]
    if normalized == "fedora-cosmic-postinstall":
        return [
            "target-select",
            "wait-ssh",
            "package-plan",
            "desktop-install",
            "graphical-enable",
            "reboot",
            "gui-validate",
            "register-resource",
        ]
    if normalized == "k3s-fedora-cluster":
        return [
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
        ]
    if normalized == "k3s-host-telemetry":
        return [
            "verify-k3s",
            "nfs-projects",
            "apply-host-telemetry",
            "apply-loki-logs",
            "loadgen-steady",
            "open-firewall",
            "prometheus-targets",
            "scrape-validate",
            "dashboard-link",
        ]
    if normalized == "small-office-foobar-reference":
        return [
            "load-recipe-intent",
            "plan-identity-storage",
            "plan-crm-intranet",
            "provision-linux-developer-workstations",
            "provision-windows-helpdesk-workstations",
            "personalize-workstations",
            "validate-small-office",
            "render-demo-lifecycle",
        ]
    if normalized == "small-office-foobar-reset":
        return [
            "load-reset-scope",
            "plan-demo-vm-removal",
            "plan-pxe-route-cleanup",
            "plan-evidence-archive",
            "verify-reset-boundary",
        ]
    if normalized == "small-office-foobar-app-vms":
        return [
            "load-app-vm-plan",
            "select-trixie-source",
            "clone-app-vms",
            "boot-app-vms",
            "record-app-relationships",
        ]
    if normalized == "small-office-foobar-services":
        return [
            "load-service-plan",
            "ensure-identity-vm",
            "wait-service-guests",
            "configure-demo-lan",
            "install-identity-packages",
            "seed-ldap-directory",
            "configure-samba-homes",
            "publish-identity-portal",
            "provision-suitecrm-service",
            "provision-kanboard-service",
            "validate-foobar-services",
            "record-service-relationships",
        ]
    if normalized == "baremetal-bmc-discovery-prepare":
        return [
            "load-bmc-discovery-intent",
            "observe-ns1-neighbors",
            "probe-redfish-roots",
            "map-bmcs-to-hosts",
            "record-discovery-handoff",
        ]
    if normalized == "ns1-lan-mac-pxe-prepare":
        return [
            "resolve-ns1-node",
            "validate-lan-interface",
            "ensure-dhcp-include",
            "render-lan-mac-pxe-fragment",
            "render-lan-dhcp-defaults",
            "validate-dhcp-config",
            "restart-dhcp-if-enabled",
            "record-pxe-mac-relationship",
        ]
    if normalized == "baremetal-r630-pxe-validation":
        return [
            "resolve-physical-identity",
            "validate-bmc-reachability",
            "validate-provisioning-services",
            "validate-image-assets",
            "validate-storage-visibility",
            "render-boot-intent",
            "record-validation-evidence",
        ]
    if normalized == "ns1-default-pxe-diagnostics":
        return [
            "load-diagnostic-intent",
            "validate-diagnostic-boundary",
            "render-default-diagnostic-ipxe",
            "render-dhcp-diagnostic-fragment",
            "validate-live-assets",
            "plan-enable-lease-only-boundary",
            "record-default-diagnostic-profile",
        ]
    if normalized == "baremetal-openstack-lab-prepare":
        return [
            "load-hardware-intent",
            "register-physical-nodes",
            "validate-bmc-access",
            "validate-provisioning-services",
            "plan-openstack-edge-network",
            "validate-base-os-image",
            "select-storage-profile",
            "render-openstack-base-boot-intent",
            "plan-base-os-install",
            "plan-firstboot-enrollment",
            "validate-openstack-host-baseline",
            "prepare-trixie-neutron-hosts",
            "plan-openstack-installer-handoff",
        ]
    if normalized == "baremetal-openstack-lab-deploy":
        return ["destructive-one-shot-trixie"]
    if normalized == "baremetal-proxmox-deploy":
        return ["validate-unattended-media", "arm-and-boot-server2", "wipe-install-observe-and-disarm", "validate-proxmox-firstboot"]
    if normalized == "native-openstack-all-in-one":
        return ["install-native-openstack", "validate-openstack-services"]
    if normalized == "lab-dual-platform-seed-validate":
        return ["seed-openstack-resources", "seed-proxmox-base-guests", "validate-both-platforms"]
    if normalized == "openstack-local-ai-openwebui-preflight":
        return [
            "preflight-server1-ai-capacity",
            "ensure-openstack-ai-vm",
            "install-ollama-baremetal",
            "deploy-openwebui-container",
            "validate-local-ai-stack",
            "record-ollama-openwebui-fragments",
        ]
    if normalized == "openstack-docker-swarm-seed":
        return [
            "preflight-openstack-swarm-base",
            "ensure-openstack-swarm-vms",
            "bootstrap-openstack-docker-swarm",
            "validate-openstack-docker-swarm",
            "record-openstack-swarm-fragments",
        ]
    if normalized == "trixie-openstack-host-prepare":
        return [
            "load-openstack-host-intent",
            "validate-firstboot-login",
            "normalize-firstboot-baseline",
            "validate-network-sides",
            "prepare-neutron-host-packages",
            "validate-neutron-host-readiness",
            "cache-openstack-image-assets",
            "validate-openstack-web-target",
            "record-openstack-network-profile",
        ]
    if normalized == "openstack-lab-seed-and-validate":
        return [
            "load-openstack-seed-intent",
            "validate-openstack-api-access",
            "validate-horizon-dashboard",
            "seed-project-and-user",
            "seed-networks",
            "seed-image-flavor-keypair-security",
            "launch-smoke-instance",
            "validate-smoke-instance",
            "record-bkc-openstack-ownership",
        ]
    if normalized == "openstack-kolla-single-node-install":
        return [
            "load-kolla-install-intent",
            "validate-kolla-host-readiness",
            "prepare-kolla-dependencies",
            "install-kolla-ansible",
            "render-kolla-configuration",
            "kolla-bootstrap-servers",
            "kolla-prechecks",
            "kolla-deploy",
            "kolla-post-deploy",
            "validate-horizon-keystone",
            "record-kolla-handoff",
        ]
    if normalized == "baremetal-vmware-trial-prepare":
        return [
            "load-hardware-intent",
            "register-physical-nodes",
            "validate-bmc-access",
            "validate-provisioning-services",
            "validate-operator-supplied-media",
            "render-vmware-kickstart-intent",
            "plan-stage-vmware-installer-media",
            "plan-esxi-install",
            "validate-vmware-firstboot",
            "validate-esxi-web-target",
            "plan-vcenter-registration",
        ]
    if normalized == "baremetal-proxmox-trial-prepare":
        return [
            "load-proxmox-intent",
            "validate-unattended-media",
            "arm-server2-one-shot-pxe",
            "plan-server2-install",
            "validate-proxmox-management",
            "disarm-server2-one-shot-pxe",
        ]
    if normalized == "baremetal-lab-reset":
        return [
            "load-reset-scope",
            "archive-current-evidence",
            "plan-pxe-state-clear",
            "plan-unattended-profile-restore",
            "plan-power-reset",
            "plan-disk-wipe",
            "plan-rerun-sequence",
            "verify-rerun-boundary",
        ]
    if normalized == "demo-swarm-image-registry":
        return [
            "storage-ready",
            "deploy-registry-stack",
            "registry-health",
            "k3s-dns-or-ip",
            "k3s-containerd-trust",
            "push-smoke-image",
            "pull-smoke-image",
        ]
    if normalized == "rx-demo-k3s-registry-preflight":
        return [
            "registry-reachable",
            "k3s-registry-trust",
            "build-and-push",
            "registry-catalog",
        ]
    if normalized == "rx-demo-k3s-deploy":
        return [
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
        ]
    if normalized == "rx-demo-k3s-undeploy":
        return [
            "capture-state",
            "delete-overlay",
            "delete-namespace",
            "verify-removed",
            "registry-retained",
        ]
    if normalized == "rx-demo-redeploy-from-git-event" or normalized == "rx-demo-k3s-redeploy-from-git":
        return [
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
        ]
    if normalized == "rx-demo-k3s-observability-refresh":
        return [
            "git-event",
            "sync-source-from-git",
            "publish-source-to-shared",
            "apply-observability",
            "rollout-observability",
            "telemetry-check",
            "grafana-loki-check",
            "access-links",
        ]
    if normalized == "demo-k3s-add-node":
        if action_mode == "undeploy":
            return [
                "select-worker",
                "delete-k3s-node",
                "destroy-worker-vm",
                "verify-reset",
            ]
        return [
            "select-target",
            "clone-worker",
            "boot-worker",
            "discover-ssh",
            "base-os-prep",
            "capture-join-token",
            "install-k3s-agent",
            "verify-node-ready",
            "extend-telemetry",
            "register-inventory",
        ]
    if normalized == "rx-demo-k3s-app-refresh":
        return [
            "verify-k3s",
            "source-check",
            "build-rx-ui-image",
            "import-rx-ui-image",
            "apply-lab-overlay",
            "smoke-ui-routes",
            "dashboard-link",
        ]
    if normalized == "wordpress-appliance-import":
        return ["source-select", "proxmox-clone", "boot", "ssh-validate"]
    if normalized == "blackknight-sync":
        return ["repo-sync", "service-build", "deploy", "health-check"]
    if normalized == "ns1-provisioning-network-prepare":
        return [
            "resolve-ns1-node",
            "discover-current-network",
            "select-provisioning-interface",
            "apply-provisioning-address",
            "validate-management-still-reachable",
            "record-network-relationships",
        ]
    if normalized == "ns1-provisioning-dhcp-prepare":
        return [
            "resolve-ns1-node",
            "verify-provisioning-network",
            "ensure-dhcp-include",
            "render-dhcp-fragment",
            "render-dhcp-defaults",
            "install-dhcp-package",
            "validate-dhcp-config",
            "keep-dhcp-disabled",
            "record-dhcp-relationships",
        ]
    if normalized == "ns1-trixie-pxe-smoke":
        return [
            "resolve-provisioning-context",
            "verify-ns1-pxe-prereqs",
            "fetch-trixie-netboot",
            "render-ipxe-entry",
            "render-preseed-profile",
            "prepare-vm132-pxe-target",
            "pxe-boot-vm132",
            "observe-installer-handoff",
            "post-boot-recollect",
            "record-trixie-relationships",
        ]
    if normalized == "windows10-reference-discover":
        return [
            "verify-iso",
            "inspect-vm113",
            "validate-openssh",
            "stage-firstboot-artifacts",
            "record-windows-relationships",
        ]
    if normalized == "windows10-pxe-smoke":
        return [
            "resolve-windows-pxe-context",
            "verify-ns1-pxe-prereqs",
            "verify-windows-iso",
            "fetch-wimboot",
            "stage-windows-install-media",
            "render-windows-ipxe",
            "configure-windows-media-share",
            "render-unattend-firstboot",
            "render-dhcp-windows-route",
            "prepare-vm136-pxe-target",
            "pxe-boot-vm136",
            "observe-winpe-handoff",
            "post-install-ssh-check",
            "record-windows-pxe-relationships",
        ]
    if normalized == "trixie-workstation-personalize":
        return [
            "discover-installed-trixie",
            "normalize-local-login",
            "publish-demo-checkpoints",
            "install-workstation-packages",
            "install-vscode-if-enabled",
            "install-rustdesk-if-configured",
            "enable-graphical-services",
            "verify-trixie-personality",
            "record-trixie-personality",
        ]
    if normalized == "windows10-workstation-personalize":
        return [
            "discover-installed-windows",
            "normalize-local-login",
            "publish-demo-checkpoints",
            "ensure-chocolatey",
            "install-workstation-packages",
            "verify-windows-personality",
            "record-windows-personality",
        ]
    if normalized == "windows10-winpe-builder":
        return [
            "resolve-builder-context",
            "inspect-vm113",
            "validate-builder-ssh",
            "inspect-adk-tooling",
            "stage-builder-scripts",
            "install-adk-if-enabled",
            "build-winpe-if-enabled",
            "publish-winpe-if-enabled",
            "record-winpe-builder-relationships",
        ]
    if normalized == "host-telemetry":
        if action_mode == "undeploy":
            return ["telemetry-plan", "telemetry-remove", "health-check", "inventory-refresh", "dashboard-link"]
        return ["telemetry-apply", "health-check", "inventory-refresh", "dashboard-link"]
    if normalized in {"monitoring-stack", "microblog-publish"}:
        if action_mode == "undeploy":
            return ["stack-plan", "stack-remove", "health-check", "inventory-refresh", "dashboard-link"]
        if normalized == "monitoring-stack":
            return ["stack-render", "stack-deploy", "health-check", "grafana-init", "inventory-refresh", "dashboard-link"]
        return ["repo-sync", "stack-deploy", "health-check", "inventory-refresh", "dashboard-link"]
    if normalized == "lab-demo":
        return [
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
        ]
    return [
        "repo-sync",
        "tabor-build",
        "auzix-image",
        "ansible-post",
        "proxmox-boot-test",
        "summary-publish",
    ]


def create_run(
    *,
    tenant_slug: str,
    requested_by: str,
    trigger_source: str,
    repo: str,
    workflow: str,
    ref: str = "",
    commit: str = "",
    notes: str = "",
    extra: dict | None = None,
) -> dict:
    now = utc_now_iso()
    stages = [
        {
            "name": stage_name,
            "status": "planned",
            "updated_at": now,
            "detail": "",
        }
        for stage_name in default_stages(workflow, extra)
    ]
    return {
        "id": str(uuid4()),
        "tenant_slug": tenant_slug.strip() or "default",
        "requested_by": requested_by.strip(),
        "trigger_source": trigger_source.strip() or "api",
        "repo": repo.strip(),
        "workflow": workflow.strip() or "auzix-test-loop",
        "ref": ref.strip(),
        "commit": commit.strip(),
        "notes": notes.strip(),
        "status": "planned",
        "created_at": now,
        "updated_at": now,
        "stages": stages,
        "artifacts": {},
        "events": [
            {
                "at": now,
                "level": "info",
                "stage": "pipeline",
                "message": f"Run created for {repo.strip()} using {workflow.strip() or 'auzix-test-loop'}.",
            }
        ],
        "extra": deepcopy(extra or {}),
    }


def append_run(run: dict) -> dict:
    lock_path = _lock_path()
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            runs = load_runs()
            runs.insert(0, run)
            save_runs(runs)
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
    return run


def get_run(run_id: str) -> dict | None:
    for run in load_runs():
        if run.get("id") == run_id:
            return run
    return None


def update_run(run_id: str, updater) -> dict | None:
    lock_path = _lock_path()
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            runs = load_runs()
            updated = None
            for idx, run in enumerate(runs):
                if run.get("id") != run_id:
                    continue
                candidate = deepcopy(run)
                updater(candidate)
                candidate["updated_at"] = utc_now_iso()
                runs[idx] = candidate
                updated = candidate
                break
            if updated is not None:
                save_runs(runs)
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
    return updated


def update_stage(run_id: str, stage_name: str, status: str, detail: str = "") -> dict | None:
    def _apply(run: dict) -> None:
        for stage in run.get("stages", []):
            if stage.get("name") == stage_name:
                stage["status"] = status
                stage["detail"] = detail
                stage["updated_at"] = utc_now_iso()
                break

    return update_run(run_id, _apply)


def append_event(run_id: str, level: str, stage: str, message: str) -> dict | None:
    def _apply(run: dict) -> None:
        run.setdefault("events", []).append(
            {
                "at": utc_now_iso(),
                "level": level.strip() or "info",
                "stage": stage.strip() or "pipeline",
                "message": message.strip(),
            }
        )

    return update_run(run_id, _apply)
