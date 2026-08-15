"""Structural tests verifying ``gludd.spec`` collects ALL data files needed
to prevent the ``Missing base YAML definition file (bad install?)`` runtime
crash that shipped in v0.1.0-beta.1.

Background:
    v0.1.0-beta.1 shipped a binary that crashed on macOS because
    ``ansible/config/base.yml`` was not collected into the PyInstaller
    bundle. ``ansible.config.manager`` reads that YAML at startup via
    ``importlib.resources``; PyInstaller's static analyzer does not see the
    runtime resource lookup, so the file is silently dropped.

    The fix layers are pinned here:
      1. ``collect_data_files('ansible')`` must run AND its result must be
         passed to ``Analysis(... datas=[...] ...)``. (Computing it but
         dropping the value is the bug that re-shipped the crash.)
      2. ``collect_submodules('ansible.module_utils')`` (plus plugins,
         template, galaxy) must run so PyInstaller's static analyzer does
         not miss ansible's dynamic imports.
      3. ``ansible`` (the executor stack) must NOT appear in ``excludes=``;
         only ``ansible.cli`` is excluded (Windows cp1252 locale issue).
      4. Every ``general_ludd.compat`` submodule must be listed as a
         hiddenimport — ``src/general_ludd/__init__.py`` loads
         ``general_ludd.compat.annotated_types`` through a dynamic
         ``importlib.import_module`` call at package init, so the static
         analyzer drops it and the frozen CLI crashes with
         ``ModuleNotFoundError: No module named 'general_ludd.compat'``.

    The other libraries gludd depends on (jinja2, pydantic, sqlalchemy,
    uvicorn) ship their own non-.py data files. PyInstaller ships built-in
    hooks for each, so explicit ``collect_data_files`` is not normally
    required — but if a library ever introduces a runtime-critical YAML or
    JSON file its built-in hook does not cover, the tests below will surface
    it by scanning the installed package at test time.
"""

from __future__ import annotations

import ast
import re
from importlib import resources
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SPEC_PATH = REPO_ROOT / "gludd.spec"

# Extensions PyInstaller does NOT auto-detect — they must be explicitly
# collected via ``collect_data_files`` or a built-in PyInstaller hook.
DATA_FILE_EXTENSIONS = (".yml", ".yaml", ".json", ".txt", ".cfg", ".ini", ".toml")

# Path subsegments that mark a data file as test/documentation/example rather
# than runtime-critical. Files under these paths do NOT need to be bundled.
_NON_RUNTIME_MARKERS = (
    "test",
    "tests",
    "example",
    "examples",
    "doc",
    "docs",
    "guideline",
)


@pytest.fixture(scope="module")
def spec_text() -> str:
    """Contents of ``gludd.spec`` as a single string."""
    assert SPEC_PATH.is_file(), f"gludd.spec missing at {SPEC_PATH}"
    return SPEC_PATH.read_text(encoding="utf-8")


def _analysis_keyword_node(spec_text: str, keyword: str) -> ast.expr | None:
    """Return an ``Analysis(...)`` keyword value from a PyInstaller spec."""
    tree = ast.parse(spec_text, filename=str(SPEC_PATH))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Name) or node.func.id != "Analysis":
            continue
        for item in node.keywords:
            if item.arg == keyword:
                return item.value
    return None


def _string_literals(node: ast.AST | None) -> set[str]:
    """Collect string literals contained in an AST expression."""
    if node is None:
        return set()
    return {child.value for child in ast.walk(node) if isinstance(child, ast.Constant) and isinstance(child.value, str)}


def _excludes_from_spec(spec_text: str) -> set[str]:
    """Parse literal entries passed to ``Analysis(excludes=...)``.

    AST inspection remains correct when the literal list is concatenated with
    a platform-specific variable, unlike the former bracket-matching regex.
    """
    return _string_literals(_analysis_keyword_node(spec_text, "excludes"))


def _analysis_datas_body(spec_text: str) -> str | None:
    """Return the source expression passed to ``Analysis(datas=...)``."""
    node = _analysis_keyword_node(spec_text, "datas")
    return ast.unparse(node) if node is not None else None


def _collect_submodule_packages(spec_text: str) -> set[str]:
    """Return package names passed to ``collect_submodules(...)``."""
    tree = ast.parse(spec_text, filename=str(SPEC_PATH))
    packages: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Name) or node.func.id != "collect_submodules":
            continue
        if node.args and isinstance(node.args[0], ast.Constant):
            package = node.args[0].value
            if isinstance(package, str):
                packages.add(package)
    return packages


def _collect_submodule_calls(spec_text: str) -> list[ast.Call]:
    """Return every ``collect_submodules(...)`` call in the spec."""
    tree = ast.parse(spec_text, filename=str(SPEC_PATH))
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "collect_submodules"
    ]


def _find_runtime_data_files(package_name: str) -> list[Path]:
    """Find runtime-critical non-.py data files shipped by an installed package.

    Uses ``importlib.resources`` so the lookup works in both dev mode
    (site-packages) and PyInstaller mode (``collect_data_files`` wires the
    resource reader). Returns paths relative to the package root, filtered
    to exclude test/doc/example files (those do not need bundling).

    Returns an empty list if the package is not importable in this
    environment — that is a soft-fail so the test still produces a useful
    assertion on the spec structure.
    """
    try:
        root = Path(str(resources.files(package_name)))
    except (ImportError, ModuleNotFoundError, TypeError, ValueError):
        return []
    if not root.is_dir():
        return []
    runtime_files: list[Path] = []
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        if p.suffix.lower() not in DATA_FILE_EXTENSIONS:
            continue
        rel = p.relative_to(root)
        rel_lower = str(rel).lower()
        if any(marker in rel_lower for marker in _NON_RUNTIME_MARKERS):
            continue
        runtime_files.append(rel)
    return runtime_files


def _hiddenimports_from_spec(spec_text: str) -> set[str]:
    """Parse literal entries passed to ``Analysis(hiddenimports=...)``."""
    return _string_literals(_analysis_keyword_node(spec_text, "hiddenimports"))


def _compat_submodules() -> set[str]:
    """Every importable module under ``src/general_ludd/compat/``.

    The dynamic import in ``src/general_ludd/__init__.py`` (and any future
    shim added there) must be mirrored by a spec hiddenimport. Discovering
    the module list from disk means a new compat shim that forgets to update
    ``gludd.spec`` fails this test instead of crashing the frozen binary.
    """
    compat_root = REPO_ROOT / "src" / "general_ludd" / "compat"
    submodules: set[str] = {"general_ludd.compat"}
    for p in sorted(compat_root.rglob("*.py")):
        rel = p.relative_to(compat_root.parent)
        if rel.name == "__init__.py":
            continue
        module = "general_ludd." + str(rel.with_suffix("")).replace("/", ".")
        submodules.add(module)
    return submodules


class TestAnsibleDataCollection:
    """The gludd binary crashed on macOS because ``ansible/config/base.yml``
    wasn't bundled. These tests verify the spec collects ALL ansible data."""

    def test_spec_uses_collect_data_files(self, spec_text: str) -> None:
        """Verify gludd.spec calls ``collect_data_files('ansible')``.

        Without this call, PyInstaller's static analyzer misses every
        non-.py file under the ansible package — including
        ``ansible/config/base.yml`` (the crash file), the plugin YAML
        definitions, and the module-utils data files.
        """
        assert re.search(r"collect_data_files\(\s*['\"]ansible['\"]\s*\)", spec_text), (
            "gludd.spec must call collect_data_files('ansible') to bundle "
            "ansible/config/base.yml and the rest of ansible's YAML data. "
            "Without it the binary crashes on startup with "
            "'Missing base YAML definition file (bad install?)'."
        )

    def test_ansible_datas_actually_passed_to_analysis(self, spec_text: str) -> None:
        """The ``collect_data_files('ansible')`` result MUST reach Analysis.

        PyInstaller only bundles what is in ``Analysis(datas=[...])``. A
        module-level ``datas = [...] + _ansible_datas`` variable is computed
        but IGNORED unless it is the same object passed to ``Analysis``.
        The v0.1.0-beta.1 crash returned because the spec computed the data
        and then handed Analysis a different (ansible-less) inline list.

        This test pins the fix: the Analysis ``datas=`` body must reference
        the ansible-data variable (or contain the collect_data_files call
        inline).
        """
        body = _analysis_datas_body(spec_text)
        assert body is not None, "gludd.spec must have an Analysis(...) block with a datas=[...] argument"
        # Accept either (a) the module-level variable pattern, or (b) an
        # inline collect_data_files('ansible') call inside Analysis(datas=).
        uses_module_var = bool(re.search(r"\b_ansible_datas\b", body))
        uses_module_datas_var = bool(re.search(r"\bdatas\b", body)) and bool(
            re.search(r"^datas\s*=\s*\[", spec_text, re.MULTILINE)
        )
        inline_collect = bool(re.search(r"collect_data_files\(\s*['\"]ansible['\"]\s*\)", body))
        assert uses_module_var or uses_module_datas_var or inline_collect, (
            "Analysis(datas=[...]) must include the ansible data — either by "
            "appending _ansible_datas, by passing the module-level `datas` "
            "variable, or by calling collect_data_files('ansible') inline. "
            "The current spec computes _ansible_datas but drops it before "
            "Analysis, so ansible/config/base.yml is NOT bundled. This is "
            "the root cause of the 'Missing base YAML definition file' crash."
        )

    def test_spec_uses_collect_submodules(self, spec_text: str) -> None:
        """Verify gludd.spec calls ``collect_submodules('ansible.module_utils')``.

        ansible performs dynamic imports inside module_utils, plugins,
        template, and galaxy. PyInstaller's static analyzer cannot follow
        importlib.import_module() calls, so each must be collected
        explicitly. Without this, the bundle's executor path silently
        fails the first time ansible resolves a module/action.
        """
        packages = _collect_submodule_packages(spec_text)
        assert "ansible.module_utils" in packages, (
            "gludd.spec must call collect_submodules('ansible.module_utils') "
            "to bundle ansible's dynamically-imported subpackage. The static "
            "analyzer misses these imports otherwise."
        )
        # The spec should also collect the other dynamically-loaded ansible
        # subpackages (plugins, template, galaxy) — these are the ones
        # core_runner.py and the executor touch at runtime.
        for sub in ("ansible.plugins", "ansible.template", "ansible.galaxy"):
            assert sub in packages, (
                f"gludd.spec must call collect_submodules('{sub}') — used at runtime by ansible's executor path."
            )

    def test_spec_does_not_exclude_ansible(self, spec_text: str) -> None:
        """Verify ansible is NOT in the excludes list (only ansible.cli is).

        ``ansible.cli`` is excluded because ``ansible.cli.initialize_locale()``
        hard-fails on Windows' cp1252 locale. The executor stack
        (ansible.parsing, ansible.executor, ansible.inventory, ansible.vars,
        ansible.template, ansible.plugins) MUST be bundled — core_runner.py
        drives it at runtime.
        """
        excluded = _excludes_from_spec(spec_text)
        # The whole 'ansible' package must NOT be excluded.
        assert "ansible" not in excluded, (
            "ansible must NOT be in excludes — gludd drives ansible-core's "
            "executor API (src/general_ludd/ansible/core_runner.py:77-83) at "
            "runtime. Excluding it strips the entire executor stack."
        )
        # Sanity: ansible.cli SHOULD remain excluded (Windows cp1252 issue).
        assert "ansible.cli" in excluded, (
            "ansible.cli MUST remain in excludes — "
            "ansible.cli.initialize_locale() hard-fails on Windows cp1252 "
            "locale ('Ansible requires UTF-8; Detected 1252')."
        )

    def test_spec_filters_nonexistent_ansible_distro_children(self, spec_text: str) -> None:
        """Ansible's distro compatibility package advertises two absent children."""
        assert "filter=_is_collectable_ansible_submodule" in spec_text
        assert "ansible.module_utils.distro.__main__" in spec_text
        assert "ansible.module_utils.distro.distro" in spec_text


class TestBuildWarningPrevention:
    """Intentional optional/platform imports must not pollute release builds."""

    def test_windows_ansible_collection_ignores_posix_import_failures(self, spec_text: str) -> None:
        """Windows discovery skips warnings from unsupported Ansible internals."""
        calls = _collect_submodule_calls(spec_text)
        assert calls
        for call in calls:
            on_error = next(
                (item.value for item in call.keywords if item.arg == "on_error"),
                None,
            )
            assert isinstance(on_error, ast.Name)
            assert on_error.id == "_ansible_collect_error_mode"

        assert 'sys.platform == "win32"' in spec_text
        assert '_ansible_collect_error_mode = "ignore"' in spec_text

    def test_windows_excludes_posix_only_stdlib_modules(self, spec_text: str) -> None:
        """Static analysis must not warn for stdlib modules absent on Windows."""
        assert 'sys.platform == "win32"' in spec_text
        for module in ("fcntl", "grp", "pty", "pwd", "resource", "termios", "tty"):
            assert f"'{module}'" in spec_text

    def test_unused_sqlalchemy_drivers_are_explicitly_excluded(self, spec_text: str) -> None:
        excluded = _excludes_from_spec(spec_text)
        assert {"pysqlite2", "MySQLdb"} <= excluded

    def test_non_windows_modules_have_platform_excludes(self, spec_text: str) -> None:
        assert 'sys.platform != "win32"' in spec_text
        for module in (
            "appdirs",
            "click._winconsole",
            "dateutil.tz.win",
            "platformdirs.windows",
            "prompt_toolkit.output.win32",
        ):
            assert module in spec_text


class TestOtherLibraryDataFiles:
    """Check other libraries that ship data files PyInstaller might miss.

    PyInstaller ships built-in hooks for the major libraries gludd uses
    (jinja2, pydantic, sqlalchemy, uvicorn). Those hooks auto-collect each
    library's standard data files, so explicit ``collect_data_files`` calls
    in gludd.spec are normally redundant.

    These tests are the structural pin: each library must (a) NOT be in the
    spec's excludes list and (b) NOT ship a runtime-critical YAML/JSON/cfg
    file that its built-in hook misses. If a future version of any library
    introduces such a file, the test surfaces it here so the spec can be
    updated before another "Missing base YAML definition file" class crash.
    """

    def test_jinja2_data_collected(self, spec_text: str) -> None:
        """Jinja2 ships test templates; runtime templates are Python-loaded.

        ``ansible.template.Templar`` (used by core_runner.py:79) wraps jinja2.
        PyInstaller's hook-jinja2.py auto-collects jinja2's data files.
        """
        excluded = _excludes_from_spec(spec_text)
        assert "jinja2" not in excluded, (
            "jinja2 must not be excluded — ansible's Templar uses jinja2 for playbook rendering at runtime."
        )
        critical = _find_runtime_data_files("jinja2")
        assert not critical, (
            "jinja2 ships runtime YAML/JSON/CFG files that PyInstaller's "
            f"built-in hook may miss: {critical}. gludd.spec must add "
            "collect_data_files('jinja2') to bundle them."
        )

    def test_pydantic_data_collected(self, spec_text: str) -> None:
        """Pydantic ships JSON schema files for its core schema model.

        gludd's models (e.g. AnsibleResult in core_runner.py:201) use pydantic.
        PyInstaller's hook-pydantic.py auto-collects pydantic's data files.
        """
        excluded = _excludes_from_spec(spec_text)
        assert "pydantic" not in excluded, (
            "pydantic must not be excluded — gludd's pydantic models use it "
            "at runtime for validation and JSON schema generation."
        )
        critical = _find_runtime_data_files("pydantic")
        # pydantic v2 ships a few non-test .pyi / .json files internally; the
        # schema-related .json files are read via importlib.resources, so a
        # built-in-hook miss would crash at first model_dump_json call.
        assert not critical, (
            "pydantic ships runtime YAML/JSON/CFG files that PyInstaller's "
            f"built-in hook may miss: {critical}. gludd.spec must add "
            "collect_data_files('pydantic') to bundle them."
        )

    def test_sqlalchemy_data_collected(self, spec_text: str) -> None:
        """SQLAlchemy may need plugin data files (e.g. dialect configs).

        gludd's db layer (db.models, db.repository) uses sqlalchemy.
        PyInstaller's hook-sqlalchemy.py auto-collects its data files.
        """
        excluded = _excludes_from_spec(spec_text)
        assert "sqlalchemy" not in excluded, (
            "sqlalchemy must not be excluded — gludd's db layer "
            "(db/models.py, db/repository.py) uses sqlalchemy at runtime."
        )
        critical = _find_runtime_data_files("sqlalchemy")
        assert not critical, (
            "sqlalchemy ships runtime YAML/JSON/CFG files that PyInstaller's "
            f"built-in hook may miss: {critical}. gludd.spec must add "
            "collect_data_files('sqlalchemy') to bundle them."
        )

    def test_uvicorn_data_collected(self, spec_text: str) -> None:
        """Uvicorn ships default config files and process-control submodules.

        gludd's worker uses uvicorn. The spec already lists uvicorn.loops,
        uvicorn.protocols, uvicorn.lifespan as hiddenimports (lines ~57-66).
        """
        excluded = _excludes_from_spec(spec_text)
        assert "uvicorn" not in excluded, (
            "uvicorn must not be excluded — gludd's worker uses uvicorn to serve the FastAPI app at runtime."
        )
        # Verify uvicorn's dynamic-import subpackages are in hiddenimports.
        # uvicorn uses importlib to load loops/protocols/lifespan based on
        # the --loop/--http/--lifespan flags; PyInstaller's static analyzer
        # misses these. The spec MUST list each as a hiddenimport.
        for required_hidden in ("uvicorn.loops", "uvicorn.protocols", "uvicorn.lifespan"):
            assert re.search(r"['\"]" + re.escape(required_hidden) + r"['\"]", spec_text), (
                f"gludd.spec must list '{required_hidden}' as a hiddenimport — "
                f"uvicorn resolves these dynamically at startup."
            )
        critical = _find_runtime_data_files("uvicorn")
        assert not critical, (
            "uvicorn ships runtime YAML/JSON/CFG files that PyInstaller's "
            f"built-in hook may miss: {critical}. gludd.spec must add "
            "collect_data_files('uvicorn') to bundle them."
        )


class TestCompatHiddenImports:
    """``general_ludd.compat`` shims are loaded dynamically at package init.

    ``src/general_ludd/__init__.py:19`` calls
    ``importlib.import_module("general_ludd.compat.annotated_types")`` to
    apply the Annotated-types runtime patch. PyInstaller's static analyzer
    does not follow ``importlib.import_module``, so the frozen CLI crashed
    with ``ModuleNotFoundError: No module named 'general_ludd.compat'``.

    These tests pin the fix: every module under ``src/general_ludd/compat/``
    must be listed in ``Analysis(hiddenimports=[...])``.
    """

    def test_compat_package_is_hiddenimport(self, spec_text: str) -> None:
        hidden = _hiddenimports_from_spec(spec_text)
        assert "general_ludd.compat" in hidden, (
            "gludd.spec must list 'general_ludd.compat' as a hiddenimport — "
            "src/general_ludd/__init__.py imports it dynamically at package "
            "init via importlib.import_module, which PyInstaller's static "
            "analyzer cannot follow. Without it the frozen CLI crashes with "
            "'ModuleNotFoundError: No module named general_ludd.compat'."
        )

    def test_every_compat_submodule_is_hiddenimport(self, spec_text: str) -> None:
        """Every shim on disk under src/general_ludd/compat/ must be declared.

        The discovery mirrors the spec's own semantics: any .py file in the
        compat package that is not an ``__init__.py`` is a submodule that
        must appear as a hiddenimport. A future shim added without updating
        the spec fails here instead of shipping a frozen-CLI crash.
        """
        hidden = _hiddenimports_from_spec(spec_text)
        missing = _compat_submodules() - hidden
        assert not missing, (
            "gludd.spec is missing hiddenimports for compat submodules: "
            f"{sorted(missing)}. Every module under src/general_ludd/compat/ "
            "must be declared in Analysis(hiddenimports=[...]) because the "
            "package is loaded via dynamic importlib.import_module calls "
            "(see src/general_ludd/__init__.py)."
        )
