"""Structural tests for project_runner/detect.py — ToolchainDetector + helpers."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from general_ludd.project_runner.detect import (
    MARKER_MAP,
    ToolchainDetector,
    _derive_allowed_exec,
)


class TestMarkerMap:
    def test_marker_map_keys_and_order(self):
        keys = list(MARKER_MAP.keys())
        assert keys == ["pyproject.toml", "package.json", "go.mod", "Cargo.toml", "Makefile"]

    def test_marker_map_values(self):
        assert MARKER_MAP["pyproject.toml"] == "python"
        assert MARKER_MAP["package.json"] == "node"
        assert MARKER_MAP["go.mod"] == "go"
        assert MARKER_MAP["Cargo.toml"] == "rust"
        assert MARKER_MAP["Makefile"] == "make"


class TestToolchainDetectorClass:
    def test_class_exists(self):
        assert hasattr(ToolchainDetector, "MARKER_MAP")
        assert hasattr(ToolchainDetector, "detect")

    def test_marker_map_is_class_attribute(self):
        assert ToolchainDetector.MARKER_MAP == MARKER_MAP

    def test_detect_is_static_method(self):
        assert callable(ToolchainDetector.detect)


class TestDetect:
    def test_detect_empty_dir_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = ToolchainDetector.detect(tmp)
            assert result is None

    def test_detect_non_existent_returns_none(self):
        result = ToolchainDetector.detect("/nonexistent/path/12345")
        assert result is None

    def test_detect_python_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "pyproject.toml").write_text("")
            profile = ToolchainDetector.detect(tmp)
            assert profile is not None
            assert profile.name == "python-detected"
            assert "test" in profile.commands
            assert "lint" in profile.commands
            assert "typecheck" in profile.commands
            assert "pytest" in profile.allowed_exec
            assert "ruff" in profile.allowed_exec
            assert "mypy" in profile.allowed_exec

    def test_detect_go_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "go.mod").write_text("")
            profile = ToolchainDetector.detect(tmp)
            assert profile is not None
            assert profile.name == "go-detected"
            assert "test" in profile.commands
            assert "lint" in profile.commands
            assert "go" in profile.allowed_exec

    def test_detect_rust_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "Cargo.toml").write_text("")
            profile = ToolchainDetector.detect(tmp)
            assert profile is not None
            assert profile.name == "rust-detected"
            assert "test" in profile.commands
            assert "lint" in profile.commands
            assert "cargo" in profile.allowed_exec

    def test_detect_makefile_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "Makefile").write_text("")
            profile = ToolchainDetector.detect(tmp)
            assert profile is not None
            assert profile.name == "make-detected"
            assert "test" in profile.commands
            assert "lint" in profile.commands
            assert "make" in profile.allowed_exec

    def test_detect_node_with_no_scripts_skips(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "package.json").write_text("{}")
            (Path(tmp) / "Makefile").write_text("")
            profile = ToolchainDetector.detect(tmp)
            assert profile is not None
            assert profile.name == "make-detected"

    def test_detect_node_with_scripts(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "package.json").write_text(
                json.dumps({"scripts": {"test": "jest", "lint": "eslint ."}})
            )
            profile = ToolchainDetector.detect(tmp)
            assert profile is not None
            assert profile.name == "node-detected"
            assert profile.commands.get("test") == "npm test"
            assert profile.commands.get("lint") == "npm run lint"

    def test_detect_resolution_order_pyproject_wins(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "pyproject.toml").write_text("")
            (Path(tmp) / "Makefile").write_text("")
            profile = ToolchainDetector.detect(tmp)
            assert profile is not None
            assert profile.name == "python-detected"


class TestDeriveAllowedExec:
    def test_empty_commands(self):
        assert _derive_allowed_exec({}) == []

    def test_single_command(self):
        result = _derive_allowed_exec({"test": "pytest -q"})
        assert result == ["pytest"]

    def test_multiple_commands_same_exe(self):
        result = _derive_allowed_exec({"test": "pytest -q", "lint": "ruff check ."})
        assert "pytest" in result
        assert "ruff" in result
        assert len(result) == 2

    def test_unknown_shell_returns_empty(self):
        result = _derive_allowed_exec({})
        assert result == []
