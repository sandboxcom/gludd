"""E2E: Continuous self-dogfooding release loop.

Covers sprint objective 16 — dogfood runner, sprint parsing, seed todo
generation, smoke tasks, bypass detection, and playbook stubs.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from unittest.mock import MagicMock, patch

from agentic_harness.dogfood import (
    BypassFinding,
    DogfoodConfig,
    DogfoodProfile,
    DogfoodRunner,
    DogfoodValidationResult,
    DogfoodValidator,
    SmokeTaskResult,
    SprintItem,
    parse_sprint_markdown,
)


class TestDogfoodRunnerImportAndInstantiation:
    def test_runner_imports_from_package(self):
        from agentic_harness.dogfood import DogfoodRunner
        assert DogfoodRunner is not None

    def test_runner_instantiation_with_config(self):
        config = DogfoodConfig(
            repo_root="/tmp/test_repo",
            target_repo="self",
            runtime_profile="native_uv",
            model_profile="openai_strong",
        )
        runner = DogfoodRunner(config)
        assert runner.config.repo_root == "/tmp/test_repo"
        assert runner.config.target_repo == "self"
        assert runner.config.runtime_profile == "native_uv"
        assert runner.config.model_profile == "openai_strong"

    def test_config_auto_commit_default(self):
        config = DogfoodConfig(
            repo_root="/tmp/test_repo",
            target_repo="self",
            runtime_profile="native_uv",
            model_profile="openai_strong",
        )
        assert config.auto_commit is True

    def test_config_auto_commit_disabled(self):
        config = DogfoodConfig(
            repo_root="/tmp/test_repo",
            target_repo="self",
            runtime_profile="native_pip",
            model_profile="openrouter_code",
            auto_commit=False,
        )
        assert config.auto_commit is False


class TestSprintParser:
    def test_parse_sprint0_returns_items(self):
        repo_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        sprint_path = os.path.join(repo_root, "docs", "sprint0.md")
        items = parse_sprint_markdown(sprint_path)
        assert isinstance(items, list)

    def test_parse_sprint_fixture_with_correct_format(self, tmp_path):
        fixture = tmp_path / "sprint_fixture.md"
        fixture.write_text(
            "## Objective 1: Repository Skeleton\n"
            "Status: Not started\n\n"
            "- [ ] Create Python package\n"
            "- [ ] Add pyproject.toml\n"
            "- AC1: uv run pytest passes\n\n"
            "## Objective 16: Continuous Self-Dogfooding Release Loop\n"
            "Status: Not started\n\n"
            "- [ ] Define a dogfood run profile\n"
            "- [ ] Seed the todo list from this sprint\n"
            "- [ ] Ensure the event loop can select harness-improvement todos\n"
            "- [ ] Require library_research_gate.yml artifacts\n"
            "- [ ] Require failing tests or Molecule scenarios\n"
            "- [ ] Add log-audit rules that detect special-case dogfood bypasses\n"
            "- AC1: Harness can create and complete a real improvement todo\n"
            "- AC2: Dogfood run uses configured model and runtime profiles\n"
        )
        items = parse_sprint_markdown(str(fixture))
        assert len(items) == 2
        obj_numbers = {item.objective_number for item in items}
        assert 1 in obj_numbers
        assert 16 in obj_numbers

    def test_sprint_fixture_items_have_titles(self, tmp_path):
        fixture = tmp_path / "sprint_fixture.md"
        fixture.write_text(
            "## Objective 16: Continuous Self-Dogfooding Release Loop\n"
            "Status: Not started\n\n"
            "- [ ] Define a dogfood run profile\n"
        )
        items = parse_sprint_markdown(str(fixture))
        assert len(items) == 1
        assert "Dogfood" in items[0].title

    def test_sprint_fixture_items_have_tasks(self, tmp_path):
        fixture = tmp_path / "sprint_fixture.md"
        fixture.write_text(
            "## Objective 16: Continuous Self-Dogfooding Release Loop\n"
            "Status: Not started\n\n"
            "- [ ] Define a dogfood run profile\n"
            "- [ ] Seed the todo list\n"
            "- [ ] Ensure event loop can select todos\n"
            "- [ ] Require library_research_gate.yml artifacts\n"
            "- [ ] Require failing tests\n"
            "- [ ] Add log-audit rules\n"
        )
        items = parse_sprint_markdown(str(fixture))
        assert len(items) == 1
        assert len(items[0].tasks) >= 5

    def test_sprint_item_dataclass_fields(self):
        item = SprintItem(
            objective_number=99,
            title="Test Objective",
            status="not started",
            tasks=["Task A", "Task B"],
            acceptance_criteria=["AC1: Pass"],
        )
        assert item.objective_number == 99
        assert item.title == "Test Objective"
        assert item.status == "not started"
        assert len(item.tasks) == 2
        assert len(item.acceptance_criteria) == 1


class TestSeedTodos:
    def test_seed_todos_from_sprint_fixture(self, tmp_path):
        fixture = tmp_path / "sprint_fixture.md"
        fixture.write_text(
            "## Objective 1: Repository Skeleton\n"
            "Status: Not started\n\n"
            "- [ ] Create Python package\n"
            "- [ ] Add pyproject.toml\n\n"
            "## Objective 16: Continuous Self-Dogfooding Release Loop\n"
            "Status: Not started\n\n"
            "- [ ] Define a dogfood run profile\n"
            "- [ ] Seed the todo list from this sprint\n"
            "- [ ] Ensure the event loop can select todos\n"
        )
        config = DogfoodConfig(
            repo_root=str(tmp_path),
            target_repo="self",
            runtime_profile="native_uv",
            model_profile="openai_strong",
        )
        runner = DogfoodRunner(config)
        todos = runner.seed_todos_from_sprint(str(fixture))
        assert len(todos) >= 5
        for todo in todos:
            assert "description" in todo
            assert todo["source"] == "sprint"
            assert "objective_number" in todo
            assert "objective_title" in todo

    def test_seed_todos_from_gap_analysis(self):
        @dataclass
        class FakeGap:
            description: str
            category: str
            severity: str
            suggested_action: str

        @dataclass
        class FakeGapReport:
            gaps: list[FakeGap]

        config = DogfoodConfig(
            repo_root="/tmp/test_repo",
            target_repo="self",
            runtime_profile="native_uv",
            model_profile="openai_strong",
        )
        runner = DogfoodRunner(config)
        report = FakeGapReport(gaps=[
            FakeGap(
                description="Missing Molecule scenario for noop.yml",
                category="molecule_coverage",
                severity="high",
                suggested_action="Add Molecule scenario",
            ),
            FakeGap(
                description="No test for quality gate enforcement",
                category="test_coverage",
                severity="medium",
                suggested_action="Add pytest test",
            ),
        ])
        todos = runner.seed_todos_from_gap_analysis(report)
        assert len(todos) == 2
        assert todos[0]["source"] == "gap_analysis"
        assert todos[0]["category"] == "molecule_coverage"
        assert todos[1]["severity"] == "medium"

    def test_seed_todos_from_test_failures(self):
        config = DogfoodConfig(
            repo_root="/tmp/test_repo",
            target_repo="self",
            runtime_profile="native_uv",
            model_profile="openai_strong",
        )
        runner = DogfoodRunner(config)
        test_output = """
some passing output
FAILED tests/unit/test_foo.py::test_bar
FAILED tests/unit/test_baz.py::test_qux
more output
"""
        todos = runner.seed_todos_from_test_failures(test_output)
        assert len(todos) == 2
        assert todos[0]["source"] == "test_failure"
        assert "test_bar" in todos[0]["description"]
        assert todos[0]["test_id"] == "tests/unit/test_foo.py::test_bar"
        assert todos[1]["test_id"] == "tests/unit/test_baz.py::test_qux"

    def test_seed_todos_from_test_failures_empty(self):
        config = DogfoodConfig(
            repo_root="/tmp/test_repo",
            target_repo="self",
            runtime_profile="native_uv",
            model_profile="openai_strong",
        )
        runner = DogfoodRunner(config)
        todos = runner.seed_todos_from_test_failures("all tests passed")
        assert todos == []


class TestSmokeTasks:
    def test_smoke_task_result_dataclass(self):
        result = SmokeTaskResult(
            task_name="noop",
            success=True,
            duration_seconds=0.5,
            output="ok",
        )
        assert result.task_name == "noop"
        assert result.success is True
        assert result.duration_seconds == 0.5

    def test_smoke_task_failure_result(self):
        result = SmokeTaskResult(
            task_name="missing_playbook",
            success=False,
            duration_seconds=0.1,
            output="file not found",
        )
        assert result.success is False
        assert "not found" in result.output

    @patch("subprocess.run")
    def test_run_smoke_task_mocked_success(self, mock_run: MagicMock) -> None:
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "syntax check passed"
        mock_result.stderr = ""
        mock_run.return_value = mock_result

        config = DogfoodConfig(
            repo_root="/tmp/test_repo",
            target_repo="self",
            runtime_profile="native_uv",
            model_profile="openai_strong",
        )
        runner = DogfoodRunner(config)
        result = runner.run_smoke_task("noop")
        assert result.success is True
        assert result.task_name == "noop"
        assert "syntax check passed" in result.output

    @patch("subprocess.run")
    def test_run_smoke_task_mocked_failure(self, mock_run: MagicMock) -> None:
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        mock_result.stderr = "ERROR: playbook not found"
        mock_run.return_value = mock_result

        config = DogfoodConfig(
            repo_root="/tmp/test_repo",
            target_repo="self",
            runtime_profile="native_uv",
            model_profile="openai_strong",
        )
        runner = DogfoodRunner(config)
        result = runner.run_smoke_task("nonexistent")
        assert result.success is False

    @patch("subprocess.run")
    def test_run_smoke_task_exception(self, mock_run: MagicMock) -> None:
        mock_run.side_effect = FileNotFoundError("ansible-playbook not found")

        config = DogfoodConfig(
            repo_root="/tmp/test_repo",
            target_repo="self",
            runtime_profile="native_uv",
            model_profile="openai_strong",
        )
        runner = DogfoodRunner(config)
        result = runner.run_smoke_task("noop")
        assert result.success is False
        assert "not found" in result.output


class TestBypassDetection:
    def test_validator_returns_valid_for_success(self):
        validator = DogfoodValidator()
        result = SmokeTaskResult(
            task_name="noop",
            success=True,
            duration_seconds=0.5,
            output="ok",
        )
        validation = validator.validate_dogfood_run(result)
        assert isinstance(validation, DogfoodValidationResult)
        assert validation.valid is True
        assert validation.uses_configured_runtime is True

    def test_validator_returns_invalid_for_failure(self):
        validator = DogfoodValidator()
        result = SmokeTaskResult(
            task_name="noop",
            success=False,
            duration_seconds=0.5,
            output="error",
        )
        validation = validator.validate_dogfood_run(result)
        assert validation.valid is False
        assert validation.uses_configured_runtime is False

    def test_detect_local_bypass_by_runtime_field(self):
        validator = DogfoodValidator()
        log_entries = [
            {"runtime": "local", "command": "pytest tests/"},
            {"runtime": "ansible", "command": "ansible-playbook noop.yml"},
        ]
        findings = validator.check_no_local_bypasses(log_entries)
        assert len(findings) == 1
        assert findings[0].category == "local_bypass"
        assert isinstance(findings[0], BypassFinding)
        assert findings[0].evidence

    def test_detect_local_bypass_by_command_pattern(self):
        validator = DogfoodValidator()
        log_entries = [
            {"runtime": "unknown", "command": "bash -c 'run thing directly'"},
            {"runtime": "unknown", "command": "pip install something"},
            {"runtime": "unknown", "command": "python -c 'import thing'"},
            {"runtime": "unknown", "command": "sh -c 'do stuff'"},
            {"runtime": "unknown", "command": "npm run build"},
        ]
        findings = validator.check_no_local_bypasses(log_entries)
        assert len(findings) == 5

    def test_no_bypass_for_clean_ansible_entries(self):
        validator = DogfoodValidator()
        log_entries = [
            {"runtime": "ansible", "command": "ansible-playbook playbooks/validate_task.yml"},
            {"runtime": "ansible", "command": "ansible-playbook playbooks/noop.yml"},
        ]
        findings = validator.check_no_local_bypasses(log_entries)
        assert len(findings) == 0

    def test_artifacts_use_configured_runtime(self):
        validator = DogfoodValidator()
        artifacts = [
            {"runtime": "ansible", "path": "/data/artifacts/noop.json"},
            {"runtime": "ansible", "path": "/data/artifacts/validate.json"},
        ]
        assert validator.check_artifacts_use_configured_runtime(artifacts) is True

    def test_artifacts_reject_non_ansible_runtime(self):
        validator = DogfoodValidator()
        artifacts = [
            {"runtime": "local", "path": "/data/artifacts/noop.json"},
        ]
        assert validator.check_artifacts_use_configured_runtime(artifacts) is False

    def test_empty_artifacts_pass_runtime_check(self):
        validator = DogfoodValidator()
        assert validator.check_artifacts_use_configured_runtime([]) is True


class TestDogfoodProfile:
    def test_create_dogfood_profile(self):
        config = DogfoodConfig(
            repo_root="/tmp/test_repo",
            target_repo="self",
            runtime_profile="native_uv",
            model_profile="openai_strong",
        )
        runner = DogfoodRunner(config)
        profile = runner.create_dogfood_profile()
        assert isinstance(profile, DogfoodProfile)
        assert profile.repo_root == "/tmp/test_repo"
        assert profile.target_repo == "self"
        assert profile.runtime_mode == "native_uv"
        assert profile.model_profiles == ["openai_strong"]
        assert profile.enabled is True


class TestDogfoodPlaybookStubs:
    def test_dogfood_related_playbook_stubs_exist(self):
        repo_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        playbooks_dir = os.path.join(repo_root, "playbooks")
        required_playbooks = [
            "self_improve_harness.yml",
            "validate_task.yml",
            "quality_gate_validate.yml",
            "molecule_test.yml",
            "dependency_update.yml",
            "release_artifacts_validate.yml",
            "pip_install_bundle.yml",
            "slim_agent_container_build.yml",
            "runtime_validate.yml",
            "gap_analysis.yml",
            "log_audit.yml",
            "reload_harness.yml",
            "noop.yml",
        ]
        for pb in required_playbooks:
            path = os.path.join(playbooks_dir, pb)
            assert os.path.isfile(path), f"Missing playbook stub: {pb}"
