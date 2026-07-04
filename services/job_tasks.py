"""Background jobs (import paths used by RQ — keep signatures stable)."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def _app():
    import bkc_server

    return bkc_server.app


def pull_proxmox_inventory_job(tenant_slug: str, tenant_id: int | None, user_id: int | None, remote_ip: str | None):
    import os

    os.environ["BKC_TENANT_SLUG"] = tenant_slug
    from services import bkc_db
    from services.integration_store import save_proxmox_snapshot
    from services.proxmox import ProxmoxClient, load_proxmox_config, summarize_inventory

    app = _app()
    with app.app_context():
        inv = summarize_inventory(ProxmoxClient(load_proxmox_config()))
        save_proxmox_snapshot(inv)
        bkc_db.append_audit(
            user_id,
            tenant_id,
            "job.pull_proxmox_inventory",
            "integrations",
            {
                "nodes": len(inv.get("nodes", [])),
                "vms": len(inv.get("virtual_machines", [])),
                "cts": len(inv.get("containers", [])),
            },
            remote_ip,
        )
        return {
            "nodes": len(inv.get("nodes", [])),
            "vms": len(inv.get("virtual_machines", [])),
            "cts": len(inv.get("containers", [])),
        }


def sync_proxmox_inventory_job(tenant_slug: str, tenant_id: int | None, user_id: int | None, remote_ip: str | None):
    import os

    os.environ["BKC_TENANT_SLUG"] = tenant_slug
    from services import bkc_db
    from services.integration_store import save_proxmox_snapshot
    from services.inventory_model import reconcile_rules_inventory
    from services.proxmox import ProxmoxClient, load_proxmox_config, summarize_inventory, sync_inventory_to_rules
    from services.rules_store import load_rules, save_rules

    app = _app()
    with app.app_context():
        inv = summarize_inventory(ProxmoxClient(load_proxmox_config()))
        save_proxmox_snapshot(inv)
        rules = load_rules()
        result = sync_inventory_to_rules(rules, inv)
        reconcile = reconcile_rules_inventory(rules)
        save_rules(rules)
        bkc_db.append_audit(
            user_id,
            tenant_id,
            "job.sync_proxmox_inventory",
            "integrations",
            {**result, "reconcile_clusters": reconcile.get("clusters")},
            remote_ip,
        )
        return {"sync": result, "reconcile": reconcile}


def scan_ansible_job(tenant_slug: str, tenant_id: int | None, user_id: int | None, remote_ip: str | None):
    import os

    os.environ["BKC_TENANT_SLUG"] = tenant_slug
    from services import bkc_db
    from services.ansible import scan_ansible_controller
    from services.integration_store import save_ansible_snapshot

    app = _app()
    with app.app_context():
        scan = scan_ansible_controller()
        save_ansible_snapshot(scan)
        bkc_db.append_audit(
            user_id,
            tenant_id,
            "job.scan_ansible",
            "integrations",
            {"playbooks": len(scan.get("playbooks", []))},
            remote_ip,
        )
        return {"playbooks": len(scan.get("playbooks", []))}


def sync_ansible_inventory_job(tenant_slug: str, tenant_id: int | None, user_id: int | None, remote_ip: str | None):
    import os

    os.environ["BKC_TENANT_SLUG"] = tenant_slug
    from services import bkc_db
    from services.ansible import scan_ansible_controller
    from services.ansible_inventory import parse_ansible_hosts, sync_ansible_inventory_to_rules
    from services.integration_store import save_ansible_snapshot
    from services.inventory_model import reconcile_rules_inventory
    from services.rules_store import load_rules, save_rules

    app = _app()
    with app.app_context():
        scan = scan_ansible_controller()
        save_ansible_snapshot(scan)
        parsed = parse_ansible_hosts(scan["inventory_content"])
        rules = load_rules()
        result = sync_ansible_inventory_to_rules(rules, parsed)
        reconcile = reconcile_rules_inventory(rules)
        save_rules(rules)
        bkc_db.append_audit(
            user_id,
            tenant_id,
            "job.sync_ansible_inventory",
            "integrations",
            {**result, "reconcile_clusters": reconcile.get("clusters")},
            remote_ip,
        )
        return {"sync": result, "reconcile": reconcile}


def scan_docker_job(tenant_slug: str, tenant_id: int | None, user_id: int | None, remote_ip: str | None):
    import os

    os.environ["BKC_TENANT_SLUG"] = tenant_slug
    from services import bkc_db
    from services.docker_swarm import scan_docker_controller
    from services.integration_store import save_docker_snapshot

    app = _app()
    with app.app_context():
        scan = scan_docker_controller()
        save_docker_snapshot(scan)
        bkc_db.append_audit(
            user_id,
            tenant_id,
            "job.scan_docker",
            "integrations",
            {
                "nodes": len(scan.get("nodes", [])),
                "stacks": len(scan.get("stacks", [])),
                "services": len(scan.get("services", [])),
            },
            remote_ip,
        )
        return {
            "nodes": len(scan.get("nodes", [])),
            "stacks": len(scan.get("stacks", [])),
            "services": len(scan.get("services", [])),
        }


def sync_docker_inventory_job(tenant_slug: str, tenant_id: int | None, user_id: int | None, remote_ip: str | None):
    import os

    os.environ["BKC_TENANT_SLUG"] = tenant_slug
    from services import bkc_db
    from services.docker_swarm import scan_docker_controller, sync_docker_inventory_to_rules
    from services.integration_store import save_docker_snapshot
    from services.inventory_model import reconcile_rules_inventory
    from services.rules_store import load_rules, save_rules

    app = _app()
    with app.app_context():
        scan = scan_docker_controller()
        save_docker_snapshot(scan)
        rules = load_rules()
        result = sync_docker_inventory_to_rules(rules, scan)
        reconcile = reconcile_rules_inventory(rules)
        save_rules(rules)
        bkc_db.append_audit(
            user_id,
            tenant_id,
            "job.sync_docker_inventory",
            "integrations",
            {**result, "reconcile_clusters": reconcile.get("clusters")},
            remote_ip,
        )
        return {"sync": result, "reconcile": reconcile}


def scan_kubernetes_job(tenant_slug: str, tenant_id: int | None, user_id: int | None, remote_ip: str | None):
    import os

    os.environ["BKC_TENANT_SLUG"] = tenant_slug
    from services import bkc_db
    from services.integration_store import save_kubernetes_snapshot
    from services.kubernetes_api import scan_kubernetes_cluster

    app = _app()
    with app.app_context():
        scan = scan_kubernetes_cluster()
        save_kubernetes_snapshot(scan)
        bkc_db.append_audit(
            user_id,
            tenant_id,
            "job.scan_kubernetes",
            "integrations",
            {
                "nodes": len(scan.get("nodes", [])),
                "namespaces": len(scan.get("namespaces", [])),
                "pods": len(scan.get("pods", [])),
                "services": len(scan.get("services", [])),
            },
            remote_ip,
        )
        return {
            "nodes": len(scan.get("nodes", [])),
            "namespaces": len(scan.get("namespaces", [])),
            "pods": len(scan.get("pods", [])),
            "services": len(scan.get("services", [])),
        }


def scan_subnet_job(payload: dict[str, Any]):
    """Subnet discovery + import (single dict arg for RQ simplicity)."""
    import os

    tenant_slug = str(payload["tenant_slug"])
    os.environ["BKC_TENANT_SLUG"] = tenant_slug
    from services import bkc_db
    from services.discovery import DiscoveryError, import_discovered_nodes, scan_subnet_ssh
    from services.inventory_model import reconcile_rules_inventory
    from services.rules_store import load_rules, save_rules

    app = _app()
    with app.app_context():
        try:
            scan_result = scan_subnet_ssh(
                subnet=str(payload["subnet"]),
                username=str(payload["username"]),
                password=str(payload.get("password") or ""),
                install_key=bool(payload.get("install_key")),
            )
            rules = load_rules()
            imported = import_discovered_nodes(
                rules,
                group_name=str(payload["group_name"]),
                scan_result=scan_result,
            )
            reconcile = reconcile_rules_inventory(rules)
            save_rules(rules)
            bkc_db.append_audit(
                int(payload["user_id"]) if payload.get("user_id") is not None else None,
                int(payload["tenant_id"]) if payload.get("tenant_id") is not None else None,
                "job.scan_subnet",
                "add_nodes",
                {
                    "subnet": scan_result.get("subnet"),
                    "ssh_hosts": scan_result.get("ssh_hosts"),
                    "imported": imported,
                    "clusters": reconcile.get("clusters"),
                },
                payload.get("remote_ip"),
            )
            return {
                "subnet": scan_result.get("subnet"),
                "ssh_hosts": scan_result.get("ssh_hosts"),
                "imported": imported,
                "clusters": reconcile.get("clusters"),
            }
        except DiscoveryError as exc:
            logger.warning("scan_subnet_job failed: %s", exc)
            raise


def automation_pipeline_job(
    run_id: str,
    tenant_slug: str,
    tenant_id: int | None,
    user_id: int | None,
    remote_ip: str | None,
):
    import os

    os.environ["BKC_TENANT_SLUG"] = tenant_slug
    from services import bkc_db
    from services.automation_pipeline import mark_run_failed, mark_run_waiting_executor
    from services.pipeline_executor import PipelineExecutionError, current_active_stage_name, execute_pipeline_run

    app = _app()
    with app.app_context():
        run = mark_run_waiting_executor(run_id)
        try:
            run = execute_pipeline_run(run_id)
        except PipelineExecutionError as exc:
            run = mark_run_failed(run_id, str(exc), stage_name=current_active_stage_name(run_id)) or run
        bkc_db.append_audit(
            user_id,
            tenant_id,
            "job.automation_pipeline",
            "automation",
            {"run_id": run_id, "status": (run or {}).get("status", "missing")},
            remote_ip,
        )
        return {
            "run_id": run_id,
            "status": (run or {}).get("status", "missing"),
        }
