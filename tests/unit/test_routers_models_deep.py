"""Deep tests for download and local model routes in routers/models.py."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

# ── helpers ──────────────────────────────────────────────────────────

_DEFAULT_MODEL = "qwen-0.5b"  # valid entry in _LOCAL_MODELS
_DEFAULT_TASK = "coding"  # valid entry in DEFAULT_TASK_CONTRACTS


def _build_app() -> FastAPI:
    from general_ludd.routers.models import register

    app = FastAPI()
    app.state._model_gateway = None
    app.state._health_tracker = None
    app.state._project_manager = None
    app.state._metrics_collector = None
    app.state._session_factory = None
    app.state._budget_guard = None

    _mock_registry = MagicMock()
    _mock_registry.search.return_value = []
    _mock_registry.list_downloaded.return_value = []
    app.state._model_registry = _mock_registry

    app.state._sm_server_store = {}
    app.state._sm_capability_store = {}
    app.state._sm_eval_store = {}
    app.state._sm_rollout_store = {}
    app.state._sm_radar_store = {}
    app.state._sm_quantize_store = {}
    app.state._model_downloader = MagicMock()
    app.state._searx_model_discoverer = None
    app.state._local_inference = None
    app.state._local_inference_manager = None
    app.state._small_model_task_policy = None
    app.state._sm_model_quantizer = MagicMock()
    app.state._hardware_inventory = None

    register(app, {})
    return app


def _client() -> TestClient:
    return TestClient(_build_app())


# ── Downloaded ───────────────────────────────────────────────────────


class TestDownloaded:
    def test_returns_empty_list_when_no_downloads(self):
        client = _client()
        app = _build_app()
        app.state._model_registry.list_downloaded.return_value = []
        from general_ludd.routers.models import register

        register(app, {})
        client = TestClient(app)
        resp = client.get("/admin/models/downloaded")
        assert resp.status_code == 200
        assert resp.json() == {"models": []}

    def test_returns_downloaded_models(self):
        from dataclasses import dataclass

        @dataclass
        class _Model:
            model_id: str
            local_path: str
            engine: str
            size_bytes: int

        client = _client()
        app = _build_app()
        app.state._model_registry.list_downloaded.return_value = [
            _Model("a", "/tmp/a", "llamacpp", 100),
            _Model("b", "/tmp/b", "llamacpp", 200),
        ]
        from general_ludd.routers.models import register

        register(app, {})
        client = TestClient(app)
        resp = client.get("/admin/models/downloaded")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["models"]) == 2
        assert data["models"][0]["model_id"] == "a"
        assert data["models"][1]["size_bytes"] == 200


# ── Local Serve ──────────────────────────────────────────────────────


class TestLocalServe:
    def test_model_id_required(self):
        client = _client()
        resp = client.post("/admin/models/local/serve", json={})
        assert resp.status_code == 422
        assert "model_id" in resp.json()["detail"]

    def test_port_below_1024_rejected(self):
        client = _client()
        resp = client.post(
            "/admin/models/local/serve",
            json={"model_id": _DEFAULT_MODEL, "port": 1},
        )
        assert resp.status_code == 422
        assert "port" in resp.json()["detail"]

    def test_port_above_65535_rejected(self):
        client = _client()
        resp = client.post(
            "/admin/models/local/serve",
            json={"model_id": _DEFAULT_MODEL, "port": 99999},
        )
        assert resp.status_code == 422
        assert "port" in resp.json()["detail"]

    def test_port_1024_accepted(self):
        app = _build_app()
        mgr = MagicMock()
        server = MagicMock()
        server.server_id = "s1"
        server.endpoint_url = "http://localhost:1024"
        server.status = "created"
        mgr.create_server.return_value = server
        app.state._local_inference_manager = mgr
        from general_ludd.routers.models import register

        register(app, {})
        client = TestClient(app)
        resp = client.post(
            "/admin/models/local/serve",
            json={"model_id": _DEFAULT_MODEL, "port": 1024},
        )
        assert resp.status_code == 200
        assert resp.json()["server_id"] == "s1"


# ── Local Download ───────────────────────────────────────────────────


class TestLocalDownload:
    def test_model_id_required(self):
        client = _client()
        resp = client.post("/admin/models/local/download", json={})
        assert resp.status_code == 422
        assert "model_id" in resp.json()["detail"]

    def test_invalid_source_rejected(self):
        client = _client()
        resp = client.post(
            "/admin/models/local/download",
            json={"model_id": _DEFAULT_MODEL, "source": "invalid-src"},
        )
        assert resp.status_code == 422
        assert "source" in resp.json()["detail"]

    def test_cloud_source_downloads_via_fallback(self):
        app = _build_app()
        downloader = MagicMock()
        downloader.timeout = 60
        downloader.cache_dir = "/tmp/models"
        app.state._model_downloader = downloader
        from general_ludd.routers.models import register

        register(app, {})
        client = TestClient(app)
        with patch(
            "general_ludd.routers.models.download_with_fallback",
            return_value=MagicMock(
                source=MagicMock(value="huggingface"),
                local_path="/tmp/model.gguf",
                size_bytes=12345,
            ),
        ):
            resp = client.post(
                "/admin/models/local/download",
                json={"model_id": _DEFAULT_MODEL, "source": "huggingface"},
                params={"source": "huggingface"},
            )
        assert resp.status_code == 200
        assert resp.json()["downloaded"] is True

    def test_source_ollama_pulls(self):
        app = _build_app()
        downloader = MagicMock()
        from general_ludd.small_models.download import DownloadedModel, DownloadSource

        downloader.pull_ollama.return_value = DownloadedModel(
            model_id="m",
            local_path="/tmp/m",
            source=DownloadSource.OLLAMA,
            size_bytes=0,
        )
        app.state._model_downloader = downloader
        from general_ludd.routers.models import register

        register(app, {})
        client = TestClient(app)
        resp = client.post(
            "/admin/models/local/download",
            json={"model_id": "qwen-0.5b", "source": "ollama"},
            params={"source": "ollama"},
        )
        assert resp.status_code == 200
        assert resp.json()["source"] == "ollama"

    def test_source_multi_downloads_both(self):
        app = _build_app()
        downloader = MagicMock()
        from general_ludd.small_models.download import DownloadedModel, DownloadSource

        downloader.download.return_value = DownloadedModel(
            model_id="m",
            local_path="/tmp/m",
            source=DownloadSource.HUGGINGFACE,
            size_bytes=0,
        )
        app.state._model_downloader = downloader
        from general_ludd.routers.models import register

        register(app, {})
        client = TestClient(app)
        resp = client.post(
            "/admin/models/local/download",
            json={"model_id": "m", "source": "multi"},
            params={"source": "multi"},
        )
        assert resp.status_code == 200
        assert resp.json()["downloaded"] is True


# ── Local Quantize ───────────────────────────────────────────────────


class TestLocalQuantize:
    def test_model_id_required(self):
        client = _client()
        resp = client.post("/admin/models/local/quantize", json={})
        assert resp.status_code == 422
        assert "model_id" in resp.json()["detail"]

    def test_invalid_method_rejected(self):
        client = _client()
        resp = client.post(
            "/admin/models/local/quantize",
            json={"model_id": _DEFAULT_MODEL, "method": "q2_0"},
        )
        assert resp.status_code == 422
        assert "method" in resp.json()["detail"]

    def test_no_gguf_found_returns_422(self):
        app = _build_app()
        quantizer = MagicMock()
        app.state._sm_model_quantizer = quantizer
        from general_ludd.routers.models import register

        register(app, {})
        client = TestClient(app)
        with patch("os.path.isdir", return_value=False):
            resp = client.post(
                "/admin/models/local/quantize",
                json={"model_id": _DEFAULT_MODEL, "method": "q4_k_m"},
            )
        assert resp.status_code == 422
        assert "GGUF" in resp.json()["detail"]

    def test_quantize_with_input_path_succeeds(self):
        app = _build_app()
        quantizer = MagicMock()
        quantizer.quantize.return_value = True
        app.state._sm_model_quantizer = quantizer
        from general_ludd.routers.models import register

        register(app, {})
        client = TestClient(app)
        with (
            patch("os.path.isfile", return_value=True),
            patch("os.path.getsize", return_value=500),
            patch("os.makedirs"),
        ):
            resp = client.post(
                "/admin/models/local/quantize",
                json={
                    "model_id": _DEFAULT_MODEL,
                    "method": "q4_k_m",
                    "input_path": "/tmp/input.gguf",
                    "output_path": "/tmp/out.gguf",
                },
            )
        assert resp.status_code == 200
        assert resp.json()["quantized"] is True
        assert resp.json()["output_path"] == "/tmp/out.gguf"

    def test_quantize_failure_returns_success_false(self):
        app = _build_app()
        quantizer = MagicMock()
        quantizer.quantize.return_value = False
        app.state._sm_model_quantizer = quantizer
        from general_ludd.routers.models import register

        register(app, {})
        client = TestClient(app)
        with (
            patch("os.path.isfile", return_value=False),
            patch("os.path.getsize", return_value=0),
            patch("os.makedirs"),
        ):
            resp = client.post(
                "/admin/models/local/quantize",
                json={
                    "model_id": _DEFAULT_MODEL,
                    "method": "q4_k_m",
                    "input_path": "/tmp/in.gguf",
                    "output_path": "/tmp/out.gguf",
                },
            )
        assert resp.status_code == 200
        assert resp.json()["quantized"] is False


# ── Local Evaluate ───────────────────────────────────────────────────


class TestLocalEvaluate:
    def test_model_id_required(self):
        client = _client()
        resp = client.post("/admin/models/local/evaluate", json={})
        assert resp.status_code == 422
        assert "model_id" in resp.json()["detail"]

    def test_task_kind_required_when_not_benchmark(self):
        client = _client()
        resp = client.post(
            "/admin/models/local/evaluate",
            json={"model_id": _DEFAULT_MODEL},
        )
        assert resp.status_code == 422
        assert "task_kind" in resp.json()["detail"]

    def test_unknown_task_kind_rejected(self):
        client = _client()
        resp = client.post(
            "/admin/models/local/evaluate",
            json={"model_id": _DEFAULT_MODEL, "task_kind": "nonexistent"},
        )
        assert resp.status_code == 422
        assert "task_kind" in resp.json()["detail"]

    def test_valid_task_kind_accepted(self):
        client = _client()
        resp = client.post(
            "/admin/models/local/evaluate",
            json={"model_id": _DEFAULT_MODEL, "task_kind": _DEFAULT_TASK},
        )
        assert resp.status_code == 200
        assert resp.json()["evaluated"] is True
        assert "evidence" in resp.json()

    def test_benchmark_flag_skips_task_kind_check(self):
        client = _client()
        resp = client.post(
            "/admin/models/local/evaluate",
            json={"model_id": _DEFAULT_MODEL, "benchmark": True},
        )
        assert resp.status_code == 200
        assert resp.json()["benchmark"] is True

    def test_benchmark_with_custom_tasks(self):
        client = _client()
        resp = client.post(
            "/admin/models/local/evaluate",
            json={
                "model_id": _DEFAULT_MODEL,
                "benchmark": True,
                "tasks": ["mmlu", "hellaswag"],
            },
        )
        assert resp.status_code == 200
        assert resp.json()["tasks_run"] == ["mmlu", "hellaswag"]

    def test_evaluate_stores_evidence(self):
        app = _build_app()
        from general_ludd.routers.models import register

        register(app, {})
        client = TestClient(app)
        resp = client.post(
            "/admin/models/local/evaluate",
            json={
                "model_id": _DEFAULT_MODEL,
                "task_kind": _DEFAULT_TASK,
                "total_cases": 10,
                "passed_cases": 8,
            },
        )
        assert resp.status_code == 200
        ev = resp.json()["evidence"]
        assert ev["passed_cases"] == 8
        assert ev["total_cases"] == 10
        assert ev["passed"] is False


# ── Local Evidence ───────────────────────────────────────────────────


class TestLocalEvidence:
    def test_returns_all_when_no_model_id(self):
        client = _client()
        resp = client.get("/admin/models/local/evidence")
        assert resp.status_code == 200
        assert "evidence" in resp.json()

    def test_returns_filtered_by_model_id(self):
        client = _client()
        resp = client.get("/admin/models/local/evidence?model_id=m")
        assert resp.status_code == 200
        assert resp.json()["model_id"] == "m"
        assert resp.json()["evidence"] == []

    def test_returns_stored_evidence(self):
        app = _build_app()
        app.state._sm_capability_store = {"cap:m": [{"task_kind": _DEFAULT_TASK, "passed_cases": 5}]}
        from general_ludd.routers.models import register

        register(app, {})
        client = TestClient(app)
        resp = client.get("/admin/models/local/evidence?model_id=m")
        assert resp.status_code == 200
        assert len(resp.json()["evidence"]) == 1


# ── Local Status ─────────────────────────────────────────────────────


class TestLocalStatus:
    def test_returns_not_configured_when_no_manager(self):
        client = _client()
        resp = client.get("/admin/models/local/status")
        assert resp.status_code == 200
        assert resp.json()["status"] == "not_configured"
        assert resp.json()["servers"] == []

    def test_returns_servers_when_manager_present(self):
        app = _build_app()
        mgr = MagicMock()
        srv = MagicMock()
        srv.server_id = "s1"
        srv.config.model_name = _DEFAULT_MODEL
        srv.status = "running"
        srv.endpoint_url = "http://localhost:8080"
        srv.uptime_seconds = 10.5
        srv.pid = 1234
        mgr.list_servers.return_value = [srv]
        mgr.get_endpoints.return_value = ["http://localhost:8080"]
        app.state._local_inference_manager = mgr
        from general_ludd.routers.models import register

        register(app, {})
        client = TestClient(app)
        resp = client.get("/admin/models/local/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["servers"][0]["server_id"] == "s1"
        assert data["servers"][0]["status"] == "running"


# ── Local Consume ────────────────────────────────────────────────────


class TestLocalConsume:
    def test_server_id_required(self):
        client = _client()
        resp = client.post("/admin/models/local/consume", json={})
        assert resp.status_code == 422
        assert "server_id" in resp.json()["detail"]

    def test_prompt_required(self):
        client = _client()
        resp = client.post("/admin/models/local/consume", json={"server_id": "s1"})
        assert resp.status_code == 422
        assert "prompt" in resp.json()["detail"]

    def test_server_not_found(self):
        app = _build_app()
        mgr = MagicMock()
        mgr.list_servers.return_value = []
        app.state._local_inference_manager = mgr
        from general_ludd.routers.models import register

        register(app, {})
        client = TestClient(app)
        resp = client.post(
            "/admin/models/local/consume",
            json={"server_id": "s1", "prompt": "hello"},
        )
        assert resp.status_code == 404

    def test_server_not_running(self):
        app = _build_app()
        mgr = MagicMock()
        srv = MagicMock()
        srv.server_id = "s1"
        srv.status = "stopped"
        mgr.list_servers.return_value = [srv]
        app.state._local_inference_manager = mgr
        from general_ludd.routers.models import register

        register(app, {})
        client = TestClient(app)
        resp = client.post(
            "/admin/models/local/consume",
            json={"server_id": "s1", "prompt": "hello"},
        )
        assert resp.status_code == 503
        assert "not running" in resp.json()["detail"]


# ── Local Shutdown ───────────────────────────────────────────────────


class TestLocalShutdown:
    def test_server_id_required(self):
        client = _client()
        resp = client.post("/admin/models/local/shutdown", json={})
        assert resp.status_code == 422
        assert "server_id" in resp.json()["detail"]

    def test_server_not_found(self):
        app = _build_app()
        mgr = MagicMock()
        mgr.stop_server.side_effect = KeyError("not found")
        app.state._local_inference_manager = mgr
        from general_ludd.routers.models import register

        register(app, {})
        client = TestClient(app)
        resp = client.post(
            "/admin/models/local/shutdown",
            json={"server_id": "s1"},
        )
        assert resp.status_code == 404

    def test_shutdown_success(self):

        app = _build_app()
        mgr = MagicMock()
        mgr.stop_server = AsyncMock(return_value=None)
        app.state._local_inference_manager = mgr
        from general_ludd.routers.models import register

        register(app, {})
        client = TestClient(app)
        resp = client.post(
            "/admin/models/local/shutdown",
            json={"server_id": "s1"},
        )
        assert resp.status_code == 200
        assert resp.json()["shutdown"] is True
        assert resp.json()["server_id"] == "s1"


# ── Rollout ──────────────────────────────────────────────────────────


class TestRollout:
    def test_model_id_required(self):
        client = _client()
        resp = client.post("/admin/models/rollout", json={})
        assert resp.status_code == 422
        assert "model_id" in resp.json()["detail"]

    def test_invalid_target_rejected(self):
        client = _client()
        resp = client.post(
            "/admin/models/rollout",
            json={"model_id": _DEFAULT_MODEL, "target": "invalid"},
        )
        assert resp.status_code == 422
        assert "target" in resp.json()["detail"]

    def test_valid_target_local_accepted(self):
        app = _build_app()
        policy = MagicMock()
        policy.is_rollout_allowed.return_value = True
        app.state._small_model_task_policy = policy
        from general_ludd.routers.models import register

        register(app, {})
        client = TestClient(app)
        resp = client.post(
            "/admin/models/rollout",
            json={"model_id": _DEFAULT_MODEL, "target": "local"},
        )
        assert resp.status_code == 200
        assert resp.json()["model_id"] == _DEFAULT_MODEL

    def test_target_canary_accepted(self):
        app = _build_app()
        policy = MagicMock()
        app.state._small_model_task_policy = policy
        from general_ludd.routers.models import register

        register(app, {})
        client = TestClient(app)
        resp = client.post(
            "/admin/models/rollout",
            json={"model_id": _DEFAULT_MODEL, "target": "canary"},
        )
        assert resp.status_code == 200

    def test_target_full_accepted(self):
        app = _build_app()
        policy = MagicMock()
        app.state._small_model_task_policy = policy
        from general_ludd.routers.models import register

        register(app, {})
        client = TestClient(app)
        resp = client.post(
            "/admin/models/rollout",
            json={"model_id": _DEFAULT_MODEL, "target": "full"},
        )
        assert resp.status_code == 200

    def test_missing_evidence_with_task_kind_returns_412(self):
        app = _build_app()
        policy = MagicMock()
        app.state._small_model_task_policy = policy
        from general_ludd.routers.models import register

        register(app, {})
        client = TestClient(app)
        resp = client.post(
            "/admin/models/rollout",
            json={
                "model_id": _DEFAULT_MODEL,
                "target": "local",
                "task_kind": _DEFAULT_TASK,
            },
        )
        assert resp.status_code == 412
        assert "evidence" in resp.json()["detail"].lower()


# ── Recommend ────────────────────────────────────────────────────────


class TestRecommend:
    def test_returns_empty_with_no_evidence(self):
        client = _client()
        resp = client.get("/admin/models/recommend?task=coding")
        assert resp.status_code == 200
        assert resp.json()["recommendations"] == []
        assert resp.json()["total"] == 0

    def test_returns_matching_recommendations(self):
        app = _build_app()
        app.state._sm_eval_store = {
            "eval:m:coding": {
                "model_id": "m",
                "task_kind": "coding",
                "total_cases": 10,
                "passed_cases": 8,
                "passed": False,
                "evidence_digest": "abc",
            }
        }
        from general_ludd.routers.models import register

        register(app, {})
        client = TestClient(app)
        with (
            patch(
                "general_ludd.small_models.cost.estimate_inference_cost",
                return_value={"estimated_usd_per_hour": 0.01, "tier": "small_local"},
            ),
            patch(
                "general_ludd.small_models.cost.estimate_download_cost",
                return_value={
                    "size_gb": 1.0,
                    "data_transfer_usd": 0.0,
                    "estimated_storage_usd_per_month": 0.5,
                },
            ),
        ):
            resp = client.get("/admin/models/recommend?task=coding")
        assert resp.status_code == 200
        assert resp.json()["total"] == 1
        assert resp.json()["recommendations"][0]["model_id"] == "m"


# ── Cost ─────────────────────────────────────────────────────────────


class TestCost:
    def test_returns_cost_structure(self):
        client = _client()
        with (
            patch(
                "general_ludd.small_models.cost.estimate_inference_cost",
                return_value={"estimated_usd_per_hour": 0.01, "tier": "small_local"},
            ),
            patch(
                "general_ludd.small_models.cost.estimate_download_cost",
                return_value={
                    "size_gb": 1.0,
                    "data_transfer_usd": 0.0,
                    "estimated_storage_usd_per_month": 0.5,
                },
            ),
            patch(
                "general_ludd.small_models.cost.estimate_quantize_cost",
                return_value={"cpu_seconds": 60},
            ),
            patch(
                "general_ludd.small_models.cost.is_off_peak",
                return_value=False,
            ),
            patch(
                "general_ludd.small_models.cost.next_off_peak_window",
                return_value="22:00-06:00",
            ),
            patch(
                "general_ludd.small_models.cost.should_defer_download",
                return_value={"defer": False},
            ),
        ):
            resp = client.get("/admin/models/cost?model=m")
        assert resp.status_code == 200
        data = resp.json()
        assert data["model_id"] == "m"
        assert "inference" in data
        assert "download" in data
        assert "quantize" in data
        assert "off_peak" in data
        assert "scheduling" in data


# ── Tasks ────────────────────────────────────────────────────────────


class TestTasks:
    def test_returns_empty_for_unknown_model(self):
        client = _client()
        resp = client.get("/admin/models/tasks?model=nonexistent")
        assert resp.status_code == 200
        assert resp.json()["model_id"] == "nonexistent"
        assert resp.json()["tasks"] == []

    def test_returns_tasks_for_known_model(self):
        app = _build_app()
        app.state._sm_capability_store = {
            "cap:m": [
                {
                    "task_kind": _DEFAULT_TASK,
                    "passed_cases": 5,
                    "total_cases": 10,
                    "passed": False,
                    "role": "coder",
                }
            ]
        }
        from general_ludd.routers.models import register

        register(app, {})
        client = TestClient(app)
        resp = client.get("/admin/models/tasks?model=m")
        assert resp.status_code == 200
        assert resp.json()["total"] == 1
        assert resp.json()["tasks"][0]["task_kind"] == _DEFAULT_TASK


# ── Report ───────────────────────────────────────────────────────────


class TestReport:
    def test_returns_report_structure(self):
        client = _client()
        resp = client.get("/admin/models/report?model=m")
        assert resp.status_code == 200
        data = resp.json()
        assert "models" in data
        assert data["models"] == ["m"]


# ── Compare ──────────────────────────────────────────────────────────


class TestCompare:
    def test_model_ids_required(self):
        client = _client()
        resp = client.post("/admin/models/compare", json={})
        assert resp.status_code == 422
        assert "model_ids" in resp.json()["detail"]

    def test_at_least_two_required(self):
        client = _client()
        resp = client.post("/admin/models/compare", json={"model_ids": ["a"]})
        assert resp.status_code == 422
        assert "at least" in resp.json()["detail"].lower()

    def test_two_models_accepted(self):
        client = _client()
        resp = client.post("/admin/models/compare", json={"model_ids": ["a", "b"]})
        assert resp.status_code == 200


# ── Route Registration ───────────────────────────────────────────────


class TestRouteRegistration:
    def test_all_download_local_routes_registered(self):
        app = _build_app()
        from starlette.routing import Route

        routes = {r.path for r in app.routes if isinstance(r, Route)}
        expected = {
            "/admin/models/downloaded",
            "/admin/models/local/serve",
            "/admin/models/local/download",
            "/admin/models/local/quantize",
            "/admin/models/local/evaluate",
            "/admin/models/local/evidence",
            "/admin/models/local/status",
            "/admin/models/local/consume",
            "/admin/models/local/shutdown",
            "/admin/models/rollout",
        }
        missing = expected - routes
        assert not missing, f"Missing routes: {missing}"
