from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest
import yaml

ROLE_ROOT = Path("collections/ansible_collections/general_ludd/agent/roles/local_game_gen")


def _load_yaml(rel: str) -> Any:
    path = ROLE_ROOT / rel
    assert path.exists(), f"Missing role file: {path}"
    with open(path) as fh:
        doc = yaml.safe_load(fh)
    assert doc is not None, f"Empty/unparseable YAML in {path}"
    return doc


def _iter_all_tasks(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for t in tasks:
        result.append(t)
        for block_key in ("block", "always", "rescue"):
            if block_key in t:
                result.extend(_iter_all_tasks(cast(list[dict[str, Any]], t[block_key])))
    return result


def _combined_tasks() -> list[dict[str, Any]]:
    combined: list[dict[str, Any]] = []
    for rel in ("tasks/main.yml", "tasks/generate_and_verify.yml"):
        tasks = cast(list[dict[str, Any]], _load_yaml(rel))
        combined.extend(_iter_all_tasks(tasks))
    return combined


def _combined_text() -> str:
    return "\n".join((ROLE_ROOT / rel).read_text() for rel in ("tasks/main.yml", "tasks/generate_and_verify.yml"))


# =============================================================================
#  A. Verify failure surfaces as a REJECTED event naming the failing check
# =============================================================================


class TestRejectionEventSurfacing:
    def test_rejection_event_task_exists(self) -> None:
        all_tasks = _combined_tasks()
        assert any("rejected" in t.get("name", "").lower() for t in all_tasks), (
            "A verify-phase failure must surface as a REJECTED event (task name containing 'rejected')"
        )

    def test_rejection_event_lives_in_rescue_or_after_verify(self) -> None:
        for t in _combined_tasks():
            if "rejected" in t.get("name", "").lower():
                assert any(
                    "set_fact" in k or "debug" in k or "uri" in k or "fail" in k or "include_tasks" in k for k in t
                ), f"REJECTED event task must emit via a real module, got: {list(t)}"
                assert "verif" in str(t).lower() or "check" in str(t).lower(), (
                    "REJECTED event must name the failing verify check in its content"
                )
                return
        pytest.fail("No REJECTED event task found")

    def test_rejection_event_names_the_failed_check(self) -> None:
        tasks_text = _combined_text()
        assert "_verify_failed" in tasks_text or "failed_check" in tasks_text or "REJECTED" in tasks_text, (
            "The rejection path must carry the name of the failing check (e.g. failed_check)"
        )

    def test_verify_failure_captured_before_rejection(self) -> None:
        tasks_text = _combined_text()
        for phase in ("_ast_result", "_import_result", "_runtime_result"):
            assert phase in tasks_text, f"Verify result var {phase} must still be registered"

    def test_rejection_fires_only_when_verify_failed(self) -> None:
        rejected = [
            t
            for t in _combined_tasks()
            if "rejected" in t.get("name", "").lower()
            and not any("include_tasks" in k for k in t)
            and not any("fail" in k for k in t)
        ]
        assert rejected, "REJECTED event task missing"
        for t in rejected:
            assert "when" in t, "REJECTED event must be conditional (when: verify failed)"
            assert any(v in str(t["when"]).lower() for v in ("failed", "is not success", "!= 0")), (
                "REJECTED event condition must reference verify failure state"
            )


# =============================================================================
#  B. Retry path: re-run generation with corrective prompt after rejection
# =============================================================================


class TestCorrectivePromptRetry:
    def test_retry_prompt_in_defaults(self) -> None:
        defaults = cast(dict[str, Any], _load_yaml("defaults/main.yml"))
        assert "retry_prompt" in defaults, "defaults must define retry_prompt (corrective prompt)"

    def test_retry_prompt_is_non_empty(self) -> None:
        defaults = cast(dict[str, Any], _load_yaml("defaults/main.yml"))
        assert isinstance(defaults["retry_prompt"], str)
        assert len(defaults["retry_prompt"]) > 20, "retry_prompt must be a substantive corrective prompt"

    def test_retry_prompt_same_shape_as_game_prompt(self) -> None:
        defaults = cast(dict[str, Any], _load_yaml("defaults/main.yml"))
        assert "{{" not in defaults["retry_prompt"], "retry_prompt default must not contain template syntax"

    def test_retry_path_re_runs_generation_after_rejection(self) -> None:
        tasks_text = _combined_text()
        assert "retry_prompt" in tasks_text, "tasks must reference retry_prompt"
        assert "/v1/completions" in tasks_text, "generation call must still exist"
        assert tasks_text.count("/v1/completions") >= 1

    def test_retry_uses_effective_prompt(self) -> None:
        tasks_text = _combined_text()
        assert "_attempt" in tasks_text, "tasks must track the current attempt number"
        assert "_effective_prompt" in tasks_text or "_active_prompt" in tasks_text, (
            "generation must use an effective prompt that becomes the corrective prompt on retry"
        )

    def test_retry_loops_back_to_generation(self) -> None:
        all_tasks = _combined_tasks()
        rescue_or_retry_tasks = [
            t for t in all_tasks if "retry" in t.get("name", "").lower() or "rejected" in t.get("name", "").lower()
        ]
        assert rescue_or_retry_tasks, "A retry path (rescue or include_tasks) must exist"
        assert any(any("include_tasks" in k for k in t) for t in rescue_or_retry_tasks), (
            "Retry must re-run generation via include_tasks recursion or a block rescue"
        )


# =============================================================================
#  C. Model fallback: iterate to next model on repeated rejection
# =============================================================================


class TestModelFallback:
    def test_fallback_models_in_defaults(self) -> None:
        defaults = cast(dict[str, Any], _load_yaml("defaults/main.yml"))
        assert "fallback_models" in defaults, "defaults must define fallback_models (list)"

    def test_fallback_models_is_list(self) -> None:
        defaults = cast(dict[str, Any], _load_yaml("defaults/main.yml"))
        assert isinstance(defaults["fallback_models"], list), "fallback_models must be a list"

    def test_fallback_models_entries_are_org_repo_pairs(self) -> None:
        defaults = cast(dict[str, Any], _load_yaml("defaults/main.yml"))
        for entry in defaults["fallback_models"]:
            assert isinstance(entry, str), f"fallback_models entries must be strings, got {entry!r}"
            assert entry.count("/") == 1, f"fallback model must be org/repo, got {entry!r}"

    def test_fallback_models_does_not_repeat_primary(self) -> None:
        defaults = cast(dict[str, Any], _load_yaml("defaults/main.yml"))
        assert defaults["model_repo"] not in defaults["fallback_models"], (
            "fallback_models must not repeat the primary model_repo"
        )

    def test_tasks_reference_fallback_models(self) -> None:
        tasks_text = _combined_text()
        assert "fallback_models" in tasks_text, "tasks must reference fallback_models"

    def test_generation_task_selects_model_per_attempt(self) -> None:
        all_tasks = _combined_tasks()
        gen = next(
            (t for t in all_tasks if "v1/completions" in str(t.get("ansible.builtin.uri", ""))),
            None,
        )
        assert gen is not None, "v1/completions generation task must exist"
        for t in all_tasks:
            if any("set_fact" in k for k in t) and "model_repo" in str(t) and "fallback_models" in str(t):
                assert "_attempt" in str(t), "Model selection must be keyed by the attempt number"
                return
        pytest.fail("No set_fact task selecting the model for the current attempt")


# =============================================================================
#  D. Bounded retry: no infinite retry
# =============================================================================


class TestBoundedRetry:
    def test_max_attempts_in_defaults(self) -> None:
        defaults = cast(dict[str, Any], _load_yaml("defaults/main.yml"))
        assert "max_attempts" in defaults, "defaults must define max_attempts"

    def test_default_max_attempts_is_one(self) -> None:
        defaults = cast(dict[str, Any], _load_yaml("defaults/main.yml"))
        assert int(defaults["max_attempts"]) == 1, (
            "default max_attempts must be 1 to preserve current single-shot behavior (CI-cheap)"
        )

    def test_tasks_reference_max_attempts(self) -> None:
        tasks_text = _combined_text()
        assert "max_attempts" in tasks_text, "tasks must reference max_attempts"

    def test_retry_recurse_is_guarded_by_attempt_limit(self) -> None:
        all_tasks = _combined_tasks()
        retry_tasks = [
            t for t in all_tasks if "include_tasks" in t or ("retry" in t.get("name", "").lower() and "when" in t)
        ]
        assert retry_tasks, "Retry re-invocation must exist as an include_tasks task"
        for t in retry_tasks:
            if "include_tasks" in t:
                assert "when" in t, "Retry re-invocation must be conditional"
                assert "_attempt" in str(t["when"]) and "max_attempts" in str(t["when"]), (
                    "Retry must only recurse while _attempt < max_attempts"
                )

    def test_attempt_counter_increments_on_retry(self) -> None:
        all_tasks = _combined_tasks()
        assert any("_attempt | int + 1" in str(t) or "_attempt + 1" in str(t) for t in all_tasks), (
            "Retry path must increment the attempt counter"
        )

    def test_attempt_counter_starts_at_one(self) -> None:
        tasks_text = _combined_text()
        assert "_attempt: 1" in tasks_text, "Attempt counter must start at 1"

    def test_max_attempts_positive(self) -> None:
        defaults = cast(dict[str, Any], _load_yaml("defaults/main.yml"))
        assert int(defaults["max_attempts"]) >= 1, "max_attempts must be >= 1 (bounded, no infinite retry)"

    def test_final_failure_is_hard_fail_when_attempts_exhausted(self) -> None:
        all_tasks = _combined_tasks()
        assert any("ansible.builtin.fail" in t for t in all_tasks if "attempt" in str(t).lower()), (
            "When attempts are exhausted the role must fail hard, not silently succeed"
        )

    def test_fallback_models_cannot_exceed_attempts_consumption(self) -> None:
        defaults = cast(dict[str, Any], _load_yaml("defaults/main.yml"))
        max_attempts = int(defaults["max_attempts"])
        assert max_attempts < 10, "max_attempts default must be small (CI-cheap bounded retry)"


# =============================================================================
#  F. Rejection is fail-closed by default but molecule-observable (CI contract)
# =============================================================================


class TestFailOnRejectionContract:
    def test_fail_on_rejection_defaults_true(self) -> None:
        defaults = cast(dict[str, Any], _load_yaml("defaults/main.yml"))
        assert bool(defaults.get("fail_on_rejection")) is True, (
            "fail_on_rejection must default to true so production rejections fail closed"
        )

    def test_hard_fail_gated_by_fail_on_rejection(self) -> None:
        tasks_text = _combined_text()
        assert "fail_on_rejection" in tasks_text, "final fail must be gated by fail_on_rejection"
        assert "fail_on_rejection | default(true) | bool" in tasks_text or (
            "fail_on_rejection" in tasks_text and "when" in tasks_text
        ), "final hard-fail must be skippable when fail_on_rejection=false"

    def test_rejected_artifact_removed_on_exhaustion(self) -> None:
        tasks_text = _combined_text()
        assert "state: absent" in tasks_text, "rejected artifact must be removed when attempts are exhausted"
        assert "artifact_dir" in tasks_text, "artifact removal must reference artifact_dir"

    def test_molecule_converge_exercises_retry_without_hard_fail(self) -> None:
        converge = Path("molecule/playbooks/local_game_gen/default/converge.yml")
        plays = cast(list[dict[str, Any]], yaml.safe_load(converge.read_text()))
        role_vars: dict[str, Any] = {}
        for play in plays:
            for task in play.get("tasks", []):
                include = task.get("ansible.builtin.include_role")
                if isinstance(include, dict) and "local_game_gen" in str(include.get("name", "")):
                    role_vars = cast(dict[str, Any], include.get("vars", {}))
        assert int(role_vars.get("max_attempts", 0)) >= 3, (
            "molecule converge must set max_attempts >= 3 (attempt 1 + a retry requires"
            " the rescue-incremented counter to satisfy _attempt < max_attempts)"
        )
        assert bool(role_vars.get("fail_on_rejection")) is False, (
            "molecule converge must set fail_on_rejection=false so rejection is observed, not fatal"
        )

    def test_molecule_verify_tolerates_rejection(self) -> None:
        verify = Path("molecule/playbooks/local_game_gen/default/verify.yml")
        text = verify.read_text()
        assert "_artifact_stat" in text, "verify must stat the artifact to branch on rejection"
        assert "when:" in text, "verify must gate the accepted-artifact checks on artifact presence"


# =============================================================================
#  Self-pin
# =============================================================================
def test_rejection_retry_test_count() -> None:
    import re

    source = Path(__file__).read_text()
    count = len(re.findall(r"^\s*def test_", source, re.MULTILINE))
    assert count >= 20, f"Expected >=20 test functions, found {count}"
