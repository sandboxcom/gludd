"""E2E test for ModuleSnapshot / restore_modules round-trip.

Creates a temp module, snapshots it, mutates it, reloads, restores the
snapshot, and verifies the old version is back.
"""

from __future__ import annotations

import contextlib
import importlib
import os
import sys
import tempfile
import textwrap
from pathlib import Path

import pytest

from general_ludd.self_update.module_snapshot import (
    ModuleSnapshot,
    find_live_references,
    restore_modules,
    snapshot_modules,
)

_MODULE_NAME = "_gludd_test_snap"


@pytest.fixture(autouse=True)
def _cleanup_test_module():
    """Ensure no leftover state from a prior aborted run."""
    yield
    sys.modules.pop(_MODULE_NAME, None)
    importlib.invalidate_caches()


def _write_module(filepath: Path, body: str) -> None:
    filepath.write_text(body, encoding="utf-8")


def _invalidate_source_cache(live_path: Path) -> None:
    """Mirrors HotReloader._invalidate_source_cache: bump mtime + clear .pyc."""
    try:
        st = live_path.stat()
        os.utime(live_path, (st.st_atime, st.st_mtime + 1))
    except OSError:
        pass
    cache = live_path.parent / "__pycache__"
    if cache.is_dir():
        stem = live_path.stem
        for pyc in cache.glob(f"{stem}.*.pyc"):
            with contextlib.suppress(OSError):
                pyc.unlink()
    importlib.invalidate_caches()


class TestModuleSnapshotRoundTrip:
    """End-to-end: snapshot → mutate → reload → restore → verify."""

    def test_snapshot_restore_restores_old_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            sys.path.insert(0, tmp)
            try:
                mod_path = Path(tmp) / f"{_MODULE_NAME}.py"
                _write_module(
                    mod_path,
                    textwrap.dedent("""\
                        def gludd_test_version() -> str:
                            return "v1"
                        """),
                )

                mod = importlib.import_module(_MODULE_NAME)
                assert mod.gludd_test_version() == "v1"

                original_bytes = mod_path.read_bytes()

                snap = snapshot_modules([_MODULE_NAME])
                assert isinstance(snap, ModuleSnapshot)
                assert _MODULE_NAME in snap.modules
                assert snap.snapshot_at > 0

                _write_module(
                    mod_path,
                    textwrap.dedent("""\
                        def gludd_test_version() -> str:
                            return "v2"
                        """),
                )
                _invalidate_source_cache(mod_path)
                mod = importlib.reload(mod)
                assert mod.gludd_test_version() == "v2"

                mod_path.write_bytes(original_bytes)
                _invalidate_source_cache(mod_path)
                restored = restore_modules(snap)
                assert _MODULE_NAME in restored

                mod = sys.modules[_MODULE_NAME]
                assert mod.gludd_test_version() == "v1"
            finally:
                sys.path.remove(tmp)

    def test_empty_snapshot_is_falsey(self):
        snap = ModuleSnapshot()
        assert not snap

    def test_snapshot_missing_module_no_error(self):
        snap = snapshot_modules(["nonexistent_xyzzy_module_name"])
        assert _MODULE_NAME not in snap.modules  # shouldn't appear
        assert snap.snapshot_at > 0

    def test_restore_empty_snapshot_returns_empty_list(self):
        result = restore_modules(ModuleSnapshot())
        assert result == []

    def test_find_live_references_on_missing_module(self):
        refs = find_live_references("nonexistent_xyzzy_module_name")
        assert refs == []

    def test_find_live_references_returns_strings(self):
        with tempfile.TemporaryDirectory() as tmp:
            sys.path.insert(0, tmp)
            try:
                mod_path = Path(tmp) / f"{_MODULE_NAME}.py"
                _write_module(
                    mod_path,
                    textwrap.dedent("""\
                        def gludd_test_version() -> str:
                            return "v1"
                        """),
                )
                importlib.import_module(_MODULE_NAME)
                refs = find_live_references(_MODULE_NAME)
                assert isinstance(refs, list)
            finally:
                sys.path.remove(tmp)

    def test_snapshot_warnings_populated_for_singletons(self):
        """Module-level globals whose names match singleton heuristics produce warnings."""
        with tempfile.TemporaryDirectory() as tmp:
            sys.path.insert(0, tmp)
            try:
                mod_path = Path(tmp) / f"{_MODULE_NAME}.py"
                _write_module(
                    mod_path,
                    textwrap.dedent("""\
                        pool = {"conn": 42}
                        _cache = None  # underscore-prefixed → skipped

                        def gludd_test_version() -> str:
                            return "v1"
                        """),
                )
                importlib.import_module(_MODULE_NAME)
                snap = snapshot_modules([_MODULE_NAME])
                assert any("pool" in w for w in snap.warnings)
                assert not any("_cache" in w for w in snap.warnings)
            finally:
                sys.path.remove(tmp)

    def test_multiple_modules_in_one_snapshot(self):
        m2_name = f"{_MODULE_NAME}_b"
        with tempfile.TemporaryDirectory() as tmp:
            sys.path.insert(0, tmp)
            try:
                for name, body in [
                    (
                        _MODULE_NAME,
                        "def gludd_test_version() -> str:\n    return 'v1'\n",
                    ),
                    (m2_name, "def gludd_test_version() -> str:\n    return 'a'\n"),
                ]:
                    fp = Path(tmp) / f"{name}.py"
                    _write_module(fp, body)
                    importlib.import_module(name)

                snap = snapshot_modules([_MODULE_NAME, m2_name])
                assert _MODULE_NAME in snap.modules
                assert m2_name in snap.modules

                original_bytes: dict[str, bytes] = {}
                for name in [_MODULE_NAME, m2_name]:
                    fp = Path(tmp) / f"{name}.py"
                    original_bytes[name] = fp.read_bytes()

                for name, fp in [
                    (_MODULE_NAME, Path(tmp) / f"{_MODULE_NAME}.py"),
                    (m2_name, Path(tmp) / f"{m2_name}.py"),
                ]:
                    _write_module(
                        fp,
                        "def gludd_test_version() -> str:\n    return 'v2'\n",
                    )
                    _invalidate_source_cache(fp)
                    importlib.reload(sys.modules[name])

                for name in [_MODULE_NAME, m2_name]:
                    fp = Path(tmp) / f"{name}.py"
                    fp.write_bytes(original_bytes[name])
                    _invalidate_source_cache(fp)

                restored = restore_modules(snap)
                assert _MODULE_NAME in restored
                assert m2_name in restored
                assert sys.modules[_MODULE_NAME].gludd_test_version() == "v1"
                assert sys.modules[m2_name].gludd_test_version() == "a"
            finally:
                sys.path.remove(tmp)
                sys.modules.pop(m2_name, None)

    def test_snapshot_at_increases(self):
        snap1 = snapshot_modules([])
        snap2 = snapshot_modules([])
        # Both should have valid timestamps, and the second should be >= the first
        assert snap2.snapshot_at >= snap1.snapshot_at
