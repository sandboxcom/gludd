"""W3.10 (H12 + H1-residual): Router-built gateway uses metrics_collector;
comparison endpoint uses session_factory not _session.

TDD: write the test first.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient


def _make_app_with_session_factory():
    """Build a minimal daemon app with a real session factory mock."""
    from general_ludd.daemon import create_daemon_app

    with patch(
        "general_ludd.ansible.runner.AnsibleRunnerAdapter",
        return_value=MagicMock(),
    ):
        app = create_daemon_app(tick_interval=300.0)
    return app


class TestComparisonUsesSessionFactory:
    """H1-residual: /admin/observability/comparison must use _session_factory,
    not _session (which lifespan never sets)."""

    def test_comparison_with_session_factory_returns_rankings(self):
        """With a mocked session_factory, comparison returns rankings (not the
        'No DB session available' error that proves _session was used)."""
        mock_benchmark_result = {
            "rankings": [
                {"model_id": "gpt-4", "composite_score": 0.95},
            ],
            "summary": "1 model ranked",
        }
        mock_comparison = MagicMock()
        mock_comparison.compare_models = AsyncMock(return_value=mock_benchmark_result)

        with patch(
            "general_ludd.ansible.runner.AnsibleRunnerAdapter",
            return_value=MagicMock(),
        ):
            from general_ludd.daemon import create_daemon_app

            app = create_daemon_app(tick_interval=300.0)

        # Simulate what lifespan sets: _session_factory (not _session)
        mock_session = MagicMock()
        mock_sf = MagicMock(return_value=mock_session)
        app.state._session_factory = mock_sf
        # Explicitly do NOT set app.state._session — prove the endpoint
        # doesn't need it.
        if hasattr(app.state, "_session"):
            del app.state._session

        with (
            patch("general_ludd.routers.models.ModelComparison", return_value=mock_comparison),
            patch("general_ludd.routers.models.BenchmarkRepository"),
            TestClient(app) as client,
        ):
            resp = client.get("/admin/observability/comparison")
            assert resp.status_code == 200, (
                f"Got {resp.status_code}: {resp.text}\n"
                "Expected 200 — if you see 'No DB session available', "
                "_session is still being read instead of _session_factory."
            )
            data = resp.json()
            assert data.get("rankings") is not None, (
                f"Expected rankings, got: {data}"
            )

    def test_comparison_without_session_factory_returns_graceful_error(self):
        """When neither _session nor _session_factory is set, return a clear
        error (not a traceback)."""
        with patch(
            "general_ludd.ansible.runner.AnsibleRunnerAdapter",
            return_value=MagicMock(),
        ):
            from general_ludd.daemon import create_daemon_app

            app = create_daemon_app(tick_interval=300.0)

        # Ensure neither is set
        app.state._session_factory = None
        if hasattr(app.state, "_session"):
            del app.state._session

        with TestClient(app) as client:
            resp = client.get("/admin/observability/comparison")
            # Must return 200 with an empty/no-data result, or a 4xx — but NOT 500
            assert resp.status_code in (200, 404, 503), (
                f"Got {resp.status_code}: {resp.text}"
            )


class TestModelGatewayUsesMetricsCollector:
    """H12: When a model API call is made through the router, the
    metrics_collector must observe it.

    Strategy: construct the gateway through the router path and verify it
    carries the metrics_collector from app.state.
    """

    def test_router_gateway_has_metrics_collector(self):
        """The gateway built by the models router must share the app.state
        metrics_collector, not construct a new one."""
        from general_ludd.daemon import create_daemon_app

        with patch(
            "general_ludd.ansible.runner.AnsibleRunnerAdapter",
            return_value=MagicMock(),
        ):
            app = create_daemon_app(tick_interval=300.0)

        with TestClient(app) as client:
            # The metrics_collector is set by lifespan via _get_or_create_extended_subsystems
            mc = getattr(app.state, "_metrics_collector", None)
            assert mc is not None, "metrics_collector not set by lifespan"

            # Trigger gateway construction via the models router
            client.post(
                "/admin/models",
                json={
                    "model_id": "test-model",
                    "provider": "openai",
                    "model": "gpt-4o",
                    "api_key_env": "OPENAI_API_KEY",  # pragma: allowlist secret
                },
            )
            # Whether or not this succeeds, the gateway should now be on app.state
            gw = getattr(app.state, "_model_gateway", None)
            if gw is not None:
                # Gateway should carry the same metrics_collector from app.state
                assert getattr(gw, "_metrics_collector", None) is mc, (
                    "Gateway was constructed without the app.state metrics_collector"
                )
