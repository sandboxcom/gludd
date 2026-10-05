"""load_auth_posture default-secure behavior.

Regression lock for the worker fail-open fix: with no PSK and no explicit
GLUDD_REQUIRE_AUTH, the posture must now REQUIRE auth (fail-closed) unless the
operator explicitly opts into no-auth via GLUDD_PSK_DISABLE. Tests pass an
explicit `env` mapping so they are independent of the process environment /
conftest opt-out.
"""

from __future__ import annotations

from general_ludd.security.auth import load_auth_posture


def test_no_psk_no_optout_requires_auth():
    posture = load_auth_posture("worker", env={})
    assert posture.require_auth is True
    assert posture.no_auth is True


def test_no_psk_with_psk_disable_opt_out_serves():
    posture = load_auth_posture("worker", env={"GLUDD_PSK_DISABLE": "1"})
    assert posture.require_auth is False
    assert posture.no_auth is True


def test_psk_present_does_not_require_env_flags():
    posture = load_auth_posture(
        "worker", env={"GLUDD_AUTH_PSK": "s3kr3t"}  # pragma: allowlist secret
    )
    assert posture.require_auth is False
    assert posture.no_auth is False


def test_require_auth_env_forces_closed_even_with_opt_out():
    posture = load_auth_posture(
        "worker", env={"GLUDD_REQUIRE_AUTH": "1", "GLUDD_PSK_DISABLE": "1"}
    )
    assert posture.require_auth is True


def test_allow_no_auth_alias_also_opt_out():
    """GLUDD_ALLOW_NO_AUTH=1 should also opt out (deprecated daemon alias)."""
    posture = load_auth_posture("worker", env={"GLUDD_ALLOW_NO_AUTH": "1"})
    assert posture.require_auth is False
    assert posture.no_auth is True


def test_psk_disable_falsey_values_still_require():
    for val in ("0", "false", "no", "off", ""):
        posture = load_auth_posture("worker", env={"GLUDD_PSK_DISABLE": val})
        assert posture.require_auth is True, f"value {val!r} should not opt out"
