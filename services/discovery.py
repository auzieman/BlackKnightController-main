import ipaddress
import socket
from pathlib import Path

import paramiko

from services.integration_store import load_integrations
from services.ssh_keys import read_key_pair


class DiscoveryError(RuntimeError):
    pass


def _expand_targets(target: str) -> list[str]:
    target = target.strip()
    if not target:
        raise DiscoveryError("Scan target is empty.")

    if "/" in target:
        try:
            network = ipaddress.ip_network(target, strict=False)
        except ValueError as exc:
            raise DiscoveryError(str(exc)) from exc
        return [str(address) for address in network.hosts()]

    if "-" in target:
        start, end = target.rsplit(".", 1)[0], target.rsplit(".", 1)[1]
        if "-" not in end:
            raise DiscoveryError(f"Invalid range: {target}")
        start_octet, end_octet = end.split("-", 1)
        prefix = target.rsplit(".", 1)[0]
        try:
            first = int(start_octet)
            last = int(end_octet)
        except ValueError as exc:
            raise DiscoveryError(f"Invalid range: {target}") from exc
        if first > last:
            raise DiscoveryError(f"Invalid range: {target}")
        hosts = []
        for octet in range(first, last + 1):
            hosts.append(str(ipaddress.ip_address(f"{prefix}.{octet}")))
        return hosts

    try:
        return [str(ipaddress.ip_address(target))]
    except ValueError as exc:
        raise DiscoveryError(str(exc)) from exc


def _tcp_open(host: str, port: int = 22, timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _ssh_connect(host: str, username: str, password: str = "", timeout: float = 5.0) -> tuple[paramiko.SSHClient, str]:
    integrations = load_integrations()
    ssh = integrations["ssh"]
    key_info = read_key_pair(ssh["private_key_path"], ssh["public_key_path"])
    private_key = Path(key_info["private_key_path"])

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    connect_kwargs = {
        "hostname": host,
        "username": username,
        "timeout": timeout,
        "banner_timeout": timeout,
        "auth_timeout": timeout,
    }
    if password:
        connect_kwargs["password"] = password
        auth_method = "password"
    elif private_key.exists():
        connect_kwargs["key_filename"] = str(private_key)
        auth_method = "ssh-key"
    else:
        raise DiscoveryError("No SSH auth available. Provide a password or generate/install the BKC SSH key.")

    client.connect(**connect_kwargs)
    return client, auth_method


def _run_command(client: paramiko.SSHClient, command: str) -> str:
    stdin, stdout, stderr = client.exec_command(command)
    output = stdout.read().decode("utf-8", errors="replace").strip()
    error = stderr.read().decode("utf-8", errors="replace").strip()
    if error and not output:
        raise DiscoveryError(error)
    return output


def _install_public_key(client: paramiko.SSHClient, public_key: str) -> None:
    escaped = public_key.replace("'", "'\"'\"'")
    command = (
        "mkdir -p ~/.ssh && chmod 700 ~/.ssh && "
        f"grep -qxF '{escaped}' ~/.ssh/authorized_keys || "
        f"printf '%s\\n' '{escaped}' >> ~/.ssh/authorized_keys && "
        "chmod 600 ~/.ssh/authorized_keys"
    )
    _run_command(client, command)


def scan_subnet_ssh(
    subnet: str,
    username: str,
    password: str = "",
    install_key: bool = False,
    timeout: float = 0.5,
) -> dict:
    hosts_to_scan = _expand_targets(subnet)

    integrations = load_integrations()
    key_info = read_key_pair(
        integrations["ssh"]["private_key_path"],
        integrations["ssh"]["public_key_path"],
    )

    discovered = []
    open_hosts = []
    for host in hosts_to_scan:
        if _tcp_open(host, port=22, timeout=timeout):
            open_hosts.append(host)

    for host in open_hosts:
        record = {
            "host": host,
            "ip": host,
            "reachable": True,
            "auth_method": "",
            "hostname": "",
            "user": username,
            "port": 22,
            "private_key": "",
            "password": password,
            "errors": [],
        }
        try:
            client, auth_method = _ssh_connect(host, username=username, password=password)
            record["auth_method"] = auth_method
            record["private_key"] = key_info["private_key_path"] if auth_method == "ssh-key" else ""
            try:
                record["hostname"] = _run_command(client, "hostname")
            except DiscoveryError as exc:
                record["errors"].append(str(exc))

            if install_key and key_info["public_key"]:
                if auth_method == "password":
                    try:
                        _install_public_key(client, key_info["public_key"])
                        record["private_key"] = key_info["private_key_path"]
                        record["password"] = ""
                    except DiscoveryError as exc:
                        record["errors"].append(f"key install failed: {exc}")
            client.close()
        except Exception as exc:
            record["errors"].append(str(exc))

        discovered.append(record)

    return {
        "subnet": subnet,
        "scanned_hosts": len(hosts_to_scan),
        "ssh_hosts": len(open_hosts),
        "discovered": discovered,
    }


def import_discovered_nodes(
    rules: dict,
    group_name: str,
    scan_result: dict,
    provider: str = "manual-scan",
    configuration: str = "ansible",
    provisioner: str = "ssh-bootstrap",
) -> int:
    group = rules["groups"].setdefault(group_name, {"locals": {}, "nodes": {}})
    imported = 0
    for entry in scan_result.get("discovered", []):
        host_key = entry.get("hostname") or entry.get("host")
        if not host_key:
            continue
        existing = group["nodes"].get(host_key, {})
        group["nodes"][host_key] = {
            **existing,
            "ip": entry.get("ip", ""),
            "user": entry.get("user", existing.get("user", "")),
            "password": entry.get("password", existing.get("password", "")),
            "private_key": entry.get("private_key", existing.get("private_key", "")),
            "port": entry.get("port", existing.get("port", 22)),
            "provider": existing.get("provider", provider),
            "provisioner": existing.get("provisioner", provisioner),
            "configuration": existing.get("configuration", configuration),
            "state": existing.get("state", "discovered"),
            "application": existing.get("application", "bkc-managed"),
        }
        imported += 1
    return imported
