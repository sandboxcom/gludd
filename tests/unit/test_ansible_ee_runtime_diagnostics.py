"""Executable acceptance for the warning-bearing installed EE repair."""

from __future__ import annotations

import ast
import copy
import hashlib
import logging
import os
import sqlite3
import subprocess
import sys
import tomllib
from collections.abc import Callable, Mapping
from importlib import import_module
from itertools import filterfalse
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest
import yaml

if TYPE_CHECKING:
    import ansible_runtime_artifacts as artifacts
else:
    from scripts import ansible_runtime_artifacts as artifacts

MANAGED_PYTHON = "/opt/gludd/ansible-ee-python/bin/python3.11"
IMPORTS = (
    "ansible", "ansible_runner", "azure.identity", "azure.monitor.query",
    "frictionless", "jsonpointer", "openapi_core", "searx.webapp", "signxml",
)
SOURCE_HASHES = (
    "7086a336e6ea0a49595cf891066ab6517156c85116f77fbc23b62c1d9e9b7d92",
    "26ee69bff5d6d4173360275fcf38d4d25228286b79a7d9b36ad39af970909e37",
)


def test_managed_native_python_is_the_single_controller_path() -> None:
    definition = yaml.safe_load(artifacts.DEFINITION.read_text())
    interpreter = {"package_system": "python3.11", "python_path": MANAGED_PYTHON}
    assert definition["dependencies"]["python_interpreter"] == interpreter
    assert artifacts.EXPECTED_DEPENDENCIES["python_interpreter"] == interpreter
    steps = definition["additional_build_steps"]
    bootstrap = "\n".join(steps.get("prepend_base", []))
    assert "$PKGMGR install -y python3.11" in bootstrap
    assert "/usr/bin/python3.11" in bootstrap
    assert "3.11.17+20261001" in bootstrap
    assert all(identity in bootstrap for identity in SOURCE_HASHES)
    assert "sha256sum --check --strict" in bootstrap
    assert "--strip-components=1" in bootstrap
    assert "/usr/lib64" not in bootstrap and "LD_LIBRARY_PATH" not in bootstrap
    base = "\n".join(steps["append_base"])
    assert f'PIPCMD="{MANAGED_PYTHON} -m uv pip"' in base
    assert f"UV_PYTHON={MANAGED_PYTHON}" in base
    assert "/usr/bin/python3.11" not in "\n".join(steps["append_final"])
    assert "SEARXNG_SETTINGS_PATH" not in "\n".join(
        entry for stage in steps.values() for entry in stage
    )


def test_native_smoke_config_and_limiter_are_hashed_and_staged() -> None:
    assert "smoke_settings" in artifacts.INPUTS
    assert "smoke_limiter" in artifacts.INPUTS
    settings = yaml.safe_load(artifacts.INPUTS["smoke_settings"].read_text())
    assert settings == {"use_default_settings": {"engines": {"keep_only": ["wikipedia"]}}}
    assert tomllib.loads(artifacts.INPUTS["smoke_limiter"].read_text()) == {"botdetection": {}}
    definition = yaml.safe_load(artifacts.DEFINITION.read_text())
    assert definition["additional_build_files"] == artifacts.EXPECTED_BUILD_FILES
    assert {"src": "ee-smoke-settings.yml", "dest": "smoke"} in artifacts.EXPECTED_BUILD_FILES
    assert {"src": "ee-smoke-limiter.toml", "dest": "smoke"} in artifacts.EXPECTED_BUILD_FILES
    assert str(artifacts.SMOKE_SETTINGS_PATH) == "/etc/gludd/ansible-ee-smoke/settings.yml"


def _smoke_sections() -> tuple[ast.Module, ast.Module]:
    tree = ast.parse(artifacts._controller_import_smoke_code())
    imports = [
        (index, node) for index, node in enumerate(tree.body)
        if isinstance(node, ast.Import) and tuple(alias.name for alias in node.names) == IMPORTS
    ]
    assert len(imports) == 1
    index, _node = imports[0]
    checks_start = next(
        (
            position for position, node in enumerate(tree.body)
            if isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == "runtime_errors" for target in node.targets)
        ),
        len(tree.body) - 1,
    )
    assert checks_start > index
    assert ast.unparse(tree.body[-1]) == "print('ANSIBLE_EE_IMPORT_OK')"
    assert "sys.modules" not in artifacts._controller_import_smoke_code()
    return ast.Module(body=tree.body[:index], type_ignores=[]), ast.Module(
        body=tree.body[checks_start:], type_ignores=[]
    )


@pytest.mark.parametrize(
    "levels",
    [(logging.WARNING,), (logging.ERROR,), (logging.CRITICAL,), (logging.WARNING, logging.ERROR, logging.CRITICAL)],
)
def test_real_logrecords_fail_after_collecting_every_diagnostic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str], levels: tuple[int, ...],
) -> None:
    settings = tmp_path / "settings.yml"
    settings.write_text("use_default_settings: {engines: {keep_only: [wikipedia]}}\n")
    (tmp_path / "limiter.toml").write_text("[botdetection]\n")
    monkeypatch.setattr(artifacts, "SMOKE_SETTINGS_PATH", settings, raising=False)
    monkeypatch.setattr(os, "environ", dict(os.environ))
    prelude, final = _smoke_sections()
    namespace: dict[str, object] = {}
    original = tuple(logging.getLogger().handlers)
    try:
        exec(compile(prelude, "<native-smoke-prelude>", "exec"), namespace)
        for index, level in enumerate(levels):
            logging.getLogger("searx.runtime.acceptance").log(level, "native diagnostic %d", index)
        with pytest.raises(SystemExit) as refused:
            exec(compile(final, "<native-smoke-final>", "exec"), namespace)
        assert all(f"native diagnostic {index}" in str(refused.value) for index in range(len(levels)))
        assert all(f"native diagnostic {index}" in caplog.text for index in range(len(levels)))
        assert "ANSIBLE_EE_IMPORT_OK" not in capsys.readouterr().out
    finally:
        logging.getLogger().handlers[:] = original


@pytest.mark.parametrize("version,threading", [((3, 34, 1), 3), ((3, 35, 0), 1), ((3, 35, 0), 0)])
def test_real_stdlib_sqlite_requirements_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, version: tuple[int, int, int], threading: int,
) -> None:
    settings = tmp_path / "settings.yml"
    settings.write_text("use_default_settings: {engines: {keep_only: [wikipedia]}}\n")
    (tmp_path / "limiter.toml").write_text("[botdetection]\n")
    monkeypatch.setattr(artifacts, "SMOKE_SETTINGS_PATH", settings, raising=False)
    monkeypatch.setattr(os, "environ", dict(os.environ))
    monkeypatch.setattr(sqlite3, "sqlite_version_info", version)
    monkeypatch.setattr(sqlite3, "threadsafety", threading)
    prelude, final = _smoke_sections()
    namespace: dict[str, object] = {}
    original = tuple(logging.getLogger().handlers)
    try:
        exec(compile(prelude, "<native-smoke-prelude>", "exec"), namespace)
        with pytest.raises(SystemExit, match="SQLite"):
            exec(compile(final, "<native-smoke-final>", "exec"), namespace)
    finally:
        logging.getLogger().handlers[:] = original


def test_clean_native_sqlite_and_no_logrecords_reach_ok(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    settings = tmp_path / "settings.yml"
    settings.write_text("use_default_settings: {engines: {keep_only: [wikipedia]}}\n")
    (tmp_path / "limiter.toml").write_text("[botdetection]\n")
    monkeypatch.setattr(artifacts, "SMOKE_SETTINGS_PATH", settings, raising=False)
    monkeypatch.setattr(os, "environ", dict(os.environ))
    prelude, final = _smoke_sections()
    namespace: dict[str, object] = {}
    original = tuple(logging.getLogger().handlers)
    try:
        exec(compile(prelude, "<native-smoke-prelude>", "exec"), namespace)
        exec(compile(final, "<native-smoke-final>", "exec"), namespace)
        assert capsys.readouterr().out == "ANSIBLE_EE_IMPORT_OK\n"
        assert len(os.environ["SEARXNG_SECRET"]) == 64
        assert os.environ["SEARXNG_SETTINGS_PATH"] == str(settings)
    finally:
        logging.getLogger().handlers[:] = original


def test_hosted_smoke_uses_the_same_managed_interpreter() -> None:
    workflow = yaml.safe_load((artifacts.ROOT / ".github/workflows/build.yml").read_text())
    job = workflow["jobs"]["ansible-ee"]
    smoke = next(step["run"] for step in job["steps"] if step.get("name") == "Smoke Ansible execution environment")
    assert MANAGED_PYTHON in smoke and "/usr/bin/python3.11" not in smoke


def test_actual_builder_places_managed_bootstrap_before_native_consumers(tmp_path: Path) -> None:
    staged = tmp_path / "config/ansible"
    staged.mkdir(parents=True)
    for source in artifacts.INPUTS.values():
        (staged / source.name).write_bytes(source.read_bytes())
    for _source, artifact in artifacts.COLLECTION_ARTIFACTS:
        target = tmp_path / "dist/collections" / artifact.name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"generation-only fixture")
    result = subprocess.run(
        [
            sys.executable, "-W", "error", "-m", "ansible_builder", "create",
            "--file", str(staged / "execution-environment.yml"),
            "--context", str(tmp_path / "context"), "--output-filename", "Dockerfile",
        ],
        cwd=tmp_path, capture_output=True, text=True, check=False, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    rendered = (tmp_path / "context/Dockerfile").read_text()
    base, downstream = rendered.split("FROM base AS galaxy", 1)
    assert f'ARG PYCMD="{MANAGED_PYTHON}"' in rendered
    assert base.index("3.11.17+20261001") < base.index("RUN /output/scripts/pip_install $PYCMD")
    assert base.index("3.11.17+20261001") < base.index("RUN $PYCMD -m pip install --no-cache-dir $ANSIBLE_INSTALL_REFS")
    assert base.index("pip==26.2.1") < base.index("RUN $PYCMD -m pip install --no-cache-dir $ANSIBLE_INSTALL_REFS")
    assert "COPY _build/smoke/ee-smoke-settings.yml" in downstream
    assert "COPY _build/smoke/ee-smoke-limiter.toml" in downstream


def test_bootstrap_role_accepts_only_the_managed_controller_path() -> None:
    role = artifacts.ROOT / "collections/ansible_collections/general_ludd/agent/roles/execution_environment_bootstrap"
    defaults = yaml.safe_load((role / "defaults/main.yml").read_text())
    assert defaults["execution_environment_bootstrap_candidate_python_executable"] == MANAGED_PYTHON
    selector = (role / "tasks/validate.yml").read_text()
    assert "is match('^/opt/gludd/ansible-ee-python/bin/python3[.]11$')" in selector
    assert "is match('^/usr/bin/python3[.]11$')" not in selector


def test_pinned_wikipedia_source_has_no_network_initialization_callback() -> None:
    source = artifacts.ROOT / "tests/fixtures/ansible_ee_searxng/wikipedia.py.txt"
    tree = ast.parse(source.read_text())
    functions = {node.name for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
    assert {"request", "response", "fetch_traits"} <= functions
    assert not {"init", "setup"} & functions
    calls = [
        ast.unparse(node.value.func) for node in tree.body
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
    ]
    assert calls == ["lang_map.update"], "only the pinned local language-map update runs at module scope"


def test_pinned_native_settings_loader_keeps_only_wikipedia_without_changing_defaults() -> None:
    fixture = artifacts.ROOT / "tests/fixtures/ansible_ee_searxng"
    source = ast.parse((fixture / "settings_loader.py.txt").read_text())
    functions = [node for node in source.body if isinstance(node, ast.FunctionDef)]
    native = ast.Module(
        body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), *functions],
        type_ignores=[],
    )
    ast.fix_missing_locations(native)
    namespace: dict[str, object] = {"Mapping": Mapping, "Path": Path, "filterfalse": filterfalse}
    exec(compile(native, "<pinned-native-settings-loader>", "exec"), namespace)
    original = yaml.safe_load((fixture / "settings.yml.effective.txt").read_text())
    copied = copy.deepcopy(original)
    smoke = yaml.safe_load(artifacts.INPUTS["smoke_settings"].read_text())
    update_settings = cast(Callable[[dict[str, object], dict[str, object]], None], namespace["update_settings"])
    update_settings(copied, smoke)
    assert copied["engines"] == [entry for entry in original["engines"] if entry["name"] == "wikipedia"]
    assert len(copied["engines"]) == 1 and len(original["engines"]) > 1
    assert {key: value for key, value in copied.items() if key != "engines"} == {
        key: value for key, value in original.items() if key != "engines"
    }


def test_pinned_native_limiter_accepts_config_and_retains_every_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    fixture = artifacts.ROOT / "tests/fixtures/ansible_ee_searxng"
    name = "gludd_native_config_" + hashlib.sha256(str(tmp_path).encode()).hexdigest()[:12]
    package = tmp_path / name
    (package / "botdetection").mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (package / "compat.py").write_bytes((fixture / "compat.py.txt").read_bytes())
    (package / "botdetection/config.py").write_bytes((fixture / "botdetection-config.py.txt").read_bytes())
    # Preserve a suite's pkg_resources shim; native importlib needs only this path.
    with monkeypatch.context() as context:
        context.setattr(sys, "path", [str(tmp_path), *sys.path])
        native = import_module(name + ".botdetection.config")
    assert isinstance(native.__file__, str)
    assert Path(native.__file__).resolve() == (package / "botdetection/config.py").resolve()
    schema = fixture / "limiter.toml.txt"
    configured = native.Config.from_toml(schema, artifacts.INPUTS["smoke_limiter"], {})
    assert configured.cfg == tomllib.loads(schema.read_text())
    assert not [record for record in caplog.records if record.levelno >= logging.WARNING]


@pytest.mark.parametrize("change", ["missing_settings", "missing_limiter", "malformed_limiter", "symlink_settings"])
def test_native_smoke_refuses_unusable_configuration_before_imports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str,
) -> None:
    settings = tmp_path / "settings.yml"
    settings.write_bytes(artifacts.INPUTS["smoke_settings"].read_bytes())
    limiter = tmp_path / "limiter.toml"
    limiter.write_bytes(artifacts.INPUTS["smoke_limiter"].read_bytes())
    if change == "missing_settings":
        settings.unlink()
    elif change == "missing_limiter":
        limiter.unlink()
    elif change == "malformed_limiter":
        limiter.write_text("[broken\n")
    else:
        target = tmp_path / "actual-settings.yml"
        settings.rename(target)
        settings.symlink_to(target)
    monkeypatch.setattr(artifacts, "SMOKE_SETTINGS_PATH", settings)
    monkeypatch.setattr(os, "environ", dict(os.environ))
    prelude, _final = _smoke_sections()
    original = tuple(logging.getLogger().handlers)
    try:
        with pytest.raises((SystemExit, tomllib.TOMLDecodeError)):
            exec(compile(prelude, "<native-config-refusal>", "exec"), {})
    finally:
        logging.getLogger().handlers[:] = original


@pytest.mark.parametrize(
    ("input_name", "contents", "message"),
    [
        ("smoke_settings", "use_default_settings: true\n", "must use only the native Wikipedia engine"),
        ("smoke_limiter", "[invalid\n", "invalid TOML"),
        ("smoke_limiter", "[botdetection]\nipv4_prefix = 16\n", "must inherit pinned native defaults"),
    ],
)
def test_configuration_drift_fails_existing_admission_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, input_name: str, contents: str, message: str,
) -> None:
    changed = tmp_path / "changed-config"
    changed.write_text(contents)
    monkeypatch.setattr(artifacts, "INPUTS", {**artifacts.INPUTS, input_name: changed})
    assert any(message in error for error in artifacts.validate_files())
