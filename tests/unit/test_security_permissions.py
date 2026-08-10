"""Deep edge-case tests for permissions.py — PermissionSpec, denials, intersection, is_subset.

Covers: deny propagation, path-scoped denials, intersection narrowing,
constraint comparison, empty/null handling, unknown-type defaults,
union_denied dedup, parser validation edges, subject enum round-trips.
"""

from __future__ import annotations

from typing import cast

import pytest

from general_ludd.security.permissions import (
    Capability,
    PermissionSpec,
    PermissionSpecParser,
    PermissionSubject,
    _psk_admin_default_spec,
    check_capability,
    default_human_spec,
    default_spec,
    union_denied,
)

# ---------------------------------------------------------------------------
# Denial edge cases
# ---------------------------------------------------------------------------


class TestDenialMatching:
    def test_empty_actions_deny_all_actions_on_resource(self):
        spec = PermissionSpec(
            agent_type="test",
            capabilities=[Capability(resource="secret:openbao", actions=["read", "write"])],
            denied=[Capability(resource="secret:openbao", actions=[])],
        )
        assert spec.is_denied("secret:openbao", "read") is True
        assert spec.is_denied("secret:openbao", "write") is True
        assert spec.is_denied("secret:openbao", "list") is True

    def test_specific_action_deny_only_matches_that_action(self):
        spec = PermissionSpec(
            agent_type="test",
            denied=[Capability(resource="admin:account", actions=["delete"])],
        )
        assert spec.is_denied("admin:account", "delete") is True
        assert spec.is_denied("admin:account", "create") is False
        assert spec.is_denied("admin:account", "read") is False

    def test_different_resource_denial_does_not_match(self):
        spec = PermissionSpec(
            agent_type="test",
            denied=[Capability(resource="secret:openbao", actions=["read"])],
        )
        assert spec.is_denied("file:", "read") is False
        assert spec.is_denied("net:", "read") is False

    def test_denial_with_openbao_paths_matches_path(self):
        spec = PermissionSpec(
            agent_type="test",
            denied=[
                Capability(
                    resource="secret:openbao",
                    actions=["read"],
                    constraints={"openbao_paths": ["secret/data/gludd/restricted/*"]},
                )
            ],
        )
        assert spec.is_denied("secret:openbao", "read", "secret/data/gludd/restricted/foo") is True
        assert spec.is_denied("secret:openbao", "read", "secret/data/gludd/allowed/bar") is False

    def test_denial_with_openbao_paths_no_path_arg_ignores_constraint(self):
        spec = PermissionSpec(
            agent_type="test",
            denied=[
                Capability(
                    resource="secret:openbao",
                    actions=["read"],
                    constraints={"openbao_paths": ["secret/data/gludd/restricted/*"]},
                )
            ],
        )
        assert spec.is_denied("secret:openbao", "read") is True

    def test_denial_with_empty_openbao_paths_list_denies_all_paths(self):
        """Empty openbao_paths list means no path constraint → deny fires for any path."""
        spec = PermissionSpec(
            agent_type="test",
            denied=[
                Capability(
                    resource="secret:openbao",
                    actions=["read"],
                    constraints={"openbao_paths": []},
                )
            ],
        )
        assert spec.is_denied("secret:openbao", "read", "secret/data/gludd/foo") is True

    def test_denial_with_path_prefix_matches_glob(self):
        """fnmatch glob: '/etc/*' matches '/etc/hosts'."""
        spec = PermissionSpec(
            agent_type="test",
            denied=[
                Capability(
                    resource="file:",
                    actions=["write"],
                    constraints={"path_prefix": "/etc/*"},
                )
            ],
        )
        assert spec.is_denied("file:", "write", "/etc/hosts") is True
        assert spec.is_denied("file:", "write", "/opt/app/config") is False

    def test_denial_with_path_prefix_exact_match(self):
        """Exact path_prefix: fnmatch requires an exact match without wildcard."""
        spec = PermissionSpec(
            agent_type="test",
            denied=[
                Capability(
                    resource="file:",
                    actions=["write"],
                    constraints={"path_prefix": "/etc/hosts"},
                )
            ],
        )
        assert spec.is_denied("file:", "write", "/etc/hosts") is True
        assert spec.is_denied("file:", "write", "/etc/other") is False

    def test_denial_without_path_constraints_blocks_unconditionally(self):
        spec = PermissionSpec(
            agent_type="test",
            denied=[Capability(resource="admin:account", actions=["delete"])],
        )
        assert spec.is_denied("admin:account", "delete") is True
        assert spec.is_denied("admin:account", "delete", "anything") is True

    def test_multiple_denials_first_match_wins(self):
        spec = PermissionSpec(
            agent_type="test",
            denied=[
                Capability(resource="secret:openbao", actions=["read"], constraints={"openbao_paths": ["foo/*"]}),
                Capability(resource="secret:openbao", actions=["write"]),
            ],
        )
        assert spec.is_denied("secret:openbao", "read", "foo/bar") is True
        assert spec.is_denied("secret:openbao", "write") is True
        assert spec.is_denied("secret:openbao", "read", "other/baz") is False

    def test_denial_empty_path_prefix_denies_all(self):
        """Empty path_prefix means no constraint → deny fires for any path."""
        spec = PermissionSpec(
            agent_type="test",
            denied=[
                Capability(
                    resource="file:",
                    actions=["read"],
                    constraints={"path_prefix": ""},
                )
            ],
        )
        assert spec.is_denied("file:", "read", "/etc/hosts") is True

    def test_denial_both_openbao_and_prefix_present_prefers_openbao(self):
        spec = PermissionSpec(
            agent_type="test",
            denied=[
                Capability(
                    resource="secret:openbao",
                    actions=["read"],
                    constraints={"openbao_paths": ["restricted/*"], "path_prefix": "/ignored/"},
                )
            ],
        )
        assert spec.is_denied("secret:openbao", "read", "restricted/foo") is True
        assert spec.is_denied("secret:openbao", "read", "allowed/bar") is False


# ---------------------------------------------------------------------------
# check_capability
# ---------------------------------------------------------------------------


class TestCheckCapability:
    def test_denial_checked_before_grant(self):
        spec = PermissionSpec(
            agent_type="test",
            capabilities=[Capability(resource="admin:account", actions=["delete"])],
            denied=[Capability(resource="admin:account", actions=[])],
        )
        assert check_capability(spec, "admin:account", "delete") is False

    def test_no_capability_returns_false(self):
        spec = PermissionSpec(agent_type="test", capabilities=[])
        assert check_capability(spec, "admin:account", "delete") is False

    def test_missing_action_in_capability(self):
        spec = PermissionSpec(
            agent_type="test",
            capabilities=[Capability(resource="admin:account", actions=["read"])],
        )
        assert check_capability(spec, "admin:account", "delete") is False

    def test_denial_empty_actions_blocks_all(self):
        spec = PermissionSpec(
            agent_type="test",
            capabilities=[Capability(resource="admin:account", actions=["read", "write", "delete"])],
            denied=[Capability(resource="admin:account", actions=[])],
        )
        assert check_capability(spec, "admin:account", "read") is False
        assert check_capability(spec, "admin:account", "write") is False
        assert check_capability(spec, "admin:account", "delete") is False


# ---------------------------------------------------------------------------
# PermissionSpecParser.validate
# ---------------------------------------------------------------------------


class TestValidate:
    def test_valid_spec_no_errors(self):
        spec = PermissionSpec(
            agent_type="build",
            capabilities=[
                Capability(resource="secret:openbao", actions=["read"], constraints={"openbao_paths": ["*"]})
            ],
        )
        assert PermissionSpecParser.validate(spec) == []

    def test_unknown_resource_family(self):
        spec = PermissionSpec(
            agent_type="test",
            capabilities=[Capability(resource="unknown:thing", actions=["read"])],
        )
        errors = PermissionSpecParser.validate(spec)
        assert any("unknown resource type" in e.lower() for e in errors)

    def test_missing_required_constraint_openbao(self):
        spec = PermissionSpec(
            agent_type="test",
            capabilities=[Capability(resource="secret:openbao", actions=["read"])],
        )
        errors = PermissionSpecParser.validate(spec)
        assert any("openbao_paths" in e for e in errors)

    def test_missing_required_constraint_file(self):
        spec = PermissionSpec(
            agent_type="test",
            capabilities=[Capability(resource="file:", actions=["read"])],
        )
        errors = PermissionSpecParser.validate(spec)
        assert any("path_prefix" in e for e in errors)

    def test_net_capability_missing_both_allowed_hosts_and_ports(self):
        spec = PermissionSpec(
            agent_type="test",
            capabilities=[Capability(resource="net:", actions=["connect"])],
        )
        errors = PermissionSpecParser.validate(spec)
        assert any("allowed_hosts" in e.lower() and "allowed_ports" in e.lower() for e in errors)

    def test_net_capability_with_allowed_hosts_passes(self):
        spec = PermissionSpec(
            agent_type="test",
            capabilities=[
                Capability(
                    resource="net:egress:any",
                    actions=["connect"],
                    constraints={"allowed_hosts": ["api.example.com"]},
                )
            ],
        )
        errors = PermissionSpecParser.validate(spec)
        assert errors == []

    def test_capability_with_no_actions(self):
        spec = PermissionSpec(
            agent_type="test",
            capabilities=[Capability(resource="secret:openbao", actions=[], constraints={"openbao_paths": ["*"]})],
        )
        errors = PermissionSpecParser.validate(spec)
        assert any("must declare at least one action" in e for e in errors)

    def test_overlapping_capability_and_denial(self):
        spec = PermissionSpec(
            agent_type="test",
            capabilities=[Capability(resource="admin:account", actions=["delete", "create"])],
            denied=[Capability(resource="admin:account", actions=["delete"])],
        )
        errors = PermissionSpecParser.validate(spec)
        assert any("appears in both capabilities and denied" in e for e in errors)

    def test_no_overlap_different_actions(self):
        spec = PermissionSpec(
            agent_type="test",
            capabilities=[Capability(resource="admin:account", actions=["read"])],
            denied=[Capability(resource="admin:account", actions=["delete"])],
        )
        errors = PermissionSpecParser.validate(spec)
        assert not any("appears in both capabilities and denied" in e for e in errors)


# ---------------------------------------------------------------------------
# PermissionSpecParser.is_subset
# ---------------------------------------------------------------------------


class TestIsSubset:
    def test_exact_match(self):
        issuer = PermissionSpec(
            agent_type="primary",
            capabilities=[
                Capability(resource="secret:openbao", actions=["read"], constraints={"openbao_paths": ["*"]})
            ],
        )
        requested = PermissionSpec(
            agent_type="subagent",
            capabilities=[
                Capability(resource="secret:openbao", actions=["read"], constraints={"openbao_paths": ["*"]})
            ],
        )
        assert PermissionSpecParser.is_subset(requested, issuer) is True

    def test_narrower_actions_is_subset(self):
        issuer = PermissionSpec(
            agent_type="primary",
            capabilities=[
                Capability(
                    resource="secret:openbao",
                    actions=["read", "write", "delete"],
                    constraints={"openbao_paths": ["*"]},
                )
            ],
        )
        requested = PermissionSpec(
            agent_type="subagent",
            capabilities=[
                Capability(
                    resource="secret:openbao",
                    actions=["read"],
                    constraints={"openbao_paths": ["*"]},
                )
            ],
        )
        assert PermissionSpecParser.is_subset(requested, issuer) is True

    def test_wider_actions_not_subset(self):
        issuer = PermissionSpec(
            agent_type="primary",
            capabilities=[Capability(resource="admin:account", actions=["read"])],
        )
        requested = PermissionSpec(
            agent_type="subagent",
            capabilities=[Capability(resource="admin:account", actions=["read", "delete"])],
        )
        assert PermissionSpecParser.is_subset(requested, issuer) is False

    def test_missing_resource_not_subset(self):
        issuer = PermissionSpec(
            agent_type="primary",
            capabilities=[Capability(resource="secret:openbao", actions=["read"])],
        )
        requested = PermissionSpec(
            agent_type="subagent",
            capabilities=[Capability(resource="admin:account", actions=["delete"])],
        )
        assert PermissionSpecParser.is_subset(requested, issuer) is False

    def test_unscoped_denial_blocks_delegation(self):
        issuer = PermissionSpec(
            agent_type="primary",
            capabilities=[Capability(resource="secret:openbao", actions=["read"])],
            denied=[Capability(resource="secret:openbao", actions=["read"])],
        )
        requested = PermissionSpec(
            agent_type="subagent",
            capabilities=[Capability(resource="secret:openbao", actions=["read"])],
        )
        assert PermissionSpecParser.is_subset(requested, issuer) is False

    def test_scoped_denial_does_not_block_unscoped_delegation(self):
        issuer = PermissionSpec(
            agent_type="primary",
            capabilities=[Capability(resource="secret:openbao", actions=["read"])],
            denied=[
                Capability(
                    resource="secret:openbao",
                    actions=["read"],
                    constraints={"openbao_paths": ["restricted/*"]},
                )
            ],
        )
        requested = PermissionSpec(
            agent_type="subagent",
            capabilities=[Capability(resource="secret:openbao", actions=["read"])],
        )
        assert PermissionSpecParser.is_subset(requested, issuer) is True

    def test_unknown_resource_family_not_subset(self):
        issuer = PermissionSpec(
            agent_type="primary",
            capabilities=[Capability(resource="secret:openbao", actions=["read"])],
        )
        requested = PermissionSpec(
            agent_type="subagent",
            capabilities=[Capability(resource="weird:thing", actions=["poke"])],
        )
        assert PermissionSpecParser.is_subset(requested, issuer) is False

    def test_empty_actions_denial_blocks_all(self):
        issuer = PermissionSpec(
            agent_type="primary",
            capabilities=[Capability(resource="secret:openbao", actions=["read", "write"])],
            denied=[Capability(resource="secret:openbao", actions=[])],
        )
        requested = PermissionSpec(
            agent_type="subagent",
            capabilities=[Capability(resource="secret:openbao", actions=["read"])],
        )
        assert PermissionSpecParser.is_subset(requested, issuer) is False

    def test_file_constraint_narrower_subset(self):
        issuer = PermissionSpec(
            agent_type="primary",
            capabilities=[Capability(resource="file:", actions=["read"], constraints={"path_prefix": "/repo/"})],
        )
        requested = PermissionSpec(
            agent_type="subagent",
            capabilities=[Capability(resource="file:", actions=["read"], constraints={"path_prefix": "/repo/sub/"})],
        )
        assert PermissionSpecParser.is_subset(requested, issuer) is True

    def test_file_constraint_wider_not_subset(self):
        issuer = PermissionSpec(
            agent_type="primary",
            capabilities=[Capability(resource="file:", actions=["read"], constraints={"path_prefix": "/repo/sub/"})],
        )
        requested = PermissionSpec(
            agent_type="subagent",
            capabilities=[Capability(resource="file:", actions=["read"], constraints={"path_prefix": "/repo/"})],
        )
        assert PermissionSpecParser.is_subset(requested, issuer) is False


# ---------------------------------------------------------------------------
# PermissionSpecParser.intersection
# ---------------------------------------------------------------------------


class TestIntersection:
    def test_same_resource_actions_intersect(self):
        a = PermissionSpec(
            agent_type="primary",
            capabilities=[
                Capability(
                    resource="secret:openbao",
                    actions=["read", "write"],
                    constraints={"openbao_paths": ["*"]},
                )
            ],
        )
        b = PermissionSpec(
            agent_type="human-admin",
            capabilities=[
                Capability(
                    resource="secret:openbao",
                    actions=["write", "delete"],
                    constraints={"openbao_paths": ["*"]},
                )
            ],
        )
        result = PermissionSpecParser.intersection(a, b)
        assert len(result.capabilities) == 1
        cap = result.capabilities[0]
        assert cap.resource == "secret:openbao"
        assert set(cap.actions) == {"write"}

    def test_non_overlapping_actions_drops_capability(self):
        a = PermissionSpec(
            agent_type="primary",
            capabilities=[Capability(resource="admin:account", actions=["read"])],
        )
        b = PermissionSpec(
            agent_type="human-admin",
            capabilities=[Capability(resource="admin:account", actions=["delete"])],
        )
        result = PermissionSpecParser.intersection(a, b)
        assert len(result.capabilities) == 0

    def test_disjoint_resources_no_caps(self):
        a = PermissionSpec(
            agent_type="primary",
            capabilities=[Capability(resource="secret:openbao", actions=["read"])],
        )
        b = PermissionSpec(
            agent_type="human-admin",
            capabilities=[Capability(resource="file:", actions=["write"])],
        )
        result = PermissionSpecParser.intersection(a, b)
        assert len(result.capabilities) == 0

    def test_denied_union_present(self):
        a = PermissionSpec(
            agent_type="primary",
            denied=[Capability(resource="secret:openbao", actions=["read"])],
        )
        b = PermissionSpec(
            agent_type="human-admin",
            denied=[Capability(resource="net:", actions=["connect"])],
        )
        result = PermissionSpecParser.intersection(a, b)
        assert len(result.denied) == 2

    def test_ttl_is_min_of_both(self):
        a = PermissionSpec(agent_type="primary", max_sts_ttl_seconds=7200)
        b = PermissionSpec(agent_type="human-admin", max_sts_ttl_seconds=3600)
        result = PermissionSpecParser.intersection(a, b)
        assert result.max_sts_ttl_seconds == 3600

    def test_subject_is_sts_token(self):
        a = PermissionSpec(agent_type="primary")
        b = PermissionSpec(agent_type="human-admin")
        result = PermissionSpecParser.intersection(a, b)
        assert result.subject == PermissionSubject.STS_TOKEN
        assert result.agent_type == "sts_token"

    def test_file_prefix_intersection_narrower_wins(self):
        a = PermissionSpec(
            agent_type="primary",
            capabilities=[Capability(resource="file:", actions=["read"], constraints={"path_prefix": "/repo/"})],
        )
        b = PermissionSpec(
            agent_type="human-admin",
            capabilities=[Capability(resource="file:", actions=["read"], constraints={"path_prefix": "/repo/sub/"})],
        )
        result = PermissionSpecParser.intersection(a, b)
        assert len(result.capabilities) == 1
        assert result.capabilities[0].constraints["path_prefix"] == "/repo/sub/"

    def test_file_prefix_intersection_equal(self):
        a = PermissionSpec(
            agent_type="primary",
            capabilities=[Capability(resource="file:", actions=["read"], constraints={"path_prefix": "/repo/"})],
        )
        b = PermissionSpec(
            agent_type="human-admin",
            capabilities=[Capability(resource="file:", actions=["read"], constraints={"path_prefix": "/repo/"})],
        )
        result = PermissionSpecParser.intersection(a, b)
        assert len(result.capabilities) == 1
        assert result.capabilities[0].constraints["path_prefix"] == "/repo/"

    def test_file_prefix_disjoint_drops_capability(self):
        a = PermissionSpec(
            agent_type="primary",
            capabilities=[Capability(resource="file:", actions=["read"], constraints={"path_prefix": "/repo/"})],
        )
        b = PermissionSpec(
            agent_type="human-admin",
            capabilities=[Capability(resource="file:", actions=["read"], constraints={"path_prefix": "/etc/"})],
        )
        result = PermissionSpecParser.intersection(a, b)
        assert len(result.capabilities) == 0

    def test_file_prefix_near_miss_not_nested(self):
        a = PermissionSpec(
            agent_type="primary",
            capabilities=[Capability(resource="file:", actions=["read"], constraints={"path_prefix": "/etc/pass"})],
        )
        b = PermissionSpec(
            agent_type="human-admin",
            capabilities=[Capability(resource="file:", actions=["read"], constraints={"path_prefix": "/etc/passwd"})],
        )
        result = PermissionSpecParser.intersection(a, b)
        assert len(result.capabilities) == 0

    def test_net_constraint_intersection(self):
        a = PermissionSpec(
            agent_type="primary",
            capabilities=[
                Capability(
                    resource="net:egress:any",
                    actions=["connect"],
                    constraints={"allowed_hosts": ["a.com", "b.com"]},
                )
            ],
        )
        b = PermissionSpec(
            agent_type="human-admin",
            capabilities=[
                Capability(
                    resource="net:egress:any",
                    actions=["connect"],
                    constraints={"allowed_hosts": ["b.com", "c.com"]},
                )
            ],
        )
        result = PermissionSpecParser.intersection(a, b)
        assert len(result.capabilities) == 1
        assert set(cast(list[object], result.capabilities[0].constraints["allowed_hosts"])) == {"b.com"}

    def test_net_constraint_empty_intersection_drops(self):
        a = PermissionSpec(
            agent_type="primary",
            capabilities=[
                Capability(
                    resource="net:egress:any",
                    actions=["connect"],
                    constraints={"allowed_hosts": ["a.com"]},
                )
            ],
        )
        b = PermissionSpec(
            agent_type="human-admin",
            capabilities=[
                Capability(
                    resource="net:egress:any",
                    actions=["connect"],
                    constraints={"allowed_hosts": ["b.com"]},
                )
            ],
        )
        result = PermissionSpecParser.intersection(a, b)
        assert len(result.capabilities) == 0

    def test_net_only_one_side_constrains_preserves_restriction(self):
        a = PermissionSpec(
            agent_type="primary",
            capabilities=[
                Capability(
                    resource="net:egress:any",
                    actions=["connect"],
                    constraints={"allowed_hosts": ["a.com"]},
                )
            ],
        )
        b = PermissionSpec(
            agent_type="human-admin",
            capabilities=[Capability(resource="net:egress:any", actions=["connect"], constraints={})],
        )
        result = PermissionSpecParser.intersection(a, b)
        assert len(result.capabilities) == 1
        assert set(cast(list[object], result.capabilities[0].constraints["allowed_hosts"])) == {"a.com"}

    def test_openbao_paths_intersection(self):
        a = PermissionSpec(
            agent_type="primary",
            capabilities=[
                Capability(
                    resource="secret:openbao",
                    actions=["read"],
                    constraints={"openbao_paths": ["a/*", "shared/*"]},
                )
            ],
        )
        b = PermissionSpec(
            agent_type="human-admin",
            capabilities=[
                Capability(
                    resource="secret:openbao",
                    actions=["read"],
                    constraints={"openbao_paths": ["b/*", "shared/*"]},
                )
            ],
        )
        result = PermissionSpecParser.intersection(a, b)
        assert len(result.capabilities) == 1
        assert set(cast(list[object], result.capabilities[0].constraints["openbao_paths"])) == {"shared/*"}

    def test_empty_intersection_on_all_dimensions_drops(self):
        """Both sides have empty constraints → net intersection returns None → cap dropped."""
        a = PermissionSpec(
            agent_type="primary",
            capabilities=[
                Capability(
                    resource="net:egress:any",
                    actions=["connect"],
                    constraints={"allowed_hosts": [], "allowed_ports": []},
                )
            ],
        )
        b = PermissionSpec(
            agent_type="human-admin",
            capabilities=[
                Capability(
                    resource="net:egress:any",
                    actions=["connect"],
                    constraints={},
                )
            ],
        )
        result = PermissionSpecParser.intersection(a, b)
        assert len(result.capabilities) == 0


# ---------------------------------------------------------------------------
# union_denied
# ---------------------------------------------------------------------------


class TestUnionDenied:
    def test_empty_inputs(self):
        assert union_denied() == []

    def test_single_list_passthrough(self):
        caps = [Capability(resource="admin:account", actions=["delete"])]
        assert union_denied(caps) == caps

    def test_deduplicates_exact_duplicates(self):
        c = Capability(resource="admin:account", actions=["delete"])
        result = union_denied([c], [c])
        assert len(result) == 1

    def test_retains_differently_scoped_denials(self):
        a = Capability(resource="secret:openbao", actions=["read"], constraints={"openbao_paths": ["a/*"]})
        b = Capability(resource="secret:openbao", actions=["read"], constraints={"openbao_paths": ["b/*"]})
        result = union_denied([a], [b])
        assert len(result) == 2

    def test_empty_lists_in_input(self):
        result = union_denied([], [Capability(resource="admin:account", actions=["delete"])], [])
        assert len(result) == 1


# ---------------------------------------------------------------------------
# _denial_has_scope (tested via is_subset denial semantics)
# ---------------------------------------------------------------------------


class TestDenialHasScope:
    def test_unscoped_denial_blocks_globally(self):
        issuer = PermissionSpec(
            agent_type="primary",
            capabilities=[Capability(resource="secret:openbao", actions=["read"])],
            denied=[Capability(resource="secret:openbao", actions=["read"])],
        )
        requested = PermissionSpec(
            agent_type="subagent",
            capabilities=[Capability(resource="secret:openbao", actions=["read"])],
        )
        assert PermissionSpecParser.is_subset(requested, issuer) is False

    def test_scoped_openbao_denial_does_not_block_global_delegation(self):
        issuer = PermissionSpec(
            agent_type="primary",
            capabilities=[Capability(resource="secret:openbao", actions=["read"])],
            denied=[
                Capability(
                    resource="secret:openbao",
                    actions=["read"],
                    constraints={"openbao_paths": ["restricted/*"]},
                )
            ],
        )
        requested = PermissionSpec(
            agent_type="subagent",
            capabilities=[Capability(resource="secret:openbao", actions=["read"])],
        )
        assert PermissionSpecParser.is_subset(requested, issuer) is True

    def test_scoped_file_denial_does_not_block_global_delegation(self):
        issuer = PermissionSpec(
            agent_type="primary",
            capabilities=[Capability(resource="file:", actions=["read"])],
            denied=[
                Capability(
                    resource="file:",
                    actions=["read"],
                    constraints={"path_prefix": "/etc/"},
                )
            ],
        )
        requested = PermissionSpec(
            agent_type="subagent",
            capabilities=[Capability(resource="file:", actions=["read"])],
        )
        assert PermissionSpecParser.is_subset(requested, issuer) is True

    def test_scoped_net_denial_does_not_block_global_delegation(self):
        issuer = PermissionSpec(
            agent_type="primary",
            capabilities=[
                Capability(resource="net:egress:any", actions=["connect"], constraints={"allowed_hosts": ["*"]})
            ],
            denied=[
                Capability(
                    resource="net:egress:any",
                    actions=["connect"],
                    constraints={"allowed_hosts": ["evil.com"]},
                )
            ],
        )
        requested = PermissionSpec(
            agent_type="subagent",
            capabilities=[
                Capability(resource="net:egress:any", actions=["connect"], constraints={"allowed_hosts": ["*"]})
            ],
        )
        assert PermissionSpecParser.is_subset(requested, issuer) is True


# ---------------------------------------------------------------------------
# PermissionSpecParser.parse
# ---------------------------------------------------------------------------


class TestParse:
    def test_round_trip_basic_spec(self):
        yaml = """agent_type: build
capabilities:
- resource: secret:openbao
  actions:
  - read
  constraints:
    openbao_paths:
    - secret/data/gludd/build/*
"""
        spec = PermissionSpecParser.parse(yaml)
        assert spec.agent_type == "build"
        assert len(spec.capabilities) == 1
        assert spec.capabilities[0].resource == "secret:openbao"

    def test_parse_with_denials(self):
        yaml = """agent_type: primary
capabilities:
- resource: admin:account
  actions:
  - read
  - write
denied:
- resource: admin:account
  actions:
  - delete
"""
        spec = PermissionSpecParser.parse(yaml)
        assert len(spec.denied) == 1
        assert spec.denied[0].resource == "admin:account"

    def test_parse_with_subject_human(self):
        yaml = """agent_type: human-admin
subject: human
capabilities: []
"""
        spec = PermissionSpecParser.parse(yaml)
        assert spec.subject == PermissionSubject.HUMAN

    def test_parse_with_subject_agent_default(self):
        yaml = """agent_type: build
capabilities: []
"""
        spec = PermissionSpecParser.parse(yaml)
        assert spec.subject == PermissionSubject.AGENT

    def test_parse_unknown_subject_falls_back_to_agent(self):
        yaml = """agent_type: test
subject: spaceship_pilot
capabilities: []
"""
        spec = PermissionSpecParser.parse(yaml)
        assert spec.subject == PermissionSubject.AGENT

    def test_parse_empty_yaml(self):
        spec = PermissionSpecParser.parse("")
        assert spec.agent_type == "unknown"

    def test_parse_yaml_null(self):
        spec = PermissionSpecParser.parse("null")
        assert spec.agent_type == "unknown"
        assert spec.capabilities == []


# ---------------------------------------------------------------------------
# _constraints_narrower edges
# ---------------------------------------------------------------------------


class TestConstraintsNarrower:
    def test_file_unconstrained_wide_contains_any_narrow(self):
        issuer = PermissionSpec(
            agent_type="primary",
            capabilities=[Capability(resource="file:", actions=["read"])],
        )
        requested = PermissionSpec(
            agent_type="subagent",
            capabilities=[Capability(resource="file:", actions=["read"], constraints={"path_prefix": "/repo/"})],
        )
        assert PermissionSpecParser.is_subset(requested, issuer) is True

    def test_file_unconstrained_narrow_not_narrower_than_constrained_wide(self):
        issuer = PermissionSpec(
            agent_type="primary",
            capabilities=[Capability(resource="file:", actions=["read"], constraints={"path_prefix": "/repo/"})],
        )
        requested = PermissionSpec(
            agent_type="subagent",
            capabilities=[Capability(resource="file:", actions=["read"])],
        )
        assert PermissionSpecParser.is_subset(requested, issuer) is False

    def test_net_unconstrained_wide_allows_any_narrow(self):
        issuer = PermissionSpec(
            agent_type="primary",
            capabilities=[Capability(resource="net:egress:any", actions=["connect"])],
        )
        requested = PermissionSpec(
            agent_type="subagent",
            capabilities=[
                Capability(
                    resource="net:egress:any",
                    actions=["connect"],
                    constraints={"allowed_hosts": ["api.example.com"]},
                )
            ],
        )
        assert PermissionSpecParser.is_subset(requested, issuer) is True

    def test_openbao_narrower_path_set(self):
        issuer = PermissionSpec(
            agent_type="primary",
            capabilities=[
                Capability(
                    resource="secret:openbao",
                    actions=["read"],
                    constraints={"openbao_paths": ["a/*", "b/*", "c/*"]},
                )
            ],
        )
        requested = PermissionSpec(
            agent_type="subagent",
            capabilities=[
                Capability(
                    resource="secret:openbao",
                    actions=["read"],
                    constraints={"openbao_paths": ["a/*", "c/*"]},
                )
            ],
        )
        assert PermissionSpecParser.is_subset(requested, issuer) is True

    def test_openbao_wider_path_set_not_subset(self):
        issuer = PermissionSpec(
            agent_type="primary",
            capabilities=[
                Capability(
                    resource="secret:openbao",
                    actions=["read"],
                    constraints={"openbao_paths": ["a/*"]},
                )
            ],
        )
        requested = PermissionSpec(
            agent_type="subagent",
            capabilities=[
                Capability(
                    resource="secret:openbao",
                    actions=["read"],
                    constraints={"openbao_paths": ["a/*", "b/*"]},
                )
            ],
        )
        assert PermissionSpecParser.is_subset(requested, issuer) is False


# ---------------------------------------------------------------------------
# default_spec / default_human_spec
# ---------------------------------------------------------------------------


class TestDefaults:
    def test_default_spec_build_has_read_openbao(self):
        spec = default_spec("build")
        assert check_capability(spec, "secret:openbao", "read") is True
        assert check_capability(spec, "secret:openbao", "write") is False

    def test_default_spec_primary_has_read_openbao(self):
        spec = default_spec("primary")
        assert check_capability(spec, "secret:openbao", "read") is True

    def test_default_spec_subagent_has_no_capabilities(self):
        spec = default_spec("subagent")
        assert spec.capabilities == []

    def test_default_spec_unknown_falls_back_to_subagent(self):
        spec = default_spec("invalid_agent_type_xyz")
        assert spec.capabilities == []
        assert spec.agent_type == "subagent"

    def test_default_human_admin_full_perms(self):
        spec = default_human_spec("human-admin")
        assert check_capability(spec, "secret:openbao", "write") is True
        assert check_capability(spec, "secret:openbao", "read") is True

    def test_default_human_operator_read_only(self):
        spec = default_human_spec("human-operator")
        assert check_capability(spec, "secret:openbao", "read") is True
        assert check_capability(spec, "secret:openbao", "write") is False

    def test_default_human_viewer_limited_net(self):
        spec = default_human_spec("human-viewer")
        cap = spec.capability_for("net:egress:llm_api")
        assert cap is not None
        hosts = cast(list[object], cap.constraints.get("allowed_hosts", []))
        assert "api.anthropic.com" in hosts

    def test_default_human_unknown_falls_back_to_viewer(self):
        spec = default_human_spec("invalid_role_xyz")
        cap = spec.capability_for("net:egress:llm_api")
        assert cap is not None

    def test_psk_admin_has_all_required_caps(self):
        spec = _psk_admin_default_spec()
        for resource in ("admin:account", "admin:sts", "admin:permissions", "admin:compute", "admin:deploy"):
            assert spec.capability_for(resource) is not None, f"missing {resource}"


# ---------------------------------------------------------------------------
# PermissionSpec.capability_for
# ---------------------------------------------------------------------------


class TestCapabilityFor:
    def test_exact_match(self):
        spec = PermissionSpec(
            agent_type="test",
            capabilities=[Capability(resource="admin:account", actions=["read"])],
        )
        assert spec.capability_for("admin:account") is not None

    def test_no_match(self):
        spec = PermissionSpec(agent_type="test", capabilities=[])
        assert spec.capability_for("admin:account") is None

    def test_first_match_returned(self):
        spec = PermissionSpec(
            agent_type="test",
            capabilities=[
                Capability(resource="admin:account", actions=["read"]),
                Capability(resource="admin:account", actions=["write"]),
            ],
        )
        cap = spec.capability_for("admin:account")
        assert cap is not None
        assert cap.actions == ["read"]


# ---------------------------------------------------------------------------
# PermissionSubject enum
# ---------------------------------------------------------------------------


class TestPermissionSubject:
    def test_str_values(self):
        assert str(PermissionSubject.AGENT) == "agent"
        assert str(PermissionSubject.HUMAN) == "human"
        assert str(PermissionSubject.STS_TOKEN) == "sts_token"

    def test_constructor_from_string(self):
        assert PermissionSubject("agent") == PermissionSubject.AGENT
        assert PermissionSubject("human") == PermissionSubject.HUMAN
        assert PermissionSubject("sts_token") == PermissionSubject.STS_TOKEN

    def test_invalid_string_raises(self):
        with pytest.raises(ValueError):
            PermissionSubject("alien")
