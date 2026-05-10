from __future__ import annotations

import json

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user

from services import bkc_db
from services.automation_pipeline import create_automation_run, mark_run_blocked, mark_run_queued
from services.automation_runs import get_run, load_runs
from services.integration_store import load_proxmox_snapshot
from services.job_queue import enqueue_job, job_queue_enabled
from services.pipeline_executor import (
    workflow_job_timeout,
    workflow_is_supported,
    workflow_runtime_snapshot,
    workflow_stage_definitions,
    workflow_supports_undeploy,
)
from services.pipeline_catalog import (
    create_custom_pipeline,
    demo_pipelines,
    pipeline_by_id,
    save_pipeline_override,
    save_stage_override,
    stage_override,
)
from services.tenant_context import get_current_tenant_id, get_effective_tenant_slug

pipelines_blueprint = Blueprint("pipelines", __name__)


PIPELINE_TAGS = (
    "planned",
    "runnable",
    "build",
    "candidate",
    "deploy",
    "monitoring",
    "content",
    "hypervisor",
    "telemetry",
)


def _stage_summary(run: dict) -> dict:
    summary = {"complete": 0, "active": 0, "planned": 0, "failed": 0, "other": 0}
    for stage in run.get("stages", []):
        status = str(stage.get("status", "planned")).strip().lower()
        if status in ("queued", "running", "waiting-executor", "blocked"):
            summary["active"] += 1
        elif status in summary:
            summary[status] += 1
        else:
            summary["other"] += 1
    return summary


def _queue_run(run: dict, *, tenant_slug: str, tenant_id: int, remote_ip: str | None, user_id: int) -> dict:
    action_mode = str(run.get("extra", {}).get("action_mode", "deploy"))
    timeout = workflow_job_timeout(str(run.get("workflow", "")), action_mode=action_mode)
    if job_queue_enabled():
        try:
            job = enqueue_job(
                "services.job_tasks.automation_pipeline_job",
                (
                    run["id"],
                    tenant_slug,
                    tenant_id,
                    user_id,
                    remote_ip,
                ),
                job_timeout=timeout,
                meta={
                    "kind": "automation",
                    "run_id": run["id"],
                    "tenant_slug": tenant_slug,
                    "repo": run["repo"],
                    "workflow": run["workflow"],
                    "job_timeout": timeout,
                },
            )
            return mark_run_queued(run["id"], job.id) or run
        except Exception as exc:
            detail = f"Queue backend unavailable: {exc}"
            return mark_run_blocked(run["id"], detail) or run
    return run


def _run_external_links(run: dict) -> list[dict]:
    pipeline_id = str(run.get("extra", {}).get("pipeline_id", ""))
    pipeline = pipeline_by_id(pipeline_id) if pipeline_id else None
    if not pipeline:
        pipeline = next((item for item in demo_pipelines() if item["workflow"] == run.get("workflow")), None)
    return list(pipeline.get("links", [])) if pipeline else []


def _pipeline_tags(pipeline: dict, *, supported: bool) -> list[str]:
    workflow = str(pipeline.get("workflow", "")).strip().lower()
    repo = str(pipeline.get("repo", "")).strip().lower()
    tags = {"runnable" if supported else "planned"}
    if workflow in {"tabor-build", "fedora-workstation-spin"}:
        tags.add("build")
    if workflow in {"wordpress-appliance-import", "fedora-cloud-import", "fedora-template-deploy"}:
        tags.update({"hypervisor", "candidate"})
    if workflow in {"fedora-cloud-import", "fedora-template-deploy"}:
        tags.add("deploy")
    if workflow in {"monitoring-stack", "microblog-publish"}:
        tags.add("deploy")
    if workflow == "monitoring-stack":
        tags.add("monitoring")
    if workflow == "microblog-publish":
        tags.add("content")
    if workflow == "host-telemetry":
        tags.update({"monitoring", "telemetry"})
    if workflow == "lab-demo":
        tags.add("hypervisor")
    if "proxmox" in repo:
        tags.add("hypervisor")
    for tag in pipeline.get("tags", []):
        if str(tag).strip():
            tags.add(str(tag).strip().lower())
    return sorted(tags)


def _matches_search(pipeline: dict, query: str) -> bool:
    if not query:
        return True
    haystack = " ".join(
        [
            str(pipeline.get("name", "")),
            str(pipeline.get("repo", "")),
            str(pipeline.get("workflow", "")),
            str(pipeline.get("description", "")),
            str(pipeline.get("notes", "")),
            " ".join(str(stage) for stage in pipeline.get("stages", [])),
        ]
    ).lower()
    return query in haystack


def _run_matches_search(run: dict, query: str) -> bool:
    if not query:
        return True
    haystack = " ".join(
        [
            str(run.get("repo", "")),
            str(run.get("workflow", "")),
            str(run.get("ref", "")),
            str(run.get("commit", "")),
            str(run.get("notes", "")),
            str(run.get("status", "")),
        ]
    ).lower()
    return query in haystack


def _run_tags(run: dict) -> list[str]:
    tags = set()
    workflow = str(run.get("workflow", "")).strip().lower()
    status = str(run.get("status", "")).strip().lower()
    if workflow in {"tabor-build", "fedora-workstation-spin"}:
        tags.add("build")
    if workflow in {"wordpress-appliance-import", "fedora-cloud-import", "fedora-template-deploy"}:
        tags.update({"hypervisor", "candidate"})
    if workflow in {"fedora-cloud-import", "fedora-template-deploy"}:
        tags.add("deploy")
    if workflow in {"monitoring-stack", "microblog-publish"}:
        tags.add("deploy")
    if workflow == "monitoring-stack":
        tags.add("monitoring")
    if workflow == "microblog-publish":
        tags.add("content")
    if workflow == "host-telemetry":
        tags.update({"monitoring", "telemetry"})
    if workflow == "lab-demo":
        tags.add("hypervisor")
    if status in {"planned", "blocked", "failed", "complete", "running", "waiting-executor"}:
        tags.add(status)
    return sorted(tags)


def _executor_source_files(workflow: str) -> list[str]:
    sources = [
        "/home/auzieman/Projects/BlackKnightController/services/pipeline_catalog.py",
        "/home/auzieman/Projects/BlackKnightController/services/pipeline_executor.py",
    ]
    workflow = (workflow or "").strip().lower()
    if workflow == "tabor-build":
        sources.extend(
            [
                "/home/auzieman/Projects/lab/ns1/ansible/tabor-linux-forge-builder.yml",
                "/home/auzieman/Projects/lab/ns1/ansible/group_vars/tabor_linux_forge.yml",
                "/home/auzieman/Projects/tabor-linux-forge/scripts/fetch-linux.sh",
                "/home/auzieman/Projects/tabor-linux-forge/scripts/build-auzix-x86-image.sh",
            ]
        )
    elif workflow == "monitoring-stack":
        sources.extend(
            [
                "/home/auzieman/Projects/lab/ns1/ansible/monitoring-stack.yml",
                "/home/auzieman/Projects/lab/ns1/ansible/files/grafana-dashboards/host-ops.json",
            ]
        )
    elif workflow == "microblog-publish":
        sources.append("/home/auzieman/Projects/lab/ns1/ansible/microblog-stack.yml")
    elif workflow == "host-telemetry":
        sources.append("/home/auzieman/Projects/lab/ns1/ansible/setup_monitoring.yml")
    elif workflow == "fedora-workstation-spin":
        sources.extend(
            [
                "/home/auzieman/Projects/BlackKnightController/services/fresh_build_library.py",
                "/home/auzieman/Projects/BlackKnightController/file_templates/fedora-server-minimal.ks.j2",
            ]
        )
    elif workflow in {"fedora-cloud-import", "fedora-template-deploy"}:
        sources.extend(
            [
                "/home/auzieman/Projects/BlackKnightController/services/proxmox.py",
                "/home/auzieman/Projects/BlackKnightController/services/remote_ops.py",
            ]
        )
    elif workflow == "wordpress-appliance-import":
        sources.extend(
            [
                "/home/auzieman/Projects/BlackKnightController/services/proxmox.py",
                "/home/auzieman/Projects/BlackKnightController/routes/proxmox_ops.py",
            ]
        )
    return sources


def _candidate_catalog() -> list[dict]:
    candidates: list[dict] = []
    snapshot = load_proxmox_snapshot() or {}
    for template in snapshot.get("templates", []):
        name = str(template.get("name") or template.get("vmid") or "Proxmox VM Template")
        candidates.append(
            {
                "id": f"proxmox-template-{template.get('vmid')}",
                "source": "proxmox",
                "kind": "vm-template",
                "name": name,
                "summary": f"VM template on {template.get('node', 'unknown node')} (vmid {template.get('vmid', 'n/a')}).",
                "candidate": template,
                "recommended_stages": [
                    "source-select",
                    "proxmox-import",
                    "instance-configure",
                    "boot",
                    "ssh-validate",
                ],
            }
        )
    for vm in snapshot.get("virtual_machines", []):
        if not vm.get("template"):
            continue
        name = str(vm.get("name") or vm.get("vmid") or "Proxmox VM Template")
        candidate = {
            "id": f"proxmox-template-{vm.get('vmid')}",
            "source": "proxmox",
            "kind": "vm-template",
            "name": name,
            "summary": f"VM template on {vm.get('node', 'unknown node')} (vmid {vm.get('vmid', 'n/a')}).",
            "candidate": vm,
            "recommended_stages": [
                "source-select",
                "proxmox-import",
                "instance-configure",
                "boot",
                "ssh-validate",
            ],
        }
        if not any(existing["id"] == candidate["id"] for existing in candidates):
            candidates.append(candidate)
    for template in snapshot.get("container_templates", []):
        volid = str(template.get("volid") or template.get("name") or "ct-template")
        candidates.append(
            {
                "id": f"proxmox-ct-{volid.replace('/', '-').replace(':', '-')}",
                "source": "proxmox",
                "kind": "lxc-template",
                "name": volid,
                "summary": f"LXC template from storage {template.get('storage', 'unknown')} on {template.get('node', 'unknown node')}.",
                "candidate": template,
                "recommended_stages": [
                    "source-select",
                    "proxmox-import",
                    "instance-configure",
                    "boot",
                    "ssh-validate",
                ],
            }
        )

    candidates.extend(
        [
            {
                "id": "turnkey-core",
                "source": "catalog",
                "kind": "appliance",
                "name": "TurnKey Linux Core",
                "summary": "Starter appliance candidate for a lightweight imported VM workflow with post-boot SSH validation.",
                "candidate": {"vendor": "TurnKey Linux", "slug": "core"},
                "recommended_stages": ["source-select", "proxmox-import", "boot", "ssh-validate"],
            },
            {
                "id": "turnkey-wordpress",
                "source": "catalog",
                "kind": "appliance",
                "name": "TurnKey WordPress",
                "summary": "Appliance candidate for import-first pipeline testing and service-specific post-boot customization.",
                "candidate": {"vendor": "TurnKey Linux", "slug": "wordpress"},
                "recommended_stages": ["source-select", "proxmox-import", "instance-configure", "boot", "ssh-validate"],
            },
            {
                "id": "fedora-template-base",
                "source": "catalog",
                "kind": "image-kit",
                "name": "Fedora 44 Minimal Template",
                "summary": "Starter candidate for cloning a known local Fedora minimal Proxmox template, then taking it over with later chain-install or SSH-driven customization.",
                "candidate": {"vendor": "Local Proxmox", "slug": "fedora-template"},
                "recommended_stages": ["source-select", "proxmox-import", "instance-configure", "boot", "ssh-validate"],
            },
        ]
    )
    return candidates


def _candidate_matches(candidate: dict, query: str) -> bool:
    if not query:
        return True
    haystack = " ".join(
        [
            str(candidate.get("name", "")),
            str(candidate.get("kind", "")),
            str(candidate.get("source", "")),
            str(candidate.get("summary", "")),
        ]
    ).lower()
    return query in haystack


def _starter_lane_from_candidate(candidate: dict) -> dict:
    name = str(candidate.get("name", "Candidate Import")).strip()
    kind = str(candidate.get("kind", "candidate")).strip()
    source = str(candidate.get("source", "catalog")).strip()
    slug = str((candidate.get("candidate") or {}).get("slug", "")).strip().lower()
    workflow = "candidate-import"
    repo = "candidate-import"
    tags = {"planned", "candidate", kind}
    name_lc = name.lower()
    if slug in {"cloud-base", "fedora-template"} or "fedora" in name_lc or "fc44" in name_lc:
        workflow = "fedora-template-deploy"
        repo = "proxmox-template-deploy"
        tags.update({"hypervisor", "deploy"})
        tags.discard("planned")
        tags.add("runnable")
    elif slug == "wordpress":
        workflow = "wordpress-appliance-import"
        tags.update({"hypervisor"})
        tags.discard("planned")
        tags.add("runnable")
    stages = list(candidate.get("recommended_stages", [])) or [
        "source-select",
        "proxmox-import",
        "boot",
        "ssh-validate",
    ]
    return {
        "id": f"custom-{candidate['id']}",
        "name": f"Draft: {name}",
        "repo": repo,
        "workflow": workflow,
        "description": f"Draft lane generated from {source} {kind} candidate {name}.",
        "stages": stages,
        "notes": (
            "Candidate-derived draft lane. Adjust the stage notes and definition blocks as needed."
            if workflow != "candidate-import"
            else "Candidate-derived draft lane. Flesh out the executor path and stage definitions before trying to run it."
        ),
        "links": [
            {"label": "BlackKnightController", "url": "http://swarm1.lab.auzietek.com:5000"},
            {"label": "Proxmox", "url": "https://192.168.1.9:8006"},
            {"label": "Grafana", "url": "http://swarm1.lab.auzietek.com:3000"},
        ],
        "dashboards": [
            {
                "name": "Pipeline Control",
                "summary": "Use the draft lane alongside the pipeline control dashboard while wiring import and validation stages.",
                "url": "http://swarm1.lab.auzietek.com:3000",
            }
        ],
        "candidate": dict(candidate),
        "tags": sorted(tags),
    }


def _stage_logic_map(workflow: str) -> dict[str, dict]:
    return {
        str(stage.get("name", "")).strip(): stage
        for stage in workflow_stage_definitions(workflow)
        if str(stage.get("name", "")).strip()
    }


@pipelines_blueprint.route("/pipelines", methods=["GET", "POST"])
def pipelines():
    tenant_id = get_current_tenant_id()
    tenant_slug = get_effective_tenant_slug()
    search_query = request.args.get("q", "").strip().lower()
    selected_tag = request.args.get("tag", "").strip().lower()
    if request.method == "POST":
        if request.form.get("action") == "create-candidate-pipeline":
            candidate_id = request.form.get("candidate_id", "").strip()
            candidate = next((item for item in _candidate_catalog() if item["id"] == candidate_id), None)
            if not candidate:
                flash("Candidate selection is invalid.", "error")
                return redirect(url_for("pipelines.pipelines"))
            lane = create_custom_pipeline(_starter_lane_from_candidate(candidate))
            flash(f"Created draft lane from {candidate['name']}.")
            return redirect(url_for("pipelines.pipeline_edit", pipeline_id=lane["id"]))

        pipeline_id = request.form.get("pipeline_id", "").strip()
        ref = request.form.get("ref", "").strip() or "refs/heads/main"
        commit = request.form.get("commit", "").strip()
        notes = request.form.get("notes", "").strip()
        pipeline = pipeline_by_id(pipeline_id)
        if not pipeline:
            flash("Pipeline selection is invalid.", "error")
            return redirect(url_for("pipelines.pipelines"))
        if not workflow_is_supported(str(pipeline.get("workflow", ""))):
            flash(f"{pipeline['name']} is still a planned lane. Its executor is not wired yet.", "error")
            return redirect(url_for("pipelines.pipelines"))

        run = create_automation_run(
            tenant_slug=tenant_slug,
            requested_by=f"user:{getattr(current_user, 'id', 'unknown')}",
            trigger_source="ui",
            repo=pipeline["repo"],
            workflow=pipeline["workflow"],
            ref=ref,
            commit=commit,
            notes=notes or pipeline.get("notes", ""),
            extra={"pipeline_id": pipeline["id"], "pipeline_name": pipeline["name"]},
        )

        queued = _queue_run(
            run,
            tenant_slug=tenant_slug,
            tenant_id=tenant_id,
            remote_ip=request.remote_addr,
            user_id=int(current_user.id),
        )
        if queued.get("status") == "blocked":
            flash(f"{pipeline['name']} was registered, but queueing failed. {queued.get('extra', {}).get('executor_status', '')}", "error")
        elif job_queue_enabled():
            flash(f"Queued {pipeline['name']} as run {queued['id']}.")
        else:
            flash(f"Registered {pipeline['name']} as run {run['id']}.")

        bkc_db.append_audit(
            int(current_user.id),
            tenant_id,
            "pipelines.trigger",
            "automation",
            {"pipeline_id": pipeline["id"], "run_id": run["id"], "workflow": pipeline["workflow"]},
            request.remote_addr,
        )
        return redirect(url_for("pipelines.pipelines"))

    supported_workflows = {item["workflow"]: workflow_is_supported(item["workflow"]) for item in demo_pipelines()}
    visible_pipelines = []
    for item in demo_pipelines():
        supported = supported_workflows.get(item["workflow"], False)
        tags = _pipeline_tags(item, supported=supported)
        if selected_tag and selected_tag not in tags:
            continue
        if not _matches_search(item, search_query):
            continue
        enriched = dict(item)
        enriched["tags"] = tags
        visible_pipelines.append(enriched)

    visible_runs = []
    for run in load_runs():
        if run.get("tenant_slug") != tenant_slug:
            continue
        run_tags = _run_tags(run)
        if selected_tag and selected_tag not in run_tags:
            continue
        if not _run_matches_search(run, search_query):
            continue
        enriched = dict(run)
        enriched["stage_summary"] = _stage_summary(run)
        enriched["tags"] = run_tags
        visible_runs.append(enriched)

    latest_runs_by_workflow: dict[str, dict] = {}
    for run in visible_runs:
        latest_runs_by_workflow.setdefault(str(run.get("workflow", "")), run)

    return render_template(
        "pipelines.html.j2",
        pipelines=visible_pipelines,
        runs=visible_runs[:12],
        latest_runs_by_workflow=latest_runs_by_workflow,
        supported_workflows=supported_workflows,
        search_query=search_query,
        selected_tag=selected_tag,
        available_tags=PIPELINE_TAGS,
        candidates=[item for item in _candidate_catalog() if _candidate_matches(item, search_query)],
    )


@pipelines_blueprint.route("/pipelines/<run_id>", methods=["GET"])
def pipeline_run_detail(run_id: str):
    tenant_slug = get_effective_tenant_slug()
    run = get_run(run_id)
    if not run or run.get("tenant_slug") != tenant_slug:
        abort(404)

    logs_snapshot = None
    try:
        logs_snapshot = workflow_runtime_snapshot(str(run.get("workflow", "")))
    except Exception as exc:  # noqa: BLE001
        logs_snapshot = {"services": [], "logs": [], "error": str(exc)}

    enriched = dict(run)
    enriched["stage_summary"] = _stage_summary(run)
    return render_template(
        "pipeline_run_detail.html.j2",
        run=enriched,
        logs_snapshot=logs_snapshot,
        supports_undeploy=workflow_supports_undeploy(str(run.get("workflow", ""))),
        external_links=_run_external_links(run),
        pipeline_definition=pipeline_by_id(str(run.get("extra", {}).get("pipeline_id", ""))),
        workflow_stage_details=workflow_stage_definitions(
            str(run.get("workflow", "")),
            action_mode=str(run.get("extra", {}).get("action_mode", "deploy")),
        ),
    )


@pipelines_blueprint.route("/pipelines/<run_id>/actions", methods=["POST"])
def pipeline_run_action(run_id: str):
    tenant_id = get_current_tenant_id()
    tenant_slug = get_effective_tenant_slug()
    source = get_run(run_id)
    if not source or source.get("tenant_slug") != tenant_slug:
        abort(404)

    action = request.form.get("action", "").strip().lower()
    if action not in {"retry", "redeploy", "undeploy"}:
        flash("Pipeline action is invalid.", "error")
        return redirect(url_for("pipelines.pipeline_run_detail", run_id=run_id))
    if not workflow_is_supported(str(source.get("workflow", ""))):
        flash("This lane is still planned. Its executor is not wired yet.", "error")
        return redirect(url_for("pipelines.pipeline_run_detail", run_id=run_id))
    if action == "undeploy" and not workflow_supports_undeploy(str(source.get("workflow", ""))):
        flash("This pipeline does not support undeploy.", "error")
        return redirect(url_for("pipelines.pipeline_run_detail", run_id=run_id))

    extra = dict(source.get("extra", {}))
    extra["parent_run_id"] = source["id"]
    extra["action_mode"] = "undeploy" if action == "undeploy" else "deploy"
    extra["trigger_action"] = action

    note_prefix = {
        "retry": "Retry",
        "redeploy": "Redeploy",
        "undeploy": "Undeploy",
    }[action]

    run = create_automation_run(
        tenant_slug=tenant_slug,
        requested_by=f"user:{getattr(current_user, 'id', 'unknown')}",
        trigger_source="ui",
        repo=source.get("repo", ""),
        workflow=source.get("workflow", ""),
        ref=source.get("ref", ""),
        commit=source.get("commit", ""),
        notes=f"{note_prefix} of {source['id'][:8]}. {source.get('notes', '').strip()}".strip(),
        extra=extra,
    )
    queued = _queue_run(
        run,
        tenant_slug=tenant_slug,
        tenant_id=tenant_id,
        remote_ip=request.remote_addr,
        user_id=int(current_user.id),
    )

    bkc_db.append_audit(
        int(current_user.id),
        tenant_id,
        "pipelines.run_action",
        "automation",
        {"run_id": queued["id"], "source_run_id": source["id"], "action": action},
        request.remote_addr,
    )

    if queued.get("status") == "blocked":
        flash(f"{note_prefix} run was registered, but queueing failed.", "error")
    else:
        flash(f"{note_prefix} run queued as {queued['id']}.")
    return redirect(url_for("pipelines.pipeline_run_detail", run_id=queued["id"]))


@pipelines_blueprint.route("/pipelines/<pipeline_id>/edit", methods=["GET", "POST"])
def pipeline_edit(pipeline_id: str):
    pipeline = pipeline_by_id(pipeline_id)
    if not pipeline:
        abort(404)

    if request.method == "POST":
        try:
            stages = json.loads(request.form.get("stages_json", "[]") or "[]")
            links = json.loads(request.form.get("links_json", "[]") or "[]")
            dashboards = json.loads(request.form.get("dashboards_json", "[]") or "[]")
        except json.JSONDecodeError as exc:
            flash(f"Pipeline JSON is invalid: {exc}", "error")
            return redirect(url_for("pipelines.pipeline_edit", pipeline_id=pipeline_id))

        if not isinstance(stages, list) or not isinstance(links, list) or not isinstance(dashboards, list):
            flash("Stages, links, and dashboards must be JSON arrays.", "error")
            return redirect(url_for("pipelines.pipeline_edit", pipeline_id=pipeline_id))
        stage_names = [str(item).strip() for item in stages if str(item).strip()]

        save_pipeline_override(
            pipeline_id,
            name=request.form.get("name", ""),
            repo=request.form.get("repo", ""),
            description=request.form.get("description", ""),
            notes=request.form.get("notes", ""),
            stages=stage_names,
            links=links,
            dashboards=dashboards,
        )
        flash("Pipeline metadata saved.")
        return redirect(url_for("pipelines.pipeline_edit", pipeline_id=pipeline_id))

    return render_template(
        "pipeline_edit.html.j2",
        pipeline=pipeline,
        supported=workflow_is_supported(str(pipeline.get("workflow", ""))),
        workflow_stage_details=workflow_stage_definitions(str(pipeline.get("workflow", ""))),
        stages_json=json.dumps(pipeline.get("stages", []), indent=2),
        links_json=json.dumps(pipeline.get("links", []), indent=2),
        dashboards_json=json.dumps(pipeline.get("dashboards", []), indent=2),
        executor_source_files=_executor_source_files(str(pipeline.get("workflow", ""))),
    )


@pipelines_blueprint.route("/pipelines/<pipeline_id>/stages/<stage_name>/edit", methods=["GET", "POST"])
def pipeline_stage_edit(pipeline_id: str, stage_name: str):
    pipeline = pipeline_by_id(pipeline_id)
    if not pipeline:
        abort(404)

    stage_name = stage_name.strip()
    if stage_name not in [str(item).strip() for item in pipeline.get("stages", [])]:
        abort(404)

    logic = _stage_logic_map(str(pipeline.get("workflow", ""))).get(stage_name)
    saved = stage_override(pipeline_id, stage_name)

    if request.method == "POST":
        save_stage_override(
            pipeline_id,
            stage_name,
            display_name=request.form.get("display_name", ""),
            operator_notes=request.form.get("operator_notes", ""),
            draft_definition=request.form.get("draft_definition", ""),
        )
        flash("Stage notes saved.")
        return redirect(
            url_for(
                "pipelines.pipeline_stage_edit",
                pipeline_id=pipeline_id,
                stage_name=stage_name,
            )
        )

    return render_template(
        "pipeline_stage_edit.html.j2",
        pipeline=pipeline,
        stage_name=stage_name,
        supported=workflow_is_supported(str(pipeline.get("workflow", ""))),
        logic=logic,
        saved=saved,
        executor_source_files=_executor_source_files(str(pipeline.get("workflow", ""))),
    )
