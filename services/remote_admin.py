from pathlib import Path

import paramiko

from services.integration_store import load_integrations
from services.inventory_model import merge_host_config
from services.rules_store import load_rules
from services.ssh_keys import read_key_pair


class RemoteAdminError(RuntimeError):
    pass


def _run_command(client: paramiko.SSHClient, command: str) -> tuple[str, str, int]:
    stdin, stdout, stderr = client.exec_command(command)
    exit_status = stdout.channel.recv_exit_status()
    output = stdout.read().decode("utf-8", errors="replace")
    error = stderr.read().decode("utf-8", errors="replace")
    return output.strip(), error.strip(), exit_status


def resolve_host_config(group_name: str, host_name: str) -> tuple[dict, dict]:
    rules = load_rules()
    group = rules["groups"].get(group_name)
    if not group:
        raise RemoteAdminError(f"Unknown group {group_name}.")
    node = group.get("nodes", {}).get(host_name)
    if not node:
        raise RemoteAdminError(f"Unknown host {host_name} in group {group_name}.")
    return group, merge_host_config(rules.get("globals", {}), group, node)


def _host_connect_args(host_config: dict, hostname: str) -> tuple[dict, str]:
    connect_kwargs = {
        "hostname": host_config.get("ip") or hostname,
        "username": host_config.get("user"),
        "port": int(host_config.get("port") or 22),
        "timeout": 10,
    }
    password = host_config.get("password", "")
    private_key = host_config.get("private_key", "")
    if password:
        connect_kwargs["password"] = password
        return connect_kwargs, "password"
    if private_key:
        connect_kwargs["key_filename"] = private_key
        return connect_kwargs, "ssh-key"
    raise RemoteAdminError(f"No auth configured for host {hostname}.")


def connect_host(group_name: str, host_name: str) -> tuple[paramiko.SSHClient, dict, str]:
    _, resolved = resolve_host_config(group_name, host_name)
    connect_kwargs, auth_method = _host_connect_args(resolved, host_name)

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(**connect_kwargs)
    except Exception as exc:
        client.close()
        raise RemoteAdminError(str(exc)) from exc
    return client, resolved, auth_method


def run_client_command(client: paramiko.SSHClient, command: str) -> tuple[str, str, int]:
    return _run_command(client, command)


def run_host_command(group_name: str, host_name: str, command: str) -> dict:
    try:
        client, _, auth_method = connect_host(group_name, host_name)
        stdout, stderr, exit_status = _run_command(client, command)
    except Exception as exc:
        if isinstance(exc, RemoteAdminError):
            raise
        raise RemoteAdminError(str(exc)) from exc
    finally:
        if "client" in locals():
            client.close()

    return {
        "target": host_name,
        "group": group_name,
        "command": command,
        "auth_method": auth_method,
        "stdout": stdout,
        "stderr": stderr,
        "exit_status": exit_status,
    }


def run_host_commands(targets: list[tuple[str, str]], command: str) -> list[dict]:
    results = []
    for group_name, host_name in targets:
        try:
            results.append(run_host_command(group_name, host_name, command))
        except RemoteAdminError as exc:
            results.append(
                {
                    "target": host_name,
                    "group": group_name,
                    "command": command,
                    "auth_method": "failed",
                    "stdout": "",
                    "stderr": str(exc),
                    "exit_status": -1,
                }
            )
    return results


def bucket_command_results(results: list[dict]) -> list[dict]:
    buckets: dict[tuple[int, str, str], dict] = {}
    for result in results:
        key = (result["exit_status"], result["stdout"], result["stderr"])
        bucket = buckets.setdefault(
            key,
            {
                "exit_status": result["exit_status"],
                "stdout": result["stdout"],
                "stderr": result["stderr"],
                "targets": [],
            },
        )
        bucket["targets"].append(f"{result.get('group', '')}:{result['target']}")
    return sorted(buckets.values(), key=lambda item: (item["exit_status"], len(item["targets"]) * -1))


def run_ansible_playbook(limit: str = "", extra_args: str = "", playbook_override: str = "") -> dict:
    integrations = load_integrations()
    ansible = integrations["ansible"]
    ssh = integrations["ssh"]

    controller_host = ansible.get("controller_host", "").strip()
    controller_user = ansible.get("controller_user", "").strip()
    controller_password = ansible.get("controller_password", "").strip()
    inventory_path = ansible.get("inventory_path", "").strip() or "/etc/ansible/hosts"
    playbook = playbook_override.strip() or ansible.get("playbook", "").strip()
    if not controller_host or not controller_user or not playbook:
        raise RemoteAdminError("Ansible controller host, user, and playbook must be configured first.")

    key_info = read_key_pair(ssh["private_key_path"], ssh["public_key_path"])
    private_key = Path(key_info["private_key_path"])

    connect_kwargs = {
        "hostname": controller_host,
        "username": controller_user,
        "timeout": 10,
    }
    auth_method = ""
    if controller_password:
        connect_kwargs["password"] = controller_password
        auth_method = "password"
    elif private_key.exists():
        connect_kwargs["key_filename"] = str(private_key)
        auth_method = "ssh-key"
    else:
        raise RemoteAdminError("No auth configured for Ansible controller.")

    limit_arg = f" --limit {limit}" if limit else ""
    extra_arg = f" {extra_args.strip()}" if extra_args.strip() else ""
    command = f"ansible-playbook -i {inventory_path} {playbook}{limit_arg}{extra_arg}"

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(**connect_kwargs)
        stdout, stderr, exit_status = _run_command(client, command)
    except Exception as exc:
        raise RemoteAdminError(str(exc)) from exc
    finally:
        client.close()

    return {
        "target": controller_host,
        "command": command,
        "auth_method": auth_method,
        "stdout": stdout,
        "stderr": stderr,
        "exit_status": exit_status,
    }
