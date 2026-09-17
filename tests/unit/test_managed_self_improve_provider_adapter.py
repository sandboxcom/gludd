"""Local managed-runner compatibility tests for the provider backend seam."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest

from general_ludd.self_improve.codex_comparison import CodexReference
from general_ludd.self_improve.managed_runner import (
    LocalProposalBackendAdapter,
    LocalProposalInvocation,
    TaskSpec,
)
from general_ludd.self_improve.model_candidates import (
    CandidateBackend,
    LocalGGUFCandidateIdentity,
)


def _identity() -> LocalGGUFCandidateIdentity:
    return LocalGGUFCandidateIdentity(
        model_id="qwen2.5-coder-0.5b",
        repo_id="bartowski/Qwen2.5-Coder-0.5B-Instruct-GGUF",
        filename="Qwen2.5-Coder-0.5B-Instruct-Q4_K_M.gguf",
        revision="a" * 40,
        artifact_sha256="b" * 64,
    )


def _task() -> TaskSpec:
    return TaskSpec(
        task_id="S83.200",
        objective="Preserve the exact local proposal call.",
        canonical_make_commands=("make test-count",),
    )


def _reference() -> CodexReference:
    return CodexReference(
        baseline_sha="c" * 40,
        reference_sha="d" * 40,
        changed_files=frozenset({"src/general_ludd/example.py"}),
        test_files=frozenset({"tests/unit/test_example.py"}),
        changed_lines=1,
        elapsed_seconds=1.0,
    )


def test_local_adapter_is_provider_protocol_and_preserves_exact_legacy_call(
    tmp_path: Path,
) -> None:
    model_path = tmp_path / "model.gguf"
    task = _task()
    reference = _reference()
    prompt = "exact prompt bytes\n"
    sentinel = object()
    observed: list[tuple[Path, str, TaskSpec, CodexReference]] = []

    def legacy_generator(
        path: Path,
        given_prompt: str,
        given_task: TaskSpec,
        given_reference: CodexReference,
    ) -> object:
        observed.append((path, given_prompt, given_task, given_reference))
        return sentinel

    adapter = LocalProposalBackendAdapter(
        _identity(),
        cast(Any, legacy_generator),
    )
    invocation = LocalProposalInvocation(model_path, prompt, task, reference)

    assert isinstance(adapter, CandidateBackend)
    assert adapter.candidate_identity == _identity()
    assert adapter.generate(
        invocation,
        max_output_tokens=1_024,
        timeout_seconds=30.0,
    ) is sentinel
    assert observed == [(model_path, prompt, task, reference)]


def test_local_adapter_preserves_legacy_exception_identity(tmp_path: Path) -> None:
    failure = ValueError("legacy validation category")

    def legacy_generator(*_args: object) -> object:
        raise failure

    adapter = LocalProposalBackendAdapter(
        _identity(),
        cast(Any, legacy_generator),
    )
    invocation = LocalProposalInvocation(
        tmp_path / "model.gguf",
        "prompt",
        _task(),
        _reference(),
    )

    with pytest.raises(ValueError) as captured:
        adapter.generate(
            invocation,
            max_output_tokens=1,
            timeout_seconds=1.0,
        )

    assert captured.value is failure
