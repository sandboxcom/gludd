"""Compaction eval wiring: daemon startup + admin endpoint for arena status.

Covers:
  - create_daemon_app initialises _compaction_compactor / _compaction_metrics to None
  - After app.state is populated, GET /admin/compaction/eval-status returns wired state
  - Unwired state returns wired=False and champion=None
  - A manually-set SelfImprovingCompactor on app.state is reflected in the endpoint
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from general_ludd.compaction.arena import SelfImprovingCompactor
from general_ludd.compaction.baselines import NoOpCompactor
from general_ludd.compaction.evaluate import CompactionMetrics


def _make_minimal_app():
    """Build a daemon app that does NOT run the full lifespan (DB etc.)."""
    with patch(
        "general_ludd.ansible.runner.AnsibleRunnerAdapter",
        return_value=MagicMock(),
    ):
        from general_ludd.daemon import create_daemon_app

        app = create_daemon_app(tick_interval=300.0)
    return app


class TestCompactionEvalWiring:
    """Unit tests for compaction eval wiring in daemon.py."""

    def test_create_daemon_app_initializes_compaction_state_none(self):
        """create_daemon_app sets _compaction_compactor and _compaction_metrics to None."""
        app = _make_minimal_app()
        assert app.state._compaction_compactor is None
        assert app.state._compaction_metrics is None

    def test_endpoint_returns_unwired_when_state_is_none(self):
        """When neither compactor nor metrics are set, the endpoint returns wired=False."""
        app = _make_minimal_app()
        client = TestClient(app)
        response = client.get("/admin/compaction/eval-status")
        assert response.status_code == 200
        data = response.json()
        assert data["wired"] is False
        assert data["champion"] is None
        assert data["metrics"] is None

    def test_endpoint_returns_wired_when_compactor_is_set(self):
        """With a compactor and metrics set on app.state, the endpoint reflects both."""
        app = _make_minimal_app()
        candidate = NoOpCompactor()
        compactor = SelfImprovingCompactor(
            candidates=[candidate],
            champion=candidate,
        )
        metrics = CompactionMetrics(
            compactor="noop",
            samples=0,
            mean_ratio=1.0,
            mean_fidelity=1.0,
            mean_tokens_saved=0.0,
            score=0.3,
        )
        app.state._compaction_compactor = compactor
        app.state._compaction_metrics = metrics

        client = TestClient(app)
        response = client.get("/admin/compaction/eval-status")
        assert response.status_code == 200
        data = response.json()
        assert data["wired"] is True
        assert data["champion"] == "noop"
        assert data["metrics"] is not None
        assert data["metrics"]["compactor"] == "noop"
        assert data["metrics"]["score"] == pytest.approx(0.3)

    def test_compactor_champion_reflected_in_endpoint(self):
        """The champion name in the response matches the compactor's champion."""
        app = _make_minimal_app()
        candidate = NoOpCompactor()
        compactor = SelfImprovingCompactor(
            candidates=[candidate],
            champion=candidate,
        )
        app.state._compaction_compactor = compactor
        app.state._compaction_metrics = CompactionMetrics(compactor="noop")

        client = TestClient(app)
        response = client.get("/admin/compaction/eval-status")
        assert response.status_code == 200
        assert response.json()["champion"] == "noop"

    def test_compaction_metrics_model_dump_structure(self):
        """CompactionMetrics.model_dump() returns expected keys in endpoint."""
        app = _make_minimal_app()
        metrics = CompactionMetrics(
            compactor="truncate",
            samples=42,
            mean_ratio=0.5,
            mean_fidelity=0.85,
            mean_tokens_saved=1500.0,
            score=0.745,
        )
        app.state._compaction_metrics = metrics

        client = TestClient(app)
        response = client.get("/admin/compaction/eval-status")
        data = response.json()
        assert data["metrics"]["compactor"] == "truncate"
        assert data["metrics"]["samples"] == 42
        assert data["metrics"]["mean_ratio"] == pytest.approx(0.5)
        assert data["metrics"]["mean_fidelity"] == pytest.approx(0.85)
        assert data["metrics"]["mean_tokens_saved"] == pytest.approx(1500.0)
        assert data["metrics"]["score"] == pytest.approx(0.745)

    def test_compactor_without_metrics_still_reports_wired(self):
        """Having a compactor without metrics still reports wired=True."""
        app = _make_minimal_app()
        app.state._compaction_compactor = SelfImprovingCompactor(
            candidates=[NoOpCompactor()],
        )
        app.state._compaction_metrics = None

        client = TestClient(app)
        response = client.get("/admin/compaction/eval-status")
        assert response.status_code == 200
        assert response.json()["wired"] is True

    def test_metrics_without_compactor_still_reports_unwired(self):
        """Metrics alone without a compactor still reports wired=False."""
        app = _make_minimal_app()
        app.state._compaction_compactor = None
        app.state._compaction_metrics = CompactionMetrics(compactor="truncate")

        client = TestClient(app)
        response = client.get("/admin/compaction/eval-status")
        assert response.status_code == 200
        assert response.json()["wired"] is False
