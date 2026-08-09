"""Regression tests for bounded, offline OpenCode database maintenance."""

from __future__ import annotations

import importlib
import os
import sqlite3
import subprocess
import time
from contextlib import closing
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "opencode_db_maintenance.py"
MAKEFILE = ROOT / "Makefile"


def _load_maintenance() -> ModuleType:
    assert SCRIPT.is_file()
    return importlib.import_module("scripts.opencode_db_maintenance")


def _fixture_db(path: Path, *, old_ms: int, recent_ms: int) -> None:
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA foreign_keys=ON")
    connection.executescript(
        """
        CREATE TABLE session (
            id TEXT PRIMARY KEY,
            parent_id TEXT,
            time_created INTEGER NOT NULL
        );
        CREATE TABLE message (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL REFERENCES session(id) ON DELETE CASCADE,
            time_created INTEGER NOT NULL
        );
        CREATE TABLE part (
            id TEXT PRIMARY KEY,
            message_id TEXT NOT NULL REFERENCES message(id) ON DELETE CASCADE,
            time_created INTEGER NOT NULL
        );
        CREATE TABLE todo (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL REFERENCES session(id) ON DELETE CASCADE,
            time_created INTEGER NOT NULL
        );
        CREATE TABLE session_message (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL REFERENCES session(id) ON DELETE CASCADE,
            time_created INTEGER NOT NULL
        );
        CREATE TABLE event_sequence (
            aggregate_id TEXT PRIMARY KEY,
            seq INTEGER NOT NULL
        );
        CREATE TABLE event (
            id TEXT PRIMARY KEY,
            aggregate_id TEXT NOT NULL
                REFERENCES event_sequence(aggregate_id) ON DELETE CASCADE,
            seq INTEGER NOT NULL
        );
        """
    )
    connection.executemany(
        "INSERT INTO session VALUES (?, ?, ?)",
        (
            ("old-session", None, old_ms),
            ("recent-child", "old-session", recent_ms),
            ("recent-session", None, recent_ms),
        ),
    )
    connection.executemany(
        "INSERT INTO message VALUES (?, ?, ?)",
        (
            ("old-message", "old-session", old_ms),
            ("recent-message", "recent-session", recent_ms),
        ),
    )
    connection.executemany(
        "INSERT INTO part VALUES (?, ?, ?)",
        (
            ("old-part", "old-message", old_ms),
            ("recent-part", "recent-message", recent_ms),
        ),
    )
    for table in ("todo", "session_message"):
        connection.executemany(
            f"INSERT INTO {table} VALUES (?, ?, ?)",
            (
                (f"old-{table}", "old-session", old_ms),
                (f"recent-{table}", "recent-session", recent_ms),
            ),
        )
    connection.executemany(
        "INSERT INTO event_sequence VALUES (?, ?)",
        (("old-session", 1), ("recent-child", 1), ("recent-session", 1)),
    )
    connection.executemany(
        "INSERT INTO event VALUES (?, ?, ?)",
        (
            ("old-event", "old-session", 1),
            ("child-event", "recent-child", 1),
            ("recent-event", "recent-session", 1),
        ),
    )
    connection.commit()
    connection.close()


def _row_count(path: Path, table: str) -> int:
    with closing(sqlite3.connect(path)) as connection:
        value = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
    assert value is not None
    return int(value[0])


def _config(module: ModuleType, **overrides: Any) -> Any:
    values: dict[str, Any] = {
        "retention_days": 30,
        "batch_size": 1,
        "max_sessions": 100,
        "timeout_seconds": 2.0,
        "busy_timeout_ms": 20,
    }
    values.update(overrides)
    return module.MaintenanceConfig(**values)


def test_prune_fixture_removes_only_expired_rows_in_bounded_batches(tmp_path: Path) -> None:
    module = _load_maintenance()
    now_ms = 2_000_000_000_000
    old_ms = now_ms - (31 * 86_400_000)
    recent_ms = now_ms - (2 * 86_400_000)
    database = tmp_path / "opencode.db"
    _fixture_db(database, old_ms=old_ms, recent_ms=recent_ms)
    progress: list[str] = []

    result = module.prune_database(
        database,
        _config(module),
        now_ms=now_ms,
        process_check=lambda: False,
        emit=progress.append,
    )

    assert result.sessions_removed == 2
    assert result.events_removed == 2
    assert result.event_sequences_removed == 2
    assert result.limit_reached is False
    for table in (
        "todo",
        "session_message",
        "part",
        "message",
        "event",
        "event_sequence",
        "session",
    ):
        assert _row_count(database, table) == 1
    assert any("phase=prune" in line and "batch=1" in line for line in progress)
    assert any("phase=complete" in line and "sessions_removed=2" in line for line in progress)


def test_running_guard_short_circuits_every_mutation_and_preserves_sidecars(
    tmp_path: Path,
) -> None:
    module = _load_maintenance()
    now_ms = 2_000_000_000_000
    database = tmp_path / "opencode.db"
    _fixture_db(database, old_ms=1, recent_ms=now_ms)
    cache_dir = tmp_path / "tool-output"
    log_dir = tmp_path / "log"
    cache_dir.mkdir()
    log_dir.mkdir()
    cache_file = cache_dir / "active-output"
    log_file = log_dir / "active.log"
    cache_file.write_text("keep", encoding="utf-8")
    log_file.write_text("keep", encoding="utf-8")
    wal = Path(f"{database}-wal")
    shm = Path(f"{database}-shm")
    wal.write_bytes(b"wal-marker")
    shm.write_bytes(b"shm-marker")

    with pytest.raises(module.OpenCodeRunningError, match="still running"):
        module.run_cleanup(
            database,
            tmp_path,
            _config(module),
            hard=False,
            now_ms=now_ms,
            process_check=lambda: True,
            emit=lambda _message: None,
        )

    assert cache_file.read_text(encoding="utf-8") == "keep"
    assert log_file.read_text(encoding="utf-8") == "keep"
    assert wal.read_bytes() == b"wal-marker"
    assert shm.read_bytes() == b"shm-marker"
    assert _row_count(database, "session") == 3


def test_locked_database_fails_within_busy_timeout_and_keeps_rows(tmp_path: Path) -> None:
    module = _load_maintenance()
    now_ms = 2_000_000_000_000
    database = tmp_path / "opencode.db"
    _fixture_db(database, old_ms=1, recent_ms=now_ms)
    locker = sqlite3.connect(database)
    locker.execute("BEGIN IMMEDIATE")
    started = time.monotonic()
    try:
        with pytest.raises(module.DatabaseBusyError, match=r"busy|locked"):
            module.prune_database(
                database,
                _config(module, timeout_seconds=0.5, busy_timeout_ms=25),
                now_ms=now_ms,
                process_check=lambda: False,
                emit=lambda _message: None,
            )
    finally:
        locker.rollback()
        locker.close()

    assert time.monotonic() - started < 1.0
    assert _row_count(database, "session") == 3


def test_missing_optional_tables_are_skipped_without_creating_them(tmp_path: Path) -> None:
    module = _load_maintenance()
    database = tmp_path / "opencode.db"
    with closing(sqlite3.connect(database)) as connection:
        connection.execute(
            "CREATE TABLE session ("
            "id TEXT PRIMARY KEY, parent_id TEXT, time_created INTEGER NOT NULL)"
        )
        connection.execute("INSERT INTO session VALUES ('old', NULL, 1)")
        connection.commit()
    progress: list[str] = []

    result = module.prune_database(
        database,
        _config(module),
        now_ms=2_000_000_000_000,
        process_check=lambda: False,
        emit=progress.append,
    )

    assert result.sessions_removed == 1
    assert any("phase=skip" in line and "table=part" in line for line in progress)
    with closing(sqlite3.connect(database)) as connection:
        tables = {
            str(row[0])
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    assert tables == {"session"}


def test_make_targets_delegate_safety_to_one_process_and_never_unlink_sidecars() -> None:
    content = MAKEFILE.read_text(encoding="utf-8")
    for target in ("opencode-clean", "opencode-clean-hard", "opencode-db-prune"):
        start = content.index(f"{target}:")
        end = content.find("\n\n", start)
        recipe = content[start : end if end >= 0 else None]
        assert "scripts/opencode_db_maintenance.py" in recipe
        assert "_opencode-running-guard" not in recipe
    assert "rm -f ~/.local/share/opencode/opencode.db-wal" not in content
    assert "rm -f ~/.local/share/opencode/opencode.db-shm" not in content
    assert "PRAGMA wal_checkpoint(TRUNCATE); VACUUM;" not in content
    for target in ("opencode-disk", "opencode-db-stats", "opencode-db-schema", "opencode-db-sample"):
        start = content.index(f"{target}:")
        end = content.find("\n\n", start)
        recipe = content[start : end if end >= 0 else None]
        assert "scripts/opencode_db_maintenance.py" in recipe
        assert "~/.local/share/opencode" not in recipe


@pytest.mark.parametrize(
    ("field", "value", "message"),
    (
        ("retention_days", 0, "retention_days"),
        ("retention_days", 3_651, "retention_days"),
        ("batch_size", 0, "batch_size"),
        ("batch_size", 1_001, "batch_size"),
        ("max_sessions", 0, "max_sessions"),
        ("max_sessions", 1_000_001, "max_sessions"),
        ("timeout_seconds", 0.01, "timeout_seconds"),
        ("timeout_seconds", 3_601, "timeout_seconds"),
        ("busy_timeout_ms", 0, "busy_timeout_ms"),
        ("busy_timeout_ms", 30_001, "busy_timeout_ms"),
        ("busy_timeout_ms", 2_001, "total timeout"),
        ("incremental_pages", -1, "incremental_pages"),
        ("incremental_pages", 10_001, "incremental_pages"),
        ("max_file_entries", 0, "max_file_entries"),
        ("max_file_entries", 1_000_001, "max_file_entries"),
    ),
)
def test_config_rejects_unsafe_resource_bounds(
    field: str,
    value: int | float,
    message: str,
) -> None:
    module = _load_maintenance()
    values = {
        "retention_days": 30,
        "batch_size": 10,
        "max_sessions": 100,
        "timeout_seconds": 2,
        "busy_timeout_ms": 20,
    }
    values[field] = value
    with pytest.raises(module.MaintenanceError, match=message):
        module.MaintenanceConfig(**values).validate()


def test_process_detection_is_exact_and_fails_closed() -> None:
    module = _load_maintenance()

    def completed(stdout: str, returncode: int = 0, stderr: str = "") -> Any:
        return lambda *_args, **_kwargs: subprocess.CompletedProcess(
            ["ps"], returncode, stdout, stderr
        )

    for executable in (
        "/usr/local/bin/opencode",
        "/Applications/OpenCode Desktop.app/Contents/MacOS/OpenCode Desktop",
        "/usr/local/bin/opencode-beta",
        "/usr/local/bin/opencode_helper",
        "/Applications/OpenCode.app/Contents/MacOS/OpenCode(Renderer)",
    ):
        assert module.is_opencode_running(completed(f"123 {executable}\n")) is True
    assert module.is_opencode_running(completed("123 /usr/bin/make\n")) is False
    assert module.is_opencode_running(completed("123 /tmp/opencoded\n")) is False
    assert module.is_opencode_running(completed("not-a-pid /tmp/opencode\n")) is False
    with pytest.raises(module.MaintenanceError, match="permission denied"):
        module.is_opencode_running(completed("", 2, "permission denied"))

    def timed_out(*_args: Any, **_kwargs: Any) -> Any:
        raise subprocess.TimeoutExpired("pgrep", 2)

    with pytest.raises(module.MaintenanceError, match="cannot verify"):
        module.is_opencode_running(timed_out)


def test_database_path_resolution_uses_explicit_or_authoritative_cli(tmp_path: Path) -> None:
    module = _load_maintenance()
    absolute = tmp_path / "explicit.db"
    calls: list[tuple[list[str], dict[str, Any]]] = []

    def runner(command: list[str], **kwargs: Any) -> Any:
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, f"{tmp_path / 'resolved.db'}\n", "")

    assert module.resolve_database_path(str(absolute), runner=runner) == absolute
    assert calls == []
    assert module.resolve_database_path(None, runner=runner) == tmp_path / "resolved.db"
    assert calls[-1][0] == ["opencode", "db", "path"]
    assert module.resolve_database_path("channel.db", runner=runner) == tmp_path / "resolved.db"
    assert calls[-1][1]["env"]["OPENCODE_DB"] == "channel.db"


@pytest.mark.parametrize(
    ("result", "message"),
    (
        (subprocess.CompletedProcess(["opencode"], 2, "", "bad config"), "bad config"),
        (subprocess.CompletedProcess(["opencode"], 0, "", ""), "no database path"),
        (subprocess.CompletedProcess(["opencode"], 0, "relative.db\n", ""), "non-absolute"),
    ),
)
def test_database_path_resolution_rejects_ambiguous_cli_output(
    result: Any,
    message: str,
) -> None:
    module = _load_maintenance()
    with pytest.raises(module.MaintenanceError, match=message):
        module.resolve_database_path(None, runner=lambda *_args, **_kwargs: result)


def test_database_path_resolution_and_sql_errors_fail_visibly() -> None:
    module = _load_maintenance()

    def unavailable(*_args: Any, **_kwargs: Any) -> Any:
        raise OSError("missing executable")

    with pytest.raises(module.MaintenanceError, match="missing executable"):
        module.resolve_database_path(None, runner=unavailable)
    timeout_error = module._translate_sqlite_error(
        sqlite3.OperationalError("interrupted"), partial=2
    )
    generic_error = module._translate_sqlite_error(sqlite3.OperationalError("malformed"))
    assert isinstance(timeout_error, module.MaintenanceTimeoutError)
    assert "2 sessions" in str(timeout_error)
    assert type(generic_error) is module.MaintenanceError


def test_prune_stops_before_splitting_a_session_tree(tmp_path: Path) -> None:
    module = _load_maintenance()
    database = tmp_path / "opencode.db"
    _fixture_db(database, old_ms=1, recent_ms=2_000_000_000_000)

    result = module.prune_database(
        database,
        _config(module, max_sessions=1),
        now_ms=2_000_000_000_000,
        process_check=lambda: False,
        emit=lambda _message: None,
    )

    assert result.limit_reached is True
    assert result.sessions_removed == 0
    assert _row_count(database, "session") == 3


def test_prune_reports_partial_when_global_session_cap_is_reached(tmp_path: Path) -> None:
    module = _load_maintenance()
    database = tmp_path / "opencode.db"
    with closing(sqlite3.connect(database)) as connection:
        connection.execute(
            "CREATE TABLE session (id TEXT PRIMARY KEY, time_created INTEGER NOT NULL)"
        )
        connection.executemany(
            "INSERT INTO session VALUES (?, ?)", (("old-a", 1), ("old-b", 2))
        )
        connection.commit()
    progress: list[str] = []

    result = module.prune_database(
        database,
        _config(module, max_sessions=1),
        now_ms=2_000_000_000_000,
        process_check=lambda: False,
        emit=progress.append,
    )

    assert result.sessions_removed == 1
    assert result.limit_reached is True
    assert _row_count(database, "session") == 1
    assert any("reason=max-sessions-reached" in line for line in progress)


def test_schema_drift_fails_closed_before_delete(tmp_path: Path) -> None:
    module = _load_maintenance()
    database = tmp_path / "opencode.db"
    with closing(sqlite3.connect(database)) as connection:
        connection.executescript(
            """
            PRAGMA foreign_keys=ON;
            CREATE TABLE session (id TEXT PRIMARY KEY, time_created INTEGER NOT NULL);
            CREATE TABLE unsafe_child (
                id TEXT PRIMARY KEY,
                session_id TEXT REFERENCES session(id) ON DELETE RESTRICT
            );
            INSERT INTO session VALUES ('old', 1);
            """
        )
        connection.commit()

    with pytest.raises(module.SchemaError, match="without ON DELETE CASCADE"):
        module.prune_database(
            database,
            _config(module),
            now_ms=2_000_000_000_000,
            process_check=lambda: False,
            emit=lambda _message: None,
        )
    assert _row_count(database, "session") == 1


@pytest.mark.parametrize("hard", (False, True))
def test_cleanup_success_is_bounded_and_removes_only_expired_logs(
    tmp_path: Path,
    hard: bool,
) -> None:
    module = _load_maintenance()
    database = tmp_path / "opencode.db"
    _fixture_db(database, old_ms=1, recent_ms=2_000_000_000_000)
    cache_dir = tmp_path / "tool-output"
    nested_cache = cache_dir / "nested"
    nested_cache.mkdir(parents=True)
    (cache_dir / "one").write_text("cached", encoding="utf-8")
    (nested_cache / "two").write_text("cached", encoding="utf-8")
    log_dir = tmp_path / "log"
    log_dir.mkdir()
    old_log = log_dir / "old.log"
    recent_log = log_dir / "recent.log"
    old_log.write_text("old", encoding="utf-8")
    recent_log.write_text("recent", encoding="utf-8")
    now_seconds = 2_000_000_000
    os.utime(old_log, (now_seconds - (8 * 86_400),) * 2)
    os.utime(recent_log, (now_seconds, now_seconds))
    progress: list[str] = []

    module.run_cleanup(
        database,
        tmp_path,
        _config(module),
        hard=hard,
        now_ms=now_seconds * 1_000,
        process_check=lambda: False,
        emit=progress.append,
    )

    assert list(cache_dir.iterdir()) == []
    assert not old_log.exists()
    assert recent_log.is_file()
    assert _row_count(database, "session") == 3
    assert any("phase=optimize status=complete" in line for line in progress)
    assert any("phase=compact mode=incremental status=skipped" in line for line in progress)
    assert any("phase=file-clean" in line for line in progress)


def test_broad_cleanup_directory_is_rejected() -> None:
    module = _load_maintenance()
    with pytest.raises(module.MaintenanceError, match="refusing broad"):
        module._validate_data_directory(Path("/"))


def test_cleanup_refuses_symlinked_roots_before_database_maintenance(
    tmp_path: Path,
) -> None:
    module = _load_maintenance()
    database = tmp_path / "opencode.db"
    _fixture_db(database, old_ms=1, recent_ms=2_000_000_000_000)
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / "tool-output").symlink_to(outside, target_is_directory=True)
    before = database.stat().st_mtime_ns

    with pytest.raises(module.MaintenanceError, match="symlinked cleanup root"):
        module.run_cleanup(
            database,
            tmp_path,
            _config(module),
            hard=False,
            process_check=lambda: False,
            emit=lambda _message: None,
        )

    assert database.stat().st_mtime_ns == before


def test_cleanup_entry_budget_fails_visibly_with_partial_progress(tmp_path: Path) -> None:
    module = _load_maintenance()
    database = tmp_path / "opencode.db"
    _fixture_db(database, old_ms=1, recent_ms=2_000_000_000_000)
    cache_dir = tmp_path / "tool-output"
    cache_dir.mkdir()
    for index in range(3):
        (cache_dir / f"entry-{index}").write_text("cached", encoding="utf-8")

    with pytest.raises(module.MaintenanceError, match="file-entry limit"):
        module.run_cleanup(
            database,
            tmp_path,
            _config(module, max_file_entries=2),
            hard=False,
            process_check=lambda: False,
            emit=lambda _message: None,
        )


def test_required_cascade_edge_is_enforced(tmp_path: Path) -> None:
    module = _load_maintenance()
    database = tmp_path / "opencode.db"
    with closing(sqlite3.connect(database)) as connection:
        connection.executescript(
            """
            CREATE TABLE session (id TEXT PRIMARY KEY, time_created INTEGER NOT NULL);
            CREATE TABLE message (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                time_created INTEGER NOT NULL
            );
            INSERT INTO session VALUES ('old', 1);
            """
        )
        connection.commit()

    with pytest.raises(module.SchemaError, match="missing required ON DELETE CASCADE"):
        module.prune_database(
            database,
            _config(module),
            now_ms=2_000_000_000_000,
            process_check=lambda: False,
            emit=lambda _message: None,
        )


def test_existing_incremental_auto_vacuum_gets_a_bounded_step(tmp_path: Path) -> None:
    module = _load_maintenance()
    database = tmp_path / "opencode.db"
    with closing(sqlite3.connect(database)) as connection:
        connection.execute("PRAGMA auto_vacuum=INCREMENTAL")
        connection.execute("VACUUM")
        connection.execute("CREATE TABLE sample (payload TEXT)")
        connection.execute("INSERT INTO sample VALUES (?)", ("x" * 100_000,))
        connection.commit()
        connection.execute("DELETE FROM sample")
        connection.commit()
    progress: list[str] = []

    module.maintain_database(
        database,
        _config(module, incremental_pages=2),
        process_check=lambda: False,
        emit=progress.append,
    )

    assert any("mode=incremental status=complete max_pages=2" in line for line in progress)

    zero_budget_progress: list[str] = []
    module.maintain_database(
        database,
        _config(module, incremental_pages=0),
        process_check=lambda: False,
        emit=zero_budget_progress.append,
    )
    assert any("reason=page-budget-zero" in line for line in zero_budget_progress)


@pytest.mark.parametrize("report", ("disk", "stats", "schema", "sample"))
def test_read_only_reports_use_fixture_database(
    tmp_path: Path,
    report: str,
) -> None:
    module = _load_maintenance()
    database = tmp_path / "opencode.db"
    _fixture_db(database, old_ms=1, recent_ms=2_000_000_000_000)
    (tmp_path / "tool-output").mkdir()
    (tmp_path / "log").mkdir()
    progress: list[str] = []

    module.inspect_database(
        database,
        tmp_path,
        report,
        _config(module),
        emit=progress.append,
    )

    assert progress
    assert any(f"phase={report}" in line for line in progress)


def test_unknown_read_only_report_is_rejected(tmp_path: Path) -> None:
    module = _load_maintenance()
    database = tmp_path / "opencode.db"
    _fixture_db(database, old_ms=1, recent_ms=2_000_000_000_000)
    with pytest.raises(module.MaintenanceError, match="unknown inspection report"):
        module.inspect_database(
            database,
            tmp_path,
            "unknown",
            _config(module),
            emit=lambda _message: None,
        )


def test_cli_validate_only_and_error_exit_codes(tmp_path: Path, monkeypatch: Any) -> None:
    module = _load_maintenance()
    database = tmp_path / "missing.db"
    assert (
        module.main(
            [
                "clean",
                "--db",
                str(database),
                "--data-dir",
                str(tmp_path),
                "--validate-only",
            ]
        )
        == 0
    )

    errors = (
        (module.OpenCodeRunningError("running"), 3),
        (module.DatabaseBusyError("busy"), 4),
        (module.MaintenanceError("bad"), 2),
    )
    for error, expected in errors:
        def raise_error(*_args: Any, _error: Exception = error, **_kwargs: Any) -> None:
            raise _error

        monkeypatch.setattr(module, "prune_database", raise_error)
        assert module.main(["prune", "--db", str(database)]) == expected


def test_cli_delegates_clean_action(tmp_path: Path, monkeypatch: Any) -> None:
    module = _load_maintenance()
    called: list[bool] = []

    def record_cleanup(*_args: Any, hard: bool, **_kwargs: Any) -> None:
        called.append(hard)

    monkeypatch.setattr(module, "run_cleanup", record_cleanup)
    assert module.main(["clean-hard", "--db", str(tmp_path / "db")]) == 0
    assert called == [True]
