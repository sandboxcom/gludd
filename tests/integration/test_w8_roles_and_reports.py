"""W8: AI-coding-agent task roles + audit/report roles structural tests.

TDD tests that assert:
1. Every new role has the required files (tasks/defaults/meta/README).
2. Every new playbook parses + passes manifest extraction + ActionPolicy.
3. Audit/report roles reference gludd_facts in their tasks/main.yml.
4. agent_coordination_demo.yml references gludd_message send and receive.
5. system_report.yml references report_status, report_metrics, report_audit roles.

This is pytest-level structural validation (molecule infrastructure is not
present in this repo — this is the accepted fallback per W6.9 decision).
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from general_ludd.ansible.action_policy import (
    ActionPolicyConfig,
    validate_action,
)
from general_ludd.ansible.manifest import generate_manifest

ROOT = Path(__file__).parent.parent.parent
COLLECTION_DIR = ROOT / "collections" / "ansible_collections" / "general_ludd" / "agent"
ROLES_DIR = COLLECTION_DIR / "roles"
PLAYBOOKS_DIR = ROOT / "playbooks"

# ── Deliverable A: AI-coding-agent task roles ──────────────────────────────

AGENT_TASK_ROLES = [
    "implement_change",
    "write_tests",
    "triage_issue",
    "refactor_code",
    "debug_failure",
    "document_change",
    "dependency_update",
]

# ── Deliverable B: Audit + report roles ───────────────────────────────────

AUDIT_REPORT_ROLES = [
    "audit_security",
    "audit_dependencies",
    "report_status",
    "report_metrics",
    "report_audit",
]

ALL_NEW_ROLES = AGENT_TASK_ROLES + AUDIT_REPORT_ROLES

# ── Deliverable C: New playbooks ──────────────────────────────────────────

NEW_PLAYBOOKS = [
    "agent_coordination_demo.yml",
    "system_report.yml",
]


class TestRoleStructure:
    """Every new role must have the four required files."""

    @pytest.mark.parametrize("role_name", ALL_NEW_ROLES)
    def test_role_tasks_main_exists(self, role_name: str):
        p = ROLES_DIR / role_name / "tasks" / "main.yml"
        assert p.is_file(), f"roles/{role_name}/tasks/main.yml missing"

    @pytest.mark.parametrize("role_name", ALL_NEW_ROLES)
    def test_role_defaults_main_exists(self, role_name: str):
        p = ROLES_DIR / role_name / "defaults" / "main.yml"
        assert p.is_file(), f"roles/{role_name}/defaults/main.yml missing"

    @pytest.mark.parametrize("role_name", ALL_NEW_ROLES)
    def test_role_meta_main_exists(self, role_name: str):
        p = ROLES_DIR / role_name / "meta" / "main.yml"
        assert p.is_file(), f"roles/{role_name}/meta/main.yml missing"

    @pytest.mark.parametrize("role_name", ALL_NEW_ROLES)
    def test_role_readme_exists(self, role_name: str):
        p = ROLES_DIR / role_name / "README.md"
        assert p.is_file(), f"roles/{role_name}/README.md missing"

    @pytest.mark.parametrize("role_name", ALL_NEW_ROLES)
    def test_role_tasks_is_valid_yaml(self, role_name: str):
        p = ROLES_DIR / role_name / "tasks" / "main.yml"
        if not p.is_file():
            pytest.skip(f"tasks/main.yml for {role_name} not yet created")
        parsed = yaml.safe_load(p.read_text())
        assert isinstance(parsed, list), (
            f"roles/{role_name}/tasks/main.yml must be a list of tasks"
        )


class TestAgentTaskRolesUseGluddFacts:
    """Every agent task role must gather live facts via gludd_facts."""

    @pytest.mark.parametrize("role_name", AGENT_TASK_ROLES)
    def test_role_references_gludd_facts(self, role_name: str):
        p = ROLES_DIR / role_name / "tasks" / "main.yml"
        if not p.is_file():
            pytest.skip(f"tasks/main.yml for {role_name} not yet created")
        content = p.read_text()
        assert "gludd_facts" in content, (
            f"roles/{role_name}/tasks/main.yml must reference gludd_facts "
            "to gather live system facts"
        )

    @pytest.mark.parametrize("role_name", AGENT_TASK_ROLES)
    def test_role_has_enable_flag_for_destructive_ops(self, role_name: str):
        """Roles that may mutate must default to safe (push/PR disabled)."""
        p = ROLES_DIR / role_name / "defaults" / "main.yml"
        if not p.is_file():
            pytest.skip(f"defaults/main.yml for {role_name} not yet created")
        content = p.read_text()
        # All agent task roles must have an enable_git_push=false default
        assert "enable_git_push" in content, (
            f"roles/{role_name}/defaults/main.yml must declare enable_git_push "
            "(must default to false — no pushes without explicit opt-in)"
        )
        assert "false" in content.lower(), (
            f"roles/{role_name}/defaults/main.yml enable_git_push must default to false"
        )


class TestAgentTaskRolesUseGluddMessage:
    """Agent task roles that coordinate must reference gludd_message."""

    @pytest.mark.parametrize("role_name", [
        "triage_issue",      # announces work via message
        "implement_change",  # announces completion
        "debug_failure",     # sends diagnosis
    ])
    def test_role_references_gludd_message(self, role_name: str):
        p = ROLES_DIR / role_name / "tasks" / "main.yml"
        if not p.is_file():
            pytest.skip(f"tasks/main.yml for {role_name} not yet created")
        content = p.read_text()
        assert "gludd_message" in content, (
            f"roles/{role_name}/tasks/main.yml must reference gludd_message "
            "for inter-agent coordination"
        )


class TestAuditReportRolesUseGluddFacts:
    """Audit/report roles must reference gludd_facts — they derive data from it."""

    @pytest.mark.parametrize("role_name", AUDIT_REPORT_ROLES)
    def test_report_role_references_gludd_facts(self, role_name: str):
        p = ROLES_DIR / role_name / "tasks" / "main.yml"
        if not p.is_file():
            pytest.skip(f"tasks/main.yml for {role_name} not yet created")
        content = p.read_text()
        assert "gludd_facts" in content, (
            f"roles/{role_name}/tasks/main.yml must reference gludd_facts "
            "to derive report data from live system state"
        )

    @pytest.mark.parametrize("role_name", AUDIT_REPORT_ROLES)
    def test_report_role_writes_json_artifact(self, role_name: str):
        """Report roles must write a JSON artifact (the structured output)."""
        p = ROLES_DIR / role_name / "tasks" / "main.yml"
        if not p.is_file():
            pytest.skip(f"tasks/main.yml for {role_name} not yet created")
        content = p.read_text()
        # Must write an artifact to artifact_dir
        assert "artifact_dir" in content, (
            f"roles/{role_name}/tasks/main.yml must use artifact_dir "
            "to write structured report output"
        )

    @pytest.mark.parametrize("role_name", ["audit_security", "audit_dependencies"])
    def test_audit_role_never_mutates(self, role_name: str):
        """Audit roles must be report-only — no git commits, no todo updates."""
        p = ROLES_DIR / role_name / "tasks" / "main.yml"
        if not p.is_file():
            pytest.skip(f"tasks/main.yml for {role_name} not yet created")
        content = p.read_text()
        # Must not contain git commit or todo_update_status operations
        assert "gludd_git" not in content, (
            f"roles/{role_name} is report-only: must not use gludd_git (no mutations)"
        )
        assert "todo_update_status" not in content, (
            f"roles/{role_name} is report-only: must not use todo_update_status"
        )


class TestNewPlaybooksStructure:
    """New playbooks must be valid YAML with the required play structure."""

    @pytest.mark.parametrize("playbook_name", NEW_PLAYBOOKS)
    def test_playbook_exists(self, playbook_name: str):
        p = PLAYBOOKS_DIR / playbook_name
        assert p.is_file(), f"playbooks/{playbook_name} missing"

    @pytest.mark.parametrize("playbook_name", NEW_PLAYBOOKS)
    def test_playbook_is_valid_yaml(self, playbook_name: str):
        p = PLAYBOOKS_DIR / playbook_name
        if not p.is_file():
            pytest.skip(f"{playbook_name} not yet created")
        parsed = yaml.safe_load(p.read_text())
        assert isinstance(parsed, list), f"{playbook_name} must be a list of plays"
        assert len(parsed) > 0, f"{playbook_name} must have at least one play"

    @pytest.mark.parametrize("playbook_name", NEW_PLAYBOOKS)
    def test_playbook_has_hosts_and_gather_facts_false(self, playbook_name: str):
        p = PLAYBOOKS_DIR / playbook_name
        if not p.is_file():
            pytest.skip(f"{playbook_name} not yet created")
        plays = yaml.safe_load(p.read_text()) or []
        for i, play in enumerate(plays):
            if not isinstance(play, dict):
                continue
            assert "hosts" in play, f"{playbook_name} play[{i}] missing 'hosts'"
            assert play.get("gather_facts") is False, (
                f"{playbook_name} play[{i}] must have gather_facts: false"
            )

    @pytest.mark.parametrize("playbook_name", NEW_PLAYBOOKS)
    def test_manifest_extraction_succeeds(self, playbook_name: str):
        p = PLAYBOOKS_DIR / playbook_name
        if not p.is_file():
            pytest.skip(f"{playbook_name} not yet created")
        manifest = generate_manifest(str(p))
        assert manifest.playbook == playbook_name

    @pytest.mark.parametrize("playbook_name", NEW_PLAYBOOKS)
    def test_action_policy_allows_playbook(self, playbook_name: str):
        p = PLAYBOOKS_DIR / playbook_name
        if not p.is_file():
            pytest.skip(f"{playbook_name} not yet created")
        policy = ActionPolicyConfig(enabled=True, default_mode="allow")
        manifest = generate_manifest(str(p))
        result = validate_action(policy, manifest)
        assert result.allowed, f"Policy denied {playbook_name}: {result.reason}"


class TestAgentCoordinationDemo:
    """agent_coordination_demo.yml must prove facts-as-message-queue inter-agent channel."""

    PLAYBOOK = PLAYBOOKS_DIR / "agent_coordination_demo.yml"

    def test_playbook_exists(self):
        assert self.PLAYBOOK.is_file(), "agent_coordination_demo.yml missing"

    def test_references_gludd_facts(self):
        if not self.PLAYBOOK.is_file():
            pytest.skip("playbook not yet created")
        content = self.PLAYBOOK.read_text()
        assert "gludd_facts" in content, (
            "agent_coordination_demo.yml must use gludd_facts "
            "to demonstrate fact-based coordination"
        )

    def test_references_gludd_message_send(self):
        if not self.PLAYBOOK.is_file():
            pytest.skip("playbook not yet created")
        content = self.PLAYBOOK.read_text()
        assert "gludd_message" in content, (
            "agent_coordination_demo.yml must use gludd_message"
        )
        assert "send" in content, (
            "agent_coordination_demo.yml must demonstrate send"
        )

    def test_references_gludd_message_receive(self):
        if not self.PLAYBOOK.is_file():
            pytest.skip("playbook not yet created")
        content = self.PLAYBOOK.read_text()
        assert "receive" in content, (
            "agent_coordination_demo.yml must demonstrate receive "
            "so role B picks up what role A sent"
        )


class TestSystemReportPlaybook:
    """system_report.yml must wire report_status + report_metrics + report_audit."""

    PLAYBOOK = PLAYBOOKS_DIR / "system_report.yml"

    def test_playbook_exists(self):
        assert self.PLAYBOOK.is_file(), "system_report.yml missing"

    def test_references_report_status_role(self):
        if not self.PLAYBOOK.is_file():
            pytest.skip("playbook not yet created")
        content = self.PLAYBOOK.read_text()
        assert "report_status" in content, (
            "system_report.yml must include report_status role"
        )

    def test_references_report_metrics_role(self):
        if not self.PLAYBOOK.is_file():
            pytest.skip("playbook not yet created")
        content = self.PLAYBOOK.read_text()
        assert "report_metrics" in content, (
            "system_report.yml must include report_metrics role"
        )

    def test_references_report_audit_role(self):
        if not self.PLAYBOOK.is_file():
            pytest.skip("playbook not yet created")
        content = self.PLAYBOOK.read_text()
        assert "report_audit" in content, (
            "system_report.yml must include report_audit role"
        )
