import json
from urllib.parse import quote

from flask import Blueprint, current_app, jsonify, render_template, request
from flask_login import current_user
from services import bkc_db
from services.access_control import Perm, require_perm
from services.automation_pipeline import create_automation_run, mark_run_blocked, mark_run_queued
from services.job_queue import enqueue_job, job_queue_enabled
from services.pipeline_catalog import pipeline_by_id
from services.pipeline_executor import workflow_is_supported, workflow_job_timeout
from services.resource_graph import (
    RESOURCE_KIND_META,
    apply_cytoscape_positions,
    build_resource_graph,
    cytoscape_elements_from_resource_graph,
    related_to,
)
from services.tenant_context import get_current_tenant_id, get_effective_tenant_slug

resource_graph_blueprint = Blueprint("resource_graph", __name__)


@resource_graph_blueprint.route("/resources", methods=["GET"])
def resource_graph():
    graph = build_resource_graph()
    tenant_id = get_current_tenant_id()
    cytoscape_elements = cytoscape_elements_from_resource_graph(graph)
    if tenant_id is not None:
        cytoscape_elements = apply_cytoscape_positions(
            cytoscape_elements,
            bkc_db.load_graph_positions(int(tenant_id)),
        )
    tab = request.args.get("tab", "summary").strip().lower()
    if tab not in {"summary", "relationships", "actions", "inventory"}:
        tab = "summary"

    selected_id = request.args.get("resource", "").strip()
    selected_kind = request.args.get("kind", "").strip().lower()
    search = request.args.get("q", "").strip().lower()

    visible_tree = []
    for group in graph["tree"]:
        resources = group["resources"]
        if selected_kind and group["kind"] != selected_kind:
            resources = []
        if search:
            resources = [
                resource
                for resource in resources
                if search in resource["name"].lower()
                or search in resource["kind"].lower()
                or search in resource.get("summary", "").lower()
                or any(search in str(value).lower() for value in resource.get("facts", {}).values())
            ]
        if resources:
            visible_tree.append({**group, "resources": resources})

    visible_resources = [resource for group in visible_tree for resource in group["resources"]]
    if selected_id not in graph["resources_by_id"] and visible_resources:
        selected_id = visible_resources[0]["id"]
    selected = graph["resources_by_id"].get(selected_id)

    return render_template(
        "resource_graph.html.j2",
        graph=graph,
        kind_meta=RESOURCE_KIND_META,
        visible_tree=visible_tree,
        visible_resources=visible_resources,
        selected=selected,
        selected_id=selected_id,
        selected_kind=selected_kind,
        tab=tab,
        search=search,
        relationships=related_to(graph, selected_id) if selected else [],
        cytoscape_elements_json=json.dumps(cytoscape_elements, sort_keys=True),
    )


def _positions_from_payload(payload: dict) -> list[dict]:
    if not isinstance(payload, dict):
        return []
    raw_positions = payload.get("positions")
    if raw_positions is None and payload.get("id"):
        raw_positions = [payload]
    if not isinstance(raw_positions, list):
        return []
    positions = []
    for item in raw_positions:
        if not isinstance(item, dict):
            continue
        node_id = str(item.get("id") or item.get("node_id") or "").strip()
        if not node_id:
            continue
        try:
            x = float(item["x"])
            y = float(item["y"])
        except (KeyError, TypeError, ValueError):
            continue
        positions.append({"id": node_id, "x": x, "y": y})
    return positions


@resource_graph_blueprint.post("/resources/graph/save-positions")
@require_perm(Perm.INVENTORY_WRITE)
def save_resource_graph_positions():
    tenant_id = get_current_tenant_id()
    if tenant_id is None:
        return jsonify({"error": "tenant_required"}), 403
    payload = request.get_json(silent=True) or {}
    positions = _positions_from_payload(payload)
    if not positions:
        return jsonify({"error": "positions_required"}), 400
    try:
        saved = bkc_db.save_graph_positions(int(tenant_id), positions)
    except Exception:
        current_app.logger.exception(
            "Failed to save Cytoscape graph positions for tenant %s",
            get_effective_tenant_slug(),
        )
        return jsonify({"error": "position_save_failed"}), 500
    return jsonify({"status": "ok", "saved": saved, "tenant_slug": get_effective_tenant_slug()})


def _pipeline_from_node_id(node_id: str):
    pipeline_id = node_id.removeprefix("pipeline:").strip()
    return pipeline_by_id(pipeline_id) or pipeline_by_id(node_id)


def _queue_graph_pipeline_run(pipeline: dict, node_id: str):
    tenant_id = get_current_tenant_id()
    tenant_slug = get_effective_tenant_slug()
    run = create_automation_run(
        tenant_slug=tenant_slug,
        requested_by=f"user:{getattr(current_user, 'id', 'unknown')}",
        trigger_source="resource-graph",
        repo=pipeline["repo"],
        workflow=pipeline["workflow"],
        ref="refs/heads/main",
        commit="",
        notes=f"Triggered from resource graph node {node_id}.",
        extra={
            "pipeline_id": pipeline["id"],
            "pipeline_name": pipeline["name"],
            "resource_id": node_id,
        },
    )
    if not job_queue_enabled():
        return run, False
    try:
        timeout = workflow_job_timeout(str(pipeline.get("workflow", "")), action_mode="deploy")
        job = enqueue_job(
            "services.job_tasks.automation_pipeline_job",
            (
                run["id"],
                tenant_slug,
                tenant_id,
                int(current_user.id),
                request.remote_addr,
            ),
            job_timeout=timeout,
            meta={
                "kind": "automation",
                "run_id": run["id"],
                "tenant_slug": tenant_slug,
                "repo": run.get("repo", ""),
                "workflow": run["workflow"],
                "job_timeout": timeout,
            },
        )
        return mark_run_queued(run["id"], job.id) or run, True
    except Exception as exc:
        detail = f"Queue backend unavailable: {exc}"
        return mark_run_blocked(run["id"], detail) or run, False


@resource_graph_blueprint.post("/resources/graph/context-action")
@require_perm(Perm.INVENTORY_WRITE)
def resource_graph_context_action():
    payload = request.get_json(silent=True) or {}
    action = str(payload.get("action") or "").strip().lower()
    node_id = str(payload.get("node_id") or payload.get("id") or "").strip()
    node_type = str(payload.get("node_type") or "").strip().lower()
    if not action or not node_id:
        return jsonify({"error": "action_and_node_required"}), 400

    if node_type == "host":
        if action == "view-metrics":
            return jsonify({"status": "ok", "redirect_url": f"/resources?resource={quote(node_id)}&tab=inventory"})
        if action in {"fast-ssh-check", "deploy-ansible-playbook"}:
            return (
                jsonify(
                    {
                        "error": "context_action_not_wired",
                        "action": action,
                        "node_id": node_id,
                        "message": "Host graph actions are defined in the UI but do not have a runnable workflow yet.",
                    }
                ),
                409,
            )

    if node_type == "pipeline":
        pipeline = _pipeline_from_node_id(node_id)
        if action == "view-stage-history":
            if not pipeline:
                return jsonify({"error": "pipeline_not_found", "node_id": node_id}), 404
            return jsonify({"status": "ok", "redirect_url": f"/pipelines?pipeline={pipeline['id']}#pipeline-runs"})
        if action == "trigger-run":
            if not pipeline:
                return jsonify({"error": "pipeline_not_found", "node_id": node_id}), 404
            if not workflow_is_supported(str(pipeline.get("workflow", ""))):
                return jsonify({"error": "workflow_not_supported", "workflow": pipeline.get("workflow", "")}), 400
            run, queued = _queue_graph_pipeline_run(pipeline, node_id)
            bkc_db.append_audit(
                int(current_user.id),
                get_current_tenant_id(),
                "resource_graph.context_action",
                node_id,
                {"action": action, "run_id": run["id"], "queued": queued},
                request.remote_addr,
            )
            return jsonify(
                {
                    "status": run["status"],
                    "queued": queued,
                    "run_id": run["id"],
                    "redirect_url": f"/pipelines/{run['id']}",
                }
            )

    return jsonify({"error": "unsupported_context_action", "action": action, "node_type": node_type}), 400
