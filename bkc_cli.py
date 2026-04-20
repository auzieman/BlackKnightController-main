import argparse
import json
from pathlib import Path

from services.fresh_build_library import fresh_build_plan
from services.rules_store import get_rules_path, load_rules, save_rules


def summarize_rules(rules: dict) -> dict:
    groups = rules.get("groups", {})
    hosts = sum(len(group.get("nodes", {})) for group in groups.values())
    return {
        "rules_file": str(get_rules_path()),
        "group_count": len(groups),
        "host_count": hosts,
        "groups": {
            name: {
                "host_count": len(group.get("nodes", {})),
                "locals": group.get("locals", {}),
            }
            for name, group in groups.items()
        },
    }


def print_summary(summary: dict) -> None:
    print(f"Rules file: {summary['rules_file']}")
    print(f"Groups: {summary['group_count']}")
    print(f"Hosts: {summary['host_count']}")
    for name, group in summary["groups"].items():
        env = group["locals"].get("env", "unset")
        datacenter = group["locals"].get("datacenter", "unset")
        print(f"- {name}: {group['host_count']} hosts, env={env}, datacenter={datacenter}")


def validate_rules(rules: dict) -> list[str]:
    issues = []
    groups = rules.get("groups", {})
    if not groups:
        issues.append("No groups defined.")

    for name, group in groups.items():
        locals_block = group.get("locals", {})
        nodes = group.get("nodes", {})
        if not locals_block:
            issues.append(f"Group '{name}' is missing locals metadata.")
        if not nodes:
            issues.append(f"Group '{name}' has no nodes.")
        for host, node in nodes.items():
            if "user" not in node:
                issues.append(f"Node '{host}' in group '{name}' is missing user.")
            if "port" not in node:
                issues.append(f"Node '{host}' in group '{name}' is missing port.")

    return issues


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect BlackKnightController inventory metadata.")
    parser.add_argument(
        "command",
        choices=[
            "summary",
            "validate",
            "dump",
            "proxmox-check",
            "proxmox-inventory",
            "proxmox-clone",
            "scan-subnet",
            "migrate-secrets",
            "fresh-build-plan",
        ],
        nargs="?",
        default="summary",
        help="Action to perform against dictionaries/rules.json",
    )
    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        help="Optional path to write the JSON output for the selected command.",
    )
    parser.add_argument("--node", help="Proxmox node name for API operations.")
    parser.add_argument("--source-vmid", type=int, help="Source VMID for Proxmox clone operations.")
    parser.add_argument("--new-vmid", type=int, help="New VMID for Proxmox clone operations.")
    parser.add_argument("--name", help="New VM name for Proxmox clone operations.")
    parser.add_argument(
        "--subnet",
        help="CIDR, single IP, or range for SSH discovery, e.g. 192.168.1.0/24, 192.168.1.10, or 192.168.1.9-15.",
    )
    parser.add_argument("--username", help="SSH username for subnet discovery.")
    parser.add_argument("--password", default="", help="SSH password for subnet discovery/bootstrap.")
    parser.add_argument("--release", default="43", help="Fedora release number for fresh build planning.")
    parser.add_argument("--arch", default="x86_64", help="Architecture for Fedora ISO planning.")
    parser.add_argument("--hostname", help="Hostname for fresh build planning, e.g. swarm4.morgans.lan.")
    parser.add_argument("--network-mode", default="dhcp", choices=["dhcp", "static"], help="Network mode for Kickstart generation.")
    parser.add_argument("--ip-address", default="", help="Static IP for Kickstart generation.")
    parser.add_argument("--gateway", default="", help="Gateway for static Kickstart generation.")
    parser.add_argument("--dns-servers", default="", help="Comma-separated DNS server list for static Kickstart generation.")
    parser.add_argument("--nameserver-host", default="ns1.morgans.lan", help="Host that will serve Kickstart files.")
    parser.add_argument(
        "--install-key",
        action="store_true",
        help="Install the BKC public key on discovered hosts after password login.",
    )
    args = parser.parse_args()

    rules = load_rules()

    if args.command == "summary":
        summary = summarize_rules(rules)
        print_summary(summary)
        if args.output:
            args.output.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
        return 0

    if args.command == "validate":
        issues = validate_rules(rules)
        if not issues:
            print("Inventory validation passed.")
            return 0

        print("Inventory validation issues:")
        for issue in issues:
            print(f"- {issue}")
        return 1

    if args.command in {"proxmox-check", "proxmox-inventory", "proxmox-clone"}:
        from services.proxmox import (
            ProxmoxAPIError,
            ProxmoxClient,
            ProxmoxConfigError,
            load_proxmox_config,
            summarize_inventory,
        )

        try:
            proxmox = ProxmoxClient(load_proxmox_config())
        except ProxmoxConfigError as exc:
            print(f"Proxmox configuration error: {exc}")
            return 2

        try:
            if args.command == "proxmox-check":
                version = proxmox.version()
                print(
                    f"Connected to Proxmox {version.get('version', 'unknown')} "
                    f"({version.get('release', 'unknown release')})."
                )
                return 0

            if args.command == "proxmox-inventory":
                inventory = summarize_inventory(proxmox)
                print(json.dumps(inventory, indent=2))
                if args.output:
                    args.output.write_text(json.dumps(inventory, indent=2) + "\n", encoding="utf-8")
                return 0

            if not args.node or args.source_vmid is None or args.new_vmid is None or not args.name:
                print("proxmox-clone requires --node, --source-vmid, --new-vmid, and --name.")
                return 2

            result = proxmox.clone_vm(
                node=args.node,
                source_vmid=args.source_vmid,
                new_vmid=args.new_vmid,
                name=args.name,
            )
            print(f"Clone task submitted: {result}")
            return 0
        except ProxmoxAPIError as exc:
            print(f"Proxmox API error: {exc}")
            return 1

    if args.command == "scan-subnet":
        from services.discovery import DiscoveryError, scan_subnet_ssh

        if not args.subnet or not args.username:
            print("scan-subnet requires --subnet and --username.")
            return 2
        try:
            result = scan_subnet_ssh(
                subnet=args.subnet,
                username=args.username,
                password=args.password,
                install_key=args.install_key,
            )
            print(json.dumps(result, indent=2))
            if args.output:
                args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
            return 0
        except DiscoveryError as exc:
            print(f"Discovery error: {exc}")
            return 1

    if args.command == "migrate-secrets":
        from services.integration_store import load_integrations, save_integrations

        integrations = load_integrations()
        save_integrations(integrations)
        save_rules(rules)
        print("Encrypted secrets rewritten to dictionaries/integrations.json and dictionaries/rules.json.")
        print("Back up keys/bkc_master_key and dictionaries/secrets_meta.json to preserve recovery.")
        return 0

    if args.command == "fresh-build-plan":
        if not args.hostname:
            print("fresh-build-plan requires --hostname.")
            return 2
        plan = fresh_build_plan(
            hostname=args.hostname,
            release=args.release,
            arch=args.arch,
            username=args.username or "deployer",
            password=args.password or "changeme",
            network_mode=args.network_mode,
            ip_address=args.ip_address,
            gateway=args.gateway,
            dns_servers=args.dns_servers,
            nameserver_host=args.nameserver_host,
        )
        print(json.dumps(plan, indent=2))
        if args.output:
            args.output.write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")
        return 0

    print(json.dumps(rules, indent=2))
    if args.output:
        args.output.write_text(json.dumps(rules, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
