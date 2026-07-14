"""Structural tests for connectors/local_files.py — JsonlLogSource, SyslogGrepSource."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest

from general_ludd.connectors.local_files import (
    JsonlLogSource,
    SyslogGrepSource,
    _confine,
    _to_float,
)


class TestConfine:
    def test_path_inside_root_passes(self, tmp_path: Path):
        root = str(tmp_path)
        sub = tmp_path / "subdir"
        sub.mkdir()
        file = sub / "file.txt"
        file.write_text("data")
        result = _confine(str(file), root)
        assert os.path.realpath(result) == os.path.realpath(str(file))

    def test_path_equals_root_passes(self, tmp_path: Path):
        root = str(tmp_path)
        result = _confine(root, root)
        assert os.path.realpath(result) == os.path.realpath(root)

    def test_path_outside_root_raises(self, tmp_path: Path):
        root = tmp_path / "allowed"
        root.mkdir()
        outside = tmp_path / "outside.txt"
        outside.write_text("data")
        with pytest.raises(ValueError, match="outside the allowed root"):
            _confine(str(outside), str(root))


class TestToFloat:
    def test_int(self):
        assert _to_float(42) == 42.0

    def test_float(self):
        assert _to_float(3.14) == 3.14

    def test_bool_returns_none(self):
        assert _to_float(True) is None
        assert _to_float(False) is None

    def test_str_returns_none(self):
        assert _to_float("123") is None

    def test_none_returns_none(self):
        assert _to_float(None) is None


class TestJsonlLogSource:
    def test_requires_root(self):
        with pytest.raises(ValueError, match="root"):
            JsonlLogSource({"path": "/some/file"})

    def test_requires_path_or_paths(self, tmp_path: Path):
        root = str(tmp_path)
        with pytest.raises(ValueError, match="path"):
            JsonlLogSource({"root": root})

    def test_constructs_with_single_path(self, tmp_path: Path):
        root = str(tmp_path)
        file = tmp_path / "log.jsonl"
        file.write_text('{"msg": "hello"}\n')
        src = JsonlLogSource({"root": root, "path": str(file)})
        assert src.name == "jsonl_log_source"
        assert src.KIND == "logs"

    def test_constructs_with_paths_list(self, tmp_path: Path):
        root = str(tmp_path)
        file = tmp_path / "a.jsonl"
        file.write_text('{"msg": "a"}\n')
        src = JsonlLogSource({"root": root, "paths": [str(file)]})
        assert src.KIND == "logs"

    def test_path_outside_root_raises(self, tmp_path: Path):
        root = tmp_path / "allowed"
        root.mkdir()
        outside = tmp_path / "log.jsonl"
        outside.write_text('{"msg": "x"}\n')
        with pytest.raises(ValueError, match="outside"):
            JsonlLogSource({"root": str(root), "path": str(outside)})

    def test_health_reports_missing(self, tmp_path: Path):
        root = str(tmp_path)
        missing = str(tmp_path / "nonexistent.jsonl")
        src = JsonlLogSource({"root": root, "path": missing})
        h = src.health()
        assert h["healthy"] is False
        assert h["missing"] == [os.path.realpath(missing)]

    def test_health_reports_healthy(self, tmp_path: Path):
        root = str(tmp_path)
        file = tmp_path / "present.jsonl"
        file.write_text('{"msg": "hi"}\n')
        src = JsonlLogSource({"root": root, "path": str(file)})
        h = src.health()
        assert h["healthy"] is True
        assert h["missing"] == []

    def test_query_reads_normalized_records(self, tmp_path: Path):
        root = str(tmp_path)
        file = tmp_path / "log.jsonl"
        file.write_text('{"ts": "2024-01-01T00:00:00", "level": "info", "message": "hello world"}\n')
        src = JsonlLogSource({"root": root, "path": str(file)})
        records = src.query({})
        assert len(records) == 1
        assert records[0]["message"] == "hello world"
        assert records[0]["level_or_status"] == "info"
        assert records[0]["source"] == "jsonl_log_source"
        assert records[0]["kind"] == "logs"

    def test_query_skips_malformed_lines(self, tmp_path: Path):
        root = str(tmp_path)
        file = tmp_path / "log.jsonl"
        file.write_text('not json\n{"msg": "valid"}\n')
        src = JsonlLogSource({"root": root, "path": str(file)})
        records = src.query({})
        assert len(records) == 1
        assert src.last_malformed_count == 1

    def test_query_skips_non_dict_objects(self, tmp_path: Path):
        root = str(tmp_path)
        file = tmp_path / "log.jsonl"
        file.write_text('123\n["array"]\n{"msg": "ok"}\n')
        src = JsonlLogSource({"root": root, "path": str(file)})
        records = src.query({})
        assert len(records) == 1
        assert src.last_malformed_count == 2

    def test_query_filters_by_regex(self, tmp_path: Path):
        root = str(tmp_path)
        file = tmp_path / "log.jsonl"
        file.write_text('{"message": "error: disk full"}\n{"message": "info: all good"}\n')
        src = JsonlLogSource({"root": root, "path": str(file)})
        records = src.query({"pattern": "error"})
        assert len(records) == 1
        assert "error" in records[0]["message"]

    def test_query_filters_by_level(self, tmp_path: Path):
        root = str(tmp_path)
        file = tmp_path / "log.jsonl"
        file.write_text('{"level": "ERROR", "message": "fail"}\n{"level": "info", "message": "ok"}\n')
        src = JsonlLogSource({"root": root, "path": str(file)})
        records = src.query({"level": "ERROR"})
        assert len(records) == 1
        assert records[0]["message"] == "fail"

    def test_query_filters_by_time_range(self, tmp_path: Path):
        root = str(tmp_path)
        file = tmp_path / "log.jsonl"
        file.write_text('{"ts": "2024-01-01", "message": "old"}\n{"ts": "2024-06-01", "message": "mid"}\n{"ts": "2024-12-01", "message": "new"}\n')
        src = JsonlLogSource({"root": root, "path": str(file)})
        records = src.query({"start": "2024-03-01", "end": "2024-09-01"})
        assert len(records) == 1
        assert records[0]["message"] == "mid"

    def test_query_respects_limit(self, tmp_path: Path):
        root = str(tmp_path)
        file = tmp_path / "log.jsonl"
        file.write_text('\n'.join(f'{{"message": "line{i}"}}' for i in range(10)) + '\n')
        src = JsonlLogSource({"root": root, "path": str(file)})
        records = src.query({"limit": 3})
        assert len(records) == 3

    def test_query_file_not_found_not_fatal(self, tmp_path: Path):
        root = str(tmp_path)
        missing = str(tmp_path / "no_file.jsonl")
        src = JsonlLogSource({"root": root, "path": missing})
        records = src.query({})
        assert records == []


class TestSyslogGrepSource:
    def test_requires_root(self):
        with pytest.raises(ValueError, match="root"):
            SyslogGrepSource({"path": "/some/file"})

    def test_constructs_with_custom_name(self, tmp_path: Path):
        root = str(tmp_path)
        file = tmp_path / "syslog.txt"
        file.write_text("")
        src = SyslogGrepSource({"root": root, "path": str(file), "name": "my-syslog"})
        assert src.KIND == "logs"
        assert src.name == "my-syslog"

    def test_constructs_with_custom_path(self, tmp_path: Path):
        root = str(tmp_path)
        file = tmp_path / "custom.log"
        file.write_text("")
        src = SyslogGrepSource({"root": root, "path": str(file)})
        assert src.KIND == "logs"

    def test_path_outside_root_raises(self, tmp_path: Path):
        root = tmp_path / "allowed"
        root.mkdir()
        outside = tmp_path / "log.txt"
        outside.write_text("")
        with pytest.raises(ValueError, match="outside"):
            SyslogGrepSource({"root": str(root), "path": str(outside)})

    def test_health_missing_file(self, tmp_path: Path):
        root = str(tmp_path)
        missing = str(tmp_path / "nonexistent.log")
        src = SyslogGrepSource({"root": root, "path": missing})
        h = src.health()
        assert h["healthy"] is False

    def test_health_file_present(self, tmp_path: Path):
        root = str(tmp_path)
        file = tmp_path / "syslog"
        file.write_text("")
        src = SyslogGrepSource({"root": root, "path": str(file)})
        h = src.health()
        assert h["healthy"] is True

    def test_query_parses_rfc3164_line(self, tmp_path: Path):
        root = str(tmp_path)
        file = tmp_path / "syslog"
        file.write_text("Jun 15 10:00:00 myhost sshd[1234]: Accepted publickey\n")
        src = SyslogGrepSource({"root": root, "path": str(file)})
        records = src.query({})
        assert len(records) == 1
        assert records[0]["message"] == "Accepted publickey"
        assert records[0]["labels"]["host"] == "myhost"
        assert records[0]["labels"]["process"] == "sshd"
        assert records[0]["labels"]["pid"] == "1234"

    def test_query_skips_non_matching_lines(self, tmp_path: Path):
        root = str(tmp_path)
        file = tmp_path / "syslog"
        file.write_text("some random line\nJun 15 10:00:00 host app: real message\n")
        src = SyslogGrepSource({"root": root, "path": str(file)})
        records = src.query({"pattern": "real message"})
        assert len(records) == 1
        assert records[0]["message"] == "real message"

    def test_query_no_pattern_matches_all(self, tmp_path: Path):
        root = str(tmp_path)
        file = tmp_path / "syslog"
        file.write_text("Jun 15 10:00:00 host app: hello\n")
        src = SyslogGrepSource({"root": root, "path": str(file)})
        records = src.query({})
        assert len(records) == 1

    def test_query_since_filters_time(self, tmp_path: Path):
        root = str(tmp_path)
        file = tmp_path / "syslog"
        file.write_text("Jan 01 00:00:00 host app: old\n")
        src = SyslogGrepSource({"root": root, "path": str(file)})
        records = src.query({"since": "2099-01-01"})
        assert len(records) == 0

    def test_query_respects_limit(self, tmp_path: Path):
        root = str(tmp_path)
        file = tmp_path / "syslog"
        lines = "\n".join(f"Jun 15 10:00:01 host app: msg{i}" for i in range(10))
        file.write_text(lines + "\n")
        src = SyslogGrepSource({"root": root, "path": str(file)})
        records = src.query({"limit": 3})
        assert len(records) == 3

    def test_query_file_not_found_returns_empty(self, tmp_path: Path):
        root = str(tmp_path)
        missing = str(tmp_path / "no_file.log")
        src = SyslogGrepSource({"root": root, "path": missing})
        records = src.query({})
        assert records == []

    def test_non_syslog_line_gets_empty_ts(self, tmp_path: Path):
        root = str(tmp_path)
        file = tmp_path / "syslog"
        file.write_text("this is not syslog format at all\n")
        src = SyslogGrepSource({"root": root, "path": str(file)})
        records = src.query({})
        assert len(records) == 1
        assert records[0]["ts"] == ""
        assert records[0]["message"] == "this is not syslog format at all"
