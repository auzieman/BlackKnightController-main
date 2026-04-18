"""SQLite persistence for CE: users, tenants, RBAC, API keys, audit."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from werkzeug.security import check_password_hash, generate_password_hash

BASE_DIR = Path(__file__).resolve().parents[1]
DB_PATH = BASE_DIR / "dictionaries" / "bkc.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS tenants (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    slug TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1,
    is_superuser INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS memberships (
    user_id INTEGER NOT NULL,
    tenant_id INTEGER NOT NULL,
    role TEXT NOT NULL,
    PRIMARY KEY (user_id, tenant_id),
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS api_keys (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    key_hash TEXT NOT NULL,
    prefix TEXT NOT NULL,
    created_by INTEGER,
    created_at TEXT NOT NULL,
    scopes TEXT NOT NULL DEFAULT 'read:me,read:inventory',
    rate_limit_per_minute INTEGER,
    FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE,
    FOREIGN KEY (created_by) REFERENCES users(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    user_id INTEGER,
    tenant_id INTEGER,
    action TEXT NOT NULL,
    resource TEXT NOT NULL,
    detail TEXT,
    ip TEXT,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL,
    FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_log(created_at);
CREATE INDEX IF NOT EXISTS idx_memberships_tenant ON memberships(tenant_id);
"""


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def get_connection() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, detect_types=sqlite3.PARSE_DECLTYPES)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _migrate_api_keys_columns(conn: sqlite3.Connection) -> None:
    cols = {row[1] for row in conn.execute("PRAGMA table_info(api_keys)").fetchall()}
    if "scopes" not in cols:
        conn.execute("ALTER TABLE api_keys ADD COLUMN scopes TEXT")
        conn.execute(
            "UPDATE api_keys SET scopes = 'read:me,read:inventory' WHERE scopes IS NULL OR scopes = ''",
        )
    if "rate_limit_per_minute" not in cols:
        conn.execute("ALTER TABLE api_keys ADD COLUMN rate_limit_per_minute INTEGER")


def init_db() -> None:
    with get_connection() as conn:
        conn.executescript(SCHEMA)
        _migrate_api_keys_columns(conn)
        row = conn.execute("SELECT COUNT(*) AS c FROM tenants").fetchone()
        if row and row["c"] == 0:
            now = utc_now_iso()
            conn.execute(
                "INSERT INTO tenants (name, slug, created_at) VALUES (?, ?, ?)",
                ("Home", "default", now),
            )
        conn.commit()


def count_users(conn: sqlite3.Connection | None = None) -> int:
    own = conn is None
    if own:
        conn = get_connection()
    try:
        row = conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()
        return int(row["c"]) if row else 0
    finally:
        if own:
            conn.close()


def bootstrap_admin_if_configured() -> str | None:
    """
    If the database has no users and BKC_BOOTSTRAP_ADMIN_PASSWORD is set,
    create the default tenant (if needed), admin user, and owner+superuser flags.
    Returns a status message for logs, or None.
    """
    password = os.environ.get("BKC_BOOTSTRAP_ADMIN_PASSWORD", "").strip()
    if not password:
        return None
    init_db()
    with get_connection() as conn:
        if conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"] > 0:
            return None
        username = os.environ.get("BKC_BOOTSTRAP_ADMIN_USERNAME", "admin").strip() or "admin"
        now = utc_now_iso()
        ph = generate_password_hash(password)
        cur = conn.execute(
            "INSERT INTO users (username, password_hash, is_active, is_superuser, created_at) "
            "VALUES (?, ?, 1, 1, ?)",
            (username, ph, now),
        )
        user_id = cur.lastrowid
        tenant_row = conn.execute(
            "SELECT id FROM tenants WHERE slug = ?", ("default",)
        ).fetchone()
        if not tenant_row:
            conn.execute(
                "INSERT INTO tenants (name, slug, created_at) VALUES (?, ?, ?)",
                ("Home", "default", now),
            )
            tenant_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
        else:
            tenant_id = tenant_row["id"]
        conn.execute(
            "INSERT OR REPLACE INTO memberships (user_id, tenant_id, role) VALUES (?, ?, ?)",
            (user_id, tenant_id, "owner"),
        )
        conn.commit()
    return f"Bootstrap created user {username!r} (superuser) on tenant default."


def fetch_user_by_id(user_id: int) -> dict[str, Any] | None:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return dict(row) if row else None


def fetch_user_by_username(username: str) -> dict[str, Any] | None:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        return dict(row) if row else None


def verify_user(username: str, password: str) -> dict[str, Any] | None:
    user = fetch_user_by_username(username)
    if not user or not user.get("is_active"):
        return None
    if check_password_hash(user["password_hash"], password):
        return user
    return None


def list_memberships(user_id: int) -> list[dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT m.tenant_id, m.role, t.slug, t.name
            FROM memberships m
            JOIN tenants t ON t.id = m.tenant_id
            WHERE m.user_id = ?
            ORDER BY t.slug
            """,
            (user_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def fetch_tenant_by_id(tenant_id: int) -> dict[str, Any] | None:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM tenants WHERE id = ?", (tenant_id,)).fetchone()
        return dict(row) if row else None


def fetch_tenant_by_slug(slug: str) -> dict[str, Any] | None:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM tenants WHERE slug = ?", (slug,)).fetchone()
        return dict(row) if row else None


def membership_role(user_id: int, tenant_id: int) -> str | None:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT role FROM memberships WHERE user_id = ? AND tenant_id = ?",
            (user_id, tenant_id),
        ).fetchone()
        return row["role"] if row else None


def create_user(username: str, password: str, is_superuser: bool = False) -> int:
    ph = generate_password_hash(password)
    now = utc_now_iso()
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO users (username, password_hash, is_active, is_superuser, created_at) "
            "VALUES (?, ?, 1, ?, ?)",
            (username, ph, 1 if is_superuser else 0, now),
        )
        conn.commit()
        return int(cur.lastrowid)


def set_membership(user_id: int, tenant_id: int, role: str) -> None:
    with get_connection() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO memberships (user_id, tenant_id, role) VALUES (?, ?, ?)",
            (user_id, tenant_id, role),
        )
        conn.commit()


def list_users() -> list[dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, username, is_active, is_superuser, created_at FROM users ORDER BY username"
        ).fetchall()
        return [dict(r) for r in rows]


def list_tenants() -> list[dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute("SELECT id, name, slug, created_at FROM tenants ORDER BY slug").fetchall()
        return [dict(r) for r in rows]


def create_tenant(name: str, slug: str) -> int:
    slug_clean = slug.strip().lower().replace(" ", "-")
    now = utc_now_iso()
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO tenants (name, slug, created_at) VALUES (?, ?, ?)",
            (name.strip(), slug_clean, now),
        )
        conn.commit()
        return int(cur.lastrowid)


def hash_api_key(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def create_api_key(
    tenant_id: int,
    name: str,
    created_by: int | None,
    *,
    scopes: str = "read:me,read:inventory",
    rate_limit_per_minute: int | None = None,
) -> tuple[str, int]:
    """Returns (plaintext_key, row_id). Plaintext shown once."""
    raw = f"bkc_{secrets.token_urlsafe(24)}"
    prefix = raw[:12]
    now = utc_now_iso()
    scopes_clean = ",".join(s.strip().lower() for s in scopes.split(",") if s.strip()) or "read:me,read:inventory"
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO api_keys (tenant_id, name, key_hash, prefix, created_by, created_at, scopes, rate_limit_per_minute) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (tenant_id, name, hash_api_key(raw), prefix, created_by, now, scopes_clean, rate_limit_per_minute),
        )
        conn.commit()
        return raw, int(cur.lastrowid)


def verify_api_key(raw: str) -> dict[str, Any] | None:
    digest = hash_api_key(raw)
    with get_connection() as conn:
        row = conn.execute(
            "SELECT k.*, t.slug AS tenant_slug FROM api_keys k "
            "JOIN tenants t ON t.id = k.tenant_id WHERE k.key_hash = ?",
            (digest,),
        ).fetchone()
        return dict(row) if row else None


def list_api_keys(tenant_id: int) -> list[dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, tenant_id, name, prefix, created_at, scopes, rate_limit_per_minute FROM api_keys WHERE tenant_id = ? ORDER BY id DESC",
            (tenant_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def list_all_api_keys() -> list[dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT k.id, k.tenant_id, k.name, k.prefix, k.created_at, k.scopes, k.rate_limit_per_minute,
                   t.slug AS tenant_slug
            FROM api_keys k
            JOIN tenants t ON t.id = k.tenant_id
            ORDER BY k.id DESC
            """
        ).fetchall()
        return [dict(r) for r in rows]


def append_audit(
    user_id: int | None,
    tenant_id: int | None,
    action: str,
    resource: str,
    detail: dict[str, Any] | None = None,
    ip: str | None = None,
) -> None:
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO audit_log (created_at, user_id, tenant_id, action, resource, detail, ip) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                utc_now_iso(),
                user_id,
                tenant_id,
                action,
                resource,
                json.dumps(detail, sort_keys=True) if detail else None,
                ip,
            ),
        )
        conn.commit()


def recent_audit(limit: int = 200) -> list[dict[str, Any]]:
    limit = min(max(int(limit), 1), 100_000)
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT a.*, u.username
            FROM audit_log a
            LEFT JOIN users u ON u.id = a.user_id
            ORDER BY a.id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
