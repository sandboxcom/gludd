"""Contracts for local self-improvement comparison with a Codex reference."""

from __future__ import annotations

import hashlib
import importlib
import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from types import ModuleType
from typing import Any, TypedDict, Unpack, cast

import pytest

import general_ludd.self_improve.codex_comparison as comparison_module
from general_ludd.self_improve.codex_comparison import (
    CandidateEvidence,
    CodexReference,
    LocalProposalGateway,
    ProposalContract,
    ProposalManifest,
    build_retry_prompt,
    compare_with_codex,
)


def _proposal(**updates: object) -> ProposalManifest:
    data: dict[str, object] = {
        "schema_version": 1,
        "baseline_sha": "a" * 40,
        "task_id": "S83.133",
        "edits": [
            {
                "operation": "replace",
                "path": "src/general_ludd/example.py",
                "old_text": "return 0",
                "new_text": "return 42",
            }
        ],
        "tests": ["tests/unit/test_example.py"],
        "make_commands": [
            "make test-files TESTFILES=tests/unit/test_example.py PYTEST_ARGS=-q",
            "make lint-files FILES=src/general_ludd/example.py",
        ],
        "commit_message": "fix: return the validated answer",
    }
    data.update(updates)
    return ProposalManifest.from_json(json.dumps(data))


class _EvidenceUpdates(TypedDict, total=False):
    changed_files: frozenset[str]
    tests_passed: bool
    warnings: int
    coverage_aggregate: float
    coverage_min_file: float
    ruff_passed: bool
    mypy_passed: bool
    docstrings_passed: bool
    markdown_passed: bool
    cleanup_passed: bool
    commit_count: int
    worktree_clean: bool
    elapsed_seconds: float
    changed_lines: int


def _evidence(**updates: Unpack[_EvidenceUpdates]) -> CandidateEvidence:
    base = CandidateEvidence(
        changed_files=frozenset(
            {"src/general_ludd/example.py", "tests/unit/test_example.py"}
        ),
        tests_passed=True,
        warnings=0,
        coverage_aggregate=92.0,
        coverage_min_file=84.0,
        ruff_passed=True,
        mypy_passed=True,
        docstrings_passed=True,
        markdown_passed=True,
        cleanup_passed=True,
        commit_count=1,
        worktree_clean=True,
        elapsed_seconds=12.0,
    )
    return replace(base, **updates)


def _reference() -> CodexReference:
    return CodexReference(
        baseline_sha="a" * 40,
        reference_sha="b" * 40,
        changed_files=frozenset(
            {"src/general_ludd/example.py", "tests/unit/test_example.py"}
        ),
        test_files=frozenset({"tests/unit/test_example.py"}),
        changed_lines=10,
        elapsed_seconds=10.0,
    )


def _contract() -> ProposalContract:
    return ProposalContract(
        baseline_sha="a" * 40,
        task_id="S83.133",
        tests=("tests/unit/test_example.py",),
        make_commands=("make test-files TESTFILES=tests/unit/test_example.py",),
    )


def _span_proposal(
    raw: object,
    *,
    path: str = "src/general_ludd/example.py",
) -> comparison_module.CompactSpanProposal:
    return comparison_module._decode_compact_span_proposal(
        json.dumps(raw),
        focus_path=path,
    )


def _expand_span_proposals(
    proposals: tuple[comparison_module.CompactSpanProposal, ...],
    *,
    paths: tuple[str, ...] = ("src/general_ludd/example.py",),
    baselines: dict[str, str | None] | None = None,
    editable_ranges: tuple[tuple[tuple[int, int], ...], ...] = (((1, 5),),),
) -> ProposalManifest:
    return comparison_module.expand_compact_span_proposals(
        proposals,
        contract=_contract(),
        expected_path_groups=tuple((path,) for path in paths),
        expected_baseline_files=(
            baselines
            if baselines is not None
            else {"src/general_ludd/example.py": "same\nsame\nunique\ntail\n"}
        ),
        expected_editable_ranges=editable_ranges,
    )


def test_proposal_contract_rejects_mutable_identity_collections() -> None:
    """Keep trusted tests and commands immutable across the worker boundary."""
    with pytest.raises(ValueError, match="tests must be a tuple"):
        ProposalContract(
            baseline_sha="a" * 40,
            task_id="S83.133",
            tests=cast(tuple[str, ...], ["tests/unit/test_example.py"]),
            make_commands=("make test-files TESTFILES=tests/unit/test_example.py",),
        )
    with pytest.raises(ValueError, match="make_commands must be a tuple"):
        ProposalContract(
            baseline_sha="a" * 40,
            task_id="S83.133",
            tests=("tests/unit/test_example.py",),
            make_commands=cast(
                tuple[str, ...],
                ["make test-files TESTFILES=tests/unit/test_example.py"],
            ),
        )


def test_worker_protocol_entry_points_are_declared_public_exports() -> None:
    """Keep script consumers explicit so dead-code checks see the real API seam."""
    expected = {
        "COMPACT_PROPOSAL_CONTRACT_TRANSPORT_PROTOCOL",
        "COMPACT_PROPOSAL_PROTOCOL_V3",
        "COMPACT_PROPOSAL_PROTOCOL_V4",
        "COMPACT_V4_REPAIR_SEED_DERIVATION_POLICY_ID",
        "COMPACT_V4_REPAIR_SHARD_PROMPT_POLICY_ID",
        "COMPACT_V4_REPAIR_SPAN_PROVENANCE_POLICY_ID",
        "COMPACT_V4_SYNTAX_REPAIR_SAMPLING_PROFILE_ID",
        "DEFAULT_PROPOSAL_SAMPLING_PROFILE_ID",
        "CompactLineSpan",
        "CompactSpanProposal",
        "EVALUATION_DIAGNOSIS_PROTOCOL",
        "LocalProposalGateway",
        "bind_compact_focus_path",
        "build_retry_prompt",
        "compare_with_codex",
        "compact_v4_syntax_repair_sampling_identity",
        "decode_prompt_batch",
        "decode_compact_span_batch",
        "decode_proposal_batch",
        "encode_compact_span_batch",
        "encode_proposal_batch",
        "expand_compact_span_proposals",
        "local_proposal_attempt_identity_digest",
        "merge_proposal_manifests",
        "safe_evaluation_retry_diagnosis",
    }

    assert expected <= set(getattr(comparison_module, "__all__", ()))


def test_prompt_batch_rejects_ambiguous_protocol_identity() -> None:
    """Reject malformed request envelopes instead of guessing their identity."""
    digest = "a" * 64
    marker = comparison_module._PROMPT_BATCH_MARKER

    with pytest.raises(ValueError, match="must contain 1"):
        comparison_module.encode_prompt_batch("prompt", protocol_digest=digest)
    with pytest.raises(ValueError, match="lowercase SHA-256"):
        comparison_module.encode_prompt_batch(("prompt",), protocol_digest="bad")
    with pytest.raises(ValueError, match="each prompt batch item"):
        comparison_module.encode_prompt_batch(
            (cast(str, object()),),
            protocol_digest=digest,
        )
    oversized = ("x" * comparison_module._MAX_PROMPT_SHARD_BYTES,) * (
        comparison_module._MAX_PROMPT_BATCH_SHARDS
    )
    with pytest.raises(ValueError, match="prompt batch exceeds"):
        comparison_module.encode_prompt_batch(oversized, protocol_digest=digest)

    valid = {
        "protocol": comparison_module._PROMPT_BATCH_PROTOCOL,
        "protocol_digest": digest,
        "prompts": ["prompt"],
    }
    malformed_requests = (
        (marker + "{", "valid JSON"),
        (marker + json.dumps({}), "exactly protocol"),
        (
            marker + json.dumps({**valid, "protocol": "wrong"}),
            "unsupported",
        ),
        (
            marker + json.dumps({**valid, "prompts": "prompt"}),
            "invalid types",
        ),
    )
    for raw, match in malformed_requests:
        with pytest.raises(ValueError, match=match):
            comparison_module.decode_prompt_batch(raw)


def test_proposal_batch_rejects_ambiguous_protocol_identity() -> None:
    """Bind every response envelope to its expected prompt plan identity."""
    digest = "a" * 64
    proposal = _proposal()

    with pytest.raises(ValueError, match="must contain 1"):
        comparison_module.encode_proposal_batch((), protocol_digest=digest)
    with pytest.raises(ValueError, match="must contain 1"):
        comparison_module.encode_proposal_batch(
            (cast(ProposalManifest, object()),),
            protocol_digest=digest,
        )
    with pytest.raises(ValueError, match="lowercase SHA-256"):
        comparison_module.encode_proposal_batch((proposal,), protocol_digest="bad")
    with pytest.raises(ValueError, match="outside the batch bound"):
        comparison_module.decode_proposal_batch(
            "{}",
            expected_protocol_digest=digest,
            expected_count=0,
        )
    with pytest.raises(ValueError, match="lowercase SHA-256"):
        comparison_module.decode_proposal_batch(
            "{}",
            expected_protocol_digest="bad",
            expected_count=1,
        )

    valid = {
        "protocol": comparison_module._PROPOSAL_BATCH_PROTOCOL,
        "protocol_digest": digest,
        "proposals": [json.loads(proposal.to_json())],
    }
    malformed_responses = (
        ("{", "valid JSON"),
        (json.dumps({}), "exactly protocol"),
        (json.dumps({**valid, "protocol": "wrong"}), "unsupported"),
        (json.dumps({**valid, "protocol_digest": "b" * 64}), "identity drifted"),
        (json.dumps({**valid, "proposals": []}), "count does not match"),
    )
    for raw, match in malformed_responses:
        with pytest.raises(ValueError, match=match):
            comparison_module.decode_proposal_batch(
                raw,
                expected_protocol_digest=digest,
                expected_count=1,
            )


def test_attempt_identity_binds_complete_managed_output_protocol(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prompt_digest = "a" * 64
    baseline = comparison_module.local_proposal_attempt_identity_digest(prompt_digest)

    assert baseline == comparison_module.local_proposal_attempt_identity_digest(
        prompt_digest
    )
    assert baseline != comparison_module.local_proposal_attempt_identity_digest(
        "b" * 64
    )
    protocol_changes: tuple[tuple[str, object], ...] = (
        ("_COMPACT_PROPOSAL_PROTOCOL_VERSION", "compact-proposal-v-next"),
        ("_COMPACT_PROPOSAL_JSON_SCHEMA", {"type": "object"}),
        ("_STRUCTURED_CANARY_SCHEMA", {"type": "boolean"}),
        ("_STRUCTURED_CANARY_PROMPT", "Return a different canary."),
        ("_STRUCTURED_CANARY_EXPECTED", {"ok": False}),
        ("_COMPACT_SPAN_PROPOSAL_TOKENS", 4097),
        ("_COMPACT_SPAN_MAX_EDITS", 5),
        ("_COMPACT_MAX_CONTENT_BYTES", 3073),
        ("_COMPACT_FOCUS_PATH_MARKER", "CHANGED_FOCUS_PATH="),
        ("_COMPACT_EDITABLE_RANGES_MARKER", "CHANGED_EDITABLE_RANGES="),
        ("_COMPACT_MAX_SCOPE_MARKER_BYTES", 16_383),
        ("_COMPACT_MAX_SCOPE_COORDINATES", 2047),
        ("_COMPACT_COMMIT_MESSAGE", "fix: changed trusted commit message"),
        ("_STRUCTURED_CANARY_TOKENS", 33),
        ("_DETERMINISTIC_DECODE_SEED", 1),
        ("_DETERMINISTIC_DECODE_TEMPERATURE", 0.1),
        ("_STRUCTURED_OUTPUT_REQUIRE_STOP", False),
        ("_COMPACT_ROOT_FIELDS", frozenset({"c", "e", "unexpected"})),
        ("_COMPACT_LINE_MATERIALIZATION_POLICY", "trusted-eol-mutated"),
        ("_MAX_SNAPSHOT_CONTENT_BYTES", 8_391_679),
        ("_SNAPSHOT_MANIFEST_SCHEMA_VERSION", 3),
        ("_STRICT_PARENT_DECODER_VERSION", "proposal-manifest-strict-v-next"),
    )
    for name, changed_value in protocol_changes:
        with monkeypatch.context() as scoped:
            scoped.setattr(comparison_module, name, changed_value)
            assert (
                comparison_module.local_proposal_attempt_identity_digest(prompt_digest)
                != baseline
            ), name

    reordered_schema = dict(
        reversed(list(comparison_module._COMPACT_PROPOSAL_JSON_SCHEMA.items()))
    )
    with monkeypatch.context() as scoped:
        scoped.setattr(
            comparison_module,
            "_COMPACT_PROPOSAL_JSON_SCHEMA",
            reordered_schema,
        )
        assert (
            comparison_module.local_proposal_attempt_identity_digest(prompt_digest)
            == baseline
        )


def test_legacy_identity_keeps_its_historical_token_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rotate only v4 for the bounded-span budget while retaining v3 semantics."""
    prompt_digest = "a" * 64
    legacy = comparison_module.COMPACT_PROPOSAL_PROTOCOL_V3
    baseline = comparison_module.local_proposal_attempt_identity_digest(
        prompt_digest,
        proposal_protocol=legacy,
    )
    v4_baseline = comparison_module.local_proposal_attempt_identity_digest(prompt_digest)

    with monkeypatch.context() as scoped:
        scoped.setattr(comparison_module, "_COMPACT_SPAN_MAX_EDITS", 5)
        assert comparison_module.local_proposal_attempt_identity_digest(
            prompt_digest,
            proposal_protocol=legacy,
        ) == baseline
        assert (
            comparison_module.local_proposal_attempt_identity_digest(prompt_digest)
            != v4_baseline
        )

    with monkeypatch.context() as scoped:
        scoped.setattr(comparison_module, "_COMPACT_SPAN_PROPOSAL_TOKENS", 4097)
        assert comparison_module.local_proposal_attempt_identity_digest(
            prompt_digest,
            proposal_protocol=legacy,
        ) == baseline

    with monkeypatch.context() as scoped:
        scoped.setattr(comparison_module, "_COMPACT_PROPOSAL_TOKENS", 1025)
        assert comparison_module.local_proposal_attempt_identity_digest(
            prompt_digest,
            proposal_protocol=legacy,
        ) != baseline

    with monkeypatch.context() as scoped:
        scoped.setattr(
            comparison_module,
            "_STRUCTURED_DECODING_MODE",
            "unconstrained-response-format-only",
        )
        assert (
            comparison_module.local_proposal_attempt_identity_digest(prompt_digest)
            != v4_baseline
        )
        assert (
            comparison_module.local_proposal_attempt_identity_digest(
                prompt_digest,
                proposal_protocol=legacy,
            )
            == baseline
        )

    with monkeypatch.context() as scoped:
        scoped.setattr(
            comparison_module,
            "_LEGACY_STRUCTURED_DECODING_MODE",
            "unconstrained-response-format-only",
            raising=False,
        )
        assert (
            comparison_module.local_proposal_attempt_identity_digest(
                prompt_digest,
                proposal_protocol=legacy,
            )
            != baseline
        )


def test_v4_identity_binds_evaluation_diagnosis_without_reinterpreting_v3(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A typed retry-evidence change rotates v4 while legacy v3 stays byte-stable."""
    prompt_digest = "a" * 64
    legacy = comparison_module.COMPACT_PROPOSAL_PROTOCOL_V3
    v4_baseline = comparison_module.local_proposal_attempt_identity_digest(prompt_digest)
    v3_baseline = comparison_module.local_proposal_attempt_identity_digest(
        prompt_digest,
        proposal_protocol=legacy,
    )

    monkeypatch.setattr(
        comparison_module,
        "EVALUATION_DIAGNOSIS_PROTOCOL",
        replace(
            comparison_module.EVALUATION_DIAGNOSIS_PROTOCOL,
            version="self-improve-evaluation-diagnosis-v-next",
        ),
    )

    assert comparison_module.local_proposal_attempt_identity_digest(
        prompt_digest
    ) != v4_baseline
    assert (
        comparison_module.local_proposal_attempt_identity_digest(
            prompt_digest,
            proposal_protocol=legacy,
        )
        == v3_baseline
    )


def test_attempt_identity_binds_model_acquisition_and_outcome_protocol(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prompt_digest = "a" * 64
    baseline = comparison_module.local_proposal_attempt_identity_digest(prompt_digest)
    protocol = comparison_module.LOCAL_MODEL_ATTEMPT_OUTCOME_PROTOCOL
    changed_protocols = (
        replace(protocol, version="self-improve-model-attempt-outcome-v-next"),
        replace(
            protocol,
            acquisition_failure="recorded_as_model_failure",
        ),
        replace(protocol, plan_exhaustion="counts_as_attempt"),
        replace(protocol, outcome_eligibility="candidate_selected"),
    )

    for changed in changed_protocols:
        with monkeypatch.context() as scoped:
            scoped.setattr(
                comparison_module,
                "LOCAL_MODEL_ATTEMPT_OUTCOME_PROTOCOL",
                changed,
            )
            assert (
                comparison_module.local_proposal_attempt_identity_digest(prompt_digest)
                != baseline
            )


def test_attempt_identity_binds_runtime_validation_retry_protocol(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prompt_digest = "a" * 64
    baseline = comparison_module.local_proposal_attempt_identity_digest(prompt_digest)
    protocol = comparison_module.LOCAL_PROPOSAL_VALIDATION_RETRY_PROTOCOL

    assert baseline == comparison_module.local_proposal_attempt_identity_digest(
        prompt_digest
    )
    changed_protocols = (
        replace(protocol, version="self-improve-validation-retry-v-next"),
        replace(
            protocol,
            fallback_tail_bytes=protocol.fallback_tail_bytes + 1,
        ),
        replace(
            protocol,
            max_feedback_bytes=protocol.max_feedback_bytes + 1,
        ),
    )
    for changed in changed_protocols:
        with monkeypatch.context() as scoped:
            scoped.setattr(
                comparison_module,
                "LOCAL_PROPOSAL_VALIDATION_RETRY_PROTOCOL",
                changed,
            )
            assert (
                comparison_module.local_proposal_attempt_identity_digest(prompt_digest)
                != baseline
            )


@pytest.mark.parametrize("prompt_digest", ["", "A" * 64, "a" * 63, "g" * 64])
def test_attempt_identity_rejects_noncanonical_prompt_digest(
    prompt_digest: str,
) -> None:
    with pytest.raises(ValueError, match="prompt protocol digest"):
        comparison_module.local_proposal_attempt_identity_digest(prompt_digest)


def test_proposal_contract_round_trips_only_trusted_immutable_fields() -> None:
    contract = _contract()
    assert ProposalContract.from_json(contract.to_json()) == contract
    assert set(json.loads(contract.to_json())) == {
        "baseline_sha",
        "task_id",
        "tests",
        "make_commands",
    }


def test_repair_sampling_profile_round_trips_without_changing_normal_v4_bytes() -> None:
    """Keep ordinary v4 requests byte-stable while carrying one trusted repair profile."""
    normal = replace(
        _contract(),
        proposal_protocol=comparison_module.COMPACT_PROPOSAL_PROTOCOL_V4,
    )
    expected_normal = (
        '{"baseline_sha":"'
        + ("a" * 40)
        + '","make_commands":["make test-files TESTFILES=tests/unit/test_example.py"],'
        '"proposal_protocol":"self-improve-compact-proposal-v4",'
        '"task_id":"S83.133","tests":["tests/unit/test_example.py"]}'
    )
    request = comparison_module.encode_prompt_batch(
        ("bounded repair prompt",),
        protocol_digest="f" * 64,
    )
    repair = ProposalContract.for_request(
        request=request,
        baseline_sha=normal.baseline_sha,
        task_id=normal.task_id,
        tests=normal.tests,
        make_commands=normal.make_commands,
        proposal_protocol=normal.proposal_protocol,
        sampling_profile=(
            comparison_module.COMPACT_V4_SYNTAX_REPAIR_SAMPLING_PROFILE_ID
        ),
    )

    assert normal.to_json() == expected_normal
    assert ProposalContract.from_json(normal.to_json()) == normal
    assert ProposalContract.from_json(repair.to_json()) == repair
    assert json.loads(repair.to_json())["sampling_profile"] == (
        comparison_module.COMPACT_V4_SYNTAX_REPAIR_SAMPLING_PROFILE_ID
    )


def test_repair_seed_is_reproducibly_derived_from_canonical_immutable_context() -> None:
    """Bind each sampled repair to its prompt, rejected draft, and trusted contract."""
    request = comparison_module.encode_prompt_batch(
        ('bounded prompt\nRejected compact object: {"e":[]}',),
        protocol_digest="f" * 64,
    )
    def contract_for(
        candidate_request: str,
        *,
        task_id: str = "S83.133",
    ) -> ProposalContract:
        return ProposalContract.for_request(
            request=candidate_request,
            baseline_sha="a" * 40,
            task_id=task_id,
            tests=("tests/unit/test_example.py",),
            make_commands=("make test-files TESTFILES=tests/unit/test_example.py",),
            proposal_protocol=comparison_module.COMPACT_PROPOSAL_PROTOCOL_V4,
            sampling_profile=(
                comparison_module.COMPACT_V4_SYNTAX_REPAIR_SAMPLING_PROFILE_ID
            ),
        )

    first = contract_for(request)
    repeated = contract_for(request)
    changed_draft = contract_for(
        comparison_module.encode_prompt_batch(
            ('bounded prompt\nRejected compact object: {"e":[{"s":1,"n":0,"z":"x"}]}',),
            protocol_digest="f" * 64,
        )
    )
    changed_contract = contract_for(request, task_id="S83.134")
    changed_model_context = contract_for(
        comparison_module.encode_prompt_batch(
            ('different bounded model context\nRejected compact object: {"e":[]}',),
            protocol_digest="f" * 64,
        )
    )
    changed_shard_state = ProposalContract.for_request(
        request=request,
        baseline_sha="a" * 40,
        task_id="S83.133",
        tests=("tests/unit/test_example.py",),
        make_commands=("make test-files TESTFILES=tests/unit/test_example.py",),
        proposal_protocol=comparison_module.COMPACT_PROPOSAL_PROTOCOL_V4,
        sampling_profile=(
            comparison_module.COMPACT_V4_SYNTAX_REPAIR_SAMPLING_PROFILE_ID
        ),
        repair_state_sha256="b" * 64,
    )

    assert first.sampling_seed == repeated.sampling_seed
    assert first.sampling_context_sha256 == repeated.sampling_context_sha256
    assert isinstance(first.sampling_seed, int)
    assert 1 <= first.sampling_seed <= (2**31 - 1)
    assert len(first.sampling_context_sha256) == 64
    expected_seed_digest = hashlib.sha256(
        f"{first.sampling_context_sha256}:0".encode("ascii")
    ).digest()
    expected_seed = int.from_bytes(expected_seed_digest[:4], "big") & ((2**31) - 1)
    assert first.sampling_seed == max(expected_seed, 1)
    assert changed_draft.sampling_seed != first.sampling_seed
    assert changed_contract.sampling_seed != first.sampling_seed
    assert changed_model_context.sampling_seed != first.sampling_seed
    assert changed_shard_state.sampling_seed != first.sampling_seed
    assert first.verify_sampling_context(request) == first.sampling_seed


def test_repair_seed_schedule_is_distinct_reproducible_and_round_trips() -> None:
    """Derive a fixed three-candidate schedule from one immutable repair context."""
    request = comparison_module.encode_prompt_batch(
        ('bounded prompt\nRejected compact object: {"e":[]}',),
        protocol_digest="f" * 64,
    )

    def candidate(index: int) -> ProposalContract:
        return ProposalContract.for_request(
            request=request,
            baseline_sha="a" * 40,
            task_id="S83.133",
            tests=("tests/unit/test_example.py",),
            make_commands=("make test-files TESTFILES=tests/unit/test_example.py",),
            proposal_protocol=comparison_module.COMPACT_PROPOSAL_PROTOCOL_V4,
            sampling_profile=(
                comparison_module.COMPACT_V4_SYNTAX_REPAIR_SAMPLING_PROFILE_ID
            ),
            sampling_candidate_index=index,
        )

    schedule = tuple(
        candidate(index)
        for index in range(comparison_module.COMPACT_V4_REPAIR_CANDIDATE_LIMIT)
    )
    repeated = tuple(
        candidate(index)
        for index in range(comparison_module.COMPACT_V4_REPAIR_CANDIDATE_LIMIT)
    )

    assert comparison_module.COMPACT_V4_REPAIR_CANDIDATE_LIMIT == 3
    assert schedule == repeated
    assert len({item.sampling_seed for item in schedule}) == len(schedule)
    assert len({item.sampling_context_sha256 for item in schedule}) == 1
    assert tuple(item.sampling_candidate_index for item in schedule) == (0, 1, 2)
    assert all(ProposalContract.from_json(item.to_json()) == item for item in schedule)


@pytest.mark.parametrize("candidate_index", [True, -1, 3, "1", None])
def test_repair_seed_schedule_rejects_malformed_or_excess_candidates(
    candidate_index: object,
) -> None:
    """Fail closed before decoding outside the identity-bound candidate cap."""
    request = comparison_module.encode_prompt_batch(
        ("bounded repair prompt",),
        protocol_digest="f" * 64,
    )

    with pytest.raises(ValueError, match="sampling candidate"):
        ProposalContract.for_request(
            request=request,
            baseline_sha="a" * 40,
            task_id="S83.133",
            tests=("tests/unit/test_example.py",),
            make_commands=("make test-files TESTFILES=tests/unit/test_example.py",),
            proposal_protocol=comparison_module.COMPACT_PROPOSAL_PROTOCOL_V4,
            sampling_profile=(
                comparison_module.COMPACT_V4_SYNTAX_REPAIR_SAMPLING_PROFILE_ID
            ),
            sampling_candidate_index=cast(int, candidate_index),
        )


def test_greedy_contract_rejects_nonzero_sampling_candidate() -> None:
    """Keep the ordinary first pass byte-stable and outside the repair schedule."""
    request = comparison_module.encode_prompt_batch(
        ("bounded ordinary prompt",),
        protocol_digest="f" * 64,
    )

    with pytest.raises(ValueError, match=r"greedy.*sampling candidate"):
        ProposalContract.for_request(
            request=request,
            baseline_sha="a" * 40,
            task_id="S83.133",
            tests=("tests/unit/test_example.py",),
            make_commands=("make test-files TESTFILES=tests/unit/test_example.py",),
            proposal_protocol=comparison_module.COMPACT_PROPOSAL_PROTOCOL_V4,
            sampling_candidate_index=1,
        )


@pytest.mark.parametrize(
    "field",
    [
        "sampling_seed",
        "sampling_context_sha256",
        "sampling_candidate_index",
        "repair_state_sha256",
    ],
)
def test_repair_seed_context_tampering_fails_closed(field: str) -> None:
    """Reject syntactically valid transport changes when recomputation disagrees."""
    request = comparison_module.encode_prompt_batch(
        ("bounded repair prompt",),
        protocol_digest="f" * 64,
    )
    contract = ProposalContract.for_request(
        request=request,
        baseline_sha="a" * 40,
        task_id="S83.133",
        tests=("tests/unit/test_example.py",),
        make_commands=("make test-files TESTFILES=tests/unit/test_example.py",),
        proposal_protocol=comparison_module.COMPACT_PROPOSAL_PROTOCOL_V4,
        sampling_profile=comparison_module.COMPACT_V4_SYNTAX_REPAIR_SAMPLING_PROFILE_ID,
    )
    value = json.loads(contract.to_json())
    assert isinstance(contract.sampling_seed, int)
    if field == "sampling_seed":
        value[field] = contract.sampling_seed + 1
    elif field == "sampling_context_sha256":
        value[field] = "b" * 64
    elif field == "sampling_candidate_index":
        value[field] = 1
    else:
        value[field] = "b" * 64
    tampered = ProposalContract.from_json(json.dumps(value))

    with pytest.raises(ValueError, match="sampling context mismatch"):
        tampered.verify_sampling_context(request)


def test_repair_seed_rejects_noncanonical_and_legacy_context_artifacts() -> None:
    """Never reinterpret pre-derivation repair contracts or noncanonical batches."""
    request = comparison_module.encode_prompt_batch(
        ("bounded repair prompt",),
        protocol_digest="f" * 64,
    )
    with pytest.raises(ValueError, match="canonical prompt batch"):
        ProposalContract.for_request(
            request=request + " ",
            baseline_sha="a" * 40,
            task_id="S83.133",
            tests=("tests/unit/test_example.py",),
            make_commands=("make test-files TESTFILES=tests/unit/test_example.py",),
            proposal_protocol=comparison_module.COMPACT_PROPOSAL_PROTOCOL_V4,
            sampling_profile=(
                comparison_module.COMPACT_V4_SYNTAX_REPAIR_SAMPLING_PROFILE_ID
            ),
        )
    normal_v4 = replace(
        _contract(), proposal_protocol=comparison_module.COMPACT_PROPOSAL_PROTOCOL_V4
    )
    legacy = {
        **json.loads(normal_v4.to_json()),
        "sampling_profile": "compact-v4-syntax-repair-seeded-sampling-v2",
        "sampling_seed": 104729,
        "sampling_context_sha256": "b" * 64,
    }
    with pytest.raises(ValueError, match="sampling profile"):
        ProposalContract.from_json(json.dumps(legacy))


@pytest.mark.parametrize(
    ("field", "invalid"),
    [
        ("sampling_seed", True),
        ("sampling_seed", 0),
        ("sampling_seed", 2**31),
        ("sampling_seed", "7"),
        ("sampling_seed", None),
        ("sampling_context_sha256", "B" * 64),
        ("sampling_context_sha256", "b" * 63),
        ("sampling_context_sha256", 7),
    ],
)
def test_repair_sampling_context_rejects_malformed_controls(
    field: str,
    invalid: object,
) -> None:
    """Reject malformed derived controls before a local sampler can observe them."""
    request = comparison_module.encode_prompt_batch(
        ("bounded repair prompt",),
        protocol_digest="f" * 64,
    )
    contract = ProposalContract.for_request(
        request=request,
        baseline_sha="a" * 40,
        task_id="S83.133",
        tests=("tests/unit/test_example.py",),
        make_commands=("make test-files TESTFILES=tests/unit/test_example.py",),
        proposal_protocol=comparison_module.COMPACT_PROPOSAL_PROTOCOL_V4,
        sampling_profile=comparison_module.COMPACT_V4_SYNTAX_REPAIR_SAMPLING_PROFILE_ID,
    )
    value = json.loads(contract.to_json())
    value[field] = invalid

    with pytest.raises(ValueError, match="derived sampling context"):
        ProposalContract.from_json(json.dumps(value))


@pytest.mark.parametrize("candidate_index", [True, -1, 3, "1"])
def test_repair_contract_json_rejects_malformed_candidate_index(
    candidate_index: object,
) -> None:
    """Reject a transported candidate index outside the immutable schedule."""
    request = comparison_module.encode_prompt_batch(
        ("bounded repair prompt",),
        protocol_digest="f" * 64,
    )
    contract = ProposalContract.for_request(
        request=request,
        baseline_sha="a" * 40,
        task_id="S83.133",
        tests=("tests/unit/test_example.py",),
        make_commands=("make test-files TESTFILES=tests/unit/test_example.py",),
        proposal_protocol=comparison_module.COMPACT_PROPOSAL_PROTOCOL_V4,
        sampling_profile=comparison_module.COMPACT_V4_SYNTAX_REPAIR_SAMPLING_PROFILE_ID,
    )
    value = json.loads(contract.to_json())
    value["sampling_candidate_index"] = candidate_index

    with pytest.raises(ValueError, match="sampling candidate"):
        ProposalContract.from_json(json.dumps(value))


@pytest.mark.parametrize("profile", [True, 7, "unknown-profile", ""])
def test_proposal_contract_rejects_malformed_repair_sampling_controls(
    profile: object,
) -> None:
    """Reject untrusted scalar controls instead of forwarding them to llama.cpp."""
    value = json.loads(
        replace(
            _contract(),
            proposal_protocol=comparison_module.COMPACT_PROPOSAL_PROTOCOL_V4,
        ).to_json()
    )
    value["sampling_profile"] = profile

    with pytest.raises(ValueError, match="sampling profile"):
        ProposalContract.from_json(json.dumps(value))


def test_legacy_contract_rejects_repair_sampling_profile() -> None:
    """Never reinterpret a legacy compact request as a sampled repair."""
    with pytest.raises(ValueError, match="compact-v4"):
        replace(
            _contract(),
            sampling_profile=(
                comparison_module.COMPACT_V4_SYNTAX_REPAIR_SAMPLING_PROFILE_ID
            ),
        )


@pytest.mark.parametrize(
    ("raw", "match"),
    [
        ("{", "valid JSON"),
        ("{}", "fields"),
        (
            json.dumps(
                {
                    "baseline_sha": 7,
                    "task_id": "S83.133",
                    "tests": ["tests/unit/test_example.py"],
                    "make_commands": [
                        "make test-files TESTFILES=tests/unit/test_example.py"
                    ],
                }
            ),
            "identity",
        ),
        (
            json.dumps(
                {
                    "baseline_sha": "a" * 40,
                    "task_id": "S83.133",
                    "tests": "tests/unit/test_example.py",
                    "make_commands": [
                        "make test-files TESTFILES=tests/unit/test_example.py"
                    ],
                }
            ),
            "tests",
        ),
        (
            json.dumps(
                {
                    "baseline_sha": "a" * 40,
                    "task_id": "S83.133",
                    "tests": [7],
                    "make_commands": [
                        "make test-files TESTFILES=tests/unit/test_example.py"
                    ],
                }
            ),
            "tests",
        ),
        (
            json.dumps(
                {
                    "baseline_sha": "a" * 40,
                    "task_id": "S83.133",
                    "tests": ["tests/unit/test_example.py"],
                    "make_commands": "make test-files",
                }
            ),
            "make_commands",
        ),
        (
            json.dumps(
                {
                    "baseline_sha": "a" * 40,
                    "task_id": "S83.133",
                    "tests": ["tests/unit/test_example.py"],
                    "make_commands": [7],
                }
            ),
            "make_commands",
        ),
        (
            json.dumps(
                {
                    "baseline_sha": "short",
                    "task_id": "S83.133",
                    "tests": ["tests/unit/test_example.py"],
                    "make_commands": ["make test-files TESTFILES=tests/unit/test_example.py"],
                }
            ),
            "baseline_sha",
        ),
        (
            json.dumps(
                {
                    "baseline_sha": "a" * 40,
                    "task_id": "bad",
                    "tests": ["tests/unit/test_example.py"],
                    "make_commands": ["make test-files TESTFILES=tests/unit/test_example.py"],
                }
            ),
            "task_id",
        ),
        (
            json.dumps(
                {
                    "baseline_sha": "a" * 40,
                    "task_id": "S83.133",
                    "tests": [
                        "tests/unit/test_example.py",
                        "tests/unit/test_example.py",
                    ],
                    "make_commands": ["make test-files TESTFILES=tests/unit/test_example.py"],
                }
            ),
            "duplicate",
        ),
        (
            json.dumps(
                {
                    "baseline_sha": "a" * 40,
                    "task_id": "S83.133",
                    "tests": ["tests/unit/test_example.py"],
                    "make_commands": ["python -m pytest"],
                }
            ),
            "make command",
        ),
        (
            json.dumps(
                {
                    "baseline_sha": "a" * 40,
                    "task_id": "S83.133",
                    "tests": ["tests/unit/test_example.py"],
                    "make_commands": [
                        "make test-files TESTFILES=tests/unit/test_example.py"
                    ],
                    "proposal_protocol": {"untrusted": "mapping"},
                }
            ),
            "protocol",
        ),
    ],
)
def test_proposal_contract_rejects_malformed_exchange_json(
    raw: str,
    match: str,
) -> None:
    with pytest.raises(ValueError, match=match):
        ProposalContract.from_json(raw)


def test_proposal_manifest_accepts_bounded_make_only_multi_file_plan() -> None:
    proposal = _proposal()
    assert proposal.task_id == "S83.133"
    assert proposal.edits[0].path == "src/general_ludd/example.py"
    assert proposal.edits[0].old_text == "return 0"
    assert proposal.edits[0].new_text == "return 42"
    assert proposal.make_commands[0].startswith("make ")


@pytest.mark.parametrize(
    "updates, match",
    [
        ({"baseline_sha": "short"}, "baseline_sha"),
        (
            {
                "edits": [
                    {
                        "operation": "replace",
                        "path": "../escape.py",
                        "old_text": "x",
                        "new_text": "y",
                    }
                ]
            },
            "path",
        ),
        (
            {"make_commands": ["python -m pytest tests/unit/test_example.py"]},
            "make command",
        ),
        (
            {"make_commands": ["make test-files; rm -rf /tmp/example"]},
            "metacharacter",
        ),
        ({"tests": ["../test_escape.py"]}, "test path"),
        ({"extra": "unreviewed"}, "unknown"),
    ],
)
def test_proposal_manifest_fails_closed_on_unsafe_or_ambiguous_input(
    updates: dict[str, object],
    match: str,
) -> None:
    with pytest.raises(ValueError, match=match):
        _proposal(**updates)


def test_proposal_manifest_rejects_symlink_escape(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside-self-improve"
    outside.mkdir(exist_ok=True)
    (tmp_path / "src").symlink_to(outside, target_is_directory=True)
    proposal = _proposal(
        edits=[
            {
                "operation": "create",
                "path": "src/escape.py",
                "old_text": "",
                "new_text": "unsafe = True\n",
            }
        ]
    )
    with pytest.raises(ValueError, match="escapes repository root"):
        proposal.validate_paths(tmp_path)


def test_comparison_accepts_only_full_codex_quality_parity() -> None:
    result = compare_with_codex(_proposal(), _evidence(), _reference())
    assert result.accepted is True
    assert result.score == 100.0
    assert result.blockers == ()


def test_comparison_rejects_green_tests_without_release_quality() -> None:
    result = compare_with_codex(
        _proposal(),
        _evidence(
            warnings=1,
            coverage_aggregate=84.9,
            coverage_min_file=74.9,
            cleanup_passed=False,
            worktree_clean=False,
        ),
        _reference(),
    )
    assert result.accepted is False
    assert {
        "warnings",
        "aggregate coverage",
        "per-file coverage",
        "resource cleanup",
        "clean worktree",
    } <= set(result.blockers)


def test_comparison_penalizes_bloated_scope_relative_to_codex() -> None:
    exact = compare_with_codex(_proposal(), _evidence(), _reference())
    bloated = compare_with_codex(
        _proposal(
            edits=[
                {
                    "operation": "replace",
                    "path": "src/general_ludd/example.py",
                    "old_text": "return 0",
                    "new_text": "return 42",
                },
                {
                    "operation": "create",
                    "path": "src/general_ludd/unrelated.py",
                    "old_text": "",
                    "new_text": "noise = 1\n",
                },
            ]
        ),
        _evidence(
            changed_files=frozenset(
                {
                    "src/general_ludd/example.py",
                    "src/general_ludd/unrelated.py",
                    "tests/unit/test_example.py",
                }
            )
        ),
        _reference(),
    )
    assert bloated.accepted is False
    assert bloated.score < exact.score
    assert "changed-file precision" in bloated.blockers


def test_retry_prompt_contains_deterministic_score_gaps() -> None:
    comparison = compare_with_codex(
        _proposal(),
        _evidence(mypy_passed=False, commit_count=2),
        _reference(),
    )
    prompt = build_retry_prompt(
        "Repair the example.",
        comparison,
        diagnostics=(
            "command=make test-files TESTFILES=tests/unit/test_example.py rc=1\n"
            "PSK=top-secret\nE assert 41 == 42"
        ),
    )
    assert "mypy" in prompt
    assert "atomic commit" in prompt
    assert "Do not broaden the changed-file set" in prompt
    assert "E assert 41 == 42" in prompt
    assert "PSK=<redacted>" in prompt
    assert "top-secret" not in prompt


@pytest.mark.parametrize(
    ("updates", "blocker"),
    [
        ({"tests_passed": False}, "tests"),
        ({"warnings": 1}, "warnings"),
        ({"coverage_aggregate": 84.0}, "aggregate coverage"),
        ({"coverage_min_file": 74.0}, "per-file coverage"),
        ({"ruff_passed": False}, "ruff"),
        ({"docstrings_passed": False}, "docstrings"),
        ({"markdown_passed": False}, "markdown"),
        ({"cleanup_passed": False}, "resource cleanup"),
        ({"worktree_clean": False}, "clean worktree"),
        ({"changed_lines": 40}, "diff size"),
        ({"elapsed_seconds": 30.0}, "tool efficiency"),
    ],
)
def test_comparison_scores_every_release_contract(
    updates: _EvidenceUpdates,
    blocker: str,
) -> None:
    result = compare_with_codex(_proposal(), _evidence(**updates), _reference())
    assert result.accepted is False
    assert blocker in result.blockers


def test_proposal_parser_rejects_each_malformed_edit_contract() -> None:
    base = json.loads(_proposal().to_json())

    malformed: list[tuple[dict[str, object], str]] = []
    wrong_edits = dict(base)
    wrong_edits["edits"] = "not-a-list"
    malformed.append((wrong_edits, "edits must"))

    for edit, match in [
        (
            {
                "operation": "unknown",
                "path": "src/example.py",
                "old_text": "a",
                "new_text": "b",
            },
            "unsupported",
        ),
        (
            {
                "operation": "replace",
                "path": 1,
                "old_text": "a",
                "new_text": "b",
            },
            "path",
        ),
        (
            {
                "operation": "replace",
                "path": "src/example.py",
                "old_text": 1,
                "new_text": "b",
            },
            "UTF-8",
        ),
        (
            {
                "operation": "replace",
                "path": "src/example.py",
                "old_text": "same",
                "new_text": "same",
            },
            "distinct",
        ),
        (
            {
                "operation": "create",
                "path": "src/example.py",
                "old_text": "exists",
                "new_text": "new",
            },
            "empty old_text",
        ),
        (
            {
                "operation": "delete",
                "path": "src/example.py",
                "old_text": "exists",
                "new_text": "still exists",
            },
            "empty new_text",
        ),
    ]:
        payload = dict(base)
        payload["edits"] = [edit]
        malformed.append((payload, match))

    duplicate = dict(base)
    duplicate["edits"] = [base["edits"][0], base["edits"][0]]
    malformed.append((duplicate, "duplicate edit"))

    for payload, match in malformed:
        with pytest.raises(ValueError, match=match):
            ProposalManifest.from_json(json.dumps(payload))


def test_proposal_parser_enforces_all_outer_bounds_and_identities() -> None:
    with pytest.raises(ValueError, match="JSON object"):
        ProposalManifest.from_json("[]")
    with pytest.raises(ValueError, match="missing fields"):
        ProposalManifest.from_json("{}")

    base = json.loads(_proposal().to_json())
    cases: list[tuple[dict[str, object], str]] = []

    wrong_schema = dict(base)
    wrong_schema["schema_version"] = 3
    cases.append((wrong_schema, "schema_version"))

    wrong_task = dict(base)
    wrong_task["task_id"] = "bad"
    cases.append((wrong_task, "task_id"))

    duplicate_tests = dict(base)
    duplicate_tests["tests"] = [
        "tests/unit/test_example.py",
        "tests/unit/test_example.py",
    ]
    cases.append((duplicate_tests, "duplicate test path"))

    no_commands = dict(base)
    no_commands["make_commands"] = []
    cases.append((no_commands, "make_commands"))

    huge_command = dict(base)
    huge_command["make_commands"] = ["make " + ("x" * 4097)]
    cases.append((huge_command, "exceeds"))

    newline_commit = dict(base)
    newline_commit["commit_message"] = "bad\nmessage"
    cases.append((newline_commit, "commit_message"))

    huge_content = dict(base)
    huge_content["edits"] = [
        {
            "operation": "replace",
            "path": "src/example.py",
            "old_text": "a",
            "new_text": "x" * 1_048_576,
        }
    ]
    cases.append((huge_content, "content exceeds"))

    for payload, match in cases:
        with pytest.raises(ValueError, match=match):
            ProposalManifest.from_json(json.dumps(payload))


def test_local_gateway_reports_bounded_output_without_json_start(tmp_path: Path) -> None:
    model_path = tmp_path / "model.gguf"
    model_path.write_bytes(b"GGUF")

    class FakeChatModel:
        def create_chat_completion(self, **_kwargs: object) -> dict[str, object]:
            return {"choices": [{"message": {"content": "plain text only"}}]}

        def __call__(self, prompt: str, **kwargs: object) -> object:
            raise AssertionError("raw completion must not be used")

    gateway = LocalProposalGateway(
        model_path,
        model_factory=lambda **_kwargs: FakeChatModel(),
    )

    with pytest.raises(ValueError, match=r"no JSON start.*output_bytes=15") as error:
        gateway.propose("Repair exactly.")

    assert "plain text" not in str(error.value)


def test_local_gateway_uses_explicit_model_and_deterministic_decode(
    tmp_path: Path,
) -> None:
    model_path = tmp_path / "model.gguf"
    model_path.write_bytes(b"GGUF")
    calls: dict[str, Any] = {}

    class FakeModel:
        def __call__(self, prompt: str, **kwargs: object) -> dict[str, object]:
            calls["prompt"] = prompt
            calls["decode"] = kwargs
            return {"choices": [{"text": json.dumps({
                "schema_version": 1,
                "baseline_sha": "a" * 40,
                "task_id": "S83.133",
                "edits": [
                    {
                        "operation": "replace",
                        "path": "src/general_ludd/example.py",
                        "old_text": "x = 0",
                        "new_text": "x = 1",
                    }
                ],
                "tests": ["tests/unit/test_example.py"],
                "make_commands": ["make test-files TESTFILES=tests/unit/test_example.py"],
                "commit_message": "fix: local proposal",
            })}]}

    def factory(**kwargs: object) -> FakeModel:
        calls["factory"] = kwargs
        return FakeModel()

    gateway = LocalProposalGateway(
        model_path,
        model_factory=factory,
        n_gpu_layers=12,
    )
    proposal = gateway.propose("Repair the example.")

    assert isinstance(proposal, ProposalManifest)
    assert proposal.task_id == "S83.133"
    assert calls["factory"] == {
        "model_path": str(model_path),
        "n_ctx": 0,
        "n_gpu_layers": 12,
        "verbose": False,
    }
    assert calls["decode"] == {
        "max_tokens": 4096,
        "temperature": 0.0,
        "echo": False,
    }


@pytest.mark.parametrize("n_gpu_layers", [True, -2, 1.5])
def test_local_gateway_rejects_invalid_hardware_offload_seam(
    tmp_path: Path,
    n_gpu_layers: int,
) -> None:
    model_path = tmp_path / "model.gguf"
    model_path.write_bytes(b"GGUF")

    with pytest.raises(ValueError, match="n_gpu_layers"):
        LocalProposalGateway(model_path, n_gpu_layers=n_gpu_layers)


def test_local_gateway_prefers_native_schema_constrained_chat_completion(
    tmp_path: Path,
) -> None:
    model_path = tmp_path / "model.gguf"
    model_path.write_bytes(b"GGUF")
    calls: dict[str, object] = {}
    proposal_json = json.dumps(
        {
            "schema_version": 1,
            "baseline_sha": "a" * 40,
            "task_id": "S83.133",
            "edits": [
                {
                    "operation": "replace",
                    "path": "src/general_ludd/example.py",
                    "old_text": "x = 0",
                    "new_text": "x = 1",
                }
            ],
            "tests": ["tests/unit/test_example.py"],
            "make_commands": ["make test-files TESTFILES=tests/unit/test_example.py"],
            "commit_message": "fix: local chat proposal",
        }
    )

    class FakeChatModel:
        def create_chat_completion(self, **kwargs: object) -> dict[str, object]:
            calls.update(kwargs)
            return {
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": proposal_json},
                    }
                ]
            }

        def __call__(self, prompt: str, **kwargs: object) -> object:
            raise AssertionError("raw completion must not be used when chat is available")

    gateway = LocalProposalGateway(
        model_path,
        model_factory=lambda **_kwargs: FakeChatModel(),
    )
    proposal = gateway.propose("Repair the example.")

    assert isinstance(proposal, ProposalManifest)
    assert proposal.commit_message == "fix: local chat proposal"
    assert calls["temperature"] == 0.0
    assert calls["max_tokens"] == 4096
    assert calls["seed"] == 0
    assert calls["grammar"] is None
    response_format = calls["response_format"]
    assert isinstance(response_format, dict)
    assert response_format["type"] == "json_object"
    schema = response_format["schema"]
    assert isinstance(schema, dict)
    assert schema["additionalProperties"] is False
    schema_text = json.dumps(schema, sort_keys=True)
    assert "maxLength" not in schema_text
    assert "minLength" not in schema_text
    assert set(schema["required"]) == {
        "schema_version",
        "baseline_sha",
        "task_id",
        "edits",
        "tests",
        "make_commands",
        "commit_message",
    }
    messages = calls["messages"]
    assert isinstance(messages, list)
    assert messages[-1] == {"role": "user", "content": "Repair the example."}


def test_compact_gateway_uses_one_fast_canary_and_expands_trusted_contract(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    model_path = tmp_path / "model.gguf"
    model_path.write_bytes(b"GGUF")
    calls: list[dict[str, object]] = []
    factory_calls = 0

    class CompactChatModel:
        def create_chat_completion(self, **kwargs: object) -> dict[str, object]:
            calls.append(dict(kwargs))
            if len(calls) == 1:
                content = json.dumps({"ok": True})
                usage = {
                    "prompt_tokens": 24,
                    "completion_tokens": 5,
                    "total_tokens": 29,
                }
            else:
                content = json.dumps(
                    {
                        "e": [
                            {
                                "a": "x = 0",
                                "z": "x = 1",
                            }
                        ]
                    }
                )
                usage = {
                    "prompt_tokens": 640,
                    "completion_tokens": 88,
                    "total_tokens": 728,
                }
            return {
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": content},
                    }
                ],
                "usage": usage,
            }

        def __call__(self, prompt: str, **kwargs: object) -> object:
            raise AssertionError("raw completion must not be used")

    def factory(**_kwargs: object) -> CompactChatModel:
        nonlocal factory_calls
        factory_calls += 1
        return CompactChatModel()

    contract = ProposalContract(
        baseline_sha="a" * 40,
        task_id="S83.133",
        tests=("tests/unit/test_example.py",),
        make_commands=("make test-files TESTFILES=tests/unit/test_example.py",),
    )
    gateway = LocalProposalGateway(model_path, model_factory=factory)
    first_prompt = comparison_module.bind_compact_focus_path(
        "Repair the example.",
        "src/general_ludd/example.py",
    )
    second_prompt = comparison_module.bind_compact_focus_path(
        "Repair the example again.",
        "src/general_ludd/example.py",
    )
    proposal = gateway.propose(first_prompt, contract=contract)
    second = gateway.propose(second_prompt, contract=contract)

    assert isinstance(proposal, ProposalManifest)
    assert isinstance(second, ProposalManifest)
    assert factory_calls == 1
    assert second == proposal
    assert proposal.baseline_sha == contract.baseline_sha
    assert proposal.task_id == contract.task_id
    assert proposal.tests == contract.tests
    assert proposal.make_commands == contract.make_commands
    assert proposal.edits[0].old_text == "x = 0"
    assert proposal.edits[0].path == "src/general_ludd/example.py"
    assert proposal.commit_message == "fix: apply bounded self-improvement proposal"
    assert [call["max_tokens"] for call in calls] == [32, 1024, 1024]
    canary_schema = calls[0]["response_format"]
    compact_schema = calls[1]["response_format"]
    assert isinstance(canary_schema, dict)
    assert isinstance(compact_schema, dict)
    assert canary_schema["schema"]["required"] == ["ok"]
    proposal_schema = compact_schema["schema"]
    assert isinstance(proposal_schema, dict)
    assert proposal_schema["required"] == ["e"]
    properties = proposal_schema["properties"]
    assert isinstance(properties, dict)
    edits_schema = properties["e"]
    assert isinstance(edits_schema, dict)
    edit_schema = edits_schema["items"]
    assert isinstance(edit_schema, dict)
    assert edit_schema["required"] == ["a", "z"]
    assert edit_schema["additionalProperties"] is False
    edit_properties = edit_schema["properties"]
    assert isinstance(edit_properties, dict)
    assert set(edit_properties) == {"a", "z"}
    assert "p" not in edit_properties
    assert "c" not in properties
    assert "maxLength" not in json.dumps(compact_schema, sort_keys=True)
    output = capsys.readouterr().out
    assert "phase=canary finish=stop" in output
    assert "phase=proposal finish=stop" in output
    assert "completion_tokens=88" in output


@pytest.mark.parametrize(
    ("old_text", "new_text", "expected_operation"),
    [
        ("x = 0", "x = 1", "replace"),
        ("", "created = True\n", "create"),
        ("obsolete = True\n", "", "delete"),
    ],
)
def test_compact_codec_infers_operation_only_from_validated_text(
    old_text: str,
    new_text: str,
    expected_operation: str,
) -> None:
    raw = json.dumps(
        {
            "e": [
                {
                    "a": old_text,
                    "z": new_text,
                }
            ]
        }
    )

    proposal = comparison_module._decode_compact_proposal(
        raw,
        _contract(),
        focus_path="src/general_ludd/example.py",
    )

    assert proposal.edits[0].operation == expected_operation
    assert proposal.edits[0].path == "src/general_ludd/example.py"
    assert proposal.commit_message == "fix: apply bounded self-improvement proposal"


@pytest.mark.parametrize("extra_field", ["o", "operation", "p"])
def test_compact_codec_rejects_parent_owned_model_fields(extra_field: str) -> None:
    item = {"a": "x = 0", "z": "x = 1", extra_field: "model-controlled"}
    raw = json.dumps({"e": [item]})

    with pytest.raises(ValueError, match="exactly a and z"):
        comparison_module._decode_compact_proposal(
            raw,
            _contract(),
            focus_path="src/general_ludd/example.py",
        )


def test_compact_protocol_bounds_one_file_output_before_manifest_limits() -> None:
    assert comparison_module._COMPACT_PROPOSAL_TOKENS == 1024
    assert comparison_module._COMPACT_MAX_CONTENT_BYTES == 3072
    assert "3,072 UTF-8 bytes total" in comparison_module._COMPACT_SYSTEM_PROMPT
    assert "n=0 may insert with s=x..y+1" in comparison_module._COMPACT_SYSTEM_PROMPT
    assert (
        "distinct non-empty strings"
        in comparison_module._LEGACY_COMPACT_SYSTEM_PROMPT
    )

    raw = json.dumps(
        {
            "e": [
                {
                    "a": "x = 0",
                    "z": "x" * 3068,
                }
            ]
        }
    )

    with pytest.raises(ValueError, match="compact edit content exceeds 3072 bytes"):
        comparison_module._decode_compact_proposal(
            raw,
            _contract(),
            focus_path="src/general_ludd/example.py",
        )


def test_compact_v4_binds_per_edit_line_limits_into_schema_and_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bound old and replacement lines without shrinking the UTF-8 byte authority."""
    assert comparison_module._COMPACT_SPAN_MAX_OLD_LINES == 64
    assert comparison_module._COMPACT_SPAN_MAX_NEW_LINES == 64
    assert comparison_module._COMPACT_SPAN_MAX_CHANGED_LINES == 96
    schema = comparison_module._compact_proposal_schema_for_ranges(((1, 130),))
    properties = cast(dict[str, object], schema["properties"])
    edits = cast(dict[str, object], properties["e"])
    item = cast(dict[str, object], edits["items"])
    item_properties = cast(dict[str, object], item["properties"])
    old_line_count = cast(dict[str, object], item_properties["n"])
    replacement = cast(dict[str, object], item_properties["z"])
    assert old_line_count["maximum"] == 64
    assert replacement["maxLength"] == 768
    assert "at most 64 old lines" in comparison_module._COMPACT_SYSTEM_PROMPT
    assert "at most 64 replacement lines" in comparison_module._COMPACT_SYSTEM_PROMPT
    assert "96 changed lines" in comparison_module._COMPACT_SYSTEM_PROMPT

    comparison_module.CompactLineSpan(
        start_line=1,
        old_line_count=64,
        new_text="line\r\n" * 64,
    )
    with pytest.raises(ValueError, match="old lines exceed 64"):
        comparison_module.CompactLineSpan(
            start_line=1,
            old_line_count=65,
            new_text="replacement\n",
        )
    with pytest.raises(ValueError, match="new lines exceed 64"):
        comparison_module.CompactLineSpan(
            start_line=1,
            old_line_count=1,
            new_text="replacement\n" * 65,
        )

    baseline = comparison_module.local_proposal_attempt_identity_digest("a" * 64)
    for name, changed in (
        ("_COMPACT_SPAN_MAX_OLD_LINES", 63),
        ("_COMPACT_SPAN_MAX_NEW_LINES", 63),
        ("_COMPACT_SPAN_MAX_CHANGED_LINES", 95),
    ):
        with monkeypatch.context() as scoped:
            scoped.setattr(comparison_module, name, changed)
            assert comparison_module.local_proposal_attempt_identity_digest("a" * 64) != baseline


def test_v4_identity_binds_snapshot_line_materialization_without_rotating_v3(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rotate corrected whole-line semantics while preserving legacy identity bytes."""
    v4 = comparison_module.local_proposal_attempt_identity_digest("a" * 64)
    v3 = comparison_module.local_proposal_attempt_identity_digest(
        "a" * 64,
        proposal_protocol=comparison_module.COMPACT_PROPOSAL_PROTOCOL_V3,
    )

    monkeypatch.setattr(
        comparison_module,
        "_COMPACT_LINE_MATERIALIZATION_POLICY",
        "mutated-for-regression",
    )

    assert comparison_module.local_proposal_attempt_identity_digest("a" * 64) != v4
    assert (
        comparison_module.local_proposal_attempt_identity_digest(
            "a" * 64,
            proposal_protocol=comparison_module.COMPACT_PROPOSAL_PROTOCOL_V3,
        )
        == v3
    )


def test_snapshot_manifest_content_bound_is_finite_and_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bound complete snapshot preimages and results independently of repetition."""
    monkeypatch.setattr(comparison_module, "_MAX_SNAPSHOT_CONTENT_BYTES", 64)
    exact = _proposal(
        schema_version=2,
        edits=[
            {
                "operation": "replace",
                "path": "src/general_ludd/example.py",
                "old_text": "a" * 32,
                "new_text": "b" * 32,
            }
        ],
    )
    assert exact.schema_version == 2

    with pytest.raises(ValueError, match="proposal edit content exceeds 64 bytes"):
        _proposal(
            schema_version=2,
            edits=[
                {
                    "operation": "replace",
                    "path": "src/general_ludd/example.py",
                    "old_text": "a" * 33,
                    "new_text": "b" * 32,
                }
            ],
        )


def test_compact_v4_total_line_budget_admits_reference_and_rejects_live_rewrite() -> None:
    """Admit the 33-add/4-delete reference but stop a 360-line rewrite pre-apply."""
    source_path = "src/general_ludd/local_model/_local_model_configs.py"
    test_path = "tests/unit/test_e2e_model_configs.py"
    contract = replace(
        _contract(),
        proposal_protocol=comparison_module.COMPACT_PROPOSAL_PROTOCOL_V4,
    )
    reference_proposals = (
        comparison_module.CompactSpanProposal(
            focus_path=source_path,
            edits=(comparison_module.CompactLineSpan(1, 4, ""),),
        ),
        comparison_module.CompactSpanProposal(
            focus_path=test_path,
            edits=(
                comparison_module.CompactLineSpan(
                    2,
                    0,
                    "".join(f"assert catalog[{index}]\n" for index in range(33)),
                ),
            ),
        ),
    )

    manifest = comparison_module.expand_compact_span_proposals(
        reference_proposals,
        contract=contract,
        expected_path_groups=((source_path,), (test_path,)),
        expected_baseline_files={
            source_path: "one\ntwo\nthree\nfour\n",
            test_path: "anchor\n",
        },
        expected_editable_ranges=(((1, 5),), ((1, 2),)),
    )

    assert len(manifest.edits) == 2
    assert sum(
        edit.old_line_count + len(edit.new_text.splitlines())
        for proposal in reference_proposals
        for edit in proposal.edits
    ) == 37

    live_rewrite = tuple(
        comparison_module.CompactSpanProposal(
            focus_path=path,
            edits=tuple(
                comparison_module.CompactLineSpan(start, 60, "")
                for start in (1, 61, 121)
            ),
        )
        for path in (source_path, test_path)
    )
    with pytest.raises(ValueError) as captured:
        comparison_module.expand_compact_span_proposals(
            live_rewrite,
            contract=contract,
            expected_path_groups=((source_path,), (test_path,)),
            expected_baseline_files={source_path: "secret", test_path: "secret"},
            expected_editable_ranges=(((1, 2),), ((1, 2),)),
        )
    assert str(captured.value) == (
        "SELF_IMPROVE_PARENT_PROPOSAL_ERROR compact span changed lines exceed 96; "
        "received_changed_lines=>96 max_changed_lines=96"
    )
    assert all(
        value not in str(captured.value)
        for value in (source_path, test_path, "secret")
    )


def test_compact_codec_rejects_noop_text_pair() -> None:
    raw = json.dumps({"e": [{"a": "", "z": ""}]})

    with pytest.raises(ValueError, match="must change content"):
        comparison_module._decode_compact_proposal(
            raw,
            _contract(),
            focus_path="src/general_ludd/example.py",
        )


@pytest.mark.parametrize(
    ("raw", "match"),
    [
        ("{", "complete JSON"),
        ("{}", "exactly e"),
        ('{"e":[]}', "1..16"),
        ('{"e":[7]}', "compact edit"),
        ('{"e":[{"a":7,"z":"y"}]}', "text fields"),
        ('{"e":[{"p":"src/example.py","a":"x","z":"y"}]}', "exactly a and z"),
    ],
)
def test_compact_proposal_codec_rejects_ambiguous_or_unsafe_output(
    raw: str,
    match: str,
) -> None:
    with pytest.raises(ValueError, match=match):
        comparison_module._decode_compact_proposal(
            raw,
            _contract(),
            focus_path="src/general_ludd/example.py",
        )


def test_compact_codec_rejects_untrusted_or_duplicate_focus_marker() -> None:
    raw = json.dumps({"e": [{"a": "x", "z": "y"}]})
    with pytest.raises(ValueError, match="focus path"):
        comparison_module._decode_compact_proposal(
            raw,
            _contract(),
            focus_path="../escape.py",
        )
    prompt = comparison_module.bind_compact_focus_path(
        "bounded task",
        "src/general_ludd/example.py",
    )
    with pytest.raises(ValueError, match="already contains"):
        comparison_module.bind_compact_focus_path(
            prompt,
            "src/general_ludd/example.py",
        )


def test_compact_v4_gateway_emits_only_bounded_line_span_fields(
    tmp_path: Path,
) -> None:
    model_path = tmp_path / "model.gguf"
    model_path.write_bytes(b"GGUF")
    calls: list[dict[str, object]] = []

    class SpanModel:
        def __call__(
            self,
            prompt: str,
            *,
            max_tokens: int,
            temperature: float,
            echo: bool,
        ) -> object:
            del prompt, max_tokens, temperature, echo
            raise AssertionError("compact mode must use chat completion")

        def create_chat_completion(self, **kwargs: object) -> dict[str, object]:
            calls.append(dict(kwargs))
            content = (
                '{"ok":true}'
                if len(calls) == 1
                else '{"e":[{"s":2,"n":1,"z":"changed\\n"}]}'
            )
            return {
                "choices": [
                    {"finish_reason": "stop", "message": {"content": content}}
                ]
            }

    contract = replace(
        _contract(),
        proposal_protocol="self-improve-compact-proposal-v4",
    )
    prompt = comparison_module.bind_compact_focus_path(
        "Use the numbered source lines.",
        "src/general_ludd/example.py",
    )

    proposal = LocalProposalGateway(
        model_path,
        model_factory=lambda **_kwargs: SpanModel(),
    ).propose(prompt, contract=contract)

    assert isinstance(proposal, comparison_module.CompactSpanProposal)
    assert proposal.focus_path == "src/general_ludd/example.py"
    assert proposal.edits == (
        comparison_module.CompactLineSpan(start_line=2, old_line_count=1, new_text="changed\n"),
    )
    schema = calls[1]["response_format"]
    assert isinstance(schema, dict)
    item = schema["schema"]["properties"]["e"]["items"]
    assert item["required"] == ["s", "n", "z"]
    assert set(item["properties"]) == {"s", "n", "z"}
    assert calls[1]["max_tokens"] == 4096


def test_compact_v4_repair_uses_reproducible_non_greedy_sampling_only(
    tmp_path: Path,
) -> None:
    """Diversify a repair deterministically without changing canary or first-pass decode."""
    model_path = tmp_path / "model.gguf"
    model_path.write_bytes(b"GGUF")
    prompt = comparison_module.bind_compact_focus_path(
        "Use the numbered source lines.",
        "src/general_ludd/example.py",
        editable_ranges=((1, 3),),
    )
    normal_contract = replace(
        _contract(),
        proposal_protocol=comparison_module.COMPACT_PROPOSAL_PROTOCOL_V4,
    )
    request = comparison_module.encode_prompt_batch(
        (prompt,),
        protocol_digest="f" * 64,
    )
    repair_contract = ProposalContract.for_request(
        request=request,
        baseline_sha=normal_contract.baseline_sha,
        task_id=normal_contract.task_id,
        tests=normal_contract.tests,
        make_commands=normal_contract.make_commands,
        proposal_protocol=normal_contract.proposal_protocol,
        sampling_profile=(
            comparison_module.COMPACT_V4_SYNTAX_REPAIR_SAMPLING_PROFILE_ID
        ),
    )

    def run(contract: ProposalContract) -> list[dict[str, object]]:
        calls: list[dict[str, object]] = []

        class SpanModel:
            def create_chat_completion(self, **kwargs: object) -> dict[str, object]:
                calls.append(dict(kwargs))
                content = (
                    '{"ok":true}'
                    if len(calls) == 1
                    else '{"e":[{"s":1,"n":1,"z":"changed\\n"}]}'
                )
                return {
                    "choices": [
                        {"finish_reason": "stop", "message": {"content": content}}
                    ]
                }

            def __call__(self, prompt: str, **kwargs: object) -> object:
                del prompt, kwargs
                raise AssertionError("compact mode must use chat completion")

        proposal = LocalProposalGateway(
            model_path,
            model_factory=lambda **_kwargs: SpanModel(),
        ).propose(prompt, contract=contract)
        assert isinstance(proposal, comparison_module.CompactSpanProposal)
        return calls

    normal_calls = run(normal_contract)
    first_repair_calls = run(repair_contract)
    second_repair_calls = run(repair_contract)

    assert normal_calls[0]["temperature"] == 0.0
    assert normal_calls[0]["seed"] == 0
    assert normal_calls[1]["temperature"] == 0.0
    assert normal_calls[1]["seed"] == 0
    assert "top_p" not in normal_calls[1]
    assert "top_k" not in normal_calls[1]
    assert first_repair_calls[0]["temperature"] == 0.0
    assert first_repair_calls[0]["seed"] == 0
    assert "top_p" not in first_repair_calls[0]
    assert "top_k" not in first_repair_calls[0]
    assert {
        key: first_repair_calls[1][key]
        for key in ("temperature", "top_p", "top_k", "seed")
    } == {
        "temperature": 0.8,
        "top_p": 0.95,
        "top_k": 40,
        "seed": repair_contract.sampling_seed,
    }
    assert first_repair_calls[1] == second_repair_calls[1]
    assert first_repair_calls[1]["response_format"] == normal_calls[1]["response_format"]
    assert first_repair_calls[1]["max_tokens"] == normal_calls[1]["max_tokens"]


def test_compact_v4_gateway_passes_distinct_explicit_json_schema_grammars(
    tmp_path: Path,
) -> None:
    """Use llama.cpp grammar objects for both the canary and v4 proposal."""
    model_path = tmp_path / "model.gguf"
    model_path.write_bytes(b"GGUF")
    calls: list[dict[str, object]] = []
    grammar_schemas: list[dict[str, object]] = []
    grammars: list[object] = []

    def grammar_factory(schema: dict[str, object]) -> object:
        grammar_schemas.append(schema)
        grammar = object()
        grammars.append(grammar)
        return grammar

    class SpanModel:
        def __call__(self, prompt: str, **kwargs: object) -> object:
            del prompt, kwargs
            raise AssertionError("raw completion must not be used")

        def create_chat_completion(self, **kwargs: object) -> dict[str, object]:
            calls.append(dict(kwargs))
            content = (
                '{"ok":true}'
                if len(calls) == 1
                else '{"e":[{"s":1,"n":1,"z":"changed\\n"}]}'
            )
            return {
                "choices": [
                    {"finish_reason": "stop", "message": {"content": content}}
                ]
            }

    contract = replace(
        _contract(),
        proposal_protocol=comparison_module.COMPACT_PROPOSAL_PROTOCOL_V4,
    )
    prompt = comparison_module.bind_compact_focus_path(
        "Use the numbered source lines.",
        "src/general_ludd/example.py",
    )

    proposal = LocalProposalGateway(
        model_path,
        model_factory=lambda **_kwargs: SpanModel(),
        grammar_factory=grammar_factory,
    ).propose(prompt, contract=contract)

    assert isinstance(proposal, comparison_module.CompactSpanProposal)
    assert grammar_schemas == [
        comparison_module._STRUCTURED_CANARY_SCHEMA,
        comparison_module._COMPACT_PROPOSAL_JSON_SCHEMA,
    ]
    assert calls[0]["grammar"] is grammars[0]
    assert calls[1]["grammar"] is grammars[1]
    assert grammars[0] is not grammars[1]


def test_compact_v4_gateway_compiles_parent_scope_into_integer_enum(
    tmp_path: Path,
) -> None:
    """Constrain s to exact shown-section boundaries before model sampling."""
    model_path = tmp_path / "model.gguf"
    model_path.write_bytes(b"GGUF")
    calls: list[dict[str, object]] = []
    grammar_schemas: list[dict[str, object]] = []

    class SpanModel:
        def __call__(self, prompt: str, **kwargs: object) -> object:
            del prompt, kwargs
            raise AssertionError("raw completion must not be used")

        def create_chat_completion(self, **kwargs: object) -> dict[str, object]:
            calls.append(dict(kwargs))
            content = (
                '{"ok":true}'
                if len(calls) == 1
                else '{"e":[{"s":3,"n":1,"z":"changed\\n"}]}'
            )
            return {
                "choices": [
                    {"finish_reason": "stop", "message": {"content": content}}
                ]
            }

    def grammar_factory(schema: dict[str, object]) -> object:
        grammar_schemas.append(schema)
        return object()

    prompt = comparison_module.bind_compact_focus_path(
        "Use only the numbered source lines.",
        "src/general_ludd/example.py",
        editable_ranges=((3, 6), (10, 12)),
    )
    proposal = LocalProposalGateway(
        model_path,
        model_factory=lambda **_kwargs: SpanModel(),
        grammar_factory=grammar_factory,
    ).propose(
        prompt,
        contract=replace(
            _contract(),
            proposal_protocol=comparison_module.COMPACT_PROPOSAL_PROTOCOL_V4,
        ),
    )

    assert isinstance(proposal, comparison_module.CompactSpanProposal)
    root_properties = cast(dict[str, object], grammar_schemas[1]["properties"])
    edits = cast(dict[str, object], root_properties["e"])
    item = cast(dict[str, object], edits["items"])
    item_properties = cast(dict[str, object], item["properties"])
    assert item_properties["s"] == {
        "type": "integer",
        "enum": [3, 4, 5, 6, 10, 11, 12],
    }
    assert item_properties["n"] == {
        "type": "integer",
        "minimum": 0,
        "maximum": 3,
    }
    assert calls[1]["response_format"] == {
        "type": "json_object",
        "schema": grammar_schemas[1],
    }


def test_compact_v4_gateway_hides_parent_bindings_from_model_visible_prompt(
    tmp_path: Path,
) -> None:
    """Keep trusted decoder metadata out of model-authored replacement text."""
    model_path = tmp_path / "model.gguf"
    model_path.write_bytes(b"GGUF")
    calls: list[dict[str, object]] = []

    class SpanModel:
        def __call__(
            self,
            prompt: str,
            *,
            max_tokens: int,
            temperature: float,
            echo: bool,
        ) -> object:
            del prompt, max_tokens, temperature, echo
            raise AssertionError("compact v4 must use chat completion")

        def create_chat_completion(self, **kwargs: object) -> dict[str, object]:
            calls.append(dict(kwargs))
            content = (
                '{"ok":true}'
                if len(calls) == 1
                else '{"e":[{"s":3,"n":1,"z":"value = 2\\n"}]}'
            )
            return {
                "choices": [
                    {"finish_reason": "stop", "message": {"content": content}}
                ]
            }

    def model_factory(
        *,
        model_path: str,
        n_ctx: int,
        verbose: bool,
        n_gpu_layers: int = 0,
    ) -> SpanModel:
        del model_path, n_ctx, verbose, n_gpu_layers
        return SpanModel()

    visible = (
        "EDIT_TASK_BEGIN\nRepair the catalog mapping.\nEDIT_TASK_END\n"
        "FOCUS_BASELINE_BEGIN\n"
        "FILE src/general_ludd/example.py state=present\n"
        "LINES 3-3\nL3|value = 1\n"
        "FOCUS_BASELINE_END"
    )
    bound = comparison_module.bind_compact_focus_path(
        visible,
        "src/general_ludd/example.py",
        editable_ranges=((3, 4),),
    )

    LocalProposalGateway(
        model_path,
        model_factory=model_factory,
        grammar_factory=lambda _schema: object(),
    ).propose(
        bound,
        contract=replace(
            _contract(),
            proposal_protocol=comparison_module.COMPACT_PROPOSAL_PROTOCOL_V4,
        ),
    )

    messages = cast(list[dict[str, str]], calls[1]["messages"])
    assert messages[-1] == {"role": "user", "content": visible}
    assert "Repair the catalog mapping." in messages[-1]["content"]
    assert "FILE src/general_ludd/example.py" in messages[-1]["content"]
    assert "GLUDD_SELF_IMPROVE_" not in messages[-1]["content"]


def test_compact_v4_scope_marker_collision_and_enum_overflow_fail_closed() -> None:
    """Trust only one parent-prepended scope and bound grammar construction."""
    marker = "GLUDD_SELF_IMPROVE_EDITABLE_RANGES="
    with pytest.raises(ValueError, match="already contains an editable-range marker"):
        comparison_module.bind_compact_focus_path(
            f"task text\n{marker}[[1,2]]",
            "src/general_ludd/example.py",
            editable_ranges=((1, 2),),
        )

    with pytest.raises(ValueError, match="scope coordinate enum exceeds 2048"):
        comparison_module.bind_compact_focus_path(
            "bounded task",
            "src/general_ludd/example.py",
            editable_ranges=((1, 2049),),
        )


@pytest.mark.parametrize(
    ("encoded", "match"),
    [
        ("[[1,3],[1,3]]", "ordered half-open ranges"),
        ("[[1,4],[3,5]]", "ordered half-open ranges"),
        ("[[3,5],[1,3]]", "ordered half-open ranges"),
        ("[[1, 3]]", "not canonical JSON"),
        ("[[1,3]]\N{NO-BREAK SPACE}", "must be ASCII"),
        (f"[[{'9' * 5000},1]]", "not canonical JSON"),
    ],
)
def test_compact_v4_scope_marker_rejects_noncanonical_sections(
    encoded: str,
    match: str,
) -> None:
    """Reject duplicate, overlapping, unordered, or noncanonical scope markers."""
    marker = "GLUDD_SELF_IMPROVE_EDITABLE_RANGES="
    prompt = (
        f"{marker}{encoded}\n"
        "GLUDD_SELF_IMPROVE_FOCUS_PATH=src/general_ludd/example.py\n"
        "bounded task"
    )

    with pytest.raises(ValueError, match=match):
        comparison_module._trusted_compact_editable_ranges(prompt)


def test_compact_v4_scope_marker_has_an_independent_byte_bound() -> None:
    """Reject an oversized leading marker before JSON parsing or grammar work."""
    marker = "GLUDD_SELF_IMPROVE_EDITABLE_RANGES="
    oversized = marker + ("0" * (16_384 - len(marker) + 1))

    with pytest.raises(ValueError, match="editable-range marker exceeds 16384 bytes"):
        comparison_module._trusted_compact_editable_ranges(f"{oversized}\nbounded task")


def test_compact_v4_scope_coordinates_cannot_exceed_baseline_byte_space() -> None:
    """Reject sparse huge coordinates even when their enum cardinality is small."""
    with pytest.raises(ValueError, match="outside bounded baseline coordinates"):
        comparison_module._compact_proposal_schema_for_ranges(
            ((1_048_577, 1_048_578),)
        )


@pytest.mark.parametrize(
    ("prompt", "match"),
    [
        (
            "bounded task\nGLUDD_SELF_IMPROVE_EDITABLE_RANGES=[[1,2]]",
            "must be the first prompt line",
        ),
        (
            "GLUDD_SELF_IMPROVE_EDITABLE_RANGES=[[1,2]]\n"
            "GLUDD_SELF_IMPROVE_EDITABLE_RANGES=[[3,4]]",
            "exactly one editable-range marker",
        ),
        (
            "GLUDD_SELF_IMPROVE_EDITABLE_RANGES={\nbounded task",
            "not canonical JSON",
        ),
        (
            "GLUDD_SELF_IMPROVE_EDITABLE_RANGES=[1,2]\nbounded task",
            "must contain integer pairs",
        ),
        (
            "GLUDD_SELF_IMPROVE_EDITABLE_RANGES=[[true,2]]\nbounded task",
            "must contain integer pairs",
        ),
    ],
)
def test_compact_v4_scope_marker_fail_closed_parser_paths(
    prompt: str,
    match: str,
) -> None:
    """Reject every ambiguous leading-marker shape without reading source labels."""
    with pytest.raises(ValueError, match=match):
        comparison_module._trusted_compact_editable_ranges(prompt)


def test_compact_v4_scope_enum_is_sorted_and_deduplicates_adjacent_boundaries() -> None:
    """Compile adjacent half-open sections to one ordered unique integer enum."""
    schema = comparison_module._compact_proposal_schema_for_ranges(
        ((8, 10), (10, 12), (15, 16))
    )
    root_properties = cast(dict[str, object], schema["properties"])
    edits = cast(dict[str, object], root_properties["e"])
    item = cast(dict[str, object], edits["items"])
    item_properties = cast(dict[str, object], item["properties"])

    assert item_properties["s"] == {
        "type": "integer",
        "enum": [8, 9, 10, 11, 12, 15, 16],
    }
    assert item_properties["n"] == {
        "type": "integer",
        "minimum": 0,
        "maximum": 2,
    }
    assert edits["maxItems"] == 4
    assert item_properties["z"] == {"type": "string", "maxLength": 768}


def test_compact_v4_schema_bounds_runaway_replacement_text() -> None:
    """Bound each generated replacement while retaining the parent byte ceiling."""
    schema = comparison_module._compact_proposal_schema_for_ranges(((3, 6),))
    root_properties = cast(dict[str, object], schema["properties"])
    edits = cast(dict[str, object], root_properties["e"])
    item = cast(dict[str, object], edits["items"])
    item_properties = cast(dict[str, object], item["properties"])

    assert edits["minItems"] == 1
    assert edits["maxItems"] == 4
    assert item_properties["z"] == {
        "type": "string",
        "maxLength": 768,
    }
    assert comparison_module._COMPACT_MAX_CONTENT_BYTES == 3072


def test_compact_v4_schema_preserves_multiple_edits_in_one_shown_section() -> None:
    """Do not trade the line-span protocol's same-section multi-edit support for closure."""
    schema = comparison_module._compact_proposal_schema_for_ranges(((3, 20),))
    root_properties = cast(dict[str, object], schema["properties"])
    edits = cast(dict[str, object], root_properties["e"])
    item = cast(dict[str, object], edits["items"])
    item_properties = cast(dict[str, object], item["properties"])

    assert edits["maxItems"] == 4
    assert item_properties["z"] == {"type": "string", "maxLength": 768}


def test_compact_v4_multisection_schema_conservatively_bounds_unicode_bytes() -> None:
    """Allocate the shared byte ceiling across sections at four bytes per codepoint."""
    schema = comparison_module._compact_proposal_schema_for_ranges(
        ((1, 3), (8, 10), (20, 21))
    )
    root_properties = cast(dict[str, object], schema["properties"])
    edits = cast(dict[str, object], root_properties["e"])
    item = cast(dict[str, object], edits["items"])
    item_properties = cast(dict[str, object], item["properties"])
    z_schema = cast(dict[str, object], item_properties["z"])

    assert edits["maxItems"] == 4
    assert z_schema["maxLength"] == 768
    assert 768 * len("😀".encode()) == 3072


def test_compact_v4_maximum_sections_have_a_finite_per_item_content_budget() -> None:
    """Keep every grammar dimension finite at the four-edit shard limit."""
    ranges = tuple((line, line + 1) for line in range(1, 48, 3))
    schema = comparison_module._compact_proposal_schema_for_ranges(ranges)
    root_properties = cast(dict[str, object], schema["properties"])
    edits = cast(dict[str, object], root_properties["e"])
    item = cast(dict[str, object], edits["items"])
    item_properties = cast(dict[str, object], item["properties"])
    start_schema = cast(dict[str, object], item_properties["s"])
    length_schema = cast(dict[str, object], item_properties["n"])
    content_schema = cast(dict[str, object], item_properties["z"])

    assert edits["maxItems"] == 4
    assert len(cast(list[int], start_schema["enum"])) == 32
    assert length_schema["maximum"] == 1
    assert content_schema["maxLength"] == 768
    assert (
        768 * comparison_module._COMPACT_MAX_UTF8_BYTES_PER_CODEPOINT
        == comparison_module._COMPACT_MAX_CONTENT_BYTES
    )
    assert comparison_module._COMPACT_SPAN_PROPOSAL_TOKENS == 4096
    assert comparison_module._STRUCTURED_OUTPUT_REQUIRE_STOP is True


def test_compact_v3_retains_its_historical_sixteen_edit_limit() -> None:
    """Keep stored v3 schema and decoder semantics unchanged by the v4 cap."""
    root = cast(
        dict[str, object],
        comparison_module._LEGACY_COMPACT_PROPOSAL_JSON_SCHEMA["properties"],
    )
    edits_schema = cast(dict[str, object], root["e"])
    raw = json.dumps(
        {
            "e": [
                {"a": f"old-{index}", "z": f"new-{index}"}
                for index in range(16)
            ]
        }
    )

    assert edits_schema["maxItems"] == 16
    manifest = comparison_module._decode_compact_proposal(
        raw,
        _contract(),
        focus_path="src/general_ludd/example.py",
    )
    assert len(manifest.edits) == 16


def test_compact_v4_parent_counts_decoded_utf8_not_json_escape_bytes() -> None:
    """Keep the parent byte cap authoritative after JSON escape decoding."""
    raw = json.dumps(
        {
            "e": [
                {"s": 1, "n": 1, "z": "😀" * 257},
                {"s": 8, "n": 1, "z": "😀" * 257},
                {"s": 20, "n": 1, "z": "😀" * 257},
            ]
        }
    )

    assert len(raw.encode("utf-8")) > 3072
    with pytest.raises(ValueError, match="new text exceeds 3072 bytes"):
        comparison_module._decode_compact_span_proposal(
            raw,
            focus_path="src/general_ludd/example.py",
        )


def test_compact_v4_empty_scope_schema_allows_only_create_coordinate() -> None:
    """Constrain an absent-file shard to the sole valid create coordinate."""
    schema = comparison_module._compact_proposal_schema_for_ranges(())
    root_properties = cast(dict[str, object], schema["properties"])
    edits = cast(dict[str, object], root_properties["e"])
    item = cast(dict[str, object], edits["items"])
    item_properties = cast(dict[str, object], item["properties"])

    assert edits["maxItems"] == 1
    assert item_properties["s"] == {"type": "integer", "enum": [1]}
    assert item_properties["n"] == {
        "type": "integer",
        "minimum": 0,
        "maximum": 0,
    }
    assert item_properties["z"] == {"type": "string", "maxLength": 768}


def test_locked_llama_grammar_compiles_multirange_integer_enum() -> None:
    """Exercise the locked 0.3.24 converter without fragile anyOf/oneOf."""
    if importlib.util.find_spec("llama_cpp") is None:
        pytest.skip("locked optional local-inference extra is not materialized")
    schema = comparison_module._compact_proposal_schema_for_ranges(
        ((3, 6), (10, 12))
    )
    encoded_schema = json.dumps(schema, ensure_ascii=True, separators=(",", ":"))
    script = (
        "from llama_cpp import LlamaGrammar, _utils\n"
        f"grammar = LlamaGrammar.from_json_schema({encoded_schema!r}, verbose=False)\n"
        "assert grammar is not None\n"
        "_utils.outnull_file.close()\n"
        "_utils.errnull_file.close()\n"
    )

    completed = subprocess.run(
        [sys.executable, "-c", script],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert completed.returncode == 0, completed.stderr
    assert "ResourceWarning" not in completed.stderr


def test_locked_llama_grammar_honors_small_string_and_array_bounds() -> None:
    """Prove locked 0.3.24 turns maxLength/maxItems into bounded GBNF."""
    if importlib.util.find_spec("llama_cpp") is None:
        pytest.skip("locked optional local-inference extra is not materialized")
    schema = comparison_module._compact_proposal_schema_for_ranges(((3, 6), (10, 12)))
    properties = cast(dict[str, object], schema["properties"])
    edits = cast(dict[str, object], properties["e"])
    item = cast(dict[str, object], edits["items"])
    item_properties = cast(dict[str, object], item["properties"])
    item_properties["z"] = {"type": "string", "maxLength": 8}
    encoded_schema = json.dumps(schema, ensure_ascii=True, separators=(",", ":"))
    script = (
        "from llama_cpp import LlamaGrammar, _utils\n"
        f"grammar = LlamaGrammar.from_json_schema({encoded_schema!r}, verbose=False)\n"
        "rendered = grammar._grammar\n"
        "array_rule = next(line for line in rendered.splitlines() if line.startswith('e ::='))\n"
        "string_rule = next(line for line in rendered.splitlines() if line.startswith('e-item-z ::='))\n"
        "assert array_rule.count('e-item') == 4, rendered\n"
        "assert string_rule.count('char') == 8, rendered\n"
        "assert '*' not in rendered and '+' not in rendered, rendered\n"
        "_utils.outnull_file.close()\n"
        "_utils.errnull_file.close()\n"
    )

    completed = subprocess.run(
        [sys.executable, "-c", script],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert completed.returncode == 0, completed.stderr
    assert "ResourceWarning" not in completed.stderr


@pytest.mark.parametrize(
    ("raw", "match"),
    [
        ({"e": []}, "1..4"),
        ({"e": [{"s": True, "n": 1, "z": "x"}]}, "integers, not booleans"),
        ({"e": [{"s": 1, "n": False, "z": "x"}]}, "integers, not booleans"),
        ({"e": [{"s": 0, "n": 1, "z": "x"}]}, "positive"),
        ({"e": [{"s": 1, "n": -1, "z": "x"}]}, "non-negative"),
        ({"e": [{"s": 1, "n": 0, "z": ""}]}, "must change content"),
        ({"e": [{"s": 1, "n": 1, "z": 7}]}, "new text must be a string"),
        ({"e": [{"s": 1, "n": 1, "z": "x", "a": "old"}]}, "exactly n, s, and z"),
        (
            {"e": [{"s": 3, "n": 2, "z": "x"}, {"s": 4, "n": 1, "z": "y"}]},
            "must not overlap",
        ),
        (
            {"e": [{"s": 2, "n": 0, "z": "x"}, {"s": 2, "n": 0, "z": "y"}]},
            "distinct start coordinates",
        ),
    ],
)
def test_compact_v4_decoder_rejects_ambiguous_spans(raw: object, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        _span_proposal(raw)


def test_compact_v4_decoder_enforces_new_text_byte_budget() -> None:
    with pytest.raises(ValueError, match="exceeds 3072 bytes"):
        _span_proposal({"e": [{"s": 1, "n": 1, "z": "x" * 3073}]})


def test_compact_v4_decoder_canonicalizes_unordered_snapshot_spans() -> None:
    """Sort valid model spans by immutable baseline coordinate before expansion."""
    proposal = _span_proposal(
        {
            "e": [
                {"s": 4, "n": 1, "z": "delta = 2\n"},
                {"s": 1, "n": 1, "z": "alpha = 2\n"},
            ]
        }
    )

    assert tuple(edit.start_line for edit in proposal.edits) == (1, 4)


@pytest.mark.parametrize(
    ("edits", "match"),
    (
        (
            (
                {"s": 4, "n": 2, "z": "right\n"},
                {"s": 3, "n": 2, "z": "left\n"},
            ),
            "must not overlap",
        ),
        (
            (
                {"s": 4, "n": 0, "z": "first\n"},
                {"s": 4, "n": 0, "z": "second\n"},
            ),
            "distinct start coordinates",
        ),
    ),
)
def test_compact_v4_decoder_rejects_overlap_after_canonical_sort(
    edits: tuple[dict[str, object], ...],
    match: str,
) -> None:
    """Canonical sorting must not turn duplicate or overlapping spans into authority."""
    with pytest.raises(ValueError, match=match):
        _span_proposal({"e": list(edits)})


def test_compact_v4_decoder_checks_aggregate_budget_before_input_order() -> None:
    """Classify the Qwen overgeneration class before any harmless input ordering."""
    secret = "PRIVATE_SOURCE=" + ("😀" * 768)
    raw = {
        "e": [
            {"s": 7, "n": 1, "z": secret},
            {"s": 1, "n": 1, "z": "😀" * 768},
        ]
    }

    with pytest.raises(ValueError, match="new text exceeds 3072 bytes") as captured:
        _span_proposal(raw)

    detail = str(captured.value)
    assert "received_edits=2" in detail
    assert "received_content_bytes=>3072" in detail
    assert "PRIVATE_SOURCE" not in detail
    assert secret not in detail
    assert len(detail.encode("utf-8")) <= 192


def test_compact_v4_parent_rechecks_content_budget_across_shards() -> None:
    """Frozen and regenerated shards must share the original aggregate byte cap."""
    secret = "PRIVATE_SOURCE=" + ("x" * 1_590)
    proposals = (
        _span_proposal(
            {"e": [{"s": 1, "n": 1, "z": secret}]},
            path="src/general_ludd/one.py",
        ),
        _span_proposal(
            {"e": [{"s": 1, "n": 1, "z": "y" * 1_600}]},
            path="src/general_ludd/two.py",
        ),
    )

    with pytest.raises(ValueError, match="new text exceeds 3072 bytes") as captured:
        _expand_span_proposals(
            proposals,
            paths=("src/general_ludd/one.py", "src/general_ludd/two.py"),
            baselines={
                "src/general_ludd/one.py": "old\n",
                "src/general_ludd/two.py": "old\n",
            },
            editable_ranges=(((1, 2),), ((1, 2),)),
        )

    detail = str(captured.value)
    assert "received_shards=2" in detail
    assert "received_content_bytes=>3072" in detail
    assert "PRIVATE_SOURCE" not in detail
    assert secret not in detail
    assert len(detail.encode("utf-8")) <= 192


def test_compact_v4_decoder_rejects_fifth_edit_with_bounded_telemetry() -> None:
    """Keep one shard useful while preventing the observed sixteen-edit runaway."""
    edits = [
        {"s": index * 2 + 1, "n": 1, "z": f"value_{index} = True\n"}
        for index in range(5)
    ]

    with pytest.raises(ValueError, match=r"1\.\.4 entries") as captured:
        _span_proposal({"e": edits})

    detail = str(captured.value)
    assert "received_edits=>4 max_edits=4" in detail
    assert "value_" not in detail
    assert len(detail.encode("utf-8")) <= 192


def test_compact_v4_parent_derives_unique_preimage_for_duplicate_source_text() -> None:
    baseline = "header\nsame\nsame\ntail\n"
    proposal = _span_proposal({"e": [{"s": 3, "n": 1, "z": "changed\n"}]})

    manifest = _expand_span_proposals(
        (proposal,),
        baselines={"src/general_ludd/example.py": baseline},
        editable_ranges=(((1, 5),),),
    )

    edit = manifest.edits[0]
    assert edit.operation == "replace"
    assert baseline.count(edit.old_text) == 1
    assert baseline.replace(edit.old_text, edit.new_text, 1) == (
        "header\nsame\nchanged\ntail\n"
    )


@pytest.mark.parametrize(
    ("raw", "ranges", "match"),
    [
        ({"e": [{"s": 2, "n": 1, "z": "changed\n"}]}, ((1, 2),), "explicitly shown"),
        (
            {"e": [{"s": 4, "n": 0, "z": "inserted\n"}]},
            ((1, 3),),
            "first shown line through one past the last shown line",
        ),
        ({"e": [{"s": 6, "n": 1, "z": "changed\n"}]}, ((1, 5),), "outside trusted baseline"),
        ({"e": [{"s": 2, "n": 1, "z": "same\n"}]}, ((1, 5),), "must change content"),
    ],
)
def test_compact_v4_parent_rejects_hidden_out_of_range_and_noop_spans(
    raw: object,
    ranges: tuple[tuple[int, int], ...],
    match: str,
) -> None:
    proposal = _span_proposal(raw)

    with pytest.raises(ValueError, match=match):
        _expand_span_proposals((proposal,), editable_ranges=(ranges,))


def test_compact_v4_insertion_uses_closed_boundaries_of_each_shown_section() -> None:
    """Admit shard-edge coordinates while rejecting a boundary in a hidden gap."""
    path = "src/general_ludd/example.py"
    baseline = "hidden-a\nshown-b\nshown-c\nhidden-d\nhidden-e\nshown-f\nshown-g\n"
    ranges = (((2, 4), (6, 8)),)
    expected_by_start = {
        2: "hidden-a\ninserted\nshown-b\nshown-c\nhidden-d\nhidden-e\nshown-f\nshown-g\n",
        4: "hidden-a\nshown-b\nshown-c\ninserted\nhidden-d\nhidden-e\nshown-f\nshown-g\n",
    }

    for start_line, expected in expected_by_start.items():
        manifest = _expand_span_proposals(
            (_span_proposal({"e": [{"s": start_line, "n": 0, "z": "inserted\n"}]}),),
            baselines={path: baseline},
            editable_ranges=ranges,
        )
        edit = manifest.edits[0]
        assert baseline.replace(edit.old_text, edit.new_text, 1) == expected

    hidden_gap = _span_proposal(
        {"e": [{"s": 5, "n": 0, "z": "MODEL_SECRET=do-not-copy\n"}]}
    )
    with pytest.raises(
        ValueError,
        match="first shown line through one past the last shown line",
    ) as error:
        _expand_span_proposals(
            (hidden_gap,),
            baselines={path: baseline},
            editable_ranges=ranges,
        )
    assert "MODEL_SECRET" not in str(error.value)


def test_compact_v4_scope_error_exposes_only_typed_bounded_parent_telemetry() -> None:
    """Carry useful coordinates without model text, source, or the raw path."""
    path = "src/private/TOKEN=path-secret.py"
    baseline = "shown-a\nshown-b\nhidden-c\nhidden-d\nshown-e\nshown-f\n"
    ranges = (((1, 3), (5, 7)),)
    proposal = comparison_module._decode_compact_span_proposal(
        '{"e":[{"s":4,"n":0,"z":"PASSWORD=hunter2\\n"}]}',
        focus_path=path,
    )

    with pytest.raises(ValueError) as captured:
        _expand_span_proposals(
            (proposal,),
            paths=(path,),
            baselines={path: baseline},
            editable_ranges=ranges,
        )

    feedback = comparison_module._safe_compact_scope_telemetry(captured.value)
    assert feedback == (
        f"path_sha256={hashlib.sha256(path.encode()).hexdigest()} "
        "received_s=4 received_n=0 "
        "sections=[1,3),[5,7) boundaries=[1,3],[5,7]"
    )
    assert all(
        secret not in feedback
        for secret in (path, "TOKEN", "path-secret", "PASSWORD", "hunter2", "shown-a")
    )
    assert len(feedback.encode("utf-8")) <= 256


def test_compact_v4_scope_telemetry_bounds_many_sections() -> None:
    """Truncate only trusted ranges while keeping received coordinates actionable."""
    path = "src/private/TOKEN=path-secret.py"
    ranges = ((1, 2), (4, 5), (7, 8), (10, 11), (13, 14))
    baseline = "".join(f"line-{index}\n" for index in range(1, 14))
    proposal = comparison_module._decode_compact_span_proposal(
        '{"e":[{"s":3,"n":0,"z":"PASSWORD=hunter2\\n"}]}',
        focus_path=path,
    )

    with pytest.raises(ValueError) as captured:
        _expand_span_proposals(
            (proposal,),
            paths=(path,),
            baselines={path: baseline},
            editable_ranges=(ranges,),
        )

    feedback = comparison_module._safe_compact_scope_telemetry(captured.value)
    assert "sections=[1,2),[4,5),[7,8),[10,11),+1" in feedback
    assert "boundaries=[1,2],[4,5],[7,8],[10,11],+1" in feedback
    assert all(secret not in feedback for secret in (path, "TOKEN", "PASSWORD", "hunter2"))
    assert len(feedback.encode("utf-8")) <= 256


def test_compact_v4_strict_decoder_redacts_live_deepseek_framing_failure() -> None:
    """Reject the exact 2,308-byte live failure without echoing model output."""
    sensitive = (
        "Reasoning TOKEN=do-not-publish before object\n"
        '{"e":[{"s":1,"n":1,"z":"PASSWORD=hunter2"}]}\n'
    )
    raw = sensitive + ("x" * (2308 - len(sensitive.encode("utf-8"))))
    assert len(raw.encode("utf-8")) == 2308

    with pytest.raises(
        ValueError,
        match=r"compact-v4 proposal is not one complete JSON object; output_bytes=2308",
    ) as error:
        comparison_module._decode_compact_span_proposal(
            raw,
            focus_path="src/general_ludd/example.py",
        )

    diagnostic = str(error.value)
    assert all(secret not in diagnostic for secret in ("TOKEN", "PASSWORD", "hunter2"))
    assert len(diagnostic.encode("utf-8")) < 160


def test_compact_v4_parent_compiles_insert_partial_delete_and_whole_delete() -> None:
    path = "src/general_ludd/example.py"
    baseline = "first\nsecond\nthird\n"
    cases = (
        ({"e": [{"s": 2, "n": 0, "z": "inserted\n"}]}, "first\ninserted\nsecond\nthird\n", "replace"),
        ({"e": [{"s": 2, "n": 1, "z": ""}]}, "first\nthird\n", "replace"),
        ({"e": [{"s": 1, "n": 3, "z": ""}]}, "", "delete"),
    )

    for raw, expected, operation in cases:
        manifest = _expand_span_proposals(
            (_span_proposal(raw),),
            baselines={path: baseline},
            editable_ranges=(((1, 4),),),
        )
        edit = manifest.edits[0]
        assert edit.operation == operation
        actual = "" if operation == "delete" else baseline.replace(
            edit.old_text, edit.new_text, 1
        )
        assert actual == expected


@pytest.mark.parametrize(
    ("baseline", "start_line", "old_line_count", "new_text", "expected"),
    [
        ("first\nsecond\nthird\n", 2, 1, "changed", "first\nchanged\nthird\n"),
        ("first\r\nsecond\r\nthird\r\n", 2, 1, "changed", "first\r\nchanged\r\nthird\r\n"),
        ("first\nsecond\n", 2, 0, "inserted", "first\ninserted\nsecond\n"),
        ("first\r\nsecond\r\n", 2, 0, "inserted", "first\r\ninserted\r\nsecond\r\n"),
        ("first\nsecond\nthird\n", 2, 1, "", "first\nthird\n"),
        ("first\nsecond\n", 2, 1, "changed", "first\nchanged\n"),
        ("first\nsecond", 2, 1, "changed\n", "first\nchanged"),
        ("first\n", 2, 0, "last", "first\nlast\n"),
        ("first", 2, 0, "last\n", "first\nlast"),
    ],
)
def test_compact_v4_parent_owns_whole_line_boundaries(
    baseline: str,
    start_line: int,
    old_line_count: int,
    new_text: str,
    expected: str,
) -> None:
    """Materialize logical line spans without delegating separator bytes to a model."""
    path = "src/general_ludd/example.py"
    line_count = len(baseline.splitlines())

    manifest = _expand_span_proposals(
        (
            _span_proposal(
                {"e": [{"s": start_line, "n": old_line_count, "z": new_text}]}
            ),
        ),
        baselines={path: baseline},
        editable_ranges=(((1, line_count + 1),),),
    )

    assert manifest.schema_version == 2
    assert manifest.edits == (
        comparison_module.ProposalEdit(
            operation="replace",
            path=path,
            old_text=baseline,
            new_text=expected,
        ),
    )


def test_compact_v4_parent_accepts_only_canonical_absent_file_create() -> None:
    path = "src/general_ludd/example.py"
    manifest = _expand_span_proposals(
        (_span_proposal({"e": [{"s": 1, "n": 0, "z": "created = True\n"}]}),),
        baselines={path: None},
        editable_ranges=((),),
    )
    assert manifest.edits[0].operation == "create"

    for raw in (
        {"e": [{"s": 2, "n": 0, "z": "created = True\n"}]},
        {"e": [{"s": 1, "n": 1, "z": "created = True\n"}]},
    ):
        with pytest.raises(ValueError, match="absent file create"):
            _expand_span_proposals(
                (_span_proposal(raw),),
                baselines={path: None},
                editable_ranges=((),),
            )

    with pytest.raises(ValueError, match=r"absent file.*editable baseline ranges"):
        _expand_span_proposals(
            (_span_proposal({"e": [{"s": 1, "n": 0, "z": "created = True\n"}]}),),
            baselines={path: None},
            editable_ranges=(((1, 2),),),
        )


def test_compact_v4_parent_materializes_empty_existing_snapshot_without_anchor() -> None:
    path = "src/general_ludd/example.py"
    manifest = _expand_span_proposals(
        (_span_proposal({"e": [{"s": 1, "n": 0, "z": "created = True\n"}]}),),
        baselines={path: ""},
        editable_ranges=((),),
    )

    assert manifest.schema_version == 2
    assert manifest.edits == (
        comparison_module.ProposalEdit(
            operation="replace",
            path=path,
            old_text="",
            new_text="created = True\n",
        ),
    )


def test_compact_v4_parent_preserves_two_ordered_edits_in_one_file() -> None:
    path = "src/general_ludd/example.py"
    baseline = "alpha = 1\nbetween = 0\nomega = 1\n"
    proposal = _span_proposal(
        {
            "e": [
                {"s": 1, "n": 1, "z": "alpha = 2\n"},
                {"s": 3, "n": 1, "z": "omega = 2\n"},
            ]
        }
    )

    manifest = _expand_span_proposals(
        (proposal,),
        baselines={path: baseline},
        editable_ranges=(((1, 4),),),
    )

    current = baseline
    for edit in manifest.edits:
        assert current.count(edit.old_text) == 1
        current = current.replace(edit.old_text, edit.new_text, 1)
    assert current == "alpha = 2\nbetween = 0\nomega = 2\n"


def test_compact_v4_multi_edit_coordinates_stay_bound_to_immutable_snapshot() -> None:
    path = "src/general_ludd/example.py"
    baseline = "alpha = 1\nbetween = 0\nomega = 1\n"
    proposal = _span_proposal(
        {
            "e": [
                {"s": 1, "n": 1, "z": "alpha = 2\ninserted = True\n"},
                {"s": 3, "n": 1, "z": "omega = 2\n"},
            ]
        }
    )

    manifest = _expand_span_proposals(
        (proposal,),
        baselines={path: baseline},
        editable_ranges=(((1, 4),),),
    )

    current = baseline
    for edit in manifest.edits:
        assert current.count(edit.old_text) == 1
        current = current.replace(edit.old_text, edit.new_text, 1)
    assert current == "alpha = 2\ninserted = True\nbetween = 0\nomega = 2\n"


@pytest.mark.parametrize(
    ("baseline", "replacement", "expected"),
    [
        (
            "first\r\nsecond\r\nthird",
            "changed\r\n",
            "first\r\nchanged\r\nthird",
        ),
        (
            "first\r\nsecond\r\n",
            "changed\r\n",
            "first\r\nchanged\r\n",
        ),
    ],
)
def test_compact_v4_parent_preserves_crlf_and_final_newline_state(
    baseline: str,
    replacement: str,
    expected: str,
) -> None:
    path = "src/general_ludd/example.py"
    manifest = _expand_span_proposals(
        (_span_proposal({"e": [{"s": 2, "n": 1, "z": replacement}]}),),
        baselines={path: baseline},
        editable_ranges=(((1, len(baseline.splitlines()) + 1),),),
    )

    edit = manifest.edits[0]
    assert baseline.replace(edit.old_text, edit.new_text, 1) == expected


@pytest.mark.parametrize(
    ("start_line", "new_text", "expected"),
    [
        (1, "before\n", "before\nfirst\nsecond\nthird\n"),
        (4, "after\n", "first\nsecond\nthird\nafter\n"),
    ],
)
def test_compact_v4_parent_accepts_shown_zero_width_edge_boundaries(
    start_line: int,
    new_text: str,
    expected: str,
) -> None:
    path = "src/general_ludd/example.py"
    baseline = "first\nsecond\nthird\n"
    manifest = _expand_span_proposals(
        (_span_proposal({"e": [{"s": start_line, "n": 0, "z": new_text}]}),),
        baselines={path: baseline},
        editable_ranges=(((1, 4),),),
    )

    edit = manifest.edits[0]
    assert baseline.replace(edit.old_text, edit.new_text, 1) == expected


def test_compact_v4_parent_materializes_repeated_snapshot_without_unique_anchor() -> None:
    path = "src/general_ludd/example.py"
    baseline = "same\n" * 30_000
    proposal = _span_proposal(
        {"e": [{"s": 15_000, "n": 1, "z": "changed\n"}]}
    )

    manifest = _expand_span_proposals(
        (proposal,),
        baselines={path: baseline},
        editable_ranges=(((1, 30_001),),),
    )

    assert manifest.schema_version == 2
    assert manifest.edits[0].old_text == baseline
    expected_lines = baseline.splitlines(keepends=True)
    expected_lines[14_999] = "changed\n"
    assert manifest.edits[0].new_text == "".join(expected_lines)


def test_compact_v4_parent_materializes_repeated_closers_and_blank_line_insertion() -> None:
    """Immutable coordinates disambiguate content that has no unique local anchor."""
    path = "src/general_ludd/example.py"
    baseline = "def first():\n    pass\n\n}\n\n}\n"
    proposal = _span_proposal(
        {
            "e": [
                {"s": 3, "n": 0, "z": "    return 1"},
                {"s": 6, "n": 1, "z": "# final closer"},
            ]
        }
    )

    manifest = _expand_span_proposals(
        (proposal,),
        baselines={path: baseline},
        editable_ranges=(((1, 7),),),
    )

    assert manifest.edits[0].old_text == baseline
    assert manifest.edits[0].new_text == (
        "def first():\n    pass\n    return 1\n\n}\n\n# final closer\n"
    )


def test_compact_gateway_rejects_failed_canary_without_task_decode_or_secret_leak(
    tmp_path: Path,
) -> None:
    model_path = tmp_path / "model.gguf"
    model_path.write_bytes(b"GGUF")
    calls = 0

    class FailedCanaryModel:
        def create_chat_completion(self, **_kwargs: object) -> dict[str, object]:
            nonlocal calls
            calls += 1
            return {
                "choices": [
                    {
                        "finish_reason": "length",
                        "message": {"content": '{"token=do-not-log":"secret"'},
                    }
                ],
                "usage": {
                    "prompt_tokens": 20,
                    "completion_tokens": 32,
                    "total_tokens": 52,
                },
            }

        def __call__(self, prompt: str, **kwargs: object) -> object:
            raise AssertionError("raw completion must not be used")

    gateway = LocalProposalGateway(
        model_path,
        model_factory=lambda **_kwargs: FailedCanaryModel(),
    )
    contract = ProposalContract(
        baseline_sha="a" * 40,
        task_id="S83.133",
        tests=("tests/unit/test_example.py",),
        make_commands=("make test-files TESTFILES=tests/unit/test_example.py",),
    )

    with pytest.raises(ValueError, match="structured-output canary") as error:
        gateway.propose("Repair the example.", contract=contract)

    assert calls == 1
    assert "finish=length" in str(error.value)
    assert "completion_tokens=32" in str(error.value)
    assert "do-not-log" not in str(error.value)


def test_compact_gateway_rejects_non_stop_even_when_json_looks_complete(
    tmp_path: Path,
) -> None:
    model_path = tmp_path / "model.gguf"
    model_path.write_bytes(b"GGUF")
    outputs: list[dict[str, object]] = [
        {
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {"content": '{"ok":true}'},
                }
            ]
        },
        {
            "choices": [
                {
                    "finish_reason": "length",
                    "message": {
                        "content": json.dumps(
                            {
                                "e": [
                                    {
                                        "a": "x = 0",
                                        "z": "x = 1",
                                    }
                                ]
                            }
                        )
                    },
                }
            ],
            "usage": {
                "prompt_tokens": 500,
                "completion_tokens": 1024,
                "total_tokens": 1524,
            },
        },
    ]

    class LengthModel:
        def create_chat_completion(self, **_kwargs: object) -> dict[str, object]:
            return outputs.pop(0)

        def __call__(self, prompt: str, **kwargs: object) -> object:
            raise AssertionError("raw completion must not be used")

    gateway = LocalProposalGateway(
        model_path,
        model_factory=lambda **_kwargs: LengthModel(),
    )
    contract = ProposalContract(
        baseline_sha="a" * 40,
        task_id="S83.133",
        tests=("tests/unit/test_example.py",),
        make_commands=("make test-files TESTFILES=tests/unit/test_example.py",),
    )

    with pytest.raises(ValueError, match="token budget") as error:
        gateway.propose(
            comparison_module.bind_compact_focus_path(
                "Repair the example.",
                "src/general_ludd/example.py",
            ),
            contract=contract,
        )

    assert "budget=1024" in str(error.value)
    assert "completion_tokens=1024" in str(error.value)


def test_local_gateway_rejects_token_budget_truncation_even_with_valid_json(
    tmp_path: Path,
) -> None:
    model_path = tmp_path / "model.gguf"
    model_path.write_bytes(b"GGUF")

    class TruncatedChatModel:
        def create_chat_completion(self, **_kwargs: object) -> dict[str, object]:
            return {
                "choices": [
                    {
                        "finish_reason": "length",
                        "message": {"content": json.dumps({
                            "schema_version": 1,
                            "baseline_sha": "a" * 40,
                            "task_id": "S83.133",
                            "edits": [{
                                "operation": "replace",
                                "path": "src/general_ludd/example.py",
                                "old_text": "x = 0",
                                "new_text": "x = 1",
                            }],
                            "tests": ["tests/unit/test_example.py"],
                            "make_commands": [
                                "make test-files TESTFILES=tests/unit/test_example.py"
                            ],
                            "commit_message": "fix: apparently complete",
                        })},
                    }
                ]
            }

        def __call__(self, prompt: str, **kwargs: object) -> object:
            raise AssertionError("raw completion must not be used")

    gateway = LocalProposalGateway(
        model_path,
        model_factory=lambda **_kwargs: TruncatedChatModel(),
    )

    with pytest.raises(ValueError, match="token budget"):
        gateway.propose("Repair the example.")


def test_local_gateway_length_stop_reports_secret_safe_finish_and_usage(
    tmp_path: Path,
) -> None:
    model_path = tmp_path / "model.gguf"
    model_path.write_bytes(b"GGUF")

    class ExhaustedChatModel:
        def create_chat_completion(self, **_kwargs: object) -> dict[str, object]:
            return {
                "choices": [
                    {
                        "finish_reason": "length",
                        "message": {
                            "content": '{"token=do-not-log-this":"still truncated"'
                        },
                    }
                ],
                "usage": {
                    "prompt_tokens": 640,
                    "completion_tokens": 4096,
                    "total_tokens": 4736,
                },
            }

        def __call__(self, prompt: str, **kwargs: object) -> object:
            raise AssertionError("raw completion must not be used")

    gateway = LocalProposalGateway(
        model_path,
        model_factory=lambda **_kwargs: ExhaustedChatModel(),
    )

    with pytest.raises(ValueError, match="token budget") as error:
        gateway.propose("Repair the example.")

    diagnostic = str(error.value)
    assert "finish=length" in diagnostic
    assert "prompt_tokens=640" in diagnostic
    assert "completion_tokens=4096" in diagnostic
    assert "total_tokens=4736" in diagnostic
    assert "do-not-log-this" not in diagnostic
    assert len(diagnostic.encode("utf-8")) <= 300


def test_completion_telemetry_hashes_output_before_strict_decode(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Make invalid-but-complete model output auditable without revealing content."""
    secret_output = '{"e":[{"s":1,"n":0,"z":"SECRET_TOKEN"}]}'
    expected_sha256 = hashlib.sha256(secret_output.encode("utf-8")).hexdigest()

    decoded = comparison_module._completion_text(
        {
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {"content": secret_output},
                }
            ],
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 12,
                "total_tokens": 22,
            },
        },
        phase="proposal",
        budget=4096,
        require_stop=True,
    )

    telemetry = capsys.readouterr().out
    assert decoded == secret_output
    assert f"output_bytes={len(secret_output.encode('utf-8'))}" in telemetry
    assert f"output_sha256={expected_sha256}" in telemetry
    assert "SECRET_TOKEN" not in telemetry


def test_local_gateway_reports_bounded_incomplete_json_output(tmp_path: Path) -> None:
    model_path = tmp_path / "model.gguf"
    model_path.write_bytes(b"GGUF")
    incomplete = '{"schema_version":1,"edits":[' + ("x" * 5000)

    class FakeChatModel:
        def create_chat_completion(self, **_kwargs: object) -> dict[str, object]:
            return {"choices": [{"message": {"content": incomplete}}]}

        def __call__(self, prompt: str, **kwargs: object) -> object:
            raise AssertionError("raw completion must not be used")

    gateway = LocalProposalGateway(
        model_path,
        model_factory=lambda **_kwargs: FakeChatModel(),
    )

    with pytest.raises(ValueError, match="incomplete JSON") as error:
        gateway.propose("Repair exactly.")

    assert "output_bytes=" in str(error.value)
    assert "schema_version" not in str(error.value)
    assert len(str(error.value).encode("utf-8")) <= 300


def test_self_improve_runner_uses_local_model_and_make_only_git_workflow() -> None:
    source = Path("scripts/run_self_improve_e2e.py").read_text(encoding="utf-8")
    assert "--local-model-path" in source
    assert "--baseline-ref" in source
    assert "--reference-ref" in source
    assert '["git"' not in source
    assert "agent-worktree-base" in source
    assert "patch-equivalence" in source


def test_make_contract_forwards_local_comparison_inputs() -> None:
    makefile = Path("Makefile").read_text(encoding="utf-8")
    contract = Path("config/make_target_contract.json").read_text(encoding="utf-8")
    for token in (
        "SELF_IMPROVE_CONTRACT_FILE",
        "SELF_IMPROVE_MODEL_PATH",
        "SELF_IMPROVE_BASELINE_REF",
        "SELF_IMPROVE_REFERENCE_REF",
        "SELF_IMPROVE_TASK_FILE",
    ):
        assert token in makefile
        assert token in contract
    assert '--contract-file "$(SELF_IMPROVE_CONTRACT_FILE)"' in makefile


def test_gateway_fails_closed_for_each_malformed_model_response(tmp_path: Path) -> None:
    model = tmp_path / "model.gguf"
    model.write_bytes(b"gguf")

    class FakeModel:
        def __init__(self, output: object) -> None:
            self.output = output

        def __call__(
            self,
            prompt: str,
            *,
            max_tokens: int,
            temperature: float,
            echo: bool,
        ) -> object:
            del prompt, max_tokens, temperature, echo
            return self.output

    class FakeFactory:
        def __init__(self, output: object) -> None:
            self.output = output

        def __call__(
            self,
            *,
            model_path: str,
            n_ctx: int,
            verbose: bool,
            n_gpu_layers: int = 0,
        ) -> FakeModel:
            del model_path, n_ctx, verbose, n_gpu_layers
            return FakeModel(self.output)

    malformed = [
        [],
        {},
        {"choices": []},
        {"choices": ["not-a-mapping"]},
        {"choices": [{}]},
        {"choices": [{"message": {}}]},
    ]
    expected = [
        "non-object",
        "no choices",
        "no choices",
        "no choices",
        "no proposal text",
        "no proposal text",
    ]
    for output, match in zip(malformed, expected, strict=True):
        gateway = LocalProposalGateway(
            model,
            model_factory=FakeFactory(output),
        )
        with pytest.raises(ValueError, match=match):
            gateway.propose("repair")


def test_json_extractor_accepts_fenced_json_and_rejects_incomplete_tail() -> None:
    raw = comparison_module._extract_json_object('''```json\n{"ok":true}\n```''')
    assert raw == '{"ok":true}'
    with pytest.raises(ValueError, match="incomplete JSON"):
        comparison_module._extract_json_object('prefix {"ok": true')



@pytest.mark.parametrize(
    ("offload_probe_result", "expected_gpu_layers"),
    [(True, -1), (False, 0), (OSError("probe failed"), 0)],
)
def test_optional_llama_runtime_gates_offload_through_native_support_probe(
    monkeypatch: pytest.MonkeyPatch,
    offload_probe_result: bool | OSError,
    expected_gpu_layers: int,
) -> None:
    fake_module = ModuleType("llama_cpp")
    imports: list[str] = []

    class FakeModel:
        def __init__(
            self,
            *,
            model_path: str,
            n_ctx: int,
            verbose: bool,
            n_gpu_layers: int,
        ) -> None:
            self.settings = model_path, n_ctx, verbose, n_gpu_layers

    vars(fake_module)["Llama"] = FakeModel

    def probe_gpu_offload() -> bool:
        if isinstance(offload_probe_result, OSError):
            raise offload_probe_result
        return offload_probe_result

    vars(fake_module)["llama_supports_gpu_offload"] = probe_gpu_offload

    def import_runtime(name: str) -> ModuleType:
        imports.append(name)
        return fake_module

    monkeypatch.setattr(importlib, "import_module", import_runtime)
    model = comparison_module._default_model_factory(
        model_path="/tmp/gludd-model.gguf",
        n_ctx=0,
        verbose=False,
    )
    assert isinstance(model, FakeModel)
    assert model.settings == (
        "/tmp/gludd-model.gguf",
        0,
        False,
        expected_gpu_layers,
    )
    assert imports == ["llama_cpp"]


def test_locked_llama_runtime_compiles_canonical_schema_with_public_grammar_api(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Call the 0.3.24 public LlamaGrammar seam without a custom converter."""
    calls: list[tuple[str, bool]] = []
    grammar = object()
    runtime = cast(Any, ModuleType("llama_cpp"))

    class GrammarType:
        @staticmethod
        def from_json_schema(schema: str, *, verbose: bool = True) -> object:
            calls.append((schema, verbose))
            return grammar

    runtime.LlamaGrammar = GrammarType
    monkeypatch.setattr(comparison_module, "_load_llama_cpp_runtime", lambda: runtime)
    schema: dict[str, object] = {"required": ["e"], "type": "object"}

    assert comparison_module._default_json_schema_grammar(schema) is grammar
    assert calls == [('{"required":["e"],"type":"object"}', False)]


def test_locked_llama_grammar_construction_failure_is_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fail closed without copying a native converter diagnostic."""
    runtime = cast(Any, ModuleType("llama_cpp"))

    class FailedGrammarType:
        @staticmethod
        def from_json_schema(_schema: str, *, verbose: bool = True) -> object:
            del verbose
            raise ValueError("TOKEN=do-not-publish native schema detail")

    runtime.LlamaGrammar = FailedGrammarType
    monkeypatch.setattr(comparison_module, "_load_llama_cpp_runtime", lambda: runtime)

    with pytest.raises(
        RuntimeError,
        match="JSON-schema grammar construction failed",
    ) as error:
        comparison_module._default_json_schema_grammar({"type": "object"})

    assert "TOKEN" not in str(error.value)


def test_gateway_rejects_missing_injected_grammar_before_decode(tmp_path: Path) -> None:
    """Treat an injected grammar factory returning no object as a hard failure."""
    model_path = tmp_path / "model.gguf"
    model_path.write_bytes(b"GGUF")
    decode_calls = 0

    class Model:
        def __call__(self, prompt: str, **kwargs: object) -> object:
            del prompt, kwargs
            raise AssertionError("raw completion must not be used")

        def create_chat_completion(self, **_kwargs: object) -> dict[str, object]:
            nonlocal decode_calls
            decode_calls += 1
            return {"choices": []}

    def missing_grammar(_schema: dict[str, object]) -> object:
        return cast(object, None)

    gateway = LocalProposalGateway(
        model_path,
        model_factory=lambda **_kwargs: Model(),
        grammar_factory=missing_grammar,
    )
    contract = replace(
        _contract(),
        proposal_protocol=comparison_module.COMPACT_PROPOSAL_PROTOCOL_V4,
    )

    with pytest.raises(RuntimeError, match="returned no grammar"):
        gateway.propose(
            comparison_module.bind_compact_focus_path(
                "bounded",
                "src/general_ludd/example.py",
            ),
            contract=contract,
        )

    assert decode_calls == 0
