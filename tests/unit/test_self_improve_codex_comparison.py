"""Contracts for local self-improvement comparison with a Codex reference."""

from __future__ import annotations

import importlib
import json
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
        "LocalProposalGateway",
        "bind_compact_focus_path",
        "build_retry_prompt",
        "compare_with_codex",
        "decode_prompt_batch",
        "decode_proposal_batch",
        "encode_proposal_batch",
        "local_proposal_attempt_identity_digest",
        "merge_proposal_manifests",
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
        ("_COMPACT_PROPOSAL_TOKENS", 1025),
        ("_COMPACT_MAX_CONTENT_BYTES", 3073),
        ("_COMPACT_FOCUS_PATH_MARKER", "CHANGED_FOCUS_PATH="),
        ("_COMPACT_COMMIT_MESSAGE", "fix: changed trusted commit message"),
        ("_STRUCTURED_CANARY_TOKENS", 33),
        ("_DETERMINISTIC_DECODE_SEED", 1),
        ("_DETERMINISTIC_DECODE_TEMPERATURE", 0.1),
        ("_STRUCTURED_OUTPUT_REQUIRE_STOP", False),
        ("_COMPACT_ROOT_FIELDS", frozenset({"c", "e", "unexpected"})),
        (
            "_COMPACT_OPERATION_BY_EMPTY_TEXT",
            {(False, False): "replace", (False, True): "delete"},
        ),
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
    wrong_schema["schema_version"] = 2
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

    assert proposal.commit_message == "fix: local chat proposal"
    assert calls["temperature"] == 0.0
    assert calls["max_tokens"] == 4096
    assert calls["seed"] == 0
    assert "grammar" not in calls
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
    assert "distinct non-empty strings" in comparison_module._COMPACT_SYSTEM_PROMPT

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
        gateway.propose("Repair the example.", contract=contract)

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
        "SELF_IMPROVE_MODEL_PATH",
        "SELF_IMPROVE_BASELINE_REF",
        "SELF_IMPROVE_REFERENCE_REF",
        "SELF_IMPROVE_TASK_FILE",
    ):
        assert token in makefile
        assert token in contract


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
