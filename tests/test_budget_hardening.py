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


# ---------------------------------------------------------------------------
# /admin/models/call: system prompt + extra-field tolerance
# (the gludd_langchain_generate / gludd_langgraph_decision Ansible modules POST
# `system`, `response_format`, and `options` to this endpoint — assert the
# system prompt reaches the gateway and the extras never 422.)
# ---------------------------------------------------------------------------

class TestModelCallSystemPrompt:
    """The handler must forward an optional `system` field as a system message
    and tolerate the extra body keys the Ansible modules send."""

    def _client(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from general_ludd.models.gateway import ModelGateway
        from general_ludd.routers.models import register

        app = FastAPI()
        gw = MagicMock(spec=ModelGateway)
        profile = MagicMock()
        profile.model_profile_id = "default"
        gw.list_profiles.return_value = [profile]
        gw.call_model.return_value = MagicMock(content="ok", usage_metadata=None)
        app.state._model_gateway = gw
        app.state._budget_guard = None  # no budget configured -> proceed
        app.state._health_tracker = None
        app.state._project_manager = None
        app.state._metrics_collector = None
        app.state._session_factory = None
        app.state._model_registry = MagicMock()
        app.state._model_registry.search.return_value = []
        app.state._model_registry.list_downloaded.return_value = []
        register(app, {})
        return TestClient(app, raise_server_exceptions=False), gw

    @staticmethod
    def _messages_arg(gw):
        """Extract the `messages` list passed positionally to call_model."""
        gw.call_model.assert_called_once()
        args, kwargs = gw.call_model.call_args
        # call_model(used_profile_id, messages)
        if len(args) >= 2:
            return args[1]
        return kwargs.get("messages")

    def test_system_field_becomes_system_message(self):
        client, gw = self._client()
        resp = client.post(
            "/admin/models/call",
            json={"prompt": "hi there", "system": "You are terse."},
        )
        assert resp.status_code == 200
        messages = self._messages_arg(gw)
        assert messages[0]["role"] == "system"
        assert messages[0]["content"] == "You are terse."
        assert messages[1]["role"] == "user"
        assert messages[1]["content"] == "hi there"

    def test_no_system_field_is_single_user_message(self):
        """Backward compat: absent/empty system -> just the user message."""
        client, gw = self._client()
        resp = client.post("/admin/models/call", json={"prompt": "hi"})
        assert resp.status_code == 200
        messages = self._messages_arg(gw)
        assert len(messages) == 1
        assert messages[0]["role"] == "user"
        assert messages[0]["content"] == "hi"

    def test_empty_system_field_is_single_user_message(self):
        client, gw = self._client()
        resp = client.post("/admin/models/call", json={"prompt": "hi", "system": ""})
        assert resp.status_code == 200
        messages = self._messages_arg(gw)
        assert len(messages) == 1
        assert messages[0]["role"] == "user"

    def test_extra_fields_do_not_422(self):
        """The langgraph_decision module posts response_format/options/max_tokens
        on top of system — none of these unknown keys may trigger a 422."""
        client, gw = self._client()
        resp = client.post(
            "/admin/models/call",
            json={
                "prompt": "pick one",
                "system": "decide",
                "response_format": "json",
                "options": ["a", "b", "c"],
                "max_tokens": 256,
            },
        )
        assert resp.status_code == 200
        messages = self._messages_arg(gw)
        assert messages[0]["role"] == "system"
        # response_format=json appends a best-effort JSON nudge to the system msg
        assert "JSON" in messages[0]["content"]
        assert "decide" in messages[0]["content"]

    def test_response_schema_nudge_without_system(self):
        """response_schema with no explicit system still produces a system
        message carrying the JSON nudge + the schema."""
        client, gw = self._client()
        resp = client.post(
            "/admin/models/call",
            json={
                "prompt": "give me the package",
                "response_schema": {"name": "str", "version": "str"},
            },
        )
        assert resp.status_code == 200
        messages = self._messages_arg(gw)
        assert messages[0]["role"] == "system"
        assert "JSON" in messages[0]["content"]
        assert "version" in messages[0]["content"]


# ---------------------------------------------------------------------------
# Real-instance tests for budget_pre_check
# These use ACTUAL RunBudgetGuard and SpendLimiter instances — NOT MagicMock —
# so a signature mismatch (wrong kwarg, missing required arg, wrong method name)
# FAILS the test rather than silently passing.
# ---------------------------------------------------------------------------


class TestBudgetPreCheckRealInstances:
    """budget_pre_check() against real guard objects (no mocks)."""

    # ------------------------------------------------------------------
    # RunBudgetGuard — uses check_all_limits(estimated_cost=0.0)
    # ------------------------------------------------------------------

    def test_real_run_budget_guard_allowed_proceeds(self):
        """Real RunBudgetGuard with headroom → budget_pre_check returns None."""
        from general_ludd.budget_guard_check import budget_pre_check
        from general_ludd.controllers.budget import RunBudgetGuard

        guard = RunBudgetGuard(run_budget_usd=10.0)
        # No spend recorded → allowed
        result = budget_pre_check(guard)
        assert result is None, f"expected None (allowed), got {result!r}"

    def test_real_run_budget_guard_over_limit_denied(self):
        """Real RunBudgetGuard with spend > limit → budget_pre_check returns denial."""
        from general_ludd.budget_guard_check import budget_pre_check
        from general_ludd.controllers.budget import RunBudgetGuard

        guard = RunBudgetGuard(run_budget_usd=1.0)
        guard.record_spend(2.0)  # push over the $1 limit
        result = budget_pre_check(guard)
        assert result is not None, "expected denial string, got None"
        assert isinstance(result, str)
        # The real check_all_limits returns reason containing "run budget exceeded"
        assert "budget" in result.lower() or "exceeded" in result.lower(), (
            f"unexpected denial reason: {result!r}"
        )

    def test_real_run_budget_guard_none_proceeds(self):
        """None guard → budget_pre_check returns None (no budget configured)."""
        from general_ludd.budget_guard_check import budget_pre_check

        result = budget_pre_check(None)
        assert result is None

    # ------------------------------------------------------------------
    # SpendLimiter — uses would_exceed(projected_usd: float) -> bool
    # NOT try_charge (which is mutating and requires kind=... kwarg)
    # ------------------------------------------------------------------

    def test_real_spend_limiter_allowed_proceeds(self):
        """Real SpendLimiter with headroom → budget_pre_check returns None."""
        from general_ludd.budget_guard_check import budget_pre_check
        from general_ludd.controllers.spend_limiter import SpendLimiter

        # $10 limit, no spend → would_exceed(0.0) is False → allowed
        limiter = SpendLimiter(limit_usd=10.0, window_seconds=3600)
        result = budget_pre_check(limiter)
        assert result is None, f"expected None (allowed), got {result!r}"

    def test_real_spend_limiter_over_limit_denied(self):
        """Real SpendLimiter already over limit → budget_pre_check returns denial."""
        from general_ludd.budget_guard_check import budget_pre_check
        from general_ludd.controllers.spend_limiter import SpendLimiter

        # $1 limit, record $2 spend → would_exceed(0.0) is True (already over)
        limiter = SpendLimiter(limit_usd=1.0, window_seconds=3600)
        limiter.record(2.0, kind="token")
        result = budget_pre_check(limiter)
        assert result is not None, "expected denial string, got None"
        assert isinstance(result, str)
        assert "spend" in result.lower() or "limit" in result.lower() or "exceeded" in result.lower(), (
            f"unexpected denial reason: {result!r}"
        )

    def test_real_spend_limiter_wrong_kwarg_would_fail(self):
        """Demonstrate that calling try_charge(cost=0.0) on SpendLimiter fails.

        This is the bug that the old code had: it called try_charge with a
        positional ``cost=`` kwarg that doesn't match ``cost_usd``.  The real
        SpendLimiter.try_charge signature is:
            try_charge(self, cost_usd, *, kind, ...)
        so ``try_charge(cost=0.0)`` raises TypeError.  This test DOCUMENTS
        that the old call pattern is broken, so a regression back to it
        fails this test immediately.
        """
        from general_ludd.controllers.spend_limiter import SpendLimiter

        limiter = SpendLimiter(limit_usd=10.0, window_seconds=3600)
        import pytest

        # Wrong positional kwarg (old buggy pattern)
        with pytest.raises(TypeError):
            limiter.try_charge(cost=0.0)  # type: ignore[call-arg]

    def test_real_spend_limiter_try_charge_requires_kind_kwarg(self):
        """try_charge requires 'kind' keyword argument — positional cost_usd only."""
        from general_ludd.controllers.spend_limiter import SpendLimiter

        limiter = SpendLimiter(limit_usd=10.0, window_seconds=3600)
        import pytest

        # try_charge(cost_usd) alone is missing required kwarg 'kind'
        with pytest.raises(TypeError):
            limiter.try_charge(0.0)  # missing kind=

    # ------------------------------------------------------------------
    # Unknown guard interface — fail-closed
    # ------------------------------------------------------------------

    def test_unknown_interface_real_object_denied(self):
        """An object with no check_all_limits / would_exceed → denied."""
        from general_ludd.budget_guard_check import budget_pre_check

        class WeirdGuard:
            """Has neither check_all_limits nor would_exceed."""
            def some_other_method(self) -> bool:
                return True

        result = budget_pre_check(WeirdGuard())
        assert result is not None
        assert "unknown interface" in result

    # ------------------------------------------------------------------
    # reviewer._call_model with REAL RunBudgetGuard
    # ------------------------------------------------------------------

    def test_reviewer_real_run_budget_guard_allowed(self):
        """reviewer._call_model proceeds when real RunBudgetGuard has headroom."""
        from unittest.mock import MagicMock

        from general_ludd.controllers.budget import RunBudgetGuard
        from general_ludd.prompts.registry import PromptRegistry
        from general_ludd.review.reviewer import ReturnReviewer

        guard = RunBudgetGuard(run_budget_usd=10.0)
        gw = MagicMock()
        gw.call_model.return_value = MagicMock(content="ok")
        rv = ReturnReviewer(
            gateway=gw,
            prompt_registry=MagicMock(spec=PromptRegistry),
            budget_guard=guard,
        )
        content, err = rv._call_model("test prompt")
        assert content == "ok"
        assert err is None

    def test_reviewer_real_run_budget_guard_over_limit_denied(self):
        """reviewer._call_model denied when real RunBudgetGuard is exhausted."""
        from unittest.mock import MagicMock

        from general_ludd.controllers.budget import RunBudgetGuard
        from general_ludd.prompts.registry import PromptRegistry
        from general_ludd.review.reviewer import ReturnReviewer

        guard = RunBudgetGuard(run_budget_usd=1.0)
        guard.record_spend(5.0)  # way over the $1 limit
        gw = MagicMock()
        rv = ReturnReviewer(
            gateway=gw,
            prompt_registry=MagicMock(spec=PromptRegistry),
            budget_guard=guard,
        )
        content, err = rv._call_model("test prompt")
        assert content is None
        assert err is not None
        assert "Budget denied" in err
        gw.call_model.assert_not_called()

    # ------------------------------------------------------------------
    # invoke_model_for_generation with REAL SpendLimiter
    # ------------------------------------------------------------------

    def test_invoke_real_spend_limiter_allowed(self):
        """invoke_model_for_generation proceeds when real SpendLimiter has headroom."""
        from unittest.mock import MagicMock

        from general_ludd.controllers.spend_limiter import SpendLimiter
        from general_ludd.models.job_invocation import invoke_model_for_generation

        limiter = SpendLimiter(limit_usd=10.0, window_seconds=3600)
        gw = MagicMock()
        gw.call_model.return_value = MagicMock(content="generated")
        result = invoke_model_for_generation(
            gw,
            job_id="J-real-1",
            work_type="code",
            model_profile="default",
            prompt_text="write a test",
            skill_body=None,
            budget_guard=limiter,
        )
        assert result == "generated"

    def test_invoke_real_spend_limiter_over_limit_returns_none(self):
        """invoke_model_for_generation returns None when real SpendLimiter is exhausted."""
        from unittest.mock import MagicMock

        from general_ludd.controllers.spend_limiter import SpendLimiter
        from general_ludd.models.job_invocation import invoke_model_for_generation

        limiter = SpendLimiter(limit_usd=1.0, window_seconds=3600)
        limiter.record(5.0, kind="token")  # push well past $1 limit
        gw = MagicMock()
        result = invoke_model_for_generation(
            gw,
            job_id="J-real-2",
            work_type="code",
            model_profile="default",
            prompt_text="write a test",
            skill_body=None,
            budget_guard=limiter,
        )
        assert result is None
        gw.call_model.assert_not_called()
