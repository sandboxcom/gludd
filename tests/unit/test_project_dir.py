"""Unit tests for config/project_dir.py — Phase 1 discovery + merge."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from general_ludd.config.project_dir import (
    find_project_gludd_dir,
    merge_config,
    project_config_path,
)

# ---------------------------------------------------------------------------
# find_project_gludd_dir
# ---------------------------------------------------------------------------


class TestFindProjectGluddDir:
    def test_walk_up_finds_nearest_gludd(self, tmp_path: Path) -> None:
        """Walking up from a nested subdir returns the nearest .gludd/ ancestor."""
        gludd_dir = tmp_path / ".gludd"
        gludd_dir.mkdir()
        nested = tmp_path / "a" / "b" / "c"
        nested.mkdir(parents=True)
        result = find_project_gludd_dir(start=nested)
        assert result == gludd_dir

    def test_finds_gludd_in_start_dir_itself(self, tmp_path: Path) -> None:
        """If start dir itself contains .gludd/, it is returned."""
        gludd_dir = tmp_path / ".gludd"
        gludd_dir.mkdir()
        result = find_project_gludd_dir(start=tmp_path)
        assert result == gludd_dir

    def test_absent_returns_none(self, tmp_path: Path) -> None:
        """When no .gludd/ exists in the ancestry, returns None."""
        nested = tmp_path / "x" / "y"
        nested.mkdir(parents=True)
        result = find_project_gludd_dir(start=nested)
        assert result is None

    def test_env_override_existing_dir(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """GLUDD_PROJECT_DIR env var overrides walk-up and returns the env path."""
        gludd_dir = tmp_path / "custom_gludd"
        gludd_dir.mkdir()
        monkeypatch.setenv("GLUDD_PROJECT_DIR", str(gludd_dir))
        result = find_project_gludd_dir()
        assert result == gludd_dir

    def test_env_override_nonexistent_returns_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """GLUDD_PROJECT_DIR pointing at a missing path returns None (not an error)."""
        monkeypatch.setenv("GLUDD_PROJECT_DIR", "/does/not/exist/__gludd__")
        result = find_project_gludd_dir()
        assert result is None

    def test_nearest_wins_over_ancestor(self, tmp_path: Path) -> None:
        """When both a parent and a grandparent have .gludd/, the nearer one wins."""
        outer_gludd = tmp_path / ".gludd"
        outer_gludd.mkdir()
        inner_dir = tmp_path / "sub"
        inner_dir.mkdir()
        inner_gludd = inner_dir / ".gludd"
        inner_gludd.mkdir()
        nested = inner_dir / "deep"
        nested.mkdir()
        result = find_project_gludd_dir(start=nested)
        assert result == inner_gludd


# ---------------------------------------------------------------------------
# project_config_path
# ---------------------------------------------------------------------------


class TestProjectConfigPath:
    def test_returns_path_when_yml_exists(self, tmp_path: Path) -> None:
        gludd_dir = tmp_path / ".gludd"
        gludd_dir.mkdir()
        yml = gludd_dir / "general-ludd.yml"
        yml.write_text("agents: {}")
        result = project_config_path(gludd_dir)
        assert result == yml

    def test_returns_none_when_yml_absent(self, tmp_path: Path) -> None:
        gludd_dir = tmp_path / ".gludd"
        gludd_dir.mkdir()
        result = project_config_path(gludd_dir)
        assert result is None

    def test_returns_none_when_project_gludd_dir_is_none(self) -> None:
        result = project_config_path(None)
        assert result is None


# ---------------------------------------------------------------------------
# merge_config
# ---------------------------------------------------------------------------


class TestMergeConfig:
    def test_scalar_project_wins(self) -> None:
        result = merge_config({"key": "user_val"}, {"key": "proj_val"})
        assert result["key"] == "proj_val"

    def test_absent_in_project_keeps_user_val(self) -> None:
        result = merge_config({"a": 1, "b": 2}, {"a": 99})
        assert result["b"] == 2
        assert result["a"] == 99

    def test_absent_in_user_added_from_project(self) -> None:
        result = merge_config({"a": 1}, {"b": "new"})
        assert result["b"] == "new"
        assert result["a"] == 1

    def test_nested_dict_deep_merged(self) -> None:
        user: dict[str, Any] = {"nested": {"x": 1, "y": 2}}
        proj: dict[str, Any] = {"nested": {"y": 99, "z": 3}}
        result = merge_config(user, proj)
        assert result["nested"]["x"] == 1   # from user
        assert result["nested"]["y"] == 99  # project wins
        assert result["nested"]["z"] == 3   # from project

    def test_list_project_replaces_user(self) -> None:
        """Lists are replaced wholesale — project list wins entirely."""
        user: dict[str, Any] = {"rules": ["a", "b"]}
        proj: dict[str, Any] = {"rules": ["c"]}
        result = merge_config(user, proj)
        assert result["rules"] == ["c"]

    def test_neither_dict_mutated(self) -> None:
        user: dict[str, Any] = {"k": "u"}
        proj: dict[str, Any] = {"k": "p"}
        merge_config(user, proj)
        assert user["k"] == "u"
        assert proj["k"] == "p"

    def test_none_value_in_project_replaces_user(self) -> None:
        result = merge_config({"x": 5}, {"x": None})
        assert result["x"] is None


# ---------------------------------------------------------------------------
# Integration: project overlay applies with no user config dir
# ---------------------------------------------------------------------------


class TestProjectOverlayInLoadStartupConfig:
    """Verify that load_startup_config applies project overlay on early-return paths."""

    def test_overlay_applied_with_no_user_config_dir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Even when no ~/.config/general-ludd exists, the project overlay applies."""
        # Set up a .gludd/ dir with a config overlay that sets a recognisable value.
        gludd_dir = tmp_path / ".gludd"
        gludd_dir.mkdir()
        (gludd_dir / "general-ludd.yml").write_text(
            "rules:\n  - name: project_rule\npipeline:\n  enabled: true\n"
        )
        # Make GLUDD_PROJECT_DIR point to our tmp .gludd dir.
        monkeypatch.setenv("GLUDD_PROJECT_DIR", str(gludd_dir))
        # Prevent HOME discovery from finding an actual config dir.
        monkeypatch.setenv("HOME", str(tmp_path / "nonexistent_home"))

        from general_ludd.daemon import load_startup_config

        cfg = load_startup_config(config_dir=None)

        assert cfg["project_gludd_dir"] == gludd_dir
        uc = cfg["user_config"]
        rules = getattr(uc, "rules", []) or []
        assert rules == [{"name": "project_rule"}]
        pipeline = getattr(uc, "pipeline", None)
        assert pipeline is not None
        assert getattr(pipeline, "enabled", False) is True

    def test_project_gludd_dir_in_cfg(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """cfg['project_gludd_dir'] is always populated (or None when absent)."""
        monkeypatch.setenv("GLUDD_PROJECT_DIR", str(tmp_path / "nonexistent"))
        monkeypatch.setenv("HOME", str(tmp_path / "nonexistent_home"))
        from general_ludd.daemon import load_startup_config

        cfg = load_startup_config()
        # Env-override points at non-existent path → find_project_gludd_dir returns None.
        assert cfg["project_gludd_dir"] is None

    def test_merge_precedence_project_over_user(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Project overlay values win over the user config values."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "general-ludd.yml").write_text(
            "agents:\n  timeout: 10\n"
        )
        gludd_dir = tmp_path / ".gludd"
        gludd_dir.mkdir()
        # Project sets a different (winning) timeout.
        (gludd_dir / "general-ludd.yml").write_text(
            "agents:\n  timeout: 99\n"
        )
        monkeypatch.setenv("GLUDD_PROJECT_DIR", str(gludd_dir))

        from general_ludd.daemon import load_startup_config

        cfg = load_startup_config(config_dir=str(config_dir))
        uc = cfg["user_config"]
        agents = getattr(uc, "agents", {}) or {}
        assert agents.get("timeout") == 99
