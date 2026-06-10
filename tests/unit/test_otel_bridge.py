from __future__ import annotations

import importlib
import sys
import types
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

from general_ludd.config.user_config import ObservabilityConfig, UserConfig
from general_ludd.observability.tracer import ExecutionSpan, ExecutionTrace


def _make_trace() -> ExecutionTrace:
    trace = ExecutionTrace(todo_id="todo-1", work_type="code")
    span = trace.start_span("generate", "generate")
    span.complete(
        status="success",
        output_tokens=100,
        input_tokens=50,
        cost_usd=0.002,
    )
    return trace


def _make_span(
    trace_id: str = "trace-abc",
    span_id: str = "span-123",
    status: str = "success",
) -> ExecutionSpan:
    started = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
    span = ExecutionSpan(
        trace_id=trace_id,
        span_id=span_id,
        name="test-span",
        phase="generate",
        started_at=started,
    )
    span.complete(
        status=status,
        ended_at=datetime(2026, 1, 1, 0, 0, 1, tzinfo=UTC),
        output_tokens=100,
        input_tokens=50,
        cost_usd=0.002,
    )
    return span


class TestObservabilityConfig:
    def test_defaults(self):
        cfg = ObservabilityConfig()
        assert cfg.otel_endpoint is None
        assert cfg.service_name == "general-ludd"

    def test_custom_values(self):
        cfg = ObservabilityConfig(otel_endpoint="http://localhost:4317", service_name="my-service")
        assert cfg.otel_endpoint == "http://localhost:4317"
        assert cfg.service_name == "my-service"

    def test_user_config_includes_observability(self):
        uc = UserConfig()
        assert uc.observability.otel_endpoint is None

    def test_user_config_parse(self):
        uc = UserConfig(observability={"otel_endpoint": "http://phoenix:4317"})
        assert uc.observability.otel_endpoint == "http://phoenix:4317"


class TestOTelBridgeNoOTel:
    def test_is_available_false_without_otel(self):
        from general_ludd.observability.otel_bridge import OTelBridge

        bridge = OTelBridge.__new__(OTelBridge)
        bridge._available = False
        assert bridge.is_available() is False

    def test_export_trace_noop_when_unavailable(self):
        from general_ludd.observability.otel_bridge import OTelBridge

        bridge = OTelBridge.__new__(OTelBridge)
        bridge._available = False
        bridge._tracer = None
        trace = _make_trace()
        result = bridge.export_trace(trace)
        assert result is None

    def test_shutdown_noop_when_unavailable(self):
        from general_ludd.observability.otel_bridge import OTelBridge

        bridge = OTelBridge.__new__(OTelBridge)
        bridge._available = False
        bridge._provider = None
        bridge.shutdown()


class TestOTelBridgeWithOTel:
    def _make_mocked_bridge(self):
        from general_ludd.observability.otel_bridge import OTelBridge

        mock_tracer = MagicMock()
        mock_provider = MagicMock()
        mock_status = MagicMock()
        mock_status_code = MagicMock()

        bridge = OTelBridge.__new__(OTelBridge)
        bridge._available = True
        bridge._tracer = mock_tracer
        bridge._provider = mock_provider
        bridge._service_name = "test-service"
        bridge._Status = mock_status
        bridge._StatusCode = mock_status_code
        return bridge, mock_tracer, mock_provider

    def test_export_trace_converts_spans(self):
        bridge, mock_tracer, _ = self._make_mocked_bridge()
        mock_tracer.start_as_current_span.return_value = MagicMock()
        trace = _make_trace()

        bridge.export_trace(trace)

        assert mock_tracer.start_as_current_span.call_count == len(trace.spans)

    def test_export_trace_sets_attributes(self):
        bridge, mock_tracer, _ = self._make_mocked_bridge()
        span = _make_span()
        span.error_message = "timeout"
        trace = ExecutionTrace(todo_id="todo-1")
        trace.spans = [span]

        mock_otel_span = MagicMock()
        mock_tracer.start_as_current_span.return_value = mock_otel_span

        bridge.export_trace(trace)

        mock_otel_span.set_attribute.assert_called()

    def test_export_trace_sets_status_ok(self):
        bridge, mock_tracer, _ = self._make_mocked_bridge()
        span = _make_span(status="success")
        trace = ExecutionTrace(todo_id="todo-1")
        trace.spans = [span]

        mock_otel_span = MagicMock()
        mock_tracer.start_as_current_span.return_value = mock_otel_span

        bridge.export_trace(trace)

        mock_otel_span.set_status.assert_called()

    def test_export_trace_sets_status_error(self):
        bridge, mock_tracer, _ = self._make_mocked_bridge()
        span = _make_span(status="error")
        span.error_message = "something broke"
        trace = ExecutionTrace(todo_id="todo-1")
        trace.spans = [span]

        mock_otel_span = MagicMock()
        mock_tracer.start_as_current_span.return_value = mock_otel_span

        bridge.export_trace(trace)

        mock_otel_span.set_status.assert_called()

    def test_export_trace_empty_spans(self):
        bridge, mock_tracer, _ = self._make_mocked_bridge()
        trace = ExecutionTrace(todo_id="todo-1")

        bridge.export_trace(trace)

        mock_tracer.start_as_current_span.assert_not_called()

    def test_shutdown_flushes_provider(self):
        bridge, _, mock_provider = self._make_mocked_bridge()

        bridge.shutdown()

        mock_provider.force_flush.assert_called_once()
        mock_provider.shutdown.assert_called_once()

    def test_is_available_true(self):
        bridge, _, _ = self._make_mocked_bridge()
        assert bridge.is_available() is True


class TestOTelBridgeInit:
    def test_init_sets_up_bridge(self):
        mock_status_mod = types.ModuleType("opentelemetry.trace")
        mock_status_cls = MagicMock()
        mock_status_code = MagicMock()
        mock_status_mod.Status = mock_status_cls
        mock_status_mod.StatusCode = mock_status_code
        mock_status_mod.get_tracer = MagicMock()

        mock_sdk_resources = types.ModuleType("opentelemetry.sdk.resources")
        mock_sdk_resources.Resource = MagicMock()
        mock_sdk_resources.Resource.create = MagicMock(return_value=MagicMock())

        mock_export_mod = types.ModuleType("opentelemetry.sdk.trace.export")
        mock_export_mod.BatchSpanProcessor = MagicMock()

        mock_sdk_trace = types.ModuleType("opentelemetry.sdk.trace")
        mock_sdk_trace.TracerProvider = MagicMock()
        mock_sdk_trace.TracerProvider.return_value = MagicMock()
        mock_sdk_trace.export = mock_export_mod

        mock_otlp = types.ModuleType("opentelemetry.exporter.otlp.proto.grpc.trace_exporter")
        mock_otlp.OTLPSpanExporter = MagicMock()

        fake_modules = {
            "opentelemetry": types.ModuleType("opentelemetry"),
            "opentelemetry.sdk": types.ModuleType("opentelemetry.sdk"),
            "opentelemetry.sdk.resources": mock_sdk_resources,
            "opentelemetry.sdk.trace": mock_sdk_trace,
            "opentelemetry.sdk.trace.export": mock_export_mod,
            "opentelemetry.exporter": types.ModuleType("opentelemetry.exporter"),
            "opentelemetry.exporter.otlp": types.ModuleType("opentelemetry.exporter.otlp"),
            "opentelemetry.exporter.otlp.proto": types.ModuleType("opentelemetry.exporter.otlp.proto"),
            "opentelemetry.exporter.otlp.proto.grpc": types.ModuleType("opentelemetry.exporter.otlp.proto.grpc"),
            "opentelemetry.exporter.otlp.proto.grpc.trace_exporter": mock_otlp,
            "opentelemetry.trace": mock_status_mod,
        }

        with patch.dict(sys.modules, fake_modules):
            import general_ludd.observability.otel_bridge as bridge_mod

            importlib.reload(bridge_mod)
            bridge = bridge_mod.OTelBridge(endpoint="http://localhost:4317", service_name="test-svc")

        assert bridge.is_available() is True
        assert bridge._service_name == "test-svc"
