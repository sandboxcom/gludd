"""Structural tests verifying the PyInstaller spec produces a valid bundle.

Covers hook ordering, hidden imports, data file collections, deduplication,
platform exclusions, and bundle metadata correctness.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SPEC_PATH = REPO_ROOT / "gludd.spec"


@pytest.fixture(scope="module")
def spec_text() -> str:
    assert SPEC_PATH.is_file(), f"gludd.spec missing at {SPEC_PATH}"
    return SPEC_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def spec_lines(spec_text: str) -> list[str]:
    return spec_text.splitlines()


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _find_call_args(spec_text: str, func_name: str) -> list[str]:
    """Return string representations of call args for each match of *func_name*(...)."""
    pattern = rf"{re.escape(func_name)}\s*\((.*?)\)"
    return re.findall(pattern, spec_text, re.DOTALL)


def _parse_string_list(text: str) -> list[str]:
    """Extract quoted strings from a list literal fragment."""
    return re.findall(r"""['"]([^'"]+)['"]""", text)


def _tokenize_python_source(text: str) -> list[ast.stmt]:
    """Parse top-level statements from Python source text."""
    try:
        tree = ast.parse(text)
        return tree.body  # type: ignore[return-value]
    except SyntaxError:
        return []


# ---------------------------------------------------------------------------
# 1. Hook ordering
# ---------------------------------------------------------------------------


class TestHookOrdering:
    def test_analysis_before_pyz(self, spec_lines: list[str]) -> None:
        """Analysis must appear before PYZ in the spec file."""
        analysis_idx = next(i for i, line in enumerate(spec_lines) if line.strip().startswith("a = Analysis("))
        pyz_idx = next(i for i, line in enumerate(spec_lines) if line.strip().startswith("pyz = PYZ("))
        assert analysis_idx < pyz_idx, f"Analysis (line {analysis_idx + 1}) must precede PYZ (line {pyz_idx + 1})"

    def test_pyz_before_exe(self, spec_lines: list[str]) -> None:
        """PYZ must appear before EXE in the spec file."""
        pyz_idx = next(i for i, line in enumerate(spec_lines) if line.strip().startswith("pyz = PYZ("))
        exe_idx = next(i for i, line in enumerate(spec_lines) if line.strip().startswith("exe = EXE("))
        assert pyz_idx < exe_idx, f"PYZ (line {pyz_idx + 1}) must precede EXE (line {exe_idx + 1})"

    def test_exe_is_onefile_no_collect(self, spec_text: str) -> None:
        """EXE (one-file) bundle must NOT be followed by COLLECT (one-directory).

        COLLECT is only used for one-directory builds. A one-file build uses
        Analysis → PYZ → EXE without a COLLECT step.
        """
        assert "COLLECT(" not in spec_text, (
            "COLLECT should not appear in a one-file (EXE) bundle spec. COLLECT is for --onedir builds only."
        )
        assert "EXE(" in spec_text, "Spec must produce an EXE (one-file bundle)."

    def test_analysis_feeds_pyz(self, spec_text: str) -> None:
        """PYZ(...) must receive a.pure and a.zipped_data from Analysis."""
        assert "a.pure" in spec_text, "PYZ must receive a.pure from Analysis — this feeds all pure-Python modules."
        assert "a.zipped_data" in spec_text, "PYZ must receive a.zipped_data from Analysis."

    def test_exe_receives_all_analysis_outputs(self, spec_text: str) -> None:
        """EXE(...) must receive a.scripts, a.binaries, a.zipfiles, a.datas."""
        for attr in ("a.scripts", "a.binaries", "a.zipfiles", "a.datas"):
            assert attr in spec_text, f"EXE must receive {attr} from Analysis — missing from spec."


# ---------------------------------------------------------------------------
# 2. Required hidden imports
# ---------------------------------------------------------------------------


class TestHiddenImports:
    def test_general_ludd_submodules_listed(self, spec_text: str) -> None:
        """All gludd source submodules are listed in hiddenimports."""
        required = [
            "general_ludd",
            "general_ludd.compat",
            "general_ludd.cli",
            "general_ludd.daemon",
            "general_ludd.worker.app",
            "general_ludd.event_loop.loop",
            "general_ludd.ansible.runner",
            "general_ludd.db.models",
            "general_ludd.secrets.manager",
        ]
        for mod in required:
            assert f"'{mod}'" in spec_text or f'"{mod}"' in spec_text, f"hiddenimports must include '{mod}'"

    def test_uvicorn_submodules_listed(self, spec_text: str) -> None:
        """Uvicorn dynamic-import subpackages are listed in hiddenimports."""
        required = [
            "uvicorn.logging",
            "uvicorn.loops",
            "uvicorn.loops.auto",
            "uvicorn.protocols",
            "uvicorn.protocols.http",
            "uvicorn.protocols.http.auto",
            "uvicorn.protocols.websockets",
            "uvicorn.protocols.websockets.auto",
            "uvicorn.lifespan",
            "uvicorn.lifespan.on",
        ]
        for mod in required:
            assert f"'{mod}'" in spec_text or f'"{mod}"' in spec_text, f"hiddenimports must include '{mod}'"

    def test_ansible_submodules_collected(self, spec_text: str) -> None:
        """collect_submodules calls exist for ansible's dynamic-import packages."""
        required = [
            "ansible.module_utils",
            "ansible.plugins",
            "ansible.template",
            "ansible.galaxy",
        ]
        for sub in required:
            assert re.search(
                r"collect_submodules\(\s*['\"]" + re.escape(sub) + r"['\"]\s*\)",
                spec_text,
            ), f"collect_submodules('{sub}') is required"

    def test_no_unknown_hiddenimport_duplication(self, spec_text: str) -> None:
        """No hidden import appears more than once in the hiddenimports list."""
        hidden_match = re.search(r"hiddenimports\s*=\s*\[(.*?)\]", spec_text, re.DOTALL)
        assert hidden_match, "hiddenimports list must exist in Analysis"
        entries = _parse_string_list(hidden_match.group(1))
        seen: dict[str, int] = {}
        for entry in entries:
            seen[entry] = seen.get(entry, 0) + 1
        dupes = {k: v for k, v in seen.items() if v > 1}
        assert not dupes, f"Duplicate hidden imports found: {dupes}. Each import must appear exactly once."


# ---------------------------------------------------------------------------
# 3. Data file collections
# ---------------------------------------------------------------------------


class TestDataFileCollections:
    def test_project_data_dirs_collected(self, spec_text: str) -> None:
        """config, templates, playbooks dirs are in the datas list."""
        body = _find_analysis_datas_body(spec_text)
        assert body is not None, "Analysis datas= must exist"
        required = ["config", "templates", "playbooks"]
        for dir_name in required:
            assert re.search(rf"""\(\s*['"]{re.escape(dir_name)}['"]""", body), (
                f"datas must include ('{dir_name}', ...)"
            )

    def test_license_files_collected(self, spec_text: str) -> None:
        """LICENSE and THIRD_PARTY_LICENSES.md are in the datas list."""
        body = _find_analysis_datas_body(spec_text)
        assert body is not None
        for filename in ("LICENSE", "THIRD_PARTY_LICENSES.md"):
            assert re.search(rf"""\(\s*['"]{re.escape(filename)}['"]""", body), (
                f"datas must include ('{filename}', ...)"
            )

    def test_ansible_data_collected(self, spec_text: str) -> None:
        """collect_data_files('ansible') is called AND reaches the bundle."""
        assert re.search(
            r"collect_data_files\(\s*['\"]ansible['\"]\s*\)",
            spec_text,
        ), "collect_data_files('ansible') must be called"
        # The spec assembles ansible data into the module-level datas variable
        # and passes it to Analysis via ``datas=datas``.
        assert re.search(r"\+ _ansible_datas", spec_text), (
            "_ansible_datas must be joined into the module-level datas list"
        )
        analysis_match = re.search(
            r"Analysis\s*\((?P<an_body>.*?)\)\s*$",
            spec_text,
            re.DOTALL | re.MULTILINE,
        )
        an_body = analysis_match.group("an_body") if analysis_match else ""
        assert "datas=datas" in an_body, (
            "Analysis must reference the module-level datas variable that carries ansible data files."
        )

    def test_no_duplicate_datas_entries(self, spec_text: str) -> None:
        """Each (source, dest) pair appears at most once in datas."""
        body = _find_analysis_datas_body(spec_text)
        assert body is not None
        pairs = re.findall(r"""\(\s*(['"][^'"]+['"])\s*,\s*(['"][^'"]+['"])""", body)
        seen: dict[tuple[str, str], int] = {}
        for src, dst in pairs:
            key = (src.strip("'\""), dst.strip("'\""))
            seen[key] = seen.get(key, 0) + 1
        dupes = {k: v for k, v in seen.items() if v > 1}
        assert not dupes, f"Duplicate datas entries: {dupes}"


# ---------------------------------------------------------------------------
# 4. Icon / metadata checks
# ---------------------------------------------------------------------------


class TestBundleMetadata:
    def test_binary_name_is_gludd(self, spec_text: str) -> None:
        """EXE name is 'gludd'."""
        exe_match = re.search(r"name\s*=\s*['\"]([^'\"]+)['\"]", spec_text)
        assert exe_match, "EXE must have name='gludd'"
        assert exe_match.group(1) == "gludd", f"Binary name must be 'gludd', got '{exe_match.group(1)}'"

    def test_console_enabled(self, spec_text: str) -> None:
        """EXE has console=True for CLI operation."""
        assert re.search(r"console\s*=\s*True", spec_text), "console=True required for CLI binary"

    def test_no_icon_referenced(self, spec_lines: list[str]) -> None:
        """If an icon path is referenced, the file must exist on disk."""
        icon_match = re.search(r"icon\s*=\s*['\"]([^'\"]+)['\"]", "\n".join(spec_lines))
        if icon_match:
            icon_path = REPO_ROOT / icon_match.group(1)
            assert icon_path.is_file(), (
                f"icon file referenced in spec ('{icon_match.group(1)}') does not exist at {icon_path}"
            )


# ---------------------------------------------------------------------------
# 5. Platform exclusions
# ---------------------------------------------------------------------------


class TestPlatformExclusions:
    def test_ansible_dot_cli_excluded(self, spec_text: str) -> None:
        """ansible.cli must be excluded (Windows cp1252 locale crash)."""
        assert re.search(
            r"""excludes\s*=\s*\[.*['"]ansible\.cli['"].*\]""",
            spec_text,
            re.DOTALL,
        ), "ansible.cli must be in excludes"

    def test_dev_tools_excluded(self, spec_text: str) -> None:
        """Development-only tools are excluded from the production bundle."""
        dev_tools = ["pytest", "mypy", "ruff", "pre_commit", "molecule", "ansible_lint"]
        for tool in dev_tools:
            assert re.search(
                rf"""excludes\s*=\s*\[.*['"]{re.escape(tool)}['"].*\]""",
                spec_text,
                re.DOTALL,
            ), f"'{tool}' must be in excludes — it is a dev-only tool"

    @pytest.mark.skipif(sys.platform != "win32", reason="win_no_prefer_redirects is Windows-only")
    def test_windows_redirects_disabled(self, spec_text: str) -> None:
        """win_no_prefer_redirects=False keeps standard DLL redirect behaviour."""
        assert "win_no_prefer_redirects" in spec_text, "win_no_prefer_redirects should be explicitly set"

    @pytest.mark.skipif(sys.platform != "win32", reason="UPX is cross-platform but skip on non-Windows for focus")
    def test_upx_enabled(self, spec_text: str) -> None:
        """UPX compression is enabled to reduce binary size."""
        assert re.search(r"upx\s*=\s*True", spec_text), "upx=True should be set for binary compression"


# ---------------------------------------------------------------------------
# helpers (module-level, shared with existing patterns)
# ---------------------------------------------------------------------------


def _find_analysis_datas_body(spec_text: str) -> str | None:
    """Extract the inner text of the ``datas`` list that feeds Analysis.

    Handles both patterns:
      (a) ``Analysis(... datas=[(...), ...] ...)`` — inline list.
      (b) ``Analysis(... datas=datas ...)`` — module-level variable.
    """
    analysis_match = re.search(
        r"Analysis\s*\((?P<body>.*?)\)\s*$",
        spec_text,
        re.DOTALL | re.MULTILINE,
    )
    if not analysis_match:
        return None
    body = analysis_match.group("body")

    # Pattern A: inline list
    inline_match = re.search(r"datas\s*=\s*\[(?P<inner>.*?)\]", body, re.DOTALL)
    if inline_match:
        return inline_match.group("inner")

    # Pattern B: module-level ``datas = [...] + _ansible_datas``
    modvar_match = re.search(
        r"^datas\s*=\s*\[(?P<inner>.*?)\]",
        spec_text,
        re.DOTALL | re.MULTILINE,
    )
    if modvar_match:
        return modvar_match.group("inner")

    return None
