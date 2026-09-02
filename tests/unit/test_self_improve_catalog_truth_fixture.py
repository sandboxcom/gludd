"""Tracked acceptance fixture for the proven catalog-truth task."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from scripts.run_self_improve_e2e import TaskSpec

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "config/self-improve/catalog-truth.json"
DOCUMENT = ROOT / "docs/features/SELF_IMPROVEMENT_CATALOG_TRUTH_FIXTURE.md"
BASELINE = "eac05dc88c03f14fbd7dd5f4c6d72943609d9e26"
REFERENCE = "80b381bd87f32487d784964ce93566e3b016b191"
FIXTURE_SHA256 = "30fa70cb42c5dd408a0b3d4678bbf90b419706faac4d120d658a9b05548dbaac"


def _target_block(makefile: str, name: str) -> str:
    marker = f"\n{name}:"
    return makefile.split(marker, 1)[1].split("\n\n", 1)[0]


def test_catalog_truth_fixture_reuses_strict_task_spec() -> None:
    spec = TaskSpec.from_path(FIXTURE)

    assert spec == TaskSpec(
        task_id="S83.134",
        objective=(
            "Fix the local coding model catalog artifact repository and filename "
            "mappings and add regression coverage."
        ),
        canonical_make_commands=(
            'make test-specific TESTFILE=tests/unit/test_e2e_model_configs.py '
            'PYTEST_ARGS="-q -W error --tb=short"',
            'make lint-files FILES="src/general_ludd/local_model/'
            '_local_model_configs.py tests/unit/test_e2e_model_configs.py"',
            'make typecheck-scope FILES="src/general_ludd/local_model/'
            '_local_model_configs.py tests/unit/test_e2e_model_configs.py"',
            "make lint-docstrings "
            "DOCSTRING_FILES=src/general_ludd/local_model/_local_model_configs.py",
        ),
        reference_elapsed_seconds=600.0,
    )
    fixture_text = FIXTURE.read_text(encoding="utf-8")
    payload = json.loads(fixture_text)
    assert fixture_text == json.dumps(payload, sort_keys=True, separators=(",", ":"))
    assert set(payload) == {
        "task_id",
        "objective",
        "canonical_make_commands",
        "reference_elapsed_seconds",
    }


def test_catalog_truth_fixture_bytes_are_immutable() -> None:
    assert hashlib.sha256(FIXTURE.read_bytes()).hexdigest() == FIXTURE_SHA256


def test_catalog_truth_make_target_is_pinned_and_safe_by_default() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    target = _target_block(makefile, "test-self-improve-catalog-truth")

    assert "SELF_IMPROVE_CATALOG_LIVE ?= 0" in makefile
    assert 'case "$(SELF_IMPROVE_CATALOG_LIVE)" in 0|1)' in target
    assert f"SELF_IMPROVE_BASELINE_REF={BASELINE}" in target
    assert f"SELF_IMPROVE_REFERENCE_REF={REFERENCE}" in target
    assert "SELF_IMPROVE_TASK_FILE=config/self-improve/catalog-truth.json" in target
    assert "SELF_IMPROVE_MAX_ATTEMPTS=2" in target
    assert "SELF_IMPROVE_MODEL_PATH=" in target
    assert (
        'SELF_IMPROVE_VALIDATE_ONLY="$(if $(filter '
        '1,$(SELF_IMPROVE_CATALOG_LIVE)),0,1)"'
    ) in target
    assert FIXTURE_SHA256 in target
    assert (
        f'[ "$$ACTUAL_FIXTURE_SHA256" = "{FIXTURE_SHA256}" ]'
        in target
    )
    assert "sha256" in target.lower()
    assert target.index(FIXTURE_SHA256) < target.index(
        "$(MAKE) --no-print-directory test-self-improve"
    )
    assert "release-" not in target
    assert "git tag" not in target


def test_catalog_truth_target_has_complete_safe_make_contract() -> None:
    contract = json.loads(
        (ROOT / "config/make_target_contract.json").read_text(encoding="utf-8")
    )
    entry = next(
        item
        for item in contract["targets"]
        if item["name"] == "test-self-improve-catalog-truth"
    )

    assert entry == {
        "name": "test-self-improve-catalog-truth",
        "make_variables": ["SELF_IMPROVE_CATALOG_LIVE"],
        "behavior": (
            "make test-self-improve-catalog-truth "
            "SELF_IMPROVE_CATALOG_LIVE=0"
        ),
    }
    help_text = (ROOT / "Makefile").read_text(encoding="utf-8")
    assert "test-self-improve-catalog-truth" in help_text.split(
        "# --- Git ---", 1
    )[0]


def test_catalog_truth_fixture_documents_lifecycle_and_central_evidence() -> None:
    document = DOCUMENT.read_text(encoding="utf-8")

    for fact in (
        BASELINE,
        REFERENCE,
        FIXTURE_SHA256,
        "SELF_IMPROVEMENT_ACCEPTANCE_MATRIX.md"
        "#evaluation-practice-and-practitioner-evidence",
        "SELF_IMPROVE_CATALOG_LIVE=1",
        "zero-downtime",
        "rollback",
        "8 GiB",
        "2 GiB",
        "isolated worktree",
    ):
        assert fact in document
