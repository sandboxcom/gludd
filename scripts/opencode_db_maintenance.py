#!/usr/bin/env python3
"""Bounded, offline maintenance for the OpenCode SQLite database.

Mutating operations fail closed while OpenCode is running.  Full ``VACUUM``
and manual WAL/SHM deletion are intentionally absent: both are unsafe for a
large live database and full compaction needs roughly another database's worth
of free disk space.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import subprocess
import sys
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

Emit = Callable[[str], None]
ProcessCheck = Callable[[], bool]
PRUNE_TABLES = (
    "session",
    "message",
    "part",
    "todo",
    "session_message",
    "session_input",
    "session_context_epoch",
    "event",
    "event_sequence",
)
REQUIRED_CASCADE_EDGES = (
    ("message", "session_id", "session"),
    ("part", "message_id", "message"),
    ("todo", "session_id", "session"),
    ("session_message", "session_id", "session"),
    ("session_input", "session_id", "session"),
    ("session_context_epoch", "session_id", "session"),
    ("event", "aggregate_id", "event_sequence"),
)
OPENCODE_EXECUTABLE_DELIMITERS = ("-", "_", " ", "(")
SQLITE_HEARTBEAT_SECONDS = 5.0


class MaintenanceError(RuntimeError):
    """Base class for safe, user-facing maintenance failures."""


class OpenCodeRunningError(MaintenanceError):
    """Raised before mutation when an OpenCode process is active."""


class DatabaseBusyError(MaintenanceError):
    """Raised when a bounded database lock cannot be acquired."""


class MaintenanceTimeoutError(MaintenanceError):
    """Raised when the global maintenance time budget is exhausted."""


class SchemaError(MaintenanceError):
    """Raised when an unknown schema would make deletion unsafe."""


@dataclass(frozen=True)
class MaintenanceConfig:
    """Resource bounds shared by cleanup and pruning."""

    retention_days: int = 30
    batch_size: int = 500
    max_sessions: int = 50_000
    timeout_seconds: float = 60.0
    busy_timeout_ms: int = 1_000
    incremental_pages: int = 1_000
    max_file_entries: int = 100_000

    def validate(self) -> None:
        if not 1 <= self.retention_days <= 3_650:
            raise MaintenanceError("retention_days must be between 1 and 3650")
        if not 1 <= self.batch_size <= 1_000:
            raise MaintenanceError("batch_size must be between 1 and 1000")
        if not 1 <= self.max_sessions <= 1_000_000:
            raise MaintenanceError("max_sessions must be between 1 and 1000000")
        if not 0.1 <= self.timeout_seconds <= 3_600:
            raise MaintenanceError("timeout_seconds must be between 0.1 and 3600")
        if not 1 <= self.busy_timeout_ms <= 30_000:
            raise MaintenanceError("busy_timeout_ms must be between 1 and 30000")
        if self.busy_timeout_ms > int(self.timeout_seconds * 1_000):
            raise MaintenanceError("busy_timeout_ms cannot exceed the total timeout")
        if not 0 <= self.incremental_pages <= 10_000:
            raise MaintenanceError("incremental_pages must be between 0 and 10000")
        if not 1 <= self.max_file_entries <= 1_000_000:
            raise MaintenanceError("max_file_entries must be between 1 and 1000000")


@dataclass(frozen=True)
class PruneResult:
    """Committed direct-delete counts; FK-cascaded child rows are additional."""

    sessions_removed: int
    events_removed: int
    event_sequences_removed: int
    batches: int
    limit_reached: bool


def _print_progress(message: str) -> None:
    print(message, flush=True)


def is_opencode_running(
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> bool:
    """Detect OpenCode executable variants without matching Make target arguments."""

    try:
        result = runner(
            ["ps", "-axo", "pid=,comm="],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise MaintenanceError(f"cannot verify whether OpenCode is running: {exc}") from exc
    if result.returncode != 0:
        detail = result.stderr.strip() or f"ps exited {result.returncode}"
        raise MaintenanceError(f"cannot verify whether OpenCode is running: {detail}")
    for line in result.stdout.splitlines():
        fields = line.strip().split(maxsplit=1)
        if len(fields) != 2 or not fields[0].isdigit():
            continue
        executable = Path(fields[1]).name.casefold()
        if executable == "opencode" or executable.startswith(
            tuple(f"opencode{delimiter}" for delimiter in OPENCODE_EXECUTABLE_DELIMITERS)
        ):
            return True
    return False


def _require_opencode_stopped(
    process_check: ProcessCheck,
    emit: Emit,
    *,
    force: bool = False,
) -> None:
    emit("phase=safety-check status=checking")
    if process_check():
        if force:
            emit(
                "phase=safety-check status=force-override "
                'warning="OpenCode appears to be running; --force bypasses the safety guard"'
            )
            return
        raise OpenCodeRunningError("OpenCode is still running; stop every OpenCode process before cleanup")
    emit("phase=safety-check status=stopped")


def resolve_database_path(
    explicit: str | None,
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> Path:
    """Resolve an explicit path or ask the installed OpenCode CLI authoritatively."""

    if explicit:
        path = Path(explicit).expanduser()
        if path.is_absolute():
            return path.resolve()
    environment = None
    if explicit:
        environment = dict(os.environ)
        environment["OPENCODE_DB"] = explicit
    try:
        result = runner(
            ["opencode", "db", "path"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
            env=environment,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise MaintenanceError(f"cannot resolve database via 'opencode db path': {exc}") from exc
    if result.returncode != 0:
        detail = result.stderr.strip() or f"opencode exited {result.returncode}"
        raise MaintenanceError(f"cannot resolve database via 'opencode db path': {detail}")
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if not lines:
        raise MaintenanceError("'opencode db path' returned no database path")
    resolved = Path(lines[-1]).expanduser()
    if not resolved.is_absolute():
        raise MaintenanceError("'opencode db path' returned a non-absolute path")
    return resolved.resolve()


def _translate_sqlite_error(exc: sqlite3.Error, *, partial: int = 0) -> MaintenanceError:
    detail = str(exc)
    suffix = f"; {partial} sessions were already committed" if partial else ""
    lowered = detail.casefold()
    if "locked" in lowered or "busy" in lowered:
        return DatabaseBusyError(f"database is busy or locked: {detail}{suffix}")
    if "interrupted" in lowered:
        return MaintenanceTimeoutError(f"database operation exceeded its time budget{suffix}")
    return MaintenanceError(f"SQLite maintenance failed: {detail}{suffix}")


def _install_progress_handler(
    connection: sqlite3.Connection,
    *,
    deadline: float,
    emit: Emit,
    phase: str,
    heartbeat_seconds: float = SQLITE_HEARTBEAT_SECONDS,
) -> None:
    """Bound SQLite work by time while making long-running phases observable."""

    started = time.monotonic()
    next_heartbeat = started + heartbeat_seconds

    def progress() -> int:
        nonlocal next_heartbeat
        now = time.monotonic()
        if now >= deadline:
            return 1
        if now >= next_heartbeat:
            elapsed = max(int(now - started), 0)
            emit(f"phase={phase}-heartbeat status=running elapsed_seconds={elapsed}")
            next_heartbeat = now + heartbeat_seconds
        return 0

    connection.set_progress_handler(progress, 1_000)


def _connect(
    database: Path,
    config: MaintenanceConfig,
    deadline: float,
    *,
    emit: Emit = _print_progress,
    phase: str = "database",
) -> sqlite3.Connection:
    if not database.is_file():
        raise MaintenanceError(f"database does not exist: {database}")
    try:
        connection = sqlite3.connect(
            f"{database.resolve().as_uri()}?mode=rw",
            uri=True,
            timeout=config.busy_timeout_ms / 1_000,
            isolation_level=None,
        )
        connection.execute(f"PRAGMA busy_timeout={config.busy_timeout_ms}")
        connection.execute("PRAGMA foreign_keys=ON")
        foreign_keys = connection.execute("PRAGMA foreign_keys").fetchone()
        if foreign_keys is None or int(foreign_keys[0]) != 1:
            connection.close()
            raise MaintenanceError("could not enable SQLite foreign-key enforcement")
        _install_progress_handler(
            connection,
            deadline=deadline,
            emit=emit,
            phase=phase,
        )
        return connection
    except sqlite3.Error as exc:
        raise _translate_sqlite_error(exc) from exc


def _connect_read_only(
    database: Path,
    config: MaintenanceConfig,
    deadline: float,
    *,
    emit: Emit = _print_progress,
    phase: str = "inspection",
) -> sqlite3.Connection:
    if not database.is_file():
        raise MaintenanceError(f"database does not exist: {database}")
    try:
        connection = sqlite3.connect(
            f"{database.resolve().as_uri()}?mode=ro",
            uri=True,
            timeout=config.busy_timeout_ms / 1_000,
            isolation_level=None,
        )
        connection.execute(f"PRAGMA busy_timeout={config.busy_timeout_ms}")
        connection.execute("PRAGMA query_only=ON")
        _install_progress_handler(
            connection,
            deadline=deadline,
            emit=emit,
            phase=phase,
        )
        return connection
    except sqlite3.Error as exc:
        raise _translate_sqlite_error(exc) from exc


def _quoted(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _table_names(connection: sqlite3.Connection) -> set[str]:
    rows = connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    return {str(row[0]) for row in rows}


def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in connection.execute(f"PRAGMA table_info({_quoted(table)})").fetchall()}


def _directory_usage(
    directory: Path,
    config: MaintenanceConfig,
    deadline: float,
    emit: Emit,
    label: str,
) -> tuple[int, int]:
    if not directory.exists():
        emit(f"phase=disk label={label} status=absent")
        return 0, 0
    if directory.is_symlink():
        emit(f"phase=disk label={label} status=symlink-not-followed")
        return 0, 1
    total_bytes = 0
    entries = 0
    stack = [directory]
    while stack:
        if entries >= config.max_file_entries:
            raise MaintenanceError(f"disk inspection reached {config.max_file_entries} entries")
        if time.monotonic() >= deadline:
            raise MaintenanceTimeoutError(f"disk inspection timed out after {entries} entries")
        entry = stack.pop()
        entries += 1
        if entries % 1_000 == 0:
            emit(f"phase=disk-heartbeat label={label} entries_scanned={entries}")
        if entry.is_symlink():
            continue
        if entry.is_dir():
            for child in entry.iterdir():
                if entries + len(stack) >= config.max_file_entries:
                    raise MaintenanceError(f"disk inspection reached {config.max_file_entries} entries")
                if time.monotonic() >= deadline:
                    raise MaintenanceTimeoutError(f"disk inspection timed out after {entries} entries")
                stack.append(child)
        elif entry.is_file():
            total_bytes += entry.stat().st_size
    emit(f"phase=disk label={label} bytes={total_bytes} entries={max(entries - 1, 0)}")
    return total_bytes, max(entries - 1, 0)


def inspect_database(
    database: Path,
    data_directory: Path,
    report: str,
    config: MaintenanceConfig,
    *,
    emit: Emit = _print_progress,
) -> None:
    """Run a bounded read-only disk, stats, schema, or timestamp report."""

    config.validate()
    deadline = time.monotonic() + config.timeout_seconds
    if report == "disk":
        for path, label in (
            (database, "database"),
            (Path(f"{database}-wal"), "wal"),
            (Path(f"{database}-shm"), "shm"),
        ):
            size = path.stat().st_size if path.is_file() else 0
            emit(f"phase=disk label={label} bytes={size} status={'present' if path.is_file() else 'absent'}")
        _directory_usage(data_directory / "tool-output", config, deadline, emit, "tool-output")
        _directory_usage(data_directory / "log", config, deadline, emit, "log")
        return
    connection = _connect_read_only(
        database,
        config,
        deadline,
        emit=emit,
        phase=report,
    )
    try:
        tables = sorted(_table_names(connection))
        if report == "stats":
            for table in tables:
                row = connection.execute(f"SELECT COUNT(*) FROM {_quoted(table)}").fetchone()
                count = int(row[0]) if row else 0
                emit(f"phase=stats table={table} rows={count}")
        elif report == "schema":
            for table in tables:
                columns = ",".join(sorted(_columns(connection, table)))
                emit(f"phase=schema table={table} columns={columns}")
        elif report == "sample":
            for table in ("session", "message"):
                if table not in tables or "time_created" not in _columns(connection, table):
                    emit(f"phase=sample table={table} status=unavailable")
                    continue
                row = connection.execute(
                    f"SELECT MIN(time_created), MAX(time_created) FROM {_quoted(table)}"
                ).fetchone()
                minimum, maximum = row if row else (None, None)
                emit(f"phase=sample table={table} min_time_created={minimum} max_time_created={maximum}")
        else:
            raise MaintenanceError(f"unknown inspection report: {report}")
    except sqlite3.Error as exc:
        raise _translate_sqlite_error(exc) from exc
    finally:
        connection.close()


def _validate_prune_schema(
    connection: sqlite3.Connection,
    tables: set[str],
    emit: Emit,
) -> set[str]:
    if "session" not in tables:
        raise SchemaError("session table is missing; refusing unknown OpenCode schema")
    session_columns = _columns(connection, "session")
    missing = {"id", "time_created"} - session_columns
    if missing:
        raise SchemaError(f"session table is missing required columns: {sorted(missing)}")
    for table in ("event", "event_sequence"):
        if table in tables and "aggregate_id" not in _columns(connection, table):
            raise SchemaError(f"{table} table has no aggregate_id column")
    foreign_keys_by_table: dict[str, list[tuple[object, ...]]] = {}
    for table in sorted(tables):
        foreign_keys = connection.execute(f"PRAGMA foreign_key_list({_quoted(table)})").fetchall()
        foreign_keys_by_table[table] = foreign_keys
        for foreign_key in foreign_keys:
            referenced_table = str(foreign_key[2])
            on_delete = str(foreign_key[6]).upper()
            if referenced_table == "session" and on_delete != "CASCADE":
                raise SchemaError(f"{table} references session without ON DELETE CASCADE")
    for child, child_column, parent in REQUIRED_CASCADE_EDGES:
        if child not in tables:
            continue
        matching_edges = [
            foreign_key
            for foreign_key in foreign_keys_by_table[child]
            if str(foreign_key[2]) == parent
            and str(foreign_key[3]) == child_column
            and str(foreign_key[6]).upper() == "CASCADE"
        ]
        if not matching_edges:
            raise SchemaError(f"{child}.{child_column} is missing required ON DELETE CASCADE to {parent}")
    for table in PRUNE_TABLES:
        if table not in tables:
            emit(f"phase=skip table={table} reason=absent")
    return session_columns


def _select_session_tree(
    connection: sqlite3.Connection,
    cutoff_ms: int,
    root_limit: int,
    *,
    has_parent_id: bool,
) -> list[str]:
    if not has_parent_id:
        rows = connection.execute(
            "SELECT id FROM session WHERE time_created < ? ORDER BY time_created, id LIMIT ?",
            (cutoff_ms, root_limit),
        ).fetchall()
        return [str(row[0]) for row in rows]
    rows = connection.execute(
        """
        WITH RECURSIVE
        roots(id) AS (
            SELECT id FROM session
            WHERE time_created < ?
            ORDER BY time_created, id
            LIMIT ?
        ),
        doomed(id) AS (
            SELECT id FROM roots
            UNION
            SELECT child.id
            FROM session AS child
            JOIN doomed AS parent ON child.parent_id = parent.id
        )
        SELECT id FROM doomed ORDER BY id
        """,
        (cutoff_ms, root_limit),
    ).fetchall()
    return [str(row[0]) for row in rows]


def _chunks(values: Sequence[str], size: int) -> Iterable[Sequence[str]]:
    for start in range(0, len(values), size):
        yield values[start : start + size]


def _delete_ids(
    connection: sqlite3.Connection,
    table: str,
    column: str,
    identifiers: Sequence[str],
    chunk_size: int,
) -> int:
    removed = 0
    for chunk in _chunks(identifiers, chunk_size):
        placeholders = ",".join("?" for _item in chunk)
        cursor = connection.execute(
            f"DELETE FROM {_quoted(table)} WHERE {_quoted(column)} IN ({placeholders})",
            tuple(chunk),
        )
        removed += max(cursor.rowcount, 0)
    return removed


def _checkpoint_passive(connection: sqlite3.Connection, emit: Emit) -> None:
    row = connection.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchone()
    if row is None:
        raise MaintenanceError("PASSIVE WAL checkpoint returned no status")
    busy, log_pages, checkpointed_pages = (int(value) for value in row[:3])
    emit(f"phase=checkpoint mode=PASSIVE busy={busy} log_pages={log_pages} checkpointed_pages={checkpointed_pages}")
    if busy:
        raise DatabaseBusyError("PASSIVE WAL checkpoint reported a busy database")


def prune_database(
    database: Path,
    config: MaintenanceConfig,
    *,
    now_ms: int | None = None,
    process_check: ProcessCheck = is_opencode_running,
    emit: Emit = _print_progress,
    force: bool = False,
) -> PruneResult:
    """Delete bounded expired session trees and their event aggregates."""

    config.validate()
    _require_opencode_stopped(process_check, emit, force=force)
    deadline = time.monotonic() + config.timeout_seconds
    cutoff_ms = (now_ms if now_ms is not None else time.time_ns() // 1_000_000) - (config.retention_days * 86_400_000)
    emit(
        f"phase=plan cutoff_ms={cutoff_ms} batch_size={config.batch_size} "
        f"max_sessions={config.max_sessions} timeout_seconds={config.timeout_seconds:g}"
    )
    connection = _connect(
        database,
        config,
        deadline,
        emit=emit,
        phase="prune",
    )
    sessions_removed = 0
    events_removed = 0
    sequences_removed = 0
    batches = 0
    limit_reached = False
    try:
        tables = _table_names(connection)
        session_columns = _validate_prune_schema(connection, tables, emit)
        while sessions_removed < config.max_sessions:
            if time.monotonic() >= deadline:
                raise MaintenanceTimeoutError(
                    f"maintenance timed out; {sessions_removed} sessions were already committed"
                )
            remaining = config.max_sessions - sessions_removed
            identifiers = _select_session_tree(
                connection,
                cutoff_ms,
                min(config.batch_size, remaining),
                has_parent_id="parent_id" in session_columns,
            )
            if not identifiers:
                break
            if len(identifiers) > remaining:
                limit_reached = True
                emit(
                    "phase=partial reason=session-tree-exceeds-limit "
                    f"tree_sessions={len(identifiers)} remaining={remaining}"
                )
                break
            _require_opencode_stopped(process_check, emit, force=force)
            try:
                connection.execute("BEGIN IMMEDIATE")
                removed_events = (
                    _delete_ids(
                        connection,
                        "event",
                        "aggregate_id",
                        identifiers,
                        config.batch_size,
                    )
                    if "event" in tables
                    else 0
                )
                removed_sequences = (
                    _delete_ids(
                        connection,
                        "event_sequence",
                        "aggregate_id",
                        identifiers,
                        config.batch_size,
                    )
                    if "event_sequence" in tables
                    else 0
                )
                removed_sessions = _delete_ids(
                    connection,
                    "session",
                    "id",
                    identifiers,
                    config.batch_size,
                )
                if removed_sessions != len(identifiers):
                    raise SchemaError("session set changed during maintenance; transaction rolled back")
                connection.commit()
            except sqlite3.Error as exc:
                connection.rollback()
                raise _translate_sqlite_error(exc, partial=sessions_removed) from exc
            except Exception:
                connection.rollback()
                raise
            batches += 1
            sessions_removed += removed_sessions
            events_removed += removed_events
            sequences_removed += removed_sequences
            emit(
                f"phase=prune batch={batches} sessions={removed_sessions} "
                f"events={removed_events} event_sequences={removed_sequences} "
                f"sessions_total={sessions_removed}"
            )
            try:
                _checkpoint_passive(connection, emit)
            except sqlite3.Error as exc:
                raise _translate_sqlite_error(exc, partial=sessions_removed) from exc
        if sessions_removed >= config.max_sessions:
            row = connection.execute(
                "SELECT EXISTS(SELECT 1 FROM session WHERE time_created < ?)",
                (cutoff_ms,),
            ).fetchone()
            limit_reached = row is not None and bool(row[0])
            if limit_reached:
                emit("phase=partial reason=max-sessions-reached")
        emit(
            f"phase=complete sessions_removed={sessions_removed} "
            f"events_removed={events_removed} event_sequences_removed={sequences_removed} "
            f"batches={batches} limit_reached={str(limit_reached).lower()}"
        )
        return PruneResult(
            sessions_removed=sessions_removed,
            events_removed=events_removed,
            event_sequences_removed=sequences_removed,
            batches=batches,
            limit_reached=limit_reached,
        )
    except sqlite3.Error as exc:
        raise _translate_sqlite_error(exc, partial=sessions_removed) from exc
    finally:
        connection.close()


def maintain_database(
    database: Path,
    config: MaintenanceConfig,
    *,
    process_check: ProcessCheck = is_opencode_running,
    emit: Emit = _print_progress,
    _deadline: float | None = None,
    force: bool = False,
) -> None:
    """Run bounded optimize/checkpoint work without full-file compaction."""

    config.validate()
    _require_opencode_stopped(process_check, emit, force=force)
    deadline = _deadline if _deadline is not None else time.monotonic() + config.timeout_seconds
    connection = _connect(
        database,
        config,
        deadline,
        emit=emit,
        phase="maintenance",
    )
    try:
        _require_opencode_stopped(process_check, emit, force=force)
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("PRAGMA optimize")
        connection.commit()
        emit("phase=optimize status=complete")
        _checkpoint_passive(connection, emit)
        auto_vacuum = connection.execute("PRAGMA auto_vacuum").fetchone()
        auto_vacuum_mode = int(auto_vacuum[0]) if auto_vacuum else 0
        if auto_vacuum_mode == 2 and config.incremental_pages:
            connection.execute(f"PRAGMA incremental_vacuum({config.incremental_pages})")
            emit(f"phase=compact mode=incremental status=complete max_pages={config.incremental_pages}")
        else:
            reason = "page-budget-zero" if auto_vacuum_mode == 2 else "auto-vacuum-not-incremental"
            emit(f"phase=compact mode=incremental status=skipped reason={reason}")
        free_pages = connection.execute("PRAGMA freelist_count").fetchone()
        page_size = connection.execute("PRAGMA page_size").fetchone()
        reusable = int(free_pages[0]) * int(page_size[0]) if free_pages and page_size else 0
        emit(f"phase=compact mode=full status=disabled reason=headroom-unsafe reusable_bytes={reusable}")
    except sqlite3.Error as exc:
        connection.rollback()
        raise _translate_sqlite_error(exc) from exc
    finally:
        connection.close()


def _file_budget_checkpoint(
    *,
    scanned: int,
    config: MaintenanceConfig,
    deadline: float,
    process_check: ProcessCheck,
    emit: Emit,
    force: bool = False,
) -> None:
    if scanned >= config.max_file_entries:
        raise MaintenanceError(f"file-entry limit reached after {scanned} entries; cleanup is partial")
    if time.monotonic() >= deadline:
        raise MaintenanceTimeoutError(f"file cleanup timed out after {scanned} entries; cleanup is partial")
    if scanned % 100 == 0:
        emit(f"phase=file-clean-heartbeat entries_scanned={scanned}")
        if not force:
            _require_opencode_stopped(process_check, emit)


def _clear_directory(
    directory: Path,
    config: MaintenanceConfig,
    deadline: float,
    process_check: ProcessCheck,
    emit: Emit,
    *,
    force: bool = False,
) -> tuple[int, int]:
    if not directory.exists():
        return 0, 0
    if directory.is_symlink():
        raise MaintenanceError(f"refusing symlinked cleanup root: {directory}")
    removed = 0
    scanned = 0
    stack: list[tuple[Path, bool]] = []
    for entry in directory.iterdir():
        _file_budget_checkpoint(
            scanned=scanned,
            config=config,
            deadline=deadline,
            process_check=process_check,
            emit=emit,
            force=force,
        )
        scanned += 1
        stack.append((entry, False))
    while stack:
        entry, visited = stack.pop()
        if entry.is_symlink() or entry.is_file():
            entry.unlink()
            removed += 1
        elif entry.is_dir():
            if visited:
                entry.rmdir()
                removed += 1
            else:
                stack.append((entry, True))
                for child in entry.iterdir():
                    _file_budget_checkpoint(
                        scanned=scanned,
                        config=config,
                        deadline=deadline,
                        process_check=process_check,
                        emit=emit,
                        force=force,
                    )
                    scanned += 1
                    stack.append((child, False))
    return removed, scanned


def _clean_logs(
    directory: Path,
    older_than_days: int,
    now_seconds: float,
    config: MaintenanceConfig,
    deadline: float,
    process_check: ProcessCheck,
    emit: Emit,
    *,
    already_scanned: int,
    force: bool = False,
) -> tuple[int, int]:
    if not directory.exists():
        return 0, 0
    if directory.is_symlink():
        raise MaintenanceError(f"refusing symlinked cleanup root: {directory}")
    cutoff = now_seconds - (older_than_days * 86_400)
    removed = 0
    scanned = 0
    stack: list[Path] = []
    for entry in directory.iterdir():
        _file_budget_checkpoint(
            scanned=already_scanned + scanned,
            config=config,
            deadline=deadline,
            process_check=process_check,
            emit=emit,
            force=force,
        )
        scanned += 1
        stack.append(entry)
    while stack:
        entry = stack.pop()
        if entry.is_symlink():
            continue
        if entry.is_dir():
            for child in entry.iterdir():
                _file_budget_checkpoint(
                    scanned=already_scanned + scanned,
                    config=config,
                    deadline=deadline,
                    process_check=process_check,
                    emit=emit,
                    force=force,
                )
                scanned += 1
                stack.append(child)
        elif entry.is_file() and entry.stat().st_mtime < cutoff:
            entry.unlink()
            removed += 1
    return removed, scanned


def _validate_data_directory(data_directory: Path) -> Path:
    if data_directory.is_symlink():
        raise MaintenanceError(f"refusing symlinked cleanup root: {data_directory}")
    resolved = data_directory.expanduser().resolve()
    if resolved in {Path("/"), Path.home().resolve()}:
        raise MaintenanceError(f"refusing broad data directory: {resolved}")
    return resolved


def _validate_cleanup_roots(data_directory: Path) -> None:
    for child in (data_directory / "tool-output", data_directory / "log"):
        if child.is_symlink():
            raise MaintenanceError(f"refusing symlinked cleanup root: {child}")


def run_cleanup(
    database: Path,
    data_directory: Path,
    config: MaintenanceConfig,
    *,
    hard: bool,
    now_ms: int | None = None,
    process_check: ProcessCheck = is_opencode_running,
    emit: Emit = _print_progress,
    force: bool = False,
) -> None:
    """Maintain the DB, then remove cache/log files after a final live guard."""

    config.validate()
    deadline = time.monotonic() + config.timeout_seconds
    _require_opencode_stopped(process_check, emit, force=force)
    data_directory = _validate_data_directory(data_directory)
    _validate_cleanup_roots(data_directory)
    maintain_database(
        database,
        config,
        process_check=process_check,
        emit=emit,
        _deadline=deadline,
        force=force,
    )
    _require_opencode_stopped(process_check, emit, force=force)
    cache_removed, cache_scanned = _clear_directory(
        data_directory / "tool-output",
        config,
        deadline,
        process_check,
        emit,
        force=force,
    )
    current_seconds = now_ms / 1_000 if now_ms is not None else time.time_ns() / 1_000_000_000
    log_days = 1 if hard else 7
    logs_removed, logs_scanned = _clean_logs(
        data_directory / "log",
        log_days,
        current_seconds,
        config,
        deadline,
        process_check,
        emit,
        already_scanned=cache_scanned,
        force=force,
    )
    emit(
        f"phase=file-clean cache_entries_removed={cache_removed} "
        f"logs_removed={logs_removed} entries_scanned={cache_scanned + logs_scanned} "
        f"log_retention_days={log_days}"
    )


def vacuum_incremental(
    database: Path,
    config: MaintenanceConfig,
    *,
    emit: Emit = _print_progress,
) -> None:
    """Run PRAGMA incremental_vacuum safely while OpenCode is running."""
    config.validate()
    deadline = time.monotonic() + config.timeout_seconds
    emit(
        f"phase=vacuum-incremental status=starting pages={config.incremental_pages} "
        f"timeout_seconds={config.timeout_seconds:g}"
    )
    connection = _connect(
        database,
        config,
        deadline,
        emit=emit,
        phase="vacuum-incremental",
    )
    try:
        auto_vacuum = connection.execute("PRAGMA auto_vacuum").fetchone()
        auto_vacuum_mode = int(auto_vacuum[0]) if auto_vacuum else 0
        if auto_vacuum_mode != 2:
            raise MaintenanceError(
                f"auto_vacuum is not INCREMENTAL (mode={auto_vacuum_mode}); "
                "incremental_vacuum requires PRAGMA auto_vacuum=INCREMENTAL"
            )
        if config.incremental_pages <= 0:
            raise MaintenanceError("incremental_pages must be positive")
        connection.execute(f"PRAGMA incremental_vacuum({config.incremental_pages})")
        free_pages = connection.execute("PRAGMA freelist_count").fetchone()
        page_size = connection.execute("PRAGMA page_size").fetchone()
        remaining_bytes = int(free_pages[0]) * int(page_size[0]) if free_pages and page_size else 0
        emit(f"phase=vacuum-incremental status=complete remaining_free_bytes={remaining_bytes}")
    except sqlite3.Error as exc:
        raise _translate_sqlite_error(exc) from exc
    finally:
        connection.close()


def vacuum_full(
    database: Path,
    config: MaintenanceConfig,
    *,
    process_check: ProcessCheck = is_opencode_running,
    emit: Emit = _print_progress,
    force: bool = False,
    into: str | None = None,
) -> None:
    """Run full VACUUM to reclaim disk space. Requires exclusive lock."""
    config.validate()
    _require_opencode_stopped(process_check, emit, force=force)
    deadline = time.monotonic() + config.timeout_seconds
    size_before = database.stat().st_size if database.is_file() else 0
    emit(f"phase=vacuum-full status=starting size_bytes={size_before} timeout_seconds={config.timeout_seconds}")
    connection = _connect(
        database,
        config,
        deadline,
        emit=emit,
        phase="vacuum-full",
    )
    try:
        if into:
            connection.execute(f"VACUUM INTO '{into}'")
            emit(f"phase=vacuum-full status=complete into={into}")
        else:
            connection.execute("VACUUM")
            size_after = database.stat().st_size if database.is_file() else 0
            emit(
                f"phase=vacuum-full status=complete size_before={size_before} size_after={size_after} freed={size_before - size_after}"
            )
    except sqlite3.Error as exc:
        raise _translate_sqlite_error(exc) from exc
    finally:
        connection.close()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action",
        choices=(
            "clean",
            "clean-hard",
            "prune",
            "disk",
            "stats",
            "schema",
            "sample",
            "incremental-vacuum",
            "vacuum-full",
        ),
    )
    parser.add_argument("--db")
    parser.add_argument("--data-dir")
    parser.add_argument("--retention-days", type=int, default=30)
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument("--max-sessions", type=int, default=50_000)
    parser.add_argument("--timeout-seconds", type=float, default=60.0)
    parser.add_argument("--busy-timeout-ms", type=int, default=1_000)
    parser.add_argument("--incremental-pages", type=int, default=1_000)
    parser.add_argument("--max-file-entries", type=int, default=100_000)
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--force", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    config = MaintenanceConfig(
        retention_days=args.retention_days,
        batch_size=args.batch_size,
        max_sessions=args.max_sessions,
        timeout_seconds=args.timeout_seconds,
        busy_timeout_ms=args.busy_timeout_ms,
        incremental_pages=args.incremental_pages,
        max_file_entries=args.max_file_entries,
    )
    try:
        config.validate()
        database = resolve_database_path(args.db)
        data_directory = Path(args.data_dir).expanduser() if args.data_dir else database.parent
        if args.validate_only:
            _validate_data_directory(data_directory)
            _print_progress(f"phase=validate status=ok action={args.action} db={database} data_dir={data_directory}")
            return 0
        if args.action in {"disk", "stats", "schema", "sample"}:
            inspect_database(database, data_directory, args.action, config)
        elif args.action == "incremental-vacuum":
            vacuum_incremental(database, config)
        elif args.action == "vacuum-full":
            vacuum_full(database, config, force=args.force)
        elif args.action == "prune":
            prune_database(database, config, force=args.force)
        else:
            run_cleanup(
                database,
                data_directory,
                config,
                hard=args.action == "clean-hard",
                force=args.force,
            )
        return 0
    except OpenCodeRunningError as exc:
        print(f"REFUSED: {exc}", file=sys.stderr, flush=True)
        return 3
    except DatabaseBusyError as exc:
        print(f"BUSY: {exc}", file=sys.stderr, flush=True)
        return 4
    except MaintenanceError as exc:
        print(f"ERROR: {exc}", file=sys.stderr, flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
