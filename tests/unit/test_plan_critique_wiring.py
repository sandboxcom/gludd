"""Unit tests for PlanCritique wiring into the daemon."""

from fastapi.testclient import TestClient

from general_ludd.daemon import create_daemon_app
from general_ludd.planning.critique import PlanCritique


class TestPlanCritiqueWiring:
    def test_plan_critique_wired_on_app_state(self):
        app = create_daemon_app()
        critique = app.state.plan_critique
        assert critique is not None
        assert isinstance(critique, PlanCritique)

    def test_plan_critique_endpoint_returns_wired_true(self):
        app = create_daemon_app()
        client = TestClient(app)
        response = client.get("/admin/plan/critique-status")
        assert response.status_code == 200
        data = response.json()
        assert data["wired"] is True
        assert data["class"] == "PlanCritique"
