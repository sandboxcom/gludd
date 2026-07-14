"""Structural tests for self_update/module_snapshot.py."""

from __future__ import annotations

from types import ModuleType

from general_ludd.self_update.module_snapshot import (
    ModuleSnapshot,
    _is_extension_module,
    _warn_singletons,
    find_live_references,
    restore_modules,
    snapshot_modules,
)


class TestModuleSnapshot:
    def test_construct_empty(self):
        snap = ModuleSnapshot()
        assert snap.snapshot_at == 0.0
        assert snap.warnings == []
        assert snap.modules == {}

    def test_bool_false_when_empty(self):
        snap = ModuleSnapshot()
        assert not bool(snap)

    def test_bool_true_when_has_modules(self):
        snap = ModuleSnapshot()
        snap.modules["os"] = __import__("os")
        assert bool(snap) is True

    def test_snapshot_at_settable(self):
        snap = ModuleSnapshot(snapshot_at=42.0)
        assert snap.snapshot_at == 42.0

    def test_warnings_settable(self):
        snap = ModuleSnapshot(warnings=["warn1"])
        assert snap.warnings == ["warn1"]


class TestSnapshotModules:
    def test_empty_list_returns_empty_snapshot(self):
        snap = snapshot_modules([])
        assert not bool(snap)

    def test_nonexistent_module_skipped(self):
        snap = snapshot_modules(["nonexistent_module_xyz"])
        assert not bool(snap)

    def test_known_module_snapshotted(self):
        snap = snapshot_modules(["os"])
        assert bool(snap)
        assert "os" in snap.modules
        assert snap.snapshot_at > 0.0

    def test_returns_module_snapshot_type(self):
        snap = snapshot_modules(["os"])
        assert isinstance(snap, ModuleSnapshot)


class TestRestoreModules:
    def test_restore_empty_snapshot(self):
        snap = ModuleSnapshot()
        restored = restore_modules(snap)
        assert restored == []

    def test_restore_returns_list_of_names(self):
        snap = snapshot_modules(["os"])
        restored = restore_modules(snap)
        assert isinstance(restored, list)


class TestFindLiveReferences:
    def test_nonexistent_module_returns_empty(self):
        refs = find_live_references("nonexistent_module_xyz")
        assert refs == []

    def test_returns_list_of_strings(self):
        refs = find_live_references("os")
        assert isinstance(refs, list)


class TestIsExtensionModule:
    def test_regular_python_module_is_false(self):
        import os
        assert _is_extension_module(os) is False

    def test_module_without_file_is_false(self):
        mod = ModuleType("fake")
        assert _is_extension_module(mod) is False

    def test_extension_suffix_dylib_detected(self):
        mod = ModuleType("fake_ext")
        mod.__file__ = "foo.dylib"
        assert _is_extension_module(mod) is True

    def test_extension_suffix_so_detected(self):
        mod = ModuleType("fake_ext")
        mod.__file__ = "foo.so"
        assert _is_extension_module(mod) is True

    def test_extension_suffix_pyd_detected(self):
        mod = ModuleType("fake_ext")
        mod.__file__ = "foo.pyd"
        assert _is_extension_module(mod) is True

    def test_extension_loader_detected(self):
        mod = ModuleType("fake_ext")

        class ExtensionFileLoader:
            pass

        mod.__loader__ = ExtensionFileLoader()
        assert _is_extension_module(mod) is True


class TestWarnSingletons:
    def test_no_warnings_for_plain_module(self):
        mod = ModuleType("plain")
        mod.x = 1
        warnings: list[str] = []
        _warn_singletons("plain", mod, warnings)
        assert warnings == []

    def test_warns_on_pool_attribute(self):
        mod = ModuleType("srv")
        mod.pool = object()
        warnings: list[str] = []
        _warn_singletons("srv", mod, warnings)
        assert len(warnings) >= 1
        assert "srv.pool" in warnings[0]

    def test_skips_private_attributes(self):
        mod = ModuleType("srv")
        mod._pool = object()
        warnings: list[str] = []
        _warn_singletons("srv", mod, warnings)
        assert warnings == []

    def test_skips_callable_attributes(self):
        mod = ModuleType("srv")

        def get_pool():
            pass

        mod.get_pool = get_pool
        warnings: list[str] = []
        _warn_singletons("srv", mod, warnings)
        assert warnings == []
