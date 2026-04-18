from flask import Blueprint, render_template
from services.rules_store import load_rules
from services.workflow import parse_workflow_stages

index_blueprint = Blueprint("index", __name__)


@index_blueprint.route("/", methods=["GET"])
def index():
    rules = load_rules()
    groups = rules["groups"]
    group_cards = [
        {
            "name": name,
            "host_count": len(group.get("nodes", {})),
            "workflow": group.get("locals", {}).get(
                "workflow", "build -> provision -> configure -> deploy"
            ),
            "stages": parse_workflow_stages(
                group.get("locals", {}).get("workflow", "build -> provision -> configure -> deploy")
            ),
            "env": group.get("locals", {}).get("env", rules["globals"].get("env", "unset")),
            "datacenter": group.get("locals", {}).get(
                "datacenter", rules["globals"].get("datacenter", "unset")
            ),
        }
        for name, group in sorted(groups.items())
    ]
    return render_template(
        "index.html.j2",
        groups=group_cards,
        globals_meta=rules["globals"],
    )
