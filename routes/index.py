from flask import Blueprint, render_template
from services.automation_runs import load_runs
from services.pipeline_catalog import demo_pipelines
from services.rules_store import load_rules
from services.tenant_context import get_effective_tenant_slug
from services.workflow import parse_workflow_stages

index_blueprint = Blueprint("index", __name__)


def _stage_summary(run: dict) -> dict:
    summary = {"complete": 0, "active": 0, "planned": 0, "failed": 0, "other": 0}
    for stage in run.get("stages", []):
        status = str(stage.get("status", "planned")).strip().lower()
        if status in ("queued", "running", "waiting-executor", "blocked", "active"):
            summary["active"] += 1
        elif status in summary:
            summary[status] += 1
        else:
            summary["other"] += 1
    return summary


@index_blueprint.route("/", methods=["GET"])
def index():
    rules = load_rules()
    tenant_slug = get_effective_tenant_slug()
    groups = rules["groups"]
    total_hosts = sum(len(group.get("nodes", {})) for group in groups.values())
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

    pipelines = demo_pipelines()
    links_by_url: dict[str, dict] = {}
    dashboard_cards = []
    for pipeline in pipelines:
        for link in pipeline.get("links", []):
            links_by_url.setdefault(link["url"], link)
        for dashboard in pipeline.get("dashboards", []):
            dashboard_cards.append(
                {
                    "pipeline_name": pipeline["name"],
                    "pipeline_id": pipeline["id"],
                    **dashboard,
                }
            )

    recent_runs = []
    latest_runs_by_workflow: dict[str, dict] = {}
    active_runs = 0
    failed_runs = 0
    for run in load_runs():
        if run.get("tenant_slug") != tenant_slug:
            continue
        enriched = dict(run)
        enriched["stage_summary"] = _stage_summary(run)
        recent_runs.append(enriched)
        latest_runs_by_workflow.setdefault(str(run.get("workflow", "")), enriched)
        if enriched["stage_summary"]["active"] > 0 and str(run.get("status", "")).lower() not in {"complete", "failed"}:
            active_runs += 1
        if enriched["stage_summary"]["failed"] > 0 or str(run.get("status", "")).lower() == "failed":
            failed_runs += 1

    return render_template(
        "index.html.j2",
        groups=group_cards,
        globals_meta=rules["globals"],
        total_hosts=total_hosts,
        pipelines=pipelines,
        recent_runs=recent_runs[:6],
        latest_runs_by_workflow=latest_runs_by_workflow,
        quick_links=list(links_by_url.values()),
        dashboard_cards=dashboard_cards,
        active_runs=active_runs,
        failed_runs=failed_runs,
    )
