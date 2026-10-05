"""Tests for atomic recovery of Codex file-change events."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from scripts import replay_codex_file_changes as replay


def _database(path: Path, events: list[tuple[int, dict[str, object]]]) -> Path:
    connection = sqlite3.connect(path)
    connection.execute(
        """
        CREATE TABLE thread_items (
            thread_id TEXT NOT NULL,
            rollout_ordinal INTEGER NOT NULL,
            item_id TEXT NOT NULL,
            item_type TEXT NOT NULL,
            item_json TEXT NOT NULL
        )
        """
    )
    connection.executemany(
        "INSERT INTO thread_items VALUES (?, ?, ?, 'fileChange', ?)",
        [
            (
                "thread-1",
                ordinal,
                str(event["id"]),
                json.dumps(event),
            )
            for ordinal, event in events
        ],
    )
    connection.commit()
    connection.close()
    return path


def _event(item_id: str, path: Path, kind: str, diff: str) -> dict[str, object]:
    return {
        "type": "fileChange",
        "id": item_id,
        "status": "completed",
        "changes": [{"path": str(path), "kind": {"type": kind}, "diff": diff}],
    }


def test_replay_applies_add_then_update_atomically(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    target = repo / "notes.txt"
    database = _database(
        tmp_path / "history.sqlite",
        [
            (10, _event("add", target, "add", "alpha\nbeta\n")),
            (
                11,
                _event(
                    "update",
                    target,
                    "update",
                    "@@ -1,2 +1,2 @@\n alpha\n-beta\n+gamma\n",
                ),
            ),
        ],
    )

    result = replay.replay_changes(
        database=database,
        repo=repo,
        thread_id="thread-1",
        start_ordinal=10,
        end_ordinal=11,
        apply=True,
    )

    assert target.read_text() == "alpha\ngamma\n"
    assert result.event_count == 2
    assert result.changed_paths == ("notes.txt",)


def test_dry_run_validates_without_writing(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    target = repo / "new.txt"
    database = _database(
        tmp_path / "history.sqlite",
        [(20, _event("add", target, "add", "recovered\n"))],
    )

    result = replay.replay_changes(
        database=database,
        repo=repo,
        thread_id="thread-1",
        start_ordinal=20,
        end_ordinal=20,
        apply=False,
    )

    assert not target.exists()
    assert result.changed_paths == ("new.txt",)


def test_replay_rejects_paths_outside_repository(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    outside = tmp_path / "outside.txt"
    database = _database(
        tmp_path / "history.sqlite",
        [(30, _event("outside", outside, "add", "unsafe\n"))],
    )

    with pytest.raises(replay.ReplayError, match="outside repository"):
        replay.replay_changes(
            database=database,
            repo=repo,
            thread_id="thread-1",
            start_ordinal=30,
            end_ordinal=30,
            apply=True,
        )


def test_failed_late_patch_leaves_repository_unchanged(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    target = repo / "notes.txt"
    target.write_text("original\n")
    database = _database(
        tmp_path / "history.sqlite",
        [
            (
                40,
                _event(
                    "valid",
                    target,
                    "update",
                    "@@ -1 +1 @@\n-original\n+changed\n",
                ),
            ),
            (
                41,
                _event(
                    "invalid",
                    target,
                    "update",
                    "@@ -1 +1 @@\n-missing\n+broken\n",
                ),
            ),
        ],
    )

    with pytest.raises(replay.ReplayError, match="invalid"):
        replay.replay_changes(
            database=database,
            repo=repo,
            thread_id="thread-1",
            start_ordinal=40,
            end_ordinal=41,
            apply=True,
        )

    assert target.read_text() == "original\n"


def test_replay_maps_paths_recorded_in_another_checkout(tmp_path: Path) -> None:
    recorded_repo = tmp_path / "recorded"
    target_repo = tmp_path / "target"
    recorded_repo.mkdir()
    target_repo.mkdir()
    recorded_path = recorded_repo / "docs" / "proof.txt"
    database = _database(
        tmp_path / "history.sqlite",
        [(50, _event("mapped", recorded_path, "add", "portable\n"))],
    )

    result = replay.replay_changes(
        database=database,
        repo=target_repo,
        recorded_repo=recorded_repo,
        thread_id="thread-1",
        start_ordinal=50,
        end_ordinal=50,
        apply=True,
    )

    assert (target_repo / "docs" / "proof.txt").read_text() == "portable\n"
    assert result.changed_paths == ("docs/proof.txt",)


def test_load_rejects_invalid_range_and_missing_database(tmp_path: Path) -> None:
    with pytest.raises(replay.ReplayError, match="start ordinal"):
        replay.replay_changes(
            database=tmp_path / "missing.sqlite",
            repo=tmp_path,
            thread_id="thread-1",
            start_ordinal=2,
            end_ordinal=1,
            apply=False,
        )
    with pytest.raises(replay.ReplayError, match="database does not exist"):
        replay.replay_changes(
            database=tmp_path / "missing.sqlite",
            repo=tmp_path,
            thread_id="thread-1",
            start_ordinal=1,
            end_ordinal=1,
            apply=False,
        )


def test_load_rejects_bad_schema_and_empty_selection(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    malformed = tmp_path / "malformed.sqlite"
    connection = sqlite3.connect(malformed)
    connection.execute("CREATE TABLE unrelated (value TEXT)")
    connection.close()
    with pytest.raises(replay.ReplayError, match="thread projection"):
        replay.replay_changes(
            database=malformed,
            repo=repo,
            thread_id="thread-1",
            start_ordinal=1,
            end_ordinal=1,
            apply=False,
        )

    valid = _database(
        tmp_path / "valid.sqlite",
        [(1, _event("event", repo / "one.txt", "add", "one\n"))],
    )
    with pytest.raises(replay.ReplayError, match="no completed"):
        replay.replay_changes(
            database=valid,
            repo=repo,
            thread_id="another-thread",
            start_ordinal=1,
            end_ordinal=1,
            apply=False,
        )


@pytest.mark.parametrize(
    ("record", "message"),
    [
        ({"type": "other", "status": "completed", "changes": [{}]}, "item type"),
        ({"type": "fileChange", "status": "running", "changes": [{}]}, "incomplete"),
        ({"type": "fileChange", "status": "completed", "changes": []}, "no changes"),
    ],
)
def test_load_rejects_malformed_records(
    tmp_path: Path,
    record: dict[str, object],
    message: str,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    record["id"] = "bad-record"
    database = _database(tmp_path / "history.sqlite", [(1, record)])

    with pytest.raises(replay.ReplayError, match=message):
        replay.replay_changes(
            database=database,
            repo=repo,
            thread_id="thread-1",
            start_ordinal=1,
            end_ordinal=1,
            apply=False,
        )


def test_load_rejects_invalid_json(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    database = _database(
        tmp_path / "history.sqlite",
        [(1, _event("bad-json", repo / "one.txt", "add", "one\n"))],
    )
    connection = sqlite3.connect(database)
    connection.execute("UPDATE thread_items SET item_json = '{'")
    connection.commit()
    connection.close()

    with pytest.raises(replay.ReplayError, match="invalid JSON"):
        replay.replay_changes(
            database=database,
            repo=repo,
            thread_id="thread-1",
            start_ordinal=1,
            end_ordinal=1,
            apply=False,
        )


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"kind": {"type": "add"}, "diff": "one\n"}, "no path"),
        ({"path": None, "kind": {"type": "add"}, "diff": "one\n"}, "no path"),
    ],
)
def test_replay_rejects_changes_without_paths(
    tmp_path: Path,
    change: dict[str, object],
    message: str,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    record = {
        "type": "fileChange",
        "id": "bad-path",
        "status": "completed",
        "changes": [change],
    }
    database = _database(tmp_path / "history.sqlite", [(1, record)])
    with pytest.raises(replay.ReplayError, match=message):
        replay.replay_changes(
            database=database,
            repo=repo,
            thread_id="thread-1",
            start_ordinal=1,
            end_ordinal=1,
            apply=False,
        )


def test_replay_rejects_repository_root_and_symlink(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    root_db = _database(
        tmp_path / "root.sqlite",
        [(1, _event("root", repo, "add", "unsafe\n"))],
    )
    with pytest.raises(replay.ReplayError, match="root cannot"):
        replay.replay_changes(
            database=root_db,
            repo=repo,
            thread_id="thread-1",
            start_ordinal=1,
            end_ordinal=1,
            apply=False,
        )

    target = repo / "linked.txt"
    target.symlink_to(tmp_path / "outside.txt")
    link_db = _database(
        tmp_path / "link.sqlite",
        [(1, _event("link", target, "update", "@@ -0,0 +1 @@\n+unsafe\n"))],
    )
    with pytest.raises(replay.ReplayError, match=r"outside repository|symlink"):
        replay.replay_changes(
            database=link_db,
            repo=repo,
            thread_id="thread-1",
            start_ordinal=1,
            end_ordinal=1,
            apply=False,
        )


@pytest.mark.parametrize(
    ("kind", "diff", "message"),
    [
        ("add", "new\n", "already exists"),
        ("update", "@@ -1 +1 @@\n-old\n+new\n", "target is missing"),
        ("unknown", "new\n", "unsupported"),
    ],
)
def test_replay_rejects_invalid_change_transitions(
    tmp_path: Path,
    kind: str,
    diff: str,
    message: str,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    target = repo / "target.txt"
    if kind == "add":
        target.write_text("old\n")
    database = _database(
        tmp_path / "history.sqlite",
        [(1, _event(kind, target, kind, diff))],
    )
    with pytest.raises(replay.ReplayError, match=message):
        replay.replay_changes(
            database=database,
            repo=repo,
            thread_id="thread-1",
            start_ordinal=1,
            end_ordinal=1,
            apply=False,
        )


def test_replay_rejects_missing_diff_and_deletes_valid_target(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    target = repo / "target.txt"
    target.write_text("old\n")
    missing_diff = {
        "type": "fileChange",
        "id": "missing-diff",
        "status": "completed",
        "changes": [{"path": str(target), "kind": {"type": "update"}}],
    }
    bad_db = _database(tmp_path / "bad.sqlite", [(1, missing_diff)])
    with pytest.raises(replay.ReplayError, match="missing diff"):
        replay.replay_changes(
            database=bad_db,
            repo=repo,
            thread_id="thread-1",
            start_ordinal=1,
            end_ordinal=1,
            apply=False,
        )

    delete_db = _database(
        tmp_path / "delete.sqlite",
        [(1, _event("delete", target, "delete", "old\n"))],
    )
    replay.replay_changes(
        database=delete_db,
        repo=repo,
        thread_id="thread-1",
        start_ordinal=1,
        end_ordinal=1,
        apply=True,
    )
    assert not target.exists()


def test_main_reports_success_and_failure(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    target = repo / "target.txt"
    database = _database(
        tmp_path / "history.sqlite",
        [(1, _event("cli", target, "add", "one\n"))],
    )
    common = [
        "--repo",
        str(repo),
        "--thread-id",
        "thread-1",
        "--start-ordinal",
        "1",
        "--end-ordinal",
        "1",
    ]
    assert replay.main(["--database", str(database), *common]) == 0
    assert "VALIDATED" in capsys.readouterr().out

    assert replay.main(["--database", str(tmp_path / "missing.sqlite"), *common]) == 2
    assert "database does not exist" in capsys.readouterr().err
