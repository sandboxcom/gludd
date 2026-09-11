"""Provider-neutral transport codec for managed remote proposal candidates."""

from __future__ import annotations

import copy
import hashlib
import json
from functools import partial

from general_ludd.self_improve.codex_comparison import (
    COMPACT_PROPOSAL_PROTOCOL_V4,
    COMPACT_V4_SYNTAX_REPAIR_SAMPLING_PROFILE_ID,
    CodexReference,
    CompactSpanProposal,
    CompactSpanScopeError,
    ProposalContract,
    decode_compact_span_batch,
    decode_proposal_batch,
    encode_prompt_batch,
    expand_compact_span_proposals,
    merge_proposal_manifests,
    proposal_batch_json_schema,
    proposal_batch_response_instruction,
)
from general_ludd.self_improve.managed_candidate_routing import (
    CandidateProposalDecodeFailure,
    CandidateProposalDecodeRejected,
    ManagedCandidateProposalCodec,
)
from general_ludd.self_improve.managed_runner import (
    GeneratedProposal,
    PromptPlan,
    TaskSpec,
)

MANAGED_PROPOSAL_BATCH_PROTOCOL = "self-improve-managed-proposal-batch-v1"
MANAGED_PROPOSAL_RESPONSE_INSTRUCTION = (
    proposal_batch_response_instruction(COMPACT_PROPOSAL_PROTOCOL_V4)
    + "The proposals member is an object keyed by decimal prompt ordinal, starting "
    "at 0; emit every key exactly once and bind each value to that prompt's path."
)


def _routing_contract_digest(*parts: str) -> str:
    payload = json.dumps(
        {"parts": parts, "protocol": "gludd-managed-remote-proposal-codec-v1"},
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    return hashlib.sha256(payload).hexdigest()


def _canonical_schema_json(schema: dict[str, object]) -> str:
    return json.dumps(
        schema,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def _managed_batch_items(
    text: str,
    *,
    expected_protocol_digest: str,
    expected_count: int,
    compact: bool,
) -> tuple[dict[str, object], ...]:
    try:
        value = json.loads(text)
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise CandidateProposalDecodeRejected(
            CandidateProposalDecodeFailure.JSON_CONTRACT
        ) from None
    if not isinstance(value, dict) or set(value) != {
        "protocol",
        "protocol_digest",
        "proposals",
    }:
        raise CandidateProposalDecodeRejected(
            CandidateProposalDecodeFailure.ROOT_CONTRACT
        )
    if (
        value["protocol"] != MANAGED_PROPOSAL_BATCH_PROTOCOL
        or value["protocol_digest"] != expected_protocol_digest
    ):
        raise CandidateProposalDecodeRejected(
            CandidateProposalDecodeFailure.PROTOCOL_IDENTITY
        )
    proposals = value["proposals"]
    expected_keys = tuple(str(ordinal) for ordinal in range(expected_count))
    if not isinstance(proposals, dict) or set(proposals) != set(expected_keys):
        raise CandidateProposalDecodeRejected(
            CandidateProposalDecodeFailure.PROPOSAL_COUNT
        )
    ordered = tuple(proposals[key] for key in expected_keys)
    if compact and any(
        not isinstance(proposal, dict) or set(proposal) != {"focus_path", "e"}
        for proposal in ordered
    ):
        raise CandidateProposalDecodeRejected(
            CandidateProposalDecodeFailure.PROPOSAL_SHAPE
        )
    if any(not isinstance(proposal, dict) for proposal in ordered):
        raise CandidateProposalDecodeRejected(
            CandidateProposalDecodeFailure.PROPOSAL_SHAPE
        )
    return tuple(proposal for proposal in ordered if isinstance(proposal, dict))


def _managed_response_instruction(proposal_protocol: str) -> str:
    """Describe the portable fixed-property response shape to every model."""
    return (
        proposal_batch_response_instruction(proposal_protocol)
        + "The proposals member is an object keyed by decimal prompt ordinal, "
        "starting at 0; emit every key exactly once and bind each value to that "
        "prompt's path."
    )


def _managed_response_schema(
    *,
    proposal_protocol: str,
    protocol_digest: str,
    expected_count: int,
    focus_paths: tuple[str, ...] = (),
    editable_ranges: tuple[tuple[tuple[int, int], ...], ...] = (),
) -> dict[str, object]:
    """Use fixed object properties instead of non-portable array uniqueness."""
    schema = proposal_batch_json_schema(
        proposal_protocol=proposal_protocol,
        protocol_digest=protocol_digest,
        expected_count=expected_count,
        focus_paths=focus_paths,
        editable_ranges=editable_ranges,
    )
    properties = schema["properties"]
    if not isinstance(properties, dict):
        raise ValueError("proposal schema root properties are invalid")
    protocol = properties["protocol"]
    proposals = properties["proposals"]
    if not isinstance(protocol, dict) or not isinstance(proposals, dict):
        raise ValueError("proposal schema transport properties are invalid")
    item_schema = proposals.get("items")
    if not isinstance(item_schema, dict):
        raise ValueError("proposal schema item is invalid")
    ordinal_properties: dict[str, object] = {}
    ordinal_keys = tuple(str(ordinal) for ordinal in range(expected_count))
    for ordinal, key in enumerate(ordinal_keys):
        item = copy.deepcopy(item_schema)
        if focus_paths:
            item_properties = item.get("properties")
            if not isinstance(item_properties, dict):
                raise ValueError("compact proposal schema properties are invalid")
            item_properties["focus_path"] = {
                "const": focus_paths[ordinal],
                "type": "string",
            }
        ordinal_properties[key] = item
    protocol["const"] = MANAGED_PROPOSAL_BATCH_PROTOCOL
    properties["proposals"] = {
        "type": "object",
        "additionalProperties": False,
        "required": list(ordinal_keys),
        "properties": ordinal_properties,
    }
    return schema


def _validation_failure(error: Exception) -> CandidateProposalDecodeFailure:
    if isinstance(error, CompactSpanScopeError):
        return CandidateProposalDecodeFailure.PROPOSAL_SCOPE
    detail = str(error)
    if "must change content" in detail:
        return CandidateProposalDecodeFailure.EDIT_NO_CHANGE
    if "distinct start coordinates" in detail or "must not overlap" in detail:
        return CandidateProposalDecodeFailure.EDIT_OVERLAP
    if "lines exceed" in detail or "changed lines" in detail:
        return CandidateProposalDecodeFailure.EDIT_LINE_BUDGET
    if "text exceeds" in detail or "content exceeds" in detail:
        return CandidateProposalDecodeFailure.EDIT_CONTENT_BUDGET
    if any(
        marker in detail
        for marker in (
            "scope",
            "editable",
            "outside trusted",
            "explicitly shown",
            "exact focus paths",
            "immutable path set",
            "start line must be positive",
        )
    ):
        return CandidateProposalDecodeFailure.PROPOSAL_SCOPE
    if any(
        marker in detail
        for marker in (
            "proposal edits must contain",
            "each compact edit",
            "coordinates must be integers",
            "new text must be a string",
            "repeated one immutable path",
        )
    ):
        return CandidateProposalDecodeFailure.PROPOSAL_SHAPE
    return CandidateProposalDecodeFailure.PROPOSAL_VALIDATION


def _decode_remote_legacy_plan(
    text: str,
    *,
    plan: PromptPlan,
    task: TaskSpec,
    reference: CodexReference,
    required_tests: tuple[str, ...],
) -> GeneratedProposal:
    proposals = _managed_batch_items(
        text,
        expected_protocol_digest=plan.protocol_digest,
        expected_count=len(plan.shards),
        compact=False,
    )
    try:
        legacy_response = json.dumps(
            {
                "protocol": "self-improve-local-proposal-batch-v1",
                "protocol_digest": plan.protocol_digest,
                "proposals": proposals,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        decoded = decode_proposal_batch(
            legacy_response,
            expected_protocol_digest=plan.protocol_digest,
            expected_count=len(plan.shards),
        )
        merged = merge_proposal_manifests(
            decoded,
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
    except (TypeError, ValueError, UnicodeError) as error:
        raise CandidateProposalDecodeRejected(_validation_failure(error)) from None


def _decode_remote_compact_plan(
    text: str,
    *,
    plan: PromptPlan,
    contract: ProposalContract,
) -> GeneratedProposal:
    path_groups = tuple(shard.focus_paths for shard in plan.shards)
    paths = tuple(group[0] for group in path_groups)
    proposals = _managed_batch_items(
        text,
        expected_protocol_digest=plan.protocol_digest,
        expected_count=len(plan.shards),
        compact=True,
    )
    try:
        legacy_response = json.dumps(
            {
                "protocol": "self-improve-local-proposal-batch-v2",
                "protocol_digest": plan.protocol_digest,
                "proposals": proposals,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        spans = decode_compact_span_batch(
            legacy_response,
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
    except (KeyError, TypeError, ValueError, UnicodeError) as error:
        raise CandidateProposalDecodeRejected(_validation_failure(error)) from None


def build_managed_remote_proposal_codec(
    prompt: PromptPlan | str,
    task: TaskSpec,
    reference: CodexReference,
    *,
    required_tests: tuple[str, ...] = (),
) -> ManagedCandidateProposalCodec[GeneratedProposal] | None:
    """Prepare the exact local-equivalent remote transport and decoder."""
    if isinstance(prompt, str):
        # A raw prompt has no immutable request contract or response protocol.
        # Never create a managed worker that cannot consume the same complete
        # envelope as the owned local path.
        return None
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
        response_schema = _managed_response_schema(
            proposal_protocol=prompt.proposal_protocol,
            protocol_digest=prompt.protocol_digest,
            expected_count=len(prompt.shards),
            focus_paths=tuple(shard.focus_paths[0] for shard in prompt.shards),
            editable_ranges=tuple(shard.editable_ranges for shard in prompt.shards),
        )
    else:
        decoder = partial(
            _decode_remote_legacy_plan,
            plan=prompt,
            task=task,
            reference=reference,
            required_tests=required_tests,
        )
        response_schema = _managed_response_schema(
            proposal_protocol=prompt.proposal_protocol,
            protocol_digest=prompt.protocol_digest,
            expected_count=len(prompt.shards),
        )
    return ManagedCandidateProposalCodec(
        request_text=request,
        decoder=decoder,
        protocol_digest=_routing_contract_digest(prompt.proposal_protocol),
        sampling_digest=_routing_contract_digest(prompt.sampling_profile),
        request_contract_json=contract.to_json(),
        response_instruction=_managed_response_instruction(prompt.proposal_protocol),
        response_schema_json=_canonical_schema_json(response_schema),
    )


__all__ = (
    "MANAGED_PROPOSAL_BATCH_PROTOCOL",
    "MANAGED_PROPOSAL_RESPONSE_INSTRUCTION",
    "build_managed_remote_proposal_codec",
)
