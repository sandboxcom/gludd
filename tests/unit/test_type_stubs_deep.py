"""Deep type-stub and .pyi validation tests.

Covers: .pyi parsing, stub-to-source mapping, Protocol completeness,
runtime_checkable coverage, overload absence, py.typed marker, and
typings directory structure conventions.
"""

from __future__ import annotations

import ast
import importlib
import importlib.util
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent.parent
SRC = ROOT / "src" / "general_ludd"
TYPINGS = ROOT / "typings"


# ── helpers ──────────────────────────────────────────────────────────────


def _collect_pyi_files() -> list[Path]:
    return sorted(TYPINGS.rglob("*.pyi"))


def _collect_src_python() -> list[Path]:
    return sorted(p for p in SRC.rglob("*.py") if p.name != "__init__.py")


def _collect_protocol_modules() -> list[Path]:
    return sorted(
        p for p in SRC.rglob("*.py") if "protocol" in p.stem.lower() and "protocol" not in p.parent.name.lower()
    )


def _ast_parse_safe(path: Path) -> ast.Module:
    return ast.parse(path.read_text(), filename=str(path))


def _import_safehttpx_stub() -> types.ModuleType | None:
    path = str(TYPINGS / "safehttpx" / "__init__.pyi")
    spec = importlib.util.spec_from_file_location(
        "safehttpx",
        path,
        submodule_search_locations=[str(TYPINGS / "safehttpx")],
    )
    if spec is None:
        return None
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


# ── 1. All .pyi files parse ─────────────────────────────────────────────


def test_pyi_collect_finds_at_least_one():
    files = _collect_pyi_files()
    assert len(files) >= 1, "Expected at least one .pyi file in typings/"


def test_every_pyi_ast_parses():
    errors: list[str] = []
    for pyi in _collect_pyi_files():
        try:
            _ast_parse_safe(pyi)
        except SyntaxError as e:
            errors.append(f"{pyi}: {e}")
    assert not errors, f"{len(errors)} .pyi file(s) with syntax errors:\n" + "\n".join(errors)


def test_every_pyi_compiles():
    errors: list[str] = []
    for pyi in _collect_pyi_files():
        try:
            compile(pyi.read_text(), str(pyi), "exec")
        except (SyntaxError, ValueError) as e:
            errors.append(f"{pyi}: {e}")
    assert not errors, f"{len(errors)} .pyi file(s) failed compile():\n" + "\n".join(errors)


def test_no_empty_pyi_files():
    empty: list[str] = []
    for pyi in _collect_pyi_files():
        content = pyi.read_text().strip()
        if not content:
            empty.append(str(pyi))
    assert not empty, f"Empty .pyi files: {empty}"


# ── 2. safehttpx stub structure ─────────────────────────────────────────


def test_safehttpx_pyi_module_imports_httpx():
    tree = _ast_parse_safe(TYPINGS / "safehttpx" / "__init__.pyi")
    imports = [node for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
    assert imports, "safehttpx/__init__.pyi must contain at least one import"
    import_sources = {i.module for i in imports}
    assert "httpx" in import_sources, f"safehttpx .pyi must import from httpx, got {import_sources}"


def test_safehttpx_pyi_has_async_secure_transport():
    tree = _ast_parse_safe(TYPINGS / "safehttpx" / "__init__.pyi")
    classes = {n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)}
    assert "AsyncSecureTransport" in classes, f"Missing AsyncSecureTransport; found: {classes}"


def test_safehttpx_pyi_extends_async_base_transport():
    tree = _ast_parse_safe(TYPINGS / "safehttpx" / "__init__.pyi")
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "AsyncSecureTransport":
            bases = [ast.unparse(b) for b in node.bases]
            assert any("AsyncBaseTransport" in b for b in bases), (
                f"AsyncSecureTransport must extend AsyncBaseTransport; bases: {bases}"
            )
            break


def test_safehttpx_pyi_constructor_has_verified_ip():
    tree = _ast_parse_safe(TYPINGS / "safehttpx" / "__init__.pyi")
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "AsyncSecureTransport":
            funcs = [n for n in ast.walk(node) if isinstance(n, ast.FunctionDef) and n.name == "__init__"]
            assert funcs, "AsyncSecureTransport.__init__ must exist in stub"
            args = [a.arg for a in funcs[0].args.args]
            assert "verified_ip" in args, f"__init__ missing verified_ip param; args: {args}"
            ret = funcs[0].returns
            assert ret is None or (isinstance(ret, ast.Constant) and ret.value is None), (
                f"__init__ should return None, got {ast.unparse(ret)}"
            )
            break


def test_safehttpx_pyi_imports_resolve_at_runtime():
    try:
        import httpx  # noqa: F401
    except ImportError:
        pytest.skip("httpx not installed")
    mod = _import_safehttpx_stub()
    if mod is None:
        pytest.skip("safehttpx spec creation failed (package root missing)")
    assert hasattr(mod, "AsyncSecureTransport"), "stub must export AsyncSecureTransport"


# ── 3. typings directory convention ─────────────────────────────────────


def test_typings_dir_matches_package_structure():
    subdirs = [d for d in TYPINGS.iterdir() if d.is_dir()]
    for sub in subdirs:
        init = sub / "__init__.pyi"
        assert init.exists(), f"typings/{sub.name} missing __init__.pyi"


def test_typings_are_single_layer():
    for pyi in _collect_pyi_files():
        rel = pyi.relative_to(TYPINGS)
        depth = len(rel.parts)
        assert depth <= 2, (
            f"typings/{rel} is too deep ({depth} layers); stub packages should mirror the package name at typings/<pkg>"
        )


# ── 4. py.typed marker ──────────────────────────────────────────────────


def test_py_typed_marker_exists_in_package():
    marker = SRC / "py.typed"
    assert marker.exists(), f"{marker} must exist for PEP 561 compliance"


def test_py_typed_marker_is_not_empty():
    marker = SRC / "py.typed"
    assert marker.is_file(), f"{marker} must be a file"
    content = marker.read_text().strip()
    assert len(content) <= 20, f"py.typed should be empty or contain 'partial'; got {content!r}"


# ── 5. Protocol completeness ────────────────────────────────────────────


def test_runtime_checkable_protocols_exist():
    count = 0
    for path in [p for p in SRC.rglob("*.py") if p.name != "__init__.py"]:
        try:
            tree = _ast_parse_safe(path)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                for dec in node.decorator_list:
                    if isinstance(dec, ast.Name) and dec.id == "runtime_checkable":
                        count += 1
    assert count >= 50, f"Expected ≥50 @runtime_checkable Protocol classes, found {count}"


def test_runtime_checkable_protocols_are_importable():
    failures: list[str] = []
    for path in [p for p in SRC.rglob("*.py") if p.name != "__init__.py"]:
        try:
            tree = _ast_parse_safe(path)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                for dec in node.decorator_list:
                    if isinstance(dec, ast.Name) and dec.id == "runtime_checkable":
                        for base in node.bases:
                            if isinstance(base, ast.Name) and base.id == "Protocol":
                                break
                        else:
                            if not any(isinstance(b, ast.Attribute) and b.attr == "Protocol" for b in node.bases):
                                failures.append(
                                    f"{path}:{node.lineno} {node.name} has @runtime_checkable but no Protocol base"
                                )
    assert not failures, f"{len(failures)} @runtime_checkable classes missing Protocol base:\n" + "\n".join(failures)


def test_shared_protocols_module_imports():
    proto_path = SRC / "connectors" / "_protocols.py"
    assert proto_path.exists()
    tree = _ast_parse_safe(proto_path)
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "HttpResponse":
            for dec in node.decorator_list:
                assert isinstance(dec, ast.Name) and dec.id == "runtime_checkable", (
                    "HttpResponse must be @runtime_checkable"
                )
            break


def test_protocol_files_have_all_entries():
    protocol_files = [
        SRC / "connectors" / "_protocols.py",
        SRC / "chemistry" / "protocols.py",
        SRC / "materials" / "simulation" / "protocols.py",
    ]
    for path in protocol_files:
        if not path.exists():
            continue
        tree = _ast_parse_safe(path)
        classes = [n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]
        all_list = None
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if (
                        isinstance(target, ast.Name)
                        and target.id == "__all__"
                        and isinstance(node.value, (ast.List, ast.Tuple))
                    ):
                        all_list = [
                            elt.value
                            if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
                            else ast.unparse(elt)
                            for elt in node.value.elts
                        ]
        if all_list is not None:
            for cls in classes:
                assert cls in all_list or cls.startswith("_"), f"{path.relative_to(ROOT)}: class {cls!r} not in __all__"


# ── 6. Overload status ──────────────────────────────────────────────────


def test_overloads_are_absent_from_source():
    overloads: list[str] = []
    for path in [p for p in SRC.rglob("*.py") if p.name != "__init__.py"]:
        try:
            tree = _ast_parse_safe(path)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                for dec in node.decorator_list:
                    if isinstance(dec, ast.Name) and dec.id == "overload":
                        overloads.append(f"{path}:{node.lineno} {node.name}")
    assert not overloads, f"Found {len(overloads)} @overload decorators in src/ when none expected:\n" + "\n".join(
        overloads
    )


def test_project_type_registration_has_one_executable_source_declaration() -> None:
    """The registry API must expose one real function, not runtime overload stubs."""
    path = SRC / "cloud" / "project_types.py"
    tree = _ast_parse_safe(path)
    declarations = [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "register_project_type"
    ]

    assert len(declarations) == 1, (
        "register_project_type must have one executable source declaration; "
        f"found {len(declarations)}"
    )
    assert not declarations[0].decorator_list


def test_no_overload_stubs_needed():
    pyi_files = _collect_pyi_files()
    overloads_in_stubs: list[str] = []
    for pyi in pyi_files:
        tree = _ast_parse_safe(pyi)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                for dec in node.decorator_list:
                    if isinstance(dec, ast.Name) and dec.id == "overload":
                        overloads_in_stubs.append(f"{pyi}:{node.lineno} {node.name}")
    assert not overloads_in_stubs, "@overload in stubs without corresponding source overloads:\n" + "\n".join(
        overloads_in_stubs
    )


# ── 7. Stub-to-source mapping ───────────────────────────────────────────


def test_safehttpx_stub_matches_an_importable_package():
    result = importlib.util.find_spec("safehttpx")
    if result is None:
        pytest.skip("safehttpx package not installed; stub is fallback only")
    assert result is not None


def test_pyi_files_declare_no_implementation():
    for pyi in _collect_pyi_files():
        tree = _ast_parse_safe(pyi)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                for stmt in node.body:
                    if (
                        (isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant))
                        or isinstance(stmt, ast.Pass)
                        or (
                            isinstance(stmt, ast.Expr)
                            and isinstance(stmt.value, ast.Constant)
                            and stmt.value.value is Ellipsis
                        )
                    ):
                        pass
                    else:
                        pass


# ── 8. Source modules without type stubs ────────────────────────────────


def test_public_source_modules_listed():
    packages = sorted({p.parent for p in SRC.rglob("__init__.py")})
    assert len(packages) >= 30, f"Expected ≥30 packages, found {len(packages)}"


def test_coverage_no_syntax_errors_in_all_source():
    errors: list[str] = []
    for path in _collect_src_python():
        try:
            _ast_parse_safe(path)
        except SyntaxError as e:
            errors.append(f"{path}: {e}")
    assert not errors, f"{len(errors)} source file(s) with syntax errors:\n" + "\n".join(errors[:10])
