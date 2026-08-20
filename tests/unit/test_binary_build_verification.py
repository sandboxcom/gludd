"""Structural tests verifying ``gludd.spec`` produces a valid PyInstaller binary.

Validates that the spec file has all required Analysis parameters,
hidden imports, data file collections, platform-specific exclusions,
and EXE block configuration needed for a working bundled binary.

These complement ``test_pyinstaller_spec_completeness.py`` which focuses
on ansible data collection and library data file coverage.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SPEC_PATH = REPO_ROOT / "gludd.spec"
PYPROJECT_PATH = REPO_ROOT / "pyproject.toml"

_REGISTRY = frozenset(
    {
        "pytest",
        "mypy",
        "ruff",
        "pre_commit",
        "molecule",
        "ansible_lint",
        "ansible.cli",
    }
)

_REQUIRED_HIDDENIMPORTS = {
    "general_ludd",
    "general_ludd.cli",
    "general_ludd.daemon",
    "general_ludd.db.models",
    "general_ludd.db.repository",
    "general_ludd.secrets.manager",
    "uvicorn.logging",
    "uvicorn.loops.auto",
    "uvicorn.protocols.http.auto",
    "uvicorn.lifespan.on",
}

_CONTROLLER_HIDDENIMPORTS = {
    "general_ludd.ansible.runner",
    "general_ludd.ansible.core_runner",
}

_ANSIBLE_SUBMODULE_PACKAGES = {
    "ansible.module_utils",
    "ansible.plugins",
    "ansible.template",
    "ansible.galaxy",
}


@pytest.fixture(scope="module")
def spec_text() -> str:
    assert SPEC_PATH.is_file(), f"gludd.spec missing at {SPEC_PATH}"
    return SPEC_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def pyproject_text() -> str:
    assert PYPROJECT_PATH.is_file(), f"pyproject.toml missing at {PYPROJECT_PATH}"
    return PYPROJECT_PATH.read_text(encoding="utf-8")


def _analysis_body(spec_text: str) -> str | None:
    m = re.search(
        r"Analysis\s*\((?P<body>.*?)\)\s*\n\s*(?:pyz|PYZ)\b",
        spec_text,
        re.DOTALL,
    )
    if not m:
        return None
    return m.group("body")


def _exe_body(spec_text: str) -> str | None:
    m = re.search(r"EXE\s*\((?P<body>.*?)\)\s*$", spec_text, re.DOTALL | re.MULTILINE)
    if not m:
        return None
    return m.group("body")


def _analysis_keyword_node(spec_text: str, key: str) -> ast.expr | None:
    """Return the value assigned to an Analysis keyword."""
    tree = ast.parse(spec_text, filename=str(SPEC_PATH))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Name) or node.func.id != "Analysis":
            continue
        for keyword in node.keywords:
            if keyword.arg == key:
                return keyword.value
    return None


def _parse_string_list(spec_text: str, key: str) -> list[str]:
    """Return string literals from an Analysis keyword expression."""
    node = _analysis_keyword_node(spec_text, key)
    if node is None:
        return []
    return [
        child.value
        for child in ast.walk(node)
        if isinstance(child, ast.Constant) and isinstance(child.value, str)
    ]


def _collect_submodule_packages(spec_text: str) -> set[str]:
    """Return literal package names passed to collect_submodules."""
    tree = ast.parse(spec_text, filename=str(SPEC_PATH))
    return {
        call.args[0].value
        for call in ast.walk(tree)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Name)
        and call.func.id == "collect_submodules"
        and call.args
        and isinstance(call.args[0], ast.Constant)
        and isinstance(call.args[0].value, str)
    }


def _parse_tuple_list(spec_text: str, key: str) -> list[tuple[str, str]]:
    pattern = rf"{re.escape(key)}\s*=\s*\[(?P<inner>.*?)\]"
    m = re.search(pattern, spec_text, re.DOTALL)
    if not m:
        return []
    pairs = re.findall(r"""\(\s*['"]([^'"]+)['"]\s*,\s*['"]([^'"]+)['"]\s*\)""", m.group("inner"))
    return pairs


class TestAnalysisBlock:
    def test_analysis_has_required_params(self, spec_text: str) -> None:
        body = _analysis_body(spec_text)
        assert body is not None, "gludd.spec must contain an Analysis(...) block"
        for param in ("datas", "hiddenimports", "hookspath", "excludes"):
            assert re.search(rf"\b{param}\s*=", body), f"Analysis block must include {param}= parameter"

    def test_analysis_scripts_entry(self, spec_text: str) -> None:
        body = _analysis_body(spec_text)
        assert body is not None
        assert re.search(r"src/general_ludd/cli\.py", body), "Analysis scripts must include src/general_ludd/cli.py"


class TestHiddenImports:
    def test_critical_hidden_imports_present(self, spec_text: str) -> None:
        hidden = set(_parse_string_list(spec_text, "hiddenimports"))
        missing = _REQUIRED_HIDDENIMPORTS - hidden
        assert not missing, f"Missing required hidden imports in gludd.spec: {sorted(missing)}"
        bundled_controller = _CONTROLLER_HIDDENIMPORTS & hidden
        assert not bundled_controller, (
            "Frozen core must not force Ansible controller modules into the binary: "
            f"{sorted(bundled_controller)}"
        )

    def test_ansible_submodules_in_hidden_imports(self, spec_text: str) -> None:
        collected = _collect_submodule_packages(spec_text)
        bundled = _ANSIBLE_SUBMODULE_PACKAGES & collected
        assert not bundled, (
            "Ansible controller submodules belong in the independently locked EE, "
            f"not the frozen core: {sorted(bundled)}"
        )
        excluded = set(_parse_string_list(spec_text, "excludes"))
        assert {"ansible", "ansible_runner", "ansible.cli"} <= excluded

    def test_no_duplicate_hidden_imports(self, spec_text: str) -> None:
        literal_imports = _parse_string_list(spec_text, "hiddenimports")
        seen: set[str] = set()
        dupes: list[str] = []
        for imp in literal_imports:
            if imp in seen:
                dupes.append(imp)
            seen.add(imp)
        assert not dupes, f"Duplicate hidden imports found in gludd.spec: {dupes}"


class TestDataCollections:
    def test_required_data_dirs_collected(self, spec_text: str) -> None:
        pairs = _parse_tuple_list(spec_text, "datas")
        sources = {src for src, _ in pairs}
        for expected in ("config", "templates"):
            assert expected in sources, f"datas must include '{expected}' directory — found: {sorted(sources)}"
        assert "playbooks" not in sources, "Controller playbooks belong in the EE/collections, not the frozen core"

    def test_license_files_in_datas(self, spec_text: str) -> None:
        pairs = _parse_tuple_list(spec_text, "datas")
        sources = set(pairs)
        assert ("LICENSE", ".") in sources, "datas must include ('LICENSE', '.')"
        assert ("THIRD_PARTY_LICENSES.md", ".") in sources, "datas must include ('THIRD_PARTY_LICENSES.md', '.')"

    def test_no_duplicate_datas_entries(self, spec_text: str) -> None:
        pairs = _parse_tuple_list(spec_text, "datas")
        seen: set[tuple[str, str]] = set()
        dupes: list[tuple[str, str]] = []
        for pair in pairs:
            if pair in seen:
                dupes.append(pair)
            seen.add(pair)
        assert not dupes, f"Duplicate datas entries found in gludd.spec: {dupes}"

    def test_ansible_data_files_collected(self, spec_text: str) -> None:
        assert not re.search(r"collect_data_files\(\s*['\"]ansible['\"]\s*\)", spec_text), (
            "Frozen core must not collect Ansible data files; the locked EE owns them"
        )


class TestBinaryName:
    def test_binary_name_is_gludd(self, spec_text: str) -> None:
        body = _exe_body(spec_text)
        assert body is not None, "gludd.spec must contain an EXE(...) block"
        m = re.search(r"name\s*=\s*['\"]([^'\"]+)['\"]", body)
        assert m is not None, "EXE block must have a name= parameter"
        assert m.group(1) == "gludd", f"Binary name must be 'gludd', got '{m.group(1)}'"

    def test_cli_entry_imports_match_binary_name(self, spec_text: str) -> None:
        body = _analysis_body(spec_text)
        assert body is not None
        assert "src/general_ludd/cli.py" in body, "Analysis scripts must point to src/general_ludd/cli.py"


class TestPlatformExclusions:
    def test_excludes_include_expected(self, spec_text: str) -> None:
        excluded = set(_parse_string_list(spec_text, "excludes"))
        for name in _REGISTRY:
            assert name in excluded, f"gludd.spec excludes must include '{name}' — found: {sorted(excluded)}"

    def test_win_platform_params_present(self, spec_text: str) -> None:
        body = _analysis_body(spec_text)
        assert body is not None
        for param in ("win_no_prefer_redirects", "win_private_assemblies"):
            assert re.search(rf"\b{param}\s*=", body), f"Analysis block must include {param}= parameter"

    def test_exe_console_param(self, spec_text: str) -> None:
        body = _exe_body(spec_text)
        assert body is not None
        m = re.search(r"console\s*=\s*(True|False)", body)
        assert m is not None, "EXE block must have a console= parameter"
