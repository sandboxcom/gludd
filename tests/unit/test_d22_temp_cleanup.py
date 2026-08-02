"""D-22: Per-run temp roots with ownership manifests and scoped cleanup."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from general_ludd.security.temp_cleanup import (
    TempRoot,
    TempRootError,
    cleanup_all_temp_roots,
    compute_age_seconds,
    is_temp_root_expired,
    register_temp_root,
    unregister_temp_root,
)


class TestTempRootCreation:
    def test_create_temp_root(self, tmp_path: Path) -> None:
        parent = tmp_path / "temp-parent"
        parent.mkdir()
        root = TempRoot.create(prefix="test-", parent=parent)
        assert root.root.exists()
        assert root.root.name.startswith("test-")
        assert root.manifest_path.exists()
        assert root.manifest_path.name == ".temp-root-manifest.json"
        assert root.max_bytes == 100 * 1024 * 1024
        assert root.max_age_seconds == 3600.0
        root.cleanup()
        assert not root.root.exists()

    def test_create_rejects_relative_parent(self) -> None:
        with pytest.raises(TempRootError, match="absolute"):
            TempRoot.create(prefix="test-", parent=Path("relative"))

    def test_create_rejects_symlink_parent(self, tmp_path: Path) -> None:
        real = tmp_path / "real"
        real.mkdir()
        link = tmp_path / "link"
        os.symlink(str(real), str(link))
        with pytest.raises(TempRootError, match="symlink"):
            TempRoot.create(prefix="test-", parent=link)

    def test_create_work_dir_is_created(self, tmp_path: Path) -> None:
        parent = tmp_path / "parent"
        parent.mkdir()
        root = TempRoot.create(prefix="w-", parent=parent)
        work = root.root / "work"
        assert work.is_dir()
        root.cleanup()


class TestTempRootBounds:
    def test_check_bounds_passes_on_empty(self, tmp_path: Path) -> None:
        parent = tmp_path / "p"
        parent.mkdir()
        root = TempRoot.create(prefix="b-", parent=parent)
        root.check_bounds()  # should not raise
        root.cleanup()

    def test_check_bounds_fails_above_max_bytes(self, tmp_path: Path) -> None:
        parent = tmp_path / "p"
        parent.mkdir()
        root = TempRoot.create(prefix="b-", parent=parent, max_bytes=10)
        (root.root / "work" / "big.dat").write_bytes(b"x" * 20)
        with pytest.raises(TempRootError, match="exceeded max"):
            root.check_bounds()
        root.cleanup()

    def test_check_bounds_fails_missing_manifest(self, tmp_path: Path) -> None:
        parent = tmp_path / "p"
        parent.mkdir()
        root = TempRoot.create(prefix="b-", parent=parent)
        root.manifest_path.unlink()
        with pytest.raises(TempRootError, match="manifest"):
            root.check_bounds()
        root.root.rmdir()  # manifest already gone, just remove root

    def test_is_temp_root_expired(self, tmp_path: Path) -> None:
        parent = tmp_path / "p"
        parent.mkdir()
        root = TempRoot.create(prefix="e-", parent=parent, max_age_seconds=0.01)
        time.sleep(0.02)
        assert is_temp_root_expired(root)
        assert is_temp_root_expired(root, max_age_seconds=3600) is False
        root.cleanup()

    def test_compute_age_seconds(self, tmp_path: Path) -> None:
        parent = tmp_path / "p"
        parent.mkdir()
        root = TempRoot.create(prefix="a-", parent=parent)
        age = compute_age_seconds(root.manifest_path)
        assert age >= 0
        root.cleanup()

    def test_compute_age_seconds_missing_manifest(self, tmp_path: Path) -> None:
        age = compute_age_seconds(tmp_path / "nonexistent.json")
        assert age == float("inf")


class TestTempRootRegistration:
    def test_register_and_unregister(self, tmp_path: Path) -> None:
        parent = tmp_path / "p"
        parent.mkdir()
        root = TempRoot.create(prefix="r-", parent=parent)
        register_temp_root(root)
        from general_ludd.security.temp_cleanup import _registry

        key = str(root.root)
        assert key in _registry
        unregister_temp_root(root)
        assert key not in _registry
        root.cleanup()


class TestCleanupAll:
    def test_cleanup_expired_roots(self, tmp_path: Path) -> None:
        parent = tmp_path / "parent"
        parent.mkdir()
        root = TempRoot.create(prefix="c-", parent=parent, max_age_seconds=0.01)
        time.sleep(0.02)
        cleaned = cleanup_all_temp_roots(manifest_root=parent, max_age_seconds=0.005)
        assert str(root.root) in cleaned
        assert not root.root.exists()


class TestTempRootCleanup:
    def test_cleanup_is_idempotent(self, tmp_path: Path) -> None:
        parent = tmp_path / "p"
        parent.mkdir()
        root = TempRoot.create(prefix="i-", parent=parent)
        root.cleanup()
        assert not root.root.exists()
        root.cleanup()  # should not raise
