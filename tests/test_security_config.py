"""Session and response header defaults (services.security_config)."""

from __future__ import annotations

from flask import Flask
from services.security_config import apply_session_cookie_hardening, register_security_headers


def _app():
    return Flask(__name__)


def test_session_defaults_insecure_local():
    app = _app()
    apply_session_cookie_hardening(app, {})
    assert app.config["SESSION_COOKIE_SECURE"] is False
    assert app.config["SESSION_COOKIE_HTTPONLY"] is True
    assert app.config["SESSION_COOKIE_SAMESITE"] == "Lax"


def test_session_secure_env():
    app = _app()
    apply_session_cookie_hardening(app, {"BKC_SESSION_COOKIE_SECURE": "1"})
    assert app.config["SESSION_COOKIE_SECURE"] is True


def test_session_trusted_https_alias():
    app = _app()
    apply_session_cookie_hardening(app, {"BKC_TRUSTED_HTTPS": "true"})
    assert app.config["SESSION_COOKIE_SECURE"] is True


def test_session_samesite_strict():
    app = _app()
    apply_session_cookie_hardening(app, {"BKC_SESSION_SAMESITE": "Strict"})
    assert app.config["SESSION_COOKIE_SAMESITE"] == "Strict"


def test_session_samesite_none_requires_secure():
    app = _app()
    apply_session_cookie_hardening(app, {"BKC_SESSION_SAMESITE": "none"})
    assert app.config["SESSION_COOKIE_SAMESITE"] == "Lax"
    apply_session_cookie_hardening(
        app,
        {"BKC_SESSION_SAMESITE": "none", "BKC_SESSION_COOKIE_SECURE": "1"},
    )
    assert app.config["SESSION_COOKIE_SAMESITE"] == "None"


def test_security_headers_on_response():
    app = _app()
    register_security_headers(app, {})

    @app.get("/t")
    def t():
        return "ok"

    c = app.test_client()
    r = c.get("/t")
    assert r.headers.get("X-Content-Type-Options") == "nosniff"
    assert r.headers.get("X-Frame-Options") == "SAMEORIGIN"
    assert r.headers.get("Referrer-Policy") == "strict-origin-when-cross-origin"
    assert "Permissions-Policy" in r.headers


def test_security_headers_disabled():
    app = _app()
    register_security_headers(app, {"BKC_DISABLE_SECURITY_HEADERS": "1"})

    @app.get("/t")
    def t():
        return "ok"

    c = app.test_client()
    r = c.get("/t")
    assert r.headers.get("X-Content-Type-Options") is None
