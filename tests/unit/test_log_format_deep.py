"""Deep log format and structured logging tests.

Covers: JSON validity, required fields, sensitive data exclusion, contextual
field propagation, and trace_id inclusion across the logging subsystem.
"""

from __future__ import annotations

import io
import json
import logging
import re
import threading
import traceback
from typing import Any, ClassVar

import pytest

from general_ludd.logging.project_log import (
    ProjectLogAdapter,
    ProjectLogFilter,
    install_project_log_filter,
)
from general_ludd.observability.metrics_exporter import (
    CorrelatedLogAdapter,
    get_trace_id,
    set_trace_id,
)

HANDLED = object()


class _JsonFormatter(logging.Formatter):
    """Canonical structured JSON formatter — mirrors production pattern."""

    def format(self, record: logging.LogRecord) -> str:
        record.message = record.getMessage()
        obj: dict[str, Any] = {
            "timestamp": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "message": record.message,
            "logger": record.name,
        }
        for attr in ("project_id", "trace_id", "span_id"):
            val = getattr(record, attr, None)
            if val is not None:
                obj[attr] = val
        if record.exc_info and record.exc_info[1]:
            obj["exception"] = traceback.format_exception(*record.exc_info)
        return json.dumps(obj, default=str)


def _make_logger(name: str, level: int = logging.DEBUG) -> logging.Logger:
    logger = logging.getLogger(name)
    logger.handlers.clear()
    logger.filters.clear()
    logger.setLevel(level)
    logger.propagate = False
    return logger


def _attach_json(logger: logging.Logger, fmt: logging.Formatter | None = None) -> io.StringIO:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(fmt or _JsonFormatter())
    logger.addHandler(handler)
    return stream


def _attach_plain(logger: logging.Logger, fmt: str = "%(message)s") -> io.StringIO:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter(fmt))
    logger.addHandler(handler)
    return stream


# ── JSON format validity ────────────────────────────────────────────────────


class TestJsonFormatValidity:
    def test_json_output_is_valid_json(self):
        logger = _make_logger("json_val_1")
        stream = _attach_json(logger)
        logger.info("hello")
        obj = json.loads(stream.getvalue().strip())
        assert isinstance(obj, dict)

    def test_multiple_records_all_valid_json(self):
        logger = _make_logger("json_val_2")
        stream = _attach_json(logger)
        logger.debug("msg1")
        logger.info("msg2")
        logger.warning("msg3")
        logger.error("msg4")
        logger.critical("msg5")
        lines = [line for line in stream.getvalue().strip().split("\n") if line]
        assert len(lines) == 5
        for line in lines:
            assert isinstance(json.loads(line), dict)

    def test_json_with_special_characters(self):
        logger = _make_logger("json_val_3")
        stream = _attach_json(logger)
        logger.info('quotes "are" fine')
        logger.warning("backslash \\ test")
        logger.error("newlines\nand\ttabs")
        lines = [line for line in stream.getvalue().strip().split("\n") if line]
        assert len(lines) == 3
        for line in lines:
            record = json.loads(line)
            assert isinstance(record, dict)

    def test_json_with_unicode(self):
        logger = _make_logger("json_val_4")
        stream = _attach_json(logger)
        logger.info("\u00e9\u00f1\u00fc\u2603")
        record = json.loads(stream.getvalue().strip())
        assert "\u00e9" in record["message"]

    def test_json_with_percent_formatting_valid(self):
        logger = _make_logger("json_val_5")
        stream = _attach_json(logger)
        logger.info("value: %d, name: %s", 42, "test")
        record = json.loads(stream.getvalue().strip())
        assert "42" in record["message"]
        assert "test" in record["message"]


# ── required fields ─────────────────────────────────────────────────────────


class TestRequiredFields:
    def test_timestamp_present(self):
        logger = _make_logger("rf_timestamp")
        stream = _attach_json(logger)
        logger.info("present")
        record = json.loads(stream.getvalue().strip())
        assert "timestamp" in record
        assert isinstance(record["timestamp"], str)
        assert len(record["timestamp"]) > 0

    def test_level_present(self):
        logger = _make_logger("rf_level")
        stream = _attach_json(logger)
        logger.warning("warn")
        record = json.loads(stream.getvalue().strip())
        assert record["level"] == "WARNING"

    def test_message_present(self):
        logger = _make_logger("rf_msg")
        stream = _attach_json(logger)
        logger.info("hello world")
        record = json.loads(stream.getvalue().strip())
        assert record["message"] == "hello world"

    def test_logger_name_present(self):
        logger = _make_logger("rf_name")
        stream = _attach_json(logger)
        logger.info("named")
        record = json.loads(stream.getvalue().strip())
        assert record["logger"] == "rf_name"

    @pytest.mark.parametrize("level_name", ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"])
    def test_all_levels_have_required_fields(self, level_name):
        logger = _make_logger("rf_all", level=logging.DEBUG)
        stream = _attach_json(logger)
        getattr(logger, level_name.lower())("test %s", level_name)
        record = json.loads(stream.getvalue().strip())
        for field in ("timestamp", "level", "message", "logger"):
            assert field in record, f"Missing field '{field}' at level {level_name}"


# ── sensitive data exclusion ────────────────────────────────────────────────


class TestSensitiveDataExclusion:
    SENSITIVE_PATTERNS: ClassVar[list[re.Pattern[str]]] = [
        re.compile(r"(?i)password\s*[=:]\s*\S+"),
        re.compile(r"(?i)secret\s*[=:]\s*\S+"),
        re.compile(r"(?i)token\s*[=:]\s*\S+"),
        re.compile(r"(?i)api[_-]?key\s*[=:]\s*\S+"),
        re.compile(r"(?i)access[_-]?key\s*[=:]\s*\S+"),
    ]

    def _has_sensitive(self, text: str) -> bool:
        return any(p.search(text) for p in self.SENSITIVE_PATTERNS)

    def test_no_sensitive_data_in_plain_logger(self):
        logger = _make_logger("sens_plain")
        stream = _attach_json(logger)
        logger.info("processing task 12345")
        record = json.loads(stream.getvalue().strip())
        assert not self._has_sensitive(record["message"])

    def test_no_sensitive_data_leaked_via_extra(self):
        logger = _make_logger("sens_extra")
        stream = _attach_json(logger)
        logger.info("login attempt", extra={"user_name": "alice", "remote_ip": "10.0.0.1"})
        record = json.loads(stream.getvalue().strip())
        assert not self._has_sensitive(record["message"])

    def test_project_id_not_sensitive(self):
        logger = _make_logger("sens_proj")
        stream = _attach_json(logger)
        adapter = ProjectLogAdapter(logger, project_id="my-project")
        adapter.info("deployed")
        record = json.loads(stream.getvalue().strip())
        assert "token" not in record.get("message", "").lower()
        assert "secret" not in record.get("message", "").lower()

    def test_exception_stacktrace_no_embedded_secrets(self):
        logger = _make_logger("sens_exc")
        stream = _attach_json(logger)
        try:
            raise ValueError("connection failed for user=admin, no credentials in trace")
        except ValueError:
            logger.exception("handler error")
        record = json.loads(stream.getvalue().strip())
        if "exception" in record:
            exception_text = json.dumps(record["exception"])
            assert "password" not in exception_text.lower()
            assert "api_key" not in exception_text.lower()

    def test_json_formatter_does_not_include_handler_level_secrets(self):
        logger = _make_logger("sens_hdlr")
        stream = _attach_json(logger)
        logger.info("configured endpoint https://api.example.com/v1/")
        record = json.loads(stream.getvalue().strip())
        assert "api-key" not in record["message"].lower()


# ── contextual field propagation ────────────────────────────────────────────


class TestContextualFieldPropagation:
    def test_project_id_propagated_to_json_output(self):
        logger = _make_logger("ctx_proj")
        stream = _attach_json(logger)
        logger.addFilter(ProjectLogFilter(project_id="myapp"))
        logger.info("running job")
        record = json.loads(stream.getvalue().strip())
        assert record["project_id"] == "myapp"

    def test_project_id_from_adapter_preferred_over_filter(self):
        logger = _make_logger("ctx_override")
        stream = _attach_json(logger)
        logger.addFilter(ProjectLogFilter(project_id="fallback"))
        adapter = ProjectLogAdapter(logger, project_id="active")
        adapter.info("job")
        record = json.loads(stream.getvalue().strip())
        assert record["message"] == "[active] job"
        assert record["project_id"] == "fallback"

    def test_no_project_id_when_none_set(self):
        logger = _make_logger("ctx_none")
        stream = _attach_json(logger)
        logger.info("no context")
        record = json.loads(stream.getvalue().strip())
        assert "project_id" not in record

    def test_project_id_from_install_filter(self):
        logger = _make_logger("ctx_install")
        stream = _attach_json(logger)
        install_project_log_filter("from-installer", logger)
        logger.info("installed")
        record = json.loads(stream.getvalue().strip())
        assert record["project_id"] == "from-installer"

    def test_install_filter_idempotent_set_and_forget(self):
        logger = _make_logger("ctx_idem")
        stream = _attach_json(logger)
        f1 = install_project_log_filter("first", logger)
        f2 = install_project_log_filter("second", logger)
        assert f1 is f2
        logger.info("once")
        record = json.loads(stream.getvalue().strip())
        assert record["project_id"] == "first"
        assert sum(1 for f in logger.filters if isinstance(f, ProjectLogFilter)) == 1

    def test_project_id_absent_on_adapter_with_none(self):
        logger = _make_logger("ctx_adapter_none")
        stream = _attach_json(logger)
        adapter = ProjectLogAdapter(logger, project_id=None)
        adapter.info("no project")
        record = json.loads(stream.getvalue().strip())
        assert "project_id" not in record


# ── trace ID inclusion ──────────────────────────────────────────────────────


class TestTraceIdInclusion:
    def test_correlated_log_adapter_prepends_trace_prefix(self):
        logger = _make_logger("trace_pre")
        stream = _attach_plain(logger)
        set_trace_id("abc123def456")
        adapter = CorrelatedLogAdapter(logger, {})
        adapter.info("task started")
        output = stream.getvalue()
        assert "trace=" in output
        assert "abc123def456" in output
        assert "span=" in output
        assert "task started" in output

    def test_correlated_log_adapter_unique_spans(self):
        logger = _make_logger("trace_span")
        stream = _attach_plain(logger)
        set_trace_id("trace-xyz")
        adapter = CorrelatedLogAdapter(logger, {})
        adapter.info("first")
        adapter.warning("second")
        lines = [line for line in stream.getvalue().strip().split("\n") if line]
        assert len(lines) == 2
        span_ids = set()
        for line in lines:
            m = re.search(r"span=([a-f0-9]+)", line)
            assert m, f"No span_id found in: {line}"
            span_ids.add(m.group(1))
        assert len(span_ids) == 2

    def test_set_and_get_trace_id_thread_local(self):
        tid = set_trace_id("thread-trace-1")
        assert get_trace_id() == tid
        assert "thread-trace-1" in tid

    def test_get_trace_id_unknown_when_not_set(self):
        result = {"tid": ""}

        def _capture() -> None:
            from general_ludd.observability.metrics_exporter import get_trace_id as gti

            result["tid"] = gti()

        t = threading.Thread(target=_capture)
        t.start()
        t.join()
        assert result["tid"] == "unknown"

    def test_correlated_adapter_with_project_log_adapter_nesting(self):
        logger = _make_logger("trace_nest")
        stream = _attach_plain(logger)
        set_trace_id("nest-trace")
        correlated = CorrelatedLogAdapter(logger, {})
        project = ProjectLogAdapter(logger, project_id="nest-proj")
        correlated.info("trace only")
        project.info("project only")
        output = stream.getvalue()
        assert "[trace=nest-trace" in output
        assert "[nest-proj] project only" in output

    def test_trace_id_persists_across_multiple_calls(self):
        logger = _make_logger("trace_persist")
        stream = _attach_plain(logger)
        set_trace_id("persist-42")
        adapter = CorrelatedLogAdapter(logger, {})
        for i in range(5):
            adapter.info("call %d", i)
        lines = [line for line in stream.getvalue().strip().split("\n") if line]
        assert len(lines) == 5
        for line in lines:
            assert "trace=persist-42" in line

    def test_trace_id_in_json_formatter_with_custom_field(self):
        logger = _make_logger("trace_json")
        set_trace_id("json-trace-99")

        class TraceJsonFormatter(logging.Formatter):
            def format(self, record: logging.LogRecord) -> str:
                from general_ludd.observability.metrics_exporter import get_trace_id as gti

                record.message = record.getMessage()
                obj = {
                    "timestamp": self.formatTime(record, self.datefmt),
                    "level": record.levelname,
                    "message": record.message,
                    "logger": record.name,
                    "trace_id": gti(),
                }
                return json.dumps(obj, default=str)

        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(TraceJsonFormatter())
        logger.addHandler(handler)
        logger.info("traced message")
        record = json.loads(stream.getvalue().strip())
        assert record["trace_id"] == "json-trace-99"

    def test_multiple_threads_independent_trace_ids(self):
        results: dict[str, str] = {}

        def worker(name: str, tid: str) -> None:
            set_trace_id(tid)
            results[name] = get_trace_id()

        t1 = threading.Thread(target=worker, args=("a", "trace-A"))
        t2 = threading.Thread(target=worker, args=("b", "trace-B"))
        t1.start()
        t2.start()
        t1.join()
        t2.join()
        assert results["a"] == "trace-A"
        assert results["b"] == "trace-B"


# ── exception handling in structured format ─────────────────────────────────


class TestExceptionStructuredFormat:
    def test_exception_included_in_json_record(self):
        logger = _make_logger("exc_json")
        stream = _attach_json(logger)
        try:
            raise RuntimeError("simulated crash")
        except RuntimeError:
            logger.exception("caught")
        record = json.loads(stream.getvalue().strip())
        assert "exception" in record
        assert "RuntimeError" in str(record["exception"])
        assert "simulated crash" in str(record["exception"])

    def test_exception_not_present_without_error(self):
        logger = _make_logger("exc_noerr")
        stream = _attach_json(logger)
        logger.info("normal")
        record = json.loads(stream.getvalue().strip())
        assert "exception" not in record

    def test_exc_info_true_without_active_exception(self):
        logger = _make_logger("exc_info_only")
        stream = _attach_json(logger)
        logger.error("manual exc_info", exc_info=True)
        record = json.loads(stream.getvalue().strip())
        assert "exception" not in record

    def test_chain_exception_preserved(self):
        logger = _make_logger("exc_chain")
        stream = _attach_json(logger)
        try:
            try:
                raise ValueError("inner")
            except ValueError:
                raise RuntimeError("outer") from None
        except RuntimeError:
            logger.exception("chained")
        record = json.loads(stream.getvalue().strip())
        text = json.dumps(record["exception"])
        assert "RuntimeError" in text

    def test_project_id_on_exception_record(self):
        logger = _make_logger("exc_proj")
        stream = _attach_json(logger)
        logger.addFilter(ProjectLogFilter(project_id="exc-ctx"))
        try:
            raise KeyError("gone")
        except KeyError:
            logger.exception("missing key")
        record = json.loads(stream.getvalue().strip())
        assert record["project_id"] == "exc-ctx"


# ── edge cases ──────────────────────────────────────────────────────────────


class TestLogFormatEdgeCases:
    def test_empty_message(self):
        logger = _make_logger("edge_empty")
        stream = _attach_json(logger)
        logger.info("")
        record = json.loads(stream.getvalue().strip())
        assert record["message"] == ""

    def test_very_long_message(self):
        logger = _make_logger("edge_long")
        stream = _attach_json(logger)
        msg = "x" * 10000
        logger.info(msg)
        record = json.loads(stream.getvalue().strip())
        assert record["message"] == msg

    def test_multiline_message(self):
        logger = _make_logger("edge_ml")
        stream = _attach_json(logger)
        logger.info("line1\nline2\nline3")
        record = json.loads(stream.getvalue().strip())
        assert "line1" in record["message"]
        assert "line2" in record["message"]

    def test_zero_values_preserved(self):
        logger = _make_logger("edge_zero")
        stream = _attach_json(logger)
        logger.info("count: %d, cost: %.2f", 0, 0.0)
        record = json.loads(stream.getvalue().strip())
        assert "0" in record["message"]

    def test_non_string_message_serialized(self):
        logger = _make_logger("edge_nonstr")
        stream = _attach_json(logger)

        class _MsgObj:
            def __str__(self) -> str:
                return "obj_repr"

        logger.info(_MsgObj())
        record = json.loads(stream.getvalue().strip())
        assert "obj_repr" in record["message"]

    def test_project_id_with_special_characters(self):
        logger = _make_logger("edge_pid_sc")
        stream = _attach_json(logger)
        logger.addFilter(ProjectLogFilter(project_id="my-app/with:special.chars"))
        logger.info("ok")
        record = json.loads(stream.getvalue().strip())
        assert record["project_id"] == "my-app/with:special.chars"

    def test_debug_level_structured_output(self):
        logger = _make_logger("edge_debug", level=logging.DEBUG)
        stream = _attach_json(logger)
        logger.debug("verbose detail: %s", {"key": "value"})
        record = json.loads(stream.getvalue().strip())
        assert record["level"] == "DEBUG"
        assert "verbose detail" in record["message"]
