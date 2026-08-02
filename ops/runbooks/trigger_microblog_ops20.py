"""Queue the MICROBLOG OPS 20 lab canary pipeline from inside a BKC container.

This helper is intentionally small and inspectable. It is meant for the lab
control path where web login/session state is not the thing being tested; the
result is still a real BKC automation run processed by the worker.
"""

from __future__ import annotations

import json
import os


def main() -> None:
    tenant_slug = os.environ.get("BKC_PIPELINE_TENANT", "default").strip() or "default"
    os.environ["BKC_TENANT_SLUG"] = tenant_slug

    import bkc_server
    from services.automation_pipeline import create_automation_run, mark_run_blocked, mark_run_queued
    from services.job_queue import enqueue_job, job_queue_enabled
    from services.pipeline_catalog import pipeline_by_id
    from services.pipeline_executor import workflow_is_supported, workflow_job_timeout

    pipeline_id = os.environ.get("BKC_PIPELINE_ID", "micro-blog-esxi-lab-canary-refresh").strip()
    if not pipeline_id:
        pipeline_id = "micro-blog-esxi-lab-canary-refresh"
    app = bkc_server.app
    with app.app_context():
        pipeline = pipeline_by_id(pipeline_id)
        if not pipeline:
            raise SystemExit(f"missing pipeline {pipeline_id}")

        workflow = str(pipeline.get("workflow") or "")
        if not workflow_is_supported(workflow):
            raise SystemExit(f"workflow not supported: {workflow}")

        extra = {
            "pipeline_id": pipeline["id"],
            "pipeline_name": pipeline["name"],
            "resource_class": pipeline.get("resource_class", "standard"),
            "action_mode": "deploy",
        }
        run = create_automation_run(
            tenant_slug=tenant_slug,
            requested_by="codex:pipeline-real-run",
            trigger_source="codex-internal",
            repo=pipeline.get("repo", "BlackKnightController"),
            workflow=workflow,
            ref=os.environ.get("BKC_PIPELINE_REF", "refs/heads/beta/company-mind-workbench-20260726"),
            commit=os.environ.get("BKC_PIPELINE_COMMIT", "unknown"),
            notes=pipeline.get("notes", ""),
            extra=extra,
        )

        if not job_queue_enabled():
            print(json.dumps({"run_id": run["id"], "status": "created-no-queue"}, sort_keys=True))
            return

        try:
            job = enqueue_job(
                "services.job_tasks.automation_pipeline_job",
                (run["id"], tenant_slug, None, None, "codex-internal"),
                job_timeout=workflow_job_timeout(workflow, action_mode="deploy"),
                queue_name="bkc",
                meta={
                    "kind": "automation",
                    "run_id": run["id"],
                    "tenant_slug": tenant_slug,
                    "repo": run["repo"],
                    "workflow": workflow,
                    "queue_name": "bkc",
                },
            )
            mark_run_queued(run["id"], job.id)
            print(json.dumps({"run_id": run["id"], "job_id": job.id, "status": "queued"}, sort_keys=True))
        except Exception as exc:  # noqa: BLE001
            mark_run_blocked(run["id"], f"Queue backend unavailable: {exc}")
            raise


if __name__ == "__main__":
    main()
