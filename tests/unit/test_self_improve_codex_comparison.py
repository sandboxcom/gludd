"""Contracts for local self-improvement comparison with a Codex reference."""

from __future__ import annotations

import importlib
import json
import sys
from dataclasses import replace
from pathlib import Path
from types import ModuleType
from typing import Any, TypedDict, Unpack

import pytest

import general_ludd.self_improve.codex_comparison as comparison_module
from general_ludd.self_improve.codex_comparison import (
    CandidateEvidence,
    CodexReference,
    LocalProposalGateway,
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
        grammar_factory=lambda _schema: object(),
    )

    with pytest.raises(ValueError, match=r"no JSON start.*plain text"):
        gateway.propose("Repair exactly.")


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

    gateway = LocalProposalGateway(model_path, model_factory=factory)
    proposal = gateway.propose("Repair the example.")

    assert proposal.task_id == "S83.133"
    assert calls["factory"] == {
        "model_path": str(model_path),
        "n_ctx": 32768,
        "verbose": False,
    }
    assert calls["decode"] == {
        "max_tokens": 4096,
        "temperature": 0.0,
        "echo": False,
    }


def test_local_gateway_prefers_json_constrained_chat_completion(
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
            return {"choices": [{"message": {"content": proposal_json}}]}

        def __call__(self, prompt: str, **kwargs: object) -> object:
            raise AssertionError("raw completion must not be used when chat is available")

    grammar_inputs: list[str] = []
    grammar = object()

    def grammar_factory(schema_json: str) -> object:
        grammar_inputs.append(schema_json)
        return grammar

    gateway = LocalProposalGateway(
        model_path,
        model_factory=lambda **_kwargs: FakeChatModel(),
        grammar_factory=grammar_factory,
    )
    proposal = gateway.propose("Repair the example.")

    assert proposal.commit_message == "fix: local chat proposal"
    assert calls["temperature"] == 0.0
    assert calls["max_tokens"] == 4096
    assert calls["grammar"] is grammar
    assert "response_format" not in calls
    schema = json.loads(grammar_inputs[0])
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
        grammar_factory=lambda _schema: object(),
    )

    with pytest.raises(ValueError, match="incomplete JSON") as error:
        gateway.propose("Repair exactly.")

    assert "schema_version" in str(error.value)
    assert len(str(error.value).encode("utf-8")) <= 1400


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
        ) -> FakeModel:
            del model_path, n_ctx, verbose
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


def test_default_grammar_factory_requires_runtime_grammar_support(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_module = ModuleType("llama_cpp")

    class FakeGrammar:
        @staticmethod
        def from_json_schema(schema_json: str, *, verbose: bool) -> object:
            return schema_json, verbose

    vars(fake_module)["LlamaGrammar"] = FakeGrammar
    monkeypatch.setitem(sys.modules, "llama_cpp", fake_module)
    assert comparison_module._default_grammar_factory("{}") == ("{}", False)

    vars(fake_module).pop("LlamaGrammar")
    with pytest.raises(RuntimeError, match="does not expose JSON grammar"):
        comparison_module._default_grammar_factory("{}")


def test_optional_llama_runtime_loads_through_dynamic_typed_boundary(
    monkeypatch: pytest.MonkeyPatch,
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
        ) -> None:
            self.settings = model_path, n_ctx, verbose

    class FakeGrammar:
        @staticmethod
        def from_json_schema(schema_json: str, *, verbose: bool) -> object:
            return schema_json, verbose

    vars(fake_module)["Llama"] = FakeModel
    vars(fake_module)["LlamaGrammar"] = FakeGrammar

    def import_runtime(name: str) -> ModuleType:
        imports.append(name)
        return fake_module

    monkeypatch.setattr(importlib, "import_module", import_runtime)
    model = comparison_module._default_model_factory(
        model_path="/tmp/gludd-model.gguf",
        n_ctx=8192,
        verbose=False,
    )
    assert isinstance(model, FakeModel)
    assert model.settings == ("/tmp/gludd-model.gguf", 8192, False)
    assert comparison_module._default_grammar_factory("{}") == ("{}", False)
    assert imports == ["llama_cpp", "llama_cpp"]
