"""Readiness JSON shape (no external Redis in test env)."""

from __future__ import annotations


def test_api_ready_json_shape(client, fresh_ce_db):
    r = client.get("/api/v1/ready")
    assert r.status_code == 200
    body = r.get_json()
    assert body["status"] == "ready"
    assert "checks" in body
    assert body["checks"]["sqlite"]["ok"] is True
    assert body["checks"]["redis"].get("ok") or body["checks"]["redis"].get("skipped")
