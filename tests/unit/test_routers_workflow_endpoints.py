"""Tests split from :mod:`tests.unit.test_routers_endpoints` by coherent behavior."""

from __future__ import annotations

import hmac
from typing import Any, ClassVar
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from general_ludd.security.permissions import Capability, PermissionSpec
from tests.unit.test_routers_endpoints import (
    _PSK,
    _SAFE_METHODS,
    _SI_PSK_CASES,
    _app_with_psk_gate,
)


def _make_self_improve_harness() -> MagicMock:
    h = MagicMock()
    h.run_gap_analysis.return_value = [
        {"id": "gap-1", "description": "Missing tests", "severity": "high"},
    ]
    h.run_full_cycle.return_value = {
        "findings": [{"id": "gap-1", "description": "Missing tests"}],
        "findings_count": 1,
        "todos": [{"title": "Add tests", "description": "Add unit tests"}],
        "todos_enqueued": 1,
    }
    return h


def _make_approval_manager() -> MagicMock:
    mgr = MagicMock()
    todo = MagicMock()
    todo.todo_id = "todo-1"
    todo.title = "Add tests"
    todo.status = "APPROVAL_REQUIRED"
    todo.work_type = "self_improve"
    todo.priority = 5
    todo.project_id = None
    todo.version = 1
    todo.created_at = "2024-01-01T00:00:00Z"
    todo.created_by = "self_improve_harness"
    mgr.list_pending = AsyncMock(return_value=[todo])
    mgr.approve_by_id = AsyncMock(return_value=todo)
    mgr.reject_by_id = AsyncMock(return_value=todo)
    return mgr


def _make_async_session_factory(session: MagicMock) -> MagicMock:
    factory = MagicMock()
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=session)
    ctx.__aexit__ = AsyncMock(return_value=None)
    factory.return_value = ctx
    return factory


def _make_db_session() -> MagicMock:
    session = MagicMock()
    empty_result = MagicMock()
    empty_result.scalars.return_value.all.return_value = []
    session.execute = AsyncMock(return_value=empty_result)
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    session.flush = AsyncMock()
    return session


def _make_todo_repo_with_create() -> MagicMock:
    repo = MagicMock()
    created = MagicMock()
    created.todo_id = "si-todo-1"
    repo.create = AsyncMock(return_value=created)
    repo.list_by_work_type = AsyncMock(return_value=[])
    repo.list_by_status = AsyncMock(return_value=[])
    repo.get_by_id = AsyncMock(return_value=None)
    repo.transition = AsyncMock(return_value=MagicMock())
    return repo


def _setup_self_improve_state(app: FastAPI) -> None:
    session = _make_db_session()
    app.state._session_factory = _make_async_session_factory(session)


@pytest.fixture
def si_app() -> FastAPI:
    import general_ludd.routers.self_improve as si_router

    app = FastAPI()
    _setup_self_improve_state(app)
    si_router.register(app, {})
    return app


@pytest.fixture
def si_client(si_app: FastAPI) -> TestClient:
    return TestClient(si_app)


class TestSelfImproveEndpoints:
    class TestAnalyze:
        def test_happy_path(self) -> None:
            import general_ludd.routers.self_improve as si_router

            app = FastAPI()
            si_router.register(app, {})
            with patch.object(
                si_router, "SelfImprovementHarness",
                return_value=_make_self_improve_harness(),
            ):
                client = TestClient(app)
                resp = client.post("/admin/self-improve/analyze")
            assert resp.status_code == 200
            data = resp.json()
            assert data["findings_count"] == 1
            assert data["findings"][0]["id"] == "gap-1"

    class TestRun:
        def test_happy_path(self) -> None:
            import general_ludd.routers.self_improve as si_router

            app = FastAPI()
            si_router.register(app, {"todos": []})
            with patch.object(
                si_router, "SelfImprovementHarness",
                return_value=_make_self_improve_harness(),
            ):
                client = TestClient(app)
                resp = client.post("/admin/self-improve/run")
            assert resp.status_code == 200
            data = resp.json()
            assert data["findings_count"] == 1
            assert data["todos_enqueued"] == 1

    class TestStatus:
        def test_never_run_returns_empty(self) -> None:
            import general_ludd.routers.self_improve as si_router

            app = FastAPI()
            si_router.register(app, {})
            client = TestClient(app)
            resp = client.get("/admin/self-improve/status")
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "never_run"
            assert data["findings_count"] == 0

        def test_after_analyze(self) -> None:
            import general_ludd.routers.self_improve as si_router

            app = FastAPI()
            state: dict[str, object] = {}
            si_router.register(app, state)
            with patch.object(
                si_router, "SelfImprovementHarness",
                return_value=_make_self_improve_harness(),
            ):
                client = TestClient(app)
                client.post("/admin/self-improve/analyze")
            resp = client.get("/admin/self-improve/status")
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "completed"
            assert data["findings_count"] == 1

    class TestApply:
        def test_config_no_approval_id_enqueues(self) -> None:
            import general_ludd.routers.self_improve as si_router

            app = FastAPI()
            session = _make_db_session()
            app.state._session_factory = _make_async_session_factory(session)
            si_router.register(app, {})
            with patch.object(
                si_router, "TodoRepository",
                return_value=_make_todo_repo_with_create(),
            ):
                client = TestClient(app)
                resp = client.post(
                    "/admin/self-improve/apply",
                    json={"kind": "config", "title": "Update config"},
                )
            assert resp.status_code == 200
            data = resp.json()
            assert data["tier"] == "config"
            assert data["status"] == "approval_required"
            assert "approval_id" in data

        def test_apply_non_config_no_db_returns_503(self) -> None:
            import general_ludd.routers.self_improve as si_router

            app = FastAPI()
            si_router.register(app, {})
            client = TestClient(app)
            resp = client.post(
                "/admin/self-improve/apply",
                json={"kind": "code", "title": "Fix bug"},
            )
            assert resp.status_code == 503

    class TestApprovals:
        def test_list_pending_no_db_returns_empty(self) -> None:
            import general_ludd.routers.self_improve as si_router

            app = FastAPI()
            si_router.register(app, {})
            client = TestClient(app)
            resp = client.get("/admin/self-improve/approvals")
            assert resp.status_code == 200
            assert resp.json() == {"pending": [], "count": 0}

        def test_list_pending_with_db(self) -> None:
            import general_ludd.routers.self_improve as si_router

            app = FastAPI()
            session = _make_db_session()
            app.state._session_factory = _make_async_session_factory(session)
            si_router.register(app, {})
            with patch.object(
                si_router, "SelfImproveApprovalManager",
                return_value=_make_approval_manager(),
            ):
                client = TestClient(app)
                resp = client.get("/admin/self-improve/approvals")
            assert resp.status_code == 200
            data = resp.json()
            assert data["count"] == 1
            assert data["pending"][0]["todo_id"] == "todo-1"

    class TestApproveReject:
        def test_approve_no_db_returns_503(self) -> None:
            import general_ludd.routers.self_improve as si_router

            app = FastAPI()
            si_router.register(app, {})
            client = TestClient(app)
            resp = client.post("/admin/self-improve/approvals/todo-1/approve")
            assert resp.status_code == 503

        def test_reject_no_db_returns_503(self) -> None:
            import general_ludd.routers.self_improve as si_router

            app = FastAPI()
            si_router.register(app, {})
            client = TestClient(app)
            resp = client.post(
                "/admin/self-improve/approvals/todo-1/reject",
                json={"reason": "not needed"},
            )
            assert resp.status_code == 503

        def test_approve_with_db(self) -> None:
            import general_ludd.routers.self_improve as si_router

            app = FastAPI()
            session = _make_db_session()
            app.state._session_factory = _make_async_session_factory(session)
            si_router.register(app, {})
            with patch.object(
                si_router, "SelfImproveApprovalManager",
                return_value=_make_approval_manager(),
            ):
                client = TestClient(app)
                resp = client.post(
                    "/admin/self-improve/approvals/todo-1/approve",
                )
            assert resp.status_code == 200
            assert resp.json()["approved"] is True

        def test_reject_with_db(self) -> None:
            import general_ludd.routers.self_improve as si_router

            app = FastAPI()
            session = _make_db_session()
            app.state._session_factory = _make_async_session_factory(session)
            si_router.register(app, {})
            with patch.object(
                si_router, "SelfImproveApprovalManager",
                return_value=_make_approval_manager(),
            ):
                client = TestClient(app)
                resp = client.post(
                    "/admin/self-improve/approvals/todo-1/reject",
                    json={"reason": "not needed"},
                )
            assert resp.status_code == 200
            assert resp.json()["rejected"] is True

    class TestAuthPosture:
        @pytest.mark.parametrize("method,path,body", _SI_PSK_CASES)
        def test_unauthenticated_is_refused(
            self, method: str, path: str, body
        ) -> None:
            import general_ludd.routers.self_improve as si_router

            client = TestClient(
                _app_with_psk_gate(
                    si_router.register,
                    setup_state=_setup_self_improve_state,
                )
            )
            resp = client.request(method, path, json=body)
            assert resp.status_code == 401

        @pytest.mark.parametrize("method,path,body", _SI_PSK_CASES)
        def test_with_psk_succeeds(
            self, method: str, path: str, body
        ) -> None:
            import general_ludd.routers.self_improve as si_router

            session = _make_db_session()
            with (
                patch.object(
                    si_router, "SelfImprovementHarness",
                    return_value=_make_self_improve_harness(),
                ),
                patch.object(
                    si_router, "SelfImproveApprovalManager",
                    return_value=_make_approval_manager(),
                ),
                patch.object(
                    si_router, "TodoRepository",
                    return_value=_make_todo_repo_with_create(),
                ),
            ):

                def _setup(app: FastAPI) -> None:
                    app.state._session_factory = _make_async_session_factory(session)

                client = TestClient(
                    _app_with_psk_gate(
                        si_router.register, setup_state=_setup,
                    )
                )
                resp = client.request(
                    method,
                    path,
                    json=body,
                    headers={"Authorization": f"Bearer {_PSK}"},
                )
                assert resp.status_code in (200, 503)


# ==========================================================================
# Account router endpoint tests
# ==========================================================================


def _make_ephemeral_account_manager() -> MagicMock:
    creds = MagicMock()
    creds.account_id = "acct-abc123"
    creds.provider = "aws"
    creds.access_key_id = "AKIATEST"
    creds.budget_limit = 10.0
    mgr = MagicMock()
    mgr.create_account = MagicMock(return_value=creds)
    mgr.cleanup_expired = MagicMock(return_value={"deleted": ["acct-1"], "kept": []})
    return mgr


def _app_with_selective_auth(
    register_fn,
    *,
    public_get_prefixes: frozenset[str] | None = None,
    public_get_paths: frozenset[str] | None = None,
    setup_state=None,
) -> FastAPI:
    public_prefixes = public_get_prefixes or frozenset()
    public_paths = public_get_paths or frozenset()
    app = FastAPI()
    if setup_state:
        setup_state(app)
    register_fn(app, {})

    @app.middleware("http")
    async def _auth(request, call_next):
        path = request.url.path
        if request.method in _SAFE_METHODS and (
            path in public_paths
            or any(path.startswith(p) for p in public_prefixes)
        ):
            return await call_next(request)
        auth = request.headers.get("Authorization", "")
        token = (
            auth.removeprefix("Bearer ").strip()
            if auth.startswith("Bearer ")
            else ""
        )
        if not token or not hmac.compare_digest(token, _PSK):
            return JSONResponse(status_code=401, content={"error": "unauthorized"})
        return await call_next(request)

    return app


def _authorize_account_admin(app: FastAPI) -> None:
    """Attach the capability required by the account router's inner guard."""
    spec = PermissionSpec(
        agent_type="test-admin",
        capabilities=[
            Capability(
                resource="admin:account",
                actions=["backup", "delete", "create", "cleanup"],
            )
        ],
    )

    @app.middleware("http")
    async def _attach_auth_spec(request: Request, call_next: Any) -> Any:
        request.state.auth_spec = spec
        return await call_next(request)


def _build_account_app(*, with_manager: bool = True) -> FastAPI:
    import general_ludd.routers.account as account_router

    app = FastAPI()
    session = _make_db_session()
    app.state._session_factory = _make_async_session_factory(session)
    if with_manager:
        app.state._ephemeral_account_manager = _make_ephemeral_account_manager()
    account_router.register(app, {})
    _authorize_account_admin(app)
    return app


class TestAccountEndpoints:
    _ACCOUNT_PUBLIC_PATHS: ClassVar[frozenset[str]] = frozenset(
        {"/api/account/policy"}
    )
    _ACCOUNT_WRITE_CASES: ClassVar[list[tuple[str, str, dict[str, object] | None]]] = [
        ("POST", "/api/account/backup", {"user_id": "u1"}),
        ("DELETE", "/api/account", {"user_id": "u1", "confirm": True}),
        ("POST", "/api/account/create", {"provider": "aws", "ephemeral": True}),
        ("POST", "/api/account/cleanup", None),
    ]

    def _auth_app(self) -> FastAPI:
        import general_ludd.routers.account as account_router

        def _setup(app: FastAPI) -> None:
            session = _make_db_session()
            app.state._session_factory = _make_async_session_factory(session)
            app.state._ephemeral_account_manager = _make_ephemeral_account_manager()

        app = _app_with_selective_auth(
            account_router.register,
            public_get_paths=self._ACCOUNT_PUBLIC_PATHS,
            setup_state=_setup,
        )
        _authorize_account_admin(app)
        return app

    # ---- POST /api/account/backup ----

    def test_backup_happy_path(self) -> None:
        import general_ludd.routers.account as account_router

        with patch.object(
            account_router,
            "_export_user_data",
            new=AsyncMock(return_value={"user": "data", "todos": 3}),
        ):
            client = TestClient(_build_account_app())
            resp = client.post("/api/account/backup", json={"user_id": "user-1"})
        assert resp.status_code == 200
        assert resp.json()["user"] == "data"

    def test_backup_missing_user_id_returns_422(self) -> None:
        client = TestClient(_build_account_app())
        resp = client.post("/api/account/backup", json={})
        assert resp.status_code == 422

    # ---- DELETE /api/account ----

    def test_delete_happy_path(self) -> None:
        import general_ludd.routers.account as account_router

        with patch.object(
            account_router,
            "_delete_user_data",
            new=AsyncMock(return_value={"deleted": 3, "todos": 2, "sessions": 1}),
        ):
            client = TestClient(_build_account_app())
            resp = client.request(
                "DELETE",
                "/api/account",
                json={"user_id": "user-1", "confirm": True},
            )
        assert resp.status_code == 200
        assert resp.json()["deleted"] == 3

    def test_delete_missing_confirm_returns_400(self) -> None:
        client = TestClient(_build_account_app())
        resp = client.request(
            "DELETE", "/api/account", json={"user_id": "user-1"}
        )
        assert resp.status_code == 400

    def test_delete_confirm_false_returns_400(self) -> None:
        client = TestClient(_build_account_app())
        resp = client.request(
            "DELETE",
            "/api/account",
            json={"user_id": "user-1", "confirm": False},
        )
        assert resp.status_code == 400

    # ---- GET /api/account/policy ----

    def test_policy_happy_path(self) -> None:
        import general_ludd.routers.account as account_router

        with (
            patch.object(
                account_router,
                "get_policy_text",
                return_value="Retain logs for 30 days.",
            ),
            patch.object(
                account_router,
                "build_deletion_notice",
                return_value="Notice: deletion after 30d.",
            ),
        ):
            client = TestClient(_build_account_app())
            resp = client.get("/api/account/policy", params={"service": "aws"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["service"] == "aws"
        assert "policy" in data
        assert "notice" in data

    def test_policy_unknown_service_returns_422(self) -> None:
        import general_ludd.routers.account as account_router

        with (
            patch.object(
                account_router,
                "get_policy_text",
                side_effect=ValueError("unknown service"),
            ),
            patch.object(account_router, "build_deletion_notice"),
        ):
            client = TestClient(_build_account_app())
            resp = client.get(
                "/api/account/policy", params={"service": "unknown-svc"}
            )
        assert resp.status_code == 422

    # ---- POST /api/account/create ----

    def test_create_happy_path(self) -> None:
        client = TestClient(_build_account_app())
        resp = client.post(
            "/api/account/create",
            json={"provider": "aws", "budget": 5.0, "ephemeral": True},
        )
        assert resp.status_code == 200
        assert resp.json()["account_id"] == "acct-abc123"

    def test_create_bad_provider_returns_422(self) -> None:
        client = TestClient(_build_account_app())
        resp = client.post(
            "/api/account/create",
            json={"provider": "nopecloud", "ephemeral": True},
        )
        assert resp.status_code == 422

    # ---- POST /api/account/cleanup ----

    def test_cleanup_happy_path(self) -> None:
        client = TestClient(_build_account_app())
        resp = client.post("/api/account/cleanup")
        assert resp.status_code == 200
        assert "deleted" in resp.json()

    def test_cleanup_empty_state_returns_503(self) -> None:
        client = TestClient(_build_account_app(with_manager=False))
        resp = client.post("/api/account/cleanup")
        assert resp.status_code == 503

    # ---- Auth posture ----

    def test_public_policy_no_auth_returns_200(self) -> None:
        import general_ludd.routers.account as account_router

        with (
            patch.object(account_router, "get_policy_text", return_value="policy text"),
            patch.object(
                account_router,
                "build_deletion_notice",
                return_value="notice",
            ),
        ):
            client = TestClient(self._auth_app())
            resp = client.get("/api/account/policy", params={"service": "aws"})
        assert resp.status_code == 200

    @pytest.mark.parametrize("method,path,body", _ACCOUNT_WRITE_CASES)
    def test_write_unauthenticated_returns_401(
        self, method: str, path: str, body
    ) -> None:
        import general_ludd.routers.account as account_router

        with (
            patch.object(
                account_router,
                "_export_user_data",
                new=AsyncMock(return_value={"ok": True}),
            ),
            patch.object(
                account_router,
                "_delete_user_data",
                new=AsyncMock(return_value={"ok": True}),
            ),
        ):
            client = TestClient(self._auth_app())
            resp = client.request(method, path, json=body)
        assert resp.status_code == 401

    @pytest.mark.parametrize("method,path,body", _ACCOUNT_WRITE_CASES)
    def test_write_with_psk_succeeds(
        self, method: str, path: str, body
    ) -> None:
        import general_ludd.routers.account as account_router

        with (
            patch.object(
                account_router,
                "_export_user_data",
                new=AsyncMock(return_value={"ok": True}),
            ),
            patch.object(
                account_router,
                "_delete_user_data",
                new=AsyncMock(return_value={"ok": True}),
            ),
        ):
            client = TestClient(self._auth_app())
            resp = client.request(
                method,
                path,
                json=body,
                headers={"Authorization": f"Bearer {_PSK}"},
            )
        assert resp.status_code == 200


# ==========================================================================
# HumanTodos router endpoint tests
# ==========================================================================


def _make_ht_row(ht_id: str = "ht-1", status: str = "open", **kw: object) -> MagicMock:
    row = MagicMock()
    row.id = ht_id
    row.parent_agent_todo_id = kw.get("parent_agent_todo_id")
    row.agent_id = kw.get("agent_id", "agent-1")
    row.session_id = kw.get("session_id")
    row.title = kw.get("title", "Test Todo")
    row.body = kw.get("body", "Test body")
    row.category = kw.get("category", "human_input")
    row.priority = kw.get("priority", "medium")
    row.status = status
    row.human_resolution = kw.get("human_resolution")
    row.human_resolver = kw.get("human_resolver")
    row.created_at = kw.get("created_at")
    row.updated_at = kw.get("updated_at")
    row.resolved_at = kw.get("resolved_at")
    row.due_at = None
    row.tags = kw.get("tags", "[]")
    return row


def _make_ht_repo(**overrides: object) -> MagicMock:
    repo = MagicMock()
    repo.create = AsyncMock(return_value=overrides.get("create", _make_ht_row()))
    repo.list_all = AsyncMock(
        return_value=overrides.get("list_all", [_make_ht_row()])
    )
    repo.list_changed_since = AsyncMock(
        return_value=overrides.get("feed", [_make_ht_row()])
    )
    repo.get = AsyncMock(return_value=overrides.get("get", _make_ht_row()))
    repo.mark_done = AsyncMock(
        return_value=overrides.get("mark_done", _make_ht_row(status="done"))
    )
    repo.mark_in_progress = AsyncMock(
        return_value=overrides.get(
            "mark_in_progress", _make_ht_row(status="in_progress")
        )
    )
    repo.dismiss = AsyncMock(
        return_value=overrides.get("dismiss", _make_ht_row(status="dismissed"))
    )
    repo.add_tag = AsyncMock(return_value=overrides.get("add_tag", _make_ht_row()))
    return repo


class TestHumanTodosEndpoints:
    _HT_PUBLIC_PREFIXES: ClassVar[frozenset[str]] = frozenset(
        {"/api/human-todos"}
    )
    _HT_WRITE_CASES: ClassVar[list[tuple[str, str, dict[str, object] | None]]] = [
        (
            "POST",
            "/api/human-todos",
            {
                "agent_id": "agent-1",
                "title": "Test",
                "body": "Body",
                "category": "permission_escalation",
            },
        ),
        (
            "PATCH",
            "/api/human-todos/ht-1",
            {"status": "in_progress"},
        ),
        ("DELETE", "/api/human-todos/ht-1", None),
        (
            "POST",
            "/api/human-todos/ht-1/tags",
            {"tag": "urgent"},
        ),
    ]
    _HT_GET_PUBLIC_CASES: ClassVar[list[tuple[str, str]]] = [
        ("GET", "/api/human-todos"),
        ("GET", "/api/human-todos/feed"),
        ("GET", "/api/human-todos/ht-1"),
    ]

    def _build_app(self, repo: MagicMock | None = None) -> FastAPI:
        import general_ludd.routers.human_todos as ht_router

        app = FastAPI()
        session = _make_db_session()
        app.state._session_factory = _make_async_session_factory(session)
        repo = repo or _make_ht_repo()
        ht_router.HumanTodoRepository = MagicMock(return_value=repo)
        ht_router.TodoRepository = MagicMock()
        ht_router.NotificationDispatcher = MagicMock()
        ht_router.register(app, {})
        return app

    def _auth_app(self) -> FastAPI:
        import general_ludd.routers.human_todos as ht_router

        repo = _make_ht_repo()

        def _setup(app: FastAPI) -> None:
            session = _make_db_session()
            app.state._session_factory = _make_async_session_factory(session)
            ht_router.HumanTodoRepository = MagicMock(return_value=repo)
            ht_router.TodoRepository = MagicMock()
            ht_router.NotificationDispatcher = MagicMock()

        return _app_with_selective_auth(
            ht_router.register,
            public_get_prefixes=self._HT_PUBLIC_PREFIXES,
            setup_state=_setup,
        )

    # ---- POST /api/human-todos ----

    def test_create_happy_path(self) -> None:
        import general_ludd.routers.human_todos as ht_router

        repo = _make_ht_repo()
        ht_router.HumanTodoRepository = MagicMock(return_value=repo)
        ht_router.TodoRepository = MagicMock()
        ht_router.NotificationDispatcher = MagicMock()

        app = FastAPI()
        session = _make_db_session()
        app.state._session_factory = _make_async_session_factory(session)
        ht_router.register(app, {})
        client = TestClient(app)

        resp = client.post(
            "/api/human-todos",
            json={
                "agent_id": "agent-1",
                "title": "Test",
                "body": "Body",
                "category": "permission_escalation",
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["id"] == "ht-1"
        assert data["category"] == "human_input"

    def test_create_missing_category_returns_422(self) -> None:
        client = TestClient(self._build_app())
        resp = client.post(
            "/api/human-todos",
            json={
                "agent_id": "agent-1",
                "title": "Test",
                "body": "Body",
            },
        )
        assert resp.status_code == 422

    def test_create_no_session_factory_returns_503(self) -> None:
        import general_ludd.routers.human_todos as ht_router

        app = FastAPI()
        ht_router.register(app, {})
        client = TestClient(app)
        resp = client.post(
            "/api/human-todos",
            json={
                "agent_id": "agent-1",
                "title": "Test",
                "body": "Body",
                "category": "permission_escalation",
            },
        )
        assert resp.status_code == 503

    # ---- GET /api/human-todos ----

    def test_list_happy_path(self) -> None:
        client = TestClient(self._build_app())
        resp = client.get("/api/human-todos")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) == 1

    def test_list_status_filter(self) -> None:
        repo = _make_ht_repo(
            list_all=[_make_ht_row(status="done")]
        )
        client = TestClient(self._build_app(repo=repo))
        resp = client.get("/api/human-todos", params={"status": "done"})
        assert resp.status_code == 200
        assert resp.json()[0]["status"] == "done"

    # ---- GET /api/human-todos/feed ----

    def test_feed_with_since(self) -> None:
        client = TestClient(self._build_app())
        resp = client.get(
            "/api/human-todos/feed",
            params={"since": "2026-01-01T00:00:00Z"},
        )
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    # ---- GET /api/human-todos/{id} ----

    def test_get_by_id_happy_path(self) -> None:
        client = TestClient(self._build_app())
        resp = client.get("/api/human-todos/ht-1")
        assert resp.status_code == 200
        assert resp.json()["id"] == "ht-1"

    def test_get_by_id_not_found_returns_404(self) -> None:
        repo = _make_ht_repo(get=None)
        client = TestClient(self._build_app(repo=repo))
        resp = client.get("/api/human-todos/ht-nonexistent")
        assert resp.status_code == 404

    # ---- PATCH /api/human-todos/{id} ----

    def test_patch_mark_done_happy_path(self) -> None:
        repo = _make_ht_repo()
        client = TestClient(self._build_app(repo=repo))
        resp = client.patch(
            "/api/human-todos/ht-1",
            json={
                "status": "done",
                "human_resolver": "operator-1",
                "human_resolution": "approved",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "done"

    def test_patch_empty_returns_422(self) -> None:
        client = TestClient(self._build_app())
        resp = client.patch("/api/human-todos/ht-1", json={})
        assert resp.status_code == 422

    def test_patch_not_found_returns_404(self) -> None:
        repo = _make_ht_repo(get=None)
        client = TestClient(self._build_app(repo=repo))
        resp = client.patch(
            "/api/human-todos/ht-nonexistent",
            json={"status": "in_progress"},
        )
        assert resp.status_code == 404

    # ---- DELETE /api/human-todos/{id} ----

    def test_delete_soft_delete_happy_path(self) -> None:
        client = TestClient(self._build_app())
        resp = client.delete("/api/human-todos/ht-1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == "ht-1"
        assert data["status"] == "deleted"

    def test_delete_not_found_returns_404(self) -> None:
        repo = _make_ht_repo(get=None)
        client = TestClient(self._build_app(repo=repo))
        resp = client.delete("/api/human-todos/ht-nonexistent")
        assert resp.status_code == 404

    # ---- POST /api/human-todos/{id}/tags ----

    def test_add_tag_happy_path(self) -> None:
        client = TestClient(self._build_app())
        resp = client.post(
            "/api/human-todos/ht-1/tags",
            json={"tag": "urgent"},
        )
        assert resp.status_code == 200
        assert resp.json()["id"] == "ht-1"

    def test_add_tag_empty_returns_422(self) -> None:
        client = TestClient(self._build_app())
        resp = client.post(
            "/api/human-todos/ht-1/tags",
            json={"tag": ""},
        )
        assert resp.status_code == 422

    # ---- Auth posture ----

    @pytest.mark.parametrize("method,path", _HT_GET_PUBLIC_CASES)
    def test_get_public_no_auth_returns_200(self, method: str, path: str) -> None:
        import general_ludd.routers.human_todos as ht_router

        repo = _make_ht_repo()
        ht_router.HumanTodoRepository = MagicMock(return_value=repo)
        ht_router.TodoRepository = MagicMock()
        ht_router.NotificationDispatcher = MagicMock()

        app = FastAPI()
        session = _make_db_session()
        app.state._session_factory = _make_async_session_factory(session)
        ht_router.register(app, {})

        @app.middleware("http")
        async def _auth(request, call_next):
            path_req = request.url.path
            if request.method in _SAFE_METHODS and path_req.startswith(
                "/api/human-todos"
            ):
                return await call_next(request)
            auth = request.headers.get("Authorization", "")
            token = (
                auth.removeprefix("Bearer ").strip()
                if auth.startswith("Bearer ")
                else ""
            )
            if not token or not hmac.compare_digest(token, _PSK):
                return JSONResponse(status_code=401, content={"error": "unauthorized"})
            return await call_next(request)

        client = TestClient(app)
        resp = client.request(method, path)
        assert resp.status_code == 200

    @pytest.mark.parametrize("method,path,body", _HT_WRITE_CASES)
    def test_write_unauthenticated_returns_401(
        self, method: str, path: str, body
    ) -> None:
        client = TestClient(self._auth_app())
        resp = client.request(method, path, json=body)
        assert resp.status_code == 401

    @pytest.mark.parametrize("method,path,body", _HT_WRITE_CASES)
    def test_write_with_psk_succeeds(
        self, method: str, path: str, body
    ) -> None:
        client = TestClient(self._auth_app())
        resp = client.request(
            method,
            path,
            json=body,
            headers={"Authorization": f"Bearer {_PSK}"},
        )
        assert resp.status_code in (200, 201)


# ==========================================================================
# Coordination router endpoint tests
# ==========================================================================


def _make_coordination_registry(**overrides: object) -> MagicMock:
    registry = MagicMock()
    registry.claim = MagicMock()
    registry.release = MagicMock()
    registry.overlaps = MagicMock(
        return_value=overrides.get("overlaps", {})
    )
    registry.should_wait = MagicMock(
        return_value=overrides.get("should_wait", [])
    )
    registry.all_claims = MagicMock(
        return_value=overrides.get("all_claims", {})
    )
    registry.merge_plan = MagicMock(
        return_value=overrides.get("merge_plan", {})
    )
    registry.claims_with_age = MagicMock(
        return_value=overrides.get("claims_with_age", {})
    )
    return registry


class TestCoordinationEndpoints:
    _COORD_PSK_CASES: ClassVar[
        list[tuple[str, str, dict[str, object] | None, dict[str, str] | None]]
    ] = [
        ("POST", "/api/coordination/claim", {"worker_id": "w1", "files": ["a.py"]}, None),
        ("POST", "/api/coordination/release", {"worker_id": "w1"}, None),
        ("GET", "/api/coordination/overlaps", None, {"worker_id": "w1"}),
        ("GET", "/api/coordination/claims", None, None),
    ]

    def _build_app(self, registry: MagicMock | None = None) -> FastAPI:
        import general_ludd.routers.coordination as coord_router

        registry = registry or _make_coordination_registry()
        coord_router.FileClaimRegistry = MagicMock(return_value=registry)
        app = FastAPI()
        coord_router.register(app, {})
        return app

    # ---- POST /api/coordination/claim ----

    def test_claim_happy_path(self) -> None:
        registry = _make_coordination_registry()
        client = TestClient(self._build_app(registry=registry))
        resp = client.post(
            "/api/coordination/claim",
            json={"worker_id": "worker-1", "files": ["src/a.py"]},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["worker_id"] == "worker-1"
        registry.claim.assert_called_once_with("worker-1", ["src/a.py"])

    def test_claim_missing_worker_id_returns_422(self) -> None:
        client = TestClient(self._build_app())
        resp = client.post("/api/coordination/claim", json={"files": ["src/a.py"]})
        assert resp.status_code == 422

    # ---- POST /api/coordination/release ----

    def test_release_happy_path(self) -> None:
        registry = _make_coordination_registry()
        client = TestClient(self._build_app(registry=registry))
        resp = client.post(
            "/api/coordination/release", json={"worker_id": "worker-1"}
        )
        assert resp.status_code == 200
        assert resp.json()["released"] is True
        registry.release.assert_called_once_with("worker-1")

    def test_release_missing_worker_id_returns_422(self) -> None:
        client = TestClient(self._build_app())
        resp = client.post("/api/coordination/release", json={})
        assert resp.status_code == 422

    # ---- GET /api/coordination/overlaps ----

    def test_overlaps_happy_path(self) -> None:
        client = TestClient(self._build_app())
        resp = client.get(
            "/api/coordination/overlaps", params={"worker_id": "worker-1"}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["worker_id"] == "worker-1"
        assert "overlaps" in data
        assert "should_wait" in data

    def test_overlaps_conflict_detected(self) -> None:
        registry = _make_coordination_registry(
            overlaps={"src/a.py": ["worker-2"]},
            should_wait=["worker-2"],
        )
        client = TestClient(self._build_app(registry=registry))
        resp = client.get(
            "/api/coordination/overlaps", params={"worker_id": "worker-1"}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["overlaps"]["src/a.py"] == ["worker-2"]
        assert data["should_wait"] == ["worker-2"]

    # ---- GET /api/coordination/claims ----

    def test_claims_happy_path(self) -> None:
        client = TestClient(self._build_app())
        resp = client.get("/api/coordination/claims")
        assert resp.status_code == 200
        data = resp.json()
        assert "claims" in data
        assert "merge_plan" in data
        assert "claims_by_worker" in data

    # ---- Auth posture ----

    @pytest.mark.parametrize("method,path,body,params", _COORD_PSK_CASES)
    def test_unauthenticated_returns_401(
        self, method: str, path: str, body, params: dict[str, str] | None
    ) -> None:
        import general_ludd.routers.coordination as coord_router

        coord_router.FileClaimRegistry = MagicMock(
            return_value=_make_coordination_registry()
        )
        client = TestClient(
            _app_with_psk_gate(coord_router.register)
        )
        resp = client.request(method, path, json=body, params=params)
        assert resp.status_code == 401

    @pytest.mark.parametrize("method,path,body,params", _COORD_PSK_CASES)
    def test_with_psk_succeeds(
        self, method: str, path: str, body, params: dict[str, str] | None
    ) -> None:
        import general_ludd.routers.coordination as coord_router

        coord_router.FileClaimRegistry = MagicMock(
            return_value=_make_coordination_registry()
        )
        client = TestClient(
            _app_with_psk_gate(coord_router.register)
        )
        resp = client.request(
            method,
            path,
            json=body,
            params=params,
            headers={"Authorization": f"Bearer {_PSK}"},
        )
        assert resp.status_code in (200, 201)
