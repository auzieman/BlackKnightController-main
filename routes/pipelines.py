from __future__ import annotations

import json
import hashlib
import re
import shlex
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from html import escape
from pathlib import Path

from flask import Blueprint, abort, current_app, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user
from services import bkc_db
from services.automation_pipeline import create_automation_run, mark_run_blocked, mark_run_queued
from services.automation_runs import get_run, load_runs
from services.integration_store import load_proxmox_snapshot
from services.job_queue import SLOW_QUEUE_NAME, enqueue_job, job_queue_enabled
from services.pipeline_catalog import (
    catalog_signature,
    create_custom_pipeline,
    demo_pipelines,
    pipeline_by_id,
    resolve_pipeline_dictionary,
    save_pipeline_override,
    save_stage_override,
    stage_override,
)
from services.pipeline_executor import (
    workflow_is_supported,
    workflow_job_timeout,
    workflow_runtime_snapshot,
    workflow_stage_definitions,
    workflow_supports_undeploy,
)
from services.remote_ops import run_remote_command
from services.tenant_context import get_current_tenant_id, get_effective_tenant_slug

pipelines_blueprint = Blueprint("pipelines", __name__)


@pipelines_blueprint.route("/api/v1/pipelines/catalog-signature", methods=["GET"])
def pipeline_catalog_signature_api():
    return jsonify(catalog_signature())


@pipelines_blueprint.post("/api/v1/pipelines/explain")
def pipeline_explain_api():
    payload = request.get_json(silent=True) or {}
    pipeline_id = str(payload.get("pipeline_id") or "").strip()
    run_id = str(payload.get("run_id") or "").strip()
    latest = None
    pipeline = None
    if run_id:
        latest = get_run(run_id)
        if not latest or latest.get("tenant_slug") != get_effective_tenant_slug():
            return jsonify({"error": "run_not_found"}), 404
        latest = {**latest, "stage_summary": _stage_summary(latest)}
        pipeline = pipeline_by_id(str(latest.get("extra", {}).get("pipeline_id", ""))) or _pipeline_from_run(latest)
    else:
        pipeline = pipeline_by_id(pipeline_id)
        if not pipeline:
            return jsonify({"error": "pipeline_not_found"}), 404
        runs = load_runs()
        latest_runs_by_pipeline_key = {
            _run_group_key(run): {
                **run,
                "stage_summary": _stage_summary(run),
            }
            for run in runs
        }
        latest = _pipeline_latest_run(pipeline, latest_runs_by_pipeline_key)
    model = str(payload.get("model") or "qwen2.5-coder:1.5b")
    host = str(payload.get("ollama_host") or "10.20.0.240")
    try:
        explanation = _explain_pipeline_with_ollama(pipeline, latest, model=model, host=host)
        source = "ollama"
    except Exception as exc:
        current_app.logger.warning("Ollama pipeline explanation failed for %s: %s", pipeline_id, exc)
        explanation = _fallback_pipeline_explanation(pipeline, latest)
        source = "fallback"
    return jsonify({
        "status": "ok",
        "pipeline_id": pipeline_id,
        "run_id": run_id,
        "model": model,
        "source": source,
        "explanation": explanation,
        "explanation_html": _render_explainer_markdown(explanation),
    })


@pipelines_blueprint.post("/api/v1/pipelines/explain-drawio")
def pipeline_explain_drawio_api():
    payload = request.get_json(silent=True) or {}
    pipeline_id = str(payload.get("pipeline_id") or "").strip()
    run_id = str(payload.get("run_id") or "").strip()
    run = get_run(run_id) if run_id else None
    if run and run.get("tenant_slug") != get_effective_tenant_slug():
        return jsonify({"error": "run_not_found"}), 404
    pipeline = None
    if run:
        pipeline = pipeline_by_id(str(run.get("extra", {}).get("pipeline_id", ""))) or _pipeline_from_run(run)
    elif pipeline_id:
        pipeline = pipeline_by_id(pipeline_id)
    if not pipeline:
        return jsonify({"error": "pipeline_not_found"}), 404
    explanation = str(payload.get("explanation") or "").strip()
    run_map = _pipeline_run_map(pipeline, {**run, "stage_summary": _stage_summary(run)} if run else None)
    result = _export_pipeline_explainer_drawio(
        pipeline,
        run_map,
        explanation,
        Path(current_app.static_folder or "static") / "exports",
        tenant_slug=get_effective_tenant_slug(),
    )
    return jsonify(result)


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
    "demo",
    "rx-demo",
    "k3s",
    "registry",
    "cluster",
    "ssh",
    "add-node",
)


def _available_pipeline_tags(pipelines: list[dict], runs: list[dict]) -> list[str]:
    tags = set(PIPELINE_TAGS)
    for pipeline in pipelines:
        tags.update(_pipeline_tags(pipeline, supported=workflow_is_supported(str(pipeline.get("workflow", "")))))
    for run in runs:
        tags.update(_run_tags(run))
    return sorted(tag for tag in tags if tag)


def _run_timestamp(run: dict) -> datetime:
    value = str(run.get("updated_at") or run.get("created_at") or "").strip()
    if value.endswith("Z"):
        value = f"{value[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


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
    queue_name = (
        SLOW_QUEUE_NAME
        if str(run.get("extra", {}).get("resource_class", "")).strip().lower() == "slow"
        else "bkc"
    )
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
                queue_name=queue_name,
                meta={
                    "kind": "automation",
                    "run_id": run["id"],
                    "tenant_slug": tenant_slug,
                    "repo": run["repo"],
                    "workflow": run["workflow"],
                    "job_timeout": timeout,
                    "queue_name": queue_name,
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


def _run_group_key(run: dict) -> str:
    extra = run.get("extra", {}) or {}
    pipeline_id = str(extra.get("pipeline_id", "")).strip()
    if pipeline_id:
        return f"pipeline:{pipeline_id}"
    return f"workflow:{str(run.get('workflow', '')).strip()}"


def _pipeline_latest_run(pipeline: dict, latest_runs: dict[str, dict]) -> dict | None:
    pipeline_id = str(pipeline.get("id", "")).strip()
    workflow = str(pipeline.get("workflow", "")).strip()
    if pipeline_id:
        run = latest_runs.get(f"pipeline:{pipeline_id}")
        if run:
            return run
    if workflow:
        return latest_runs.get(f"workflow:{workflow}")
    return None


def _pipeline_from_run(run: dict) -> dict:
    workflow = str(run.get("workflow") or "").strip()
    stages = [stage.get("name") for stage in run.get("stages", []) if stage.get("name")]
    return {
        "id": str(run.get("extra", {}).get("pipeline_id") or workflow or run.get("id")),
        "name": str(run.get("extra", {}).get("pipeline_name") or workflow or "Pipeline run"),
        "repo": str(run.get("repo") or "run-ledger"),
        "workflow": workflow,
        "description": str(run.get("notes") or "Run reconstructed from the automation ledger."),
        "notes": str(run.get("notes") or ""),
        "tags": ["run-ledger"],
        "stages": stages,
        "actions": [],
    }


def _render_inline_markdown(text: str) -> str:
    rendered = escape(text)
    rendered = re.sub(r"`([^`]+)`", r"<code>\1</code>", rendered)
    rendered = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", rendered)
    return rendered


def _render_explainer_markdown(markdown: str) -> str:
    """Render a small, safe markdown subset for Ollama operator briefs."""
    blocks: list[str] = []
    list_items: list[str] = []
    in_code = False
    code_lines: list[str] = []

    def flush_list() -> None:
        nonlocal list_items
        if list_items:
            blocks.append("<ul>" + "".join(f"<li>{item}</li>" for item in list_items) + "</ul>")
            list_items = []

    for raw_line in (markdown or "").splitlines():
        line = raw_line.rstrip()
        if line.strip().startswith("```"):
            if in_code:
                blocks.append("<pre><code>" + escape("\n".join(code_lines)) + "</code></pre>")
                code_lines = []
                in_code = False
            else:
                flush_list()
                in_code = True
            continue
        if in_code:
            code_lines.append(line)
            continue
        stripped = line.strip()
        if not stripped:
            flush_list()
            continue
        if stripped.startswith("### "):
            flush_list()
            blocks.append(f"<h4>{_render_inline_markdown(stripped[4:])}</h4>")
        elif stripped.startswith("## "):
            flush_list()
            blocks.append(f"<h3>{_render_inline_markdown(stripped[3:])}</h3>")
        elif stripped.startswith("# "):
            flush_list()
            blocks.append(f"<h3>{_render_inline_markdown(stripped[2:])}</h3>")
        elif stripped.startswith(("- ", "* ")):
            list_items.append(_render_inline_markdown(stripped[2:]))
        else:
            flush_list()
            blocks.append(f"<p>{_render_inline_markdown(stripped)}</p>")
    flush_list()
    if in_code and code_lines:
        blocks.append("<pre><code>" + escape("\n".join(code_lines)) + "</code></pre>")
    return "\n".join(blocks)


def _drawio_cell(root: ET.Element, cell_id: str, value: str = "", style: str = "", parent: str = "1", *, vertex: bool = False, edge: bool = False, source: str = "", target: str = "") -> ET.Element:
    attrs = {"id": cell_id, "parent": parent}
    if value:
        attrs["value"] = value
    if style:
        attrs["style"] = style
    if vertex:
        attrs["vertex"] = "1"
    if edge:
        attrs["edge"] = "1"
    if source:
        attrs["source"] = source
    if target:
        attrs["target"] = target
    return ET.SubElement(root, "mxCell", attrs)


def _drawio_geom(parent: ET.Element, x: float, y: float, width: float, height: float, as_: str = "geometry") -> None:
    ET.SubElement(parent, "mxGeometry", {
        "x": str(round(x, 2)),
        "y": str(round(y, 2)),
        "width": str(round(width, 2)),
        "height": str(round(height, 2)),
        "as": as_,
    })


def _export_pipeline_explainer_drawio(pipeline: dict, run_map: dict, explanation: str, output_dir: Path, *, tenant_slug: str = "lab") -> dict:
    stages = list(run_map.get("stages") or [])[:28]
    seed = json.dumps({
        "pipeline": pipeline.get("id"),
        "run": (run_map.get("run") or {}).get("id"),
        "stages": stages,
        "explanation": explanation[:1200],
    }, sort_keys=True, default=str)
    scene_hash = hashlib.sha256(seed.encode()).hexdigest()[:16]
    stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    file_name = f"bkc-pipeline-explainer-{tenant_slug}-{stamp}-{scene_hash}.drawio"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / file_name

    mxfile = ET.Element("mxfile", {
        "host": "app.diagrams.net",
        "modified": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "agent": "BlackKnightController pipeline explainer",
        "version": "24.7.17",
    })
    diagram = ET.SubElement(mxfile, "diagram", {"id": scene_hash, "name": "BKC Pipeline Explainer"})
    model = ET.SubElement(diagram, "mxGraphModel", {
        "dx": "1800",
        "dy": "1200",
        "grid": "1",
        "gridSize": "10",
        "guides": "1",
        "tooltips": "1",
        "connect": "1",
        "arrows": "1",
        "fold": "1",
        "page": "1",
        "pageScale": "1",
        "pageWidth": "1800",
        "pageHeight": "1200",
        "math": "0",
        "shadow": "0",
    })
    root = ET.SubElement(model, "root")
    _drawio_cell(root, "0", parent="")
    _drawio_cell(root, "1", parent="0")

    title = _drawio_cell(
        root,
        "title",
        f"<b>{escape(str(pipeline.get('name') or pipeline.get('id') or 'Pipeline'))}</b><br><font style='font-size:11px;color:#64748b'>workflow {escape(str(pipeline.get('workflow') or ''))} · scene {scene_hash}</font>",
        "text;html=1;strokeColor=none;fillColor=none;fontSize=22;fontColor=#0f172a;align=left;",
        vertex=True,
    )
    _drawio_geom(title, 40, 26, 920, 62)

    brief_text = escape((explanation or "No explainer text supplied.")[:1800]).replace("\n", "<br>")
    brief = _drawio_cell(
        root,
        "brief",
        f"<b>Operator brief</b><br>{brief_text}",
        "rounded=1;whiteSpace=wrap;html=1;arcSize=8;shadow=1;fillColor=#dae8fc;strokeColor=#6c8ebf;fontColor=#1f2937;fontSize=12;align=left;verticalAlign=top;spacing=10;",
        vertex=True,
    )
    _drawio_geom(brief, 40, 110, 430, 560)

    previous_id = ""
    for index, stage in enumerate(stages):
        row = index % 12
        column = index // 12
        x = 540 + column * 390
        y = 115 + row * 88
        status = str(stage.get("status") or "planned").lower()
        fill = "#d5e8d4" if status == "complete" else "#ffe6cc" if status in {"active", "running", "queued"} else "#f8cecc" if status in {"failed", "blocked"} else "#fff2cc"
        stroke = "#82b366" if status == "complete" else "#d79b00" if status in {"active", "running", "queued", "planned"} else "#b85450"
        label = escape(str(stage.get("name") or f"stage {index + 1}"))
        detail = escape(str(stage.get("detail") or stage.get("action") or "")[:120])
        target = escape(", ".join(str(item) for item in (stage.get("targets") or [])[:4]))
        value = f"<b>{index + 1}. {label}</b><br><font style='font-size:10px;color:#52606d'>{escape(status)} · {target}</font><br><font style='font-size:10px'>{detail}</font>"
        cell_id = f"stage:{index}"
        cell = _drawio_cell(
            root,
            cell_id,
            value,
            f"rounded=1;whiteSpace=wrap;html=1;arcSize=8;shadow=1;fillColor={fill};strokeColor={stroke};fontColor=#1f2937;fontSize=12;align=left;verticalAlign=top;spacing=8;",
            vertex=True,
        )
        _drawio_geom(cell, x, y, 320, 66)
        if previous_id:
            edge = _drawio_cell(
                root,
                f"edge:{index}",
                "",
                "edgeStyle=orthogonalEdgeStyle;rounded=1;html=1;strokeColor=#64748b;endArrow=block;",
                edge=True,
                source=previous_id,
                target=cell_id,
            )
            ET.SubElement(edge, "mxGeometry", {"relative": "1", "as": "geometry"})
        previous_id = cell_id

    ET.ElementTree(mxfile).write(output_path, encoding="utf-8", xml_declaration=True)
    return {
        "status": "ready",
        "scene_hash": scene_hash,
        "stage_count": len(stages),
        "artifact_path": str(output_path),
        "artifact_url": f"/static/exports/{file_name}",
    }


def _rx_demo_undeploy_workflow(workflow: str) -> str | None:
    return {
        "rx-demo-k3s-deploy": "rx-demo-k3s-undeploy",
        "rx-demo-k3s-redeploy-from-git": "rx-demo-k3s-undeploy",
        "rx-demo-redeploy-from-git-event": "rx-demo-k3s-undeploy",
    }.get(workflow.strip())


def _run_supports_undeploy(run: dict) -> bool:
    workflow = str(run.get("workflow", "")).strip()
    return bool(_rx_demo_undeploy_workflow(workflow) or workflow_supports_undeploy(workflow))


def _pipeline_tags(pipeline: dict, *, supported: bool) -> list[str]:
    workflow = str(pipeline.get("workflow", "")).strip().lower()
    repo = str(pipeline.get("repo", "")).strip().lower()
    tags = {"runnable" if supported else "planned"}
    if workflow in {"tabor-build", "fedora-workstation-spin"}:
        tags.add("build")
    if workflow == "auzix-vm130-deploy":
        tags.update({"deploy", "ssh", "auzix"})
    if workflow == "auzix-vm134-install-refresh":
        tags.update({"auzix", "build", "installer", "iso", "proxmox", "vm134"})
    if workflow == "auzix-vm135-fresh-install-target":
        tags.update({"auzix", "deploy", "installer", "iso", "proxmox", "vm135"})
    if workflow in {"wordpress-appliance-import", "fedora-cloud-import", "fedora-template-deploy", "fedora-cosmic-postinstall"}:
        tags.update({"hypervisor", "candidate"})
    if workflow in {"fedora-cloud-import", "fedora-template-deploy", "fedora-cosmic-postinstall"}:
        tags.add("deploy")
    if workflow == "fedora-cosmic-postinstall":
        tags.update({"desktop", "ssh"})
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
    extra = run.get("extra", {}) or {}
    pipeline = None
    pipeline_id = str(extra.get("pipeline_id", "")).strip()
    if pipeline_id:
        pipeline = pipeline_by_id(pipeline_id)
    if not pipeline:
        pipeline = next((item for item in demo_pipelines() if item["workflow"] == run.get("workflow")), None)
    haystack = " ".join(
        [
            str(run.get("repo", "")),
            str(run.get("workflow", "")),
            str(run.get("ref", "")),
            str(run.get("commit", "")),
            str(run.get("notes", "")),
            str(run.get("status", "")),
            str(pipeline.get("id", "") if pipeline else ""),
            str(pipeline.get("name", "") if pipeline else ""),
            str(pipeline.get("description", "") if pipeline else ""),
            str(pipeline.get("notes", "") if pipeline else ""),
            " ".join(str(tag) for tag in (pipeline.get("tags", []) if pipeline else [])),
        ]
    ).lower()
    return query in haystack


def _run_tags(run: dict) -> list[str]:
    tags = set()
    workflow = str(run.get("workflow", "")).strip().lower()
    status = str(run.get("status", "")).strip().lower()
    if workflow in {"tabor-build", "fedora-workstation-spin"}:
        tags.add("build")
    if workflow == "auzix-vm130-deploy":
        tags.update({"deploy", "ssh", "auzix"})
    if workflow == "auzix-vm134-install-refresh":
        tags.update({"auzix", "build", "installer", "iso", "proxmox", "vm134"})
    if workflow == "auzix-vm135-fresh-install-target":
        tags.update({"auzix", "deploy", "installer", "iso", "proxmox", "vm135"})
    if workflow in {"wordpress-appliance-import", "fedora-cloud-import", "fedora-template-deploy", "fedora-cosmic-postinstall"}:
        tags.update({"hypervisor", "candidate"})
    if workflow in {"fedora-cloud-import", "fedora-template-deploy", "fedora-cosmic-postinstall"}:
        tags.add("deploy")
    if workflow == "fedora-cosmic-postinstall":
        tags.update({"desktop", "ssh"})
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


def _stage_targets(workflow: str, stage_name: str) -> list[str]:
    workflow = (workflow or "").strip().lower()
    stage = (stage_name or "").strip().lower()
    if workflow == "auzix-vm130-deploy":
        if "source" in stage:
            return ["/srv/nfs/swarm/AuziX", "generated AuzixRoot"]
        return ["VMID 130", "192.168.1.163"]
    if workflow == "rx-demo-k3s-app-refresh":
        if "source" in stage:
            return ["/mnt/swarm/shared/rx-demo"]
        if "build" in stage:
            return ["swarm1", "rx-demo/rx-ui:latest"]
        if "import" in stage:
            return ["kube1 containerd", "kube2 containerd"]
        if "apply" in stage:
            return ["rx-demo namespace", "rx-ui deployment"]
        if "smoke" in stage:
            return ["/lookup", "/approve", "/refill"]
        if "verify" in stage:
            return ["kube1", "kube2"]
    if workflow in {"k3s-fedora-cluster", "k3s-host-telemetry"}:
        if "k3s" in stage or "cluster" in stage or "verify" in stage:
            return ["kube1", "kube2"]
        if "loki" in stage or "logs" in stage:
            return ["promtail", "Loki"]
        if "telemetry" in stage or "cadvisor" in stage:
            return ["Telegraf", "cAdvisor"]
        if "loadgen" in stage:
            return ["rx-demo loadgen"]
    if workflow in {"fedora-template-deploy", "fedora-cloud-import", "wordpress-appliance-import"}:
        if "proxmox" in stage or "clone" in stage or "import" in stage:
            return ["Proxmox", "VM target"]
        if "ssh" in stage or "boot" in stage:
            return ["guest VM", "BKC SSH"]
    if "build" in stage or "image" in stage:
        return ["builder", "artifact"]
    if "deploy" in stage or "apply" in stage:
        return ["runtime", "service"]
    if "health" in stage or "smoke" in stage or "verify" in stage:
        return ["health check"]
    if "repo" in stage or "source" in stage:
        return ["source"]
    return []


def _pipeline_run_map(pipeline: dict | None, latest_run: dict | None) -> dict:
    if not pipeline:
        return {"run": None, "stages": []}
    workflow = str(pipeline.get("workflow", ""))
    pipeline_stage_names = [str(stage_name) for stage_name in pipeline.get("stages", [])]
    run_stages = list((latest_run or {}).get("stages") or [])
    if run_stages and pipeline_stage_names:
        run_stage_names = {str(stage.get("name") or "") for stage in run_stages}
        if not run_stage_names.intersection(pipeline_stage_names):
            run_stages = []
    if not run_stages:
        run_stages = [
            {"name": stage_name, "status": "planned", "detail": ""}
            for stage_name in pipeline_stage_names
        ]
    action_names = list(pipeline.get("actions") or [])
    stages = []
    for idx, stage in enumerate(run_stages):
        name = str(stage.get("name") or "")
        status = str(stage.get("status") or "planned").strip().lower() or "planned"
        stages.append(
            {
                "name": name,
                "status": status,
                "detail": str(stage.get("detail") or ""),
                "updated_at": str(stage.get("updated_at") or ""),
                "action": action_names[idx] if idx < len(action_names) else "",
                "targets": _stage_targets(workflow, name),
            }
        )
    return {"run": latest_run, "stages": stages}


def _pipeline_explain_payload(pipeline: dict, latest_run: dict | None) -> dict:
    stages = list(pipeline.get("stages") or [])[:16]
    actions = list(pipeline.get("actions") or [])[:16]
    return {
        "id": pipeline.get("id"),
        "name": pipeline.get("name"),
        "repo": pipeline.get("repo"),
        "workflow": pipeline.get("workflow"),
        "description": pipeline.get("description"),
        "notes": pipeline.get("notes"),
        "tags": list(pipeline.get("tags") or [])[:12],
        "stages": stages,
        "actions": actions,
        "latest_run": {
            "id": (latest_run or {}).get("id"),
            "status": (latest_run or {}).get("status"),
            "updated_at": (latest_run or {}).get("updated_at"),
            "stage_summary": (latest_run or {}).get("stage_summary"),
        } if latest_run else None,
        "support": {
            "executor_wired": workflow_is_supported(str(pipeline.get("workflow", ""))),
            "undeploy_supported": workflow_supports_undeploy(str(pipeline.get("workflow", ""))),
            "timeout_seconds": workflow_job_timeout(str(pipeline.get("workflow", ""))),
        },
    }


def _fallback_pipeline_explanation(pipeline: dict, latest_run: dict | None) -> str:
    stage_count = len(list(pipeline.get("stages") or []))
    action_count = len(list(pipeline.get("actions") or []))
    status = (latest_run or {}).get("status") or "not run"
    risk = "destructive/rebuild lane" if any(token in " ".join(str(value) for value in pipeline.values()).lower() for token in ("baremetal", "wipe", "pxe", "provision")) else "standard automation lane"
    return (
        f"### {pipeline.get('name') or pipeline.get('id')}\n\n"
        f"This is a {risk} in `{pipeline.get('repo')}` using workflow `{pipeline.get('workflow')}`.\n\n"
        f"- Stages: {stage_count}\n"
        f"- Action bricks: {action_count}\n"
        f"- Latest run: {status}\n"
        f"- Executor: {'wired' if workflow_is_supported(str(pipeline.get('workflow', ''))) else 'planned'}\n\n"
        "Review the stage list, dictionary values, and latest run state before triggering it."
    )


def _explain_pipeline_with_ollama(pipeline: dict, latest_run: dict | None, *, model: str, host: str) -> str:
    payload = _pipeline_explain_payload(pipeline, latest_run)
    prompt = (
        "You are BlackKnightController's local pipeline explainer. "
        "Explain this pipeline to an infrastructure operator in concise markdown using these exact sections: "
        "Operator read, Safety/risk, Before running, Expected proof, Rerun notes. "
        "Do not invent external facts. Separate lifecycle/status from safety/risk. "
        "If the pipeline is destructive, say so plainly without scolding. "
        "Include likely targets, whether it appears runnable, what to check before running, "
        "and what evidence/output should be expected. Keep it under 350 words.\n\n"
        f"PIPELINE_JSON:\n{json.dumps(payload, indent=2, sort_keys=True)}"
    )
    body = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.2, "num_ctx": 8192},
    }
    command = (
        "set -euo pipefail; "
        f"curl -fsS http://127.0.0.1:11434/api/generate -d {shlex.quote(json.dumps(body))}"
    )
    output = run_remote_command(host=host, user="root", command=command, timeout=240)
    response = json.loads(output)
    explanation = str(response.get("response") or "").strip()
    if not explanation:
        raise RuntimeError("Ollama returned an empty explanation.")
    return explanation[:6000]


def _executor_source_files(workflow: str) -> list[str]:
    sources = [
        "/home/auzieman/Projects/BlackKnightController/services/action_catalog.py",
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
    elif workflow == "k3s-host-telemetry":
        sources.extend(
            [
                "/home/auzieman/Projects/BlackKnightController/file_templates/k3s-host-telemetry.yaml",
                "/home/auzieman/Projects/BlackKnightController/file_templates/k3s-loki-logs.yaml",
                "/home/auzieman/Projects/BlackKnightController/file_templates/rx-loadgen-deployment.yaml",
                "/home/auzieman/Projects/rx-demo/tools/pipelines/bkc-k3s-host-telemetry.md",
            ]
        )
    elif workflow == "rx-demo-k3s-app-refresh":
        sources.extend(
            [
                "/home/auzieman/Projects/BlackKnightController/docs/k3s-deployment-linkage.md",
                "/home/auzieman/Projects/rx-demo/k8s/overlays/lab/kustomization.yaml",
                "/home/auzieman/Projects/rx-demo/k8s/base/apps.yaml",
                "/home/auzieman/Projects/rx-demo/src/rx-ui/Rx.Ui/Pages/Index.cshtml.cs",
            ]
        )
    elif workflow == "fedora-workstation-spin":
        sources.extend(
            [
                "/home/auzieman/Projects/BlackKnightController/services/fresh_build_library.py",
                "/home/auzieman/Projects/BlackKnightController/file_templates/fedora-server-minimal.ks.j2",
            ]
        )
    elif workflow in {"fedora-cloud-import", "fedora-template-deploy", "fedora-cosmic-postinstall"}:
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


def _default_stage_definition(pipeline: dict, stage_name: str, logic: dict | None) -> str:
    workflow = str(pipeline.get("workflow", ""))
    lines = [
        f"stage: {stage_name}",
        f"workflow: {workflow}",
    ]
    if not logic:
        lines.extend(
            [
                "state: catalog-only",
                "intent: define action, transport, target, inputs, validation, and rollback before wiring executor logic",
            ]
        )
        return "\n".join(lines)

    fields = [
        ("transport", logic.get("transport")),
        ("action", logic.get("action")),
        ("handler", logic.get("kind")),
        ("target", logic.get("target")),
        ("timeout_seconds", logic.get("timeout")),
    ]
    for key, value in fields:
        if value not in (None, ""):
            lines.append(f"{key}: {value}")

    if logic.get("active"):
        lines.append(f"run: {logic['active']}")
    if logic.get("complete"):
        lines.append(f"success: {logic['complete']}")
    if logic.get("message"):
        lines.append(f"operator_message: {logic['message']}")
    if logic.get("command"):
        lines.extend(["command: |", *[f"  {line}" for line in str(logic["command"]).splitlines()]])
    lines.append("validation: use stage completion, run events, and the run map target chips")
    if str(logic.get("transport") or "").startswith("bkc-ssh"):
        lines.append("rollback: rerun or repair through BKC SSH against the same target")
    elif str(logic.get("transport") or "") == "ssh-manager":
        lines.append("rollback: inspect manager-side artifacts, then rerun this stage or queue a redeploy")
    return "\n".join(lines)


def _default_stage_notes(stage_name: str, logic: dict | None) -> str:
    if not logic:
        return "Catalog-only stage. Add the desired action contract here before executor wiring."
    action = str(logic.get("action") or logic.get("kind") or "stage action")
    transport = str(logic.get("transport") or "internal")
    return f"{stage_name} uses {action} over {transport}. Inputs, expected output, and rollback notes can be refined here."


@pipelines_blueprint.route("/pipelines", methods=["GET", "POST"])
def pipelines():
    tenant_id = get_current_tenant_id()
    tenant_slug = get_effective_tenant_slug()
    search_query = request.args.get("q", "").strip().lower()
    selected_tag = request.args.get("tag", "").strip().lower()
    selected_pipeline_id = request.args.get("pipeline", "").strip()
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

        extra = {"pipeline_id": pipeline["id"], "pipeline_name": pipeline["name"]}
        declared_inputs = pipeline.get("inputs") if isinstance(pipeline.get("inputs"), dict) else {}
        request_inputs: dict[str, object] = {}
        for input_name, input_spec in declared_inputs.items():
            field_name = f"input__{input_name}"
            spec = input_spec if isinstance(input_spec, dict) else {}
            default = spec.get("default")
            if isinstance(default, bool):
                request_inputs[input_name] = request.form.get(field_name) == "true"
            elif field_name in request.form:
                request_inputs[input_name] = request.form.get(field_name, "").strip()
        if request_inputs:
            extra["request_payload"] = {"inputs": request_inputs}
        resource_class = str(pipeline.get("resource_class", "")).strip().lower()
        if resource_class:
            extra["resource_class"] = resource_class
        if str(pipeline.get("workflow", "")).strip().lower() in {"fedora-cosmic-postinstall", "demo-k3s-add-node"}:
            target_host = request.form.get("target_host", "").strip()
            target_name = request.form.get("target_name", "").strip()
            target_vmid = request.form.get("target_vmid", "").strip()
            if target_host:
                extra["target_host"] = target_host
            if target_name:
                extra["target_name"] = target_name
            if target_vmid:
                extra["target_vmid"] = target_vmid

        run = create_automation_run(
            tenant_slug=tenant_slug,
            requested_by=f"user:{getattr(current_user, 'id', 'unknown')}",
            trigger_source="ui",
            repo=pipeline["repo"],
            workflow=pipeline["workflow"],
            ref=ref,
            commit=commit,
            notes=notes or pipeline.get("notes", ""),
            extra=extra,
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

    all_pipelines = demo_pipelines()
    tenant_runs = [run for run in load_runs() if run.get("tenant_slug") == tenant_slug]
    supported_workflows = {
        str(item.get("workflow") or item.get("id") or ""): workflow_is_supported(str(item.get("workflow") or item.get("id") or ""))
        for item in all_pipelines
    }
    visible_pipelines = []
    for item in all_pipelines:
        workflow = str(item.get("workflow") or item.get("id") or "")
        supported = supported_workflows.get(workflow, False)
        tags = _pipeline_tags(item, supported=supported)
        if selected_tag and selected_tag not in tags:
            continue
        if not _matches_search(item, search_query):
            continue
        enriched = dict(item)
        enriched["tags"] = tags
        visible_pipelines.append(enriched)

    visible_runs = []
    for run in sorted(tenant_runs, key=_run_timestamp, reverse=True):
        run_tags = _run_tags(run)
        if selected_tag and selected_tag not in run_tags:
            continue
        if not _run_matches_search(run, search_query):
            continue
        enriched = dict(run)
        enriched["stage_summary"] = _stage_summary(run)
        enriched["tags"] = run_tags
        enriched["supports_undeploy"] = _run_supports_undeploy(run)
        visible_runs.append(enriched)

    run_groups: dict[str, list[dict]] = {}
    for run in visible_runs:
        run_groups.setdefault(_run_group_key(run), []).append(run)
    ledger_runs = []
    seen_run_groups = set()
    for run in visible_runs:
        group_key = _run_group_key(run)
        if group_key in seen_run_groups:
            continue
        seen_run_groups.add(group_key)
        attempts = run_groups.get(group_key, [run])
        enriched = dict(run)
        enriched["attempt_count"] = len(attempts)
        enriched["previous_attempt_count"] = max(0, len(attempts) - 1)
        enriched["failed_attempt_count"] = sum(1 for item in attempts if str(item.get("status", "")).lower() == "failed")
        enriched["run_group_key"] = group_key
        ledger_runs.append(enriched)

    latest_runs_by_pipeline_key: dict[str, dict] = {}
    for run in visible_runs:
        latest_runs_by_pipeline_key.setdefault(_run_group_key(run), run)
        workflow = str(run.get("workflow", "")).strip()
        if workflow:
            latest_runs_by_pipeline_key.setdefault(f"workflow:{workflow}", run)
    visible_pipelines.sort(
        key=lambda item: (
            _pipeline_latest_run(item, latest_runs_by_pipeline_key) is None,
            -_run_timestamp(_pipeline_latest_run(item, latest_runs_by_pipeline_key) or {}).timestamp(),
            str(item.get("name", "")).lower(),
        )
    )
    selected_pipeline = next((item for item in visible_pipelines if item.get("id") == selected_pipeline_id), None)
    if not selected_pipeline and visible_pipelines:
        selected_pipeline = visible_pipelines[0]
    selected_latest = _pipeline_latest_run(selected_pipeline, latest_runs_by_pipeline_key) if selected_pipeline else None

    return render_template(
        "pipelines.html.j2",
        pipelines=visible_pipelines,
        catalog_signature=catalog_signature(),
        selected_pipeline=selected_pipeline,
        selected_dictionary=resolve_pipeline_dictionary(selected_pipeline),
        selected_run_map=_pipeline_run_map(selected_pipeline, selected_latest),
        runs=ledger_runs[:12],
        raw_run_count=len(visible_runs),
        latest_runs_by_pipeline_key=latest_runs_by_pipeline_key,
        supported_workflows=supported_workflows,
        search_query=search_query,
        selected_tag=selected_tag,
        available_tags=_available_pipeline_tags(all_pipelines, tenant_runs),
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
        supports_undeploy=_run_supports_undeploy(run),
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
    source_workflow = str(source.get("workflow", "")).strip()
    undeploy_workflow = _rx_demo_undeploy_workflow(source_workflow)
    action_workflow = undeploy_workflow if action == "undeploy" and undeploy_workflow else source_workflow
    if action == "undeploy" and not (undeploy_workflow or workflow_supports_undeploy(source_workflow)):
        flash("This pipeline does not support undeploy.", "error")
        return redirect(url_for("pipelines.pipeline_run_detail", run_id=run_id))

    extra = dict(source.get("extra", {}))
    extra["parent_run_id"] = source["id"]
    extra["action_mode"] = "deploy" if undeploy_workflow else ("undeploy" if action == "undeploy" else "deploy")
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
        workflow=action_workflow,
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
        default_operator_notes=_default_stage_notes(stage_name, logic),
        default_draft_definition=_default_stage_definition(pipeline, stage_name, logic),
        executor_source_files=_executor_source_files(str(pipeline.get("workflow", ""))),
    )
