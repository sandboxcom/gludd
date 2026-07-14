"""Structural tests for project_runner/profile.py — ProjectProfile + loader."""

from __future__ import annotations

import pytest

from general_ludd.project_runner.profile import (
    ProjectProfile,
    ProjectProfileError,
    load_project_profile,
)


class TestProjectProfileError:
    def test_is_value_error(self):
        err = ProjectProfileError("bad config")
        assert isinstance(err, ValueError)

    def test_message_preserved(self):
        err = ProjectProfileError("custom")
        assert str(err) == "custom"


class TestProjectProfileDefaults:
    def test_empty_construction(self):
        p = ProjectProfile()
        assert p.name == "target-project"
        assert p.commands == {}
        assert p.allowed_exec == []

    def test_default_env_passthrough(self):
        p = ProjectProfile()
        assert "NODE_ENV" in p.env_passthrough
        assert "CI" in p.env_passthrough

    def test_has_returns_false_for_missing(self):
        p = ProjectProfile()
        assert not p.has("test")

    def test_has_returns_true_for_present(self):
        p = ProjectProfile(commands={"test": "npm test"}, allowed_exec=["npm"])
        assert p.has("test")

    def test_has_returns_false_for_empty_command(self):
        p = ProjectProfile(commands={"test": "pytest"}, allowed_exec=["pytest"])
        assert not p.has("lint")


class TestProjectProfileCommands:
    def test_rejects_empty_command(self):
        try:
            ProjectProfile(commands={"test": ""})
        except Exception as exc:
            assert "empty" in str(exc)
        else:
            pytest.fail("expected validation error for empty command")

    def test_rejects_whitespace_command(self):
        try:
            ProjectProfile(commands={"lint": "   "})
        except Exception as exc:
            assert "empty" in str(exc)
        else:
            pytest.fail("expected validation error for whitespace command")


class TestResolveArgv:
    def test_undeclared_check_raises(self):
        p = ProjectProfile(commands={"test": "pytest"}, allowed_exec=["pytest"])
        with pytest.raises(ProjectProfileError, match="no 'lint' command"):
            p.resolve_argv("lint")

    def test_resolves_simple_command(self):
        p = ProjectProfile(commands={"test": "pytest"}, allowed_exec=["pytest"])
        argv = p.resolve_argv("test")
        assert argv == ["pytest"]

    def test_resolves_command_with_args(self):
        p = ProjectProfile(commands={"test": "pytest -v --tb=short"}, allowed_exec=["pytest"])
        argv = p.resolve_argv("test")
        assert argv[0] == "pytest"
        assert "-v" in argv

    def test_rejects_shell_metachar_pipe(self):
        p = ProjectProfile(commands={"test": "cat file | grep x"}, allowed_exec=["cat"])
        with pytest.raises(ProjectProfileError, match="metacharacters"):
            p.resolve_argv("test")

    def test_rejects_shell_metachar_semicolon(self):
        p = ProjectProfile(commands={"test": "pytest; ls"}, allowed_exec=["pytest"])
        with pytest.raises(ProjectProfileError, match="metacharacters"):
            p.resolve_argv("test")

    def test_rejects_shell_metachar_redirect(self):
        p = ProjectProfile(commands={"test": "echo > file"}, allowed_exec=["echo"])
        with pytest.raises(ProjectProfileError, match="metacharacters"):
            p.resolve_argv("test")

    def test_rejects_shell_metachar_subshell(self):
        p = ProjectProfile(commands={"test": "echo $(date)"}, allowed_exec=["echo"])
        with pytest.raises(ProjectProfileError, match="metacharacters"):
            p.resolve_argv("test")

    def test_rejects_unallowed_executable(self):
        p = ProjectProfile(commands={"test": "pytest"}, allowed_exec=["npm"])
        with pytest.raises(ProjectProfileError, match="not in allowed_exec"):
            p.resolve_argv("test")

    def test_allows_when_in_list(self):
        p = ProjectProfile(commands={"test": "pytest"}, allowed_exec=["pytest", "npm"])
        argv = p.resolve_argv("test")
        assert argv[0] == "pytest"

    def test_unbalanced_quotes_raises(self):
        p = ProjectProfile(commands={"test": "echo 'unclosed"}, allowed_exec=["echo"])
        with pytest.raises(ProjectProfileError, match="unparsable"):
            p.resolve_argv("test")

class TestAllowAnyExecEnv:
    def test_env_allows_any(self, monkeypatch):
        monkeypatch.setenv("GLUDD_PROJECT_ALLOW_ANY_EXEC", "1")
        p = ProjectProfile(commands={"test": "pytest --co"}, allowed_exec=[])
        argv = p.resolve_argv("test")
        assert argv[0] == "pytest"
        assert "--co" in argv


class TestLoadProjectProfile:
    def test_missing_file_in_nonexistent_dir_raises(self, tmp_path):
        with pytest.raises(ProjectProfileError, match=r"no project\.yml"):
            load_project_profile(str(tmp_path / "nonexistent"))

    def test_creates_profile_from_yaml(self, tmp_path):
        yml = tmp_path / "project.yml"
        yml.write_text("name: test\ncommands:\n  test: pytest\nallowed_exec: [pytest]\n")
        profile = load_project_profile(str(tmp_path))
        assert profile.name == "test"
        assert profile.has("test")

    def test_non_dict_yaml_raises(self, tmp_path):
        yml = tmp_path / "project.yml"
        yml.write_text("- item 1\n- item 2\n")
        with pytest.raises(ProjectProfileError, match="must be a mapping"):
            load_project_profile(str(tmp_path))

    def test_invalid_yaml_raises(self, tmp_path):
        yml = tmp_path / "project.yml"
        yml.write_text(": bad yaml: : :\n")
        with pytest.raises(ProjectProfileError, match="not valid YAML"):
            load_project_profile(str(tmp_path))

    def test_custom_filename(self, tmp_path):
        yml = tmp_path / "custom.yml"
        yml.write_text("name: custom\ncommands: {}\nallowed_exec: []\n")
        profile = load_project_profile(str(tmp_path), filename="custom.yml")
        assert profile.name == "custom"

    def test_falls_back_to_toolchain_detection(self, tmp_path):
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text("[tool.pytest]\n")
        profile = load_project_profile(str(tmp_path))
        assert profile is not None
