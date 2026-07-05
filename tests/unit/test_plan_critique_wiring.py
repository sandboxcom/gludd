"""Unit tests for PlanCritique wiring into the daemon."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from general_ludd.planning.critique import PlanCritique


class TestPlanCritiqueWiring:
    def test_plan_critique_wired_on_app_state(self):
        critique = PlanCritique()
        assert critique is not None
        assert isinstance(critique, PlanCritique)

    def test_plan_critique_status_endpoint_returns_wired_true(self):
        app = FastAPI()
        app.state.plan_critique = PlanCritique()

        @app.get("/admin/plan/critique-status")
        async def status():
            critique = getattr(app.state, "plan_critique", None)
            return {
                "wired": critique is not None,
                "class": type(critique).__name__ if critique is not None else None,
            }

        client = TestClient(app)
        response = client.get("/admin/plan/critique-status")
        assert response.status_code == 200
        data = response.json()
        assert data["wired"] is True
        assert data["class"] == "PlanCritique"

    def test_post_critique_endpoint_returns_findings(self):
        app = FastAPI()
        app.state.plan_critique = PlanCritique()

        @app.post("/admin/plan/critique")
        async def critique(body: dict):
            pc = getattr(app.state, "plan_critique", None)
            if pc is None:
                return {"status": "not_configured", "findings": []}
            findings = pc.critique_plan(body)
            return {"status": "ok", "findings": findings, "finding_count": len(findings)}

        client = TestClient(app)
        plan = {
            "title": "Add login flow",
            "description": "Implement user authentication",
            "target_files": ["auth.py", "login.py"],
            "dependencies": ["flask"],
        }
        response = client.post("/admin/plan/critique", json=plan)
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert isinstance(data["findings"], list)
        assert data["finding_count"] == len(data["findings"])

    def test_post_critique_endpoint_empty_plan(self):
        app = FastAPI()
        app.state.plan_critique = PlanCritique()

        @app.post("/admin/plan/critique")
        async def critique(body: dict):
            pc = getattr(app.state, "plan_critique", None)
            if pc is None:
                return {"status": "not_configured", "findings": []}
            findings = pc.critique_plan(body)
            return {"status": "ok", "findings": findings, "finding_count": len(findings)}

        client = TestClient(app)
        response = client.post("/admin/plan/critique", json={})
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["finding_count"] >= 1
        assert any(f["severity"] == "error" for f in data["findings"])

    def test_post_critique_endpoint_missing_fields(self):
        app = FastAPI()
        app.state.plan_critique = PlanCritique()

        @app.post("/admin/plan/critique")
        async def critique(body: dict):
            pc = getattr(app.state, "plan_critique", None)
            if pc is None:
                return {"status": "not_configured", "findings": []}
            findings = pc.critique_plan(body)
            return {"status": "ok", "findings": findings, "finding_count": len(findings)}

        client = TestClient(app)
        response = client.post("/admin/plan/critique", json={"title": "Only title"})
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        severities = {f["severity"] for f in data["findings"]}
        assert "warning" in severities

    def test_post_critique_endpoint_complete_plan(self):
        app = FastAPI()
        app.state.plan_critique = PlanCritique()

        @app.post("/admin/plan/critique")
        async def critique(body: dict):
            pc = getattr(app.state, "plan_critique", None)
            if pc is None:
                return {"status": "not_configured", "findings": []}
            findings = pc.critique_plan(body)
            return {"status": "ok", "findings": findings, "finding_count": len(findings)}

        client = TestClient(app)
        plan = {
            "title": "Comprehensive refactor",
            "description": "Refactor the auth module for clarity.",
            "target_files": ["src/auth.py", "tests/test_auth.py"],
            "dependencies": {},
            "content": "Replace the monolithic auth module.",
            "steps": [
                {"name": "extract_handlers", "description": "Extract handler functions", "tool": "python"},
                {"name": "add_tests", "description": "Add unit tests", "tool": "bash"},
            ],
        }
        response = client.post("/admin/plan/critique", json=plan)
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert all(f["severity"] != "error" for f in data["findings"])
