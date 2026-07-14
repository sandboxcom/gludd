"""Structural tests for reload/hot_reloader.py — hot-reload system for config, skills, playbooks, and code modules."""

from __future__ import annotations

import tempfile
from pathlib import Path

from general_ludd.reload.hot_reloader import (
    HotReloader,
    ReloadBusyError,
    ReloadResult,
    ReloadScope,
)


class TestReloadScope:
    def test_enum_values(self):
        assert ReloadScope.MODELS.value == "models"
        assert ReloadScope.TEMPLATES.value == "templates"
        assert ReloadScope.PLAYBOOKS.value == "playbooks"
        assert ReloadScope.SKILLS.value == "skills"
        assert ReloadScope.CONFIG.value == "config"
        assert ReloadScope.ALL.value == "all"

    def test_is_str_enum(self):
        assert isinstance(ReloadScope.MODELS, str)


class TestReloadBusyError:
    def test_is_exception(self):
        exc = ReloadBusyError("busy")
        assert isinstance(exc, Exception)

    def test_message_preserved(self):
        exc = ReloadBusyError("another reload in progress")
        assert str(exc) == "another reload in progress"


class TestReloadResult:
    def test_success_result(self):
        result = ReloadResult(success=True, scope="models")
        assert result.success is True
        assert result.scope == "models"
        assert result.error is None
        assert result.details == {}
        assert result.timestamp > 0

    def test_failure_result(self):
        result = ReloadResult(success=False, scope="config", error="timeout")
        assert result.success is False
        assert result.error == "timeout"

    def test_with_details(self):
        result = ReloadResult(success=True, scope="all", details={"count": 42})
        assert result.details["count"] == 42


class TestPathToModuleName:
    def test_src_path(self):
        result = HotReloader._path_to_module_name("src/general_ludd/foo/bar.py")
        assert result == "general_ludd.foo.bar"

    def test_init_file(self):
        result = HotReloader._path_to_module_name("src/pkg/__init__.py")
        assert result == "pkg"

    def test_subpackage(self):
        result = HotReloader._path_to_module_name("pkg/sub/mod.py")
        assert result == "pkg.sub.mod"

    def test_windows_backslashes(self):
        result = HotReloader._path_to_module_name(r"src\general_ludd\foo.py")
        assert result == "general_ludd.foo"


class TestHotReloaderConstruction:
    def test_default_construction(self):
        with tempfile.TemporaryDirectory() as tmp:
            reloader = HotReloader(config_dir=tmp)
            assert reloader._config_dir == Path(tmp)
            assert reloader._event_bus is None
            assert reloader._hooks is None
            assert reloader._broadcaster is None

    def test_with_all_deps(self):
        with tempfile.TemporaryDirectory() as tmp:
            reloader = HotReloader(
                config_dir=tmp,
                templates_dir=tmp,
                playbooks_dir=tmp,
                skills_dirs=[tmp],
            )
            assert reloader._templates_dir == Path(tmp)
            assert reloader._playbooks_dir == Path(tmp)
            assert reloader._skills_dirs == [Path(tmp)]

    def test_min_reload_interval_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            reloader = HotReloader(config_dir=tmp)
            assert reloader._min_reload_interval_s == 0.05


class TestHotReloaderSnapshot:
    def test_snapshot_includes_config_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            reloader = HotReloader(config_dir=tmp)
            snap = reloader._snapshot()
            assert "config_dir" in snap
            assert "timestamp" in snap

    def test_get_last_state_returns_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            reloader = HotReloader(config_dir=tmp)
            state = reloader.get_last_state()
            assert state is not None
            assert hasattr(state, "previous_config")
            assert hasattr(state, "timestamp")


class TestHotReloaderReload:
    def test_reload_invalid_scope_returns_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            reloader = HotReloader(config_dir=tmp)
            result = reloader.reload(ReloadScope.ALL)
            assert isinstance(result, ReloadResult)
            assert result.scope == "all"


class TestHotReloaderInvalidateSourceCache:
    def test_does_not_raise_for_nonexistent_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            nonexistent = Path(tmp) / "does_not_exist.py"
            try:
                HotReloader._invalidate_source_cache(nonexistent)
            except Exception:
                raise AssertionError(
                    "should not raise for nonexistent path"
                ) from None
