from __future__ import annotations

import bkc_worker
from services import job_queue


def test_worker_queue_names_default_and_override(monkeypatch):
    monkeypatch.delenv("BKC_WORKER_QUEUES", raising=False)
    assert bkc_worker.worker_queue_names() == ["bkc"]

    monkeypatch.setenv("BKC_WORKER_QUEUES", "bkc-slow, bkc")
    assert bkc_worker.worker_queue_names() == ["bkc-slow", "bkc"]


def test_enqueue_job_selects_requested_queue(monkeypatch):
    calls = {}

    class FakeQueue:
        def enqueue(self, function_path, *args, **kwargs):
            calls.update(function_path=function_path, args=args, kwargs=kwargs)
            return object()

    monkeypatch.setattr(job_queue, "get_queue", lambda name: (calls.update(queue_name=name) or FakeQueue()))
    job_queue.enqueue_job("tasks.build", ("manifest.json",), queue_name="bkc-slow", job_timeout=3600)

    assert calls["queue_name"] == "bkc-slow"
    assert calls["function_path"] == "tasks.build"
    assert calls["kwargs"]["job_timeout"] == 3600
