"""Unit tests for Phase 3 of the project-local .gludd/ feature: live reload.

Tests cover:
- HotReloader._reload_skills calls skill_registry.refresh when dirs are active
- New skills picked up without restart (SkillUpdatedEvent + registry refresh)
- Missing skill_registry does not raise
- POST /admin/reload wires project .gludd/skills into skills_dirs
- POST /admin/config/reload merges rules in-place (object identity preserved)
- POST /admin/config/reload reports updated vs unchanged keys
- POST /admin/config/reload with no event_loop still returns success
- POST /admin/config/reload fires ConfigReloadedEvent
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_skill_md(path: Path, name: str) -> None:
    path.write_text(f"# {name}\n\nDescription of {name}.\n\nTriggers: {name.lower()}\n")


def _make_hot_reloader(
    config_dir: str,
    skills_dirs: list[str] | None = None,
    skill_registry: Any = None,
    event_bus: Any = None,
) -> Any:
    from general_ludd.reload.hot_reloader import HotReloader

    return HotReloader(
        config_dir=config_dir,
        skills_dirs=skills_dirs,
        skill_registry=skill_registry,
        event_bus=event_bus,
    )


# ---------------------------------------------------------------------------
# TestHotReloaderSkillsRefresh
# ---------------------------------------------------------------------------


class TestHotReloaderSkillsRefresh:
    """HotReloader._reload_skills calls skill_registry.refresh when dirs exist."""

    def test_registry_refreshed_on_reload(self, tmp_path: Path) -> None:
        """skill_registry.refresh() is called with active dirs when skills exist."""
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        _make_skill_md(skills_dir / "deploy.md", "deploy")

        mock_registry = MagicMock()
        reloader = _make_hot_reloader(
            config_dir=str(tmp_path),
            skills_dirs=[str(skills_dir)],
            skill_registry=mock_registry,
        )
        result = reloader._reload_skills()

        assert result["registry_refreshed"] is True
        mock_registry.refresh.assert_called_once_with(search_paths=[str(skills_dir)])

    def test_new_skill_live_without_restart(self, tmp_path: Path) -> None:
        """A newly added .md file is picked up and SkillUpdatedEvent is fired."""
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()

        published: list[Any] = []
        mock_bus = MagicMock()
        mock_bus.publish.side_effect = lambda e: published.append(e)
        mock_registry = MagicMock()

        reloader = _make_hot_reloader(
            config_dir=str(tmp_path),
            skills_dirs=[str(skills_dir)],
            skill_registry=mock_registry,
            event_bus=mock_bus,
        )

        # First reload: dir exists but is empty — active_dirs is non-empty (dir exists),
        # so refresh IS called even though no .md files are present.
        result = reloader._reload_skills()
        assert result["skills"] == []
        # refresh IS called because the dir exists (active), regardless of skill count
        mock_registry.refresh.assert_called_once_with(search_paths=[str(skills_dir)])
        mock_registry.reset_mock()

        # Add a new skill
        _make_skill_md(skills_dir / "new_skill.md", "new_skill")
        result2 = reloader._reload_skills()

        assert "new_skill" in result2["skills"]
        assert result2.get("registry_refreshed") is True
        mock_registry.refresh.assert_called_once_with(search_paths=[str(skills_dir)])

        # Verify SkillUpdatedEvent was published
        from general_ludd.events.types import SkillUpdatedEvent
        skill_events = [e for e in published if isinstance(e, SkillUpdatedEvent)]
        assert any(getattr(e, "payload", {}).get("skill") == "new_skill" for e in skill_events)

    def test_no_skill_registry_does_not_raise(self, tmp_path: Path) -> None:
        """When skill_registry is None, _reload_skills completes without error."""
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        _make_skill_md(skills_dir / "alpha.md", "alpha")

        reloader = _make_hot_reloader(
            config_dir=str(tmp_path),
            skills_dirs=[str(skills_dir)],
            skill_registry=None,
        )
        result = reloader._reload_skills()

        assert "alpha" in result["skills"]
        assert "registry_refreshed" not in result
        assert "registry_error" not in result

    def test_registry_refresh_error_captured_in_result(self, tmp_path: Path) -> None:
        """A failing skill_registry.refresh() is caught and reported, not raised."""
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        _make_skill_md(skills_dir / "beta.md", "beta")

        mock_registry = MagicMock()
        mock_registry.refresh.side_effect = RuntimeError("boom")

        reloader = _make_hot_reloader(
            config_dir=str(tmp_path),
            skills_dirs=[str(skills_dir)],
            skill_registry=mock_registry,
        )
        result = reloader._reload_skills()

        assert "registry_error" in result
        assert "boom" in result["registry_error"]
        assert "registry_refreshed" not in result

    def test_project_gludd_skills_dir_included_by_handler(self, tmp_path: Path) -> None:
        """POST /admin/reload wires the project .gludd/skills dir into HotReloader."""
        from fastapi.testclient import TestClient

        # Set up project .gludd/skills dir
        proj_gludd = tmp_path / ".gludd"
        proj_skills = proj_gludd / "skills"
        proj_skills.mkdir(parents=True)
        _make_skill_md(proj_skills / "project_skill.md", "project_skill")

        captured_skills_dirs: list[list[str] | None] = []

        original_init = None

        def _patched_init(self: Any, *args: Any, **kwargs: Any) -> None:
            captured_skills_dirs.append(kwargs.get("skills_dirs"))
            original_init(self, *args, **kwargs)  # type: ignore[misc]

        from general_ludd import daemon as daemon_mod
        from general_ludd.reload import hot_reloader as hr_mod

        original_init = hr_mod.HotReloader.__init__

        with patch.object(hr_mod.HotReloader, "__init__", _patched_init):
            app = daemon_mod.create_daemon_app()
            app.state._config_dir = str(tmp_path / "config")
            app.state._project_gludd_dir = str(proj_gludd)
            app.state.GLUDD_ALLOW_NO_AUTH = True
            # Bypass auth middleware for tests
            import os
            os.environ["GLUDD_ALLOW_NO_AUTH"] = "1"
            try:
                with TestClient(app) as client:
                    client.post("/admin/reload", json={"scope": "skills"})
                    # May return any status; we care about skills_dirs
            finally:
                os.environ.pop("GLUDD_ALLOW_NO_AUTH", None)

        assert captured_skills_dirs, "HotReloader.__init__ was never called"
        last_dirs = captured_skills_dirs[-1] or []
        assert any(str(proj_skills) in d for d in last_dirs), (
            f"project .gludd/skills not in skills_dirs: {last_dirs}"
        )

    def test_global_config_skills_subdir_used_not_root(self, tmp_path: Path) -> None:
        """POST /admin/reload uses <config_dir>/skills, NOT <config_dir> root.

        A skill .md placed in config_dir/skills/ must appear in skills_dirs;
        a .md placed directly in config_dir root must NOT cause config_dir itself
        to be added (the is_dir guard on the /skills subdir controls inclusion).
        """
        from fastapi.testclient import TestClient

        config_dir = tmp_path / "config"
        config_dir.mkdir()

        # File directly in config root — should NOT be scanned by reload
        _make_skill_md(config_dir / "stray_root_skill.md", "stray_root_skill")

        # File in config/skills/ — SHOULD be scanned by reload
        config_skills = config_dir / "skills"
        config_skills.mkdir()
        _make_skill_md(config_skills / "global_skill.md", "global_skill")

        captured_skills_dirs: list[list[str] | None] = []
        original_init = None

        def _patched_init(self: Any, *args: Any, **kwargs: Any) -> None:
            captured_skills_dirs.append(kwargs.get("skills_dirs"))
            original_init(self, *args, **kwargs)  # type: ignore[misc]

        from general_ludd import daemon as daemon_mod
        from general_ludd.reload import hot_reloader as hr_mod

        original_init = hr_mod.HotReloader.__init__

        with patch.object(hr_mod.HotReloader, "__init__", _patched_init):
            app = daemon_mod.create_daemon_app()
            app.state._config_dir = str(config_dir)
            app.state._project_gludd_dir = None
            app.state.GLUDD_ALLOW_NO_AUTH = True
            import os
            os.environ["GLUDD_ALLOW_NO_AUTH"] = "1"
            try:
                with TestClient(app) as client:
                    client.post("/admin/reload", json={"scope": "skills"})
            finally:
                os.environ.pop("GLUDD_ALLOW_NO_AUTH", None)

        assert captured_skills_dirs, "HotReloader.__init__ was never called"
        last_dirs = captured_skills_dirs[-1] or []

        # config_dir/skills MUST be in skills_dirs
        assert any(str(config_skills) in d for d in last_dirs), (
            f"config_dir/skills not in skills_dirs: {last_dirs}"
        )
        # config_dir root must NOT be in skills_dirs (stray root .md not scanned)
        assert not any(d == str(config_dir) for d in last_dirs), (
            f"config_dir root was incorrectly added to skills_dirs: {last_dirs}"
        )

    def test_global_config_skills_subdir_absent_adds_nothing(self, tmp_path: Path) -> None:
        """When config_dir/skills/ does not exist, no global skills entry is added."""
        from fastapi.testclient import TestClient

        config_dir = tmp_path / "config"
        config_dir.mkdir()
        # No config_dir/skills/ subdir — only a stray .md at root
        _make_skill_md(config_dir / "orphan.md", "orphan")

        captured_skills_dirs: list[list[str] | None] = []
        original_init = None

        def _patched_init(self: Any, *args: Any, **kwargs: Any) -> None:
            captured_skills_dirs.append(kwargs.get("skills_dirs"))
            original_init(self, *args, **kwargs)  # type: ignore[misc]

        from general_ludd import daemon as daemon_mod
        from general_ludd.reload import hot_reloader as hr_mod

        original_init = hr_mod.HotReloader.__init__

        with patch.object(hr_mod.HotReloader, "__init__", _patched_init):
            app = daemon_mod.create_daemon_app()
            app.state._config_dir = str(config_dir)
            app.state._project_gludd_dir = None
            app.state.GLUDD_ALLOW_NO_AUTH = True
            import os
            os.environ["GLUDD_ALLOW_NO_AUTH"] = "1"
            try:
                with TestClient(app) as client:
                    client.post("/admin/reload", json={"scope": "skills"})
            finally:
                os.environ.pop("GLUDD_ALLOW_NO_AUTH", None)

        assert captured_skills_dirs, "HotReloader.__init__ was never called"
        last_dirs = captured_skills_dirs[-1] or []
        # Neither config_dir nor config_dir/skills should appear
        assert not any(str(config_dir) in d for d in last_dirs), (
            f"config_dir (or subdir) was added despite skills/ not existing: {last_dirs}"
        )


# ---------------------------------------------------------------------------
# TestAdminConfigReload
# ---------------------------------------------------------------------------


def _make_minimal_app(tmp_path: Path, startup_config: dict[str, Any] | None = None) -> Any:
    """Build a minimal FastAPI app with the reload router registered."""
    from fastapi import FastAPI

    from general_ludd.routers import reload as reload_router

    app = FastAPI()
    app.state._config_dir = str(tmp_path)
    app.state._templates_dir = None
    app.state._playbooks_dir = None
    app.state._startup_config = startup_config or {}
    app.state.event_loop = None
    reload_router.register(app, {})
    return app


class TestAdminConfigReload:
    """POST /admin/config/reload merges live-reloadable config keys in-place."""

    def test_updated_rules_merged_in_place_object_identity_preserved(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Rules from the new config are merged; the config dict object is unchanged."""
        from fastapi.testclient import TestClient

        new_rules = [{"id": "r1", "condition": "always", "action": "log"}]
        new_startup = {
            "rules": new_rules,
            "model_profiles": [],
            "user_config": None,
        }
        monkeypatch.setattr(
            "general_ludd.daemon.load_startup_config",
            lambda *a, **kw: new_startup,
        )

        app = _make_minimal_app(tmp_path)

        # Wire a mock EventLoop with a live config dict
        mock_loop = MagicMock()
        initial_config: dict[str, Any] = {
            "rules": [],
            "model_profiles": [],
            "queues": [],
            "budget": {},
            "self_improve": {},
        }
        mock_loop.config = initial_config
        config_id_before = id(initial_config)
        app.state.event_loop = mock_loop

        with TestClient(app) as client:
            resp = client.post("/admin/config/reload")

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["merged"]["rules"] == "updated"

        # Object identity: the SAME dict was mutated, not rebound
        assert id(mock_loop.config) == config_id_before
        assert mock_loop.config["rules"] == new_rules

    def test_unchanged_keys_reported(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Keys whose value hasn't changed are reported as 'unchanged'."""
        from fastapi.testclient import TestClient

        existing_rules = [{"id": "r1"}]
        new_startup = {
            "rules": existing_rules,
            "model_profiles": [],
            "user_config": None,
        }
        monkeypatch.setattr(
            "general_ludd.daemon.load_startup_config",
            lambda *a, **kw: new_startup,
        )

        app = _make_minimal_app(tmp_path)
        mock_loop = MagicMock()
        mock_loop.config = {
            "rules": existing_rules,
            "model_profiles": [],
            "queues": [],
            "budget": {},
            "self_improve": {},
        }
        app.state.event_loop = mock_loop

        with TestClient(app) as client:
            resp = client.post("/admin/config/reload")

        data = resp.json()
        assert data["success"] is True
        assert data["merged"]["rules"] == "unchanged"

    def test_no_event_loop_still_succeeds(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When event_loop is absent, the endpoint still returns success=True."""
        from fastapi.testclient import TestClient

        monkeypatch.setattr(
            "general_ludd.daemon.load_startup_config",
            lambda *a, **kw: {"rules": [], "model_profiles": [], "user_config": None},
        )

        app = _make_minimal_app(tmp_path)
        # event_loop stays None (set in _make_minimal_app)

        with TestClient(app) as client:
            resp = client.post("/admin/config/reload")

        data = resp.json()
        assert data["success"] is True
        assert data["merged"] == {}

    def test_config_reloaded_event_fired(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A ConfigReloadedEvent with scope='config' is published after reload."""
        from fastapi.testclient import TestClient

        monkeypatch.setattr(
            "general_ludd.daemon.load_startup_config",
            lambda *a, **kw: {"rules": [], "model_profiles": [], "user_config": None},
        )

        app = _make_minimal_app(tmp_path)

        published: list[Any] = []
        mock_bus = MagicMock()
        mock_bus.publish.side_effect = lambda e: published.append(e)
        mock_bus.get_history.return_value = []

        # Inject the bus into subsystems so the router's _get_or_create_subsystems picks it up
        app.state._event_bus = mock_bus
        app.state._hook_system = MagicMock()
        app.state._hook_system.list_hooks.return_value = []
        app.state._worker_broadcaster = MagicMock()
        app.state._worker_broadcaster.list_workers.return_value = []

        with TestClient(app) as client:
            resp = client.post("/admin/config/reload")

        assert resp.status_code == 200
        from general_ludd.events.types import ConfigReloadedEvent
        config_events = [e for e in published if isinstance(e, ConfigReloadedEvent)]
        assert config_events, "ConfigReloadedEvent was not published"
        assert config_events[0].payload["scope"] == "config"

    def test_load_startup_config_exception_returns_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If load_startup_config raises, the endpoint returns success=False."""
        from fastapi.testclient import TestClient

        monkeypatch.setattr(
            "general_ludd.daemon.load_startup_config",
            lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("disk read error")),
        )

        app = _make_minimal_app(tmp_path)

        with TestClient(app) as client:
            resp = client.post("/admin/config/reload")

        data = resp.json()
        assert data["success"] is False
        assert "disk read error" in data["error"]
