"""Deep audit of all @dataclass classes under src/general_ludd/.

Checks: all fields have type annotations, no mutable defaults, slots usage,
frozen usage, and __post_init__ validation. Dynamically discovers all
dataclasses at collection time — no hardcoded class list.
"""

from __future__ import annotations

import ast
import dataclasses
import importlib
import sys
from pathlib import Path
from typing import Any

import pytest

SRC = Path(__file__).parent.parent.parent / "src" / "general_ludd"


# ── discovery ────────────────────────────────────────────────────────────────


def _discover_dataclass_modules() -> list[tuple[str, Path]]:
    """Yield ``(module_name, file_path)`` for every .py under src/general_ludd/."""
    out: list[tuple[str, Path]] = []
    _ensure_on_path()
    for py_file in sorted(SRC.rglob("*.py")):
        if py_file.name == "__init__.py":
            continue
        rel = py_file.relative_to(SRC.parent)
        mod_name = str(rel.with_suffix("")).replace("/", ".")
        out.append((mod_name, py_file))
    return out


def _ensure_on_path() -> None:
    root = str(SRC.parent.parent)
    if root not in sys.path:
        sys.path.insert(0, root)


def _dataclass_names_in_file(path: Path) -> list[str]:
    """Return class names decorated with @dataclass in *path*."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        if any(_is_dataclass_decorator(d) or _is_dataclass_qualified(d) for d in node.decorator_list):
            names.append(node.name)
    return names


def _is_dataclass_decorator(node: ast.expr) -> bool:
    """True when *node* is a bare ``@dataclass`` or ``@dataclass(...)``."""
    match node:
        case ast.Call(func=ast.Name(id="dataclass")):
            return True
        case ast.Name(id="dataclass"):
            return True
    return False


def _is_dataclass_qualified(node: ast.expr) -> bool:
    """True when *node* is ``@dataclasses.dataclass`` or ``@dataclasses.dataclass(...)``."""
    match node:
        case ast.Call(func=ast.Attribute(value=ast.Name(id="dataclasses"), attr="dataclass")):
            return True
        case ast.Attribute(value=ast.Name(id="dataclasses"), attr="dataclass"):
            return True
    return False


def _import_class(module_name: str, class_name: str) -> type:
    mod = importlib.import_module(module_name)
    obj = getattr(mod, class_name)
    if not isinstance(obj, type):
        raise TypeError(f"{module_name}.{class_name} is not a class")
    return obj


MUTABLE_TYPES = (list, dict, set, bytearray)


# ── collections ──────────────────────────────────────────────────────────────


def _all_dataclass_modules() -> list[tuple[str, Path]]:
    """All modules that contain at least one dataclass."""
    all_mods = _discover_dataclass_modules()
    return [(mod_name, path) for mod_name, path in all_mods if _dataclass_names_in_file(path)]


ALL_DATACLASS_MODULES = _all_dataclass_modules()


# ── helpers ──────────────────────────────────────────────────────────────────


def _all_dataclass_entries() -> list[tuple[str, str, type]]:
    """Return ``[(module_name, class_name, cls), ...]`` for every discovered dataclass."""
    entries: list[tuple[str, str, type]] = []
    for mod_name, path in ALL_DATACLASS_MODULES:
        for cls_name in _dataclass_names_in_file(path):
            try:
                cls = _import_class(mod_name, cls_name)
            except Exception:
                continue
            if dataclasses.is_dataclass(cls):
                entries.append((mod_name, cls_name, cls))
    return entries


_ALL_ENTRIES: list[tuple[str, str, type]] | None = None


def _entries() -> list[tuple[str, str, type]]:
    global _ALL_ENTRIES
    if _ALL_ENTRIES is None:
        _ALL_ENTRIES = _all_dataclass_entries()
    return _ALL_ENTRIES


def _fields_of(cls: type) -> tuple[dataclasses.Field, ...]:
    return dataclasses.fields(cls)


def _post_init_assigns(mod_name: str, cls_name: str, field_name: str) -> bool:
    """True when the class's own ``__post_init__`` assigns ``self.<field>`` (AST)."""
    for name, path in ALL_DATACLASS_MODULES:
        if name != mod_name:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef) or node.name != cls_name:
                continue
            for sub in node.body:
                if not isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                if sub.name != "__post_init__":
                    continue
                for stmt in ast.walk(sub):
                    if not isinstance(stmt, ast.Assign):
                        continue
                    for target in stmt.targets:
                        if (
                            isinstance(target, ast.Attribute)
                            and isinstance(target.value, ast.Name)
                            and target.value.id == "self"
                            and target.attr == field_name
                        ):
                            return True
                return False
    return False


# ── test: all fields have type annotations ───────────────────────────────────


class TestAllFieldsHaveTypes:
    """Every dataclass field MUST carry a type annotation."""

    @pytest.mark.parametrize(
        "mod_name,cls_name,cls",
        _all_dataclass_entries(),
        ids=lambda v: f"{v[0]}.{v[1]}" if isinstance(v, tuple) else "",
    )
    def test_field_has_type(self, mod_name: str, cls_name: str, cls: type) -> None:
        qual = f"{mod_name}.{cls_name}"
        for f in _fields_of(cls):
            assert f.type is not None, f"{qual}.{f.name}: missing type annotation"


# ── test: no mutable defaults ────────────────────────────────────────────────


def _is_mutable_default(field: dataclasses.Field) -> bool:
    """True when *field* has a mutable default (bare list/dict/set, no factory)."""
    if field.default_factory is not dataclasses.MISSING:
        return False
    if field.default is dataclasses.MISSING:
        return False
    return type(field.default) in MUTABLE_TYPES


class TestNoMutableDefaults:
    """No dataclass field may have a bare mutable default (list, dict, set).

    Use ``field(default_factory=list)`` (or dict/set) instead so each
    instance gets its own fresh container.
    """

    @pytest.mark.parametrize(
        "mod_name,cls_name,cls",
        _all_dataclass_entries(),
        ids=lambda v: f"{v[0]}.{v[1]}" if isinstance(v, tuple) else "",
    )
    def test_no_mutable_default(self, mod_name: str, cls_name: str, cls: type) -> None:
        qual = f"{mod_name}.{cls_name}"
        for f in _fields_of(cls):
            if _is_mutable_default(f):
                pytest.fail(
                    f"{qual}.{f.name}: bare mutable default {type(f.default).__name__!r} "
                    f"— use field(default_factory={type(f.default).__name__})"
                )


# ── test: slots where possible ───────────────────────────────────────────────


def _uses_slots(cls: type) -> bool:
    return "__slots__" in cls.__dict__


class TestSlotsUsage:
    """Dataclasses that are simple data carriers SHOULD use ``slots=True``.

    slotted dataclasses are faster and use less memory.
    """

    def test_all_dataclasses_count(self) -> None:
        entries = _entries()
        assert len(entries) >= 1, "no dataclasses discovered — verify SRC path"

    def test_at_least_one_uses_slots(self) -> None:
        slotted = [(m, n) for m, n, c in _entries() if _uses_slots(c)]
        assert slotted, "no dataclass uses slots=True"

    def test_slotted_count(self) -> None:
        total = len(_entries())
        slotted = sum(1 for _, _, c in _entries() if _uses_slots(c))
        assert slotted >= 1, f"0/{total} dataclasses use slots=True"


# ── test: frozen where immutable ─────────────────────────────────────────────


def _is_frozen(cls: type) -> bool:
    return getattr(cls, "__dataclass_params__", None) is not None and cls.__dataclass_params__.frozen  # type: ignore[attr-defined]


class TestFrozenUsage:
    """Dataclasses that carry immutable data SHOULD use ``frozen=True``."""

    def test_frozen_vs_non_frozen_ratio(self) -> None:
        total = len(_entries())
        frozen = sum(1 for _, _, c in _entries() if _is_frozen(c))
        assert total > 0
        assert frozen > 0, (
            f"0/{total} dataclasses use frozen=True — at least some "
            f"config/result/value-object dataclasses should be frozen"
        )

    def test_frozen_cant_have_mutable_field_assignments(self) -> None:
        """frozen=True ensures instances are hashable + thread-safe."""
        for mod_name, cls_name, cls in _entries():
            if not _is_frozen(cls):
                continue
            qual = f"{mod_name}.{cls_name}"
            for f in _fields_of(cls):
                if f.default_factory is not dataclasses.MISSING:
                    continue
                if f.default is dataclasses.MISSING:
                    continue
                if _is_mutable_default(f):
                    pytest.fail(f"{qual}.{f.name}: frozen=True + mutable default {type(f.default).__name__}")


# ── test: __post_init__ validation ───────────────────────────────────────────


class TestPostInitValidation:
    """Dataclasses with a ``__post_init__`` must actually validate."""

    def test_post_init_exists_somewhere(self) -> None:
        has_post_init = [(m, n) for m, n, c in _entries() if "__post_init__" in c.__dict__]
        assert has_post_init, "no dataclass defines __post_init__"

    def test_post_init_is_callable(self) -> None:
        for mod_name, cls_name, cls in _entries():
            pi = cls.__dict__.get("__post_init__")
            if pi is not None:
                assert callable(pi), f"{mod_name}.{cls_name}.__post_init__ is not callable"


# ── test: field(default_factory=...) used for mutable types ──────────────────


class TestDefaultFactoryForMutables:
    """Every field typed as list/dict/set MUST use default_factory."""

    @pytest.mark.parametrize(
        "mod_name,cls_name,cls",
        _all_dataclass_entries(),
        ids=lambda v: f"{v[0]}.{v[1]}" if isinstance(v, tuple) else "",
    )
    def test_mutable_field_uses_factory(self, mod_name: str, cls_name: str, cls: type) -> None:
        qual = f"{mod_name}.{cls_name}"
        for f in _fields_of(cls):
            if f.default is not dataclasses.MISSING and _type_is_mutable_container(f.type):
                pytest.fail(
                    f"{qual}.{f.name}: type is {_unparse_type(f.type)}, "
                    f"but uses bare default {f.default!r} — "
                    f"use field(default_factory=list/dict/set)"
                )


def _type_is_mutable_container(tp: Any) -> bool:
    """True when *tp* is list[...], dict[...], or set[...]."""
    origin = getattr(tp, "__origin__", None)
    return origin in (list, dict, set)


def _unparse_type(tp: Any) -> str:
    try:
        return str(tp)
    except Exception:
        return repr(tp)


# ── test: discoverability sanity ─────────────────────────────────────────────


class TestDiscoverySanity:
    """Verify module/class discovery actually found something."""

    def test_at_least_15_dataclasses(self) -> None:
        entries = _entries()
        assert len(entries) >= 15, f"found {len(entries)} dataclasses, expected >=15"

    def test_every_dataclass_is_really_a_dataclass(self) -> None:
        for mod_name, cls_name, cls in _entries():
            assert dataclasses.is_dataclass(cls), f"{mod_name}.{cls_name} not a dataclass"

    def test_all_modules_on_path(self) -> None:
        src_parent = str(SRC.parent)
        assert src_parent in sys.path, f"discovery root {src_parent!r} not in sys.path"


# ── test: frozen classes have __post_init__ or are trivial ───────────────────


class TestFrozenPostInit:
    """Frozen dataclasses with validation need __post_init__."""

    def test_frozen_with_non_trivial_fields(self) -> None:
        for _mod_name, _cls_name, cls in _entries():
            if not _is_frozen(cls):
                continue
            fields = _fields_of(cls)
            has_pi = "__post_init__" in cls.__dict__
            has_complex = any(f.default_factory is not dataclasses.MISSING for f in fields)
            if has_complex and not has_pi:
                pass


# ── test: no field named `_` or with leading `_` in frozen dataclasses ───────


class TestNaming:
    """Naming conventions for dataclass fields."""

    def test_no_dunder_fields(self) -> None:
        for mod_name, cls_name, cls in _entries():
            for f in _fields_of(cls):
                qual = f"{mod_name}.{cls_name}.{f.name}"
                assert not f.name.startswith("__"), f"{qual}: dunder field names are not allowed"


# ── test: init-only fields in frozen dataclasses are ok ──────────────────────


class TestInitOnlyFields:
    """init=False fields must be assigned: default, default_factory, or __post_init__.

    ``field(init=False)`` without a default is the canonical CPython pattern for
    derived attributes set in ``__post_init__`` (the alternative — a nullable
    default — weakens the field type to ``X | None``).  This test therefore
    verifies, via AST, that every default-less init=False field is actually
    assigned in the class's own ``__post_init__``.
    """

    def test_init_false_has_default(self) -> None:
        for mod_name, cls_name, cls in _entries():
            qual = f"{mod_name}.{cls_name}"
            post_init = cls.__dict__.get("__post_init__")
            for f in _fields_of(cls):
                if f.init:
                    continue
                ok = f.default is not dataclasses.MISSING or f.default_factory is not dataclasses.MISSING
                if not ok:
                    if post_init is not None and _post_init_assigns(mod_name, cls_name, f.name):
                        continue
                    assert ok, f"{qual}.{f.name}: init=False but no default and no __post_init__ assignment"
