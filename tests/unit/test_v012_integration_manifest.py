"""Structural contract for the v0.1.2 integration manifest."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).parent.parent.parent
MANIFEST = ROOT / "docs" / "releases" / "v0.1.2-integration-manifest.md"
INVENTORY_PATTERN = re.compile(
    r"<!-- v012-integration-inventory:start -->\s*"
    r"```json\s*(.*?)\s*```\s*"
    r"<!-- v012-integration-inventory:end -->",
    re.DOTALL,
)

NATURAL_HARDENING = [
    "5cc1c843aba2f9656f9b637b024d249d73b7fd4a",
    "11411449dbe15fabb00a5f889d82ccef661d94d2",
    "ad997d89690662b7f91643df8a68622d2b4ff76d",
]
NATURAL_EVALUATION = [
    "5cc1c843aba2f9656f9b637b024d249d73b7fd4a",
    "11411449dbe15fabb00a5f889d82ccef661d94d2",
    "43efab68e4872b34997b77a3b18a680185e7598c",
]
NATURAL_ACCEPTANCE = [
    "350434f58780f96e4db0e16192223de3a257231b",
    "f25fe50bcae7f0be0bcb0d941187e51e62e739fe",
    "4d410148defa1faeb0ceb2bf028d79102d1c85c3",
    "5ffb817825396812c46106f5b189fcf5e911d4a0",
    "f4ef07b56544ad6d27d5328fa6563a0b4ea0e937",
    "2a22d8117aebe1c8c3bd2a2cbca2a24ed21dce90",
    "df4302802d8ff7d10c6a04d14522ab15539536ff",
    "085ebc7af82aa0031edbc1a429a8b36db6663fad",
    "0a173c0ccf7475677563d9aa18fd7a3bf02aed35",
    "e19b8ef48d5779fc8a9356b17a88cc6afc59636c",
    "ea12b54b97e3ce389395bc69fb43343362564522",
]
TYPED_DOMAIN = [
    "1f937c6b45c968f9be690485ae1c4705569d7bd3",
    "1b31d64867ca735cf1d5761508c9c701dfa29bdd",
    "4a73385895737654093b89f545aa03e805a2be5c",
    "43315e63f89487b9ff365f1d89c8afee2b45763d",
    "437d976b157d9f029f2fa93e1cc7c50707b76ba2",
    "60e0882fb24d259587e551f9935ba275ec03fba3",
    "9def8309f6146a9e4e7443fe54241edbe6ad28e7",
    "56512a726c873b67e764c8d05e5d470681317a1f",
    "fa9674bdfe5691b0402e6a81ec75b708ba305f60",
    "165b8bdcb1c2ec23e06d9cd5cd0766f807998f02",
    "140824b6f467f4662ebe13bc04a39cdfa93ac356",
    "a72827ec539d3a66114663274a3b3ce9f2d59d22",
    "dbc5444d74e2ca46643502bb42828d2aaccc2596",
    "32ffe159616982f50d8e0d2f8743d0fa9059c0fe",
    "0bbffa7573e67ab42bf4e64a318acf7e958b625d",
    "0769f40e105ec1b829c9e31c7033706380577cba",
]
OPENJEV_LAYA = [
    "7771ba3c5d6d4e347651c9b6a7fc323cd0e941ec",
    "3864112f9b7caba1dfdabcf98f35146e53cbdc96",
    "4a07532555373cf846a0a93048d2593d9e8d44b4",
    "21f8e16040fe8b608aba8e7ce91226c43aaced7e",
    "7d901a84f0378cd84d4e7c8d8a3f41414badf78e",
    "03f4988b163337bb937d60a6e926b14680a4ea9b",
    "d670706e50b41278ae266b0464a3a7e787662e00",
    "f3ba69d406ec58f5890d4a1d2c545f8b753c3f51",
    "7fa24a5c62b2937d2a98ac423cf337c81cbddb6d",
    "96f89954ee2d0cab63045b4912ecac0f048ddce8",
    "ca90c0929962346b2ed62dcdb0ec48e073980155",
    "fefd52d6b5c7c94733a01d8fd1d867d702f5c9a6",
    "77951c828cac146fa3b73710d76d16050724b6c2",
    "4a0c944b5f8499037d05e2ae2a3429d71a23d5d9",
    "762c75dadffcdc4d7661246ad642831d3e04e6ff",
    "6419da16783416340ec6b8efd98608c5806855a2",
    "6b7d3020718f787478afc89c06a8ef1244247a0e",
    "927358986e08eed55687825fb8b5933656aac985",
    "10dbe2243a00c9a3ffe3d2fda8ea8416b685dedb",
    "9b16aec6bac63ae80624cf8b91561d1504f3046c",
    "c48c7619552e5b5bfc42937937476874fef8fcb6",
]


def _text() -> str:
    return MANIFEST.read_text(encoding="utf-8")


def _inventory() -> dict[str, Any]:
    match = INVENTORY_PATTERN.search(_text())
    assert match is not None, "manifest must contain its parseable JSON inventory"
    value = json.loads(match.group(1))
    assert isinstance(value, dict)
    return value


def test_inventory_pins_snapshot_heads_and_pending_state() -> None:
    inventory = _inventory()
    assert inventory["schema_version"] == 1
    assert inventory["development_head"] == (
        "7c4fbc7e695d6a20b1a332cd2ba2285bb08091a3"
    )
    streams = inventory["streams"]
    assert streams["natural_language_config"]["head"] == NATURAL_ACCEPTANCE[-1]
    assert streams["natural_language_config"]["status"] == "pending_descendant"
    assert streams["typed_domain_adapters"]["head"] == TYPED_DOMAIN[-1]
    assert streams["typed_domain_adapters"]["status"] == "complete_source_snapshot"
    assert streams["openjev_laya"]["head"] == OPENJEV_LAYA[-1]
    assert streams["openjev_laya"]["status"] == "complete_source_snapshot"


def test_inventory_pins_exact_source_parent_child_order() -> None:
    streams = _inventory()["streams"]
    assert streams["natural_language_config"]["source_chains"] == {
        "foundation_hardening": NATURAL_HARDENING,
        "foundation_evaluation": NATURAL_EVALUATION,
        "acceptance": NATURAL_ACCEPTANCE,
    }
    assert streams["typed_domain_adapters"]["source_chains"] == {
        "main": TYPED_DOMAIN
    }
    assert streams["openjev_laya"]["source_chains"] == {"main": OPENJEV_LAYA}


def test_feature_only_sets_are_exact_unique_and_nonduplicative() -> None:
    streams = _inventory()["streams"]
    expected = {
        "natural_language_config": [
            *NATURAL_HARDENING,
            NATURAL_EVALUATION[-1],
            NATURAL_ACCEPTANCE[1],
            *NATURAL_ACCEPTANCE[3:],
        ],
        "typed_domain_adapters": [
            *TYPED_DOMAIN[:3],
            *TYPED_DOMAIN[4:7],
            *TYPED_DOMAIN[9:],
        ],
        "openjev_laya": OPENJEV_LAYA[9:],
    }
    actual = {
        name: stream["feature_only_cherry_picks"]
        for name, stream in streams.items()
    }
    assert actual == expected
    flattened = [commit for commits in actual.values() for commit in commits]
    assert len(flattened) == len(set(flattened))

    excluded = {
        commit
        for stream in streams.values()
        for commit in stream["excluded_commits"]
    }
    assert excluded.isdisjoint(flattened)
    assert {
        NATURAL_ACCEPTANCE[0],
        NATURAL_ACCEPTANCE[2],
        TYPED_DOMAIN[3],
        TYPED_DOMAIN[7],
        TYPED_DOMAIN[8],
        *OPENJEV_LAYA[:9],
    } <= excluded


def test_proof_and_integration_boundaries_fail_closed() -> None:
    streams = _inventory()["streams"]
    assert streams["natural_language_config"]["proof_status"] == "pending"
    assert streams["natural_language_config"]["integration_status"] == (
        "blocked_pending_reconciliation"
    )
    for name in ("typed_domain_adapters", "openjev_laya"):
        assert streams[name]["proof_status"] == "partial_branch_local"
        assert streams[name]["integration_status"] == "feature_only_review_ready"
    assert _inventory()["combined_release_proof"] == "pending"


def test_manifest_retains_trust_zdd_rollback_and_practitioner_evidence() -> None:
    text = _text()
    for heading in (
        "## Trust boundaries",
        "## Proof status",
        "## Conflict and overlap inventory",
        "## ZDD and rollback",
        "## Practitioner reports",
        "## Next integration sequence",
    ):
        assert heading in text
    for source in (
        "https://groups.google.com/g/ansible-project/c/xF-v6edS-EQ",
        "https://github.com/ansible/ansible/issues/65687",
        "https://github.com/ollama/ollama/issues/8063",
        "https://github.com/MolSSI/QCSchema/issues/68",
        "https://github.com/cue-lang/cue/issues/1487",
        "https://github.com/NandhaKishorM/laya/issues/302",
        "https://github.com/razorback16/openjev/issues/5",
    ):
        assert source in text
    assert "85% aggregate" in text
    assert "75% per file" in text
    assert "full gate: pending" in text.lower()
