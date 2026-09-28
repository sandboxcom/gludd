"""Structural contract for the v0.1.2 FreeLLMAPI upstream-sync boundary."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).parent.parent.parent
CONTRACT = ROOT / "docs" / "releases" / "v0.1.2-freellmapi-upstream-sync-contract.md"
STATUS_PATTERN = re.compile(
    r"<!-- v012-freellmapi-sync-status:start -->\s*"
    r"```json\s*(.*?)\s*```\s*"
    r"<!-- v012-freellmapi-sync-status:end -->",
    re.DOTALL,
)

LATEST_COMMIT = "e4a47f203dba3b610dc180f628d3e821abbaf564"
CANDIDATE_COMMIT = "4191d8e7abef39fcd93fab009123467036f39750"
ARTIFACT_COMMIT = "780a7d8d6dcbc818eb10ec17da210635b569ae22"


def _text() -> str:
    return CONTRACT.read_text(encoding="utf-8")


def _status() -> dict[str, Any]:
    match = STATUS_PATTERN.search(_text())
    assert match is not None, "contract needs a parseable status block"
    value = json.loads(match.group(1))
    assert isinstance(value, dict)
    return value


def test_status_pins_latest_candidate_and_artifact_as_distinct_identities() -> None:
    status = _status()
    assert status["schema_version"] == 1
    assert status["reviewed_at"] == "2026-09-28"
    assert status["base_commit"] == (
        "dcd27ffdd9425eb5ff80e1d037b0e6066dccb017"
    )
    assert status["upstream_observation"] == {
        "repository": "tashfeenahmed/freellmapi",
        "latest_release": "v0.12.0",
        "full_commit": LATEST_COMMIT,
        "released_on": "2026-09-24",
        "signature_observation": "github_verified_commit",
        "gludd_state": "observed_not_candidate_locked",
    }
    assert status["v011_baseline"] == {
        "admitted_scoring_source_release": "v0.9.9",
        "admitted_scoring_source_commit": ARTIFACT_COMMIT,
        "candidate_release": "v0.11.1",
        "candidate_commit": CANDIDATE_COMMIT,
        "candidate_state": "pending_frozen_delta",
        "candidate_runtime_admitted": False,
        "scoring_mode": "default_off_shadow_only",
    }


def test_machine_contract_keeps_ownership_and_proof_fail_closed() -> None:
    status = _status()
    assert status["ownership"] == {
        "copied_upstream_source_allowed": False,
        "copied_upstream_tests_allowed": False,
        "server_or_sidecar_allowed": False,
        "generated_artifact_rule": "attributed_digest_bound_output_only",
        "gludd_owned": [
            "bridge_abi",
            "bounded_validation",
            "candidate_lock",
            "catalog_adapter",
            "generation_pointer",
            "provenance_receipt",
        ],
        "upstream_owned": [
            "catalog_schema",
            "model_config_semantics",
            "provider_quirks",
            "scoring_algorithms",
            "server_migrations",
            "upstream_tests",
        ],
    }
    assert status["proof"] == {
        "v012_sync": "contract_only",
        "v012_compatibility": "not_established",
        "node_24": "unproved",
        "npm_11": "unproved",
        "live_provider": "not_established",
        "schema_migration": "not_executed",
        "full_gate": "pending",
    }


def test_contract_covers_the_complete_sync_and_rollback_workflow() -> None:
    text = _text()
    for heading in (
        "## Scope and status",
        "## Dated upstream facts and Gludd inference",
        "## Current v0.1.1 baseline",
        "## Immutable upstream identity",
        "## Ownership and allowed shim surface",
        "## Update and diff workflow",
        "## Schema and model-config migration",
        "## Upstream test execution and fixture reuse",
        "## Supply-chain verification",
        "## Security and privacy",
        "## ZDD and rollback",
        "## Unresolved Node, npm, and live-provider constraints",
        "## Practitioner reports",
        "## Exit criteria",
    ):
        assert heading in text

    lowered = text.lower()
    for boundary in (
        "freellmapi v0.12.0 compatibility is not established",
        "no upstream source file or upstream test file may be copied into gludd",
        "node 24, npm 11, and live-provider proof remain unresolved",
        "an abi-version match is necessary but not compatibility proof",
        "exact verified archive",
        "run upstream tests unmodified",
        "disabled by default",
        "replacement generation",
        "fail closed",
        "85% aggregate",
        "75% per file",
        "full gate remains pending",
    ):
        assert boundary in lowered

    assert text.count("**Source fact.**") >= 5
    assert text.count("**Gludd inference/requirement.**") >= 5


def test_contract_links_exact_upstream_and_local_authorities() -> None:
    text = _text()
    for source in (
        "https://github.com/tashfeenahmed/freellmapi",
        "https://github.com/tashfeenahmed/freellmapi/releases/tag/v0.12.0",
        f"https://github.com/tashfeenahmed/freellmapi/commit/{LATEST_COMMIT}",
        f"https://github.com/tashfeenahmed/freellmapi/blob/{LATEST_COMMIT}/package.json",
        f"https://github.com/tashfeenahmed/freellmapi/blob/{LATEST_COMMIT}/server/package.json",
        f"https://github.com/tashfeenahmed/freellmapi/blob/{LATEST_COMMIT}/package-lock.json",
        f"https://github.com/tashfeenahmed/freellmapi/blob/{LATEST_COMMIT}/LICENSE",
        f"https://github.com/tashfeenahmed/freellmapi/blob/{LATEST_COMMIT}/.github/workflows/ci.yml",
        "https://github.com/tashfeenahmed/freellmapi/releases/tag/v0.11.1",
        f"https://github.com/tashfeenahmed/freellmapi/commit/{CANDIDATE_COMMIT}",
        "https://github.com/tashfeenahmed/freellmapi/issues/1283",
        "https://github.com/tashfeenahmed/freellmapi/issues/671",
        "https://github.com/tashfeenahmed/freellmapi/issues/880",
        "https://github.com/tashfeenahmed/freellmapi/issues/608",
        "https://github.com/tashfeenahmed/freellmapi/issues/35",
        "https://github.com/tashfeenahmed/freellmapi/discussions/533",
        "https://github.com/tashfeenahmed/freellmapi/pull/1212",
    ):
        assert source in text

    for local_source in (
        "(v0.1.1.md)",
        "(../design/FREELLMAPI_UPSTREAM_INTEGRATION.md)",
        "(../../config/freellmapi/upstream_candidate.json)",
        "(../../config/freellmapi/upstream_build_plan.json)",
        "(../../src/general_ludd/models/vendor/freellmapi/scoring_kernel.json)",
    ):
        assert local_source in text


def test_contract_forbids_copied_ownership_and_unsupported_compatibility() -> None:
    lowered = _text().lower()
    for unsupported_claim in (
        "freellmapi v0.12.0 is compatible with gludd",
        "gludd supports freellmapi v0.12.0",
        "drop-in compatible",
        "fully compatible with freellmapi",
        "v0.12.0 is runtime admitted",
        "upstream tests are vendored",
        "gludd owns freellmapi's scoring algorithms",
        "gludd owns freellmapi's catalog schema",
        "gludd supports node 24",
        "gludd supports npm 11",
        "live-provider proof is complete",
        "the freellmapi server is embedded",
        '"candidate_runtime_admitted": true',
        '"v012_compatibility": "established"',
        '"full_gate": "passed"',
    ):
        assert unsupported_claim not in lowered
