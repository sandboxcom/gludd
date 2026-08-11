"""Tests for LangSmithTracer — observability side-channel for ModelGateway."""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import pytest


def _make_enabled_tracer(monkeypatch: pytest.MonkeyPatch, client: object = None):
    monkeypatch.setenv("LANGSMITH_API_KEY", "lsv2_test_key")
    monkeypatch.setenv("LANGSMITH_PROJECT", "my-project")
    with patch(
        "general_ludd.observability.langsmith_tracer.LangSmithTracer._build_client",
        return_value=client if client is not None else MagicMock(),
    ):
        from general_ludd.observability.langsmith_tracer import LangSmithTracer

        return LangSmithTracer()


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
        tracer.trace_call(
            model_name="gpt-4",
            messages=[{"role": "user", "content": "hello"}],
            response="world",
            tokens={"input": 10, "output": 5},
            cost=0.001,
        )

    def test_enabled_with_env_vars(self, monkeypatch: pytest.MonkeyPatch) -> None:
        tracer = _make_enabled_tracer(monkeypatch)
        assert tracer.is_enabled() is True

    def test_trace_call_does_not_raise_when_client_is_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        tracer = _make_enabled_tracer(monkeypatch)
        tracer._client = None
        tracer.trace_call(
            model_name="gpt-4",
            messages=[{"role": "user", "content": "hello"}],
            response="world",
            tokens={"input": 10, "output": 5},
            cost=0.001,
        )

    def test_trace_call_with_client_error_is_noop(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mock_client = MagicMock()
        mock_client.create_run.side_effect = RuntimeError("connection refused")
        tracer = _make_enabled_tracer(monkeypatch, client=mock_client)

        tracer.trace_call(
            model_name="gpt-4",
            messages=[{"role": "user", "content": "hello"}],
            response="world",
            tokens={"input": 10, "output": 5},
            cost=0.001,
        )
        mock_client.create_run.assert_called_once()

    def test_trace_call_trims_response_to_2000_chars(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mock_client = MagicMock()
        tracer = _make_enabled_tracer(monkeypatch, client=mock_client)

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

    def test_can_be_constructed_and_queried_for_enabled_state(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("LANGSMITH_API_KEY", raising=False)
        monkeypatch.delenv("LANGSMITH_PROJECT", raising=False)
        from general_ludd.observability.langsmith_tracer import LangSmithTracer

        tracer = LangSmithTracer()
        assert isinstance(tracer.is_enabled(), bool)

    def test_langsmith_import_error_disables_tracer(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LANGSMITH_API_KEY", "lsv2_test_key")
        monkeypatch.setenv("LANGSMITH_PROJECT", "my-project")
        with patch(
            "general_ludd.observability.langsmith_tracer.LangSmithTracer._build_client",
            side_effect=ImportError("no langsmith"),
        ):
            from general_ludd.observability.langsmith_tracer import LangSmithTracer

            tracer = LangSmithTracer()
            assert tracer.is_enabled() is False

    def test_disabled_when_api_key_empty_string(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LANGSMITH_API_KEY", "")
        monkeypatch.setenv("LANGSMITH_PROJECT", "my-project")
        from general_ludd.observability.langsmith_tracer import LangSmithTracer

        tracer = LangSmithTracer()
        assert tracer.is_enabled() is False

    def test_disabled_when_project_empty_string(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LANGSMITH_API_KEY", "lsv2_test_key")
        monkeypatch.setenv("LANGSMITH_PROJECT", "")
        from general_ludd.observability.langsmith_tracer import LangSmithTracer

        tracer = LangSmithTracer()
        assert tracer.is_enabled() is False

    def test_init_generic_client_exception_disables_tracer(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        monkeypatch.setenv("LANGSMITH_API_KEY", "lsv2_test_key")
        monkeypatch.setenv("LANGSMITH_PROJECT", "my-project")
        with patch(
            "general_ludd.observability.langsmith_tracer.LangSmithTracer._build_client",
            side_effect=ConnectionError("unreachable"),
        ):
            from general_ludd.observability.langsmith_tracer import LangSmithTracer

            with caplog.at_level(logging.DEBUG, logger="general_ludd.observability.langsmith_tracer"):
                tracer = LangSmithTracer()
            assert tracer.is_enabled() is False
            assert any("unreachable" in r.message for r in caplog.records)

    def test_trace_call_with_none_response_passes_empty_string(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mock_client = MagicMock()
        tracer = _make_enabled_tracer(monkeypatch, client=mock_client)

        tracer.trace_call(
            model_name="claude-4",
            messages=[{"role": "user", "content": "test"}],
            response="",
            tokens={"input": 1, "output": 0},
            cost=0.0,
        )

        text = mock_client.create_run.call_args.kwargs["outputs"]["text"]
        assert text == ""

    def test_trace_call_trims_message_content_to_500_chars(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mock_client = MagicMock()
        tracer = _make_enabled_tracer(monkeypatch, client=mock_client)

        long_content = "a" * 800
        tracer.trace_call(
            model_name="gpt-4",
            messages=[{"role": "user", "content": long_content}],
            response="ok",
            tokens={"input": 100, "output": 2},
            cost=0.02,
        )

        trimmed = mock_client.create_run.call_args.kwargs["inputs"]["messages"]
        assert len(trimmed[0]["content"]) == 500

    def test_trace_call_metadata_preserved_and_overlaid(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mock_client = MagicMock()
        tracer = _make_enabled_tracer(monkeypatch, client=mock_client)

        tracer.trace_call(
            model_name="claude-4",
            messages=[{"role": "user", "content": "hello"}],
            response="world",
            tokens={"input": 50, "output": 20},
            cost=0.005,
            metadata={"session_id": "abc123", "tenant": "dev"},
        )

        extra_meta = mock_client.create_run.call_args.kwargs["extra"]["metadata"]
        assert extra_meta["session_id"] == "abc123"
        assert extra_meta["tenant"] == "dev"
        assert extra_meta["model_name"] == "claude-4"
        assert extra_meta["input_tokens"] == "50"
        assert extra_meta["output_tokens"] == "20"
        assert extra_meta["cost"] == "0.00500000"

    def test_trace_call_defaults_missing_token_keys_to_zero(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mock_client = MagicMock()
        tracer = _make_enabled_tracer(monkeypatch, client=mock_client)

        tracer.trace_call(
            model_name="qwen2",
            messages=[{"role": "user", "content": "x"}],
            response="y",
            tokens={},
            cost=0.0,
        )

        extra_meta = mock_client.create_run.call_args.kwargs["extra"]["metadata"]
        assert extra_meta["input_tokens"] == "0"
        assert extra_meta["output_tokens"] == "0"

    def test_trace_call_logs_debug_on_exception(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        mock_client = MagicMock()
        mock_client.create_run.side_effect = OSError("network down")
        tracer = _make_enabled_tracer(monkeypatch, client=mock_client)

        with caplog.at_level(logging.DEBUG, logger="general_ludd.observability.langsmith_tracer"):
            tracer.trace_call(
                model_name="gpt-4",
                messages=[{"role": "user", "content": "hello"}],
                response="world",
                tokens={"input": 10, "output": 5},
                cost=0.001,
            )

        assert any("network down" in r.message for r in caplog.records)

    def test_multiple_trace_calls_produce_distinct_runs(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mock_client = MagicMock()
        tracer = _make_enabled_tracer(monkeypatch, client=mock_client)

        for i in range(3):
            tracer.trace_call(
                model_name=f"model-{i}",
                messages=[{"role": "user", "content": f"msg{i}"}],
                response=f"resp{i}",
                tokens={"input": i, "output": i + 1},
                cost=float(i) * 0.01,
            )

        assert mock_client.create_run.call_count == 3
        names = [c.kwargs["name"] for c in mock_client.create_run.call_args_list]
        assert names == ["model-0 call", "model-1 call", "model-2 call"]

    def test_trace_call_uses_configured_project_name(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mock_client = MagicMock()
        tracer = _make_enabled_tracer(monkeypatch, client=mock_client)

        tracer.trace_call(
            model_name="gemini",
            messages=[{"role": "user", "content": "x"}],
            response="y",
            tokens={"input": 1, "output": 1},
            cost=0.001,
        )

        assert mock_client.create_run.call_args.kwargs["project_name"] == "my-project"

    def test_trace_call_uses_correct_run_type_and_tags(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mock_client = MagicMock()
        tracer = _make_enabled_tracer(monkeypatch, client=mock_client)

        tracer.trace_call(
            model_name="gpt-4",
            messages=[{"role": "user", "content": "x"}],
            response="y",
            tokens={"input": 1, "output": 1},
            cost=0.001,
        )

        kwargs = mock_client.create_run.call_args.kwargs
        assert kwargs["run_type"] == "llm"
        assert kwargs["tags"] == ["model-gateway"]

    def test_trace_call_short_response_not_trimmed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mock_client = MagicMock()
        tracer = _make_enabled_tracer(monkeypatch, client=mock_client)

        short = "hello"
        tracer.trace_call(
            model_name="gpt-4",
            messages=[{"role": "user", "content": "x"}],
            response=short,
            tokens={"input": 1, "output": 1},
            cost=0.001,
        )

        text = mock_client.create_run.call_args.kwargs["outputs"]["text"]
        assert text == "hello"

    def test_trace_call_handles_messages_with_missing_role(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mock_client = MagicMock()
        tracer = _make_enabled_tracer(monkeypatch, client=mock_client)

        tracer.trace_call(
            model_name="gpt-4",
            messages=[{"content": "no role key"}],
            response="ok",
            tokens={"input": 1, "output": 1},
            cost=0.001,
        )

        messages = mock_client.create_run.call_args.kwargs["inputs"]["messages"]
        assert messages[0]["role"] == ""

    def test_trace_call_handles_messages_with_non_string_content(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mock_client = MagicMock()
        tracer = _make_enabled_tracer(monkeypatch, client=mock_client)

        tracer.trace_call(
            model_name="gpt-4",
            messages=[{"role": "user", "content": 42}],
            response="ok",
            tokens={"input": 1, "output": 1},
            cost=0.001,
        )

        messages = mock_client.create_run.call_args.kwargs["inputs"]["messages"]
        assert messages[0]["content"] == "42"

    def test_trace_call_response_exactly_2000_not_re_trimmed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mock_client = MagicMock()
        tracer = _make_enabled_tracer(monkeypatch, client=mock_client)

        exact = "y" * 2000
        tracer.trace_call(
            model_name="gpt-4",
            messages=[{"role": "user", "content": "x"}],
            response=exact,
            tokens={"input": 1, "output": 2000},
            cost=0.001,
        )

        text = mock_client.create_run.call_args.kwargs["outputs"]["text"]
        assert len(text) == 2000

    def test_trace_call_import_error_during_init_logs_and_disables(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        monkeypatch.setenv("LANGSMITH_API_KEY", "lsv2_test_key")
        monkeypatch.setenv("LANGSMITH_PROJECT", "my-project")
        with patch(
            "general_ludd.observability.langsmith_tracer.LangSmithTracer._build_client",
            side_effect=ImportError("langsmith SDK missing"),
        ):
            from general_ludd.observability.langsmith_tracer import LangSmithTracer

            with caplog.at_level(logging.DEBUG, logger="general_ludd.observability.langsmith_tracer"):
                tracer = LangSmithTracer()
            assert tracer.is_enabled() is False
            assert any("LangSmith SDK" in r.message for r in caplog.records)

    def test_is_enabled_returns_false_after_generic_init_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LANGSMITH_API_KEY", "lsv2_test_key")
        monkeypatch.setenv("LANGSMITH_PROJECT", "my-project")
        with patch(
            "general_ludd.observability.langsmith_tracer.LangSmithTracer._build_client",
            side_effect=ValueError("bad config"),
        ):
            from general_ludd.observability.langsmith_tracer import LangSmithTracer

            tracer = LangSmithTracer()
            assert tracer.is_enabled() is False
            assert tracer._client is None
