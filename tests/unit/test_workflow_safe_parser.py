"""Fresh, safe workflow parsing with the installed LibYAML backend."""

from __future__ import annotations

import importlib.util
import runpy
import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from types import ModuleType
from typing import NamedTuple, cast
from unittest.mock import patch

import pytest
import yaml


class WorkflowReader(NamedTuple):
    module: ModuleType
    helper: str
    path_attribute: str
    normalize_on: bool

    def load(self) -> object:
        return cast(Callable[[], object], getattr(self.module, self.helper))()

    @property
    def path(self) -> Path:
        return cast(Path, getattr(self.module, self.path_attribute))


AvailableCSafeLoader = type("AvailableCSafeLoader", (yaml.SafeLoader,), {})


def test_installed_safe_yaml_backend_is_retained_on_supported_python() -> None:
    """Only the affected 3.14 interpreter needs the Ansible C-backend workaround."""
    affected_interpreter = sys.version_info[:2] == (3, 14)
    expected_available = yaml.__with_libyaml__ and not affected_interpreter
    for name in ("CSafeLoader", "CSafeDumper"):
        assert hasattr(yaml, name) is expected_available, name


@pytest.mark.parametrize("minor", (11, 12, 14, 15))
def test_unit_yaml_workaround_routes_only_affected_interpreter(
    minor: int, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Execute the real unit bootstrap, including its collection-path ownership."""
    root = Path(__file__).resolve().parents[1]
    collections_path = str(root.parent / "collections")
    monkeypatch.setattr(sys, "version_info", (3, minor, 0, "final", 0))
    monkeypatch.setattr(sys, "path", [entry for entry in sys.path if entry != collections_path])
    backends = {name: object() for name in ("CSafeLoader", "CSafeDumper", "CParser")}
    for name, backend in backends.items():
        monkeypatch.setitem(yaml.__dict__, name, backend)

    namespace = runpy.run_path(str(root / "unit/conftest.py"))

    for name, backend in backends.items():
        if minor == 14:
            assert not hasattr(yaml, name)
        else:
            assert getattr(yaml, name) is backend
    assert sys.path.count(collections_path) == 1
    assert sys.path[0] == collections_path
    assert not {"_yaml_mod", "_name", "_collections"}.intersection(namespace)


@pytest.fixture(scope="module")
def workflow_modules(pytestconfig: pytest.Config) -> Iterator[dict[str, ModuleType]]:
    root = pytestconfig.rootpath
    if not (root / "tests/unit/test_ci_workflow_integrity.py").is_file():
        root = Path.cwd()
    modules: dict[str, ModuleType] = {}
    try:
        for label, relative in (
            ("unit", "tests/unit/test_ci_workflow_integrity.py"),
            ("security", "tests/security/test_ci_workflow.py"),
        ):
            spec = importlib.util.spec_from_file_location(
                f"_gludd_workflow_parser_{label}", root / relative
            )
            assert spec is not None and spec.loader is not None
            module = importlib.util.module_from_spec(spec)
            modules[label] = module
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)
        yield modules
    finally:
        for module in modules.values():
            sys.modules.pop(module.__name__, None)


@pytest.fixture(
    params=(
        ("unit", "_load_workflow", "WORKFLOW_PATH", False),
        ("security", "_load_workflow", "WORKFLOW_PATH", True),
        ("security", "_load_pages_workflow", "PAGES_WORKFLOW_PATH", True),
    ),
    ids=("unit-build", "security-build", "security-pages"),
)
def reader(
    request: pytest.FixtureRequest, workflow_modules: dict[str, ModuleType]
) -> WorkflowReader:
    label, helper, path_attribute, normalize_on = cast(
        tuple[str, str, str, bool], request.param
    )
    return WorkflowReader(
        workflow_modules[label], helper, path_attribute, normalize_on
    )


@pytest.fixture(params=("available", "absent"), ids=("c-safe", "python-safe"))
def safe_backend(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> type[object]:
    if request.param == "absent":
        monkeypatch.delattr(yaml, "CSafeLoader", raising=False)
        return cast(type[object], yaml.SafeLoader)
    backend = getattr(yaml, "CSafeLoader", AvailableCSafeLoader)
    monkeypatch.setattr(yaml, "CSafeLoader", backend, raising=False)
    return cast(type[object], backend)


def _expected(reader: WorkflowReader, source: str) -> object:
    parsed: object = yaml.safe_load(source)
    if (
        reader.normalize_on
        and isinstance(parsed, dict)
        and True in parsed
        and "on" not in parsed
    ):
        parsed["on"] = parsed.pop(True)
    return parsed


def test_each_workflow_reader_uses_selected_safe_backend(
    reader: WorkflowReader, safe_backend: type[object]
) -> None:
    with patch.object(yaml, "load", wraps=yaml.load) as parse:
        reader.load()
    assert parse.call_count == 1
    call = parse.call_args
    assert call is not None
    selected = call.kwargs.get("Loader")
    if selected is None and len(call.args) > 1:
        selected = call.args[1]
    assert selected is safe_backend


def test_complete_real_workflow_matches_safe_load(
    reader: WorkflowReader, safe_backend: type[object]
) -> None:
    assert reader.load() == _expected(reader, reader.path.read_text())


def test_reader_reparses_source_and_isolates_nested_results(
    reader: WorkflowReader,
    safe_backend: type[object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / reader.path.name
    source = "name: first\non: [push]\njobs:\n  verify:\n    steps: []\n"
    path.write_text(source)
    monkeypatch.setattr(reader.module, reader.path_attribute, path)
    first = reader.load()
    assert isinstance(first, dict) and isinstance(first["jobs"], dict)
    first["jobs"]["injected"] = {"steps": ["mutated"]}
    second = reader.load()
    assert second == _expected(reader, source)
    assert isinstance(second, dict) and isinstance(second["jobs"], dict)
    assert second is not first
    assert second["jobs"] is not first["jobs"]
    changed = source.replace("first", "changed").replace("[push]", "[pull_request]")
    path.write_text(changed)
    assert reader.load() == _expected(reader, changed)


def test_existing_on_key_normalization_is_unchanged(
    reader: WorkflowReader,
    safe_backend: type[object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / reader.path.name
    source = '"on": [pull_request]\non: [push]\njobs: {}\n'
    path.write_text(source)
    monkeypatch.setattr(reader.module, reader.path_attribute, path)
    assert reader.load() == _expected(reader, source)


def test_unsafe_python_tag_is_refused(
    reader: WorkflowReader,
    safe_backend: type[object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / reader.path.name
    path.write_text("!!python/object/apply:builtins.str ['unsafe']\n")
    monkeypatch.setattr(reader.module, reader.path_attribute, path)
    with pytest.raises(yaml.constructor.ConstructorError):
        reader.load()


def test_malformed_yaml_still_raises_yaml_error(
    reader: WorkflowReader,
    safe_backend: type[object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / reader.path.name
    path.write_text("jobs: [\n")
    monkeypatch.setattr(reader.module, reader.path_attribute, path)
    with pytest.raises(yaml.YAMLError):
        reader.load()


def test_existing_mapping_validation_is_unchanged(
    reader: WorkflowReader,
    safe_backend: type[object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / reader.path.name
    source = "[one, two]\n"
    path.write_text(source)
    monkeypatch.setattr(reader.module, reader.path_attribute, path)
    if reader.normalize_on:
        with pytest.raises(AssertionError, match=f"{path.name} must be a valid YAML mapping"):
            reader.load()
    else:
        assert reader.load() == yaml.safe_load(source)


def test_missing_workflow_preserves_existing_error(
    reader: WorkflowReader,
    safe_backend: type[object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / reader.path.name
    monkeypatch.setattr(reader.module, reader.path_attribute, path)
    if reader.normalize_on:
        with pytest.raises(AssertionError, match=f"{path.name} not found"):
            reader.load()
    else:
        with pytest.raises(FileNotFoundError):
            reader.load()
