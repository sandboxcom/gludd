"""Structural contract for the v0.1.2 NL configuration reconciliation plan."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).parent.parent.parent
PLAN = (
    ROOT
    / "docs"
    / "releases"
    / "v0.1.2-natural-language-configuration-reconciliation.md"
)
INVENTORY_PATTERN = re.compile(
    r"<!-- v012-nl-reconciliation:start -->\s*"
    r"```json\s*(.*?)\s*```\s*"
    r"<!-- v012-nl-reconciliation:end -->",
    re.DOTALL,
)

BASE = "269106e707a8160a96e2cb79a699afd9f3ce5d86"
DEVELOPMENT_ROOT = "7c4fbc7e695d6a20b1a332cd2ba2285bb08091a3"
FOUNDATION = [
    {
        "commit": "5cc1c843aba2f9656f9b637b024d249d73b7fd4a",
        "parent": DEVELOPMENT_ROOT,
    },
    {
        "commit": "11411449dbe15fabb00a5f889d82ccef661d94d2",
        "parent": "5cc1c843aba2f9656f9b637b024d249d73b7fd4a",
    },
    {
        "commit": "ad997d89690662b7f91643df8a68622d2b4ff76d",
        "parent": "11411449dbe15fabb00a5f889d82ccef661d94d2",
    },
]
EVALUATION = [
    {
        "commit": "43efab68e4872b34997b77a3b18a680185e7598c",
        "parent": "11411449dbe15fabb00a5f889d82ccef661d94d2",
    }
]
ACCEPTANCE = [
    {"commit": "350434f58780f96e4db0e16192223de3a257231b", "parent": DEVELOPMENT_ROOT},
    {
        "commit": "f25fe50bcae7f0be0bcb0d941187e51e62e739fe",
        "parent": "350434f58780f96e4db0e16192223de3a257231b",
    },
    {
        "commit": "4d410148defa1faeb0ceb2bf028d79102d1c85c3",
        "parent": "f25fe50bcae7f0be0bcb0d941187e51e62e739fe",
    },
    {
        "commit": "5ffb817825396812c46106f5b189fcf5e911d4a0",
        "parent": "4d410148defa1faeb0ceb2bf028d79102d1c85c3",
    },
    {
        "commit": "f4ef07b56544ad6d27d5328fa6563a0b4ea0e937",
        "parent": "5ffb817825396812c46106f5b189fcf5e911d4a0",
    },
    {
        "commit": "2a22d8117aebe1c8c3bd2a2cbca2a24ed21dce90",
        "parent": "f4ef07b56544ad6d27d5328fa6563a0b4ea0e937",
    },
    {
        "commit": "df4302802d8ff7d10c6a04d14522ab15539536ff",
        "parent": "2a22d8117aebe1c8c3bd2a2cbca2a24ed21dce90",
    },
    {
        "commit": "085ebc7af82aa0031edbc1a429a8b36db6663fad",
        "parent": "df4302802d8ff7d10c6a04d14522ab15539536ff",
    },
    {
        "commit": "0a173c0ccf7475677563d9aa18fd7a3bf02aed35",
        "parent": "085ebc7af82aa0031edbc1a429a8b36db6663fad",
    },
    {
        "commit": "e19b8ef48d5779fc8a9356b17a88cc6afc59636c",
        "parent": "0a173c0ccf7475677563d9aa18fd7a3bf02aed35",
    },
    {
        "commit": "ea12b54b97e3ce389395bc69fb43343362564522",
        "parent": "e19b8ef48d5779fc8a9356b17a88cc6afc59636c",
    },
    {
        "commit": "d0591e43c826ddbbc9456f83a2fd0b09b82ccfd6",
        "parent": "ea12b54b97e3ce389395bc69fb43343362564522",
    },
    {
        "commit": "84a314d05963519bab331981e1a0d8d86d96e42b",
        "parent": "d0591e43c826ddbbc9456f83a2fd0b09b82ccfd6",
    },
]
CANONICAL_ORDER = [
    *(entry["commit"] for entry in FOUNDATION),
    EVALUATION[0]["commit"],
    ACCEPTANCE[1]["commit"],
    *(entry["commit"] for entry in ACCEPTANCE[3:]),
]


def _text() -> str:
    return PLAN.read_text(encoding="utf-8")


def _inventory() -> dict[str, Any]:
    match = INVENTORY_PATTERN.search(_text())
    assert match is not None, "plan must contain its parseable JSON inventory"
    value = json.loads(match.group(1))
    assert isinstance(value, dict)
    return value


def test_inventory_pins_base_exact_parents_and_one_linear_order() -> None:
    inventory = _inventory()
    assert inventory["schema_version"] == 1
    assert inventory["plan_base"] == BASE
    assert inventory["development_root"] == DEVELOPMENT_ROOT
    assert inventory["source_chains"] == {
        "foundation_hardening": FOUNDATION,
        "foundation_evaluation_sibling": EVALUATION,
        "acceptance": ACCEPTANCE,
    }
    assert inventory["canonical_source_order"] == CANONICAL_ORDER
    assert len(CANONICAL_ORDER) == len(set(CANONICAL_ORDER))
    assert inventory["canonical_strategy"] == "linear_cherry_pick_reconciliation"
    assert inventory["final_source_head"] == ACCEPTANCE[-1]["commit"]


def test_conflicts_select_exact_single_source_blobs() -> None:
    inventory = _inventory()
    assert inventory["already_present"] == [ACCEPTANCE[0]["commit"]]
    assert inventory["excluded_commits"] == {
        ACCEPTANCE[2]["commit"]: "superseded_manifest_only_snapshot"
    }
    resolutions = inventory["conflict_resolutions"]
    assert resolutions["core_exact_blob_duplicates"] == {
        "src/general_ludd/configuration/catalog.py": (
            "484fe8a4f528afa81ea39429cf5166f03f8d4721"
        ),
        "src/general_ludd/configuration/contracts.py": (
            "31e87392b2c6314d17f19e8127a581e156fd2a1f"
        ),
        "src/general_ludd/configuration/planner.py": (
            "e66ddf4070e958d58123fcfe51509b2f9b007e7d"
        ),
    }
    assert resolutions["core_resolution"] == "keep_foundation_blob"
    assert resolutions["collection_resolution"] == "keep_acceptance_lineage_blob"
    assert len(resolutions["collection_final_blobs"]) == 10
    assert resolutions["manifest_resolution"] == "keep_plan_base_refresh"
    assert resolutions["shared_target_resolution"] == "union_each_named_target_once"


def test_plan_retains_validation_zdd_rollback_and_forum_evidence() -> None:
    text = _text()
    for heading in (
        "## Exact execution sequence",
        "## Conflict decisions by file",
        "## Validation matrix",
        "## ZDD activation and rollback",
        "## No-independent-recreation proof",
        "## Long-lived practitioner evidence",
    ):
        assert heading in text
    for phrase in (
        "85% aggregate",
        "75% per file",
        "make gate",
        "changed: false",
        "mutation_performed: false",
        "d0591e43c826ddbbc9456f83a2fd0b09b82ccfd6",
        "84a314d05963519bab331981e1a0d8d86d96e42b",
        "https://groups.google.com/g/ansible-project/c/xF-v6edS-EQ",
        "https://groups.google.com/g/ansible-project/c/H1JwC9i4y3U",
    ):
        assert phrase in text
