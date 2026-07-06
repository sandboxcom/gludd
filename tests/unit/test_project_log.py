"""Unit tests for project_log logging adapter and filter."""

from __future__ import annotations

import logging

from general_ludd.logging.project_log import (
    ProjectLogAdapter,
    ProjectLogFilter,
    install_project_log_filter,
)


class TestProjectLogAdapter:
    def test_process_adds_project_id_prefix(self):
        logger = logging.getLogger("test_adapter")
        adapter = ProjectLogAdapter(logger, project_id="proj-1")
        msg, _kwargs = adapter.process("hello", {})
        assert msg == "[proj-1] hello"

    def test_process_no_project_id_is_passthrough(self):
        logger = logging.getLogger("test_adapter_none")
        adapter = ProjectLogAdapter(logger, project_id=None)
        msg, _kwargs = adapter.process("hello", {})
        assert msg == "hello"

    def test_process_empty_project_id_is_passthrough(self):
        logger = logging.getLogger("test_adapter_empty")
        adapter = ProjectLogAdapter(logger, project_id="")
        msg, _kwargs = adapter.process("hello", {})
        assert msg == "hello"

    def test_project_id_stored(self):
        logger = logging.getLogger("test_adapter_store")
        adapter = ProjectLogAdapter(logger, project_id="p123")
        assert adapter.project_id == "p123"


class TestProjectLogFilter:
    def test_filter_sets_project_id_on_record(self):
        flt = ProjectLogFilter(project_id="proj-x")
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=1,
            msg="test", args=(), exc_info=None,
        )
        assert flt.filter(record) is True
        assert record.project_id == "proj-x"

    def test_filter_does_not_overwrite_existing_project_id(self):
        flt = ProjectLogFilter(project_id="new-id")
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=1,
            msg="test", args=(), exc_info=None,
        )
        record.project_id = "existing-id"
        assert flt.filter(record) is True
        assert record.project_id == "existing-id"

    def test_filter_none_project_id(self):
        flt = ProjectLogFilter(project_id=None)
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=1,
            msg="test", args=(), exc_info=None,
        )
        assert flt.filter(record) is True
        assert record.project_id is None

    def test_filter_always_returns_true(self):
        flt = ProjectLogFilter(project_id="p")
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=1,
            msg="test", args=(), exc_info=None,
        )
        assert flt.filter(record) is True


class TestInstallProjectLogFilter:
    def test_installs_filter_on_logger(self):
        logger = logging.getLogger("test_install")
        for f in list(logger.filters):
            if isinstance(f, ProjectLogFilter):
                logger.removeFilter(f)

        flt = install_project_log_filter(project_id="p1", logger=logger)
        assert isinstance(flt, ProjectLogFilter)
        assert flt.project_id == "p1"
        assert any(isinstance(f, ProjectLogFilter) for f in logger.filters)

    def test_idempotent_returns_existing(self):
        logger = logging.getLogger("test_idempotent")
        for f in list(logger.filters):
            if isinstance(f, ProjectLogFilter):
                logger.removeFilter(f)

        flt1 = install_project_log_filter(project_id="a", logger=logger)
        flt2 = install_project_log_filter(project_id="b", logger=logger)
        assert flt1 is flt2
        assert flt2.project_id == "a"

    def test_uses_root_logger_when_none_specified(self):
        root = logging.getLogger()
        flt = install_project_log_filter(project_id="root-test")
        assert isinstance(flt, ProjectLogFilter)
        assert any(isinstance(f, ProjectLogFilter) for f in root.filters)
