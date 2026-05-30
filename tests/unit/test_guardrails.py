"""Tests for the guardrail infrastructure: makefile targets, scripts, config."""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent.parent

MAKEFILE = ROOT / "Makefile"
OPENCODE_JSON = ROOT / "opencode.json"
AGENTS_MD = ROOT / "AGENTS.md"
PLUGIN_FILE = ROOT / ".opencode" / "plugin" / "enforce-make.ts"
SKELETON_SCRIPT = ROOT / "scripts" / "skeleton.py"
SKILL_FILE = ROOT / ".opencode" / "skills" / "guardrail-pattern" / "SKILL.md"


class TestMakefileTargets:
    def test_makefile_exists(self):
        assert MAKEFILE.exists(), "Makefile must exist"

    def test_makefile_has_required_targets(self):
        content = MAKEFILE.read_text()
        required = [
            "init", "sync", "test", "test-unit", "lint", "lint-fix",
            "typecheck", "healthcheck", "clean", "qa", "validate",
            "bootstrap", "ansible-syntax", "git-status", "test-and-commit",
            "test-guardrails",
        ]
        for target in required:
            assert f"{target}:" in content, f"Makefile missing target: {target}"

    def test_makefile_targets_listed_in_phony(self):
        content = MAKEFILE.read_text()
        assert ".PHONY" in content
        assert "test" in content
        assert "lint" in content

    def test_make_test_passes(self):
        env = {**os.environ, "PYTEST_ADDOPTS": "--ignore=tests/unit/test_guardrails.py"}
        result = subprocess.run(
            ["make", "test-unit"],
            capture_output=True, text=True, cwd=str(ROOT), timeout=300,
            env=env,
        )
        assert result.returncode == 0, f"make test-unit failed:\n{result.stderr}\n{result.stdout}"

    def test_make_lint_passes(self):
        result = subprocess.run(
            ["make", "lint"],
            capture_output=True, text=True, cwd=str(ROOT), timeout=60,
        )
        assert result.returncode == 0, f"make lint failed:\n{result.stderr}\n{result.stdout}"

    def test_make_healthcheck_passes(self):
        result = subprocess.run(
            ["make", "healthcheck"],
            capture_output=True, text=True, cwd=str(ROOT), timeout=60,
        )
        assert result.returncode == 0, f"make healthcheck failed:\n{result.stderr}\n{result.stdout}"

    def test_make_version_passes(self):
        result = subprocess.run(
            ["make", "version"],
            capture_output=True, text=True, cwd=str(ROOT), timeout=30,
        )
        assert result.returncode == 0, f"make version failed:\n{result.stderr}\n{result.stdout}"
        assert "0.1.0" in result.stdout

    def test_make_ansible_syntax_passes(self):
        if not shutil.which("ansible-playbook"):
            pytest.skip("ansible-playbook not installed")
        result = subprocess.run(
            ["make", "ansible-syntax"],
            capture_output=True, text=True, cwd=str(ROOT), timeout=60,
        )
        assert result.returncode == 0, f"make ansible-syntax failed:\n{result.stderr}\n{result.stdout}"


class TestBashGuardrailConfig:
    def test_opencode_json_exists(self):
        assert OPENCODE_JSON.exists(), "opencode.json must exist"

    def test_opencode_json_has_bash_permission(self):
        cfg = json.loads(OPENCODE_JSON.read_text())
        assert "permission" in cfg
        assert "bash" in cfg["permission"]
        assert cfg["permission"]["bash"].get("make *") == "allow"
        assert cfg["permission"]["bash"].get("*") == "deny"

    def test_opencode_json_has_plugin(self):
        cfg = json.loads(OPENCODE_JSON.read_text())
        assert "plugin" in cfg
        assert any("enforce-make" in p for p in cfg["plugin"])

    def test_opencode_json_has_schema(self):
        cfg = json.loads(OPENCODE_JSON.read_text())
        assert "$schema" in cfg
        assert "opencode.ai/config.json" in cfg["$schema"]


class TestBashGuardrailPlugin:
    def test_plugin_file_exists(self):
        assert PLUGIN_FILE.exists(), "enforce-make.ts plugin must exist"

    def test_plugin_exports_default(self):
        content = PLUGIN_FILE.read_text()
        assert "export default" in content
        assert "tool.execute.before" in content
        assert "input.tool" in content

    def test_plugin_checks_bash_tool(self):
        content = PLUGIN_FILE.read_text()
        assert '"bash"' in content

    def test_plugin_checks_make_prefix(self):
        content = PLUGIN_FILE.read_text()
        assert "make " in content
        assert "throw new Error" in content

    def test_plugin_provides_helpful_error_message(self):
        content = PLUGIN_FILE.read_text()
        assert "BLOCKED" in content
        assert "Makefile" in content
        assert "AGENTS.md" in content

    def test_plugin_allows_make_commands(self):
        content = PLUGIN_FILE.read_text()
        assert "startsWith" in content or "make" in content


class TestBashGuardrailPrompting:
    def test_agents_md_exists(self):
        assert AGENTS_MD.exists(), "AGENTS.md must exist"

    def test_agents_md_has_bash_policy(self):
        content = AGENTS_MD.read_text()
        assert "make" in content.lower()
        assert "CRITICAL" in content or "MUST" in content

    def test_agents_md_has_guardrail_meta_rule(self):
        content = AGENTS_MD.read_text()
        assert "Guardrail" in content or "guardrail" in content
        assert "three" in content.lower() or "3" in content

    def test_agents_md_mentions_all_three_layers(self):
        content = AGENTS_MD.read_text()
        assert "opencode.json" in content
        assert "plugin" in content or ".opencode/plugin" in content
        assert "AGENTS.md" in content

    def test_agents_md_lists_make_targets(self):
        content = AGENTS_MD.read_text()
        assert "make test" in content
        assert "make lint" in content
        assert "make init" in content


class TestTDDGuardrail:
    def test_makefile_has_test_and_commit_target(self):
        content = MAKEFILE.read_text()
        assert "test-and-commit:" in content, "Makefile must have test-and-commit target"

    def test_plugin_emits_tdd_reminder_on_src_edit(self):
        content = PLUGIN_FILE.read_text()
        assert "TDD REMINDER" in content, "Plugin must emit TDD reminder on src/ edits"
        assert "production" in content.lower() or "src/" in content

    def test_agents_md_has_tdd_policy_section(self):
        content = AGENTS_MD.read_text()
        assert "TDD Policy" in content or "TDD policy" in content or "CRITICAL: TDD" in content
        assert "failing test" in content.lower()
        assert "BEFORE" in content

    def test_agents_md_tdd_lists_workflow_steps(self):
        content = AGENTS_MD.read_text()
        assert "Write a test" in content or "write a test" in content
        assert "make test-unit" in content

    def test_tdd_guardrail_has_all_three_layers(self):
        content_plugin = PLUGIN_FILE.read_text()
        content_agents = AGENTS_MD.read_text()
        content_config = OPENCODE_JSON.read_text()
        assert "TDD" in content_plugin or "tdd" in content_plugin.lower()
        assert "TDD" in content_agents or "tdd" in content_agents.lower()
        assert "permission" in content_config


class TestCommitAfterGreenGuardrail:
    def test_makefile_test_and_commit_runs_tests_first(self):
        content = MAKEFILE.read_text()
        tac_section = content[content.index("test-and-commit:"):content.index("test-and-commit:") + 500]
        assert "pytest" in tac_section
        assert "git commit" in tac_section

    def test_makefile_test_and_commit_rejects_if_tests_fail(self):
        content = MAKEFILE.read_text()
        tac_section = content[content.index("test-and-commit:"):content.index("test-and-commit:") + 500]
        assert "pytest" in tac_section
        lines = tac_section.split("\n")
        pytest_line = None
        commit_line = None
        for i, line in enumerate(lines):
            if "pytest" in line:
                pytest_line = i
            if "git commit" in line:
                commit_line = i
        if pytest_line is not None and commit_line is not None:
            assert pytest_line < commit_line, "Tests must run before commit in test-and-commit target"

    def test_makefile_test_and_commit_supports_custom_msg(self):
        content = MAKEFILE.read_text()
        assert 'MSG' in content, "test-and-commit should support MSG variable"
        tac_start = content.index("test-and-commit:")
        tac_end = content.index("\n\n", tac_start) if "\n\n" in content[tac_start:] else len(content)
        tac_section = content[tac_start:tac_end]
        assert "$(MSG)" in tac_section

    def test_plugin_emits_commit_reminder_after_test_pass(self):
        content = PLUGIN_FILE.read_text()
        assert "COMMIT REMINDER" in content, "Plugin must remind to commit after test pass"
        assert "test-and-commit" in content or "uncommitted" in content.lower()

    def test_agents_md_has_commit_policy_section(self):
        content = AGENTS_MD.read_text()
        assert (
            ("Commit" in content and "Green" in content)
            or ("commit" in content.lower() and "green" in content.lower())
        )
        assert "test-and-commit" in content
        assert "uncommitted" in content.lower() or "MUST commit" in content

    def test_commit_guardrail_has_all_three_layers(self):
        content_plugin = PLUGIN_FILE.read_text()
        content_agents = AGENTS_MD.read_text()
        content_makefile = MAKEFILE.read_text()
        assert "COMMIT REMINDER" in content_plugin
        assert "commit" in content_agents.lower()
        assert "test-and-commit" in content_makefile


class TestTaskCompletionGuardrail:
    def test_agents_md_has_completion_policy(self):
        content = AGENTS_MD.read_text()
        assert "Task Completion Policy" in content
        assert "CRITICAL" in content
        assert "ALL requested work" in content

    def test_agents_md_completion_policy_forbids_early_stop(self):
        content = AGENTS_MD.read_text()
        assert "Do NOT stop early" in content
        assert "Do NOT get sidetracked" in content

    def test_plugin_injects_system_prompt(self):
        content = PLUGIN_FILE.read_text()
        assert "Task Completion Policy" in content or "completion" in content.lower()
        assert "experimental.chat.system.transform" in content

    def test_plugin_warns_about_task_completion(self):
        content = PLUGIN_FILE.read_text()
        assert "TASK COMPLETION CHECK" in content or "RESUME WORK" in content

    def test_completion_guardrail_has_all_three_layers(self):
        content_plugin = PLUGIN_FILE.read_text()
        content_agents = AGENTS_MD.read_text()
        assert "completion" in content_plugin.lower() or "RESUME" in content_plugin
        assert "completion" in content_agents.lower() or "Task Completion" in content_agents


class TestGuardrailSkill:
    def test_skill_file_exists(self):
        assert SKILL_FILE.exists(), "guardrail-pattern skill SKILL.md must exist"

    def test_skill_has_frontmatter(self):
        content = SKILL_FILE.read_text()
        assert "---" in content
        assert "name:" in content
        assert "description:" in content

    def test_skill_documents_three_layers(self):
        content = SKILL_FILE.read_text()
        assert "Config" in content or "config" in content
        assert "Runtime" in content or "runtime" in content or "hook" in content
        assert "Agent" in content or "prompt" in content

    def test_skill_has_checklist(self):
        content = SKILL_FILE.read_text()
        assert "Checklist" in content or "checklist" in content or "[ ]" in content


class TestSkeletonScript:
    def test_skeleton_script_exists(self):
        assert SKELETON_SCRIPT.exists()

    def test_skeleton_creates_directories(self, tmp_path):
        result = subprocess.run(
            ["python3", str(SKELETON_SCRIPT)],
            capture_output=True, text=True, cwd=str(tmp_path),
            timeout=30,
        )
        assert result.returncode == 0

    def test_skeleton_creates_init_files(self, tmp_path):
        subprocess.run(
            ["python3", str(SKELETON_SCRIPT)],
            capture_output=True, text=True, cwd=str(tmp_path),
            timeout=30,
        )
        src_dir = tmp_path / "src" / "agentic_harness"
        if src_dir.exists():
            inits = list(src_dir.rglob("__init__.py"))
            assert len(inits) > 0, "skeleton should create __init__.py files"


class TestMakeTargetSmokeTests:
    def test_make_qa_passes(self):
        if not shutil.which("ansible-playbook"):
            pytest.skip("ansible-playbook not installed")
        result = subprocess.run(
            ["make", "qa"],
            capture_output=True, text=True, cwd=str(ROOT), timeout=300,
        )
        assert result.returncode == 0, f"make qa failed:\n{result.stderr}\n{result.stdout}"
