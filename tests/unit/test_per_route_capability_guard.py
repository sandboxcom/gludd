"""Public PSK capability contract recovered from the requeue-sweep branch."""

from __future__ import annotations

from general_ludd.security.permissions import (
    Capability,
    PermissionSpec,
    _psk_admin_default_spec,
    check_capability,
    psk_admin_default_spec,
)


def test_public_psk_admin_spec_covers_guarded_admin_routes() -> None:
    spec = psk_admin_default_spec()

    assert spec == _psk_admin_default_spec()
    assert spec.agent_type == "psk-admin"
    assert check_capability(spec, "admin:sts", "revoke") is True
    assert check_capability(spec, "admin:permissions", "write") is True
    assert check_capability(spec, "admin:account", "delete") is True
    assert check_capability(spec, "admin:deploy", "write") is True


def test_public_capability_check_remains_fail_closed_for_explicit_denial() -> None:
    spec = PermissionSpec(
        agent_type="limited",
        capabilities=[Capability(resource="admin:sts", actions=["revoke"])],
        denied=[Capability(resource="admin:sts", actions=["revoke"])],
    )

    assert check_capability(spec, "admin:sts", "revoke") is False
