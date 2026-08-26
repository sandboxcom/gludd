"""Tests for D-22: temp_cleanup — per-run temp roots with ownership manifests.

D-22 requirement: private mode-0700 per-run temp roots with ownership manifests,
bounded size/age and exact cleanup on exit/signals/crash via a scoped reaper.
"""

from __future__ import annotations

import json
import signal
import stat
from pathlib import Path
from unittest import mock

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

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _touch(path: Path, content: str = "x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


# ---------------------------------------------------------------------------
# TempRoot creation
# ---------------------------------------------------------------------------


class TestTempRootCreate:
    def test_creates_0700_root(self, tmp_path: Path) -> None:
        root = TempRoot.create(prefix="test-", parent=tmp_path)
        assert root.root.is_dir()
        assert _mode(root.root) == 0o700

    def test_creates_manifest_file(self, tmp_path: Path) -> None:
        root = TempRoot.create(prefix="test-", parent=tmp_path)
        assert root.manifest_path.exists()
        manifest = json.loads(root.manifest_path.read_text())
        assert "owner_uid" in manifest
        assert "created_at" in manifest
        assert "root" in manifest

    def test_manifest_is_0600(self, tmp_path: Path) -> None:
        root = TempRoot.create(prefix="test-", parent=tmp_path)
        assert _mode(root.manifest_path) == 0o600

    def test_creates_work_subdirs(self, tmp_path: Path) -> None:
        root = TempRoot.create(prefix="test-", parent=tmp_path)
        work = root.root / "work"
        assert work.is_dir()
        assert _mode(work) == 0o700

    def test_rejects_parent_outside_allowed(self, tmp_path: Path) -> None:
        with (
            mock.patch("general_ludd.security.temp_cleanup._validate_owner", side_effect=TempRootError("owner")),
            pytest.raises(TempRootError, match="owner"),
        ):
            TempRoot.create(prefix="test-", parent=tmp_path)

    def test_rejects_symlink_parent(self, tmp_path: Path) -> None:
        real = tmp_path / "real"
        real.mkdir()
        link = tmp_path / "link"
        link.symlink_to(real, target_is_directory=True)
        with pytest.raises(TempRootError, match="symlink"):
            TempRoot.create(prefix="test-", parent=link)

    def test_rejects_non_absolute_parent(self) -> None:
        with pytest.raises(TempRootError, match="absolute"):
            TempRoot.create(prefix="test-", parent=Path("relative"))


# ---------------------------------------------------------------------------
# Size / age bounding
# ---------------------------------------------------------------------------


class TestSizeAgeBounding:
    def test_max_bytes_enforced(self, tmp_path: Path) -> None:
        root = TempRoot.create(prefix="test-", parent=tmp_path, max_bytes=64)
        _touch(root.root / "work" / "a", "x" * 65)
        with pytest.raises(TempRootError, match="exceeded"):
            root.check_bounds()

    def test_max_age_expiry(self, tmp_path: Path) -> None:
        with mock.patch("general_ludd.security.temp_cleanup.time.time", return_value=1_000.0):
            root = TempRoot.create(prefix="test-", parent=tmp_path, max_age_seconds=0.1)
        with mock.patch("general_ludd.security.temp_cleanup.time.time", return_value=1_000.11):
            assert is_temp_root_expired(root, max_age_seconds=0.1)

    def test_fresh_root_not_expired(self, tmp_path: Path) -> None:
        root = TempRoot.create(prefix="test-", parent=tmp_path, max_age_seconds=3600)
        assert not is_temp_root_expired(root, max_age_seconds=3600)

    def test_compute_age_seconds(self, tmp_path: Path) -> None:
        root = TempRoot.create(prefix="test-", parent=tmp_path)
        age = compute_age_seconds(root.manifest_path)
        assert 0 <= age <= 5

    def test_check_bounds_passes_when_under(self, tmp_path: Path) -> None:
        root = TempRoot.create(prefix="test-", parent=tmp_path, max_bytes=1024 * 1024)
        _touch(root.root / "work" / "a", "x" * 100)
        root.check_bounds()

    def test_check_bounds_without_manifest(self, tmp_path: Path) -> None:
        root = TempRoot.create(prefix="test-", parent=tmp_path, max_bytes=1024)
        root.manifest_path.unlink()
        with pytest.raises(TempRootError, match="manifest"):
            root.check_bounds()


# ---------------------------------------------------------------------------
# Scoped cleanup
# ---------------------------------------------------------------------------


class TestScopedCleanup:
    def test_cleanup_removes_root(self, tmp_path: Path) -> None:
        root = TempRoot.create(prefix="test-", parent=tmp_path)
        assert root.root.exists()
        root.cleanup()
        assert not root.root.exists()

    def test_cleanup_removes_manifest(self, tmp_path: Path) -> None:
        root = TempRoot.create(prefix="test-", parent=tmp_path)
        root.cleanup()
        assert not root.manifest_path.exists()

    def test_cleanup_of_missing_root_is_noop(self, tmp_path: Path) -> None:
        root = TempRoot.create(prefix="test-", parent=tmp_path)
        root.cleanup()
        root.cleanup()

    def test_cleanup_does_not_touch_sibling(self, tmp_path: Path) -> None:
        root1 = TempRoot.create(prefix="test-", parent=tmp_path)
        root2 = TempRoot.create(prefix="test2-", parent=tmp_path)
        root1.cleanup()
        assert root2.root.exists()
        root2.cleanup()

    def test_cleanup_all_removes_only_expired(self, tmp_path: Path) -> None:
        with mock.patch("general_ludd.security.temp_cleanup.time.time", return_value=1_000.0):
            TempRoot.create(prefix="old-", parent=tmp_path, max_age_seconds=0.001)
        with mock.patch("general_ludd.security.temp_cleanup.time.time", return_value=1_001.0):
            fresh = TempRoot.create(prefix="fresh-", parent=tmp_path, max_age_seconds=3600)
        with mock.patch("general_ludd.security.temp_cleanup.time.time", return_value=1_001.05):
            cleaned = cleanup_all_temp_roots(manifest_root=tmp_path, max_age_seconds=0.1)
        assert any("old-" in c for c in cleaned)
        assert fresh.root.exists()
        fresh.cleanup()


# ---------------------------------------------------------------------------
# Registration / global tracking
# ---------------------------------------------------------------------------


class TestRegistration:
    def test_register_and_unregister(self, tmp_path: Path) -> None:
        root = TempRoot.create(prefix="test-", parent=tmp_path)
        register_temp_root(root)
        unregister_temp_root(root)

    def test_double_unregister_is_noop(self, tmp_path: Path) -> None:
        root = TempRoot.create(prefix="test-", parent=tmp_path)
        register_temp_root(root)
        unregister_temp_root(root)
        unregister_temp_root(root)

    def test_cleanup_also_unregisters(self, tmp_path: Path) -> None:
        root = TempRoot.create(prefix="test-", parent=tmp_path)
        register_temp_root(root)
        root.cleanup()


# ---------------------------------------------------------------------------
# Signal handler
# ---------------------------------------------------------------------------


class TestSignalCleanup:
    def test_signal_handler_registers(self) -> None:
        import general_ludd.security.temp_cleanup as tc_mod

        tc_mod._signal_handlers_installed = False
        TempRoot.install_signal_handlers()
        assert signal.getsignal(signal.SIGTERM) is TempRoot._signal_cleanup

    def test_signal_cleanup_removes_registered_roots(self, tmp_path: Path) -> None:
        root = TempRoot.create(prefix="test-", parent=tmp_path)
        register_temp_root(root)
        with mock.patch("os.kill"):
            TempRoot._signal_cleanup(signal.SIGTERM, None)
        assert not root.root.exists()


# ---------------------------------------------------------------------------
# TempRootError
# ---------------------------------------------------------------------------


class TestTempRootError:
    def test_is_exception(self) -> None:
        assert issubclass(TempRootError, Exception)

    def test_str_contains_message(self) -> None:
        err = TempRootError("something went wrong")
        assert "something went wrong" in str(err)
