"""CE bootstrap: DB, auth, CSRF, rate limits, tenant binding, template context."""

from __future__ import annotations

import logging
import secrets
from pathlib import Path

from flask import Flask, abort, redirect, request, session, url_for
from flask_login import LoginManager, current_user
from flask_wtf.csrf import CSRFProtect
from services import bkc_db
from services.access_control import permission_flags
from services.access_json_log import register_access_correlation_and_logging
from services.auth_user import BKCUser
from services.job_queue import job_queue_enabled
from services.rate_limit import limiter
from services.security_config import apply_session_cookie_hardening, register_security_headers
from services.tenant_context import get_current_tenant_id, set_request_tenant

logger = logging.getLogger(__name__)

login_manager = LoginManager()
csrf = CSRFProtect()

_ce_initialized = False
_bootstrap_logged = False


def _secret_key(app: Flask) -> str:
    env = app.config.get("BKC_SECRET_KEY_OVERRIDE")
    if env:
        return str(env)
    key_path = Path(app.root_path) / "keys" / "bkc_flask_secret"
    if key_path.exists():
        raw = key_path.read_text(encoding="utf-8").strip()
        if raw:
            return raw
    key_path.parent.mkdir(parents=True, exist_ok=True)
    raw = secrets.token_hex(32)
    key_path.write_text(raw + "\n", encoding="utf-8")
    try:
        key_path.chmod(0o600)
    except OSError:
        pass
    return raw


def _ensure_db() -> None:
    global _bootstrap_logged
    bkc_db.init_db()
    msg = bkc_db.bootstrap_admin_if_configured()
    if msg and not _bootstrap_logged:
        logger.warning("%s", msg)
        _bootstrap_logged = True


def _bind_tenant_for_ui() -> None:
    user_id = int(current_user.id)
    user_row = bkc_db.fetch_user_by_id(user_id)
    if not user_row:
        abort(401)
    slug = (session.get("tenant_slug") or "default").strip().lower()
    tenant = bkc_db.fetch_tenant_by_slug(slug)
    if not tenant:
        tenant = bkc_db.fetch_tenant_by_slug("default")
        session["tenant_slug"] = "default"
    tid = int(tenant["id"])
    role = bkc_db.membership_role(user_id, tid)
    if not role and not user_row.get("is_superuser"):
        memberships = bkc_db.list_memberships(user_id)
        if not memberships:
            abort(403)
        first = memberships[0]
        session["tenant_slug"] = first["slug"]
        tenant = bkc_db.fetch_tenant_by_slug(first["slug"])
        tid = int(tenant["id"])
    set_request_tenant(tid, tenant["slug"])


def init_ce_app(app: Flask) -> None:
    global _ce_initialized
    if _ce_initialized:
        return

    import os

    from routes.api_v1 import install_api_v1_early_middleware

    install_api_v1_early_middleware(app)

    sk = os.environ.get("BKC_SECRET_KEY", "").strip() or _secret_key(app)
    app.config["SECRET_KEY"] = sk
    app.config.setdefault("WTF_CSRF_TIME_LIMIT", None)
    apply_session_cookie_hardening(app, os.environ)
    register_security_headers(app, os.environ)

    ratelimit_uri = (
        os.environ.get("BKC_RATELIMIT_STORAGE_URI", "").strip()
        or os.environ.get("RATELIMIT_STORAGE_URI", "").strip()
    )
    if ratelimit_uri:
        app.config["RATELIMIT_STORAGE_URI"] = ratelimit_uri
        logger.info("Flask-Limiter using configured storage backend (multi-container safe).")
    else:
        app.config.setdefault("RATELIMIT_STORAGE_URI", "memory://")
        logger.warning(
            "Flask-Limiter using in-memory storage. Set BKC_RATELIMIT_STORAGE_URI (e.g. redis://redis:6379/0) "
            "when running more than one BKC worker or multiple containers."
        )

    register_access_correlation_and_logging(app)

    if os.environ.get("BKC_BEHIND_PROXY", "").strip().lower() in ("1", "true", "yes", "on"):
        from werkzeug.middleware.proxy_fix import ProxyFix

        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1, x_prefix=1)  # type: ignore[method-assign]
        logger.info("ProxyFix enabled (trust X-Forwarded-* from reverse proxy).")

    login_manager.init_app(app)
    login_manager.login_view = "auth.login"
    login_manager.session_protection = "strong"

    csrf.init_app(app)
    limiter.init_app(app)

    @login_manager.user_loader
    def load_user(user_id: str):
        if not user_id:
            return None
        row = bkc_db.fetch_user_by_id(int(user_id))
        if not row or not row.get("is_active"):
            return None
        return BKCUser(row["id"])

    @app.before_request
    def ce_require_login_and_tenant():
        _ensure_db()
        ep = request.endpoint
        if ep in (None, "auth.login"):
            return None
        if ep and ep.startswith("static"):
            return None
        if ep and ep.startswith("api_v1."):
            if ep in ("api_v1.health_check", "api_v1.ready_check"):
                return None
            return None
        if ep == "health_public.ready":
            return None

        if not current_user.is_authenticated:
            return redirect(url_for("auth.login", next=request.url))
        _bind_tenant_for_ui()
        return None

    @app.context_processor
    def ce_template_globals():
        tid = get_current_tenant_id()
        memberships: list[dict] = []
        user_row = None
        all_tenants: list[dict] = []
        username = ""
        resource_nav_tree: list[dict] = []
        if current_user.is_authenticated:
            uid = int(current_user.id)
            memberships = bkc_db.list_memberships(uid)
            user_row = bkc_db.fetch_user_by_id(uid)
            if user_row:
                username = user_row.get("username") or ""
            if user_row and user_row.get("is_superuser"):
                all_tenants = bkc_db.list_tenants()
            try:
                from services.resource_graph import build_resource_graph

                resource_nav_tree = build_resource_graph().get("tree", [])
            except Exception:
                resource_nav_tree = []
        return {
            "bkc_tenant_id": tid,
            "bkc_tenant_slug": session.get("tenant_slug", "default"),
            "bkc_memberships": memberships,
            "bkc_perm": permission_flags(tid),
            "bkc_user": user_row,
            "bkc_all_tenants": all_tenants,
            "bkc_username": username,
            "bkc_job_queue": job_queue_enabled(),
            "bkc_resource_nav_tree": resource_nav_tree,
        }

    _ce_initialized = True
