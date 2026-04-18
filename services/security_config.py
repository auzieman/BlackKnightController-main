"""Session cookie flags and baseline HTTP security headers for CE deployments."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from flask import Flask


def _truthy(val: str | None) -> bool:
    if not val:
        return False
    return val.strip().lower() in ("1", "true", "yes", "on")


def apply_session_cookie_hardening(app: Flask, environ: dict) -> None:
    """
    Harden Flask session cookies. Behind TLS, set ``BKC_SESSION_COOKIE_SECURE=1``
    (or ``BKC_TRUSTED_HTTPS=1``). ``BKC_SESSION_SAMESITE`` may be ``Lax`` (default),
    ``Strict``, or ``None`` (``None`` is downgraded to ``Lax`` unless Secure is enabled).
    """
    secure = _truthy(environ.get("BKC_SESSION_COOKIE_SECURE")) or _truthy(environ.get("BKC_TRUSTED_HTTPS"))
    app.config["SESSION_COOKIE_SECURE"] = bool(secure)
    app.config["SESSION_COOKIE_HTTPONLY"] = True

    raw = (environ.get("BKC_SESSION_SAMESITE") or "Lax").strip().lower()
    if raw == "strict":
        app.config["SESSION_COOKIE_SAMESITE"] = "Strict"
    elif raw == "none":
        if secure:
            app.config["SESSION_COOKIE_SAMESITE"] = "None"
        else:
            app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    else:
        app.config["SESSION_COOKIE_SAMESITE"] = "Lax"


def register_security_headers(app: Flask, environ: dict) -> None:
    """Best-effort headers; disable with ``BKC_DISABLE_SECURITY_HEADERS=1``."""
    if _truthy(environ.get("BKC_DISABLE_SECURITY_HEADERS")):
        return

    xfo = (environ.get("BKC_SECURITY_X_FRAME_OPTIONS") or "SAMEORIGIN").strip() or "SAMEORIGIN"

    @app.after_request
    def _security_headers(response):  # type: ignore[unused-ignore]
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", xfo)
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault(
            "Permissions-Policy",
            "accelerometer=(), camera=(), geolocation=(), gyroscope=(), magnetometer=(), "
            "microphone=(), payment=(), usb=()",
        )
        return response
