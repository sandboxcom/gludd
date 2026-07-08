"""WP-E1: ``ToolchainDetector`` — marker-file detection heuristics.

A target project that has NO ``project.yml`` is detected from its marker
files (``pyproject.toml`` / ``package.json`` / ``go.mod`` / ``Cargo.toml`` /
``Makefile``) and a sensible default :class:`ProjectProfile` is synthesized.
Detection is the FALLBACK: an explicit ``project.yml`` always wins.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from general_ludd.project_runner import ProjectProfileError
from general_ludd.project_runner.detect import ToolchainDetector
from general_ludd.project_runner.profile import load_project_profile


def _write(root: Path, name: str, body: str) -> None:
    (root / name).write_text(body, encoding="utf-8")


def test_detect_pyproject_toml_returns_python_profile(tmp_path):
    """A ``pyproject.toml`` marker yields a Python profile with pytest/ruff/mypy
    commands — the canonical gludd-shaped toolchain."""
    _write(tmp_path, "pyproject.toml", "[project]\nname = 'demo'\n")
    prof = ToolchainDetector.detect(tmp_path)
    assert prof is not None
    assert prof.has("test")
    assert prof.has("lint")
    assert prof.has("typecheck")
    assert "pytest" in prof.commands["test"]
    assert "ruff" in prof.commands["lint"]
    assert "mypy" in prof.commands["typecheck"]


def test_detect_package_json_returns_node_profile(tmp_path):
    """A ``package.json`` with a test script yields a Node profile with
    ``npm test`` / ``npm run lint`` commands."""
    _write(
        tmp_path,
        "package.json",
        json.dumps(
            {"name": "app", "scripts": {"test": "jest", "lint": "eslint ."}}
        ),
    )
    prof = ToolchainDetector.detect(tmp_path)
    assert prof is not None
    assert prof.has("test")
    assert prof.has("lint")
    assert prof.commands["test"].split()[0] == "npm"
    assert prof.commands["lint"].split()[0] == "npm"


def test_detect_go_mod_returns_go_profile(tmp_path):
    """A ``go.mod`` marker yields a Go profile with ``go test`` / ``go vet``."""
    _write(tmp_path, "go.mod", "module example.com/app\n\ngo 1.21\n")
    prof = ToolchainDetector.detect(tmp_path)
    assert prof is not None
    assert prof.commands["test"].split()[0] == "go"
    assert "test" in prof.commands["test"]
    assert prof.has("lint")
    assert "vet" in prof.commands["lint"]


def test_detect_cargo_toml_returns_rust_profile(tmp_path):
    """A ``Cargo.toml`` marker yields a Rust profile with ``cargo test`` / clippy."""
    _write(tmp_path, "Cargo.toml", "[package]\nname = 'app'\nversion = '0.1'\n")
    prof = ToolchainDetector.detect(tmp_path)
    assert prof is not None
    assert prof.commands["test"].split()[0] == "cargo"
    assert prof.has("lint")
    assert "clippy" in prof.commands["lint"]


def test_detect_makefile_returns_make_profile(tmp_path):
    """A bare ``Makefile`` (no other markers) yields a make-driven profile with
    ``make test`` / ``make lint`` — gludd's own toolchain shape."""
    _write(tmp_path, "Makefile", "test:\n\ttrue\nlint:\n\ttrue\n")
    prof = ToolchainDetector.detect(tmp_path)
    assert prof is not None
    assert prof.commands["test"].split()[0] == "make"
    assert prof.commands["lint"].split()[0] == "make"


def test_detect_no_markers_returns_none(tmp_path):
    """An empty workspace yields None — detection does not invent a toolchain,
    the caller decides fail-closed behavior."""
    assert ToolchainDetector.detect(tmp_path) is None


def test_detect_multiple_markers_prefers_pyproject(tmp_path):
    """When both ``pyproject.toml`` and ``Makefile`` are present, the Python
    profile wins (deterministic resolution order)."""
    _write(tmp_path, "pyproject.toml", "[project]\nname = 'app'\n")
    _write(tmp_path, "Makefile", "test:\n\ttrue\n")
    prof = ToolchainDetector.detect(tmp_path)
    assert prof is not None
    # Python's pytest, not make.
    assert "pytest" in prof.commands["test"]


def test_detect_package_json_without_test_script_omits_test_cmd(tmp_path):
    """A ``package.json`` without ``scripts.test`` produces a profile that has
    no ``test`` command — detection only declares what the project declares."""
    _write(
        tmp_path,
        "package.json",
        json.dumps({"name": "app", "scripts": {"build": "tsc"}}),
    )
    prof = ToolchainDetector.detect(tmp_path)
    assert prof is not None
    assert not prof.has("test")
    # Lint is also absent → not declared.
    assert not prof.has("lint")


def test_detected_profile_allowed_exec_matches_commands(tmp_path):
    """Every binary the detected commands invoke must be in ``allowed_exec`` —
    otherwise the safety allowlist in ``ProjectProfile.resolve_argv`` would
    reject the detected commands as a configuration error."""
    _write(tmp_path, "pyproject.toml", "[project]\nname = 'app'\n")
    prof = ToolchainDetector.detect(tmp_path)
    assert prof is not None
    import shlex

    for cmd in prof.commands.values():
        exe = shlex.split(cmd)[0]
        # ``make``-shaped commands are parsed by the runner via the profile's
        # resolve_argv, which checks basename against allowed_exec. The basename
        # of every declared command must be present.
        import os

        assert os.path.basename(exe) in prof.allowed_exec, (
            f"detected command '{cmd}' invokes '{exe}' but '{os.path.basename(exe)}' "
            f"is not in allowed_exec={prof.allowed_exec}"
        )


def test_explicit_project_yml_overrides_detection(tmp_path):
    """When ``project.yml`` is present, it wins — detection is only the fallback
    for repos that have not authored one."""
    _write(
        tmp_path,
        "project.yml",
        "name: explicit\nallowed_exec: [pytest]\ncommands:\n  test: pytest -q\n",
    )
    _write(tmp_path, "pyproject.toml", "[project]\nname = 'app'\n")  # would-be detector
    prof = load_project_profile(tmp_path)
    assert prof.name == "explicit"
    # The explicit profile has no lint; the detector would have supplied one.
    # The explicit file wins, so lint is absent.
    assert not prof.has("lint")


def test_load_project_profile_falls_back_to_detection(tmp_path):
    """Without a ``project.yml``, ``load_project_profile`` falls back to
    detection rather than raising — and raises ONLY when both fail (empty repo)."""
    _write(tmp_path, "pyproject.toml", "[project]\nname = 'app'\n")
    prof = load_project_profile(tmp_path)
    assert prof.has("test")  # detection supplied it
    assert "pytest" in prof.commands["test"]


def test_load_project_profile_raises_when_no_yml_and_no_markers(tmp_path):
    """Empty workspace → ``load_project_profile`` raises (fail-closed). Detection
    returning None is not silent acceptance — the caller surfaces an error."""
    with pytest.raises(ProjectProfileError):
        load_project_profile(tmp_path)
