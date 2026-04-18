from flask import Blueprint, render_template
from services.rules_store import load_rules
from services.workflow import build_stage_columns

workflow_blueprint = Blueprint("workflow_view", __name__)


@workflow_blueprint.route("/workflow", methods=["GET"])
def workflow():
    rules = load_rules()
    workflow_groups = []

    for group_name, group_data in sorted(rules.get("groups", {}).items()):
        workflow = group_data.get("locals", {}).get("workflow", "gathered -> accepted -> build -> configure -> validate -> done")
        hosts = sorted(group_data.get("nodes", {}).items())
        workflow_groups.append(
            {
                "name": group_name,
                "host_count": len(hosts),
                "workflow": workflow,
                "columns": build_stage_columns(hosts, workflow),
            }
        )

    return render_template("workflow.html.j2", workflow_groups=workflow_groups)
