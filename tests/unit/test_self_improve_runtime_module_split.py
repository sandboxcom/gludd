"""Compatibility contract for the self-improvement runtime module split."""

from __future__ import annotations

import ast
import hashlib
import inspect
import json
import pickle
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

import general_ludd.self_improve.codex_comparison as comparison
import general_ludd.self_improve.runtime as facade
import general_ludd.self_improve.runtime_local_proposals as proposals
from general_ludd.self_improve.codex_evaluation import CodexReference
from general_ludd.self_improve.codex_gateway import _decode_compact_span_proposal
from general_ludd.self_improve.codex_protocol import (
    CompactSpanProposal,
    ProposalContract,
)
from general_ludd.self_improve.managed_candidate_routing_types import (
    ManagedCandidateProposalCodec,
)
from general_ludd.self_improve.managed_prompt_contracts import PromptShard
from general_ludd.self_improve.managed_runner_contracts import PromptPlan, TaskSpec

ROOT = Path(__file__).resolve().parents[2]
FACADE_SOURCE = ROOT / "src" / "general_ludd" / "self_improve" / "runtime.py"
PROPOSAL_SOURCE = (
    ROOT
    / "src"
    / "general_ludd"
    / "self_improve"
    / "runtime_local_proposals.py"
)
VALIDATION_SOURCE = (
    ROOT / "src" / "general_ludd" / "self_improve" / "runtime_validation.py"
)
MAX_SOURCE_LINES = 2_500


def _hooks(
    *,
    diagnostics: dict[str, str] | None = None,
    progress: list[str] | None = None,
    request_response: str | None = None,
    syntax_preflight: str | None = None,
) -> proposals.ProposalRuntimeHooks:
    events = progress if progress is not None else []

    def unused_request(*_args: object, **_kwargs: object) -> str:
        if request_response is None:
            raise AssertionError("proposal request was not expected")
        return request_response

    return proposals.ProposalRuntimeHooks(
        proposal_request=cast(Any, unused_request),
        progress=events.append,
        proposal_python_syntax_diagnostics=lambda _proposal: (
            {} if diagnostics is None else diagnostics
        ),
        proposal_python_syntax_preflight=lambda _proposal: syntax_preflight,
        render_selected_lines=lambda lines, selected: "".join(
            lines[index] for index in sorted(selected)
        ),
        required_prompt_tests=lambda _task, _reference: (
            "tests/unit/test_example.py",
        ),
        prompt_context_lines=5,
    )


def _span(
    path: str = "src/example.py",
    *,
    start: int = 1,
    old_lines: int = 1,
    replacement: str = "value = 2\n",
) -> CompactSpanProposal:
    return _decode_compact_span_proposal(
        json.dumps(
            {"e": [{"s": start, "n": old_lines, "z": replacement}]}
        ),
        focus_path=path,
    )


def test_runtime_split_components_stay_below_the_source_line_limit() -> None:
    """The compatibility facade and cohesive implementation retain headroom."""
    assert PROPOSAL_SOURCE.is_file()
    for path in (FACADE_SOURCE, PROPOSAL_SOURCE, VALIDATION_SOURCE):
        assert len(path.read_text(encoding="utf-8").splitlines()) < MAX_SOURCE_LINES


def test_runtime_public_callable_signatures_and_pickle_paths_remain_stable() -> None:
    """Wrappers cannot erase signatures or move established pickle identities."""
    signatures = {
        "generate_local_proposal": (
            "runner",
            "model_path",
            "prompt",
            "timeout_seconds",
        ),
        "generate_local_proposal_plan": (
            "runner",
            "model_path",
            "plan",
            "task",
            "reference",
            "max_output_tokens",
        ),
    }
    for name, parameter_names in signatures.items():
        function = getattr(facade, name)
        assert tuple(inspect.signature(function).parameters) == parameter_names
        assert function.__module__ == facade.__name__
        assert pickle.loads(pickle.dumps(function)) is function

    result = facade.MakeResult(("make", "test"), 0, "ok", "", 0.25)
    assert facade.MakeResult.__module__ == facade.__name__
    assert pickle.loads(pickle.dumps(result)) == result


def test_runtime_plan_wrapper_retains_the_facade_monkeypatch_seam(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The public manifest API must resolve the historical facade helper."""
    sentinel = cast(Any, object())
    calls: list[tuple[tuple[object, ...], int | None]] = []

    def generate(
        runner: object,
        model_path: object,
        plan: object,
        task: object,
        reference: object,
        *,
        max_output_tokens: int | None = None,
    ) -> SimpleNamespace:
        calls.append(
            ((runner, model_path, plan, task, reference), max_output_tokens)
        )
        return SimpleNamespace(proposal=sentinel)

    monkeypatch.setattr(facade, "_generate_local_proposal_plan_result", generate)
    arguments = tuple(object() for _ in range(5))

    actual = facade.generate_local_proposal_plan(
        *cast(tuple[Any, Any, Any, Any, Any], arguments),
        max_output_tokens=321,
    )

    assert actual is sentinel
    assert calls == [(arguments, 321)]


def test_extracted_proposal_module_does_not_import_the_facade() -> None:
    """The implementation dependency graph stays one-way and cycle-free."""
    for path in (PROPOSAL_SOURCE, VALIDATION_SOURCE):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported_modules = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module is not None
        }
        assert "general_ludd.self_improve.runtime" not in imported_modules


def test_extracted_prompt_plan_helpers_retain_fail_closed_shapes() -> None:
    """Shard isolation rejects ambiguous or absent trusted baseline identity."""
    multi = PromptShard(("src/a.py", "src/b.py"), "bounded")
    multi_plan = PromptPlan(
        (multi,),
        2,
        baseline_files=(("src/a.py", "a"), ("src/b.py", "b")),
    )
    with pytest.raises(ValueError, match="exactly one focus path"):
        proposals._one_shard_prompt_plan(multi_plan, multi)

    single = PromptShard(("src/a.py",), "bounded")
    baseline_free = PromptPlan((single,), 0)
    with pytest.raises(ValueError, match="absent from the trusted baseline"):
        proposals._one_shard_prompt_plan(baseline_free, single)
    with pytest.raises(ValueError, match="at least one failing shard"):
        proposals._combine_shard_prompt_plans(
            (),
            protocol_digest=baseline_free.protocol_digest,
        )


def test_extracted_repair_telemetry_rejects_inconsistent_state() -> None:
    """Invalid state, diagnosis, target, and event shapes remain fail closed."""
    path = "src/example.py"
    hooks = _hooks()
    diagnostic = facade._syntax_diagnostic(
        path,
        failure_type="python_syntax",
        line=1,
        column=1,
    )

    with pytest.raises(RuntimeError, match="state is unsupported"):
        proposals._report_repair_shard_state(
            path,
            candidate="initial",
            state="unknown",
            hooks=hooks,
        )
    with pytest.raises(RuntimeError, match="state and diagnosis disagree"):
        proposals._report_repair_shard_state(
            path,
            candidate="initial",
            state="syntax_rejected",
            hooks=hooks,
        )
    with pytest.raises(RuntimeError, match="target telemetry is inconsistent"):
        proposals._report_repair_shard_state(
            path,
            candidate="initial",
            state="frozen",
            target_span=(1, 1),
            hooks=hooks,
        )
    with pytest.raises(RuntimeError, match="target telemetry is invalid"):
        proposals._report_repair_shard_state(
            path,
            candidate="initial",
            state="span_targeted",
            diagnostic=diagnostic,
            target_span=(cast(Any, True), 0),
            hooks=hooks,
        )
    with pytest.raises(RuntimeError, match="exceeded 256 bytes"):
        proposals._report_repair_shard_state(
            path,
            candidate="x" * 300,
            state="frozen",
            hooks=hooks,
        )


def test_extracted_span_provenance_and_replacement_reject_drift() -> None:
    """Path, parser class, baseline extent, and target coordinates stay bound."""
    path = "src/example.py"
    proposal = _span(path)
    wrong_path = _span("src/other.py")
    wrong_coordinate = _span(path, start=3)

    with pytest.raises(RuntimeError, match="owning shard"):
        proposals._compact_v4_syntax_owning_span_index(
            "value = 1\n",
            proposal,
            facade._syntax_diagnostic(
                "src/other.py",
                failure_type="python_syntax",
                line=1,
            ),
        )
    assert (
        proposals._compact_v4_syntax_owning_span_index(
            "value = 1\n",
            proposal,
            facade._syntax_diagnostic(
                path,
                failure_type="python_read",
                line=1,
            ),
        )
        is None
    )
    assert (
        proposals._compact_v4_syntax_owning_span_index(
            "value = 1\n",
            wrong_coordinate,
            facade._syntax_diagnostic(
                path,
                failure_type="python_syntax",
                line=1,
            ),
        )
        is None
    )
    with pytest.raises(ValueError, match="exactly one owning span"):
        proposals._proposal_with_repaired_span(
            proposal,
            wrong_path,
            (1, 1),
        )
    with pytest.raises(ValueError, match="immutable span coordinates"):
        proposals._proposal_with_repaired_span(
            proposal,
            wrong_coordinate,
            (1, 1),
        )
    with pytest.raises(ValueError, match="not uniquely owned"):
        proposals._proposal_with_repaired_span(
            proposal,
            wrong_coordinate,
            (3, 1),
        )


def test_targeted_repair_prompt_plan_rejects_drift_and_out_of_bounds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Targeted repair keeps shard identity and absent-file bounds immutable."""
    path = "src/example.py"
    shard = PromptShard((path,), "bounded", ((1, 2),))
    task = TaskSpec(
        "S1",
        "bounded",
        ("make test-specific TESTFILE=tests/unit/test_example.py",),
    )
    diagnostic = facade._syntax_diagnostic(
        path,
        failure_type="python_syntax",
        line=1,
    )
    plan = PromptPlan(
        (shard,),
        len("value = 1\n"),
        baseline_files=((path, "value = 1\n"),),
    )
    with pytest.raises(ValueError, match="immutable shard"):
        proposals._build_targeted_repair_prompt_plan(
            plan,
            PromptShard(("src/other.py",), "bounded"),
            task,
            _span(path),
            diagnostic,
            (1, 1),
            _hooks(),
        )
    with pytest.raises(ValueError, match="outside its baseline"):
        proposals._build_targeted_repair_prompt_plan(
            plan,
            shard,
            task,
            _span(path),
            diagnostic,
            (3, 1),
            _hooks(),
        )

    monkeypatch.setattr(
        proposals,
        "build_syntax_repair_prompt_plan",
        lambda targeted, *_args, **_kwargs: targeted,
    )
    absent = PromptPlan(
        (shard,),
        0,
        baseline_files=((path, None),),
    )
    targeted = proposals._build_targeted_repair_prompt_plan(
        absent,
        shard,
        task,
        _span(path, old_lines=0),
        diagnostic,
        (1, 0),
        _hooks(),
    )
    assert targeted.source_bytes == 0
    assert "ABSENT FILE" in targeted.shards[0].prompt


def test_repair_state_rejects_ambiguous_or_already_clean_parent() -> None:
    """Repair preparation requires one failing proposal for every bound shard."""
    path = "src/example.py"
    task = TaskSpec(
        "S1",
        "bounded",
        ("make test-specific TESTFILE=tests/unit/test_example.py",),
    )
    reference = CodexReference(
        baseline_sha="a" * 40,
        reference_sha="b" * 40,
        changed_files=frozenset({path}),
        test_files=frozenset({"tests/unit/test_example.py"}),
        changed_lines=1,
        elapsed_seconds=1.0,
    )
    multi = PromptShard((path, "src/other.py"), "bounded")
    multi_plan = PromptPlan(
        (multi,),
        2,
        baseline_files=((path, "a"), ("src/other.py", "b")),
        proposal_protocol=comparison.COMPACT_PROPOSAL_PROTOCOL_V4,
    )
    with pytest.raises(ValueError, match="exactly one focus path"):
        proposals._prepare_compact_repair_state(
            multi_plan,
            task,
            reference,
            ("tests/unit/test_example.py",),
            None,
            _hooks(),
        )

    baseline = "value = 1\n"
    shard = PromptShard((path,), "bounded", ((1, 2),))
    clean_parent = PromptPlan(
        (shard,),
        len(baseline),
        baseline_files=((path, baseline),),
        proposal_protocol=comparison.COMPACT_PROPOSAL_PROTOCOL_V4,
        sampling_profile=(
            comparison.COMPACT_V4_SYNTAX_REPAIR_SAMPLING_PROFILE_ID
        ),
        repair_proposals=(_span(path),),
        repair_diagnosis_path_sha256=hashlib.sha256(path.encode()).hexdigest(),
    )
    with pytest.raises(ValueError, match="did not reproduce"):
        proposals._prepare_compact_repair_state(
            clean_parent,
            task,
            reference,
            ("tests/unit/test_example.py",),
            None,
            _hooks(),
        )


def test_repair_shard_advancement_clears_stale_target_and_preserves_read_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only syntax failures retarget spans; other parser failures retain scope."""
    path = "src/example.py"
    baseline = "value = 1\n"
    shard = PromptShard((path,), "bounded")
    plan = PromptPlan(
        (shard,),
        len(baseline),
        baseline_files=((path, baseline),),
    )
    state = proposals._CompactRepairState(
        active_plan=plan,
        original_path_groups=((path,),),
        original_paths=(path,),
        original_ranges=((),),
        original_baselines={path: baseline},
        frozen={},
        latest={},
        targets={path: (1, 1)},
    )
    task = TaskSpec(
        "S1",
        "bounded",
        ("make test-specific TESTFILE=tests/unit/test_example.py",),
    )
    read_diagnostic = facade._syntax_diagnostic(
        path,
        failure_type="python_read",
        line=1,
    )
    next_plan, actual = proposals._advance_repair_shard(
        state,
        plan,
        shard,
        _span(path),
        cast(Any, object()),
        task,
        "1/2",
        _hooks(diagnostics={path: read_diagnostic}),
    )
    assert next_plan is plan
    assert actual == read_diagnostic
    assert state.targets[path] == (1, 1)

    monkeypatch.setattr(
        proposals,
        "build_syntax_repair_prompt_plan",
        lambda current, *_args, **_kwargs: current,
    )
    syntax_diagnostic = facade._syntax_diagnostic(
        path,
        failure_type="python_syntax",
        line=2,
    )
    next_plan, actual = proposals._advance_repair_shard(
        state,
        plan,
        shard,
        _span(path),
        cast(Any, object()),
        task,
        "2/2",
        _hooks(diagnostics={path: syntax_diagnostic}),
    )
    assert next_plan is plan
    assert actual == syntax_diagnostic
    assert path not in state.targets


def test_extracted_generation_boundaries_reject_ambiguous_inputs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Top-level compact generation still denies unbound and multi-path shards."""
    task = TaskSpec(
        "S1",
        "bounded",
        ("make test-specific TESTFILE=tests/unit/test_example.py",),
    )
    reference = CodexReference(
        baseline_sha="a" * 40,
        reference_sha="b" * 40,
        changed_files=frozenset({"src/a.py", "src/b.py"}),
        test_files=frozenset({"tests/unit/test_example.py"}),
        changed_lines=2,
        elapsed_seconds=1.0,
    )
    single = PromptShard(("src/a.py",), "bounded")
    without_baseline = PromptPlan((single,), 0)
    with pytest.raises(ValueError, match="trusted baseline snapshots"):
        proposals._generate_compact_v4_plan_result(
            cast(Any, object()),
            Path("model.gguf"),
            without_baseline,
            task,
            reference,
            ("tests/unit/test_example.py",),
            None,
            _hooks(),
        )

    multi = PromptShard(("src/a.py", "src/b.py"), "bounded")
    multi_plan = PromptPlan(
        (multi,),
        2,
        baseline_files=(("src/a.py", "a"), ("src/b.py", "b")),
        proposal_protocol=comparison.COMPACT_PROPOSAL_PROTOCOL_V4,
    )
    with pytest.raises(ValueError, match="exactly one focus path"):
        proposals._generate_compact_v4_plan_result(
            cast(Any, object()),
            Path("model.gguf"),
            multi_plan,
            task,
            reference,
            ("tests/unit/test_example.py",),
            None,
            _hooks(),
        )

    with pytest.raises(ValueError, match="managed proposal envelope"):
        proposals.generate_local_proposal_plan_result(
            cast(Any, object()),
            Path("model.gguf"),
            without_baseline,
            task,
            reference,
            hooks=_hooks(),
            proposal_codec=cast(Any, object()),
        )
    codec = ManagedCandidateProposalCodec(
        request_text="bounded",
        decoder=lambda _raw: cast(Any, object()),
        protocol_digest="c" * 64,
        sampling_digest="d" * 64,
    )
    with pytest.raises(ValueError, match="has no request contract"):
        proposals.generate_local_proposal_plan_result(
            cast(Any, object()),
            Path("model.gguf"),
            without_baseline,
            task,
            reference,
            hooks=_hooks(),
            proposal_codec=codec,
        )

    contract = ProposalContract(
        baseline_sha=reference.baseline_sha,
        task_id=task.task_id,
        tests=("tests/unit/test_example.py",),
        make_commands=task.canonical_make_commands,
        proposal_protocol=comparison.COMPACT_PROPOSAL_PROTOCOL_V4,
    )
    invalid_decoder = ManagedCandidateProposalCodec(
        request_text="bounded",
        decoder=lambda _raw: cast(Any, object()),
        protocol_digest="c" * 64,
        sampling_digest="d" * 64,
        request_contract_json=contract.to_json(),
        response_instruction="Return one bounded proposal.",
        response_schema_json="{}",
    )
    with pytest.raises(ValueError, match="decoder returned invalid output"):
        proposals.generate_local_proposal_plan_result(
            cast(Any, object()),
            Path("model.gguf"),
            without_baseline,
            task,
            reference,
            hooks=_hooks(request_response="{}"),
            proposal_codec=invalid_decoder,
        )

    shards = (
        PromptShard(("src/a.py",), "bounded"),
        PromptShard(("src/b.py",), "bounded"),
    )
    complete_plan = PromptPlan(
        shards,
        4,
        baseline_files=(("src/a.py", "a\n"), ("src/b.py", "b\n")),
        proposal_protocol=comparison.COMPACT_PROPOSAL_PROTOCOL_V4,
    )
    monkeypatch.setattr(
        proposals,
        "decode_compact_span_batch",
        lambda *_args, **_kwargs: (_span("src/a.py"), _span("src/a.py")),
    )
    with pytest.raises(ValueError, match="replace a frozen shard"):
        proposals._generate_compact_v4_plan_result(
            cast(Any, object()),
            Path("model.gguf"),
            complete_plan,
            task,
            reference,
            ("tests/unit/test_example.py",),
            None,
            _hooks(request_response="{}"),
        )
    monkeypatch.setattr(
        proposals,
        "decode_compact_span_batch",
        lambda *_args, **_kwargs: (_span("src/a.py"), _span("src/c.py")),
    )
    with pytest.raises(ValueError, match="immutable shard set"):
        proposals._generate_compact_v4_plan_result(
            cast(Any, object()),
            Path("model.gguf"),
            complete_plan,
            task,
            reference,
            ("tests/unit/test_example.py",),
            None,
            _hooks(request_response="{}"),
        )


def test_extracted_repair_finalization_rejects_incomplete_or_invalid_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Final aggregation cannot publish an incomplete or syntax-invalid repair."""
    path = "src/example.py"
    shard = PromptShard((path,), "bounded")
    plan = PromptPlan(
        (shard,),
        len("value = 1\n"),
        baseline_files=((path, "value = 1\n"),),
    )
    state = proposals._CompactRepairState(
        active_plan=plan,
        original_path_groups=((path,),),
        original_paths=(path,),
        original_ranges=((),),
        original_baselines={path: "value = 1\n"},
        frozen={},
        latest={},
        targets={},
    )
    incomplete = proposals._RepairCandidateOutcome((), None, None)
    with pytest.raises(ValueError, match="immutable shard set"):
        proposals._finalize_repair_candidate(state, incomplete, 0, _hooks())

    state.frozen[path] = _span(path)
    contract = ProposalContract(
        baseline_sha="a" * 40,
        task_id="S1",
        tests=("tests/unit/test_example.py",),
        make_commands=(
            "make test-specific TESTFILE=tests/unit/test_example.py",
        ),
        proposal_protocol=comparison.COMPACT_PROPOSAL_PROTOCOL_V4,
    )
    complete = proposals._RepairCandidateOutcome((), None, contract)
    monkeypatch.setattr(
        proposals,
        "expand_compact_span_proposals",
        lambda *_args, **_kwargs: cast(Any, object()),
    )
    with pytest.raises(ValueError, match="syntax revalidation"):
        proposals._finalize_repair_candidate(
            state,
            complete,
            0,
            _hooks(syntax_preflight="invalid"),
        )
