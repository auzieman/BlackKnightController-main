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


def _lines(content: str, limit: int = 80) -> list[str]:
    values = [line.strip() for line in content.splitlines() if line.strip()]
    return values[:limit]


def _run_optional(client, command: str) -> str:
    stdout, _, _ = run_client_command(client, command)
    return stdout.strip()


def probe_host(group_name: str, host_name: str) -> dict:
    try:
        client, _, auth_method = connect_host(group_name, host_name)
    except RemoteAdminError as exc:
        raise InventoryProbeError(str(exc)) from exc

    try:
        hostname = _run_optional(client, "hostname")
        fqdn = _run_optional(client, "hostname -f 2>/dev/null || hostname")
        os_release = _run_optional(client, "cat /etc/os-release 2>/dev/null || true")
        ip_output = _run_optional(client, "ip -o -4 addr show scope global 2>/dev/null || hostname -I 2>/dev/null")
        gateway = _run_optional(client, "ip route show default 2>/dev/null | awk '/default/ {print $3; exit}'")
        kernel = _run_optional(client, "uname -srvmo 2>/dev/null || uname -a 2>/dev/null || true")
        uptime = _run_optional(client, "uptime -p 2>/dev/null || awk '{print int($1)}' /proc/uptime 2>/dev/null || true")
        package_manager = _run_optional(
            client,
            "sh -lc 'for tool in apt-get dnf yum apk pacman zypper auzix-pkg flatpak; do "
            "command -v \"$tool\" >/dev/null 2>&1 && { echo \"$tool\"; break; }; done'",
        )
        tools = _run_optional(
            client,
            "sh -lc 'for tool in docker podman docker-compose buildah flatpak auzix-pkg containerd kubelet kubectl facter python3 git curl wget; do "
            "command -v \"$tool\" >/dev/null 2>&1 && echo \"$tool\"; done'",
        )
        versions = _run_optional(
            client,
            "sh -lc 'for tool in docker podman flatpak auzix-pkg python3 git facter; do "
            "command -v \"$tool\" >/dev/null 2>&1 || continue; "
            "printf \"%s: \" \"$tool\"; \"$tool\" --version 2>/dev/null | head -n 1 || true; done'",
        )
        disks = _run_optional(client, "df -hT -x tmpfs -x devtmpfs 2>/dev/null | sed -n '1,25p' || true")
        mounts = _run_optional(
            client,
            "findmnt -rn -o TARGET,SOURCE,FSTYPE,OPTIONS 2>/dev/null | sed -n '1,40p' || mount 2>/dev/null | sed -n '1,40p' || true",
        )
        service_units = _run_optional(
            client,
            "systemctl list-units --type=service --state=running --no-legend --no-pager 2>/dev/null | awk '{print $1}' | sed -n '1,80p' || true",
        )
        failed_units = _run_optional(
            client,
            "systemctl --failed --no-legend --no-pager 2>/dev/null | sed -n '1,40p' || true",
        )
        repo_states = _run_optional(
            client,
            "sh -lc 'for path in /Work /workspace /srv /opt /home/*/Projects /Users/*/Projects; do "
            "test -d \"$path\" || continue; find \"$path\" -maxdepth 3 -name .git -type d 2>/dev/null; done | sed -n \"1,40p\" | "
            "while read gitdir; do repo=${gitdir%/.git}; branch=$(git -C \"$repo\" branch --show-current 2>/dev/null || true); commit=$(git -C \"$repo\" rev-parse --short HEAD 2>/dev/null || true); dirty=$(git -C \"$repo\" status --porcelain 2>/dev/null | wc -l | tr -d \" \"); printf \"%s branch=%s commit=%s dirty=%s\\n\" \"$repo\" \"$branch\" \"$commit\" \"$dirty\"; done'",
        )
        facter_status = _run_optional(
            client,
            "sh -lc 'if command -v facter >/dev/null 2>&1; then facter --version 2>/dev/null | sed \"s/^/installed /\"; else echo missing; fi'",
        )
    except Exception as exc:
        raise InventoryProbeError(str(exc)) from exc
    finally:
        client.close()

    os_values = _parse_os_release(os_release)
    observed_ips = _parse_ip_output(ip_output)
    services_detected = _lines(tools)

    return {
        "group": group_name,
        "target": host_name,
        "auth_method": auth_method,
        "hostname": hostname,
        "fqdn": fqdn,
        "ip": observed_ips[0] if observed_ips else "",
        "observed_ips": observed_ips,
        "default_gateway": gateway,
        "os_name": os_values.get("PRETTY_NAME", ""),
        "os_version": os_values.get("VERSION_ID", ""),
        "os_family": os_values.get("ID", ""),
        "kernel": kernel,
        "uptime": uptime,
        "package_manager": package_manager,
        "services_detected": services_detected,
        "program_versions": _lines(versions),
        "disk_report": _lines(disks),
        "mount_report": _lines(mounts),
        "running_services": _lines(service_units),
        "failed_services": _lines(failed_units),
        "repo_states": _lines(repo_states),
        "facter_status": facter_status,
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
    node["kernel"] = probe_result.get("kernel", "")
    node["uptime"] = probe_result.get("uptime", "")
    node["package_manager"] = probe_result.get("package_manager", "")
    node["services_detected"] = services_detected
    node["program_versions"] = probe_result.get("program_versions", [])
    node["disk_report"] = probe_result.get("disk_report", [])
    node["mount_report"] = probe_result.get("mount_report", [])
    node["running_services"] = probe_result.get("running_services", [])
    node["failed_services"] = probe_result.get("failed_services", [])
    node["repo_states"] = probe_result.get("repo_states", [])
    node["facter_status"] = probe_result.get("facter_status", "")
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
