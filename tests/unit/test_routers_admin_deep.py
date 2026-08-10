"""Deep edge-case tests for untested admin compute routes.

Endpoints covered:
  - GET  /admin/compute/idle               (no state, idle, torn_down)
  - POST /admin/compute/endpoints          (validation errors, 422)
  - DELETE /admin/compute/endpoints/{id}   (no-op unknown, active=false)
  - GET  /admin/compute/gpu-metrics        (empty, mixed object/dict)
  - GET  /admin/compute/gpu-metrics/{id}   (not-found, matched)
"""

from __future__ import annotations

from unittest import mock
from unittest.mock import MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from general_ludd.routers.compute import register

_MODULE = "general_ludd.routers.compute._get_or_create_extended_subsystems"


def _make_app(daemon_state=None):
    app = FastAPI()
    if daemon_state is not None:
        app.state.daemon_state = daemon_state
    register(app, {})
    return app


def _compute_endpoint_stub(endpoint_id="ep-1", url="http://ep:8000", model="gpt", active=True):
    ep = MagicMock()
    ep.endpoint_id = endpoint_id
    ep.url = url
    ep.model = model
    ep.utilization = 0.25
    ep.current_load = 1
    ep.max_concurrent = 4
    ep.available_slots = 3
    ep.active = active
    return ep


class _FakeGpuMetric:
    def __init__(self, **kwargs: float):
        self._vals = kwargs

    def as_dict(self) -> dict[str, float]:
        return dict(self._vals)


# ---------------------------------------------------------------------------
# /admin/compute/idle  (no subsystems mock needed)
# ---------------------------------------------------------------------------


class TestAdminComputeIdle:
    def test_empty_when_no_daemon_state(self):
        app = _make_app()
        client = TestClient(app)
        resp = client.get("/admin/compute/idle")
        assert resp.status_code == 200
        assert resp.json() == {"idle_endpoints": [], "torn_down_endpoints": []}

    def test_empty_when_state_has_no_idle_keys(self):
        app = _make_app(daemon_state={"other": 1})
        client = TestClient(app)
        resp = client.get("/admin/compute/idle")
        data = resp.json()
        assert data["idle_endpoints"] == []
        assert data["torn_down_endpoints"] == []

    def test_returns_idle_endpoints(self):
        app = _make_app(
            daemon_state={
                "idle_endpoints": {"a": {"name": "ep-a"}, "b": {"name": "ep-b"}},
                "torn_down_endpoints": [],
            }
        )
        client = TestClient(app)
        resp = client.get("/admin/compute/idle")
        data = resp.json()
        assert len(data["idle_endpoints"]) == 2
        assert data["torn_down_endpoints"] == []

    def test_returns_torn_down_endpoints(self):
        app = _make_app(
            daemon_state={
                "idle_endpoints": {},
                "torn_down_endpoints": ["ep-x", "ep-y", "ep-z"],
            }
        )
        client = TestClient(app)
        resp = client.get("/admin/compute/idle")
        assert resp.json()["torn_down_endpoints"] == ["ep-x", "ep-y", "ep-z"]

    def test_mixed_idle_and_torn_down(self):
        app = _make_app(
            daemon_state={
                "idle_endpoints": {"id1": {"ip": "10.0.0.1"}},
                "torn_down_endpoints": ["td1"],
            }
        )
        client = TestClient(app)
        resp = client.get("/admin/compute/idle")
        data = resp.json()
        assert len(data["idle_endpoints"]) == 1
        assert data["torn_down_endpoints"] == ["td1"]

    def test_idle_endpoints_preserves_dict_values(self):
        app = _make_app(
            daemon_state={
                "idle_endpoints": {"a": {"ip": "10.0.0.1", "port": 8080}},
                "torn_down_endpoints": [],
            }
        )
        client = TestClient(app)
        resp = client.get("/admin/compute/idle")
        data = resp.json()
        assert {"ip": "10.0.0.1", "port": 8080} in data["idle_endpoints"]


# ---------------------------------------------------------------------------
# POST /admin/compute/endpoints  (validation edge cases)
# ---------------------------------------------------------------------------


class TestAdminRegisterComputeEndpoint:
    def test_missing_both_endpoint_id_and_url_returns_422(self):
        app = _make_app()
        client = TestClient(app)
        resp = client.post("/admin/compute/endpoints", json={})
        assert resp.status_code == 422
        assert "endpoint_id" in resp.json()["detail"]

    def test_missing_url_returns_422(self):
        app = _make_app()
        client = TestClient(app)
        resp = client.post("/admin/compute/endpoints", json={"endpoint_id": "ep-1"})
        assert resp.status_code == 422

    def test_missing_endpoint_id_returns_422(self):
        app = _make_app()
        client = TestClient(app)
        resp = client.post("/admin/compute/endpoints", json={"url": "http://x:8000"})
        assert resp.status_code == 422

    def test_empty_string_endpoint_id_returns_422(self):
        app = _make_app()
        client = TestClient(app)
        resp = client.post(
            "/admin/compute/endpoints",
            json={"endpoint_id": "", "url": "http://x:8000"},
        )
        assert resp.status_code == 422

    def test_empty_string_url_returns_422(self):
        app = _make_app()
        client = TestClient(app)
        resp = client.post(
            "/admin/compute/endpoints",
            json={"endpoint_id": "ep-1", "url": ""},
        )
        assert resp.status_code == 422

    def test_successful_registration_includes_defaults(self):
        ut = MagicMock()
        stub = _compute_endpoint_stub("ep-d", "http://d:8000", "gemma")
        ut.register_endpoint.return_value = stub
        ext = {"utilization": ut}
        with mock.patch(_MODULE, return_value=ext):
            app = _make_app()
            client = TestClient(app)
            resp = client.post(
                "/admin/compute/endpoints",
                json={"endpoint_id": "ep-d", "url": "http://d:8000", "model": "gemma"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["endpoint_id"] == "ep-d"
        assert data["model"] == "gemma"
        ut.register_endpoint.assert_called_once()
        call_kwargs = ut.register_endpoint.call_args.kwargs
        assert call_kwargs["gpu_count"] == 1
        assert call_kwargs["max_concurrent"] == 4

    def test_passes_optional_fields(self):
        ut = MagicMock()
        stub = _compute_endpoint_stub("ep-g", "http://g:9000", "mixtral")
        ut.register_endpoint.return_value = stub
        ext = {"utilization": ut}
        with mock.patch(_MODULE, return_value=ext):
            app = _make_app()
            client = TestClient(app)
            resp = client.post(
                "/admin/compute/endpoints",
                json={
                    "endpoint_id": "ep-g",
                    "url": "http://g:9000",
                    "model": "mixtral",
                    "gpu_type": "A100",
                    "gpu_count": 8,
                    "max_concurrent": 128,
                },
            )
        assert resp.status_code == 200
        call_kwargs = ut.register_endpoint.call_args.kwargs
        assert call_kwargs["gpu_type"] == "A100"
        assert call_kwargs["gpu_count"] == 8
        assert call_kwargs["max_concurrent"] == 128


# ---------------------------------------------------------------------------
# DELETE /admin/compute/endpoints/{endpoint_id}
# ---------------------------------------------------------------------------


class TestAdminUnregisterComputeEndpoint:
    def test_deletes_existing_returns_removed(self):
        ut = MagicMock()
        ext = {"utilization": ut}
        with mock.patch(_MODULE, return_value=ext):
            app = _make_app()
            client = TestClient(app)
            resp = client.delete("/admin/compute/endpoints/ep-rm")
        assert resp.status_code == 200
        assert resp.json() == {"removed": "ep-rm"}
        ut.unregister_endpoint.assert_called_once_with("ep-rm")

    def test_deleting_unknown_is_noop_and_still_returns_200(self):
        ut = MagicMock()
        ext = {"utilization": ut}
        with mock.patch(_MODULE, return_value=ext):
            app = _make_app()
            client = TestClient(app)
            resp = client.delete("/admin/compute/endpoints/nonexistent")
        assert resp.status_code == 200
        assert resp.json() == {"removed": "nonexistent"}

    def test_unregister_propagates_endpoint_id(self):
        ut = MagicMock()
        ext = {"utilization": ut}
        with mock.patch(_MODULE, return_value=ext):
            app = _make_app()
            client = TestClient(app)
            resp = client.delete("/admin/compute/endpoints/complex-id-123")
        assert resp.status_code == 200
        ut.unregister_endpoint.assert_called_once_with("complex-id-123")


# ---------------------------------------------------------------------------
# GET /admin/compute/gpu-metrics
# ---------------------------------------------------------------------------


class TestAdminComputeGpuMetrics:
    def test_no_metrics_returns_empty_dict(self):
        app = _make_app(daemon_state={})
        client = TestClient(app)
        resp = client.get("/admin/compute/gpu-metrics")
        assert resp.status_code == 200
        data = resp.json()
        assert data["metrics"] == {}
        assert data["collected_at"] is None

    def test_raw_none_returns_empty(self):
        app = _make_app(daemon_state={"_last_gpu_metrics": None})
        client = TestClient(app)
        resp = client.get("/admin/compute/gpu-metrics")
        assert resp.status_code == 200
        data = resp.json()
        assert data["metrics"] == {}

    def test_metrics_as_objects_with_as_dict(self):
        m1 = _FakeGpuMetric(gpu_sm_util_pct=85.0, gpu_mem_util_pct=60.0)
        m2 = _FakeGpuMetric(gpu_sm_util_pct=12.0, gpu_mem_util_pct=8.0)
        ut = MagicMock()
        ut.list_endpoints.return_value = [
            _compute_endpoint_stub("ep-a"),
            _compute_endpoint_stub("ep-b"),
        ]
        ext = {"utilization": ut}
        with mock.patch(_MODULE, return_value=ext):
            app = _make_app(
                daemon_state={"_last_gpu_metrics": [m1, m2], "_last_gpu_metrics_at": "2025-01-01T00:00:00Z"},
            )
            client = TestClient(app)
            resp = client.get("/admin/compute/gpu-metrics")
        assert resp.status_code == 200
        data = resp.json()
        assert data["collected_at"] == "2025-01-01T00:00:00Z"
        assert data["metrics"]["ep-a"]["gpu_sm_util_pct"] == 85.0
        assert data["metrics"]["ep-b"]["gpu_mem_util_pct"] == 8.0

    def test_metrics_as_raw_dicts(self):
        ut = MagicMock()
        ut.list_endpoints.return_value = [_compute_endpoint_stub("ep-dict")]
        ext = {"utilization": ut}
        with mock.patch(_MODULE, return_value=ext):
            app = _make_app(
                daemon_state={"_last_gpu_metrics": [{"gpu_sm_util_pct": 42.0, "gpu_mem_util_pct": 11.0}]},
            )
            client = TestClient(app)
            resp = client.get("/admin/compute/gpu-metrics")
        data = resp.json()
        assert data["metrics"]["ep-dict"]["gpu_sm_util_pct"] == 42.0

    def test_metrics_mixed_object_and_dict(self):
        m_obj = _FakeGpuMetric(gpu_sm_util_pct=50.0, gpu_mem_util_pct=30.0)
        m_dict = {"gpu_sm_util_pct": 20.0, "gpu_mem_util_pct": 10.0}
        ut = MagicMock()
        ut.list_endpoints.return_value = [
            _compute_endpoint_stub("ep-obj"),
            _compute_endpoint_stub("ep-dict"),
        ]
        ext = {"utilization": ut}
        with mock.patch(_MODULE, return_value=ext):
            app = _make_app(daemon_state={"_last_gpu_metrics": [m_obj, m_dict]})
            client = TestClient(app)
            resp = client.get("/admin/compute/gpu-metrics")
        data = resp.json()
        assert data["metrics"]["ep-obj"]["gpu_sm_util_pct"] == 50.0
        assert data["metrics"]["ep-dict"]["gpu_sm_util_pct"] == 20.0

    def test_more_metrics_than_endpoints_falls_back_to_device_names(self):
        ut = MagicMock()
        ut.list_endpoints.return_value = [_compute_endpoint_stub("only-one")]
        ext = {"utilization": ut}
        with mock.patch(_MODULE, return_value=ext):
            app = _make_app(
                daemon_state={
                    "_last_gpu_metrics": [
                        _FakeGpuMetric(gpu_sm_util_pct=1.0, gpu_mem_util_pct=2.0),
                        _FakeGpuMetric(gpu_sm_util_pct=3.0, gpu_mem_util_pct=4.0),
                        _FakeGpuMetric(gpu_sm_util_pct=5.0, gpu_mem_util_pct=6.0),
                    ],
                },
            )
            client = TestClient(app)
            resp = client.get("/admin/compute/gpu-metrics")
        data = resp.json()
        assert "only-one" in data["metrics"]
        assert "device_1" in data["metrics"]
        assert "device_2" in data["metrics"]

    def test_plain_object_without_as_dict_uses_getattr(self):
        class _BareMetric:
            gpu_sm_util_pct = 77.0
            gpu_mem_util_pct = 55.0
            gpu_temp_c = 42.0
            power_draw_w = 200.0
            memory_used_mb = 16000.0
            memory_total_mb = 24000.0

        ut = MagicMock()
        ut.list_endpoints.return_value = [_compute_endpoint_stub("ep-bare")]
        ext = {"utilization": ut}
        with mock.patch(_MODULE, return_value=ext):
            app = _make_app(daemon_state={"_last_gpu_metrics": [_BareMetric()]})
            client = TestClient(app)
            resp = client.get("/admin/compute/gpu-metrics")
        data = resp.json()
        m = data["metrics"]["ep-bare"]
        assert m["gpu_sm_util_pct"] == 77.0
        assert m["memory_used_mb"] == 16000.0


# ---------------------------------------------------------------------------
# GET /admin/compute/gpu-metrics/{endpoint_id}
# ---------------------------------------------------------------------------


class TestAdminComputeGpuMetricByEndpoint:
    def test_no_endpoint_match_returns_404(self):
        ut = MagicMock()
        ut.list_endpoints.return_value = []
        ext = {"utilization": ut}
        with mock.patch(_MODULE, return_value=ext):
            app = _make_app(daemon_state={"_last_gpu_metrics": []})
            client = TestClient(app)
            resp = client.get("/admin/compute/gpu-metrics/missing")
        assert resp.status_code == 404
        assert "missing" in resp.json()["detail"]

    def test_no_metrics_at_all_returns_404(self):
        ut = MagicMock()
        ut.list_endpoints.return_value = [_compute_endpoint_stub("ep-x")]
        ext = {"utilization": ut}
        with mock.patch(_MODULE, return_value=ext):
            app = _make_app(daemon_state={})
            client = TestClient(app)
            resp = client.get("/admin/compute/gpu-metrics/ep-x")
        assert resp.status_code == 404

    def test_raw_none_returns_404(self):
        ut = MagicMock()
        ut.list_endpoints.return_value = [_compute_endpoint_stub("ep-x")]
        ext = {"utilization": ut}
        with mock.patch(_MODULE, return_value=ext):
            app = _make_app(daemon_state={"_last_gpu_metrics": None})
            client = TestClient(app)
            resp = client.get("/admin/compute/gpu-metrics/ep-x")
        assert resp.status_code == 404

    def test_endpoint_index_out_of_range_returns_404(self):
        ut = MagicMock()
        ut.list_endpoints.return_value = [
            _compute_endpoint_stub("ep-0"),
            _compute_endpoint_stub("ep-1"),
        ]
        ext = {"utilization": ut}
        with mock.patch(_MODULE, return_value=ext):
            app = _make_app(
                daemon_state={"_last_gpu_metrics": [_FakeGpuMetric(gpu_sm_util_pct=0.0)]},
            )
            client = TestClient(app)
            resp = client.get("/admin/compute/gpu-metrics/ep-1")
        assert resp.status_code == 404

    def test_matches_endpoint_returns_metrics_object(self):
        m = _FakeGpuMetric(gpu_sm_util_pct=88.0, gpu_mem_util_pct=44.0)
        ut = MagicMock()
        ut.list_endpoints.return_value = [_compute_endpoint_stub("target-ep")]
        ext = {"utilization": ut}
        with mock.patch(_MODULE, return_value=ext):
            app = _make_app(daemon_state={"_last_gpu_metrics": [m]})
            client = TestClient(app)
            resp = client.get("/admin/compute/gpu-metrics/target-ep")
        assert resp.status_code == 200
        data = resp.json()
        assert data["endpoint_id"] == "target-ep"
        assert data["metrics"]["gpu_sm_util_pct"] == 88.0
        assert data["metrics"]["gpu_mem_util_pct"] == 44.0

    def test_matches_endpoint_with_dict_metrics(self):
        ut = MagicMock()
        ut.list_endpoints.return_value = [_compute_endpoint_stub("dict-ep")]
        ext = {"utilization": ut}
        with mock.patch(_MODULE, return_value=ext):
            app = _make_app(
                daemon_state={"_last_gpu_metrics": [{"gpu_sm_util_pct": 33.0}]},
            )
            client = TestClient(app)
            resp = client.get("/admin/compute/gpu-metrics/dict-ep")
        assert resp.status_code == 200
        assert resp.json()["metrics"]["gpu_sm_util_pct"] == 33.0

    def test_matches_with_bare_object_no_as_dict(self):
        class _Bare:
            gpu_sm_util_pct = 99.0
            gpu_mem_util_pct = 0.0
            gpu_temp_c = 0.0
            power_draw_w = 0.0
            memory_used_mb = 0.0
            memory_total_mb = 0.0

        ut = MagicMock()
        ut.list_endpoints.return_value = [_compute_endpoint_stub("bare-ep")]
        ext = {"utilization": ut}
        with mock.patch(_MODULE, return_value=ext):
            app = _make_app(daemon_state={"_last_gpu_metrics": [_Bare()]})
            client = TestClient(app)
            resp = client.get("/admin/compute/gpu-metrics/bare-ep")
        assert resp.status_code == 200
        assert resp.json()["metrics"]["gpu_sm_util_pct"] == 99.0
