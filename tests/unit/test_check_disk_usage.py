from __future__ import annotations

import importlib.util
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


def test_tmp_size_excludes_active_worktree_source_and_venv_but_counts_caches(
    tmp_path: Path,
) -> None:
    module = _load_module()
    scratch = tmp_path / "gludd-collect-output.txt"
    scratch.write_bytes(b"scratch-bytes")

    worktree_root = tmp_path / "gludd-worktrees"
    release_worktree = worktree_root / "release-sync"
    source_dir = release_worktree / "src"
    venv_dir = release_worktree / ".venv"
    cache_dir = release_worktree / ".pytest_cache"
    source_dir.mkdir(parents=True)
    venv_dir.mkdir(parents=True)
    cache_dir.mkdir(parents=True)
    (source_dir / "source.py").write_bytes(b"source-should-not-count")
    (venv_dir / "python").write_bytes(b"venv-should-not-count")
    (cache_dir / "cache.bin").write_bytes(b"cache-counts")

    actual = module._gludd_tmp_size_mb(tmp_root=tmp_path, worktree_root=worktree_root)
    expected = (len(b"scratch-bytes") + len(b"cache-counts")) / (1024 * 1024)

    assert actual == pytest.approx(expected)


def test_tmp_size_counts_non_worktree_gludd_directories(tmp_path: Path) -> None:
    module = _load_module()
    generated_dir = tmp_path / "gludd-ci-shard-unit-3-123"
    generated_dir.mkdir()
    (generated_dir / "node.log").write_bytes(b"generated")

    actual = module._gludd_tmp_size_mb(tmp_root=tmp_path, worktree_root=tmp_path / "gludd-worktrees")
    expected = len(b"generated") / (1024 * 1024)

    assert actual == pytest.approx(expected)


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
