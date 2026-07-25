"""Verify hot-reload module freshness exit-code behavior.

Tests scripts/check_hot_reload_fresh.py::main() exit codes:
  - exit 0 when no hot modules exist (clean/compiled-default state)
  - exit 0 when a hot module is newer than its source (fresh)
  - exit 1 when a hot module is older than its source (STALE — loads old code)

Uses tmp_path isolation via GLUDD_PLUGIN_DIR / GLUDD_HOT_OUT_DIR env overrides
so no real /tmp/gludd-hot-*.js or .opencode/plugin/*.ts files are touched.
"""

import importlib.util
import os
import sys
import time
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_hot_reload_fresh.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("check_hot_reload_fresh", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules["check_hot_reload_fresh"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def fresh_module(monkeypatch, tmp_path):
    """Load the script with PLUGIN_DIR and OUT_DIR pointed at tmp_path."""
    plugin_dir = tmp_path / "plugin"
    out_dir = tmp_path / "hot"
    plugin_dir.mkdir()
    out_dir.mkdir()
    monkeypatch.setenv("GLUDD_PLUGIN_DIR", str(plugin_dir))
    monkeypatch.setenv("GLUDD_HOT_OUT_DIR", str(out_dir))

    sys.modules.pop("check_hot_reload_fresh", None)
    mod = _load_module()
    mod.PLUGIN_DIR = plugin_dir
    mod.OUT_DIR = out_dir
    return mod, plugin_dir, out_dir


def _write_plugin_src(plugin_dir: Path, name: str = "enforce-stop") -> Path:
    src = plugin_dir / f"{name}.ts"
    src.write_text(
        "const defaultImpl = { };\nexport default (async () => defaultImpl);\n",
        encoding="utf-8",
    )
    return src


def _write_hot_module(out_dir: Path, name: str = "enforce-stop", body: str | None = None) -> Path:
    hot = out_dir / f"gludd-hot-{name}.js"
    if body is None:
        body = (
            "module.exports = {\n"
            "  'tool.execute.before': function() { return; },\n"
            "  'text.complete': function() { return; },\n"
            "};\n"
        )
    hot.write_text(body, encoding="utf-8")
    return hot


class TestScriptExists:
    def test_script_exists(self):
        assert SCRIPT.exists(), f"script not found at {SCRIPT}"

    def test_script_is_executable(self):
        if os.name == "nt":
            pytest.skip("executable bit not meaningful on Windows")
        assert os.access(SCRIPT, os.X_OK), f"{SCRIPT} is not executable"


class TestNoHotModules:
    def test_exit_zero_when_no_hot_modules_exist(self, fresh_module):
        mod, plugin_dir, _out_dir = fresh_module
        _write_plugin_src(plugin_dir)
        assert mod.main([]) == 0


class TestFreshHotModule:
    def test_exit_zero_when_hot_newer_than_source(self, fresh_module):
        mod, plugin_dir, out_dir = fresh_module
        src = _write_plugin_src(plugin_dir)
        hot = _write_hot_module(out_dir)

        time.sleep(0.05)
        os.utime(hot, None)
        now = time.time()
        os.utime(hot, (now, now))
        os.utime(src, (now - 100, now - 100))

        assert hot.stat().st_mtime > src.stat().st_mtime
        assert mod.main([]) == 0


class TestStaleHotModule:
    def test_exit_one_when_hot_older_than_source(self, fresh_module):
        mod, plugin_dir, out_dir = fresh_module
        hot = _write_hot_module(out_dir)
        time.sleep(0.05)
        src = _write_plugin_src(plugin_dir)

        now = time.time()
        os.utime(hot, (now - 200, now - 200))
        os.utime(src, (now, now))

        assert hot.stat().st_mtime < src.stat().st_mtime
        assert mod.main([]) == 1
