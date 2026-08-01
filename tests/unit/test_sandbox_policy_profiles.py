"""Executable contract for versioned, deny-by-default sandbox profiles."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from general_ludd.security.policy.profiles import (
    BUILTIN_SANDBOX_PROFILES,
    PolicyLayer,
    PolicyWideningError,
    SandboxProfile,
    resolve_sandbox_profile,
)


def test_locked_is_the_immutable_deny_by_default_profile() -> None:
    resolved = resolve_sandbox_profile()

    assert resolved.requested_profile == "locked"
    assert resolved.policy.posture == "locked"
    assert resolved.policy.network.mode == "deny"
    assert resolved.policy.network.hosts == ()
    assert resolved.policy.secrets.allowed_refs == ()
    assert resolved.policy.backend.require_attestation is True
    assert resolved.policy.backend.fallback == "deny"
    assert len(resolved.policy_hash) == 64
    with pytest.raises(ValidationError):
        resolved.policy.network.mode = "proxy"  # type: ignore[misc]


def test_builtin_registry_cannot_be_replaced() -> None:
    with pytest.raises(TypeError):
        BUILTIN_SANDBOX_PROFILES["locked"] = BUILTIN_SANDBOX_PROFILES["standard"]  # type: ignore[index]


def test_strict_schema_rejects_unknown_nested_keys_and_coercion() -> None:
    with pytest.raises(ValidationError, match="unexpected"):
        resolve_sandbox_profile(
            administrator={"network": {"unexpected": True}},
        )
    with pytest.raises(ValidationError):
        resolve_sandbox_profile(
            administrator={"resources": {"memory_bytes": "536870912"}},
        )


@pytest.mark.parametrize("field", ["schema_version", "posture", "profile"])
def test_layers_cannot_replace_profile_identity(field: str) -> None:
    with pytest.raises(PolicyWideningError, match=field):
        resolve_sandbox_profile(
            layers=(PolicyLayer(scope="project", values={field: "development"}),),
        )


def test_locked_profile_rejects_audit_fallback_and_weak_backend() -> None:
    with pytest.raises(ValidationError, match="locked"):
        resolve_sandbox_profile(administrator={"backend": {"fallback": "audit"}})
    with pytest.raises(ValidationError, match="application-kernel"):
        resolve_sandbox_profile(
            administrator={"backend": {"minimum_strength": "process-isolation"}},
        )


def test_administrator_can_grant_bounded_capabilities_then_child_can_narrow() -> None:
    resolved = resolve_sandbox_profile(
        administrator={
            "network": {
                "mode": "allowlist",
                "hosts": ["api.example.com", "models.example.com"],
                "ports": [443],
                "max_connections": 12,
            },
            "secrets": {
                "allowed_refs": ["model/inference", "scm/read-only"],
                "max_ttl_seconds": 600,
            },
        },
        layers=(
            PolicyLayer(
                scope="project",
                values={
                    "network": {
                        "hosts": ["models.example.com"],
                        "max_connections": 4,
                    },
                    "secrets": {
                        "allowed_refs": ["model/inference"],
                        "max_ttl_seconds": 300,
                    },
                },
            ),
            PolicyLayer(
                scope="work_item",
                values={"resources": {"memory_bytes": 268_435_456}},
            ),
        ),
    )

    assert resolved.policy.network.hosts == ("models.example.com",)
    assert resolved.policy.network.max_connections == 4
    assert resolved.policy.secrets.allowed_refs == ("model/inference",)
    assert resolved.policy.resources.memory_bytes == 268_435_456
    assert resolved.applied_layers == ("builtin", "administrator", "project", "work_item")


@pytest.mark.parametrize(
    ("parent", "child", "path"),
    [
        ({}, {"resources": {"memory_bytes": 1_073_741_824}}, "resources.memory_bytes"),
        ({}, {"network": {"mode": "proxy"}}, "network.mode"),
        ({}, {"backend": {"require_attestation": False}}, "backend.require_attestation"),
        (
            {"network": {"mode": "allowlist", "hosts": ["api.example.com"]}},
            {"network": {"hosts": ["evil.example.com"]}},
            "network.hosts",
        ),
    ],
)
def test_non_administrator_layers_cannot_widen(
    parent: dict[str, object],
    child: dict[str, object],
    path: str,
) -> None:
    with pytest.raises(PolicyWideningError, match=path):
        resolve_sandbox_profile(
            administrator=parent,
            layers=(PolicyLayer(scope="agent", values=child),),
        )


def test_hash_is_canonical_across_mapping_order() -> None:
    first = resolve_sandbox_profile(
        administrator={
            "network": {"hosts": ["api.example.com"], "mode": "allowlist"},
            "resources": {"wall_seconds": 120, "cpu_seconds": 100},
        }
    )
    second = resolve_sandbox_profile(
        administrator={
            "resources": {"cpu_seconds": 100, "wall_seconds": 120},
            "network": {"mode": "allowlist", "hosts": ["api.example.com"]},
        }
    )

    assert first.policy_hash == second.policy_hash
    assert first.canonical_json == second.canonical_json


def test_policy_collections_are_normalized_for_deterministic_attestation() -> None:
    resolved = resolve_sandbox_profile(
        administrator={
            "filesystem": {"host_paths": ["/var/model", "/opt/cache"]},
            "network": {
                "mode": "allowlist",
                "hosts": ["MODELS.EXAMPLE.COM.", "api.example.com"],
                "cidrs": ["2001:db8::/32", "192.0.2.0/24"],
                "ports": [8443, 443],
            },
            "process": {"executable_allowlist": ["/usr/bin/python", "/bin/sh"]},
            "secrets": {"allowed_refs": ["scm/read", "model/inference"]},
        }
    )

    assert resolved.policy.filesystem.host_paths == ("/opt/cache", "/var/model")
    assert resolved.policy.network.hosts == (
        "api.example.com",
        "models.example.com",
    )
    assert resolved.policy.network.cidrs == ("192.0.2.0/24", "2001:db8::/32")
    assert resolved.policy.network.ports == (443, 8443)
    assert resolved.policy.process.executable_allowlist == ("/bin/sh", "/usr/bin/python")
    assert resolved.policy.secrets.allowed_refs == ("model/inference", "scm/read")

    with pytest.raises(ValidationError, match="duplicate"):
        resolve_sandbox_profile(
            administrator={
                "network": {
                    "mode": "allowlist",
                    "hosts": ["API.EXAMPLE.COM", "api.example.com."],
                }
            }
        )


def test_development_layer_cannot_restore_administrator_audit_fallback() -> None:
    with pytest.raises(PolicyWideningError, match=r"backend\.fallback"):
        resolve_sandbox_profile(
            "development",
            administrator={"backend": {"fallback": "deny"}},
            layers=(
                PolicyLayer(
                    scope="project",
                    values={"backend": {"fallback": "audit"}},
                ),
            ),
        )


def test_profile_bounds_and_backend_names_are_validated() -> None:
    with pytest.raises(ValidationError):
        SandboxProfile.model_validate(
            {
                **BUILTIN_SANDBOX_PROFILES["locked"].model_dump(mode="python"),
                "resources": {
                    **BUILTIN_SANDBOX_PROFILES["locked"].resources.model_dump(),
                    "output_bytes": 0,
                },
            }
        )
    with pytest.raises(ValidationError):
        resolve_sandbox_profile(
            administrator={"backend": {"preference": ["imaginary-sandbox"]}},
        )


@pytest.mark.parametrize(
    "override",
    [
        {"backend": {"preference": []}},
        {"backend": {"preference": ["firecracker", "firecracker"]}},
        {"filesystem": {"host_paths": ["relative/path"]}},
        {"network": {"hosts": ["https://not-a-host"]}},
        {"network": {"cidrs": ["10.0.0.1/24"]}},
        {"network": {"ports": [443, 443]}},
        {"network": {"hosts": ["api.example.com"]}},
        {"process": {"executable_allowlist": ["python"]}},
        {"resources": {"cpu_seconds": 300, "wall_seconds": 299}},
        {"secrets": {"allowed_refs": ["../host-secret"]}},
        {"secrets": {"mode": "none", "allowed_refs": ["model/inference"]}},
        {"secrets": {"allowed_refs": ["model/inference", "model/inference"]}},
    ],
)
def test_contradictory_or_ambiguous_configuration_fails_closed(
    override: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        resolve_sandbox_profile(administrator=override)


@pytest.mark.parametrize(
    ("administrator", "child", "path"),
    [
        (
            {"backend": {"minimum_strength": "virtual-machine"}},
            {"backend": {"minimum_strength": "application-kernel"}},
            "backend.minimum_strength",
        ),
        (
            {"filesystem": {"workspace": "deny"}},
            {"filesystem": {"workspace": "read-only"}},
            "filesystem.workspace",
        ),
        ({}, {"network": {"deny_metadata": False}}, "network.deny_metadata"),
        (
            {},
            {"process": {"syscall_profile": "different-profile-v1"}},
            "process.syscall_profile",
        ),
        ({}, {"process": {"no_new_privileges": False}}, "process.no_new_privileges"),
        (
            {"secrets": {"mode": "none"}},
            {"secrets": {"mode": "brokered"}},
            "secrets.mode",
        ),
        ({}, {"audit": {"include_denials": False}}, "audit.include_denials"),
    ],
)
def test_model_level_comparison_rejects_incomparable_or_weaker_controls(
    administrator: dict[str, object],
    child: dict[str, object],
    path: str,
) -> None:
    with pytest.raises(PolicyWideningError, match=path):
        resolve_sandbox_profile(
            administrator=administrator,
            layers=(PolicyLayer(scope="user", values=child),),
        )


def test_layer_constructor_and_administrator_scope_fail_closed() -> None:
    with pytest.raises(ValueError, match="unsupported"):
        PolicyLayer(scope="unknown", values={})  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="mapping"):
        PolicyLayer(scope="user", values=[])  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="mapping"):
        resolve_sandbox_profile(administrator=[])  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="administrator argument"):
        resolve_sandbox_profile(layers=(PolicyLayer(scope="administrator", values={}),))


def test_policy_layer_is_deeply_immutable_after_validation() -> None:
    original = {"network": {"hosts": ["api.example.com"]}}
    layer = PolicyLayer(scope="project", values=original)
    original["network"]["hosts"].append("mutated.example.com")  # type: ignore[index,union-attr]

    assert layer.values["network"]["hosts"] == ("api.example.com",)  # type: ignore[index]
    with pytest.raises(TypeError):
        layer.values["network"]["mode"] = "proxy"  # type: ignore[index]
