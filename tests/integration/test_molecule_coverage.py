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


# Scenarios that cover a role but use a non-conventional name.
# project_init has TWO scenarios: project_init_role (scaffolding contract)
# and project_init_override (project-tier precedence shadowing). Neither
# matches the strict role_<name> convention, so they're mapped here.
_ROLE_SCENARIO_ALIASES: dict[str, set[str]] = {
    "local_game_gen": {"local_game_gen"},
    "local_model_server": {"local_model_server"},
    "project_init": {"project_init_role", "project_init_override"},
    "openbao_break_glass_backup": {"openbao_break_glass_backup"},
    "stream_input_key_both": {"stream_input_key_both"},
}

_MODULE_SCENARIO_ALIASES: dict[str, set[str]] = {
    "gludd_local_model": {"local_game_gen"},
}


def _module_covered(module: str, scenarios: set[str]) -> bool:
    """Return whether a conventional or role-owned scenario covers a module."""
    if _module_scenario(module) in scenarios:
        return True
    return bool(_MODULE_SCENARIO_ALIASES.get(module, set()) & scenarios)


def _role_covered(role: str, scenarios: set[str]) -> bool:
    """True if any covering scenario (conventional role_<name> OR an alias) exists."""
    if _role_scenario(role) in scenarios:
        return True
    return bool(_ROLE_SCENARIO_ALIASES.get(role, set()) & scenarios)


# --- The shrinking checklist -------------------------------------------------
# Modules that DO NOT yet have a dedicated test_<module> molecule scenario.
# Empty: every gludd_* module now has a dedicated test_gludd_* scenario,
# including gludd_reload (re-added under #47 with an import-clean PYTHONPATH +
# /readyz health gate so the HotReloader hot-swap + degraded rollback both run).
# gludd_langchain_generate and gludd_langgraph_decision both POST the shared
# /admin/models/call endpoint and are exercised end-to-end through the
# role_langgraph_decision scenario (the decision module runs unchanged over real
# HTTP inside the role); gludd_langgraph_workflow has its own dedicated
# test_gludd_langgraph_workflow scenario (POST /admin/models/workflow). The two
# call-endpoint modules have no separate test_<module> dir yet, so they remain on
# the shrinking checklist.
# gludd_stream is exercised end-to-end (HTTP path + payload shaping +
# /admin/stream/dispatch round-trip) by the three operator-example scenarios
# under molecule/playbooks/stream_audio_to_tasks, stream_video_feature_detection,
# and stream_text_log_tail — but those names do NOT satisfy the strict
# test_gludd_stream naming convention, so the module remains on the checklist
# until a dedicated test_gludd_stream scenario lands. See Phase Stream in
# TASKS.md.
# ``gludd_scapy`` is no longer in this agent-only inventory: networking owns
# the canonical module and agent exposes a metadata redirect for compatibility.
_NOT_YET_COVERED_MODULES: set[str] = {
    "gludd_break_glass",
    "gludd_embed",
    "gludd_environment",
    "gludd_human_todo",
    "gludd_langchain_generate",
    "gludd_langgraph_decision",
    "gludd_open_code",  # TODO: add molecule scenario
    "gludd_ornith",  # TODO: add molecule scenario
    "gludd_proc_monitor",
    "gludd_rag",
    "gludd_slurm_deploy",
    "gludd_stream",
}
# All other gludd_* modules now have molecule scenarios (W10 complete):
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
#   gludd_introspect  -> test_gludd_introspect  (port 8838, GET /api/facts -> codebase block)
#   gludd_abtest      -> test_gludd_abtest       (port 8839, in-proc run_ab: good promote + crasher reject)
#   gludd_reload      -> test_gludd_reload       (port 8840, in-proc HotReloader: healthy swap + 404-gate rollback)
#   gludd_observe     -> test_gludd_observe      (port 8897, all four cross-source ops + isolated connector failure)

# Roles that DO NOT yet have a role_<name> molecule scenario.
# New roles added in the observability batch (W-observe) that don't yet have
# molecule scenarios — tracked here as the shrinking checklist.
# run_tests / lint_and_check: thin wrappers ported from the legacy root
# roles/ dir during the single-home migration (2026-06-28). They do not hit
# the daemon — scenarios are TODO but the roles are wired via FQCN.
# managed_python_preflight is a private ``include_role`` dependency exercised
# by managed-host roles with ``public: false``. It remains explicitly listed
# until a dedicated role_managed_python_preflight scenario is added; private
# roles are not exempt from the exhaustive inventory partition.
_NOT_YET_COVERED_ROLES: set[str] = {
    "account_lifecycle",
    "agent_floor_check",
    "background_test_runner",
    "backlog_guard_audit",
    "ci_annotations_poll",
    "cost_audit",
    "cost_optimizer",
    "coverage_audit",
    "credit_audit",
    "delegate_discipline_check",
    "deletion_gate",
    "enforce_disengage",
    "enforcement_gate",
    "enforcement_verify",
    "feature_evidence_audit",
    "game_build_audit",
    "generate_status_table",
    "git_automation",
    "git_commit_push",
    "gludd_update",
    "log_prompt_evaluator",
    "guardrail_pattern",
    "managed_python_preflight",
    "manage_processes",
    "model_benchmark",
    "model_download",
    "model_evaluate",
    "model_quantize",
    "model_register",
    "model_serve",
    "networking",
    "multitasking_backlog_check",
    "observe_deploy_correlator",
    "observe_error_spike_rca",
    "observe_incident_triage",
    "observe_latency_regression",
    "observe_saturation_capacity",
    "observe_security_signal",
    "ornith_self_improve",
    "rag_example",
    "scan_conflict_markers",
    "service_login",
    "spec_lifecycle",
    "stream_input_key_before",
    "task_deadline_check",
    "token_window_monitor",
    "type_safety_audit",
    "verify_feature_claims",
    "watchdog_check",
}
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
#   self_improve_propose  -> role_self_improve_propose  (8841, introspect->target->worktree->proposal.json)


class TestMoleculeHarnessExists:
    def test_mock_daemon_server_present(self) -> None:
        assert MOCK_DAEMON.is_file(), f"missing reusable mock daemon at {MOCK_DAEMON}"

    def test_exemplar_scenarios_present(self) -> None:
        scenarios = _scenario_names()
        for exemplar in ("test_gludd_ping", "test_gludd_facts", "role_implement_change"):
            assert exemplar in scenarios, f"exemplar scenario missing: {exemplar}"
            mol = SCENARIOS_DIR / exemplar / "molecule.yml"
            conv = SCENARIOS_DIR / exemplar / "default" / "converge.yml"
            ver = SCENARIOS_DIR / exemplar / "default" / "verify.yml"
            assert mol.is_file(), f"{exemplar}: molecule.yml missing"
            assert conv.is_file(), f"{exemplar}: default/converge.yml missing"
            assert ver.is_file(), f"{exemplar}: default/verify.yml missing"

    def test_module_scenarios_start_the_mock_daemon(self) -> None:
        # Module scenarios must hit a real (mock) HTTP endpoint — they must ship
        # a prepare.yml that launches the mock daemon. (Honest coverage rule.)
        for exemplar in ("test_gludd_ping", "test_gludd_facts"):
            prep = SCENARIOS_DIR / exemplar / "default" / "prepare.yml"
            assert prep.is_file(), f"{exemplar}: module scenario must have prepare.yml"
            assert "mock_daemon/server.py" in prep.read_text(), f"{exemplar}: prepare.yml must launch the mock daemon"


class TestModuleCoverageChecklist:
    def test_inventory_partition_is_exact(self) -> None:
        """Covered + not-yet-covered must exactly equal the module inventory.

        This forces the checklist to stay honest: you cannot add a scenario
        without ticking the module off ``_NOT_YET_COVERED_MODULES``, and you
        cannot delete a scenario for a 'covered' module without it reappearing.
        """
        modules = _module_names()
        scenarios = _scenario_names()
        covered = {m for m in modules if _module_covered(m, scenarios)}
        not_covered = modules - covered

        # Every not-yet-covered module must be in the declared checklist.
        undeclared = not_covered - _NOT_YET_COVERED_MODULES
        assert not undeclared, f"modules with no scenario and not on the checklist: {sorted(undeclared)}"
        # Every checklist entry must really be uncovered (tick it off when added).
        stale = _NOT_YET_COVERED_MODULES - not_covered
        assert not stale, f"checklist lists modules that now HAVE a scenario — remove them: {sorted(stale)}"

    def test_at_least_two_module_scenarios_exist(self) -> None:
        modules = _module_names()
        scenarios = _scenario_names()
        covered = {m for m in modules if _module_covered(m, scenarios)}
        assert len(covered) >= 2, f"expected >= 2 module scenarios, have {sorted(covered)}"


class TestGluddObserveScenario:
    """The observe module scenario must exercise real HTTP-backed workflows."""

    def test_scenario_exercises_all_operations_through_mock_daemon(self) -> None:
        scenario = SCENARIOS_DIR / "test_gludd_observe"
        molecule = scenario / "molecule.yml"
        prepare = scenario / "default" / "prepare.yml"
        converge = scenario / "default" / "converge.yml"
        verify = scenario / "default" / "verify.yml"
        cleanup = scenario / "default" / "cleanup.yml"

        for required in (molecule, prepare, converge, verify, cleanup):
            assert required.is_file(), f"gludd_observe scenario file missing: {required}"

        molecule_text = molecule.read_text()
        converge_text = converge.read_text()
        verify_text = verify.read_text()
        daemon_text = MOCK_DAEMON.read_text()

        assert "mock_daemon_start.yml" in converge_text
        assert "mock_daemon_stop.yml" in converge_text
        assert "mock_daemon_cleanup.yml" in molecule_text
        assert "mock_daemon_destroy.yml" in molecule_text
        assert "general_ludd.agent.gludd_observe" in converge_text
        for operation in (
            "query_sources",
            "timeline",
            "topology",
            "correlate_incident",
        ):
            assert f"op: {operation}" in converge_text
        assert "source_count" in verify_text
        assert "/api/observe/sources" in daemon_text
        assert "/api/observe/query" in daemon_text


class TestRoleCoverageChecklist:
    def test_inventory_partition_is_exact(self) -> None:
        roles = _role_names()
        scenarios = _scenario_names()
        covered = {r for r in roles if _role_covered(r, scenarios)}
        not_covered = roles - covered

        undeclared = not_covered - _NOT_YET_COVERED_ROLES
        assert not undeclared, f"roles with no scenario and not on the checklist: {sorted(undeclared)}"
        stale = _NOT_YET_COVERED_ROLES - not_covered
        assert not stale, f"checklist lists roles that now HAVE a scenario — remove them: {sorted(stale)}"

    def test_at_least_one_role_scenario_exists(self) -> None:
        roles = _role_names()
        scenarios = _scenario_names()
        covered = {r for r in roles if _role_covered(r, scenarios)}
        assert len(covered) >= 1, f"expected >= 1 role scenario, have {sorted(covered)}"


_PROJECT_INIT_SCENARIOS: tuple[str, ...] = (
    "project_init_role",
    "project_init_override",
)


class TestProjectInitScenarios:
    """The project_init role has two non-conventional scenarios: one for the
    scaffolding contract, one for the project-tier override (precedence)."""

    def test_project_init_scenarios_present(self) -> None:
        scenarios = _scenario_names()
        for name in _PROJECT_INIT_SCENARIOS:
            assert name in scenarios, f"project_init scenario missing: {name}"
            mol = SCENARIOS_DIR / name / "molecule.yml"
            conv = SCENARIOS_DIR / name / "default" / "converge.yml"
            ver = SCENARIOS_DIR / name / "default" / "verify.yml"
            prep = SCENARIOS_DIR / name / "default" / "prepare.yml"
            cleanup = SCENARIOS_DIR / name / "default" / "cleanup.yml"
            assert mol.is_file(), f"{name}: molecule.yml missing"
            assert conv.is_file(), f"{name}: default/converge.yml missing"
            assert ver.is_file(), f"{name}: default/verify.yml missing"
            assert prep.is_file(), f"{name}: default/prepare.yml missing"
            assert cleanup.is_file(), f"{name}: default/cleanup.yml missing"

    def test_project_init_scenarios_invoke_the_role(self) -> None:
        for name in _PROJECT_INIT_SCENARIOS:
            conv = SCENARIOS_DIR / name / "default" / "converge.yml"
            text = conv.read_text()
            assert "general_ludd.agent.project_init" in text, (
                f"{name}: converge.yml must invoke general_ludd.agent.project_init"
            )

    def test_override_scenario_wires_precedence_env(self) -> None:
        """project_init_override must set ANSIBLE_COLLECTIONS_PATH project-first."""
        mol = SCENARIOS_DIR / "project_init_override" / "molecule.yml"
        text = mol.read_text()
        assert "ANSIBLE_COLLECTIONS_PATH" in text, (
            "project_init_override molecule.yml must set ANSIBLE_COLLECTIONS_PATH"
        )
        assert "molecule_scenario_project_dir" in text or ".gludd/collections" in text, (
            "project_init_override must put the project tier first in ANSIBLE_COLLECTIONS_PATH"
        )


# ---------------------------------------------------------------------------
# Operator-example scenarios for the gludd_stream module + /admin/stream/dispatch
# endpoint. These do NOT satisfy the strict test_gludd_stream naming convention
# (they're scenario-led examples, not module-coverage scenarios), so they're
# tracked here independently. Each scenario MUST ship molecule.yml + the
# default/ trio (prepare/converge/verify) and the mock daemon MUST have been
# extended with the /admin/stream/dispatch handler.
# ---------------------------------------------------------------------------
_STREAM_SCENARIOS: tuple[str, ...] = (
    "stream_audio_to_tasks",
    "stream_video_feature_detection",
    "stream_text_log_tail",
    "stream_input_key_dispatch",
    "stream_input_key_both",
)


class TestStreamExampleScenarios:
    def test_stream_dispatch_handler_in_mock_daemon(self) -> None:
        """Mock daemon MUST implement POST /admin/stream/dispatch."""
        src = MOCK_DAEMON.read_text()
        assert "/admin/stream/dispatch" in src, "mock_daemon/server.py missing POST /admin/stream/dispatch handler"
        assert "_stream_dispatch_response" in src, "mock_daemon/server.py missing _stream_dispatch_response helper"

    def test_stream_scenarios_present(self) -> None:
        scenarios = _scenario_names()
        for name in _STREAM_SCENARIOS:
            assert name in scenarios, f"stream scenario missing: {name}"
            mol = SCENARIOS_DIR / name / "molecule.yml"
            conv = SCENARIOS_DIR / name / "default" / "converge.yml"
            ver = SCENARIOS_DIR / name / "default" / "verify.yml"
            prep = SCENARIOS_DIR / name / "default" / "prepare.yml"
            assert mol.is_file(), f"{name}: molecule.yml missing"
            assert conv.is_file(), f"{name}: default/converge.yml missing"
            assert ver.is_file(), f"{name}: default/verify.yml missing"
            assert prep.is_file(), f"{name}: default/prepare.yml missing"

    def test_stream_scenarios_use_mock_daemon(self) -> None:
        """Each stream scenario's prepare.yml MUST launch the mock daemon."""
        for name in _STREAM_SCENARIOS:
            prep = SCENARIOS_DIR / name / "default" / "prepare.yml"
            text = prep.read_text()
            assert "mock_daemon/server.py" in text, f"{name}: prepare.yml must launch the mock daemon"

    def test_stream_scenarios_target_stream_dispatch_endpoint(self) -> None:
        """Each stream scenario's converge.yml MUST invoke gludd_stream."""
        for name in _STREAM_SCENARIOS:
            conv = SCENARIOS_DIR / name / "default" / "converge.yml"
            text = conv.read_text()
            assert "general_ludd.agent.gludd_stream" in text, (
                f"{name}: converge.yml must invoke the gludd_stream module"
            )
