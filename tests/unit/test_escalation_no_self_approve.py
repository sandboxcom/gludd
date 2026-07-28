"""Unit tests for the no-self-approve guardrail on escalation approve/deny.

Verifies that POST /admin/perm/escalations/{id}/approve and
POST /admin/perm/escalations/{id}/deny both reject requests where the
caller attempts to self-approve (human_reviewer matches agent_id) or
omits human_reviewer entirely.
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
        ],
    )


def _build_app(
    human_spec: PermissionSpec | None = None,
    agent_spec: PermissionSpec | None = None,
) -> FastAPI:
    from general_ludd.routers import security as sec

    app = FastAPI()
    app.state._sts_issuer_spec = agent_spec or _agent_spec()
    app.state._human_spec = human_spec or _human_viewer_spec()
    app.state._escalation_store: list[dict[str, Any]] = []
    app.state._escalation_counter = [0]
    sec.register(app, {})
    return app


def _alternatives(n: int) -> list[dict[str, str]]:
    return [{"approach": f"approach-{i}", "outcome": f"outcome-{i}"} for i in range(n)]


def _cap_to_dict(cap: Capability) -> dict[str, Any]:
    return {
        "resource": cap.resource,
        "actions": list(cap.actions),
        "constraints": dict(cap.constraints),
    }


def _spec_to_yaml(spec: PermissionSpec) -> str:
    import yaml as _yaml

    return _yaml.safe_dump(
        {
            "agent_type": spec.agent_type,
            "subject": spec.subject.value,
            "capabilities": [_cap_to_dict(c) for c in spec.capabilities],
            "denied": [_cap_to_dict(c) for c in spec.denied],
            "max_sts_ttl_seconds": spec.max_sts_ttl_seconds,
        },
        sort_keys=False,
    )


def _post_escalation(
    client: TestClient,
    *,
    agent_id: str = "agent-001",
) -> Any:
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
    body = {
        "agent_id": agent_id,
        "current_spec_yaml": _spec_to_yaml(current),
        "requested_additional_capabilities": [_cap_to_dict(c) for c in requested],
        "reason": "need write access",
        "alternatives_tried": _alternatives(3),
    }
    resp = client.post("/admin/perm/escalation-request", json=body)
    assert resp.status_code == 201, resp.text
    return resp


def _approve(client: TestClient, esc_id: int, **kw: Any) -> Any:
    return client.post(
        f"/admin/perm/escalations/{esc_id}/approve",
        json=dict(kw),
    )


def _deny(client: TestClient, esc_id: int, **kw: Any) -> Any:
    return client.post(
        f"/admin/perm/escalations/{esc_id}/deny",
        json=dict(kw),
    )


# ---------------------------------------------------------------------------
# Approve endpoint — no-self-approve guardrail
# ---------------------------------------------------------------------------


def test_approve_rejects_self_approve() -> None:
    app = _build_app()
    client = TestClient(app)
    resp = _post_escalation(client, agent_id="agent-self")
    esc_id = resp.json()["id"]

    a_resp = _approve(client, esc_id, reason="ok", human_reviewer="agent-self")
    assert a_resp.status_code == 403, a_resp.text
    body = a_resp.json()
    assert body["error"] == "self_approval_forbidden"
    assert "different from the requesting agent" in body["detail"]


def test_approve_rejects_missing_reviewer() -> None:
    app = _build_app()
    client = TestClient(app)
    resp = _post_escalation(client)
    esc_id = resp.json()["id"]

    a_resp = _approve(client, esc_id, reason="ok")
    assert a_resp.status_code == 403, a_resp.text
    assert a_resp.json()["error"] == "self_approval_forbidden"


def test_approve_rejects_empty_reviewer() -> None:
    app = _build_app()
    client = TestClient(app)
    resp = _post_escalation(client)
    esc_id = resp.json()["id"]

    a_resp = _approve(client, esc_id, reason="ok", human_reviewer="")
    assert a_resp.status_code == 403, a_resp.text
    assert a_resp.json()["error"] == "self_approval_forbidden"


def test_approve_rejects_whitespace_only_reviewer() -> None:
    app = _build_app()
    client = TestClient(app)
    resp = _post_escalation(client)
    esc_id = resp.json()["id"]

    a_resp = _approve(client, esc_id, reason="ok", human_reviewer="   ")
    assert a_resp.status_code == 403, a_resp.text
    assert a_resp.json()["error"] == "self_approval_forbidden"


def test_approve_rejects_case_variant_self_approve() -> None:
    app = _build_app()
    client = TestClient(app)
    resp = _post_escalation(client, agent_id="Agent-Self")
    esc_id = resp.json()["id"]

    a_resp = _approve(client, esc_id, reason="ok", human_reviewer="agent-self")
    assert a_resp.status_code == 403, a_resp.text
    assert a_resp.json()["error"] == "self_approval_forbidden"


def test_approve_rejects_padded_self_approve() -> None:
    app = _build_app()
    client = TestClient(app)
    resp = _post_escalation(client, agent_id="agent-self")
    esc_id = resp.json()["id"]

    a_resp = _approve(client, esc_id, reason="ok", human_reviewer=" agent-self ")
    assert a_resp.status_code == 403, a_resp.text
    assert a_resp.json()["error"] == "self_approval_forbidden"


def test_approve_allows_different_reviewer() -> None:
    app = _build_app()
    client = TestClient(app)
    resp = _post_escalation(client, agent_id="agent-abc")
    esc_id = resp.json()["id"]

    a_resp = _approve(client, esc_id, reason="ok", human_reviewer="alice")
    assert a_resp.status_code == 200, a_resp.text
    assert a_resp.json()["status"] == "approved"


# ---------------------------------------------------------------------------
# Deny endpoint — no-self-approve guardrail
# ---------------------------------------------------------------------------


def test_deny_rejects_self_deny() -> None:
    app = _build_app()
    client = TestClient(app)
    resp = _post_escalation(client, agent_id="agent-self-denier")
    esc_id = resp.json()["id"]

    d_resp = _deny(client, esc_id, reason="no", human_reviewer="agent-self-denier")
    assert d_resp.status_code == 403, d_resp.text
    body = d_resp.json()
    assert body["error"] == "self_denial_forbidden"
    assert "different from the requesting agent" in body["detail"]


def test_deny_rejects_missing_reviewer() -> None:
    app = _build_app()
    client = TestClient(app)
    resp = _post_escalation(client)
    esc_id = resp.json()["id"]

    d_resp = _deny(client, esc_id, reason="not needed")
    assert d_resp.status_code == 403, d_resp.text
    assert d_resp.json()["error"] == "self_denial_forbidden"


def test_deny_rejects_empty_reviewer() -> None:
    app = _build_app()
    client = TestClient(app)
    resp = _post_escalation(client)
    esc_id = resp.json()["id"]

    d_resp = _deny(client, esc_id, reason="not needed")
    assert d_resp.status_code == 403, d_resp.text
    assert d_resp.json()["error"] == "self_denial_forbidden"


def test_deny_rejects_whitespace_only_reviewer() -> None:
    app = _build_app()
    client = TestClient(app)
    resp = _post_escalation(client)
    esc_id = resp.json()["id"]

    d_resp = _deny(client, esc_id, reason="no", human_reviewer="\t  ")
    assert d_resp.status_code == 403, d_resp.text
    assert d_resp.json()["error"] == "self_denial_forbidden"


def test_deny_rejects_case_variant_self_deny() -> None:
    app = _build_app()
    client = TestClient(app)
    resp = _post_escalation(client, agent_id="Agent-Self-Denier")
    esc_id = resp.json()["id"]

    d_resp = _deny(client, esc_id, reason="no", human_reviewer="agent-self-denier")
    assert d_resp.status_code == 403, d_resp.text
    assert d_resp.json()["error"] == "self_denial_forbidden"


def test_deny_allows_different_reviewer() -> None:
    app = _build_app()
    client = TestClient(app)
    resp = _post_escalation(client, agent_id="agent-xyz")
    esc_id = resp.json()["id"]

    d_resp = _deny(client, esc_id, reason="not authorized", human_reviewer="bob")
    assert d_resp.status_code == 200, d_resp.text
    assert d_resp.json()["status"] == "denied"


# ---------------------------------------------------------------------------
# Edge: auto-approved escalations are not affected
# ---------------------------------------------------------------------------


def test_auto_approved_not_affected() -> None:
    app = _build_app(human_spec=_agent_spec(), agent_spec=_agent_spec())
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
    body = {
        "agent_id": "agent-auto",
        "current_spec_yaml": _spec_to_yaml(current),
        "requested_additional_capabilities": [
            _cap_to_dict(
                Capability(
                    resource="file:repo",
                    actions=["write"],
                    constraints={"path_prefix": "/repo/"},
                )
            )
        ],
        "reason": "need write",
        "alternatives_tried": _alternatives(3),
    }
    resp = client.post("/admin/perm/escalation-request", json=body)
    assert resp.status_code == 201
    assert resp.json()["status"] == "auto_approved"


# ---------------------------------------------------------------------------
# Human-todo self-resolution rejection
# ---------------------------------------------------------------------------

_HTODO_APP: FastAPI | None = None
_HTODO_CLIENT: TestClient | None = None


def _htodo_setup(human_spec: PermissionSpec | None = None) -> TestClient:
    """Build an app with the human-todo router registered and a mock DB session."""
    global _HTODO_APP, _HTODO_CLIENT
    from unittest.mock import AsyncMock, MagicMock

    app = FastAPI()
    from general_ludd.db.repository import HumanTodoRepository

<<<<<<< Updated upstream
    MagicMock(spec=HumanTodoRepository)
=======
    mock_repo = MagicMock(spec=HumanTodoRepository)
>>>>>>> Stashed changes
    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=None)

    async def _mock_create(**kw: Any) -> Any:
        from general_ludd.db.models import HumanTodoModel

        return HumanTodoModel(
            id="HTODO-TEST001",
            agent_id=kw.get("agent_id", "agent-001"),
            title=kw.get("title", "test"),
            body=kw.get("body", ""),
            category=kw.get("category", "permission_escalation"),
            priority=kw.get("priority", "high"),
            status="open",
            tags=kw.get("tags", "[]"),
        )

    from general_ludd.db.repository import TodoRepository

<<<<<<< Updated upstream
    MagicMock(spec=TodoRepository)
=======
    mock_todo_repo = MagicMock(spec=TodoRepository)
>>>>>>> Stashed changes

    app.state._session_factory = lambda: mock_session
    app.state._escalation_store = []
    app.state._escalation_counter = [0]
    app.state._sts_issuer_spec = _agent_spec()
    app.state._human_spec = human_spec or _human_viewer_spec()

    from general_ludd.routers import human_todos as htd
    from general_ludd.routers import security as sec

    sec.register(app, {})
    htd.register(app, {})

    _HTODO_APP = app
    _HTODO_CLIENT = TestClient(app)
    return _HTODO_CLIENT


def test_human_todo_done_rejects_self_resolution() -> None:
    """PATCH human-todo to done with human_resolver == agent_id is rejected."""
    from unittest.mock import AsyncMock, MagicMock, patch

    app = FastAPI()
    from general_ludd.routers import human_todos as htd
    from general_ludd.routers import security as sec

    sec.register(app, {})
    htd.register(app, {})

    from general_ludd.db.repository import HumanTodoRepository, TodoRepository

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=None)

    mock_repo = MagicMock(spec=HumanTodoRepository)
    mock_todo_repo = MagicMock(spec=TodoRepository)

    app.state._session_factory = lambda: mock_session
    app.state._escalation_store = []
    app.state._escalation_counter = [0]
    app.state._sts_issuer_spec = _agent_spec()
    app.state._human_spec = _human_viewer_spec()

<<<<<<< Updated upstream
    from datetime import datetime

    from general_ludd.db.models import HumanTodoModel
=======
    from general_ludd.db.models import HumanTodoModel
    from datetime import datetime
>>>>>>> Stashed changes

    row = HumanTodoModel(
        id="HTODO-TEST001",
        agent_id="agent-001",
        title="Test escalation",
        body="needs approval",
        category="permission_escalation",
        priority="high",
        status="open",
        tags='["escalation:1"]',
        created_at=datetime(2025, 1, 1),
        updated_at=datetime(2025, 1, 1),
    )
    mock_repo.get = AsyncMock(return_value=row)

<<<<<<< Updated upstream
    with (
        patch("general_ludd.routers.human_todos.HumanTodoRepository", return_value=mock_repo),
        patch("general_ludd.routers.human_todos.TodoRepository", return_value=mock_todo_repo),
    ):
        client = TestClient(app)
        resp = client.patch(
            "/api/human-todos/HTODO-TEST001",
            json={
                "status": "done",
                "human_resolver": "agent-001",
                "human_resolution": "I approve my own request",
            },
        )
=======
    with patch("general_ludd.routers.human_todos.HumanTodoRepository", return_value=mock_repo):
        with patch("general_ludd.routers.human_todos.TodoRepository", return_value=mock_todo_repo):
            client = TestClient(app)
            resp = client.patch(
                "/api/human-todos/HTODO-TEST001",
                json={
                    "status": "done",
                    "human_resolver": "agent-001",
                    "human_resolution": "I approve my own request",
                },
            )
>>>>>>> Stashed changes
    assert resp.status_code == 403, resp.text
    body = resp.json()
    assert "self_resolution_forbidden" in body["detail"]


def test_human_todo_dismissed_rejects_self_resolution() -> None:
    """PATCH human-todo to dismissed with human_resolver == agent_id is rejected."""
    from unittest.mock import AsyncMock, MagicMock, patch

    app = FastAPI()
    from general_ludd.routers import human_todos as htd
    from general_ludd.routers import security as sec

    sec.register(app, {})
    htd.register(app, {})

    from general_ludd.db.repository import HumanTodoRepository, TodoRepository

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=None)

    mock_repo = MagicMock(spec=HumanTodoRepository)
    mock_todo_repo = MagicMock(spec=TodoRepository)

    app.state._session_factory = lambda: mock_session
    app.state._escalation_store = []
    app.state._escalation_counter = [0]
    app.state._sts_issuer_spec = _agent_spec()
    app.state._human_spec = _human_viewer_spec()

<<<<<<< Updated upstream
    from datetime import datetime

    from general_ludd.db.models import HumanTodoModel
=======
    from general_ludd.db.models import HumanTodoModel
    from datetime import datetime
>>>>>>> Stashed changes

    row = HumanTodoModel(
        id="HTODO-TEST002",
        agent_id="agent-002",
        title="Test escalation",
        body="needs approval",
        category="permission_escalation",
        priority="high",
        status="open",
        tags='["escalation:2"]',
        created_at=datetime(2025, 1, 1),
        updated_at=datetime(2025, 1, 1),
    )
    mock_repo.get = AsyncMock(return_value=row)

<<<<<<< Updated upstream
    with (
        patch("general_ludd.routers.human_todos.HumanTodoRepository", return_value=mock_repo),
        patch("general_ludd.routers.human_todos.TodoRepository", return_value=mock_todo_repo),
    ):
        client = TestClient(app)
        resp = client.patch(
            "/api/human-todos/HTODO-TEST002",
            json={
                "status": "dismissed",
                "human_resolver": "agent-002",
                "human_resolution": "Not needed",
            },
        )
=======
    with patch("general_ludd.routers.human_todos.HumanTodoRepository", return_value=mock_repo):
        with patch("general_ludd.routers.human_todos.TodoRepository", return_value=mock_todo_repo):
            client = TestClient(app)
            resp = client.patch(
                "/api/human-todos/HTODO-TEST002",
                json={
                    "status": "dismissed",
                    "human_resolver": "agent-002",
                    "human_resolution": "Not needed",
                },
            )
>>>>>>> Stashed changes
    assert resp.status_code == 403, resp.text
    body = resp.json()
    assert "self_resolution_forbidden" in body["detail"]


def test_human_todo_done_allows_different_resolver() -> None:
    """PATCH human-todo to done with different human_resolver is allowed."""
    from unittest.mock import AsyncMock, MagicMock, patch

    app = FastAPI()
    from general_ludd.routers import human_todos as htd
    from general_ludd.routers import security as sec

    sec.register(app, {})
    htd.register(app, {})

    from general_ludd.db.repository import HumanTodoRepository, TodoRepository

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=None)

    mock_repo = MagicMock(spec=HumanTodoRepository)
    mock_todo_repo = MagicMock(spec=TodoRepository)

    app.state._session_factory = lambda: mock_session
    app.state._escalation_store = []
    app.state._escalation_counter = [0]
    app.state._sts_issuer_spec = _agent_spec()
    app.state._human_spec = _human_viewer_spec()

<<<<<<< Updated upstream
    from datetime import datetime

    from general_ludd.db.models import HumanTodoModel
=======
    from general_ludd.db.models import HumanTodoModel
    from datetime import datetime
>>>>>>> Stashed changes

    row = HumanTodoModel(
        id="HTODO-TEST003",
        agent_id="agent-003",
        title="Test escalation",
        body="needs approval",
        category="permission_escalation",
        priority="high",
        status="open",
        tags='["escalation:3"]',
        created_at=datetime(2025, 1, 1),
        updated_at=datetime(2025, 1, 1),
    )
    mock_repo.get = AsyncMock(return_value=row)
    mock_repo.mark_done = AsyncMock(return_value=row)

<<<<<<< Updated upstream
    with (
        patch("general_ludd.routers.human_todos.HumanTodoRepository", return_value=mock_repo),
        patch("general_ludd.routers.human_todos.TodoRepository", return_value=mock_todo_repo),
    ):
        client = TestClient(app)
        resp = client.patch(
            "/api/human-todos/HTODO-TEST003",
            json={
                "status": "done",
                "human_resolver": "alice-operator",
                "human_resolution": "Approved",
            },
        )
=======
    with patch("general_ludd.routers.human_todos.HumanTodoRepository", return_value=mock_repo):
        with patch("general_ludd.routers.human_todos.TodoRepository", return_value=mock_todo_repo):
            client = TestClient(app)
            resp = client.patch(
                "/api/human-todos/HTODO-TEST003",
                json={
                    "status": "done",
                    "human_resolver": "alice-operator",
                    "human_resolution": "Approved",
                },
            )
>>>>>>> Stashed changes
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "open"


def test_human_todo_case_insensitive_self_resolution() -> None:
    """Case-insensitive match also rejects self-resolution."""
    from unittest.mock import AsyncMock, MagicMock, patch

    app = FastAPI()
    from general_ludd.routers import human_todos as htd
    from general_ludd.routers import security as sec

    sec.register(app, {})
    htd.register(app, {})

    from general_ludd.db.repository import HumanTodoRepository, TodoRepository

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=None)

    mock_repo = MagicMock(spec=HumanTodoRepository)
    mock_todo_repo = MagicMock(spec=TodoRepository)

    app.state._session_factory = lambda: mock_session
    app.state._escalation_store = []
    app.state._escalation_counter = [0]
    app.state._sts_issuer_spec = _agent_spec()
    app.state._human_spec = _human_viewer_spec()

<<<<<<< Updated upstream
    from datetime import datetime

    from general_ludd.db.models import HumanTodoModel
=======
    from general_ludd.db.models import HumanTodoModel
    from datetime import datetime
>>>>>>> Stashed changes

    row = HumanTodoModel(
        id="HTODO-TEST004",
        agent_id="Agent-004",
        title="Test escalation",
        body="needs approval",
        category="permission_escalation",
        priority="high",
        status="open",
        tags='["escalation:4"]',
        created_at=datetime(2025, 1, 1),
        updated_at=datetime(2025, 1, 1),
    )
    mock_repo.get = AsyncMock(return_value=row)

<<<<<<< Updated upstream
    with (
        patch("general_ludd.routers.human_todos.HumanTodoRepository", return_value=mock_repo),
        patch("general_ludd.routers.human_todos.TodoRepository", return_value=mock_todo_repo),
    ):
        client = TestClient(app)
        resp = client.patch(
            "/api/human-todos/HTODO-TEST004",
            json={
                "status": "done",
                "human_resolver": "agent-004",
                "human_resolution": "I'm the same agent",
            },
        )
=======
    with patch("general_ludd.routers.human_todos.HumanTodoRepository", return_value=mock_repo):
        with patch("general_ludd.routers.human_todos.TodoRepository", return_value=mock_todo_repo):
            client = TestClient(app)
            resp = client.patch(
                "/api/human-todos/HTODO-TEST004",
                json={
                    "status": "done",
                    "human_resolver": "agent-004",
                    "human_resolution": "I'm the same agent",
                },
            )
>>>>>>> Stashed changes
    assert resp.status_code == 403, resp.text
    assert "self_resolution_forbidden" in resp.json()["detail"]
