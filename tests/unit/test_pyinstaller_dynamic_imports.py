"""Regression coverage for dependencies imported dynamically at runtime."""

import ast
import importlib.machinery
import runpy
import sys
import tomllib
from pathlib import Path

import filelock
import pytest

_ROOT = Path(__file__).parents[2]


def test_safe_diskcache_serializer_is_bundled() -> None:
    spec = (_ROOT / "gludd.spec").read_text()

    assert "'msgpack'," in spec


def test_project_collections_are_externalized_from_frozen_core() -> None:
    spec = (_ROOT / "gludd.spec").read_text()

    assert "('collections', 'collections')" not in spec
    assert (_ROOT / "config/ansible/execution-environment.yml").is_file()
    assert (_ROOT / "config/ansible/requirements.yml").is_file()


def test_frozen_daemon_runtime_is_bundled() -> None:
    spec = (_ROOT / "gludd.spec").read_text()

    assert "'gunicorn.app.wsgiapp'," in spec
    assert "'gunicorn.glogging'," in spec
    assert "'uvicorn_worker'," in spec


def test_gunicorn_type_stubs_are_declared_in_all_dev_profile_sets() -> None:
    with (
        _ROOT / "requirements/profiles/dev-quality/pyproject.toml"
    ).open("rb") as stream:
        profile = tomllib.load(stream)
    with (_ROOT / "config/dependency_profiles.toml").open("rb") as stream:
        catalog = tomllib.load(stream)

    assert any(
        dependency.startswith("types-gunicorn")
        for dependency in profile["project"]["dependencies"]
    )
    for set_name in ("development", "ci"):
        assert "dev-quality" in catalog["sets"][set_name]["profiles"]


def test_native_commit_observer_is_resolved_from_frozen_analysis_inputs() -> None:
    """Archive the native observer that the frozen mutex resolves dynamically."""
    source = ast.parse((_ROOT / "gludd.spec").read_text())
    analysis = next(
        node for node in ast.walk(source)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "Analysis"
    )
    keywords = {item.arg: item.value for item in analysis.keywords}
    hiddenimports = ast.literal_eval(keywords["hiddenimports"])
    pathex = [str(_ROOT / item) for item in ast.literal_eval(keywords["pathex"])]
    assert "stream_command" in hiddenimports
    observer_spec = importlib.machinery.PathFinder.find_spec("stream_command", pathex)
    assert observer_spec is not None and observer_spec.origin is not None
    assert Path(observer_spec.origin).resolve() == (_ROOT / "scripts" / "stream_command.py").resolve()


@pytest.mark.parametrize(
    ("platform", "expected_excludedimports"),
    [
        ("darwin", ["ctypes"]),
        ("linux", ["ctypes"]),
        ("win32", []),
    ],
)
def test_filelock_windows_hook_is_platform_local_and_preserves_the_module(
    monkeypatch: pytest.MonkeyPatch,
    platform: str,
    expected_excludedimports: list[str],
) -> None:
    """Execute the actual hook without removing filelock's public Windows module."""
    source = ast.parse((_ROOT / "gludd.spec").read_text(encoding="utf-8"))
    analysis = next(
        node
        for node in ast.walk(source)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "Analysis"
    )
    keywords = {item.arg: item.value for item in analysis.keywords}
    hook_paths = ast.literal_eval(keywords["hookspath"])
    assert hook_paths == ["scripts/pyinstaller_hooks"]
    hook_path = _ROOT / hook_paths[0] / "hook-filelock._windows.py"
    assert hook_path.is_file()

    exclusion_values = [keywords["excludes"]]
    exclusion_values.extend(
        node.value
        for node in ast.walk(source)
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "_platform_excludes"
            for target in node.targets
        )
    )
    explicit_exclusions = {
        node.value
        for value in exclusion_values
        for node in ast.walk(value)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    assert not {"filelock._windows", "ctypes"} & explicit_exclusions
    windows = importlib.import_module("filelock._windows")
    assert windows.WindowsFileLock is filelock.WindowsFileLock
    assert issubclass(windows.WindowsFileLock, filelock.BaseFileLock)
    assert filelock.AsyncWindowsFileLock is not None

    with monkeypatch.context() as platform_patch:
        platform_patch.setattr(sys, "platform", platform)
        namespace = runpy.run_path(str(hook_path))
    assert namespace["excludedimports"] == expected_excludedimports
    assert "hiddenimports" not in namespace
