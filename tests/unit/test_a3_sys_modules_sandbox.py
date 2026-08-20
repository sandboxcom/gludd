from __future__ import annotations

import sys
from typing import Any, cast

import tests.conftest as ct


def test_fixture_exists_in_conftest():
    assert hasattr(ct, "_sandbox_sys_modules_and_path")
    assert hasattr(ct, "_snapshot_sys_modules_and_path")
    assert hasattr(ct, "_restore_sys_modules_and_path")
    assert hasattr(ct, "_snapshot_import_state")
    assert hasattr(ct, "_restore_import_state")
    assert hasattr(ct, "_load_path_module_isolated")

    fixture = ct._sandbox_sys_modules_and_path
    marker = getattr(fixture, "_pytestfixturefunction", None)
    if marker is not None:
        kwargs = marker.kwargs if hasattr(marker, "kwargs") else {}
        assert kwargs.get("autouse") is True


def test_denylisted_module_removed_after_test():
    """Inject a denylist-prefixed module; snapshot + restore; verify evicted."""
    assert "live_pkg_test_fake_a3" not in sys.modules

    snap_modules, snap_path = ct._snapshot_sys_modules_and_path()
    sys.modules["live_pkg_test_fake_a3"] = "fake_stub"
    assert "live_pkg_test_fake_a3" in sys.modules
    ct._restore_sys_modules_and_path(snap_modules, snap_path)

    assert "live_pkg_test_fake_a3" not in sys.modules


def test_denylist_exact_name_removed():
    """Exact denylist names (capability_policy, fs_write_policy) are also evicted."""
    sys.modules.pop("capability_policy", None)
    assert "capability_policy" not in sys.modules

    snap_modules, snap_path = ct._snapshot_sys_modules_and_path()
    sys.modules["capability_policy"] = "fake_policy"
    assert "capability_policy" in sys.modules
    ct._restore_sys_modules_and_path(snap_modules, snap_path)

    assert "capability_policy" not in sys.modules


def test_standard_module_preserved():
    """A legitimate import (general_ludd.routing_roles) survives sandbox teardown."""
    import general_ludd.routing_roles

    assert "general_ludd" in sys.modules
    assert "general_ludd.routing_roles" in sys.modules

    snap_modules, snap_path = ct._snapshot_sys_modules_and_path()
    del general_ludd  # silence unused-import
    ct._restore_sys_modules_and_path(snap_modules, snap_path)

    assert "general_ludd" in sys.modules
    assert "general_ludd.routing_roles" in sys.modules


def test_sys_path_restored():
    """sys.path modifications are reverted to the snapshot."""
    snap_modules, snap_path = ct._snapshot_sys_modules_and_path()

    sys.path.insert(0, "/fake/injected/path")
    assert "/fake/injected/path" in sys.path
    ct._restore_sys_modules_and_path(snap_modules, snap_path)

    assert sys.path == snap_path
    assert "/fake/injected/path" not in sys.path


def test_full_import_state_restored():
    """Loader hooks, importer cache, and argv are restored as one boundary."""
    snapshot = ct._snapshot_import_state()
    fake_finder = cast(Any, object())
    fake_path_hook = cast(Any, object())
    fake_cache_entry = cast(Any, object())

    try:
        sys.meta_path.append(fake_finder)
        sys.path_hooks.append(fake_path_hook)
        sys.path_importer_cache["/fake/import-state-entry"] = fake_cache_entry
        sys.argv[:] = ["polluted-test-argv"]
    finally:
        ct._restore_import_state(snapshot)

    assert sys.meta_path == list(snapshot.meta_path)
    assert sys.path_hooks == list(snapshot.path_hooks)
    assert sys.path_importer_cache == snapshot.path_importer_cache
    assert sys.argv == list(snapshot.argv)


def test_isolated_path_loader_does_not_cache_short_alias(tmp_path):
    """Path-loaded compatibility CLIs never survive under a global alias."""
    module_path = tmp_path / "compatibility_cli.py"
    module_path.write_text("VALUE = 42\n")
    alias = "gludd_test_isolated_compatibility_cli"
    sys.modules.pop(alias, None)

    module = ct._load_path_module_isolated(alias, module_path)

    assert module.VALUE == 42
    assert alias not in sys.modules


def test_replaced_module_restored():
    """If an existing module object is replaced, fixture restores it from snapshot."""
    snap_modules, snap_path = ct._snapshot_sys_modules_and_path()
    original = sys.modules["general_ludd.routing_roles"]
    assert original is not None

    sys.modules["general_ludd.routing_roles"] = "replaced_stub"
    ct._restore_sys_modules_and_path(snap_modules, snap_path)

    assert sys.modules["general_ludd.routing_roles"] is original


def test_non_denylisted_new_module_preserved():
    """New modules that don't match the denylist are left alone by the sandbox."""
    key = "some_legit_test_module_a3"
    assert key not in sys.modules

    snap_modules, snap_path = ct._snapshot_sys_modules_and_path()
    sys.modules[key] = "legitimate_stub"
    ct._restore_sys_modules_and_path(snap_modules, snap_path)

    assert key in sys.modules
    sys.modules.pop(key, None)
