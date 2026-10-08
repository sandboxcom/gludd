"""Branch coverage for project-scoped collection lock ownership."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import scripts.collection_lock as locks

ROOT = Path(__file__).parents[2]


def _git_common_dir_result(
    common_dirs: dict[Path, Path],
):
    def run(
        args: list[str],
        *,
        capture_output: bool,
        text: bool,
        timeout: float,
        check: bool,
    ) -> subprocess.CompletedProcess[str]:
        assert args[:2] == ["git", "-C"]
        assert args[3:] == ["rev-parse", "--git-common-dir"]
        assert capture_output is True
        assert text is True
        assert timeout > 0
        assert check is False
        checkout = Path(args[2]).resolve()
        common_dir = common_dirs[checkout]
        return subprocess.CompletedProcess(args, 0, f"{common_dir}\n", "")

    return run


def test_linked_worktrees_share_one_repository_collection_lease(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    main = tmp_path / "main"
    linked = tmp_path / "linked"
    clone = tmp_path / "independent-clone"
    common = main / ".git"
    clone_common = clone / ".git"
    for directory in (main, linked, clone, common, clone_common):
        directory.mkdir(exist_ok=True)
    monkeypatch.delenv("GLUDD_COLLECTION_LOCK", raising=False)
    monkeypatch.setenv("GLUDD_RESOURCE_ROOT", str(tmp_path / "resources"))
    monkeypatch.setattr(locks, "project_root", lambda start=None: Path(start).resolve())
    monkeypatch.setattr(
        locks.subprocess,
        "run",
        _git_common_dir_result(
            {
                main.resolve(): common.resolve(),
                linked.resolve(): common.resolve(),
                clone.resolve(): clone_common.resolve(),
            }
        ),
    )

    main_lock = locks.default_collection_lock(main)
    linked_lock = locks.default_collection_lock(linked)
    clone_lock = locks.default_collection_lock(clone)

    assert main_lock == linked_lock
    assert main_lock != clone_lock
    assert main_lock.parent.parent == (tmp_path / "resources").resolve()
    assert all(char.isalnum() or char in "_.-" for char in main_lock.parent.name)
    with (
        locks.collection_lock(main_lock, timeout=0),
        pytest.raises(TimeoutError, match="collection lock is busy"),
        locks.collection_lock(linked_lock, timeout=0),
    ):
        pass


def test_repository_identity_failure_is_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GLUDD_COLLECTION_LOCK", raising=False)
    monkeypatch.setattr(locks, "project_root", lambda start=None: tmp_path)
    monkeypatch.setattr(
        locks.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0], 128, "", "not a repository"
        ),
    )

    with pytest.raises(locks.RepositoryIdentityError, match="common directory"):
        locks.default_collection_lock(tmp_path)


def test_resource_paths_and_timeout_overrides(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configured = tmp_path / "configured.lock"
    monkeypatch.setenv("GLUDD_COLLECTION_LOCK", str(configured))
    assert locks.default_resource_lock() == configured

    monkeypatch.delenv("GLUDD_COLLECTION_LOCK")
    monkeypatch.setattr(
        locks,
        "repository_resource_lock",
        lambda resource, start=None: tmp_path / resource,
    )
    assert locks.default_collection_lock() == tmp_path / "collection"
    assert locks.default_resource_lock("gate-refresh") == tmp_path / "gate-refresh"

    monkeypatch.setenv("GLUDD_COLLECTION_LOCK_TIMEOUT", "7.5")
    assert locks.lock_timeout() == 7.5
    assert locks.lock_timeout("gate-refresh") == 7.5
    monkeypatch.setenv("GLUDD_GATE_REFRESH_LOCK_TIMEOUT", "2.5")
    assert locks.lock_timeout("gate-refresh") == 2.5


def test_collection_lock_validates_times_out_and_releases(tmp_path: Path) -> None:
    path = tmp_path / "locks" / "collection.lock"
    with pytest.raises(ValueError, match="non-negative"), locks.collection_lock(
        path, timeout=-1
    ):
        pass
    with pytest.raises(ValueError, match="positive"), locks.collection_lock(
        path, poll_interval=0
    ):
        pass

    with locks.collection_lock(path, timeout=0) as acquired:
        assert acquired == path
        assert path.read_text(encoding="utf-8").startswith("pid=")
        with pytest.raises(
            TimeoutError, match="collection lock is busy"
        ), locks.collection_lock(path, timeout=0):
            pass

    with locks.collection_lock(path, timeout=0) as reacquired:
        assert reacquired == path


def test_lease_record_parser_is_bounded_and_ignores_unknown_content() -> None:
    record = locks.parse_lease_record(
        "pid=123\nacquired_unix_ns=456\nnote=do-not-echo-this\n"
    )

    assert record == locks.LeaseRecord(pid=123, acquired_unix_ns=456)
    assert locks.parse_lease_record("pid=123\n") == locks.LeaseRecord(
        pid=123,
        acquired_unix_ns=None,
    )
    assert locks.parse_lease_record("pid=123\nacquired_unix_ns=invalid\n") is None
    assert locks.parse_lease_record("pid=123\npid=456\n") is None
    assert locks.parse_lease_record("pid=-1\n") is None
    assert locks.parse_lease_record("x" * (locks.MAX_LEASE_RECORD_BYTES + 1)) is None


def test_lease_inspection_distinguishes_missing_stale_and_held(
    tmp_path: Path,
) -> None:
    path = tmp_path / "collection.lock"

    missing = locks.inspect_lease(path)
    assert missing == locks.LeaseInspection(state="available", record=None)

    path.write_text("pid=123\nacquired_unix_ns=456\n", encoding="utf-8")
    stale = locks.inspect_lease(path)
    assert stale == locks.LeaseInspection(
        state="available",
        record=locks.LeaseRecord(pid=123, acquired_unix_ns=456),
    )

    with locks.collection_lock(path, timeout=0):
        held = locks.inspect_lease(path)
        assert held.state == "held"
        assert held.record is not None


def test_run_locked_propagates_child_status(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    command = ["tool", "--check"]
    observed: list[list[str]] = []

    def completed(args: list[str], *, check: bool) -> subprocess.CompletedProcess[list[str]]:
        assert check is False
        observed.append(args)
        return subprocess.CompletedProcess(args, 23)

    monkeypatch.setattr(
        locks,
        "default_resource_lock",
        lambda resource: tmp_path / resource,
    )
    monkeypatch.setattr(subprocess, "run", completed)
    assert locks.run_locked(command, timeout=0) == 23
    assert observed == [command]
    output = capsys.readouterr().out
    assert "collection lock waiting: resource=collection" in output
    assert "collection lock acquired: resource=collection" in output
    assert "collection lock released: resource=collection" in output
    assert "waited=" in output
    assert "elapsed=" in output


def test_main_validates_arguments_and_maps_busy_lock_to_temporary_failure(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert locks.main([]) == 2
    assert locks.main(["--resource", "gate-refresh"]) == 2
    assert locks.main(["tool"]) == 2

    observed: list[tuple[list[str], str]] = []

    def successful(command: list[str], *, resource: str) -> int:
        observed.append((command, resource))
        return 17

    monkeypatch.setattr(locks, "run_locked", successful)
    assert locks.main(["--resource", "gate-refresh", "--run", "tool", "arg"]) == 17
    assert observed == [(["tool", "arg"], "gate-refresh")]

    def busy(_command: list[str], *, resource: str) -> int:
        raise TimeoutError(resource)

    monkeypatch.setattr(locks, "run_locked", busy)
    assert locks.main(["--run", "tool"]) == 75

    def unidentified(_command: list[str], *, resource: str) -> int:
        raise locks.RepositoryIdentityError(resource)

    monkeypatch.setattr(locks, "run_locked", unidentified)
    assert locks.main(["--run", "tool"]) == 75
    assert "collection lock unavailable" in capsys.readouterr().err
