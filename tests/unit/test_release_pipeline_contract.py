"""Structural tests verifying the release pipeline contract.

Ensures version consistency across the three canonical sources
(pyproject.toml, __init__.py, README.md), Makefile target existence,
TASKS/CHANGELOG version references, and git tag semver conformance.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _read(path: str) -> str:
    return (ROOT / path).read_text()


def _current_version() -> str:
    """Extract the canonical version from pyproject.toml."""
    content = _read("pyproject.toml")
    m = re.search(r'^version\s*=\s*"([^"]+)"', content, re.MULTILINE)
    assert m, "version not found in pyproject.toml"
    return m.group(1)


class TestVersionConsistency:
    """The version in pyproject.toml, __init__.py, and README must match."""

    def test_pyproject_version_matches_init_py_version(self):
        version = _current_version()
        init = _read("src/general_ludd/__init__.py")
        m = re.search(r'^__version__\s*=\s*"([^"]+)"', init, re.MULTILINE)
        assert m, "__version__ not found in __init__.py"
        assert m.group(1) == version, f"__init__.py version {m.group(1)!r} != pyproject.toml version {version!r}"

    def test_readme_status_line_matches_current_version(self):
        version = _current_version()
        readme = _read("README.md")
        m = re.search(r"\*\*Status as of\s+(v?[\d.]+(?:-[a-z]+\.[\d]+)?)\b", readme)
        assert m, "'Status as of <version>' line not found in README.md — add or update it before cutting a release"
        readme_version = m.group(1).lstrip("v")
        assert readme_version == version, (
            f"README 'Status as of' version {readme_version!r} != pyproject.toml version {version!r}"
        )


class TestMakefileReleaseTargets:
    """Essential release pipeline targets must exist in the Makefile."""

    def test_release_cut_target_exists(self):
        makefile = _read("Makefile")
        assert re.search(r"^release-cut:\s*$", makefile, re.MULTILINE), "release-cut target must exist in Makefile"

    def test_verify_release_completeness_target_exists(self):
        makefile = _read("Makefile")
        assert re.search(r"^verify-release-completeness:\s*$", makefile, re.MULTILINE), (
            "verify-release-completeness target must exist in Makefile"
        )


class TestTasksReferencesCurrentVersion:
    """TASKS.md must reference the current version string."""

    def test_tasks_has_current_version_referenced(self):
        version = _current_version()
        tasks = _read("TASKS.md")
        # The version may appear with or without the 'v' prefix
        pattern = re.compile(r"\b" + re.escape(f"v{version}") + r"\b")
        assert pattern.search(tasks), f"TASKS.md does not reference the current version v{version!r}"


class TestChangelogHasCurrentVersion:
    """CHANGELOG.md must have a section for the current version."""

    def test_changelog_has_current_version_section(self):
        version = _current_version()
        changelog = _read("CHANGELOG.md")
        # Match "## [0.1.0-beta.3]" or "## [v0.1.0-beta.3]"
        pattern = re.compile(r"^##\s+\[" + re.escape(version) + r"\]", re.MULTILINE)
        pattern_v = re.compile(r"^##\s+\[" + re.escape(f"v{version}") + r"\]", re.MULTILINE)
        assert pattern.search(changelog) or pattern_v.search(changelog), (
            f"CHANGELOG.md missing section header for version [{version}]"
        )


class TestGitTagSemver:
    """Published tags must follow the semver pattern v0.1.0-beta.N."""

    SEMVER_TAG_RE = re.compile(r"^v?\d+\.\d+\.\d+(-[a-zA-Z0-9.]+)?$")

    def _git_tag_list(self) -> list[str]:
        result = subprocess.run(
            ["git", "tag", "--list"],
            capture_output=True,
            text=True,
            cwd=ROOT,
        )
        if result.returncode != 0:
            return []
        return [t.strip() for t in result.stdout.splitlines() if t.strip()]

    def test_all_git_tags_follow_semver(self):
        tags = self._git_tag_list()
        violations: list[str] = []
        for tag in tags:
            if not self.SEMVER_TAG_RE.match(tag):
                violations.append(tag)
        all_tags_str = ", ".join(tags) if tags else "(no tags found)"
        assert not violations, (
            f"Tags not matching semver pattern "
            f"([v]N.N.N[-prerelease]): "
            f"{', '.join(violations)}. All tags: {all_tags_str}"
        )

    def test_release_tags_have_beta_prerelease(self):
        """All v0.1.0-* release tags must have a semver prerelease suffix."""
        tags = self._git_tag_list()
        release_tags = [t for t in tags if t.startswith("v")]
        stray: list[str] = []
        for tag in release_tags:
            # Must be semver with a prerelease: v0.1.0-alpha.N, v0.1.0-beta.N, etc.
            if not re.match(r"^v0\.1\.0-[a-zA-Z0-9]+.*$", tag):
                stray.append(tag)
        assert not stray, (
            f"Release tags not matching v0.1.0-prerelease pattern: "
            f"{', '.join(stray)}. All v-tags: {', '.join(release_tags)}"
        )
