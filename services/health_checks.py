"""Liveness/readiness checks for orchestrators and reverse proxies."""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

from services import bkc_db


def check_sqlite() -> dict[str, Any]:
    try:
        conn = bkc_db.get_connection()
        try:
            conn.execute("SELECT 1").fetchone()
        finally:
            conn.close()
        return {"ok": True}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def check_redis(uri: str) -> dict[str, Any]:
    if not uri.strip().lower().startswith("redis://"):
        return {"ok": True, "skipped": "not a redis URI"}
    try:
        import redis

        client = redis.from_url(uri, socket_connect_timeout=2, socket_timeout=2)
        client.ping()
        return {"ok": True}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def check_dictionaries_volume(app_root: Path) -> dict[str, Any]:
    d = app_root / "dictionaries"
    probe = d / ".bkc_ready_write_probe"
    try:
        d.mkdir(parents=True, exist_ok=True)
        probe.write_text(str(time.time()), encoding="utf-8")
        probe.unlink(missing_ok=True)
        return {"ok": True}
    except OSError as exc:
        return {"ok": False, "error": str(exc)}


def readiness_report(app_root: Path) -> tuple[bool, dict[str, Any]]:
    """
    Returns (all_ok, body) where body is suitable for JSON ``/ready`` responses.
    """
    checks: dict[str, Any] = {}
    all_ok = True

    sqlite_r = check_sqlite()
    checks["sqlite"] = sqlite_r
    if not sqlite_r.get("ok"):
        all_ok = False

    redis_uri = (
        os.environ.get("BKC_RATELIMIT_STORAGE_URI", "").strip()
        or os.environ.get("RATELIMIT_STORAGE_URI", "").strip()
    )
    redis_r = check_redis(redis_uri) if redis_uri else {"ok": True, "skipped": "no redis configured"}
    checks["redis"] = redis_r
    if not redis_r.get("ok"):
        all_ok = False

    vol_r = check_dictionaries_volume(app_root)
    checks["dictionaries_volume"] = vol_r
    if not vol_r.get("ok"):
        all_ok = False

    body = {"status": "ready" if all_ok else "not_ready", "checks": checks}
    return all_ok, body
