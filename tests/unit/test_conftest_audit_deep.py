"""Deep conftest and fixture audit — structural integrity of all pytest configuration files."""

from __future__ import annotations

import ast
import os
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
EXTERNAL_PREFIX = str(REPO_ROOT / "external")
VENV_PREFIX = str(REPO_ROOT / ".venv")

SKIP_PREFIXES = (EXTERNAL_PREFIX, VENV_PREFIX)


def _discover_conftest_files() -> list[Path]:
    paths: list[Path] = []
    for dirpath, _, filenames in os.walk(REPO_ROOT):
        abspath = str(Path(dirpath).resolve())
        if any(abspath.startswith(p) for p in SKIP_PREFIXES):
            continue
        if "conftest.py" in filenames:
            p = Path(dirpath) / "conftest.py"
            paths.append(p)
    return sorted(paths)


def _is_external(path: Path) -> bool:
    return str(path).startswith(EXTERNAL_PREFIX)


def _is_project(path: Path) -> bool:
    return not _is_external(path) and not str(path).startswith(VENV_PREFIX)


class _FixtureInfo:
    __slots__ = ("docstring", "fixture_kwargs", "has_yield", "lineno", "params")

    def __init__(
        self,
        docstring: str,
        params: list[str],
        fixture_kwargs: dict[str, Any],
        has_yield: bool,
        lineno: int,
    ) -> None:
        self.docstring = docstring
        self.params = params
        self.fixture_kwargs = fixture_kwargs
        self.has_yield = has_yield
        self.lineno = lineno


class _ConftestData:
    __slots__ = ("fixtures", "module_docstring", "other_functions", "source")

    def __init__(
        self,
        module_docstring: str,
        fixtures: dict[str, _FixtureInfo],
        other_functions: dict[str, _FixtureInfo],
        source: str,
    ) -> None:
        self.module_docstring = module_docstring
        self.fixtures = fixtures
        self.other_functions = other_functions
        self.source = source


def _resolve_fixture_kwarg_value(node: ast.expr) -> Any:
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name) and node.id in ("True", "False", "None"):
        return {"True": True, "False": False, "None": None}[node.id]
    return None


def _parse_conftest(path: Path) -> _ConftestData | None:
    with open(path) as fh:
        source = fh.read()
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:
        return None

    module_docstring = ast.get_docstring(tree) or ""
    fixtures: dict[str, _FixtureInfo] = {}
    other_functions: dict[str, _FixtureInfo] = {}

    for node in ast.iter_child_nodes(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue

        is_fixture = False
        fixture_kwargs: dict[str, Any] = {}
        for decorator in node.decorator_list:
            if isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Attribute):
                if (
                    isinstance(decorator.func.value, ast.Name)
                    and decorator.func.value.id == "pytest"
                    and decorator.func.attr == "fixture"
                ):
                    is_fixture = True
                    for kw in decorator.keywords:
                        if kw.arg is not None:
                            fixture_kwargs[kw.arg] = _resolve_fixture_kwarg_value(kw.value)
            elif isinstance(decorator, ast.Attribute) and (
                isinstance(decorator.value, ast.Name) and decorator.value.id == "pytest" and decorator.attr == "fixture"
            ):
                is_fixture = True

        func_docstring = ast.get_docstring(node) or ""
        params = [arg.arg for arg in node.args.args]

        has_yield = any(isinstance(n, (ast.Yield, ast.YieldFrom)) for n in ast.walk(node))

        info = _FixtureInfo(
            docstring=func_docstring,
            params=params,
            fixture_kwargs=fixture_kwargs,
            has_yield=has_yield,
            lineno=node.lineno,
        )

        if is_fixture:
            fixtures[node.name] = info
        else:
            other_functions[node.name] = info

    return _ConftestData(
        module_docstring=module_docstring,
        fixtures=fixtures,
        other_functions=other_functions,
        source=source,
    )


CONFTEST_PATHS = _discover_conftest_files()
CONFTEST_DATA = {str(p): _parse_conftest(p) for p in CONFTEST_PATHS}

ALL_FIXTURES: dict[str, list[tuple[str, _FixtureInfo]]] = {}
for path_str, data in CONFTEST_DATA.items():
    if data is None:
        continue
    for name, info in data.fixtures.items():
        ALL_FIXTURES.setdefault(name, []).append((path_str, info))


# ---------------------------------------------------------------------------
# Test 1: All conftest files are discovered
# ---------------------------------------------------------------------------


def test_all_conftest_files_discovered() -> None:
    assert len(CONFTEST_PATHS) >= 10, f"Expected >=10 conftest.py files, found {len(CONFTEST_PATHS)}: " + ", ".join(
        str(p.relative_to(REPO_ROOT)) for p in CONFTEST_PATHS
    )


# ---------------------------------------------------------------------------
# Test 2: Every fixture function has a docstring
# ---------------------------------------------------------------------------


def test_all_fixtures_have_docstrings() -> None:
    failures: list[str] = []
    for path_str, data in CONFTEST_DATA.items():
        if data is None:
            continue
        p = Path(path_str)
        if not _is_project(p):
            continue
        rel = p.relative_to(REPO_ROOT)
        for name, info in data.fixtures.items():
            if not info.docstring.strip():
                failures.append(f"{rel}::{name} (line {info.lineno})")
    assert not failures, f"{len(failures)} fixture(s) missing docstrings:\n" + "\n".join(f"  - {f}" for f in failures)


# ---------------------------------------------------------------------------
# Test 3: No fixture naming collisions across conftest files
# ---------------------------------------------------------------------------


def test_no_cross_conftest_fixture_name_collisions() -> None:
    shadow_collisions: list[str] = []
    for name, occurrences in ALL_FIXTURES.items():
        if len(occurrences) <= 1:
            continue
        for i, (path_a, _) in enumerate(occurrences):
            for j, (path_b, _) in enumerate(occurrences):
                if i >= j:
                    continue
                pa = Path(path_a)
                pb = Path(path_b)
                if not _is_project(pa) or not _is_project(pb):
                    continue
                try:
                    ra = pa.relative_to(REPO_ROOT)
                    rb = pb.relative_to(REPO_ROOT)
                except ValueError:
                    continue
                pa_parents = set(pa.parents)
                pb_parents = set(pb.parents)
                if pa in pb_parents or pb in pa_parents:
                    shadow_collisions.append(
                        f"  {name}: {ra} shadows {rb}" if pa in pb_parents else f"  {name}: {rb} shadows {ra}"
                    )
    assert not shadow_collisions, (
        "Fixture name collisions detected (child conftest shadows parent):\n"
        + "\n".join(shadow_collisions)
        + "\n\nIf intentional, add a comment in the child fixture docstring."
    )


# ---------------------------------------------------------------------------
# Test 4: Session-scoped fixtures return constants or use generators
# ---------------------------------------------------------------------------


_CONSTANT_RETURN_FIXTURES = frozenset(
    {
        "repo_root",
        "zai_creds",
        "gateway_mode",
    }
)
_CONSTANT_SETUP_FIXTURES = frozenset(
    {
        "_ensure_gludd_dir_exists",
    }
)


def test_session_scoped_fixtures_are_generators() -> None:
    failures: list[str] = []
    for path_str, data in CONFTEST_DATA.items():
        if data is None:
            continue
        p = Path(path_str)
        if not _is_project(p):
            continue
        rel = p.relative_to(REPO_ROOT)
        for name, info in data.fixtures.items():
            if info.fixture_kwargs.get("scope") != "session":
                continue
            if info.has_yield:
                continue
            if name in _CONSTANT_RETURN_FIXTURES:
                continue
            if name in _CONSTANT_SETUP_FIXTURES:
                continue
            body_text = _extract_function_body(data.source, name)
            if not body_text:
                continue
            lines = body_text.split("\n")
            sum(1 for ln in lines if ln.lstrip().startswith(("import ", "from ")))
            non_empty = [ln for ln in lines if ln.strip() and not ln.lstrip().startswith(("@", "import ", "from "))]
            if len(non_empty) > 1:
                failures.append(
                    f"{rel}::{name} — session-scoped with body ({len(non_empty)} stmts) but no yield/teardown"
                )
    if failures:
        pytest.fail(
            f"{len(failures)} session-scoped fixture(s) lack generator teardown:\n"
            + "\n".join(f"  - {f}" for f in failures)
            + "\n\nIf the fixture only returns a constant, it's fine. "
            "Otherwise add yield for cleanup."
        )


def _extract_function_body(source: str, name: str) -> str:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return ""
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            if node.end_lineno is None:
                return ""
            lines = source.split("\n")
            return "\n".join(lines[node.lineno - 1 : node.end_lineno])
    return ""


# ---------------------------------------------------------------------------
# Test 5: Autouse fixtures are justified in their docstring
# ---------------------------------------------------------------------------


def test_autouse_fixtures_have_justification() -> None:
    justification_keywords = {
        "prevent",
        "ensure",
        "isolat",
        "reset",
        "restore",
        "snapshot",
        "sandbox",
        "global",
        "leak",
        "pollut",
        "backstop",
        "safety",
        "block",
        "guard",
        "patch",
        "worker",
        "xdist",
        "process",
        "singleton",
    }
    failures: list[str] = []
    for path_str, data in CONFTEST_DATA.items():
        if data is None:
            continue
        p = Path(path_str)
        if not _is_project(p):
            continue
        rel = p.relative_to(REPO_ROOT)
        for name, info in data.fixtures.items():
            if info.fixture_kwargs.get("autouse") is not True:
                continue
            doc_lower = info.docstring.lower()
            has_justification = any(kw in doc_lower for kw in justification_keywords)
            if not has_justification:
                failures.append(f"{rel}::{name} — autouse without clear justification")
    assert not failures, (
        f"{len(failures)} autouse fixture(s) lack justification in docstring:\n"
        + "\n".join(f"  - {f}" for f in failures)
        + "\n\nAutouse fixtures must explain WHY autouse is needed "
        "(not just what they do)."
    )


# ---------------------------------------------------------------------------
# Test 6: Autouse fixtures follow underscore-prefix naming convention
# ---------------------------------------------------------------------------


def test_autouse_fixtures_start_with_underscore() -> None:
    failures: list[str] = []
    for path_str, data in CONFTEST_DATA.items():
        if data is None:
            continue
        p = Path(path_str)
        if not _is_project(p):
            continue
        rel = p.relative_to(REPO_ROOT)
        for name, info in data.fixtures.items():
            if info.fixture_kwargs.get("autouse") is not True:
                continue
            if not name.startswith("_"):
                failures.append(f"{rel}::{name}")
    assert not failures, f"{len(failures)} autouse fixture(s) don't start with underscore:\n" + "\n".join(
        f"  - {f}" for f in failures
    )


# ---------------------------------------------------------------------------
# Test 7: Every conftest.py has a module-level docstring
# ---------------------------------------------------------------------------


def test_conftest_files_have_module_docstrings() -> None:
    failures: list[str] = []
    for path in CONFTEST_PATHS:
        if not _is_project(path):
            continue
        data = CONFTEST_DATA[str(path)]
        if data is None:
            failures.append(f"{path.relative_to(REPO_ROOT)} — unparseable")
            continue
        if not data.module_docstring.strip():
            failures.append(f"{path.relative_to(REPO_ROOT)} — missing module docstring")
    assert not failures, f"{len(failures)} conftest file(s) missing module docstrings:\n" + "\n".join(
        f"  - {f}" for f in failures
    )


# ---------------------------------------------------------------------------
# Test 8: External conftest files are recognized
# ---------------------------------------------------------------------------


def test_external_conftests_recognized() -> None:
    ext_root = REPO_ROOT / "external"
    if not ext_root.is_dir():
        pytest.skip("external/ directory not found in repo")

    external_paths: list[Path] = []
    for dirpath, _, filenames in os.walk(str(ext_root)):
        if "conftest.py" in filenames:
            external_paths.append(Path(dirpath) / "conftest.py")
    if not external_paths:
        # external/ holds git-submodule content (e.g. external/llamacpp); a
        # CI checkout without initialized submodules has no conftests at all.
        pytest.skip("external/ contains no conftest.py (submodules uninitialized)")
    assert len(external_paths) >= 2, f"Expected >=2 external conftest files; found {len(external_paths)}"
    for ep in external_paths:
        rel = ep.relative_to(REPO_ROOT)
        assert str(rel).startswith("external/"), f"{rel} should start with 'external/'"


# ---------------------------------------------------------------------------
# Test 9: Root conftest autouse fixtures are adequately documented
# ---------------------------------------------------------------------------


def test_root_conftest_autouse_fixture_count() -> None:
    path_str = str(REPO_ROOT / "tests" / "conftest.py")
    root = CONFTEST_DATA.get(path_str)
    assert root is not None, "Root conftest (tests/conftest.py) is missing"
    autouse = [(n, i) for n, i in root.fixtures.items() if i.fixture_kwargs.get("autouse") is True]
    assert len(autouse) >= 10, f"Root conftest has {len(autouse)} autouse fixtures, expected >=10"
    for name, info in autouse:
        assert len(info.docstring) >= 30, f"Root conftest::{name} docstring too short ({len(info.docstring)} chars)"


# ---------------------------------------------------------------------------
# Test 10: Integration conftest session fixture has teardown
# ---------------------------------------------------------------------------


def test_integration_conftest_session_fixture_has_teardown() -> None:
    path_str = str(REPO_ROOT / "tests" / "integration" / "conftest.py")
    data = CONFTEST_DATA.get(path_str)
    assert data is not None, "tests/integration/conftest.py is missing"
    block_hf = data.fixtures.get("_block_hf_downloads")
    assert block_hf is not None, "_block_hf_downloads fixture is missing"
    assert block_hf.has_yield, "_block_hf_downloads must use yield for teardown"
    assert block_hf.fixture_kwargs.get("scope") == "session"
    assert block_hf.fixture_kwargs.get("autouse") is True


# ---------------------------------------------------------------------------
# Test 11: E2E provider conftest fixtures are generators
# ---------------------------------------------------------------------------


def test_e2e_provider_conftest_fixtures_are_generators() -> None:
    path_str = str(REPO_ROOT / "tests" / "e2e" / "providers" / "conftest.py")
    data = CONFTEST_DATA.get(path_str)
    assert data is not None, "tests/e2e/providers/conftest.py is missing"
    for name in ("vllm_base_url", "llamacpp_base_url"):
        info = data.fixtures.get(name)
        assert info is not None, f"Fixture '{name}' is missing"
        assert info.has_yield, f"Fixture '{name}' must yield for teardown"


# ---------------------------------------------------------------------------
# Test 12: Dogfood conftest fixtures have docstrings
# ---------------------------------------------------------------------------


def test_dogfood_conftest_all_fixtures_documented() -> None:
    path_str = str(REPO_ROOT / "tests" / "e2e" / "dogfood" / "conftest.py")
    data = CONFTEST_DATA.get(path_str)
    assert data is not None, "tests/e2e/dogfood/conftest.py is missing"
    assert len(data.fixtures) >= 5, "Dogfood conftest should have >=5 fixtures"
    for name, info in data.fixtures.items():
        assert info.docstring.strip(), f"Fixture '{name}' has no docstring"


# ---------------------------------------------------------------------------
# Test 13: No conftest file has hardcoded /tmp/gludd- paths (in logic)
# ---------------------------------------------------------------------------


_HARDCODED_TMP_GLUDD_ALLOWLIST = frozenset(
    {
        str(REPO_ROOT / "tests" / "conftest.py"),
    }
)


def test_no_conftest_has_hardcoded_tmp_gludd() -> None:
    failures: list[str] = []
    for path_str, data in CONFTEST_DATA.items():
        if data is None:
            continue
        if path_str in _HARDCODED_TMP_GLUDD_ALLOWLIST:
            continue
        if not _is_project(Path(path_str)):
            continue
        if "/tmp/gludd-" in data.source:
            failures.append(str(Path(path_str).relative_to(REPO_ROOT)))
    assert not failures, (
        f"{len(failures)} conftest file(s) contain hardcoded "
        f"'/tmp/gludd-' paths:\n"
        + "\n".join(f"  - {f}" for f in failures)
        + "\n\nUse tmp_path fixture or env-var-redirected paths instead."
    )


# ---------------------------------------------------------------------------
# Test 14: Fixture parameter names match available fixtures
# ---------------------------------------------------------------------------


def test_fixture_parameters_match_dependencies() -> None:
    global_fixture_names: set[str] = set()
    for name, occurrences in ALL_FIXTURES.items():
        has_project = any(_is_project(Path(p)) for p, _ in occurrences)
        if has_project:
            global_fixture_names.add(name)

    known_non_fixtures = {
        "request",
        "config",
        "monkeypatch",
        "capsys",
        "capfd",
        "tmp_path",
        "tmp_path_factory",
        "tmpdir",
        "caplog",
        "record_property",
        "record_testsuite_property",
        "pytestconfig",
        "testrun_uid",
        "worker_id",
        "recwarn",
        "doctest_namespace",
    }

    failures: list[str] = []
    for path_str, data in CONFTEST_DATA.items():
        if data is None:
            continue
        p = Path(path_str)
        if not _is_project(p):
            continue
        rel = p.relative_to(REPO_ROOT)
        for name, info in data.fixtures.items():
            for param in info.params:
                if param in known_non_fixtures:
                    continue
                if param not in global_fixture_names:
                    failures.append(
                        f"{rel}::{name} requests param '{param}' — no fixture with that name found in any conftest"
                    )
    assert not failures, f"{len(failures)} fixture(s) with unsatisfied parameter dependencies:\n" + "\n".join(
        f"  - {f}" for f in failures
    )


# ---------------------------------------------------------------------------
# Test 15: Fixture decorator count is consistent with AST
# ---------------------------------------------------------------------------


def test_fixture_decorator_counts_consistent() -> None:
    failures: list[str] = []
    for path_str, data in CONFTEST_DATA.items():
        if data is None:
            continue
        p = Path(path_str)
        if not _is_project(p):
            continue
        rel = p.relative_to(REPO_ROOT)
        try:
            tree = ast.parse(data.source, filename=path_str)
        except SyntaxError:
            continue

        for node in ast.iter_child_nodes(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            name = node.name
            if name not in data.fixtures:
                continue

            fixture_decorator_count = sum(
                1
                for d in node.decorator_list
                if (
                    isinstance(d, ast.Attribute)
                    and isinstance(d.value, ast.Name)
                    and d.value.id == "pytest"
                    and d.attr == "fixture"
                )
                or (
                    isinstance(d, ast.Call)
                    and isinstance(d.func, ast.Attribute)
                    and isinstance(d.func.value, ast.Name)
                    and d.func.value.id == "pytest"
                    and d.func.attr == "fixture"
                )
            )
            if fixture_decorator_count > 1:
                failures.append(f"{rel}::{name} — {fixture_decorator_count} @pytest.fixture decorators on one function")
            elif fixture_decorator_count == 0:
                failures.append(f"{rel}::{name} — in fixtures dict but no @pytest.fixture decorator found in AST")

    assert not failures, f"{len(failures)} fixture(s) with decorator count issues:\n" + "\n".join(
        f"  - {f}" for f in failures
    )


# ---------------------------------------------------------------------------
# Test 16: Session-scoped fixtures explain their scope choice
# ---------------------------------------------------------------------------


_SESSION_SCOPE_OK_WITHOUT_EXPLANATION = frozenset(
    {
        "repo_root",
        "zai_creds",
        "gateway_mode",
    }
)


def test_session_scoped_fixtures_explain_scope_choice() -> None:
    scope_keywords = {"session", "worker", "process", "xdist", "once", "lifetime"}
    failures: list[str] = []
    for path_str, data in CONFTEST_DATA.items():
        if data is None:
            continue
        p = Path(path_str)
        if not _is_project(p):
            continue
        rel = p.relative_to(REPO_ROOT)
        for name, info in data.fixtures.items():
            if info.fixture_kwargs.get("scope") != "session":
                continue
            if name in _SESSION_SCOPE_OK_WITHOUT_EXPLANATION:
                continue
            if info.fixture_kwargs.get("autouse"):
                continue
            doc_lower = info.docstring.lower()
            has_explanation = any(kw in doc_lower for kw in scope_keywords)
            if not has_explanation:
                failures.append(f"{rel}::{name} — session-scoped but docstring doesn't explain why")
    if failures:
        pytest.fail(
            f"{len(failures)} session-scoped fixture(s) lack scope "
            f"justification:\n" + "\n".join(f"  - {f}" for f in failures)
        )


# ---------------------------------------------------------------------------
# Test 17: conftest files under ansible collections are valid
# ---------------------------------------------------------------------------


def test_ansible_collection_conftests_are_valid() -> None:
    ppaths = [p for p in CONFTEST_PATHS if "collections/ansible_collections" in str(p)]
    assert len(ppaths) >= 3, f"Expected >=3 ansible collection conftest files; found {len(ppaths)}"
    for p in ppaths:
        data = CONFTEST_DATA[str(p)]
        assert data is not None, f"Unparseable: {p.relative_to(REPO_ROOT)}"
        assert data.module_docstring.strip(), f"Missing module docstring: {p.relative_to(REPO_ROOT)}"
        assert data.fixtures == {}, f"Unexpected fixtures in {p.relative_to(REPO_ROOT)}: {list(data.fixtures)}"


# ---------------------------------------------------------------------------
# Test 18: Root conftest session-scoped autouse fixtures use yield
# ---------------------------------------------------------------------------


def test_root_conftest_session_autouse_fixtures_use_yield() -> None:
    path_str = str(REPO_ROOT / "tests" / "conftest.py")
    root = CONFTEST_DATA.get(path_str)
    assert root is not None
    session_autouse = [
        (n, i)
        for n, i in root.fixtures.items()
        if i.fixture_kwargs.get("scope") == "session" and i.fixture_kwargs.get("autouse") is True
    ]
    assert len(session_autouse) >= 2, f"Root conftest has {len(session_autouse)} session-scoped autouse fixtures"
    for name, info in session_autouse:
        assert info.has_yield, f"Root conftest::{name} — session-scoped autouse fixture must use yield for teardown"
