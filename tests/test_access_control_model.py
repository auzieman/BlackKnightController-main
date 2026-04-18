"""RBAC model invariants (no Flask request context)."""

from __future__ import annotations

from services.access_control import ROLE_PERMS, Perm


def test_all_roles_defined():
    assert set(ROLE_PERMS) == {"viewer", "operator", "owner"}


def test_viewer_minimal():
    assert ROLE_PERMS["viewer"] == {Perm.READ}


def test_operator_includes_inventory_write():
    assert Perm.INVENTORY_WRITE in ROLE_PERMS["operator"]
    assert Perm.INTEGRATION_WRITE not in ROLE_PERMS["operator"]


def test_owner_covers_integration_write_and_hypervisor_ops():
    assert Perm.INTEGRATION_WRITE in ROLE_PERMS["owner"]
    assert Perm.HYPERVISOR_OPS in ROLE_PERMS["owner"]
    assert Perm.REMOTE_EXEC in ROLE_PERMS["owner"]


def test_settings_perm_value():
    assert Perm.SETTINGS.value == "settings"
