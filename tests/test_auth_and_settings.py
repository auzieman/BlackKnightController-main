"""Login gate and superuser access to /settings."""

from __future__ import annotations

import pytest
from services import bkc_db


@pytest.fixture
def superuser_setup(fresh_ce_db):
    default = bkc_db.fetch_tenant_by_slug("default")
    assert default is not None
    tid = int(default["id"])
    uid = bkc_db.create_user("super1", "super-secret-pass", is_superuser=True)
    bkc_db.set_membership(uid, tid, "owner")
    return {"username": "super1", "password": "super-secret-pass"}


def test_settings_redirects_when_anonymous(client, fresh_ce_db):
    r = client.get("/settings/", follow_redirects=False)
    assert r.status_code == 302
    assert "/login" in r.headers.get("Location", "")


def test_settings_ok_for_superuser(client, superuser_setup):
    creds = superuser_setup
    r = client.post(
        "/login",
        data={"username": creds["username"], "password": creds["password"]},
        follow_redirects=False,
    )
    assert r.status_code in (302, 303)
    r2 = client.get("/settings/")
    assert r2.status_code == 200
    assert b"Platform settings" in r2.data or b"API keys" in r2.data


def test_settings_forbidden_for_non_superuser(client, fresh_ce_db):
    default = bkc_db.fetch_tenant_by_slug("default")
    tid = int(default["id"])
    uid = bkc_db.create_user("plain", "plain-pass", is_superuser=False)
    bkc_db.set_membership(uid, tid, "owner")
    client.post("/login", data={"username": "plain", "password": "plain-pass"})
    r = client.get("/settings/")
    assert r.status_code == 403
