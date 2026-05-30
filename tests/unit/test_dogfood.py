"""Unit tests for dogfood runner, validator, and sprint parser."""

from __future__ import annotations

import os
import tempfile

from agentic_harness.dogfood.runner import DogfoodConfig, DogfoodProfile, DogfoodRunner, SmokeTaskResult
from agentic_harness.dogfood.sprint_parser import parse_sprint_markdown
from agentic_harness.dogfood.validator import BypassFinding, DogfoodValidationResult, DogfoodValidator

PLAYBOOK_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "playbooks")


class TestDogfoodConfigDefaults:
    def test_dogfood_config_defaults(self) -> None:
        config = DogfoodConfig(
            repo_root="/tmp/repo",
            target_repo="/tmp/target",
            runtime_profile="ansible",
            model_profile="gpt-4",
        )
        assert config.repo_root == "/tmp/repo"
        assert config.target_repo == "/tmp/target"
        assert config.runtime_profile == "ansible"
        assert config.model_profile == "gpt-4"
        assert config.auto_commit is True


class TestDogfoodRunnerSeedTodosFromSprint:
    def test_dogfood_runner_seed_todos_from_sprint(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write("# Sprint 0\n")
            f.write("## Objective 1\n")
            f.write("- [ ] Implement feature A\n")
            f.write("- [x] Already done task\n")
            f.write("- [ ] Implement feature B\n")
            f.flush()
            config = DogfoodConfig(
                repo_root="/tmp/repo",
                target_repo="/tmp/target",
                runtime_profile="ansible",
                model_profile="gpt-4",
            )
            runner = DogfoodRunner(config)
            todos = runner.seed_todos_from_sprint(f.name)
        assert len(todos) == 2
        descriptions = [t["description"] for t in todos]
        assert "Implement feature A" in descriptions
        assert "Implement feature B" in descriptions
        assert all(t["source"] == "sprint" for t in todos)


class TestDogfoodRunnerSeedTodosFromGapAnalysis:
    def test_dogfood_runner_seed_todos_from_gap_analysis(self) -> None:
        from agentic_harness.validation.gap_analyzer import GapItem, GapReport

        gap_report = GapReport(
            total_gaps=2,
            gaps=[
                GapItem(
                    category="missing_tests",
                    description="No tests for foo.py",
                    severity="high",
                    suggested_action="Create test_foo.py",
                ),
                GapItem(
                    category="missing_molecule",
                    description="No molecule for deploy.yml",
                    severity="medium",
                    suggested_action="Create molecule scenario",
                ),
            ],
        )
        config = DogfoodConfig(
            repo_root="/tmp/repo",
            target_repo="/tmp/target",
            runtime_profile="ansible",
            model_profile="gpt-4",
        )
        runner = DogfoodRunner(config)
        todos = runner.seed_todos_from_gap_analysis(gap_report)
        assert len(todos) == 2
        assert all(t["source"] == "gap_analysis" for t in todos)
        categories = [t["category"] for t in todos]
        assert "missing_tests" in categories
        assert "missing_molecule" in categories


class TestDogfoodRunnerSeedTodosFromTestFailures:
    def test_dogfood_runner_seed_todos_from_test_failures(self) -> None:
        test_output = (
            "2 failed, 5 passed\n"
            "FAILED tests/unit/test_foo.py::test_bar - AssertionError\n"
            "FAILED tests/unit/test_baz.py::test_qux - RuntimeError\n"
        )
        config = DogfoodConfig(
            repo_root="/tmp/repo",
            target_repo="/tmp/target",
            runtime_profile="ansible",
            model_profile="gpt-4",
        )
        runner = DogfoodRunner(config)
        todos = runner.seed_todos_from_test_failures(test_output)
        assert len(todos) == 2
        assert all(t["source"] == "test_failure" for t in todos)
        assert todos[0]["test_id"] == "tests/unit/test_foo.py::test_bar"
        assert todos[1]["test_id"] == "tests/unit/test_baz.py::test_qux"


class TestDogfoodRunnerSmokeTask:
    def test_dogfood_runner_smoke_task(self) -> None:
        config = DogfoodConfig(
            repo_root="/tmp/repo",
            target_repo="/tmp/target",
            runtime_profile="ansible",
            model_profile="gpt-4",
        )
        runner = DogfoodRunner(config)
        result = runner.run_smoke_task("noop")
        assert isinstance(result, SmokeTaskResult)
        assert result.task_name == "noop"
        assert isinstance(result.success, bool)
        assert isinstance(result.duration_seconds, float)
        assert isinstance(result.output, str)


class TestDogfoodRunnerCreateProfile:
    def test_dogfood_runner_create_profile(self) -> None:
        config = DogfoodConfig(
            repo_root="/tmp/repo",
            target_repo="/tmp/target",
            runtime_profile="ansible",
            model_profile="gpt-4",
        )
        runner = DogfoodRunner(config)
        profile = runner.create_dogfood_profile()
        assert isinstance(profile, DogfoodProfile)
        assert profile.repo_root == "/tmp/repo"
        assert profile.target_repo == "/tmp/target"
        assert profile.runtime_mode == "ansible"
        assert "gpt-4" in profile.model_profiles
        assert profile.enabled is True


class TestDogfoodValidatorValidRun:
    def test_dogfood_validator_valid_run(self) -> None:
        validator = DogfoodValidator()
        result = validator.validate_dogfood_run(
            SmokeTaskResult(
                task_name="noop",
                success=True,
                duration_seconds=1.5,
                output="ok",
            ),
        )
        assert isinstance(result, DogfoodValidationResult)
        assert result.valid is True
        assert result.uses_configured_runtime is True


class TestDogfoodValidatorDetectsBypass:
    def test_dogfood_validator_detects_bypass(self) -> None:
        log_entries = [
            {"action": "run_command", "command": "make test", "runtime": "ansible"},
            {"action": "run_command", "command": "bash -c 'skip tests'", "runtime": "local"},
            {"action": "run_command", "command": "pip install foo", "runtime": "local"},
        ]
        validator = DogfoodValidator()
        findings = validator.check_no_local_bypasses(log_entries)
        assert len(findings) >= 1
        assert all(isinstance(f, BypassFinding) for f in findings)
        assert any(f.category == "local_bypass" for f in findings)


class TestDogfoodValidatorChecksRuntime:
    def test_dogfood_validator_checks_runtime(self) -> None:
        artifacts = [
            {"runtime": "ansible", "playbook": "noop.yml"},
            {"runtime": "ansible", "playbook": "validate.yml"},
        ]
        validator = DogfoodValidator()
        assert validator.check_artifacts_use_configured_runtime(artifacts) is True

    def test_dogfood_validator_detects_unconfigured_runtime(self) -> None:
        artifacts = [
            {"runtime": "ansible", "playbook": "noop.yml"},
            {"runtime": "local", "playbook": "custom.sh"},
        ]
        validator = DogfoodValidator()
        assert validator.check_artifacts_use_configured_runtime(artifacts) is False


class TestSprintParserExtractsObjectives:
    def test_sprint_parser_extracts_objectives(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write("# Sprint 0\n\n")
            f.write("## Objective 1: Build Core\n")
            f.write("- [ ] Task A\n")
            f.write("## Objective 2: Add Tests\n")
            f.write("- [ ] Task B\n")
            f.flush()
            items = parse_sprint_markdown(f.name)
        assert len(items) == 2
        assert items[0].objective_number == 1
        assert items[0].title == "Build Core"
        assert items[1].objective_number == 2
        assert items[1].title == "Add Tests"


class TestSprintParserExtractsTasks:
    def test_sprint_parser_extracts_tasks(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write("# Sprint 0\n\n")
            f.write("## Objective 3: Something\n")
            f.write("- [ ] Do the first thing\n")
            f.write("- [x] Already done\n")
            f.write("- [ ] Do another thing\n")
            f.write("\n**Acceptance Criteria:**\n")
            f.write("- AC1: Must work\n")
            f.write("- AC2: Must be fast\n")
            f.flush()
            items = parse_sprint_markdown(f.name)
        assert len(items) == 1
        item = items[0]
        assert len(item.tasks) == 2
        assert "Do the first thing" in item.tasks
        assert "Do another thing" in item.tasks
        assert "Already done" not in item.tasks
        assert len(item.acceptance_criteria) == 2
        assert "Must work" in item.acceptance_criteria


class TestSprintParserRealSprintFile:
    def test_sprint_parser_real_sprint_file(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write("# Sprint 0 — Foundation\n\n")
            f.write("## Objective 16: Dogfood Release Loop\n")
            f.write("Status: in-progress\n\n")
            f.write("Tasks:\n")
            f.write("- [ ] Create dogfood runner\n")
            f.write("- [ ] Add sprint parser\n")
            f.write("- [x] Define data models\n")
            f.write("\n**Acceptance Criteria:**\n")
            f.write("- AC1: Runner seeds todos from sprint\n")
            f.write("- AC2: Validator detects bypasses\n")
            f.flush()
            items = parse_sprint_markdown(f.name)
        assert len(items) == 1
        assert items[0].objective_number == 16
        assert items[0].title == "Dogfood Release Loop"
        assert items[0].status == "in-progress"
        assert len(items[0].tasks) == 2
        assert len(items[0].acceptance_criteria) == 2


class TestPlaybookExists:
    def test_prompt_eval_playbook_exists(self) -> None:
        assert os.path.isfile(os.path.join(PLAYBOOK_DIR, "prompt_eval.yml"))
