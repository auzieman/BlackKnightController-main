"""Superuser POST flows on /settings (CSRF off in test app)."""

from __future__ import annotations

from services import bkc_db


def test_create_tenant_via_settings(client, fresh_ce_db):
    default = bkc_db.fetch_tenant_by_slug("default")
    uid = bkc_db.create_user("owner1", "pw-owner", is_superuser=True)
    bkc_db.set_membership(uid, int(default["id"]), "owner")

    client.post("/login", data={"username": "owner1", "password": "pw-owner"})
    slug = "acme-ci"
    r = client.post(
        "/settings/tenants",
        data={"name": "Acme CI", "slug": slug},
        follow_redirects=False,
    )
    assert r.status_code in (302, 303)
    row = bkc_db.fetch_tenant_by_slug(slug)
    assert row is not None
    assert row["name"] == "Acme CI"


def test_create_user_via_settings(client, fresh_ce_db):
    default = bkc_db.fetch_tenant_by_slug("default")
    tid = int(default["id"])
    su = bkc_db.create_user("su2", "pw-su", is_superuser=True)
    bkc_db.set_membership(su, tid, "owner")

    client.post("/login", data={"username": "su2", "password": "pw-su"})
    r = client.post(
        "/settings/users",
        data={
            "username": "newviewer",
            "password": "viewer-pass-9",
            "role": "viewer",
            "tenant_id": str(tid),
        },
        follow_redirects=False,
    )
    assert r.status_code in (302, 303)
    u = bkc_db.verify_user("newviewer", "viewer-pass-9")
    assert u is not None
    assert bkc_db.membership_role(int(u["id"]), tid) == "viewer"
