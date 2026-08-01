"""Behavioral tests for structured spec-quality auditing."""

from pathlib import Path

import pytest
from scripts import audit_spec_entry, check_spec_quality_ratio
from scripts.audit_spec_entry import (
    check_spec_quality,
    has_measurable_outcome,
    has_specific_enforcement,
    parse_specs,
)
from scripts.check_spec_quality_ratio import has_real_enforcement

ROOT = Path(__file__).resolve().parents[2]
SPECS = ROOT / "docs" / "specs" / "BEHAVIORAL_SPECS.md"


def test_named_guard_and_binary_outcome_are_measurable() -> None:
    body = """
**Category:** Release Discipline
**Enforcement:** `_release-ci-green-guard` in Makefile
**Behavior:** A release requires CI green on the exact commit; otherwise the
guard denies the operation and records the failed check.
"""

    assert check_spec_quality("AA001", "release guard", body) == []
    assert has_real_enforcement(body)


def test_make_target_without_literal_make_prefix_is_concrete() -> None:
    body = """
**Category:** CI Discipline
**Enforcement:** `ci-busy-check` on all push targets
**Behavior:** Every push invokes the named prerequisite and is blocked while a
run is queued or in progress.
"""

    assert check_spec_quality("AA002", "push guard", body) == []
    assert has_real_enforcement(body)


def test_vague_behavior_still_fails_quality_gate() -> None:
    body = """
**Category:** Quality Gate
**Enforcement:** `make lint-specs`
**Behavior:** The agent should consider checking documentation when possible.
"""

    violations = check_spec_quality("AA003", "vague", body)

    assert any("measurable" in item for item in violations)
    assert any("vague language" in item for item in violations)


def test_non_selected_heading_ends_selected_spec() -> None:
    text = """### AA001 — selected
**Enforcement:** `make lint-specs`
**Behavior:** Every run is verified.

### SEC001 — another family
**Enforcement:** planned
**Behavior:** template spec filler
"""

    assert parse_specs(text) == [
        (
            "AA001",
            "selected",
            "**Enforcement:** `make lint-specs`\n**Behavior:** Every run is verified.",
        )
    ]
    assert check_spec_quality_ratio.parse_specs(text) == [
        (
            "AA001",
            "**Enforcement:** `make lint-specs`\n**Behavior:** Every run is verified.",
        )
    ]


def test_missing_and_planned_enforcement_are_rejected() -> None:
    assert not has_specific_enforcement("**Behavior:** Every run is blocked.")
    assert not has_specific_enforcement(
        "**Enforcement:** planned plugin\n**Behavior:** Every run is blocked."
    )
    assert not has_real_enforcement("**Behavior:** Every run is blocked.")
    assert not has_real_enforcement(
        "**Enforcement:** future `guard`\n**Behavior:** Every run is blocked."
    )


def test_missing_behavior_is_not_measurable() -> None:
    assert not has_measurable_outcome("**Enforcement:** `make lint-specs`")


def test_short_filler_and_missing_fields_report_all_relevant_violations() -> None:
    violations = check_spec_quality(
        "AA004",
        "bad",
        "The plugin MUST block this behavior; agent should consider it when possible.",
    )

    assert any("body too short" in item for item in violations)
    assert any("template filler" in item for item in violations)
    assert "missing Behavior: field" in violations
    assert "missing Enforcement: field" in violations


def test_audit_main_reports_missing_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(audit_spec_entry, "SPECS_FILE", tmp_path / "missing.md")

    assert audit_spec_entry.main() == 1
    assert "not found" in capsys.readouterr().out


def test_audit_main_reports_draft_and_pass(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    specs_file = tmp_path / "specs.md"
    monkeypatch.setattr(audit_spec_entry, "SPECS_FILE", specs_file)
    specs_file.write_text("### AA001 — bad\nshort", encoding="utf-8")

    assert audit_spec_entry.main() == 1
    assert "1/1 specs are DRAFT" in capsys.readouterr().out

    specs_file.write_text(SPECS.read_text(encoding="utf-8"), encoding="utf-8")
    assert audit_spec_entry.main() == 0
    assert "All 200 specs pass" in capsys.readouterr().out


def test_ratio_main_handles_missing_empty_failing_and_passing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    specs_file = tmp_path / "specs.md"
    monkeypatch.setattr(check_spec_quality_ratio, "SPECS_FILE", specs_file)

    assert check_spec_quality_ratio.main() == 0

    specs_file.write_text("", encoding="utf-8")
    assert check_spec_quality_ratio.main() == 0
    assert "0/0" in capsys.readouterr().out

    specs_file.write_text(
        "### AA001 — pending\n**Enforcement:** planned plugin\n"
        "**Behavior:** Every run is blocked.\n",
        encoding="utf-8",
    )
    assert check_spec_quality_ratio.main() == 1
    assert "BLOCKED" in capsys.readouterr().out

    specs_file.write_text(SPECS.read_text(encoding="utf-8"), encoding="utf-8")
    assert check_spec_quality_ratio.main() == 0
    assert "100.0%" in capsys.readouterr().out


def test_all_tracked_aa_ab_specs_pass_the_structured_gate() -> None:
    failures = {
        spec_id: check_spec_quality(spec_id, title, body)
        for spec_id, title, body in parse_specs(SPECS.read_text(encoding="utf-8"))
        if check_spec_quality(spec_id, title, body)
    }

    assert failures == {}
