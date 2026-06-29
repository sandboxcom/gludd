"""Unit tests for PermissionSpec intersection and human subject support.

These pin the conservative intersection semantics: ``intersection(a, b)``
returns a spec whose capabilities are present in BOTH ``a`` and ``b``, each
narrowed to the more restrictive of the two, with ``denied`` unioned,
``max_sts_ttl_seconds`` minimized, and ``subject = STS_TOKEN``.
"""
from __future__ import annotations

from general_ludd.security.permissions import (
    Capability,
    PermissionSpec,
    PermissionSpecParser,
    PermissionSubject,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _file_cap(prefix: str, actions: list[str]) -> Capability:
    return Capability(
        resource="file:repo",
        actions=actions,
        constraints={"path_prefix": prefix},
    )


def _net_cap(hosts: list[str], actions: list[str] | None = None) -> Capability:
    return Capability(
        resource="net:egress:llm_api",
        actions=actions or ["connect"],
        constraints={"allowed_hosts": hosts},
    )


def _secret_cap(paths: list[str], actions: list[str]) -> Capability:
    return Capability(
        resource="secret:openbao",
        actions=actions,
        constraints={"openbao_paths": paths},
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_intersection_of_equal_specs_is_identical() -> None:
    spec = PermissionSpec(
        agent_type="primary",
        capabilities=[_file_cap("/repo/", ["read", "write"])],
        max_sts_ttl_seconds=3600,
    )
    out = PermissionSpecParser.intersection(spec, spec)
    assert [c.resource for c in out.capabilities] == ["file:repo"]
    cap = out.capabilities[0]
    assert sorted(cap.actions) == ["read", "write"]
    assert cap.constraints["path_prefix"] == "/repo/"


def test_intersection_narrows_path_prefix_to_longest_common() -> None:
    a = PermissionSpec(
        agent_type="human-operator",
        capabilities=[_file_cap("/repo/project-a/", ["read", "write"])],
    )
    b = PermissionSpec(
        agent_type="primary",
        capabilities=[_file_cap("/repo/project-a/sub/", ["read"])],
    )
    out = PermissionSpecParser.intersection(a, b)
    cap = out.capabilities[0]
    # Narrower prefix wins (longest common prefix of the two).
    assert cap.constraints["path_prefix"] == "/repo/project-a/sub/"
    # Action intersection.
    assert cap.actions == ["read"]


def test_intersection_intersects_allowed_hosts() -> None:
    a = PermissionSpec(
        agent_type="human-operator",
        capabilities=[_net_cap(["api.anthropic.com", "api.openai.com"])],
    )
    b = PermissionSpec(
        agent_type="primary",
        capabilities=[_net_cap(["api.openai.com", "api.z.ai"])],
    )
    out = PermissionSpecParser.intersection(a, b)
    cap = out.capabilities[0]
    assert set(cap.constraints["allowed_hosts"]) == {"api.openai.com"}


def test_intersection_unions_denied_lists() -> None:
    a = PermissionSpec(
        agent_type="human-operator",
        capabilities=[_file_cap("/repo/", ["read"])],
        denied=[Capability(resource="file:tmp", actions=["write"], constraints={"path_prefix": "/tmp/"})],
    )
    b = PermissionSpec(
        agent_type="primary",
        capabilities=[_file_cap("/repo/", ["read"])],
        denied=[Capability(resource="file:cache", actions=["write"], constraints={"path_prefix": "/cache/"})],
    )
    out = PermissionSpecParser.intersection(a, b)
    denied_resources = sorted(d.resource for d in out.denied)
    assert denied_resources == ["file:cache", "file:tmp"]


def test_intersection_takes_min_ttl() -> None:
    a = PermissionSpec(agent_type="x", capabilities=[_file_cap("/r/", ["read"])], max_sts_ttl_seconds=600)
    b = PermissionSpec(agent_type="y", capabilities=[_file_cap("/r/", ["read"])], max_sts_ttl_seconds=3600)
    out = PermissionSpecParser.intersection(a, b)
    assert out.max_sts_ttl_seconds == 600


def test_intersection_drops_capabilities_missing_in_either_side() -> None:
    a = PermissionSpec(
        agent_type="human-admin",
        capabilities=[
            _file_cap("/repo/", ["read"]),
            Capability(
                resource="secret:openbao",
                actions=["read"],
                constraints={"openbao_paths": ["secret/data/gludd/*"]},
            ),
        ],
    )
    b = PermissionSpec(
        agent_type="human-viewer",
        capabilities=[_file_cap("/repo/", ["read"])],
    )
    out = PermissionSpecParser.intersection(a, b)
    resources = [c.resource for c in out.capabilities]
    assert "file:repo" in resources
    assert "secret:openbao" not in resources


def test_intersection_subject_is_sts_token() -> None:
    a = PermissionSpec(
        agent_type="human-admin",
        subject=PermissionSubject.HUMAN,
        capabilities=[_file_cap("/r/", ["read"])],
    )
    b = PermissionSpec(
        agent_type="primary",
        subject=PermissionSubject.AGENT,
        capabilities=[_file_cap("/r/", ["read"])],
    )
    out = PermissionSpecParser.intersection(a, b)
    assert out.subject == PermissionSubject.STS_TOKEN


def test_intersection_with_human_viewer_drops_write_actions() -> None:
    viewer = PermissionSpec(
        agent_type="human-viewer",
        subject=PermissionSubject.HUMAN,
        capabilities=[_file_cap("/repo/", ["read"])],
    )
    primary = PermissionSpec(
        agent_type="primary",
        subject=PermissionSubject.AGENT,
        capabilities=[_file_cap("/repo/", ["read", "write"])],
    )
    out = PermissionSpecParser.intersection(viewer, primary)
    cap = out.capabilities[0]
    assert cap.actions == ["read"]


def test_intersection_records_parent_ids() -> None:
    a = PermissionSpec(
        agent_type="human-operator",
        subject=PermissionSubject.HUMAN,
        capabilities=[_file_cap("/r/", ["read"])],
        parent_agent_id=None,
    )
    b = PermissionSpec(
        agent_type="primary",
        subject=PermissionSubject.AGENT,
        capabilities=[_file_cap("/r/", ["read"])],
        parent_agent_id="agent-001",
    )
    out = PermissionSpecParser.intersection(a, b, parent_human_id="human-007")
    assert out.parent_agent_id == "agent-001"
    # ``parent_human_id`` is recorded on the result for audit.
    assert getattr(out, "parent_human_id", None) == "human-007"


def test_intersection_openbao_paths_intersect() -> None:
    a = PermissionSpec(
        agent_type="human-operator",
        capabilities=[_secret_cap(["secret/data/gludd/*", "secret/data/gludd/read-only/*"], ["read"])],
    )
    b = PermissionSpec(
        agent_type="primary",
        capabilities=[_secret_cap(["secret/data/gludd/read-only/*"], ["read"])],
    )
    out = PermissionSpecParser.intersection(a, b)
    cap = out.capabilities[0]
    assert set(cap.constraints["openbao_paths"]) == {"secret/data/gludd/read-only/*"}


def test_intersection_empty_when_no_capability_overlap() -> None:
    a = PermissionSpec(agent_type="x", capabilities=[_file_cap("/r/", ["read"])])
    b = PermissionSpec(agent_type="y", capabilities=[_net_cap(["h.example"])])
    out = PermissionSpecParser.intersection(a, b)
    assert out.capabilities == []


def test_permission_subject_enum_values() -> None:
    assert PermissionSubject.AGENT.value == "agent"
    assert PermissionSubject.HUMAN.value == "human"
    assert PermissionSubject.STS_TOKEN.value == "sts_token"


def test_permission_spec_subject_defaults_to_agent() -> None:
    spec = PermissionSpec(agent_type="primary", capabilities=[])
    assert spec.subject == PermissionSubject.AGENT


def test_parser_reads_subject_field() -> None:
    yaml_doc = """
agent_type: human-admin
subject: human
capabilities:
  - resource: file:repo
    actions: ["read", "write"]
    constraints:
      path_prefix: "/repo/"
"""
    spec = PermissionSpecParser.parse(yaml_doc)
    assert spec.subject == PermissionSubject.HUMAN
