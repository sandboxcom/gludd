"""Pinned upstream SearXNG freeze and smoke contracts without an EE allocation."""

from __future__ import annotations

import ast
import importlib.util
import json
import os
import runpy
import shlex
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import NamedTuple, cast

import pytest
import yaml
from scripts import ansible_runtime_artifacts as artifacts

IMPORTS = (
    "ansible", "ansible_runner", "azure.identity", "azure.monitor.query",
    "frictionless", "jsonpointer", "openapi_core", "searx.webapp", "signxml",
)


class FrozenSource(NamedTuple):
    root: Path
    frozen: Path
    package: Path
    commit: str
    version: str


def _fixture(name: str) -> Path:
    adjacent = Path(__file__).parent / "fixtures" / f"{name}.txt"
    if adjacent.is_file():
        return adjacent
    return artifacts.ROOT / "tests/fixtures/ansible_ee_searxng" / f"{name}.txt"


def _git(root: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=True, capture_output=True, text=True, timeout=10,
    )
    return result.stdout.strip()


@pytest.fixture
def frozen_source(tmp_path: Path) -> FrozenSource:
    root = tmp_path / "gludd-searxng-source"
    package = root / "searx"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    for name in ("version.py", "settings_defaults.py", "sxng_locales.py"):
        (package / name).write_text(_fixture(name).read_text())
    _git(root, "init", "--quiet", "--initial-branch=master")
    _git(root, "config", "user.name", "Gludd source fixture")
    _git(root, "config", "user.email", "fixture@example.test")
    _git(root, "config", "core.abbrev", "40")
    _git(root, "add", "searx")
    _git(
        root, "-c", "core.hooksPath=/dev/null", "-c", "commit.gpgsign=false",
        "commit", "--quiet", "--message", "pinned-source behavior fixture",
    )
    commit = _git(root, "rev-parse", "HEAD")
    _git(root, "remote", "add", "origin", "https://github.com/searxng/searxng.git")
    _git(root, "update-ref", "refs/remotes/origin/master", commit)
    _git(root, "branch", "--set-upstream-to=origin/master", "master")
    _git(root, "checkout", "--quiet", "--detach", commit)
    generated = subprocess.run(
        [sys.executable, "-W", "error", "-m", "searx.version", "freeze"],
        cwd=root, check=True, capture_output=True, text=True, timeout=10,
    )
    assert "version_frozen.py created" in generated.stdout
    assert "ERROR" not in generated.stderr
    frozen = package / "version_frozen.py"
    values = runpy.run_path(str(frozen))
    version = str(values["VERSION_TAG"])
    return FrozenSource(root, frozen, tmp_path / "installed" / "searx", commit, version)


def _smoke(monkeypatch: pytest.MonkeyPatch) -> str:
    calls: list[list[str]] = []

    def run(command: list[str], *, check: bool = False) -> SimpleNamespace:
        assert check is False
        calls.append(command)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(subprocess, "run", run)
    assert artifacts._smoke_environment("podman", "sha256:" + "c" * 64) == 0
    assert len(calls) == 2
    python = calls[1]
    assert "--network=none" in python and "--pull=never" in python
    assert python[-3:-1] == ["/usr/bin/python3.11", "-c"]
    return python[-1]


def _controller_import(tree: ast.Module) -> ast.Import:
    matches = [
        node for node in tree.body
        if isinstance(node, ast.Import) and tuple(alias.name for alias in node.names) == IMPORTS
    ]
    assert len(matches) == 1, "the entire ordered nine-import statement must remain"
    return matches[0]


def _freeze_step() -> str:
    definition: object = yaml.safe_load(artifacts.DEFINITION.read_text())
    assert isinstance(definition, dict)
    steps = definition["additional_build_steps"]
    assert isinstance(steps, dict)
    final = steps["append_final"]
    assert isinstance(final, list)
    matches = [
        step for step in final
        if isinstance(step, str) and "-m searx.version freeze" in step
    ]
    assert len(matches) == 1, "package the upstream frozen module in the final image"
    return matches[0]


def _installer() -> str:
    tokens = shlex.split(_freeze_step().removeprefix("RUN "))
    indexes = [
        index for index, token in enumerate(tokens)
        if token == "-c" and index > 0 and tokens[index - 1] == "/usr/bin/python3.11"
    ]
    assert len(indexes) == 1
    return tokens[indexes[0] + 1]


def test_complete_smoke_uses_ephemeral_secret_before_all_nine_imports(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tree = ast.parse(_smoke(monkeypatch))
    imports = _controller_import(tree)
    assignments = [
        node for node in tree.body if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Subscript)
            and isinstance(target.value, ast.Attribute)
            and isinstance(target.value.value, ast.Name)
            and target.value.value.id == "os"
            and target.value.attr == "environ"
            and isinstance(target.slice, ast.Constant)
            and target.slice.value == "SEARXNG_SECRET"
            for target in node.targets
        )
    ]
    assert len(assignments) == 1 and assignments[0].lineno < imports.lineno
    value = assignments[0].value
    assert isinstance(value, ast.Call) and isinstance(value.func, ast.Attribute)
    assert isinstance(value.func.value, ast.Name) and value.func.value.id == "secrets"
    assert value.func.attr == "token_hex" and [ast.literal_eval(arg) for arg in value.args] == [32]
    assert isinstance(tree.body[-1], ast.Expr)
    assert ast.unparse(tree.body[-1]) == "print('ANSIBLE_EE_IMPORT_OK')"


def test_smoke_requires_installed_frozen_source_provenance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _smoke(monkeypatch)
    tree = ast.parse(source)
    _controller_import(tree)
    assert "searx.version_frozen" in source
    assert "direct_url.json" in source and artifacts.SEARXNG_SOURCE_REVISION in source
    assert "locate_file" in source and "__file__" in source
    assert "sys.modules" not in source
    assert "SEARXNG_DEBUG" not in source
    assert "logging.disable" not in source


def test_packaging_uses_verified_owned_clone_and_native_upstream_freezer() -> None:
    step = _freeze_step()
    assert "mktemp -d /tmp/gludd-ansible-ee-searxng-" in step
    assert "trap " in step and "rm -rf --" in step
    assert "https://github.com/searxng/searxng.git" in step
    assert artifacts.SEARXNG_SOURCE_REVISION in step
    assert "checkout --detach" in step and "rev-parse HEAD" in step
    assert "/usr/bin/python3.11 -m searx.version freeze" in step
    assert "direct_url.json" in _installer()
    assert "SEARXNG_SECRET" not in step and "sys.modules" not in step


def test_real_pinned_freezer_preserves_verified_detached_git_identity(
    frozen_source: FrozenSource,
) -> None:
    values = runpy.run_path(str(frozen_source.frozen))
    assert values["VERSION_STRING"] == values["VERSION_TAG"] == frozen_source.version
    assert frozen_source.version.endswith("+" + frozen_source.commit)
    assert values["DOCKER_TAG"] == frozen_source.version.replace("+", "-")
    assert values["GIT_URL"] == "https://github.com/searxng/searxng"
    assert values["GIT_BRANCH"] == "master"


def test_ephemeral_smoke_secret_uses_real_pinned_settings_schema(
    monkeypatch: pytest.MonkeyPatch, frozen_source: FrozenSource,
) -> None:
    tree = ast.parse(_smoke(monkeypatch))
    imports = _controller_import(tree)
    prefix_nodes: list[ast.stmt] = []
    for node in tree.body:
        if node.lineno >= imports.lineno:
            break
        if (
            isinstance(node, ast.Import)
            and all(alias.name in {"os", "secrets", "json", "sys"} for alias in node.names)
        ) or (
            isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Subscript)
                and isinstance(target.slice, ast.Constant)
                and target.slice.value == "SEARXNG_SECRET"
                for target in node.targets
            )
        ):
            prefix_nodes.append(node)
    prefix = ast.Module(body=prefix_nodes, type_ignores=[])
    monkeypatch.setenv("SEARXNG_SECRET", "ultrasecretkey")
    exec(compile(prefix, "<real-smoke-secret-prefix>", "exec"), {})
    secret = os.environ["SEARXNG_SECRET"]
    assert len(secret) == 64 and secret != "ultrasecretkey"
    with monkeypatch.context() as context:
        package = ModuleType("searx")
        package.__path__ = [str(frozen_source.root / "searx")]
        context.setitem(sys.modules, "searx", package)
        for name in ("sxng_locales", "settings_defaults"):
            spec = importlib.util.spec_from_file_location(
                f"searx.{name}", frozen_source.root / "searx" / f"{name}.py"
            )
            assert spec is not None and spec.loader is not None
            module = importlib.util.module_from_spec(spec)
            context.setitem(sys.modules, spec.name, module)
            spec.loader.exec_module(module)
        schema = cast(dict[str, dict[str, object]], module.SCHEMA)
        validate_secret = cast(Callable[[str], str], schema["server"]["secret_key"])
        assert validate_secret("ultrasecretkey") == secret


@pytest.mark.parametrize(
    "change", ("valid", "normalized_short_version", "foreign_url", "foreign_commit", "foreign_version", "missing"),
)
def test_installer_copies_only_matching_real_frozen_provenance(
    frozen_source: FrozenSource, monkeypatch: pytest.MonkeyPatch, change: str,
) -> None:
    from importlib import metadata

    package = frozen_source.package
    package.mkdir(parents=True)
    target = package / "version_frozen.py"
    original = b"original package file\n"
    target.write_bytes(original)
    vcs_info = {"vcs": "git", "commit_id": frozen_source.commit}
    record: dict[str, object] = {
        "url": "https://github.com/searxng/searxng.git",
        "vcs_info": vcs_info,
    }
    if change == "foreign_url":
        record["url"] = "https://example.test/foreign.git"
    elif change == "foreign_commit":
        vcs_info["commit_id"] = "f" * 40
    version = "1.0.0" if change == "foreign_version" else frozen_source.version
    if change == "normalized_short_version":
        release = ".".join(part.rjust(4, "0") for part in version.partition("+")[0].split("."))
        version = release + "+" + frozen_source.commit[:9]
    distribution = SimpleNamespace(
        version=version,
        read_text=lambda name: None if change == "missing" else json.dumps(record),
        locate_file=lambda name: package.parent / name,
    )
    monkeypatch.setattr(metadata, "distribution", lambda name: distribution)
    monkeypatch.setattr(sys, "argv", ["freeze-installer", str(frozen_source.frozen), frozen_source.commit])
    source = _installer()
    if change in {"valid", "normalized_short_version"}:
        exec(compile(source, "<real-freeze-installer>", "exec"), {})
        assert target.read_bytes() == frozen_source.frozen.read_bytes()
    else:
        with pytest.raises((SystemExit, ValueError, TypeError)):
            exec(compile(source, "<real-freeze-installer>", "exec"), {})
        assert target.read_bytes() == original


@pytest.mark.parametrize(
    "change",
    (
        "valid", "normalized_short_version", "foreign_url", "foreign_commit",
        "foreign_version", "missing_provenance", "foreign_package_path",
        "missing_frozen", "foreign_frozen_commit",
    ),
)
def test_actual_installed_smoke_enforces_frozen_provenance_and_normalization(
    frozen_source: FrozenSource,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    change: str,
) -> None:
    from importlib import metadata

    package = frozen_source.package
    package.mkdir(parents=True)
    target = package / "version_frozen.py"
    target.write_bytes(frozen_source.frozen.read_bytes())
    monkeypatch.setattr(artifacts, "SEARXNG_SOURCE_REVISION", frozen_source.commit)
    monkeypatch.setenv("SEARXNG_SECRET", "ultrasecretkey")
    source = _smoke(monkeypatch)
    capsys.readouterr()

    # Isolate only dependency availability; execute the emitted smoke unchanged.
    modules: dict[str, ModuleType] = {}
    for imported in IMPORTS:
        parts = imported.split(".")
        for count in range(1, len(parts) + 1):
            name = ".".join(parts[:count])
            if name in modules:
                continue
            module = ModuleType(name)
            module.__path__ = [str(package)] if name == "searx" else []
            modules[name] = module
            monkeypatch.setitem(sys.modules, name, module)
            if count > 1:
                setattr(modules[".".join(parts[:count - 1])], parts[count - 1], module)
    monkeypatch.delitem(sys.modules, "searx.version_frozen", raising=False)

    module_path = target
    if change == "foreign_package_path":
        module_path = package.parent / "foreign" / "version_frozen.py"
        module_path.parent.mkdir()
        module_path.write_bytes(target.read_bytes())
    if change == "missing_frozen":
        target.unlink()
    else:
        spec = importlib.util.spec_from_file_location("searx.version_frozen", module_path)
        assert spec is not None and spec.loader is not None
        frozen = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(frozen)
        if change == "foreign_frozen_commit":
            wrong_commit = ("0" if frozen_source.commit[0] != "0" else "1") + frozen_source.commit[1:]
            wrong_version = frozen_source.version.replace(frozen_source.commit, wrong_commit)
            monkeypatch.setattr(frozen, "VERSION_TAG", wrong_version)
            monkeypatch.setattr(frozen, "VERSION_STRING", wrong_version)
            monkeypatch.setattr(frozen, "DOCKER_TAG", wrong_version.replace("+", "-"))
        monkeypatch.setitem(sys.modules, "searx.version_frozen", frozen)
        modules["searx"].__dict__["version_frozen"] = frozen

    vcs = {"vcs": "git", "commit_id": frozen_source.commit}
    record: dict[str, object] = {
        "url": "https://github.com/searxng/searxng.git", "vcs_info": vcs,
    }
    if change == "foreign_url":
        record["url"] = "https://example.test/foreign.git"
    elif change == "foreign_commit":
        vcs["commit_id"] = ("0" if frozen_source.commit[0] != "0" else "1") + frozen_source.commit[1:]
    version = frozen_source.version
    if change == "normalized_short_version":
        release = ".".join(part.rjust(4, "0") for part in version.partition("+")[0].split("."))
        version = release + "+" + frozen_source.commit[:9]
    elif change == "foreign_version":
        version = "1.0.0+" + frozen_source.commit
    distribution = SimpleNamespace(
        version=version,
        read_text=lambda name: None if change == "missing_provenance" else json.dumps(record),
        locate_file=lambda name: package.parent / name,
    )
    monkeypatch.setattr(metadata, "distribution", lambda name: distribution)

    if change in {"valid", "normalized_short_version"}:
        exec(compile(source, "<actual-installed-controller-smoke>", "exec"), {})
        assert capsys.readouterr().out == "ANSIBLE_EE_IMPORT_OK\n"
        assert len(os.environ["SEARXNG_SECRET"]) == 64
        assert os.environ["SEARXNG_SECRET"] != "ultrasecretkey"
    else:
        with pytest.raises((SystemExit, ValueError, TypeError, ImportError)):
            exec(compile(source, "<actual-installed-controller-smoke>", "exec"), {})
        assert "ANSIBLE_EE_IMPORT_OK" not in capsys.readouterr().out
