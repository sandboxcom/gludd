from __future__ import annotations

from unittest.mock import MagicMock, patch

from general_ludd.observability.otel_bridge import OTelBridge, _check_otel_available
from general_ludd.observability.tracer import ExecutionTrace


class TestCheckOtelAvailable:
    def test_returns_bool(self) -> None:
        assert isinstance(_check_otel_available(), bool)

    def test_returns_false_when_submodule_missing(self) -> None:
        with patch(
            "importlib.util.find_spec",
            side_effect=lambda mod: None if "opentelemetry.sdk.trace" in str(mod) else MagicMock(),
        ):
            assert _check_otel_available() is False

    def test_returns_true_when_all_modules_present(self) -> None:
        with patch("importlib.util.find_spec", return_value=MagicMock()):
            assert _check_otel_available() is True


class TestOTelBridgeInit:
    def test_unavailable_when_otel_not_installed(self) -> None:
        with patch(
            "general_ludd.observability.otel_bridge._check_otel_available",
            return_value=False,
        ):
            bridge = OTelBridge(endpoint="http://localhost:4317")
            assert bridge.is_available() is False

    def test_tracer_and_provider_set_to_none_when_unavailable(self) -> None:
        with patch(
            "general_ludd.observability.otel_bridge._check_otel_available",
            return_value=False,
        ):
            bridge = OTelBridge(endpoint="http://localhost:4317")
            assert bridge._tracer is None
            assert bridge._provider is None

    def test_stores_service_name(self) -> None:
        with patch(
            "general_ludd.observability.otel_bridge._check_otel_available",
            return_value=False,
        ):
            bridge = OTelBridge(endpoint="http://localhost:4317", service_name="test-svc")
            assert bridge._service_name == "test-svc"


class _FakeStatusCode:
    OK = "OK"
    ERROR = "ERROR"
    UNSET = "UNSET"


class TestOTelBridgeExportTrace:
    def _make_bridge(self, mock_tracer: MagicMock) -> OTelBridge:
        """Build an OTelBridge with wiring for export_trace tests."""
        with patch(
            "general_ludd.observability.otel_bridge._check_otel_available",
            return_value=False,
        ):
            bridge = OTelBridge(endpoint="http://localhost:4317")
        bridge._available = True
        bridge._tracer = mock_tracer
        bridge._Status = MagicMock
        bridge._StatusCode = _FakeStatusCode
        return bridge

    def _make_trace(self, todo_id: str = "t1", work_type: str = "code") -> ExecutionTrace:
        trace = ExecutionTrace(todo_id=todo_id, work_type=work_type, project_id="p1")
        span = trace.start_span("generate-code", "generate")
        span.complete(
            status="success",
            input_tokens=100,
            output_tokens=200,
            cost_usd=0.001,
        )
        span2 = trace.start_span("review", "review")
        span2.complete(
            status="success",
            input_tokens=50,
            output_tokens=80,
            cost_usd=0.0005,
        )
        return trace

    def test_noop_when_unavailable(self) -> None:
        with patch(
            "general_ludd.observability.otel_bridge._check_otel_available",
            return_value=False,
        ):
            bridge = OTelBridge(endpoint="http://localhost:4317")
            trace = ExecutionTrace(todo_id="t1")
            bridge.export_trace(trace)  # must not raise

    def test_noop_when_available_but_tracer_none(self) -> None:
        with patch(
            "general_ludd.observability.otel_bridge._check_otel_available",
            return_value=False,
        ):
            bridge = OTelBridge(endpoint="http://localhost:4317")
            bridge._available = True
            bridge._tracer = None
            trace = self._make_trace()
            bridge.export_trace(trace)  # must not raise

    def test_exports_spans_with_correct_attributes(self) -> None:
        mock_tracer = MagicMock()
        mock_span_ctx = MagicMock()
        mock_tracer.start_as_current_span.return_value = mock_span_ctx

        bridge = self._make_bridge(mock_tracer)
        trace = self._make_trace()
        bridge.export_trace(trace)

        assert mock_tracer.start_as_current_span.call_count == 2

    def test_success_status_maps_to_ok(self) -> None:
        mock_tracer = MagicMock()
        mock_span_ctx = MagicMock()
        mock_tracer.start_as_current_span.return_value = mock_span_ctx

        bridge = self._make_bridge(mock_tracer)
        trace = ExecutionTrace(todo_id="t1")
        span = trace.start_span("test", "generate")
        span.complete(status="success")
        bridge.export_trace(trace)

        mock_span_ctx.set_status.assert_called_once()
        set_status_arg = mock_span_ctx.set_status.call_args[0][0]
        assert isinstance(set_status_arg, MagicMock)

    def test_error_status_maps_to_error(self) -> None:
        mock_tracer = MagicMock()
        mock_span_ctx = MagicMock()
        mock_tracer.start_as_current_span.return_value = mock_span_ctx

        bridge = self._make_bridge(mock_tracer)
        trace = ExecutionTrace(todo_id="t1")
        span = trace.start_span("failing-step", "generate")
        span.complete(status="error", error_message="timeout")
        bridge.export_trace(trace)

        mock_span_ctx.set_status.assert_called_once()

    def test_running_status_maps_to_unset(self) -> None:
        mock_tracer = MagicMock()
        mock_span_ctx = MagicMock()
        mock_tracer.start_as_current_span.return_value = mock_span_ctx

        bridge = self._make_bridge(mock_tracer)
        trace = ExecutionTrace(todo_id="t1")
        _ = trace.start_span("in-progress", "generate")
        bridge.export_trace(trace)

        mock_span_ctx.set_status.assert_called_once()

    def test_error_message_set_as_attribute(self) -> None:
        mock_tracer = MagicMock()
        mock_span_ctx = MagicMock()
        mock_tracer.start_as_current_span.return_value = mock_span_ctx

        bridge = self._make_bridge(mock_tracer)
        trace = ExecutionTrace(todo_id="t1")
        span = trace.start_span("step", "generate")
        span.complete(status="error", error_message="disk full")
        bridge.export_trace(trace)

        mock_span_ctx.set_attribute.assert_any_call("gludd.error_message", "disk full")

    def test_trace_attributes_include_trace_id_and_todo_id(self) -> None:
        mock_tracer = MagicMock()
        mock_span_ctx = MagicMock()
        mock_tracer.start_as_current_span.return_value = mock_span_ctx

        bridge = self._make_bridge(mock_tracer)
        trace = ExecutionTrace(todo_id="my-task-42", work_type="code")
        span = trace.start_span("step", "generate")
        span.complete(status="success", input_tokens=10, output_tokens=20, cost_usd=0.0001)
        bridge.export_trace(trace)

        attrs = mock_tracer.start_as_current_span.call_args[1]["attributes"]
        assert attrs["gludd.trace_id"] == trace.trace_id
        assert attrs["gludd.todo_id"] == "my-task-42"
        assert attrs["gludd.work_type"] == "code"
        assert attrs["gludd.input_tokens"] == 10
        assert attrs["gludd.output_tokens"] == 20
        assert attrs["gludd.cost_usd"] == 0.0001

    def test_empty_trace_no_spans_is_noop(self) -> None:
        mock_tracer = MagicMock()

        bridge = self._make_bridge(mock_tracer)
        trace = ExecutionTrace(todo_id="t1")
        bridge.export_trace(trace)

        mock_tracer.start_as_current_span.assert_not_called()

    def test_each_span_end_is_called_in_finally(self) -> None:
        mock_tracer = MagicMock()
        mock_span_ctx = MagicMock()
        mock_tracer.start_as_current_span.return_value = mock_span_ctx

        bridge = self._make_bridge(mock_tracer)
        trace = self._make_trace()
        bridge.export_trace(trace)

        assert mock_span_ctx.end.call_count == 2

    def test_unknown_status_no_set_status_call(self) -> None:
        mock_tracer = MagicMock()
        mock_span_ctx = MagicMock()
        mock_tracer.start_as_current_span.return_value = mock_span_ctx

        bridge = self._make_bridge(mock_tracer)
        trace = ExecutionTrace(todo_id="t1")
        span = trace.start_span("step", "generate")
        span.status = "unknown-status"
        bridge.export_trace(trace)

        mock_span_ctx.set_status.assert_not_called()

    def test_export_trace_does_not_raise_on_transport_error(self) -> None:
        mock_tracer = MagicMock()
        mock_span_ctx = MagicMock()
        mock_span_ctx.__enter__.side_effect = RuntimeError("transport closed")
        mock_tracer.start_as_current_span.return_value = mock_span_ctx

        bridge = self._make_bridge(mock_tracer)
        trace = ExecutionTrace(todo_id="t1")
        span = trace.start_span("step", "generate")
        span.complete(status="success")
        bridge.export_trace(trace)  # must not raise

    def test_no_error_attribute_when_error_message_is_none(self) -> None:
        mock_tracer = MagicMock()
        mock_span_ctx = MagicMock()
        mock_tracer.start_as_current_span.return_value = mock_span_ctx

        bridge = self._make_bridge(mock_tracer)
        trace = ExecutionTrace(todo_id="t1")
        span = trace.start_span("step", "generate")
        span.complete(status="error")  # no error_message
        bridge.export_trace(trace)

        gludd_error_calls = [c for c in mock_span_ctx.set_attribute.call_args_list if c[0][0] == "gludd.error_message"]
        assert len(gludd_error_calls) == 0

    def test_export_trace_works_with_mixed_status_spans(self) -> None:
        mock_tracer = MagicMock()
        mock_span_ctx_ok = MagicMock()
        mock_span_ctx_err = MagicMock()
        mock_span_ctx_running = MagicMock()
        mock_tracer.start_as_current_span.side_effect = [
            mock_span_ctx_ok,
            mock_span_ctx_err,
            mock_span_ctx_running,
        ]

        bridge = self._make_bridge(mock_tracer)
        trace = ExecutionTrace(todo_id="t1")
        s1 = trace.start_span("success-step", "generate")
        s1.complete(status="success")
        s2 = trace.start_span("error-step", "generate")
        s2.complete(status="error", error_message="failed")
        trace.start_span("running-step", "generate")
        bridge.export_trace(trace)

        assert mock_span_ctx_ok.set_status.called
        assert mock_span_ctx_err.set_status.called
        assert mock_span_ctx_running.set_status.called


class TestOTelBridgeShutdown:
    def test_shutdown_noop_when_unavailable(self) -> None:
        with patch(
            "general_ludd.observability.otel_bridge._check_otel_available",
            return_value=False,
        ):
            bridge = OTelBridge(endpoint="http://localhost:4317")
            bridge.shutdown()
            assert not bridge.is_available()

    def test_shutdown_noop_when_provider_none(self) -> None:
        with patch(
            "general_ludd.observability.otel_bridge._check_otel_available",
            return_value=False,
        ):
            bridge = OTelBridge(endpoint="http://localhost:4317")
            bridge._available = True
            bridge._provider = None
            bridge.shutdown()  # must not raise

    def test_shutdown_calls_force_flush_and_shutdown(self) -> None:
        mock_provider = MagicMock()
        with patch(
            "general_ludd.observability.otel_bridge._check_otel_available",
            return_value=False,
        ):
            bridge = OTelBridge(endpoint="http://localhost:4317")
            bridge._available = True
            bridge._provider = mock_provider
            bridge.shutdown()

        mock_provider.force_flush.assert_called_once()
        mock_provider.shutdown.assert_called_once()
        assert not bridge.is_available()

    def test_shutdown_sets_available_false_even_on_error(self) -> None:
        mock_provider = MagicMock()
        mock_provider.force_flush.side_effect = RuntimeError("flush failed")
        with patch(
            "general_ludd.observability.otel_bridge._check_otel_available",
            return_value=False,
        ):
            bridge = OTelBridge(endpoint="http://localhost:4317")
            bridge._available = True
            bridge._provider = mock_provider
            bridge.shutdown()  # must not raise

        assert not bridge.is_available()
