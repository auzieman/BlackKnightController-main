from flask import Blueprint, flash, redirect, render_template, request, url_for

from services.execution_assets_store import load_execution_assets
from services.inventory_model import resolve_group_hosts
from services.rules_store import load_rules
from services.workflow import build_stage_columns
from services.work_items_store import create_work_item, load_work_items, save_work_items

workflow_blueprint = Blueprint("workflow_view", __name__)


def _workflow_stages() -> list[str]:
    return ["gathered", "accepted", "build", "configure", "validate", "done"]


def _inventory_lookup(rules: dict) -> dict:
    lookup = {}
    for group_name in sorted(rules.get("groups", {}).keys()):
        for host_name, node_data, resolved in resolve_group_hosts(rules, group_name):
            lookup[(group_name, host_name)] = {
                "group": group_name,
                "host": host_name,
                "resolved": resolved,
            }
    return lookup


@workflow_blueprint.route("/workflow", methods=["GET", "POST"])
def workflow():
    rules = load_rules()
    execution_assets = load_execution_assets()
    work_items = load_work_items()
    workflow_stages = _workflow_stages()
    execution_asset_ids = {asset["id"] for asset in execution_assets}
    inventory_lookup = _inventory_lookup(rules)

    if request.method == "POST":
        title = request.form.get("title", "").strip()
        group_name = request.form.get("group", "").strip()
        target_host = request.form.get("target_host", "").strip()
        source_group = request.form.get("source_group", "").strip()
        source_host = request.form.get("source_host", "").strip()
        source_ref = request.form.get("source_ref", "").strip()
        execution_asset_id = request.form.get("execution_asset_id", "").strip()
        firstboot_asset_id = request.form.get("firstboot_asset_id", "").strip()
        stage = request.form.get("stage", "gathered").strip() or "gathered"
        network_mode = request.form.get("network_mode", "existing-lan-dhcp").strip() or "existing-lan-dhcp"
        requested_ip = request.form.get("requested_ip", "").strip()
        gateway = request.form.get("gateway", "").strip()
        dns_servers = request.form.get("dns_servers", "").strip()
        validation_profile = request.form.get("validation_profile", "").strip()
        notes = request.form.get("notes", "").strip()

        if source_ref and "|" in source_ref:
            source_group, source_host = source_ref.split("|", 1)

        if not title or not group_name or not target_host:
            flash("Workflow item requires a title, target group, and target host.")
            return redirect(url_for("workflow_view.workflow"))
        if group_name not in rules.get("groups", {}):
            flash("Workflow item target group is invalid.")
            return redirect(url_for("workflow_view.workflow"))
        if stage not in workflow_stages:
            flash("Workflow item stage is invalid.")
            return redirect(url_for("workflow_view.workflow"))
        if execution_asset_id and execution_asset_id not in execution_asset_ids:
            flash("Execution asset is invalid.")
            return redirect(url_for("workflow_view.workflow"))
        if firstboot_asset_id and firstboot_asset_id not in execution_asset_ids:
            flash("Firstboot asset is invalid.")
            return redirect(url_for("workflow_view.workflow"))
        if source_group or source_host:
            if (source_group, source_host) not in inventory_lookup:
                flash("Workflow source resource is invalid.")
                return redirect(url_for("workflow_view.workflow"))

        work_items.append(
            create_work_item(
                title=title,
                stage=stage,
                group_name=group_name,
                target_host=target_host,
                execution_asset_id=execution_asset_id,
                source_group=source_group,
                source_host=source_host,
                network_mode=network_mode,
                requested_ip=requested_ip,
                gateway=gateway,
                dns_servers=dns_servers,
                firstboot_asset_id=firstboot_asset_id,
                validation_profile=validation_profile,
                notes=notes,
            )
        )
        save_work_items(work_items)
        flash(f"Created workflow item for {target_host} in stage {stage}.")
        return redirect(url_for("workflow_view.workflow"))

    workflow_groups = []
    inventory_choices = []

    for group_name, group_data in sorted(rules.get("groups", {}).items()):
        workflow = group_data.get("locals", {}).get("workflow", "gathered -> accepted -> build -> configure -> validate -> done")
        hosts = sorted(group_data.get("nodes", {}).items())
        for host_name, _ in hosts:
            inventory_choices.append(
                {
                    "value": f"{group_name}|{host_name}",
                    "label": f"{group_name} / {host_name}",
                }
            )
        workflow_groups.append(
            {
                "name": group_name,
                "host_count": len(hosts),
                "workflow": workflow,
                "columns": build_stage_columns(hosts, workflow),
            }
        )

    work_item_columns = [{"name": stage, "items": []} for stage in workflow_stages]
    work_item_columns.append({"name": "unassigned", "items": []})
    assets_by_id = {asset["id"]: asset for asset in execution_assets}
    for item in work_items:
        target_stage = next(
            (column for column in work_item_columns if column["name"].lower() == item.get("stage", "").lower()),
            work_item_columns[-1],
        )
        enriched = dict(item)
        enriched["asset"] = assets_by_id.get(item.get("execution_asset_id", ""), {})
        enriched["firstboot_asset"] = assets_by_id.get(item.get("firstboot_asset_id", ""), {})
        source_ref = inventory_lookup.get((item.get("source_group", ""), item.get("source_host", "")), {})
        enriched["source_resolved"] = source_ref.get("resolved", {})
        target_stage["items"].append(enriched)

    return render_template(
        "workflow.html.j2",
        workflow_groups=workflow_groups,
        work_item_columns=work_item_columns,
        execution_assets=execution_assets,
        inventory_choices=inventory_choices,
        rules=rules,
        workflow_stages=workflow_stages,
    )
