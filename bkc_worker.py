"""
RQ worker — run in a separate container or process.

Requires BKC_JOB_QUEUE_URL (e.g. redis://redis:6379/2).

Example (Compose): command is set on the bkc-worker service.
"""

from __future__ import annotations

import services.job_tasks  # noqa: F401 — ensure task modules load
from redis import Redis
from rq import Connection, Worker
from services import bkc_db
from services.job_queue import QUEUE_NAME, job_queue_url


def main() -> None:
    url = job_queue_url()
    if not url:
        raise SystemExit("BKC_JOB_QUEUE_URL must be set for the worker (e.g. redis://redis:6379/2).")
    bkc_db.init_db()
    redis = Redis.from_url(url)
    with Connection(redis):
        Worker([QUEUE_NAME]).work(with_scheduler=False)


if __name__ == "__main__":
    main()
