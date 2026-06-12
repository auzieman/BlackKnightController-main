from __future__ import annotations

import json
import os
import tempfile
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
    if normalized == "auzix-installer-foundation":
        return ["source-verify", "installer-build", "contract-test", "artifact-report"]
    if normalized == "auzix-installer-package-bot":
        return ["source-verify", "queue-contract", "package-build", "artifact-report"]
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
            "image-build",
            "monitoring-deploy",
            "microblog-publish",
            "hypervisor-handoff",
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
    runs = load_runs()
    runs.insert(0, run)
    save_runs(runs)
    return run


def get_run(run_id: str) -> dict | None:
    for run in load_runs():
        if run.get("id") == run_id:
            return run
    return None


def update_run(run_id: str, updater) -> dict | None:
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
