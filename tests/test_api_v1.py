"""HTTP tests for /api/v1 (auth, scopes, rate limits)."""

from __future__ import annotations

import pytest
from services import bkc_db


@pytest.fixture
def api_setup(fresh_ce_db):
    default = bkc_db.fetch_tenant_by_slug("default")
    assert default is not None
    tid = int(default["id"])
    uid = bkc_db.create_user("apiuser", "not-used-here", is_superuser=False)
    bkc_db.set_membership(uid, tid, "owner")
    return {"tenant_id": tid, "user_id": uid}


def test_health_no_auth(client):
    r = client.get("/api/v1/health")
    assert r.status_code == 200
    body = r.get_json()
    assert body["status"] == "ok"
    assert r.headers.get("X-Content-Type-Options") == "nosniff"


def test_me_missing_bearer(client, fresh_ce_db):
    r = client.get("/api/v1/me")
    assert r.status_code == 401
    assert r.get_json()["error"] == "missing_or_invalid_authorization"


def test_me_invalid_key(client, fresh_ce_db):
    r = client.get("/api/v1/me", headers={"Authorization": "Bearer bkc_not_a_real_key"})
    assert r.status_code == 401
    assert r.get_json()["error"] == "invalid_api_key"


def test_me_and_inventory_happy_path(client, api_setup):
    tid = api_setup["tenant_id"]
    uid = api_setup["user_id"]
    raw, _kid = bkc_db.create_api_key(tid, "full", uid, scopes="read:me,read:inventory")
    h = {"Authorization": f"Bearer {raw}"}

    r_me = client.get("/api/v1/me", headers=h)
    assert r_me.status_code == 200
    j = r_me.get_json()
    assert j["tenant_slug"] == "default"
    assert "read:me" in j["scopes"]

    r_inv = client.get("/api/v1/inventory", headers=h)
    assert r_inv.status_code == 200
    assert "groups" in r_inv.get_json()


def test_inventory_forbidden_without_scope(client, api_setup):
    tid = api_setup["tenant_id"]
    uid = api_setup["user_id"]
    raw, _kid = bkc_db.create_api_key(tid, "me-only", uid, scopes="read:me")
    r = client.get("/api/v1/inventory", headers={"Authorization": f"Bearer {raw}"})
    assert r.status_code == 403
    assert r.get_json()["error"] == "insufficient_scope"
    assert r.get_json()["required"] == "read:inventory"


def test_inventory_allowed_with_star_scope(client, api_setup):
    tid = api_setup["tenant_id"]
    uid = api_setup["user_id"]
    raw, _kid = bkc_db.create_api_key(tid, "admin-ish", uid, scopes="*")
    r = client.get("/api/v1/inventory", headers={"Authorization": f"Bearer {raw}"})
    assert r.status_code == 200


def test_me_rate_limit_env(client, api_setup, monkeypatch):
    monkeypatch.setenv("BKC_API_KEY_RATE_LIMIT", "2 per minute")
    tid = api_setup["tenant_id"]
    uid = api_setup["user_id"]
    raw, _kid = bkc_db.create_api_key(tid, "rl", uid, scopes="read:me")
    h = {"Authorization": f"Bearer {raw}"}
    assert client.get("/api/v1/me", headers=h).status_code == 200
    assert client.get("/api/v1/me", headers=h).status_code == 200
    r3 = client.get("/api/v1/me", headers=h)
    assert r3.status_code == 429
