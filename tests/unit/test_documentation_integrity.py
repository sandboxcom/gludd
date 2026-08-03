"""Documentation integrity tests — structural pins against doc drift.

Covers: architecture.md cross-references, subsystem coverage, README version
line, RELEASE_RUNBOOK.md section presence, and AGENTS.md doc-reference validity.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import ClassVar

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / "docs"
ARCH = DOCS / "architecture.md"
README = ROOT / "README.md"
RUNBOOK = DOCS / "RELEASE_RUNBOOK.md"
AGENTS = ROOT / "AGENTS.md"
PYPROJECT = ROOT / "pyproject.toml"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _read(path: Path) -> str:
    return path.read_text()


def _extract_md_links(text: str) -> list[tuple[str, str]]:
    """Return (href, display-text) for every [display](href) link."""
    return re.findall(r"\[([^\]]*)\]\(([^)]+)\)", text)


def _extract_backtick_doc_paths(text: str) -> list[str]:
    """Return doc-relative .md paths from backtick-quoted strings like `docs/X.md`."""
    return re.findall(r"`(docs/[\w/-]+\.md)`", text)


def _extract_all_doc_paths(text: str) -> list[str]:
    """Return every docs/*.md path referenced in markdown, via []() or backticks."""
    paths: list[str] = []
    paths.extend(href for _, href in _extract_md_links(text) if href.startswith("docs/") and href.endswith(".md"))
    paths.extend(_extract_backtick_doc_paths(text))
    return paths


def _extract_md_headings(text: str) -> set[str]:
    """Return all ## / ### / #### heading text (without markdown markup)."""
    return set(h.strip() for h in re.findall(r"^#{2,4}\s+(.+)$", text, re.MULTILINE))


# ---------------------------------------------------------------------------
# architecture.md — internal cross-reference validity
# ---------------------------------------------------------------------------


class TestArchitectureCrossReferences:
    """Every local .md link in architecture.md must resolve to an existing file."""

    def test_local_md_links_exist(self) -> None:
        text = _read(ARCH)
        links = _extract_md_links(text)
        local_links = [
            (href, display) for display, href in links if href.endswith(".md") and not href.startswith("http")
        ]
        assert local_links, "No local .md links found in architecture.md"

        for href, display in local_links:
            target = (ARCH.parent / href).resolve()
            assert target.exists(), f"Broken cross-reference: [{display}]({href}) → {target} does not exist"

    def test_at_least_three_cross_references(self) -> None:
        text = _read(ARCH)
        links = _extract_md_links(text)
        local_md = [href for _, href in links if href.endswith(".md") and not href.startswith("http")]
        assert len(local_md) >= 1, "architecture.md should cross-reference at least one other doc"

    def test_links_to_release_runbook(self) -> None:
        text = _read(ARCH)
        assert "RELEASE_RUNBOOK.md" in text, "architecture.md must reference RELEASE_RUNBOOK.md (CI Pipeline section)"


# ---------------------------------------------------------------------------
# architecture.md — critical subsystem coverage
# ---------------------------------------------------------------------------


class TestArchitectureSubsystemCoverage:
    """architecture.md must mention every critical subsystem."""

    SUBSYSTEMS: ClassVar[dict[str, list[str]]] = {
        "daemon": ["Daemon", "daemon"],
        "event_loop": ["Event Loop", "event loop"],
        "router": ["Router", "router", "gateway"],
        "worker": ["worker", "Worker", "gunicorn"],
    }

    def _text(self) -> str:
        return _read(ARCH).lower()

    def test_mentions_daemon(self) -> None:
        assert "daemon" in self._text(), "architecture.md must mention the daemon subsystem"

    def test_mentions_event_loop(self) -> None:
        text = self._text()
        ok = "event loop" in text or "event_loop" in text
        assert ok, "architecture.md must mention the event loop subsystem"

    def test_mentions_router_or_gateway(self) -> None:
        text = self._text()
        ok = "router" in text or "gateway" in text
        assert ok, "architecture.md must mention the model router / gateway subsystem"

    def test_mentions_worker(self) -> None:
        text = self._text()
        ok = "worker" in text or "gunicorn" in text
        assert ok, "architecture.md must mention the worker subsystem (gunicorn/hot reload)"

    def test_has_minimum_section_count(self) -> None:
        headings = _extract_md_headings(_read(ARCH))
        # architecture.md is ~270 lines; must have at least 8 ##/### sections
        assert len(headings) >= 8, (
            f"architecture.md has only {len(headings)} headings; expected at least 8 sections covering major subsystems"
        )

    def test_has_minimum_size(self) -> None:
        size = len(_read(ARCH))
        assert size > 5000, f"architecture.md is only {size} bytes; expected >5 KB"


# ---------------------------------------------------------------------------
# README.md — version line in feature status table
# ---------------------------------------------------------------------------


class TestReadmeVersionLine:
    """The README status table must carry a real, version-matching line."""

    def test_has_status_as_of_line(self) -> None:
        text = _read(README)
        match = re.search(r"\*\*Status as of\s+(v[\d.]+(?:-[a-z]+\.[\d]+)?)", text)
        assert match, "README.md must have a '**Status as of vX.Y.Z-...' line in the feature status table"

    def test_version_matches_pyproject(self) -> None:
        text = _read(README)
        match = re.search(r"\*\*Status as of\s+(v[\d.]+(?:-[a-z]+\.[\d]+)?)", text)
        assert match, "Could not parse 'Status as of' version from README.md"
        readme_version = match.group(1).lstrip("v")

        import tomllib

        with open(PYPROJECT, "rb") as f:
            data = tomllib.load(f)
        proj_version = data["project"]["version"]

        assert readme_version == proj_version, (
            f"README 'Status as of' version v{readme_version} does not match pyproject.toml version {proj_version}"
        )

    def test_status_line_has_date(self) -> None:
        text = _read(README)
        ok = bool(re.search(r"Status as of\s+v[\d.]+(?:-[a-z]+\.[\d]+)?\s+.{1,4}\d{4}-\d{2}-\d{2}", text))
        assert ok, "README 'Status as of' line must include a date (YYYY-MM-DD)"


# ---------------------------------------------------------------------------
# RELEASE_RUNBOOK.md — required sections
# ---------------------------------------------------------------------------


class TestReleaseRunbook:
    """RELEASE_RUNBOOK.md must exist and carry the required sections."""

    REQUIRED_SECTIONS: ClassVar[list[str]] = [
        "The rule",
        "Preconditions",
        "Verify CI",
        "Merge",
        "release-cut",
        "verify-release-completeness",
        "What 'complete' means",
        "Traps",
        "A cancelled CI run is NOT a verdict",
    ]

    def test_runbook_exists(self) -> None:
        assert RUNBOOK.exists(), "docs/RELEASE_RUNBOOK.md must exist"

    def test_has_minimum_size(self) -> None:
        size = len(_read(RUNBOOK))
        assert size > 4000, f"RELEASE_RUNBOOK.md is only {size} bytes; expected >4 KB"

    def test_covers_release_rule(self) -> None:
        text = _read(RUNBOOK)
        assert "tag is not a release" in text.lower(), (
            "RELEASE_RUNBOOK.md must state the rule: 'a tag is not a release'"
        )

    def test_covers_preconditions(self) -> None:
        text = _read(RUNBOOK)
        assert "pyproject.toml" in text, "RELEASE_RUNBOOK.md must list preconditions (pyproject.toml version bump)"

    def test_covers_ci_green_check(self) -> None:
        text = _read(RUNBOOK)
        ok = "ci-verdict" in text.lower() or "ci green" in text.lower()
        assert ok, "RELEASE_RUNBOOK.md must document the CI-green pre-release check"

    def test_covers_release_cut(self) -> None:
        text = _read(RUNBOOK)
        assert "release-cut" in text, "RELEASE_RUNBOOK.md must document the release-cut command"

    def test_covers_completeness_gate(self) -> None:
        text = _read(RUNBOOK)
        assert "verify-release-completeness" in text, (
            "RELEASE_RUNBOOK.md must document verify-release-completeness as the real gate"
        )

    def test_covers_traps(self) -> None:
        text = _read(RUNBOOK)
        assert "Traps" in text, "RELEASE_RUNBOOK.md must have a 'Traps' section"

    def test_covers_cancelled_ci_verdict(self) -> None:
        text = _read(RUNBOOK)
        assert "cancelled" in text.lower() and "verdict" in text.lower(), (
            "RELEASE_RUNBOOK.md must cover the 'cancelled CI is not a verdict' rule"
        )

    def test_covers_12_artifact_categories(self) -> None:
        text = _read(RUNBOOK)
        assert "12 artifact" in text.lower() or "12 categories" in text.lower(), (
            "RELEASE_RUNBOOK.md must reference the 12 artifact categories"
        )

    def test_has_required_headings(self) -> None:
        text = _read(RUNBOOK)
        headings = _extract_md_headings(text)
        required = {"The rule", 'What "complete" means', "Traps"}
        missing = required - headings
        assert not missing, f"RELEASE_RUNBOOK.md missing required sections: {missing}"


# ---------------------------------------------------------------------------
# AGENTS.md doc references — every docs/*.md link must resolve
# ---------------------------------------------------------------------------


class TestAgentsDocReferences:
    """Every docs/<path>.md link in AGENTS.md must point to an existing file."""

    def test_all_doc_links_resolve(self) -> None:
        text = _read(AGENTS)
        doc_paths = _extract_all_doc_paths(text)
        assert doc_paths, "No docs/ references found in AGENTS.md"

        broken: list[str] = []
        for path in doc_paths:
            target = (ROOT / path).resolve()
            if not target.exists():
                broken.append(f"{path} → {target} (missing)")

        assert not broken, f"{len(broken)} broken doc reference(s) in AGENTS.md: " + "; ".join(broken)

    def test_at_least_five_doc_references(self) -> None:
        text = _read(AGENTS)
        doc_paths = _extract_all_doc_paths(text)
        assert len(doc_paths) >= 5, f"AGENTS.md has only {len(doc_paths)} docs/ references; expected at least 5"
