"""Detect test parameters that shadow fixtures supplied by pytest plugins."""

from __future__ import annotations

import ast
import importlib.util
from collections.abc import Iterable
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path


@dataclass(frozen=True, order=True)
class ParametrizedFixtureCollision:
    """One statically detectable parameter/fixture name collision."""

    path: Path
    line: int
    name: str


@dataclass(frozen=True)
class _PytestAliases:
    modules: frozenset[str]
    fixture_decorators: frozenset[str]
    marks: frozenset[str]


def _pytest_aliases(tree: ast.AST) -> _PytestAliases:
    modules = {"pytest"}
    fixture_decorators: set[str] = set()
    marks: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for imported in node.names:
                if imported.name == "pytest":
                    modules.add(imported.asname or imported.name)
        elif isinstance(node, ast.ImportFrom) and node.module == "pytest":
            for imported in node.names:
                public_name = imported.asname or imported.name
                if imported.name == "fixture":
                    fixture_decorators.add(public_name)
                elif imported.name == "mark":
                    marks.add(public_name)

    return _PytestAliases(
        modules=frozenset(modules),
        fixture_decorators=frozenset(fixture_decorators),
        marks=frozenset(marks),
    )


def _fixture_name(
    decorator: ast.expr,
    *,
    function_name: str,
    aliases: _PytestAliases,
) -> str | None:
    call = decorator if isinstance(decorator, ast.Call) else None
    target = call.func if call is not None else decorator

    is_fixture = isinstance(target, ast.Name) and target.id in aliases.fixture_decorators
    if isinstance(target, ast.Attribute):
        is_fixture = (
            target.attr == "fixture"
            and isinstance(target.value, ast.Name)
            and target.value.id in aliases.modules
        )
    if not is_fixture:
        return None

    if call is not None:
        for keyword in call.keywords:
            if (
                keyword.arg == "name"
                and isinstance(keyword.value, ast.Constant)
                and isinstance(keyword.value.value, str)
            ):
                return keyword.value.value
    return function_name


def fixture_names_from_source(source: str, *, filename: str = "<plugin>") -> set[str]:
    """Extract public fixture names from one pytest plugin source module."""

    tree = ast.parse(source, filename=filename)
    aliases = _pytest_aliases(tree)
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            name = _fixture_name(decorator, function_name=node.name, aliases=aliases)
            if name is not None:
                names.add(name)
    return names


def pytest_plugin_modules_from_source(source: str, *, filename: str) -> set[str]:
    """Extract literal modules delegated through ``pytest_plugins``."""

    tree = ast.parse(source, filename=filename)
    modules: set[str] = set()

    def add_literals(value: ast.expr) -> None:
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            modules.add(value.value)
        elif isinstance(value, (ast.List, ast.Tuple, ast.Set)):
            for element in value.elts:
                add_literals(element)

    for node in ast.walk(tree):
        value: ast.expr | None = None
        if (isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "pytest_plugins"
            for target in node.targets
        )) or (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == "pytest_plugins"
        ):
            value = node.value
        if value is not None:
            add_literals(value)
    return modules


def _module_source(module_name: str) -> tuple[str, str]:
    spec = importlib.util.find_spec(module_name)
    if spec is None or spec.origin is None:
        raise RuntimeError(f"cannot resolve pytest plugin module {module_name!r}")
    path = Path(spec.origin)
    if path.suffix != ".py":
        raise RuntimeError(f"pytest plugin module is not inspectable Python source: {path}")
    return path.read_text(encoding="utf-8"), str(path)


def _pytest_entry_point_modules() -> set[str]:
    entry_points = metadata.entry_points()
    selected = entry_points.select(group="pytest11")
    return {entry_point.value.partition(":")[0] for entry_point in selected}


def external_pytest_fixture_names() -> set[str]:
    """Discover fixtures from installed pytest entry-point plugins statically."""

    pending = sorted(_pytest_entry_point_modules())
    visited: set[str] = set()
    fixture_names: set[str] = set()
    while pending:
        module_name = pending.pop()
        if module_name in visited:
            continue
        visited.add(module_name)
        source, filename = _module_source(module_name)
        fixture_names.update(fixture_names_from_source(source, filename=filename))
        pending.extend(
            sorted(pytest_plugin_modules_from_source(source, filename=filename) - visited)
        )
    return fixture_names


def _parameter_names(value: ast.expr) -> tuple[str, ...]:
    if isinstance(value, ast.Constant) and isinstance(value.value, str):
        return tuple(name.strip() for name in value.value.split(",") if name.strip())
    if isinstance(value, (ast.List, ast.Tuple)):
        names: list[str] = []
        for element in value.elts:
            if not isinstance(element, ast.Constant) or not isinstance(element.value, str):
                return ()
            names.append(element.value)
        return tuple(names)
    return ()


def _parametrize_names(
    decorator: ast.expr,
    *,
    aliases: _PytestAliases,
) -> tuple[str, ...]:
    if not isinstance(decorator, ast.Call) or not isinstance(decorator.func, ast.Attribute):
        return ()
    if decorator.func.attr != "parametrize":
        return ()

    owner = decorator.func.value
    from_pytest_mark = (
        isinstance(owner, ast.Attribute)
        and owner.attr == "mark"
        and isinstance(owner.value, ast.Name)
        and owner.value.id in aliases.modules
    ) or (isinstance(owner, ast.Name) and owner.id in aliases.marks)
    if not from_pytest_mark:
        return ()

    if decorator.args:
        return _parameter_names(decorator.args[0])
    for keyword in decorator.keywords:
        if keyword.arg == "argnames":
            return _parameter_names(keyword.value)
    return ()


def find_parametrized_fixture_collisions(
    paths: Iterable[Path],
    *,
    external_fixture_names: set[str],
) -> list[ParametrizedFixtureCollision]:
    """Return static collisions in deterministic path/line/name order."""

    collisions: list[ParametrizedFixtureCollision] = []
    for path in sorted(set(paths)):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        aliases = _pytest_aliases(tree)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in node.decorator_list:
                for name in _parametrize_names(decorator, aliases=aliases):
                    if name in external_fixture_names:
                        collisions.append(
                            ParametrizedFixtureCollision(
                                path=path,
                                line=decorator.lineno,
                                name=name,
                            )
                        )
    return sorted(collisions)
