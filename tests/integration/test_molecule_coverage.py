"""W10 molecule coverage gate.

This is the COVERAGE checklist that later passes must satisfy: every role under
the collection roles dir AND every ``gludd_*`` module must eventually have a
matching molecule scenario directory under ``molecule/playbooks/``.

Strategy so the gate is GREEN now but becomes a shrinking checklist:
  - Roles/modules that DO have a scenario are asserted present.
  - Roles/modules not yet covered are listed in ``_NOT_YET_COVERED_*`` with a
    TODO. The test asserts those sets exactly partition the inventory, so:
      * adding a scenario without removing its name here -> test fails (forces
        you to tick it off the checklist), and
      * deleting a covered scenario -> test fails (regression guard).
  - The mock-daemon harness and the three exemplar scenarios are asserted
    present so the reusable pattern cannot silently rot.

Naming convention enforced:
  - module ``gludd_<x>`` -> scenario ``molecule/playbooks/test_gludd_<x>``
  - role ``<name>``      -> scenario ``molecule/playbooks/role_<name>``
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
ROLES_DIR = ROOT / "collections" / "ansible_collections" / "general_ludd" / "agent" / "roles"
MODULES_DIR = ROOT / "collections" / "ansible_collections" / "general_ludd" / "agent" / "plugins" / "modules"
SCENARIOS_DIR = ROOT / "molecule" / "playbooks"
MOCK_DAEMON = ROOT / "molecule" / "mock_daemon" / "server.py"


def _module_names() -> set[str]:
    return {p.stem for p in MODULES_DIR.glob("gludd_*.py")}


def _role_names() -> set[str]:
    return {p.name for p in ROLES_DIR.iterdir() if p.is_dir()}


def _scenario_names() -> set[str]:
    return {p.name for p in SCENARIOS_DIR.iterdir() if p.is_dir()}


def _module_scenario(module: str) -> str:
    return f"test_{module}"


def _role_scenario(role: str) -> str:
    return f"role_{role}"


# --- The shrinking checklist -------------------------------------------------
# Modules that DO NOT yet have a test_<module> molecule scenario.
# Remove a name here the moment you add its scenario (see test below).
_NOT_YET_COVERED_MODULES: set[str] = set()
# All gludd_* modules now have molecule scenarios (W10 complete):
#   gludd_agent_run   -> test_gludd_agent_run  (port 8781, POST /admin/models/call HTTP fallback)
#   gludd_db          -> test_gludd_db          (port 8776, todo_get/update/resource_preference)
#   gludd_git         -> test_gludd_git         (port 8779, real git commit+branch on throwaway repo)
#   gludd_mcp_tool    -> test_gludd_mcp_tool    (port 8778, honest not_implemented W3.9 fence)
#   gludd_message     -> test_gludd_message     (port 8774, send/receive/ack)
#   gludd_model_call  -> test_gludd_model_call  (port 8775, POST /admin/models/call)
#   gludd_skill       -> test_gludd_skill       (port 8777, local skill render with Jinja2)
#   gludd_worktree    -> test_gludd_worktree    (port 8780, real git worktree present+absent)

# Roles that DO NOT yet have a role_<name> molecule scenario.
_NOT_YET_COVERED_ROLES: set[str] = set()
# All roles now have molecule scenarios (W10 role-coverage complete):
#   agent_task         -> role_agent_task         (port 8793, full lifecycle: todo_get/worktree/agent/commit/todo_done)
#   audit_dependencies -> role_audit_dependencies (port 8786, gludd_facts+gludd_agent_run -> artifact)
#   audit_security     -> role_audit_security     (port 8785, gludd_facts+gludd_agent_run -> artifact)
#   debug_failure      -> role_debug_failure      (port 8790, gludd_agent_run+gludd_message -> diagnosis artifact)
#   dependency_update  -> role_dependency_update  (port 8792, gludd_agent_run analysis-only -> artifact)
#   document_change    -> role_document_change    (port 8791, gludd_agent_run artifact-only write_to_repo=false)
#   refactor_code      -> role_refactor_code      (port 8789, worktree+gludd_agent_run+gludd_git -> artifact)
#   report_audit       -> role_report_audit       (port 8784, gludd_facts consolidation no_data path)
#   report_metrics     -> role_report_metrics     (port 8783, gludd_facts -> metrics artifact)
#   report_status      -> role_report_status      (port 8782, gludd_facts -> status artifact)
#   triage_issue       -> role_triage_issue       (port 8787, gludd_agent_run+gludd_message -> triage artifact)
#   write_tests        -> role_write_tests        (port 8788, gludd_agent_run test_run_cmd empty -> artifact)


class TestMoleculeHarnessExists:
    def test_mock_daemon_server_present(self):
        assert MOCK_DAEMON.is_file(), f"missing reusable mock daemon at {MOCK_DAEMON}"

    def test_exemplar_scenarios_present(self):
        scenarios = _scenario_names()
        for exemplar in ("test_gludd_ping", "test_gludd_facts", "role_implement_change"):
            assert exemplar in scenarios, f"exemplar scenario missing: {exemplar}"
            mol = SCENARIOS_DIR / exemplar / "molecule.yml"
            conv = SCENARIOS_DIR / exemplar / "default" / "converge.yml"
            ver = SCENARIOS_DIR / exemplar / "default" / "verify.yml"
            assert mol.is_file(), f"{exemplar}: molecule.yml missing"
            assert conv.is_file(), f"{exemplar}: default/converge.yml missing"
            assert ver.is_file(), f"{exemplar}: default/verify.yml missing"

    def test_module_scenarios_start_the_mock_daemon(self):
        # Module scenarios must hit a real (mock) HTTP endpoint — they must ship
        # a prepare.yml that launches the mock daemon. (Honest coverage rule.)
        for exemplar in ("test_gludd_ping", "test_gludd_facts"):
            prep = SCENARIOS_DIR / exemplar / "default" / "prepare.yml"
            assert prep.is_file(), f"{exemplar}: module scenario must have prepare.yml"
            assert "mock_daemon/server.py" in prep.read_text(), (
                f"{exemplar}: prepare.yml must launch the mock daemon"
            )


class TestModuleCoverageChecklist:
    def test_inventory_partition_is_exact(self):
        """Covered + not-yet-covered must exactly equal the module inventory.

        This forces the checklist to stay honest: you cannot add a scenario
        without ticking the module off ``_NOT_YET_COVERED_MODULES``, and you
        cannot delete a scenario for a 'covered' module without it reappearing.
        """
        modules = _module_names()
        scenarios = _scenario_names()
        covered = {m for m in modules if _module_scenario(m) in scenarios}
        not_covered = modules - covered

        # Every not-yet-covered module must be in the declared checklist.
        undeclared = not_covered - _NOT_YET_COVERED_MODULES
        assert not undeclared, (
            f"modules with no scenario and not on the checklist: {sorted(undeclared)}"
        )
        # Every checklist entry must really be uncovered (tick it off when added).
        stale = _NOT_YET_COVERED_MODULES - not_covered
        assert not stale, (
            f"checklist lists modules that now HAVE a scenario — remove them: {sorted(stale)}"
        )

    def test_at_least_two_module_scenarios_exist(self):
        modules = _module_names()
        scenarios = _scenario_names()
        covered = {m for m in modules if _module_scenario(m) in scenarios}
        assert len(covered) >= 2, f"expected >= 2 module scenarios, have {sorted(covered)}"


class TestRoleCoverageChecklist:
    def test_inventory_partition_is_exact(self):
        roles = _role_names()
        scenarios = _scenario_names()
        covered = {r for r in roles if _role_scenario(r) in scenarios}
        not_covered = roles - covered

        undeclared = not_covered - _NOT_YET_COVERED_ROLES
        assert not undeclared, (
            f"roles with no scenario and not on the checklist: {sorted(undeclared)}"
        )
        stale = _NOT_YET_COVERED_ROLES - not_covered
        assert not stale, (
            f"checklist lists roles that now HAVE a scenario — remove them: {sorted(stale)}"
        )

    def test_at_least_one_role_scenario_exists(self):
        roles = _role_names()
        scenarios = _scenario_names()
        covered = {r for r in roles if _role_scenario(r) in scenarios}
        assert len(covered) >= 1, f"expected >= 1 role scenario, have {sorted(covered)}"
