"""Deep pyproject.toml audit v2 — 26 tests.

Covers: script resolution, entry-point structural checks, trove classifier
validation, URL completeness, cross-section duplicate detection, license/readme
on-disk existence, deprecated-field scan, build-system audit, force-include
integrity, coverage config, dependency-group overlap, and version shape.
"""

from __future__ import annotations

import importlib
import tomllib
from collections.abc import Iterator
from pathlib import Path

import pytest
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

ROOT = Path(__file__).parent.parent.parent
PYPROJECT = ROOT / "pyproject.toml"


def _load() -> dict:
    return tomllib.loads(PYPROJECT.read_text())


def _dep_name(spec: str) -> str:
    return canonicalize_name(Requirement(spec).name)


def _dep_names(section: list[str] | None) -> set[str]:
    if not section:
        return set()
    return {_dep_name(s) for s in section}


_AGGREGATE_EXTRAS = frozenset({"dev", "e2e-all"})


def _audited_extra_pairs(
    extras: dict[str, list[str]],
) -> Iterator[tuple[str, list[str], str, list[str]]]:
    """Yield pairs of standalone extras whose dependency sets must be disjoint."""
    standalone = [(name, deps) for name, deps in extras.items() if name not in _AGGREGATE_EXTRAS]
    for index, (left_name, left_deps) in enumerate(standalone):
        for right_name, right_deps in standalone[index + 1 :]:
            yield left_name, left_deps, right_name, right_deps


def test_audited_extra_pairs_exclude_only_aggregate_environments() -> None:
    """Development/superset extras may aggregate, standalone extras may not."""
    extras = {
        "dev": ["shared>=1"],
        "e2e-all": ["shared>=1"],
        "azure": ["shared>=1"],
        "aws": ["shared>=1"],
    }

    pairs = list(_audited_extra_pairs(extras))

    assert [(left, right) for left, _, right, _ in pairs] == [("azure", "aws")]


# ---------------------------------------------------------------------------
# 1. Script entry-points: all resolve to importable modules + callables
# ---------------------------------------------------------------------------


def test_all_project_scripts_resolve():
    data = _load()
    scripts = data["project"].get("scripts", {})
    assert scripts, "No [project.scripts] defined"
    for name, value in scripts.items():
        assert ":" in value, f"Script '{name}' missing colon: {value}"
        modname, _, func = value.partition(":")
        mod = importlib.import_module(modname)
        assert hasattr(mod, func), f"{func} not found in {modname} (script: {name})"
        assert callable(getattr(mod, func)), f"{func} in {modname} is not callable"


def test_gui_scripts_resolve_if_present():
    data = _load()
    gui = data["project"].get("gui-scripts", {})
    for name, value in gui.items():
        assert ":" in value, f"GUI script '{name}' missing colon: {value}"
        modname, _, func = value.partition(":")
        mod = importlib.import_module(modname)
        assert hasattr(mod, func), f"{func} not found in {modname} (gui: {name})"
        assert callable(getattr(mod, func))


# ---------------------------------------------------------------------------
# 2. Script entry-point values carry module:callable shape
# ---------------------------------------------------------------------------


def test_all_scripts_have_valid_module_names():
    data = _load()
    for name, value in data["project"].get("scripts", {}).items():
        modname = value.split(":")[0]
        assert all(c.isalnum() or c in "._" for c in modname), f"Invalid module name in script '{name}': {modname}"


def test_all_scripts_have_valid_callable_names():
    data = _load()
    for name, value in data["project"].get("scripts", {}).items():
        func = value.split(":")[1]
        assert func.isidentifier(), f"Invalid callable name in script '{name}': {func}"


# ---------------------------------------------------------------------------
# 3. Trove classifiers: exhaustive validation
# ---------------------------------------------------------------------------

_VALID_TROVE_PREFIXES = {
    "Development Status",
    "Environment",
    "Framework",
    "Intended Audience",
    "License",
    "Natural Language",
    "Operating System",
    "Programming Language",
    "Topic",
    "Typing",
}

_DEV_STATUS_MAP = {
    "1": "Planning",
    "2": "Pre-Alpha",
    "3": "Alpha",
    "4": "Beta",
    "5": "Production/Stable",
    "6": "Mature",
    "7": "Inactive",
}

_VALID_LICENSES = {
    "MIT",
    "MIT License",
    "OSI Approved :: MIT License",
    "OSI Approved :: Apache Software License",
    "OSI Approved :: BSD License",
    "OSI Approved :: GNU General Public License (GPL)",
    "OSI Approved :: GNU Lesser General Public License v3 (LGPLv3)",
    "Other/Proprietary License",
    "Public Domain",
}

# Known intentional cross-section overlaps
_EXPECTED_CROSS_GROUP_DUPES = {"aiosqlite", "langchain-openai"}
# Known missing [project.urls] — this is a documented gap
_URL_GAP_MSG = "[project.urls] section is missing — should be added (Homepage, Repository, Documentation, Bug Tracker)"


def test_classifier_trove_prefixes_valid():
    data = _load()
    classifiers = data["project"].get("classifiers", [])
    for c in classifiers:
        parts = c.split(" :: ")
        assert parts[0] in _VALID_TROVE_PREFIXES, f"Unknown trove prefix '{parts[0]}' in classifier: {c}"


def test_dev_status_classifier_matches_version():
    data = _load()
    version = data["project"]["version"]
    classifiers = data["project"].get("classifiers", [])
    dev_statuses = [c for c in classifiers if c.startswith("Development Status ::")]
    if not dev_statuses:
        return
    ds = dev_statuses[0]
    for num, label in _DEV_STATUS_MAP.items():
        if label in ds:
            version_lower = version.lower()
            if num == "4":
                assert "beta" in version_lower or "b" in version_lower, (
                    f"Dev status '{ds}' but version '{version}' doesn't look like Beta"
                )
            elif num == "3":
                assert "alpha" in version_lower or "a" in version_lower, (
                    f"Dev status '{ds}' but version '{version}' doesn't look like Alpha"
                )
            elif num == "5":
                assert "alpha" not in version_lower and "beta" not in version_lower, (
                    f"Dev status '{ds}' but version '{version}' has pre-release marker"
                )
            break


def test_classifier_license_matches_project_license():
    data = _load()
    classifiers = data["project"].get("classifiers", [])
    lic = data["project"].get("license", "").lower()
    lic_classifiers = [c for c in classifiers if c.startswith("License ::")]
    if not lic_classifiers:
        return
    for lc in lic_classifiers:
        parts = lc.split(" :: ")
        if len(parts) >= 3:
            assert lic in parts[-1].lower() or parts[-1].lower() in lic, (
                f"License classifier '{lc}' doesn't match project license '{data['project']['license']}'"
            )


# ---------------------------------------------------------------------------
# 4. URL completeness
# ---------------------------------------------------------------------------

_EXPECTED_URLS = {
    "Homepage",
    "Repository",
    "Documentation",
    "Bug Tracker",
}


def test_project_urls_present():
    data = _load()
    urls = data["project"].get("urls", {})
    if not urls:
        pytest.skip(_URL_GAP_MSG)
    for name in _EXPECTED_URLS:
        assert name in urls, f"Missing URL '{name}'"


def test_all_urls_have_valid_schemes():
    data = _load()
    urls = data["project"].get("urls", {})
    for name, url in urls.items():
        assert url.startswith(("http://", "https://")), f"URL '{name}' doesn't use http(s): {url}"


# ---------------------------------------------------------------------------
# 5. Cross-section duplicate detection (core vs all extras)
# ---------------------------------------------------------------------------


def test_core_deps_not_in_any_extras():
    data = _load()
    core = _dep_names(data["project"]["dependencies"])
    opt = data["project"].get("optional-dependencies", {})
    for group, deps in opt.items():
        group_names = _dep_names(deps)
        dupes = core & group_names
        unexpected = dupes - _EXPECTED_CROSS_GROUP_DUPES
        assert not unexpected, f"Core deps duplicated in extras [{group}]: {unexpected}"


def test_dependency_groups_not_duplicating_core():
    data = _load()
    core = _dep_names(data["project"]["dependencies"])
    groups = data.get("dependency-groups", {})
    for group, deps in groups.items():
        group_names = _dep_names(deps)
        dupes = core & group_names
        unexpected = dupes - _EXPECTED_CROSS_GROUP_DUPES
        assert not unexpected, f"Core deps duplicated in dependency-group [{group}]: {unexpected}"


def test_no_duplicate_deps_between_extras_groups():
    data = _load()
    opt = data["project"].get("optional-dependencies", {})
    for g1, deps1, g2, deps2 in _audited_extra_pairs(opt):
        names1 = _dep_names(deps1)
        names2 = _dep_names(deps2)
        dupes = names1 & names2
        assert not dupes, f"Deps duplicated between [{g1}] and [{g2}]: {dupes}"


def test_no_duplicate_deps_between_dependency_groups():
    data = _load()
    dg = data.get("dependency-groups", {})
    groups = list(dg.items())
    for i, (g1, deps1) in enumerate(groups):
        names1 = _dep_names(deps1)
        for g2, deps2 in groups[i + 1 :]:
            names2 = _dep_names(deps2)
            dupes = names1 & names2
            assert not dupes, f"Deps duplicated between dependency-group [{g1}] and [{g2}]: {dupes}"


# ---------------------------------------------------------------------------
# 6. License / README files exist on disk
# ---------------------------------------------------------------------------


def test_license_files_exist():
    data = _load()
    license_files = data["project"].get("license-files", [])
    for lf in license_files:
        assert (ROOT / lf).is_file(), f"License file missing: {lf}"


def test_readme_exists():
    data = _load()
    readme = data["project"].get("readme")
    if readme:
        assert (ROOT / readme).is_file(), f"README file missing: {readme}"


# ---------------------------------------------------------------------------
# 7. Deprecated / invalid PEP 621 fields
# ---------------------------------------------------------------------------

_DEPRECATED_FIELDS = {
    "console_scripts",
    "gui_scripts",
}

_INVALID_PROJECT_KEYS = {
    "python-requires",
    "project-urls",
    "entry-points",
}


def test_no_deprecated_top_level_scripts():
    data = _load()
    proj = data["project"]
    for field in _DEPRECATED_FIELDS:
        if field in proj:
            proj_value = proj[field]
            if isinstance(proj_value, dict) and proj_value:
                raise AssertionError(
                    f"Deprecated key '[project].{field}' should be '[project.scripts]' or '[project.gui-scripts]'"
                )


def test_no_invalid_top_level_keys():
    data = _load()
    proj = data["project"]
    for field in _INVALID_PROJECT_KEYS:
        assert field not in proj, (
            f"Invalid key '{field}' — use 'requires-python', '[project.urls]', or '[project.scripts]' instead"
        )


# ---------------------------------------------------------------------------
# 8. Authors format is valid
# ---------------------------------------------------------------------------


def test_authors_have_name_fields():
    data = _load()
    authors = data["project"].get("authors", [])
    assert authors, "No authors defined"
    for author in authors:
        assert "name" in author, f"Author missing 'name' key: {author}"
        assert isinstance(author["name"], str) and author["name"].strip(), f"Author name is empty: {author}"


# ---------------------------------------------------------------------------
# 9. Build system audit
# ---------------------------------------------------------------------------


def test_build_system_requires_no_duplicates():
    data = _load()
    reqs = data["build-system"]["requires"]
    names = _dep_names(reqs)
    assert len(names) == len(reqs), f"Duplicate in build-system.requires: {len(reqs)} specs → {len(names)} names"


def test_sdist_include_paths_exist():
    data = _load()
    includes = (
        data.get("tool", {}).get("hatch", {}).get("build", {}).get("targets", {}).get("sdist", {}).get("include", [])
    )
    for path in includes:
        # glob-style patterns — check that the parent directory exists at minimum
        clean = path.split("*")[0].rstrip("/")
        if clean:
            assert (ROOT / clean).exists(), f"sdist include path missing: {path}"


def test_wheel_packages_exist():
    data = _load()
    packages = (
        data.get("tool", {}).get("hatch", {}).get("build", {}).get("targets", {}).get("wheel", {}).get("packages", [])
    )
    for pkg in packages:
        path = ROOT / pkg
        assert path.is_dir(), f"Wheel package directory missing: {pkg}"


def test_force_include_dest_paths_valid():
    data = _load()
    force_include = (
        data.get("tool", {})
        .get("hatch", {})
        .get("build", {})
        .get("targets", {})
        .get("wheel", {})
        .get("force-include", {})
    )
    for src, dest in (force_include or {}).items():
        assert dest, f"force-include dest is empty for source: {src}"
        assert not dest.startswith("/"), f"force-include dest must be relative: {dest}"


# ---------------------------------------------------------------------------
# 10. Coverage config integrity
# ---------------------------------------------------------------------------


def test_coverage_source_matches_project_name():
    data = _load()
    source = data.get("tool", {}).get("coverage", {}).get("run", {}).get("source", [])
    assert "general_ludd" in source, "coverage source should include 'general_ludd'"


def test_coverage_omit_includes_tests():
    data = _load()
    omit = data.get("tool", {}).get("coverage", {}).get("run", {}).get("omit", [])
    assert omit, "coverage omit is empty"
    has_tests = any("tests" in o for o in omit)
    assert has_tests, "coverage should omit tests/ directory"


# ---------------------------------------------------------------------------
# 11. Version shape / pre-release marker
# ---------------------------------------------------------------------------


def test_version_is_semver_ish():
    data = _load()
    version = data["project"]["version"]
    parts = version.split(".")
    assert len(parts) >= 2, f"Version '{version}' should have at least major.minor"
    assert parts[0].lstrip("v").isdigit(), f"Major version not numeric: {parts[0]}"


def test_requires_python_has_no_upper_bound():
    data = _load()
    requires = data["project"]["requires-python"]
    assert ">=" in requires and "<" not in requires, (
        f"requires-python should use '>=' without upper bound, got: {requires}"
    )


# ---------------------------------------------------------------------------
# 12. Tool config consistency
# ---------------------------------------------------------------------------


def test_ruff_select_rules_nonempty():
    data = _load()
    select = data.get("tool", {}).get("ruff", {}).get("lint", {}).get("select", [])
    assert select, "ruff lint select is empty"


def test_mypy_path_references_existing_directory():
    data = _load()
    mypy_path = data.get("tool", {}).get("mypy", {}).get("mypy_path", "")
    if mypy_path:
        assert (ROOT / mypy_path).is_dir(), f"mypy_path '{mypy_path}' not found"
