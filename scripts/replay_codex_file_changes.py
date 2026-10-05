#!/usr/bin/env python3
"""Recover completed Codex file-change events without partial repository edits."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class ReplayError(RuntimeError):
    """Raised when a replay record is unsafe or cannot be applied completely."""


@dataclass(frozen=True)
class ReplayEvent:
    """One durable Codex file-change event."""

    ordinal: int
    item_id: str
    changes: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class ReplayResult:
    """Stable receipt for a validated or applied replay batch."""

    event_count: int
    changed_paths: tuple[str, ...]
    applied: bool


def _load_events(
    database: Path,
    thread_id: str,
    start_ordinal: int,
    end_ordinal: int,
) -> tuple[ReplayEvent, ...]:
    if start_ordinal > end_ordinal:
        raise ReplayError("start ordinal must not exceed end ordinal")
    if not database.is_file():
        raise ReplayError(f"database does not exist: {database}")

    uri = f"file:{database.resolve()}?mode=ro"
    try:
        with closing(sqlite3.connect(uri, uri=True)) as connection:
            rows = connection.execute(
                """
                SELECT rollout_ordinal, item_id, item_json
                FROM thread_items
                WHERE thread_id = ?
                  AND item_type = 'fileChange'
                  AND rollout_ordinal BETWEEN ? AND ?
                ORDER BY rollout_ordinal
                """,
                (thread_id, start_ordinal, end_ordinal),
            ).fetchall()
    except sqlite3.Error as exc:
        raise ReplayError(f"could not read Codex thread projection: {exc}") from exc

    events: list[ReplayEvent] = []
    for ordinal, item_id, payload in rows:
        try:
            record = json.loads(payload)
        except (TypeError, json.JSONDecodeError) as exc:
            raise ReplayError(f"invalid JSON in {item_id}") from exc
        if record.get("type") != "fileChange":
            raise ReplayError(f"unexpected item type in {item_id}")
        if record.get("status") not in (None, "completed"):
            raise ReplayError(f"incomplete file-change event: {item_id}")
        changes = record.get("changes")
        if not isinstance(changes, list) or not changes:
            raise ReplayError(f"file-change event has no changes: {item_id}")
        events.append(
            ReplayEvent(
                ordinal=int(ordinal),
                item_id=str(item_id),
                changes=tuple(changes),
            )
        )
    if not events:
        raise ReplayError("no completed file-change events matched the requested range")
    return tuple(events)


def _relative_path(recorded_repo: Path, raw_path: object) -> Path:
    if not isinstance(raw_path, str) or not raw_path:
        raise ReplayError("file-change record has no path")
    root = recorded_repo.resolve()
    recorded = Path(raw_path)
    candidate = (recorded if recorded.is_absolute() else root / recorded).resolve()
    try:
        relative = candidate.relative_to(root)
    except ValueError as exc:
        raise ReplayError(f"path is outside repository: {raw_path}") from exc
    if relative == Path("."):
        raise ReplayError("repository root cannot be a replay target")
    return relative


def _update_patch(relative: Path, diff: str) -> str:
    path = relative.as_posix()
    body = diff if diff.endswith("\n") else f"{diff}\n"
    return f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n{body}"


def _apply_event(
    staging: Path,
    event: ReplayEvent,
    recorded_repo: Path,
) -> set[Path]:
    touched: set[Path] = set()
    for change in event.changes:
        if not isinstance(change, dict):
            raise ReplayError(f"malformed change in {event.item_id}")
        relative = _relative_path(recorded_repo, change.get("path"))
        target = staging / relative
        kind_data = change.get("kind")
        kind = kind_data.get("type") if isinstance(kind_data, dict) else None
        diff = change.get("diff")
        if not isinstance(diff, str):
            raise ReplayError(f"missing diff in {event.item_id}: {relative}")

        target.parent.mkdir(parents=True, exist_ok=True)
        if kind == "add":
            if target.exists() and target.read_text() != diff:
                raise ReplayError(f"add target already exists in {event.item_id}: {relative}")
            target.write_text(diff)
        elif kind == "update":
            if not target.is_file():
                raise ReplayError(f"update target is missing in {event.item_id}: {relative}")
            completed = subprocess.run(
                ["git", "apply", "--no-index", "--whitespace=nowarn", "-"],
                cwd=staging,
                input=_update_patch(relative, diff),
                text=True,
                capture_output=True,
                check=False,
            )
            if completed.returncode != 0:
                detail = completed.stderr.strip() or completed.stdout.strip()
                raise ReplayError(f"could not apply {event.item_id}: {detail}")
        elif kind == "delete":
            if not target.is_file():
                raise ReplayError(f"delete target is missing in {event.item_id}: {relative}")
            target.unlink()
        else:
            raise ReplayError(f"unsupported change kind in {event.item_id}: {kind}")
        touched.add(relative)
    return touched


def _publish(staging: Path, repo: Path, paths: tuple[Path, ...]) -> None:
    for relative in paths:
        source = staging / relative
        destination = repo / relative
        if not source.exists():
            destination.unlink(missing_ok=True)
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{destination.name}.codex-replay-",
            dir=destination.parent,
        )
        os.close(descriptor)
        temporary = Path(temporary_name)
        try:
            shutil.copy2(source, temporary)
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)


def replay_changes(
    *,
    database: Path,
    repo: Path,
    recorded_repo: Path | None = None,
    thread_id: str,
    start_ordinal: int,
    end_ordinal: int,
    apply: bool,
) -> ReplayResult:
    """Validate a bounded replay in isolation, then optionally publish it."""
    root = repo.resolve()
    recorded_root = (recorded_repo or repo).resolve()
    if not root.is_dir():
        raise ReplayError(f"repository does not exist: {repo}")
    events = _load_events(database, thread_id, start_ordinal, end_ordinal)

    all_paths = {
        _relative_path(recorded_root, change.get("path"))
        for event in events
        for change in event.changes
        if isinstance(change, dict)
    }
    ordered_paths = tuple(sorted(all_paths, key=lambda item: item.as_posix()))

    with tempfile.TemporaryDirectory(prefix="gludd-codex-replay-") as temporary:
        staging = Path(temporary)
        for relative in ordered_paths:
            source = root / relative
            if source.is_symlink():
                raise ReplayError(f"refusing symlink replay target: {relative}")
            if source.is_file():
                staged = staging / relative
                staged.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, staged)

        touched: set[Path] = set()
        for event in events:
            touched.update(_apply_event(staging, event, recorded_root))
        if apply:
            _publish(staging, root, tuple(sorted(touched, key=lambda item: item.as_posix())))

    return ReplayResult(
        event_count=len(events),
        changed_paths=tuple(path.as_posix() for path in ordered_paths),
        applied=apply,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--recorded-repo", type=Path)
    parser.add_argument("--thread-id", required=True)
    parser.add_argument("--start-ordinal", type=int, required=True)
    parser.add_argument("--end-ordinal", type=int, required=True)
    parser.add_argument("--apply", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = replay_changes(
            database=args.database,
            repo=args.repo,
            recorded_repo=args.recorded_repo,
            thread_id=args.thread_id,
            start_ordinal=args.start_ordinal,
            end_ordinal=args.end_ordinal,
            apply=args.apply,
        )
    except ReplayError as exc:
        print(f"CODEX-REPLAY: FAIL — {exc}", file=sys.stderr)
        return 2
    mode = "APPLIED" if result.applied else "VALIDATED"
    print(
        f"CODEX-REPLAY: {mode} events={result.event_count} "
        f"files={len(result.changed_paths)}"
    )
    for path in result.changed_paths:
        print(f"  {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
