"""Regression tests for deterministic local proposal prompt decomposition."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest

import general_ludd.self_improve.codex_comparison as comparison_module
import general_ludd.self_improve.runtime as runtime_module
from general_ludd.self_improve.codex_comparison import (
    CodexReference,
    ComparisonResult,
    ProposalContract,
    ProposalManifest,
    decode_prompt_batch,
    encode_compact_span_batch,
    local_proposal_attempt_identity_digest,
    merge_proposal_manifests,
)
from general_ludd.self_improve.managed_runner import (
    PromptPlan,
    PromptShard,
    build_retry_prompt_plan,
)
from general_ludd.self_improve.runtime import TaskSpec, build_prompt, generate_local_proposal_plan

runner_module = cast(Any, runtime_module)

_OBJECTIVE = ("Fix the local-model self-improvement runner so it rejects model candidates "
"whose native context cannot hold the full rendered prompt and required proposal, "
"uses the GGUF native context instead of an oversized forced context, bounds each "
"proposal worker to five minutes, publishes lease release and persistent outcome "
"evidence, and exits with a bounded terminal diagnostic instead of a traceback.")
_PATHS = (
"scripts/run_self_improve_e2e.py",
"src/general_ludd/self_improve/codex_comparison.py",
"src/general_ludd/self_improve/model_candidate_planner.py",
"tests/unit/test_self_improve_codex_comparison.py",
"tests/unit/test_self_improve_codex_runner.py",
"tests/unit/test_self_improve_local_worker.py",
"tests/unit/test_self_improve_model_candidate_planner.py",
"tests/unit/test_self_improve_runner_model_lifecycle.py",
)
_TESTS = tuple(path for path in _PATHS if path.startswith("tests/"))
_COMMANDS = (
"make test-files TESTFILES=" + " ".join(_TESTS),
"make lint-files FILES=" + " ".join(_PATHS[:3]),
)

def _task() -> TaskSpec:
    return TaskSpec(task_id="S83.133", objective=_OBJECTIVE, canonical_make_commands=_COMMANDS)

def _reference() -> CodexReference:
    return CodexReference(baseline_sha="a" * 40, reference_sha="b" * 40,
        changed_files=frozenset(_PATHS), test_files=frozenset(_TESTS),
        changed_lines=213, elapsed_seconds=900.0)

def test_attempt_identity_uses_exact_prompt_protocol_and_is_legacy_sensitive() -> None:
    plan = PromptPlan(
        shards=(PromptShard(("src/general_ludd/example.py",), "bounded prompt"),),
        source_bytes=14,
    )

    attempt_identity = runner_module._attempt_identity_digest(plan)
    assert attempt_identity != plan.protocol_digest
    assert attempt_identity == local_proposal_attempt_identity_digest(
        plan.protocol_digest,
        proposal_protocol=plan.proposal_protocol,
    )
    legacy = runner_module._attempt_identity_digest("bounded prompt")
    assert len(legacy) == 64
    int(legacy, 16)
    assert legacy == runner_module._attempt_identity_digest("bounded prompt")
    assert legacy != runner_module._attempt_identity_digest("changed prompt")


def test_prompt_plan_rejects_mutable_or_drifted_baseline_identity() -> None:
    """Fail closed when baseline evidence cannot identify the exact prompt plan."""
    shard = PromptShard(("src/one.py",), "bounded prompt")

    with pytest.raises(ValueError, match="immutable tuple"):
        PromptPlan(
            shards=(shard,),
            source_bytes=1,
            baseline_files=cast(
                tuple[tuple[str, str | None], ...],
                [("src/one.py", "x")],
            ),
        )
    with pytest.raises(ValueError, match="path/text pairs"):
        PromptPlan(
            shards=(shard,),
            source_bytes=1,
            baseline_files=cast(
                tuple[tuple[str, str | None], ...],
                (("src/one.py",),),
            ),
        )
    with pytest.raises(ValueError, match="match focus paths"):
        PromptPlan(
            shards=(shard,),
            source_bytes=1,
            baseline_files=(("src/two.py", "x"),),
        )
    with pytest.raises(ValueError, match="source byte count"):
        PromptPlan(
            shards=(shard,),
            source_bytes=1,
            baseline_files=(("src/one.py", "xx"),),
        )
    with pytest.raises(ValueError, match="proposal protocol"):
        PromptPlan(
            shards=(shard,),
            source_bytes=0,
            proposal_protocol=cast(str, {"untrusted": "mapping"}),
        )


def _large_fixture(root: Path) -> None:
    bodies = {
        _PATHS[0]: (
            "import json\nimport sys\n\n"
            "def generate_local_proposal(prompt: str) -> str:\n"
            "    \"\"\"Bound the proposal worker to five minutes.\"\"\"\n"
            "    return prompt\n\n"
            "def run_benchmark() -> None:\n"
            "    \"\"\"Publish lease release and persistent outcome evidence.\"\"\"\n"
            "    print('SELF_IMPROVE_MODEL_RELEASED')\n\n"
            "def main() -> int:\n"
            "    \"\"\"Return a bounded terminal diagnostic without a traceback.\"\"\"\n"
            "    return 2\n"
        ),
        _PATHS[1]: (
            "class LocalProposalGateway:\n"
            "    \"\"\"Use the GGUF native context for a strict proposal.\"\"\"\n"
            "    def propose(self, prompt: str) -> str:\n"
            "        return prompt\n"
        ),
        _PATHS[2]: (
            "def plan_model_candidates(input_tokens: int, output_tokens: int) -> bool:\n"
            "    \"\"\"Reject native context that cannot hold the rendered prompt.\"\"\"\n"
            "    return input_tokens + output_tokens <= 8192\n"
        ),
        _PATHS[3]: "def test_gateway_uses_gguf_native_context() -> None:\n    assert True\n",
        _PATHS[4]: "def test_terminal_diagnostic_has_no_traceback() -> None:\n    assert True\n",
        _PATHS[5]: "def test_proposal_worker_is_bounded_to_five_minutes() -> None:\n    assert True\n",
        _PATHS[6]: "def test_full_rendered_prompt_rejects_native_context_overflow() -> None:\n    assert True\n",
        _PATHS[7]: "def test_model_lease_release_and_persistent_outcome_evidence() -> None:\n    assert True\n",
    }
    current = sum(len(value.encode()) for value in bodies.values())
    filler_prefix = "\n# unrelated historical filler\n#"
    bodies[_PATHS[-1]] += filler_prefix + ("x" * (51_859 - current - len(filler_prefix.encode())))
    assert sum(len(value.encode()) for value in bodies.values()) == 51_859
    for relative, value in bodies.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(value, encoding="utf-8")

def _manifest(
    paths: tuple[str, ...],
    *,
    baseline: str = "a" * 40,
    task_id: str = "S83.133",
    tests: tuple[str, ...] = _TESTS,
    commands: tuple[str, ...] = _COMMANDS,
) -> ProposalManifest:
    return ProposalManifest.from_json(json.dumps({
        "schema_version": 1, "baseline_sha": baseline, "task_id": task_id,
        "edits": [{"operation": "replace", "path": path, "old_text": "assert True",
                   "new_text": "assert 1 == 1"} for path in paths],
        "tests": list(tests), "make_commands": list(commands),
        "commit_message": "fix(self-improve): complete one bounded shard"}))

def test_multifile_plan_uses_singleton_focus_shards_in_one_worker_request(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep every model turn single-file while retaining one batch worker."""
    paths = ("src/one.py", "tests/unit/test_one.py")
    contents = (
        "value = 1\nassert True\n",
        "def test_value() -> None:\n    assert True\n",
    )
    for relative, content in zip(paths, contents, strict=True):
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    commands = (
        "make test-files TESTFILES=tests/unit/test_one.py",
    )
    task = TaskSpec(
        task_id="S83.133",
        objective="Change both exact files without omitting either path.",
        canonical_make_commands=commands,
    )
    reference = CodexReference(
        baseline_sha="a" * 40,
        reference_sha="b" * 40,
        changed_files=frozenset(paths),
        test_files=frozenset({paths[1]}),
        changed_lines=2,
        elapsed_seconds=1.0,
    )

    plan = build_prompt(task, reference, tmp_path)

    expected_groups = tuple((path,) for path in sorted(paths))
    expected_baseline_files = tuple(
        (path, content)
        for path, content in sorted(zip(paths, contents, strict=True))
    )
    assert plan.baseline_files == expected_baseline_files
    assert plan.proposal_protocol == "self-improve-compact-proposal-v4"
    assert "baseline_files=" not in repr(plan)
    assert tuple(shard.focus_paths for shard in plan.shards) == expected_groups
    assert len(plan.shards) == len(paths)
    assert all(
        len(shard.prompt.encode("utf-8"))
        <= runner_module._MAX_BASE_PROMPT_SHARD_BYTES
        for shard in plan.shards
    )
    assert all(
        shard.prompt.startswith(
            f"GLUDD_SELF_IMPROVE_FOCUS_PATH={shard.focus_paths[0]}\n"
        )
        for shard in plan.shards
    )
    assert all(shard.editable_ranges == ((1, 3),) for shard in plan.shards)
    assert all("L1|" in shard.prompt and "L2|" in shard.prompt for shard in plan.shards)
    assert all('only integer keys s and n and string key z' in shard.prompt for shard in plan.shards)
    assert all('\"p\"' not in shard.prompt and '\"c\"' not in shard.prompt for shard in plan.shards)
    comparison = ComparisonResult(
        accepted=False,
        score=80.0,
        blockers=("scope",),
        changed_file_precision=1.0,
        changed_file_recall=0.5,
    )
    retried = build_retry_prompt_plan(plan, comparison, diagnostics="bounded retry")
    assert tuple(shard.focus_paths for shard in retried.shards) == expected_groups
    assert retried.protocol_digest == plan.protocol_digest
    assert retried.baseline_files == plan.baseline_files

    model = tmp_path / "model.gguf"
    model.write_bytes(b"GGUF")
    requests: list[str] = []

    def propose(
        _runner: object,
        _model: Path,
        request: str,
        *,
        contract: ProposalContract | None = None,
    ) -> str:
        assert contract is not None
        requests.append(request)
        return encode_compact_span_batch(
            tuple(
                comparison_module._decode_compact_span_proposal(
                    '{"e":[{"s":2,"n":1,"z":"    assert 1 == 1\\n"}]}'
                    if shard.focus_paths[0].startswith("tests/")
                    else '{"e":[{"s":2,"n":1,"z":"assert 1 == 1\\n"}]}',
                    focus_path=shard.focus_paths[0],
                )
                for shard in plan.shards
            ),
            protocol_digest=plan.protocol_digest,
        )

    monkeypatch.setattr(runner_module, "_run_local_proposal_request", propose)
    merged = generate_local_proposal_plan(
        runner_module.MakeRunner(tmp_path),
        model,
        plan,
        task,
        reference,
    )

    decoded_prompts, decoded_digest = decode_prompt_batch(requests[0])
    assert len(requests) == 1
    assert decoded_prompts == tuple(shard.prompt for shard in plan.shards)
    assert decoded_digest == plan.protocol_digest
    assert {edit.path for edit in merged.edits} == set(paths)
    assert merged.tests == (paths[1],)
    assert merged.make_commands == commands


def test_parent_rejects_inapplicable_batch_before_attempt_worktree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Parent validation must make a schema-valid bad edit retryable."""
    relative = "src/one.py"
    baseline = "before = 1\n"
    target = tmp_path / relative
    target.parent.mkdir(parents=True)
    target.write_text(baseline, encoding="utf-8")
    commands = ("make test-files TESTFILES=tests/unit/test_one.py",)
    task = TaskSpec(
        task_id="S83.133",
        objective="Replace one exact assignment.",
        canonical_make_commands=commands,
    )
    reference = CodexReference(
        baseline_sha="a" * 40,
        reference_sha="b" * 40,
        changed_files=frozenset({relative}),
        test_files=frozenset({"tests/unit/test_one.py"}),
        changed_lines=1,
        elapsed_seconds=1.0,
    )
    plan = build_prompt(task, reference, tmp_path)
    secret_old_text = "MODEL_SECRET=must-not-escape"
    span_proposal = comparison_module._decode_compact_span_proposal(
        json.dumps({"e": [{"s": 2, "n": 1, "z": secret_old_text}]}),
        focus_path=relative,
    )

    def propose(
        _runner: object,
        _model: Path,
        _request: str,
        *,
        contract: ProposalContract | None = None,
    ) -> str:
        assert contract is not None
        return encode_compact_span_batch(
            (span_proposal,),
            protocol_digest=plan.protocol_digest,
        )

    monkeypatch.setattr(runner_module, "_run_local_proposal_request", propose)
    model = tmp_path / "model.gguf"
    model.write_bytes(b"GGUF")

    with pytest.raises(
        ValueError,
        match="compact span is outside trusted baseline lines",
    ) as error:
        generate_local_proposal_plan(
            runner_module.MakeRunner(tmp_path),
            model,
            plan,
            task,
            reference,
        )

    feedback = runner_module._validation_retry_feedback(str(error.value))
    assert "type=edit_span_precondition" in feedback
    assert "source=parent_validation" in feedback
    assert secret_old_text not in str(error.value)
    assert secret_old_text not in feedback


def test_51859_byte_multifile_context_is_decomposed_without_identity_loss(tmp_path: Path) -> None:
    _large_fixture(tmp_path)
    plan = build_prompt(_task(), _reference(), tmp_path)
    assert isinstance(plan, PromptPlan)
    assert plan.source_bytes == 51_859
    assert 2 <= len(plan.shards) <= len(_PATHS)
    sizes = [len(shard.prompt.encode()) for shard in plan.shards]
    assert max(sizes) <= 16_384
    assert max(sizes) < 51_859 // 3
    assert {path for shard in plan.shards for path in shard.focus_paths} == set(_PATHS)
    shared_prefixes = {
        shard.prompt.split("\n", 1)[1].split("\nShard-specific contract:", 1)[0]
        for shard in plan.shards
    }
    assert len(shared_prefixes) == 1
    shared_prefix = shared_prefixes.pop()
    assert all(path in shared_prefix for path in _PATHS)
    assert all(test in shared_prefix for test in _TESTS)
    assert all(command in shared_prefix for command in _COMMANDS)
    for shard in plan.shards:
        assert "Global immutable Codex reference paths:" in shard.prompt
        assert "Exact focus paths for this shard:" in shard.prompt
        assert all(path in shard.prompt for path in _PATHS)
        assert all(test in shard.prompt for test in _TESTS)
        assert all(command in shard.prompt for command in _COMMANDS)
        assert "sha256=" in shard.prompt
    for path in _PATHS:
        owning = next(shard for shard in plan.shards if path in shard.focus_paths)
        assert Path(path).name in owning.prompt

@pytest.mark.parametrize(
    ("focus_paths", "prompt", "match"),
    [
        ((), "prompt", "non-empty"),
        (("a.py", "a.py"), "prompt", "unique"),
        (("a.py",), " ", "must not be empty"),
        (("a.py",), "x" * 16_385, "exceeds"),
    ],
)
def test_prompt_shard_fails_closed_on_invalid_boundaries(
    focus_paths: tuple[str, ...],
    prompt: str,
    match: str,
) -> None:
    with pytest.raises(ValueError, match=match):
        PromptShard(focus_paths=focus_paths, prompt=prompt)


def test_prompt_plan_fails_closed_on_invalid_structure_and_digest() -> None:
    shard = PromptShard(focus_paths=("a.py",), prompt="exact prompt")
    duplicate = PromptShard(focus_paths=("a.py",), prompt="other exact prompt")
    with pytest.raises(ValueError, match="at least one"):
        PromptPlan(shards=(), source_bytes=0)
    with pytest.raises(ValueError, match="non-negative"):
        PromptPlan(shards=(shard,), source_bytes=-1)
    with pytest.raises(ValueError, match="disjoint"):
        PromptPlan(shards=(shard, duplicate), source_bytes=1)
    with pytest.raises(ValueError, match="lowercase SHA-256"):
        PromptPlan(shards=(shard,), source_bytes=1, protocol_digest="A" * 64)
    plan = PromptPlan(shards=(shard,), source_bytes=1)
    assert "exact" in plan
    assert "missing" not in plan
    assert object() not in plan


def test_prompt_plan_exposes_stable_protocol_digest_across_retries(tmp_path: Path) -> None:
    _large_fixture(tmp_path)
    first = build_prompt(_task(), _reference(), tmp_path)
    second = build_prompt(_task(), _reference(), tmp_path)
    comparison = ComparisonResult(
        accepted=False,
        score=80.0,
        blockers=("tests",),
        changed_file_precision=1.0,
        changed_file_recall=1.0,
    )
    retried = build_retry_prompt_plan(first, comparison, diagnostics="bounded failure")
    assert len(first.protocol_digest) == 64
    assert all(character in "0123456789abcdef" for character in first.protocol_digest)
    assert second.protocol_digest == first.protocol_digest
    assert retried.protocol_digest == first.protocol_digest
    altered = PromptPlan(
        shards=(
            *first.shards[:-1],
            runner_module.PromptShard(
                focus_paths=first.shards[-1].focus_paths,
                prompt=first.shards[-1].prompt + "\nmaterial protocol change",
            ),
        ),
        source_bytes=first.source_bytes,
    )
    assert altered.protocol_digest != first.protocol_digest


def test_validation_retry_uses_last_typed_marker_without_raw_worker_output() -> None:
    plan = PromptPlan(
        shards=(
            PromptShard(("src/one.py",), "first bounded prompt"),
            PromptShard(("tests/unit/test_one.py",), "second bounded prompt"),
        ),
        source_bytes=31,
    )
    error = (
        "llama loader /Users/operator/models/deepseek.gguf TOKEN=top-secret\n"
        "SELF_IMPROVE_LOCAL_PROPOSAL_ERROR compact proposal must contain exactly e\n"
        '{"e":[{"p":"src/private.py","a":"raw model body","z":"PASSWORD=hunter2"}]}\n'
        "worker failed rc=2: SELF_IMPROVE_LOCAL_PROPOSAL_ERROR "
        "replace requires distinct non-empty old_text\n"
    )

    retried = runner_module._build_validation_retry_prompt_plan(plan, error)

    suffixes = tuple(
        retried_shard.prompt[len(original.prompt) :]
        for original, retried_shard in zip(plan.shards, retried.shards, strict=True)
    )
    assert len(set(suffixes)) == 1
    assert "type=edit_replace_contract" in suffixes[0]
    assert "source=proposal_error" in suffixes[0]
    assert "replace requires distinct non-empty old_text" in suffixes[0]
    assert len(suffixes[0].encode("utf-8")) < 384
    assert all(
        value not in suffixes[0]
        for value in (
            "/Users/operator",
            "deepseek.gguf",
            "top-secret",
            "src/private.py",
            "raw model body",
            "hunter2",
            "compact proposal must contain exactly e",
        )
    )
    assert tuple(shard.focus_paths for shard in retried.shards) == tuple(
        shard.focus_paths for shard in plan.shards
    )
    assert all(
        retried_shard.prompt.startswith(original.prompt)
        for original, retried_shard in zip(plan.shards, retried.shards, strict=True)
    )
    assert retried.source_bytes == plan.source_bytes
    assert retried.protocol_digest == plan.protocol_digest
    assert runner_module._attempt_identity_digest(retried) == (
        runner_module._attempt_identity_digest(plan)
    )


def test_validation_retry_markerless_feedback_uses_bounded_tail() -> None:
    plan = PromptPlan(
        shards=(PromptShard(("src/one.py",), "bounded prompt"),),
        source_bytes=14,
    )
    error = (
        "replace requires distinct non-empty old_text\n"
        + ("early noisy loader output " * 100)
        + "\ncreate requires empty old_text and non-empty new_text "
        "API_KEY=tail-secret /tmp/private/model.gguf raw-model-fragment"
    )

    retried = runner_module._build_validation_retry_prompt_plan(plan, error)
    suffix = retried.shards[0].prompt[len(plan.shards[0].prompt) :]

    assert "type=edit_create_contract" in suffix
    assert "source=worker_tail" in suffix
    assert "create requires empty old_text and non-empty new_text" in suffix
    assert "replace requires distinct non-empty old_text" not in suffix
    assert "tail-secret" not in suffix
    assert "/tmp/private" not in suffix
    assert "raw-model-fragment" not in suffix
    assert len(suffix.encode("utf-8")) < 384


def test_validation_retry_redacts_unrecognized_marker_detail() -> None:
    plan = PromptPlan(
        shards=(PromptShard(("src/one.py",), "bounded prompt"),),
        source_bytes=14,
    )
    error = (
        "SELF_IMPROVE_LOCAL_PROPOSAL_ERROR "
        "/Users/operator/private.py TOKEN=top-secret raw-model-fragment"
    )

    retried = runner_module._build_validation_retry_prompt_plan(plan, error)
    suffix = retried.shards[0].prompt[len(plan.shards[0].prompt) :]

    assert "type=proposal_validation" in suffix
    assert "source=proposal_error" in suffix
    assert "detail=<redacted>" in suffix
    assert "/Users/operator" not in suffix
    assert "top-secret" not in suffix
    assert "raw-model-fragment" not in suffix


def test_compact_v4_live_json_retry_is_typed_bounded_and_actionable() -> None:
    """Turn the DeepSeek framing class into safe v4-specific retry guidance."""
    plan = PromptPlan(
        shards=(
            PromptShard(
                ("src/one.py",),
                "LINES 3-5\nL3|one\nL4|two\nL5|three\n",
                editable_ranges=((3, 6),),
            ),
        ),
        source_bytes=14,
        baseline_files=(("src/one.py", "one\ntwo\nthree\n"),),
        proposal_protocol=runner_module.COMPACT_PROPOSAL_PROTOCOL_V4,
    )
    error = (
        "llama loader /Users/operator/private.gguf TOKEN=top-secret\n"
        "SELF_IMPROVE_LOCAL_PROPOSAL_ERROR "
        "compact-v4 proposal is not one complete JSON object; output_bytes=2308\n"
        "PASSWORD=hunter2 raw-model-fragment"
    )

    retried = runner_module._build_validation_retry_prompt_plan(plan, error)
    suffix = retried.shards[0].prompt[len(plan.shards[0].prompt) :]

    assert (
        "protocol=self-improve-validation-retry-v4 type=proposal_json_contract "
        "source=proposal_error detail=compact-v4 proposal is not one complete JSON object"
        in suffix
    )
    assert "For shown Lx-Ly, use insertion s=x..y+1" in suffix
    assert "output_bytes=2308" not in suffix
    assert all(
        secret not in suffix
        for secret in ("/Users/operator", "top-secret", "hunter2", "raw-model-fragment")
    )
    assert len(suffix.encode("utf-8")) < 384
    assert retried.protocol_digest == plan.protocol_digest
    assert retried.proposal_protocol == runner_module.COMPACT_PROPOSAL_PROTOCOL_V4


def test_shard_merger_preserves_exact_scope_tests_and_commands() -> None:
    groups = (
        (_PATHS[0], _PATHS[3]),
        (_PATHS[1], _PATHS[4]),
        (_PATHS[2], *_PATHS[5:]),
    )
    proposal = merge_proposal_manifests(tuple(_manifest(group) for group in groups),
        expected_path_groups=groups, expected_baseline_sha="a" * 40,
        expected_task_id="S83.133", expected_tests=_TESTS,
        expected_make_commands=_COMMANDS)
    assert {edit.path for edit in proposal.edits} == set(_PATHS)
    assert proposal.tests == _TESTS
    assert proposal.make_commands == _COMMANDS

@pytest.mark.parametrize(("manifests", "groups", "match"), [
    ((_manifest((_PATHS[0],)),), ((_PATHS[0], _PATHS[1]),), "exact focus paths"),
    ((_manifest((_PATHS[0],), baseline="c" * 40),), ((_PATHS[0],),), "baseline"),
    ((_manifest((_PATHS[0],)), _manifest((_PATHS[0],))),
     ((_PATHS[0],), (_PATHS[0],)), "disjoint"),
    ((_manifest((_PATHS[0],), task_id="S83.134"),),
     ((_PATHS[0],),), "task identity"),
    ((_manifest((_PATHS[0],), tests=(_TESTS[0],)),),
     ((_PATHS[0],),), "test identity"),
    ((_manifest((_PATHS[0],), commands=("make test-count",)),),
     ((_PATHS[0],),), "Make command identity"),
])
def test_shard_merger_fails_closed_on_identity_drift(manifests: tuple[ProposalManifest, ...],
        groups: tuple[tuple[str, ...], ...], match: str) -> None:
    with pytest.raises(ValueError, match=match):
        merge_proposal_manifests(manifests, expected_path_groups=groups,
            expected_baseline_sha="a" * 40, expected_task_id="S83.133",
            expected_tests=_TESTS, expected_make_commands=_COMMANDS)


@pytest.mark.parametrize(
    ("baseline", "task_id", "tests", "commands", "match"),
    [
        ("bad", "S83.133", _TESTS, _COMMANDS, "baseline"),
        ("a" * 40, "bad", _TESTS, _COMMANDS, "task"),
        ("a" * 40, "S83.133", (), _COMMANDS, "tests"),
        ("a" * 40, "S83.133", (_TESTS[0], _TESTS[0]), _COMMANDS, "tests"),
        ("a" * 40, "S83.133", _TESTS, (), "commands"),
    ],
)
def test_shard_merger_rejects_invalid_global_contract(
    baseline: str,
    task_id: str,
    tests: tuple[str, ...],
    commands: tuple[str, ...],
    match: str,
) -> None:
    with pytest.raises(ValueError, match=match):
        merge_proposal_manifests(
            (_manifest((_PATHS[0],)),),
            expected_path_groups=((_PATHS[0],),),
            expected_baseline_sha=baseline,
            expected_task_id=task_id,
            expected_tests=tests,
            expected_make_commands=commands,
        )


@pytest.mark.parametrize(
    ("manifests", "groups", "match"),
    [
        ((), ((_PATHS[0],),), "count"),
        ((_manifest((_PATHS[0],)),), ((),), "have focus"),
        ((_manifest((_PATHS[0],)),), ((_PATHS[0], _PATHS[0]),), "disjoint"),
        ((_manifest((_PATHS[0],)),), (("../escape.py",),), "unsafe"),
    ],
)
def test_shard_merger_rejects_invalid_focus_partition(
    manifests: tuple[ProposalManifest, ...],
    groups: tuple[tuple[str, ...], ...],
    match: str,
) -> None:
    with pytest.raises(ValueError, match=match):
        merge_proposal_manifests(
            manifests,
            expected_path_groups=groups,
            expected_baseline_sha="a" * 40,
            expected_task_id="S83.133",
            expected_tests=_TESTS,
            expected_make_commands=_COMMANDS,
        )


def test_local_plan_runs_one_retained_worker_then_merges_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _large_fixture(tmp_path)
    plan = build_prompt(_task(), _reference(), tmp_path)
    model = tmp_path / "model.gguf"
    model.write_bytes(b"GGUF")
    calls: list[str] = []
    contracts: list[ProposalContract] = []

    def propose(
        _runner: object,
        _model: Path,
        request: str,
        *,
        contract: ProposalContract | None = None,
    ) -> str:
        assert contract is not None
        calls.append(request)
        contracts.append(contract)
        span_proposals: list[comparison_module.CompactSpanProposal] = []
        for shard in plan.shards:
            path = shard.focus_paths[0]
            content = (tmp_path / path).read_text(encoding="utf-8")
            old_text = content.splitlines(keepends=True)[0]
            assert content.count(old_text) == 1
            span_proposals.append(
                comparison_module._decode_compact_span_proposal(
                    json.dumps(
                        {
                            "e": [
                                {
                                    "s": 1,
                                    "n": 1,
                                    "z": old_text + "# exact change\n",
                                }
                            ]
                        }
                    ),
                    focus_path=path,
                )
            )
        return encode_compact_span_batch(
            tuple(span_proposals),
            protocol_digest=plan.protocol_digest,
        )

    monkeypatch.setattr(runner_module, "_run_local_proposal_request", propose)
    proposal = generate_local_proposal_plan(
        runner_module.MakeRunner(tmp_path), model, plan, _task(), _reference()
    )
    prompts, digest = decode_prompt_batch(calls[0])
    assert len(calls) == 1
    assert len(contracts) == 1
    assert contracts[0].baseline_sha == _reference().baseline_sha
    assert contracts[0].task_id == _task().task_id
    assert prompts == tuple(shard.prompt for shard in plan.shards)
    assert digest == plan.protocol_digest
    assert {edit.path for edit in proposal.edits} == set(_PATHS)

def test_retry_plan_redacts_and_bounds_diagnostics_without_identity_drift(tmp_path: Path) -> None:
    _large_fixture(tmp_path)
    plan = build_prompt(_task(), _reference(), tmp_path)
    comparison = ComparisonResult(accepted=False, score=80.0, blockers=("tests",),
        changed_file_precision=1.0, changed_file_recall=1.0)
    retried = build_retry_prompt_plan(plan, comparison,
        diagnostics=("failure " * 3000) + "\nPSK=top-secret")
    assert tuple(s.focus_paths for s in retried.shards) == tuple(s.focus_paths for s in plan.shards)
    assert all(len(s.prompt.encode()) <= 16_384 for s in retried.shards)
    assert all("top-secret" not in s.prompt for s in retried.shards)
    assert all("PSK=<redacted>" in s.prompt for s in retried.shards)
