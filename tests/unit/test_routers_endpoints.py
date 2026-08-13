"""Endpoint-level tests for slurm, models, and reload routers.

Follows the convention from ``test_routers_eval_endpoints.py``:
- FastAPI + TestClient
- Mock external dependencies with MagicMock
- Test happy path, auth posture, missing params -> 422, empty-state -> 503

Slurm router:
  dep mocked: ``SlurmAdapter`` (monkey-patched on the imported module)
  endpoints: status, submit, job status, cancel, list, cost

Models router:
  deps mocked: ``_get_or_create_subsystems``, ``_get_or_create_extended_subsystems``,
               ``ModelGateway`` (on ``app.state._model_gateway``),
               ``ModelHealthTracker`` (on ``app.state._health_tracker``)
  endpoints: add, list, health, search, call, workflow

Reload router:
  deps mocked: ``_get_or_create_subsystems``, ``HotReloader``,
               ``PromptRegistry`` (on ``app.state._prompt_registry``),
               ``AnsibleRunnerAdapter`` (on ``app.state._runner``)
  endpoints: reload, rollback, config/reload, reload/status, workers, hooks
"""

from __future__ import annotations

import hmac
from typing import Any, ClassVar
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from general_ludd.security.permissions import Capability, PermissionSpec

_PSK = "unit-test-psk-endpoints"
_SIGNING_ADMIN_TOKEN = "unit-test-signing-admin"
_SIGNING_ADMIN_HEADERS = {"X-Admin-Token": _SIGNING_ADMIN_TOKEN}
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
_PUBLIC_PATHS: set[str] = {"/healthz"}


def _app_with_psk_gate(register_fn, *, setup_state=None) -> FastAPI:
    app = FastAPI()
    if setup_state:
        setup_state(app)
    register_fn(app, {})

    @app.middleware("http")
    async def _auth(request, call_next):
        if not _is_public(request.method, request.url.path):
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


def _is_public(method: str, path: str) -> bool:
    if method.upper() not in _SAFE_METHODS:
        return False
    return path in _PUBLIC_PATHS


def _make_bus() -> MagicMock:
    bus = MagicMock()
    bus.get_history.return_value = []
    return bus


def _make_hooks() -> MagicMock:
    hooks = MagicMock()
    hooks.list_hooks.return_value = []
    hooks.register_webhook.return_value = "hook-abc"
    return hooks


def _make_broadcaster() -> MagicMock:
    bc = MagicMock()
    bc.list_workers.return_value = []
    return bc


def _make_model_search_registry() -> MagicMock:
    reg = MagicMock()
    mock_result = MagicMock()
    mock_result.model_id = "huggingface/model-a"
    mock_result.author = "author"
    mock_result.downloads = 1000
    mock_result.tags = ["nlp"]
    mock_result.pipeline_tag = "text-generation"
    mock_result.library_name = "transformers"
    reg.search.return_value = [mock_result]
    return reg


def _make_metrics_collector() -> MagicMock:
    mc = MagicMock()
    mc.list_agents.return_value = []
    estimate = MagicMock()
    estimate.total_cost_usd = 0.0
    mc.get_cost_estimate.return_value = estimate
    return mc


# ---------------------------------------------------------------------------
# Slurm
# ---------------------------------------------------------------------------

@pytest.fixture
def slurm_adapter_mock() -> MagicMock:
    from general_ludd.infra.slurm import SlurmJobInfo, SlurmJobState

    mock_adapter = MagicMock()
    mock_adapter.available = MagicMock(return_value=True)
    mock_adapter.submit.return_value = "job-abc123"
    mock_adapter.status.return_value = SlurmJobInfo(
        job_id="job-abc123", state=SlurmJobState.RUNNING, exit_code=None,
    )
    mock_adapter.list_jobs.return_value = [
        SlurmJobInfo(job_id="job-1", state=SlurmJobState.COMPLETED, exit_code=0),
        SlurmJobInfo(job_id="job-2", state=SlurmJobState.RUNNING, exit_code=None),
    ]
    mock_adapter.cancel.return_value = None
    return mock_adapter


@pytest.fixture
def slurm_app(
    slurm_adapter_mock: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> FastAPI:
    import general_ludd.routers.slurm as slurm_router

    monkeypatch.setattr(
        slurm_router,
        "SlurmAdapter",
        MagicMock(return_value=slurm_adapter_mock),
    )

    app = FastAPI()
    slurm_router.register(app, {})
    return app


@pytest.fixture
def slurm_client(slurm_app: FastAPI) -> TestClient:
    return TestClient(slurm_app)


class TestSlurmEndpoints:
    class TestStatus:
        def test_status_returns_available(self, slurm_client: TestClient) -> None:
            resp = slurm_client.get("/admin/slurm/status")
            assert resp.status_code == 200
            assert resp.json() == {"available": True}

    class TestSubmit:
        def test_submit_with_command_returns_job_id(self, slurm_client: TestClient) -> None:
            resp = slurm_client.post("/admin/slurm/submit", json={"command": "hostname"})
            assert resp.status_code == 200
            assert resp.json()["job_id"] == "job-abc123"

        def test_submit_missing_command_returns_422(self, slurm_client: TestClient) -> None:
            resp = slurm_client.post("/admin/slurm/submit", json={})
            assert resp.status_code == 422

        def test_submit_with_all_options(self, slurm_client: TestClient) -> None:
            resp = slurm_client.post(
                "/admin/slurm/submit",
                json={
                    "command": "hostname",
                    "job_name": "test-job",
                    "partition": "gpu",
                    "cpus_per_task": 4,
                    "gpus": "1",
                    "memory": "16G",
                    "time_limit": "01:00:00",
                    "output": "/tmp/out.txt",
                    "extra_args": ["--gres=gpu:1"],
                    "account": "myaccount",
                    "qos": "normal",
                },
            )
            assert resp.status_code == 200

    class TestJobStatus:
        def test_job_status_returns_info(self, slurm_client: TestClient) -> None:
            resp = slurm_client.get("/admin/slurm/jobs/job-abc123")
            assert resp.status_code == 200
            data = resp.json()
            assert data["job_id"] == "job-abc123"
            assert data["state"] == "RUNNING"

    class TestJobCancel:
        def test_job_cancel_returns_cancelled(self, slurm_client: TestClient) -> None:
            resp = slurm_client.delete("/admin/slurm/jobs/job-abc123")
            assert resp.status_code == 200
            assert resp.json() == {"cancelled": "job-abc123"}

    class TestJobsList:
        def test_jobs_list_returns_jobs(self, slurm_client: TestClient) -> None:
            resp = slurm_client.get("/admin/slurm/jobs")
            assert resp.status_code == 200
            data = resp.json()
            assert len(data["jobs"]) == 2
            assert data["jobs"][0]["job_id"] == "job-1"

    class TestJobCost:
        def test_job_cost_returns_breakdown(self, slurm_client: TestClient) -> None:
            resp = slurm_client.get("/admin/slurm/jobs/job-abc123/cost")
            assert resp.status_code == 200
            data = resp.json()
            assert data["job_id"] == "job-abc123"
            assert "cost_breakdown" in data
            assert "estimated_cost_usd" in data["cost_breakdown"]

    class TestEmptyStateDegradation:
        def test_slurm_not_installed_returns_503(
            self,
            monkeypatch: pytest.MonkeyPatch,
        ) -> None:
            import general_ludd.routers.slurm as slurm_router
            from general_ludd.infra.slurm import SlurmNotInstalledError

            mock_adapter = MagicMock()
            mock_adapter.available = MagicMock(side_effect=SlurmNotInstalledError())
            monkeypatch.setattr(
                slurm_router,
                "SlurmAdapter",
                MagicMock(return_value=mock_adapter),
            )

            app = FastAPI()
            slurm_router.register(app, {})
            client = TestClient(app)
            resp = client.get("/admin/slurm/status")
            assert resp.status_code == 503


_SLURM_PSK_CASES: ClassVar[list[tuple[str, str, dict[str, object] | None]]] = [
    ("GET", "/admin/slurm/status", None),
    ("POST", "/admin/slurm/submit", {"command": "hostname"}),
    ("GET", "/admin/slurm/jobs/job-abc123", None),
    ("DELETE", "/admin/slurm/jobs/job-abc123", None),
    ("GET", "/admin/slurm/jobs", None),
    ("GET", "/admin/slurm/jobs/job-abc123/cost", None),
]


class TestSlurmAuthPosture:
    @pytest.mark.parametrize("method,path,body", _SLURM_PSK_CASES)
    def test_unauthenticated_is_refused(
        self,
        method: str,
        path: str,
        body,
        slurm_adapter_mock: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import general_ludd.routers.slurm as slurm_router

        monkeypatch.setattr(
            slurm_router,
            "SlurmAdapter",
            MagicMock(return_value=slurm_adapter_mock),
        )
        client = TestClient(_app_with_psk_gate(slurm_router.register))
        resp = client.request(method, path, json=body)
        assert resp.status_code == 401

    @pytest.mark.parametrize("method,path,body", _SLURM_PSK_CASES)
    def test_with_psk_succeeds(
        self,
        method: str,
        path: str,
        body,
        slurm_adapter_mock: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import general_ludd.routers.slurm as slurm_router

        monkeypatch.setattr(
            slurm_router,
            "SlurmAdapter",
            MagicMock(return_value=slurm_adapter_mock),
        )
        client = TestClient(_app_with_psk_gate(slurm_router.register))
        resp = client.request(
            method, path, json=body, headers={"Authorization": f"Bearer {_PSK}"},
        )
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


def _setup_models_state(app: FastAPI) -> None:
    mock_gateway = MagicMock()
    mock_profile = MagicMock()
    mock_profile.model_profile_id = "model-1"
    mock_profile.model_dump.return_value = {"model_profile_id": "model-1"}
    mock_gateway.add_profile.return_value = mock_profile
    mock_gateway.list_profiles.return_value = [mock_profile]

    mock_response = MagicMock()
    mock_response.content = "hello world"
    mock_response.usage_metadata = {}
    mock_gateway.call_model.return_value = mock_response

    app.state._model_gateway = mock_gateway
    app.state._health_tracker = MagicMock()
    app.state._health_tracker.get_health.return_value = {"profile": "model-1", "healthy": True}


def _mock_models_subsystems(models_module) -> None:
    bus = _make_bus()
    hooks = _make_hooks()
    bc = _make_broadcaster()
    models_module._get_or_create_subsystems = MagicMock(
        return_value={"bus": bus, "hooks": hooks, "broadcaster": bc},
    )
    models_module._get_or_create_extended_subsystems = MagicMock(
        return_value={
            "model_registry": _make_model_search_registry(),
            "metrics": _make_metrics_collector(),
        },
    )


@pytest.fixture
def models_app() -> FastAPI:
    import general_ludd.routers.models as models_router

    _mock_models_subsystems(models_router)

    app = FastAPI()
    _setup_models_state(app)
    models_router.register(app, {})
    return app


@pytest.fixture
def models_client(models_app: FastAPI) -> TestClient:
    return TestClient(models_app)


class TestModelsEndpoints:
    class TestAddModel:
        def test_add_model_returns_profile(self, models_client: TestClient) -> None:
            resp = models_client.post("/admin/models", json={"model_id": "my-model"})
            assert resp.status_code == 200
            data = resp.json()
            assert data["model_id"] == "my-model"
            assert "profile" in data

    class TestListModels:
        def test_list_models_returns_profiles(self, models_client: TestClient) -> None:
            resp = models_client.get("/admin/models")
            assert resp.status_code == 200
            data = resp.json()
            assert len(data["profiles"]) == 1

        def test_list_models_empty_without_gateway(self) -> None:
            import general_ludd.routers.models as models_router

            _mock_models_subsystems(models_router)
            app = FastAPI()
            models_router.register(app, {})
            client = TestClient(app)
            resp = client.get("/admin/models")
            assert resp.status_code == 200
            assert resp.json() == {"profiles": []}

    class TestModelsHealth:
        def test_health_returns_data(self, models_client: TestClient) -> None:
            resp = models_client.get("/admin/models/health")
            assert resp.status_code == 200
            data = resp.json()
            assert "health" in data

        def test_health_empty_without_tracker(self) -> None:
            import general_ludd.routers.models as models_router

            _mock_models_subsystems(models_router)
            app = FastAPI()
            models_router.register(app, {})
            client = TestClient(app)
            resp = client.get("/admin/models/health")
            assert resp.status_code == 200
            assert resp.json() == {"health": []}

    class TestModelSearch:
        def test_search_returns_results(self, models_client: TestClient) -> None:
            resp = models_client.post(
                "/admin/models/search", json={"query": "bert", "limit": 5},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert len(data["results"]) == 1
            assert data["results"][0]["model_id"] == "huggingface/model-a"

    class TestModelCall:
        def test_call_with_prompt_returns_text(self, models_client: TestClient) -> None:
            resp = models_client.post(
                "/admin/models/call", json={"prompt": "Hello"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["text"] == "hello world"
            assert "model_profile_id" in data

        def test_call_missing_prompt_returns_422(self, models_client: TestClient) -> None:
            resp = models_client.post("/admin/models/call", json={})
            assert resp.status_code == 422

        def test_call_max_tokens_exceeds_limit_returns_413(self, models_client: TestClient) -> None:
            resp = models_client.post(
                "/admin/models/call",
                json={"prompt": "Hello", "max_tokens": 10_000_000},
            )
            assert resp.status_code == 413

        def test_call_no_profiles_returns_503(self) -> None:
            import general_ludd.routers.models as models_router

            _mock_models_subsystems(models_router)
            app = FastAPI()
            app.state._model_gateway = None
            models_router.register(app, {})
            with TestClient(app) as client:
                resp = client.post("/admin/models/call", json={"prompt": "Hello"})
            assert resp.status_code == 503

        def test_fallback_gateway_closes_on_app_shutdown(self) -> None:
            import general_ludd.routers.models as models_router

            _mock_models_subsystems(models_router)
            cache = MagicMock()
            app = FastAPI()
            app.state._model_gateway = None
            with patch.object(
                models_router,
                "ModelResponseCache",
                return_value=cache,
            ):
                models_router.register(app, {})
                with TestClient(app) as client:
                    resp = client.post("/admin/models/call", json={"prompt": "Hello"})
                    assert resp.status_code == 503

            cache.close.assert_called_once()

        def test_injected_gateway_is_not_closed_by_router_shutdown(self) -> None:
            import general_ludd.routers.models as models_router

            _mock_models_subsystems(models_router)
            gateway = MagicMock()
            gateway.list_profiles.return_value = []
            app = FastAPI()
            app.state._model_gateway = gateway
            models_router.register(app, {})
            with TestClient(app) as client:
                resp = client.get("/admin/models")
                assert resp.status_code == 200

            gateway.close.assert_not_called()

    class TestModelWorkflow:
        def test_workflow_with_messages_returns_result(self, models_client: TestClient) -> None:
            resp = models_client.post(
                "/admin/models/workflow",
                json={"messages": [{"role": "user", "content": "hi"}]},
            )
            assert resp.status_code == 200

        def test_workflow_missing_messages_returns_422(self, models_client: TestClient) -> None:
            resp = models_client.post("/admin/models/workflow", json={})
            assert resp.status_code == 422

        def test_workflow_empty_messages_returns_422(self, models_client: TestClient) -> None:
            resp = models_client.post(
                "/admin/models/workflow", json={"messages": []},
            )
            assert resp.status_code == 422


_MODELS_PSK_CASES: ClassVar[list[tuple[str, str, dict[str, object] | None]]] = [
    ("POST", "/admin/models", {"model_id": "m"}),
    ("GET", "/admin/models", None),
    ("GET", "/admin/models/health", None),
    ("POST", "/admin/models/search", {"query": "test"}),
    ("POST", "/admin/models/call", {"prompt": "Hello"}),
    ("POST", "/admin/models/workflow", {"messages": [{"role": "user", "content": "hi"}]}),
]


class TestModelsAuthPosture:
    @pytest.mark.parametrize("method,path,body", _MODELS_PSK_CASES)
    def test_unauthenticated_is_refused(self, method: str, path: str, body) -> None:
        import general_ludd.routers.models as models_router

        _mock_models_subsystems(models_router)

        client = TestClient(
            _app_with_psk_gate(models_router.register, setup_state=_setup_models_state),
        )
        resp = client.request(method, path, json=body)
        assert resp.status_code == 401

    @pytest.mark.parametrize("method,path,body", _MODELS_PSK_CASES)
    def test_with_psk_succeeds(self, method: str, path: str, body) -> None:
        import general_ludd.routers.models as models_router

        _mock_models_subsystems(models_router)

        client = TestClient(
            _app_with_psk_gate(models_router.register, setup_state=_setup_models_state),
        )
        resp = client.request(
            method, path, json=body, headers={"Authorization": f"Bearer {_PSK}"},
        )
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Reload
# ---------------------------------------------------------------------------


def _mock_reload_subsystems(reload_module) -> dict:
    bus = _make_bus()
    hooks = _make_hooks()
    bc = _make_broadcaster()
    subsys = {"bus": bus, "hooks": hooks, "broadcaster": bc}
    reload_module._get_or_create_subsystems = MagicMock(return_value=subsys)
    return subsys


def _setup_reload_state(app: FastAPI) -> None:
    bus = _make_bus()
    hooks = _make_hooks()
    bc = _make_broadcaster()

    app.state._event_bus = bus
    app.state._hook_system = hooks
    app.state._worker_broadcaster = bc
    app.state._config_dir = "/tmp/gl-test-config"
    app.state._templates_dir = None
    app.state._playbooks_dir = None
    app.state._prompt_registry = MagicMock()
    app.state._prompt_registry.refresh.return_value = {"templates": []}
    app.state._prompt_registry.list_templates.return_value = []
    app.state._runner = MagicMock()
    app.state._runner.refresh_playbooks.return_value = {"playbooks": []}
    app.state._runner.list_playbooks.return_value = []
    app.state._startup_config = {
        "user_config": None, "rules": [], "model_profiles": [],
        "queues": [], "budget": {}, "self_improve": {},
    }


@pytest.fixture
def reload_app() -> FastAPI:
    import general_ludd.routers.reload as reload_router

    _mock_reload_subsystems(reload_router)
    with (
        patch.object(reload_router, "HotReloader") as mock_hr,
        patch.object(reload_router, "snapshot_modules") as mock_snap,
        patch.object(reload_router, "restore_modules") as mock_restore,
        patch("general_ludd.daemon.load_startup_config") as mock_load_cfg,
    ):
        mock_result = MagicMock()
        mock_result.success = True
        mock_result.scope = "all"
        mock_result.details = {}
        mock_result.error = None
        mock_hr.return_value.reload.return_value = mock_result
        mock_snap.return_value = MagicMock(
            modules={"general_ludd.x": MagicMock()}, warnings=[],
        )
        mock_restore.return_value = ["general_ludd.x"]
        mock_load_cfg.return_value = {
            "user_config": None, "rules": [], "model_profiles": [],
        }

        app = FastAPI()
        _setup_reload_state(app)
        reload_router.register(app, {})
        return app


@pytest.fixture
def reload_client(reload_app: FastAPI) -> TestClient:
    return TestClient(reload_app)


class TestReloadEndpoints:
    class TestReload:
        def test_reload_returns_success(self, reload_client: TestClient) -> None:
            resp = reload_client.post("/admin/reload", json={"scope": "all"})
            assert resp.status_code == 200
            data = resp.json()
            assert data["success"] is True
            assert data["scope"] == "all"

    class TestRollback:
        def test_rollback_no_snapshot_returns_false(self, reload_client: TestClient) -> None:
            resp = reload_client.post("/admin/rollback", json={})
            assert resp.status_code == 200
            data = resp.json()
            assert data["success"] is False
            assert "no module snapshot" in data.get("error", "")

    class TestConfigReload:
        def test_config_reload_returns_success(self, reload_client: TestClient) -> None:
            resp = reload_client.post("/admin/config/reload")
            assert resp.status_code == 200
            data = resp.json()
            assert data["success"] is True

    class TestReloadStatus:
        def test_status_returns_events(self, reload_client: TestClient) -> None:
            resp = reload_client.get("/admin/reload/status")
            assert resp.status_code == 200
            data = resp.json()
            assert "recent_events" in data
            assert "total_events" in data

    class TestWorkers:
        def test_register_worker_returns_success(self, reload_client: TestClient) -> None:
            resp = reload_client.post(
                "/admin/workers",
                json={"worker_id": "w1", "address": "https://worker.example.com"},
            )
            assert resp.status_code == 200

        def test_register_worker_bad_address_returns_422(self, reload_client: TestClient) -> None:
            resp = reload_client.post(
                "/admin/workers",
                json={"worker_id": "w1", "address": "http://localhost:8000"},
            )
            assert resp.status_code == 422

    class TestHooks:
        def test_register_hook_returns_hook_id(self, reload_client: TestClient) -> None:
            resp = reload_client.post(
                "/admin/hooks",
                json={
                    "event_name": "test.event",
                    "url": "https://hooks.example.com/webhook",
                },
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["hook_id"] == "hook-abc"

        def test_register_hook_forbidden_header_returns_422(
            self, reload_client: TestClient,
        ) -> None:
            resp = reload_client.post(
                "/admin/hooks",
                json={
                    "event_name": "test.event",
                    "url": "https://hooks.example.com/webhook",
                    "headers": {"authorization": "Bearer secret"},
                },
            )
            assert resp.status_code == 422

        def test_register_hook_unsafe_url_returns_422(
            self, reload_client: TestClient,
        ) -> None:
            resp = reload_client.post(
                "/admin/hooks",
                json={
                    "event_name": "test.event",
                    "url": "http://169.254.169.254/latest/meta-data",
                },
            )
            assert resp.status_code == 422


_RELOAD_PSK_CASES: ClassVar[list[tuple[str, str, dict[str, object] | None]]] = [
    ("POST", "/admin/reload", {"scope": "all"}),
    ("POST", "/admin/rollback", {}),
    ("POST", "/admin/config/reload", None),
    ("GET", "/admin/reload/status", None),
    ("POST", "/admin/workers", {"worker_id": "w1", "address": "https://w.example.com"}),
    ("POST", "/admin/hooks", {"event_name": "e", "url": "https://h.example.com/w"}),
]


class TestReloadAuthPosture:
    @pytest.mark.parametrize("method,path,body", _RELOAD_PSK_CASES)
    def test_unauthenticated_is_refused(self, method: str, path: str, body) -> None:
        import general_ludd.routers.reload as reload_router

        _mock_reload_subsystems(reload_router)
        with (
            patch.object(reload_router, "HotReloader"),
            patch.object(reload_router, "snapshot_modules"),
            patch.object(reload_router, "restore_modules"),
            patch("general_ludd.daemon.load_startup_config"),
        ):
            client = TestClient(
                _app_with_psk_gate(reload_router.register, setup_state=_setup_reload_state),
            )
            resp = client.request(method, path, json=body)
            assert resp.status_code == 401

    @pytest.mark.parametrize("method,path,body", _RELOAD_PSK_CASES)
    def test_with_psk_succeeds(self, method: str, path: str, body) -> None:
        import general_ludd.routers.reload as reload_router

        _mock_reload_subsystems(reload_router)
        with (
            patch.object(reload_router, "HotReloader") as mock_hr,
            patch.object(reload_router, "snapshot_modules") as mock_snap,
            patch.object(reload_router, "restore_modules") as mock_restore,
            patch("general_ludd.daemon.load_startup_config"),
        ):
            mock_result = MagicMock()
            mock_result.success = True
            mock_result.scope = "all"
            mock_result.details = {}
            mock_result.error = None
            mock_hr.return_value.reload.return_value = mock_result
            mock_snap.return_value = MagicMock(
                modules={"general_ludd.x": MagicMock()}, warnings=[],
            )
            mock_restore.return_value = ["general_ludd.x"]

            client = TestClient(
                _app_with_psk_gate(reload_router.register, setup_state=_setup_reload_state),
            )
            resp = client.request(
                method, path, json=body, headers={"Authorization": f"Bearer {_PSK}"},
            )
            assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Registration smoke (all three)
# ---------------------------------------------------------------------------


class TestAllRoutersRegister:
    def test_slurm_register_adds_routes(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import general_ludd.routers.slurm as slurm_router

        mock_adapter = MagicMock()
        mock_adapter.available = MagicMock(return_value=True)
        monkeypatch.setattr(
            slurm_router,
            "SlurmAdapter",
            MagicMock(return_value=mock_adapter),
        )

        app = FastAPI()
        before = len(app.routes)
        slurm_router.register(app, {})
        assert len(app.routes) > before

    def test_models_register_adds_routes(self) -> None:
        import general_ludd.routers.models as models_router

        _mock_models_subsystems(models_router)
        app = FastAPI()
        _setup_models_state(app)
        before = len(app.routes)
        models_router.register(app, {})
        assert len(app.routes) > before

    def test_reload_register_adds_routes(self) -> None:
        import general_ludd.routers.reload as reload_router

        _mock_reload_subsystems(reload_router)
        with (
            patch.object(reload_router, "HotReloader"),
            patch.object(reload_router, "snapshot_modules"),
            patch.object(reload_router, "restore_modules"),
        ):
            app = FastAPI()
            _setup_reload_state(app)
            before = len(app.routes)
            reload_router.register(app, {})
            assert len(app.routes) > before


# ═══════════════════════════════════════════════════════════════════════════
# Adversarial
# ═══════════════════════════════════════════════════════════════════════════


_ADV_PSK_CASES: ClassVar[list[tuple[str, str, dict[str, object] | None]]] = [
    ("POST", "/admin/security/scan-text", {"text": "eval(user_input)"}),
    ("POST", "/admin/security/scan-file", {"file_path": "/tmp/test.py"}),
    ("GET", "/admin/security/adversarial/report", None),
]


def _make_adversarial_detector() -> MagicMock:
    det = MagicMock()
    finding = MagicMock()
    finding.pattern_id = "ADV-001"
    finding.category = MagicMock(value="injection")
    finding.severity = MagicMock(value="critical")
    finding.description = "eval injection"
    finding.match_text = "eval("
    finding.file_path = None
    finding.line_number = 1
    finding.confidence = 0.95
    finding.remediation = "Remove eval()"

    result = MagicMock()
    result.findings = [finding]
    result.high_confidence = True
    result.scanned_files = 1
    result.lines_scanned = 100
    result.critical_count = 1
    result.blocked = True

    det.scan_text.return_value = result
    det.scan_file.return_value = result

    cat = MagicMock(value="injection")
    det.get_all_categories.return_value = [cat]
    pattern = MagicMock()
    pattern.id = "ADV-001"
    pattern.description = "eval injection"
    pattern.severity = MagicMock(value="critical")
    det.get_patterns_by_category.return_value = [pattern]
    return det


def _setup_adversarial_state(app: FastAPI) -> None:
    app.state._adversarial_detector = _make_adversarial_detector()


@pytest.fixture
def adv_app() -> FastAPI:
    import general_ludd.routers.adversarial as adv_router

    app = FastAPI()
    _setup_adversarial_state(app)
    adv_router.register(app, {})
    return app


@pytest.fixture
def adv_client(adv_app: FastAPI) -> TestClient:
    return TestClient(adv_app)


class TestAdversarialEndpoints:
    class TestScanText:
        def test_happy_path(self, adv_client: TestClient) -> None:
            resp = adv_client.post(
                "/admin/security/scan-text",
                json={"text": "x = eval(user_input)"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["critical_count"] == 1
            assert data["blocked"] is True
            assert data["high_confidence"] is True

        def test_with_file_path(self, adv_client: TestClient) -> None:
            resp = adv_client.post(
                "/admin/security/scan-text",
                json={"text": "x = 1", "file_path": "/src/main.py"},
            )
            assert resp.status_code == 200

        def test_missing_text_returns_422(self, adv_client: TestClient) -> None:
            resp = adv_client.post("/admin/security/scan-text", json={})
            assert resp.status_code == 422

        def test_empty_text_returns_422(self, adv_client: TestClient) -> None:
            resp = adv_client.post("/admin/security/scan-text", json={"text": ""})
            assert resp.status_code == 422

    class TestScanFile:
        def test_happy_path(self, adv_client: TestClient) -> None:
            resp = adv_client.post(
                "/admin/security/scan-file",
                json={"file_path": "/tmp/test.py"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["parsed"] is True

        def test_missing_file_path_returns_422(self, adv_client: TestClient) -> None:
            resp = adv_client.post("/admin/security/scan-file", json={})
            assert resp.status_code == 422

        def test_empty_file_path_returns_422(self, adv_client: TestClient) -> None:
            resp = adv_client.post(
                "/admin/security/scan-file", json={"file_path": ""}
            )
            assert resp.status_code == 422

        def test_permission_error_returns_400(self) -> None:
            import general_ludd.routers.adversarial as adv_router

            det = _make_adversarial_detector()
            det.scan_file.side_effect = PermissionError("access denied")
            app = FastAPI()
            app.state._adversarial_detector = det
            adv_router.register(app, {})
            client = TestClient(app)
            resp = client.post(
                "/admin/security/scan-file",
                json={"file_path": "/etc/shadow"},
            )
            assert resp.status_code == 400

    class TestReport:
        def test_happy_path(self, adv_client: TestClient) -> None:
            resp = adv_client.get("/admin/security/adversarial/report")
            assert resp.status_code == 200
            data = resp.json()
            assert "generated_at" in data
            assert "categories" in data
            assert data["total_patterns"] >= 0

        def test_with_category_filter(self, adv_client: TestClient) -> None:
            resp = adv_client.get(
                "/admin/security/adversarial/report?category=injection"
            )
            assert resp.status_code == 200

        def test_limit_out_of_range_returns_422(
            self, adv_client: TestClient
        ) -> None:
            resp = adv_client.get("/admin/security/adversarial/report?limit=0")
            assert resp.status_code == 422

    class TestAuthPosture:
        @pytest.mark.parametrize("method,path,body", _ADV_PSK_CASES)
        def test_unauthenticated_is_refused(
            self, method: str, path: str, body
        ) -> None:
            import general_ludd.routers.adversarial as adv_router

            client = TestClient(
                _app_with_psk_gate(
                    adv_router.register, setup_state=_setup_adversarial_state
                )
            )
            resp = client.request(method, path, json=body)
            assert resp.status_code == 401

        @pytest.mark.parametrize("method,path,body", _ADV_PSK_CASES)
        def test_with_psk_succeeds(
            self, method: str, path: str, body
        ) -> None:
            import general_ludd.routers.adversarial as adv_router

            client = TestClient(
                _app_with_psk_gate(
                    adv_router.register, setup_state=_setup_adversarial_state
                )
            )
            resp = client.request(
                method,
                path,
                json=body,
                headers={"Authorization": f"Bearer {_PSK}"},
            )
            assert resp.status_code == 200


# ═══════════════════════════════════════════════════════════════════════════
# Signing
# ═══════════════════════════════════════════════════════════════════════════


_SIGNING_PSK_CASES: ClassVar[list[tuple[str, str, dict[str, object] | None]]] = [
    ("POST", "/admin/signing/cosign/generate", {"project_id": "p1", "key_name": "k1"}),
    ("GET", "/admin/signing/cosign/list/p1", None),
    ("GET", "/admin/signing/cosign/p1/k1", None),
    ("DELETE", "/admin/signing/cosign/p1/k1", None),
    ("POST", "/admin/signing/gitsign/config", {"project_id": "p1"}),
    ("GET", "/admin/signing/gitsign/p1", None),
]
_SIGNING_ADMIN_TOKEN = "test-admin-token"
_SIGNING_ADMIN_HEADERS = {"X-Admin-Token": _SIGNING_ADMIN_TOKEN}


def _make_secrets_resolver() -> MagicMock:
    r = MagicMock()
    r.write_secret = MagicMock()
    r.read_secret = MagicMock()
    r.delete_secret = MagicMock()
    r.list_secrets = MagicMock(return_value=[])
    return r


def _make_cosign_key_mock() -> MagicMock:
    k = MagicMock()
    k.key_name = "test-key"
    k.public_key = "-----BEGIN PUBLIC KEY-----\nMOCK\n-----END PUBLIC KEY-----"
    k.created_at = "2024-01-01T00:00:00Z"
    return k


def _make_gitsign_config_mock() -> MagicMock:
    c = MagicMock()
    c.fulcio_url = "https://fulcio.example.com"
    c.rekor_url = "https://rekor.example.com"
    c.oidc_issuer = "https://oauth2.example.com/auth"
    c.key_ref = "refs/heads/main"
    c.enabled = True
    return c


def _setup_signing_state(app: FastAPI) -> None:
    app.state._secrets_resolver = _make_secrets_resolver()


@pytest.fixture
def signing_app() -> FastAPI:
    import general_ludd.routers.signing as signing_router

    app = FastAPI()
    _setup_signing_state(app)
    signing_router.register(app, {})
    return app


@pytest.fixture
def signing_client(signing_app: FastAPI) -> TestClient:
    return _signing_test_client(signing_app)


def _signing_test_client(app: FastAPI, **kwargs: object) -> TestClient:
    return TestClient(app, headers=_SIGNING_ADMIN_HEADERS, **kwargs)


class TestSigningEndpoints:
    @pytest.fixture(autouse=True)
    def _signing_admin_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GLUDD_ADMIN_TOKEN", _SIGNING_ADMIN_TOKEN)

    class TestCosignGenerate:
        def test_happy_path(self) -> None:
            import general_ludd.routers.signing as signing_router

            app = FastAPI()
            _setup_signing_state(app)
            signing_router.register(app, {})
            with patch.object(
                signing_router, "generate_and_store_cosign_key",
                return_value=_make_cosign_key_mock(),
            ):
                client = _signing_test_client(app)
                resp = client.post(
                    "/admin/signing/cosign/generate",
                    json={"project_id": "p1", "key_name": "my-key"},
                )
            assert resp.status_code == 200
            assert resp.json()["key_name"] == "test-key"

        def test_value_error_returns_400(self) -> None:
            import general_ludd.routers.signing as signing_router

            app = FastAPI()
            _setup_signing_state(app)
            signing_router.register(app, {})
            with patch.object(
                signing_router, "generate_and_store_cosign_key",
                side_effect=ValueError("invalid key name"),
            ):
                client = _signing_test_client(app)
                resp = client.post(
                    "/admin/signing/cosign/generate",
                    json={"project_id": "p1", "key_name": "bad..name"},
                )
            assert resp.status_code == 400

    class TestCosignList:
        def test_happy_path(self) -> None:
            import general_ludd.routers.signing as signing_router

            app = FastAPI()
            resolver = _make_secrets_resolver()
            resolver.list_secrets.return_value = [
                "projects/p1/cosign/key-a",
                "projects/p1/cosign/key-b",
            ]
            app.state._secrets_resolver = resolver
            signing_router.register(app, {})
            with patch.object(
                signing_router, "read_cosign_key",
                return_value=_make_cosign_key_mock(),
            ):
                client = _signing_test_client(app)
                resp = client.get("/admin/signing/cosign/list/p1")
            assert resp.status_code == 200
            assert len(resp.json()) == 2

    class TestCosignRead:
        def test_happy_path(self) -> None:
            import general_ludd.routers.signing as signing_router

            app = FastAPI()
            _setup_signing_state(app)
            signing_router.register(app, {})
            with patch.object(
                signing_router, "read_cosign_key",
                return_value=_make_cosign_key_mock(),
            ):
                client = _signing_test_client(app)
                resp = client.get("/admin/signing/cosign/p1/my-key")
            assert resp.status_code == 200
            assert resp.json()["key_name"] == "test-key"

        def test_not_found_returns_404(self) -> None:
            import general_ludd.routers.signing as signing_router

            app = FastAPI()
            _setup_signing_state(app)
            signing_router.register(app, {})
            with patch.object(
                signing_router, "read_cosign_key", return_value=None,
            ):
                client = _signing_test_client(app)
                resp = client.get("/admin/signing/cosign/p1/nonexistent")
            assert resp.status_code == 404

    class TestCosignDelete:
        def test_happy_path(self) -> None:
            import general_ludd.routers.signing as signing_router

            app = FastAPI()
            _setup_signing_state(app)
            signing_router.register(app, {})
            with patch.object(signing_router, "delete_cosign_key"):
                client = _signing_test_client(app)
                resp = client.delete("/admin/signing/cosign/p1/my-key")
            assert resp.status_code == 200
            assert resp.json()["status"] == "deleted"

    class TestGitsignWrite:
        def test_happy_path(self) -> None:
            import general_ludd.routers.signing as signing_router

            app = FastAPI()
            _setup_signing_state(app)
            signing_router.register(app, {})
            with patch.object(signing_router, "write_gitsign_config"):
                client = _signing_test_client(app)
                resp = client.post(
                    "/admin/signing/gitsign/config",
                    json={"project_id": "p1", "enabled": True},
                )
            assert resp.status_code == 200
            assert resp.json()["status"] == "ok"

    class TestGitsignRead:
        def test_happy_path(self) -> None:
            import general_ludd.routers.signing as signing_router

            app = FastAPI()
            _setup_signing_state(app)
            signing_router.register(app, {})
            with patch.object(
                signing_router, "read_gitsign_config",
                return_value=_make_gitsign_config_mock(),
            ):
                client = _signing_test_client(app)
                resp = client.get("/admin/signing/gitsign/p1")
            assert resp.status_code == 200
            data = resp.json()
            assert data["fulcio_url"] == "https://fulcio.example.com"
            assert data["enabled"] is True

        def test_not_found_returns_404(self) -> None:
            import general_ludd.routers.signing as signing_router

            app = FastAPI()
            _setup_signing_state(app)
            signing_router.register(app, {})
            with patch.object(
                signing_router, "read_gitsign_config", return_value=None,
            ):
                client = _signing_test_client(app)
                resp = client.get("/admin/signing/gitsign/p1")
            assert resp.status_code == 404

    class TestEmptyStateDegradation:
        def test_generate_without_resolver_returns_503(self) -> None:
            import general_ludd.routers.signing as signing_router

            app = FastAPI()
            signing_router.register(app, {})
            client = _signing_test_client(app)
            resp = client.post(
                "/admin/signing/cosign/generate",
                json={"project_id": "p1", "key_name": "k1"},
            )
            assert resp.status_code == 503

        def test_list_without_resolver_returns_503(self) -> None:
            import general_ludd.routers.signing as signing_router

            app = FastAPI()
            signing_router.register(app, {})
            client = _signing_test_client(app)
            resp = client.get("/admin/signing/cosign/list/p1")
            assert resp.status_code == 503

        def test_read_without_resolver_returns_503(self) -> None:
            import general_ludd.routers.signing as signing_router

            app = FastAPI()
            signing_router.register(app, {})
            client = _signing_test_client(app)
            resp = client.get("/admin/signing/cosign/p1/k1")
            assert resp.status_code == 503

        def test_delete_without_resolver_returns_503(self) -> None:
            import general_ludd.routers.signing as signing_router

            app = FastAPI()
            signing_router.register(app, {})
            client = _signing_test_client(app)
            resp = client.delete("/admin/signing/cosign/p1/k1")
            assert resp.status_code == 503

        def test_gitsign_write_without_resolver_returns_503(self) -> None:
            import general_ludd.routers.signing as signing_router

            app = FastAPI()
            signing_router.register(app, {})
            client = _signing_test_client(app)
            resp = client.post(
                "/admin/signing/gitsign/config", json={"project_id": "p1"},
            )
            assert resp.status_code == 503

        def test_gitsign_read_without_resolver_returns_503(self) -> None:
            import general_ludd.routers.signing as signing_router

            app = FastAPI()
            signing_router.register(app, {})
            client = _signing_test_client(app)
            resp = client.get("/admin/signing/gitsign/p1")
            assert resp.status_code == 503

        def test_resolver_without_write_secret_returns_503(self) -> None:
            import general_ludd.routers.signing as signing_router

            app = FastAPI()
            resolver = MagicMock(spec=[])
            app.state._secrets_resolver = resolver
            signing_router.register(app, {})
            client = _signing_test_client(app)
            resp = client.post(
                "/admin/signing/cosign/generate",
                json={"project_id": "p1", "key_name": "k1"},
            )
            assert resp.status_code == 503

    class TestAuthPosture:
        @pytest.mark.parametrize("method,path,body", _SIGNING_PSK_CASES)
        def test_unauthenticated_is_refused(
            self, method: str, path: str, body
        ) -> None:
            import general_ludd.routers.signing as signing_router

            client = TestClient(
                _app_with_psk_gate(
                    signing_router.register, setup_state=_setup_signing_state,
                )
            )
            resp = client.request(method, path, json=body)
            assert resp.status_code == 401

        @pytest.mark.parametrize("method,path,body", _SIGNING_PSK_CASES)
        def test_with_psk_succeeds(
            self, method: str, path: str, body
        ) -> None:
            import general_ludd.routers.signing as signing_router

            with (
                patch.object(
                    signing_router, "generate_and_store_cosign_key",
                    return_value=_make_cosign_key_mock(),
                ),
                patch.object(
                    signing_router, "read_cosign_key",
                    return_value=_make_cosign_key_mock(),
                ),
                patch.object(signing_router, "delete_cosign_key"),
                patch.object(signing_router, "write_gitsign_config"),
                patch.object(
                    signing_router, "read_gitsign_config",
                    return_value=_make_gitsign_config_mock(),
                ),
            ):
                client = _signing_test_client(
                    _app_with_psk_gate(
                        signing_router.register,
                        setup_state=_setup_signing_state,
                    )
                )
                resp = client.request(
                    method,
                    path,
                    json=body,
                    headers={
                        "Authorization": f"Bearer {_PSK}",
                        **_SIGNING_ADMIN_HEADERS,
                    },
                )
                assert resp.status_code == 200


# ═══════════════════════════════════════════════════════════════════════════
# Self-Improve
# ═══════════════════════════════════════════════════════════════════════════


_SI_PSK_CASES: ClassVar[list[tuple[str, str, dict[str, object] | None]]] = [
    ("POST", "/admin/self-improve/analyze", None),
    ("POST", "/admin/self-improve/run", None),
    ("POST", "/admin/self-improve/apply", {"kind": "config", "title": "Update config"}),
    ("GET", "/admin/self-improve/status", None),
    ("GET", "/admin/self-improve/approvals", None),
    ("POST", "/admin/self-improve/approvals/todo-1/approve", None),
    ("POST", "/admin/self-improve/approvals/todo-1/reject", {"reason": "not needed"}),
]


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
    session.execute = AsyncMock()
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
