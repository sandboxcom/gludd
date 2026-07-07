"""Integration tests for the 6 new capability-audit roles.

Covers the roles created per CAPABILITY_AUDIT_2026-07-06:
  - spec_lifecycle           (action)
  - enforce_disengage        (action)
  - agent_floor_check        (check, read-side)
  - delegate_discipline_check (check, read-side)
  - deletion_gate            (check, read-side)
  - task_deadline_check      (check, read-side)

Tests verify: directory structure, gludd_facts usage, state-file references,
fact-key assertions, artifact writing, and category-appropriate constraints
(read-only vs action).
"""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

import pytest
import yaml

ROOT = Path(__file__).parent.parent.parent
COLLECTION_DIR = ROOT / "collections" / "ansible_collections" / "general_ludd" / "agent"
ROLES_DIR = COLLECTION_DIR / "roles"

AUDIT_ROLES = [
    "spec_lifecycle",
    "enforce_disengage",
    "agent_floor_check",
    "delegate_discipline_check",
    "deletion_gate",
    "task_deadline_check",
]

CHECK_ROLES = {
    "agent_floor_check": {
        "state_file_vars": ["block_counter_path", "plugin_alive_path", "liveness_probe_path"],
        "defaults_keys": [
            "agent_floor", "block_counter_path", "plugin_alive_path",
            "liveness_probe_path", "fail_on_breach", "emit_alert_message",
            "repo_path",
        ],
        "artifact_keys": [
            "status", "live_count", "floor", "block_counter",
            "plugin_last_seen", "breach_duration_sec", "breached",
        ],
        "category": "check_readside",
    },
    "delegate_discipline_check": {
        "state_file_vars": ["model_util_path", "mainthread_streak_path", "worktree_root"],
        "defaults_keys": [
            "model_util_path", "mainthread_streak_path",
            "worktree_cap", "worktree_root",
            "sonnet_target_share", "disk_free_min_mb",
            "fail_on_critical",
        ],
        "artifact_keys": [
            "status", "dimensions_breached", "breach_count",
            "model", "streak", "disk", "worktree",
        ],
        "category": "check_readside",
    },
    "deletion_gate": {
        "state_file_vars": [],
        "defaults_keys": [
            "target_file", "baseline_lines", "current_lines",
            "deletion_reason", "threshold_pct", "absolute_threshold",
            "fail_closed", "audit_log_path",
        ],
        "artifact_keys": [
            "target_file", "verdict", "removed_lines", "removed_pct",
            "baseline_lines", "current_lines", "fail_closed",
            "deletion_reason",
        ],
        "category": "check_readside",
    },
    "task_deadline_check": {
        "state_file_vars": ["task_deadlines_path", "task_stale_path", "task_killed_path"],
        "defaults_keys": [
            "task_deadlines_path", "task_stale_path", "task_killed_path",
            "task_timeout_ms", "fail_on_breach", "emit_alert_message",
        ],
        "artifact_keys": [
            "status", "total_tasks", "breached_count", "breached_tasks",
            "stale_count", "killed_count", "timeout_ms",
            "deadlines_path", "stale_path", "killed_path",
        ],
        "category": "check_readside",
    },
}

ACTION_ROLES = {
    "spec_lifecycle": {
        "defaults_keys": [
            "operation", "spec_root", "task_name", "task_size",
            "discard_reason", "enable_model_call", "enable_git_write",
            "capability_role",
        ],
        "valid_operations": [
            "init", "create_task", "approve_task", "complete_task",
            "discard_task", "list", "revise", "repair",
            "interview", "map_codebase", "diagram_architecture",
        ],
        "artifact_operations": {
            "init": {"status": "initialized", "keys": ["spec_root"]},
            "create_task": {"status": "drafted", "keys": ["task_name", "stage", "size"]},
            "approve_task": {"status": "approved", "keys": ["task_name", "stage"]},
            "complete_task": {"status": "archived", "keys": ["task_name", "stage"]},
            "discard_task": {"status": "discarded", "keys": ["task_name", "reason", "stage"]},
            "list": {"keys": ["drafts", "active", "archive", "totals"]},
            "repair": {"status": "tracking_realigned", "keys": ["spec_root"]},
        },
        "category": "action",
    },
    "enforce_disengage": {
        "defaults_keys": [
            "disengage_duration_hours", "disengage_max_hours",
            "disengage_file_path", "block_counter_path",
            "skip_pre_checks", "enable_commit", "commit_msg_file",
            "enable_push", "verify_branch", "repo_path",
        ],
        "artifact_keys": [
            "status", "disengage_file", "block_counter_file",
            "issued_at", "expires_at", "duration_hours", "issuer",
            "commit_enabled", "push_verify_enabled",
        ],
        "category": "action",
    },
}

ALL_ROLES = AUDIT_ROLES


def _read_tasks(role_name: str) -> str:
    p = ROLES_DIR / role_name / "tasks" / "main.yml"
    if not p.is_file():
        return ""
    return p.read_text()


def _read_defaults(role_name: str) -> str:
    p = ROLES_DIR / role_name / "defaults" / "main.yml"
    if not p.is_file():
        return ""
    return p.read_text()


def _read_meta(role_name: str) -> str:
    p = ROLES_DIR / role_name / "meta" / "main.yml"
    if not p.is_file():
        return ""
    return p.read_text()


def _read_readme(role_name: str) -> str:
    p = ROLES_DIR / role_name / "README.md"
    if not p.is_file():
        return ""
    return p.read_text()


# ── Structural tests ──────────────────────────────────────────────────────────


class TestAuditRoleStructure:
    """Every audit role must have tasks/defaults/meta/README and valid YAML."""

    @pytest.mark.parametrize("role_name", ALL_ROLES)
    def test_role_tasks_exists(self, role_name: str):
        p = ROLES_DIR / role_name / "tasks" / "main.yml"
        assert p.is_file(), f"roles/{role_name}/tasks/main.yml missing"

    @pytest.mark.parametrize("role_name", ALL_ROLES)
    def test_role_defaults_exists(self, role_name: str):
        p = ROLES_DIR / role_name / "defaults" / "main.yml"
        assert p.is_file(), f"roles/{role_name}/defaults/main.yml missing"

    @pytest.mark.parametrize("role_name", ALL_ROLES)
    def test_role_meta_exists(self, role_name: str):
        p = ROLES_DIR / role_name / "meta" / "main.yml"
        assert p.is_file(), f"roles/{role_name}/meta/main.yml missing"

    @pytest.mark.parametrize("role_name", ALL_ROLES)
    def test_role_readme_exists(self, role_name: str):
        p = ROLES_DIR / role_name / "README.md"
        assert p.is_file(), f"roles/{role_name}/README.md missing"

    @pytest.mark.parametrize("role_name", ALL_ROLES)
    def test_role_tasks_is_valid_yaml(self, role_name: str):
        content = _read_tasks(role_name)
        if not content:
            pytest.skip(f"tasks/main.yml for {role_name} not yet created")
        parsed = yaml.safe_load(content)
        assert isinstance(parsed, list), (
            f"roles/{role_name}/tasks/main.yml must be a list of tasks"
        )

    @pytest.mark.parametrize("role_name", ALL_ROLES)
    def test_role_defaults_is_valid_yaml(self, role_name: str):
        content = _read_defaults(role_name)
        if not content:
            pytest.skip(f"defaults/main.yml for {role_name} not yet created")
        parsed = yaml.safe_load(content)
        assert isinstance(parsed, dict), (
            f"roles/{role_name}/defaults/main.yml must be a mapping"
        )

    @pytest.mark.parametrize("role_name", ALL_ROLES)
    def test_role_meta_is_valid_yaml(self, role_name: str):
        content = _read_meta(role_name)
        if not content:
            pytest.skip(f"meta/main.yml for {role_name} not yet created")
        parsed = yaml.safe_load(content)
        assert isinstance(parsed, dict), (
            f"roles/{role_name}/meta/main.yml must be a mapping"
        )


# ── gludd_facts usage ─────────────────────────────────────────────────────────


class TestAuditRolesUseGluddFacts:
    """Audit roles must reference gludd_facts for system context.

    Exemption: deletion_gate is purely computational — it operates on caller-
    supplied baseline_lines/current_lines and has no daemon dependency.
    """

    ROLES_REQUIRING_FACTS: ClassVar[list[str]] = [
        r for r in ALL_ROLES if r != "deletion_gate"
    ]

    @pytest.mark.parametrize("role_name", ROLES_REQUIRING_FACTS)
    def test_role_references_gludd_facts(self, role_name: str):
        content = _read_tasks(role_name)
        if not content:
            pytest.skip(f"tasks/main.yml for {role_name} not yet created")
        assert "gludd_facts" in content, (
            f"roles/{role_name}/tasks/main.yml must reference gludd_facts "
            "to gather live system context"
        )

    def test_deletion_gate_excluded_from_gludd_facts(self):
        content = _read_tasks("deletion_gate")
        if not content:
            pytest.skip("tasks/main.yml for deletion_gate not yet created")
        assert "gludd_facts" not in content, (
            "deletion_gate is purely computational — it must NOT depend on "
            "gludd_facts (no daemon connectivity required)"
        )


# ── Check role state-file references ──────────────────────────────────────────


class TestCheckRolesReadStateFiles:
    """Each check role must reference its target plugin state-file variable(s).

    Roles reference state files via variable names (e.g. {{ model_util_path }}),
    not literal paths. The literal path is set in defaults/main.yml; the tasks
    layer consumes the variable.
    """

    @pytest.mark.parametrize(
        "role_name, state_vars",
        [(r, d["state_file_vars"]) for r, d in CHECK_ROLES.items()],
    )
    def test_role_references_state_file_variables(self, role_name: str, state_vars: list[str]):
        content = _read_tasks(role_name)
        if not content:
            pytest.skip(f"tasks/main.yml for {role_name} not yet created")
        for var_name in state_vars:
            assert var_name in content, (
                f"roles/{role_name}/tasks/main.yml must reference "
                f"state-file variable '{var_name}'"
            )


# ── Check role artifact keys ──────────────────────────────────────────────────


class TestCheckRoleArtifactKeys:
    """Each check role's tasks must emit expected gludd_facts artifact keys."""

    @pytest.mark.parametrize(
        "role_name, expected_keys",
        [(r, d["artifact_keys"]) for r, d in CHECK_ROLES.items()],
    )
    def test_artifact_emits_expected_keys(self, role_name: str, expected_keys: list[str]):
        content = _read_tasks(role_name)
        if not content:
            pytest.skip(f"tasks/main.yml for {role_name} not yet created")
        for key in expected_keys:
            assert key in content, (
                f"roles/{role_name}/tasks/main.yml must emit "
                f"artifact key '{key}' in its JSON output"
            )


# ── Defaults keys ─────────────────────────────────────────────────────────────


class TestAuditRoleDefaultsHaveExpectedKeys:
    """Each role's defaults/main.yml must declare its expected input variables."""

    @pytest.mark.parametrize(
        "role_name, expected_keys",
        [(r, d["defaults_keys"]) for r, d in {**CHECK_ROLES, **ACTION_ROLES}.items()],
    )
    def test_defaults_declares_expected_keys(self, role_name: str, expected_keys: list[str]):
        content = _read_defaults(role_name)
        if not content:
            pytest.skip(f"defaults/main.yml for {role_name} not yet created")
        for key in expected_keys:
            assert key in content, (
                f"roles/{role_name}/defaults/main.yml must declare "
                f"input variable '{key}'"
            )


# ── Check roles are read-only (never mutate) ──────────────────────────────────


class TestCheckRolesAreReadOnly:
    """Check roles must be read-only — no gludd_git, no todo_update_status."""

    @pytest.mark.parametrize("role_name", list(CHECK_ROLES))
    def test_readside_role_never_uses_git(self, role_name: str):
        content = _read_tasks(role_name)
        if not content:
            pytest.skip(f"tasks/main.yml for {role_name} not yet created")
        assert "gludd_git" not in content, (
            f"roles/{role_name} is read-side: must not use gludd_git "
            "(no git mutations)"
        )

    @pytest.mark.parametrize("role_name", list(CHECK_ROLES))
    def test_readside_role_never_uses_todo_update(self, role_name: str):
        content = _read_tasks(role_name)
        if not content:
            pytest.skip(f"tasks/main.yml for {role_name} not yet created")
        assert "todo_update_status" not in content, (
            f"roles/{role_name} is read-side: must not use todo_update_status"
        )


# ── Artifact directory usage ──────────────────────────────────────────────────


class TestAuditRolesWriteArtifacts:
    """Every audit role must write a JSON artifact to artifact_dir."""

    @pytest.mark.parametrize("role_name", ALL_ROLES)
    def test_role_uses_artifact_dir(self, role_name: str):
        content = _read_tasks(role_name)
        if not content:
            pytest.skip(f"tasks/main.yml for {role_name} not yet created")
        assert "artifact_dir" in content, (
            f"roles/{role_name}/tasks/main.yml must use artifact_dir "
            "to write structured output"
        )

    @pytest.mark.parametrize("role_name", ALL_ROLES)
    def test_defaults_declares_artifact_dir(self, role_name: str):
        content = _read_defaults(role_name)
        if not content:
            pytest.skip(f"defaults/main.yml for {role_name} not yet created")
        assert "artifact_dir" in content, (
            f"roles/{role_name}/defaults/main.yml must default artifact_dir"
        )

    @pytest.mark.parametrize("role_name", ALL_ROLES)
    def test_task_creates_artifact_directory(self, role_name: str):
        content = _read_tasks(role_name)
        if not content:
            pytest.skip(f"tasks/main.yml for {role_name} not yet created")
        has_mkdir = "file:" in content and '"{{ artifact_dir }}"' in content
        has_dir_state = "state: directory" in content
        assert has_mkdir or has_dir_state, (
            f"roles/{role_name}/tasks/main.yml must create the artifact "
            "directory before writing artifacts"
        )


# ── Action role: spec_lifecycle ───────────────────────────────────────────────


class TestSpecLifecycleRole:
    """spec_lifecycle role: Spec-Driven Development 3-stage pipeline."""

    ROLE = "spec_lifecycle"

    def test_tasks_validates_operation(self):
        content = _read_tasks(self.ROLE)
        if not content:
            pytest.skip(f"tasks/main.yml for {self.ROLE} not yet created")
        assert "Assert operation is set and valid" in content, (
            "spec_lifecycle must validate the operation variable"
        )

    def test_tasks_declares_all_valid_operations(self):
        content = _read_tasks(self.ROLE)
        if not content:
            pytest.skip(f"tasks/main.yml for {self.ROLE} not yet created")
        for op in ACTION_ROLES[self.ROLE]["valid_operations"]:
            assert op in content, (
                f"spec_lifecycle tasks must declare operation '{op}'"
            )

    def test_tasks_requires_task_name_for_scoped_ops(self):
        content = _read_tasks(self.ROLE)
        if not content:
            pytest.skip(f"tasks/main.yml for {self.ROLE} not yet created")
        assert "task_name | length > 0" in content, (
            "spec_lifecycle must assert task_name is set for scoped operations"
        )

    def test_tasks_gathers_daemon_facts(self):
        content = _read_tasks(self.ROLE)
        if not content:
            pytest.skip(f"tasks/main.yml for {self.ROLE} not yet created")
        assert "gludd_facts" in content, (
            "spec_lifecycle must gather live daemon facts for context"
        )

    def test_tasks_creates_spec_dirs_on_init(self):
        content = _read_tasks(self.ROLE)
        if not content:
            pytest.skip(f"tasks/main.yml for {self.ROLE} not yet created")
        assert "drafts" in content
        assert "active" in content
        assert "archive" in content

    def test_tasks_writes_artifact_per_operation(self):
        content = _read_tasks(self.ROLE)
        if not content:
            pytest.skip(f"tasks/main.yml for {self.ROLE} not yet created")
        artifact_ops = ACTION_ROLES[self.ROLE]["artifact_operations"]
        for op, _expected in artifact_ops.items():
            assert op in content, (
                f"spec_lifecycle must handle operation '{op}'"
            )

    def test_readme_documents_operations(self):
        content = _read_readme(self.ROLE)
        if not content:
            pytest.skip(f"README.md for {self.ROLE} not yet created")
        for op in ACTION_ROLES[self.ROLE]["valid_operations"]:
            assert op in content, (
                f"spec_lifecycle README must document operation '{op}'"
            )


# ── Action role: enforce_disengage ────────────────────────────────────────────


class TestEnforceDisengageRole:
    """enforce_disengage role: last-resort enforcement escape hatch."""

    ROLE = "enforce_disengage"

    def test_tasks_writes_disengage_signal_file(self):
        content = _read_tasks(self.ROLE)
        if not content:
            pytest.skip(f"tasks/main.yml for {self.ROLE} not yet created")
        assert "/tmp/gludd-watchdog-disengage.json" in content, (
            "enforce_disengage must write the disengage signal file"
        )

    def test_tasks_resets_block_counter(self):
        content = _read_tasks(self.ROLE)
        if not content:
            pytest.skip(f"tasks/main.yml for {self.ROLE} not yet created")
        assert "/tmp/gludd-block-counter.json" in content, (
            "enforce_disengage must reset the block counter"
        )

    def test_tasks_asserts_duration_within_bounds(self):
        content = _read_tasks(self.ROLE)
        if not content:
            pytest.skip(f"tasks/main.yml for {self.ROLE} not yet created")
        assert "disengage_duration_hours | int" in content, (
            "enforce_disengage must assert disengage window bounds"
        )
        assert "disengage_max_hours | int" in content, (
            "enforce_disengage must reference disengage_max_hours"
        )

    def test_tasks_precheck_warns_on_normal_targets(self):
        content = _read_tasks(self.ROLE)
        if not content:
            pytest.skip(f"tasks/main.yml for {self.ROLE} not yet created")
        assert "skip_pre_checks" in content, (
            "enforce_disengage must support skip_pre_checks toggle"
        )

    def test_tasks_gathers_daemon_facts(self):
        content = _read_tasks(self.ROLE)
        if not content:
            pytest.skip(f"tasks/main.yml for {self.ROLE} not yet created")
        assert "gludd_facts" in content, (
            "enforce_disengage must gather live daemon facts"
        )

    def test_tasks_writes_disengage_artifact(self):
        content = _read_tasks(self.ROLE)
        if not content:
            pytest.skip(f"tasks/main.yml for {self.ROLE} not yet created")
        assert "disengage.json" in content, (
            "enforce_disengage must write disengage.json artifact"
        )

    def test_tasks_emits_restart_reminder(self):
        content = _read_tasks(self.ROLE)
        if not content:
            pytest.skip(f"tasks/main.yml for {self.ROLE} not yet created")
        assert "restart" in content.lower(), (
            "enforce_disengage must emit a restart-reminder message"
        )


# ── Per-role meta quality checks ──────────────────────────────────────────────


class TestAuditRoleMetaQuality:
    """Each audit role's meta/main.yml must declare galaxy_info fields."""

    @pytest.mark.parametrize("role_name", ALL_ROLES)
    def test_meta_declares_galaxy_info(self, role_name: str):
        content = _read_meta(role_name)
        if not content:
            pytest.skip(f"meta/main.yml for {role_name} not yet created")
        assert "galaxy_info" in content, (
            f"roles/{role_name}/meta/main.yml must declare galaxy_info"
        )

    @pytest.mark.parametrize("role_name", ALL_ROLES)
    def test_readme_is_not_empty(self, role_name: str):
        content = _read_readme(role_name)
        if not content:
            pytest.skip(f"README.md for {role_name} not yet created")
        stripped = content.strip()
        assert len(stripped) > 50, (
            f"roles/{role_name}/README.md is too short "
            f"({len(stripped)} chars) — must have meaningful documentation"
        )

    @pytest.mark.parametrize("role_name", ALL_ROLES)
    def test_readme_describes_role_purpose(self, role_name: str):
        content = _read_readme(role_name)
        if not content:
            pytest.skip(f"README.md for {role_name} not yet created")
        assert role_name in content.lower(), (
            f"roles/{role_name}/README.md must mention the role name '{role_name}'"
        )


# ── Coverage: enforce a role exists for each audited plugin ───────────────────


class TestPluginRoleCoverage:
    """Every enforcement plugin with a read-side counterpart must have a role."""

    PLUGIN_ROLE_MAP: ClassVar[dict[str, str]] = {
        "enforce-floor.ts": "agent_floor_check",
        "enforce-delegate.ts": "delegate_discipline_check",
        "enforce-deletion-gate.ts": "deletion_gate",
        "enforce-deadline.ts": "task_deadline_check",
        "enforce-make.ts": "enforcement_gate",
        "enforce-stop.ts": "enforcement_verify",
        "watchdog.ts": "watchdog_check",
        "enforce-disengage": "enforce_disengage",
    }

    @pytest.mark.parametrize("plugin, expected_role", list(PLUGIN_ROLE_MAP.items()))
    def test_plugin_has_role_counterpart(self, plugin: str, expected_role: str):
        role_dir = ROLES_DIR / expected_role
        assert role_dir.is_dir(), (
            f"Plugin {plugin} lacks its read-side counterpart role "
            f"'{expected_role}' — directory {role_dir} does not exist"
        )
