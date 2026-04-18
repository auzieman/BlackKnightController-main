import csv
import io
import json
import sqlite3
from datetime import datetime, timezone

from flask import Blueprint, Response, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from services import bkc_db
from services.access_control import require_superuser

settings_blueprint = Blueprint("settings", __name__, url_prefix="/settings")


@settings_blueprint.get("/audit/export")
@login_required
@require_superuser
def audit_export():
    fmt = (request.args.get("format") or "json").lower()
    try:
        limit = int(request.args.get("limit") or 50_000)
    except ValueError:
        limit = 50_000
    rows = bkc_db.recent_audit(limit)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    if fmt == "json":
        body = json.dumps(rows, indent=2, sort_keys=True, default=str) + "\n"
        return Response(
            body,
            mimetype="application/json; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="bkc-audit-{stamp}.json"'},
        )
    if fmt == "csv":
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(
            ["id", "created_at", "user_id", "username", "tenant_id", "action", "resource", "detail", "ip"]
        )
        for row in rows:
            detail = row.get("detail")
            if detail is not None and not isinstance(detail, str):
                detail = json.dumps(detail, default=str)
            writer.writerow(
                [
                    row.get("id"),
                    row.get("created_at"),
                    row.get("user_id"),
                    row.get("username") or "",
                    row.get("tenant_id"),
                    row.get("action"),
                    row.get("resource"),
                    detail or "",
                    row.get("ip") or "",
                ]
            )
        return Response(
            buf.getvalue(),
            mimetype="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="bkc-audit-{stamp}.csv"'},
        )
    abort(400)


@settings_blueprint.route("/", methods=["GET"])
@login_required
@require_superuser
def settings_home():
    users = bkc_db.list_users()
    tenants = bkc_db.list_tenants()
    api_keys = bkc_db.list_all_api_keys()
    audit = bkc_db.recent_audit(150)
    return render_template(
        "settings.html.j2",
        users=users,
        tenants=tenants,
        api_keys=api_keys,
        audit=audit,
    )


@settings_blueprint.route("/users", methods=["POST"])
@login_required
@require_superuser
def create_user():
    username = (request.form.get("username") or "").strip()
    password = request.form.get("password") or ""
    role = (request.form.get("role") or "viewer").strip().lower()
    tenant_id = int(request.form.get("tenant_id") or 0)
    is_super = request.form.get("is_superuser") == "on"
    if role not in ("viewer", "operator", "owner"):
        flash("Invalid role.", "error")
        return redirect(url_for("settings.settings_home"))
    if not username or not password:
        flash("Username and password are required.", "error")
        return redirect(url_for("settings.settings_home"))
    if bkc_db.fetch_user_by_username(username):
        flash("That username already exists.", "error")
        return redirect(url_for("settings.settings_home"))
    tenant = bkc_db.fetch_tenant_by_id(tenant_id)
    if not tenant:
        flash("Invalid tenant.", "error")
        return redirect(url_for("settings.settings_home"))
    uid = bkc_db.create_user(username, password, is_superuser=is_super)
    bkc_db.set_membership(uid, tenant_id, role)
    bkc_db.append_audit(
        int(current_user.id),
        tenant_id,
        "settings.user_create",
        f"user:{uid}",
        {"username": username, "role": role},
        request.remote_addr,
    )
    flash(f"Created user {username!r}.")
    return redirect(url_for("settings.settings_home"))


@settings_blueprint.route("/membership", methods=["POST"])
@login_required
@require_superuser
def set_membership_route():
    user_id = int(request.form.get("user_id") or 0)
    tenant_id = int(request.form.get("tenant_id") or 0)
    role = (request.form.get("role") or "viewer").strip().lower()
    if role not in ("viewer", "operator", "owner"):
        abort(400)
    if not bkc_db.fetch_user_by_id(user_id) or not bkc_db.fetch_tenant_by_id(tenant_id):
        flash("Invalid user or tenant.", "error")
        return redirect(url_for("settings.settings_home"))
    bkc_db.set_membership(user_id, tenant_id, role)
    bkc_db.append_audit(
        int(current_user.id),
        tenant_id,
        "settings.membership_set",
        f"user:{user_id}",
        {"role": role},
        request.remote_addr,
    )
    flash("Membership updated.")
    return redirect(url_for("settings.settings_home"))


@settings_blueprint.route("/tenants", methods=["POST"])
@login_required
@require_superuser
def create_tenant():
    name = (request.form.get("name") or "").strip()
    slug = (request.form.get("slug") or "").strip()
    if not name or not slug:
        flash("Tenant name and slug are required.", "error")
        return redirect(url_for("settings.settings_home"))
    try:
        tid = bkc_db.create_tenant(name, slug)
    except sqlite3.IntegrityError:
        flash("That tenant slug is already in use.", "error")
        return redirect(url_for("settings.settings_home"))
    except Exception as exc:
        flash(f"Could not create tenant: {exc}", "error")
        return redirect(url_for("settings.settings_home"))
    bkc_db.append_audit(
        int(current_user.id),
        tid,
        "settings.tenant_create",
        f"tenant:{tid}",
        {"name": name, "slug": slug},
        request.remote_addr,
    )
    flash(f"Created tenant {slug!r}.")
    return redirect(url_for("settings.settings_home"))


@settings_blueprint.route("/api-keys", methods=["POST"])
@login_required
@require_superuser
def create_api_key():
    name = (request.form.get("name") or "default").strip()
    tenant_id = int(request.form.get("tenant_id") or 0)
    if not bkc_db.fetch_tenant_by_id(tenant_id):
        flash("Invalid tenant for API key.", "error")
        return redirect(url_for("settings.settings_home"))
    if request.form.get("scope_all"):
        scopes_str = "*"
    else:
        bits: list[str] = []
        if request.form.get("scope_me"):
            bits.append("read:me")
        if request.form.get("scope_inventory"):
            bits.append("read:inventory")
        scopes_str = ",".join(bits) if bits else "read:me,read:inventory"
    rpm_raw = (request.form.get("rate_limit_per_minute") or "").strip()
    rate_limit: int | None = None
    if rpm_raw:
        try:
            n = int(rpm_raw)
            if n <= 0:
                raise ValueError
            rate_limit = n
        except ValueError:
            flash("Rate limit must be a positive integer or left blank for the default.", "error")
            return redirect(url_for("settings.settings_home"))
    raw, kid = bkc_db.create_api_key(
        tenant_id,
        name,
        int(current_user.id),
        scopes=scopes_str,
        rate_limit_per_minute=rate_limit,
    )
    bkc_db.append_audit(
        int(current_user.id),
        tenant_id,
        "settings.api_key_create",
        f"api_key:{kid}",
        {"name": name, "scopes": scopes_str, "rate_limit_per_minute": rate_limit},
        request.remote_addr,
    )
    flash(f"API key created. Copy it now; it will not be shown again: {raw}", "error")
    return redirect(url_for("settings.settings_home"))
