"""Protocol-parity tests for managed remote proposal decoding."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from general_ludd.self_improve.codex_comparison import (
    COMPACT_PROPOSAL_PROTOCOL_V3,
    COMPACT_PROPOSAL_PROTOCOL_V4,
    COMPACT_V4_SYNTAX_REPAIR_SAMPLING_PROFILE_ID,
    CodexReference,
    CompactLineSpan,
    CompactSpanProposal,
    ProposalManifest,
    encode_compact_span_batch,
    encode_proposal_batch,
)
from general_ludd.self_improve.managed_candidate_routing import (
    CandidateProposalDecodeRejected,
)
from general_ludd.self_improve.managed_remote_codec import (
    build_managed_remote_proposal_codec,
)
from general_ludd.self_improve.managed_runner import PromptPlan, PromptShard, TaskSpec
from general_ludd.self_improve.runtime import _managed_remote_proposal_codec


def _task() -> TaskSpec:
    return TaskSpec(
        task_id="S83.157",
        objective="Implement one bounded public feature.",
        canonical_make_commands=(
            "make test-files TESTFILES=tests/unit/test_example.py",
        ),
    )


def _reference() -> CodexReference:
    return CodexReference(
        baseline_sha="a" * 40,
        reference_sha="b" * 40,
        changed_files=frozenset({"src/general_ludd/example.py"}),
        test_files=frozenset({"tests/unit/test_example.py"}),
        changed_lines=1,
        elapsed_seconds=1.0,
    )


def _proposal(*, schema_version: int = 1, old_text: str = "return 0") -> ProposalManifest:
    return ProposalManifest.from_json(
        json.dumps(
            {
                "baseline_sha": "a" * 40,
                "commit_message": "feat: improve example",
                "edits": [
                    {
                        "new_text": "return 1",
                        "old_text": old_text,
                        "operation": "replace",
                        "path": "src/general_ludd/example.py",
                    }
                ],
                "make_commands": [
                    "make test-files TESTFILES=tests/unit/test_example.py"
                ],
                "schema_version": schema_version,
                "task_id": "S83.157",
                "tests": ["tests/unit/test_example.py"],
            }
        )
    )


def test_legacy_string_codec_preserves_request_and_censors_invalid_response() -> None:
    codec = build_managed_remote_proposal_codec(
        "bounded approved prompt",
        _task(),
        _reference(),
    )

    assert codec is not None
    assert codec.request_text == "bounded approved prompt"
    assert codec.decoder(_proposal().to_json()).proposal == _proposal()
    with pytest.raises(CandidateProposalDecodeRejected) as raised:
        codec.decoder("sensitive malformed provider output")
    assert "sensitive" not in str(raised.value)


def test_legacy_prompt_batch_uses_the_same_contract_as_local_generation() -> None:
    plan = PromptPlan(
        shards=(
            PromptShard(("src/general_ludd/example.py",), "bounded shard prompt"),
        ),
        source_bytes=0,
        proposal_protocol=COMPACT_PROPOSAL_PROTOCOL_V3,
    )
    codec = _managed_remote_proposal_codec(plan, _task(), _reference())
    response = encode_proposal_batch(
        (_proposal(),),
        protocol_digest=plan.protocol_digest,
    )

    assert codec is not None
    assert codec.request_text != plan.shards[0].prompt
    assert codec.decoder(response).proposal == _proposal()


def test_compact_v4_codec_expands_exact_spans_against_trusted_baseline() -> None:
    baseline = "return 0\n"
    plan = PromptPlan(
        shards=(
            PromptShard(
                ("src/general_ludd/example.py",),
                "bounded compact prompt",
                editable_ranges=((1, 2),),
            ),
        ),
        source_bytes=len(baseline.encode("utf-8")),
        baseline_files=(("src/general_ludd/example.py", baseline),),
        proposal_protocol=COMPACT_PROPOSAL_PROTOCOL_V4,
    )
    codec = _managed_remote_proposal_codec(plan, _task(), _reference())
    spans = (
        CompactSpanProposal(
            focus_path="src/general_ludd/example.py",
            edits=(CompactLineSpan(1, 1, "return 1\n"),),
        ),
    )
    response = encode_compact_span_batch(
        spans,
        protocol_digest=plan.protocol_digest,
    )

    assert codec is not None
    generated = codec.decoder(response)
    assert generated.compact_proposals == spans
    assert generated.proposal.schema_version == 2
    assert generated.proposal.edits[0].old_text == baseline
    assert generated.proposal.edits[0].new_text == "return 1\n"


def test_compact_syntax_repair_stays_on_the_owned_local_regeneration_path() -> None:
    baseline = "return 0\n"
    plan = PromptPlan(
        shards=(
            PromptShard(
                ("src/general_ludd/example.py",),
                "bounded repair prompt",
                editable_ranges=((1, 2),),
            ),
        ),
        source_bytes=len(baseline.encode("utf-8")),
        baseline_files=(("src/general_ludd/example.py", baseline),),
        proposal_protocol=COMPACT_PROPOSAL_PROTOCOL_V4,
        sampling_profile=COMPACT_V4_SYNTAX_REPAIR_SAMPLING_PROFILE_ID,
    )

    assert _managed_remote_proposal_codec(plan, _task(), _reference()) is None


def test_runtime_factory_installs_codec_only_with_explicit_live_policy(
    tmp_path: Path,
) -> None:
    from general_ludd.self_improve.live_candidate_wiring import (
        LiveCandidateWiringPolicy,
    )
    from general_ludd.self_improve.model_candidates import BackendCallBudget
    from general_ludd.self_improve.runtime import build_managed_self_improve_runner

    budget = BackendCallBudget(1, 64, 64, 128, 0, 5.0)
    local = build_managed_self_improve_runner(
        tmp_path,
        root_runner=object(),
    )
    live = build_managed_self_improve_runner(
        tmp_path,
        root_runner=object(),
        live_candidate_policy=LiveCandidateWiringPolicy(local_budget=budget),
    )

    assert local.remote_proposal_codec_factory is None
    assert live.remote_proposal_codec_factory is _managed_remote_proposal_codec
