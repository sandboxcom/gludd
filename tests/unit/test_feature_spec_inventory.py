"""Tests for the comprehensive Gludd feature-spec inventory."""

from __future__ import annotations

import json
from pathlib import Path

import yaml
from feature_spec_inventory import build_inventory, render_human


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _fixture_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    _write(repo / "src/general_ludd/widget.py", "class Widget:\n    pass\n")
    _write(repo / "tests/unit/test_widget.py", "def test_widget():\n    assert True\n")
    _write(
        repo / "docs/features.yml",
        yaml.safe_dump(
            {
                "sections": [
                    {
                        "title": "Features",
                        "features": [
                            {
                                "id": "nf1",
                                "title": "NF.1 — Widget runtime",
                                "pct": 100,
                                "evidence_refs": [
                                    "file:src/general_ludd/widget.py::Widget",
                                    "test:tests/unit/test_widget.py",
                                ],
                            }
                        ],
                    }
                ]
            },
            sort_keys=False,
        ),
    )
    _write(
        repo / "docs/specs/FEATURE_WIDGET.md",
        """\
# Feature: Widget runtime

**Feature ID:** NF.1
**Status: IMPLEMENTED**

## Implementation Plan

| Phase | Scope | Status |
| --- | --- | --- |
| P1 | Add the widget runtime. | done |
| P2 | Add a widget health endpoint. | done |

Files: `src/general_ludd/widget.py`, `tests/unit/test_widget.py`.
""",
    )
    _write(
        repo / "docs/design/ROADMAP.md",
        """\
# Product roadmap

## D.1 — Adaptive scheduler

**Status:** PLANNED

The scheduler will balance queued work.
""",
    )
    _write(
        repo / "docs/specs/FEATURE_NF8_MULTITASK_ENFORCEMENT.md",
        """\
# Feature: OpenCode multitask enforcement

**Feature ID:** NF.8
**Status: IMPLEMENTED**

## Implementation Plan
| Phase | Scope |
| --- | --- |
| P1 | Add enforce-multitask.ts. |
""",
    )
    _write(repo / "docs/guide.md", "# User guide\n\nNo specification here.\n")
    _write(
        repo / "docs/specs/BEHAVIORAL_SPECS.md",
        """\
# Behavioral specs

### AA001 — core-stop-recurrence
**Enforcement:** AGENTS.md
**Test:** `tests/unit/test_stop.py`
**Behavior:** Never stop early.

### A001 — generated-real
**Enforcement:** `enforce-stop.ts`
**Test:** `tests/unit/test_stop.py`
**Behavior:** Keep working.

### A002 — generated-template
**Enforcement:** """
        + ("generic mechanism " * 30)
        + """
**Behavior:** Generic template.

### A003 — generated-missing
**Behavior:** Missing enforcement.
""",
    )
    _write(repo / "AGENTS.md", "# Agent rules\n")
    _write(repo / ".opencode/plugin/enforce-stop.ts", "export const plugin = {};\n")
    _write(repo / "tests/unit/test_stop.py", "def test_stop():\n    assert True\n")
    _write(repo / "BUGS.md", "core stop recurrence happened again\n")
    _write(repo / "config/ratchet.yml", "{}\n")
    return repo


def test_scans_all_document_types_and_deduplicates_aliases(tmp_path: Path) -> None:
    inventory = build_inventory(_fixture_repo(tmp_path))
    gludd = inventory["gludd_features"]
    records = {record["id"]: record for record in gludd["records"]}

    assert set(records) == {"nf1", "nf1:p1", "nf1:p2", "d1"}
    assert len(records["nf1"]["sources"]) == 2
    assert records["nf1"]["claim_status"] == "implemented"
    assert records["nf1"]["verified_status"] == "implemented"
    assert records["d1"]["claim_status"] == "unimplemented"
    assert records["d1"]["verified_status"] == "unknown"
    assert gludd["counts"]["total"] == 4
    assert gludd["counts"]["verified"]["implemented"] == 3
    assert gludd["counts"]["verified"]["unknown"] == 1


def test_excludes_opencode_only_specs_but_accounts_for_every_doc(tmp_path: Path) -> None:
    inventory = build_inventory(_fixture_repo(tmp_path))
    coverage = inventory["source_coverage"]
    paths = {entry["path"]: entry for entry in coverage["files"]}

    opencode = paths["docs/specs/FEATURE_NF8_MULTITASK_ENFORCEMENT.md"]
    assert opencode["disposition"] == "excluded"
    assert opencode["reason"] == "opencode-or-enforcement-only"
    assert paths["docs/guide.md"]["disposition"] == "unrecognized"
    assert coverage["scanned"] == len(paths)
    assert coverage["scanned"] == sum(coverage["dispositions"].values())


def test_behavioral_section_separates_core_generated_and_enforcement_quality(
    tmp_path: Path,
) -> None:
    inventory = build_inventory(_fixture_repo(tmp_path))
    behavioral = inventory["opencode_behavioral"]

    assert behavioral["counts"] == {
        "documented": 4,
        "core": 1,
        "generated": 3,
        "claimed_enforcement": 3,
        "real_enforcement": 1,
        "template_enforcement": 1,
        "missing_enforcement": 2,
        "verified_enforcement": 0,
        "ineffective": 1,
        "core_verified_enforcement": 0,
        "core_missing_enforcement": 1,
        "generated_real_enforcement": 1,
        "generated_template_enforcement": 2,
    }


def test_conflicting_alias_claims_fail_closed_to_unknown(tmp_path: Path) -> None:
    repo = _fixture_repo(tmp_path)
    _write(
        repo / "docs/design/NF1_STATUS.md",
        """\
# NF.1 — Widget runtime

**Status:** NOT IMPLEMENTED
""",
    )

    records = {
        record["id"]: record
        for record in build_inventory(repo)["gludd_features"]["records"]
    }
    assert records["nf1"]["claim_status"] == "unknown"
    assert records["nf1"]["claim_conflict"] is True
    assert records["nf1"]["verified_status"] == "unknown"


def test_unrelated_reused_phase_ids_are_document_namespaced(tmp_path: Path) -> None:
    repo = _fixture_repo(tmp_path)
    manifest = yaml.safe_load((repo / "docs/features.yml").read_text())
    manifest["sections"][0]["features"].append(
        {
            "id": "f1",
            "title": "F.1 — Terraform deployment",
            "pct": 100,
            "evidence_refs": [],
        }
    )
    (repo / "docs/features.yml").write_text(yaml.safe_dump(manifest, sort_keys=False))
    _write(
        repo / "docs/guides/GLM_GUIDE.md",
        """\
# GLM implementation guide

## F1 — Pull-request delivery

The agent opens a pull request for completed work.
""",
    )

    records = {
        record["id"]: record
        for record in build_inventory(repo)["gludd_features"]["records"]
    }
    assert "f1" in records
    assert "doc:guides-glm-guide:f1" in records


def test_named_spec_document_is_counted_without_curated_section_markers(
    tmp_path: Path,
) -> None:
    repo = _fixture_repo(tmp_path)
    _write(
        repo / "docs/design/specs/SPEC_BUDGET.md",
        """\
# SPEC — Budget telemetry

**Status:** DRAFT

Cost rates are currently zero. Seed them from the catalog.
""",
    )

    records = {
        record["id"]: record
        for record in build_inventory(repo)["gludd_features"]["records"]
    }
    assert "doc:design-specs-spec-budget" in records
    assert records["doc:design-specs-spec-budget"]["claim_status"] == "unimplemented"


def test_human_and_json_contract_expose_distinct_claim_and_verification_counts(
    tmp_path: Path,
) -> None:
    inventory = build_inventory(_fixture_repo(tmp_path))
    human = render_human(inventory)
    serialized = json.loads(json.dumps(inventory))

    assert "Gludd enhancement/feature specifications" in human
    assert "Claimed status:" in human
    assert "Verified status:" in human
    assert "OpenCode behavioral/enforcement specifications" in human
    assert "Grand documented total: 8" in human
    assert serialized["grand_total"]["documented"] == 8


def test_real_repository_inventory_is_broader_than_features_manifest() -> None:
    repo = Path(__file__).resolve().parent.parent.parent
    inventory = build_inventory(repo)

    assert inventory["gludd_features"]["counts"]["total"] > 42
    assert inventory["opencode_behavioral"]["counts"]["generated"] == 4000
    assert inventory["opencode_behavioral"]["counts"]["core"] > 0
