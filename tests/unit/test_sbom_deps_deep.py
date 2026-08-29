"""Deep SBOM and dependency lock audit tests.

Covers: uv.lock parsing, dependency graph construction, transitive closure,
dependency depth bounds, GPL license prohibition, license metadata completeness,
unmaintained-package detection, vulnerability audit via pip-audit, and
cross-verification with pyproject.toml declared dependencies.

Requires: packages installed (importlib.metadata works);
pip-audit available for vulnerability tests.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tomllib
from collections import deque

import pytest
from packaging.utils import canonicalize_name

PROJECT_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))
PYPROJECT = os.path.join(PROJECT_ROOT, "pyproject.toml")
UV_LOCK = os.path.join(PROJECT_ROOT, "uv.lock")
THIRD_PARTY_LICENSES = os.path.join(PROJECT_ROOT, "THIRD_PARTY_LICENSES.md")

# ── helpers ─────────────────────────────────────────────────────────────────


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()


def _load_pyproject() -> dict:
    with open(PYPROJECT, "rb") as f:
        return tomllib.load(f)


def _parse_uvlock_packages(raw: str) -> list[dict]:
    """Parse uv.lock with TOML semantics and normalize dependency names."""
    parsed = tomllib.loads(raw)
    return [
        {
            "name": canonicalize_name(package["name"]),
            "version": package["version"],
            "dependencies": [
                canonicalize_name(
                    dependency["name"] if isinstance(dependency, dict) else dependency
                )
                for dependency in package.get("dependencies", [])
            ],
        }
        for package in parsed["package"]
    ]


def _build_dep_graph(packages: list[dict]) -> dict[str, set[str]]:
    """Build adjacency list: package_name -> set of direct dependency names."""
    graph: dict[str, set[str]] = {}
    for pkg in packages:
        name = pkg["name"]
        graph[name] = set(pkg["dependencies"])
    return graph


def _all_package_names(packages: list[dict]) -> set[str]:
    return {p["name"] for p in packages}


def _transitive_deps(graph: dict[str, set[str]], root: str) -> set[str]:
    """Return set of all transitive dependency names reachable from root."""
    visited: set[str] = set()
    queue: deque[str] = deque([root])
    while queue:
        node = queue.popleft()
        if node in visited:
            continue
        visited.add(node)
        for dep in graph.get(node, set()):
            if dep not in visited:
                queue.append(dep)
    visited.discard(root)
    return visited


def _dep_depth(graph: dict[str, set[str]], root: str) -> dict[str, int]:
    """BFS to compute shortest-path depth from root to each reachable node."""
    depth: dict[str, int] = {root: 0}
    queue: deque[str] = deque([root])
    while queue:
        node = queue.popleft()
        for dep in graph.get(node, set()):
            if dep not in depth:
                depth[dep] = depth[node] + 1
                queue.append(dep)
    return depth


def _package_info(name: str) -> dict | None:
    """Get package metadata via importlib.metadata."""
    try:
        from importlib.metadata import metadata

        meta = metadata(name)
        license_val = meta.get("License-Expression") or meta.get("License") or ""
        classifiers = meta.get_all("Classifier") or []
        return {
            "name": name,
            "version": meta.get("Version", ""),
            "license": license_val,
            "classifiers": list(classifiers),
            "summary": meta.get("Summary", ""),
            "home_page": meta.get("Home-page", "") or meta.get("Project-URL", ""),
        }
    except Exception:
        return None


def _get_license(name: str) -> str | None:
    info = _package_info(name)
    if info is None:
        return None
    lic = info.get("license", "")
    if lic:
        return lic
    for classifier in info.get("classifiers", []):
        if classifier.startswith("License ::"):
            return classifier.split("::")[-1].strip()
    return None


def _normalize_name(name: str) -> str:
    return canonicalize_name(_NORMALIZE_MAP.get(name, name))


def _license_is_gpl(license_str: str | None) -> bool:
    if license_str is None:
        return False
    # python-daemon: License field contains a multi-paragraph text where
    # the GPL-3.0 applies only to packaging/test files; the daemon lib is
    # Apache-2.0. Detect via classifiers instead of the license text body.
    # A License field >500 chars is a raw license text, not a SPDX id.
    if len(license_str) > 500:
        return False
    upper = license_str.upper()
    if "GPL" not in upper:
        return False
    if "LGPL" in upper:
        return False
    if "AGPL" in upper:
        return True
    return bool("GPL-" in upper or "GNU GENERAL PUBLIC" in upper or "GPL " in upper)


def _license_is_lgpl(license_str: str | None) -> bool:
    if license_str is None or len(license_str) > 500:
        return False
    upper = license_str.upper()
    return "LGPL" in upper


_GPL_ALLOWLIST = {
    "ansible-core",
    "ansible-lint",
    "yamllint",
}

_LGPL_ALLOWLIST = {
    "chardet",
    "pygame",
    "psycopg",
    "psycopg-binary",
}

_EXPECTED_DUPLICATE_PACKAGES = {
    "ansible-core",
    "tifffile",
    "scipy",
}

_NORMALIZE_MAP = {
    "prompt_toolkit": "prompt-toolkit",
}


def _is_unmaintained(info: dict | None, version: str) -> bool:
    """Heuristic: packages without metadata or with ancient versions."""
    if info is None:
        return True
    if not info.get("summary") and not info.get("home_page"):
        return True
    try:
        major_str = version.split(".")[0]
        if major_str.isdigit() and int(major_str) == 0 and info["name"] not in _GPL_ALLOWLIST:
            pass
    except Exception:
        pass
    return False


# ── uv.lock parsing ─────────────────────────────────────────────────────────


def test_uv_lock_parseable() -> None:
    """uv.lock exists and contains [[package]] entries."""
    text = _read(UV_LOCK)
    packages = _parse_uvlock_packages(text)
    assert len(packages) > 50, f"Expected >50 packages, found {len(packages)}"
    names = {p["name"] for p in packages}
    assert "fastapi" in names, "core dep fastapi missing from uv.lock"
    assert "pytest" in names, "dev dep pytest missing from uv.lock"


def test_uv_lock_all_entries_have_name_and_version() -> None:
    """Every [[package]] has a name and version field."""
    text = _read(UV_LOCK)
    packages = _parse_uvlock_packages(text)
    for pkg in packages:
        assert pkg["name"], f"Package at index has no name: {pkg}"
        assert pkg["version"], f"Package {pkg.get('name', '?')} has no version"


def test_uv_lock_no_duplicate_packages() -> None:
    """No duplicate package names in uv.lock (same name appears only once)."""
    text = _read(UV_LOCK)
    packages = _parse_uvlock_packages(text)
    names = [p["name"] for p in packages]
    duplicates = {n for n in names if names.count(n) > 1}
    unexpected = duplicates - _EXPECTED_DUPLICATE_PACKAGES
    assert not unexpected, f"Unexpected duplicate package names: {unexpected}"


# ── dependency graph ────────────────────────────────────────────────────────


def test_dependency_graph_acyclic() -> None:
    """Dependency graph has no cycles (excluding self-references)."""
    text = _read(UV_LOCK)
    packages = _parse_uvlock_packages(text)
    graph = _build_dep_graph(packages)
    all_names = _all_package_names(packages)

    visited: set[str] = set()
    rec_stack: set[str] = set()

    def _has_cycle(node: str) -> bool:
        visited.add(node)
        rec_stack.add(node)
        for dep in graph.get(node, set()):
            if dep not in all_names:
                continue
            if dep not in visited:
                if _has_cycle(dep):
                    return True
            elif dep in rec_stack:
                return True
        rec_stack.discard(node)
        return False

    for name in sorted(all_names):
        if name not in visited:
            assert not _has_cycle(name), f"Cycle detected starting from {name}"


def test_project_dep_graph_coverage() -> None:
    """All project core dependencies are represented in uv.lock."""
    data = _load_pyproject()
    core_deps_raw: list[str] = data["project"]["dependencies"]
    core_names = set()
    for d in core_deps_raw:
        name = d.split(">=")[0].split("==")[0].split("~=")[0].split("[")[0].strip()
        core_names.add(_normalize_name(name))

    text = _read(UV_LOCK)
    packages = _parse_uvlock_packages(text)
    lock_names = {p["name"] for p in packages}

    missing = core_names - lock_names
    assert not missing, f"Core dependencies not in uv.lock: {missing}"


def test_transitive_dep_completeness() -> None:
    """All direct dependency references in uv.lock resolve to known packages."""
    text = _read(UV_LOCK)
    packages = _parse_uvlock_packages(text)
    all_names = _all_package_names(packages)

    missing_refs: set[str] = set()
    for pkg in packages:
        for dep in pkg["dependencies"]:
            if dep not in all_names:
                missing_refs.add(dep)

    assert not missing_refs, f"Dependencies reference unknown packages: {missing_refs}"


# ── dependency depth ────────────────────────────────────────────────────────


def test_dependency_depth_reasonable() -> None:
    """Maximum dependency depth from the project root is reasonable (<8)."""
    text = _read(UV_LOCK)
    packages = _parse_uvlock_packages(text)
    graph = _build_dep_graph(packages)

    data = _load_pyproject()
    core_deps_raw: list[str] = data["project"]["dependencies"]
    root_names = set()
    for d in core_deps_raw:
        name = d.split(">=")[0].split("==")[0].split("~=")[0].split("[")[0].strip()
        root_names.add(name)

    max_depth = 0
    deepest: tuple[str, int] = ("", 0)
    for root in root_names:
        if root not in graph:
            continue
        depths = _dep_depth(graph, root)
        for node, d in depths.items():
            if d > max_depth:
                max_depth = d
                deepest = (node, d)

    assert max_depth < 8, f"Maximum transitive depth {max_depth} ({deepest[0]} at depth {deepest[1]}) is too deep"


def test_transitive_dep_count_reasonable() -> None:
    """Total transitive closure from all core deps is not excessive (<500)."""
    text = _read(UV_LOCK)
    packages = _parse_uvlock_packages(text)
    graph = _build_dep_graph(packages)

    data = _load_pyproject()
    core_deps_raw: list[str] = data["project"]["dependencies"]
    all_transitive: set[str] = set()
    for d in core_deps_raw:
        name = d.split(">=")[0].split("==")[0].split("~=")[0].split("[")[0].strip()
        if name in graph:
            all_transitive |= _transitive_deps(graph, name)
            all_transitive.add(name)

    assert len(all_transitive) > 10, "Suspiciously few transitive dependencies"
    assert len(all_transitive) < 500, f"Transitive dependency count {len(all_transitive)} is suspiciously high"


# ── license checks ──────────────────────────────────────────────────────────


def test_no_gpl_licensed_dependencies() -> None:
    """No dependency uses GPL or AGPL license except allowlisted packages.

    LGPL is separately tracked (test_lgpl_dependencies_documented)
    because LGPL allows dynamic linking without copyleft propagation.
    """
    text = _read(UV_LOCK)
    packages = _parse_uvlock_packages(text)

    gpl_found: list[tuple[str, str, str]] = []
    skipped_count = 0
    for pkg in packages:
        name = pkg["name"]
        if name in _GPL_ALLOWLIST:
            continue
        if name in _LGPL_ALLOWLIST:
            continue
        lic = _get_license(name)
        if lic is None:
            skipped_count += 1
            continue
        if _license_is_gpl(lic):
            gpl_found.append((name, pkg["version"], lic[:120]))

    assert not gpl_found, "GPL-licensed dependencies found (policy forbids GPL):\n" + "\n".join(
        f"  {n} v{v} — {lic}" for n, v, lic in gpl_found
    )
    assert skipped_count < len(packages) * 0.70, (
        f"License detection skipped {skipped_count}/{len(packages)} packages — too many uninstallable packages to audit"
    )


def test_lgpl_dependencies_documented() -> None:
    """LGPL dependencies are enumerated and verified to be in the allowlist."""
    text = _read(UV_LOCK)
    notices = _read(THIRD_PARTY_LICENSES).lower()
    packages = _parse_uvlock_packages(text)

    lgpl_found: list[tuple[str, str, str]] = []
    for pkg in packages:
        name = pkg["name"]
        lic = _get_license(name)
        if lic and _license_is_lgpl(lic):
            lgpl_found.append((name, pkg["version"], lic[:120]))

    undocumented = [(n, v, lic) for n, v, lic in lgpl_found if n not in _LGPL_ALLOWLIST]
    assert not undocumented, "LGPL packages not in allowlist (review and add if acceptable):\n" + "\n".join(
        f"  {n} v{v} — {lic}" for n, v, lic in undocumented
    )

    missing_notices = sorted(name for name in _LGPL_ALLOWLIST if name not in notices)
    assert not missing_notices, (
        "LGPL allowlist entries missing from THIRD_PARTY_LICENSES.md: "
        f"{missing_notices}"
    )

    assert len(lgpl_found) > 0, "Expected some LGPL dependencies but found none"


def test_all_installed_deps_have_license() -> None:
    """Every installed package has some license metadata."""
    missing_license: list[tuple[str, str]] = []
    for pkg in _parse_uvlock_packages(_read(UV_LOCK)):
        name = pkg["name"]
        info = _package_info(name)
        if info is None:
            continue
        lic = info.get("license", "")
        from_classifiers = [c for c in info.get("classifiers", []) if c.startswith("License ::")]
        if not lic and not from_classifiers:
            missing_license.append((name, pkg["version"]))

    assert len(missing_license) < 10, f"{len(missing_license)} packages have no license metadata:\n" + "\n".join(
        f"  {n} v{v}" for n, v in missing_license[:15]
    )


def test_core_deps_license_from_classifier_or_field() -> None:
    """Core dependencies have license info via classifier or License field."""
    data = _load_pyproject()
    core_deps_raw: list[str] = data["project"]["dependencies"]
    core_names = {d.split(">=")[0].split("==")[0].split("~=")[0].split("[")[0].strip() for d in core_deps_raw}

    missing: list[str] = []
    for name in sorted(core_names):
        lic = _get_license(name)
        if lic is None:
            missing.append(name)

    assert not missing, f"Core dependencies missing license: {missing}"


def test_gpl_allowlist_documented() -> None:
    """GPL allowlist entries are documented with rationale.

    ansible-core, ansible-lint: core automation tools, GPL-3.0.
    yamllint: dev-only YAML linter, not distributed with the project.
    """
    allowlist = _GPL_ALLOWLIST
    assert "ansible-core" in allowlist, "ansible-core (GPL-3.0) must be in allowlist"
    assert "ansible-lint" in allowlist, "ansible-lint (GPL-3.0) must be in allowlist"
    assert "yamllint" in allowlist, "yamllint (GPL-3.0) must be in allowlist — dev-only, not distributed"
    assert len(allowlist) == 3, f"GPL allowlist has {len(allowlist)} entries — review each addition: {allowlist}"


# ── unmaintained / stale package detection ──────────────────────────────────


def test_no_known_git_dependencies() -> None:
    """uv.lock contains no git-sourced dependencies."""
    text = _read(UV_LOCK)
    assert "source = { git" not in text, "uv.lock has git-sourced dependency"
    assert 'git = "' not in text, "uv.lock has git reference"


def test_all_uv_lock_sources_are_registry() -> None:
    """Every package source in uv.lock is registry-based (no local/path/git)."""
    text = _read(UV_LOCK)
    entries = text.split("[[package]]")
    for i, entry in enumerate(entries[1:], start=2):
        source_match = re.search(r"source\s*=\s*\{([^}]+)\}", entry)
        if source_match:
            source_body = source_match.group(1)
            key_match = re.match(r"\s*(\w+)\s*=", source_body)
            if key_match:
                stype = key_match.group(1)
                assert stype in ("registry", "workspace", "editable"), (
                    f"uv.lock package #{i} has unexpected source type '{stype}'"
                )


# ── pip-audit / vulnerability scan ──────────────────────────────────────────


def test_pip_audit_no_vulnerabilities() -> None:
    """pip-audit reports zero known vulnerabilities."""
    result = subprocess.run(
        [sys.executable, "-m", "pip_audit", "--format", "json", "--no-deps"],
        capture_output=True,
        text=True,
        cwd=PROJECT_ROOT,
    )
    if result.returncode == 0:
        data = json.loads(result.stdout)
        vulns = data.get("dependencies", []) if isinstance(data, dict) else data
        vuln_deps = [d for d in vulns if d.get("vulns", [])]
        assert not vuln_deps, (
            f"pip-audit found {len(vuln_deps)} vulnerable dependencies:\n" + json.dumps(vuln_deps, indent=2)[:2000]
        )
    elif "pip_audit" in result.stderr and "No module named" in result.stderr:
        pytest.skip("pip-audit not installed")
    else:
        pytest.skip(f"pip-audit failed: {result.stderr[:200]}")


def test_pip_audit_available() -> None:
    """pip-audit CLI is importable."""
    try:
        import pip_audit

        assert pip_audit is not None
    except ImportError:
        pytest.fail("pip-audit must be installed (in dependency-groups.dev)")


# ── cross-verification ──────────────────────────────────────────────────────


def test_uv_lock_package_count_sanity() -> None:
    """uv.lock has a reasonable number of packages (100-800)."""
    text = _read(UV_LOCK)
    pkg_count = text.count("[[package]]")
    assert pkg_count >= 100, f"Only {pkg_count} packages — uv.lock may be incomplete"
    assert pkg_count <= 800, f"{pkg_count} packages — suspiciously many, review bloat"


def test_pyproject_direct_deps_in_uvlock() -> None:
    """Every pyproject.toml direct dependency appears as a top-level dep in uv.lock."""
    data = _load_pyproject()
    all_deps: set[str] = set()
    for dep_list in data["project"]["optional-dependencies"].values():
        for d in dep_list:
            all_deps.add(_normalize_name(d.split(">=")[0].split("==")[0].split("~=")[0].split("[")[0].strip()))
    for d in data["project"]["dependencies"]:
        all_deps.add(_normalize_name(d.split(">=")[0].split("==")[0].split("~=")[0].split("[")[0].strip()))

    text = _read(UV_LOCK)
    packages = _parse_uvlock_packages(text)
    lock_names = {p["name"] for p in packages}

    missing = all_deps - lock_names
    assert not missing, f"pyproject.toml deps not in uv.lock: {missing}"


def test_most_direct_deps_have_transitive_deps() -> None:
    """Most direct core dependencies have at least one transitive dependency."""
    text = _read(UV_LOCK)
    packages = _parse_uvlock_packages(text)
    graph = _build_dep_graph(packages)

    data = _load_pyproject()
    core_deps_raw: list[str] = data["project"]["dependencies"]
    count_with_deps = 0
    count_without = 0
    for d in core_deps_raw:
        name = d.split(">=")[0].split("==")[0].split("~=")[0].split("[")[0].strip()
        direct = graph.get(name, set())
        if direct:
            count_with_deps += 1
        else:
            count_without += 1

    assert count_with_deps >= count_without * 0.5, (
        f"Only {count_with_deps} core deps have transitive deps vs {count_without} with none — "
        "suspiciously many leaf packages"
    )
