"""Unit tests for the ornith_self_improve role + the gludd_ornith module.

TDD: structural + behavioral tests that prove the role:
1. invokes gludd_ornith for both state=pairs and state=improve,
2. opens a PR via gludd_git,
3. files a human-todo via gludd_human_todo,
4. respects max_artifacts_per_run,
5. respects require_minimum_rejection_count.

These are pytest-level structural + behavioral tests (per the W6.9 precedent:
molecule infrastructure may not be present — pytest structural validation is
the accepted fallback). The molecule scenario under
``molecule/playbooks/ornith_self_improve/`` provides the end-to-end coverage.
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml

ROOT = Path(__file__).parent.parent.parent
COLLECTION_DIR = ROOT / "collections" / "ansible_collections" / "general_ludd" / "agent"
ROLES_DIR = COLLECTION_DIR / "roles"
MODULES_DIR = COLLECTION_DIR / "plugins" / "modules"
ROLE_DIR = ROLES_DIR / "ornith_self_improve"
PLAYBOOK = ROOT / "playbooks" / "ornith_self_improve.yml"
SEED_SCRIPT = ROOT / "scripts" / "seed_ornith_self_improve_schedule.py"
MOCK_DAEMON = ROOT / "molecule" / "mock_daemon" / "server.py"
MOLECULE_SCENARIO = ROOT / "molecule" / "playbooks" / "ornith_self_improve"
OPENBAO_SCENARIO = ROOT / "molecule" / "playbooks" / "openbao_break_glass_backup"
SHARED_MOCK_DAEMON_STOP = ROOT / "molecule" / "shared" / "mock_daemon_stop.yml"


def _load_yaml(path: Path) -> object:
    return yaml.safe_load(path.read_text())


def _load_role_tasks() -> list[object]:
    main = _load_yaml(ROLE_DIR / "tasks" / "main.yml")
    improve = _load_yaml(ROLE_DIR / "tasks" / "improve-one.yml")
    assert isinstance(main, list)
    assert isinstance(improve, list)
    return list(main) + list(improve)


# ── Structural: role files exist + parse ───────────────────────────────────

class TestRoleStructure:
    def test_tasks_main_exists(self) -> None:
        assert (ROLE_DIR / "tasks" / "main.yml").is_file()

    def test_tasks_improve_one_exists(self) -> None:
        assert (ROLE_DIR / "tasks" / "improve-one.yml").is_file()

    def test_defaults_main_exists(self) -> None:
        assert (ROLE_DIR / "defaults" / "main.yml").is_file()

    def test_meta_main_exists(self) -> None:
        assert (ROLE_DIR / "meta" / "main.yml").is_file()

    def test_readme_exists(self) -> None:
        assert (ROLE_DIR / "README.md").is_file()

    def test_defaults_safe_by_default(self) -> None:
        defaults = (ROLE_DIR / "defaults" / "main.yml").read_text()
        assert "ornith_enabled: false" in defaults, (
            "ornith_self_improve must default to ornith_enabled: false"
        )

    def test_playbook_invokes_role(self) -> None:
        pb = _load_yaml(PLAYBOOK)
        assert isinstance(pb, list) and pb, "playbook must be a non-empty list"
        play = pb[0]
        roles = play.get("roles", [])
        role_names = [
            (r.get("role") if isinstance(r, dict) else r)
            for r in roles
        ]
        assert "general_ludd.agent.ornith_self_improve" in role_names, (
            "playbook must invoke general_ludd.agent.ornith_self_improve"
        )


# ── Behavioral: the role invokes the right modules ────────────────────────

class TestRoleInvokesGluddOrnith:
    """Both state=pairs and state=improve must be present."""

    def test_role_tasks_invoke_gludd_ornith(self) -> None:
        tasks = _load_role_tasks()
        joined = json.dumps(tasks, default=str)
        assert "general_ludd.agent.gludd_ornith" in joined, (
            "role must invoke general_ludd.agent.gludd_ornith"
        )
        # state=pairs is the rejection-pull half. Match either the raw YAML
        # form ("state: pairs") or the JSON-dumped form ('"state": "pairs"').
        assert (
            "state: pairs" in joined
            or '"state": "pairs"' in joined
            or "'state': 'pairs'" in joined
        ), "role must invoke gludd_ornith with state=pairs"
        # state=improve is the rollout half
        assert (
            "state: improve" in joined
            or '"state": "improve"' in joined
            or "'state': 'improve'" in joined
        ), "role must invoke gludd_ornith with state=improve"


class TestRoleOpensPrViaGluddGit:
    def test_role_tasks_open_pr_via_gludd_git(self) -> None:
        tasks = _load_role_tasks()
        joined = json.dumps(tasks, default=str)
        assert "general_ludd.agent.gludd_git" in joined, (
            "role must invoke general_ludd.agent.gludd_git"
        )
        # The role must do all three PR-shaping ops: branch, commit, push.
        # Match either raw-YAML form ("op: branch") or JSON-dumped form.
        for op in ("branch", "commit", "push"):
            assert (
                f"op: {op}" in joined
                or f'"op": "{op}"' in joined
                or f"'op': '{op}'" in joined
            ), f"role must invoke gludd_git op={op}"


class TestRoleFilesHumanTodo:
    def test_role_tasks_file_human_todo(self) -> None:
        tasks = _load_role_tasks()
        joined = json.dumps(tasks, default=str)
        assert "general_ludd.agent.gludd_human_todo" in joined, (
            "role must invoke general_ludd.agent.gludd_human_todo"
        )
        # category=decision (the human-todo is a review gate)
        assert (
            "category: decision" in joined
            or '"category": "decision"' in joined
            or "'category': 'decision'" in joined
        ), "the human-todo must be category=decision (a review gate)"
        # priority=high (the PR is NOT auto-merged)
        assert (
            "priority: high" in joined
            or '"priority": "high"' in joined
            or "'priority': 'high'" in joined
        ), "the human-todo should be high priority (the PR is NOT auto-merged)"


# ── Safety: max_artifacts_per_run + require_minimum_rejection_count ────────

class TestRoleSafetyDefaults:
    def test_role_respects_max_artifacts_per_run(self) -> None:
        """The role must cap the per-run improvement count."""
        defaults_raw = (ROLE_DIR / "defaults" / "main.yml").read_text()
        defaults = yaml.safe_load(defaults_raw)
        assert defaults["max_artifacts_per_run"] == 1, (
            "max_artifacts_per_run must default to 1 (one PR per run — never batch)"
        )
        # The candidate list is sliced to max_artifacts_per_run in tasks/main.yml
        main_raw = (ROLE_DIR / "tasks" / "main.yml").read_text()
        assert "max_artifacts_per_run" in main_raw, (
            "tasks/main.yml must reference max_artifacts_per_run when slicing candidates"
        )
        # And the improve-one.yml loop is gated on candidates being non-empty
        assert "_osi_candidates" in main_raw

    def test_role_respects_require_minimum_rejection_count(self) -> None:
        """Artifacts below the rejection threshold must be skipped."""
        defaults_raw = (ROLE_DIR / "defaults" / "main.yml").read_text()
        defaults = yaml.safe_load(defaults_raw)
        assert defaults["require_minimum_rejection_count"] == 3, (
            "require_minimum_rejection_count must default to 3"
        )
        main_raw = (ROLE_DIR / "tasks" / "main.yml").read_text()
        # The candidate filter rejects files whose count is < threshold
        assert "require_minimum_rejection_count" in main_raw
        assert "rejectattr" in main_raw or ">= require_minimum_rejection_count" in main_raw, (
            "the candidate list must filter out artifacts below the threshold"
        )

    def test_role_skips_when_ornith_disabled(self) -> None:
        main_raw = (ROLE_DIR / "tasks" / "main.yml").read_text()
        assert "ornith_enabled" in main_raw
        assert "Skip ornith_self_improve when Ornith is disabled" in main_raw, (
            "role must have an explicit graceful-skip block when ornith_enabled is false"
        )


# ── gludd_ornith module structure ──────────────────────────────────────────

class TestGluddOrnithModule:
    def test_module_file_exists(self) -> None:
        assert (MODULES_DIR / "gludd_ornith.py").is_file()

    def test_module_states_are_pairs_and_improve(self) -> None:
        src = (MODULES_DIR / "gludd_ornith.py").read_text()
        assert '"pairs"' in src and '"improve"' in src
        assert "state=pairs" in src and "state=improve" in src

    def test_module_requires_task_description_for_improve(self) -> None:
        src = (MODULES_DIR / "gludd_ornith.py").read_text()
        # The argument_spec must enforce the require_if contract
        assert '("state", "improve", ["task_description"])' in src

    def test_module_psk_is_no_log(self) -> None:
        src = (MODULES_DIR / "gludd_ornith.py").read_text()
        assert 'no_log=True' in src

    def test_module_supports_check_mode(self) -> None:
        src = (MODULES_DIR / "gludd_ornith.py").read_text()
        assert "supports_check_mode=True" in src


# ── Seed script + molecule scenario ────────────────────────────────────────

class TestSeedScript:
    def test_seed_script_exists(self) -> None:
        assert SEED_SCRIPT.is_file()

    def test_seed_script_skips_when_ornith_disabled(self) -> None:
        src = SEED_SCRIPT.read_text()
        assert "ornith_enabled" in src
        assert "skipping" in src

    def test_seed_script_uses_monday_cron(self) -> None:
        src = SEED_SCRIPT.read_text()
        # Monday 04:00 UTC by default
        assert "0 4 * * 1" in src

    def test_seed_script_idempotent(self) -> None:
        src = SEED_SCRIPT.read_text()
        assert "already registered" in src


class TestMoleculeScenario:
    def test_scenario_exists(self) -> None:
        assert MOLECULE_SCENARIO.is_dir()
        assert (MOLECULE_SCENARIO / "molecule.yml").is_file()
        assert (MOLECULE_SCENARIO / "default" / "prepare.yml").is_file()
        assert (MOLECULE_SCENARIO / "default" / "converge.yml").is_file()
        assert (MOLECULE_SCENARIO / "default" / "verify.yml").is_file()

    def test_verify_asserts_patch_and_endpoints(self) -> None:
        verify = (MOLECULE_SCENARIO / "default" / "verify.yml").read_text()
        # Patch file
        assert "proposed-agent_orchestrate.yml.patch" in verify
        # Endpoint-hit assertions
        assert "/admin/ornith/pairs" in verify
        assert "/api/human-todos" in verify
        assert "/admin/models/call" in verify

    def test_converge_enables_ornith(self) -> None:
        converge = (MOLECULE_SCENARIO / "default" / "converge.yml").read_text()
        assert "ornith_enabled: true" in converge

    def test_mock_daemon_lifecycle_is_isolated_from_preceding_openbao(self) -> None:
        """The manifest-owned Ornith daemon cannot collide with legacy OpenBao."""
        ornith = _load_yaml(MOLECULE_SCENARIO / "molecule.yml")
        openbao = _load_yaml(OPENBAO_SCENARIO / "molecule.yml")
        assert isinstance(ornith, dict)
        assert isinstance(openbao, dict)

        ornith_provisioner = ornith["provisioner"]
        openbao_provisioner = openbao["provisioner"]
        assert isinstance(ornith_provisioner, dict)
        assert isinstance(openbao_provisioner, dict)
        ornith_env = ornith_provisioner["env"]
        openbao_env = openbao_provisioner["env"]
        assert isinstance(ornith_env, dict)
        assert isinstance(openbao_env, dict)
        assert "GLUDD_MOCK_PORT" not in ornith_env, (
            "Ornith must bind port 0 and consume its private ready manifest"
        )
        assert openbao_env["GLUDD_MOCK_PORT"] == "8794", (
            "the adjacent legacy scenario's fixed port must stay explicit"
        )

        ornith_playbooks = ornith_provisioner["playbooks"]
        assert isinstance(ornith_playbooks, dict)
        assert ornith_playbooks["cleanup"].endswith("/mock_daemon_cleanup.yml")
        assert ornith_playbooks["destroy"].endswith("/mock_daemon_destroy.yml")
        ornith_scenario = ornith["scenario"]
        assert isinstance(ornith_scenario, dict)
        ornith_sequence = ornith_scenario["test_sequence"]
        assert ornith_sequence[:2] == ["cleanup", "destroy"]
        assert ornith_sequence[-2:] == ["cleanup", "destroy"]
        ornith_verify = (MOLECULE_SCENARIO / "default" / "verify.yml").read_text()
        assert "kill $(cat" not in ornith_verify

        stop = SHARED_MOCK_DAEMON_STOP.read_text()
        assert "_gludd_mock_owned" in stop
        assert "ansible.builtin.wait_for" in stop, (
            "cleanup must prove the owned daemon released its port before returning"
        )

        openbao_playbooks = openbao_provisioner["playbooks"]
        assert isinstance(openbao_playbooks, dict)
        assert openbao_playbooks["cleanup"].endswith("/molecule/shared/cleanup.yml")
        openbao_scenario = openbao["scenario"]
        assert isinstance(openbao_scenario, dict)
        openbao_sequence = openbao_scenario["test_sequence"]
        assert openbao_sequence[0] == "cleanup"
        assert openbao_sequence[-1] == "cleanup"
        openbao_verify = (OPENBAO_SCENARIO / "default" / "verify.yml").read_text()
        assert "kill $(cat" not in openbao_verify

        openbao_prepare = _load_yaml(
            OPENBAO_SCENARIO / "default" / "prepare.yml"
        )
        assert isinstance(openbao_prepare, list)
        tasks = openbao_prepare[0]["tasks"]
        launch_index = next(
            index
            for index, task in enumerate(tasks)
            if task["name"] == "Launch mock daemon (background, nohup)"
        )
        assert all(
            "{{ pidfile }}" not in json.dumps(task)
            for task in tasks[launch_index + 1 :]
        ), "prepare must preserve the launched daemon's ownership pidfile"


class TestMockDaemonExtensions:
    def test_mock_serves_ornith_pairs(self) -> None:
        src = MOCK_DAEMON.read_text()
        assert "/admin/ornith/pairs" in src
        assert "ORNITH_PAIRS_SNAPSHOT" in src

    def test_mock_serves_human_todos(self) -> None:
        src = MOCK_DAEMON.read_text()
        assert "/api/human-todos" in src
        assert "_human_todo_created" in src

    def test_mock_returns_three_rejected_pairs_for_same_artifact(self) -> None:
        """So the role's threshold of 3 is met and the artifact is picked."""
        src = MOCK_DAEMON.read_text()
        # Three pairs, all rejected-status, all targeting the same artifact
        assert src.count("rejected_by_gate") >= 1
        assert src.count("rejected_by_review") >= 1
        assert src.count("reverted") >= 1
        assert src.count("playbooks/agent_orchestrate.yml") >= 3
