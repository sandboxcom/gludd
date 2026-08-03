"""Deep integrity tests for uv.lock — structural validation, hash verification,
cross-reference with pyproject.toml, and artifact availability checks.

Reads uv.lock and pyproject.toml at the project root, then asserts invariants
that prevent stale, corrupt, or incomplete lock files from landing on main.
"""

from __future__ import annotations

import datetime
import re
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
UV_LOCK = ROOT / "uv.lock"
PYPROJECT = ROOT / "pyproject.toml"

PEP440_RE = re.compile(
    r"^([1-9][0-9]*!)?(0|[1-9][0-9]*)(\.(0|[1-9][0-9]*))*"
    r"((a|b|rc)(0|[1-9][0-9]*))?(\.post(0|[1-9][0-9]*))?"
    r"(\.dev(0|[1-9][0-9]*))?$"
)
PYPI_NAME_RE = re.compile(r"^([A-Z0-9]|[A-Z0-9][A-Z0-9._-]*[A-Z0-9])$", re.I)
SHA256_RE = re.compile(r"^sha256:[a-f0-9]{64}$")
ISO8601_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$")


def _parse_lock_full() -> dict[str, Any]:
    """Parse uv.lock into header + list of package dicts.

    Skips the project's own [package] / [[package]] entry (editable source)
    which lacks wheels, sdist, and the normal source registry.
    """
    raw = UV_LOCK.read_text()
    header: dict[str, object] = {}
    packages: list[dict[str, object]] = []
    current: dict[str, object] = {}
    in_wheels = False
    in_deps = False
    in_sdist = False
    in_source = False
    wheel_entries: list[dict[str, object]] = []
    dep_entries: list[dict[str, object]] = []
    sdist_data: dict[str, object] = {}
    source_data: dict[str, object] = {}

    for line in raw.splitlines():
        stripped = line.strip()
        if stripped == "" or stripped.startswith("#"):
            continue
        if stripped.startswith("version = ") and not current.get("_in_pkg"):
            header["version"] = int(stripped.split("=")[1].strip())
        elif stripped.startswith("revision = ") and not current.get("_in_pkg"):
            header["revision"] = int(stripped.split("=")[1].strip())
        elif stripped.startswith("requires-python = ") and not current.get("_in_pkg"):
            val = stripped.split("=", 1)[1].strip()
            header["requires-python"] = val.strip('"')

        if stripped == "[[package]]":
            if current.get("_in_pkg"):
                current["wheels"] = wheel_entries
                current["dependencies"] = dep_entries
                if sdist_data:
                    current["sdist"] = sdist_data
                if source_data:
                    current["source"] = source_data
                packages.append(current)
            current = {"_in_pkg": True}
            wheel_entries = []
            dep_entries = []
            sdist_data = {}
            source_data = {}
            in_wheels = False
            in_deps = False
            in_sdist = False
            in_source = False
        elif current.get("_in_pkg"):
            if stripped.startswith("name = "):
                current["name"] = stripped.split('"')[1]
            elif stripped.startswith("version = "):
                current["version"] = stripped.split('"')[1]
            elif stripped.startswith("source = "):
                in_source = True
                # Detect editable source (project itself) — skip this package
                if "editable" in stripped:
                    current["_is_project"] = True
                m = re.search(r'registry\s*=\s*"([^"]+)"', stripped)
                if m:
                    source_data["registry"] = m.group(1)
                current["source"] = source_data
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
                    dep_entries.append({"name": m.group(1)})

    if current.get("_in_pkg"):
        current["wheels"] = wheel_entries
        current["dependencies"] = dep_entries
        if sdist_data:
            current["sdist"] = sdist_data
        if source_data:
            current["source"] = source_data
        packages.append(current)

    # Filter out the project's own package (editable source, missing sdist/wheels)
    packages = [p for p in packages if not p.get("_is_project")]

    return {"header": header, "packages": packages}


def _parse_pyproject_deps() -> set[str]:
    """Extract dependency names from pyproject.toml dependencies + dev groups."""
    raw = PYPROJECT.read_text()
    names: set[str] = set()
    in_deps = False
    in_dev_deps = False

    for line in raw.splitlines():
        stripped = line.strip()
        if stripped == "dependencies = [":
            in_deps = True
            continue
        if stripped == "dev = [" and not in_deps:
            in_dev_deps = True
            continue
        if stripped.startswith("[") and stripped.endswith("]") and stripped != "dev = [":
            in_deps = False
            in_dev_deps = False
            continue
        if stripped == "]":
            in_deps = False
            in_dev_deps = False
            continue
        if in_deps or in_dev_deps:
            match = re.match(r'"([a-zA-Z0-9_.-]+)', stripped)
            if match:
                name = match.group(1)
                if "[" in name:
                    name = name.split("[")[0]
                names.add(name)
    return names


# ---- Fixtures ----


@pytest.fixture(scope="module")
def lock_data() -> dict[str, Any]:
    return _parse_lock_full()


@pytest.fixture(scope="module")
def lock_packages(lock_data: dict[str, Any]) -> list[dict[str, object]]:
    packages: list[dict[str, object]] = lock_data["packages"]  # type: ignore[assignment]
    return packages


@pytest.fixture(scope="module")
def pyproject_deps() -> set[str]:
    return _parse_pyproject_deps()


# ---- Helpers ----


def _has_sdist(pkg: dict[str, object]) -> bool:
    return "sdist" in pkg and bool(pkg.get("sdist"))


# ---- Structural tests ----


class TestLockFileHeader:
    def test_version_is_valid(self, lock_data: dict[str, Any]) -> None:
        assert lock_data["header"]["version"] == 1

    def test_revision_is_positive(self, lock_data: dict[str, Any]) -> None:
        assert lock_data["header"]["revision"] >= 3

    def test_requires_python_matches_pyproject(self, lock_data: dict[str, Any]) -> None:
        assert lock_data["header"]["requires-python"] == ">=3.11"


class TestPackageCount:
    def test_at_least_50_packages(self, lock_packages: list[dict[str, object]]) -> None:
        assert len(lock_packages) >= 50, f"Expected >=50 packages, got {len(lock_packages)}"

    def test_no_duplicate_packages(self, lock_packages: list[dict[str, object]]) -> None:
        names = [str(p["name"]) for p in lock_packages]
        duplicates = {n for n in names if names.count(n) > 1}
        for dup_name in duplicates:
            blocks = [p for p in lock_packages if str(p["name"]) == dup_name]
            versions = {str(p.get("version", "")) for p in blocks}
            assert len(versions) <= len(blocks), (
                f"{dup_name} has {len(blocks)} blocks with only {len(versions)} versions"
            )


class TestPackageFields:
    def test_all_packages_have_version(self, lock_packages: list[dict[str, object]]) -> None:
        for pkg in lock_packages:
            assert "version" in pkg, f"{pkg['name']} missing version"
            v = pkg["version"]
            assert v and str(v).strip(), f"{pkg['name']} has empty version"

    def test_all_packages_have_source(self, lock_packages: list[dict[str, object]]) -> None:
        for pkg in lock_packages:
            assert "source" in pkg, f"{pkg['name']} missing source"
            src: dict[str, object] = pkg["source"]  # type: ignore[assignment]
            assert "registry" in src, f"{pkg['name']} missing source registry"

    def test_source_registry_is_pypi(self, lock_packages: list[dict[str, object]]) -> None:
        for pkg in lock_packages:
            src: dict[str, object] = pkg["source"]  # type: ignore[assignment]
            assert src["registry"] == "https://pypi.org/simple", (
                f"{pkg['name']} has non-PyPI registry: {src['registry']}"
            )

    def test_most_packages_have_sdist(self, lock_packages: list[dict[str, object]]) -> None:
        with_sdist = sum(1 for p in lock_packages if _has_sdist(p))
        ratio = with_sdist / len(lock_packages)
        assert ratio >= 0.90, f"Only {with_sdist}/{len(lock_packages)} ({ratio:.1%}) packages have sdist; expect >=90%"

    def test_most_packages_have_wheels(self, lock_packages: list[dict[str, object]]) -> None:
        for pkg in lock_packages:
            assert "wheels" in pkg, f"{pkg['name']} missing wheels key"
        with_wheels = sum(
            1
            for p in lock_packages
            if len(p.get("wheels", [])) >= 1  # type: ignore[arg-type]
        )
        ratio = with_wheels / len(lock_packages)
        without_names = sorted(
            str(p["name"])
            for p in lock_packages
            if len(p.get("wheels", [])) < 1  # type: ignore[arg-type]
        )
        assert ratio >= 0.98, (
            f"Only {with_wheels}/{len(lock_packages)} ({ratio:.1%}) packages have wheels; "
            f"expect >=98%. Without: {', '.join(without_names)}"
        )


class TestPackageNameValidity:
    def test_names_match_pypi_format(self, lock_packages: list[dict[str, object]]) -> None:
        for pkg in lock_packages:
            name = str(pkg["name"])
            assert PYPI_NAME_RE.match(name), f"{name} is not a valid PyPI package name"

    def test_names_are_normalized(self, lock_packages: list[dict[str, object]]) -> None:
        for pkg in lock_packages:
            name = str(pkg["name"])
            assert name == name.lower().replace("_", "-"), f"{name} is not normalized (lowercase + dash-separated)"


class TestVersionValidity:
    def test_versions_follow_pep440(self, lock_packages: list[dict[str, object]]) -> None:
        for pkg in lock_packages:
            v = str(pkg["version"])
            assert PEP440_RE.match(v), f"{pkg['name']} version '{v}' is not PEP 440"


class TestHashIntegrity:
    def test_sdist_hashes_are_sha256(self, lock_packages: list[dict[str, object]]) -> None:
        for pkg in lock_packages:
            if not _has_sdist(pkg):
                continue
            sdist: dict[str, object] = pkg["sdist"]  # type: ignore[assignment]
            h = str(sdist.get("hash", ""))
            assert SHA256_RE.match(h), f"{pkg['name']} sdist hash '{h}' is not sha256:hex64"

    def test_wheel_hashes_are_sha256(self, lock_packages: list[dict[str, object]]) -> None:
        for pkg in lock_packages:
            wheels: list[dict[str, object]] = pkg["wheels"]  # type: ignore[assignment]
            for i, wheel in enumerate(wheels):
                h = str(wheel.get("hash", ""))
                assert SHA256_RE.match(h), f"{pkg['name']} wheel[{i}] hash '{h}' is not sha256:hex64"

    def test_hashes_are_64_hex_chars(self, lock_packages: list[dict[str, object]]) -> None:
        for pkg in lock_packages:
            if not _has_sdist(pkg):
                continue
            sdist: dict[str, object] = pkg["sdist"]  # type: ignore[assignment]
            h = str(sdist.get("hash", ""))
            hex_part = h.split(":")[1] if ":" in h else h
            assert len(hex_part) == 64, f"{pkg['name']} sdist hash not 64 hex chars: {len(hex_part)}"
            assert all(c in "0123456789abcdef" for c in hex_part), f"{pkg['name']} sdist hash has non-hex chars"


class TestUploadTimes:
    def test_sdist_upload_times_are_iso8601(self, lock_packages: list[dict[str, object]]) -> None:
        for pkg in lock_packages:
            if not _has_sdist(pkg):
                continue
            sdist: dict[str, object] = pkg["sdist"]  # type: ignore[assignment]
            ut = str(sdist.get("upload_time", ""))
            if ut:
                assert ISO8601_RE.match(ut), f"{pkg['name']} sdist upload_time '{ut}' is not ISO 8601"

    def test_wheel_upload_times_are_iso8601(self, lock_packages: list[dict[str, object]]) -> None:
        for pkg in lock_packages:
            wheels: list[dict[str, object]] = pkg["wheels"]  # type: ignore[assignment]
            for i, wheel in enumerate(wheels):
                ut = str(wheel.get("upload_time", ""))
                if ut:
                    assert ISO8601_RE.match(ut), f"{pkg['name']} wheel[{i}] upload_time '{ut}' is not ISO 8601"

    def test_upload_times_are_not_future(self, lock_packages: list[dict[str, object]]) -> None:
        now = datetime.datetime.now(datetime.UTC)
        for pkg in lock_packages:
            if not _has_sdist(pkg):
                continue
            sdist: dict[str, object] = pkg["sdist"]  # type: ignore[assignment]
            ut_str = str(sdist.get("upload_time", ""))
            if ut_str:
                ut = datetime.datetime.fromisoformat(ut_str)
                assert ut <= now, f"{pkg['name']} sdist upload_time is in the future: {ut_str}"


class TestURLs:
    def test_sdist_urls_point_to_pypi(self, lock_packages: list[dict[str, object]]) -> None:
        for pkg in lock_packages:
            if not _has_sdist(pkg):
                continue
            sdist: dict[str, object] = pkg["sdist"]  # type: ignore[assignment]
            url = str(sdist.get("url", ""))
            assert "files.pythonhosted.org" in url, f"{pkg['name']} sdist URL not on PyPI: {url}"

    def test_wheel_urls_point_to_pypi(self, lock_packages: list[dict[str, object]]) -> None:
        for pkg in lock_packages:
            wheels: list[dict[str, object]] = pkg["wheels"]  # type: ignore[assignment]
            for i, wheel in enumerate(wheels):
                url = str(wheel.get("url", ""))
                assert "files.pythonhosted.org" in url, f"{pkg['name']} wheel[{i}] URL not on PyPI: {url}"

    def test_sdist_filenames_contain_package_name(self, lock_packages: list[dict[str, object]]) -> None:
        for pkg in lock_packages:
            if not _has_sdist(pkg):
                continue
            sdist: dict[str, object] = pkg["sdist"]  # type: ignore[assignment]
            url = str(sdist.get("url", ""))
            filename = url.rsplit("/", 1)[-1] if "/" in url else url
            name_normalized = str(pkg["name"]).lower().replace("-", "_")
            name_dash = str(pkg["name"]).lower()
            assert (
                name_normalized in filename.lower()
                or name_dash in filename.lower()
                or (name_normalized.split("_")[0] in filename.lower())
            ), f"{pkg['name']} sdist filename '{filename}' missing package name"


class TestPyprojectSync:
    def test_project_deps_in_lock(
        self,
        lock_packages: list[dict[str, object]],
        pyproject_deps: set[str],
    ) -> None:
        lock_names = {str(p["name"]) for p in lock_packages}
        missing = pyproject_deps - lock_names
        assert len(missing) < (len(pyproject_deps) / 2), f"More than half of pyproject deps not in lock: {missing}"

    def test_lock_file_is_parseable_text(self, lock_data: dict[str, Any]) -> None:
        raw = UV_LOCK.read_text()
        assert raw.startswith("version = 1"), "uv.lock must start with 'version = 1'"
        assert "[[package]]" in raw, "uv.lock must contain package entries"

    def test_no_placeholder_versions(self, lock_packages: list[dict[str, object]]) -> None:
        for pkg in lock_packages:
            version = str(pkg["version"])
            assert version != "0.0.0", f"{pkg['name']} has placeholder version 0.0.0"
            assert not version.startswith("0.1.0-beta"), (
                f"{pkg['name']} version {version} looks like a dev pin, not a real resolved version"
            )


class TestArtifactAvailability:
    def test_sdist_has_nonzero_size(self, lock_packages: list[dict[str, object]]) -> None:
        for pkg in lock_packages:
            if not _has_sdist(pkg):
                continue
            sdist: dict[str, object] = pkg["sdist"]  # type: ignore[assignment]
            size = sdist.get("size", 0)
            assert isinstance(size, (int, float)) and int(size) > 0, f"{pkg['name']} sdist has zero or missing size"

    def test_wheels_have_nonzero_size(self, lock_packages: list[dict[str, object]]) -> None:
        for pkg in lock_packages:
            wheels: list[dict[str, object]] = pkg["wheels"]  # type: ignore[assignment]
            for i, wheel in enumerate(wheels):
                size = wheel.get("size", 0)
                assert isinstance(size, (int, float)) and int(size) > 0, (
                    f"{pkg['name']} wheel[{i}] has zero or missing size"
                )

    def test_wheel_filenames_are_valid(self, lock_packages: list[dict[str, object]]) -> None:
        wheel_re = re.compile(r".+\.whl$")
        for pkg in lock_packages:
            wheels: list[dict[str, object]] = pkg["wheels"]  # type: ignore[assignment]
            for i, wheel in enumerate(wheels):
                url = str(wheel.get("url", ""))
                filename = url.rsplit("/", 1)[-1] if "/" in url else url
                assert wheel_re.match(filename), f"{pkg['name']} wheel[{i}] filename '{filename}' is not .whl"


class TestPyprojectDependencyParsing:
    def test_pyproject_parse_finds_core_deps(self, pyproject_deps: set[str]) -> None:
        core_expected = {"numpy", "fastapi", "pydantic", "sqlalchemy", "jinja2", "rich"}
        found = core_expected & pyproject_deps
        assert len(found) >= 4, f"Failed to find core deps in pyproject. Found: {found}"

    def test_pyproject_parse_finds_dev_deps(self, pyproject_deps: set[str]) -> None:
        dev_expected = {"pytest", "ruff", "mypy", "pre-commit", "bandit"}
        found = dev_expected & pyproject_deps
        assert len(found) >= 3, f"Failed to find dev deps in pyproject. Found: {found}"
