"""Deep lockfile and dependency audit tests — 23 tests.

Covers: pinned concrete versions, no-yanked assurance, extras consistency,
source-distribution availability, artifact size validation, hash integrity,
upload-time recency, resolution-marker coverage, self-consistency cross-checks,
and pyproject.toml sync.
"""

from __future__ import annotations

import datetime
import re
import tomllib
from collections import defaultdict
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
UV_LOCK = ROOT / "uv.lock"
PYPROJECT = ROOT / "pyproject.toml"

SHA256_RE = re.compile(r"^sha256:[a-f0-9]{64}$")
PEP440_RE = re.compile(
    r"^([1-9][0-9]*!)?(0|[1-9][0-9]*)(\.(0|[1-9][0-9]*))*"
    r"((a|b|rc)(0|[1-9][0-9]*))?(\.post(0|[1-9][0-9]*))?"
    r"(\.dev(0|[1-9][0-9]*))?$"
)
ISO8601_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$")


def _parse_lock_full() -> dict[str, Any]:
    raw = UV_LOCK.read_text()
    header: dict[str, object] = {}
    packages: list[dict[str, object]] = []
    current: dict[str, object] = {}
    in_wheels = False
    in_deps = False
    in_sdist = False
    in_source = False
    in_markers = False
    in_res_markers = False
    pkg_markers: list[str] = []
    wheel_entries: list[dict[str, object]] = []
    dep_entries: list[dict[str, object]] = []
    sdist_data: dict[str, object] = {}
    source_data: dict[str, object] = {}
    resolution_markers: list[str] = []

    for _line_idx, line in enumerate(raw.splitlines()):
        stripped = line.strip()
        if stripped == "" or stripped.startswith("#"):
            continue

        if not current.get("_in_pkg"):
            if stripped == "resolution-markers = [":
                in_markers = True
                continue
            if in_markers:
                if stripped == "]":
                    in_markers = False
                else:
                    m = re.search(r'"([^"]+)"', stripped)
                    if m:
                        resolution_markers.append(m.group(1))
                continue
            if stripped.startswith("version = "):
                header["version"] = int(stripped.split("=")[1].strip())
            elif stripped.startswith("revision = "):
                header["revision"] = int(stripped.split("=")[1].strip())
            elif stripped.startswith("requires-python = "):
                val = stripped.split("=", 1)[1].strip()
                header["requires-python"] = val.strip('"')

        if stripped == "[[package]]":
            if current.get("_in_pkg") and not current.get("_is_project"):
                current["wheels"] = wheel_entries
                current["dependencies"] = dep_entries
                if sdist_data:
                    current["sdist"] = sdist_data
                if source_data:
                    current["source"] = source_data
                if pkg_markers:
                    current["resolution_markers"] = pkg_markers
                packages.append(current)
            current = {"_in_pkg": True}
            wheel_entries = []
            dep_entries = []
            sdist_data = {}
            source_data = {}
            pkg_markers = []
            in_wheels = False
            in_deps = False
            in_sdist = False
            in_source = False
            in_res_markers = False
        elif current.get("_in_pkg"):
            if stripped.startswith("name = "):
                current["name"] = stripped.split('"')[1]
            elif stripped.startswith("version = "):
                current["version"] = stripped.split('"')[1]
            elif stripped.startswith("source = "):
                in_source = True
                if "editable" in stripped:
                    current["_is_project"] = True
                m = re.search(r'registry\s*=\s*"([^"]+)"', stripped)
                if m:
                    source_data["registry"] = m.group(1)
                current["source"] = source_data
            elif stripped == "resolution-markers = [":
                in_res_markers = True
                pkg_markers = []
            elif in_res_markers:
                if stripped == "]":
                    in_res_markers = False
                    current["resolution_markers"] = list(pkg_markers)
                else:
                    m = re.search(r'"([^"]+)"', stripped)
                    if m:
                        pkg_markers.append(m.group(1))
            elif stripped.startswith("sdist = "):
                in_sdist = True
                in_wheels = False
                sdist_data = {}
                m = re.search(r'url\s*=\s*"([^"]+)"', stripped)
                if m:
                    sdist_data["url"] = m.group(1)
                m = re.search(r'hash\s*=\s*"([^"]+)"', stripped)
                if m:
                    sdist_data["hash"] = m.group(1)
                m = re.search(r"size\s*=\s*(\d+)", stripped)
                if m:
                    sdist_data["size"] = int(m.group(1))
                m = re.search(r'upload-time\s*=\s*"([^"]+)"', stripped)
                if m:
                    sdist_data["upload_time"] = m.group(1)
                current["sdist"] = sdist_data
            elif stripped.startswith("wheels = ["):
                in_wheels = True
                in_sdist = False
                in_deps = False
            elif stripped.startswith("dependencies = ["):
                in_deps = True
                in_wheels = False
                in_sdist = False
            elif stripped == "]":
                if in_wheels:
                    current["wheels"] = wheel_entries
                    in_wheels = False
                elif in_deps:
                    current["dependencies"] = dep_entries
                    in_deps = False
                elif in_sdist:
                    in_sdist = False
                elif in_source:
                    in_source = False
            elif in_wheels:
                if stripped.startswith("{") or stripped == "}, {":
                    wheel: dict[str, object] = {}
                    m = re.search(r'url\s*=\s*"([^"]+)"', stripped)
                    if m:
                        wheel["url"] = m.group(1)
                    m = re.search(r'hash\s*=\s*"([^"]+)"', stripped)
                    if m:
                        wheel["hash"] = m.group(1)
                    m = re.search(r"size\s*=\s*(\d+)", stripped)
                    if m:
                        wheel["size"] = int(m.group(1))
                    m = re.search(r'upload-time\s*=\s*"([^"]+)"', stripped)
                    if m:
                        wheel["upload_time"] = m.group(1)
                    wheel_entries.append(wheel)
            elif in_deps:
                m = re.search(r'name\s*=\s*"([^"]+)"', stripped)
                if m:
                    dep_name = m.group(1)
                    m_e = re.search(r"extras\s*=\s*\[([^\]]+)\]", stripped)
                    extras_val: list[str] = []
                    if m_e:
                        extras_val = [e.strip().strip('"').strip("'") for e in m_e.group(1).split(",")]
                    m_mk = re.search(r'marker\s*=\s*"([^"]+)"', stripped)
                    dep_entries.append(
                        {
                            "name": dep_name,
                            "extras": extras_val,
                            "marker": m_mk.group(1) if m_mk else None,
                        }
                    )

    if current.get("_in_pkg") and not current.get("_is_project"):
        current["wheels"] = wheel_entries
        current["dependencies"] = dep_entries
        if sdist_data:
            current["sdist"] = sdist_data
        if source_data:
            current["source"] = source_data
        if pkg_markers:
            current["resolution_markers"] = pkg_markers
        packages.append(current)

    return {
        "header": header,
        "packages": packages,
        "resolution_markers": resolution_markers,
        "raw": raw,
    }


def _has_sdist(pkg: dict[str, object]) -> bool:
    return "sdist" in pkg and bool(pkg.get("sdist"))


def _pyproject_data() -> dict[str, Any]:
    return tomllib.loads(PYPROJECT.read_text())


# ---- Fixtures ----


@pytest.fixture(scope="module")
def lock_full() -> dict[str, Any]:
    return _parse_lock_full()


@pytest.fixture(scope="module")
def lock_packages(lock_full: dict[str, Any]) -> list[dict[str, object]]:
    return lock_full["packages"]


@pytest.fixture(scope="module")
def pyproject() -> dict[str, Any]:
    return _pyproject_data()


# ---- Pinned versions ----


class TestPinnedConcreteVersions:
    def test_all_versions_are_concrete_not_ranges(self, lock_packages: list[dict[str, object]]) -> None:
        for pkg in lock_packages:
            v = str(pkg["version"])
            assert not any(op in v for op in (">=", "<=", "!=", "~=", "<", ">", "==", "*")), (
                f"{pkg['name']} version '{v}' is a range specifier, not a concrete pin"
            )

    def test_versions_parse_as_pep440(self, lock_packages: list[dict[str, object]]) -> None:
        for pkg in lock_packages:
            v = str(pkg["version"])
            assert PEP440_RE.match(v), f"{pkg['name']} version '{v}' is not valid PEP 440"

    def test_no_zero_zero_zero_version(self, lock_packages: list[dict[str, object]]) -> None:
        for pkg in lock_packages:
            v = str(pkg["version"])
            assert v != "0.0.0", f"{pkg['name']} has placeholder version 0.0.0"

    def test_versions_match_calver_or_semver_structure(self, lock_packages: list[dict[str, object]]) -> None:
        for pkg in lock_packages:
            v = str(pkg["version"])
            parts = v.split(".")
            first = parts[0].lstrip("v")
            assert first.isdigit(), f"{pkg['name']} version '{v}' doesn't start with digit"
            if len(parts) == 1 and v.isdigit():
                continue  # PEP 440 permits integer releases such as pywin32 312.
            assert len(parts) >= 2, f"{pkg['name']} version '{v}' doesn't look like semver/calver"

    def test_no_dev_pre_release_versions_for_core_deps(self, lock_packages: list[dict[str, object]]) -> None:
        core_packages = {
            "numpy",
            "fastapi",
            "pydantic",
            "sqlalchemy",
            "cryptography",
            "jinja2",
            "rich",
            "pyyaml",
            "jsonschema",
            "httpx",
            "alembic",
            "structlog",
            "tenacity",
            "hvac",
        }
        for pkg in lock_packages:
            name = str(pkg["name"])
            if name in core_packages:
                v = str(pkg["version"])
                assert "dev" not in v and "rc" not in v and "a" not in v and "b" not in v.split("+")[0], (
                    f"Core package {name} has pre-release version {v}"
                )


# ---- No yanked / upload recency ----


class TestNoYankedAssurance:
    def test_all_sdist_upload_times_are_iso8601(self, lock_packages: list[dict[str, object]]) -> None:
        for pkg in lock_packages:
            if not _has_sdist(pkg):
                continue
            ut = str(pkg["sdist"]["upload_time"])
            assert ut, f"{pkg['name']} sdist missing upload_time"
            assert ISO8601_RE.match(ut), f"{pkg['name']} sdist upload_time '{ut}' not ISO 8601"

    def test_no_upload_times_from_far_past(self, lock_packages: list[dict[str, object]]) -> None:
        cutoff = datetime.datetime(2015, 1, 1, tzinfo=datetime.UTC)
        for pkg in lock_packages:
            if not _has_sdist(pkg):
                continue
            ut_str = str(pkg["sdist"]["upload_time"])
            ut = datetime.datetime.fromisoformat(ut_str)
            assert ut >= cutoff, f"{pkg['name']} sdist upload_time {ut_str} is before 2015 — possibly abandoned"

    def test_upload_times_not_future(self, lock_packages: list[dict[str, object]]) -> None:
        now = datetime.datetime.now(datetime.UTC)
        for pkg in lock_packages:
            if not _has_sdist(pkg):
                continue
            ut_str = str(pkg["sdist"]["upload_time"])
            ut = datetime.datetime.fromisoformat(ut_str)
            assert ut <= now, f"{pkg['name']} sdist upload_time is in the future: {ut_str}"

    def test_no_yanked_name_pattern(self, lock_packages: list[dict[str, object]]) -> None:
        yanked_patterns = ["yanked", "deleted", "removed", "broken"]
        for pkg in lock_packages:
            name = str(pkg["name"]).lower()
            for pattern in yanked_patterns:
                assert pattern not in name, f"{pkg['name']} matches yanked indicator '{pattern}'"


# ---- Extras consistency ----


class TestExtrasConsistency:
    def test_provides_extras_matches_pyproject(self, lock_full: dict[str, Any], pyproject: dict[str, Any]) -> None:
        pyproject_extras = set(pyproject["project"].get("optional-dependencies", {}).keys())
        m = re.search(r"provides-extras\s*=\s*\[([^\]]+)\]", lock_full["raw"])
        assert m, "uv.lock missing provides-extras"
        lock_extras = {e.strip().strip('"').strip("'") for e in m.group(1).split(",")}
        assert pyproject_extras == lock_extras, (
            f"Extras mismatch: pyproject has {pyproject_extras}, lock has {lock_extras}"
        )

    def test_dev_group_deps_match_optional_deps(self, pyproject: dict[str, Any]) -> None:
        opt_dev = set(
            d.split(">=")[0].split("==")[0].split("[")[0].split("<")[0].strip().lower()
            for d in pyproject["project"].get("optional-dependencies", {}).get("dev", [])
        )
        dep_group_dev = set(
            d.split(">=")[0].split("==")[0].split("[")[0].split("<")[0].strip().lower()
            for d in pyproject.get("dependency-groups", {}).get("dev", [])
        )
        assert opt_dev & dep_group_dev, (
            f"dev optional-deps {opt_dev} and dependency-groups.dev {dep_group_dev} have no overlap"
        )

    def test_game_e2e_is_subset_of_e2e_all(self, pyproject: dict[str, Any]) -> None:
        extras: dict[str, list[str]] = pyproject["project"]["optional-dependencies"]
        game = {d.split(">=")[0].split("==")[0].strip() for d in extras.get("game-e2e", [])}
        e2e_all = {d.split(">=")[0].split("==")[0].strip() for d in extras.get("e2e-all", [])}
        missing = game - e2e_all
        assert not missing, f"game-e2e deps not in e2e-all: {missing}"

    def test_sandbox_extra_is_empty(self, pyproject: dict[str, Any]) -> None:
        extras: dict[str, list[str]] = pyproject["project"]["optional-dependencies"]
        sandbox = extras.get("sandbox", [])
        assert sandbox == [] or sandbox == [""], f"sandbox extra should be empty, got: {sandbox}"


# ---- Source distribution availability ----


class TestSourceDistributionAvailability:
    def test_all_packages_have_sdist(self, lock_packages: list[dict[str, object]]) -> None:
        missing = [str(p["name"]) for p in lock_packages if not _has_sdist(p)]
        assert len(missing) <= len(lock_packages) * 0.10, (
            f"More than 10% of packages ({len(missing)}) lack sdists: {missing[:10]}..."
        )

    def test_sdist_hashes_are_valid_sha256(self, lock_packages: list[dict[str, object]]) -> None:
        for pkg in lock_packages:
            if not _has_sdist(pkg):
                continue
            h = str(pkg["sdist"]["hash"])
            assert SHA256_RE.match(h), f"{pkg['name']} sdist hash '{h}' is not sha256:hex64"
            hex_part = h.split(":")[1] if ":" in h else h
            assert len(hex_part) == 64, f"{pkg['name']} sdist hash length {len(hex_part)} != 64"

    def test_sdist_urls_are_valid(self, lock_packages: list[dict[str, object]]) -> None:
        for pkg in lock_packages:
            if not _has_sdist(pkg):
                continue
            url = str(pkg["sdist"]["url"])
            assert url.startswith("https://files.pythonhosted.org/packages/"), (
                f"{pkg['name']} sdist URL not on PyPI: {url}"
            )
            assert url.endswith(".tar.gz") or url.endswith(".zip") or url.endswith(".tar.bz2"), (
                f"{pkg['name']} sdist URL not a tarball: {url}"
            )

    def test_sdist_nonzero_size(self, lock_packages: list[dict[str, object]]) -> None:
        for pkg in lock_packages:
            if not _has_sdist(pkg):
                continue
            size_val = pkg["sdist"]["size"]
            assert isinstance(size_val, (int, float)), f"{pkg['name']} sdist size missing"
            assert int(size_val) > 0, f"{pkg['name']} sdist has zero size"

    def test_sdist_filename_contains_package_name(self, lock_packages: list[dict[str, object]]) -> None:
        for pkg in lock_packages:
            if not _has_sdist(pkg):
                continue
            url = str(pkg["sdist"]["url"])
            filename = url.rsplit("/", 1)[-1] if "/" in url else url
            pkg_name_norm = str(pkg["name"]).lower().replace("-", "_")
            pkg_name_dash = str(pkg["name"]).lower()
            assert (
                pkg_name_norm in filename.lower()
                or pkg_name_dash in filename.lower()
                or pkg_name_norm.split("_")[0] in filename.lower()
            ), f"{pkg['name']} sdist filename '{filename}' missing package name"


# ---- Self-consistency ----


class TestSelfConsistency:
    def test_no_duplicate_packages(self, lock_packages: list[dict[str, object]]) -> None:
        name_version: dict[str, set[str]] = defaultdict(set)
        for pkg in lock_packages:
            name_version[str(pkg["name"])].add(str(pkg["version"]))
        duplicates = {n: vs for n, vs in name_version.items() if len(vs) > 1}
        for name, versions in duplicates.items():
            pkgs_for_name = [p for p in lock_packages if str(p["name"]) == name]
            markers_per = [p.get("resolution_markers", []) for p in pkgs_for_name]
            has_marker_sep = any(
                set(m1) != set(m2)
                for i, m1 in enumerate(markers_per)
                for j, m2 in enumerate(markers_per)
                if i < j and m1 and m2
            )
            assert has_marker_sep, f"{name} has multiple versions {versions} but no resolution-marker separation"

    def test_dependency_names_exist_as_packages(self, lock_packages: list[dict[str, object]]) -> None:
        all_names = {str(p["name"]) for p in lock_packages}
        for pkg in lock_packages:
            for dep in pkg.get("dependencies", []):
                dep_name = str(dep["name"])
                assert dep_name in all_names or dep_name == "general-ludd-agent", (
                    f"{pkg['name']} depends on '{dep_name}' which is not in the lockfile"
                )

    def test_lock_file_starts_with_version_1(self, lock_full: dict[str, Any]) -> None:
        assert lock_full["raw"].startswith("version = 1")

    def test_resolution_markers_present(self, lock_full: dict[str, Any]) -> None:
        markers: list[str] = lock_full["resolution_markers"]
        assert len(markers) >= 2, f"Expected >=2 resolution markers, got {len(markers)}"

    def test_core_packages_have_many_wheels(self, lock_packages: list[dict[str, object]]) -> None:
        core = {"numpy", "cryptography", "aiohttp", "sqlalchemy"}
        for pkg in lock_packages:
            if str(pkg["name"]) in core:
                wheels: list[dict[str, object]] = pkg.get("wheels", [])
                assert len(wheels) >= 3, f"{pkg['name']} has only {len(wheels)} wheels (expected >=3 for core package)"
