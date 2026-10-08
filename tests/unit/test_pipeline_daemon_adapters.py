"""Unit tests for the pipeline daemon adapters + config wiring (#77)."""

from __future__ import annotations

import subprocess
import tempfile
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest

from general_ludd.config.user_config import UserConfig
from general_ludd.controllers.load_scrape import LoadSnapshot
from general_ludd.pipeline import daemon_adapters
from general_ludd.pipeline.daemon_adapters import (
    make_disk_ok,
    make_dispatch_fn,
    make_merge_fn,
    make_pid_provider,
)
from general_ludd.pipeline.state import CompletedUnit, MergeOutcome


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
        timeout=15,
    )
    return result.stdout.strip()


def _commit_base(repo: Path, relpath: str, content: str) -> str:
    repo.mkdir()
    _git(repo, "init", "--quiet")
    path = repo / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    _git(repo, "add", "--", relpath)
    _git(
        repo,
        "-c",
        "user.name=Gludd Test",
        "-c",
        "user.email=gludd-test@example.invalid",
        "commit",
        "--quiet",
        "-m",
        "base",
    )
    return _git(repo, "rev-parse", "HEAD")


class _FakeDispatcher:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def dispatch_one(self, task: object) -> object:
        self.calls.append(getattr(task, "task_id", "?"))
        return {"status": "completed"}


class TestDispatchAdapter:
    @pytest.mark.asyncio
    async def test_dispatch_fn_routes_to_dispatcher(self) -> None:
        disp = _FakeDispatcher()
        fn = make_dispatch_fn(disp)
        await fn("unit-7")
        assert disp.calls == ["pipeline-unit-7"]


class TestMergeAdapter:
    @pytest.mark.asyncio
    async def test_missing_base_fails_closed_preserves_repo_and_worktree(
        self, tmp_path: Path
    ) -> None:
        repo = tmp_path / "repo"
        wt = tmp_path / "wt"
        repo.mkdir()
        wt.mkdir()
        (repo / "f.txt").write_text("repo version\n")
        (wt / "f.txt").write_text("worktree version\n")

        reclaimed: list[str] = []
        fn = make_merge_fn(
            str(repo),
            changed_files=lambda unit: ["f.txt"],
            reclaim=reclaimed.append,
        )

        outcome = await fn(CompletedUnit("u-missing-base", str(wt)))

        assert outcome.merged is False
        assert outcome.clobber_refused is True
        assert outcome.detail == "no_base_sha"
        assert (repo / "f.txt").read_text() == "repo version\n"
        assert (wt / "f.txt").read_text() == "worktree version\n"
        assert reclaimed == []

    @pytest.mark.asyncio
    async def test_clean_merge_writes_and_reclaims(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        wt = tmp_path / "wt"
        base_sha = _commit_base(repo, "f.txt", "line1\nline2\n")
        wt.mkdir()
        (wt / "f.txt").write_text("line1\nline2\nline3\n")

        reclaimed: list[str] = []
        fn = make_merge_fn(
            str(repo),
            changed_files=lambda u: ["f.txt"],
            reclaim=reclaimed.append,
        )
        outcome = await fn(CompletedUnit("u1", str(wt), base_sha=base_sha))
        assert outcome.merged is True
        assert (repo / "f.txt").read_text() == "line1\nline2\nline3\n"
        assert reclaimed == [str(wt)]

    @pytest.mark.asyncio
    async def test_conflict_refuses_clobber_and_preserves(
        self, tmp_path: Path
    ) -> None:
        repo = tmp_path / "repo"
        wt = tmp_path / "wt"
        base_sha = _commit_base(repo, "f.txt", "a\nb\nc\n")
        wt.mkdir()
        (repo / "f.txt").write_text("a\nREPO\nc\n")
        (wt / "f.txt").write_text("a\nWORKTREE\nc\n")
        reclaimed: list[str] = []
        fn = make_merge_fn(
            str(repo),
            changed_files=lambda unit: ["f.txt"],
            reclaim=reclaimed.append,
        )

        outcome = await fn(CompletedUnit("u-conflict", str(wt), base_sha=base_sha))

        assert outcome.merged is False
        assert outcome.clobber_refused is True
        assert outcome.detail == "conflict:f.txt"
        assert (repo / "f.txt").read_text() == "a\nREPO\nc\n"
        assert (wt / "f.txt").read_text() == "a\nWORKTREE\nc\n"
        assert reclaimed == []

    @pytest.mark.asyncio
    async def test_disjoint_real_base_edits_merge_both_sides(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = tmp_path / "repo"
        wt = tmp_path / "wt"
        base_sha = _commit_base(repo, "f.txt", "top\nmiddle\nbottom\n")
        wt.mkdir()
        (repo / "f.txt").write_text("TOP\nmiddle\nbottom\n")
        (wt / "f.txt").write_text("top\nmiddle\nBOTTOM\n")
        reclaimed: list[str] = []
        fn = make_merge_fn(
            str(repo),
            changed_files=lambda unit: ["f.txt"],
            reclaim=reclaimed.append,
        )
        temp_roots: list[str] = []
        real_merge = daemon_adapters._merge_file_with_git

        def record_temp_root(
            repo_path: str,
            relpath: str,
            ours: bytes,
            base: bytes,
            theirs: bytes,
            temp_root: str,
        ) -> daemon_adapters._FileMerge:
            temp_roots.append(temp_root)
            return real_merge(repo_path, relpath, ours, base, theirs, temp_root)

        monkeypatch.setattr(
            daemon_adapters, "_merge_file_with_git", record_temp_root
        )

        outcome = await fn(CompletedUnit("u-disjoint", str(wt), base_sha=base_sha))

        assert outcome.merged is True
        assert outcome.detail == "merged"
        assert (repo / "f.txt").read_text() == "TOP\nmiddle\nBOTTOM\n"
        assert reclaimed == [str(wt)]
        assert temp_roots
        assert all(not Path(root).exists() for root in temp_roots)

    @pytest.mark.asyncio
    async def test_new_file_added_by_agent(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        wt = tmp_path / "wt"
        base_sha = _commit_base(repo, "sentinel.txt", "base\n")
        wt.mkdir()
        (wt / "new.txt").write_text("brand new\n")
        reclaimed: list[str] = []
        fn = make_merge_fn(
            str(repo), changed_files=lambda u: ["new.txt"], reclaim=reclaimed.append,
        )
        outcome = await fn(CompletedUnit("u1", str(wt), base_sha=base_sha))
        assert outcome.merged is True
        assert (repo / "new.txt").read_text() == "brand new\n"
        assert reclaimed == [str(wt)]

    @pytest.mark.asyncio
    async def test_merge_tool_error_fails_closed_without_diagnostics_leak(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        repo = tmp_path / "repo"
        wt = tmp_path / "wt"
        base_sha = _commit_base(repo, "f.txt", "base\n")
        wt.mkdir()
        (wt / "f.txt").write_text("worktree\n")
        real_run = subprocess.run
        merge_kwargs: list[dict[str, object]] = []

        def fail_merge_file(
            args: list[str], **kwargs: Any
        ) -> subprocess.CompletedProcess[Any]:
            if "merge-file" in args:
                merge_kwargs.append(kwargs)
                return subprocess.CompletedProcess(
                    args,
                    255,
                    stdout=b"",
                    stderr=b"secret diagnostic" * 10_000,
                )
            return real_run(args, **kwargs)

        monkeypatch.setattr(subprocess, "run", fail_merge_file)
        reclaimed: list[str] = []
        fn = make_merge_fn(
            str(repo),
            changed_files=lambda unit: ["f.txt"],
            reclaim=reclaimed.append,
        )

        outcome = await fn(CompletedUnit("u-tool-error", str(wt), base_sha=base_sha))

        assert outcome.merged is False
        assert outcome.clobber_refused is True
        assert outcome.detail == "merge_tool_error:f.txt"
        assert "secret" not in outcome.detail
        assert (repo / "f.txt").read_text() == "base\n"
        assert reclaimed == []
        assert merge_kwargs == [
            {"capture_output": True, "check": False, "timeout": 15}
        ]
        assert max(len(record.message) for record in caplog.records) < 5000

    @pytest.mark.asyncio
    async def test_merge_tool_timeout_preserves_both_sides(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = tmp_path / "repo"
        wt = tmp_path / "wt"
        base_sha = _commit_base(repo, "f.txt", "base\n")
        wt.mkdir()
        (wt / "f.txt").write_text("worktree\n")
        real_run = subprocess.run

        def timeout_merge_file(
            args: list[str], **kwargs: Any
        ) -> subprocess.CompletedProcess[Any]:
            if "merge-file" in args:
                raise subprocess.TimeoutExpired(args, kwargs["timeout"])
            return real_run(args, **kwargs)

        monkeypatch.setattr(subprocess, "run", timeout_merge_file)
        reclaimed: list[str] = []
        fn = make_merge_fn(
            str(repo),
            changed_files=lambda unit: ["f.txt"],
            reclaim=reclaimed.append,
        )

        outcome = await fn(CompletedUnit("u-timeout", str(wt), base_sha=base_sha))

        assert outcome.detail == "merge_tool_error:f.txt"
        assert outcome.merged is False
        assert (repo / "f.txt").read_text() == "base\n"
        assert reclaimed == []

    @pytest.mark.asyncio
    async def test_admission_resource_ceilings_fail_closed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        assert daemon_adapters._MAX_CHANGED_FILES == 256
        assert daemon_adapters._MAX_FILE_BYTES == 8 * 1024 * 1024
        assert daemon_adapters._MAX_TOTAL_BYTES == 64 * 1024 * 1024
        assert daemon_adapters._GIT_TIMEOUT_SECONDS == 15

        repo = tmp_path / "repo"
        wt = tmp_path / "wt"
        base_sha = _commit_base(repo, "f.txt", "base\n")
        wt.mkdir()
        (wt / "f.txt").write_text("theirs\n")
        reclaimed: list[str] = []

        too_many = make_merge_fn(
            str(repo),
            changed_files=lambda unit: [f"f-{index}.txt" for index in range(257)],
            reclaim=reclaimed.append,
        )
        too_many_outcome = await too_many(
            CompletedUnit("u-files", str(wt), base_sha=base_sha)
        )
        assert too_many_outcome.detail == "too_many_files"

        monkeypatch.setattr(daemon_adapters, "_MAX_FILE_BYTES", 4)
        too_large = make_merge_fn(
            str(repo),
            changed_files=lambda unit: ["f.txt"],
            reclaim=reclaimed.append,
        )
        too_large_outcome = await too_large(
            CompletedUnit("u-file-size", str(wt), base_sha=base_sha)
        )
        assert too_large_outcome.detail == "file_too_large:f.txt"

        monkeypatch.setattr(daemon_adapters, "_MAX_FILE_BYTES", 8 * 1024 * 1024)
        monkeypatch.setattr(daemon_adapters, "_MAX_TOTAL_BYTES", 10)
        total_limited = make_merge_fn(
            str(repo),
            changed_files=lambda unit: ["f.txt"],
            reclaim=reclaimed.append,
        )
        total_outcome = await total_limited(
            CompletedUnit("u-total-size", str(wt), base_sha=base_sha)
        )
        assert total_outcome.detail == "total_size_exceeded"
        assert (repo / "f.txt").read_text() == "base\n"
        assert reclaimed == []

    @pytest.mark.asyncio
    async def test_empty_changeset_is_clean_noop(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        reclaimed: list[str] = []
        fn = make_merge_fn(
            str(repo), changed_files=lambda u: [], reclaim=reclaimed.append,
        )
        outcome = await fn(CompletedUnit("u1", "/wt/u1"))
        assert outcome.merged is True
        assert outcome.detail == "empty"
        assert reclaimed == ["/wt/u1"]


class TestMergeAdmissionFailures:
    @pytest.mark.parametrize(
        "tree_output",
        [
            b"x" * 300,
            (
                b"100644 blob " + b"a" * 40 + b"\tf.txt\x00"
                b"100644 blob " + b"b" * 40 + b"\tf.txt\x00"
            ),
            b"malformed\x00",
            b"040000 tree " + b"a" * 40 + b"\tf.txt\x00",
        ],
    )
    def test_fork_point_rejects_malformed_tree_records(
        self, monkeypatch: pytest.MonkeyPatch, tree_output: bytes
    ) -> None:
        result = subprocess.CompletedProcess([], 0, tree_output, b"")
        monkeypatch.setattr(daemon_adapters, "_run_git", lambda *args: result)

        read = daemon_adapters._read_fork_point("/repo", "a" * 40, "f.txt")

        assert read.status == "tool_error"

    @pytest.mark.parametrize(
        ("responses", "expected"),
        [
            ([None], "tool_error"),
            ([subprocess.CompletedProcess([], 1, b"", b"bad tree")], "tool_error"),
            (
                [
                    subprocess.CompletedProcess(
                        [], 0, b"100644 blob " + b"a" * 40 + b"\tf.txt\x00", b""
                    ),
                    subprocess.CompletedProcess([], 1, b"", b"bad size"),
                ],
                "tool_error",
            ),
            (
                [
                    subprocess.CompletedProcess(
                        [], 0, b"100644 blob " + b"a" * 40 + b"\tf.txt\x00", b""
                    ),
                    subprocess.CompletedProcess([], 0, b"not-a-size", b""),
                ],
                "tool_error",
            ),
            (
                [
                    subprocess.CompletedProcess(
                        [], 0, b"100644 blob " + b"a" * 40 + b"\tf.txt\x00", b""
                    ),
                    subprocess.CompletedProcess([], 0, b"-1", b""),
                ],
                "tool_error",
            ),
            (
                [
                    subprocess.CompletedProcess(
                        [], 0, b"100644 blob " + b"a" * 40 + b"\tf.txt\x00", b""
                    ),
                    subprocess.CompletedProcess([], 0, b"8388609", b""),
                ],
                "too_large",
            ),
            (
                [
                    subprocess.CompletedProcess(
                        [], 0, b"100644 blob " + b"a" * 40 + b"\tf.txt\x00", b""
                    ),
                    subprocess.CompletedProcess([], 0, b"4", b""),
                    subprocess.CompletedProcess([], 1, b"", b"bad blob"),
                ],
                "tool_error",
            ),
            (
                [
                    subprocess.CompletedProcess(
                        [], 0, b"100644 blob " + b"a" * 40 + b"\tf.txt\x00", b""
                    ),
                    subprocess.CompletedProcess([], 0, b"4", b""),
                    subprocess.CompletedProcess([], 0, b"bad", b""),
                ],
                "tool_error",
            ),
        ],
    )
    def test_fork_point_distinguishes_git_and_object_failures(
        self,
        monkeypatch: pytest.MonkeyPatch,
        responses: list[subprocess.CompletedProcess[bytes] | None],
        expected: str,
    ) -> None:
        pending = list(responses)
        monkeypatch.setattr(
            daemon_adapters, "_run_git", lambda *args: pending.pop(0)
        )

        read = daemon_adapters._read_fork_point("/repo", "a" * 40, "f.txt")

        assert read.status == expected

    def test_fork_point_rejects_symbolic_revision_and_bounds_diagnostics(self) -> None:
        assert daemon_adapters._read_fork_point("/repo", "HEAD", "f.txt").status == (
            "invalid_base"
        )
        assert daemon_adapters._bounded_diagnostic(None) == ""
        assert daemon_adapters._bounded_diagnostic(" a\x00b ") == "a?b"

    @pytest.mark.asyncio
    async def test_invalid_paths_duplicates_and_discovery_errors_fail_closed(
        self, tmp_path: Path
    ) -> None:
        repo = tmp_path / "repo"
        wt = tmp_path / "wt"
        repo.mkdir()
        wt.mkdir()
        reclaimed: list[str] = []

        def discovery_error(unit: CompletedUnit) -> list[str]:
            raise RuntimeError(unit.unit_id)

        cases: list[tuple[Any, str]] = [
            (discovery_error, "changed_files_error"),
            (lambda unit: ["f.txt", "f.txt"], "invalid_changed_files"),
            (lambda unit: [123], "invalid_changed_files"),
            (lambda unit: ["../escape"], "unsafe_path:../escape"),
            (lambda unit: [".git/config"], "unsafe_path:.git/config"),
        ]
        for changed_files, detail in cases:
            outcome = await make_merge_fn(
                str(repo), changed_files=changed_files, reclaim=reclaimed.append
            )(CompletedUnit("u-guard", str(wt), base_sha="a" * 40))
            assert outcome.detail == detail
            assert outcome.merged is False
        assert reclaimed == []

    @pytest.mark.asyncio
    async def test_absence_symlink_and_concurrent_add_modes(
        self, tmp_path: Path
    ) -> None:
        repo = tmp_path / "repo"
        wt = tmp_path / "wt"
        base_sha = _commit_base(repo, "sentinel.txt", "base\n")
        wt.mkdir()
        reclaimed: list[str] = []

        absent = await make_merge_fn(
            str(repo),
            changed_files=lambda unit: ["gone.txt"],
            reclaim=reclaimed.append,
        )(CompletedUnit("u-absent", str(wt), base_sha=base_sha))
        assert absent.merged is True

        (wt / "f.txt").write_text("theirs\n")
        (repo / "target.txt").write_text("target\n")
        (repo / "f.txt").symlink_to(repo / "target.txt")
        bad_repo = await make_merge_fn(
            str(repo),
            changed_files=lambda unit: ["f.txt"],
            reclaim=reclaimed.append,
        )(CompletedUnit("u-repo-link", str(wt), base_sha=base_sha))
        assert bad_repo.detail == "repo_read_error:f.txt"

        (repo / "f.txt").unlink()
        (repo / "f.txt").write_text("ours\n")
        (wt / "f.txt").unlink()
        bad_worktree = await make_merge_fn(
            str(repo),
            changed_files=lambda unit: ["f.txt"],
            reclaim=reclaimed.append,
        )(CompletedUnit("u-wt-missing", str(wt), base_sha=base_sha))
        assert bad_worktree.detail == "worktree_read_error:f.txt"

        (wt / "f.txt").write_text("theirs\n")
        invalid_base = await make_merge_fn(
            str(repo),
            changed_files=lambda unit: ["f.txt"],
            reclaim=reclaimed.append,
        )(CompletedUnit("u-bad-base", str(wt), base_sha="HEAD"))
        assert invalid_base.detail == "base_tool_error:f.txt"

        concurrent_add = await make_merge_fn(
            str(repo),
            changed_files=lambda unit: ["f.txt"],
            reclaim=reclaimed.append,
        )(CompletedUnit("u-two-adds", str(wt), base_sha=base_sha))
        assert concurrent_add.detail == "conflict:f.txt"

    @pytest.mark.asyncio
    async def test_delete_modify_conflict_and_delete_preservation(
        self, tmp_path: Path
    ) -> None:
        repo = tmp_path / "repo"
        wt = tmp_path / "wt"
        base_sha = _commit_base(repo, "f.txt", "base\n")
        wt.mkdir()
        (repo / "f.txt").unlink()
        (wt / "f.txt").write_text("modified\n")
        reclaimed: list[str] = []
        def fn() -> Callable[[CompletedUnit], Awaitable[MergeOutcome]]:
            return make_merge_fn(
                str(repo),
                changed_files=lambda unit: ["f.txt"],
                reclaim=reclaimed.append,
            )

        conflict = await fn()(CompletedUnit("u-delete-modify", str(wt), base_sha=base_sha))
        assert conflict.detail == "conflict:f.txt"

        (wt / "f.txt").write_text("base\n")
        preserved = await fn()(CompletedUnit("u-delete-only", str(wt), base_sha=base_sha))
        assert preserved.merged is True
        assert not (repo / "f.txt").exists()
        assert reclaimed == [str(wt)]

    @pytest.mark.asyncio
    async def test_post_merge_limits_temp_failure_and_write_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = tmp_path / "repo"
        wt = tmp_path / "wt"
        base_sha = _commit_base(repo, "f.txt", "base\n")
        wt.mkdir()
        (wt / "f.txt").write_text("theirs\n")
        reclaimed: list[str] = []

        monkeypatch.setattr(
            daemon_adapters,
            "_merge_file_with_git",
            lambda *args: daemon_adapters._FileMerge("too_large"),
        )
        too_large = await make_merge_fn(
            str(repo), changed_files=lambda unit: ["f.txt"], reclaim=reclaimed.append
        )(CompletedUnit("u-result-large", str(wt), base_sha=base_sha))
        assert too_large.detail == "file_too_large:f.txt"

        monkeypatch.setattr(daemon_adapters, "_MAX_TOTAL_BYTES", 18)
        monkeypatch.setattr(
            daemon_adapters,
            "_merge_file_with_git",
            lambda *args: daemon_adapters._FileMerge("ok", b"merged\n"),
        )
        total = await make_merge_fn(
            str(repo), changed_files=lambda unit: ["f.txt"], reclaim=reclaimed.append
        )(CompletedUnit("u-result-total", str(wt), base_sha=base_sha))
        assert total.detail == "total_size_exceeded"

        class BrokenTemporaryDirectory:
            def __init__(self, **kwargs: object) -> None:
                pass

            def __enter__(self) -> str:
                raise OSError("no temp space")

            def __exit__(self, *args: object) -> None:
                pass

        monkeypatch.setattr(daemon_adapters, "_MAX_TOTAL_BYTES", 64 * 1024 * 1024)
        real_temporary_directory = tempfile.TemporaryDirectory
        monkeypatch.setattr(
            tempfile, "TemporaryDirectory", BrokenTemporaryDirectory
        )
        temporary = await make_merge_fn(
            str(repo), changed_files=lambda unit: ["f.txt"], reclaim=reclaimed.append
        )(CompletedUnit("u-temp", str(wt), base_sha=base_sha))
        assert temporary.detail == "temporary_file_error"

        monkeypatch.setattr(
            tempfile, "TemporaryDirectory", real_temporary_directory
        )
        monkeypatch.setattr(
            daemon_adapters, "_commit_with_rollback", lambda merged, originals: False
        )
        write = await make_merge_fn(
            str(repo), changed_files=lambda unit: ["f.txt"], reclaim=reclaimed.append
        )(CompletedUnit("u-write", str(wt), base_sha=base_sha))
        assert write.detail == "write_error"
        assert reclaimed == []

    def test_write_failure_rolls_back_an_already_replaced_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        first = tmp_path / "first.txt"
        second = tmp_path / "second.txt"
        first.write_bytes(b"old-first")
        second.write_bytes(b"old-second")
        real_replace = daemon_adapters._atomic_replace

        def fail_second(path: str, content: bytes) -> None:
            if path == str(second):
                raise OSError("disk full")
            real_replace(path, content)

        monkeypatch.setattr(daemon_adapters, "_atomic_replace", fail_second)

        committed = daemon_adapters._commit_with_rollback(
            {str(first): b"new-first", str(second): b"new-second"},
            {str(first): b"old-first", str(second): b"old-second"},
        )

        assert committed is False
        assert first.read_bytes() == b"old-first"
        assert second.read_bytes() == b"old-second"


class TestDiskOk:
    def test_disk_ok_true_under_floor(self, tmp_path: Path) -> None:
        # A tiny floor (1 MiB) is essentially always satisfied.
        assert make_disk_ok(str(tmp_path), floor_mib=1)() is True

    def test_disk_pressure_with_huge_floor(self, tmp_path: Path) -> None:
        # An absurd floor (1 EiB) can never be satisfied -> back-pressure.
        assert make_disk_ok(str(tmp_path), floor_mib=10**12)() is False


class TestPidProviderAdapter:
    """The PID provider wires gludd's existing LoadController into the lane."""

    def _snapshot(self, *, loadavg_10m: float, cpu: int = 8) -> LoadSnapshot:
        return LoadSnapshot(
            loadavg_1m=0.0, loadavg_5m=0.0, loadavg_10m=loadavg_10m,
            logical_cpu_count=cpu, cpu_percent=0.0,
            memory_available_percent=100.0, disk_free_percent=100.0,
            active_jobs=0,
        )

    def test_provider_returns_controller_outputs(self) -> None:
        from general_ludd.schemas.queue import Queue

        queues = [Queue(queue_name="core", resource_profile="low_resource", soft_cap=3)]
        provider = make_pid_provider(
            queues, scrape=lambda: self._snapshot(loadavg_10m=0.1),
        )
        outputs = provider()
        assert outputs is not None
        # Low load -> soft_cap honoured for the queue + as the aggregate total.
        assert outputs.desired_active_buckets_by_queue["core"] == 3
        assert outputs.desired_total_active_buckets == 3

    def test_provider_reflects_load_throttle(self) -> None:
        from general_ludd.schemas.queue import Queue

        queues = [Queue(queue_name="ansible", resource_profile="local_heavy", soft_cap=4)]
        provider = make_pid_provider(
            queues, scrape=lambda: self._snapshot(loadavg_10m=99.0, cpu=8),
        )
        outputs = provider()
        assert outputs is not None
        # local_heavy under high load halves the buckets (4 -> 2).
        assert outputs.desired_active_buckets_by_queue["ansible"] == 2

    def test_provider_yields_none_on_scrape_error(self) -> None:
        from general_ludd.schemas.queue import Queue

        def boom() -> None:
            raise RuntimeError("psutil unavailable")

        queues = [Queue(queue_name="core", resource_profile="low_resource")]
        provider = make_pid_provider(queues, scrape=boom)
        # Never raises; yields None so the lane falls back to its static target.
        assert provider() is None


class TestUserConfigPipelineBlock:
    def test_default_pipeline_disabled(self) -> None:
        uc = UserConfig()
        assert uc.pipeline.enabled is False
        assert uc.pipeline.floor == 1
        assert uc.pipeline.target == 3

    def test_pipeline_block_from_dict(self) -> None:
        uc = UserConfig.model_validate(
            {"pipeline": {"enabled": True, "floor": 2, "target": 4, "max_worktrees": 8}}
        )
        assert uc.pipeline.enabled is True
        assert uc.pipeline.floor == 2
        assert uc.pipeline.target == 4
        assert uc.pipeline.max_worktrees == 8

    def test_pipeline_env_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GLUDD_PIPELINE__ENABLED", "true")
        monkeypatch.setenv("GLUDD_PIPELINE__TARGET", "5")
        uc = UserConfig()
        assert uc.pipeline.enabled is True
        assert uc.pipeline.target == 5
