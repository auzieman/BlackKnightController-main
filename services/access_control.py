"""RBAC: tenant roles + platform superuser."""

from __future__ import annotations

from enum import Enum
from functools import wraps
from typing import Callable

from flask import Blueprint, abort, flash, redirect, request, url_for
from flask_login import current_user
from services import bkc_db


class Perm(str, Enum):
    READ = "read"
    INVENTORY_WRITE = "inventory_write"
    INTEGRATION_PROBE = "integration_probe"  # test connections, pull read-only inventory
    INTEGRATION_WRITE = "integration_write"
    HYPERVISOR_READ = "hypervisor_read"  # catalog refresh
    HYPERVISOR_OPS = "hypervisor_ops"  # clone / destructive API
    REMOTE_EXEC = "remote_exec"
    SETTINGS = "settings"


ROLE_PERMS: dict[str, set[Perm]] = {
    "viewer": {Perm.READ},
    "operator": {
        Perm.READ,
        Perm.INVENTORY_WRITE,
        Perm.INTEGRATION_PROBE,
        Perm.HYPERVISOR_READ,
    },
    "owner": {
        Perm.READ,
        Perm.INVENTORY_WRITE,
        Perm.INTEGRATION_PROBE,
        Perm.INTEGRATION_WRITE,
        Perm.HYPERVISOR_READ,
        Perm.HYPERVISOR_OPS,
        Perm.REMOTE_EXEC,
    },
}


def _user_row() -> dict | None:
    if not current_user.is_authenticated:
        return None
    return bkc_db.fetch_user_by_id(int(current_user.id))


def current_membership_role(tenant_id: int) -> str | None:
    row = _user_row()
    if not row:
        return None
    if row.get("is_superuser"):
        return "owner"
    return bkc_db.membership_role(int(row["id"]), tenant_id)


def permissions_for(tenant_id: int) -> set[Perm]:
    row = _user_row()
    if not row:
        return set()
    if row.get("is_superuser"):
        return set(Perm)  # all including SETTINGS
    role = bkc_db.membership_role(int(row["id"]), tenant_id)
    if not role:
        return set()
    return set(ROLE_PERMS.get(role, set()))


def has_perm(tenant_id: int, perm: Perm) -> bool:
    perms = permissions_for(tenant_id)
    if perm in perms:
        return True
    if perm == Perm.SETTINGS and _user_row() and _user_row().get("is_superuser"):
        return True
    return False


def require_perm(perm: Perm) -> Callable:
    def decorator(view: Callable) -> Callable:
        @wraps(view)
        def wrapped(*args, **kwargs):
            from services.tenant_context import get_current_tenant_id

            tid = get_current_tenant_id()
            if tid is None:
                abort(403)
            if not has_perm(tid, perm):
                if request.is_json or request.path.startswith("/api/"):
                    abort(403)
                flash("You do not have permission for that action.", "error")
                return redirect(url_for("index.index"))
            return view(*args, **kwargs)

        return wrapped

    return decorator


def require_superuser(view: Callable) -> Callable:
    @wraps(view)
    def wrapped(*args, **kwargs):
        row = _user_row()
        if not row or not row.get("is_superuser"):
            abort(403)
        return view(*args, **kwargs)

    return wrapped


def register_inventory_post_guard(blueprint: Blueprint) -> None:
    """POST on inventory-editing blueprints requires INVENTORY_WRITE."""

    @blueprint.before_request
    def _guard_inventory_post():
        if not current_user.is_authenticated:
            return None
        if request.method != "POST":
            return None
        from services.tenant_context import get_current_tenant_id

        tid = get_current_tenant_id()
        if tid is None or not has_perm(tid, Perm.INVENTORY_WRITE):
            flash("You do not have permission to change inventory.", "error")
            return redirect(url_for("index.index"))
        return None


def register_admin_post_guard(blueprint: Blueprint) -> None:
    @blueprint.before_request
    def _guard_admin_post():
        if not current_user.is_authenticated:
            return None
        if request.method != "POST":
            return None
        from services.tenant_context import get_current_tenant_id

        tid = get_current_tenant_id()
        if tid is None or not has_perm(tid, Perm.REMOTE_EXEC):
            flash("You do not have permission to run remote admin actions.", "error")
            return redirect(url_for("index.index"))
        return None


def integrations_action_permission(action: str) -> Perm:
    if action in {"save-proxmox", "save-ansible", "save-ssh", "generate-ssh-key"}:
        return Perm.INTEGRATION_WRITE
    if action in {"sync-proxmox-inventory", "sync-ansible-inventory"}:
        return Perm.INVENTORY_WRITE
    if action in {"test-proxmox", "pull-proxmox-inventory", "scan-ansible"}:
        return Perm.INTEGRATION_PROBE
    return Perm.INTEGRATION_WRITE


def register_integrations_post_guard(blueprint: Blueprint) -> None:
    @blueprint.before_request
    def _guard_integrations_post():
        if not current_user.is_authenticated:
            return None
        if request.method != "POST":
            return None
        from services.tenant_context import get_current_tenant_id

        action = request.form.get("action", "")
        perm = integrations_action_permission(action)
        tid = get_current_tenant_id()
        if tid is None or not has_perm(tid, perm):
            flash("You do not have permission for that integrations action.", "error")
            return redirect(url_for("integrations.integrations"))
        return None


def register_proxmox_ops_post_guard(blueprint: Blueprint) -> None:
    @blueprint.before_request
    def _guard_pm_post():
        if not current_user.is_authenticated:
            return None
        if request.method != "POST":
            return None
        from services.tenant_context import get_current_tenant_id

        action = request.form.get("action", "")
        tid = get_current_tenant_id()
        if tid is None:
            return redirect(url_for("index.index"))
        if action == "refresh":
            perm = Perm.HYPERVISOR_READ
        elif action in ("clone-qemu", "clone-lxc"):
            perm = Perm.HYPERVISOR_OPS
        else:
            perm = Perm.HYPERVISOR_READ
        if not has_perm(tid, perm):
            flash("You do not have permission for that Proxmox action.", "error")
            return redirect(url_for("index.index"))
        return None


def permission_flags(tenant_id: int | None) -> dict[str, bool]:
    """Template-friendly booleans."""
    if tenant_id is None:
        return {p.value: False for p in Perm}
    perms = permissions_for(tenant_id)
    return {p.value: p in perms for p in Perm}
