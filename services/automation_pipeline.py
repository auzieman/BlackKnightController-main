from __future__ import annotations

from services.automation_runs import append_event, append_run, create_run, get_run, update_run, update_stage


def create_automation_run(
    *,
    tenant_slug: str,
    requested_by: str,
    trigger_source: str,
    repo: str,
    workflow: str,
    ref: str = "",
    commit: str = "",
    notes: str = "",
    extra: dict | None = None,
) -> dict:
    run = create_run(
        tenant_slug=tenant_slug,
        requested_by=requested_by,
        trigger_source=trigger_source,
        repo=repo,
        workflow=workflow,
        ref=ref,
        commit=commit,
        notes=notes,
        extra=extra,
    )
    return append_run(run)


def mark_run_queued(run_id: str, job_id: str = "") -> dict | None:
    def _apply(run: dict) -> None:
        run["status"] = "queued"
        if job_id:
            run.setdefault("extra", {})["job_id"] = job_id

    updated = update_run(run_id, _apply)
    append_event(run_id, "info", "queue", f"Run queued{f' as job {job_id}' if job_id else ''}.")
    return updated


def mark_run_waiting_executor(run_id: str) -> dict | None:
    def _apply(candidate: dict) -> None:
        candidate["status"] = "waiting-executor"
        candidate.setdefault("extra", {})["executor_status"] = "Pipeline registered; executor wiring pending."

    updated = update_run(run_id, _apply)
    append_event(run_id, "info", "executor", "Executor accepted the run and is waiting for stage handlers.")
    return updated


def mark_run_blocked(run_id: str, detail: str) -> dict | None:
    if run := get_run(run_id):
        first_stage = ""
        if run.get("stages"):
            first_stage = str(run["stages"][0].get("name", ""))
        if first_stage:
            update_stage(run_id, first_stage, "failed", detail)

    def _apply(candidate: dict) -> None:
        candidate["status"] = "blocked"
        candidate.setdefault("extra", {})["executor_status"] = detail

    updated = update_run(run_id, _apply)
    append_event(run_id, "error", "queue", detail)
    return updated


def mark_run_active(run_id: str, detail: str = "") -> dict | None:
    def _apply(candidate: dict) -> None:
        candidate["status"] = "running"
        if detail:
            candidate.setdefault("extra", {})["executor_status"] = detail

    updated = update_run(run_id, _apply)
    if detail:
        append_event(run_id, "info", "executor", detail)
    return updated


def mark_run_complete(run_id: str, detail: str = "") -> dict | None:
    def _apply(candidate: dict) -> None:
        candidate["status"] = "complete"
        if detail:
            candidate.setdefault("extra", {})["executor_status"] = detail

    updated = update_run(run_id, _apply)
    if detail:
        append_event(run_id, "info", "pipeline", detail)
    return updated


def mark_run_failed(run_id: str, detail: str, stage_name: str = "") -> dict | None:
    if stage_name:
        update_stage(run_id, stage_name, "failed", detail)

    def _apply(candidate: dict) -> None:
        candidate["status"] = "failed"
        candidate.setdefault("extra", {})["executor_status"] = detail

    updated = update_run(run_id, _apply)
    append_event(run_id, "error", stage_name or "pipeline", detail)
    return updated
