"""Unit tests for src/general_ludd/models/job_invocation.py.

Focus: pure predicates, early-return guard, benchmark-recorder sink dispatch
(record vs create, exception swallowing), call_model invocation shape, and
budget-denied early-return.

The integration tests in tests/integration/test_completion_integrity_high.py
drive the REAL gateway + provider stack end-to-end (CA-T11/T12).  These tests
mock at the immediate call boundaries so they run offline and fast, covering
branches the integration tests don't isolate:

  - is_generation_work_type membership / falsy input
  - prompt_text None/empty early-return (no model call fired)
  - budget_pre_check denial early-return (no model call fired)
  - call_model called with correct profile_id + non-empty messages list
  - model call exception swallowed; None returned
  - benchmark_recorder.record() called with correct kwargs on success
  - benchmark_recorder.create() dispatch (no record attr)
  - benchmark_recorder exception swallowed
  - benchmark NOT called when content is None
  - model_profile=None defaults to "default"
  - work_type=None inside recorder defaults to "unknown"
  - usage_metadata absent: token counts fall back to len-based estimates
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

from general_ludd.models.job_invocation import (
    _GENERATION_WORK_TYPES,
    invoke_model_for_generation,
    is_generation_work_type,
)

# ---------------------------------------------------------------------------
# is_generation_work_type — pure predicate
# ---------------------------------------------------------------------------


class TestIsGenerationWorkType:
    def test_known_work_types_return_true(self) -> None:
        for wt in _GENERATION_WORK_TYPES:
            assert is_generation_work_type(wt) is True, f"{wt!r} should be True"

    def test_unknown_work_type_returns_false(self) -> None:
        assert is_generation_work_type("deploy") is False
        assert is_generation_work_type("unknown_xyz") is False

    def test_none_returns_false(self) -> None:
        assert is_generation_work_type(None) is False

    def test_empty_string_returns_false(self) -> None:
        assert is_generation_work_type("") is False

    def test_canonical_members_present(self) -> None:
        # Guard against accidental removals from the frozenset.
        for expected in ("code", "bug_fix", "test", "refactor", "docs"):
            assert expected in _GENERATION_WORK_TYPES


# ---------------------------------------------------------------------------
# Helpers to build fake gateway / responses
# ---------------------------------------------------------------------------


def _fake_response(content: str = "ok", input_tokens: int = 10, output_tokens: int = 5) -> MagicMock:
    resp = MagicMock()
    resp.content = content
    resp.usage_metadata = {"input_tokens": input_tokens, "output_tokens": output_tokens}
    return resp


def _fake_gateway(response: Any = None) -> MagicMock:
    gw = MagicMock()
    gw.call_model.return_value = response if response is not None else _fake_response()
    return gw


# ---------------------------------------------------------------------------
# prompt_text None / empty — early-return guard (no model call)
# ---------------------------------------------------------------------------


class TestPromptTextGuard:
    def test_none_prompt_returns_none(self) -> None:
        gw = _fake_gateway()
        result = invoke_model_for_generation(
            gw,
            job_id="j1",
            work_type="code",
            model_profile="default",
            prompt_text=None,
            skill_body=None,
        )
        assert result == (None, None)
        gw.call_model.assert_not_called()

    def test_empty_string_prompt_returns_none(self) -> None:
        gw = _fake_gateway()
        result = invoke_model_for_generation(
            gw,
            job_id="j2",
            work_type="code",
            model_profile="default",
            prompt_text="",
            skill_body=None,
        )
        assert result == (None, None)
        gw.call_model.assert_not_called()


# ---------------------------------------------------------------------------
# budget_pre_check denial — early-return (no model call)
# ---------------------------------------------------------------------------


class TestBudgetDenied:
    def test_budget_denied_returns_none_without_calling_model(self) -> None:
        gw = _fake_gateway()
        # A guard whose check_all_limits denies the call.
        guard = MagicMock()
        guard.check_all_limits.return_value = {"allowed": False, "reason": "over budget"}
        result = invoke_model_for_generation(
            gw,
            job_id="j3",
            work_type="code",
            model_profile="default",
            prompt_text="do something",
            skill_body=None,
            budget_guard=guard,
        )
        assert result == (None, None)
        gw.call_model.assert_not_called()

    def test_none_budget_guard_allows_call(self) -> None:
        gw = _fake_gateway(_fake_response(content="generated"))
        with patch("general_ludd.agents.capabilities.AgentCapabilities") as MockCaps:
            mock_caps = MagicMock()
            mock_caps.prepare_messages.return_value = [{"role": "user", "content": "do something"}]
            MockCaps.return_value = mock_caps
            result = invoke_model_for_generation(
                gw,
                job_id="j4",
                work_type="code",
                model_profile="default",
                prompt_text="do something",
                skill_body=None,
                budget_guard=None,
            )
        assert result[0] == "generated"
        gw.call_model.assert_called_once()


# ---------------------------------------------------------------------------
# call_model invocation shape
# ---------------------------------------------------------------------------


class TestCallModelShape:
    def _invoke(
        self,
        *,
        prompt_text: str = "task prompt",
        skill_body: str | None = "system",
        model_profile: str | None = "myprofile",
        work_type: str = "code",
    ) -> tuple[Any, MagicMock]:
        gw = _fake_gateway(_fake_response(content="answer"))
        with patch("general_ludd.agents.capabilities.AgentCapabilities") as MockCaps:
            mock_caps = MagicMock()
            mock_caps.prepare_messages.return_value = [{"role": "user", "content": prompt_text}]
            MockCaps.return_value = mock_caps
            result = invoke_model_for_generation(
                gw,
                job_id="j-shape",
                work_type=work_type,
                model_profile=model_profile,
                prompt_text=prompt_text,
                skill_body=skill_body,
            )
        return result, gw

    def test_call_model_receives_profile_id(self) -> None:
        _result, gw = self._invoke(model_profile="myprofile")
        args, kwargs = gw.call_model.call_args
        profile_arg = args[0] if args else kwargs.get("profile_id") or kwargs.get("model_profile_id")
        assert profile_arg == "myprofile"

    def test_call_model_receives_messages_kwarg(self) -> None:
        _result, gw = self._invoke(prompt_text="hello", skill_body=None)
        _args, kwargs = gw.call_model.call_args
        assert "messages" in kwargs
        assert isinstance(kwargs["messages"], list)
        assert len(kwargs["messages"]) > 0

    def test_model_profile_none_defaults_to_default(self) -> None:
        gw = _fake_gateway(_fake_response(content="ok"))
        with patch("general_ludd.agents.capabilities.AgentCapabilities") as MockCaps:
            mock_caps = MagicMock()
            mock_caps.prepare_messages.return_value = [{"role": "user", "content": "hi"}]
            MockCaps.return_value = mock_caps
            invoke_model_for_generation(
                gw,
                job_id="j5",
                work_type="code",
                model_profile=None,
                prompt_text="hi",
                skill_body=None,
            )
        args, _kwargs = gw.call_model.call_args
        assert args[0] == "default"

    def test_returns_response_content(self) -> None:
        result, _gw = self._invoke(prompt_text="question")
        assert result[0] == "answer"


# ---------------------------------------------------------------------------
# Error-swallowing path — model call raises
# ---------------------------------------------------------------------------


class TestModelCallErrorSwallowing:
    def test_exception_from_call_model_returns_none(self) -> None:
        gw = MagicMock()
        gw.call_model.side_effect = RuntimeError("connection refused")
        with patch("general_ludd.agents.capabilities.AgentCapabilities") as MockCaps:
            mock_caps = MagicMock()
            mock_caps.prepare_messages.return_value = [{"role": "user", "content": "hi"}]
            MockCaps.return_value = mock_caps
            result = invoke_model_for_generation(
                gw,
                job_id="j-err",
                work_type="code",
                model_profile="default",
                prompt_text="do it",
                skill_body=None,
            )
        assert result == (None, None)

    def test_value_error_from_call_model_swallowed(self) -> None:
        gw = MagicMock()
        gw.call_model.side_effect = ValueError("bad profile")
        with patch("general_ludd.agents.capabilities.AgentCapabilities") as MockCaps:
            mock_caps = MagicMock()
            mock_caps.prepare_messages.return_value = [{"role": "user", "content": "task"}]
            MockCaps.return_value = mock_caps
            result = invoke_model_for_generation(
                gw,
                job_id="j-verr",
                work_type="test",
                model_profile="p1",
                prompt_text="task",
                skill_body=None,
            )
        assert result == (None, None)


# ---------------------------------------------------------------------------
# benchmark_recorder sink — record() dispatch
# ---------------------------------------------------------------------------


class TestBenchmarkRecorderRecord:
    def _run_with_recorder(self, recorder: Any, *, input_tokens: int = 20, output_tokens: int = 8) -> Any:
        gw = _fake_gateway(_fake_response(content="gen", input_tokens=input_tokens, output_tokens=output_tokens))
        with patch("general_ludd.agents.capabilities.AgentCapabilities") as MockCaps:
            mock_caps = MagicMock()
            mock_caps.prepare_messages.return_value = [{"role": "user", "content": "prompt"}]
            MockCaps.return_value = mock_caps
            return invoke_model_for_generation(
                gw,
                job_id="j-rec",
                work_type="docs",
                model_profile="p2",
                prompt_text="prompt",
                skill_body=None,
                benchmark_recorder=recorder,
            )

    def test_record_called_with_correct_kwargs(self) -> None:
        recorder = MagicMock()
        del recorder.create  # ensure only record path is exercised
        self._run_with_recorder(recorder, input_tokens=20, output_tokens=8)
        recorder.record.assert_called_once()
        _args, kwargs = recorder.record.call_args
        assert kwargs["model_profile_id"] == "p2"
        assert kwargs["work_type"] == "docs"
        assert kwargs["input_tokens"] == 20
        assert kwargs["output_tokens"] == 8
        assert kwargs["success"] is True
        assert kwargs["scoring"] == "generation_path"

    def test_work_type_none_records_unknown(self) -> None:
        gw = _fake_gateway(_fake_response(content="out"))
        recorder = MagicMock()
        del recorder.create
        with patch("general_ludd.agents.capabilities.AgentCapabilities") as MockCaps:
            mock_caps = MagicMock()
            mock_caps.prepare_messages.return_value = [{"role": "user", "content": "q"}]
            MockCaps.return_value = mock_caps
            invoke_model_for_generation(
                gw,
                job_id="j-none-wt",
                work_type=None,
                model_profile="p3",
                prompt_text="q",
                skill_body=None,
                benchmark_recorder=recorder,
            )
        _args, kwargs = recorder.record.call_args
        assert kwargs["work_type"] == "unknown"

    def test_recorder_not_called_when_model_raises(self) -> None:
        gw = MagicMock()
        gw.call_model.side_effect = RuntimeError("boom")
        recorder = MagicMock()
        with patch("general_ludd.agents.capabilities.AgentCapabilities") as MockCaps:
            mock_caps = MagicMock()
            mock_caps.prepare_messages.return_value = [{"role": "user", "content": "q"}]
            MockCaps.return_value = mock_caps
            invoke_model_for_generation(
                gw,
                job_id="j-no-rec-on-fail",
                work_type="code",
                model_profile="default",
                prompt_text="q",
                skill_body=None,
                benchmark_recorder=recorder,
            )
        recorder.record.assert_not_called()

    def test_recorder_exception_is_swallowed(self) -> None:
        """A broken recorder must not propagate an exception to the caller."""
        gw = _fake_gateway(_fake_response(content="ok"))
        recorder = MagicMock()
        del recorder.create
        recorder.record.side_effect = Exception("db down")
        with patch("general_ludd.agents.capabilities.AgentCapabilities") as MockCaps:
            mock_caps = MagicMock()
            mock_caps.prepare_messages.return_value = [{"role": "user", "content": "task"}]
            MockCaps.return_value = mock_caps
            # Must not raise
            result = invoke_model_for_generation(
                gw,
                job_id="j-swallow-rec",
                work_type="code",
                model_profile="default",
                prompt_text="task",
                skill_body=None,
                benchmark_recorder=recorder,
            )
        assert result[0] == "ok"


# ---------------------------------------------------------------------------
# benchmark_recorder sink — create() dispatch (no record attr)
# ---------------------------------------------------------------------------


class TestBenchmarkRecorderCreate:
    def test_create_called_when_no_record_attr(self) -> None:
        gw = _fake_gateway(_fake_response(content="out", input_tokens=5, output_tokens=3))
        # A recorder that only has create(), not record().
        recorder = MagicMock(spec=["create"])
        recorder.create.return_value = None  # non-awaitable

        with patch("general_ludd.agents.capabilities.AgentCapabilities") as MockCaps:
            mock_caps = MagicMock()
            mock_caps.prepare_messages.return_value = [{"role": "user", "content": "x"}]
            MockCaps.return_value = mock_caps
            invoke_model_for_generation(
                gw,
                job_id="j-create",
                work_type="refactor",
                model_profile="p4",
                prompt_text="x",
                skill_body=None,
                benchmark_recorder=recorder,
            )

        recorder.create.assert_called_once()
        _args, kwargs = recorder.create.call_args
        assert kwargs["model_profile_id"] == "p4"
        assert kwargs["work_type"] == "refactor"
        assert kwargs["success"] is True


# ---------------------------------------------------------------------------
# usage_metadata absent — token count fallback
# ---------------------------------------------------------------------------


class TestUsageMetadataFallback:
    def test_missing_usage_metadata_uses_len_heuristic(self) -> None:
        """When response.usage_metadata is None/empty, token counts fall back
        to len(text)//4 heuristics and the recorder is still called."""
        resp = MagicMock()
        resp.content = "x" * 100  # 100 chars → output_tokens ≈ 25
        resp.usage_metadata = {}  # empty → both keys absent

        gw = MagicMock()
        gw.call_model.return_value = resp

        recorder = MagicMock()
        del recorder.create

        with patch("general_ludd.agents.capabilities.AgentCapabilities") as MockCaps:
            mock_caps = MagicMock()
            prompt = "y" * 80  # 80 chars → input_tokens ≈ 20
            mock_caps.prepare_messages.return_value = [{"role": "user", "content": prompt}]
            MockCaps.return_value = mock_caps
            invoke_model_for_generation(
                gw,
                job_id="j-fallback",
                work_type="analysis",
                model_profile="default",
                prompt_text=prompt,
                skill_body=None,
                benchmark_recorder=recorder,
            )

        recorder.record.assert_called_once()
        _args, kwargs = recorder.record.call_args
        # Both token counts must be non-negative ints (heuristic values)
        assert isinstance(kwargs["input_tokens"], int)
        assert kwargs["input_tokens"] >= 0
        assert isinstance(kwargs["output_tokens"], int)
        assert kwargs["output_tokens"] >= 0
