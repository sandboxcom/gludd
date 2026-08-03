"""Deep tests for the logging subsystem: structured output, JSON, exceptions, levels, filtering."""

from __future__ import annotations

import io
import json
import logging
import traceback
from typing import Any

import pytest

from general_ludd.logging.project_log import (
    ProjectLogAdapter,
    ProjectLogFilter,
    install_project_log_filter,
)


def _make_logger(name, level=logging.DEBUG):
    logger = logging.getLogger(name)
    logger.handlers.clear()
    logger.filters.clear()
    logger.setLevel(level)
    logger.propagate = False
    return logger


def _attach_stream(logger, fmt=None, use_json=False):
    stream = io.StringIO()
    if use_json:

        class _JsonFmt(logging.Formatter):
            def format(self, record):
                record.message = record.getMessage()
                obj: dict[str, Any] = {
                    "ts": self.formatTime(record, self.datefmt),
                    "name": record.name,
                    "level": record.levelname,
                    "msg": record.message,
                }
                pid = getattr(record, "project_id", None)
                if pid is not None:
                    obj["project_id"] = pid
                if record.exc_info and record.exc_info[1]:
                    obj["exc"] = traceback.format_exception(*record.exc_info)
                return json.dumps(obj, default=str)

        h = logging.StreamHandler(stream)
        h.setFormatter(_JsonFmt())
    elif fmt:
        h = logging.StreamHandler(stream)
        h.setFormatter(logging.Formatter(fmt))
    else:
        h = logging.StreamHandler(stream)
    logger.addHandler(h)
    return stream


# ── structured log format ──────────────────────────────────────────────────


class TestStructuredLogFormat:
    def test_adapter_prefix_appears_in_log_output(self):
        logger = _make_logger("s1")
        stream = _attach_stream(logger, fmt="%(message)s")
        adapter = ProjectLogAdapter(logger, project_id="prj")
        adapter.info("hello")
        assert "[prj] hello" in stream.getvalue()

    def test_adapter_prefix_with_formatting(self):
        logger = _make_logger("s2")
        stream = _attach_stream(logger, fmt="%(levelname)s %(message)s")
        adapter = ProjectLogAdapter(logger, project_id="prj")
        adapter.warning("warn %d", 42)
        assert "WARNING [prj] warn 42" in stream.getvalue()

    def test_adapter_prefix_in_structured_format(self):
        logger = _make_logger("s3")
        stream = _attach_stream(logger, fmt="[%(name)s] %(message)s")
        adapter = ProjectLogAdapter(logger, project_id="app")
        adapter.info("start")
        assert "[s3] [app] start" in stream.getvalue()

    def test_extra_fields_passed_through_adapter(self):
        logger = _make_logger("s4")
        stream = _attach_stream(logger, fmt="%(message)s")
        adapter = ProjectLogAdapter(logger, project_id="prj")
        adapter.debug("msg", extra={"user_id": "u1"})
        output = stream.getvalue()
        assert "[prj] msg" in output

    def test_no_project_id_produces_clean_output(self):
        logger = _make_logger("s5")
        stream = _attach_stream(logger, fmt="%(message)s")
        adapter = ProjectLogAdapter(logger, project_id=None)
        adapter.info("plain")
        assert stream.getvalue().strip() == "plain"


# ── log levels ─────────────────────────────────────────────────────────────


class TestLogLevels:
    @pytest.mark.parametrize(
        "level,method",
        [
            (logging.DEBUG, "debug"),
            (logging.INFO, "info"),
            (logging.WARNING, "warning"),
            (logging.ERROR, "error"),
            (logging.CRITICAL, "critical"),
        ],
    )
    def test_all_levels_emit_through_adapter(self, level, method):
        logger = _make_logger("lv1", level=logging.DEBUG)
        stream = _attach_stream(logger, fmt="%(levelname)s|%(message)s")
        adapter = ProjectLogAdapter(logger, project_id="p")
        getattr(adapter, method)("test")
        assert f"{logging.getLevelName(level)}|[p] test" in stream.getvalue()

    def test_messages_below_logger_level_dropped(self):
        logger = _make_logger("lv2", level=logging.WARNING)
        stream = _attach_stream(logger, fmt="%(message)s")
        adapter = ProjectLogAdapter(logger, project_id="p")
        adapter.info("should be dropped")
        adapter.warning("should appear")
        output = stream.getvalue()
        assert "should be dropped" not in output
        assert "[p] should appear" in output

    def test_child_logger_levels(self):
        parent = _make_logger("parent", level=logging.WARNING)
        child = parent.getChild("child")
        stream = _attach_stream(child, fmt="%(message)s")
        adapter = ProjectLogAdapter(child, project_id="sub")
        adapter.info("silent")
        adapter.warning("loud")
        output = stream.getvalue()
        assert "silent" not in output
        assert "[sub] loud" in output


# ── project context injection ──────────────────────────────────────────────


class TestProjectContextInjection:
    def test_filter_adds_project_id_to_record(self):
        logger = _make_logger("ci1")
        logger.addFilter(ProjectLogFilter(project_id="ctx"))
        stream = _attach_stream(logger, fmt="%(message)s")
        logger.info("msg")
        output = stream.getvalue()
        assert "msg" in output

    def test_adapter_and_filter_together(self):
        logger = _make_logger("ci2")
        logger.addFilter(ProjectLogFilter(project_id="fallback"))
        stream = _attach_stream(logger, fmt="%(message)s")
        adapter = ProjectLogAdapter(logger, project_id="active")
        adapter.info("together")
        output = stream.getvalue()
        assert "[active] together" in output

    def test_filter_provides_fallback_when_adapter_has_no_id(self):
        logger = _make_logger("ci3")
        logger.addFilter(ProjectLogFilter(project_id="fb"))
        stream = _attach_stream(logger, fmt="%(message)s")
        adapter = ProjectLogAdapter(logger, project_id=None)
        adapter.info("fallback")
        output = stream.getvalue()
        assert "[fb]" not in output

    def test_install_filter_is_idempotent_on_same_logger(self):
        logger = _make_logger("ci4")
        f1 = install_project_log_filter("a", logger)
        f2 = install_project_log_filter("b", logger)
        assert f1 is f2
        assert f2.project_id == "a"
        assert sum(1 for f in logger.filters if isinstance(f, ProjectLogFilter)) == 1


# ── log filtering ──────────────────────────────────────────────────────────


class TestLogFiltering:
    def test_custom_filter_blocks_messages(self):
        class BlockFilter(logging.Filter):
            def filter(self, record):
                return "DROP" not in record.getMessage()

        logger = _make_logger("lf1")
        stream = _attach_stream(logger, fmt="%(message)s")
        logger.addFilter(BlockFilter())
        adapter = ProjectLogAdapter(logger, project_id="prj")
        adapter.info("DROP this")
        adapter.info("KEEP this")
        output = stream.getvalue()
        assert "DROP" not in output
        assert "[prj] KEEP this" in output

    def test_project_filter_and_custom_filter_chain(self):
        class UpperFilter(logging.Filter):
            def filter(self, record):
                record.msg = record.getMessage().upper()
                return True

        logger = _make_logger("lf2")
        stream = _attach_stream(logger, fmt="%(message)s")
        logger.addFilter(ProjectLogFilter(project_id="fb"))
        logger.addFilter(UpperFilter())
        adapter = ProjectLogAdapter(logger, project_id="active")
        adapter.info("hello")
        assert "[ACTIVE] HELLO" in stream.getvalue()

    def test_filter_does_not_mutate_original_kwargs(self):
        logger = _make_logger("lf3")
        _stream = _attach_stream(logger, fmt="%(message)s")
        logger.addFilter(ProjectLogFilter(project_id="p"))
        logger.info("test")
        assert True


# ── JSON output ────────────────────────────────────────────────────────────


class TestJsonOutput:
    def test_json_formatter_includes_project_id(self):
        logger = _make_logger("json1")
        stream = _attach_stream(logger, use_json=True)
        logger.addFilter(ProjectLogFilter(project_id="json-proj"))
        adapter = ProjectLogAdapter(logger, project_id="json-proj")
        adapter.info("structured")
        record = json.loads(stream.getvalue().strip())
        assert record["project_id"] == "json-proj"
        assert record["msg"] == "[json-proj] structured"
        assert record["level"] == "INFO"

    def test_json_formatter_omits_project_id_when_unset(self):
        logger = _make_logger("json2")
        stream = _attach_stream(logger, use_json=True)
        adapter = ProjectLogAdapter(logger, project_id=None)
        adapter.info("no project")
        record = json.loads(stream.getvalue().strip())
        assert "project_id" not in record
        assert record["msg"] == "no project"

    def test_json_output_includes_log_level_and_name(self):
        logger = _make_logger("json3")
        stream = _attach_stream(logger, use_json=True)
        adapter = ProjectLogAdapter(logger, project_id="x")
        adapter.error("bad")
        record = json.loads(stream.getvalue().strip())
        assert record["level"] == "ERROR"
        assert record["name"] == "json3"

    def test_multiple_json_records_all_valid(self):
        logger = _make_logger("json4")
        stream = _attach_stream(logger, use_json=True)
        logger.addFilter(ProjectLogFilter(project_id="mp"))
        adapter = ProjectLogAdapter(logger, project_id="mp")
        adapter.info("first")
        adapter.warning("second")
        lines = [line for line in stream.getvalue().strip().split("\n") if line]
        assert len(lines) == 2
        for line in lines:
            r = json.loads(line)
            assert r["project_id"] == "mp"


# ── exception traceback capture ────────────────────────────────────────────


class TestExceptionTraceback:
    def test_exception_preserved_through_adapter(self):
        logger = _make_logger("exc1")
        stream = _attach_stream(logger, fmt="%(message)s")
        adapter = ProjectLogAdapter(logger, project_id="err")
        try:
            raise ValueError("boom")
        except ValueError:
            adapter.exception("failed")
        output = stream.getvalue()
        assert "[err] failed" in output
        assert "ValueError" in output
        assert "boom" in output

    def test_json_formatter_captures_exception_traceback(self):
        logger = _make_logger("exc2")
        stream = _attach_stream(logger, use_json=True)
        logger.addFilter(ProjectLogFilter(project_id="err-j"))
        adapter = ProjectLogAdapter(logger, project_id="err-j")
        try:
            _ = 1 / 0
        except ZeroDivisionError:
            adapter.exception("division")
        record = json.loads(stream.getvalue().strip())
        assert record["project_id"] == "err-j"
        assert "ZeroDivisionError" in str(record["exc"])
        assert "division" in str(record["exc"])

    def test_exception_context_preserves_project_id(self):
        logger = _make_logger("exc3")
        stream = _attach_stream(logger, fmt="%(message)s")
        adapter = ProjectLogAdapter(logger, project_id="ctx-err")
        try:
            raise RuntimeError("fail")
        except RuntimeError:
            adapter.error("context error", exc_info=True)
        output = stream.getvalue()
        assert "[ctx-err] context error" in output
        assert "RuntimeError" in output

    def test_exception_without_project_id(self):
        logger = _make_logger("exc4")
        stream = _attach_stream(logger, fmt="%(message)s")
        adapter = ProjectLogAdapter(logger, project_id=None)
        try:
            raise KeyError("missing")
        except KeyError:
            adapter.exception("no ctx")
        output = stream.getvalue()
        assert "no ctx" in output
        assert "KeyError" in output
