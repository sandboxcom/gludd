"""Deep versioning and dependency audit tests.

Covers: minimum-version pinning, git-source prohibition, extras validity,
Python-version alignment with CI, uv.lock sync, dependency-groups consistency,
and build-system integrity.
"""

from __future__ import annotations

import os
import re
import tomllib

import pytest

PROJECT_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))
PYPROJECT = os.path.join(PROJECT_ROOT, "pyproject.toml")
UV_LOCK = os.path.join(PROJECT_ROOT, "uv.lock")
BUILD_YML = os.path.join(PROJECT_ROOT, ".github", "workflows", "build.yml")
MOLECULE_YML = os.path.join(PROJECT_ROOT, ".github", "workflows", "molecule.yml")
PAGES_YML = os.path.join(PROJECT_ROOT, ".github", "workflows", "pages.yml")

# ── helpers ────────────────────────────────────────────────────────────────


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()


def _load_pyproject() -> dict:
    with open(PYPROJECT, "rb") as f:
        return tomllib.load(f)


def _load_uvlock() -> dict:
    """Load the standards-compliant TOML lockfile without a shadow parser."""
    with open(UV_LOCK, "rb") as f:
        return tomllib.load(f)


PEP440_RE = re.compile(
    r"^([1-9]\d*!)?(0|[1-9]\d*)(\.(0|[1-9]\d*))*"
    r"((a|b|rc)(0|[1-9]\d*))?(\.post(0|[1-9]\d*))?"
    r"(\.dev(0|[1-9]\d*))?$"
)

GIT_DEP_RE = re.compile(r"(git\+https?://|git://|git\+ssh://|git\+file://|@\s*git)")

# ── version pinning ────────────────────────────────────────────────────────


def test_all_core_deps_have_minimum_version() -> None:
    """Every core dependency has a version constraint (>=, ==, ~=, >)."""
    data = _load_pyproject()
    deps: list[str] = data["project"]["dependencies"]
    for dep in deps:
        if ">=" in dep or "==" in dep or "~=" in dep or ">" in dep or "<" in dep:
            continue
        # psycopg[binary] etc. have extras brackets — check after the bracket
        m = re.match(r"^([\w.-]+(?:\[[\w,\s-]+\])?)\s*([><=!~]+.*)$", dep)
        if m:
            continue
        # dependency-groups entries are plain strings like "package>=version"
        if dep.strip() == "":
            continue
        pytest.fail(f"Core dependency has no version constraint: '{dep}'")


def test_all_core_deps_have_specific_minimum() -> None:
    """Core deps pin a minimum version that is not a bare wildcard."""
    data = _load_pyproject()
    deps: list[str] = data["project"]["dependencies"]
    for dep in deps:
        m = re.search(r">=\s*([\d.*]+)", dep)
        if m:
            version = m.group(1)
            assert version != "", f"Dependency has empty >= version: '{dep}'"
            assert re.search(r"\d", version), f"Dependency >= has no digit: '{dep}'"
            continue
        m = re.search(r"==\s*([\d.*]+)", dep)
        if m:
            version = m.group(1)
            assert version != "", f"Dependency has empty == version: '{dep}'"
            assert re.search(r"\d", version), f"Dependency == has no digit: '{dep}'"
            continue
        m = re.search(r"~=\s*([\d.*]+)", dep)
        if m:
            version = m.group(1)
            assert version != "", f"Dependency has empty ~= version: '{dep}'"
            assert re.search(r"\d", version), f"Dependency ~= has no digit: '{dep}'"
            continue


def test_no_bare_dependency_names_in_core() -> None:
    """No core dependency is an unversioned bare name."""
    data = _load_pyproject()
    deps: list[str] = data["project"]["dependencies"]
    for dep in deps:
        dep = dep.strip()
        assert dep != "", "Empty dependency entry in [project.dependencies]"
        has_constraint = any(op in dep for op in (">=", "==", "~=", ">", "<", "!="))
        assert has_constraint, f"Dependency has no version constraint: '{dep}'"


def test_no_bare_dependency_names_in_dev() -> None:
    data = _load_pyproject()
    dev_deps: list[str] = data["project"].get("optional-dependencies", {}).get("dev", [])
    for dep in dev_deps:
        has_constraint = any(op in dep for op in (">=", "==", "~=", ">", "<", "!="))
        assert has_constraint, f"Dev dependency has no version constraint: '{dep}'"


def test_no_bare_dependency_names_in_dev_group() -> None:
    data = _load_pyproject()
    groups = data.get("dependency-groups", {})
    dev_group: list[str] = groups.get("dev", [])
    for dep in dev_group:
        has_constraint = any(op in dep for op in (">=", "==", "~=", ">", "<", "!="))
        assert has_constraint, f"dependency-groups.dev entry has no version constraint: '{dep}'"


def test_all_extras_have_minimum_versions() -> None:
    data = _load_pyproject()
    extras = data["project"].get("optional-dependencies", {})
    for extra_name, deps in extras.items():
        for dep in deps:
            if not dep.strip():
                continue
            has_constraint = any(op in dep for op in (">=", "==", "~=", ">", "<", "!="))
            assert has_constraint, f"Extra '{extra_name}' dependency has no version constraint: '{dep}'"


# ── git-source prohibition ─────────────────────────────────────────────────


def test_no_git_dependencies_in_pyproject() -> None:
    text = _read(PYPROJECT)
    assert not GIT_DEP_RE.search(text), "pyproject.toml contains a git-sourced dependency"


def test_no_git_dependencies_in_uv_lock() -> None:
    text = _read(UV_LOCK)
    assert "source = { git" not in text, "uv.lock contains a git-sourced dependency"
    assert not re.search(r'git\s*=\s*"', text), "uv.lock contains a git reference in a package entry"


# ── Python version constraints ─────────────────────────────────────────────


def test_requires_python_matches_ci_matrix() -> None:
    data = _load_pyproject()
    requires = data["project"]["requires-python"]  # ">=3.11"
    build_yml = _read(BUILD_YML)

    m = re.search(r">=(\d)\.(\d+)", requires)
    assert m, f"Could not parse requires-python '{requires}'"

    py_match = re.search(r'python-version:\s*\[(["\']3\.\d+["\'].*?)\]', build_yml)
    assert py_match, "Could not find python-version matrix in build.yml"
    ci_versions_raw = py_match.group(1)
    ci_versions = re.findall(r'["\'](3\.\d+)["\']', ci_versions_raw)
    assert ci_versions, f"No Python versions found in CI matrix: {ci_versions_raw}"

    ci_minor_versions = {int(v.split(".")[1]) for v in ci_versions}
    requires_minor = int(m.group(2))
    for v in ci_minor_versions:
        assert v >= requires_minor, f"CI tests Python 3.{v} but requires-python >= 3.{requires_minor}"


def test_ci_matrix_includes_min_python() -> None:
    data = _load_pyproject()
    requires = data["project"]["requires-python"]
    m = re.search(r">=(\d)\.(\d+)", requires)
    assert m, f"Could not parse requires-python: {requires}"
    min_ver = f"3.{m.group(2)}"

    build_yml = _read(BUILD_YML)
    all_ver = set(re.findall(r'"3\.\d+"', build_yml))
    assert min_ver in all_ver or f'"{min_ver}"' in all_ver, (
        f"CI matrix must include minimum Python {min_ver}; found: {sorted(all_ver)}"
    )


def test_uv_lock_requires_python_matches_pyproject() -> None:
    data = _load_pyproject()
    requires = data["project"]["requires-python"]
    uvlock = _load_uvlock()
    assert "requires-python" in uvlock, "uv.lock missing requires-python"
    assert uvlock["requires-python"] == requires, (
        f"uv.lock requires-python '{uvlock['requires-python']}' != pyproject.toml requires-python '{requires}'"
    )


def test_ruff_target_version_matches_min_python() -> None:
    data = _load_pyproject()
    ruff_target = data.get("tool", {}).get("ruff", {}).get("target-version", "")
    assert ruff_target in ("py311", "py312"), f"ruff target-version '{ruff_target}' should be py311 or py312"


def test_mypy_python_version_matches_min_python() -> None:
    data = _load_pyproject()
    mypy_ver = data.get("tool", {}).get("mypy", {}).get("python_version", "")
    assert mypy_ver == "3.11", f"mypy python_version '{mypy_ver}' should be 3.11"


# ── uv.lock integrity ──────────────────────────────────────────────────────


def test_uv_lock_version_format() -> None:
    text = _read(UV_LOCK)
    m = re.match(r"version\s*=\s*(\d+)", text)
    assert m, "uv.lock missing 'version = N' header"
    assert int(m.group(1)) >= 1, f"uv.lock version {m.group(1)} < 1"


def test_uv_lock_has_packages() -> None:
    text = _read(UV_LOCK)
    pkg_count = text.count("[[package]]")
    assert pkg_count > 10, f"uv.lock has only {pkg_count} packages — likely corrupted"


def test_uv_lock_staleness_signals() -> None:
    """uv.lock has resolution-markers and [[package]] entries."""
    uvlock = _load_uvlock()
    assert "version" in uvlock, "uv.lock missing 'version' field"
    assert "requires-python" in uvlock, "uv.lock missing 'requires-python' field"
    assert "package" in uvlock, "uv.lock missing [[package]] entries"


def test_uv_lock_package_sources_are_registry_or_workspace() -> None:
    for package in _load_uvlock()["package"]:
        source = package.get("source", {})
        source_types = set(source)
        assert source_types <= {"registry", "workspace", "path", "editable"}, (
            f"{package['name']} has disallowed source fields {sorted(source_types)}"
        )
        if "editable" in source:
            assert package["name"] == "general-ludd-agent"
            assert source["editable"] == "."


# ── extras validity ────────────────────────────────────────────────────────


def test_all_extras_are_valid() -> None:
    data = _load_pyproject()
    extras = data["project"].get("optional-dependencies", {})
    for extra_name in extras:
        assert re.match(r"^[a-z][a-z0-9-]*$", extra_name), (
            f"Extra name '{extra_name}' is not a valid lowercase hyphenated name"
        )


def test_uv_lock_provides_extras_matches_pyproject() -> None:
    data = _load_pyproject()
    pyproject_extras = set(data["project"].get("optional-dependencies", {}).keys())
    text = _read(UV_LOCK)
    m = re.search(r"provides-extras\s*=\s*\[([^\]]+)\]", text)
    assert m, "uv.lock missing provides-extras for the project"
    uv_extras_raw = m.group(1)
    uv_extras = {s.strip().strip('"').strip("'") for s in uv_extras_raw.split(",")}
    missing = pyproject_extras - uv_extras
    assert not missing, f"pyproject.toml extras not in uv.lock provides-extras: {missing}"
    extra = uv_extras - pyproject_extras
    assert not extra, f"uv.lock provides-extras has extras not in pyproject.toml: {extra}"


def test_empty_sandbox_extra_still_defined() -> None:
    """The 'sandbox' extra exists in pyproject.toml and uv.lock even though empty."""
    data = _load_pyproject()
    extras = data["project"].get("optional-dependencies", {})
    assert "sandbox" in extras, "sandbox extra missing from pyproject.toml"
    assert extras["sandbox"] == [] or extras["sandbox"] == [""], "sandbox extra should be empty list"


def test_e2e_all_includes_game_e2e_deps() -> None:
    """The e2e-all extra should be a superset of game-e2e."""
    data = _load_pyproject()
    extras = data["project"]["optional-dependencies"]
    game_deps = set(dep.split(">=")[0].split("==")[0].strip() for dep in extras.get("game-e2e", []))
    e2e_deps = set(dep.split(">=")[0].split("==")[0].strip() for dep in extras.get("e2e-all", []))
    missing = game_deps - e2e_deps
    assert not missing, f"e2e-all missing game-e2e dependencies: {missing}"


# ── build-system ───────────────────────────────────────────────────────────


def test_build_system_is_hatchling() -> None:
    data = _load_pyproject()
    bs = data.get("build-system", {})
    assert "requires" in bs, "build-system missing 'requires'"
    assert "hatchling" in bs["requires"], f"build-system requires should include hatchling: {bs['requires']}"
    assert bs.get("build-backend") == "hatchling.build", (
        f"build-backend should be hatchling.build: {bs.get('build-backend')}"
    )


# ── dependency-groups consistency ──────────────────────────────────────────


def test_dependency_groups_are_not_empty() -> None:
    data = _load_pyproject()
    groups = data.get("dependency-groups", {})
    assert groups, "dependency-groups section is missing or empty"
    for name, deps in groups.items():
        assert deps, f"dependency-groups.{name} is empty"


def test_dependency_groups_dev_overlaps_optional_dev() -> None:
    data = _load_pyproject()
    groups = data.get("dependency-groups", {})
    dev_group = {
        dep.split(">=")[0].split("==")[0].split("~=")[0].split("[")[0].strip() for dep in groups.get("dev", [])
    }
    opt_dev = {
        dep.split(">=")[0].split("==")[0].split("~=")[0].split("[")[0].strip()
        for dep in data["project"]["optional-dependencies"].get("dev", [])
    }
    overlap = dev_group & opt_dev
    assert len(overlap) >= 12, (
        f"dependency-groups.dev and optional-dependencies.dev share only "
        f"{len(overlap)} packages (overlap={sorted(overlap)}) — "
        f"expected >=12 core dev tools in common"
    )


def test_dev_deps_include_test_tooling() -> None:
    data = _load_pyproject()
    all_dev = set()
    for dep in data["project"]["optional-dependencies"].get("dev", []):
        all_dev.add(dep.split(">=")[0].split("==")[0].split("[")[0].strip())
    for dep in data.get("dependency-groups", {}).get("dev", []):
        all_dev.add(dep.split(">=")[0].split("==")[0].split("[")[0].strip())

    essential = {"pytest", "ruff", "mypy", "pre-commit"}
    missing = essential - all_dev
    assert not missing, f"Essential dev tooling missing: {missing}"


def test_dev_deps_include_security_tooling() -> None:
    data = _load_pyproject()
    all_dev = set()
    for dep in data["project"]["optional-dependencies"].get("dev", []):
        all_dev.add(dep.split(">=")[0].split("==")[0].split("[")[0].strip())
    for dep in data.get("dependency-groups", {}).get("dev", []):
        all_dev.add(dep.split(">=")[0].split("==")[0].split("[")[0].strip())

    security = {"bandit", "pip-audit", "detect-secrets"}
    missing = security - all_dev
    assert not missing, f"Essential security tooling missing from dev deps: {missing}"


# ── version consistency ────────────────────────────────────────────────────


def test_pyproject_version_is_pep440() -> None:
    data = _load_pyproject()
    version = data["project"]["version"]
    # Allow calver or semver with pre-release
    assert not version.startswith("v"), f"Version '{version}' should not have 'v' prefix in pyproject.toml"
    calver_or_semver = re.match(r"^\d+\.\d+\.\d+(?:-[a-z]+\.\d+)?$", version)
    assert calver_or_semver, f"Version '{version}' is not valid semver/calver format"


def test_pyproject_dependency_count_sanity() -> None:
    data = _load_pyproject()
    deps = data["project"]["dependencies"]
    assert len(deps) >= 20, f"Only {len(deps)} core dependencies — suspiciously low"
    assert len(deps) <= 150, f"{len(deps)} core dependencies — suspiciously high"


def test_dev_extras_count_sanity() -> None:
    data = _load_pyproject()
    dev_deps = data["project"].get("optional-dependencies", {}).get("dev", [])
    assert len(dev_deps) >= 10, f"Only {len(dev_deps)} dev dependencies — suspiciously low"


def test_makefile_defines_version_variable() -> None:
    text = _read(os.path.join(PROJECT_ROOT, "Makefile"))
    assert "VERSION" in text, "Makefile should define VERSION variable"


def test_makefile_has_check_version_consistency() -> None:
    text = _read(os.path.join(PROJECT_ROOT, "Makefile"))
    assert "check-version-consistency" in text, "Makefile missing check-version-consistency target"


def test_makefile_has_bump_version() -> None:
    text = _read(os.path.join(PROJECT_ROOT, "Makefile"))
    assert "bump-version" in text, "Makefile missing bump-version target"
