def parse_ansible_hosts(content: str) -> dict:
    groups: dict[str, list[dict]] = {}
    current_group = "ungrouped"

    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or line.startswith(";"):
            continue

        if line.startswith("[") and line.endswith("]"):
            current_group = line[1:-1].strip()
            groups.setdefault(current_group, [])
            continue

        if current_group.endswith(":vars") or current_group.endswith(":children"):
            continue

        parts = line.split()
        host = parts[0]
        vars_map = {}
        for token in parts[1:]:
            if "=" in token:
                key, value = token.split("=", 1)
                vars_map[key] = value
        groups.setdefault(current_group, []).append({"host": host, "vars": vars_map})

    return groups


def sync_ansible_inventory_to_rules(rules: dict, parsed_inventory: dict) -> dict:
    created_groups = 0
    created_nodes = 0
    updated_nodes = 0

    for group_name, hosts in parsed_inventory.items():
        if ":" in group_name:
            continue
        group_exists = group_name in rules["groups"]
        group = rules["groups"].setdefault(group_name, {"locals": {}, "nodes": {}})
        if not group_exists:
            created_groups += 1

        group["locals"].update(
            {
                "configuration": group["locals"].get("configuration", "ansible"),
                "workflow": group["locals"].get(
                    "workflow", "provision -> configure -> deploy"
                ),
            }
        )

        for entry in hosts:
            host_name = entry["host"]
            host_vars = entry.get("vars", {})
            existing = group["nodes"].get(host_name, {})
            if existing:
                updated_nodes += 1
            else:
                created_nodes += 1
            group["nodes"][host_name] = {
                **existing,
                "provider": existing.get("provider", "ansible"),
                "configuration": "ansible",
                "state": existing.get("state", "configured"),
                "ip": host_vars.get("ansible_host", existing.get("ip", "")),
                "user": host_vars.get("ansible_user", existing.get("user", "")),
                "password": host_vars.get("ansible_password", existing.get("password", "")),
                "private_key": host_vars.get(
                    "ansible_ssh_private_key_file", existing.get("private_key", "")
                ),
                "port": int(host_vars.get("ansible_port", existing.get("port", 22) or 22)),
            }

    return {
        "groups": created_groups,
        "created_nodes": created_nodes,
        "updated_nodes": updated_nodes,
    }
