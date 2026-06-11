from __future__ import annotations

import logging
from typing import Any

from general_ludd.observability.tracer import ExecutionTrace

logger = logging.getLogger(__name__)


def _check_otel_available() -> bool:
    try:
        import importlib.util
        modules = [
            "opentelemetry.exporter.otlp.proto.grpc.trace_exporter",
            "opentelemetry.sdk.resources",
            "opentelemetry.sdk.trace",
            "opentelemetry.sdk.trace.export",
            "opentelemetry.trace",
        ]
        return all(importlib.util.find_spec(mod) is not None for mod in modules)
    except ImportError:
        return False


class OTelBridge:
    def __init__(self, endpoint: str, service_name: str = "general-ludd") -> None:
        self._available: bool = False
        self._tracer: Any = None
        self._provider: Any = None
        self._service_name: str = service_name
        self._Status: Any = None
        self._StatusCode: Any = None

        if not _check_otel_available():
            logger.debug("OpenTelemetry packages not installed, bridge disabled")
            return

        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.trace import Status, StatusCode, get_tracer

        self._Status = Status
        self._StatusCode = StatusCode

        resource = Resource.create({"service.name": service_name})
        exporter = OTLPSpanExporter(endpoint=endpoint, insecure=True)
        provider = TracerProvider(resource=resource)
        provider.add_span_processor(BatchSpanProcessor(exporter))
        self._provider = provider
        self._tracer = get_tracer(service_name, tracer_provider=provider)
        self._available = True
        logger.info("OTel bridge initialized: endpoint=%s service=%s", endpoint, service_name)

    def is_available(self) -> bool:
        return self._available

    def export_trace(self, trace: ExecutionTrace) -> None:
        if not self._available or not self._tracer:
            return

        status_map: dict[str, Any] = {
            "success": self._Status(self._StatusCode.OK),
            "error": self._Status(self._StatusCode.ERROR),
            "running": self._Status(self._StatusCode.UNSET),
        }

        for span in trace.spans:
            otel_span = self._tracer.start_as_current_span(
                span.name,
                attributes={
                    "gludd.trace_id": span.trace_id,
                    "gludd.span_id": span.span_id,
                    "gludd.phase": span.phase,
                    "gludd.todo_id": trace.todo_id,
                    "gludd.work_type": trace.work_type,
                    "gludd.input_tokens": span.input_tokens,
                    "gludd.output_tokens": span.output_tokens,
                    "gludd.cost_usd": span.cost_usd,
                    "gludd.duration_ms": span.duration_ms,
                },
            )
            try:
                if span.error_message:
                    otel_span.set_attribute("gludd.error_message", span.error_message)

                otel_status = status_map.get(span.status)
                if otel_status is not None:
                    otel_span.set_status(otel_status)
            finally:
                otel_span.end()

    def shutdown(self) -> None:
        if not self._available or not self._provider:
            return
        try:
            self._provider.force_flush()
            self._provider.shutdown()
            logger.info("OTel bridge shutdown complete")
        except Exception as exc:
            logger.warning("OTel bridge shutdown error: %s", exc)
        finally:
            self._available = False
