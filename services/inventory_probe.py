from services.inventory_model import reconcile_rules_inventory, short_hostname
from services.remote_admin import RemoteAdminError, connect_host, run_client_command


class InventoryProbeError(RuntimeError):
    pass


def _parse_os_release(content: str) -> dict:
    values = {}
    for line in content.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"')
    return values


def _parse_ip_output(content: str) -> list[str]:
    addresses = []
    for line in content.splitlines():
        if "inet" not in line:
            for token in line.split():
                if token.count(".") == 3 and token not in addresses:
                    addresses.append(token)
            continue
        parts = line.split()
        index = parts.index("inet")
        if index + 1 >= len(parts):
            continue
        address = parts[index + 1].split("/", 1)[0]
        if address and address not in addresses:
            addresses.append(address)
    return addresses


def probe_host(group_name: str, host_name: str) -> dict:
    try:
        client, _, auth_method = connect_host(group_name, host_name)
    except RemoteAdminError as exc:
        raise InventoryProbeError(str(exc)) from exc

    try:
        hostname, _, _ = run_client_command(client, "hostname")
        fqdn, _, _ = run_client_command(client, "hostname -f 2>/dev/null || hostname")
        os_release, _, _ = run_client_command(client, "cat /etc/os-release 2>/dev/null || true")
        ip_output, _, _ = run_client_command(
            client,
            "ip -o -4 addr show scope global 2>/dev/null || hostname -I 2>/dev/null",
        )
        gateway, _, _ = run_client_command(
            client,
            "ip route show default 2>/dev/null | awk '/default/ {print $3; exit}'",
        )
        package_manager, _, _ = run_client_command(
            client,
            "sh -lc 'for tool in apt-get dnf yum apk pacman zypper; do "
            "command -v \"$tool\" >/dev/null 2>&1 && { echo \"$tool\"; break; }; done'",
        )
        services, _, _ = run_client_command(
            client,
            "sh -lc 'for tool in docker docker-compose containerd kubelet kubectl facter; do "
            "command -v \"$tool\" >/dev/null 2>&1 && echo \"$tool\"; done'",
        )
    except Exception as exc:
        raise InventoryProbeError(str(exc)) from exc
    finally:
        client.close()

    os_values = _parse_os_release(os_release)
    observed_ips = _parse_ip_output(ip_output)
    services_detected = [line.strip() for line in services.splitlines() if line.strip()]

    return {
        "group": group_name,
        "target": host_name,
        "auth_method": auth_method,
        "hostname": hostname.strip(),
        "fqdn": fqdn.strip(),
        "ip": observed_ips[0] if observed_ips else "",
        "observed_ips": observed_ips,
        "default_gateway": gateway.strip(),
        "os_name": os_values.get("PRETTY_NAME", ""),
        "os_version": os_values.get("VERSION_ID", ""),
        "os_family": os_values.get("ID", ""),
        "package_manager": package_manager.strip(),
        "services_detected": services_detected,
    }


def apply_probe_to_rules(rules: dict, probe_result: dict) -> None:
    group_name = probe_result["group"]
    host_name = probe_result["target"]
    node = rules["groups"].get(group_name, {}).get("nodes", {}).get(host_name)
    if not node:
        raise InventoryProbeError(f"Unknown host {host_name} in group {group_name}.")

    aliases = list(node.get("aliases", []))
    for candidate in [probe_result.get("hostname", ""), probe_result.get("fqdn", "")]:
        normalized = candidate.strip().lower()
        if normalized and normalized not in aliases:
            aliases.append(normalized)
        shortened = short_hostname(candidate)
        if shortened and shortened not in aliases:
            aliases.append(shortened)

    sources = list(node.get("provider_sources", []))
    if "ssh-probe" not in sources:
        sources.append("ssh-probe")

    services_detected = list(node.get("services_detected", []))
    for service in probe_result.get("services_detected", []):
        if service not in services_detected:
            services_detected.append(service)

    observed_ips = list(node.get("observed_ips", []))
    for ip_value in probe_result.get("observed_ips", []):
        if ip_value not in observed_ips:
            observed_ips.append(ip_value)

    node["hostname"] = node.get("hostname", "") or probe_result.get("hostname", "")
    node["fqdn"] = node.get("fqdn", "") or probe_result.get("fqdn", "")
    node["ip"] = node.get("ip", "") or probe_result.get("ip", "")
    node["observed_ips"] = observed_ips
    node["default_gateway"] = node.get("default_gateway", "") or probe_result.get("default_gateway", "")
    node["os_name"] = probe_result.get("os_name", "")
    node["os_version"] = probe_result.get("os_version", "")
    node["os_family"] = probe_result.get("os_family", "")
    node["package_manager"] = probe_result.get("package_manager", "")
    node["services_detected"] = services_detected
    node["provider_sources"] = sources
    node["aliases"] = aliases
    node["identity"] = node.get("identity", "") or short_hostname(probe_result.get("fqdn") or probe_result.get("hostname"))


def probe_hosts(rules: dict, targets: list[tuple[str, str]]) -> list[dict]:
    results = []
    for group_name, host_name in targets:
        try:
            result = probe_host(group_name, host_name)
            apply_probe_to_rules(rules, result)
            result["status"] = "ok"
        except InventoryProbeError as exc:
            result = {
                "group": group_name,
                "target": host_name,
                "status": "failed",
                "error": str(exc),
            }
        results.append(result)

    reconcile_rules_inventory(rules)
    return results
