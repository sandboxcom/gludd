"""Structural contract for the v0.1.2 integration-preview release notes."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from scripts.release_page_notes import build_release_page_notes, load_release_ledger

ROOT = Path(__file__).parent.parent.parent
NOTES = ROOT / "docs" / "releases" / "v0.1.2.md"
MANIFEST = ROOT / "docs" / "releases" / "v0.1.2-integration-manifest.md"
STATUS_PATTERN = re.compile(
    r"<!-- v012-release-status:start -->\s*"
    r"```json\s*(.*?)\s*```\s*"
    r"<!-- v012-release-status:end -->",
    re.DOTALL,
)
INVENTORY_PATTERN = re.compile(
    r"<!-- v012-integration-inventory:start -->\s*"
    r"```json\s*(.*?)\s*```\s*"
    r"<!-- v012-integration-inventory:end -->",
    re.DOTALL,
)
CLAIMS_PATTERN = re.compile(
    r"<!-- v012-release-claims:start -->\s*(.*?)\s*"
    r"<!-- v012-release-claims:end -->",
    re.DOTALL,
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _json_block(path: Path, pattern: re.Pattern[str]) -> dict[str, Any]:
    match = pattern.search(_read(path))
    assert match is not None, f"{path.name} must contain its parseable JSON block"
    value = json.loads(match.group(1))
    assert isinstance(value, dict)
    return value


def test_release_status_is_machine_readable_and_matches_manifest() -> None:
    status = _json_block(NOTES, STATUS_PATTERN)
    inventory = _json_block(MANIFEST, INVENTORY_PATTERN)

    assert status["schema_version"] == 1
    assert status["manifest_commit"] == (
        "2bff9cb0dc5fd6cc54b11c82288caff9f892c117"
    )
    assert status["release_status"] == "draft_integration_preview"
    assert status["combined_release_proof"] == inventory["combined_release_proof"]
    assert status["full_gate"] == "pending"

    for stream_name, manifest_stream in inventory["streams"].items():
        release_stream = status["streams"][stream_name]
        for field in ("head", "status", "proof_status", "integration_status"):
            assert release_stream[field] == manifest_stream[field]

    assert status["streams"]["natural_language_config"]["default_state"] == (
        "disabled"
    )
    assert status["streams"]["typed_domain_adapters"]["default_state"] == (
        "unwired"
    )
    openjev = status["streams"]["openjev_laya"]
    assert openjev["default_state"] == "disabled"
    assert openjev["runtime_admission"] == "not_implemented"
    assert openjev["recovery_execution"] == "not_implemented"
    assert openjev["long_lived_evidence"] == "not_established"


def test_release_notes_retain_all_user_and_operator_boundaries() -> None:
    text = _read(NOTES)
    for heading in (
        "## Release status",
        "## Natural-language local configuration",
        "## Universal typed-domain work",
        "## OpenJev/Laya offline integration",
        "## Compatibility and upgrade notes",
        "## Security and privacy",
        "## Disabled by default",
        "## ZDD and rollback",
        "## Evidence status",
        "## Known limitations",
        "## Source map",
    ):
        assert heading in text

    lowered = text.lower()
    for boundary in (
        "integration inventory, not release evidence",
        "pending descendant",
        "blocked pending reconciliation",
        "non-mutating plan/status",
        "managed-host mutation remains forbidden",
        "partial branch-local evidence",
        "recommendation-only",
        "runtime unwired",
        "runtime admission is not implemented",
        "recovery execution is not implemented",
        "long-lived evidence is not established",
        "full gate is pending",
        "85% aggregate",
        "75% per file",
        "disabled by default",
        "no automatic enablement",
    ):
        assert boundary in lowered


def test_every_normative_claim_has_a_manifest_contract_or_runbook_link() -> None:
    text = _read(NOTES)
    claims_match = CLAIMS_PATTERN.search(text)
    assert claims_match is not None, "release notes need a bounded claim table"
    rows = [
        line
        for line in claims_match.group(1).splitlines()
        if line.startswith("| ")
        and not line.startswith("| Claim ")
        and not line.startswith("| ---")
    ]
    assert len(rows) >= 12
    assert all("](" in row for row in rows)

    for link in (
        "(v0.1.2-integration-manifest.md#scope-and-snapshot-boundary)",
        "(v0.1.2-integration-manifest.md#proof-status)",
        "(v0.1.2-integration-manifest.md#zdd-and-rollback)",
        "(v0.1.2-integration-manifest.md#practitioner-reports)",
        "(../features/NATURAL_LANGUAGE_CONFIGURATION_ACCEPTANCE.md)",
        "(../features/NATURAL_LANGUAGE_CONFIGURATION_EVALUATION.md)",
        "(../features/TYPED_DECISION_IMPLEMENTATION_CONTRACT.md)",
        "(../features/TYPED_DECISION_DOMAIN_ADAPTER_EVALUATION_RUNBOOK.md)",
        "(v0.1.2-openjev-laya-adapter-contract.md)",
        "(../operations/OPENJEV_LAYA_ADAPTER_RUNBOOK.md)",
    ):
        assert link in text


def test_release_notes_do_not_claim_unproved_completion() -> None:
    lowered = _read(NOTES).lower()
    for unsupported_claim in (
        "status: released",
        "generally available",
        "production-ready",
        "release-ready",
        "release complete",
        "release-complete",
        "feature complete",
        "feature-complete",
        "full gate passed",
        "full gate is green",
        "runtime admission implemented",
        "recovery execution implemented",
        "live openjev integration",
        "live laya integration",
    ):
        assert unsupported_claim not in lowered


@pytest.mark.parametrize(
    "original_contract",
    [
        test_release_status_is_machine_readable_and_matches_manifest,
        test_release_notes_retain_all_user_and_operator_boundaries,
        test_every_normative_claim_has_a_manifest_contract_or_runbook_link,
        test_release_notes_do_not_claim_unproved_completion,
    ],
    ids=["status", "operator-boundaries", "claim-links", "no-unproved-completion"],
)
def test_generator_retains_original_release_note_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    original_contract: Callable[[], None],
) -> None:
    ledger = load_release_ledger(
        ROOT / "config" / "v012_completed_backlog_reconciliation.json",
        "v0.1.2",
    )
    generated = tmp_path / "v0.1.2.md"
    generated.write_text(build_release_page_notes(ledger), encoding="utf-8")
    monkeypatch.setitem(globals(), "NOTES", generated)

    original_contract()
