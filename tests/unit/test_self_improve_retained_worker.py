"""Retained local proposal worker and attempt-lifecycle contracts."""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import selectors
import subprocess
import time
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pytest
import scripts.self_improve_local_proposal as worker_module

import general_ludd.self_improve.runtime as runtime_module
from general_ludd.self_improve.codex_comparison import (
    COMPACT_V4_SYNTAX_REPAIR_SAMPLING_PROFILE_ID,
    CodexReference,
    CompactSpanProposal,
    LocalProposalGateway,
    ProposalContract,
    ProposalManifest,
    bind_compact_focus_path,
    decode_compact_span_batch,
    decode_prompt_batch,
    decode_proposal_batch,
    encode_prompt_batch,
    encode_proposal_batch,
)
from general_ludd.self_improve.managed_runner import PromptPlan, PromptShard
from general_ludd.self_improve.runtime import MakeResult, TaskSpec

runner_module = cast(Any, runtime_module)


def _manifest(path: str) -> ProposalManifest:
    return ProposalManifest.from_json(
        json.dumps(
            {
                "schema_version": 1,
                "baseline_sha": "a" * 40,
                "task_id": "S83.133",
                "edits": [
                    {
                        "operation": "replace",
                        "path": path,
                        "old_text": "before",
                        "new_text": "after",
                    }
                ],
                "tests": ["tests/unit/test_example.py"],
                "make_commands": [
                    "make test-files TESTFILES=tests/unit/test_example.py"
                ],
                "commit_message": "fix(self-improve): complete retained shard",
            }
        )
    )


def _plan() -> PromptPlan:
    common = (
        "Immutable task identity and complete Codex file/test/Make contract.\n"
        "Global immutable Codex reference paths: src/one.py, src/two.py\n"
    )
    return PromptPlan(
        shards=(
            PromptShard(
                focus_paths=("src/one.py",),
                prompt=common + "Shard-specific contract: edit src/one.py",
            ),
            PromptShard(
                focus_paths=("src/two.py",),
                prompt=common + "Shard-specific contract: edit src/two.py",
            ),
        ),
        source_bytes=2048,
    )


def _v4_plan() -> PromptPlan:
    paths = ("src/one.py", "src/two.py")
    return PromptPlan(
        shards=tuple(
            PromptShard(
                focus_paths=(path,),
                prompt=bind_compact_focus_path("L1|before\n", path),
                editable_ranges=((1, 2),),
            )
            for path in paths
        ),
        source_bytes=14,
        baseline_files=tuple((path, "before\n") for path in paths),
        proposal_protocol="self-improve-compact-proposal-v4",
    )


def test_worker_retains_one_model_for_ordered_common_prefix_shards(
    tmp_path: Path,
) -> None:
    model_path = tmp_path / "model.gguf"
    model_path.write_bytes(b"GGUF")
    exchange = tmp_path / "exchange"
    exchange.mkdir()
    plan = _plan()
    (exchange / "prompt.txt").write_text(
        worker_module.encode_prompt_batch(
            tuple(shard.prompt for shard in plan.shards),
            protocol_digest=plan.protocol_digest,
        ),
        encoding="utf-8",
    )
    factory_calls: list[dict[str, object]] = []
    prompt_calls: list[str] = []

    class FakeChatModel:
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
            messages = kwargs["messages"]
            assert isinstance(messages, list)
            last = messages[-1]
            assert isinstance(last, dict)
            prompt = last["content"]
            assert isinstance(prompt, str)
            prompt_calls.append(prompt)
            proposal = _manifest(plan.shards[len(prompt_calls) - 1].focus_paths[0])
            return {
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": proposal.to_json()},
                    }
                ]
            }

    def model_factory(
        *,
        model_path: str,
        n_ctx: int,
        verbose: bool,
        n_gpu_layers: int = 0,
    ) -> FakeChatModel:
        factory_calls.append(
            {
                "model_path": model_path,
                "n_ctx": n_ctx,
                "verbose": verbose,
                "n_gpu_layers": n_gpu_layers,
            }
        )
        return FakeChatModel()

    def gateway_factory(path: Path) -> LocalProposalGateway:
        return LocalProposalGateway(path, model_factory=model_factory)

    output = worker_module.run_worker(
        exchange,
        model_path,
        gateway_factory=gateway_factory,
    )
    proposals = worker_module.decode_proposal_batch(
        output.read_text(encoding="utf-8"),
        expected_protocol_digest=plan.protocol_digest,
        expected_count=2,
    )

    assert len(factory_calls) == 1
    assert prompt_calls == [shard.prompt for shard in plan.shards]
    assert prompt_calls[0].split("Shard-specific contract:", 1)[0] == (
        prompt_calls[1].split("Shard-specific contract:", 1)[0]
    )
    assert [proposal.edits[0].path for proposal in proposals] == [
        "src/one.py",
        "src/two.py",
    ]
    assert not list(exchange.glob("*.tmp"))
    assert not list(exchange.glob(".*.tmp"))


def test_worker_publishes_compact_v4_span_batch_without_model_owned_paths(
    tmp_path: Path,
) -> None:
    model_path = tmp_path / "model.gguf"
    model_path.write_bytes(b"GGUF")
    exchange = tmp_path / "exchange-v4"
    exchange.mkdir()
    prompts = tuple(
        comparison_prompt
        for comparison_prompt in (
            runner_module.bind_compact_focus_path("L1|before\n", "src/one.py"),
            runner_module.bind_compact_focus_path("L1|before\n", "src/two.py"),
        )
    )
    digest = "a" * 64
    (exchange / "prompt.txt").write_text(
        encode_prompt_batch(prompts, protocol_digest=digest),
        encoding="utf-8",
    )
    contract = ProposalContract(
        baseline_sha="a" * 40,
        task_id="S83.133",
        tests=("tests/unit/test_example.py",),
        make_commands=("make test-files TESTFILES=tests/unit/test_example.py",),
        proposal_protocol="self-improve-compact-proposal-v4",
    )
    contract_path = exchange / "contract.json"
    contract_path.write_text(contract.to_json(), encoding="utf-8")

    class Gateway:
        def __init__(self, _path: Path) -> None:
            pass

        def propose(
            self,
            prompt: str,
            *,
            contract: ProposalContract | None = None,
        ) -> CompactSpanProposal:
            assert contract is not None
            focus = next(
                line.split("=", 1)[1]
                for line in prompt.splitlines()
                if line.startswith("GLUDD_SELF_IMPROVE_FOCUS_PATH=")
            )
            return worker_module._decode_compact_span_proposal(
                '{"e":[{"s":1,"n":1,"z":"after\\n"}]}',
                focus_path=focus,
            )

    output = worker_module.run_worker(
        exchange,
        model_path,
        contract_path=contract_path,
        gateway_factory=Gateway,
    )
    proposals = decode_compact_span_batch(
        output.read_text(encoding="utf-8"),
        expected_protocol_digest=digest,
        expected_count=2,
    )

    assert tuple(proposal.focus_path for proposal in proposals) == (
        "src/one.py",
        "src/two.py",
    )
    assert json.loads(output.read_text(encoding="utf-8"))["protocol"] == (
        "self-improve-local-proposal-batch-v2"
    )
    assert not list(exchange.glob("*.tmp"))
    assert not list(exchange.glob(".*.tmp"))


@pytest.mark.parametrize(
    ("prompts", "digest", "match"),
    [
        ((), "a" * 64, "1..32"),
        ("one prompt", "a" * 64, "1..32"),
        ((" ",), "a" * 64, "1..16384"),
        ((1,), "a" * 64, "1..16384"),
        (("x" * 16_385,), "a" * 64, "1..16384"),
        (("x" * 16_000,) * 17, "a" * 64, "batch exceeds"),
        (("valid",), "bad", "digest"),
    ],
)
def test_prompt_batch_encoder_rejects_every_ambiguous_boundary(
    prompts: Any,
    digest: str,
    match: str,
) -> None:
    with pytest.raises(ValueError, match=match):
        encode_prompt_batch(prompts, protocol_digest=digest)


@pytest.mark.parametrize(
    ("raw", "match"),
    [
        ("GLUDD_SELF_IMPROVE_PROMPT_BATCH_V1\n{", "valid JSON"),
        (
            "GLUDD_SELF_IMPROVE_PROMPT_BATCH_V1\n{}",
            "exactly protocol",
        ),
        (
            "GLUDD_SELF_IMPROVE_PROMPT_BATCH_V1\n"
            + json.dumps(
                {
                    "protocol": "other",
                    "protocol_digest": "a" * 64,
                    "prompts": ["valid"],
                }
            ),
            "unsupported",
        ),
        (
            "GLUDD_SELF_IMPROVE_PROMPT_BATCH_V1\n"
            + json.dumps(
                {
                    "protocol": "self-improve-local-prompt-batch-v1",
                    "protocol_digest": "a" * 64,
                    "prompts": "invalid",
                }
            ),
            "invalid types",
        ),
        (
            "GLUDD_SELF_IMPROVE_PROMPT_BATCH_V1\n"
            + json.dumps(
                {
                    "protocol": "self-improve-local-prompt-batch-v1",
                    "protocol_digest": 7,
                    "prompts": ["valid"],
                }
            ),
            "invalid types",
        ),
    ],
)
def test_prompt_batch_decoder_rejects_protocol_drift(
    raw: str,
    match: str,
) -> None:
    with pytest.raises(ValueError, match=match):
        decode_prompt_batch(raw)


def test_prompt_batch_decoder_preserves_legacy_single_string() -> None:
    assert decode_prompt_batch("legacy exact prompt") == (
        ("legacy exact prompt",),
        None,
    )


@pytest.mark.parametrize(
    ("manifests", "digest", "match"),
    [
        ((), "a" * 64, "1..32"),
        ("manifest", "a" * 64, "1..32"),
        ((object(),), "a" * 64, "1..32"),
        ((_manifest("src/one.py"),), "bad", "digest"),
    ],
)
def test_proposal_batch_encoder_rejects_invalid_manifests(
    manifests: Any,
    digest: str,
    match: str,
) -> None:
    with pytest.raises(ValueError, match=match):
        encode_proposal_batch(manifests, protocol_digest=digest)


@pytest.mark.parametrize("expected_count", [True, 0, 33, 1.5])
def test_proposal_batch_decoder_rejects_invalid_expected_count(
    expected_count: Any,
) -> None:
    with pytest.raises(ValueError, match="expected proposal count"):
        decode_proposal_batch(
            "{}",
            expected_protocol_digest="a" * 64,
            expected_count=expected_count,
        )


@pytest.mark.parametrize(
    ("raw", "expected_digest", "match"),
    [
        ("{}", "bad", "expected proposal protocol digest"),
        ("{", "a" * 64, "valid JSON"),
        ("{}", "a" * 64, "exactly protocol"),
        (
            json.dumps(
                {
                    "protocol": "other",
                    "protocol_digest": "a" * 64,
                    "proposals": [],
                }
            ),
            "a" * 64,
            "unsupported",
        ),
        (
            json.dumps(
                {
                    "protocol": "self-improve-local-proposal-batch-v1",
                    "protocol_digest": "b" * 64,
                    "proposals": [],
                }
            ),
            "a" * 64,
            "identity drifted",
        ),
        (
            json.dumps(
                {
                    "protocol": "self-improve-local-proposal-batch-v1",
                    "protocol_digest": "a" * 64,
                    "proposals": "invalid",
                }
            ),
            "a" * 64,
            "count",
        ),
        (
            json.dumps(
                {
                    "protocol": "self-improve-local-proposal-batch-v1",
                    "protocol_digest": "a" * 64,
                    "proposals": [],
                }
            ),
            "a" * 64,
            "count",
        ),
        (
            json.dumps(
                {
                    "protocol": "self-improve-local-proposal-batch-v1",
                    "protocol_digest": "a" * 64,
                    "proposals": [{}],
                }
            ),
            "a" * 64,
            "missing fields",
        ),
    ],
)
def test_proposal_batch_decoder_rejects_protocol_or_schema_drift(
    raw: str,
    expected_digest: str,
    match: str,
) -> None:
    with pytest.raises(ValueError, match=match):
        decode_proposal_batch(
            raw,
            expected_protocol_digest=expected_digest,
            expected_count=1,
        )


def _encoded_single_edit_batch(
    operation: str,
    old_text: str,
    new_text: str,
) -> tuple[ProposalManifest, ...]:
    manifest = ProposalManifest.from_json(
        json.dumps(
            {
                "schema_version": 1,
                "baseline_sha": "a" * 40,
                "task_id": "S83.133",
                "edits": [
                    {
                        "operation": operation,
                        "path": "src/one.py",
                        "old_text": old_text,
                        "new_text": new_text,
                    }
                ],
                "tests": ["tests/unit/test_example.py"],
                "make_commands": [
                    "make test-files TESTFILES=tests/unit/test_example.py"
                ],
                "commit_message": "fix(self-improve): apply exact replacement",
            }
        )
    )
    encoded = encode_proposal_batch((manifest,), protocol_digest="a" * 64)
    return decode_proposal_batch(
        encoded,
        expected_protocol_digest="a" * 64,
        expected_count=1,
    )


@pytest.mark.parametrize(
    ("operation", "old_text", "new_text", "error_type", "safe_detail"),
    [
        (
            "replace",
            "MODEL_SECRET=do-not-publish",
            "after = 2\\n",
            "edit_replace_precondition",
            "replace old_text must occur exactly once in trusted baseline",
        ),
        (
            "create",
            "",
            "MODEL_SECRET=do-not-publish",
            "edit_create_precondition",
            "create target must be absent in trusted baseline",
        ),
        (
            "delete",
            "MODEL_SECRET=do-not-publish",
            "",
            "edit_delete_precondition",
            "delete old_text must equal the complete trusted baseline file",
        ),
    ],
)
def test_parent_batch_merge_rejects_inapplicable_edit_with_typed_safe_cause(
    operation: str,
    old_text: str,
    new_text: str,
    error_type: str,
    safe_detail: str,
) -> None:
    """A schema-valid worker batch must be applicable to the trusted baseline."""
    decoded = _encoded_single_edit_batch(operation, old_text, new_text)

    with pytest.raises(ValueError, match=safe_detail) as error:
        runner_module.merge_proposal_manifests(
            decoded,
            expected_path_groups=(("src/one.py",),),
            expected_baseline_sha="a" * 40,
            expected_task_id="S83.133",
            expected_tests=("tests/unit/test_example.py",),
            expected_make_commands=(
                "make test-files TESTFILES=tests/unit/test_example.py",
            ),
            expected_baseline_files={"src/one.py": "before = 1\\n"},
        )

    feedback = runner_module._validation_retry_feedback(str(error.value))
    assert feedback == (
        "protocol=self-improve-validation-retry-v3 "
        f"type={error_type} source=parent_validation detail={safe_detail}"
    )
    assert "MODEL_SECRET" not in str(error.value)
    assert "MODEL_SECRET" not in feedback


@pytest.mark.parametrize(
    ("operation", "old_text", "new_text", "baseline"),
    [
        ("replace", "before", "after", "before = 1\\n"),
        ("create", "", "created = True\\n", None),
        ("delete", "before = 1\\n", "", "before = 1\\n"),
    ],
)
def test_parent_batch_merge_accepts_each_exact_baseline_precondition(
    operation: str,
    old_text: str,
    new_text: str,
    baseline: str | None,
) -> None:
    decoded = _encoded_single_edit_batch(operation, old_text, new_text)

    merged = runner_module.merge_proposal_manifests(
        decoded,
        expected_path_groups=(("src/one.py",),),
        expected_baseline_sha="a" * 40,
        expected_task_id="S83.133",
        expected_tests=("tests/unit/test_example.py",),
        expected_make_commands=(
            "make test-files TESTFILES=tests/unit/test_example.py",
        ),
        expected_baseline_files={"src/one.py": baseline},
    )

    assert merged == decoded[0]


class _InProcessOwnedRunner:
    def __init__(
        self,
        gateway_factory: Any,
        *,
        failure: BaseException | None = None,
    ) -> None:
        self.gateway_factory = gateway_factory
        self.failure = failure
        self.calls: list[tuple[str, dict[str, str], int]] = []
        self.exchange_paths: list[Path] = []

    def run_observable(
        self,
        target: str,
        variables: dict[str, str],
        *,
        timeout: int,
    ) -> MakeResult:
        self.calls.append((target, variables, timeout))
        prompt = Path(variables["SELF_IMPROVE_PROMPT_FILE"])
        self.exchange_paths.extend(
            (
                prompt,
                Path(variables["SELF_IMPROVE_PROPOSAL_FILE"]),
                prompt.parent / "contract.json",
            )
        )
        if self.failure is not None:
            raise self.failure
        worker_module.run_worker(
            prompt.parent,
            Path(variables["SELF_IMPROVE_MODEL_PATH"]),
            contract_path=(
                Path(variables["SELF_IMPROVE_CONTRACT_FILE"])
                if "SELF_IMPROVE_CONTRACT_FILE" in variables
                else None
            ),
            gateway_factory=self.gateway_factory,
        )
        return MakeResult(("make", target), 0, "complete", "", 0.1)


def _task_and_reference() -> tuple[TaskSpec, CodexReference]:
    task = TaskSpec(
        task_id="S83.133",
        objective="Fix both exact files.",
        canonical_make_commands=(
            "make test-files TESTFILES=tests/unit/test_example.py",
        ),
    )
    reference = CodexReference(
        baseline_sha="a" * 40,
        reference_sha="b" * 40,
        changed_files=frozenset({"src/one.py", "src/two.py"}),
        test_files=frozenset({"tests/unit/test_example.py"}),
        changed_lines=4,
        elapsed_seconds=1.0,
    )
    return task, reference


def test_parent_runs_one_owned_worker_then_strictly_merges_all_shards(
    tmp_path: Path,
) -> None:
    plan = _plan()
    model_path = tmp_path / "model.gguf"
    model_path.write_bytes(b"GGUF")
    prompts: list[str] = []
    contracts: list[ProposalContract] = []

    class Gateway:
        def __init__(self, _path: Path) -> None:
            pass

        def propose(
            self,
            prompt: str,
            *,
            contract: ProposalContract | None = None,
        ) -> ProposalManifest:
            assert contract is not None
            prompts.append(prompt)
            contracts.append(contract)
            return _manifest(plan.shards[len(prompts) - 1].focus_paths[0])

    owned = _InProcessOwnedRunner(Gateway)
    task, reference = _task_and_reference()

    merged = runner_module.generate_local_proposal_plan(
        owned,
        model_path,
        plan,
        task,
        reference,
    )

    assert len(owned.calls) == 1
    assert owned.calls[0][0] == "self-improve-local-proposal"
    assert owned.calls[0][2] == 300
    assert prompts == [shard.prompt for shard in plan.shards]
    assert len(contracts) == 2
    assert all(contract.baseline_sha == reference.baseline_sha for contract in contracts)
    assert all(contract.task_id == task.task_id for contract in contracts)
    assert all(contract.tests == ("tests/unit/test_example.py",) for contract in contracts)
    assert {edit.path for edit in merged.edits} == {"src/one.py", "src/two.py"}
    assert all(not path.exists() for path in owned.exchange_paths)


def test_parent_expands_v4_multifile_spans_and_cleans_owned_exchange(
    tmp_path: Path,
) -> None:
    plan = _v4_plan()
    model_path = tmp_path / "model.gguf"
    model_path.write_bytes(b"GGUF")
    contracts: list[ProposalContract] = []

    class Gateway:
        def __init__(self, _path: Path) -> None:
            pass

        def propose(
            self,
            prompt: str,
            *,
            contract: ProposalContract | None = None,
        ) -> CompactSpanProposal:
            assert contract is not None
            contracts.append(contract)
            focus = next(
                line.split("=", 1)[1]
                for line in prompt.splitlines()
                if line.startswith("GLUDD_SELF_IMPROVE_FOCUS_PATH=")
            )
            return worker_module._decode_compact_span_proposal(
                '{"e":[{"s":1,"n":1,"z":"after\\n"}]}',
                focus_path=focus,
            )

    owned = _InProcessOwnedRunner(Gateway)
    task, reference = _task_and_reference()

    generated = runner_module._generate_local_proposal_plan_result(
        owned,
        model_path,
        plan,
        task,
        reference,
    )
    merged = generated.proposal
    public_manifest = runner_module.generate_local_proposal_plan(
        owned,
        model_path,
        plan,
        task,
        reference,
    )

    assert type(public_manifest) is ProposalManifest
    assert public_manifest == merged
    assert {edit.path for edit in merged.edits} == {"src/one.py", "src/two.py"}
    assert all(edit.old_text == "before\n" for edit in merged.edits)
    assert all(edit.new_text == "after\n" for edit in merged.edits)
    assert contracts and all(
        contract.proposal_protocol == "self-improve-compact-proposal-v4"
        for contract in contracts
    )
    assert tuple(item.focus_path for item in generated.compact_proposals) == (
        "src/one.py",
        "src/two.py",
    )
    assert all(item.edits[0].new_text == "after\n" for item in generated.compact_proposals)
    assert all(not path.exists() for path in owned.exchange_paths)


def test_parent_carries_repair_sampling_profile_through_owned_worker(
    tmp_path: Path,
) -> None:
    """Bind the parent-selected repair profile into every shard contract."""
    plan = replace(
        _v4_plan(),
        sampling_profile=COMPACT_V4_SYNTAX_REPAIR_SAMPLING_PROFILE_ID,
    )
    model_path = tmp_path / "model.gguf"
    model_path.write_bytes(b"GGUF")
    contracts: list[ProposalContract] = []

    class Gateway:
        def __init__(self, _path: Path) -> None:
            pass

        def propose(
            self,
            prompt: str,
            *,
            contract: ProposalContract | None = None,
        ) -> CompactSpanProposal:
            assert contract is not None
            contracts.append(contract)
            focus = next(
                line.split("=", 1)[1]
                for line in prompt.splitlines()
                if line.startswith("GLUDD_SELF_IMPROVE_FOCUS_PATH=")
            )
            return worker_module._decode_compact_span_proposal(
                '{"e":[{"s":1,"n":1,"z":"after\\n"}]}',
                focus_path=focus,
            )

    owned = _InProcessOwnedRunner(Gateway)
    task, reference = _task_and_reference()

    runner_module._generate_local_proposal_plan_result(
        owned,
        model_path,
        plan,
        task,
        reference,
    )

    assert contracts and all(
        contract.sampling_profile == COMPACT_V4_SYNTAX_REPAIR_SAMPLING_PROFILE_ID
        for contract in contracts
    )
    assert all(not path.exists() for path in owned.exchange_paths)


def test_parent_selects_first_syntax_valid_repair_candidate(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Reject a compact-valid syntax failure, then select the next bounded seed."""
    plan = replace(
        _v4_plan(),
        sampling_profile=COMPACT_V4_SYNTAX_REPAIR_SAMPLING_PROFILE_ID,
    )
    model_path = tmp_path / "model.gguf"
    model_path.write_bytes(b"GGUF")
    contracts: list[ProposalContract] = []
    candidate_prompts: list[tuple[int, str]] = []

    class Gateway:
        def __init__(self, _path: Path) -> None:
            pass

        def propose(
            self,
            prompt: str,
            *,
            contract: ProposalContract | None = None,
        ) -> CompactSpanProposal:
            assert contract is not None
            contracts.append(contract)
            candidate_prompts.append((contract.sampling_candidate_index, prompt))
            focus = next(
                line.split("=", 1)[1]
                for line in prompt.splitlines()
                if line.startswith("GLUDD_SELF_IMPROVE_FOCUS_PATH=")
            )
            replacement = (
                "if True print('rejected-secret')\n"
                if contract.sampling_candidate_index == 0 and focus == "src/one.py"
                else "after = 1\n"
            )
            return worker_module._decode_compact_span_proposal(
                json.dumps({"e": [{"s": 1, "n": 1, "z": replacement}]}),
                focus_path=focus,
            )

    owned = _InProcessOwnedRunner(Gateway)
    task, reference = _task_and_reference()

    generated = runner_module._generate_local_proposal_plan_result(
        owned,
        model_path,
        plan,
        task,
        reference,
    )

    assert len(owned.calls) == 3
    assert [contract.sampling_candidate_index for contract in contracts] == [0, 0, 1]
    assert len({contract.sampling_seed for contract in contracts}) == 3
    assert len({contract.sampling_context_sha256 for contract in contracts}) == 3
    second_candidate_prompts = [
        prompt for index, prompt in candidate_prompts if index == 1
    ]
    assert second_candidate_prompts
    assert len(second_candidate_prompts) == 1
    assert "GLUDD_SELF_IMPROVE_FOCUS_PATH=src/one.py" in second_candidate_prompts[0]
    assert "src/two.py" not in second_candidate_prompts[0]
    assert all("rejected-secret" in prompt for prompt in second_candidate_prompts)
    assert all("\"category\":\"python_syntax\"" in prompt for prompt in second_candidate_prompts)
    assert all("\"line\":1" in prompt for prompt in second_candidate_prompts)
    assert all("\"path_sha256\":" in prompt for prompt in second_candidate_prompts)
    assert all(edit.new_text == "after = 1\n" for edit in generated.proposal.edits)
    output = capsys.readouterr().out
    assert "candidate=1/3 result=syntax_rejected" in output
    assert "failing_shards=1 frozen_shards=1" in output
    assert "candidate=2/3 result=selected" in output
    assert "type=python_syntax" in output
    assert "rejected-secret" not in output
    assert all(not path.exists() for path in owned.exchange_paths)


def test_parent_freezes_syntax_valid_shard_from_rejected_initial_batch(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Carry valid initial compact material into repair without decoding it again."""
    initial = _v4_plan()
    rejected = (
        worker_module._decode_compact_span_proposal(
            '{"e":[{"s":1,"n":1,"z":"after = 1\\n"}]}',
            focus_path="src/one.py",
        ),
        worker_module._decode_compact_span_proposal(
            '{"e":[{"s":1,"n":1,"z":"if True print(1)\\n"}]}',
            focus_path="src/two.py",
        ),
    )
    diagnostic = runner_module._repair_candidate_syntax_diagnosis(
        runner_module._syntax_diagnostic(
            "src/two.py",
            failure_type="python_syntax",
            line=1,
            column=9,
        )
    )
    plan = runner_module.build_syntax_repair_prompt_plan(
        initial,
        rejected,
        diagnostic,
    )
    model_path = tmp_path / "model.gguf"
    model_path.write_bytes(b"GGUF")
    calls: list[str] = []

    class Gateway:
        def __init__(self, _path: Path) -> None:
            pass

        def propose(
            self,
            prompt: str,
            *,
            contract: ProposalContract | None = None,
        ) -> CompactSpanProposal:
            assert contract is not None
            focus = next(
                line.split("=", 1)[1]
                for line in prompt.splitlines()
                if line.startswith("GLUDD_SELF_IMPROVE_FOCUS_PATH=")
            )
            calls.append(focus)
            return worker_module._decode_compact_span_proposal(
                '{"e":[{"s":1,"n":1,"z":"after = 2\\n"}]}',
                focus_path=focus,
            )

    owned = _InProcessOwnedRunner(Gateway)
    task, reference = _task_and_reference()

    generated = runner_module._generate_local_proposal_plan_result(
        owned,
        model_path,
        plan,
        task,
        reference,
    )

    assert plan.repair_proposals == rejected
    assert calls == ["src/two.py"]
    assert [edit.new_text for edit in generated.proposal.edits] == [
        "after = 1\n",
        "after = 2\n",
    ]
    output = capsys.readouterr().out
    assert "result=selected failing_shards=0 frozen_shards=2" in output
    source_sha = hashlib.sha256(b"src/one.py").hexdigest()
    test_sha = hashlib.sha256(b"src/two.py").hexdigest()
    assert (
        "SELF_IMPROVE_REPAIR_SHARD_STATE candidate=initial "
        f"path_sha256={source_sha} state=frozen category=none line=0 column=0"
        in output
    )
    assert (
        "SELF_IMPROVE_REPAIR_SHARD_STATE candidate=initial "
        f"path_sha256={test_sha} state=syntax_rejected "
        "category=python_syntax line=1 column=9"
        in output
    )
    shard_events = "\n".join(
        line
        for line in output.splitlines()
        if line.startswith("SELF_IMPROVE_REPAIR_SHARD_STATE ")
    )
    assert "src/one.py" not in shard_events
    assert "src/two.py" not in shard_events
    assert all(not path.exists() for path in owned.exchange_paths)


def test_parent_rejects_syntax_diagnosis_hash_mapped_to_wrong_shard(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bind every parser result to its owning immutable shard before feedback."""
    plan = replace(
        _v4_plan(),
        sampling_profile=COMPACT_V4_SYNTAX_REPAIR_SAMPLING_PROFILE_ID,
    )
    model_path = tmp_path / "model.gguf"
    model_path.write_bytes(b"GGUF")

    class Gateway:
        def __init__(self, _path: Path) -> None:
            pass

        def propose(
            self,
            prompt: str,
            *,
            contract: ProposalContract | None = None,
        ) -> CompactSpanProposal:
            assert contract is not None
            focus = next(
                line.split("=", 1)[1]
                for line in prompt.splitlines()
                if line.startswith("GLUDD_SELF_IMPROVE_FOCUS_PATH=")
            )
            return worker_module._decode_compact_span_proposal(
                '{"e":[{"s":1,"n":1,"z":"if True print(1)\\n"}]}',
                focus_path=focus,
            )

    def wrong_path_diagnosis(proposal: ProposalManifest) -> dict[str, str]:
        owning_path = proposal.edits[0].path
        other_path = "src/two.py" if owning_path == "src/one.py" else "src/one.py"
        return {
            owning_path: runner_module._syntax_diagnostic(
                other_path,
                failure_type="python_syntax",
                line=1,
                column=9,
            )
        }

    monkeypatch.setattr(
        runner_module,
        "_proposal_python_syntax_diagnostics",
        wrong_path_diagnosis,
    )
    task, reference = _task_and_reference()

    with pytest.raises(
        RuntimeError,
        match="repair syntax diagnosis does not match its owning shard",
    ):
        runner_module._generate_local_proposal_plan_result(
            _InProcessOwnedRunner(Gateway),
            model_path,
            plan,
            task,
            reference,
        )


def test_syntax_line_provenance_maps_shifted_output_to_one_compact_span() -> None:
    """Map parser lines after earlier expansion to the exact authored span."""
    path = "src/example.py"
    baseline = "one = 1\ntwo = 2\nthree = 3\nfour = 4\nfive = 5\n"
    proposal = worker_module._decode_compact_span_proposal(
        json.dumps(
            {
                "e": [
                    {"s": 2, "n": 1, "z": "two_a = 2\ntwo_b = 2\n"},
                    {"s": 4, "n": 1, "z": "if True print(4)\n"},
                ]
            }
        ),
        focus_path=path,
    )
    diagnostic = runner_module._syntax_diagnostic(
        path,
        failure_type="python_syntax",
        line=5,
        column=9,
    )

    assert (
        runner_module._compact_v4_syntax_owning_span_index(
            baseline,
            proposal,
            diagnostic,
        )
        == 1
    )
    untouched_diagnostic = runner_module._syntax_diagnostic(
        path,
        failure_type="python_syntax",
        line=4,
        column=1,
    )
    assert (
        runner_module._compact_v4_syntax_owning_span_index(
            baseline,
            proposal,
            untouched_diagnostic,
        )
        is None
    )


def test_parent_repairs_only_unique_syntax_owning_span_and_freezes_sibling_spans(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Preserve valid spans while regenerating one provenance-owned syntax span."""
    path = "src/one.py"
    baseline = "keep = 0\nvalue = 0\nuntouched = 0\n"
    initial = PromptPlan(
        shards=(
            PromptShard(
                focus_paths=(path,),
                prompt=bind_compact_focus_path(
                    "L1|keep = 0\nL2|value = 0\nL3|untouched = 0\n",
                    path,
                ),
                editable_ranges=((1, 4),),
            ),
        ),
        source_bytes=len(baseline),
        baseline_files=((path, baseline),),
        proposal_protocol="self-improve-compact-proposal-v4",
    )
    rejected = (
        worker_module._decode_compact_span_proposal(
            json.dumps(
                {
                    "e": [
                        {"s": 1, "n": 1, "z": "keep = 1\n"},
                        {"s": 2, "n": 1, "z": "if True print(2)\n"},
                    ]
                }
            ),
            focus_path=path,
        ),
    )
    plan = runner_module.build_syntax_repair_prompt_plan(
        initial,
        rejected,
        runner_module._repair_candidate_syntax_diagnosis(
            runner_module._syntax_diagnostic(
                path,
                failure_type="python_syntax",
                line=2,
                column=9,
            )
        ),
    )
    model_path = tmp_path / "model.gguf"
    model_path.write_bytes(b"GGUF")
    prompts: list[str] = []

    class Gateway:
        def __init__(self, _path: Path) -> None:
            pass

        def propose(
            self,
            prompt: str,
            *,
            contract: ProposalContract | None = None,
        ) -> CompactSpanProposal:
            assert contract is not None
            prompts.append(prompt)
            return worker_module._decode_compact_span_proposal(
                '{"e":[{"s":2,"n":1,"z":"value = 2\\n"}]}',
                focus_path=path,
            )

    task, reference = _task_and_reference()
    generated = runner_module._generate_local_proposal_plan_result(
        _InProcessOwnedRunner(Gateway),
        model_path,
        plan,
        task,
        reference,
    )

    assert len(prompts) == 1
    assert "Repair exactly one compact span: s=2, n=1." in prompts[0]
    assert "The parent froze 1 non-owning span in this shard" in prompts[0]
    assert "TARGET_REPAIR_CONTEXT_BEGIN" in prompts[0]
    assert "Only the exact target s/n is editable; surrounding lines are context" in (
        prompts[0]
    )
    assert [
        (edit.start_line, edit.old_line_count, edit.new_text)
        for edit in generated.compact_proposals[0].edits
    ] == [
        (1, 1, "keep = 1\n"),
        (2, 1, "value = 2\n"),
    ]
    assert generated.proposal.edits[0].new_text == (
        "keep = 1\nvalue = 2\nuntouched = 0\n"
    )
    output = capsys.readouterr().out
    assert "state=span_targeted category=python_syntax line=2 column=9 target_s=2 target_n=1" in output
    assert "keep = 1" not in output


def test_parent_stops_after_three_syntax_invalid_repair_candidates(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Bound repair decodes and clean every exchange when no candidate parses."""
    plan = replace(
        _v4_plan(),
        sampling_profile=COMPACT_V4_SYNTAX_REPAIR_SAMPLING_PROFILE_ID,
    )
    model_path = tmp_path / "model.gguf"
    model_path.write_bytes(b"GGUF")
    contracts: list[ProposalContract] = []

    class Gateway:
        def __init__(self, _path: Path) -> None:
            pass

        def propose(
            self,
            prompt: str,
            *,
            contract: ProposalContract | None = None,
        ) -> CompactSpanProposal:
            assert contract is not None
            contracts.append(contract)
            focus = next(
                line.split("=", 1)[1]
                for line in prompt.splitlines()
                if line.startswith("GLUDD_SELF_IMPROVE_FOCUS_PATH=")
            )
            return worker_module._decode_compact_span_proposal(
                '{"e":[{"s":1,"n":1,"z":"def broken(\\n"}]}',
                focus_path=focus,
            )

    owned = _InProcessOwnedRunner(Gateway)
    task, reference = _task_and_reference()

    with pytest.raises(
        ValueError,
        match="compact-v4 syntax repair exhausted 3 bounded candidates",
    ):
        runner_module._generate_local_proposal_plan_result(
            owned,
            model_path,
            plan,
            task,
            reference,
        )

    assert len(owned.calls) == 6
    assert [contract.sampling_candidate_index for contract in contracts] == [
        0,
        0,
        1,
        1,
        2,
        2,
    ]
    assert len({contract.sampling_seed for contract in contracts}) == 6
    assert capsys.readouterr().out.count("result=syntax_rejected") == 3
    assert all(not path.exists() for path in owned.exchange_paths)


def test_parent_advances_after_one_compact_invalid_repair_candidate(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Treat model-owned compact rejection as one candidate, never a new attempt."""
    plan = replace(
        _v4_plan(),
        sampling_profile=COMPACT_V4_SYNTAX_REPAIR_SAMPLING_PROFILE_ID,
    )
    model_path = tmp_path / "model.gguf"
    model_path.write_bytes(b"GGUF")
    seen_indices: list[int] = []

    class Gateway:
        def __init__(self, _path: Path) -> None:
            pass

        def propose(
            self,
            prompt: str,
            *,
            contract: ProposalContract | None = None,
        ) -> CompactSpanProposal:
            assert contract is not None
            seen_indices.append(contract.sampling_candidate_index)
            focus = next(
                line.split("=", 1)[1]
                for line in prompt.splitlines()
                if line.startswith("GLUDD_SELF_IMPROVE_FOCUS_PATH=")
            )
            if (
                contract.sampling_candidate_index == 0
                and focus == "src/one.py"
            ):
                raise ValueError("compact span must change content")
            return worker_module._decode_compact_span_proposal(
                '{"e":[{"s":1,"n":1,"z":"after = 1\\n"}]}',
                focus_path=focus,
            )

    owned = _InProcessOwnedRunner(Gateway)
    task, reference = _task_and_reference()

    generated = runner_module._generate_local_proposal_plan_result(
        owned,
        model_path,
        plan,
        task,
        reference,
    )

    assert len(owned.calls) == 3
    assert seen_indices == [0, 0, 1]
    assert generated.proposal.edits
    output = capsys.readouterr().out
    assert "candidate=1/3 result=proposal_rejected" in output
    assert "compact span must change content" not in output
    assert all(not path.exists() for path in owned.exchange_paths)


def test_parent_keeps_frozen_shard_across_compact_invalid_repair_candidate(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A later malformed shard must not discard an earlier syntax-valid shard."""
    plan = replace(
        _v4_plan(),
        sampling_profile=COMPACT_V4_SYNTAX_REPAIR_SAMPLING_PROFILE_ID,
    )
    model_path = tmp_path / "model.gguf"
    model_path.write_bytes(b"GGUF")
    calls: list[tuple[int, str]] = []

    class Gateway:
        def __init__(self, _path: Path) -> None:
            pass

        def propose(
            self,
            prompt: str,
            *,
            contract: ProposalContract | None = None,
        ) -> CompactSpanProposal:
            assert contract is not None
            focus = next(
                line.split("=", 1)[1]
                for line in prompt.splitlines()
                if line.startswith("GLUDD_SELF_IMPROVE_FOCUS_PATH=")
            )
            calls.append((contract.sampling_candidate_index, focus))
            if contract.sampling_candidate_index == 1:
                raise ValueError("compact span must change content")
            replacement = (
                "after = 1\n"
                if focus == "src/one.py"
                else (
                    "if True print('hidden-draft')\n"
                    if contract.sampling_candidate_index == 0
                    else "after = 2\n"
                )
            )
            return worker_module._decode_compact_span_proposal(
                json.dumps({"e": [{"s": 1, "n": 1, "z": replacement}]}),
                focus_path=focus,
            )

    owned = _InProcessOwnedRunner(Gateway)
    task, reference = _task_and_reference()

    generated = runner_module._generate_local_proposal_plan_result(
        owned,
        model_path,
        plan,
        task,
        reference,
    )

    assert calls == [
        (0, "src/one.py"),
        (0, "src/two.py"),
        (1, "src/two.py"),
        (2, "src/two.py"),
    ]
    assert [edit.new_text for edit in generated.proposal.edits] == [
        "after = 1\n",
        "after = 2\n",
    ]
    output = capsys.readouterr().out
    assert "candidate=2/3 result=proposal_rejected" in output
    assert "hidden-draft" not in output
    assert all(not path.exists() for path in owned.exchange_paths)


def test_repair_worker_atomic_failure_does_not_discard_prior_valid_shard(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Publish repair shards separately so a later decode failure cannot erase one."""
    plan = replace(
        _v4_plan(),
        sampling_profile=COMPACT_V4_SYNTAX_REPAIR_SAMPLING_PROFILE_ID,
    )
    model_path = tmp_path / "model.gguf"
    model_path.write_bytes(b"GGUF")
    calls: list[tuple[int, str]] = []

    class Gateway:
        def __init__(self, _path: Path) -> None:
            pass

        def propose(
            self,
            prompt: str,
            *,
            contract: ProposalContract | None = None,
        ) -> CompactSpanProposal:
            assert contract is not None
            focus = next(
                line.split("=", 1)[1]
                for line in prompt.splitlines()
                if line.startswith("GLUDD_SELF_IMPROVE_FOCUS_PATH=")
            )
            calls.append((contract.sampling_candidate_index, focus))
            if (
                contract.sampling_candidate_index == 0
                and focus == "src/two.py"
            ):
                raise ValueError("compact span must change content")
            replacement = "after = 1\n" if focus == "src/one.py" else "after = 2\n"
            return worker_module._decode_compact_span_proposal(
                json.dumps({"e": [{"s": 1, "n": 1, "z": replacement}]}),
                focus_path=focus,
            )

    owned = _InProcessOwnedRunner(Gateway)
    task, reference = _task_and_reference()

    generated = runner_module._generate_local_proposal_plan_result(
        owned,
        model_path,
        plan,
        task,
        reference,
    )

    assert calls == [
        (0, "src/one.py"),
        (0, "src/two.py"),
        (1, "src/two.py"),
    ]
    assert len(owned.calls) == 3
    assert [edit.new_text for edit in generated.proposal.edits] == [
        "after = 1\n",
        "after = 2\n",
    ]
    output = capsys.readouterr().out
    assert "candidate=1/3 result=proposal_rejected" in output
    assert "failing_shards=1 frozen_shards=1" in output
    assert all(not path.exists() for path in owned.exchange_paths)


def test_repair_sampling_crosses_parent_cli_and_real_gateway_boundary(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Prove explicit parent transport reaches the real gateway sampler call."""
    plan = replace(
        _v4_plan(),
        sampling_profile=COMPACT_V4_SYNTAX_REPAIR_SAMPLING_PROFILE_ID,
    )
    model_path = tmp_path / "model.gguf"
    model_path.write_bytes(b"GGUF")
    chat_calls: list[dict[str, object]] = []

    class FakeChatModel:
        def __call__(self, *_args: object, **_kwargs: object) -> object:
            raise AssertionError("compact mode must use chat completion")

        def create_chat_completion(self, **kwargs: object) -> dict[str, object]:
            chat_calls.append(kwargs)
            content = (
                '{"ok":true}'
                if kwargs.get("max_tokens") == 32
                else '{"e":[{"s":1,"n":1,"z":"after\\n"}]}'
            )
            return {
                "choices": [
                    {"finish_reason": "stop", "message": {"content": content}}
                ]
            }

    model = FakeChatModel()

    def gateway_factory(path: Path) -> LocalProposalGateway:
        return LocalProposalGateway(
            path,
            model_factory=lambda **_kwargs: model,
            grammar_factory=lambda _schema: object(),
        )

    class CliBoundaryRunner:
        def __init__(self) -> None:
            self.contract_paths: list[Path] = []

        def run_observable(
            self,
            target: str,
            variables: dict[str, str],
            *,
            timeout: int,
        ) -> MakeResult:
            assert target == "self-improve-local-proposal"
            assert timeout == 300
            contract_path = Path(variables["SELF_IMPROVE_CONTRACT_FILE"])
            self.contract_paths.append(contract_path)
            returncode = worker_module.main(
                [
                    "--model-path",
                    variables["SELF_IMPROVE_MODEL_PATH"],
                    "--prompt-file",
                    variables["SELF_IMPROVE_PROMPT_FILE"],
                    "--proposal-file",
                    variables["SELF_IMPROVE_PROPOSAL_FILE"],
                    "--contract-file",
                    str(contract_path),
                ],
                gateway_factory=gateway_factory,
            )
            return MakeResult(("make", target), returncode, "", "", 0.1)

    owned = CliBoundaryRunner()
    task, reference = _task_and_reference()
    generated = runner_module._generate_local_proposal_plan_result(
        owned,
        model_path,
        plan,
        task,
        reference,
    )

    assert generated.proposal.edits
    proposal_calls = [call for call in chat_calls if call.get("max_tokens") == 4096]
    assert len(proposal_calls) == len(plan.shards)
    assert all(call["temperature"] == 0.8 for call in proposal_calls)
    assert all(call["top_p"] == 0.95 for call in proposal_calls)
    assert all(call["top_k"] == 40 for call in proposal_calls)
    output = capsys.readouterr().out
    sampling_events = re.findall(
        "profile="
        + re.escape(COMPACT_V4_SYNTAX_REPAIR_SAMPLING_PROFILE_ID)
        + r" candidate=1/3 seed=([1-9][0-9]{0,9}) "
        + r"context_sha256=([0-9a-f]{64}) "
        + r"repair_state_sha256=([0-9a-f]{64})(?:\s|$)",
        output,
    )
    assert len(sampling_events) == len(plan.shards)
    assert [call["seed"] for call in proposal_calls] == [
        int(seed) for seed, _context, _state in sampling_events
    ]
    assert len({context for _seed, context, _state in sampling_events}) == len(
        plan.shards
    )
    assert len({state for _seed, _context, state in sampling_events}) == len(plan.shards)
    assert re.search(r"output_sha256=[0-9a-f]{64}(?:\s|$)", output)
    assert owned.contract_paths and all(
        not path.exists() for path in owned.contract_paths
    )


def test_parent_scope_rejection_cleans_owned_exchange(tmp_path: Path) -> None:
    """Clean every request artifact after a typed hidden-gap insertion rejection."""
    path = "src/one.py"
    baseline = "shown-a\nhidden-b\nhidden-c\nshown-d\n"
    prompt = bind_compact_focus_path(
        "Use only the numbered source lines.",
        path,
        editable_ranges=((1, 2), (4, 5)),
    )
    plan = PromptPlan(
        shards=(PromptShard((path,), prompt, ((1, 2), (4, 5))),),
        source_bytes=len(baseline.encode()),
        baseline_files=((path, baseline),),
        proposal_protocol="self-improve-compact-proposal-v4",
    )
    model_path = tmp_path / "model.gguf"
    model_path.write_bytes(b"GGUF")

    class Gateway:
        def __init__(self, _path: Path) -> None:
            pass

        def propose(
            self,
            prompt: str,
            *,
            contract: ProposalContract | None = None,
        ) -> CompactSpanProposal:
            assert contract is not None
            focus = next(
                line.split("=", 1)[1]
                for line in prompt.splitlines()
                if line.startswith("GLUDD_SELF_IMPROVE_FOCUS_PATH=")
            )
            return worker_module._decode_compact_span_proposal(
                '{"e":[{"s":3,"n":0,"z":"PASSWORD=hunter2\\n"}]}',
                focus_path=focus,
            )

    owned = _InProcessOwnedRunner(Gateway)
    task = TaskSpec(
        task_id="S83.133",
        objective="Reject a hidden-gap insertion.",
        canonical_make_commands=(
            "make test-files TESTFILES=tests/unit/test_example.py",
        ),
    )
    reference = CodexReference(
        baseline_sha="a" * 40,
        reference_sha="b" * 40,
        changed_files=frozenset({path}),
        test_files=frozenset({"tests/unit/test_example.py"}),
        changed_lines=1,
        elapsed_seconds=1.0,
    )

    with pytest.raises(ValueError, match="first shown line through one past") as error:
        runner_module.generate_local_proposal_plan(
            owned,
            model_path,
            plan,
            task,
            reference,
        )

    assert "PASSWORD" not in str(error.value)
    assert owned.exchange_paths
    assert all(not artifact.exists() for artifact in owned.exchange_paths)


def test_parent_rejects_batch_scope_drift_after_worker_schema_validation(
    tmp_path: Path,
) -> None:
    plan = _plan()
    model_path = tmp_path / "model.gguf"
    model_path.write_bytes(b"GGUF")
    count = 0

    class Gateway:
        def __init__(self, _path: Path) -> None:
            pass

        def propose(
            self,
            _prompt: str,
            *,
            contract: ProposalContract | None = None,
        ) -> ProposalManifest:
            nonlocal count
            assert contract is not None
            count += 1
            return _manifest("src/one.py" if count == 1 else "src/unexpected.py")

    owned = _InProcessOwnedRunner(Gateway)
    task, reference = _task_and_reference()

    with pytest.raises(ValueError, match="exact focus paths"):
        runner_module.generate_local_proposal_plan(
            owned,
            model_path,
            plan,
            task,
            reference,
        )

    assert len(owned.calls) == 1
    assert all(not path.exists() for path in owned.exchange_paths)


@pytest.mark.parametrize(
    "failure",
    [OSError("start failed"), RuntimeError("body failed"), KeyboardInterrupt()],
    ids=("start", "body", "cancel"),
)
def test_parent_cleans_exchange_for_start_body_and_cancel_failure(
    tmp_path: Path,
    failure: BaseException,
) -> None:
    model_path = tmp_path / "model.gguf"
    model_path.write_bytes(b"GGUF")
    owned = _InProcessOwnedRunner(lambda _path: None, failure=failure)

    with pytest.raises(type(failure)):
        runner_module.generate_local_proposal(
            owned,
            model_path,
            "legacy single-string proposal",
        )

    assert all(not path.exists() for path in owned.exchange_paths)


def test_worker_failure_publishes_no_partial_batch_or_stale_temp(
    tmp_path: Path,
) -> None:
    model_path = tmp_path / "model.gguf"
    model_path.write_bytes(b"GGUF")
    exchange = tmp_path / "exchange"
    exchange.mkdir()
    plan = _plan()
    (exchange / "prompt.txt").write_text(
        worker_module.encode_prompt_batch(
            tuple(shard.prompt for shard in plan.shards),
            protocol_digest=plan.protocol_digest,
        ),
        encoding="utf-8",
    )
    count = 0

    class Gateway:
        def __init__(self, _path: Path) -> None:
            pass

        def propose(
            self,
            _prompt: str,
            *,
            contract: ProposalContract | None = None,
        ) -> ProposalManifest:
            del contract
            nonlocal count
            count += 1
            if count == 2:
                raise ValueError("second shard failed")
            return _manifest("src/one.py")

    with pytest.raises(ValueError, match="second shard failed"):
        worker_module.run_worker(
            exchange,
            model_path,
            gateway_factory=Gateway,
        )

    assert not (exchange / "proposal.json").exists()
    assert not list(exchange.glob("*.tmp"))
    assert not list(exchange.glob(".*.tmp"))


def test_worker_cleans_live_deepseek_v4_framing_failure(
    tmp_path: Path,
) -> None:
    """Publish no shard batch when strict v4 JSON decoding rejects live framing."""
    model_path = tmp_path / "model.gguf"
    model_path.write_bytes(b"GGUF")
    exchange = tmp_path / "exchange-v4-invalid-json"
    exchange.mkdir()
    plan = _v4_plan()
    (exchange / "prompt.txt").write_text(
        encode_prompt_batch(
            tuple(shard.prompt for shard in plan.shards),
            protocol_digest=plan.protocol_digest,
        ),
        encoding="utf-8",
    )
    contract = ProposalContract(
        baseline_sha="a" * 40,
        task_id="S83.133",
        tests=("tests/unit/test_example.py",),
        make_commands=("make test-files TESTFILES=tests/unit/test_example.py",),
        proposal_protocol="self-improve-compact-proposal-v4",
    )
    contract_path = exchange / "contract.json"
    contract_path.write_text(contract.to_json(), encoding="utf-8")
    sensitive = (
        "Reasoning TOKEN=do-not-publish\n"
        '{"e":[{"s":1,"n":1,"z":"PASSWORD=hunter2"}]}\n'
    )
    raw = sensitive + ("x" * (2308 - len(sensitive.encode("utf-8"))))

    class Gateway:
        def __init__(self, _path: Path) -> None:
            pass

        def propose(
            self,
            prompt: str,
            *,
            contract: ProposalContract | None = None,
        ) -> CompactSpanProposal:
            assert contract is not None
            focus = next(
                line.split("=", 1)[1]
                for line in prompt.splitlines()
                if line.startswith("GLUDD_SELF_IMPROVE_FOCUS_PATH=")
            )
            return worker_module._decode_compact_span_proposal(raw, focus_path=focus)

    with pytest.raises(
        ValueError,
        match=r"compact-v4 proposal is not one complete JSON object; output_bytes=2308",
    ) as error:
        worker_module.run_worker(
            exchange,
            model_path,
            contract_path=contract_path,
            gateway_factory=Gateway,
        )

    assert all(secret not in str(error.value) for secret in ("TOKEN", "PASSWORD", "hunter2"))
    assert not (exchange / "proposal.json").exists()
    assert not list(exchange.glob("*.tmp"))
    assert not list(exchange.glob(".*.tmp"))


def test_worker_removes_temporary_output_when_fsync_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model_path = tmp_path / "model.gguf"
    model_path.write_bytes(b"GGUF")
    exchange = tmp_path / "exchange"
    exchange.mkdir()
    (exchange / "prompt.txt").write_text("legacy request", encoding="utf-8")

    class Gateway:
        def __init__(self, _path: Path) -> None:
            pass

        def propose(
            self,
            _prompt: str,
            *,
            contract: ProposalContract | None = None,
        ) -> ProposalManifest:
            del contract
            return _manifest("src/one.py")

    def fail_fsync(_descriptor: int) -> None:
        raise OSError("fsync failed")

    monkeypatch.setattr(os, "fsync", fail_fsync)

    with pytest.raises(OSError, match="fsync failed"):
        worker_module.run_worker(
            exchange,
            model_path,
            gateway_factory=Gateway,
        )

    assert not (exchange / "proposal.json").exists()
    assert not list(exchange.glob("*.tmp"))
    assert not list(exchange.glob(".*.tmp"))


def test_observable_runner_emits_heartbeat_and_enforces_total_deadline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    stream = io.StringIO()

    class Process:
        pid = 7734
        stdout = stream
        returncode: int | None = None

        def poll(self) -> int | None:
            return self.returncode

    process = Process()
    reaped: list[Process] = []

    class Selector:
        def register(self, _stream: object, _events: object) -> None:
            pass

        def select(self, *, timeout: float) -> list[object]:
            del timeout
            return []

        def close(self) -> None:
            pass

    moments = iter((0.0, 0.0, 16.0, 301.0, 302.0))
    monkeypatch.setattr(time, "monotonic", lambda: next(moments))
    monkeypatch.setattr(
        subprocess,
        "Popen",
        lambda *args, **kwargs: process,
    )
    monkeypatch.setattr(selectors, "DefaultSelector", Selector)
    monkeypatch.setattr(
        runner_module,
        "_terminate_process_group",
        lambda child: reaped.append(child),
    )

    result = runner_module.MakeRunner(tmp_path)._run_observable_argv(
        ["make", "test-count"],
        timeout=300,
    )

    assert result.returncode == 124
    assert result.stderr == "timed out"
    assert result.elapsed_seconds == 302.0
    assert reaped == [process]
    output = capsys.readouterr().out
    assert "SELF_IMPROVE_HEARTBEAT elapsed=16.0s pid=7734" in output
    assert "SELF_IMPROVE_COMMAND_END rc=124" in output


def test_observable_runner_reaps_process_group_when_selector_setup_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stream = io.StringIO()

    class Process:
        pid = 8124
        stdout = stream
        returncode: int | None = None

        def poll(self) -> int | None:
            return self.returncode

    process = Process()
    reaped: list[Process] = []
    selector_closed: list[bool] = []

    class Selector:
        def register(self, _stream: object, _events: object) -> None:
            raise RuntimeError("selector registration failed")

        def close(self) -> None:
            selector_closed.append(True)

    monkeypatch.setattr(
        subprocess,
        "Popen",
        lambda *args, **kwargs: process,
    )
    monkeypatch.setattr(selectors, "DefaultSelector", Selector)
    monkeypatch.setattr(
        runner_module,
        "_terminate_process_group",
        lambda child: reaped.append(child),
    )

    runner = runner_module.MakeRunner(tmp_path)
    with pytest.raises(RuntimeError, match="selector registration failed"):
        runner._run_observable_argv(["make", "test-count"], timeout=300)

    assert reaped == [process]
    assert selector_closed == [True]
    assert stream.closed


@pytest.mark.parametrize(
    "raised",
    [RuntimeError("selector failed"), KeyboardInterrupt()],
    ids=("body", "cancel"),
)
def test_observable_runner_reaps_process_group_on_body_or_cancel(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    raised: BaseException,
) -> None:
    stream = io.StringIO()

    class Process:
        pid = 4421
        stdout = stream
        returncode: int | None = None

        def poll(self) -> int | None:
            return self.returncode

    process = Process()
    reaped: list[Process] = []

    class Selector:
        def register(self, _stream: object, _events: object) -> None:
            pass

        def select(self, *, timeout: float) -> list[object]:
            del timeout
            raise raised

        def close(self) -> None:
            pass

    monkeypatch.setattr(
        subprocess,
        "Popen",
        lambda *args, **kwargs: process,
    )
    monkeypatch.setattr(selectors, "DefaultSelector", Selector)
    monkeypatch.setattr(
        runner_module,
        "_terminate_process_group",
        lambda child: reaped.append(child),
    )

    runner = runner_module.MakeRunner(tmp_path)
    with pytest.raises(type(raised)):
        runner._run_observable_argv(["make", "test-count"], timeout=300)

    assert reaped == [process]
    assert stream.closed
