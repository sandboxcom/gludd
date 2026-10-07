"""Local proposal decoding and compact-v4 repair orchestration."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from general_ludd.self_improve.codex_comparison import (
    COMPACT_PROPOSAL_PROTOCOL_V4,
    COMPACT_V4_REPAIR_CANDIDATE_LIMIT,
    COMPACT_V4_SYNTAX_REPAIR_SAMPLING_PROFILE_ID,
    CodexReference,
    CompactSpanProposal,
    ProposalContract,
    ProposalManifest,
    bind_compact_focus_path,
    compact_v4_repair_shard_state_digest,
    decode_compact_span_batch,
    decode_proposal_batch,
    encode_prompt_batch,
    expand_compact_span_proposals,
    merge_proposal_manifests,
)
from general_ludd.self_improve.evaluator import (
    _repair_candidate_syntax_diagnosis,
    _syntax_diagnosis_fields,
    _syntax_failure_class,
)
from general_ludd.self_improve.local_worker_request import (
    _ObservableRunner,
)
from general_ludd.self_improve.managed_candidate_routing import (
    ManagedCandidateProposalCodec,
    ManagedCandidateProposalEnvelope,
)
from general_ludd.self_improve.managed_runner import (
    PromptPlan,
    PromptShard,
    TaskSpec,
    build_syntax_repair_prompt_plan,
)
from general_ludd.self_improve.managed_runner_contracts import GeneratedProposal


class _LocalProposalRequest(Protocol):
    """Parent-owned local worker request boundary."""

    def __call__(
        self,
        runner: _ObservableRunner,
        model_path: Path,
        request: str,
        *,
        contract: ProposalContract | None = None,
        envelope: ManagedCandidateProposalEnvelope | None = None,
        timeout_seconds: float = 300.0,
    ) -> str:
        """Return one bounded worker response."""


@dataclass(frozen=True)
class ProposalRuntimeHooks:
    """Facade-owned callbacks required by local proposal orchestration."""

    proposal_request: _LocalProposalRequest
    progress: Callable[[str], None]
    proposal_python_syntax_diagnostics: Callable[
        [ProposalManifest], dict[str, str]
    ]
    proposal_python_syntax_preflight: Callable[[ProposalManifest], str | None]
    render_selected_lines: Callable[[list[str], set[int]], str]
    required_prompt_tests: Callable[[TaskSpec, CodexReference], tuple[str, ...]]
    prompt_context_lines: int


def _one_shard_prompt_plan(plan: PromptPlan, shard: PromptShard) -> PromptPlan:
    """Select one immutable shard without widening its baseline or edit scope."""
    if len(shard.focus_paths) != 1:
        raise ValueError("compact-v4 repair shard must bind exactly one focus path")
    path = shard.focus_paths[0]
    baseline_by_path = dict(plan.baseline_files)
    if path not in baseline_by_path:
        raise ValueError("compact-v4 repair shard is absent from the trusted baseline")
    baseline = baseline_by_path[path]
    return PromptPlan(
        shards=(shard,),
        source_bytes=len(baseline.encode("utf-8")) if baseline is not None else 0,
        protocol_digest=plan.protocol_digest,
        baseline_files=((path, baseline),),
        proposal_protocol=plan.proposal_protocol,
        sampling_profile=plan.sampling_profile,
    )


def _combine_shard_prompt_plans(
    plans: tuple[PromptPlan, ...],
    *,
    protocol_digest: str,
) -> PromptPlan:
    """Combine disjoint single-shard plans without changing trusted snapshots."""
    if not plans:
        raise ValueError("compact-v4 repair requires at least one failing shard")
    return PromptPlan(
        shards=tuple(shard for item in plans for shard in item.shards),
        source_bytes=sum(item.source_bytes for item in plans),
        protocol_digest=protocol_digest,
        baseline_files=tuple(
            baseline for item in plans for baseline in item.baseline_files
        ),
        proposal_protocol=COMPACT_PROPOSAL_PROTOCOL_V4,
        sampling_profile=COMPACT_V4_SYNTAX_REPAIR_SAMPLING_PROFILE_ID,
    )


def _report_repair_shard_state(
    relative_path: str,
    *,
    candidate: str,
    state: str,
    hooks: ProposalRuntimeHooks,
    diagnostic: str | None = None,
    target_span: tuple[int, int] | None = None,
) -> None:
    """Emit one bounded source-free state after binding diagnosis to its shard."""
    if state not in {
        "frozen",
        "preflight_rejected",
        "proposal_rejected",
        "span_targeted",
        "syntax_rejected",
    }:
        raise RuntimeError("compact-v4 repair shard state is unsupported")
    fields = _syntax_diagnosis_fields(diagnostic)
    path_sha256 = hashlib.sha256(relative_path.encode("utf-8")).hexdigest()
    if diagnostic is not None and fields["path_sha256"] != path_sha256:
        raise RuntimeError("repair syntax diagnosis does not match its owning shard")
    if (state in {"preflight_rejected", "span_targeted", "syntax_rejected"}) != (
        diagnostic is not None
    ):
        raise RuntimeError("compact-v4 repair shard state and diagnosis disagree")
    if (state == "span_targeted") != (target_span is not None):
        raise RuntimeError("compact-v4 repair target telemetry is inconsistent")
    target_detail = ""
    if target_span is not None:
        start_line, old_line_count = target_span
        if (
            isinstance(start_line, bool)
            or not isinstance(start_line, int)
            or isinstance(old_line_count, bool)
            or not isinstance(old_line_count, int)
            or start_line < 1
            or old_line_count < 0
        ):
            raise RuntimeError("compact-v4 repair target telemetry is invalid")
        target_detail = f" target_s={start_line} target_n={old_line_count}"
    category = fields["category"] if diagnostic is not None else "none"
    event = (
        "SELF_IMPROVE_REPAIR_SHARD_STATE "
        f"candidate={candidate} path_sha256={path_sha256} state={state} "
        f"category={category} line={fields['line']} column={fields['column']}"
        f"{target_detail}"
    )
    if len(event.encode("ascii")) > 256:
        raise RuntimeError("compact-v4 repair shard event exceeded 256 bytes")
    hooks.progress(event)


def _repair_preflight_state(diagnostic: str | None) -> str:
    """Classify a trusted per-file parser result without exposing its source."""
    if diagnostic is None:
        return "frozen"
    return (
        "syntax_rejected"
        if _syntax_failure_class(diagnostic) == "python_syntax"
        else "preflight_rejected"
    )


def _compact_v4_syntax_owning_span_index(
    baseline: str | None,
    proposal: CompactSpanProposal,
    diagnostic: str,
) -> int | None:
    """Return the unique model-authored output span containing one parser line."""
    fields = _syntax_diagnosis_fields(diagnostic)
    expected_path_sha256 = hashlib.sha256(
        proposal.focus_path.encode("utf-8")
    ).hexdigest()
    if fields["path_sha256"] != expected_path_sha256:
        raise RuntimeError("repair syntax diagnosis does not match its owning shard")
    if fields["category"] != "python_syntax" or not isinstance(fields["line"], int):
        return None
    parser_line = fields["line"]
    baseline_lines = [] if baseline is None else baseline.splitlines(keepends=True)
    baseline_cursor = 0
    output_lines = 0
    owners: list[int] = []
    for index, span in enumerate(proposal.edits):
        start = span.start_line - 1
        if start < baseline_cursor or start > len(baseline_lines):
            return None
        output_lines += start - baseline_cursor
        replacement_lines = len(span.new_text.splitlines())
        first_replacement_line = output_lines + 1
        last_replacement_line = output_lines + replacement_lines
        if (
            replacement_lines
            and first_replacement_line <= parser_line <= last_replacement_line
        ):
            owners.append(index)
        output_lines = last_replacement_line
        baseline_cursor = start + span.old_line_count
    return owners[0] if len(owners) == 1 else None


def _proposal_with_repaired_span(
    proposal: CompactSpanProposal,
    replacement: CompactSpanProposal,
    target_span: tuple[int, int],
) -> CompactSpanProposal:
    """Replace exactly one owned span while retaining every non-owning span."""
    if proposal.focus_path != replacement.focus_path or len(replacement.edits) != 1:
        raise ValueError("compact-v4 targeted repair must return exactly one owning span")
    edit = replacement.edits[0]
    if (edit.start_line, edit.old_line_count) != target_span:
        raise ValueError("compact-v4 targeted repair changed immutable span coordinates")
    matches = [
        index
        for index, prior in enumerate(proposal.edits)
        if (prior.start_line, prior.old_line_count) == target_span
    ]
    if len(matches) != 1:
        raise ValueError("compact-v4 targeted repair span is not uniquely owned")
    edits = list(proposal.edits)
    edits[matches[0]] = edit
    return CompactSpanProposal(proposal.focus_path, tuple(edits))


def _build_targeted_repair_prompt_plan(
    plan: PromptPlan,
    shard: PromptShard,
    task: TaskSpec,
    proposal: CompactSpanProposal,
    diagnostic: str,
    target_span: tuple[int, int],
    hooks: ProposalRuntimeHooks,
) -> PromptPlan:
    """Render bounded local baseline context for one provenance-owned span."""
    path = proposal.focus_path
    baseline_by_path = dict(plan.baseline_files)
    if shard.focus_paths != (path,) or path not in baseline_by_path:
        raise ValueError("targeted syntax repair drifted from its immutable shard")
    baseline = baseline_by_path[path]
    if baseline is None:
        context = "ABSENT FILE"
    else:
        lines = baseline.splitlines(keepends=True)
        start = target_span[0] - 1
        consumed_end = start + max(1, target_span[1])
        if start > len(lines) or consumed_end > len(lines) + (target_span[1] == 0):
            raise ValueError("targeted syntax repair span is outside its baseline")
        context_start = max(0, start - hooks.prompt_context_lines)
        context_end = min(len(lines), consumed_end + hooks.prompt_context_lines)
        context = hooks.render_selected_lines(
            lines,
            set(range(context_start, context_end)),
        )
    body = (
        "EDIT_TASK_BEGIN\n"
        f"{task.objective}\n"
        "EDIT_TASK_END\n"
        "TARGET_REPAIR_REQUIREMENTS_BEGIN\n"
        f"Repair only {path}. Only the exact target s/n is editable; surrounding "
        "lines are context, and every frozen sibling path and span remains immutable. "
        "Return compact spans only.\n"
        "TARGET_REPAIR_REQUIREMENTS_END\n"
        "TARGET_REPAIR_CONTEXT_BEGIN\n"
        f"{context}\n"
        "TARGET_REPAIR_CONTEXT_END"
    )
    targeted_shard = PromptShard(
        focus_paths=(path,),
        prompt=bind_compact_focus_path(
            body,
            path,
            editable_ranges=shard.editable_ranges,
        ),
        editable_ranges=shard.editable_ranges,
    )
    targeted = PromptPlan(
        shards=(targeted_shard,),
        source_bytes=(len(baseline.encode("utf-8")) if baseline is not None else 0),
        protocol_digest=plan.protocol_digest,
        baseline_files=((path, baseline),),
        proposal_protocol=COMPACT_PROPOSAL_PROTOCOL_V4,
        sampling_profile=COMPACT_V4_SYNTAX_REPAIR_SAMPLING_PROFILE_ID,
    )
    return build_syntax_repair_prompt_plan(
        targeted,
        (proposal,),
        _repair_candidate_syntax_diagnosis(diagnostic),
        target_span=target_span,
    )


@dataclass
class _CompactRepairState:
    """Mutable parent-owned state for bounded compact repair candidates."""

    active_plan: PromptPlan
    original_path_groups: tuple[tuple[str, ...], ...]
    original_paths: tuple[str, ...]
    original_ranges: tuple[tuple[tuple[int, int], ...], ...]
    original_baselines: dict[str, str | None]
    frozen: dict[str, CompactSpanProposal]
    latest: dict[str, CompactSpanProposal]
    targets: dict[str, tuple[int, int]]


@dataclass(frozen=True)
class _RepairCandidateOutcome:
    """One candidate pass over every currently failing repair shard."""

    next_plans: tuple[PromptPlan, ...]
    first_diagnostic: str | None
    last_contract: ProposalContract | None


def _initial_repair_shard_plan(
    state: _CompactRepairState,
    plan: PromptPlan,
    shard: PromptShard,
    span_proposal: CompactSpanProposal,
    task: TaskSpec,
    contract: ProposalContract,
    hooks: ProposalRuntimeHooks,
) -> PromptPlan | None:
    """Validate one inherited repair shard and return its next failing plan."""
    path = span_proposal.focus_path
    state.latest[path] = span_proposal
    shard_plan = _one_shard_prompt_plan(plan, shard)
    proposal = expand_compact_span_proposals(
        (span_proposal,),
        contract=contract,
        expected_path_groups=(shard.focus_paths,),
        expected_baseline_files=dict(shard_plan.baseline_files),
        expected_editable_ranges=(shard.editable_ranges,),
    )
    diagnostic = hooks.proposal_python_syntax_diagnostics(proposal).get(path)
    _report_repair_shard_state(
        path,
        candidate="initial",
        state=_repair_preflight_state(diagnostic),
        diagnostic=diagnostic,
        hooks=hooks,
    )
    if diagnostic is None:
        state.frozen[path] = span_proposal
        return None
    baseline = dict(shard_plan.baseline_files)[path]
    owning_index = _compact_v4_syntax_owning_span_index(
        baseline,
        span_proposal,
        diagnostic,
    )
    if owning_index is not None:
        owning_edit = span_proposal.edits[owning_index]
        target = (owning_edit.start_line, owning_edit.old_line_count)
        state.targets[path] = target
        _report_repair_shard_state(
            path,
            candidate="initial",
            state="span_targeted",
            diagnostic=diagnostic,
            target_span=target,
            hooks=hooks,
        )
        return _build_targeted_repair_prompt_plan(
            plan,
            shard,
            task,
            span_proposal,
            diagnostic,
            target,
            hooks,
        )
    if (
        hashlib.sha256(path.encode("utf-8")).hexdigest()
        != plan.repair_diagnosis_path_sha256
        and _syntax_failure_class(diagnostic) == "python_syntax"
    ):
        return build_syntax_repair_prompt_plan(
            shard_plan,
            (span_proposal,),
            _repair_candidate_syntax_diagnosis(diagnostic),
        )
    return shard_plan


def _prepare_compact_repair_state(
    plan: PromptPlan,
    task: TaskSpec,
    reference: CodexReference,
    required_tests: tuple[str, ...],
    max_output_tokens: int | None,
    hooks: ProposalRuntimeHooks,
) -> _CompactRepairState:
    """Validate immutable shard identity and prepare inherited repair state."""
    path_groups = tuple(shard.focus_paths for shard in plan.shards)
    if any(len(paths) != 1 for paths in path_groups):
        raise ValueError("compact-v4 prompt shards must bind exactly one focus path")
    state = _CompactRepairState(
        active_plan=plan,
        original_path_groups=path_groups,
        original_paths=tuple(paths[0] for paths in path_groups),
        original_ranges=tuple(shard.editable_ranges for shard in plan.shards),
        original_baselines=dict(plan.baseline_files),
        frozen={},
        latest={},
        targets={},
    )
    if not plan.repair_proposals:
        return state
    contract = ProposalContract(
        baseline_sha=reference.baseline_sha,
        task_id=task.task_id,
        tests=required_tests,
        make_commands=task.canonical_make_commands,
        proposal_protocol=COMPACT_PROPOSAL_PROTOCOL_V4,
        max_output_tokens=max_output_tokens,
    )
    failing = tuple(
        next_plan
        for shard, proposal in zip(plan.shards, plan.repair_proposals, strict=True)
        if (
            next_plan := _initial_repair_shard_plan(
                state,
                plan,
                shard,
                proposal,
                task,
                contract,
                hooks,
            )
        )
        is not None
    )
    if not failing:
        raise ValueError(
            "compact-v4 repair state did not reproduce its parent syntax failure"
        )
    state.active_plan = _combine_shard_prompt_plans(
        failing,
        protocol_digest=plan.protocol_digest,
    )
    return state


def _repair_shard_contract(
    plan: PromptPlan,
    task: TaskSpec,
    reference: CodexReference,
    required_tests: tuple[str, ...],
    state: _CompactRepairState,
    shard: PromptShard,
    candidate_index: int,
    max_output_tokens: int | None,
) -> tuple[str, ProposalContract, PromptPlan]:
    """Bind one repair request before entering the rejectable model boundary."""
    shard_plan = _one_shard_prompt_plan(state.active_plan, shard)
    request = encode_prompt_batch(
        (shard.prompt,),
        protocol_digest=plan.protocol_digest,
    )
    contract = ProposalContract.for_request(
        request=request,
        baseline_sha=reference.baseline_sha,
        task_id=task.task_id,
        tests=required_tests,
        make_commands=task.canonical_make_commands,
        proposal_protocol=plan.proposal_protocol,
        sampling_profile=plan.sampling_profile,
        sampling_candidate_index=candidate_index,
        repair_state_sha256=compact_v4_repair_shard_state_digest(
            tuple(
                state.latest[path]
                for path in state.original_paths
                if path in state.latest
            )
        ),
        max_output_tokens=max_output_tokens,
    )
    return request, contract, shard_plan


def _decode_repair_shard(
    runner: _ObservableRunner,
    model_path: Path,
    plan: PromptPlan,
    state: _CompactRepairState,
    shard: PromptShard,
    request: str,
    contract: ProposalContract,
    shard_plan: PromptPlan,
    hooks: ProposalRuntimeHooks,
) -> tuple[CompactSpanProposal, ProposalManifest]:
    """Request and expand one independently bounded repair shard."""
    raw = hooks.proposal_request(
        runner,
        model_path,
        request,
        contract=contract,
    )
    span = decode_compact_span_batch(
        raw,
        expected_protocol_digest=plan.protocol_digest,
        expected_count=1,
    )[0]
    target = state.targets.get(span.focus_path)
    if target is not None:
        span = _proposal_with_repaired_span(state.latest[span.focus_path], span, target)
    proposal = expand_compact_span_proposals(
        (span,),
        contract=contract,
        expected_path_groups=(shard.focus_paths,),
        expected_baseline_files=dict(shard_plan.baseline_files),
        expected_editable_ranges=(shard.editable_ranges,),
    )
    return span, proposal


def _advance_repair_shard(
    state: _CompactRepairState,
    shard_plan: PromptPlan,
    shard: PromptShard,
    span: CompactSpanProposal,
    proposal: ProposalManifest,
    task: TaskSpec,
    candidate: str,
    hooks: ProposalRuntimeHooks,
) -> tuple[PromptPlan | None, str | None]:
    """Freeze one valid shard or build its next syntax-repair plan."""
    path = span.focus_path
    state.latest[path] = span
    diagnostic = hooks.proposal_python_syntax_diagnostics(proposal).get(path)
    _report_repair_shard_state(
        path,
        candidate=candidate,
        state=_repair_preflight_state(diagnostic),
        diagnostic=diagnostic,
        hooks=hooks,
    )
    if diagnostic is None:
        state.frozen[path] = span
        state.targets.pop(path, None)
        return None, None
    next_plan = shard_plan
    if _syntax_failure_class(diagnostic) == "python_syntax":
        baseline = dict(shard_plan.baseline_files)[path]
        owning_index = _compact_v4_syntax_owning_span_index(
            baseline,
            span,
            diagnostic,
        )
        target: tuple[int, int] | None = None
        if owning_index is not None:
            edit = span.edits[owning_index]
            target = (edit.start_line, edit.old_line_count)
            state.targets[path] = target
            _report_repair_shard_state(
                path,
                candidate=candidate,
                state="span_targeted",
                diagnostic=diagnostic,
                target_span=target,
                hooks=hooks,
            )
        else:
            state.targets.pop(path, None)
        next_plan = (
            _build_targeted_repair_prompt_plan(
                shard_plan,
                shard,
                task,
                span,
                diagnostic,
                target,
                hooks,
            )
            if target is not None
            else build_syntax_repair_prompt_plan(
                shard_plan,
                (span,),
                _repair_candidate_syntax_diagnosis(diagnostic),
            )
        )
    return next_plan, diagnostic


def _run_repair_candidate(
    runner: _ObservableRunner,
    model_path: Path,
    plan: PromptPlan,
    task: TaskSpec,
    reference: CodexReference,
    required_tests: tuple[str, ...],
    state: _CompactRepairState,
    candidate_index: int,
    max_output_tokens: int | None,
    hooks: ProposalRuntimeHooks,
) -> _RepairCandidateOutcome:
    """Evaluate one candidate number across all currently failing shards."""
    next_plans: list[PromptPlan] = []
    first_diagnostic: str | None = None
    last_contract: ProposalContract | None = None
    candidate = f"{candidate_index + 1}/{COMPACT_V4_REPAIR_CANDIDATE_LIMIT}"
    for shard in state.active_plan.shards:
        request, contract, shard_plan = _repair_shard_contract(
            plan,
            task,
            reference,
            required_tests,
            state,
            shard,
            candidate_index,
            max_output_tokens,
        )
        last_contract = contract
        try:
            span, proposal = _decode_repair_shard(
                runner,
                model_path,
                plan,
                state,
                shard,
                request,
                contract,
                shard_plan,
                hooks,
            )
        except (RuntimeError, ValueError):
            _report_repair_shard_state(
                shard.focus_paths[0],
                candidate=candidate,
                state="proposal_rejected",
                hooks=hooks,
            )
            next_plans.append(shard_plan)
            continue
        next_plan, diagnostic = _advance_repair_shard(
            state,
            shard_plan,
            shard,
            span,
            proposal,
            task,
            candidate,
            hooks,
        )
        if diagnostic is not None and first_diagnostic is None:
            first_diagnostic = diagnostic
        if next_plan is not None:
            next_plans.append(next_plan)
    return _RepairCandidateOutcome(tuple(next_plans), first_diagnostic, last_contract)


def _finalize_repair_candidate(
    state: _CompactRepairState,
    outcome: _RepairCandidateOutcome,
    candidate_index: int,
    hooks: ProposalRuntimeHooks,
) -> GeneratedProposal | None:
    """Return a completely frozen aggregate or report the bounded rejection."""
    candidate = f"{candidate_index + 1}/{COMPACT_V4_REPAIR_CANDIDATE_LIMIT}"
    if outcome.next_plans:
        result = "syntax_rejected" if outcome.first_diagnostic else "proposal_rejected"
        diagnostic = (
            f" {outcome.first_diagnostic}" if outcome.first_diagnostic else ""
        )
        hooks.progress(
            "SELF_IMPROVE_REPAIR_CANDIDATE "
            f"candidate={candidate} result={result} "
            f"failing_shards={len(outcome.next_plans)} "
            f"frozen_shards={len(state.frozen)}{diagnostic}"
        )
        return None
    if outcome.last_contract is None or set(state.frozen) != set(state.original_paths):
        raise ValueError("compact-v4 repair did not cover the immutable shard set")
    spans = tuple(state.frozen[path] for path in state.original_paths)
    proposal = expand_compact_span_proposals(
        spans,
        contract=outcome.last_contract,
        expected_path_groups=state.original_path_groups,
        expected_baseline_files=state.original_baselines,
        expected_editable_ranges=state.original_ranges,
    )
    if hooks.proposal_python_syntax_preflight(proposal) is not None:
        raise ValueError(
            "compact-v4 frozen repair aggregate failed immutable syntax revalidation"
        )
    hooks.progress(
        "SELF_IMPROVE_REPAIR_CANDIDATE "
        f"candidate={candidate} result=selected "
        f"failing_shards=0 frozen_shards={len(state.frozen)}"
    )
    return GeneratedProposal(proposal, spans)


def _generate_compact_v4_repair_plan_result(
    runner: _ObservableRunner,
    model_path: Path,
    plan: PromptPlan,
    task: TaskSpec,
    reference: CodexReference,
    max_output_tokens: int | None,
    hooks: ProposalRuntimeHooks,
) -> GeneratedProposal:
    """Generate bounded repair shards independently and freeze valid results."""
    required_tests = hooks.required_prompt_tests(task, reference)
    state = _prepare_compact_repair_state(
        plan,
        task,
        reference,
        required_tests,
        max_output_tokens,
        hooks,
    )
    for candidate_index in range(COMPACT_V4_REPAIR_CANDIDATE_LIMIT):
        outcome = _run_repair_candidate(
            runner,
            model_path,
            plan,
            task,
            reference,
            required_tests,
            state,
            candidate_index,
            max_output_tokens,
            hooks,
        )
        result = _finalize_repair_candidate(
            state,
            outcome,
            candidate_index,
            hooks,
        )
        if result is not None:
            return result
        if candidate_index + 1 < COMPACT_V4_REPAIR_CANDIDATE_LIMIT:
            state.active_plan = _combine_shard_prompt_plans(
                outcome.next_plans,
                protocol_digest=plan.protocol_digest,
            )
    raise ValueError(
        "compact-v4 syntax repair exhausted "
        f"{COMPACT_V4_REPAIR_CANDIDATE_LIMIT} bounded candidates"
    )


def _generate_compact_v4_plan_result(
    runner: _ObservableRunner,
    model_path: Path,
    plan: PromptPlan,
    task: TaskSpec,
    reference: CodexReference,
    required_tests: tuple[str, ...],
    max_output_tokens: int | None,
    hooks: ProposalRuntimeHooks,
) -> GeneratedProposal:
    """Decode one initial compact-v4 candidate against trusted snapshots."""
    if not plan.baseline_files:
        raise ValueError("compact-v4 prompt plan requires trusted baseline snapshots")
    if plan.sampling_profile == COMPACT_V4_SYNTAX_REPAIR_SAMPLING_PROFILE_ID:
        return _generate_compact_v4_repair_plan_result(
            runner,
            model_path,
            plan,
            task,
            reference,
            max_output_tokens,
            hooks,
        )
    path_groups = tuple(shard.focus_paths for shard in plan.shards)
    if any(len(paths) != 1 for paths in path_groups):
        raise ValueError("compact-v4 prompt shards must bind exactly one focus path")
    paths = tuple(group[0] for group in path_groups)
    request = encode_prompt_batch(
        tuple(shard.prompt for shard in plan.shards),
        protocol_digest=plan.protocol_digest,
    )
    contract = ProposalContract.for_request(
        request=request,
        baseline_sha=reference.baseline_sha,
        task_id=task.task_id,
        tests=required_tests,
        make_commands=task.canonical_make_commands,
        proposal_protocol=plan.proposal_protocol,
        sampling_profile=plan.sampling_profile,
        sampling_candidate_index=0,
        max_output_tokens=max_output_tokens,
    )
    raw = hooks.proposal_request(
        runner,
        model_path,
        request,
        contract=contract,
    )
    spans = decode_compact_span_batch(
        raw,
        expected_protocol_digest=plan.protocol_digest,
        expected_count=len(plan.shards),
    )
    by_path: dict[str, CompactSpanProposal] = {}
    for span in spans:
        if span.focus_path in by_path:
            raise ValueError("compact-v4 repair tried to replace a frozen shard")
        by_path[span.focus_path] = span
    if set(by_path) != set(paths):
        raise ValueError("compact-v4 repair did not cover the immutable shard set")
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


def _generate_legacy_plan_result(
    runner: _ObservableRunner,
    model_path: Path,
    plan: PromptPlan,
    task: TaskSpec,
    reference: CodexReference,
    required_tests: tuple[str, ...],
    max_output_tokens: int | None,
    hooks: ProposalRuntimeHooks,
) -> GeneratedProposal:
    """Decode and merge legacy proposal shards."""
    request = encode_prompt_batch(
        tuple(shard.prompt for shard in plan.shards),
        protocol_digest=plan.protocol_digest,
    )
    contract = ProposalContract.for_request(
        request=request,
        baseline_sha=reference.baseline_sha,
        task_id=task.task_id,
        tests=required_tests,
        make_commands=task.canonical_make_commands,
        proposal_protocol=plan.proposal_protocol,
        sampling_profile=plan.sampling_profile,
        max_output_tokens=max_output_tokens,
    )
    raw = hooks.proposal_request(
        runner,
        model_path,
        request,
        contract=contract,
    )
    proposals = decode_proposal_batch(
        raw,
        expected_protocol_digest=plan.protocol_digest,
        expected_count=len(plan.shards),
    )
    return GeneratedProposal(
        merge_proposal_manifests(
            proposals,
            expected_path_groups=tuple(
                shard.focus_paths for shard in plan.shards
            ),
            expected_baseline_sha=reference.baseline_sha,
            expected_task_id=task.task_id,
            expected_tests=required_tests,
            expected_make_commands=task.canonical_make_commands,
            expected_baseline_files=(
                dict(plan.baseline_files) if plan.baseline_files else None
            ),
        )
    )


def generate_local_proposal_plan_result(
    runner: _ObservableRunner,
    model_path: Path,
    plan: PromptPlan,
    task: TaskSpec,
    reference: CodexReference,
    *,
    hooks: ProposalRuntimeHooks,
    proposal_codec: ManagedCandidateProposalCodec[GeneratedProposal] | None = None,
    max_output_tokens: int | None = None,
    timeout_seconds: float = 300.0,
) -> GeneratedProposal:
    """Decode all shards and retain only validated compact-v4 repair material."""
    if proposal_codec is not None:
        if not isinstance(proposal_codec, ManagedCandidateProposalCodec):
            raise ValueError("proposal_codec must be a managed proposal envelope")
        if proposal_codec.request_contract_json is None:
            raise ValueError("managed local proposal envelope has no request contract")
        envelope = proposal_codec.worker_envelope
        contract = ProposalContract.from_json(envelope.request_contract_json)
        if (
            max_output_tokens is not None
            and contract.max_output_tokens != max_output_tokens
        ):
            raise ValueError("managed local proposal output token budget mismatch")
        raw = hooks.proposal_request(
            runner,
            model_path,
            proposal_codec.request_text,
            envelope=envelope,
            timeout_seconds=timeout_seconds,
        )
        generated = proposal_codec.decoder(raw)
        if not isinstance(generated, GeneratedProposal):
            raise ValueError(
                "managed local proposal envelope decoder returned invalid output"
            )
        return generated
    required_tests = hooks.required_prompt_tests(task, reference)
    if plan.proposal_protocol == COMPACT_PROPOSAL_PROTOCOL_V4:
        return _generate_compact_v4_plan_result(
            runner,
            model_path,
            plan,
            task,
            reference,
            required_tests,
            max_output_tokens,
            hooks,
        )
    return _generate_legacy_plan_result(
        runner,
        model_path,
        plan,
        task,
        reference,
        required_tests,
        max_output_tokens,
        hooks,
    )
