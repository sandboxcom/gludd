"""Pinned upstream SearXNG freeze and smoke contracts without an EE allocation."""

from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import logging
import os
import runpy
import shlex
import subprocess
import sys
from collections.abc import Callable, Iterator
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


@pytest.fixture(autouse=True)
def isolated_native_smoke_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> Iterator[None]:
    """Replay the native child config without leaking log handlers into pytest."""
    settings = tmp_path / "settings.yml"
    settings.write_bytes(artifacts.INPUTS["smoke_settings"].read_bytes())
    (tmp_path / "limiter.toml").write_bytes(artifacts.INPUTS["smoke_limiter"].read_bytes())
    monkeypatch.setattr(artifacts, "SMOKE_SETTINGS_PATH", settings)
    previous = tuple(logging.getLogger().handlers)
    yield
    logging.getLogger().handlers[:] = previous


class FrozenSource(NamedTuple):
    root: Path
    frozen: Path
    package: Path
    commit: str
    version: str


def _fixture(name: str) -> Path:
    return artifacts.ROOT / "tests/fixtures/ansible_ee_searxng" / f"{name}.txt"


def _git(root: Path, *arguments: str) -> str:
    """Bind the subprocess's audited cwd to its fixture repository."""
    result = subprocess.run(
        ["git", "-C", str(root), *arguments],
        cwd=root, check=True, capture_output=True, text=True, timeout=10,
    )
    return result.stdout.strip()


def test_git_helper_preserves_argv_and_uses_explicit_repository_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path.resolve()
    arguments = ("checkout", "--quiet", "--detach", "a" * 40)

    def run(command: list[str], **options: object) -> subprocess.CompletedProcess[str]:
        assert command == ["git", "-C", str(root), *arguments]
        assert options == {
            "cwd": root, "check": True, "capture_output": True,
            "text": True, "timeout": 10,
        }
        return subprocess.CompletedProcess(command, 0, "  fixture output\n", "")

    monkeypatch.setattr(subprocess, "run", run)
    assert _git(root, *arguments) == "fixture output"


def test_git_helper_keeps_real_frozen_checkout_outside_active_repository(
    tmp_path: Path, request: pytest.FixtureRequest,
) -> None:
    protected_root = artifacts.ROOT.resolve()
    git_dir = Path(_git(protected_root, "rev-parse", "--absolute-git-dir"))

    def protected_state() -> tuple[bytes, bytes, str, str, str]:
        return (
            (git_dir / "HEAD").read_bytes(),
            (git_dir / "index").read_bytes(),
            _git(protected_root, "rev-parse", "HEAD"),
            _git(
                protected_root, "--no-optional-locks", "status", "--porcelain=v1",
                "--untracked-files=all",
            ),
            _git(
                protected_root, "--no-optional-locks", "diff", "--no-ext-diff",
                "--no-textconv", "HEAD", "--",
            ),
        )

    before = protected_state()
    try:
        source = cast(FrozenSource, request.getfixturevalue("frozen_source"))
        assert source.root == source.root.resolve(strict=True)
        assert source.root.is_relative_to(tmp_path.resolve())
        assert not source.root.is_relative_to(protected_root)
        assert (source.root / ".git").is_dir()
        assert not (source.root / ".git").is_symlink()
        assert _git(source.root, "rev-parse", "--show-toplevel") == str(source.root)
        assert _git(source.root, "rev-parse", "HEAD") == source.commit
        assert _git(source.root, "rev-parse", "--abbrev-ref", "HEAD") == "HEAD"
    finally:
        assert protected_state() == before


def test_pinned_fixture_owner_cannot_be_shadowed_by_adjacent_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    adjacent = tmp_path / "fixtures"
    adjacent.mkdir()
    (adjacent / "version.py.txt").write_text("foreign source fixture\n")
    monkeypatch.setitem(globals(), "__file__", str(tmp_path / "test_acceptance.py"))
    expected = artifacts.ROOT / "tests/fixtures/ansible_ee_searxng/version.py.txt"
    assert _fixture("version.py") == expected
    assert _fixture("version.py").read_bytes() == expected.read_bytes()


def _source_environment() -> dict[str, str]:
    """Use pinned source defaults independently of local SearXNG configuration."""
    environment = {
        key: value for key, value in os.environ.items()
        if not key.startswith("SEARXNG_") and key != "PYTHONPATH"
    }
    environment["SEARXNG_DISABLE_ETC_SETTINGS"] = "1"
    return environment


@pytest.fixture
def frozen_source(tmp_path: Path) -> FrozenSource:
    root = tmp_path / "gludd-searxng-source"
    package = root / "searx"
    package.mkdir(parents=True)
    for name in (
        "__init__.py", "exceptions.py", "settings_loader.py", "settings_defaults.py",
        "sxng_locales.py", "unixthreadname.py", "version.py", "settings.yml",
    ):
        fixture_name = "settings.yml.effective" if name == "settings.yml" else name
        (package / name).write_bytes(_fixture(fixture_name).read_bytes())
    for directory in ("static", "templates"):
        (package / directory).mkdir()
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
        cwd=root, env=_source_environment(), check=True,
        capture_output=True, text=True, timeout=10,
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
    assert python[-5:-1] == [artifacts.CONTROLLER_PYTHON_PATH, "-W", "error", "-c"]
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
        if token == "-c" and index > 0 and tokens[index - 1] == artifacts.CONTROLLER_PYTHON_PATH
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
    assert f"{artifacts.CONTROLLER_PYTHON_PATH} -m searx.version freeze" in step
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


def test_default_settings_fixture_preserves_complete_pinned_source() -> None:
    """Require every effective default while excluding nonconfiguration comments."""
    contract = json.loads(_fixture("settings.yml.provenance.json").read_text())
    settings = _fixture("settings.yml.effective").read_bytes()
    assert len(settings.splitlines()) < 2500
    assert contract["source_revision"] == artifacts.SEARXNG_SOURCE_REVISION
    assert contract["source_path"] == "searx/settings.yml"
    assert contract["source_sha256"] == (
        "def5b71f3b85eba27d54766796444a336ef1dbc360c5b37903417aeeef8f12c5"
    )
    assert contract["effective_defaults_sha256"] == (
        "81130964268a31068a7fdaac70c42f9221ca1c7fe56c584e5976f2d3d6b12add"
    )
    assert contract["effective_engine_count"] == 246
    assert contract["serialization"] == {
        "parser": "PyYAML.safe_load", "emitter": "PyYAML.safe_dump",
        "sort_keys": False, "allow_unicode": True,
    }
    assert hashlib.sha256(settings).hexdigest() == contract["serialized_yaml_sha256"]
    defaults = yaml.safe_load(settings)
    assert len(defaults["engines"]) == contract["effective_engine_count"]
    assert sorted(defaults) == contract["effective_top_level_keys"]
    semantic = json.dumps(
        defaults, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
    ).encode()
    assert hashlib.sha256(semantic).hexdigest() == contract["effective_defaults_sha256"]
    expected_sources = {
        "wikipedia.py": (
            "searx/engines/wikipedia.py",
            "184789eba028a7f7a192d61303b57a2d61af2e29752d1f0a0634aca9af9488b9",
        ),
        "botdetection-config.py": (
            "searx/botdetection/config.py",
            "aafa5a4120fb18ffabdb6085d3674030d9e89c7cd55e92660f5b9d5e70f198dd",
        ),
        "compat.py": (
            "searx/compat.py",
            "3ae8b8f374247f9cb948610141eb89328495b8873c4605913663f1c5bc740e2c",
        ),
        "limiter.toml": (
            "searx/limiter.toml",
            "f90c7cb5e8e92c14139e8f14b741b520c7e2af4f27407eab6b187905a58656cd",
        ),
    }
    sources = contract["runtime_source_fixtures"]
    assert set(sources) == set(expected_sources)
    for name, (source_path, source_sha256) in expected_sources.items():
        assert sources[name] == {
            "source_revision": artifacts.SEARXNG_SOURCE_REVISION,
            "source_path": source_path,
            "source_url": "https://raw.githubusercontent.com/searxng/searxng/"
            + artifacts.SEARXNG_SOURCE_REVISION + "/" + source_path,
            "source_sha256": source_sha256,
        }
        assert hashlib.sha256(_fixture(name).read_bytes()).hexdigest() == source_sha256


def test_module_freezer_loads_real_pinned_initializer_before_writing(
    frozen_source: FrozenSource, tmp_path: Path,
) -> None:
    """Malformed settings must fail inside the actual first-party initializer."""
    settings = tmp_path / "invalid-settings.yml"
    settings.write_text("use_default_settings: true\nserver:\n  secret_key: 7\n")
    environment = _source_environment()
    environment["SEARXNG_SETTINGS_PATH"] = str(settings)
    before = frozen_source.frozen.read_bytes()
    result = subprocess.run(
        [sys.executable, "-W", "error", "-m", "searx.version", "freeze"],
        cwd=frozen_source.root, env=environment, check=False,
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode != 0
    assert "server.secret_key" in result.stderr
    assert "Invalid settings.yml" in result.stderr
    assert "version_frozen.py created" not in result.stdout
    assert frozen_source.frozen.read_bytes() == before


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
            and all(
                alias.name in {"os", "secrets", "json", "sys", "logging", "sqlite3", "tomllib"}
                for alias in node.names
            )
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
    inherited_value = os.environ["SEARXNG_SECRET"]
    exec(compile(prefix, "<real-smoke-secret-prefix>", "exec"), {})
    secret = os.environ["SEARXNG_SECRET"]
    assert len(secret) == 64 and secret != inherited_value
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
    inherited_value = os.environ["SEARXNG_SECRET"]
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
        assert os.environ["SEARXNG_SECRET"] != inherited_value
    else:
        with pytest.raises((SystemExit, ValueError, TypeError, ImportError)):
            exec(compile(source, "<actual-installed-controller-smoke>", "exec"), {})
        assert "ANSIBLE_EE_IMPORT_OK" not in capsys.readouterr().out
