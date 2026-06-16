"""Unit tests for the local-file observability connectors.

These connectors read log-shaped data off the local filesystem and normalize
it into the canonical observability record shape. They are deliberately
self-contained (no imports from the connector package base/__init__) so they
can be exercised in isolation.

Security invariant under test: every path the connector touches MUST resolve
(via ``os.path.realpath``) to a location *inside* a configured allowed root.
A path that escapes the root via ``..`` or an absolute path outside the root
MUST be refused — the connector must never read an arbitrary host file.

All fixtures use real temp files (``tmp_path``); no network, no subprocess.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from general_ludd.connectors.local_files import JsonlLogSource, SyslogGrepSource

# --------------------------------------------------------------------------- #
# Helpers / fixtures
# --------------------------------------------------------------------------- #

NORMALIZED_KEYS = {
    "ts",
    "source",
    "kind",
    "level_or_status",
    "message",
    "value",
    "labels",
    "raw",
}


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture
def jsonl_file(tmp_path: Path) -> Path:
    """A JSONL log file with mixed levels, a malformed line, and varied fields."""
    lines = [
        json.dumps(
            {
                "ts": "2026-06-15T10:00:00Z",
                "level": "INFO",
                "message": "service started",
                "service": "api",
            }
        ),
        json.dumps(
            {
                "time": "2026-06-15T10:00:01Z",
                "severity": "ERROR",
                "msg": "database connection refused",
                "host": "db-1",
                "code": 500,
            }
        ),
        "this is not json {{{",  # malformed — must be skipped + counted
        json.dumps(
            {
                "@timestamp": "2026-06-15T10:00:02Z",
                "level": "WARN",
                "message": "retrying request",
                "attempt": 2,
            }
        ),
        "",  # blank line — skipped, not counted as malformed
    ]
    return _write(tmp_path / "app.jsonl", "\n".join(lines) + "\n")


@pytest.fixture
def syslog_file(tmp_path: Path) -> Path:
    """A plain syslog (RFC3164-ish) text file."""
    lines = [
        "Jun 15 10:00:00 host1 sshd[1234]: Accepted password for root",
        "Jun 15 10:00:05 host2 cron[55]: pam_unix(cron:session): session opened",
        "Jun 15 10:01:00 host1 kernel: out of memory: killed process 999",
        "a line that does not match the syslog prefix at all",
    ]
    return _write(tmp_path / "syslog", "\n".join(lines) + "\n")


# --------------------------------------------------------------------------- #
# Shared contract
# --------------------------------------------------------------------------- #


class TestSharedContract:
    def test_kind_is_logs(self) -> None:
        assert JsonlLogSource.KIND == "logs"
        assert SyslogGrepSource.KIND == "logs"

    def test_name_attr(self, jsonl_file: Path, syslog_file: Path) -> None:
        j = JsonlLogSource({"path": str(jsonl_file), "root": str(jsonl_file.parent)})
        s = SyslogGrepSource({"path": str(syslog_file), "root": str(syslog_file.parent)})
        assert isinstance(j.name, str) and j.name
        assert isinstance(s.name, str) and s.name

    def test_health_never_raises_on_missing_file(self, tmp_path: Path) -> None:
        j = JsonlLogSource({"path": str(tmp_path / "nope.jsonl"), "root": str(tmp_path)})
        s = SyslogGrepSource({"path": str(tmp_path / "nope.log"), "root": str(tmp_path)})
        jh = j.health()
        sh = s.health()
        assert isinstance(jh, dict)
        assert isinstance(sh, dict)
        # health reports unhealthy/degraded, but never raises
        assert jh["healthy"] is False
        assert sh["healthy"] is False

    def test_health_ok_when_file_present(self, jsonl_file: Path, syslog_file: Path) -> None:
        j = JsonlLogSource({"path": str(jsonl_file), "root": str(jsonl_file.parent)})
        s = SyslogGrepSource({"path": str(syslog_file), "root": str(syslog_file.parent)})
        assert j.health()["healthy"] is True
        assert s.health()["healthy"] is True


# --------------------------------------------------------------------------- #
# TASK A — JsonlLogSource
# --------------------------------------------------------------------------- #


class TestJsonlLogSource:
    def test_normalized_record_shape(self, jsonl_file: Path) -> None:
        src = JsonlLogSource({"path": str(jsonl_file), "root": str(jsonl_file.parent)})
        records = src.query({})
        assert records, "expected at least one record"
        for rec in records:
            assert set(rec.keys()) >= NORMALIZED_KEYS
            assert rec["kind"] == "logs"

    def test_ts_field_parsed_from_alternatives(self, jsonl_file: Path) -> None:
        src = JsonlLogSource({"path": str(jsonl_file), "root": str(jsonl_file.parent)})
        records = src.query({})
        timestamps = [r["ts"] for r in records]
        # ts pulled from "ts", "time", and "@timestamp" respectively
        assert "2026-06-15T10:00:00Z" in timestamps
        assert "2026-06-15T10:00:01Z" in timestamps
        assert "2026-06-15T10:00:02Z" in timestamps

    def test_level_pulled_from_level_or_severity(self, jsonl_file: Path) -> None:
        src = JsonlLogSource({"path": str(jsonl_file), "root": str(jsonl_file.parent)})
        levels = {r["level_or_status"] for r in src.query({})}
        assert "INFO" in levels
        assert "ERROR" in levels  # from "severity"
        assert "WARN" in levels

    def test_message_pulled_from_message_or_msg(self, jsonl_file: Path) -> None:
        src = JsonlLogSource({"path": str(jsonl_file), "root": str(jsonl_file.parent)})
        messages = {r["message"] for r in src.query({})}
        assert "service started" in messages
        assert "database connection refused" in messages  # from "msg"

    def test_labels_contain_remaining_fields(self, jsonl_file: Path) -> None:
        src = JsonlLogSource({"path": str(jsonl_file), "root": str(jsonl_file.parent)})
        err = next(r for r in src.query({}) if r["level_or_status"] == "ERROR")
        # "host"/"code" were not ts/level/message — they land in labels
        assert err["labels"].get("host") == "db-1"
        assert err["labels"].get("code") == 500
        # the parsed-out canonical fields must NOT leak into labels
        assert "msg" not in err["labels"]
        assert "severity" not in err["labels"]

    def test_raw_is_the_original_object(self, jsonl_file: Path) -> None:
        src = JsonlLogSource({"path": str(jsonl_file), "root": str(jsonl_file.parent)})
        info = next(r for r in src.query({}) if r["message"] == "service started")
        assert info["raw"]["service"] == "api"
        assert info["raw"]["level"] == "INFO"

    def test_source_is_the_connector_name(self, jsonl_file: Path) -> None:
        src = JsonlLogSource({"path": str(jsonl_file), "root": str(jsonl_file.parent)})
        for rec in src.query({}):
            assert rec["source"] == src.name

    def test_pattern_regex_filter(self, jsonl_file: Path) -> None:
        src = JsonlLogSource({"path": str(jsonl_file), "root": str(jsonl_file.parent)})
        records = src.query({"pattern": r"connection refused"})
        assert len(records) == 1
        assert records[0]["message"] == "database connection refused"

    def test_level_filter(self, jsonl_file: Path) -> None:
        src = JsonlLogSource({"path": str(jsonl_file), "root": str(jsonl_file.parent)})
        records = src.query({"level": "ERROR"})
        assert len(records) == 1
        assert records[0]["level_or_status"] == "ERROR"

    def test_level_filter_case_insensitive(self, jsonl_file: Path) -> None:
        src = JsonlLogSource({"path": str(jsonl_file), "root": str(jsonl_file.parent)})
        records = src.query({"level": "error"})
        assert len(records) == 1

    def test_limit(self, jsonl_file: Path) -> None:
        src = JsonlLogSource({"path": str(jsonl_file), "root": str(jsonl_file.parent)})
        records = src.query({"limit": 2})
        assert len(records) == 2

    def test_start_end_time_window(self, jsonl_file: Path) -> None:
        src = JsonlLogSource({"path": str(jsonl_file), "root": str(jsonl_file.parent)})
        records = src.query(
            {"start": "2026-06-15T10:00:01Z", "end": "2026-06-15T10:00:01Z"}
        )
        # only the 10:00:01 record falls in the inclusive window
        assert len(records) == 1
        assert records[0]["ts"] == "2026-06-15T10:00:01Z"

    def test_malformed_line_skipped_and_counted(self, jsonl_file: Path) -> None:
        src = JsonlLogSource({"path": str(jsonl_file), "root": str(jsonl_file.parent)})
        records = src.query({})
        # 3 well-formed JSON objects (malformed + blank excluded)
        assert len(records) == 3
        # the malformed-line count surfaces in a summary on the connector
        assert src.last_malformed_count == 1

    def test_multiple_paths(self, tmp_path: Path) -> None:
        a = _write(tmp_path / "a.jsonl", json.dumps({"message": "from a"}) + "\n")
        b = _write(tmp_path / "b.jsonl", json.dumps({"message": "from b"}) + "\n")
        src = JsonlLogSource({"paths": [str(a), str(b)], "root": str(tmp_path)})
        messages = {r["message"] for r in src.query({})}
        assert messages == {"from a", "from b"}

    # --- path confinement ---------------------------------------------------

    def test_path_outside_root_is_refused(self, tmp_path: Path) -> None:
        root = tmp_path / "allowed"
        root.mkdir()
        outside = _write(tmp_path / "secret.jsonl", json.dumps({"message": "x"}) + "\n")
        with pytest.raises(ValueError):
            JsonlLogSource({"path": str(outside), "root": str(root)})

    def test_dotdot_escape_is_refused(self, tmp_path: Path) -> None:
        root = tmp_path / "allowed"
        root.mkdir()
        _write(tmp_path / "secret.jsonl", json.dumps({"message": "x"}) + "\n")
        escape = str(root / ".." / "secret.jsonl")
        with pytest.raises(ValueError):
            JsonlLogSource({"path": escape, "root": str(root)})

    def test_absolute_outside_root_is_refused(self, tmp_path: Path) -> None:
        root = tmp_path / "allowed"
        root.mkdir()
        with pytest.raises(ValueError):
            JsonlLogSource({"path": "/etc/passwd", "root": str(root)})


# --------------------------------------------------------------------------- #
# TASK B — SyslogGrepSource
# --------------------------------------------------------------------------- #


class TestSyslogGrepSource:
    def test_default_path(self) -> None:
        # default path is /var/log/syslog; with root="/" it is in-root but the
        # file may not exist — health must not raise.
        src = SyslogGrepSource({"root": "/"})
        assert src.health()["healthy"] in (True, False)

    def test_normalized_record_shape(self, syslog_file: Path) -> None:
        src = SyslogGrepSource({"path": str(syslog_file), "root": str(syslog_file.parent)})
        records = src.query({"pattern": r".*"})
        assert records
        for rec in records:
            assert set(rec.keys()) >= NORMALIZED_KEYS
            assert rec["kind"] == "logs"
            assert rec["level_or_status"] == ""

    def test_regex_filter(self, syslog_file: Path) -> None:
        src = SyslogGrepSource({"path": str(syslog_file), "root": str(syslog_file.parent)})
        records = src.query({"pattern": r"out of memory"})
        assert len(records) == 1
        assert "out of memory" in records[0]["message"]

    def test_prefix_parsed_into_labels(self, syslog_file: Path) -> None:
        src = SyslogGrepSource({"path": str(syslog_file), "root": str(syslog_file.parent)})
        rec = next(r for r in src.query({"pattern": r"Accepted password"}))
        assert rec["labels"]["host"] == "host1"
        assert rec["labels"]["process"] == "sshd"
        assert rec["message"] == "Accepted password for root"

    def test_message_is_the_tail(self, syslog_file: Path) -> None:
        src = SyslogGrepSource({"path": str(syslog_file), "root": str(syslog_file.parent)})
        rec = next(r for r in src.query({"pattern": r"session opened"}))
        # process with [pid] -> process name only in labels
        assert rec["labels"]["process"] == "cron"
        assert "session opened" in rec["message"]

    def test_ts_best_effort_parsed(self, syslog_file: Path) -> None:
        src = SyslogGrepSource({"path": str(syslog_file), "root": str(syslog_file.parent)})
        rec = next(r for r in src.query({"pattern": r"Accepted password"}))
        # best-effort ISO timestamp using the current year
        assert str(rec["ts"]).startswith(f"{__import__('datetime').date.today().year}-06-15")

    def test_raw_is_the_original_line(self, syslog_file: Path) -> None:
        src = SyslogGrepSource({"path": str(syslog_file), "root": str(syslog_file.parent)})
        rec = next(r for r in src.query({"pattern": r"Accepted password"}))
        assert rec["raw"].startswith("Jun 15 10:00:00 host1 sshd")

    def test_nonmatching_prefix_still_returned_with_empty_labels(self, syslog_file: Path) -> None:
        src = SyslogGrepSource({"path": str(syslog_file), "root": str(syslog_file.parent)})
        rec = next(r for r in src.query({"pattern": r"does not match the syslog prefix"}))
        # unparseable prefix => message is the whole line, host/process empty
        assert rec["labels"].get("host", "") == ""
        assert rec["labels"].get("process", "") == ""
        assert "does not match" in rec["message"]

    def test_limit(self, syslog_file: Path) -> None:
        src = SyslogGrepSource({"path": str(syslog_file), "root": str(syslog_file.parent)})
        records = src.query({"pattern": r".*", "limit": 2})
        assert len(records) == 2

    def test_no_subprocess_used(self) -> None:
        # The implementation must NOT shell out (no subprocess import in module).
        import inspect

        import general_ludd.connectors.local_files as mod

        source = inspect.getsource(mod)
        assert "subprocess" not in source
        assert "shell=True" not in source
        assert "os.system" not in source

    # --- path confinement ---------------------------------------------------

    def test_path_outside_root_is_refused(self, tmp_path: Path) -> None:
        root = tmp_path / "allowed"
        root.mkdir()
        outside = _write(tmp_path / "secret.log", "Jun 15 10:00:00 h p: x\n")
        with pytest.raises(ValueError):
            SyslogGrepSource({"path": str(outside), "root": str(root)})

    def test_dotdot_escape_is_refused(self, tmp_path: Path) -> None:
        root = tmp_path / "allowed"
        root.mkdir()
        _write(tmp_path / "secret.log", "Jun 15 10:00:00 h p: x\n")
        escape = str(root / ".." / "secret.log")
        with pytest.raises(ValueError):
            SyslogGrepSource({"path": escape, "root": str(root)})

    def test_absolute_outside_root_is_refused(self, tmp_path: Path) -> None:
        root = tmp_path / "allowed"
        root.mkdir()
        with pytest.raises(ValueError):
            SyslogGrepSource({"path": "/etc/passwd", "root": str(root)})

    def test_symlink_escape_is_refused(self, tmp_path: Path) -> None:
        # a symlink inside root pointing outside must be caught by realpath
        root = tmp_path / "allowed"
        root.mkdir()
        target = _write(tmp_path / "outside.log", "Jun 15 10:00:00 h p: x\n")
        link = root / "link.log"
        os.symlink(target, link)
        with pytest.raises(ValueError):
            SyslogGrepSource({"path": str(link), "root": str(root)})
