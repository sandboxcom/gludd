"""Unit tests for the permission-spec format (Capability / PermissionSpec / parser).

TDD: written BEFORE ``src/general_ludd/security/permissions.py`` exists. Every
test below must fail with ImportError until the module is implemented, then pass
once the implementation satisfies the contract documented in
``docs/design/PERMISSION_SYSTEM.md``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from general_ludd.security.permissions import (
    Capability,
    PermissionSpec,
    PermissionSpecParser,
)


def _build_spec_yaml(
    *,
    agent_type: str = "build",
    capabilities: str = "",
    denied: str = "",
    max_sts_ttl_seconds: int = 3600,
) -> str:
    parts = [
        "version: 1",
        f"agent_type: {agent_type}",
        "parent_agent_id: null",
        f"max_sts_ttl_seconds: {max_sts_ttl_seconds}",
        'max_subagent_permissions: "same_or_fewer"',
        f"capabilities:\n{capabilities}" if capabilities else "capabilities: []",
        f"denied:\n{denied}" if denied else "denied: []",
    ]
    return "\n".join(parts) + "\n"


class TestParse:
    def test_parse_valid_spec(self) -> None:
        yaml_str = _build_spec_yaml(
            capabilities=(
                "  - resource: file:repo\n"
                '    actions: ["read", "write"]\n'
                "    constraints:\n"
                '      path_prefix: "/repo/"\n'
            ),
        )
        spec = PermissionSpecParser.parse(yaml_str)
        assert isinstance(spec, PermissionSpec)
        assert spec.version == 1
        assert spec.agent_type == "build"
        assert len(spec.capabilities) == 1
        cap = spec.capabilities[0]
        assert isinstance(cap, Capability)
        assert cap.resource == "file:repo"
        assert cap.actions == ["read", "write"]
        assert cap.constraints == {"path_prefix": "/repo/"}
        assert spec.denied == []
        assert spec.max_sts_ttl_seconds == 3600
        assert spec.max_subagent_permissions == "same_or_fewer"

    def test_parse_rejects_unknown_resource_type(self) -> None:
        yaml_str = _build_spec_yaml(
            capabilities=(
                "  - resource: banana:split\n"
                '    actions: ["read"]\n'
                "    constraints: {}\n"
            ),
        )
        spec = PermissionSpecParser.parse(yaml_str)
        errors = PermissionSpecParser.validate(spec)
        assert any("banana:split" in e for e in errors), errors
        assert errors, "unknown resource type must produce a validation error"

    def test_parse_file_reads_from_disk(self, tmp_path: Path) -> None:
        yaml_str = _build_spec_yaml(
            capabilities=(
                "  - resource: file:tmp\n"
                '    actions: ["read", "write"]\n'
                "    constraints:\n"
                '      path_prefix: "/tmp/gludd/"\n'
            ),
        )
        p = tmp_path / "build.yml"
        p.write_text(yaml_str)
        spec = PermissionSpecParser.parse_file(p)
        assert spec.agent_type == "build"
        assert spec.capabilities[0].resource == "file:tmp"


class TestValidate:
    def test_validate_catches_capability_in_both_allow_and_deny(self) -> None:
        cap_yaml = (
            "  - resource: file:repo\n"
            '    actions: ["read"]\n'
            "    constraints:\n"
            '      path_prefix: "/repo/"\n'
        )
        yaml_str = _build_spec_yaml(capabilities=cap_yaml, denied=cap_yaml)
        spec = PermissionSpecParser.parse(yaml_str)
        errors = PermissionSpecParser.validate(spec)
        assert any("both" in e.lower() or "deny" in e.lower() for e in errors), errors

    def test_validate_catches_missing_required_constraint(self) -> None:
        # file: resource requires path_prefix
        yaml_str = _build_spec_yaml(
            capabilities=(
                "  - resource: file:repo\n"
                '    actions: ["read"]\n'
                "    constraints: {}\n"
            ),
        )
        spec = PermissionSpecParser.parse(yaml_str)
        errors = PermissionSpecParser.validate(spec)
        assert any("path_prefix" in e for e in errors), errors

    def test_validate_catches_capability_with_no_actions(self) -> None:
        yaml_str = _build_spec_yaml(
            capabilities=(
                "  - resource: file:repo\n"
                "    actions: []\n"
                "    constraints:\n"
                '      path_prefix: "/repo/"\n'
            ),
        )
        spec = PermissionSpecParser.parse(yaml_str)
        errors = PermissionSpecParser.validate(spec)
        assert any("action" in e.lower() for e in errors), errors

    def test_validate_net_requires_allowed_hosts_or_ports(self) -> None:
        yaml_str = _build_spec_yaml(
            capabilities=(
                "  - resource: net:egress\n"
                '    actions: ["connect"]\n'
                "    constraints: {}\n"
            ),
        )
        spec = PermissionSpecParser.parse(yaml_str)
        errors = PermissionSpecParser.validate(spec)
        assert any("net" in e.lower() or "host" in e.lower() for e in errors), errors

    def test_validate_secret_openbao_requires_openbao_paths(self) -> None:
        yaml_str = _build_spec_yaml(
            capabilities=(
                "  - resource: secret:openbao\n"
                '    actions: ["read"]\n'
                "    constraints: {}\n"
            ),
        )
        spec = PermissionSpecParser.parse(yaml_str)
        errors = PermissionSpecParser.validate(spec)
        assert any("openbao_paths" in e for e in errors), errors

    def test_validate_passes_for_well_formed_spec(self) -> None:
        yaml_str = _build_spec_yaml(
            capabilities=(
                "  - resource: file:tmp\n"
                '    actions: ["read", "write"]\n'
                "    constraints:\n"
                '      path_prefix: "/tmp/gludd/"\n'
                "  - resource: net:egress:llm_api\n"
                '    actions: ["connect"]\n'
                "    constraints:\n"
                '      allowed_hosts: ["api.anthropic.com"]\n'
            ),
        )
        spec = PermissionSpecParser.parse(yaml_str)
        errors = PermissionSpecParser.validate(spec)
        assert errors == [], errors


class TestSubset:
    """Subset relation: requested ⊆ issuer.

    Every capability in ``requested`` must have a matching capability in
    ``issuer`` with the same resource, the same (or wider) actions set on the
    issuer side, and constraints at least as NARROW on the requested side.
    """

    @staticmethod
    def _spec(caps: list[Capability], agent_type: str = "x") -> PermissionSpec:
        return PermissionSpec(
            version=1,
            agent_type=agent_type,
            parent_agent_id=None,
            capabilities=caps,
            denied=[],
            max_sts_ttl_seconds=3600,
            max_subagent_permissions="same_or_fewer",
        )

    def test_subject_spec_subset_of_issuer_passes(self) -> None:
        issuer = self._spec(
            [
                Capability(
                    resource="file:tmp",
                    actions=["read", "write"],
                    constraints={"path_prefix": "/tmp/gludd/"},
                )
            ]
        )
        subject = self._spec(
            [
                Capability(
                    resource="file:tmp",
                    actions=["read"],
                    constraints={"path_prefix": "/tmp/gludd/sub/"},
                )
            ]
        )
        assert PermissionSpecParser.is_subset(subject, issuer) is True

    def test_subject_spec_with_extra_capability_fails(self) -> None:
        issuer = self._spec(
            [
                Capability(
                    resource="file:tmp",
                    actions=["read", "write"],
                    constraints={"path_prefix": "/tmp/gludd/"},
                )
            ]
        )
        subject = self._spec(
            [
                Capability(
                    resource="file:tmp",
                    actions=["read"],
                    constraints={"path_prefix": "/tmp/gludd/"},
                ),
                Capability(
                    resource="net:egress",
                    actions=["connect"],
                    constraints={"allowed_hosts": ["api.anthropic.com"]},
                ),
            ]
        )
        assert PermissionSpecParser.is_subset(subject, issuer) is False

    def test_subject_spec_with_wider_constraint_fails(self) -> None:
        # issuer allows /tmp/gludd/, subject asks for /tmp/ (WIDER) -> refused.
        issuer = self._spec(
            [
                Capability(
                    resource="file:tmp",
                    actions=["read", "write"],
                    constraints={"path_prefix": "/tmp/gludd/"},
                )
            ]
        )
        subject = self._spec(
            [
                Capability(
                    resource="file:tmp",
                    actions=["read"],
                    constraints={"path_prefix": "/tmp/"},
                )
            ]
        )
        assert PermissionSpecParser.is_subset(subject, issuer) is False

    def test_subject_spec_with_action_not_in_issuer_fails(self) -> None:
        issuer = self._spec(
            [
                Capability(
                    resource="file:tmp",
                    actions=["read"],
                    constraints={"path_prefix": "/tmp/gludd/"},
                )
            ]
        )
        subject = self._spec(
            [
                Capability(
                    resource="file:tmp",
                    actions=["read", "write"],  # write not granted by issuer
                    constraints={"path_prefix": "/tmp/gludd/"},
                )
            ]
        )
        assert PermissionSpecParser.is_subset(subject, issuer) is False

    def test_subject_net_subset_of_issuer_hosts_passes(self) -> None:
        issuer = self._spec(
            [
                Capability(
                    resource="net:egress:llm_api",
                    actions=["connect"],
                    constraints={
                        "allowed_hosts": [
                            "api.anthropic.com",
                            "api.openai.com",
                        ]
                    },
                )
            ]
        )
        subject = self._spec(
            [
                Capability(
                    resource="net:egress:llm_api",
                    actions=["connect"],
                    constraints={"allowed_hosts": ["api.anthropic.com"]},
                )
            ]
        )
        assert PermissionSpecParser.is_subset(subject, issuer) is True


def test_ttl_capped_at_issuer_max() -> None:
    """The issuer's ``max_sts_ttl_seconds`` caps what an STS token may be minted for.

    This is enforced in the StsIssuer, but the cap value lives on the
    PermissionSpec, so we assert it round-trips through the parser and is
    readable as an integer.
    """
    yaml_str = _build_spec_yaml(max_sts_ttl_seconds=900)
    spec = PermissionSpecParser.parse(yaml_str)
    assert spec.max_sts_ttl_seconds == 900
    assert isinstance(spec.max_sts_ttl_seconds, int)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
