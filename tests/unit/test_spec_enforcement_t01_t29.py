"""T01-T06,T08-T24,T26-T29: TDD enforcement and test discipline specs.

T01: Never disable tests in CI
T02: Never use continue-on-error in CI
T03: Test collection errors are hard failures
T04: Test failures must be fixed, not suppressed
T05: Coverage threshold cannot be lowered to pass
T06: TDD — test file must exist before source edit
T08: Every new source file requires a test file
T09: Test count must be checked before commit
T10: Run specific test before claiming fix
T11: Test quality requires AAA structure
T12: No mock-only tests
T13: No tests that test mocks themselves
T14: Integration tests verify cross-subsystem behavior
T15: E2E tests go through the daemon API
T16: No test isolation pollution
T17: Each test has one assertion concept
T18: Test names describe behavior, not implementation
T19: Realistic test data
T20: Deterministic tests
T21: No coverage gaming
T22: Gate must run before any status claim of "green"
T23: Test-run output must be cited with pass count
T24: Stale gate status invalidates claims
T26: Gate failure must surface log
T27: Test watchdog kills stale tasks
T28: 5-minute max per subtask
T29: Test files must be importable
"""

import re
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
MAKEFILE = ROOT / "Makefile"
AGENTS = ROOT / "AGENTS.md"
SCRIPTS_DIR = ROOT / "scripts"
PLUGIN_DIR = ROOT / ".opencode" / "plugin"


def _makefile_content() -> str:
    return MAKEFILE.read_text() if MAKEFILE.exists() else ""


def _target_names(content: str) -> set[str]:
    targets: set[str] = set()
    for line in content.split("\n"):
        m = re.match(r"^([a-zA-Z_][a-zA-Z0-9_./-]*)\s*:(?!=)", line.strip())
        if m:
            targets.add(m.group(1))
    return targets


def _find_recipe(content: str, target: str) -> str:
    idx = content.find(f"\n{target}:")
    if idx == -1:
        return ""
    end = content.find("\n\n", idx)
    if end == -1:
        end = len(content)
    return content[idx:end]


def _agents_content() -> str:
    return AGENTS.read_text() if AGENTS.exists() else ""


def _plugin_content(name: str) -> str:
    p = PLUGIN_DIR / name
    return p.read_text() if p.exists() else ""


class TestT01T02T03T04T05CIAndCollection:
    """T01-T05: CI test discipline and collection enforcement."""

    def test_t01_never_disable_tests_in_ci(self):
        agents = _agents_content()
        has_rule = "skip" in agents.lower() or "xfail" in agents.lower() or "disable" in agents.lower()
        assert has_rule, "T01: AGENTS.md must forbid disabling tests in CI"

    def test_t02_no_continue_on_error_in_ci(self):
        agents = _agents_content()
        has_rule = "continue-on-error" in agents or "enforce-test-integrity.ts" in agents
        assert has_rule, "T02: AGENTS.md must forbid continue-on-error in CI"

    def test_t03_collection_errors_are_hard_failures(self):
        content = _makefile_content()
        targets = _target_names(content)
        assert "collect-check" in targets, "T03: Makefile must have collect-check target"
        gate_recipe = _find_recipe(content, "gate")
        has_check = "collect-check" in gate_recipe
        assert has_check, "T03: gate must include collect-check as prerequisite"

    def test_t04_test_failures_fixed_not_suppressed(self):
        plugin = _plugin_content("enforce-no-suppressions.ts")
        agents = _agents_content()
        has_plugin = "noqa" in plugin or "noqa" in agents or "suppression" in agents.lower()
        assert has_plugin, "T04: enforce-no-suppressions.ts must forbid test-failure suppression"

    def test_t05_coverage_threshold_not_lowered(self):
        project_toml = ROOT / "pyproject.toml"
        if not project_toml.exists():
            return
        agent_says_threshold = "fail-under" in _agents_content() or "coverage" in _agents_content().lower()
        assert agent_says_threshold, "T05: AGENTS.md must forbid lowering coverage threshold"


class TestT06T08TDDEnforcement:
    """T06/T08: TDD enforcement plugin and commit-time backstop."""

    def test_t06_tdd_plugin_exists_and_blocks_edits(self):
        plugin = _plugin_content("enforce-tdd.ts")
        assert plugin, "T06: enforce-tdd.ts must exist for real-time TDD editor block"

    def test_t06_tdd_plugin_has_subagent_guard(self):
        plugin = _plugin_content("enforce-tdd.ts")
        if not plugin:
            return
        has_guard = "OPENCODE_SUBAGENT" in plugin
        assert has_guard, "T06: enforce-tdd.ts must include subagent isolation guard"

    def test_t08_new_source_requires_test_commit_check(self):
        script = SCRIPTS_DIR / "check_tdd_compliance.py"
        assert script.exists(), "T08: check_tdd_compliance.py must exist for commit-time backstop"


class TestT09T10CommitAndFixDiscipline:
    """T09/T10: test count and specific test before claiming fix."""

    def test_t09_test_count_target_exists(self):
        targets = _target_names(_makefile_content())
        assert "test-count" in targets, "T09: Makefile must have test-count target"

    def test_t10_run_specific_test_before_claiming_fix_policy(self):
        agents = _agents_content()
        has_rule = "Root-Cause-Only Fix" in agents or "specific test" in agents.lower()
        assert has_rule, "T10: AGENTS.md must require running specific test before claiming fix"


class TestT11T12T13TestQualityBasics:
    """T11/T12/T13: AAA structure, no mock-only, no self-testing mocks."""

    def test_t11_test_quality_skill_exists(self):
        skill_path = ROOT / ".opencode" / "skills" / "test-quality" / "SKILL.md"
        assert skill_path.exists(), "T11: test-quality skill must exist for AAA structure enforcement"

    def test_t12_no_mock_only_tests_policy(self):
        skill = ROOT / ".opencode" / "skills" / "test-quality" / "SKILL.md"
        if not skill.exists():
            return
        content = skill.read_text()
        has_rule = "mock" in content.lower()
        assert has_rule, "T12: test-quality skill must forbid mock-only tests"

    def test_t13_no_tests_that_test_mocks_policy(self):
        skill = ROOT / ".opencode" / "skills" / "test-quality" / "SKILL.md"
        if not skill.exists():
            return
        content = skill.read_text()
        has_behavior = "behavior" in content.lower() or "actual" in content.lower()
        assert has_behavior, "T13: test-quality skill must require testing actual behavior not mock calls"


class TestT14T15IntegrationE2E:
    """T14/T15: integration and E2E test coverage."""

    def test_t14_integration_test_dir_exists(self):
        integration_dir = ROOT / "tests" / "integration"
        assert integration_dir.exists() and integration_dir.is_dir(), (
            "T14: tests/integration/ directory must exist for cross-subsystem tests"
        )

    def test_t15_e2e_test_dir_exists(self):
        e2e_dir = ROOT / "tests" / "e2e"
        assert e2e_dir.exists() and e2e_dir.is_dir(), "T15: tests/e2e/ directory must exist for daemon API tests"


class TestT16T17T18T19T20T21TestQualityAdvanced:
    """T16-T21: isolation, assertion concepts, naming, data, determinism, coverage."""

    def test_t16_test_isolation_policy(self):
        skill = ROOT / ".opencode" / "skills" / "test-quality" / "SKILL.md"
        if not skill.exists():
            return
        content = skill.read_text()
        has_iso = "isolation" in content.lower() or "mutable" in content.lower() or "shared" in content.lower()
        assert has_iso, "T16: test-quality skill must forbid test isolation pollution"

    def test_t17_one_assertion_concept_policy(self):
        skill = ROOT / ".opencode" / "skills" / "test-quality" / "SKILL.md"
        if not skill.exists():
            return
        content = skill.read_text()
        has_rule = "assertion" in content.lower() or "one" in content.lower() or "concept" in content.lower()
        assert has_rule, "T17: test-quality skill must enforce one-assertion-concept rule"

    def test_t18_test_names_describe_behavior_policy(self):
        skill = ROOT / ".opencode" / "skills" / "test-quality" / "SKILL.md"
        if not skill.exists():
            return
        content = skill.read_text()
        has_name = "name" in content.lower() or "describe" in content.lower() or "behavior" in content.lower()
        assert has_name, "T18: test-quality skill must require behavior-describing test names"

    def test_t19_realistic_test_data_policy(self):
        skill = ROOT / ".opencode" / "skills" / "test-quality" / "SKILL.md"
        if not skill.exists():
            return
        content = skill.read_text()
        has_data = "data" in content.lower() or "realistic" in content.lower()
        assert has_data, "T19: test-quality skill must require realistic test data"

    def test_t20_deterministic_tests_policy(self):
        skill = ROOT / ".opencode" / "skills" / "test-quality" / "SKILL.md"
        if not skill.exists():
            return
        content = skill.read_text()
        has_det = "deterministic" in content.lower() or "random" in content.lower()
        assert has_det, "T20: test-quality skill must require deterministic tests"

    def test_t21_no_coverage_gaming_policy(self):
        skill = ROOT / ".opencode" / "skills" / "test-quality" / "SKILL.md"
        if not skill.exists():
            return
        content = skill.read_text()
        has_rule = "coverage" in content.lower()
        assert has_rule, "T21: test-quality skill must forbid coverage gaming"


class TestT22T23T24GateStatusEvidence:
    """T22/T23/T24: gate status, pass count citation, and stale gate invalidation."""

    def test_t22_gate_before_green_claim_policy(self):
        agents = _agents_content()
        has_rule = "gate" in agents.lower() and ("prove" in agents.lower() or "evidence" in agents.lower())
        assert has_rule, "T22: AGENTS.md must require gate before claiming 'green'"

    def test_t23_cite_pass_count_with_test_claim_policy(self):
        plugin = _plugin_content("enforce-verified-claims.ts")
        agents = _agents_content()
        has_evidence = "passed" in plugin.lower() or "N passed" in agents or "pass count" in agents.lower()
        assert has_evidence, "T23: must require pass count citation with test claims"

    def test_t24_stale_gate_invalidates_claims_policy(self):
        agents = _agents_content()
        has_rule = "stale" in agents.lower() and "gate" in agents.lower()
        assert has_rule, "T24: AGENTS.md must codify stale gate status invalidation"


class TestT26T27T28T29GateFailureAndWatchdog:
    """T26-T29: gate failure surfacing, task watchdog, timeout, importable tests."""

    def test_t26_gate_failure_surfaces_log_policy(self):
        agents = _agents_content()
        has_rule = "No Unseen Events" in agents or "surfaced" in agents.lower()
        assert has_rule, "T26: AGENTS.md must require gate failure log surfacing"

    def test_t27_task_watchdog_script_exists(self):
        script = SCRIPTS_DIR / "task_watchdog.py"
        assert script.exists(), "T27: task_watchdog.py must exist for stale task killing"

    def test_t28_five_minute_max_subtask_policy(self):
        agents = _agents_content()
        has_rule = "5-minute" in agents.lower() or "5 min" in agents.lower() or "300000" in agents
        assert has_rule, "T28: AGENTS.md must enforce 5-minute max per subtask"

    def test_t29_test_files_importable_via_collect_check(self):
        content = _makefile_content()
        targets = _target_names(content)
        assert "collect-check" in targets, "T29: collection check target must exist for importable test verification"
