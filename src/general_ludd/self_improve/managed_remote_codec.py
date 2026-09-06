"""Provider-neutral transport codec for managed remote proposal candidates."""

from __future__ import annotations

import hashlib
import json
from functools import partial

from general_ludd.self_improve.codex_comparison import (
    COMPACT_PROPOSAL_PROTOCOL_V4,
    COMPACT_V4_SYNTAX_REPAIR_SAMPLING_PROFILE_ID,
    DEFAULT_PROPOSAL_SAMPLING_PROFILE_ID,
    CodexReference,
    CompactSpanProposal,
    ProposalContract,
    ProposalManifest,
    decode_compact_span_batch,
    decode_proposal_batch,
    encode_prompt_batch,
    expand_compact_span_proposals,
    merge_proposal_manifests,
)
from general_ludd.self_improve.managed_candidate_routing import (
    CandidateProposalDecodeRejected,
    ManagedCandidateProposalCodec,
)
from general_ludd.self_improve.managed_runner import (
    GeneratedProposal,
    PromptPlan,
    TaskSpec,
)


def _routing_contract_digest(*parts: str) -> str:
    payload = json.dumps(
        {"parts": parts, "protocol": "gludd-managed-remote-proposal-codec-v1"},
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    return hashlib.sha256(payload).hexdigest()


def _decode_remote_manifest(text: str) -> GeneratedProposal:
    try:
        return GeneratedProposal(ProposalManifest.from_json(text))
    except (TypeError, ValueError, UnicodeError):
        raise CandidateProposalDecodeRejected from None


def _decode_remote_legacy_plan(
    text: str,
    *,
    plan: PromptPlan,
    task: TaskSpec,
    reference: CodexReference,
    required_tests: tuple[str, ...],
) -> GeneratedProposal:
    try:
        proposals = decode_proposal_batch(
            text,
            expected_protocol_digest=plan.protocol_digest,
            expected_count=len(plan.shards),
        )
        merged = merge_proposal_manifests(
            proposals,
            expected_path_groups=tuple(shard.focus_paths for shard in plan.shards),
            expected_baseline_sha=reference.baseline_sha,
            expected_task_id=task.task_id,
            expected_tests=required_tests,
            expected_make_commands=task.canonical_make_commands,
            expected_baseline_files=(
                dict(plan.baseline_files) if plan.baseline_files else None
            ),
        )
        return GeneratedProposal(merged)
    except (TypeError, ValueError, UnicodeError):
        raise CandidateProposalDecodeRejected from None


def _decode_remote_compact_plan(
    text: str,
    *,
    plan: PromptPlan,
    contract: ProposalContract,
) -> GeneratedProposal:
    path_groups = tuple(shard.focus_paths for shard in plan.shards)
    paths = tuple(group[0] for group in path_groups)
    try:
        spans = decode_compact_span_batch(
            text,
            expected_protocol_digest=plan.protocol_digest,
            expected_count=len(plan.shards),
        )
        by_path: dict[str, CompactSpanProposal] = {}
        for span in spans:
            if span.focus_path in by_path:
                raise ValueError("compact response repeated one immutable path")
            by_path[span.focus_path] = span
        if set(by_path) != set(paths):
            raise ValueError("compact response did not cover its immutable path set")
        ordered = tuple(by_path[path] for path in paths)
        proposal = expand_compact_span_proposals(
            ordered,
            contract=contract,
            expected_path_groups=path_groups,
            expected_baseline_files=dict(plan.baseline_files),
            expected_editable_ranges=tuple(
                shard.editable_ranges for shard in plan.shards
            ),
        )
        return GeneratedProposal(proposal, ordered)
    except (KeyError, TypeError, ValueError, UnicodeError):
        raise CandidateProposalDecodeRejected from None


def build_managed_remote_proposal_codec(
    prompt: PromptPlan | str,
    task: TaskSpec,
    reference: CodexReference,
    *,
    required_tests: tuple[str, ...] = (),
) -> ManagedCandidateProposalCodec[GeneratedProposal] | None:
    """Prepare the exact local-equivalent remote transport and decoder."""
    if isinstance(prompt, str):
        return ManagedCandidateProposalCodec(
            request_text=prompt,
            decoder=_decode_remote_manifest,
            protocol_digest=_routing_contract_digest("manifest-v1"),
            sampling_digest=_routing_contract_digest(
                DEFAULT_PROPOSAL_SAMPLING_PROFILE_ID
            ),
        )
    if (
        prompt.proposal_protocol == COMPACT_PROPOSAL_PROTOCOL_V4
        and prompt.sampling_profile
        == COMPACT_V4_SYNTAX_REPAIR_SAMPLING_PROFILE_ID
    ):
        return None
    request = encode_prompt_batch(
        tuple(shard.prompt for shard in prompt.shards),
        protocol_digest=prompt.protocol_digest,
    )
    contract = ProposalContract.for_request(
        request=request,
        baseline_sha=reference.baseline_sha,
        task_id=task.task_id,
        tests=required_tests,
        make_commands=task.canonical_make_commands,
        proposal_protocol=prompt.proposal_protocol,
        sampling_profile=prompt.sampling_profile,
        sampling_candidate_index=0,
    )
    if prompt.proposal_protocol == COMPACT_PROPOSAL_PROTOCOL_V4:
        if not prompt.baseline_files or any(
            len(shard.focus_paths) != 1 for shard in prompt.shards
        ):
            raise ValueError("compact-v4 remote codec requires exact baseline shards")
        decoder = partial(
            _decode_remote_compact_plan,
            plan=prompt,
            contract=contract,
        )
    else:
        decoder = partial(
            _decode_remote_legacy_plan,
            plan=prompt,
            task=task,
            reference=reference,
            required_tests=required_tests,
        )
    return ManagedCandidateProposalCodec(
        request_text=request,
        decoder=decoder,
        protocol_digest=_routing_contract_digest(prompt.proposal_protocol),
        sampling_digest=_routing_contract_digest(prompt.sampling_profile),
    )


__all__ = ("build_managed_remote_proposal_codec",)
