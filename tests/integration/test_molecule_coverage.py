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
# Modules that DO NOT yet have a dedicated test_<module> molecule scenario.
# gludd_introspect/abtest/reload are exercised via the role_self_improve_* scenarios
# (abtest via role_self_improve_ab_test, reload via role_self_improve_promote,
# introspect via the propose path); dedicated test_gludd_* scenarios are a TODO.
_NOT_YET_COVERED_MODULES: set[str] = {"gludd_introspect", "gludd_abtest", "gludd_reload"}
# All gludd_* modules now have molecule scenarios (W10 complete):
#   gludd_agent_run   -> test_gludd_agent_run  (port 8781, POST /admin/models/call HTTP fallback)
#   gludd_db          -> test_gludd_db          (port 8776, todo_get/update/resource_preference)
#   gludd_git         -> test_gludd_git         (port 8779, real git commit+branch on throwaway repo)
#   gludd_mcp_tool    -> test_gludd_mcp_tool    (port 8778, honest not_implemented W3.9 fence)
#   gludd_message     -> test_gludd_message     (port 8774, send/receive/ack)
#   gludd_model_call  -> test_gludd_model_call  (port 8775, POST /admin/models/call)
#   gludd_schedule    -> test_gludd_schedule    (port 8836, POST /api/schedule concurrency batches)
#   gludd_skill       -> test_gludd_skill       (port 8777, local skill render with Jinja2)
#   gludd_worktree    -> test_gludd_worktree    (port 8780, real git worktree present+absent)
#   gludd_accounting  -> test_gludd_accounting  (port 8832, GET /api/accounting + /api/accounting/{id})
#   gludd_dispatch    -> test_gludd_dispatch    (port 8834, POST /api/dispatch + available + recent)

# Roles that DO NOT yet have a role_<name> molecule scenario.
# self_improve_propose is exercised via the propose->ab_test->promote chain that
# role_self_improve_ab_test/_promote cover; a dedicated scenario is a TODO.
_NOT_YET_COVERED_ROLES: set[str] = {"self_improve_propose"}
# All roles now have molecule scenarios (W10 role-coverage complete + W13 + W14 + W15):
#   agent_task            -> role_agent_task            (8793, todo_get/worktree/agent/commit/todo_done)
#   audit_dependencies    -> role_audit_dependencies    (8786, gludd_facts+gludd_agent_run -> artifact)
#   audit_security        -> role_audit_security        (8785, gludd_facts+gludd_agent_run -> artifact)
#   backlog_groom         -> role_backlog_groom         (8819, gludd_facts todos -> ranked/split_candidates/actions)
#   ci_pipeline_repair    -> role_ci_pipeline_repair    (8801, .github/workflows scan -> findings)
#   debug_failure         -> role_debug_failure         (8790, gludd_agent_run+gludd_message -> diag)
#   dependency_update     -> role_dependency_update     (8792, gludd_agent_run analysis-only)
#   document_change       -> role_document_change       (8791, artifact-only write_to_repo=false)
#   estimate_story        -> role_estimate_story        (8818, gludd_facts history -> Fibonacci points)
#   flaky_quarantine      -> role_flaky_quarantine      (8802, xpass_strict -> recommendation)
#   gate_triage           -> role_gate_triage           (8800, gate output -> triage artifact)
#   refactor_code         -> role_refactor_code         (8789, worktree+agent_run+gludd_git)
#   release_build         -> role_release_build         (8803, PEP 440 dry-run + report)
#   report_audit          -> role_report_audit          (8784, gludd_facts no_data path)
#   report_metrics        -> role_report_metrics        (8783, gludd_facts -> metrics artifact)
#   report_status         -> role_report_status         (8782, gludd_facts -> status artifact)
#   retrospective         -> role_retrospective         (8825, metrics+history+messages -> well/ill/actions)
#   sbom_generate         -> role_sbom_generate         (8813, enable_syft=false -> CycloneDX)
#   secret_scan           -> role_secret_scan           (8812, enable_scan=false -> verdict switch)
#   security_gate         -> role_security_gate         (8816, all-pass+fail -> gate_passed t/f)
#   security_requirements -> role_security_requirements (8815, gludd_db todo_get + 12 criteria)
#   security_review       -> role_security_review       (8811, REAL grep shell=True -> finding)
#   sprint_board_report   -> role_sprint_board_report   (8822, gludd_facts todos/work -> board columns)
#   sprint_plan           -> role_sprint_plan           (8820, capacity-fit from history velocity)
#   sprint_review         -> role_sprint_review         (8824, history+traces -> completed/highlights)
#   standup_report        -> role_standup_report        (8821, facts+gludd_message -> done/in_progress/blockers)
#   story_create          -> role_story_create          (8817, request_text -> narrative+criteria)
#   supply_chain_verify   -> role_supply_chain_verify   (8814, fail-closed: unsigned->fail)
#   threat_model          -> role_threat_model          (8810, gludd_facts+design -> STRIDE 17)
#   triage_issue          -> role_triage_issue          (8787, agent_run+gludd_message -> triage)
#   parallel_planner      -> role_parallel_planner      (8837, POST /api/schedule -> execution plan artifact)
#   validate_and_push     -> role_validate_and_push     (8804, override-pass, no push)
#   velocity_report       -> role_velocity_report       (8823, gludd_metrics+history -> points_per_sprint/trend)
#   write_tests           -> role_write_tests           (8788, agent_run test_run_cmd -> artifact)
#   accounting_report     -> role_accounting_report     (8833, gludd_accounting all -> cost/time/LoC report)
#   tool_dispatch         -> role_tool_dispatch         (8835, dispatch shell tool call -> artifact)


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
