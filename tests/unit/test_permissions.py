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


class TestSubsetConstraintContainment:
    """``is_subset`` constraint dominance (``_constraints_narrower``) is
    containment-aware per dimension.

    Regression guard for a privilege-widening bug where the file: predicate used
    a bare string ``startswith`` so a DISJOINT longer path (``/etc/passwd2``)
    looked narrower than (dominated by) a shorter one (``/etc/pass``), letting an
    over-broad requested spec pass the subset check and escalate. Mirrors the
    segment-boundary containment fix already applied to ``_intersect_constraints``.
    """

    @staticmethod
    def _spec(caps: list[Capability]) -> PermissionSpec:
        return PermissionSpec(
            version=1,
            agent_type="x",
            parent_agent_id=None,
            capabilities=caps,
            denied=[],
            max_sts_ttl_seconds=3600,
            max_subagent_permissions="same_or_fewer",
        )

    def _file_cap(self, prefix: str | None) -> Capability:
        constraints: dict[str, str] = {}
        if prefix is not None:
            constraints["path_prefix"] = prefix
        return Capability(
            resource="file:repo", actions=["read"], constraints=constraints
        )

    def _is_subset(self, narrow: str | None, wide: str | None) -> bool:
        requested = self._spec([self._file_cap(narrow)])
        issuer = self._spec([self._file_cap(wide)])
        return PermissionSpecParser.is_subset(requested, issuer)

    def test_disjoint_prefixes_not_narrower(self) -> None:
        # /etc/passwd2 is NOT under /etc/pass (segment-disjoint) -> requested is
        # NOT a subset of issuer; the bare-startswith bug wrongly returned True.
        assert self._is_subset("/etc/passwd2", "/etc/pass") is False

    def test_shared_fragment_not_narrower(self) -> None:
        # /a/bc is not nested under /a/b.
        assert self._is_subset("/a/bc", "/a/b") is False

    def test_true_containment_is_narrower(self) -> None:
        # /etc/ssl/certs IS under /etc/ssl -> genuine subset.
        assert self._is_subset("/etc/ssl/certs", "/etc/ssl") is True

    def test_equal_prefix_is_narrower_or_equal(self) -> None:
        assert self._is_subset("/repo/", "/repo/") is True

    def test_trailing_slash_insensitive(self) -> None:
        # "/repo" and "/repo/" denote the same scope (segment-aware).
        assert self._is_subset("/repo", "/repo/") is True

    def test_wider_narrow_side_is_not_subset(self) -> None:
        # requested /tmp/ is WIDER than issuer /tmp/gludd/ -> not a subset.
        assert self._is_subset("/tmp/", "/tmp/gludd/") is False

    def test_root_wide_contains_any(self) -> None:
        # issuer "/" is the whole tree; any narrower request is a subset.
        assert self._is_subset("/etc/ssl", "/") is True

    def test_present_constraint_narrower_than_absent(self) -> None:
        # issuer has NO path_prefix (unconstrained == all files); a requested
        # cap WITH a constraint is narrower -> subset. (absent == WIDER)
        assert self._is_subset("/repo/x", None) is True

    def test_absent_narrow_side_is_wider_not_subset(self) -> None:
        # issuer restricts /repo/ but requested has NO path_prefix
        # (unconstrained == WIDER) -> NOT a subset. Fail-safe direction.
        assert self._is_subset(None, "/repo/") is False


class TestSubsetNetConstraintDirection:
    """``is_subset`` net: dominance treats an ABSENT/empty allow-list as the
    WIDEST scope (matching the Seatbelt/AppArmor enforcers that emit
    ``allow network-outbound`` for an empty host list).

    Regression guard for an inversion where the predicate SKIPPED an empty
    narrow-side key, so a requested cap with NO ``allowed_hosts`` (== all hosts)
    passed the subset check against an issuer that restricted hosts — an egress
    escalation. Fail-safe direction: when the narrow side is unconstrained on a
    dimension the wide side restricts, it is NOT narrower.
    """

    @staticmethod
    def _spec(caps: list[Capability]) -> PermissionSpec:
        return PermissionSpec(
            version=1,
            agent_type="x",
            parent_agent_id=None,
            capabilities=caps,
            denied=[],
            max_sts_ttl_seconds=3600,
            max_subagent_permissions="same_or_fewer",
        )

    def _net_cap(self, constraints: dict[str, object]) -> Capability:
        return Capability(
            resource="net:egress", actions=["connect"], constraints=constraints
        )

    def _is_subset(
        self, narrow: dict[str, object], wide: dict[str, object]
    ) -> bool:
        requested = self._spec([self._net_cap(narrow)])
        issuer = self._spec([self._net_cap(wide)])
        return PermissionSpecParser.is_subset(requested, issuer)

    def test_absent_hosts_narrow_side_is_not_subset_of_restricted(self) -> None:
        # requested has NO allowed_hosts (== all hosts, widest) while issuer
        # restricts to one host -> requested is WIDER -> NOT a subset.
        assert (
            self._is_subset(
                {"allowed_ports": [443]},
                {"allowed_hosts": ["api.z.ai"], "allowed_ports": [443]},
            )
            is False
        )

    def test_absent_ports_narrow_side_is_not_subset_of_restricted(self) -> None:
        # requested constrains hosts but omits ports; issuer restricts ports ->
        # requested is unconstrained (wider) on ports -> NOT a subset.
        assert (
            self._is_subset(
                {"allowed_hosts": ["api.z.ai"]},
                {"allowed_hosts": ["api.z.ai"], "allowed_ports": [443]},
            )
            is False
        )

    def test_hosts_subset_is_narrower(self) -> None:
        assert (
            self._is_subset(
                {"allowed_hosts": ["api.z.ai"]},
                {"allowed_hosts": ["api.z.ai", "api.openai.com"]},
            )
            is True
        )

    def test_wide_unconstrained_dimension_allows_any_narrow(self) -> None:
        # issuer does not restrict ports (unconstrained == widest); requested
        # narrowing to one port is a subset.
        assert (
            self._is_subset(
                {"allowed_hosts": ["api.z.ai"], "allowed_ports": [443]},
                {"allowed_hosts": ["api.z.ai"]},
            )
            is True
        )


class TestSubsetSecretPathsDirection:
    """secret:openbao empty ``openbao_paths`` == DENY-all == narrowest.

    The enforcer (``SecretsManager._enforce_permission``) denies every path when
    the glob-list is empty, so an empty narrow set is genuinely the narrowest and
    IS a subset of any issuer set. Locks the correct (non-inverted) direction.
    """

    @staticmethod
    def _spec(caps: list[Capability]) -> PermissionSpec:
        return PermissionSpec(
            version=1,
            agent_type="x",
            parent_agent_id=None,
            capabilities=caps,
            denied=[],
            max_sts_ttl_seconds=3600,
            max_subagent_permissions="same_or_fewer",
        )

    def _cap(self, paths: list[str]) -> Capability:
        return Capability(
            resource="secret:openbao",
            actions=["read"],
            constraints={"openbao_paths": paths},
        )

    def _is_subset(self, narrow: list[str], wide: list[str]) -> bool:
        requested = self._spec([self._cap(narrow)])
        issuer = self._spec([self._cap(wide)])
        return PermissionSpecParser.is_subset(requested, issuer)

    def test_empty_paths_narrow_side_is_subset(self) -> None:
        # empty == deny-all == narrowest -> subset of anything.
        assert self._is_subset([], ["secret/data/gludd/*"]) is True

    def test_wider_paths_narrow_side_is_not_subset(self) -> None:
        assert (
            self._is_subset(
                ["secret/data/gludd/*"], ["secret/data/gludd/build/*"]
            )
            is False
        )


class TestSubsetDeniedNotDropped:
    """``intersection`` unions ``denied`` and never lets a narrower/dominant cap
    drop a STRICTER deny.

    Guards the enforcement invariant: an STS token derived from two specs must
    carry EVERY deny either parent declared (union, not intersection), so a
    stricter deny is never silently weakened.
    """

    @staticmethod
    def _spec(denied: list[Capability]) -> PermissionSpec:
        return PermissionSpec(
            version=1,
            agent_type="x",
            parent_agent_id=None,
            capabilities=[],
            denied=denied,
            max_sts_ttl_seconds=3600,
            max_subagent_permissions="same_or_fewer",
        )

    def test_distinct_denies_are_unioned(self) -> None:
        a = self._spec(
            [Capability(resource="file:repo", actions=["write"], constraints={})]
        )
        b = self._spec(
            [Capability(resource="secret:openbao", actions=["delete"], constraints={})]
        )
        out = PermissionSpecParser.intersection(a, b)
        resources = {(d.resource, tuple(d.actions)) for d in out.denied}
        assert ("file:repo", ("write",)) in resources
        assert ("secret:openbao", ("delete",)) in resources
        assert len(out.denied) == 2

    def test_denies_survive_when_only_one_side_declares(self) -> None:
        a = self._spec(
            [Capability(resource="net:egress", actions=["connect"], constraints={})]
        )
        b = self._spec([])
        out = PermissionSpecParser.intersection(a, b)
        assert any(
            d.resource == "net:egress" and d.actions == ["connect"]
            for d in out.denied
        )


class TestIntersectFilePrefix:
    """``intersection`` of two ``file:`` path_prefix scopes is containment-aware.

    Regression guard for a privilege-widening bug where the intersection picked
    the LONGER path_prefix with no containment check, handing a subagent file
    access to a scope the human spec never granted. The true intersection of two
    disjoint path scopes is EMPTY (the capability must be dropped).
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

    def _file_cap(self, prefix: str) -> Capability:
        return Capability(
            resource="file:repo",
            actions=["read"],
            constraints={"path_prefix": prefix},
        )

    def _intersect_file(self, ap: str, bp: str) -> list[Capability]:
        a = self._spec([self._file_cap(ap)])
        b = self._spec([self._file_cap(bp)])
        return PermissionSpecParser.intersection(a, b).capabilities

    def test_disjoint_prefixes_drop_capability(self) -> None:
        # /a/b/c and /x/y share no filesystem scope -> empty intersection.
        caps = self._intersect_file("/a/b/c", "/x/y")
        assert caps == []

    def test_nested_prefixes_yield_the_narrower(self) -> None:
        # /a/b contains /a/b/c -> intersection is the narrower /a/b/c.
        caps = self._intersect_file("/a/b", "/a/b/c")
        assert len(caps) == 1
        assert caps[0].constraints == {"path_prefix": "/a/b/c"}

    def test_nested_prefixes_order_independent(self) -> None:
        # Argument order must not change the result.
        caps = self._intersect_file("/a/b/c", "/a/b")
        assert len(caps) == 1
        assert caps[0].constraints == {"path_prefix": "/a/b/c"}

    def test_identical_prefixes_yield_that_prefix(self) -> None:
        caps = self._intersect_file("/a/b/", "/a/b/")
        assert len(caps) == 1
        assert caps[0].constraints == {"path_prefix": "/a/b/"}

    def test_shared_fragment_is_not_false_nesting(self) -> None:
        # /a/bc is NOT under /a/b (segment-aware, not bare startswith) -> drop.
        caps = self._intersect_file("/a/bc", "/a/b")
        assert caps == []

    def test_root_scope_contains_any_and_yields_narrower(self) -> None:
        # "/" is the whole filesystem; its intersection with /x/y is /x/y.
        caps = self._intersect_file("/", "/x/y")
        assert len(caps) == 1
        assert caps[0].constraints == {"path_prefix": "/x/y"}


class TestIntersectNetSecretRegression:
    """net:/secret: intersection is set-intersection and stays unchanged."""

    @staticmethod
    def _spec(caps: list[Capability]) -> PermissionSpec:
        return PermissionSpec(
            version=1,
            agent_type="x",
            parent_agent_id=None,
            capabilities=caps,
            denied=[],
            max_sts_ttl_seconds=3600,
            max_subagent_permissions="same_or_fewer",
        )

    def test_net_hosts_are_set_intersected(self) -> None:
        a = self._spec(
            [
                Capability(
                    resource="net:egress:llm_api",
                    actions=["connect"],
                    constraints={
                        "allowed_hosts": ["api.anthropic.com", "api.openai.com"]
                    },
                )
            ]
        )
        b = self._spec(
            [
                Capability(
                    resource="net:egress:llm_api",
                    actions=["connect"],
                    constraints={
                        "allowed_hosts": ["api.anthropic.com", "example.com"]
                    },
                )
            ]
        )
        caps = PermissionSpecParser.intersection(a, b).capabilities
        assert len(caps) == 1
        assert caps[0].constraints == {"allowed_hosts": ["api.anthropic.com"]}

    def test_net_disjoint_hosts_drop_capability(self) -> None:
        a = self._spec(
            [
                Capability(
                    resource="net:egress:llm_api",
                    actions=["connect"],
                    constraints={"allowed_hosts": ["api.anthropic.com"]},
                )
            ]
        )
        b = self._spec(
            [
                Capability(
                    resource="net:egress:llm_api",
                    actions=["connect"],
                    constraints={"allowed_hosts": ["example.com"]},
                )
            ]
        )
        caps = PermissionSpecParser.intersection(a, b).capabilities
        assert caps == []

    def test_secret_openbao_paths_are_set_intersected(self) -> None:
        a = self._spec(
            [
                Capability(
                    resource="secret:openbao",
                    actions=["read"],
                    constraints={"openbao_paths": ["kv/a", "kv/b"]},
                )
            ]
        )
        b = self._spec(
            [
                Capability(
                    resource="secret:openbao",
                    actions=["read"],
                    constraints={"openbao_paths": ["kv/b", "kv/c"]},
                )
            ]
        )
        caps = PermissionSpecParser.intersection(a, b).capabilities
        assert len(caps) == 1
        assert caps[0].constraints == {"openbao_paths": ["kv/b"]}


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
