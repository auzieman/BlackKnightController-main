"""Optional Redis Queue (RQ) for long-running tasks (worker runs in separate container)."""

from __future__ import annotations

import os
from typing import Any

from redis import Redis
from rq import Queue

QUEUE_NAME = "bkc"


def job_queue_url() -> str:
    return os.environ.get("BKC_JOB_QUEUE_URL", "").strip()


def job_queue_enabled() -> bool:
    return bool(job_queue_url())


def redis_connection() -> Redis:
    url = job_queue_url()
    if not url:
        raise RuntimeError("BKC_JOB_QUEUE_URL is not set.")
    return Redis.from_url(url)


def get_queue() -> Queue:
    return Queue(QUEUE_NAME, connection=redis_connection())


def enqueue_job(
    function_path: str,
    args: tuple[Any, ...] = (),
    *,
    job_timeout: int = 900,
    meta: dict[str, Any] | None = None,
) -> Any:
    """Enqueue by import path string so the worker process can unpickle."""
    q = get_queue()
    return q.enqueue(function_path, *args, job_timeout=job_timeout, meta=meta or {})
