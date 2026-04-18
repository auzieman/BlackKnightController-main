"""
Pytest bootstrap: isolated SQLite DB and a single fully-wired Flask app (same as production imports).

DB lives in a temp directory for the session so tests never touch dictionaries/bkc.db.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

import pytest


def pytest_configure(config: pytest.Config) -> None:
    root = Path(tempfile.mkdtemp(prefix="bkc_pytest_"))
    config._bkc_test_root = root  # type: ignore[attr-defined]

    os.environ.setdefault("BKC_SECRET_KEY", "pytest-bkc-secret")
    os.environ.setdefault("BKC_SECRET_KEY_OVERRIDE", "pytest-bkc-secret")
    # Avoid optional Redis readiness failures in /api/v1/ready during tests
    os.environ.pop("BKC_RATELIMIT_STORAGE_URI", None)
    os.environ.pop("RATELIMIT_STORAGE_URI", None)

    import services.bkc_db as bkc_db

    bkc_db.DB_PATH = root / "bkc.db"

    import app as app_module

    app_module.app.config["TESTING"] = True
    # Flask-Limiter is off by default when TESTING is true; turn it back on so API rate-limit tests work.
    app_module.app.config["RATELIMIT_ENABLED"] = True
    app_module.app.config["WTF_CSRF_ENABLED"] = False

    import bkc_server  # noqa: F401 — register blueprints + CE (side effect)


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    root = getattr(session.config, "_bkc_test_root", None)
    if root and Path(root).exists():
        shutil.rmtree(root, ignore_errors=True)


@pytest.fixture
def app():
    from bkc_server import app as application

    return application


@pytest.fixture
def client(app):
    return app.test_client()


def _wipe_ce_tables(bkc_db) -> None:
    """Clear CE data without deleting the DB file (avoids Windows file locks on sqlite)."""
    with bkc_db.get_connection() as conn:
        conn.executescript(
            """
            PRAGMA foreign_keys = OFF;
            DELETE FROM audit_log;
            DELETE FROM api_keys;
            DELETE FROM memberships;
            DELETE FROM users;
            DELETE FROM tenants;
            PRAGMA foreign_keys = ON;
            """
        )
        conn.commit()


@pytest.fixture
def fresh_ce_db():
    """Default tenant only (no users, no API keys). Works on Windows where unlink() may fail on open DBs."""
    import services.bkc_db as bkc_db

    if bkc_db.DB_PATH.exists():
        try:
            bkc_db.DB_PATH.unlink()
        except OSError:
            _wipe_ce_tables(bkc_db)
    bkc_db.init_db()
    yield
