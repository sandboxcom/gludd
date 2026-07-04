"""Tests for LangSmithTracer — observability side-channel for ModelGateway."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


class TestLangSmithTracer:
    def test_disabled_when_env_vars_not_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("LANGSMITH_API_KEY", raising=False)
        monkeypatch.delenv("LANGSMITH_PROJECT", raising=False)
        from general_ludd.observability.langsmith_tracer import LangSmithTracer

        tracer = LangSmithTracer()
        assert tracer.is_enabled() is False

    def test_disabled_when_only_key_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LANGSMITH_API_KEY", "lsv2_test_key")
        monkeypatch.delenv("LANGSMITH_PROJECT", raising=False)
        from general_ludd.observability.langsmith_tracer import LangSmithTracer

        tracer = LangSmithTracer()
        assert tracer.is_enabled() is False

    def test_disabled_when_only_project_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("LANGSMITH_API_KEY", raising=False)
        monkeypatch.setenv("LANGSMITH_PROJECT", "my-project")
        from general_ludd.observability.langsmith_tracer import LangSmithTracer

        tracer = LangSmithTracer()
        assert tracer.is_enabled() is False

    def test_trace_call_noop_when_disabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("LANGSMITH_API_KEY", raising=False)
        monkeypatch.delenv("LANGSMITH_PROJECT", raising=False)
        from general_ludd.observability.langsmith_tracer import LangSmithTracer

        tracer = LangSmithTracer()
        # Must not raise.
        tracer.trace_call(
            model_name="gpt-4",
            messages=[{"role": "user", "content": "hello"}],
            response="world",
            tokens={"input": 10, "output": 5},
            cost=0.001,
        )

    def test_enabled_with_env_vars(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LANGSMITH_API_KEY", "lsv2_test_key")
        monkeypatch.setenv("LANGSMITH_PROJECT", "my-project")
        with patch(
            "general_ludd.observability.langsmith_tracer.LangSmithTracer._build_client",
            return_value=MagicMock(),
        ):
            from general_ludd.observability.langsmith_tracer import LangSmithTracer

            tracer = LangSmithTracer()
            assert tracer.is_enabled() is True

    def test_trace_call_does_not_raise_when_langsmith_unavailable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("LANGSMITH_API_KEY", "lsv2_test_key")
        monkeypatch.setenv("LANGSMITH_PROJECT", "my-project")
        with patch(
            "general_ludd.observability.langsmith_tracer.LangSmithTracer._build_client",
            return_value=MagicMock(),
        ):
            from general_ludd.observability.langsmith_tracer import LangSmithTracer

            tracer = LangSmithTracer()
            assert tracer.is_enabled() is True

        # Simulate client error during trace_call.
        tracer._client = None
        # Must not raise.
        tracer.trace_call(
            model_name="gpt-4",
            messages=[{"role": "user", "content": "hello"}],
            response="world",
            tokens={"input": 10, "output": 5},
            cost=0.001,
        )

    def test_trace_call_with_client_error_is_noop(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("LANGSMITH_API_KEY", "lsv2_test_key")
        monkeypatch.setenv("LANGSMITH_PROJECT", "my-project")
        mock_client = MagicMock()
        mock_client.create_run.side_effect = RuntimeError("connection refused")
        with patch(
            "general_ludd.observability.langsmith_tracer.LangSmithTracer._build_client",
            return_value=mock_client,
        ):
            from general_ludd.observability.langsmith_tracer import LangSmithTracer

            tracer = LangSmithTracer()
            assert tracer.is_enabled() is True

        # Must not raise despite client error.
        tracer.trace_call(
            model_name="gpt-4",
            messages=[{"role": "user", "content": "hello"}],
            response="world",
            tokens={"input": 10, "output": 5},
            cost=0.001,
        )
        mock_client.create_run.assert_called_once()

    def test_trace_call_trims_response_to_2000_chars(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("LANGSMITH_API_KEY", "lsv2_test_key")
        monkeypatch.setenv("LANGSMITH_PROJECT", "my-project")
        mock_client = MagicMock()
        with patch(
            "general_ludd.observability.langsmith_tracer.LangSmithTracer._build_client",
            return_value=mock_client,
        ):
            from general_ludd.observability.langsmith_tracer import LangSmithTracer

            tracer = LangSmithTracer()
            long_response = "x" * 3000
            tracer.trace_call(
                model_name="gpt-4",
                messages=[{"role": "user", "content": "hello"}],
                response=long_response,
                tokens={"input": 10, "output": 3000},
                cost=0.01,
            )

        call_kwargs = mock_client.create_run.call_args.kwargs
        assert len(call_kwargs["outputs"]["text"]) == 2000

    def test_can_be_constructed_and_queried_for_enabled_state(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("LANGSMITH_API_KEY", raising=False)
        monkeypatch.delenv("LANGSMITH_PROJECT", raising=False)
        from general_ludd.observability.langsmith_tracer import LangSmithTracer

        tracer = LangSmithTracer()
        assert isinstance(tracer.is_enabled(), bool)

    def test_langsmith_import_error_disables_tracer(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("LANGSMITH_API_KEY", "lsv2_test_key")
        monkeypatch.setenv("LANGSMITH_PROJECT", "my-project")
        with patch(
            "general_ludd.observability.langsmith_tracer.LangSmithTracer._build_client",
            side_effect=ImportError("no langsmith"),
        ):
            from general_ludd.observability.langsmith_tracer import LangSmithTracer

            tracer = LangSmithTracer()
            assert tracer.is_enabled() is False
