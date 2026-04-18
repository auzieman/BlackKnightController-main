import ipaddress
from copy import deepcopy

DEFAULT_NODE_FIELDS = {
    "user": "",
    "password": "",
    "private_key": "",
    "port": 22,
    "provider": "",
    "provisioner": "",
    "configuration": "",
    "state": "",
    "application": "",
    "ip": "",
    "vmid": "",
    "hostname": "",
    "fqdn": "",
    "os_name": "",
    "os_version": "",
    "os_family": "",
    "package_manager": "",
    "default_gateway": "",
    "identity": "",
    "aliases": [],
    "provider_sources": [],
    "services_detected": [],
    "observed_ips": [],
}


def merge_host_config(globals_data: dict, group_data: dict, node_data: dict) -> dict:
    resolved = deepcopy(DEFAULT_NODE_FIELDS)
    resolved.update(globals_data or {})
    resolved.update(group_data.get("locals", {}) if group_data else {})
    resolved.update(node_data or {})
    return resolved


def resolve_group_hosts(rules: dict, group_name: str) -> list[tuple[str, dict, dict]]:
    group_data = rules["groups"].get(group_name, {})
    globals_data = rules.get("globals", {})
    results = []
    for host_name, node_data in sorted(group_data.get("nodes", {}).items()):
        results.append((host_name, node_data, merge_host_config(globals_data, group_data, node_data)))
    return results


def _looks_like_ip(value: str) -> bool:
    if not value:
        return False
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        return False


def normalize_host_token(value: str) -> str:
    token = (value or "").strip().lower()
    if not token:
        return ""
    return token


def short_hostname(value: str) -> str:
    token = normalize_host_token(value)
    if not token or _looks_like_ip(token):
        return token
    return token.split(".", 1)[0]


def node_identifiers(host_name: str, node_data: dict) -> set[str]:
    identifiers: set[str] = set()
    for candidate in [
        host_name,
        node_data.get("hostname", ""),
        node_data.get("fqdn", ""),
        node_data.get("ip", ""),
        node_data.get("proxmox_name", ""),
        *node_data.get("aliases", []),
        *node_data.get("observed_ips", []),
    ]:
        normalized = normalize_host_token(candidate)
        if not normalized:
            continue
        identifiers.add(normalized)
        shortened = short_hostname(normalized)
        if shortened:
            identifiers.add(shortened)
    vmid = str(node_data.get("vmid", "")).strip()
    if vmid:
        identifiers.add(f"vmid:{vmid}")
    return identifiers


def _node_strength(host_name: str, node_data: dict) -> int:
    score = 0
    if node_data.get("vmid"):
        score += 8
    if node_data.get("ip"):
        score += 6
    if node_data.get("private_key"):
        score += 5
    if node_data.get("password"):
        score += 3
    if node_data.get("hostname") or node_data.get("fqdn"):
        score += 2
    if node_data.get("os_name"):
        score += 2
    if node_data.get("services_detected"):
        score += 1
    if not _looks_like_ip(host_name):
        score += 1
    return score


def _cluster_records(records: list[tuple[str, str, dict]]) -> list[list[tuple[str, str, dict]]]:
    clusters: list[list[tuple[str, str, dict]]] = []
    for record in records:
        record_ids = node_identifiers(record[1], record[2])
        attached = []
        for index, cluster in enumerate(clusters):
            cluster_ids = set()
            for cluster_record in cluster:
                cluster_ids.update(node_identifiers(cluster_record[1], cluster_record[2]))
            if record_ids & cluster_ids:
                attached.append(index)

        if not attached:
            clusters.append([record])
            continue

        primary = attached[0]
        clusters[primary].append(record)
        for index in reversed(attached[1:]):
            clusters[primary].extend(clusters[index])
            del clusters[index]
    return clusters


def _merge_cluster(cluster: list[tuple[str, str, dict]]) -> dict:
    merged = deepcopy(DEFAULT_NODE_FIELDS)
    sorted_cluster = sorted(cluster, key=lambda item: _node_strength(item[1], item[2]), reverse=True)

    for _, _, node_data in sorted_cluster:
        for field, value in node_data.items():
            if field in {"aliases", "provider_sources", "services_detected", "observed_ips"}:
                continue
            if merged.get(field) in ("", None, [], {}) and value not in ("", None, [], {}):
                merged[field] = deepcopy(value)

    aliases: list[str] = []
    provider_sources: list[str] = []
    services_detected: list[str] = []
    observed_ips: list[str] = []
    for _, host_name, node_data in sorted_cluster:
        for candidate in [
            host_name,
            node_data.get("hostname", ""),
            node_data.get("fqdn", ""),
            node_data.get("proxmox_name", ""),
            *node_data.get("aliases", []),
        ]:
            normalized = normalize_host_token(candidate)
            if normalized and normalized not in aliases:
                aliases.append(normalized)
        for source in [node_data.get("provider", ""), *node_data.get("provider_sources", [])]:
            if source and source not in provider_sources:
                provider_sources.append(source)
        for service in node_data.get("services_detected", []):
            if service and service not in services_detected:
                services_detected.append(service)
        for ip_value in [node_data.get("ip", ""), *node_data.get("observed_ips", [])]:
            if ip_value and ip_value not in observed_ips:
                observed_ips.append(ip_value)

    merged["aliases"] = aliases
    merged["provider_sources"] = provider_sources
    merged["services_detected"] = services_detected
    merged["observed_ips"] = observed_ips
    merged["identity"] = next((alias for alias in aliases if not _looks_like_ip(alias)), aliases[0] if aliases else "")
    if not merged.get("fqdn"):
        merged["fqdn"] = next((alias for alias in aliases if "." in alias and not _looks_like_ip(alias)), "")
    if not merged.get("hostname") and merged["identity"]:
        merged["hostname"] = merged["identity"]
    return merged


def apply_cluster_data(node_data: dict, merged: dict) -> dict:
    updated = deepcopy(node_data)
    for field, value in merged.items():
        if field in {"provider", "provisioner", "state", "configuration", "application"}:
            continue
        if field in {"aliases", "provider_sources", "services_detected", "observed_ips"}:
            existing = list(updated.get(field, []))
            for item in value:
                if item and item not in existing:
                    existing.append(item)
            updated[field] = existing
            continue
        if updated.get(field) in ("", None, [], {}) and value not in ("", None, [], {}):
            updated[field] = deepcopy(value)
    return updated


def reconcile_rules_inventory(rules: dict) -> dict:
    records = []
    for group_name, group_data in rules.get("groups", {}).items():
        for host_name, node_data in group_data.get("nodes", {}).items():
            records.append((group_name, host_name, node_data))

    clusters = _cluster_records(records)
    reconciled_nodes = 0
    linked_clusters = 0

    for cluster in clusters:
        if len(cluster) < 2:
            continue
        merged = _merge_cluster(cluster)
        linked_clusters += 1
        for group_name, host_name, node_data in cluster:
            rules["groups"][group_name]["nodes"][host_name] = apply_cluster_data(node_data, merged)
            reconciled_nodes += 1

    return {
        "clusters": linked_clusters,
        "nodes": reconciled_nodes,
    }
