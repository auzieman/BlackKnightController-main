"""Optional JSON access logs and X-Request-ID for container log collectors."""

from __future__ import annotations

import json
import logging
import os
import time
import uuid

from flask import Flask, Response, g, request, session
from flask_login import current_user

access_logger = logging.getLogger("bkc.access")


def _json_enabled() -> bool:
    return os.environ.get("BKC_ACCESS_LOG_FORMAT", "").strip().lower() == "json"


def register_access_correlation_and_logging(app: Flask) -> None:
    if getattr(app, "_bkc_access_correlation_registered", False):
        return
    app._bkc_access_correlation_registered = True

    if _json_enabled() and not access_logger.handlers:
        access_logger.setLevel(logging.INFO)
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(message)s"))
        access_logger.addHandler(handler)
        access_logger.propagate = False

    @app.before_request
    def bkc_request_correlation():
        g.bkc_request_id = str(uuid.uuid4())
        g.bkc_request_started = time.perf_counter()
        return None

    @app.after_request
    def bkc_after_request(response: Response):
        rid = getattr(g, "bkc_request_id", None)
        if rid:
            response.headers.setdefault("X-Request-ID", rid)

        if not _json_enabled():
            return response

        started = getattr(g, "bkc_request_started", None)
        elapsed_ms = None
        if started is not None:
            elapsed_ms = round((time.perf_counter() - started) * 1000, 2)

        user_id = None
        username = None
        if current_user.is_authenticated:
            user_id = current_user.get_id()
            from services import bkc_db

            row = bkc_db.fetch_user_by_id(int(user_id))
            if row:
                username = row.get("username")

        remote = request.headers.get("X-Forwarded-For", request.remote_addr)
        if remote and "," in remote:
            remote = remote.split(",")[0].strip()

        payload = {
            "event": "http_request",
            "request_id": rid,
            "method": request.method,
            "path": request.path,
            "query": request.query_string.decode("utf-8", errors="replace")[:500] or None,
            "status": response.status_code,
            "ms": elapsed_ms,
            "remote_addr": remote,
            "user_id": user_id,
            "username": username,
            "tenant_slug": session.get("tenant_slug"),
        }
        payload = {k: v for k, v in payload.items() if v is not None}
        access_logger.info(json.dumps(payload, sort_keys=True, default=str))
        return response
