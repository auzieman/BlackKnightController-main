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
            "trigger-pipeline",
            "run-status",
            "recent-runs",
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
    parser.add_argument("--pipeline-id", help="Pipeline id to trigger with trigger-pipeline.")
    parser.add_argument("--run-id", help="Automation run id for run-status.")
    parser.add_argument("--ref", default="refs/heads/main", help="Git ref recorded on triggered pipeline runs.")
    parser.add_argument("--commit", default="", help="Git commit recorded on triggered pipeline runs.")
    parser.add_argument("--notes", default="", help="Operator notes recorded on triggered pipeline runs.")
    parser.add_argument(
        "--input",
        action="append",
        default=[],
        help="Pipeline input override as key=value. Can be repeated.",
    )
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

    if args.command == "trigger-pipeline":
        from services.automation_pipeline import create_automation_run, mark_run_blocked, mark_run_queued
        from services.job_queue import SLOW_QUEUE_NAME, enqueue_job, job_queue_enabled
        from services.pipeline_catalog import pipeline_by_id
        from services.pipeline_executor import workflow_is_supported, workflow_job_timeout, workflow_stage_definitions

        if not args.pipeline_id:
            print("trigger-pipeline requires --pipeline-id.")
            return 2
        pipeline = pipeline_by_id(args.pipeline_id)
        if not pipeline:
            print(f"Pipeline not found: {args.pipeline_id}")
            return 2
        workflow = str(pipeline.get("workflow", "")).strip()
        if not workflow_is_supported(workflow):
            print(f"Pipeline is not wired: {pipeline.get('name', args.pipeline_id)} ({workflow or 'missing workflow'})")
            return 3

        input_overrides: dict[str, object] = {}
        for item in args.input:
            if "=" not in item:
                print(f"Invalid --input value {item!r}; expected key=value.")
                return 2
            key, value = item.split("=", 1)
            key = key.strip()
            value = value.strip()
            if not key:
                print(f"Invalid --input value {item!r}; key is empty.")
                return 2
            if value.lower() in {"true", "false"}:
                input_overrides[key] = value.lower() == "true"
            else:
                input_overrides[key] = value

        extra = {
            "pipeline_id": pipeline["id"],
            "pipeline_name": pipeline.get("name", pipeline["id"]),
        }
        resource_class = str(pipeline.get("resource_class", "")).strip().lower()
        if resource_class:
            extra["resource_class"] = resource_class
        if input_overrides:
            extra["request_payload"] = {"inputs": input_overrides}

        run = create_automation_run(
            tenant_slug="default",
            requested_by="bkc-cli:operator",
            trigger_source="bkc-cli",
            repo=str(pipeline.get("repo", "")),
            workflow=workflow,
            ref=args.ref,
            commit=args.commit,
            notes=args.notes or str(pipeline.get("notes", "")),
            extra=extra,
        )

        queued = False
        job_id = ""
        if job_queue_enabled():
            queue_name = SLOW_QUEUE_NAME if resource_class == "slow" else "bkc"
            try:
                timeout = workflow_job_timeout(workflow)
                job = enqueue_job(
                    "services.job_tasks.automation_pipeline_job",
                    (run["id"], "default", None, None, "bkc-cli"),
                    job_timeout=timeout,
                    queue_name=queue_name,
                    meta={
                        "kind": "automation",
                        "run_id": run["id"],
                        "tenant_slug": "default",
                        "repo": run.get("repo", ""),
                        "workflow": workflow,
                        "job_timeout": timeout,
                        "queue_name": queue_name,
                    },
                )
                job_id = job.id
                queued = True
                run = mark_run_queued(run["id"], job.id) or run
            except Exception as exc:
                run = mark_run_blocked(run["id"], f"Queue backend unavailable: {exc}") or run

        print(f"pipeline: {pipeline.get('name', pipeline['id'])}")
        print(f"workflow: {workflow}")
        print(f"run_id: {run['id']}")
        print(f"status: {run.get('status')}")
        print(f"queued: {str(queued).lower()}")
        if job_id:
            print(f"job_id: {job_id}")
        print(f"url: /pipelines/{run['id']}")
        print("stages:")
        for stage in workflow_stage_definitions(workflow):
            print(f"- {stage.get('name')}")
        return 0

    if args.command == "run-status":
        from services.automation_runs import load_runs

        if not args.run_id:
            print("run-status requires --run-id.")
            return 2
        run = next((item for item in load_runs() if item.get("id") == args.run_id), None)
        if not run:
            print(f"Run not found: {args.run_id}")
            return 2
        print(f"run_id: {run.get('id')}")
        print(f"status: {run.get('status')}")
        print(f"workflow: {run.get('workflow')}")
        print(f"updated_at: {run.get('updated_at') or run.get('created_at')}")
        extra = run.get("extra") or {}
        if extra.get("pipeline_id"):
            print(f"pipeline_id: {extra.get('pipeline_id')}")
        if extra.get("pipeline_name"):
            print(f"pipeline_name: {extra.get('pipeline_name')}")
        if run.get("notes"):
            print(f"notes: {run.get('notes')}")
        print("stages:")
        for stage in run.get("stages", []):
            detail = stage.get("detail") or stage.get("message") or ""
            print(f"- {stage.get('name')}: {stage.get('status')} {detail}".rstrip())
        events = run.get("events", [])[-12:]
        if events:
            print("events:")
            for event in events:
                message = str(event.get("message") or "")
                if len(message) > 500:
                    message = message[:497] + "..."
                print(f"- {event.get('level')} {event.get('stage')}: {message}")
        return 0

    if args.command == "recent-runs":
        from services.automation_runs import load_runs

        runs = sorted(
            load_runs(),
            key=lambda run: run.get("updated_at") or run.get("created_at") or "",
            reverse=True,
        )
        for run in runs[:30]:
            extra = run.get("extra") or {}
            pipeline_id = extra.get("pipeline_id") or run.get("workflow") or ""
            print(
                f"{run.get('id')} {run.get('status')} {pipeline_id} "
                f"{run.get('updated_at') or run.get('created_at')}"
            )
        return 0

    print(json.dumps(rules, indent=2))
    if args.output:
        args.output.write_text(json.dumps(rules, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
