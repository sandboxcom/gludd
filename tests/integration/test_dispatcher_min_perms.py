"""Integration tests for dispatcher permission intersection.

Pins that ``EventLoop._resolve_permission_spec`` returns the conservative
intersection of the human user's spec and the agent (queue) spec — a
subagent never gets more than the narrower of the two.
"""
from __future__ import annotations

from types import SimpleNamespace

from general_ludd.security.permissions import (
    Capability,
    PermissionSpec,
    PermissionSubject,
    default_human_spec,
)


def _make_loop(config: dict, human_spec: PermissionSpec | None = None) -> object:
    """Construct a minimal EventLoop-like object with the attributes
    ``_resolve_permission_spec`` reads.

    Avoids the full daemon construction (DB, repos, gateway) — we only need
    ``self.config`` and ``self._human_spec``.
    """
    from general_ludd.event_loop.loop import EventLoop

    loop = EventLoop.__new__(EventLoop)
    loop.config = config
    loop._human_spec = human_spec
    return loop


def _todo(queue: str = "core", todo_id: str = "TODO-1") -> SimpleNamespace:
    return SimpleNamespace(queue=queue, todo_id=todo_id)


def _queue_spec(
    caps: list[Capability],
    denied: list[Capability] | None = None,
) -> dict:
    return {
        "name": "core",
        "permission_spec": {
            "capabilities": [
                {
                    "resource": c.resource,
                    "actions": list(c.actions),
                    "constraints": dict(c.constraints),
                }
                for c in caps
            ],
            "denied": [
                {
                    "resource": d.resource,
                    "actions": list(d.actions),
                    "constraints": dict(d.constraints),
                }
                for d in (denied or [])
            ],
        },
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_subagent_receives_intersection_of_user_and_agent() -> None:
    """Agent grants rw; human grants read-only → effective is read-only."""
    agent_caps = [
        Capability(
            resource="file:repo",
            actions=["read", "write"],
            constraints={"path_prefix": "/repo/"},
        )
    ]
    human_spec = default_human_spec("human-viewer")  # read-only on /repo/
    loop = _make_loop(
        config={"queues": [_queue_spec(agent_caps)]},
        human_spec=human_spec,
    )
    spec = loop._resolve_permission_spec(_todo())
    assert spec is not None
    cap = spec.capability_for("file:repo")
    assert cap is not None
    assert cap.actions == ["read"], cap.actions
    assert spec.subject == PermissionSubject.STS_TOKEN


def test_human_viewer_session_caps_subagent_to_read_only() -> None:
    """Viewer session drops write actions on file:repo."""
    # NOTE: openbao_paths intersect by literal set membership — agent must
    # literally contain the viewer's pattern for the cap to survive
    # intersection. We include both patterns in the agent spec so the
    # secret cap survives (the point of this test is the action-narrowing
    # on file:repo, not the openbao glob semantics — see
    # test_intersection_openbao_paths_intersect for that).
    agent_caps = [
        Capability(
            resource="file:repo",
            actions=["read", "write"],
            constraints={"path_prefix": "/repo/"},
        ),
        Capability(
            resource="secret:openbao",
            actions=["read", "write"],
            constraints={
                "openbao_paths": [
                    "secret/data/gludd/*",
                    "secret/data/gludd/read-only/*",
                ]
            },
        ),
    ]
    loop = _make_loop(
        config={"queues": [_queue_spec(agent_caps)]},
        human_spec=default_human_spec("human-viewer"),
    )
    spec = loop._resolve_permission_spec(_todo())
    assert spec is not None
    # Viewer has secret/data/gludd/read-only/* — agent has it too (above),
    # so intersection narrows to read-only/*, actions read-only.
    secret_cap = spec.capability_for("secret:openbao")
    assert secret_cap is not None
    assert secret_cap.actions == ["read"]
    # file:repo write is dropped (viewer has only read).
    file_cap = spec.capability_for("file:repo")
    assert file_cap is not None
    assert file_cap.actions == ["read"]


def test_agent_cannot_dispatch_subagent_with_more_perms_than_human() -> None:
    """Even if the agent (queue) spec is permissive, the human spec wins.

    Formal intersection requires exact resource-string match (see
    docs/design/PERMISSION_SYSTEM.md §10), so the agent's resource must
    literally match the human's for the cap to survive. Here both sides
    declare ``file:repo``; the human's narrower ``/repo/`` prefix and
    read-only action win.
    """
    agent_caps = [
        Capability(
            resource="file:repo",
            actions=["read", "write"],
            constraints={"path_prefix": "/"},
        )
    ]
    loop = _make_loop(
        config={"queues": [_queue_spec(agent_caps)]},
        human_spec=default_human_spec("human-viewer"),
    )
    spec = loop._resolve_permission_spec(_todo())
    assert spec is not None
    assert len(spec.capabilities) == 1
    cap = spec.capabilities[0]
    # Narrowed to /repo/ (longer prefix wins).
    assert cap.constraints["path_prefix"] == "/repo/"
    # Read-only (viewer cannot write).
    assert cap.actions == ["read"]


def test_no_human_spec_returns_agent_spec_unchanged() -> None:
    """System tick (no human session) falls back to the agent spec alone."""
    agent_caps = [
        Capability(
            resource="file:repo",
            actions=["read", "write"],
            constraints={"path_prefix": "/repo/"},
        )
    ]
    loop = _make_loop(
        config={"queues": [_queue_spec(agent_caps)]},
        human_spec=None,
    )
    spec = loop._resolve_permission_spec(_todo())
    assert spec is not None
    cap = spec.capability_for("file:repo")
    assert cap is not None
    assert sorted(cap.actions) == ["read", "write"]


def test_default_human_role_from_config_when_no_session_spec() -> None:
    """When _human_spec is unset, default_human_role from config picks the role."""
    agent_caps = [
        Capability(
            resource="file:repo",
            actions=["read", "write"],
            constraints={"path_prefix": "/repo/"},
        )
    ]
    loop = _make_loop(
        config={
            "queues": [_queue_spec(agent_caps)],
            "default_human_role": "human-viewer",
        },
        human_spec=None,
    )
    spec = loop._resolve_permission_spec(_todo())
    assert spec is not None
    cap = spec.capability_for("file:repo")
    assert cap is not None
    # Viewer (from config default role) → read-only.
    assert cap.actions == ["read"]
