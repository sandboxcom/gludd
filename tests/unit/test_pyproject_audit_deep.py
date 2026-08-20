"""Deep pyproject.toml integrity audit — 18 tests.

Covers: pinned versions, duplicate detection, entry-point resolution, classifier
validation, Python-version range, TOML parse, empty groups, version sync,
specifier validity, naming conventions, and config consistency.
"""

from __future__ import annotations

import importlib
import re
import tomllib
from itertools import combinations, product
from pathlib import Path

from packaging.markers import default_environment
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

ROOT = Path(__file__).parent.parent.parent
PYPROJECT = ROOT / "pyproject.toml"


def _load() -> dict:
    return tomllib.loads(PYPROJECT.read_text())


def _all_dep_names(section: list[str] | None) -> set[str]:
    if not section:
        return set()
    return {
        canonicalize_name(Requirement(spec).name)
        for spec in section
    }


def _all_dep_keys(section: list[str]) -> set[tuple[str, str]]:
    """Treat Python-marker splits as distinct, intentional requirements."""
    return {
        (
            canonicalize_name(requirement.name),
            str(requirement.marker or ""),
        )
        for spec in section
        if (requirement := Requirement(spec))
    }


def _marker_environments() -> list[dict[str, str]]:
    """Return supported Python/platform combinations for marker overlap checks."""
    base = default_environment()
    environments: list[dict[str, str]] = []
    platforms = (
        ("linux", "Linux", "posix"),
        ("darwin", "Darwin", "posix"),
        ("win32", "Windows", "nt"),
    )
    for minor, platform, machine, implementation in product(
        range(11, 31),
        platforms,
        ("x86_64", "arm64", "AMD64"),
        ("cpython", "pypy"),
    ):
        python_version = f"3.{minor}"
        environments.append(
            base
            | {
                "implementation_name": implementation,
                "os_name": platform[2],
                "platform_machine": machine,
                "platform_system": platform[1],
                "python_full_version": f"{python_version}.0",
                "python_version": python_version,
                "sys_platform": platform[0],
            }
        )
    return environments


_MARKER_ENVIRONMENTS = _marker_environments()


def _overlapping_requirement_names(section: list[str]) -> set[str]:
    """Find repeated distributions active in any supported environment."""
    requirements = [Requirement(spec) for spec in section]
    duplicates: set[str] = set()
    for left, right in combinations(requirements, 2):
        left_name = canonicalize_name(left.name)
        if left_name != canonicalize_name(right.name):
            continue
        if left.marker == right.marker or any(
            (left.marker is None or left.marker.evaluate(environment))
            and (right.marker is None or right.marker.evaluate(environment))
            for environment in _MARKER_ENVIRONMENTS
        ):
            duplicates.add(left_name)
    return duplicates


def test_duplicate_requirement_audit_distinguishes_disjoint_markers() -> None:
    """Marker-split pins are valid, while simultaneously active pins are not."""
    disjoint = [
        "ansible-core>=2.19,<2.20; python_version < '3.12'",
        "ansible-core>=2.21,<2.22; python_version >= '3.12'",
    ]
    overlapping = [
        "ansible-core>=2.20; python_version >= '3.11'",
        "ansible-core>=2.21; python_version >= '3.12'",
    ]

    assert _overlapping_requirement_names(disjoint) == set()
    assert _overlapping_requirement_names(overlapping) == {"ansible-core"}


def test_duplicate_requirement_audit_rejects_unmarked_duplicates() -> None:
    """Two unconditional pins for one distribution remain a hard failure."""
    assert _overlapping_requirement_names(["httpx>=0.27", "httpx<1"]) == {"httpx"}


# ---------------------------------------------------------------------------
# 1. TOML parse
# ---------------------------------------------------------------------------


def test_pyproject_is_valid_toml():
    data = _load()
    assert "project" in data
    assert "name" in data["project"]


# ---------------------------------------------------------------------------
# 2. Version consistency with __init__.py
# ---------------------------------------------------------------------------


def test_version_matches_init():
    data = _load()
    proj_version = data["project"]["version"]
    from general_ludd import __version__

    assert proj_version == __version__, f"pyproject={proj_version} init={__version__}"


# ---------------------------------------------------------------------------
# 3. Version is PEP 440 compliant (strict)
# ---------------------------------------------------------------------------


def test_project_version_is_pep440():
    from packaging.version import Version

    data = _load()
    Version(data["project"]["version"])


def test_requires_python_is_valid_specifier():
    from packaging.specifiers import SpecifierSet

    data = _load()
    SpecifierSet(data["project"]["requires-python"])


# ---------------------------------------------------------------------------
# 4. No duplicate dependencies within any section
# ---------------------------------------------------------------------------


def test_no_duplicate_core_deps():
    deps = _load()["project"]["dependencies"]
    keys = _all_dep_keys(deps)
    assert len(keys) == len(deps), (
        f"Duplicate in core deps: {len(deps)} specs → {len(keys)} name/marker pairs"
    )


def test_no_duplicate_optional_deps():
    data = _load()
    for group, deps in data["project"].get("optional-dependencies", {}).items():
        duplicates = _overlapping_requirement_names(deps)
        assert not duplicates, f"Overlapping requirements in [project.optional-dependencies] {group}: {duplicates}"


def test_no_duplicate_dependency_groups():
    data = _load()
    for group, deps in data.get("dependency-groups", {}).items():
        duplicates = _overlapping_requirement_names(deps)
        assert not duplicates, f"Overlapping requirements in [dependency-groups] {group}: {duplicates}"


# ---------------------------------------------------------------------------
# 5. All dependencies carry at least one version specifier
# ---------------------------------------------------------------------------

_VERSION_SPEC_RE = re.compile(r"[><=!~]")


def test_all_core_deps_have_version_spec():
    for spec in _load()["project"]["dependencies"]:
        assert _VERSION_SPEC_RE.search(spec), f"No version specifier in: {spec}"


def test_all_optional_deps_have_version_spec():
    data = _load()
    for group, deps in data["project"].get("optional-dependencies", {}).items():
        if not deps:
            continue
        for spec in deps:
            assert _VERSION_SPEC_RE.search(spec), f"No version specifier in [{group}] dep: {spec}"


def test_all_dependency_group_deps_have_version_spec():
    data = _load()
    for group, deps in data.get("dependency-groups", {}).items():
        if not deps:
            continue
        for spec in deps:
            assert _VERSION_SPEC_RE.search(spec), f"No version specifier in [dependency-groups] {group} dep: {spec}"


# ---------------------------------------------------------------------------
# 6. Entry point resolves
# ---------------------------------------------------------------------------


def test_cli_entry_point_resolves():
    data = _load()
    entry = data["project"]["scripts"]["gludd"]
    modname, _, callable_name = entry.partition(":")
    assert modname, f"Missing module name in entry point: {entry}"
    assert callable_name, f"Missing callable in entry point: {entry}"
    mod = importlib.import_module(modname)
    assert hasattr(mod, callable_name), f"{callable_name} not found in {modname}"
    assert callable(getattr(mod, callable_name))


# ---------------------------------------------------------------------------
# 7. All entry-point values have module:callable shape
# ---------------------------------------------------------------------------


def test_all_entry_points_well_formed():
    data = _load()
    for name, value in data["project"].get("scripts", {}).items():
        assert ":" in value, f"Entry point '{name}' missing colon: {value}"
        mod, _, func = value.partition(":")
        assert mod and func, f"Entry point '{name}' malformed: {value}"


# ---------------------------------------------------------------------------
# 8. Classifier validity (trove classifiers)
# ---------------------------------------------------------------------------

_TROVE_PREFIX = re.compile(
    r"^(?:Development Status|Environment|Framework|Intended Audience|"
    r"License|Natural Language|Operating System|Programming Language|Topic) ::"
)
_TROVE_DEV_STATUS = re.compile(r"^Development Status :: [1-7] -")


def test_classifiers_well_formed():
    data = _load()
    classifiers = data["project"].get("classifiers", [])
    for c in classifiers:
        assert _TROVE_PREFIX.match(c), f"Unknown trove prefix: {c}"
        if c.startswith("Development Status ::"):
            assert _TROVE_DEV_STATUS.match(c), f"Bad dev-status classifier: {c}"


# ---------------------------------------------------------------------------
# 9. Python version range
# ---------------------------------------------------------------------------


def test_requires_python_starts_at_311():
    data = _load()
    requires = data["project"]["requires-python"]
    assert requires == ">=3.11", f"Expected >=3.11, got {requires}"


def test_ruff_target_and_mypy_match_requires_python():
    data = _load()
    ruff = data.get("tool", {}).get("ruff", {}).get("target-version", "")
    mypy_ver = data.get("tool", {}).get("mypy", {}).get("python_version", "")
    assert ruff == "py311", f"ruff target-version {ruff} != py311"
    assert mypy_ver == "3.11", f"mypy python_version {mypy_ver} != 3.11"


# ---------------------------------------------------------------------------
# 10. No empty optional-dependency groups
# ---------------------------------------------------------------------------


_PLACEHOLDER_GROUPS = {"sandbox"}


def test_no_empty_optional_dependency_groups():
    data = _load()
    for group, deps in data["project"].get("optional-dependencies", {}).items():
        if group in _PLACEHOLDER_GROUPS:
            continue
        assert deps, f"Optional-dependency group [{group}] is empty"


# ---------------------------------------------------------------------------
# 11. Build system
# ---------------------------------------------------------------------------


def test_build_system_is_hatchling():
    data = _load()
    assert data["build-system"]["build-backend"] == "hatchling.build"
    assert "hatchling" in data["build-system"]["requires"]


# ---------------------------------------------------------------------------
# 12. Project name meets PEP 508
# ---------------------------------------------------------------------------

_PEP508_NAME = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9._-]*[A-Za-z0-9])?$")


def test_project_name_valid_pep508():
    name = _load()["project"]["name"]
    assert _PEP508_NAME.match(name), f"Project name '{name}' invalid for PEP 508"


def test_all_dep_names_valid_pep508():
    data = _load()
    all_sections: list[tuple[str, list[str]]] = [
        ("core", data["project"]["dependencies"]),
    ]
    for group, deps in data["project"].get("optional-dependencies", {}).items():
        all_sections.append((f"optional.{group}", deps))
    for group, deps in data.get("dependency-groups", {}).items():
        all_sections.append((f"group.{group}", deps))
    for section_name, deps in all_sections:
        for spec in deps:
            name = (
                spec.split("[")[0]
                .split("==")[0]
                .split(">=")[0]
                .split("<=")[0]
                .split("!=")[0]
                .split("~=")[0]
                .split("<")[0]
                .split(">")[0]
                .strip()
            )
            assert _PEP508_NAME.match(name), f"Bad name '{name}' in {section_name}: {spec}"


# ---------------------------------------------------------------------------
# 13. Dependency specifiers parse cleanly (PEP 508)
# ---------------------------------------------------------------------------


def test_core_dep_specifiers_parse():
    from packaging.requirements import Requirement

    for spec in _load()["project"]["dependencies"]:
        Requirement(spec)


def test_all_dep_specifiers_parse():
    from packaging.requirements import Requirement

    data = _load()
    for _group, deps in data["project"].get("optional-dependencies", {}).items():
        for spec in deps:
            Requirement(spec)
    for _group, deps in data.get("dependency-groups", {}).items():
        for spec in deps:
            Requirement(spec)


# ---------------------------------------------------------------------------
# 14. Core fields present + non-empty
# ---------------------------------------------------------------------------


def test_required_fields_present():
    data = _load()
    proj = data["project"]
    for field in ("name", "version", "description", "requires-python", "license"):
        assert proj.get(field), f"Missing required field: {field}"
    assert proj.get("authors"), "Missing authors"


# ---------------------------------------------------------------------------
# 15. e2e-all group is a superset of game-e2e group
# ---------------------------------------------------------------------------


def test_e2e_all_superset_of_game_e2e():
    data = _load()
    opt = data["project"]["optional-dependencies"]
    game = _all_dep_names(opt.get("game-e2e", []))
    e2e = _all_dep_names(opt.get("e2e-all", []))
    missing = game - e2e
    assert not missing, f"e2e-all missing game-e2e deps: {missing}"


# ---------------------------------------------------------------------------
# 16. coverage fail_under >= 80
# ---------------------------------------------------------------------------


def test_coverage_threshold_reasonable():
    data = _load()
    threshold = data.get("tool", {}).get("coverage", {}).get("report", {}).get("fail_under", 0)
    assert threshold >= 80, f"Coverage fail_under {threshold} < 80"


# ---------------------------------------------------------------------------
# 17. pytest timeout is non-zero
# ---------------------------------------------------------------------------


def test_pytest_timeout_set():
    data = _load()
    timeout = data.get("tool", {}).get("pytest", {}).get("ini_options", {}).get("timeout", 0)
    assert timeout > 0, f"pytest timeout is {timeout}"


# ---------------------------------------------------------------------------
# 18. hatch wheel include paths exist
# ---------------------------------------------------------------------------


def test_hatch_wheel_force_include_paths_exist():
    data = _load()
    force_include = (
        data.get("tool", {})
        .get("hatch", {})
        .get("build", {})
        .get("targets", {})
        .get("wheel", {})
        .get("force-include", {})
    )
    for src, _dest in (force_include or {}).items():
        assert (ROOT / src).exists(), f"hatch wheel force-include source missing: {src}"


# ---------------------------------------------------------------------------
# 19. Dependencies across sections — no core deps duplicated in dev (optional)
# ---------------------------------------------------------------------------


_EXPECTED_CROSS_GROUP_DUPES = {"aiosqlite"}


def test_core_deps_not_duplicated_in_dev():
    data = _load()
    core = _all_dep_names(data["project"]["dependencies"])
    dev = _all_dep_names(data["project"].get("optional-dependencies", {}).get("dev", []))
    dupes = core & dev
    unexpected = dupes - _EXPECTED_CROSS_GROUP_DUPES
    assert not unexpected, f"Core deps duplicated in dev (unexpected): {unexpected}"


# ---------------------------------------------------------------------------
# 20. ruff config: line-length is reasonable
# ---------------------------------------------------------------------------


def test_ruff_line_length_reasonable():
    data = _load()
    ll = data.get("tool", {}).get("ruff", {}).get("line-length", 0)
    assert 80 <= ll <= 200, f"ruff line-length {ll} out of bounds [80, 200]"


# ---------------------------------------------------------------------------
# 21. mypy strict mode is on
# ---------------------------------------------------------------------------


def test_mypy_strict_mode():
    data = _load()
    mypy = data.get("tool", {}).get("mypy", {})
    assert mypy.get("strict") is True, "mypy strict mode should be True"
    assert mypy.get("disallow_untyped_defs") is True
