"""Request-scoped active tenant (Flask g + session); CLI uses BKC_TENANT_SLUG."""

from __future__ import annotations

import os

from flask import g, has_request_context, session

DEFAULT_SLUG = "default"


def get_current_tenant_id() -> int | None:
    if has_request_context():
        tid = getattr(g, "bkc_tenant_id", None)
        if tid is not None:
            return int(tid)
    return None


def set_request_tenant(tenant_id: int, slug: str) -> None:
    g.bkc_tenant_id = int(tenant_id)
    g.bkc_tenant_slug = slug


def get_effective_tenant_slug() -> str:
    if has_request_context():
        slug = getattr(g, "bkc_tenant_slug", None)
        if slug:
            return slug
        s = session.get("tenant_slug")
        if isinstance(s, str) and s.strip():
            return s.strip().lower()
    return os.environ.get("BKC_TENANT_SLUG", DEFAULT_SLUG).strip().lower() or DEFAULT_SLUG


def set_request_tenant_slug(slug: str) -> None:
    g.bkc_tenant_slug = slug.strip().lower()
