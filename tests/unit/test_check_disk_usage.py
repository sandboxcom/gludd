from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "check_disk_usage.py"
DISK_GUARD_SCRIPT = ROOT / "scripts" / "disk-guard.sh"


def _load_module():
    spec = importlib.util.spec_from_file_location("check_disk_usage_under_test", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _porcelain_worktree(path: Path, *, prunable: bool = False) -> bytes:
    fields = [
        f"worktree {path}".encode(),
        b"HEAD 0123456789abcdef0123456789abcdef01234567",
        b"branch refs/heads/test",
    ]
    if prunable:
        fields.append(b"prunable gitdir file points to non-existent location")
    return b"\0".join(fields) + b"\0\0"


def test_tmp_size_excludes_only_registered_worktree_source_and_venv(
    tmp_path: Path,
) -> None:
    module = _load_module()
    scratch = tmp_path / "gludd-collect-output.txt"
    scratch.write_bytes(b"scratch-bytes")

    worktree_root = tmp_path / "gludd-worktrees"
    registered = worktree_root / "registered"
    orphan = worktree_root / "orphan"
    for worktree in (registered, orphan):
        (worktree / "src").mkdir(parents=True)
        (worktree / ".venv").mkdir()
        (worktree / ".pytest_cache").mkdir()

    (registered / "src" / "source.py").write_bytes(b"registered-source")
    (registered / ".venv" / "python").write_bytes(b"registered-venv")
    (registered / ".pytest_cache" / "cache.bin").write_bytes(b"registered-cache")
    (orphan / "src" / "source.py").write_bytes(b"orphan-source")
    (orphan / ".venv" / "python").write_bytes(b"orphan-venv")
    (orphan / ".pytest_cache" / "cache.bin").write_bytes(b"orphan-cache")

    actual = module._gludd_tmp_size_mb(
        tmp_root=tmp_path,
        worktree_root=worktree_root,
        registered_worktrees={registered.resolve()},
    )
    expected = sum(
        map(
            len,
            (
                b"scratch-bytes",
                b"registered-cache",
                b"orphan-source",
                b"orphan-venv",
                b"orphan-cache",
            ),
        )
    ) / (1024 * 1024)

    assert actual == pytest.approx(expected)


def test_classification_proves_registered_bytes_are_observed_but_not_counted(
    tmp_path: Path,
) -> None:
    module = _load_module()
    worktree_root = tmp_path / "gludd-worktrees"
    registered = worktree_root / "registered"
    orphan = worktree_root / "orphan"
    (registered / ".venv").mkdir(parents=True)
    (registered / ".pytest_cache").mkdir()
    (orphan / ".venv").mkdir(parents=True)
    (registered / ".venv" / "python").write_bytes(b"registered-venv")
    (registered / ".pytest_cache" / "cache").write_bytes(b"cache")
    (orphan / ".venv" / "python").write_bytes(b"orphan-venv")

    entries = module._classify_gludd_tmp(
        tmp_root=tmp_path,
        worktree_root=worktree_root,
        registered_worktrees={registered.resolve()},
        observe_exempt=True,
    )
    by_path = {entry.path: entry for entry in entries}

    active = by_path[registered]
    assert active.category == "registered-worktree-generated"
    assert active.observed_size_bytes == len(b"registered-venv") + len(b"cache")
    assert active.counted_size_bytes == len(b"cache")
    abandoned = by_path[orphan]
    assert abandoned.category == "orphan-worktree"
    assert abandoned.observed_size_bytes == len(b"orphan-venv")
    assert abandoned.counted_size_bytes == len(b"orphan-venv")


def test_nested_registered_worktree_exempts_container_but_counts_orphan_sibling(
    tmp_path: Path,
) -> None:
    module = _load_module()
    worktree_root = tmp_path / "gludd-worktrees"
    container = worktree_root / "feature"
    registered = container / "s83-108-worktree-audit-identity"
    orphan = container / "abandoned-output"
    (registered / ".venv").mkdir(parents=True)
    (registered / ".pytest_cache").mkdir()
    orphan.mkdir()
    (registered / ".venv" / "python").write_bytes(b"active-venv")
    (registered / ".pytest_cache" / "cache").write_bytes(b"active-cache")
    (orphan / "artifact.bin").write_bytes(b"orphan-output")

    entries = module._classify_gludd_tmp(
        tmp_root=tmp_path,
        worktree_root=worktree_root,
        registered_worktrees={registered.resolve()},
        observe_exempt=True,
    )
    by_path = {entry.path: entry for entry in entries}

    assert container not in by_path
    assert by_path[registered].category == "registered-worktree-generated"
    assert by_path[registered].counted_size_bytes == len(b"active-cache")
    assert by_path[orphan].category == "orphan-worktree"
    assert by_path[orphan].counted_size_bytes == len(b"orphan-output")


def test_classification_report_is_bounded_and_machine_readable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    module = _load_module()
    entries = [
        module.ScratchClassification(
            path=tmp_path / f"gludd-{index}",
            category="generated-scratch",
            observed_size_bytes=index,
            counted_size_bytes=index,
        )
        for index in range(module.CLASSIFICATION_ENTRY_LIMIT + 2)
    ]
    monkeypatch.setattr(module, "_classify_gludd_tmp", lambda **kwargs: entries)

    assert module._print_classification_report() == 0
    records = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert len(records) == module.CLASSIFICATION_ENTRY_LIMIT + 1
    assert records[-1]["summary"]["omitted_entries"] == 2
    assert records[-1]["summary"]["total_entries"] == len(entries)


def test_registered_worktrees_use_git_stable_nul_porcelain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_module()
    worktree_root = tmp_path / "gludd-worktrees"
    registered = worktree_root / "feature" / "feature with spaces"
    registered.mkdir(parents=True)
    command_seen: list[str] = []

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        command_seen.extend(command)
        assert kwargs["cwd"] == module.REPOSITORY_ROOT
        assert kwargs["check"] is True
        assert kwargs["capture_output"] is True
        assert kwargs["timeout"] == 10
        return subprocess.CompletedProcess(command, 0, _porcelain_worktree(registered), b"")

    monkeypatch.setattr(module.subprocess, "run", fake_run)

    assert module._registered_worktree_paths(worktree_root) == {registered.resolve()}
    assert command_seen == ["git", "worktree", "list", "--porcelain", "-z"]


def test_prunable_or_missing_worktree_is_not_exempted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_module()
    worktree_root = tmp_path / "gludd-worktrees"
    stale = worktree_root / "stale-registration"
    stale.mkdir(parents=True)

    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0], 0, _porcelain_worktree(stale, prunable=True), b""
        ),
    )

    assert module._registered_worktree_paths(worktree_root) == set()


@pytest.mark.parametrize(
    "payload, message",
    [
        (b"worktree /tmp/incomplete\0", "malformed"),
        (b"HEAD abc\0\0", "malformed"),
        (b"worktree \0\0", "malformed"),
        (b"worktree relative/path\0\0", "relative path"),
    ],
)
def test_malformed_worktree_registry_is_fail_closed(
    tmp_path: Path, payload: bytes, message: str
) -> None:
    module = _load_module()

    with pytest.raises(module.WorktreeRegistryError, match=message):
        module._parse_registered_worktrees(payload, tmp_path / "gludd-worktrees")


def test_registry_does_not_exempt_missing_or_out_of_namespace_paths(
    tmp_path: Path,
) -> None:
    module = _load_module()
    worktree_root = tmp_path / "gludd-worktrees"
    worktree_root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    missing = worktree_root / "missing"
    payload = _porcelain_worktree(outside) + _porcelain_worktree(missing)

    assert module._parse_registered_worktrees(payload, worktree_root) == set()


def test_worktree_registry_failure_is_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_module()

    def fail_registry(*args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        raise subprocess.CalledProcessError(128, args[0])

    monkeypatch.setattr(module.subprocess, "run", fail_registry)

    with pytest.raises(module.WorktreeRegistryError, match="git worktree list failed"):
        module._registered_worktree_paths(tmp_path / "gludd-worktrees")


def test_unreadable_worktree_namespace_is_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_module()
    worktree_root = tmp_path / "gludd-worktrees"
    worktree_root.mkdir()
    original_iterdir = Path.iterdir

    def fail_for_namespace(path: Path):
        if path == worktree_root:
            raise PermissionError("denied")
        return original_iterdir(path)

    monkeypatch.setattr(Path, "iterdir", fail_for_namespace)

    with pytest.raises(module.DiskInspectionError, match="scratch inspection failed"):
        module._worktree_generated_size_bytes(worktree_root, set())


def test_tmp_size_counts_non_worktree_gludd_directories(tmp_path: Path) -> None:
    module = _load_module()
    generated_dir = tmp_path / "gludd-ci-shard-unit-3-123"
    generated_dir.mkdir()
    (generated_dir / "node.log").write_bytes(b"generated")

    actual = module._gludd_tmp_size_mb(tmp_root=tmp_path, worktree_root=tmp_path / "gludd-worktrees")
    expected = len(b"generated") / (1024 * 1024)

    assert actual == pytest.approx(expected)


def test_disk_percentage_limit_remains_fail_closed(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    module = _load_module()
    monkeypatch.setattr(module, "_gludd_tmp_inspection", lambda: (0.0, []))
    monkeypatch.setattr(
        module, "_disk_usage_pct", lambda: module.DISK_USAGE_PCT_LIMIT + 0.1
    )

    assert module.main() == 1
    assert "DISK FAIL: disk usage" in capsys.readouterr().err


def test_disk_usage_parser_reports_real_percentage(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load_module()
    command_seen: list[str] = []

    def fake_df(command: list[str], **kwargs: object) -> str:
        command_seen.extend(command)
        assert kwargs == {"text": True, "timeout": 10}
        return (
            "Filesystem 1024-blocks Used Available Capacity Mounted\n"
            "/dev/disk 100 92 8 92% /\n"
        )

    monkeypatch.setattr(
        module.subprocess,
        "check_output",
        fake_df,
    )

    assert module._disk_usage_pct() == 92.0
    assert command_seen == ["df", "-Pk", str(module.REPOSITORY_ROOT)]


@pytest.mark.parametrize(
    "failure",
    [subprocess.TimeoutExpired(["df", "/"], 10), ValueError("invalid output")],
)
def test_disk_usage_inspection_failure_is_fail_closed(
    monkeypatch: pytest.MonkeyPatch, failure: Exception
) -> None:
    module = _load_module()

    if isinstance(failure, ValueError):
        monkeypatch.setattr(
            module.subprocess,
            "check_output",
            lambda *args, **kwargs: "Filesystem\ninvalid\n",
        )
    else:
        def fail_df(*args: object, **kwargs: object) -> str:
            raise failure

        monkeypatch.setattr(module.subprocess, "check_output", fail_df)

    with pytest.raises(module.DiskInspectionError, match="disk usage inspection failed"):
        module._disk_usage_pct()


def test_main_fails_if_worktree_registry_is_unavailable(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    module = _load_module()
    monkeypatch.setattr(module, "_disk_usage_pct", lambda: 0.0)

    def fail_registry():
        raise module.WorktreeRegistryError("registry unavailable")

    monkeypatch.setattr(module, "_gludd_tmp_inspection", fail_registry)

    assert module.main() == 1
    assert "worktree registry unavailable" in capsys.readouterr().err


def test_main_fails_if_scratch_cannot_be_inspected(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    module = _load_module()
    monkeypatch.setattr(module, "_disk_usage_pct", lambda: 0.0)

    def fail_scratch():
        raise module.DiskInspectionError("scratch inspection failed")

    monkeypatch.setattr(module, "_gludd_tmp_inspection", fail_scratch)

    assert module.main() == 1
    assert "scratch inspection failed" in capsys.readouterr().err


def test_main_reports_healthy_bounded_usage(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    module = _load_module()
    monkeypatch.setattr(module, "_gludd_tmp_inspection", lambda: (1.0, []))
    monkeypatch.setattr(module, "_disk_usage_pct", lambda: 20.0)

    assert module.main() == 0
    assert "disk ok: generated /tmp/gludd-* scratch = 1.0 MB" in capsys.readouterr().out


def test_main_names_largest_counted_roots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    module = _load_module()
    largest = tmp_path / "gludd-orphan-large"
    smaller = tmp_path / "gludd-generated-smaller"
    entries = [
        module.ScratchClassification(
            largest, "orphan-worktree", 180 * 1024 * 1024, 180 * 1024 * 1024
        ),
        module.ScratchClassification(
            smaller, "generated-scratch", 20 * 1024 * 1024, 20 * 1024 * 1024
        ),
    ]
    monkeypatch.setattr(module, "_gludd_tmp_inspection", lambda: (200.0, entries))
    monkeypatch.setattr(module, "_disk_usage_pct", lambda: 20.0)

    assert module.main() == 1
    stderr = capsys.readouterr().err
    assert "orphan-worktree" in stderr
    assert str(largest) in stderr
    assert "180.0 MB" in stderr


def test_shell_disk_guard_uses_portable_single_line_df_output() -> None:
    source = DISK_GUARD_SCRIPT.read_text()

    assert 'df -Pk "$TARGET_DIR"' in source
    assert "awk 'END {gsub(/%/,\"\"); print $5}'" in source


def test_shell_disk_guard_never_deletes_global_pytest_roots() -> None:
    source = DISK_GUARD_SCRIPT.read_text()

    assert "rm -rf /tmp/pytest-of-*" not in source
    assert "rm -rf /private/tmp/pytest-of-*" not in source
    assert "rm -rf /tmp/gludd-iso-*" not in source
    assert "rm -rf /tmp/gludd-gate-basetemp" not in source


def test_shell_disk_guard_defers_while_project_validation_is_active() -> None:
    source = DISK_GUARD_SCRIPT.read_text()

    assert "active_project_validation" in source
    assert 'pgrep -f "${GLUDD_ROOT}/.*(pytest|mypy|ruff)"' in source
    assert "DISK_CLEANUP_DEFERRED" in source


def test_shell_disk_guard_removes_only_namespaced_node_download_caches() -> None:
    source = DISK_GUARD_SCRIPT.read_text()

    assert 'GLUDD_NODE_CACHE_DIRS=(' in source
    assert '"/tmp/gludd-npm-cache"' in source
    assert '"/tmp/gludd-npm-cache-public-v1"' in source
    assert 'rm -rf -- "$cache_dir"' in source
    assert "rm -rf /tmp/gludd-*" not in source
