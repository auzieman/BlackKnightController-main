from __future__ import annotations

import os
from pathlib import Path

from flask import Blueprint, abort, current_app, g, jsonify, request
from flask_limiter.util import get_remote_address
from services import bkc_db
from services.api_key_scopes import ENDPOINT_REQUIRED_SCOPE, parse_scopes, scope_allowed
from services.automation_pipeline import create_automation_run, mark_run_blocked, mark_run_queued
from services.automation_runs import get_run, load_runs
from services.health_checks import readiness_report
from services.job_queue import enqueue_job, job_queue_enabled
from services.pipeline_executor import workflow_is_supported, workflow_job_timeout, workflow_supports_undeploy
from services.rate_limit import limiter
from services.resource_graph import apply_cytoscape_positions, build_resource_graph, cytoscape_elements_from_resource_graph
from services.rules_store import load_rules
from services.tenant_context import set_request_tenant

api_blueprint = Blueprint("api_v1", __name__, url_prefix="/api/v1")

# Register @api_blueprint.route / .get before @limiter so Flask's URL rule points at the
# limiter-wrapped callable (same pattern as routes/auth.py). Reversing the stack leaves a raw view with no limits.


def install_api_v1_early_middleware(app) -> None:
    """
    Run Bearer auth + scope checks before Flask-Limiter so per-key rate limits can use g.bkc_api_key_row.
    """

    @app.before_request
    def _api_v1_auth_early():
        ep = request.endpoint
        if ep in (None, "api_v1.health_check", "api_v1.ready_check"):
            return None
        if not ep or not ep.startswith("api_v1."):
            return None
        bkc_db.init_db()
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            return jsonify({"error": "missing_or_invalid_authorization"}), 401
        raw = auth[7:].strip()
        row = bkc_db.verify_api_key(raw)
        if not row:
            return jsonify({"error": "invalid_api_key"}), 401
        tenant = bkc_db.fetch_tenant_by_id(int(row["tenant_id"]))
        if not tenant:
            return jsonify({"error": "invalid_api_key"}), 401
        set_request_tenant(int(tenant["id"]), tenant["slug"])
        g.bkc_api_key_row = dict(row)

        required = ENDPOINT_REQUIRED_SCOPE.get(ep)
        if required:
            scopes = parse_scopes(row.get("scopes"))
            if not scope_allowed(scopes, required):
                return jsonify({"error": "insufficient_scope", "required": required}), 403
        return None


def _api_bearer_rate_key() -> str:
    row = getattr(g, "bkc_api_key_row", None)
    if row and row.get("id") is not None:
        return f"bkc_apikey:{int(row['id'])}"
    return get_remote_address()


def _api_bearer_limit() -> str:
    row = getattr(g, "bkc_api_key_row", None)
    if row:
        rpm = row.get("rate_limit_per_minute")
        if rpm is not None:
            try:
                n = int(rpm)
                if n > 0:
                    return f"{n} per minute"
            except (TypeError, ValueError):
                pass
    return os.environ.get("BKC_API_KEY_RATE_LIMIT", "120 per minute").strip() or "120 per minute"


def _rx_demo_undeploy_workflow(workflow: str) -> str | None:
    return {
        "rx-demo-k3s-deploy": "rx-demo-k3s-undeploy",
        "rx-demo-k3s-redeploy-from-git": "rx-demo-k3s-undeploy",
        "rx-demo-redeploy-from-git-event": "rx-demo-k3s-undeploy",
    }.get(workflow.strip())


def _queue_api_automation_run(run: dict, workflow: str, action_mode: str, api_row: dict) -> tuple[dict, bool, str]:
    queued = False
    job_id = ""
    tenant_slug = str(api_row.get("tenant_slug") or "default")
    if job_queue_enabled():
        try:
            timeout = workflow_job_timeout(workflow, action_mode=action_mode)
            job = enqueue_job(
                "services.job_tasks.automation_pipeline_job",
                (
                    run["id"],
                    tenant_slug,
                    api_row.get("tenant_id"),
                    api_row.get("created_by"),
                    request.remote_addr,
                ),
                job_timeout=timeout,
                meta={
                    "kind": "automation",
                    "run_id": run["id"],
                    "tenant_slug": tenant_slug,
                    "repo": run.get("repo", ""),
                    "workflow": workflow,
                    "job_timeout": timeout,
                },
            )
            queued = True
            job_id = job.id
            run = mark_run_queued(run["id"], job.id) or run
        except Exception as exc:
            run = mark_run_blocked(run["id"], f"Queue backend unavailable: {exc}") or run
    return run, queued, job_id


@api_blueprint.get("/health")
@limiter.exempt
def health_check():
    return jsonify({"status": "ok", "service": "bkc-ce"})


@api_blueprint.get("/ready")
@limiter.exempt
def ready_check():
    ok, body = readiness_report(Path(current_app.root_path))
    return jsonify(body), (200 if ok else 503)


@api_blueprint.get("/me")
@limiter.limit(_api_bearer_limit, key_func=_api_bearer_rate_key)
def me():
    row = g.get("bkc_api_key_row")
    if not row:
        abort(401)
    return jsonify(
        {
            "key_name": row["name"],
            "tenant_slug": row["tenant_slug"],
            "prefix": row["prefix"],
            "scopes": row.get("scopes") or "read:me,read:inventory",
        }
    )


@api_blueprint.get("/inventory")
@limiter.limit(_api_bearer_limit, key_func=_api_bearer_rate_key)
def inventory():
    return jsonify(load_rules())


@api_blueprint.get("/tenant/<tenant_slug>/graph")
@limiter.limit(_api_bearer_limit, key_func=_api_bearer_rate_key)
def tenant_graph(tenant_slug: str):
    requested_slug = str(tenant_slug or "").strip().lower()
    api_row = g.get("bkc_api_key_row") or {}
    effective_slug = str(api_row.get("tenant_slug") or "default").strip().lower()
    if not requested_slug:
        return jsonify({"error": "tenant_slug_required"}), 400
    if requested_slug != effective_slug:
        return jsonify({"error": "tenant_mismatch", "tenant_slug": requested_slug}), 403
    try:
        elements = cytoscape_elements_from_resource_graph(build_resource_graph())
        tenant_id = api_row.get("tenant_id")
        if tenant_id is not None:
            elements = apply_cytoscape_positions(elements, bkc_db.load_graph_positions(int(tenant_id)))
    except Exception:
        current_app.logger.exception("Failed to build Cytoscape graph for tenant %s", requested_slug)
        return jsonify({"error": "graph_build_failed"}), 500
    return jsonify(elements)


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


@api_blueprint.post("/tenant/graph/save-positions", defaults={"tenant_slug": ""})
@api_blueprint.post("/tenant/<tenant_slug>/graph/save-positions")
@limiter.limit(_api_bearer_limit, key_func=_api_bearer_rate_key)
def tenant_graph_save_positions(tenant_slug: str):
    api_row = g.get("bkc_api_key_row") or {}
    effective_slug = str(api_row.get("tenant_slug") or "default").strip().lower()
    requested_slug = str(tenant_slug or effective_slug).strip().lower()
    if requested_slug != effective_slug:
        return jsonify({"error": "tenant_mismatch", "tenant_slug": requested_slug}), 403
    tenant_id = api_row.get("tenant_id")
    if tenant_id is None:
        return jsonify({"error": "tenant_required"}), 403
    positions = _positions_from_payload(request.get_json(silent=True) or {})
    if not positions:
        return jsonify({"error": "positions_required"}), 400
    try:
        saved = bkc_db.save_graph_positions(int(tenant_id), positions)
    except Exception:
        current_app.logger.exception("Failed to save Cytoscape graph positions for tenant %s", effective_slug)
        return jsonify({"error": "position_save_failed"}), 500
    return jsonify({"status": "ok", "saved": saved, "tenant_slug": effective_slug})


@api_blueprint.get("/automation/runs")
@limiter.limit(_api_bearer_limit, key_func=_api_bearer_rate_key)
def automation_runs():
    runs = load_runs()
    tenant_slug = g.get("bkc_api_key_row", {}).get("tenant_slug", "default")
    visible = [run for run in runs if run.get("tenant_slug") == tenant_slug]
    return jsonify({"runs": visible})


@api_blueprint.get("/automation/runs/<run_id>")
@limiter.limit(_api_bearer_limit, key_func=_api_bearer_rate_key)
def automation_run_detail(run_id: str):
    run = get_run(run_id)
    tenant_slug = g.get("bkc_api_key_row", {}).get("tenant_slug", "default")
    if not run or run.get("tenant_slug") != tenant_slug:
        abort(404)
    return jsonify(run)


@api_blueprint.post("/automation/trigger")
@limiter.limit(_api_bearer_limit, key_func=_api_bearer_rate_key)
def automation_trigger():
    payload = request.get_json(silent=True) or {}
    repo = str(payload.get("repo", "")).strip()
    workflow = str(payload.get("workflow", "auzix-test-loop")).strip() or "auzix-test-loop"
    if not repo:
        return jsonify({"error": "repo_required"}), 400

    api_row = g.get("bkc_api_key_row") or {}
    tenant_slug = str(api_row.get("tenant_slug") or "default")
    key_name = str(api_row.get("name") or "api")
    run = create_automation_run(
        tenant_slug=tenant_slug,
        requested_by=f"api-key:{key_name}",
        trigger_source="api",
        repo=repo,
        workflow=workflow,
        ref=str(payload.get("ref", "")),
        commit=str(payload.get("commit", "")),
        notes=str(payload.get("notes", "")),
        extra={"request_payload": payload},
    )

    run, queued, job_id = _queue_api_automation_run(run, workflow, "deploy", api_row)

    return (
        jsonify(
            {
                "run_id": run["id"],
                "status": run["status"],
                "queued": queued,
                "job_id": job_id,
                "workflow": run["workflow"],
                "queue_available": queued,
            }
        ),
        202,
    )


@api_blueprint.post("/automation/runs/<run_id>/action")
@limiter.limit(_api_bearer_limit, key_func=_api_bearer_rate_key)
def automation_run_action(run_id: str):
    payload = request.get_json(silent=True) or {}
    action = str(payload.get("action", "")).strip().lower()
    if action not in {"retry", "redeploy", "undeploy"}:
        return jsonify({"error": "invalid_action", "allowed": ["retry", "redeploy", "undeploy"]}), 400

    api_row = g.get("bkc_api_key_row") or {}
    tenant_slug = str(api_row.get("tenant_slug") or "default")
    key_name = str(api_row.get("name") or "api")
    source = get_run(run_id)
    if not source or source.get("tenant_slug") != tenant_slug:
        abort(404)

    source_workflow = str(source.get("workflow", "")).strip()
    if not workflow_is_supported(source_workflow):
        return jsonify({"error": "workflow_not_supported", "workflow": source_workflow}), 400

    undeploy_workflow = _rx_demo_undeploy_workflow(source_workflow)
    action_workflow = undeploy_workflow if action == "undeploy" and undeploy_workflow else source_workflow
    if action == "undeploy" and not (undeploy_workflow or workflow_supports_undeploy(source_workflow)):
        return jsonify({"error": "undeploy_not_supported", "workflow": source_workflow}), 400

    extra = dict(source.get("extra", {}))
    extra["parent_run_id"] = source["id"]
    action_mode = "deploy" if undeploy_workflow else ("undeploy" if action == "undeploy" else "deploy")
    extra["action_mode"] = action_mode
    extra["trigger_action"] = action

    note_prefix = {"retry": "Retry", "redeploy": "Redeploy", "undeploy": "Undeploy"}[action]
    run = create_automation_run(
        tenant_slug=tenant_slug,
        requested_by=f"api-key:{key_name}",
        trigger_source="api",
        repo=str(source.get("repo", "")),
        workflow=action_workflow,
        ref=str(source.get("ref", "")),
        commit=str(source.get("commit", "")),
        notes=f"{note_prefix} of {source['id'][:8]}. {str(source.get('notes', '')).strip()}".strip(),
        extra=extra,
    )
    run, queued, job_id = _queue_api_automation_run(run, action_workflow, action_mode, api_row)
    return (
        jsonify(
            {
                "run_id": run["id"],
                "source_run_id": source["id"],
                "action": action,
                "status": run["status"],
                "queued": queued,
                "job_id": job_id,
                "workflow": run["workflow"],
                "action_mode": action_mode,
                "queue_available": queued,
            }
        ),
        202,
    )
