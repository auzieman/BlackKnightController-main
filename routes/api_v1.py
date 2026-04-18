from __future__ import annotations

import os
from pathlib import Path

from flask import Blueprint, abort, current_app, g, jsonify, request
from flask_limiter.util import get_remote_address
from services import bkc_db
from services.api_key_scopes import ENDPOINT_REQUIRED_SCOPE, parse_scopes, scope_allowed
from services.health_checks import readiness_report
from services.rate_limit import limiter
from services.rules_store import load_rules
from services.tenant_context import set_request_tenant

api_blueprint = Blueprint("api_v1", __name__, url_prefix="/api/v1")

# Register @api_blueprint.route / .get before @limiter so Flask's URL rule points at the
# limiter-wrapped callable (same pattern as routes/auth.py). Reversing the stack leaves a raw view with no limits.


def install_api_v1_early_middleware(app) -> None:
    """
    Run Bearer auth + scope checks before Flask-Limiter so per-key rate limits can use g.bkc_api_key_row.
    """

    @app.before_request
    def _api_v1_auth_early():
        ep = request.endpoint
        if ep in (None, "api_v1.health_check", "api_v1.ready_check"):
            return None
        if not ep or not ep.startswith("api_v1."):
            return None
        bkc_db.init_db()
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            return jsonify({"error": "missing_or_invalid_authorization"}), 401
        raw = auth[7:].strip()
        row = bkc_db.verify_api_key(raw)
        if not row:
            return jsonify({"error": "invalid_api_key"}), 401
        tenant = bkc_db.fetch_tenant_by_id(int(row["tenant_id"]))
        if not tenant:
            return jsonify({"error": "invalid_api_key"}), 401
        set_request_tenant(int(tenant["id"]), tenant["slug"])
        g.bkc_api_key_row = dict(row)

        required = ENDPOINT_REQUIRED_SCOPE.get(ep)
        if required:
            scopes = parse_scopes(row.get("scopes"))
            if not scope_allowed(scopes, required):
                return jsonify({"error": "insufficient_scope", "required": required}), 403
        return None


def _api_bearer_rate_key() -> str:
    row = getattr(g, "bkc_api_key_row", None)
    if row and row.get("id") is not None:
        return f"bkc_apikey:{int(row['id'])}"
    return get_remote_address()


def _api_bearer_limit() -> str:
    row = getattr(g, "bkc_api_key_row", None)
    if row:
        rpm = row.get("rate_limit_per_minute")
        if rpm is not None:
            try:
                n = int(rpm)
                if n > 0:
                    return f"{n} per minute"
            except (TypeError, ValueError):
                pass
    return os.environ.get("BKC_API_KEY_RATE_LIMIT", "120 per minute").strip() or "120 per minute"


@api_blueprint.get("/health")
@limiter.exempt
def health_check():
    return jsonify({"status": "ok", "service": "bkc-ce"})


@api_blueprint.get("/ready")
@limiter.exempt
def ready_check():
    ok, body = readiness_report(Path(current_app.root_path))
    return jsonify(body), (200 if ok else 503)


@api_blueprint.get("/me")
@limiter.limit(_api_bearer_limit, key_func=_api_bearer_rate_key)
def me():
    row = g.get("bkc_api_key_row")
    if not row:
        abort(401)
    return jsonify(
        {
            "key_name": row["name"],
            "tenant_slug": row["tenant_slug"],
            "prefix": row["prefix"],
            "scopes": row.get("scopes") or "read:me,read:inventory",
        }
    )


@api_blueprint.get("/inventory")
@limiter.limit(_api_bearer_limit, key_func=_api_bearer_rate_key)
def inventory():
    return jsonify(load_rules())
