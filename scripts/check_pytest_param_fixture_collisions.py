"""Detect pytest parameter collisions and deprecated class-fixture binding."""

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


@dataclass(frozen=True, order=True)
class ClassScopedInstanceFixture:
    """A class-scoped fixture that binds to a disposable test instance."""

    path: Path
    line: int
    class_name: str
    name: str


@dataclass(frozen=True)
class PytestSourceAudit:
    """Compact findings from one fresh, bounded repository-source scan."""

    external_fixture_names: frozenset[str]
    collisions: tuple[ParametrizedFixtureCollision, ...]
    bindings: tuple[ClassScopedInstanceFixture, ...]


@dataclass(frozen=True)
class _SourceDeclarations:
    imports: tuple[ast.Import | ast.ImportFrom, ...]
    functions: tuple[
        tuple[
            ast.FunctionDef | ast.AsyncFunctionDef,
            ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef,
        ],
        ...,
    ]
    plugin_values: tuple[ast.expr, ...]


def _source_declarations(tree: ast.Module) -> _SourceDeclarations:
    imports: list[ast.Import | ast.ImportFrom] = []
    functions: list[
        tuple[
            ast.FunctionDef | ast.AsyncFunctionDef,
            ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef,
        ]
    ] = []
    plugin_values: list[ast.expr] = []
    pending: list[
        tuple[ast.AST, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef]
    ] = [(tree, tree)]
    while pending:
        node, owner = pending.pop()
        # Python expressions cannot contain import or function/class declarations.
        # Keep decorator/value nodes on their declarations, without walking large
        # parameter tables or retaining a whole-tree child-to-parent dictionary.
        if isinstance(node, ast.expr):
            continue
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            imports.append(node)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            functions.append((node, owner))
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if node.value is not None and any(
                isinstance(target, ast.Name) and target.id == "pytest_plugins" for target in targets
            ):
                plugin_values.append(node.value)
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            owner = node
        pending.extend((child, owner) for child in ast.iter_child_nodes(node))
    return _SourceDeclarations(tuple(imports), tuple(functions), tuple(plugin_values))


@dataclass(frozen=True)
class _PytestAliases:
    modules: frozenset[str]
    fixture_decorators: frozenset[str]
    marks: frozenset[str]


def _pytest_aliases(
    imports: Iterable[ast.Import | ast.ImportFrom], *, include_asyncio: bool = False,
) -> _PytestAliases:
    providers = {"pytest", "pytest_asyncio"} if include_asyncio else {"pytest"}
    modules = set(providers)
    fixture_decorators: set[str] = set()
    marks: set[str] = set()

    for node in imports:
        if isinstance(node, ast.Import):
            for imported in node.names:
                if imported.name in providers:
                    modules.add(imported.asname or imported.name)
        elif isinstance(node, ast.ImportFrom) and node.module in providers:
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
    return _declared_fixture_names(_source_declarations(ast.parse(source, filename=filename)))


def _declared_fixture_names(declarations: _SourceDeclarations) -> set[str]:
    aliases = _pytest_aliases(declarations.imports)
    names: set[str] = set()
    for node, _owner in declarations.functions:
        for decorator in node.decorator_list:
            name = _fixture_name(decorator, function_name=node.name, aliases=aliases)
            if name is not None:
                names.add(name)
    return names


def pytest_plugin_modules_from_source(source: str, *, filename: str) -> set[str]:
    """Extract literal modules delegated through ``pytest_plugins``."""
    return _declared_plugin_modules(_source_declarations(ast.parse(source, filename=filename)))


def _declared_plugin_modules(declarations: _SourceDeclarations) -> set[str]:
    modules: set[str] = set()

    def add_literals(value: ast.expr) -> None:
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            modules.add(value.value)
        elif isinstance(value, (ast.List, ast.Tuple, ast.Set)):
            for element in value.elts:
                add_literals(element)

    for value in declarations.plugin_values:
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
        declarations = _source_declarations(ast.parse(source, filename=filename))
        fixture_names.update(_declared_fixture_names(declarations))
        pending.extend(
            sorted(_declared_plugin_modules(declarations) - visited)
        )
        del declarations
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
    return list(audit_pytest_sources(paths, external_fixture_names=external_fixture_names).collisions)


def _source_findings(
    path: Path, external_fixture_names: set[str],
) -> tuple[list[ParametrizedFixtureCollision], list[ClassScopedInstanceFixture]]:
    declarations = _source_declarations(
        ast.parse(path.read_text(encoding="utf-8"), filename=str(path)),
    )
    aliases = _pytest_aliases(declarations.imports)
    binding_aliases = _pytest_aliases(declarations.imports, include_asyncio=True)
    collisions: list[ParametrizedFixtureCollision] = []
    bindings: list[ClassScopedInstanceFixture] = []
    for node, owner in declarations.functions:
        for decorator in node.decorator_list:
            for name in _parametrize_names(decorator, aliases=aliases):
                if name in external_fixture_names:
                    collisions.append(
                        ParametrizedFixtureCollision(path=path, line=decorator.lineno, name=name)
                    )
        if not isinstance(owner, ast.ClassDef):
            continue
        if any(
            isinstance(decorator, ast.Name) and decorator.id in {"staticmethod", "classmethod"}
            for decorator in node.decorator_list
        ):
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call):
                continue
            fixture_name = _fixture_name(decorator, function_name=node.name, aliases=binding_aliases)
            if fixture_name is None:
                continue
            if any(
                keyword.arg == "scope"
                and isinstance(keyword.value, ast.Constant)
                and keyword.value.value == "class"
                for keyword in decorator.keywords
            ):
                bindings.append(
                    ClassScopedInstanceFixture(
                        path=path, line=decorator.lineno, class_name=owner.name, name=fixture_name,
                    )
                )
    return collisions, bindings


def audit_pytest_sources(
    paths: Iterable[Path], *, external_fixture_names: set[str],
) -> PytestSourceAudit:
    """Parse each unique path once, releasing its AST before the next source."""
    collisions: list[ParametrizedFixtureCollision] = []
    bindings: list[ClassScopedInstanceFixture] = []
    for path in sorted(set(paths)):
        source_collisions, source_bindings = _source_findings(path, external_fixture_names)
        collisions.extend(source_collisions)
        bindings.extend(source_bindings)
    return PytestSourceAudit(
        external_fixture_names=frozenset(external_fixture_names),
        collisions=tuple(sorted(collisions)),
        bindings=tuple(sorted(bindings)),
    )


def find_class_scoped_instance_fixtures(
    paths: Iterable[Path],
) -> list[ClassScopedInstanceFixture]:
    """Report unsupported literal class-scope bindings without executing fixtures."""
    return list(audit_pytest_sources(paths, external_fixture_names=set()).bindings)
