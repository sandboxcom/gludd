"""Tests for budget-gating hardening (default-DENY) across engine, reviewer, job_invocation, and B5 router."""
from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _exhausted_guard(reason: str = "daily limit reached") -> MagicMock:
    g = MagicMock()
    g.check_all_limits.return_value = {"allowed": False, "reason": reason}
    return g


def _headroom_guard() -> MagicMock:
    g = MagicMock()
    g.check_all_limits.return_value = {"allowed": True, "reason": "ok"}
    return g


def _nondict_guard() -> MagicMock:
    g = MagicMock()
    g.check_all_limits.return_value = "not-a-dict"
    return g


def _missing_allowed_guard() -> MagicMock:
    """Dict returned but 'allowed' key absent — should default-DENY."""
    g = MagicMock()
    g.check_all_limits.return_value = {"reason": "no allowed key"}
    return g


def _raising_guard() -> MagicMock:
    g = MagicMock()
    g.check_all_limits.side_effect = RuntimeError("boom")
    return g


def _unknown_interface_guard() -> MagicMock:
    """Has neither check_all_limits nor try_charge."""
    g = MagicMock(spec=[])  # no attributes
    return g


def _try_charge_allowed_guard() -> MagicMock:
    g = MagicMock(spec=["try_charge"])
    g.try_charge.return_value = {"allowed": True}
    return g


def _try_charge_denied_guard() -> MagicMock:
    g = MagicMock(spec=["try_charge"])
    g.try_charge.return_value = {"allowed": False, "reason": "charge denied"}
    return g


# ---------------------------------------------------------------------------
# engine._budget_pre_check
# ---------------------------------------------------------------------------

class TestBudgetPreCheck:
    def _engine(self, guard=None):
        from general_ludd.execution.engine import ExecutionEngine
        return ExecutionEngine(workspace_path="/tmp/test-engine-budget", budget_guard=guard)

    def test_none_guard_is_allowed(self):
        eng = self._engine(guard=None)
        assert eng._budget_pre_check(None) is None

    def test_exhausted_check_all_limits_returns_denial(self):
        eng = self._engine()
        result = eng._budget_pre_check(_exhausted_guard("limit hit"))
        assert result == "limit hit"

    def test_missing_allowed_key_default_deny(self):
        eng = self._engine()
        result = eng._budget_pre_check(_missing_allowed_guard())
        assert result is not None
        assert isinstance(result, str)

    def test_nondict_return_is_denial(self):
        eng = self._engine()
        result = eng._budget_pre_check(_nondict_guard())
        assert result == "budget check returned non-dict"

    def test_unknown_interface_is_denial(self):
        eng = self._engine()
        result = eng._budget_pre_check(_unknown_interface_guard())
        assert result == "budget guard has unknown interface"

    def test_raising_guard_returns_denial_string(self):
        eng = self._engine()
        result = eng._budget_pre_check(_raising_guard())
        assert result is not None
        assert "boom" in result

    def test_try_charge_allowed(self):
        eng = self._engine()
        result = eng._budget_pre_check(_try_charge_allowed_guard())
        assert result is None

    def test_try_charge_denied(self):
        eng = self._engine()
        result = eng._budget_pre_check(_try_charge_denied_guard())
        assert result == "charge denied"


# ---------------------------------------------------------------------------
# engine.execute() integration
# ---------------------------------------------------------------------------

class TestEngineExecuteBudgetGate:
    def _job(self):
        from general_ludd.schemas.job import JobSpec
        return JobSpec(
            job_id="JOB-001", todo_id="TODO-001",
            playbook="code", queue="core",
            prompt_text="write hello world", work_type="code",
        )

    def test_execute_denied_when_exhausted(self, tmp_path):
        from general_ludd.execution.engine import ExecutionEngine
        eng = ExecutionEngine(
            model_gateway=MagicMock(),
            workspace_path=str(tmp_path),
            budget_guard=_exhausted_guard("run limit"),
        )
        result = eng.execute(self._job())
        assert result.exit_code == 1
        assert "Budget" in result.result_summary
        assert "run limit" in result.result_summary

    def test_execute_proceeds_with_headroom(self, tmp_path):
        from general_ludd.execution.engine import ExecutionEngine
        gw = MagicMock()
        resp = MagicMock()
        resp.content = ""
        gw.call_model.return_value = resp
        eng = ExecutionEngine(
            model_gateway=gw,
            workspace_path=str(tmp_path),
            budget_guard=_headroom_guard(),
        )
        eng.execute(self._job())
        gw.call_model.assert_called_once()

    def test_execute_proceeds_no_guard(self, tmp_path):
        from general_ludd.execution.engine import ExecutionEngine
        gw = MagicMock()
        resp = MagicMock()
        resp.content = ""
        gw.call_model.return_value = resp
        eng = ExecutionEngine(
            model_gateway=gw,
            workspace_path=str(tmp_path),
            budget_guard=None,
        )
        eng.execute(self._job())
        gw.call_model.assert_called_once()


# ---------------------------------------------------------------------------
# reviewer._call_model budget gate
# ---------------------------------------------------------------------------

class TestReviewerCallModelBudgetGate:
    def _reviewer(self, guard=None):
        from general_ludd.prompts.registry import PromptRegistry
        from general_ludd.review.reviewer import ReturnReviewer
        gw = MagicMock()
        pr = MagicMock(spec=PromptRegistry)
        return ReturnReviewer(gateway=gw, prompt_registry=pr, budget_guard=guard)

    def test_exhausted_guard_returns_none_and_denial(self):
        rv = self._reviewer(guard=_exhausted_guard("reviewer daily cap"))
        content, err = rv._call_model("some prompt")
        assert content is None
        assert err is not None
        assert "Budget denied" in err

    def test_no_guard_proceeds(self):
        rv = self._reviewer(guard=None)
        rv._gateway.call_model.return_value = MagicMock(content="ok")
        content, err = rv._call_model("some prompt")
        assert content == "ok"
        assert err is None

    def test_headroom_guard_proceeds(self):
        rv = self._reviewer(guard=_headroom_guard())
        rv._gateway.call_model.return_value = MagicMock(content="ok")
        content, _err = rv._call_model("some prompt")
        assert content == "ok"

    def test_missing_allowed_key_denied(self):
        rv = self._reviewer(guard=_missing_allowed_guard())
        content, err = rv._call_model("some prompt")
        assert content is None
        assert err is not None

    def test_nondict_guard_denied(self):
        rv = self._reviewer(guard=_nondict_guard())
        content, err = rv._call_model("some prompt")
        assert content is None
        assert err is not None

    def test_raising_guard_denied(self):
        rv = self._reviewer(guard=_raising_guard())
        content, err = rv._call_model("some prompt")
        assert content is None
        assert "boom" in (err or "")

    def test_try_charge_only_denied_fail_closed(self):
        """try_charge-ONLY guard that refuses must block the model call."""
        rv = self._reviewer(guard=_try_charge_denied_guard())
        # Record whether call_model was ever invoked
        content, err = rv._call_model("some prompt")
        assert content is None, "must not return content when try_charge denies"
        assert err is not None
        assert "Budget denied" in err
        rv._gateway.call_model.assert_not_called()

    def test_try_charge_only_allowed_proceeds(self):
        """try_charge-ONLY guard with headroom must allow the model call."""
        rv = self._reviewer(guard=_try_charge_allowed_guard())
        rv._gateway.call_model.return_value = MagicMock(content="ok")
        content, err = rv._call_model("some prompt")
        assert content == "ok"
        assert err is None
        rv._gateway.call_model.assert_called_once()


# ---------------------------------------------------------------------------
# invoke_model_for_generation budget gate
# ---------------------------------------------------------------------------

class TestInvokeModelForGenerationBudgetGate:
    def _gw(self):
        gw = MagicMock()
        gw.call_model.return_value = MagicMock(content="generated text")
        return gw

    def test_exhausted_guard_returns_none(self):
        from general_ludd.models.job_invocation import invoke_model_for_generation
        result = invoke_model_for_generation(
            self._gw(),
            job_id="J1", work_type="code",
            model_profile="default", prompt_text="do stuff",
            skill_body=None, budget_guard=_exhausted_guard(),
        )
        assert result is None

    def test_no_guard_proceeds(self):
        from general_ludd.models.job_invocation import invoke_model_for_generation
        gw = self._gw()
        result = invoke_model_for_generation(
            gw,
            job_id="J1", work_type="code",
            model_profile="default", prompt_text="do stuff",
            skill_body=None, budget_guard=None,
        )
        assert result == "generated text"

    def test_headroom_guard_proceeds(self):
        from general_ludd.models.job_invocation import invoke_model_for_generation
        gw = self._gw()
        result = invoke_model_for_generation(
            gw,
            job_id="J1", work_type="code",
            model_profile="default", prompt_text="do stuff",
            skill_body=None, budget_guard=_headroom_guard(),
        )
        assert result == "generated text"

    def test_missing_allowed_key_returns_none(self):
        from general_ludd.models.job_invocation import invoke_model_for_generation
        result = invoke_model_for_generation(
            self._gw(),
            job_id="J1", work_type="code",
            model_profile="default", prompt_text="do stuff",
            skill_body=None, budget_guard=_missing_allowed_guard(),
        )
        assert result is None

    def test_nondict_returns_none(self):
        from general_ludd.models.job_invocation import invoke_model_for_generation
        result = invoke_model_for_generation(
            self._gw(),
            job_id="J1", work_type="code",
            model_profile="default", prompt_text="do stuff",
            skill_body=None, budget_guard=_nondict_guard(),
        )
        assert result is None

    def test_try_charge_only_denied_fail_closed(self):
        """try_charge-ONLY guard that refuses must block the model call (fail-closed)."""
        from general_ludd.models.job_invocation import invoke_model_for_generation
        gw = self._gw()
        result = invoke_model_for_generation(
            gw,
            job_id="J2", work_type="code",
            model_profile="default", prompt_text="do stuff",
            skill_body=None, budget_guard=_try_charge_denied_guard(),
        )
        assert result is None, "must return None when try_charge denies"
        gw.call_model.assert_not_called()

    def test_try_charge_only_allowed_proceeds(self):
        """try_charge-ONLY guard with headroom must allow the model call."""
        from general_ludd.models.job_invocation import invoke_model_for_generation
        gw = self._gw()
        result = invoke_model_for_generation(
            gw,
            job_id="J3", work_type="code",
            model_profile="default", prompt_text="do stuff",
            skill_body=None, budget_guard=_try_charge_allowed_guard(),
        )
        assert result == "generated text"
        gw.call_model.assert_called_once()


# ---------------------------------------------------------------------------
# routers/models.py B5 budget gate (/admin/models/call)
# ---------------------------------------------------------------------------

class TestB5ModelCallBudgetGate:
    """Tests for the /admin/models/call budget gate using FastAPI TestClient."""

    def _app_with_guard(self, guard, *, set_attr: bool = True):
        """Build a minimal FastAPI app with _budget_guard set on state."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from general_ludd.routers.models import register

        app = FastAPI()
        daemon_state: dict = {}
        if set_attr:
            app.state._budget_guard = guard
        # Minimal state so register() doesn't crash
        app.state._model_gateway = None
        app.state._health_tracker = None
        app.state._project_manager = None
        app.state._metrics_collector = None
        app.state._session_factory = None
        app.state._model_registry = MagicMock()
        app.state._model_registry.search.return_value = []
        app.state._model_registry.list_downloaded.return_value = []
        register(app, daemon_state)
        return TestClient(app)

    def _client_with_profiles(self, guard, *, set_attr: bool = True):
        """Build client with a real gateway that has a profile configured."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from general_ludd.models.gateway import ModelGateway
        from general_ludd.routers.models import register

        app = FastAPI()
        daemon_state: dict = {}
        if set_attr:
            app.state._budget_guard = guard

        # Minimal gateway with one profile
        gw = MagicMock(spec=ModelGateway)
        profile = MagicMock()
        profile.model_profile_id = "default"
        gw.list_profiles.return_value = [profile]
        gw.call_model.return_value = MagicMock(content="hi", usage_metadata=None)
        app.state._model_gateway = gw
        app.state._health_tracker = None
        app.state._project_manager = None
        app.state._metrics_collector = None
        app.state._session_factory = None
        app.state._model_registry = MagicMock()
        app.state._model_registry.search.return_value = []
        app.state._model_registry.list_downloaded.return_value = []
        register(app, daemon_state)
        return TestClient(app, raise_server_exceptions=False)

    def test_exhausted_guard_returns_429(self):
        client = self._client_with_profiles(_exhausted_guard("daily cap"))
        resp = client.post("/admin/models/call", json={"prompt": "hello"})
        assert resp.status_code == 429
        assert "budget exhausted" in resp.json()["detail"]

    def test_budget_unset_fail_closed_degraded_returns_503(self):
        client = self._client_with_profiles(None, set_attr=False)
        with patch.dict(os.environ, {"GLUDD_BUDGET_FAIL_CLOSED_DEGRADED": "1"}):
            resp = client.post("/admin/models/call", json={"prompt": "hello"})
        assert resp.status_code == 503
        assert "degraded" in resp.json()["detail"]

    def test_budget_unset_no_env_var_proceeds(self):
        client = self._client_with_profiles(None, set_attr=False)
        env = {k: v for k, v in os.environ.items() if k != "GLUDD_BUDGET_FAIL_CLOSED_DEGRADED"}
        with patch.dict(os.environ, env, clear=True):
            resp = client.post("/admin/models/call", json={"prompt": "hello"})
        # Should succeed (200) or fail for non-budget reason (e.g. model call)
        assert resp.status_code not in (429, 503)

    def test_headroom_guard_proceeds(self):
        client = self._client_with_profiles(_headroom_guard())
        resp = client.post("/admin/models/call", json={"prompt": "hello"})
        assert resp.status_code == 200

    def test_none_guard_set_proceeds(self):
        """guard=None explicitly set (no budget configured) — should proceed."""
        client = self._client_with_profiles(None, set_attr=True)
        resp = client.post("/admin/models/call", json={"prompt": "hello"})
        assert resp.status_code == 200

    def test_missing_allowed_key_returns_429(self):
        client = self._client_with_profiles(_missing_allowed_guard())
        resp = client.post("/admin/models/call", json={"prompt": "hello"})
        assert resp.status_code == 429
