"""Deep endpoint coverage tests for routers not yet extensively tested.

Follows the convention from test_routers_endpoints.py:
- FastAPI + TestClient
- Mock external dependencies with MagicMock
- Test happy path, missing params -> 422, empty-state -> 503
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

_PSK = "unit-test-psk-deep"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_async_session_factory(session: MagicMock) -> MagicMock:
    factory = MagicMock()
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=session)
    ctx.__aexit__ = AsyncMock(return_value=None)
    factory.return_value = ctx
    return factory


def _make_db_session() -> MagicMock:
    session = MagicMock()
    session.execute = AsyncMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    session.flush = AsyncMock()
    return session


# ---------------------------------------------------------------------------
# Coordination
# ---------------------------------------------------------------------------


@pytest.fixture
def coord_app() -> FastAPI:
    import general_ludd.routers.coordination as coord_router

    app = FastAPI()
    coord_router.register(app, {})
    return app


@pytest.fixture
def coord_client(coord_app: FastAPI) -> TestClient:
    return TestClient(coord_app)


class TestCoordinationEndpoints:
    class TestClaim:
        def test_claim_returns_claims(self, coord_client: TestClient) -> None:
            resp = coord_client.post(
                "/api/coordination/claim",
                json={"worker_id": "w1", "files": ["a.py", "b.py"]},
            )
            assert resp.status_code == 201
            data = resp.json()
            assert data["worker_id"] == "w1"
            assert len(data["files"]) == 2

        def test_claim_missing_worker_id_returns_422(self, coord_client: TestClient) -> None:
            resp = coord_client.post("/api/coordination/claim", json={"files": ["a.py"]})
            assert resp.status_code == 422

    class TestRelease:
        def test_release_returns_conflicts(self, coord_client: TestClient) -> None:
            coord_client.post(
                "/api/coordination/claim",
                json={"worker_id": "w1", "files": ["a.py"]},
            )
            resp = coord_client.post(
                "/api/coordination/release",
                json={"worker_id": "w2"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["worker_id"] == "w2"

        def test_release_missing_worker_id_returns_422(self, coord_client: TestClient) -> None:
            resp = coord_client.post("/api/coordination/release", json={})
            assert resp.status_code == 422

    class TestOverlaps:
        def test_overlaps_no_conflict_returns_empty(self, coord_client: TestClient) -> None:
            resp = coord_client.get(
                "/api/coordination/overlaps?worker_id=w1",
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["worker_id"] == "w1"
            assert data["overlaps"] == {}
            assert data["should_wait"] == []

    class TestClaims:
        def test_claims_returns_all(self, coord_client: TestClient) -> None:
            resp = coord_client.get("/api/coordination/claims")
            assert resp.status_code == 200
            data = resp.json()
            assert "claims" in data
            assert "merge_plan" in data


# ---------------------------------------------------------------------------
# Approval
# ---------------------------------------------------------------------------


@pytest.fixture
def approval_app() -> FastAPI:
    import general_ludd.routers.approval as approval_router

    app = FastAPI()
    approval_router.register(app, {})
    return app


@pytest.fixture
def approval_client(approval_app: FastAPI) -> TestClient:
    return TestClient(approval_app)


class TestApprovalEndpoints:
    def test_status_not_wired_returns_false(self, approval_client: TestClient) -> None:
        resp = approval_client.get("/admin/approval/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["wired"] is False

    def test_status_wired_returns_true(self) -> None:
        import general_ludd.routers.approval as approval_router

        app = FastAPI()
        app.state._approval_gate = MagicMock()
        approval_router.register(app, {})
        client = TestClient(app)
        resp = client.get("/admin/approval/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["wired"] is True
        assert data["gate_type"] != "None"


# ---------------------------------------------------------------------------
# Hardware
# ---------------------------------------------------------------------------


@pytest.fixture
def hw_app() -> FastAPI:
    import general_ludd.routers.hardware as hw_router

    app = FastAPI()
    hw_router.register(app, {})
    return app


@pytest.fixture
def hw_client(hw_app: FastAPI) -> TestClient:
    return TestClient(hw_app)


class TestHardwareEndpoints:
    def test_inventory_not_available_returns_503(self, hw_client: TestClient) -> None:
        resp = hw_client.get("/admin/hardware/inventory")
        assert resp.status_code == 503

    def test_model_fit_missing_model_returns_422(self, hw_client: TestClient) -> None:
        resp = hw_client.get("/admin/hardware/model-fit")
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Filestore
# ---------------------------------------------------------------------------


@pytest.fixture
def filestore_app() -> FastAPI:
    import general_ludd.routers.filestore as fs_router

    app = FastAPI()
    fs_router.register(app, {})
    return app


@pytest.fixture
def filestore_client(filestore_app: FastAPI) -> TestClient:
    return TestClient(filestore_app)


class TestFilestoreEndpoints:
    def test_list_root_returns_entries(self, filestore_client: TestClient) -> None:
        resp = filestore_client.get("/admin/filestore/list")
        assert resp.status_code == 200
        data = resp.json()
        assert "entries" in data
        assert "count" in data

    def test_list_child_path_returns_entries(self, filestore_client: TestClient) -> None:
        resp = filestore_client.get("/admin/filestore/list?path=skills")
        assert resp.status_code == 200
        data = resp.json()
        assert "entries" in data

    def test_read_missing_path_returns_error(self, filestore_client: TestClient) -> None:
        resp = filestore_client.get("/admin/filestore/read?path=nonexistent")
        assert resp.status_code == 200
        data = resp.json()
        assert "error" in data

    def test_write_missing_path_returns_error(self, filestore_client: TestClient) -> None:
        resp = filestore_client.post(
            "/admin/filestore/write",
            json={"content": "hello"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is False

    def test_write_happy_path_returns_success(self, filestore_client: TestClient) -> None:
        resp = filestore_client.post(
            "/admin/filestore/write",
            json={"path": "test-deep.txt", "content": "test content"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True

    def test_remove_missing_path_returns_error(self, filestore_client: TestClient) -> None:
        resp = filestore_client.delete("/admin/filestore/remove?path=nonexistent")
        assert resp.status_code == 200
        data = resp.json()
        assert "error" in data

    def test_read_without_subpath_returns_error(self, filestore_client: TestClient) -> None:
        resp = filestore_client.get("/admin/filestore/read")
        assert resp.status_code == 200
        data = resp.json()
        assert "error" in data


# ---------------------------------------------------------------------------
# Web Search
# ---------------------------------------------------------------------------


@pytest.fixture
def websearch_app() -> FastAPI:
    import general_ludd.routers.web_search as ws_router

    app = FastAPI()
    ws_router.register(app, {})
    return app


@pytest.fixture
def websearch_client(websearch_app: FastAPI) -> TestClient:
    return TestClient(websearch_app)


class TestWebSearchEndpoints:
    def test_search_missing_query_returns_422(self, websearch_client: TestClient) -> None:
        resp = websearch_client.get("/admin/web/search")
        assert resp.status_code == 422

    def test_search_empty_query_returns_422(self, websearch_client: TestClient) -> None:
        resp = websearch_client.get("/admin/web/search?q=")
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Schedule
# ---------------------------------------------------------------------------


@pytest.fixture
def schedule_app() -> FastAPI:
    import general_ludd.routers.schedule as sched_router

    app = FastAPI()
    sched_router.register(app, {})
    return app


@pytest.fixture
def schedule_client(schedule_app: FastAPI) -> TestClient:
    return TestClient(schedule_app)


class TestScheduleEndpoints:
    def test_schedule_returns_batches(self, schedule_client: TestClient) -> None:
        resp = schedule_client.post(
            "/api/schedule",
            json={
                "items": [
                    {"id": "A", "resources": ["r1"]},
                    {"id": "B", "depends_on": ["A"]},
                ]
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "batches" in data

    def test_schedule_empty_items_returns_empty_batches(self, schedule_client: TestClient) -> None:
        resp = schedule_client.post("/api/schedule", json={"items": []})
        assert resp.status_code == 200

    def test_schedule_missing_items_returns_422(self, schedule_client: TestClient) -> None:
        resp = schedule_client.post("/api/schedule", json={})
        assert resp.status_code == 422

    def test_schedule_cycle_returns_409(self, schedule_client: TestClient) -> None:
        resp = schedule_client.post(
            "/api/schedule",
            json={
                "items": [
                    {"id": "A", "depends_on": ["B"]},
                    {"id": "B", "depends_on": ["A"]},
                ]
            },
        )
        assert resp.status_code == 409

    def test_schedule_with_greenfield_items(self, schedule_client: TestClient) -> None:
        resp = schedule_client.post(
            "/api/schedule",
            json={
                "items": [
                    {"id": "C", "is_greenfield": True},
                    {"id": "D"},
                ]
            },
        )
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Worktree
# ---------------------------------------------------------------------------


@pytest.fixture
def worktree_app() -> FastAPI:
    import general_ludd.routers.worktree as wt_router

    app = FastAPI()
    wt_router.register(app, {})
    return app


@pytest.fixture
def worktree_client(worktree_app: FastAPI) -> TestClient:
    return TestClient(worktree_app)


class TestWorktreeEndpoints:
    def test_status_returns_tracked(self, worktree_client: TestClient) -> None:
        resp = worktree_client.get("/admin/worktree/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "tracked_worktrees" in data
        assert "tracked_count" in data
        assert data["tracked_count"] == 0

    def test_scan_returns_todos(self, worktree_client: TestClient) -> None:
        resp = worktree_client.post("/admin/worktree/scan")
        assert resp.status_code == 200
        data = resp.json()
        assert "todos" in data


# ---------------------------------------------------------------------------
# Make
# ---------------------------------------------------------------------------


@pytest.fixture
def make_app() -> FastAPI:
    import general_ludd.routers.make as make_router

    app = FastAPI()
    make_router.register(app, {})
    return app


@pytest.fixture
def make_client(make_app: FastAPI) -> TestClient:
    return TestClient(make_app)


class TestMakeEndpoints:
    def test_make_missing_target_returns_422(self, make_client: TestClient) -> None:
        response = make_client.post("/admin/make", json={})
        assert response.status_code == 422

    def test_make_with_target_returns_result(self, make_client: TestClient) -> None:
        resp = make_client.post("/admin/make", json={"target": "help"})
        assert resp.status_code == 200
        data = resp.json()
        assert "target" in data
        assert "exit_code" in data


# ---------------------------------------------------------------------------
# Environments
# ---------------------------------------------------------------------------


def _setup_env_state(app: FastAPI) -> None:
    app.state._startup_config = {
        "user_config": None,
        "rules": [],
        "model_profiles": [],
        "queues": [],
        "budget": {},
        "self_improve": {},
        "model_routing": MagicMock(),
    }
    app.state._budget_guard = MagicMock()
    app.state._model_gateway = MagicMock()
    app.state._model_gateway.list_profiles.return_value = []
    app.state._mcp_client = None


@pytest.fixture
def env_app() -> FastAPI:
    import general_ludd.routers.environment as env_router

    app = FastAPI()
    _setup_env_state(app)
    env_router.register(app, {})
    return app


@pytest.fixture
def env_client(env_app: FastAPI) -> TestClient:
    return TestClient(env_app)


class TestEnvironmentEndpoints:
    def test_environment_returns_snapshot(self, env_client: TestClient) -> None:
        resp = env_client.get("/api/environment")
        assert resp.status_code == 200
        data = resp.json()
        assert "models" in data
        assert "system" in data

    def test_environment_advise_with_work_type(self, env_client: TestClient) -> None:
        resp = env_client.get("/api/environment/advise?work_type=feature")
        assert resp.status_code == 200
        data = resp.json()
        assert "recommendation" in data


# ---------------------------------------------------------------------------
# Experts
# ---------------------------------------------------------------------------


@pytest.fixture
def experts_app() -> FastAPI:
    import general_ludd.routers.experts as exp_router

    app = FastAPI()
    exp_router.register(app, {})
    return app


@pytest.fixture
def experts_client(experts_app: FastAPI) -> TestClient:
    return TestClient(experts_app)


class TestExpertsEndpoints:
    def test_materials_select_missing_requirements(self, experts_client: TestClient) -> None:
        resp = experts_client.post("/api/materials/select", json={})
        assert resp.status_code == 200

    def test_chemistry_resolve_missing_request(self, experts_client: TestClient) -> None:
        resp = experts_client.post("/api/chemistry/resolve", json={})
        assert resp.status_code == 200

    def test_ai_ml_query_missing_query_returns_422(self, experts_client: TestClient) -> None:
        resp = experts_client.post(
            "/api/ai_ml/query",
            json={"request_id": "r1", "tenant_id": "t1"},
        )
        assert resp.status_code == 422

    def test_git_release_assess_missing_repo_returns_422(self, experts_client: TestClient) -> None:
        resp = experts_client.get("/api/git_release/assess")
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Stream
# ---------------------------------------------------------------------------


@pytest.fixture
def stream_app() -> FastAPI:
    import general_ludd.routers.stream as stream_router

    app = FastAPI()
    stream_router.register(app, {})
    return app


@pytest.fixture
def stream_client(stream_app: FastAPI) -> TestClient:
    return TestClient(stream_app)


class TestStreamEndpoints:
    def test_dispatch_missing_role_returns_422(self, stream_client: TestClient) -> None:
        resp = stream_client.post(
            "/admin/stream/dispatch",
            json={},
        )
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Facts
# ---------------------------------------------------------------------------


@pytest.fixture
def facts_app() -> FastAPI:
    import general_ludd.routers.facts as facts_router

    app = FastAPI()
    app.state._startup_config = {}
    app.state._session_factory = None
    facts_router.register(app, {})
    return app


@pytest.fixture
def facts_client(facts_app: FastAPI) -> TestClient:
    return TestClient(facts_app)


class TestFactsEndpoints:
    def test_facts_returns_snapshot(self, facts_client: TestClient) -> None:
        resp = facts_client.get("/api/facts")
        assert resp.status_code == 200
        data = resp.json()
        assert "models" in data
        assert "work" in data
        assert "todos" in data

    def test_metrics_returns_data(self, facts_client: TestClient) -> None:
        resp = facts_client.get("/api/metrics")
        assert resp.status_code == 200
        data = resp.json()
        assert "metrics" in data or isinstance(data, dict)

    def test_traces_returns_data(self, facts_client: TestClient) -> None:
        resp = facts_client.get("/api/traces?project_id=test-proj")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Chat
# ---------------------------------------------------------------------------


@pytest.fixture
def chat_app() -> FastAPI:
    import general_ludd.routers.chat as chat_router

    app = FastAPI()
    chat_router.register(app, {})
    return app


@pytest.fixture
def chat_client(chat_app: FastAPI) -> TestClient:
    return TestClient(chat_app)


class TestChatEndpoints:
    def test_list_sessions_returns_sessions(self, chat_client: TestClient) -> None:
        resp = chat_client.get("/api/chat/sessions")
        assert resp.status_code == 200
        data = resp.json()
        assert "sessions" in data

    def test_session_detail_invalid_path_returns_404(self, chat_client: TestClient) -> None:
        resp = chat_client.get("/api/chat/sessions/nonexistent.json")
        assert resp.status_code == 404

    def test_search_missing_query_returns_422(self, chat_client: TestClient) -> None:
        resp = chat_client.post("/api/chat/sessions/search", json={})
        assert resp.status_code == 422

    def test_validate_missing_fields_returns_422(self, chat_client: TestClient) -> None:
        resp = chat_client.post("/api/chat/validate", json={})
        assert resp.status_code == 422

    def test_validate_happy_path(self, chat_client: TestClient) -> None:
        resp = chat_client.post(
            "/api/chat/validate",
            json={"role": "user", "content": "hello"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("valid") is True


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------


def _setup_project_subsystems(app: FastAPI) -> None:
    project_manager = MagicMock()
    project_manager.list_active.return_value = []
    project_manager.get_summary.return_value = {"projects": [], "active": 0}
    app.state._project_manager = project_manager


@pytest.fixture
def projects_app() -> FastAPI:
    import general_ludd.routers.projects as proj_router

    app = FastAPI()
    _setup_project_subsystems(app)
    proj_router.register(app, {})
    return app


@pytest.fixture
def projects_client(projects_app: FastAPI) -> TestClient:
    return TestClient(projects_app)


class TestProjectsEndpoints:
    def test_add_missing_name_returns_422(self, projects_client: TestClient) -> None:
        resp = projects_client.post("/admin/projects", json={"weight": 1.0})
        assert resp.status_code == 422

    def test_list_projects_returns_active(self, projects_client: TestClient) -> None:
        resp = projects_client.get("/admin/projects")
        assert resp.status_code == 200
        data = resp.json()
        assert "projects" in data

    def test_rebalance_missing_weights_returns_422(self, projects_client: TestClient) -> None:
        resp = projects_client.post("/admin/projects/rebalance", json={})
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Ansible
# ---------------------------------------------------------------------------


@pytest.fixture
def ansible_app() -> FastAPI:
    import general_ludd.routers.ansible as ansible_router

    app = FastAPI()
    ansible_router.register(app, {})
    return app


@pytest.fixture
def ansible_client(ansible_app: FastAPI) -> TestClient:
    return TestClient(ansible_app)


class TestAnsibleEndpoints:
    def test_builtins_returns_modules(self, ansible_client: TestClient) -> None:
        resp = ansible_client.get("/admin/ansible/builtins")
        assert resp.status_code == 200
        data = resp.json()
        assert "modules" in data

    def test_render_missing_template_renders_empty(self, ansible_client: TestClient) -> None:
        resp = ansible_client.post("/admin/ansible/render", json={})
        assert resp.status_code == 200
        data = resp.json()
        assert "rendered" in data

    def test_render_bad_extra_vars_returns_400(self, ansible_client: TestClient) -> None:
        resp = ansible_client.post(
            "/admin/ansible/render",
            json={"template": "hello", "extra_vars": "not_an_object"},
        )
        assert resp.status_code == 400

    def test_install_missing_name_returns_error(self, ansible_client: TestClient) -> None:
        resp = ansible_client.post("/admin/ansible/install", json={})
        assert resp.status_code != 200


# ---------------------------------------------------------------------------
# Deployments
# ---------------------------------------------------------------------------


@pytest.fixture
def depl_app() -> FastAPI:
    import general_ludd.routers.deployments as dep_router

    app = FastAPI()
    dep_router.register(app, {})
    return app


@pytest.fixture
def depl_client(depl_app: FastAPI) -> TestClient:
    return TestClient(depl_app)


class TestDeploymentsEndpoints:
    def test_health_returns_503_when_no_checker(self, depl_client: TestClient) -> None:
        resp = depl_client.get("/admin/deployments/health")
        assert resp.status_code == 503

    def test_incidents_returns_503_when_no_checker(self, depl_client: TestClient) -> None:
        resp = depl_client.get("/admin/deployments/incidents")
        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# Compute
# ---------------------------------------------------------------------------


def _setup_compute_state(app: FastAPI) -> None:
    from unittest.mock import MagicMock

    utilization = MagicMock()
    utilization.get_utilization_report.return_value = {"utilization": 0.0}
    utilization.list_endpoints.return_value = []
    utilization.register_endpoint.return_value = MagicMock(
        endpoint_id="ep-1",
        url="http://example.com",
        model="test-model",
    )
    app.state._utilization = utilization

    # Wire extended subsystems

    ext_mock = MagicMock()
    ext_mock.__getitem__ = MagicMock(return_value=utilization)
    ext_mock.get = MagicMock(return_value=ext_mock)
    ext_mock.utilization = utilization


@pytest.fixture
def compute_app() -> FastAPI:
    import general_ludd.routers.compute as comp_router

    app = FastAPI()
    app.state._compute_deployments = {}
    mgr = MagicMock()
    mgr.list_deployments_shared = AsyncMock(return_value=[])
    mgr.get_deployment_shared = AsyncMock(return_value=None)
    app.state._deployment_manager = mgr
    comp_router.register(app, {})
    return app


@pytest.fixture
def compute_client(compute_app: FastAPI) -> TestClient:
    return TestClient(compute_app)


class TestComputeEndpoints:
    def test_utilization_returns_report(self, compute_client: TestClient) -> None:
        resp = compute_client.get("/admin/compute/utilization")
        assert resp.status_code == 200

    def test_endpoints_list_returns_data(self, compute_client: TestClient) -> None:
        resp = compute_client.get("/admin/compute/endpoints")
        assert resp.status_code == 200
        data = resp.json()
        assert "endpoints" in data

    def test_idle_returns_data(self, compute_client: TestClient) -> None:
        resp = compute_client.get("/admin/compute/idle")
        assert resp.status_code == 200
        data = resp.json()
        assert "idle_endpoints" in data

    def test_register_endpoint_missing_id_returns_422(self, compute_client: TestClient) -> None:
        resp = compute_client.post(
            "/admin/compute/endpoints",
            json={"url": "http://example.com"},
        )
        assert resp.status_code == 422

    def test_register_endpoint_missing_url_returns_422(self, compute_client: TestClient) -> None:
        resp = compute_client.post(
            "/admin/compute/endpoints",
            json={"endpoint_id": "ep-1"},
        )
        assert resp.status_code == 422

    def test_deploy_missing_gpu_type_returns_422(self, compute_client: TestClient) -> None:
        resp = compute_client.post(
            "/admin/compute/deploy",
            json={"model_name": "test-model"},
        )
        assert resp.status_code == 422

    def test_deploy_missing_model_name_returns_422(self, compute_client: TestClient) -> None:
        resp = compute_client.post(
            "/admin/compute/deploy",
            json={"gpu_type": "a100"},
        )
        assert resp.status_code == 422

    def test_destroy_unknown_instance_returns_403_guard(self, compute_client: TestClient) -> None:
        resp = compute_client.delete("/admin/compute/destroy/unknown-instance")
        assert resp.status_code == 403

    def test_unregister_endpoint_returns_removed(self, compute_client: TestClient) -> None:
        resp = compute_client.delete("/admin/compute/endpoints/ep-to-remove")
        assert resp.status_code == 200
        data = resp.json()
        assert data["removed"] == "ep-to-remove"

    def test_gpu_metrics_returns_data(self, compute_client: TestClient) -> None:
        resp = compute_client.get("/admin/compute/gpu-metrics")
        assert resp.status_code == 200
        data = resp.json()
        assert "metrics" in data

    def test_gpu_metric_by_endpoint_unknown_returns_404(self, compute_client: TestClient) -> None:
        resp = compute_client.get("/admin/compute/gpu-metrics/unknown-ep")
        assert resp.status_code == 404

    def test_list_deployments_returns_data(self, compute_client: TestClient) -> None:
        resp = compute_client.get("/api/deployments")
        assert resp.status_code == 200
        data = resp.json()
        assert "deployments" in data
        assert "count" in data


# ---------------------------------------------------------------------------
# Processes
# ---------------------------------------------------------------------------


@pytest.fixture
def proc_app() -> FastAPI:
    import general_ludd.routers.processes as proc_router
    from general_ludd.process.registry import ProcessRegistry

    # Register a dummy registry on the app
    app = FastAPI()
    app.state._process_registry = ProcessRegistry()
    proc_router.register(app, {})
    return app


@pytest.fixture
def proc_client(proc_app: FastAPI) -> TestClient:
    return TestClient(proc_app)


class TestProcessesEndpoints:
    def test_list_processes_returns_data(self, proc_client: TestClient) -> None:
        resp = proc_client.get("/admin/processes")
        assert resp.status_code == 200
        data = resp.json()
        assert "processes" in data

    def test_process_detail_unknown_pid_returns_404(self, proc_client: TestClient) -> None:
        resp = proc_client.get("/admin/processes/99999")
        assert resp.status_code == 404

    def test_signal_unknown_pid_returns_404(self, proc_client: TestClient) -> None:
        resp = proc_client.post(
            "/admin/processes/99999/signal",
            json={"signal": "SIGTERM"},
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Registration smoke
# ---------------------------------------------------------------------------


class TestAllRoutersRegister:
    """Smoke test that each router under test registers routes successfully."""

    def test_coordination_register_adds_routes(self) -> None:
        import general_ludd.routers.coordination as mod

        app = FastAPI()
        before = len(app.routes)
        mod.register(app, {})
        assert len(app.routes) > before

    def test_approval_register_adds_routes(self) -> None:
        import general_ludd.routers.approval as mod

        app = FastAPI()
        before = len(app.routes)
        mod.register(app, {})
        assert len(app.routes) > before

    def test_hardware_register_adds_routes(self) -> None:
        import general_ludd.routers.hardware as mod

        app = FastAPI()
        before = len(app.routes)
        mod.register(app, {})
        assert len(app.routes) > before

    def test_filestore_register_adds_routes(self) -> None:
        import general_ludd.routers.filestore as mod

        app = FastAPI()
        before = len(app.routes)
        mod.register(app, {})
        assert len(app.routes) > before

    def test_web_search_register_adds_routes(self) -> None:
        import general_ludd.routers.web_search as mod

        app = FastAPI()
        before = len(app.routes)
        mod.register(app, {})
        assert len(app.routes) > before

    def test_schedule_register_adds_routes(self) -> None:
        import general_ludd.routers.schedule as mod

        app = FastAPI()
        before = len(app.routes)
        mod.register(app, {})
        assert len(app.routes) > before

    def test_worktree_register_adds_routes(self) -> None:
        import general_ludd.routers.worktree as mod

        app = FastAPI()
        before = len(app.routes)
        mod.register(app, {})
        assert len(app.routes) > before

    def test_make_register_adds_routes(self) -> None:
        import general_ludd.routers.make as mod

        app = FastAPI()
        before = len(app.routes)
        mod.register(app, {})
        assert len(app.routes) > before

    def test_compute_register_adds_routes(self) -> None:
        import general_ludd.routers.compute as mod

        app = FastAPI()
        app.state._compute_deployments = {}
        app.state._deployment_manager = MagicMock()
        before = len(app.routes)
        mod.register(app, {})
        assert len(app.routes) > before

    def test_chat_register_adds_routes(self) -> None:
        import general_ludd.routers.chat as mod

        app = FastAPI()
        before = len(app.routes)
        mod.register(app, {})
        assert len(app.routes) > before

    def test_environment_register_adds_routes(self) -> None:
        import general_ludd.routers.environment as mod

        app = FastAPI()
        _setup_env_state(app)
        before = len(app.routes)
        mod.register(app, {})
        assert len(app.routes) > before

    def test_facts_register_adds_routes(self) -> None:
        import general_ludd.routers.facts as mod

        app = FastAPI()
        app.state._startup_config = {}
        app.state._session_factory = None
        before = len(app.routes)
        mod.register(app, {})
        assert len(app.routes) > before

    def test_ansible_register_adds_routes(self) -> None:
        import general_ludd.routers.ansible as mod

        app = FastAPI()
        before = len(app.routes)
        mod.register(app, {})
        assert len(app.routes) > before

    def test_deployments_register_adds_routes(self) -> None:
        import general_ludd.routers.deployments as mod

        app = FastAPI()
        before = len(app.routes)
        mod.register(app, {})
        assert len(app.routes) > before

    def test_experts_register_adds_routes(self) -> None:
        import general_ludd.routers.experts as mod

        app = FastAPI()
        before = len(app.routes)
        mod.register(app, {})
        assert len(app.routes) > before

    def test_stream_register_adds_routes(self) -> None:
        import general_ludd.routers.stream as mod

        app = FastAPI()
        before = len(app.routes)
        mod.register(app, {})
        assert len(app.routes) > before

    def test_processes_register_adds_routes(self) -> None:
        import general_ludd.routers.processes as mod
        from general_ludd.process.registry import ProcessRegistry

        app = FastAPI()
        app.state._process_registry = ProcessRegistry()
        before = len(app.routes)
        mod.register(app, {})
        assert len(app.routes) > before

    def test_projects_register_adds_routes(self) -> None:
        import general_ludd.routers.projects as mod

        app = FastAPI()
        _setup_project_subsystems(app)
        before = len(app.routes)
        mod.register(app, {})
        assert len(app.routes) > before
