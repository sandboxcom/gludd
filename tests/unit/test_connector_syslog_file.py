"""Unit tests for SyslogFileSource (temp-file parse + path confinement)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from general_ludd.connectors.syslog_file import (
    PathConfinementError,
    SyslogFileSource,
)

RFC3164_SAMPLE = (
    "Oct 11 22:14:15 myhost sshd[1234]: Accepted password for alice\n"
    "<11>Oct 11 22:14:16 myhost kernel: Out of memory\n"
)
RFC5424_SAMPLE = (
    "<34>1 2026-06-16T22:14:15.003Z myhost su 567 ID47 - failed login\n"
)


def _write(root: Path, name: str, content: str) -> Path:
    p = root / name
    p.write_text(content)
    return p


def test_kind_and_name_attrs(tmp_path: Path) -> None:
    src = SyslogFileSource({"root": str(tmp_path), "name": "sl"})
    assert SyslogFileSource.KIND == "logs"
    assert src.name == "sl"


def test_requires_root() -> None:
    with pytest.raises(ValueError):
        SyslogFileSource({})


def test_health_ok_for_dir(tmp_path: Path) -> None:
    src = SyslogFileSource({"root": str(tmp_path)})
    h = src.health()
    assert h["ok"] is True
    assert "ok" in h["detail"]


def test_health_false_for_missing_root(tmp_path: Path) -> None:
    missing = tmp_path / "nope"
    src = SyslogFileSource({"root": str(missing)})
    h = src.health()
    assert h["ok"] is False
    # health() must not raise even on a bad root.


def test_parse_rfc3164(tmp_path: Path) -> None:
    _write(tmp_path, "syslog", RFC3164_SAMPLE)
    src = SyslogFileSource({"root": str(tmp_path)})
    recs = src.query({"path": "syslog"})
    assert len(recs) == 2
    first = recs[0]
    assert first["kind"] == "logs"
    assert first["source"] == "syslog_file"
    assert first["labels"]["host"] == "myhost"
    assert first["labels"]["process"] == "sshd"
    assert first["labels"]["pid"] == "1234"
    assert "Accepted password" in first["message"]
    assert first["value"] is None
    assert first["raw"].startswith("Oct 11")
    # Second line has PRI <11> -> facility kern (1>>3=1), severity error(11&7=3).
    second = recs[1]
    assert second["labels"]["severity"] == "error"
    assert second["level_or_status"] == "error"
    assert second["labels"]["facility"] == "user"  # 11>>3 == 1 -> 'user'


def test_parse_rfc5424(tmp_path: Path) -> None:
    _write(tmp_path, "rfc5424.log", RFC5424_SAMPLE)
    src = SyslogFileSource({"root": str(tmp_path)})
    recs = src.query({"path": "rfc5424.log"})
    assert len(recs) == 1
    r = recs[0]
    assert r["labels"]["host"] == "myhost"
    assert r["labels"]["process"] == "su"
    assert r["labels"]["pid"] == "567"
    assert r["ts"] == "2026-06-16T22:14:15.003Z"
    assert r["message"] == "failed login"
    # PRI 34 -> severity 34&7=2 critical, facility 34>>3=4 auth.
    assert r["labels"]["severity"] == "critical"
    assert r["labels"]["facility"] == "auth"


def test_all_records_have_normalized_keys(tmp_path: Path) -> None:
    _write(tmp_path, "s", RFC3164_SAMPLE)
    src = SyslogFileSource({"root": str(tmp_path)})
    required = {"ts", "source", "kind", "level_or_status", "message", "value", "labels", "raw"}
    for rec in src.query({"path": "s"}):
        assert required <= set(rec)


def test_limit_caps_records(tmp_path: Path) -> None:
    _write(tmp_path, "s", RFC3164_SAMPLE)
    src = SyslogFileSource({"root": str(tmp_path)})
    recs = src.query({"path": "s", "limit": 1})
    assert len(recs) == 1


def test_pattern_filters(tmp_path: Path) -> None:
    _write(tmp_path, "s", RFC3164_SAMPLE)
    src = SyslogFileSource({"root": str(tmp_path)})
    recs = src.query({"path": "s", "pattern": "Out of memory"})
    assert len(recs) == 1
    assert "Out of memory" in recs[0]["message"]


def test_path_refused_absolute_outside_root(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "secret.txt"
    outside.write_text("Oct 11 22:14:15 h p: secret\n")
    src = SyslogFileSource({"root": str(root)})
    with pytest.raises(PathConfinementError):
        src.query({"path": str(outside)})


def test_path_refused_dotdot_escape(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (tmp_path / "secret.txt").write_text("x\n")
    src = SyslogFileSource({"root": str(root)})
    with pytest.raises(PathConfinementError):
        src.query({"path": "../secret.txt"})


def test_path_refused_symlink_escape(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    target = tmp_path / "outside.log"
    target.write_text("Oct 11 22:14:15 h p: outside\n")
    link = root / "link.log"
    try:
        os.symlink(target, link)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not supported on this platform")
    src = SyslogFileSource({"root": str(root)})
    with pytest.raises(PathConfinementError):
        src.query({"path": "link.log"})


def test_since_filters_lexically(tmp_path: Path) -> None:
    # 'since' is a cheap lexical prefix filter on raw lines.
    content = "Aaa first\nZzz second\n"
    _write(tmp_path, "s", content)
    src = SyslogFileSource({"root": str(tmp_path)})
    recs = src.query({"path": "s", "since": "B"})
    assert len(recs) == 1
    assert recs[0]["raw"].startswith("Zzz")


def test_configured_path_can_be_used_without_explicit_root(tmp_path: Path) -> None:
    path = _write(tmp_path, "configured.log", RFC3164_SAMPLE)
    src = SyslogFileSource({"path": str(path)})
    assert src.health()["ok"] is True
    assert len(src.query({})) == 2
