"""Deep version consistency tests across CHANGELOG, pyproject.toml, __init__.py, README, and git tags."""

from __future__ import annotations

import datetime
import os
import re
import subprocess
import tomllib

from pytest import MonkeyPatch

from general_ludd import __version__

PROJECT_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))
CHANGELOG = os.path.join(PROJECT_ROOT, "CHANGELOG.md")
PYPROJECT = os.path.join(PROJECT_ROOT, "pyproject.toml")
README = os.path.join(PROJECT_ROOT, "README.md")
INIT = os.path.join(PROJECT_ROOT, "src", "general_ludd", "__init__.py")

SEMVER_RE = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-((?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*)"
    r"(?:\.(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*))*))?"
    r"(?:\+([0-9a-zA-Z-]+(?:\.[0-9a-zA-Z-]+)*))?$"
)
CHANGELOG_HEADER_RE = re.compile(r"^##\s+\[(\d+\.\d+\.\d+(?:-[a-z]+\.[\d]+)?)\]", re.MULTILINE)
KEEPACHANGELOG_SECTION_RE = re.compile(r"^### (Added|Changed|Deprecated|Removed|Fixed|Security)", re.MULTILINE)
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()


def _pyproject_version() -> str:
    with open(PYPROJECT, "rb") as f:
        data = tomllib.load(f)
    project = data.get("project")
    assert isinstance(project, dict), "pyproject.toml must contain a project table"
    version = project.get("version")
    assert isinstance(version, str), "pyproject.toml project.version must be a string"
    return version


def _init_version_line() -> str:
    text = _read(INIT)
    match = re.search(r'__version__\s*=\s*"([^"]+)"', text)
    assert match, f"No __version__ assignment found in {INIT}"
    return match.group(1)


def _changelog_versions() -> list[str]:
    text = _read(CHANGELOG)
    versions: list[str] = []
    for m in CHANGELOG_HEADER_RE.finditer(text):
        versions.append(m.group(1))
    return versions


def _changelog_header_lines() -> list[tuple[int, str, str]]:
    text = _read(CHANGELOG)
    results: list[tuple[int, str, str]] = []
    for m in CHANGELOG_HEADER_RE.finditer(text):
        line_text = text[: m.start()]
        line_no = line_text.count("\n") + 1
        results.append((line_no, m.group(1), m.group(0)))
    return results


def _readme_status_version() -> str | None:
    text = _read(README)
    match = re.search(r"\*\*Status as of\s+v([\d.]+(?:-[a-z]+\.[\d]+)?)", text)
    if match:
        return match.group(1)
    return None


# ---------------------------------------------------------------------------
# Semver / format
# ---------------------------------------------------------------------------


def test_init_version_is_valid_semver() -> None:
    assert SEMVER_RE.match(__version__), f"__version__ '{__version__}' is not valid semver"


def test_pyproject_version_is_valid_semver() -> None:
    v = _pyproject_version()
    assert SEMVER_RE.match(v), f"pyproject.toml version '{v}' is not valid semver"


# ---------------------------------------------------------------------------
# Cross-file consistency
# ---------------------------------------------------------------------------


def test_pyproject_matches_init() -> None:
    py_v = _pyproject_version()
    init_v = _init_version_line()
    assert py_v == init_v, f"pyproject.toml version '{py_v}' != __init__.py __version__ '{init_v}'"


def test_init_matches_pyproject() -> None:
    py_v = _pyproject_version()
    assert __version__ == py_v, f"__version__ '{__version__}' != pyproject.toml version '{py_v}'"


def test_readme_status_matches_current_version() -> None:
    sv = _readme_status_version()
    assert sv is not None, "Could not parse 'Status as of' version from README.md"
    assert sv == __version__, f"README 'Status as of' v{sv} != __version__ '{__version__}'"


def test_readme_has_status_line_with_date() -> None:
    text = _read(README)
    match = re.search(r"\*\*Status as of\s+v[\d.]+(?:-[a-z]+\.[\d]+)?\s.*?\d{4}-\d{2}-\d{2}", text)
    assert match, "README 'Status as of' line must include a date (YYYY-MM-DD)"


def test_readme_status_date_is_recent() -> None:
    text = _read(README)
    match = re.search(r"\*\*Status as of\s+v[\d.]+(?:-[a-z]+\.[\d]+)?\s.*?(\d{4}-\d{2}-\d{2})", text)
    assert match, "Could not extract date from README 'Status as of' line"
    date_str = match.group(1)
    status_date = datetime.date.fromisoformat(date_str)
    days_ago = (datetime.date.today() - status_date).days
    assert days_ago <= 90, (
        f"README 'Status as of' date {date_str} is {days_ago} days ago — exceeds 90-day freshness limit"
    )


# ---------------------------------------------------------------------------
# CHANGELOG structure
# ---------------------------------------------------------------------------


def test_changelog_has_entry_for_current_version() -> None:
    versions = _changelog_versions()
    assert __version__ in versions, f"CHANGELOG has no entry for current version '{__version__}'. Found: {versions}"


def _changelog_versions_deduped() -> list[str]:
    versions = _changelog_versions()
    seen: set[str] = set()
    result: list[str] = []
    for v in versions:
        if v not in seen:
            seen.add(v)
            result.append(v)
    return result


def test_changelog_version_order_non_increasing() -> None:
    versions = _changelog_versions_deduped()
    assert len(versions) > 1, f"Expected multiple versions in CHANGELOG, found {len(versions)}"
    parsed = [_parse_semver_for_sort(v) for v in versions]
    for i in range(len(parsed) - 1):
        assert parsed[i] >= parsed[i + 1], (
            f"CHANGELOG version at position {i} ('{versions[i]}') < position {i + 1} ('{versions[i + 1]}'); "
            f"versions must not increase (go backwards in time): {versions}"
        )


def test_changelog_current_version_not_duplicated() -> None:
    versions = _changelog_versions()
    current_count = versions.count(__version__)
    assert current_count == 1, (
        f"Current version '{__version__}' appears {current_count} times in CHANGELOG — should appear exactly once"
    )


def test_current_version_is_first_in_changelog() -> None:
    versions = _changelog_versions()
    first_entry = versions[0]
    assert first_entry == __version__, f"CHANGELOG first entry '{first_entry}' != __version__ '{__version__}'"


def test_changelog_headers_follow_format() -> None:
    text = _read(CHANGELOG)
    bad_lines: list[int] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        if line.startswith("## [") and not CHANGELOG_HEADER_RE.match(line):
            bad_lines.append(line_no)
    assert not bad_lines, f"CHANGELOG has malformed version headers at lines {bad_lines}"


def test_changelog_each_version_has_sections() -> None:
    text = _read(CHANGELOG)
    headers = _changelog_header_lines()
    all_text = text.splitlines()
    for i, (_line_no, ver, _hdr) in enumerate(headers):
        start_line = _line_no
        end_line = headers[i + 1][0] if i + 1 < len(headers) else len(all_text) + 1
        block = "\n".join(all_text[start_line:end_line])
        has_section = bool(KEEPACHANGELOG_SECTION_RE.search(block))
        assert has_section, f"CHANGELOG entry '{ver}' has no '### Added/Changed/...' sub-section"


def test_changelog_has_keepachangelog_notice() -> None:
    text = _read(CHANGELOG)
    notice = "Keep a Changelog"
    assert notice.lower() in text.lower(), f"CHANGELOG must reference '{notice}' as per the format"


# ---------------------------------------------------------------------------
# Version completeness — all files that carry the version agree
# ---------------------------------------------------------------------------


def test_all_version_files_agree() -> None:
    py_v = _pyproject_version()
    init_v = _init_version_line()
    sv = _readme_status_version()
    assert sv is not None
    assert py_v == init_v == sv == __version__, (
        f"Version mismatch: pyproject={py_v}, __init__={init_v}, README={sv}, __version__={__version__}"
    )


def test_init_version_is_string() -> None:
    assert isinstance(__version__, str)
    assert len(__version__) > 0
    assert " " not in __version__


def test_pyproject_version_is_string() -> None:
    v = _pyproject_version()
    assert isinstance(v, str)
    assert len(v) > 0


# ---------------------------------------------------------------------------
# Git tag consistency (read-only, skip gracefully if not a git repo)
# ---------------------------------------------------------------------------


def test_git_tags_include_current_version() -> None:
    try:
        result = subprocess.run(
            ["git", "tag", "--list", f"v{__version__}"],
            capture_output=True,
            text=True,
            cwd=PROJECT_ROOT,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return
    tags = [t.strip() for t in result.stdout.splitlines() if t.strip()]
    if _release_tag_is_required():
        assert tags == [f"v{__version__}"], (
            f"Tag-triggered release validation requires exact tag 'v{__version__}', "
            f"got: {tags}"
        )
    else:
        assert tags in ([], [f"v{__version__}"]), (
            f"Candidate validation found an unexpected current-version tag set: {tags}"
        )


def test_git_tags_are_annotated() -> None:
    try:
        result = subprocess.run(
            ["git", "tag", "--list", f"v{__version__}", "--format=%(objecttype)"],
            capture_output=True,
            text=True,
            cwd=PROJECT_ROOT,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return
    types = [t.strip() for t in result.stdout.splitlines() if t.strip()]
    if types:
        assert all(t == "tag" for t in types), (
            f"Tag(s) v{__version__} should be annotated (objecttype=tag), got: {types}"
        )


def test_git_tags_most_recent_is_current() -> None:
    try:
        result = subprocess.run(
            ["git", "tag", "--sort=-creatordate", "--format=%(refname:strip=2)"],
            capture_output=True,
            text=True,
            cwd=PROJECT_ROOT,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return
    tags = [t.strip() for t in result.stdout.splitlines() if t.strip()]
    version_tags = [t.lstrip("v") for t in tags if SEMVER_RE.match(t.lstrip("v"))]
    if _release_tag_is_required():
        assert version_tags, "Tag-triggered release validation found no semantic-version tags"
        assert version_tags[0] == __version__, (
            f"Most recent git tag by creatordate '{version_tags[0]}' != __version__ '{__version__}'"
        )


def test_branch_candidate_does_not_require_uncut_release_tag(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_REF_TYPE", "branch")
    monkeypatch.delenv("GLUDD_REQUIRE_RELEASE_TAG", raising=False)
    assert not _release_tag_is_required()


def test_tag_trigger_requires_published_release_tag(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_REF_TYPE", "tag")
    assert _release_tag_is_required()


def test_explicit_release_tag_guard_is_available_locally(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.delenv("GITHUB_REF_TYPE", raising=False)
    monkeypatch.setenv("GLUDD_REQUIRE_RELEASE_TAG", "1")
    assert _release_tag_is_required()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _release_tag_is_required() -> bool:
    """Return whether this run validates an already-created release tag."""
    return os.environ.get("GITHUB_REF_TYPE") == "tag" or os.environ.get(
        "GLUDD_REQUIRE_RELEASE_TAG"
    ) == "1"


def _parse_semver_for_sort(v: str) -> tuple[int, int, int, tuple[int | str, ...]]:
    m = SEMVER_RE.match(v)
    assert m, f"Cannot parse semver: {v}"
    major, minor, patch = int(m.group(1)), int(m.group(2)), int(m.group(3))
    prerelease = m.group(4) or ""
    if prerelease:
        parts: list[int | str] = []
        for p in prerelease.split("."):
            try:
                parts.append(int(p))
            except ValueError:
                parts.append(p)
        pre_tuple = tuple(parts)
    else:
        pre_tuple = ()
    return (major, minor, patch, pre_tuple)
