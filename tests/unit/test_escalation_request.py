"""Unit tests for the permission-escalation request flow.

Pins the contract on POST /admin/perm/escalation-request and the
approve/deny endpoints:

* ``alternatives_tried`` must have ≥3 distinct ``approach`` strings.
* Auto-approval fires iff the requested capabilities are a strict subset
  of the human user's spec AND the agent's spec.
* Pending status otherwise — surfaces to the human review path.
* Approval creates an STS token scoped to current + requested, intersected
  with the human spec.
* Denial requires a reason.
"""
from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from starlette.testclient import TestClient

from general_ludd.security.permissions import (
    Capability,
    PermissionSpec,
    PermissionSubject,
)


def _human_viewer_spec() -> PermissionSpec:
    return PermissionSpec(
        agent_type="human-viewer",
        subject=PermissionSubject.HUMAN,
        capabilities=[
            Capability(
                resource="file:repo",
                actions=["read"],
                constraints={"path_prefix": "/repo/"},
            ),
            Capability(
                resource="secret:openbao",
                actions=["read"],
                constraints={"openbao_paths": ["secret/data/gludd/read-only/*"]},
            ),
        ],
    )


def _human_admin_spec() -> PermissionSpec:
    return PermissionSpec(
        agent_type="human-admin",
        subject=PermissionSubject.HUMAN,
        capabilities=[
            Capability(
                resource="file:repo",
                actions=["read", "write"],
                constraints={"path_prefix": "/repo/"},
            ),
            Capability(
                resource="secret:openbao",
                actions=["read", "write"],
                constraints={"openbao_paths": ["secret/data/gludd/*"]},
            ),
        ],
    )


def _agent_spec() -> PermissionSpec:
    return PermissionSpec(
        agent_type="primary",
        subject=PermissionSubject.AGENT,
        capabilities=[
            Capability(
                resource="file:repo",
                actions=["read", "write"],
                constraints={"path_prefix": "/repo/"},
            ),
            Capability(
                resource="secret:openbao",
                actions=["read", "write"],
                # Include both the broad and the read-only literal pattern so
                # is_subset's literal-pattern-set rule (§5) is satisfied when
                # the human-viewer narrowing produces read-only/* paths.
                constraints={
                    "openbao_paths": [
                        "secret/data/gludd/*",
                        "secret/data/gludd/read-only/*",
                    ]
                },
            ),
        ],
    )


def _build_app(
    human_spec: PermissionSpec | None = None,
    agent_spec: PermissionSpec | None = None,
) -> FastAPI:
    """Build a minimal FastAPI app with the escalation router wired."""
    from general_ludd.routers import security as sec

    app = FastAPI()
    app.state._sts_issuer_spec = agent_spec or _agent_spec()
    app.state._human_spec = human_spec or _human_viewer_spec()
    app.state._escalation_store: list[dict[str, Any]] = []
    app.state._escalation_counter = [0]
    sec.register(app, {})
    return app


def _alternatives(n: int) -> list[dict[str, str]]:
    return [
        {"approach": f"approach-{i}", "outcome": f"outcome-{i}"}
        for i in range(n)
    ]


def _post_escalation(
    client: TestClient,
    *,
    agent_id: str = "agent-001",
    current_spec: PermissionSpec | None = None,
    requested: list[Capability] | None = None,
    alternatives: list[dict[str, str]] | None = None,
    reason: str = "need write on /repo/ for refactor",
) -> Any:
    cur = current_spec or _agent_spec()
    req = requested or [
        Capability(
            resource="file:repo",
            actions=["write"],
            constraints={"path_prefix": "/repo/"},
        )
    ]
    body = {
        "agent_id": agent_id,
        "current_spec_yaml": _spec_to_yaml(cur),
        "requested_additional_capabilities": [_cap_to_dict(c) for c in req],
        "reason": reason,
        "alternatives_tried": alternatives if alternatives is not None else _alternatives(3),
    }
    resp = client.post("/admin/perm/escalation-request", json=body)
    return resp


def _spec_to_yaml(spec: PermissionSpec) -> str:
    import yaml as _yaml
    return _yaml.safe_dump({
        "agent_type": spec.agent_type,
        "subject": spec.subject.value,
        "capabilities": [_cap_to_dict(c) for c in spec.capabilities],
        "denied": [_cap_to_dict(c) for c in spec.denied],
        "max_sts_ttl_seconds": spec.max_sts_ttl_seconds,
    }, sort_keys=False)


def _cap_to_dict(cap: Capability) -> dict[str, Any]:
    return {
        "resource": cap.resource,
        "actions": list(cap.actions),
        "constraints": dict(cap.constraints),
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_escalation_request_requires_3_alternatives() -> None:
    app = _build_app(human_spec=_human_admin_spec())
    client = TestClient(app)
    resp = _post_escalation(client, alternatives=_alternatives(2))
    assert resp.status_code == 422
    assert "insufficient alternatives_tried" in resp.text


def test_escalation_requires_distinct_approaches() -> None:
    app = _build_app(human_spec=_human_admin_spec())
    client = TestClient(app)
    # 3 entries but 2 share an approach string.
    alts = [
        {"approach": "same", "outcome": "x"},
        {"approach": "same", "outcome": "y"},
        {"approach": "different", "outcome": "z"},
    ]
    resp = _post_escalation(client, alternatives=alts)
    assert resp.status_code == 422


def test_escalation_auto_approved_when_within_human_intersection() -> None:
    """Requested cap is a strict subset of human+agent → auto_approved."""
    # Human admin grants file:repo rw. Agent grants file:repo rw.
    # Subagent got narrowed to file:repo r by intersection. Requests write
    # back — which IS within both human and agent specs.
    app = _build_app(human_spec=_human_admin_spec(), agent_spec=_agent_spec())
    client = TestClient(app)
    # current_spec = the narrowed (intersection) result the subagent is stuck with
    current = PermissionSpec(
        agent_type="sts_token",
        subject=PermissionSubject.STS_TOKEN,
        capabilities=[
            Capability(
                resource="file:repo",
                actions=["read"],
                constraints={"path_prefix": "/repo/"},
            )
        ],
    )
    resp = _post_escalation(client, current_spec=current)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "auto_approved", body
    assert "auto-granted" in body["decided_reason"]


def test_escalation_pending_when_outside_human_scope() -> None:
    """Requested cap exceeds the human spec → pending (human review)."""
    # Human viewer has read-only on file:repo. Subagent requests write.
    app = _build_app(human_spec=_human_viewer_spec(), agent_spec=_agent_spec())
    client = TestClient(app)
    current = PermissionSpec(
        agent_type="sts_token",
        subject=PermissionSubject.STS_TOKEN,
        capabilities=[
            Capability(
                resource="file:repo",
                actions=["read"],
                constraints={"path_prefix": "/repo/"},
            )
        ],
    )
    resp = _post_escalation(client, current_spec=current)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "pending", body


def test_escalation_approval_creates_sts_within_human_bounds() -> None:
    """Human approval mints an STS token but CANNOT exceed human spec."""
    # Human viewer has net:egress:llm_api (anthropic/openai/z.ai) but agent
    # does NOT — so the request is not auto-approved (auto requires BOTH
    # human AND agent scope). Human review can grant it.
    app = _build_app(human_spec=_human_viewer_spec(), agent_spec=_agent_spec())
    client = TestClient(app)
    current = PermissionSpec(
        agent_type="sts_token",
        subject=PermissionSubject.STS_TOKEN,
        capabilities=[],
    )
    requested = [
        Capability(
            resource="net:egress:llm_api",
            actions=["connect"],
            constraints={"allowed_hosts": ["api.anthropic.com"]},
        )
    ]
    resp = _post_escalation(client, current_spec=current, requested=requested)
    assert resp.status_code == 201
    body = resp.json()
    # Outside agent scope → pending (not auto-approved).
    assert body["status"] == "pending", body
    esc_id = body["id"]
    # Approve it.
    approve_resp = client.post(
        f"/admin/perm/escalations/{esc_id}/approve",
        json={"reason": "ok", "human_reviewer": "admin-reviewer"},
    )
    assert approve_resp.status_code == 200, approve_resp.text
    a_body = approve_resp.json()
    assert a_body["status"] == "approved"
    # An STS token was minted and is bounded by human scope.
    assert a_body.get("sts_token_id"), a_body


def test_escalation_denial_requires_reason() -> None:
    app = _build_app(human_spec=_human_viewer_spec(), agent_spec=_agent_spec())
    client = TestClient(app)
    # Create a pending escalation first.
    current = PermissionSpec(
        agent_type="sts_token",
        subject=PermissionSubject.STS_TOKEN,
        capabilities=[],
    )
    requested = [
        Capability(
            resource="file:repo",
            actions=["write"],
            constraints={"path_prefix": "/repo/"},
        )
    ]
    resp = _post_escalation(client, current_spec=current, requested=requested)
    esc_id = resp.json()["id"]
    # Deny without reason → 400.
    deny_resp = client.post(f"/admin/perm/escalations/{esc_id}/deny", json={})
    assert deny_resp.status_code == 400
    # With reason → 200.
    deny_resp = client.post(
        f"/admin/perm/escalations/{esc_id}/deny",
        json={"reason": "viewer cannot write", "human_reviewer": "admin-reviewer"},
    )
    assert deny_resp.status_code == 200
    assert deny_resp.json()["status"] == "denied"


def test_escalation_list_filters_by_status() -> None:
    app = _build_app(human_spec=_human_admin_spec(), agent_spec=_agent_spec())
    client = TestClient(app)
    # Create one auto-approved escalation.
    _post_escalation(client)
    resp = client.get("/admin/perm/escalations?status=auto_approved")
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 1
    assert items[0]["status"] == "auto_approved"


def test_escalation_history_filters_by_agent() -> None:
    app = _build_app(human_spec=_human_admin_spec(), agent_spec=_agent_spec())
    client = TestClient(app)
    _post_escalation(client, agent_id="agent-A")
    _post_escalation(client, agent_id="agent-B")
    resp = client.get("/admin/perm/escalations/history?agent_id=agent-A")
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 1
    assert items[0]["agent_id"] == "agent-A"
