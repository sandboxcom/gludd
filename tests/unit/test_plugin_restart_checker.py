"""Verify check_plugin_restart_needed.py exit-code behavior.

Tests scripts/check_plugin_restart_needed.py::main() exit codes:
  - exit 0 when all source files are older than session start (current)
  - exit 1 when a source file is newer than session start (restart needed)
  - exit 0 when session-start file is missing (fail-safe — cannot determine)

Uses tmp_path isolation via GLUDD_PLUGIN_DIR / GLUDD_SESSION_START_FILE
env overrides so no real files are touched.
"""

import importlib.util
import json
import sys
import time
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_plugin_restart_needed.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("check_plugin_restart_needed", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules["check_plugin_restart_needed"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    plugin_dir = tmp_path / "plugin"
    session_file = tmp_path / "session.json"
    plugin_dir.mkdir()

    monkeypatch.setenv("GLUDD_PLUGIN_DIR", str(plugin_dir))
    monkeypatch.setenv("GLUDD_SESSION_START_FILE", str(session_file))

    sys.modules.pop("check_plugin_restart_needed", None)
    mod = _load_module()
    mod.PLUGIN_DIR = plugin_dir
    mod.SESSION_START_FILE = session_file
    return mod, plugin_dir, session_file


def _write_session(session_file: Path, started_at_ms: int) -> None:
    session_file.write_text(json.dumps({"started_at": started_at_ms}), encoding="utf-8")


def _write_source(plugin_dir: Path, relpath: str = "enforce-stop.ts") -> Path:
    src = plugin_dir / relpath
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_text("// plugin source\n", encoding="utf-8")
    return src


class TestPluginRestartNeeded:
    def test_no_session_file_failsafe(self, isolated) -> None:
        mod, plugin_dir, session_file = isolated
        _write_source(plugin_dir)
        assert not session_file.exists()
        rc = mod.main([])
        assert rc == 0

    def test_session_future_all_current(self, isolated) -> None:
        mod, plugin_dir, session_file = isolated
        _write_source(plugin_dir)
        time.sleep(0.01)
        _write_session(session_file, int(time.time() * 1000) + 600_000)
        rc = mod.main([])
        assert rc == 0

    def test_source_newer_than_session_needs_restart(self, isolated) -> None:
        mod, plugin_dir, session_file = isolated
        _write_session(session_file, int((time.time() - 3600) * 1000))
        _write_source(plugin_dir)
        rc = mod.main([])
        assert rc == 1

    def test_impl_file_detected(self, isolated) -> None:
        mod, plugin_dir, session_file = isolated
        _write_session(session_file, int((time.time() - 3600) * 1000))
        _write_source(plugin_dir, "impl/enforce_stop_impl.ts")
        rc = mod.main([])
        assert rc == 1

    def test_multiple_newer_files(self, isolated) -> None:
        mod, plugin_dir, session_file = isolated
        _write_session(session_file, int((time.time() - 3600) * 1000))
        _write_source(plugin_dir, "enforce-stop.ts")
        _write_source(plugin_dir, "impl/enforce_stop_impl.ts")
        _write_source(plugin_dir, "enforce-make.ts")
        rc = mod.main([])
        assert rc == 1

    def test_no_ts_files(self, isolated) -> None:
        mod, _plugin_dir, session_file = isolated
        _write_session(session_file, int(time.time() * 1000))
        rc = mod.main([])
        assert rc == 0

    def test_invalid_session_json_failsafe(self, isolated) -> None:
        mod, plugin_dir, session_file = isolated
        session_file.write_text("not json", encoding="utf-8")
        _write_source(plugin_dir)
        rc = mod.main([])
        assert rc == 0

    def test_helpers_importable(self) -> None:
        assert SCRIPT.exists()
        spec = importlib.util.spec_from_file_location("check_plugin_restart_needed", SCRIPT)
        mod = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(mod)
        assert callable(mod.read_session_start_ms)
        assert callable(mod.collect_ts_files)
        assert callable(mod.find_newer_sources)


class TestMakeTargetExists:
    def test_target_in_makefile(self) -> None:
        makefile = Path(__file__).resolve().parents[2] / "Makefile"
        content = makefile.read_text(encoding="utf-8")
        assert "check-plugin-restart-needed:" in content
        assert "scripts/check_plugin_restart_needed.py" in content
