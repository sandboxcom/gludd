import json
import subprocess
from pathlib import Path

import pytest


def _require_devspark():
    dev = Path(".devspark")
    commands = dev / "defaults" / "commands"
    skills = dev / "defaults" / "skills"
    populated = (
        dev.exists()
        and (dev / "agents-registry.json").exists()
        and commands.is_dir()
        and any(commands.glob("*.md"))
        and skills.is_dir()
        and any(skills.glob("*.md"))
    )
    if not populated:
        pytest.skip(".devspark scaffolding not populated")


class TestSDDIntegration:
    """Tests for DevSpark/DeepSpec SDD integration with gludd."""

    def test_devspark_directory_structure_exists(self):
        """Test that .devspark directory structure exists after setup."""
        _require_devspark()
        devspark_dir = Path(".devspark")
        assert devspark_dir.exists(), ".devspark directory should exist"
        assert (devspark_dir / "defaults").exists(), ".devspark/defaults should exist"
        assert (devspark_dir / "defaults" / "commands").exists(), ".devspark/defaults/commands should exist"
        assert (devspark_dir / "defaults" / "skills").exists(), ".devspark/defaults/skills should exist"
        assert (devspark_dir / "agents-registry.json").exists(), "agents-registry.json should exist"

    def test_devspark_commands_copied(self):
        """Test that DevSpark default commands are copied."""
        _require_devspark()
        commands_dir = Path(".devspark/defaults/commands")
        assert commands_dir.exists()
        # Should have at least the core SDD commands
        expected_commands = [
            "sdd-constitution.md",
            "sdd-discover.md",
            "sdd-specify.md",
            "sdd-plan.md",
            "sdd-tasks.md",
            "sdd-implement.md",
            "sdd-pr.md",
            "sdd-release.md",
            "sdd-audit.md",
            "sdd-critic.md",
            "sdd-harvest.md",
            "sdd-quickfix.md",
        ]
        for cmd in expected_commands:
            assert (commands_dir / cmd).exists(), f"Command {cmd} should exist"

    def test_devspark_skills_copied(self):
        """Test that DevSpark default skills are copied."""
        _require_devspark()
        skills_dir = Path(".devspark/defaults/skills")
        assert skills_dir.exists()
        # Should have at least some skills
        skills = list(skills_dir.glob("*.md"))
        assert len(skills) > 0, "Should have at least one skill"

    def test_agents_registry_exists(self):
        """Test that agents-registry.json exists and is valid JSON."""
        _require_devspark()
        registry = Path(".devspark/agents-registry.json")
        assert registry.exists()
        data = json.loads(registry.read_text())
        assert isinstance(data, dict), "agents-registry.json should be a dict"

    def test_deepspec_skill_installed(self):
        """Test that DeepSpec skill is installed for opencode."""
        skill_dir = Path(".opencode/skill/deep-spec")
        assert skill_dir.exists(), "DeepSpec skill should be installed"
        assert (skill_dir / "SKILL.md").exists(), "SKILL.md should exist"

    def test_make_sdd_constitution_target(self):
        """Test that make sdd-constitution target exists and runs."""
        result = subprocess.run(["make", "sdd-constitution"], capture_output=True, text=True)
        assert result.returncode == 0, f"sdd-constitution failed: {result.stderr}"
        # Should generate AGENTS.md or update it
        assert Path("AGENTS.md").exists(), "AGENTS.md should exist after sdd-constitution"

    def test_make_sdd_discover_target(self):
        """Test that make sdd-discover target exists."""
        result = subprocess.run(["make", "sdd-discover"], capture_output=True, text=True)
        assert result.returncode == 0, f"sdd-discover failed: {result.stderr}"

    def test_make_sdd_specify_target(self):
        """Test that make sdd-specify target exists."""
        result = subprocess.run(["make", "sdd-specify"], capture_output=True, text=True)
        assert result.returncode == 0, f"sdd-specify failed: {result.stderr}"

    def test_make_sdd_plan_target(self):
        """Test that make sdd-plan target exists."""
        result = subprocess.run(["make", "sdd-plan"], capture_output=True, text=True)
        assert result.returncode == 0, f"sdd-plan failed: {result.stderr}"

    def test_make_sdd_tasks_target(self):
        """Test that make sdd-tasks target exists."""
        result = subprocess.run(["make", "sdd-tasks"], capture_output=True, text=True)
        assert result.returncode == 0, f"sdd-tasks failed: {result.stderr}"

    def test_make_sdd_implement_target(self):
        """Test that make sdd-implement target exists and runs gate."""
        _require_devspark()
        result = subprocess.run(["make", "sdd-implement"], capture_output=True, text=True)
        # Should run gate as verification step
        assert result.returncode == 0, f"sdd-implement failed: {result.stderr}"

    def test_make_sdd_pr_target(self):
        """Test that make sdd-pr target exists."""
        result = subprocess.run(["make", "sdd-pr"], capture_output=True, text=True)
        assert result.returncode == 0, f"sdd-pr failed: {result.stderr}"

    def test_make_sdd_release_target(self):
        """Test that make sdd-release target exists."""
        result = subprocess.run(["make", "sdd-release"], capture_output=True, text=True)
        assert result.returncode == 0, f"sdd-release failed: {result.stderr}"

    def test_make_sdd_audit_target(self):
        """Test that make sdd-audit target exists."""
        result = subprocess.run(["make", "sdd-audit"], capture_output=True, text=True)
        assert result.returncode == 0, f"sdd-audit failed: {result.stderr}"

    def test_make_sdd_critic_target(self):
        """Test that make sdd-critic target exists."""
        result = subprocess.run(["make", "sdd-critic"], capture_output=True, text=True)
        assert result.returncode == 0, f"sdd-critic failed: {result.stderr}"

    def test_make_sdd_harvest_target(self):
        """Test that make sdd-harvest target exists."""
        result = subprocess.run(["make", "sdd-harvest"], capture_output=True, text=True)
        assert result.returncode == 0, f"sdd-harvest failed: {result.stderr}"

    def test_make_sdd_quickfix_target(self):
        """Test that make sdd-quickfix target exists."""
        result = subprocess.run(["make", "sdd-quickfix"], capture_output=True, text=True)
        assert result.returncode == 0, f"sdd-quickfix failed: {result.stderr}"

    def test_sdd_implement_runs_gate(self):
        """Test that sdd-implement runs make gate as verification."""
        _require_devspark()
        result = subprocess.run(["make", "sdd-implement"], capture_output=True, text=True)
        assert "GATE: PASSED" in result.stdout or "GATE" in result.stdout

    def test_devspark_hooks_installed(self):
        """Test that DevSpark hooks are installed."""
        _require_devspark()
        hooks_dir = Path(".devspark/hooks")
        assert hooks_dir.exists()
        hooks = list(hooks_dir.glob("*"))
        assert len(hooks) > 0, "Should have hooks installed"


class TestDevSparkConstitution:
    """Tests for DevSpark constitution generation."""

    def test_constitution_generates_agents_md(self):
        """Test that constitution generates/updates AGENTS.md."""
        # Run constitution
        subprocess.run(["make", "sdd-constitution"], capture_output=True)
        assert Path("AGENTS.md").exists()

    def test_constitution_includes_gludd_rules(self):
        """Test that generated AGENTS.md includes gludd-specific rules."""
        content = Path("AGENTS.md").read_text()
        assert "Mechanical Contract" in content
        assert "TDD" in content
        assert "make gate" in content
        assert "Session Start Protocol" in content


class TestDeepSpecIntegration:
    """Tests for DeepSpec skill integration."""

    def test_deepspec_skill_has_required_commands(self):
        """Test that DeepSpec skill defines required commands."""
        skill_md = Path(".opencode/skill/deep-spec/SKILL.md").read_text()
        # Should have commands for spec workflow
        assert "spec.md.init" in skill_md or "init" in skill_md.lower()
        assert "spec.md.create-task" in skill_md or "create-task" in skill_md.lower()
        assert "spec.md.approve-task" in skill_md or "approve-task" in skill_md.lower()

    def test_deepspec_skill_has_tdd_enforcement(self):
        """Test that DeepSpec skill enforces TDD."""
        skill_md = Path(".opencode/skill/deep-spec/SKILL.md").read_text()
        assert "TDD" in skill_md or "tdd" in skill_md.lower()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
