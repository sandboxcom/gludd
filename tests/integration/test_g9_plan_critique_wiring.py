"""Integration tests for G9 PlanCritique daemon wiring.

Proves PlanCritique is wired to app.state.plan_critique, the admin endpoint
returns correct data, and the plan→critique→improve cycle works end-to-end.
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

from general_ludd.daemon import create_daemon_app
from general_ludd.planning.critique import PlanCritique


class TestPlanCritiqueDaemonWiring:
    def test_plan_critique_wired_to_app_state(self) -> None:
        with patch.dict(os.environ, {"GLUDD_ALLOW_NO_AUTH": "1"}):
            app = create_daemon_app(tick_interval=999.0)
        critique = getattr(app.state, "plan_critique", None)
        assert critique is not None
        assert isinstance(critique, PlanCritique)
        assert hasattr(critique, "critique_plan")

    def test_app_state_plan_critique_has_expected_methods(self) -> None:
        with patch.dict(os.environ, {"GLUDD_ALLOW_NO_AUTH": "1"}):
            app = create_daemon_app(tick_interval=999.0)
        critique = app.state.plan_critique
        assert hasattr(critique, "critique_plan")
        assert callable(critique.critique_plan)
        assert hasattr(critique, "KNOWN_TOOLS")

    @pytest.mark.asyncio
    async def test_critique_status_endpoint_returns_200(self) -> None:
        with patch.dict(os.environ, {"GLUDD_ALLOW_NO_AUTH": "1"}):
            app = create_daemon_app(tick_interval=999.0)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/admin/plan/critique-status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["wired"] is True
        assert data["class"] == "PlanCritique"

    @pytest.mark.asyncio
    async def test_critique_post_endpoint_finds_gaps(self) -> None:
        with patch.dict(os.environ, {"GLUDD_ALLOW_NO_AUTH": "1"}):
            app = create_daemon_app(tick_interval=999.0)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/admin/plan/critique",
                json={
                    "title": "",
                    "description": "",
                    "steps": [
                        {"name": "s1", "description": "do things", "tool": "bash"},
                    ],
                },
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert isinstance(data["findings"], list)
        assert data["finding_count"] > 0

    @pytest.mark.asyncio
    async def test_critique_post_endpoint_catches_missing_steps(self) -> None:
        with patch.dict(os.environ, {"GLUDD_ALLOW_NO_AUTH": "1"}):
            app = create_daemon_app(tick_interval=999.0)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/admin/plan/critique",
                json={"title": "Missing steps plan", "description": "No steps"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        findings = data["findings"]
        has_steps_error = any(
            f["field"] == "steps" and f["severity"] == "error" for f in findings
        )
        assert has_steps_error is True

    @pytest.mark.asyncio
    async def test_plan_critique_improve_cycle_with_mock_data(self) -> None:
        critique = PlanCritique()

        plan = {
            "title": "Add user authentication",
            "description": "Implement login, registration, and password reset",
            "steps": [
                {
                    "name": "create_user_model",
                    "description": "Create User SQLAlchemy model with hashed passwords",
                    "tool": "python",
                },
                {"name": "build_login_endpoint", "description": "POST /auth/login endpoint", "tool": "bash"},
                {"name": "build_register_endpoint", "description": "POST /auth/register endpoint", "tool": "bash"},
                {"name": "add_tests", "description": "Unit + integration tests for auth", "tool": "python"},
            ],
            "dependencies": {
                "build_login_endpoint": "create_user_model",
                "build_register_endpoint": "create_user_model",
            },
            "acceptance_criteria": [
                "Users can register with email and password",
                "Users can log in with valid credentials",
                "Invalid credentials return 401",
                "Password reset emails are sent",
            ],
        }

        findings = critique.critique_plan(plan)
        assert isinstance(findings, list)

        severity_counts: dict[str, int] = {}
        for f in findings:
            assert isinstance(f, dict)
            assert "severity" in f
            assert "field" in f
            assert "message" in f
            sev = f["severity"]
            severity_counts[sev] = severity_counts.get(sev, 0) + 1

        assert isinstance(severity_counts, dict)

        if findings:
            improved_plan = dict(plan)
            for f in findings:
                if f["severity"] == "error" and f["field"] == "description":
                    improved_plan["description"] = "Detailed auth system with login, registration, and password reset"
            improved_findings = critique.critique_plan(improved_plan)
            assert isinstance(improved_findings, list)
            original_error_count = sum(1 for f in findings if f["severity"] == "error")
            improved_error_count = sum(1 for f in improved_findings if f["severity"] == "error")
            assert improved_error_count <= original_error_count

    def test_critique_plan_handles_empty_dict(self) -> None:
        critique = PlanCritique()
        findings = critique.critique_plan({})
        assert isinstance(findings, list)
        has_title_error = any(
            f["field"] == "title" and f["severity"] == "error" for f in findings
        )
        assert has_title_error is True

    def test_critique_plan_handles_unknown_tool(self) -> None:
        critique = PlanCritique()
        plan = {
            "title": "Use obscure tool",
            "description": "Testing unknown tool detection",
            "steps": [
                {"name": "some_step", "description": "Use an unknown executor", "tool": "unknown_tool_v99"},
            ],
        }
        findings = critique.critique_plan(plan)
        has_tool_warning = any(
            f.get("field", "").endswith(".tool") and f.get("severity") == "warning"
            for f in findings
        )
        assert has_tool_warning is True

    def test_critique_plan_accepts_known_tools(self) -> None:
        critique = PlanCritique()
        for tool in ["bash", "python", "ansible", "git", "docker", "curl", "apt"]:
            plan = {
                "title": f"Use {tool}",
                "description": f"Testing known tool {tool}",
                "steps": [
                    {"name": "step1", "description": f"Run with {tool}", "tool": tool},
                ],
            }
            findings = critique.critique_plan(plan)
            has_tool_warning = any(
                f.get("field", "").endswith(".tool") and f.get("severity") == "warning"
                for f in findings
            )
            assert has_tool_warning is False, f"Tool '{tool}' should be known"
