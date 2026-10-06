"""Deep versioning and dependency audit tests.

Covers: minimum-version pinning, git-source prohibition, dependency-profile
validity, Python-version alignment with CI, uv.lock sync, profile-set
consistency, and build-system integrity.
"""

from __future__ import annotations

import os
import re
import tomllib
from pathlib import Path

import pytest
from scripts.makefile_layout import compose_makefile

PROJECT_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))
PYPROJECT = os.path.join(PROJECT_ROOT, "pyproject.toml")
UV_LOCK = os.path.join(PROJECT_ROOT, "uv.lock")
BUILD_YML = os.path.join(PROJECT_ROOT, ".github", "workflows", "build.yml")
MOLECULE_YML = os.path.join(PROJECT_ROOT, ".github", "workflows", "molecule.yml")
PAGES_YML = os.path.join(PROJECT_ROOT, ".github", "workflows", "pages.yml")
PROFILE_CATALOG = os.path.join(PROJECT_ROOT, "config", "dependency_profiles.toml")

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


def _load_toml(path: str) -> dict:
    with open(path, "rb") as f:
        return tomllib.load(f)


def _profile_dependencies(name: str) -> list[str]:
    catalog = _load_toml(PROFILE_CATALOG)
    project = catalog["profiles"][name]["project"]
    return _load_toml(os.path.join(PROJECT_ROOT, project, "pyproject.toml"))["project"][
        "dependencies"
    ]


def _set_dependencies(name: str) -> list[str]:
    catalog = _load_toml(PROFILE_CATALOG)
    dependencies = list(_load_pyproject()["project"]["dependencies"])
    for profile in catalog["sets"][name]["profiles"]:
        dependencies.extend(_profile_dependencies(profile))
    return dependencies


def _all_profile_projects() -> list[dict]:
    catalog = _load_toml(PROFILE_CATALOG)
    return [
        _load_toml(os.path.join(PROJECT_ROOT, metadata["project"], "pyproject.toml"))
        for metadata in catalog["profiles"].values()
    ]


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
    dev_deps = _set_dependencies("development")
    for dep in dev_deps:
        has_constraint = any(op in dep for op in (">=", "==", "~=", ">", "<", "!="))
        assert has_constraint, f"Dev dependency has no version constraint: '{dep}'"


def test_no_bare_dependency_names_in_dev_group() -> None:
    dev_group = _set_dependencies("development")
    for dep in dev_group:
        has_constraint = any(op in dep for op in (">=", "==", "~=", ">", "<", "!="))
        assert has_constraint, f"dependency-groups.dev entry has no version constraint: '{dep}'"


def test_all_extras_have_minimum_versions() -> None:
    catalog = _load_toml(PROFILE_CATALOG)
    for extra_name in catalog["profiles"]:
        deps = _profile_dependencies(extra_name)
        for dep in deps:
            if not dep.strip():
                continue
            has_constraint = any(op in dep for op in (">=", "==", "~=", ">", "<", "!="))
            assert has_constraint, f"Extra '{extra_name}' dependency has no version constraint: '{dep}'"


# ── git-source prohibition ─────────────────────────────────────────────────


def test_no_git_dependencies_in_pyproject() -> None:
    projects = [_load_pyproject(), *_all_profile_projects()]
    for project in projects:
        dependencies = project["project"]["dependencies"]
        assert not GIT_DEP_RE.search("\n".join(dependencies)), (
            f"{project['project']['name']} contains a git-sourced dependency"
        )


def test_no_git_dependencies_in_uv_lock() -> None:
    catalog = _load_toml(PROFILE_CATALOG)
    locks = [UV_LOCK]
    locks.extend(
        os.path.join(PROJECT_ROOT, metadata["project"], "uv.lock")
        for metadata in catalog["profiles"].values()
    )
    for lock in locks:
        text = _read(lock)
        assert "source = { git" not in text, f"{lock} contains a git-sourced dependency"
        assert not re.search(r'git\s*=\s*"', text), (
            f"{lock} contains a git reference in a package entry"
        )


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
    catalog = _load_toml(PROFILE_CATALOG)
    for extra_name in (*catalog["profiles"], *catalog["sets"]):
        assert re.match(r"^[a-z][a-z0-9-]*$", extra_name), (
            f"Extra name '{extra_name}' is not a valid lowercase hyphenated name"
        )


def test_every_profile_project_has_an_independent_lock() -> None:
    catalog = _load_toml(PROFILE_CATALOG)
    for name, metadata in catalog["profiles"].items():
        lock = os.path.join(PROJECT_ROOT, metadata["project"], "uv.lock")
        assert os.path.isfile(lock), f"profile {name} is missing its independent lock"
        assert _load_toml(lock)["package"], f"profile {name} lock is empty"


def test_sandbox_set_is_still_defined() -> None:
    catalog = _load_toml(PROFILE_CATALOG)
    assert catalog["sets"]["sandbox"]["profiles"] == ["agent-runtime"]


def test_e2e_all_includes_game_e2e_profiles() -> None:
    catalog = _load_toml(PROFILE_CATALOG)
    game_profiles = set(catalog["sets"]["game-e2e"]["profiles"])
    e2e_profiles = set(catalog["sets"]["e2e-all"]["profiles"])
    assert game_profiles <= e2e_profiles


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


def test_development_profile_projects_are_not_empty() -> None:
    catalog = _load_toml(PROFILE_CATALOG)
    for name in catalog["sets"]["development"]["profiles"]:
        assert _profile_dependencies(name), f"development profile {name} is empty"


def test_ci_set_contains_the_complete_development_set() -> None:
    catalog = _load_toml(PROFILE_CATALOG)
    development = set(catalog["sets"]["development"]["profiles"])
    ci = set(catalog["sets"]["ci"]["profiles"])
    assert development < ci


def test_dev_deps_include_test_tooling() -> None:
    all_dev = {
        dep.split(">=")[0].split("==")[0].split("[")[0].strip()
        for dep in _set_dependencies("development")
    }

    essential = {"pytest", "ruff", "mypy", "pre-commit"}
    missing = essential - all_dev
    assert not missing, f"Essential dev tooling missing: {missing}"


def test_dev_deps_include_security_tooling() -> None:
    all_dev = {
        dep.split(">=")[0].split("==")[0].split("[")[0].strip()
        for dep in _set_dependencies("development")
    }

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
    dev_deps = _set_dependencies("development")
    assert len(dev_deps) >= 10, f"Only {len(dev_deps)} dev dependencies — suspiciously low"


def test_makefile_defines_version_variable() -> None:
    text = compose_makefile(Path(PROJECT_ROOT) / "Makefile")
    assert "VERSION" in text, "Makefile should define VERSION variable"


def test_makefile_has_check_version_consistency() -> None:
    text = compose_makefile(Path(PROJECT_ROOT) / "Makefile")
    assert "check-version-consistency" in text, "Makefile missing check-version-consistency target"


def test_makefile_has_bump_version() -> None:
    text = compose_makefile(Path(PROJECT_ROOT) / "Makefile")
    assert "bump-version" in text, "Makefile missing bump-version target"
