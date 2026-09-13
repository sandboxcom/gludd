"""Protocol-parity tests for managed remote proposal decoding."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pytest

import general_ludd.self_improve.runtime as runtime_module
from general_ludd.self_improve import managed_remote_codec as codec_module
from general_ludd.self_improve.codex_comparison import (
    COMPACT_PROPOSAL_PROTOCOL_V3,
    COMPACT_PROPOSAL_PROTOCOL_V4,
    COMPACT_V4_SYNTAX_REPAIR_SAMPLING_PROFILE_ID,
    CodexReference,
    CompactLineSpan,
    CompactSpanProposal,
    ProposalContract,
    ProposalManifest,
    encode_compact_span_batch,
    encode_prompt_batch,
    encode_proposal_batch,
    proposal_batch_json_schema,
)
from general_ludd.self_improve.managed_candidate_routing import (
    CandidateProposalDecodeFailure,
    CandidateProposalDecodeRejected,
    ManagedCandidateProposalEnvelope,
)
from general_ludd.self_improve.managed_remote_codec import (
    MANAGED_PROPOSAL_BATCH_PROTOCOL,
    build_managed_remote_proposal_codec,
)
from general_ludd.self_improve.managed_runner import PromptPlan, PromptShard, TaskSpec
from general_ludd.self_improve.runtime import MakeRunner, _managed_remote_proposal_codec


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


def _managed_response(raw_local_batch: str) -> str:
    value = json.loads(raw_local_batch)
    proposals = value["proposals"]
    assert isinstance(proposals, list)
    return json.dumps(
        {
            "protocol": MANAGED_PROPOSAL_BATCH_PROTOCOL,
            "protocol_digest": value["protocol_digest"],
            "proposals": {
                str(ordinal): proposal
                for ordinal, proposal in enumerate(proposals)
            },
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
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


def test_legacy_string_prompt_cannot_bypass_the_canonical_worker_envelope() -> None:
    codec = build_managed_remote_proposal_codec(
        "bounded approved prompt",
        _task(),
        _reference(),
    )

    assert codec is None


def test_legacy_prompt_batch_uses_the_same_contract_as_local_generation() -> None:
    plan = PromptPlan(
        shards=(
            PromptShard(("src/general_ludd/example.py",), "bounded shard prompt"),
        ),
        source_bytes=0,
        proposal_protocol=COMPACT_PROPOSAL_PROTOCOL_V3,
    )
    codec = _managed_remote_proposal_codec(plan, _task(), _reference())
    response = _managed_response(
        encode_proposal_batch(
            (_proposal(),),
            protocol_digest=plan.protocol_digest,
        )
    )

    assert codec is not None
    assert codec.request_text == encode_prompt_batch(
        (plan.shards[0].prompt,),
        protocol_digest=plan.protocol_digest,
    )
    assert codec.response_instruction is not None
    assert codec.response_schema_json is not None
    schema = json.loads(codec.response_schema_json)
    assert schema["properties"]["protocol"]["const"] == MANAGED_PROPOSAL_BATCH_PROTOCOL
    assert schema["properties"]["protocol_digest"]["const"] == plan.protocol_digest
    assert schema["properties"]["proposals"]["required"] == ["0"]
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
    response = _managed_response(
        encode_compact_span_batch(
            spans,
            protocol_digest=plan.protocol_digest,
        )
    )

    assert codec is not None
    assert codec.request_text == encode_prompt_batch(
        (plan.shards[0].prompt,),
        protocol_digest=plan.protocol_digest,
    )
    assert codec.request_text.startswith("GLUDD_SELF_IMPROVE_PROMPT_BATCH_V1\n")
    assert codec.response_instruction is not None
    assert "supplied response JSON schema" in codec.response_instruction
    assert "s is the 1-based baseline start" in codec.response_instruction
    assert "never repeat an edit, coordinate, or unchanged context" in (
        codec.response_instruction
    )
    assert "The edit must change content" in codec.response_instruction
    assert codec.response_schema_json is not None
    response_schema = json.loads(codec.response_schema_json)
    assert response_schema["properties"]["protocol"]["const"] == (
        MANAGED_PROPOSAL_BATCH_PROTOCOL
    )
    assert response_schema["properties"]["protocol_digest"]["const"] == (
        plan.protocol_digest
    )
    proposal_schema = response_schema["properties"]["proposals"]
    assert proposal_schema["required"] == ["0"]
    ordinal_schema = proposal_schema["properties"]["0"]
    assert ordinal_schema["properties"]["focus_path"]["const"] == (
        "src/general_ludd/example.py"
    )
    assert set(ordinal_schema["properties"]["e"]["items"]["required"]) == {
        "n",
        "s",
        "z",
    }
    generated = codec.decoder(response)
    assert generated.compact_proposals == spans
    assert generated.proposal.schema_version == 2
    assert generated.proposal.edits[0].old_text == baseline
    assert generated.proposal.edits[0].new_text == "return 1\n"


def test_managed_compact_schema_binds_each_shard_to_one_ordinal_path() -> None:
    paths = (
        "src/general_ludd/example.py",
        "tests/unit/test_example.py",
    )
    baselines = ("return 0\n", "def test_example():\n    assert False\n")
    plan = PromptPlan(
        shards=tuple(
            PromptShard(
                (path,),
                f"bounded prompt {ordinal}",
                editable_ranges=((1, 2),),
            )
            for ordinal, path in enumerate(paths)
        ),
        source_bytes=sum(len(value.encode("utf-8")) for value in baselines),
        baseline_files=tuple(zip(paths, baselines, strict=True)),
        proposal_protocol=COMPACT_PROPOSAL_PROTOCOL_V4,
    )

    codec = _managed_remote_proposal_codec(plan, _task(), _reference())

    assert codec is not None
    schema = json.loads(cast(str, codec.response_schema_json))
    proposals = schema["properties"]["proposals"]
    assert proposals["type"] == "object"
    assert proposals["additionalProperties"] is False
    assert proposals["required"] == ["0", "1"]
    assert proposals["properties"]["0"]["properties"]["focus_path"] == {
        "const": paths[0],
        "type": "string",
    }
    assert proposals["properties"]["1"]["properties"]["focus_path"] == {
        "const": paths[1],
        "type": "string",
    }
    response = json.dumps(
        {
            "protocol": "self-improve-managed-proposal-batch-v1",
            "protocol_digest": plan.protocol_digest,
            "proposals": {
                "0": {
                    "focus_path": paths[0],
                    "e": [{"s": 1, "n": 1, "z": "return 1\n"}],
                },
                "1": {
                    "focus_path": paths[1],
                    "e": [
                        {
                            "s": 1,
                            "n": 1,
                            "z": "def test_example():\n    assert True\n",
                        }
                    ],
                },
            },
        },
        separators=(",", ":"),
        sort_keys=True,
    )
    generated = codec.decoder(response)
    assert tuple(edit.path for edit in generated.proposal.edits) == paths


def test_local_and_remote_candidates_consume_one_canonical_codec_envelope(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
    assert codec is not None
    response = _managed_response(
        encode_compact_span_batch(
            (
                CompactSpanProposal(
                    focus_path="src/general_ludd/example.py",
                    edits=(CompactLineSpan(1, 1, "return 1\n"),),
                ),
            ),
            protocol_digest=plan.protocol_digest,
        )
    )
    model_path = tmp_path / "model.gguf"
    model_path.write_bytes(b"GGUF")
    exchanges: list[tuple[str, str]] = []

    def run_local(
        _runner: object,
        _model_path: Path,
        request: str,
        *,
        contract: ProposalContract | None = None,
        envelope: ManagedCandidateProposalEnvelope | None = None,
        timeout_seconds: float = 300.0,
    ) -> str:
        assert contract is None
        assert envelope is not None
        assert timeout_seconds == 300.0
        exchanges.append((request, envelope.to_json()))
        return response

    monkeypatch.setattr(runtime_module, "_run_local_proposal_request", run_local)

    local = runtime_module._generate_local_proposal_plan_result(
        MakeRunner(tmp_path),
        model_path,
        plan,
        _task(),
        _reference(),
        proposal_codec=codec,
    )

    assert exchanges == [(codec.request_text, codec.worker_envelope.to_json())]
    assert local == codec.decoder(response)
    assert len(codec.envelope_digest) == 64


def test_compact_batch_schema_stays_bounded_across_maximum_shards() -> None:
    paths = tuple(f"src/general_ludd/example_{index}.py" for index in range(32))
    ranges = tuple((((index * 4096) + 1, (index * 4096) + 2048),) for index in range(32))

    schema = proposal_batch_json_schema(
        proposal_protocol=COMPACT_PROPOSAL_PROTOCOL_V4,
        protocol_digest="a" * 64,
        expected_count=len(paths),
        focus_paths=paths,
        editable_ranges=ranges,
    )

    encoded = json.dumps(schema, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    root_properties = cast(dict[str, object], schema["properties"])
    proposals_schema = cast(dict[str, object], root_properties["proposals"])
    proposal_schema = cast(dict[str, object], proposals_schema["items"])
    proposal_properties = cast(dict[str, object], proposal_schema["properties"])
    edits_schema = cast(dict[str, object], proposal_properties["e"])
    edit_schema = cast(dict[str, object], edits_schema["items"])
    edit_properties = cast(dict[str, object], edit_schema["properties"])
    start_schema = edit_properties["s"]
    assert start_schema == {"type": "integer", "minimum": 1}
    assert len(encoded.encode("ascii")) < 16_384


def test_compact_remote_decoder_exposes_only_fixed_protocol_failure_categories() -> None:
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
    assert codec is not None
    valid = json.loads(
        _managed_response(
            encode_compact_span_batch(
            (
                CompactSpanProposal(
                    focus_path="src/general_ludd/example.py",
                    edits=(CompactLineSpan(1, 1, "return 1\n"),),
                ),
            ),
                protocol_digest=plan.protocol_digest,
            )
        )
    )
    malformed = (
        ("PRIVATE_RESPONSE=not-json", CandidateProposalDecodeFailure.JSON_CONTRACT),
        (json.dumps({"private": "response"}), CandidateProposalDecodeFailure.ROOT_CONTRACT),
        (
            json.dumps({**valid, "protocol": "private-wrong-protocol"}),
            CandidateProposalDecodeFailure.PROTOCOL_IDENTITY,
        ),
        (
            json.dumps({**valid, "proposals": {}}),
            CandidateProposalDecodeFailure.PROPOSAL_COUNT,
        ),
        (
            json.dumps({**valid, "proposals": {"0": {"private": "response"}}}),
            CandidateProposalDecodeFailure.PROPOSAL_SHAPE,
        ),
    )

    for raw, failure in malformed:
        with pytest.raises(CandidateProposalDecodeRejected) as raised:
            codec.decoder(raw)
        assert raised.value.failure is failure
        assert "PRIVATE" not in str(raised.value)
        assert "private" not in str(raised.value)


def test_compact_remote_decoder_types_semantic_edit_rejections() -> None:
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
    assert codec is not None

    cases = (
        (
            [{"s": 1, "n": 1, "z": "return 0\n"}],
            CandidateProposalDecodeFailure.EDIT_NO_CHANGE,
        ),
        (
            [
                {"s": 1, "n": 1, "z": "return 1\n"},
                {"s": 1, "n": 0, "z": "# duplicate\n"},
            ],
            CandidateProposalDecodeFailure.EDIT_OVERLAP,
        ),
        (
            [{"s": 3, "n": 0, "z": "return 1\n"}],
            CandidateProposalDecodeFailure.PROPOSAL_SCOPE,
        ),
    )
    for edits, failure in cases:
        response = json.dumps(
            {
                "protocol": MANAGED_PROPOSAL_BATCH_PROTOCOL,
                "protocol_digest": plan.protocol_digest,
                "proposals": {
                    "0": {
                        "focus_path": "src/general_ludd/example.py",
                        "e": edits,
                    }
                },
            }
        )
        with pytest.raises(CandidateProposalDecodeRejected) as raised:
            codec.decoder(response)
        assert raised.value.failure is failure


@pytest.mark.parametrize(
    ("detail", "failure"),
    [
        ("changed lines exceed the contract", CandidateProposalDecodeFailure.EDIT_LINE_BUDGET),
        ("new text exceeds the budget", CandidateProposalDecodeFailure.EDIT_CONTENT_BUDGET),
        ("edit is outside trusted ranges", CandidateProposalDecodeFailure.PROPOSAL_SCOPE),
        ("coordinates must be integers", CandidateProposalDecodeFailure.PROPOSAL_SHAPE),
        ("unclassified validation failure", CandidateProposalDecodeFailure.PROPOSAL_VALIDATION),
    ],
)
def test_validation_failures_preserve_actionable_content_free_categories(
    detail: str,
    failure: CandidateProposalDecodeFailure,
) -> None:
    """Semantic failures become fixed categories without echoing model content."""
    assert codec_module._validation_failure(ValueError(detail)) is failure


def test_legacy_decoder_rejects_non_object_proposal_shape() -> None:
    """Legacy remote responses must contain one object per approved shard."""
    plan = PromptPlan(
        shards=(PromptShard(("src/general_ludd/example.py",), "bounded prompt"),),
        source_bytes=0,
        proposal_protocol=COMPACT_PROPOSAL_PROTOCOL_V3,
    )
    codec = _managed_remote_proposal_codec(plan, _task(), _reference())
    assert codec is not None
    response = json.dumps(
        {
            "protocol": MANAGED_PROPOSAL_BATCH_PROTOCOL,
            "protocol_digest": plan.protocol_digest,
            "proposals": {"0": "not-an-object"},
        }
    )

    with pytest.raises(CandidateProposalDecodeRejected) as raised:
        codec.decoder(response)

    assert raised.value.failure is CandidateProposalDecodeFailure.PROPOSAL_SHAPE


@pytest.mark.parametrize("focus_paths", [(), ("one.py", "two.py")])
def test_compact_remote_codec_requires_one_baselined_path_per_shard(
    focus_paths: tuple[str, ...],
) -> None:
    """Remote compact inference cannot run without an exact baseline/path binding."""
    baseline_files = tuple((path, "pass\n") for path in focus_paths)
    plan = PromptPlan(
        shards=(PromptShard(focus_paths or ("one.py",), "bounded", ((1, 2),)),),
        source_bytes=sum(len(content.encode()) for _, content in baseline_files),
        baseline_files=baseline_files,
        proposal_protocol=COMPACT_PROPOSAL_PROTOCOL_V4,
    )

    with pytest.raises(ValueError, match="exact baseline shards"):
        _managed_remote_proposal_codec(plan, _task(), _reference())


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
        root_runner=MakeRunner(tmp_path),
    )
    live = build_managed_self_improve_runner(
        tmp_path,
        root_runner=MakeRunner(tmp_path),
        live_candidate_policy=LiveCandidateWiringPolicy(local_budget=budget),
    )

    assert local.remote_proposal_codec_factory is None
    assert live.remote_proposal_codec_factory is _managed_remote_proposal_codec
