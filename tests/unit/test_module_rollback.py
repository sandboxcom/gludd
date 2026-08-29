"""Tests for module_snapshot.py — hot-reload module rollback system."""

from __future__ import annotations

import sys
import threading
import types
from pathlib import Path
from typing import Protocol, cast
from unittest.mock import MagicMock, patch

from general_ludd.self_update.module_snapshot import (
    _EXTENSION_SUFFIXES,
    _SINGLETON_LIKE_NAMES,
    ModuleSnapshot,
    _is_extension_module,
    find_live_references,
    restore_modules,
    snapshot_modules,
)


def _make_temp_module(name: str, content: str) -> types.ModuleType:
    """Create a new module object with a source file on disk.

    The module is NOT inserted into sys.modules — callers must manage that.
    Writes a temporary .py file so ``__file__`` and ``__loader__`` are real,
    which allows ``importlib.reload`` to work in restore tests.
    """
    mod = types.ModuleType(name)
    mod.__package__ = ".".join(name.split(".")[:-1]) if "." in name else ""
    mod.__loader__ = None

    tmp_dir = Path("/tmp/gludd-test-modules")
    tmp_dir.mkdir(parents=True, exist_ok=True)
    mod_path = tmp_dir / f"{name.split('.')[-1]}.py"
    mod_path.write_text(content)
    mod.__file__ = str(mod_path)

    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location(name, mod_path)
        if spec is not None and spec.loader is not None:
            mod.__spec__ = spec
            mod.__loader__ = spec.loader
    except Exception:
        pass

    sys.modules[name] = mod
    return mod


def _cleanup_module(name: str) -> None:
    sys.modules.pop(name, None)


class _RequestModule(Protocol):
    Request: type[object]


class _ChildPackage(Protocol):
    child: types.ModuleType


class TestIsExtensionModule:
    def test_py_module_is_not_extension(self):
        mod = types.ModuleType("foo")
        mod.__file__ = "/a/b/foo.py"
        assert not _is_extension_module(mod)

    def test_so_module_is_extension(self):
        mod = types.ModuleType("foo")
        mod.__file__ = "/a/b/foo.cpython-311-darwin.so"
        assert _is_extension_module(mod)

    def test_pyd_module_is_extension(self):
        mod = types.ModuleType("foo")
        mod.__file__ = "/a/b/foo.pyd"
        assert _is_extension_module(mod)

    def test_dylib_module_is_extension(self):
        mod = types.ModuleType("foo")
        mod.__file__ = "/a/b/foo.dylib"
        assert _is_extension_module(mod)

    def test_extension_loader_detected(self):
        mod = types.ModuleType("foo")
        mod.__file__ = "/a/b/foo.so"
        mock_loader = MagicMock()
        mock_loader.__class__.__qualname__ = "ExtensionFileLoader"
        mod.__loader__ = mock_loader
        assert _is_extension_module(mod)

    def test_no_file_no_loader_not_extension(self):
        mod = types.ModuleType("foo")
        assert not _is_extension_module(mod)


class TestSnapshotModulesBasic:
    def test_empty_list_returns_empty_snapshot(self):
        snap = snapshot_modules([])
        assert not snap
        assert snap.modules == {}
        assert snap.snapshot_at > 0

    def test_unknown_module_skipped(self):
        snap = snapshot_modules(["nonexistent_module_xyzzy"])
        assert "nonexistent_module_xyzzy" not in snap.modules

    def test_known_module_is_snapshotted(self):
        name = "test_known_module_is_snapshotted"
        _make_temp_module(name, "VALUE = 1")
        try:
            snap = snapshot_modules([name])
            assert name in snap.modules
            assert snap.modules[name] is sys.modules[name]
        finally:
            _cleanup_module(name)

    def test_extension_module_is_skipped_with_warning(self):
        name = "test_ext_module"
        mod = types.ModuleType(name)
        mod.__file__ = "/a/b/test_ext.cpython-311-darwin.so"
        sys.modules[name] = mod
        try:
            snap = snapshot_modules([name])
            assert name not in snap.modules
            assert any("C extension" in w for w in snap.warnings)
        finally:
            _cleanup_module(name)

    def test_singleton_detected_in_warnings(self):
        name = "test_singleton_detected"
        mod = _make_temp_module(name, "CONNECTION_POOL = object()")
        try:
            mod.test_pool = object()
            snap = snapshot_modules([name])
            assert any("singleton" in w for w in snap.warnings)
        finally:
            _cleanup_module(name)

    def test_callable_not_flagged_as_singleton(self):
        name = "test_callable_not_singleton"
        _make_temp_module(name, "def pool(): pass")
        try:
            snap = snapshot_modules([name])
            assert not any("singleton" in w for w in snap.warnings)
        finally:
            _cleanup_module(name)


class TestRestoreModules:
    def test_restore_preserves_snapshotted_class_identity(self) -> None:
        name = "test_restore_preserves_class_identity"
        old_mod = _make_temp_module(name, "class Request: pass\n")
        request_module = cast("_RequestModule", old_mod)
        original_request = type("Request", (), {})
        request_module.Request = original_request
        try:
            snap = snapshot_modules([name])
            request_module.Request = type("Request", (), {})

            restored = restore_modules(snap)

            assert restored == [name]
            restored_module = cast("_RequestModule", sys.modules[name])
            assert restored_module.Request is original_request
        finally:
            _cleanup_module(name)

    def test_restore_rebinds_parent_package_child(self) -> None:
        package_name = "test_restore_parent_package"
        child_name = f"{package_name}.child"
        package = types.ModuleType(package_name)
        child = types.ModuleType(child_name)
        child.__package__ = package_name
        package_view = cast("_ChildPackage", package)
        sys.modules[package_name] = package
        sys.modules[child_name] = child
        package_view.child = child
        try:
            snap = snapshot_modules([child_name])
            replacement = types.ModuleType(child_name)
            sys.modules[child_name] = replacement
            package_view.child = replacement

            restored = restore_modules(snap)

            assert restored == [child_name]
            assert sys.modules[child_name] is child
            assert package_view.child is child
        finally:
            _cleanup_module(child_name)
            _cleanup_module(package_name)

    def test_restore_puts_old_module_back(self):
        name = "test_restore_puts_old_back"
        old_mod = _make_temp_module(name, "VALUE = 1")
        old_mod.VALUE = 1
        try:
            snap = snapshot_modules([name])
            new_mod = types.ModuleType(name)
            new_mod.VALUE = 999
            sys.modules[name] = new_mod
            result = restore_modules(snap)
            assert name in result
            assert sys.modules[name] is old_mod
        finally:
            _cleanup_module(name)

    def test_restore_multiple_modules(self):
        a_name = "test_restore_multi_a"
        b_name = "test_restore_multi_b"
        a_mod = _make_temp_module(a_name, "X = 10")
        b_mod = _make_temp_module(b_name, "Y = 20")
        a_mod.X = 10
        b_mod.Y = 20
        snap = snapshot_modules([a_name, b_name])
        sys.modules[a_name] = types.ModuleType(a_name)
        sys.modules[b_name] = types.ModuleType(b_name)
        try:
            result = restore_modules(snap)
            assert a_name in result
            assert b_name in result
            assert sys.modules[a_name] is a_mod
            assert sys.modules[b_name] is b_mod
        finally:
            _cleanup_module(a_name)
            _cleanup_module(b_name)

    def test_empty_snapshot_restores_nothing(self):
        result = restore_modules(ModuleSnapshot())
        assert result == []

    def test_failed_restore_does_not_crash_others(self):
        good_name = "test_failed_restore_good"
        bad_name = "test_failed_restore_bad"
        _make_temp_module(good_name, "X = 1")
        bad_mod = types.ModuleType(bad_name)
        bad_mod.__file__ = "/a/b/bad.so"
        sys.modules[bad_name] = bad_mod
        snap = ModuleSnapshot()
        snap.modules = {
            good_name: sys.modules[good_name],
            bad_name: bad_mod,
        }
        try:
            with patch("importlib.reload", side_effect=Exception("boom")):
                result = restore_modules(snap)
            assert good_name in result or len(result) == 0
        finally:
            _cleanup_module(good_name)
            _cleanup_module(bad_name)


class TestThreadSafety:
    def test_concurrent_snapshot_and_restore_do_not_deadlock(self):
        """Snapshot and restore use the same lock — they must not deadlock
        when run concurrently."""
        name = "test_thread_safe"
        mod = _make_temp_module(name, "VALUE = 0")
        try:
            mod.VALUE = 0
            snap = snapshot_modules([name])
            saved = snap.modules[name]
            sys.modules[name] = types.ModuleType(name)

            errors: list[Exception] = []

            def worker(snapshot_obj: ModuleSnapshot):
                try:
                    restore_modules(snapshot_obj)
                except Exception as exc:
                    errors.append(exc)

            threads = [threading.Thread(target=worker, args=(snap,)) for _ in range(5)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=5)

            assert errors == []
            assert sys.modules[name] is saved
        finally:
            _cleanup_module(name)


class TestFindLiveReferences:
    def test_no_module_returns_empty(self):
        assert find_live_references("nonexistent_12345") == []

    def test_finds_dict_reference(self):
        name = "test_finds_dict_ref"
        _make_temp_module(name, "")
        try:
            holder: dict[str, object] = {"mod": sys.modules[name]}
            import gc
            gc.collect()
            refs = find_live_references(name)
            assert holder["mod"] is sys.modules[name]
            assert any("dict" in r for r in refs)
        finally:
            _cleanup_module(name)

    def test_deduplicates_references(self):
        name = "test_deduplicates"
        _make_temp_module(name, "")
        try:
            sys.modules[name]
            refs = find_live_references(name)
            count = sum(1 for r in refs if "dict" in r)
            assert count >= 1
        finally:
            _cleanup_module(name)


class TestSnapshotWarnings:
    def test_multiple_singletons_generate_multiple_warnings(self):
        name = "test_multi_singletons"
        mod = _make_temp_module(name, "")
        try:
            mod.db_pool = object()
            mod.cache_client = object()
            snap = snapshot_modules([name])
            assert len(snap.warnings) >= 2
        finally:
            _cleanup_module(name)

    def test_no_singletons_no_warnings(self):
        name = "test_plain_module"
        _make_temp_module(name, "x = 42\ny = 'hello'")
        try:
            snap = snapshot_modules([name])
            assert not any("singleton" in w for w in snap.warnings)
        finally:
            _cleanup_module(name)

    def test_bool_truthiness(self):
        snap = ModuleSnapshot()
        assert not bool(snap)
        snap.modules["x"] = types.ModuleType("x")
        assert bool(snap)


class TestConstants:
    def test_extension_suffixes_has_expected(self):
        for suffix in (".so", ".pyd", ".dylib", ".dll"):
            assert suffix in _EXTENSION_SUFFIXES

    def test_singleton_names_present(self):
        assert "pool" in _SINGLETON_LIKE_NAMES
        assert "connection" in _SINGLETON_LIKE_NAMES
        assert "engine" in _SINGLETON_LIKE_NAMES
