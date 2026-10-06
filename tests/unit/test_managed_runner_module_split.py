"""Structural regression tests for the managed-runner module boundary."""

from __future__ import annotations

import pickle
from pathlib import Path
from typing import cast

import pytest

import general_ludd.self_improve.managed_runner as facade
import general_ludd.self_improve.managed_runner_boundaries as boundaries
import general_ludd.self_improve.managed_runner_contracts as contracts
from general_ludd.self_improve.codex_comparison import (
    COMPACT_PROPOSAL_PROTOCOL_V4,
    COMPACT_V4_SYNTAX_REPAIR_SAMPLING_PROFILE_ID,
    CompactSpanProposal,
    ProposalManifest,
)
from general_ludd.self_improve.model_candidates import ModelCandidateProvider

_RUNNER_SOURCE = (
    Path(__file__).parents[2]
    / "src"
    / "general_ludd"
    / "self_improve"
    / "managed_runner.py"
)


def test_managed_runner_reexports_extracted_contracts_by_identity() -> None:
    """Existing imports must retain the exact extracted runtime objects."""
    for name in (
        "AttemptResult",
        "GeneratedProposal",
        "PlanBoundProposal",
        "PromptPlan",
        "TaskSpec",
    ):
        exported = getattr(facade, name)
        assert exported is getattr(contracts, name)
        assert exported.__module__ == facade.__name__
    task = facade.TaskSpec("S1", "bounded", ("make test",))
    assert pickle.loads(pickle.dumps(task)) == task


def test_managed_runner_reexports_extracted_boundaries_by_identity() -> None:
    """Dependency-injection and result seams remain import-compatible."""
    for name in (
        "CapabilityEvidenceOutcomeAdapter",
        "LocalProposalBackendAdapter",
        "LocalProposalInvocation",
        "ManagedOutcomeAdapter",
        "ManagedRunResult",
    ):
        exported = getattr(facade, name)
        assert exported is getattr(boundaries, name)
        assert exported.__module__ == facade.__name__


def test_managed_runner_declares_cross_module_compatibility_exports() -> None:
    """Typed consumers must see every supported facade compatibility seam."""
    expected = {
        "GeneratedProposal",
        "_OutcomeAdapterFactory",
        "_ProposalGenerator",
    }

    assert expected <= set(facade.__all__)
    assert facade._OutcomeAdapterFactory is boundaries._OutcomeAdapterFactory
    assert facade._ProposalGenerator is boundaries._ProposalGenerator


def test_managed_runner_facade_stays_below_the_repository_line_limit() -> None:
    """The orchestration facade must not regress into another oversized module."""
    assert len(_RUNNER_SOURCE.read_text(encoding="utf-8").splitlines()) < 2_500


def _manifest() -> ProposalManifest:
    return ProposalManifest.from_json(
        """{
          "schema_version": 1,
          "baseline_sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
          "task_id": "S1",
          "edits": [{
            "operation": "replace",
            "path": "src/example.py",
            "old_text": "return 0",
            "new_text": "return 1"
          }],
          "tests": ["tests/test_example.py"],
          "make_commands": ["make test-specific TESTFILE=tests/test_example.py"],
          "commit_message": "Improve example"
        }"""
    )


def test_extracted_task_contract_retains_defensive_parser_branches(
    tmp_path: Path,
) -> None:
    """Moving task validation must not drop malformed-input rejection paths."""
    assert not contracts._is_safe_make_command("make '")
    with pytest.raises(ValueError, match="objective exceeds"):
        contracts.TaskSpec("S1", "x" * 65_537, ("make test",))
    with pytest.raises(ValueError, match="JSON array"):
        contracts.TaskSpec._from_json_value(
            {
                "task_id": "S1",
                "objective": "bounded",
                "canonical_make_commands": "make test",
            }
        )
    malformed = tmp_path / "task.json"
    malformed.write_text("{", encoding="utf-8")
    with pytest.raises(ValueError, match="valid UTF-8 JSON"):
        contracts.TaskSpec.from_path(malformed)


def test_extracted_prompt_contract_retains_ephemeral_repair_guards() -> None:
    """Repair-only state remains both v4-bound and non-serializable."""
    shard = facade.PromptShard(("src/example.py",), "bounded")
    with pytest.raises(ValueError, match="repair sampling profile requires"):
        contracts.PromptPlan(
            (shard,),
            0,
            sampling_profile=COMPACT_V4_SYNTAX_REPAIR_SAMPLING_PROFILE_ID,
        )
    with pytest.raises(ValueError, match="repair diagnosis path requires"):
        contracts.PromptPlan(
            (shard,),
            0,
            repair_diagnosis_path_sha256="a" * 64,
        )
    baseline_without_text = contracts.PromptPlan(
        (shard,),
        0,
        baseline_files=(("src/example.py", None),),
    )
    assert baseline_without_text.source_bytes == 0


def test_extracted_prompt_json_parser_retains_nested_shape_guards() -> None:
    """Every mutable nested shape still fails at the immutable boundary."""
    base: dict[str, object] = {
        "baseline_files": [],
        "protocol_digest": "a" * 64,
        "proposal_protocol": COMPACT_PROPOSAL_PROTOCOL_V4,
        "shards": [
            {
                "editable_ranges": [],
                "focus_paths": ["src/example.py"],
                "prompt": "bounded",
            }
        ],
        "source_bytes": 0,
    }
    for replacement, message in (
        ({"proposal_protocol": "unsupported"}, "unsupported"),
        ({"shards": [{"editable_ranges": [], "focus_paths": "bad", "prompt": "x"}]}, "string array"),
        ({"shards": [{"editable_ranges": [1], "focus_paths": ["x"], "prompt": "x"}]}, "pair array"),
        ({"baseline_files": {}}, "baseline_files must be a JSON array"),
        ({"baseline_files": [["only-one"]]}, "entries are invalid"),
    ):
        payload = {**base, **replacement}
        with pytest.raises(ValueError, match=message):
            contracts.PromptPlan._from_json_value(payload)


def test_extracted_proposal_contracts_retain_runtime_type_guards() -> None:
    """Routing metadata cannot bypass manifest, evaluation, or provider typing."""
    manifest = _manifest()
    invalid_manifest = cast(ProposalManifest, object())
    with pytest.raises(ValueError, match="plan-bound proposal"):
        contracts.PlanBoundProposal(invalid_manifest, "a" * 64)
    with pytest.raises(ValueError, match="generated proposal"):
        contracts.GeneratedProposal(invalid_manifest)
    with pytest.raises(ValueError, match="immutable tuple"):
        contracts.GeneratedProposal(
            manifest,
            cast(tuple[CompactSpanProposal, ...], ("invalid",)),
        )
    with pytest.raises(ValueError, match="pre-evaluated result"):
        contracts.GeneratedProposal(
            manifest,
            evaluated_result=cast(contracts.AttemptResult, object()),
        )
    with pytest.raises(ValueError, match="ModelCandidateProvider"):
        contracts.GeneratedProposal(
            manifest,
            selected_candidate_identity_digest="a" * 64,
            selected_candidate_provider=cast(ModelCandidateProvider, "invalid"),
            candidate_plan_digest="b" * 64,
        )
    with pytest.raises(ValueError, match="require their evaluated result"):
        contracts.GeneratedProposal(
            manifest,
            selected_candidate_identity_digest="a" * 64,
            selected_candidate_provider=ModelCandidateProvider.LOCAL_GGUF,
            candidate_plan_digest="b" * 64,
        )
    with pytest.raises(ValueError, match="explicit boolean"):
        contracts.GeneratedProposal(
            manifest,
            routed_local_accepted=cast(bool, 1),
        )
