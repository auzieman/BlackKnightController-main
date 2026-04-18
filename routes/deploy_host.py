from flask import Blueprint, abort, render_template
from services.rules_store import load_rules

deploy_host_blueprint = Blueprint("deploy_host", __name__)


@deploy_host_blueprint.route("/deploy/<group>/<host>", methods=["GET"])
def deploy_host(group, host):
    rules = load_rules()
    group_data = rules["groups"].get(group)
    if group_data is None:
        abort(404)

    host_data = group_data.get("nodes", {}).get(host)
    if host_data is None:
        abort(404)

    return render_template(
        "deploy_hosts.html.j2",
        group=group,
        host=host,
        host_data=host_data,
        workflow=group_data.get("locals", {}).get(
            "workflow", "build -> provision -> configure -> deploy"
        ),
    )
