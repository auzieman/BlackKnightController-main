from __future__ import annotations

import json
from pathlib import Path

import paramiko

from services.integration_store import load_integrations
from services.ssh_keys import read_key_pair


class DockerScanError(RuntimeError):
    pass


def _run_command(client: paramiko.SSHClient, command: str) -> str:
    stdin, stdout, stderr = client.exec_command(command)
    output = stdout.read().decode("utf-8", errors="replace")
    error = stderr.read().decode("utf-8", errors="replace")
    if error and not output:
        raise DockerScanError(error.strip())
    return output.strip()


def _json_lines(output: str) -> list[dict]:
    items: list[dict] = []
    for line in output.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            items.append(json.loads(line))
        except json.JSONDecodeError:
            items.append({"raw": line})
    return items


def scan_docker_controller() -> dict:
    integrations = load_integrations()
    docker_cfg = integrations["docker"]
    ssh = integrations["ssh"]

    manager_host = docker_cfg.get("manager_host", "").strip()
    manager_user = docker_cfg.get("manager_user", "").strip()
    manager_password = docker_cfg.get("manager_password", "").strip()

    if not manager_host or not manager_user:
        raise DockerScanError("Set Docker manager host and user first.")

    key_info = read_key_pair(ssh["private_key_path"], ssh["public_key_path"])
    private_key = Path(key_info["private_key_path"])

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        connect_kwargs = {
            "hostname": manager_host,
            "username": manager_user,
            "timeout": 10,
        }
        if manager_password:
            connect_kwargs["password"] = manager_password
        elif private_key.exists():
            connect_kwargs["key_filename"] = str(private_key)
        else:
            raise DockerScanError(
                "No usable auth for Docker manager. Install the BKC SSH key or provide a manager password."
            )

        client.connect(**connect_kwargs)
        swarm_info = _run_command(client, "docker info --format '{{json .Swarm}}'")
        node_ls = _run_command(client, "docker node ls --format '{{json .}}'")
        stack_ls = _run_command(client, "docker stack ls --format '{{json .}}'")
        service_ls = _run_command(client, "docker service ls --format '{{json .}}'")
    except Exception as exc:
        raise DockerScanError(str(exc)) from exc
    finally:
        client.close()

    try:
        swarm = json.loads(swarm_info) if swarm_info else {}
    except json.JSONDecodeError:
        swarm = {"raw": swarm_info}

    return {
        "manager_host": manager_host,
        "manager_user": manager_user,
        "auth_method": "password" if manager_password else "ssh-key",
        "swarm": swarm,
        "nodes": _json_lines(node_ls),
        "stacks": _json_lines(stack_ls),
        "services": _json_lines(service_ls),
    }


def sync_docker_inventory_to_rules(rules: dict, docker_scan: dict) -> dict:
    created_groups = 0
    created_nodes = 0
    updated_nodes = 0

    group_name = "docker-swarm"
    group_exists = group_name in rules["groups"]
    group = rules["groups"].setdefault(group_name, {"locals": {}, "nodes": {}})
    if not group_exists:
        created_groups += 1

    group["locals"].update(
        {
            "configuration": group["locals"].get("configuration", "docker"),
            "provider": group["locals"].get("provider", "docker-swarm"),
            "workflow": group["locals"].get(
                "workflow", "build -> configure -> validate -> deploy"
            ),
            "swarm_cluster_id": str(docker_scan.get("swarm", {}).get("Cluster", {}).get("ID", "")),
        }
    )

    for entry in docker_scan.get("nodes", []):
        host_name = str(entry.get("Hostname") or entry.get("Name") or "").strip()
        if not host_name:
            continue
        existing = group["nodes"].get(host_name, {})
        if existing:
            updated_nodes += 1
        else:
            created_nodes += 1

        manager_status = str(entry.get("ManagerStatus", "")).strip()
        application = "docker-swarm-worker"
        if manager_status:
            application = "docker-swarm-manager"

        group["nodes"][host_name] = {
            **existing,
            "provider": existing.get("provider", "docker-swarm"),
            "configuration": existing.get("configuration", "docker"),
            "state": str(entry.get("Status") or existing.get("state") or "unknown").lower(),
            "application": existing.get("application", application),
            "hostname": existing.get("hostname", host_name.split(".", 1)[0]),
            "fqdn": existing.get("fqdn", host_name if "." in host_name else ""),
            "user": existing.get("user", ""),
            "private_key": existing.get("private_key", ""),
            "docker_role": "manager" if manager_status else "worker",
            "docker_availability": str(entry.get("Availability", "")),
            "docker_engine_version": str(entry.get("EngineVersion", "")),
        }

    services_by_stack: dict[str, list[dict]] = {}
    for entry in docker_scan.get("services", []):
        service_name = str(entry.get("Name") or "").strip()
        if not service_name:
            continue
        stack_name, short_name = (service_name.split("_", 1) + [service_name])[:2]
        if "_" not in service_name:
            stack_name = "unstacked"
            short_name = service_name
        service_entry = {
            "full_name": service_name,
            "short_name": short_name,
            "stack_name": stack_name,
            "mode": str(entry.get("Mode", "")),
            "replicas": str(entry.get("Replicas", "")),
            "image": str(entry.get("Image", "")),
            "ports": str(entry.get("Ports", "")),
        }
        services_by_stack.setdefault(stack_name, []).append(service_entry)

    for stack_name, services in services_by_stack.items():
        stack_group_name = f"stack-{stack_name}"
        stack_group_exists = stack_group_name in rules["groups"]
        stack_group = rules["groups"].setdefault(stack_group_name, {"locals": {}, "nodes": {}})
        if not stack_group_exists:
            created_groups += 1

        stack_group["locals"].update(
            {
                "configuration": stack_group["locals"].get("configuration", "docker-service"),
                "provider": stack_group["locals"].get("provider", "docker-swarm"),
                "workflow": stack_group["locals"].get(
                    "workflow", "plan -> deploy -> health-check -> observe"
                ),
                "stack_name": stack_name,
            }
        )

        for service in services:
            service_key = service["full_name"]
            existing = stack_group["nodes"].get(service_key, {})
            if existing:
                updated_nodes += 1
            else:
                created_nodes += 1

            replicas = service["replicas"]
            state = "configured"
            if "/" in replicas:
                desired, actual = replicas.split("/", 1)
                state = "running" if desired == actual else "degraded"

            stack_group["nodes"][service_key] = {
                **existing,
                "provider": existing.get("provider", "docker-swarm"),
                "provisioner": existing.get("provisioner", "docker-stack"),
                "configuration": existing.get("configuration", "docker-service"),
                "state": state,
                "application": existing.get("application", service["short_name"]),
                "hostname": existing.get("hostname", service["short_name"]),
                "fqdn": existing.get("fqdn", service_key),
                "identity": existing.get("identity", service["short_name"]),
                "docker_stack": stack_name,
                "docker_service": service_key,
                "docker_mode": service["mode"],
                "docker_replicas": replicas,
                "docker_image": service["image"],
                "docker_ports": service["ports"],
                "services_detected": existing.get("services_detected", [service["short_name"]]),
                "provider_sources": existing.get("provider_sources", ["docker-swarm"]),
            }

    return {
        "groups": created_groups,
        "created_nodes": created_nodes,
        "updated_nodes": updated_nodes,
    }
