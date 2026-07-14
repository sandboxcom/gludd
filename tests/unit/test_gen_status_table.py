"""tests/unit/test_gen_status_table.py

Unit tests for scripts/gen_status_table.py.

Tests are intentionally hermetic — no subprocess, no pytest invocations, no
network, no disk I/O beyond reading fixtures written inline.  FeatureVerifier is
passed a fake runner that never executes pytest so the suite stays fast.
"""
from __future__ import annotations

import textwrap
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

import pytest
from gen_status_table import (
    _END_MARKER,
    _START_MARKER,
    _badge,
    _extract_between_markers,
    _fast_check_test_ref,
    _generate_block,
    _inject_into_readme,
    _load_manifest,
    _render_section,
    _strip_mode_artifacts,
    _verify_feature,
    main,
)

from general_ludd.quality.feature_verifier import FeatureVerifier

# ── path setup (now handled by tests/conftest.py) ──────────────────────────
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# ── helpers ──────────────────────────────────────────────────────────────────


def _make_verifier(repo_root: Path | None = None) -> FeatureVerifier:
    """Return a FeatureVerifier with a runner that always fails (no pytest)."""
    root = repo_root or _REPO_ROOT
    return FeatureVerifier(repo_root=str(root), runner=lambda _node_id: 1)


def _minimal_sections(tmp_path: Path) -> list[dict[str, Any]]:
    """A minimal two-section manifest whose evidence refs are satisfiable on disk."""
    # Create the fake test file and role dir so fast-mode checks pass.
    test_file = tmp_path / "tests" / "unit" / "test_example.py"
    test_file.parent.mkdir(parents=True, exist_ok=True)
    test_file.write_text("# placeholder\n")

    role_dir = (
        tmp_path
        / "collections"
        / "ansible_collections"
        / "general_ludd"
        / "agent"
        / "roles"
        / "my_role"
    )
    role_dir.mkdir(parents=True, exist_ok=True)

    return [
        {
            "title": "Alpha Section",
            "features": [
                {
                    "id": "feat-a",
                    "title": "Feature A — fully verified",
                    "pct": 100,
                    "evidence_refs": [
                        "test:tests/unit/test_example.py",
                        "role:my_role",
                    ],
                    "notes": "`[abc1234]`",
                },
                {
                    "id": "feat-b",
                    "title": "Feature B — no evidence",
                    "pct": 0,
                    "evidence_refs": [],
                    "notes": "design-only",
                },
            ],
        },
        {
            "title": "Beta Section",
            "features": [
                {
                    "id": "feat-c",
                    "title": "Feature C — file ref",
                    "pct": 80,
                    "evidence_refs": [
                        "file:tests/unit/test_example.py::placeholder",
                    ],
                    "notes": "file present",
                },
            ],
        },
    ]


# ── _load_manifest ────────────────────────────────────────────────────────────


class TestLoadManifest:
    def test_loads_sections_list(self, tmp_path: Path) -> None:
        manifest = tmp_path / "features.yml"
        manifest.write_text(
            textwrap.dedent(
                """\
                sections:
                  - title: "Section One"
                    features:
                      - id: f1
                        title: "F1"
                        pct: 100
                        evidence_refs: []
                        notes: ""
                """
            )
        )
        sections = _load_manifest(manifest)
        assert len(sections) == 1
        assert sections[0]["title"] == "Section One"
        assert sections[0]["features"][0]["id"] == "f1"

    def test_empty_sections_returns_list(self, tmp_path: Path) -> None:
        manifest = tmp_path / "features.yml"
        manifest.write_text("sections: []\n")
        assert _load_manifest(manifest) == []

    def test_missing_sections_key_returns_empty(self, tmp_path: Path) -> None:
        manifest = tmp_path / "features.yml"
        manifest.write_text("other_key: 42\n")
        assert _load_manifest(manifest) == []


# ── _fast_check_test_ref ─────────────────────────────────────────────────────


class TestFastCheckTestRef:
    def test_file_exists_returns_true(self, tmp_path: Path) -> None:
        f = tmp_path / "tests" / "unit" / "test_foo.py"
        f.parent.mkdir(parents=True)
        f.write_text("# test\n")
        met, detail = _fast_check_test_ref(
            "test:tests/unit/test_foo.py::TestClass::test_bar", tmp_path
        )
        assert met is True
        assert "test_foo.py" in detail

    def test_file_missing_returns_false(self, tmp_path: Path) -> None:
        met, detail = _fast_check_test_ref("test:tests/unit/test_missing.py", tmp_path)
        assert met is False
        assert "not found" in detail

    def test_strips_node_selector(self, tmp_path: Path) -> None:
        f = tmp_path / "tests" / "unit" / "test_abc.py"
        f.parent.mkdir(parents=True)
        f.write_text("# x\n")
        met, _ = _fast_check_test_ref(
            "test:tests/unit/test_abc.py::SomeClass::some_method[param]", tmp_path
        )
        assert met is True

    def test_path_escape_rejected(self, tmp_path: Path) -> None:
        met, detail = _fast_check_test_ref("test:../../etc/passwd", tmp_path)
        assert met is False
        assert "escapes" in detail


# ── _verify_feature ───────────────────────────────────────────────────────────


class TestVerifyFeature:
    def test_no_evidence_refs_returns_requested(self, tmp_path: Path) -> None:
        verifier = _make_verifier(tmp_path)
        feat = {"id": "x", "title": "X", "pct": 0, "evidence_refs": [], "notes": ""}
        result = _verify_feature(verifier, feat, tmp_path, fast=True)
        assert result["status"] == "requested"
        assert result["evidence_results"]["total_count"] == 0

    def test_fast_mode_test_ref_resolved_by_file_presence(
        self, tmp_path: Path
    ) -> None:
        f = tmp_path / "tests" / "unit" / "test_present.py"
        f.parent.mkdir(parents=True)
        f.write_text("# x\n")
        verifier = _make_verifier(tmp_path)
        feat = {
            "id": "y",
            "title": "Y",
            "pct": 100,
            "evidence_refs": ["test:tests/unit/test_present.py"],
            "notes": "",
        }
        result = _verify_feature(verifier, feat, tmp_path, fast=True)
        assert result["status"] == "verified"
        assert result["evidence_results"]["met_count"] == 1

    def test_fast_mode_missing_test_file_not_met(self, tmp_path: Path) -> None:
        verifier = _make_verifier(tmp_path)
        feat = {
            "id": "z",
            "title": "Z",
            "pct": 100,
            "evidence_refs": ["test:tests/unit/test_ghost.py"],
            "notes": "",
        }
        result = _verify_feature(verifier, feat, tmp_path, fast=True)
        assert result["status"] != "verified"
        assert result["evidence_results"]["met_count"] == 0

    def test_full_mode_delegates_to_verifier(self, tmp_path: Path) -> None:
        verifier = _make_verifier(tmp_path)
        feat = {
            "id": "w",
            "title": "W",
            "pct": 50,
            "evidence_refs": ["test:tests/unit/test_anywhere.py"],
            "notes": "",
        }
        # Runner returns rc=1 → test fails → regressed or requested.
        result = _verify_feature(verifier, feat, tmp_path, fast=False)
        assert result["status"] in {"requested", "regressed"}

    def test_file_ref_checked_in_fast_mode(self, tmp_path: Path) -> None:
        src = tmp_path / "src" / "general_ludd" / "daemon.py"
        src.parent.mkdir(parents=True)
        src.write_text("def register_all(): pass\n")
        verifier = _make_verifier(tmp_path)
        feat = {
            "id": "fv",
            "title": "FV",
            "pct": 100,
            "evidence_refs": [
                "file:src/general_ludd/daemon.py::register_all"
            ],
            "notes": "",
        }
        result = _verify_feature(verifier, feat, tmp_path, fast=True)
        assert result["status"] == "verified"

    def test_partial_evidence_returns_implemented(self, tmp_path: Path) -> None:
        f = tmp_path / "tests" / "unit" / "test_partial.py"
        f.parent.mkdir(parents=True)
        f.write_text("# partial\n")
        verifier = _make_verifier(tmp_path)
        feat = {
            "id": "p",
            "title": "P",
            "pct": 60,
            "evidence_refs": [
                "test:tests/unit/test_partial.py",   # exists → met
                "test:tests/unit/test_nope.py",       # absent → not met
            ],
            "notes": "",
        }
        result = _verify_feature(verifier, feat, tmp_path, fast=True)
        assert result["status"] == "implemented"
        assert result["evidence_results"]["met_count"] == 1
        assert result["evidence_results"]["total_count"] == 2


# ── _render_section ───────────────────────────────────────────────────────────


class TestRenderSection:
    def _make_result(self, status: str, met: int = 1, total: int = 1) -> dict[str, Any]:
        return {
            "status": status,
            "verified_at": None,
            "evidence_results": {
                "all_met": met == total and total > 0,
                "met_count": met,
                "total_count": total,
                "per_ref": [],
            },
        }

    def test_section_heading_present(self) -> None:
        section = {
            "title": "My Section",
            "features": [
                {"id": "f", "title": "F", "pct": 100, "evidence_refs": ["test:x.py"], "notes": "OK"}
            ],
        }
        results = [self._make_result("verified")]
        rendered = _render_section(section, results, fast=False)
        assert "### My Section" in rendered

    def test_verified_badge_in_output(self) -> None:
        section = {
            "title": "S",
            "features": [
                {"id": "x", "title": "X feat", "pct": 100, "evidence_refs": ["test:x.py"], "notes": "note"}
            ],
        }
        rendered = _render_section(section, [self._make_result("verified")], fast=False)
        assert "✓" in rendered
        assert "PASS" in rendered

    def test_pending_badge_for_no_evidence(self) -> None:
        section = {
            "title": "S",
            "features": [
                {"id": "y", "title": "Y feat", "pct": 0, "evidence_refs": [], "notes": "design-only"}
            ],
        }
        rendered = _render_section(section, [self._make_result("requested", 0, 0)], fast=False)
        assert "PENDING" in rendered

    def test_fast_suffix_appended_when_fast_with_refs(self) -> None:
        section = {
            "title": "S",
            "features": [
                {"id": "z", "title": "Z feat", "pct": 80, "evidence_refs": ["test:z.py"], "notes": "n"}
            ],
        }
        rendered = _render_section(section, [self._make_result("verified")], fast=True)
        assert "file-refs only" in rendered

    def test_no_fast_suffix_when_no_evidence_refs(self) -> None:
        section = {
            "title": "S",
            "features": [
                {"id": "a", "title": "A feat", "pct": 0, "evidence_refs": [], "notes": "n"}
            ],
        }
        rendered = _render_section(section, [self._make_result("requested", 0, 0)], fast=True)
        # No evidence refs → no fast suffix
        assert "file-refs only" not in rendered

    def test_pct_note_rendered(self) -> None:
        section = {
            "title": "S",
            "features": [
                {
                    "id": "b",
                    "title": "B feat",
                    "pct": 100,
                    "pct_note": "(local)",
                    "evidence_refs": ["molecule:my_scenario"],
                    "notes": "49/49",
                }
            ],
        }
        rendered = _render_section(section, [self._make_result("verified")], fast=False)
        assert "(local)" in rendered

    def test_table_header_row_present(self) -> None:
        section = {"title": "S", "features": []}
        rendered = _render_section(section, [], fast=False)
        assert "Feature / Task" in rendered
        assert "Verified %" in rendered
        assert "Evidence" in rendered


# ── _generate_block ───────────────────────────────────────────────────────────


class TestGenerateBlock:
    def test_produces_both_sections(self, tmp_path: Path) -> None:
        sections = _minimal_sections(tmp_path)
        verifier = _make_verifier(tmp_path)
        block = _generate_block(sections, verifier, tmp_path, fast=True)
        assert "Alpha Section" in block
        assert "Beta Section" in block

    def test_block_is_non_empty(self, tmp_path: Path) -> None:
        sections = _minimal_sections(tmp_path)
        verifier = _make_verifier(tmp_path)
        block = _generate_block(sections, verifier, tmp_path, fast=True)
        assert len(block) > 50

    def test_fast_header_present(self, tmp_path: Path) -> None:
        sections = _minimal_sections(tmp_path)
        verifier = _make_verifier(tmp_path)
        block = _generate_block(sections, verifier, tmp_path, fast=True)
        assert "--fast" in block

    def test_full_mode_header_present(self, tmp_path: Path) -> None:
        sections = _minimal_sections(tmp_path)
        verifier = _make_verifier(tmp_path)
        block = _generate_block(sections, verifier, tmp_path, fast=False)
        assert "gen-status-table" in block

    def test_idempotent_fast_mode(self, tmp_path: Path) -> None:
        sections = _minimal_sections(tmp_path)
        verifier = _make_verifier(tmp_path)
        block_a = _generate_block(sections, verifier, tmp_path, fast=True)
        block_b = _generate_block(sections, verifier, tmp_path, fast=True)
        assert block_a == block_b


# ── _extract_between_markers ──────────────────────────────────────────────────


class TestExtractBetweenMarkers:
    def test_returns_content_between_markers(self) -> None:
        text = f"before\n{_START_MARKER}\nhello world\n{_END_MARKER}\nafter"
        result = _extract_between_markers(text)
        assert result == "\nhello world\n"

    def test_returns_none_when_no_markers(self) -> None:
        assert _extract_between_markers("no markers here") is None

    def test_returns_none_when_only_start_marker(self) -> None:
        assert _extract_between_markers(f"text {_START_MARKER} more") is None

    def test_returns_none_when_end_before_start(self) -> None:
        text = f"{_END_MARKER}\nstuff\n{_START_MARKER}"
        assert _extract_between_markers(text) is None

    def test_empty_content_between_markers(self) -> None:
        text = f"{_START_MARKER}{_END_MARKER}"
        result = _extract_between_markers(text)
        assert result == ""


# ── _inject_into_readme ───────────────────────────────────────────────────────


class TestInjectIntoReadme:
    def _make_readme(self, tmp_path: Path, content: str) -> Path:
        readme = tmp_path / "README.md"
        readme.write_text(content)
        return readme

    def test_replaces_between_existing_markers(self, tmp_path: Path) -> None:
        readme = self._make_readme(
            tmp_path,
            f"# Header\n{_START_MARKER}\nOLD CONTENT\n{_END_MARKER}\n## Next\n",
        )
        new_text = _inject_into_readme(readme, "NEW BLOCK\n")
        assert "NEW BLOCK" in new_text
        assert "OLD CONTENT" not in new_text
        assert _START_MARKER in new_text
        assert _END_MARKER in new_text

    def test_inserts_markers_when_absent_with_heading(self, tmp_path: Path) -> None:
        readme = self._make_readme(
            tmp_path,
            "# Doc\n\n## Feature & Task Completion Status\n\nOld table\n\n## Next Section\n",
        )
        new_text = _inject_into_readme(readme, "GENERATED\n")
        assert _START_MARKER in new_text
        assert _END_MARKER in new_text
        assert "GENERATED" in new_text

    def test_raises_when_no_markers_no_heading(self, tmp_path: Path) -> None:
        readme = self._make_readme(tmp_path, "# No heading that matches\n\nsome content\n")
        with pytest.raises(ValueError, match="neither STATUS-TABLE markers nor"):
            _inject_into_readme(readme, "block\n")

    def test_injected_content_is_between_markers(self, tmp_path: Path) -> None:
        readme = self._make_readme(
            tmp_path,
            f"header\n{_START_MARKER}\nold\n{_END_MARKER}\nfooter",
        )
        new_text = _inject_into_readme(readme, "fresh block\n")
        extracted = _extract_between_markers(new_text)
        assert extracted is not None
        assert "fresh block" in extracted

    def test_idempotent_repeated_inject(self, tmp_path: Path) -> None:
        readme = self._make_readme(
            tmp_path,
            f"A\n{_START_MARKER}\nold\n{_END_MARKER}\nB",
        )
        first = _inject_into_readme(readme, "BLOCK\n")
        readme.write_text(first)
        second = _inject_into_readme(readme, "BLOCK\n")
        assert first == second


# ── _strip_mode_artifacts ─────────────────────────────────────────────────────


class TestStripModeArtifacts:
    """Unit tests for the mode-agnostic normalization used by --check.

    The body (sections, rows, badges, notes) MUST be preserved exactly. Only
    the two generation-mode artifacts (header line + ``*(file-refs only)*``
    suffix) are stripped, so a README written in --fast mode passes a full-mode
    --check (the CI scenario) and vice versa.
    """

    _FAST_HEADER = (
        "*(auto-generated with `--fast`; `test:` refs checked by file existence only —"
        " run `make gen-status-table` locally to verify tests pass)*\n"
    )
    _FULL_HEADER = (
        "*(auto-generated — do not edit between markers; regenerate with `make gen-status-table`)*\n"
    )
    _BODY = (
        "\n### Section One\n"
        "| Feature / Task | Verified % | Evidence |\n"
        "|---|---|---|\n"
        "| Feature A | ✓ 100% | **PASS**: notes |\n"
    )

    def test_strips_fast_header(self) -> None:
        block = self._FAST_HEADER + self._BODY
        assert _strip_mode_artifacts(block) == self._BODY

    def test_strips_full_header(self) -> None:
        block = self._FULL_HEADER + self._BODY
        assert _strip_mode_artifacts(block) == self._BODY

    def test_fast_and_full_normalize_equal(self) -> None:
        """The core invariant: fast-mode block and full-mode block normalize
        to the same string when only the header differs."""
        fast_block = self._FAST_HEADER + self._BODY
        full_block = self._FULL_HEADER + self._BODY
        assert _strip_mode_artifacts(fast_block) == _strip_mode_artifacts(full_block)

    def test_strips_file_refs_suffix(self) -> None:
        block = self._FAST_HEADER + (
            "| Feature A | ✓ 100% | **PASS** *(file-refs only)*: notes |\n"
        )
        normalized = _strip_mode_artifacts(block)
        assert "file-refs only" not in normalized
        assert "**PASS**: notes" in normalized

    def test_strips_multiple_file_refs_suffixes(self) -> None:
        block = (
            self._FAST_HEADER
            + "| A | ✓ 100% | **PASS** *(file-refs only)*: n1 |\n"
            + "| B | ✓ 100% | **PASS** *(file-refs only)*: n2 |\n"
        )
        normalized = _strip_mode_artifacts(block)
        assert normalized.count("file-refs only") == 0

    def test_strips_both_header_and_suffix(self) -> None:
        block = self._FAST_HEADER + (
            "| A | ✓ 100% | **PASS** *(file-refs only)*: notes |\n"
        )
        normalized = _strip_mode_artifacts(block)
        assert "auto-generated" not in normalized
        assert "file-refs only" not in normalized
        assert "**PASS**: notes" in normalized

    def test_no_artifacts_returns_unchanged(self) -> None:
        block = self._BODY
        assert _strip_mode_artifacts(block) == block

    def test_preserves_body_exactly(self) -> None:
        """Regression guard: every body element (section heading, table rows,
        badges, notes, percentages) must survive normalization intact."""
        block = self._FAST_HEADER + self._BODY
        normalized = _strip_mode_artifacts(block)
        assert "### Section One" in normalized
        assert "| Feature / Task | Verified % | Evidence |" in normalized
        assert "| Feature A | ✓ 100% | **PASS**: notes |" in normalized

    def test_strips_header_when_leading_newline_present(self) -> None:
        """The on-disk block extracted via _extract_between_markers starts
        with a leading \\n (the newline immediately after START marker).
        The header-stripping regex must still find the header line."""
        block = "\n" + self._FAST_HEADER + self._BODY
        normalized = _strip_mode_artifacts(block)
        assert "auto-generated" not in normalized.replace("\n", "", 1) or normalized.lstrip() == self._BODY

    def test_does_not_strip_non_generation_parenthetical(self) -> None:
        """A regular parenthetical in a notes field must NOT be stripped."""
        block = self._FAST_HEADER + (
            "| A | ✓ 100% | **PASS**: see PR (review pending) |\n"
        )
        normalized = _strip_mode_artifacts(block)
        assert "(review pending)" in normalized

    def test_does_not_strip_arbitrary_auto_generated_text(self) -> None:
        """The header regex is anchored to a line that STARTS with
        ``*(auto-generated`` and ends with ``)*``. A mention of 'auto-generated'
        in the middle of a notes field must NOT be touched."""
        body_with_mention = (
            "| A | ✓ 100% | **PASS**: this is auto-generated content |\n"
        )
        block = self._FAST_HEADER + body_with_mention
        normalized = _strip_mode_artifacts(block)
        assert "auto-generated content" in normalized


# ── main() integration ────────────────────────────────────────────────────────


class TestMain:
    """Integration tests for main().  Operates on tmp_path fixtures."""

    def _write_minimal_manifest(self, tmp_path: Path) -> Path:
        sections = _minimal_sections(tmp_path)  # creates required test file + role dir
        manifest = tmp_path / "docs" / "features.yml"
        manifest.parent.mkdir(parents=True, exist_ok=True)
        # Serialise sections back to YAML for loading.
        import yaml
        manifest.write_text(cast(Any, yaml).dump({"sections": sections}))
        return manifest

    def _write_readme_with_markers(self, tmp_path: Path) -> Path:
        readme = tmp_path / "README.md"
        readme.write_text(
            f"# Doc\n\n## Feature & Task Completion Status\n\n"
            f"{_START_MARKER}\nOLD TABLE\n{_END_MARKER}\n\n## Next\n"
        )
        return readme

    def _patch_repo_root(self, tmp_path: Path):
        """Context manager: patch gen_status_table._REPO_ROOT to tmp_path."""
        import gen_status_table as gst
        return patch.object(gst, "_REPO_ROOT", tmp_path)

    def test_write_mode_updates_readme(self, tmp_path: Path) -> None:
        manifest = self._write_minimal_manifest(tmp_path)
        readme = self._write_readme_with_markers(tmp_path)
        import gen_status_table as gst
        with patch.object(gst, "_REPO_ROOT", tmp_path):
            rc = main(["--write", "--fast", "--manifest", str(manifest)])
        assert rc == 0
        content = readme.read_text()
        assert "OLD TABLE" not in content
        assert _START_MARKER in content

    def test_check_mode_passes_when_current(self, tmp_path: Path) -> None:
        manifest = self._write_minimal_manifest(tmp_path)
        readme = tmp_path / "README.md"
        import gen_status_table as gst
        with patch.object(gst, "_REPO_ROOT", tmp_path):
            # First write the current table.
            from gen_status_table import _generate_block, _load_manifest

            from general_ludd.quality.feature_verifier import FeatureVerifier
            sections = _load_manifest(manifest)
            verifier = FeatureVerifier(repo_root=str(tmp_path), runner=lambda _: 1)
            block = _generate_block(sections, verifier, tmp_path, fast=True)
            readme.write_text(
                f"# Doc\n{_START_MARKER}\n{block}{_END_MARKER}\n"
            )
            rc = main(["--check", "--fast", "--manifest", str(manifest)])
        assert rc == 0

    def test_check_mode_fails_when_stale(self, tmp_path: Path) -> None:
        manifest = self._write_minimal_manifest(tmp_path)
        readme = tmp_path / "README.md"
        readme.write_text(
            f"# Doc\n{_START_MARKER}\nSTALE CONTENT\n{_END_MARKER}\n"
        )
        import gen_status_table as gst
        with patch.object(gst, "_REPO_ROOT", tmp_path):
            rc = main(["--check", "--fast", "--manifest", str(manifest)])
        assert rc == 1

    def test_check_mode_header_agnostic_full_written_fast_checked(
        self, tmp_path: Path
    ) -> None:
        """README written in FULL mode passes a --fast --check.

        Pins the mode-agnostic behavior: only the header line + file-refs
        suffix differ between modes; the body (sections/rows/badges/notes)
        is the actual contract and must compare equal after normalization.

        We stub _verify_feature to return identical results regardless of
        mode so the BODY is equal in both phases — this isolates the test
        to the header/suffix normalization (not pytest-vs-file-existence
        semantics).
        """
        manifest = self._write_minimal_manifest(tmp_path)
        self._write_readme_with_markers(tmp_path)
        import gen_status_table as gst

        def _stub_verify(verifier, feat, repo_root, fast):
            return {
                "status": "verified",
                "verified_at": None,
                "evidence_results": {
                    "all_met": True,
                    "met_count": len(feat.get("evidence_refs", [])),
                    "total_count": len(feat.get("evidence_refs", [])),
                    "per_ref": [],
                },
            }

        with patch.object(gst, "_REPO_ROOT", tmp_path), \
             patch.object(gst, "_verify_feature", side_effect=_stub_verify):
            # 1. Write README in FULL mode (no --fast) — produces full header.
            rc_write = main(["--write", "--manifest", str(manifest)])
            assert rc_write == 0
            # 2. Check it in --fast mode — produces fast header.
            #    Headers differ; bodies must compare equal post-normalization.
            rc_check = main(["--check", "--fast", "--manifest", str(manifest)])
        assert rc_check == 0

    def test_check_mode_header_agnostic_fast_written_full_checked(
        self, tmp_path: Path
    ) -> None:
        """README written in --fast mode passes a full-mode --check.

        This is the exact CI scenario: ``make gen-status-table`` writes
        ``--fast``; ``playbooks/verify_feature_claims.yml`` runs ``--check``
        in full mode. Before the fix, the header-line diff alone caused CI
        RED even though the body was current.
        """
        manifest = self._write_minimal_manifest(tmp_path)
        self._write_readme_with_markers(tmp_path)
        import gen_status_table as gst

        def _stub_verify(verifier, feat, repo_root, fast):
            return {
                "status": "verified",
                "verified_at": None,
                "evidence_results": {
                    "all_met": True,
                    "met_count": len(feat.get("evidence_refs", [])),
                    "total_count": len(feat.get("evidence_refs", [])),
                    "per_ref": [],
                },
            }

        with patch.object(gst, "_REPO_ROOT", tmp_path), \
             patch.object(gst, "_verify_feature", side_effect=_stub_verify):
            # 1. Write README in --fast mode — produces fast header.
            rc_write = main(["--write", "--fast", "--manifest", str(manifest)])
            assert rc_write == 0
            # 2. Check it in FULL mode (no --fast) — produces full header.
            #    This is the CI playbook's invocation. Headers differ; bodies
            #    must compare equal post-normalization.
            rc_check = main(["--check", "--manifest", str(manifest)])
        assert rc_check == 0

    def test_check_mode_still_fails_on_real_body_diff_after_normalize(
        self, tmp_path: Path
    ) -> None:
        """The check is still strict on the BODY: a genuine content
        difference (e.g. a feature row the manifest has but the on-disk
        table lacks) must fail even after mode-artifact normalization."""
        manifest = self._write_minimal_manifest(tmp_path)
        readme = tmp_path / "README.md"
        # On-disk table has the FAST header but a TOTALLY DIFFERENT body.
        readme.write_text(
            f"# Doc\n{_START_MARKER}\n"
            "*(auto-generated with `--fast`; ...)*\n"
            "### TOTALLY DIFFERENT SECTION\n"
            "| X | ✓ 1% | **PASS**: nothing |\n"
            f"{_END_MARKER}\n"
        )
        import gen_status_table as gst
        with patch.object(gst, "_REPO_ROOT", tmp_path):
            rc = main(["--check", "--fast", "--manifest", str(manifest)])
        assert rc == 1

    def test_check_mode_fails_when_no_markers(self, tmp_path: Path) -> None:
        manifest = self._write_minimal_manifest(tmp_path)
        readme = tmp_path / "README.md"
        readme.write_text("# Doc\n\n## Feature & Task Completion Status\n\nno markers\n")
        import gen_status_table as gst
        with patch.object(gst, "_REPO_ROOT", tmp_path):
            rc = main(["--check", "--fast", "--manifest", str(manifest)])
        assert rc == 1

    def test_check_mode_exits_0_when_no_markers_and_no_heading(self, tmp_path: Path) -> None:
        """Table intentionally removed: README has neither markers nor heading → exit 0."""
        manifest = self._write_minimal_manifest(tmp_path)
        readme = tmp_path / "README.md"
        readme.write_text("# Doc\n\nSome unrelated content. No status table here.\n")
        import gen_status_table as gst
        with patch.object(gst, "_REPO_ROOT", tmp_path):
            rc = main(["--check", "--fast", "--manifest", str(manifest)])
        assert rc == 0

    def test_write_mode_exits_0_when_no_markers_and_no_heading(self, tmp_path: Path) -> None:
        """Table intentionally removed: --write should not inject, should exit 0."""
        manifest = self._write_minimal_manifest(tmp_path)
        readme = tmp_path / "README.md"
        original = "# Doc\n\nSome unrelated content. No status table here.\n"
        readme.write_text(original)
        import gen_status_table as gst
        with patch.object(gst, "_REPO_ROOT", tmp_path):
            rc = main(["--write", "--fast", "--manifest", str(manifest)])
        assert rc == 0
        # README must be unchanged — no injection performed.
        assert readme.read_text() == original

    def test_missing_manifest_exits_1(self, tmp_path: Path) -> None:
        (tmp_path / "README.md").write_text("# x\n")
        import gen_status_table as gst
        with patch.object(gst, "_REPO_ROOT", tmp_path):
            rc = main(["--check", "--fast", "--manifest", str(tmp_path / "missing.yml")])
        assert rc == 1

    def test_out_flag_writes_standalone_file(self, tmp_path: Path) -> None:
        manifest = self._write_minimal_manifest(tmp_path)
        self._write_readme_with_markers(tmp_path)
        out_file = tmp_path / "table.md"
        import gen_status_table as gst
        with patch.object(gst, "_REPO_ROOT", tmp_path):
            rc = main(["--fast", "--manifest", str(manifest), "--out", str(out_file)])
        assert rc == 0
        assert out_file.exists()
        content = out_file.read_text()
        assert "Alpha Section" in content

    def test_no_args_prints_to_stdout(self, tmp_path: Path, capsys) -> None:
        manifest = self._write_minimal_manifest(tmp_path)
        (tmp_path / "README.md").write_text("# x\n")
        import gen_status_table as gst
        with patch.object(gst, "_REPO_ROOT", tmp_path):
            rc = main(["--fast", "--manifest", str(manifest)])
        assert rc == 0
        out = capsys.readouterr().out
        assert "Alpha Section" in out


# ── real-manifest smoke test ─────────────────────────────────────────────────


class TestRealManifestSmoke:
    """Light smoke test against the real docs/features.yml.

    Verifies the manifest is loadable and renders without error.
    Does NOT run pytest; does NOT compare against README.md (that
    would make the test flaky whenever the manifest diverges from the
    checked-in README).
    """

    @pytest.fixture(autouse=True)
    def _require_manifest(self):
        manifest_path = _REPO_ROOT / "docs" / "features.yml"
        assert manifest_path.exists(), (
            f"{manifest_path} is required for manifest smoke tests — "
            "ensure docs/features.yml is tracked and populated"
        )

    def test_real_manifest_loads(self) -> None:
        manifest_path = _REPO_ROOT / "docs" / "features.yml"
        sections = _load_manifest(manifest_path)
        assert len(sections) > 0, "expected at least one section"

    def test_real_manifest_renders_fast(self) -> None:
        manifest_path = _REPO_ROOT / "docs" / "features.yml"
        sections = _load_manifest(manifest_path)
        verifier = FeatureVerifier(repo_root=str(_REPO_ROOT), runner=lambda _: 1)
        block = _generate_block(sections, verifier, _REPO_ROOT, fast=True)
        assert len(block) > 100
        for sec in sections:
            assert sec.get("title", "") in block

    def test_real_manifest_all_features_have_required_keys(self) -> None:
        manifest_path = _REPO_ROOT / "docs" / "features.yml"
        sections = _load_manifest(manifest_path)
        for sec in sections:
            assert "title" in sec, f"section missing title: {sec}"
            for feat in sec.get("features", []):
                assert "id" in feat, f"feature missing id: {feat}"
                assert "title" in feat, f"feature missing title: {feat}"
                assert "pct" in feat, f"feature missing pct: {feat}"
                assert "evidence_refs" in feat, f"feature missing evidence_refs: {feat}"


# ── edge-case / error-path coverage ──────────────────────────────────────────


class TestBadge:
    def test_known_statuses(self) -> None:
        assert _badge("verified") == "✓"
        assert _badge("implemented") == "~"
        assert _badge("requested") == "✗"
        assert _badge("regressed") == "✗!"

    def test_unknown_status_falls_back_to_question_mark(self) -> None:
        # Covers _STATUS_BADGE.get(status, "?") default branch.
        assert _badge("totally-unknown") == "?"


class TestRenderSectionEmptyNotes:
    def test_empty_notes_omits_colon_suffix(self) -> None:
        # Covers the `else` branch of ev_col (no notes).
        section = {
            "title": "S",
            "features": [
                {"id": "n", "title": "No-note feat", "pct": 100, "evidence_refs": ["test:x.py"], "notes": ""}
            ],
        }
        result = {
            "status": "verified",
            "verified_at": None,
            "evidence_results": {"all_met": True, "met_count": 1, "total_count": 1, "per_ref": []},
        }
        rendered = _render_section(section, [result], fast=False)
        # The cell should be just the bold label with no trailing ": ".
        assert "**PASS**" in rendered
        assert "**PASS**: " not in rendered

    def test_missing_notes_key_renders(self) -> None:
        section = {
            "title": "S",
            "features": [
                {"id": "m", "title": "Missing-note feat", "pct": 0, "evidence_refs": []}
            ],
        }
        result = {
            "status": "requested",
            "verified_at": None,
            "evidence_results": {"all_met": False, "met_count": 0, "total_count": 0, "per_ref": []},
        }
        rendered = _render_section(section, [result], fast=False)
        assert "PENDING" in rendered


class TestVerifyFeatureRegressed:
    def test_regressed_when_prior_verified_and_all_refs_fail(self, tmp_path: Path) -> None:
        # Covers the `regressed` branch (prior status verified/implemented + 0 met).
        verifier = _make_verifier(tmp_path)
        feat = {
            "id": "r",
            "title": "R",
            "pct": 100,
            "status": "verified",  # prior status degrades to regressed
            "evidence_refs": ["test:tests/unit/test_absent.py"],
            "notes": "",
        }
        result = _verify_feature(verifier, feat, tmp_path, fast=True)
        assert result["status"] == "regressed"

    def test_requested_when_prior_requested_and_all_refs_fail(self, tmp_path: Path) -> None:
        verifier = _make_verifier(tmp_path)
        feat = {
            "id": "rq",
            "title": "RQ",
            "pct": 0,
            "status": "requested",
            "evidence_refs": ["test:tests/unit/test_absent.py"],
            "notes": "",
        }
        result = _verify_feature(verifier, feat, tmp_path, fast=True)
        assert result["status"] == "requested"


class TestInjectLastSection:
    def test_section_is_last_in_file_no_trailing_heading(self, tmp_path: Path) -> None:
        # Covers the `else: section_end = len(text)` branch in _inject_into_readme
        # (the target heading is the LAST section — no following ## heading).
        readme = tmp_path / "README.md"
        readme.write_text(
            "# Doc\n\n## Feature & Task Completion Status\n\nOld table content\n"
        )
        new_text = _inject_into_readme(readme, "GENERATED LAST\n")
        assert _START_MARKER in new_text
        assert _END_MARKER in new_text
        assert "GENERATED LAST" in new_text
        assert "Old table content" not in new_text


class TestMainErrorPaths:
    def _patch_root(self, tmp_path: Path):
        import gen_status_table as gst
        return patch.object(gst, "_REPO_ROOT", tmp_path)

    def test_missing_readme_exits_1(self, tmp_path: Path) -> None:
        manifest = tmp_path / "docs" / "features.yml"
        manifest.parent.mkdir(parents=True)
        manifest.write_text("sections: []\n")
        # No README.md written.
        with self._patch_root(tmp_path):
            rc = main(["--fast", "--manifest", str(manifest)])
        assert rc == 1

    def test_malformed_yaml_exits_1(self, tmp_path: Path) -> None:
        manifest = tmp_path / "docs" / "features.yml"
        manifest.parent.mkdir(parents=True)
        manifest.write_text("sections: [unclosed\n")  # invalid YAML
        (tmp_path / "README.md").write_text("# x\n")
        with self._patch_root(tmp_path):
            rc = main(["--fast", "--manifest", str(manifest)])
        assert rc == 1

    def test_out_write_error_exits_1(self, tmp_path: Path) -> None:
        manifest = tmp_path / "docs" / "features.yml"
        manifest.parent.mkdir(parents=True)
        manifest.write_text("sections: []\n")
        (tmp_path / "README.md").write_text("# x\n")
        # Point --out at a path whose parent cannot be created (a file, not a dir).
        blocker = tmp_path / "blocker"
        blocker.write_text("i am a file\n")
        bad_out = blocker / "subdir" / "table.md"
        with self._patch_root(tmp_path):
            rc = main(["--fast", "--manifest", str(manifest), "--out", str(bad_out)])
        assert rc == 1
