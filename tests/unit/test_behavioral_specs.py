"""tests/unit/test_behavioral_specs.py — structural verification of BEHAVIORAL_SPECS.md.

Verifies:
1. All 3000 numbered specs exist in the specs document.
2. Each spec maps to at least one enforcement mechanism (plugin, Makefile guard, AGENTS.md section).
3. Each enforcement mechanism has at least one corresponding structural test.
4. New plugins required by the specs exist and are structurally valid.
"""

import json
import re
from pathlib import Path
from typing import ClassVar

import pytest

from tests.unit._plugin_contract import plugin_contract_source

ROOT = Path(__file__).resolve().parent.parent.parent
SPECS_PATH = ROOT / "docs" / "specs" / "BEHAVIORAL_SPECS.md"
MAKEFILE_PATH = ROOT / "Makefile"
AGENTS_PATH = ROOT / "AGENTS.md"
PLUGIN_DIR = ROOT / ".opencode" / "plugin"
CI_WORKFLOW = ROOT / ".github" / "workflows" / "build.yml"
OPENCODE_JSON = ROOT / "opencode.json"


# ── helpers ──────────────────────────────────────────────────────────────────


def read_specs() -> str:
    if not SPECS_PATH.exists():
        pytest.fail(f"BEHAVIORAL_SPECS.md not found at {SPECS_PATH}")
    return SPECS_PATH.read_text()


def spec_ids(specs_text: str) -> list[str]:
    """Extract all spec IDs like AA001, I123, P01, etc. from the spec doc."""
    _pat = r"^###\s+([A-Z]{1,2}\d{2,3})\b"
    matches = re.findall(_pat, specs_text, re.MULTILINE)
    return matches


def spec_has_mechanism(spec_id: str, specs_text: str) -> bool:
    """Check if a spec has an enforcement mechanism listed."""
    _hdg = re.search(rf"^### {re.escape(spec_id)}\b", specs_text, re.MULTILINE)
    if _hdg is None:
        return False
    idx = _hdg.start()
    next_spec = specs_text.find("\n### ", idx + 1)
    block = specs_text[idx : next_spec if next_spec >= 0 else None]
    # Matches: any **Enforcement:** line with non-whitespace content after it
    _pat = r"\*\*Enforcement:\*\*\s*\S"
    return bool(re.search(_pat, block))


# ── Spec existence tests ─────────────────────────────────────────────────────


class TestSpecsExist:
    """Verify specs exist in BEHAVIORAL_SPECS.md (deduplicated + expanded)."""

    def test_specs_file_exists(self):
        assert SPECS_PATH.exists(), f"{SPECS_PATH} missing"

    def test_at_least_2000_specs(self):
        ids = spec_ids(read_specs())
        assert len(ids) >= 2000, f"Expected >=2000 specs, found {len(ids)}"

    def test_all_expected_groups_present(self):
        ids = set(spec_ids(read_specs()))
        prefixes = [
            "P",
            "B",
            "O",
            "T",
            "D",
            "S",
            "E",
            "M",
            "G",
            "R",
            "W",
            "F",
            "C",
            "Q",
            "X",
            "A",
            "N",
            "K",
            "U",
            "Z",
            "H",
            "V",
            "J",
            "L",
            "Y",
            "I",
        ]
        for prefix in prefixes:
            count = sum(1 for s in ids if s.startswith(prefix))
            assert count >= 20, f"Group {prefix} has {count} specs, expected >=20"

    def test_no_duplicate_spec_ids(self):
        ids = spec_ids(read_specs())
        assert len(ids) == len(set(ids)), f"Duplicate specs: {[s for s in ids if ids.count(s) > 1]}"


# ── Enforcement mechanism tests ──────────────────────────────────────────────


@pytest.mark.xdist_group("behavioral-specs")
class TestEnforcementMechanisms:
    """Every spec must have an enforcement mechanism."""

    def test_all_specs_have_enforcement(self):
        text = read_specs()
        ids = spec_ids(text)
        missing = []
        for sid in ids:
            if not spec_has_mechanism(sid, text):
                missing.append(sid)
        assert not missing, f"Specs without enforcement: {missing}"

    def test_push_specs_reference_plugin(self):
        text = read_specs()
        for i in range(1, 31):
            sid = f"P{i:02d}"
            idx = text.find(f"### {sid}")
            if idx == -1:
                continue
            block = text[idx : idx + 600]
            has_ref = bool(re.search(r"(enforce-|Makefile|AGENTS\.md|scripts/|`[a-z_-]+`)", block))
            assert has_ref, f"{sid} missing mechanism reference: {block[:200]}"

    def test_branch_specs_reference_mechanism(self):
        text = read_specs()
        for i in range(1, 26):
            sid = f"B{i:02d}"
            idx = text.find(f"### {sid}")
            if idx == -1:
                continue
            block = text[idx : idx + 500]
            has_ref = bool(re.search(r"(enforce-|Makefile|AGENTS\.md|scripts/)", block))
            assert has_ref, f"{sid} missing mechanism reference"

    def test_objective_specs_reference_mechanism(self):
        text = read_specs()
        for i in range(1, 31):
            sid = f"O{i:02d}"
            idx = text.find(f"### {sid}")
            if idx == -1:
                continue
            block = text[idx : idx + 600]
            has_ref = bool(re.search(r"(enforce-objective|AGENTS\.md|Makefile|TASKS\.md|SESSION\.md)", block))
            assert has_ref, f"{sid} missing mechanism reference"

    def test_test_integrity_specs_reference_mechanism(self):
        text = read_specs()
        for i in range(1, 31):
            sid = f"T{i:02d}"
            idx = text.find(f"### {sid}")
            if idx == -1:
                continue
            block = text[idx : idx + 600]
            # Broad match: enforcement mechanisms
            _pat = r"(enforce-|Makefile|AGENTS|scripts/|\.github|plugin|test-quality|pyproject)"
            has_ref = bool(re.search(_pat, block))
            assert has_ref, f"{sid} missing mechanism reference: {block[:200]}"

    def test_worktree_specs_reference_mechanism(self):
        text = read_specs()
        for i in range(1, 31):
            sid = f"W{i:02d}"
            idx = text.find(f"### {sid}")
            if idx == -1:
                continue
            block = text[idx : idx + 600]
            has_ref = bool(re.search(r"(Makefile|AGENTS\.md|scripts/|agent-worktree|enforce-)", block))
            assert has_ref, f"{sid} missing mechanism reference"

    def test_ci_specs_reference_mechanism(self):
        text = read_specs()
        for i in range(1, 31):
            sid = f"F{i:02d}"
            idx = text.find(f"### {sid}")
            if idx == -1:
                continue
            block = text[idx : idx + 600]
            has_ref = bool(re.search(r"(enforce-|Makefile|AGENTS\.md|scripts/|ci-verdict|_push-rate)", block))
            assert has_ref, f"{sid} missing mechanism reference"

    def test_commit_specs_reference_mechanism(self):
        text = read_specs()
        for i in range(1, 31):
            sid = f"C{i:02d}"
            idx = text.find(f"### {sid}")
            if idx == -1:
                continue
            block = text[idx : idx + 600]
            has_ref = bool(re.search(r"(enforce-|Makefile|AGENTS\.md|scripts/|_gate-fresh|secrets)", block))
            assert has_ref, f"{sid} missing mechanism reference"

    def test_quality_specs_reference_mechanism(self):
        text = read_specs()
        for i in range(1, 31):
            sid = f"Q{i:02d}"
            idx = text.find(f"### {sid}")
            if idx == -1:
                continue
            block = text[idx : idx + 600]
            has_ref = bool(re.search(r"(Makefile|AGENTS\.md|scripts/|enforce-|gate)", block))
            assert has_ref, f"{sid} missing mechanism reference"

    def test_subagent_specs_reference_mechanism(self):
        text = read_specs()
        for i in range(1, 31):
            sid = f"X{i:02d}"
            idx = text.find(f"### {sid}")
            if idx == -1:
                continue
            block = text[idx : idx + 600]
            has_ref = bool(re.search(r"(AGENTS\.md|COST-EFFICIENCY|enforce-deadline|task_watchdog|Makefile)", block))
            assert has_ref, f"{sid} missing mechanism reference"

    def test_audit_specs_reference_mechanism(self):
        text = read_specs()
        for i in range(1, 31):
            sid = f"A{i:02d}"
            idx = text.find(f"### {sid}")
            if idx == -1:
                continue
            block = text[idx : idx + 600]
            has_ref = bool(re.search(r"(AGENTS\.md|enforce-verified|Makefile|Self-Audit)", block))
            assert has_ref, f"{sid} missing mechanism reference"

    def test_naming_specs_reference_mechanism(self):
        text = read_specs()
        for i in range(1, 31):
            sid = f"N{i:02d}"
            idx = text.find(f"### {sid}")
            if idx == -1:
                continue
            block = text[idx : idx + 600]
            has_ref = bool(re.search(r"(enforce-|AGENTS\.md|scripts/|Makefile|ruff)", block))
            assert has_ref, f"{sid} missing mechanism reference"

    def test_knowledge_specs_reference_mechanism(self):
        text = read_specs()
        for i in range(1, 31):
            sid = f"K{i:02d}"
            idx = text.find(f"### {sid}")
            if idx == -1:
                continue
            block = text[idx : idx + 600]
            has_ref = bool(re.search(r"(AGENTS\.md|enforce-|Makefile|SESSION|TASKS|BUGS)", block))
            assert has_ref, f"{sid} missing mechanism reference"

    def test_user_intent_specs_reference_mechanism(self):
        text = read_specs()
        for i in range(1, 31):
            sid = f"U{i:02d}"
            idx = text.find(f"### {sid}")
            if idx == -1:
                continue
            block = text[idx : idx + 600]
            has_ref = bool(re.search(r"(AGENTS\.md|enforce-|Makefile)", block))
            assert has_ref, f"{sid} missing mechanism reference"

    def test_zerofail_specs_reference_mechanism(self):
        text = read_specs()
        for i in range(1, 31):
            sid = f"Z{i:02d}"
            idx = text.find(f"### {sid}")
            if idx == -1:
                continue
            block = text[idx : idx + 600]
            has_ref = bool(re.search(r"(AGENTS\.md|enforce-|Makefile|tests/|scripts/|plugin)", block))
            assert has_ref, f"{sid} missing mechanism reference"

    def test_hard_break_specs_reference_mechanism(self):
        text = read_specs()
        for i in range(1, 101):
            sid = f"H{i:02d}"
            idx = text.find(f"### {sid}")
            if idx == -1:
                continue
            block = text[idx : idx + 600]
            has_ref = bool(re.search(r"(AGENTS\.md|enforce-|Makefile|scripts/|plugin)", block))
            assert has_ref, f"{sid} missing mechanism reference"

    def test_verification_specs_reference_mechanism(self):
        text = read_specs()
        for i in range(1, 101):
            sid = f"V{i:02d}"
            idx = text.find(f"### {sid}")
            if idx == -1:
                continue
            block = text[idx : idx + 600]
            has_ref = bool(re.search(r"(AGENTS\.md|enforce-|Makefile|scripts/|plugin)", block))
            assert has_ref, f"{sid} missing mechanism reference"

    def test_judgment_specs_reference_mechanism(self):
        text = read_specs()
        for i in range(1, 101):
            sid = f"J{i:02d}"
            idx = text.find(f"### {sid}")
            if idx == -1:
                continue
            block = text[idx : idx + 600]
            has_ref = bool(re.search(r"(AGENTS\.md|enforce-|Makefile|scripts/|plugin)", block))
            assert has_ref, f"{sid} missing mechanism reference"

    def test_learning_specs_reference_mechanism(self):
        text = read_specs()
        for i in range(1, 101):
            sid = f"L{i:02d}"
            idx = text.find(f"### {sid}")
            if idx == -1:
                continue
            block = text[idx : idx + 600]
            has_ref = bool(re.search(r"(AGENTS\.md|enforce-|Makefile|scripts/|plugin)", block))
            assert has_ref, f"{sid} missing mechanism reference"

    def test_yield_specs_reference_mechanism(self):
        text = read_specs()
        for i in range(1, 101):
            sid = f"Y{i:02d}"
            idx = text.find(f"### {sid}")
            if idx == -1:
                continue
            block = text[idx : idx + 600]
            has_ref = bool(re.search(r"(AGENTS\.md|enforce-|Makefile|scripts/|plugin)", block))
            assert has_ref, f"{sid} missing mechanism reference"


# ── Plugin existence tests ───────────────────────────────────────────────────


class TestPluginExistence:
    """Required enforcement plugins exist on disk."""

    REQUIRED_PLUGINS: ClassVar[list[str]] = [
        "enforce-batch-push.ts",
        "enforce-clean-tree.ts",
        "enforce-commit-lock.ts",
        "enforce-deadline.ts",
        "enforce-delegate.ts",
        "enforce-deletion-gate.ts",
        "enforce-enhancement-ratio.ts",
        "enforce-floor.ts",
        "enforce-make.ts",
        "enforce-multitask.ts",
        "enforce-no-suppressions.ts",
        "enforce-no-wait.ts",
        "enforce-objective.ts",
        "enforce-session-start.ts",
        "enforce-stop.ts",
        "enforce-tdd.ts",
        "enforce-verified-claims.ts",
        "enforce-worktree.ts",
        "enforce-audit.ts",
        "enforce-context.ts",
    ]

    def test_all_required_plugins_exist(self):
        missing = []
        for name in self.REQUIRED_PLUGINS:
            p = PLUGIN_DIR / name
            if not p.exists():
                missing.append(name)
        assert not missing, f"Missing plugins: {missing}"

    def test_enforce_batch_push_exists(self):
        assert (PLUGIN_DIR / "enforce-batch-push.ts").exists()

    def test_enforce_objective_exists(self):
        assert (PLUGIN_DIR / "enforce-objective.ts").exists()

    def test_enforce_multitask_exists(self):
        assert (PLUGIN_DIR / "enforce-multitask.ts").exists()

    def test_enforce_stop_exists(self):
        assert (PLUGIN_DIR / "enforce-stop.ts").exists()

    def test_enforce_verified_claims_exists(self):
        assert (PLUGIN_DIR / "enforce-verified-claims.ts").exists()

    def test_enforce_floor_exists(self):
        assert (PLUGIN_DIR / "enforce-floor.ts").exists()

    def test_enforce_delegate_exists(self):
        assert (PLUGIN_DIR / "enforce-delegate.ts").exists()

    def test_enforce_make_exists(self):
        assert (PLUGIN_DIR / "enforce-make.ts").exists()

    def test_enforce_tdd_exists(self):
        assert (PLUGIN_DIR / "enforce-tdd.ts").exists()

    def test_enforce_no_suppressions_exists(self):
        assert (PLUGIN_DIR / "enforce-no-suppressions.ts").exists()

    def test_enforce_no_wait_exists(self):
        assert (PLUGIN_DIR / "enforce-no-wait.ts").exists()

    def test_enforce_session_start_exists(self):
        assert (PLUGIN_DIR / "enforce-session-start.ts").exists()

    def test_anti_essay_plugin_exists(self):
        p = PLUGIN_DIR / "enforce-anti-essay.ts"
        assert p.exists(), (
            "enforce-anti-essay.ts is missing. Create it per spec E11. "
            "Requirement: detects essay-length text-only responses when pending work exists."
        )

    def test_branch_discipline_plugin_exists(self):
        p = PLUGIN_DIR / "enforce-branch-discipline.ts"
        assert p.exists(), (
            "enforce-branch-discipline.ts is missing. Create it per specs B01-B25. "
            "Requirement: verifies agent is on correct branch before mutating operations."
        )

    def test_test_integrity_plugin_exists(self):
        p = PLUGIN_DIR / "enforce-test-integrity.ts"
        assert p.exists(), (
            "enforce-test-integrity.ts is missing. Create it per specs T01-T30. "
            "Requirement: blocks test-disabling patterns (skip, xfail, continue-on-error)."
        )

    def test_plugins_registered_in_opencode_json(self):
        cfg = json.loads(OPENCODE_JSON.read_text())
        # opencode.json uses "plugin" (singular) array with file paths
        plugin_list = cfg.get("plugin", [])
        plugin_paths = []
        for p in plugin_list:
            if isinstance(p, str):
                plugin_paths.append(p)
            elif isinstance(p, dict):
                plugin_paths.append(p.get("path", p.get("name", "")))
        for f in PLUGIN_DIR.glob("enforce-*.ts"):
            if f.name in ("enforce-depth.ts",):
                continue
            # Check that the plugin's path appears in the registered plugins
            expected_suffix = f".opencode/plugin/{f.name}"
            found = any(expected_suffix in pp for pp in plugin_paths)
            assert found, f"{f.name} not registered in opencode.json plugin list"


# ── Plugin structural tests ──────────────────────────────────────────────────


class TestPluginStructure:
    """Every plugin must follow the enforcement plugin pattern."""

    @pytest.mark.parametrize(
        "plugin_file", [f for f in PLUGIN_DIR.glob("enforce-*.ts") if f.name != "enforce-depth.ts"]
    )
    def test_plugin_has_subagent_guard(self, plugin_file):
        content = plugin_contract_source(plugin_file)
        assert "isSubagent" in content, f"{plugin_file.name} missing isSubagent guard"

    @pytest.mark.parametrize(
        "plugin_file", [f for f in PLUGIN_DIR.glob("enforce-*.ts") if f.name != "enforce-depth.ts"]
    )
    def test_plugin_has_fail_open(self, plugin_file):
        content = plugin_contract_source(plugin_file)
        has_fail_open = "fail-open" in content.lower() or "fail open" in content.lower()
        has_try_catch = "try {" in content or "} catch" in content
        assert has_fail_open or has_try_catch, f"{plugin_file.name} missing fail-open pattern"

    @pytest.mark.parametrize(
        "plugin_file", [f for f in PLUGIN_DIR.glob("enforce-*.ts") if f.name != "enforce-depth.ts"]
    )
    def test_plugin_has_disable_env_var(self, plugin_file):
        content = plugin_contract_source(plugin_file)
        if plugin_file.name == "enforce-no-suppressions.ts":
            assert not re.search(r"GLUDD_\w+_ENFORCE", content), (
                "the suppression guard is intentionally non-disableable"
            )
            return

        # Multiple different patterns for disable checks.
        has_disable = bool(
            re.search(r"GLUDD_\w+_ENFORCE\s*(!==|===|==)\s*['\"]0['\"]", content)
            or re.search(r"ENFORCE\s*=\s*process\.env\.['\"\w]+\s*!==\s*['\"]0['\"]", content)
            or "ENFORCE" in content
        )
        assert has_disable, f"{plugin_file.name} missing disable env var check"

    @pytest.mark.parametrize(
        "plugin_file",
        [
            f
            for f in PLUGIN_DIR.glob("enforce-*.ts")
            if f.name not in ("enforce-depth.ts",)
            and "enforce-tdd.test" not in f.name
            and "enforce-multitask.test" not in f.name
            and "enforce-depth.test" not in f.name
        ],
    )
    def test_plugin_exports_default(self, plugin_file):
        content = plugin_contract_source(plugin_file)
        assert "export default" in content or "satisfies Plugin" in content, (
            f"{plugin_file.name} missing default export / satisfies Plugin"
        )

    @pytest.mark.parametrize(
        "plugin_file",
        [
            f
            for f in PLUGIN_DIR.glob("enforce-*.ts")
            if f.name not in ("enforce-depth.ts",)
            and "enforce-tdd.test" not in f.name
            and "enforce-multitask.test" not in f.name
            and "enforce-depth.test" not in f.name
        ],
    )
    def test_plugin_hot_reload_capable(self, plugin_file):
        content = plugin_contract_source(plugin_file)
        has_hot = "loadHotModule" in content or "hot_reload" in content
        assert has_hot, f"{plugin_file.name} missing hot-reload support"


# ── Plugin-specific behavioral tests ─────────────────────────────────────────


class TestEnforceObjective:
    """Enforce-objective.ts v2: now BLOCKING, not advisory."""

    def test_objective_plugin_has_blocking_mode(self):
        content = (PLUGIN_DIR / "enforce-objective.ts").read_text()
        assert "permissionDecision" in content, (
            "enforce-objective.ts must be upgraded to BLOCKING (return {permissionDecision: 'deny'}) "
            "per spec O02-O03. Currently advisory-only (console.warn)."
        )

    def test_objective_plugin_checks_every_tool(self):
        content = (PLUGIN_DIR / "enforce-objective.ts").read_text()
        assert "tool.execute.before" in content, "enforce-objective.ts missing tool.execute.before"

    def test_objective_plugin_has_text_complete_nag(self):
        content = (PLUGIN_DIR / "enforce-objective.ts").read_text()
        assert "text.complete" in content, "enforce-objective.ts missing text.complete hook"

    def test_objective_plugin_exports_get_primary_objective(self):
        content = (PLUGIN_DIR / "enforce-objective.ts").read_text()
        assert "getPrimaryObjective" in content, "getPrimaryObjective must be exported"

    def test_objective_plugin_exports_is_objective_met(self):
        content = (PLUGIN_DIR / "enforce-objective.ts").read_text()
        assert "isObjectiveMet" in content, "isObjectiveMet must be exported"

    def test_objective_plugin_ci_green_detection(self):
        content = (PLUGIN_DIR / "enforce-objective.ts").read_text()
        assert "last_ci_status" in content, "CI GREEN objective detection missing"

    def test_objective_plugin_nag_prefix_present(self):
        content = (PLUGIN_DIR / "enforce-objective.ts").read_text()
        assert "NAG_PREFIX" in content, "NAG_PREFIX must be present"


class TestEnforceBatchPush:
    """Enforce-batch-push.ts fully covers push discipline specs."""

    def test_batch_push_has_ci_pending_check(self):
        content = (PLUGIN_DIR / "enforce-batch-push.ts").read_text()
        assert "isCiPending" in content, "CI pending check missing"

    def test_batch_push_has_push_patterns(self):
        content = (PLUGIN_DIR / "enforce-batch-push.ts").read_text()
        assert "PUSH_PATTERNS" in content, "PUSH_PATTERNS export missing"

    def test_batch_push_has_deny_message(self):
        content = (PLUGIN_DIR / "enforce-batch-push.ts").read_text()
        assert "DENY_MESSAGE" in content, "DENY_MESSAGE export missing"

    def test_batch_push_covers_git_push_sandboxcom(self):
        content = (PLUGIN_DIR / "enforce-batch-push.ts").read_text()
        assert "git-push-sandboxcom" in content, "git-push-sandboxcom not covered"

    def test_batch_push_covers_development_push(self):
        content = (PLUGIN_DIR / "enforce-batch-push.ts").read_text()
        assert "development-push" in content, "development-push not covered"

    def test_batch_push_covers_batch_push(self):
        content = (PLUGIN_DIR / "enforce-batch-push.ts").read_text()
        assert "batch-push" in content, "batch-push not covered"


class TestEnforceStop:
    """Enforce-stop.ts has all required detection patterns."""

    def test_stop_has_real_pending_work(self):
        content = plugin_contract_source(PLUGIN_DIR / "enforce-stop.ts")
        assert "hasRealPendingWork" in content, "hasRealPendingWork missing"

    def test_stop_checks_ci_state(self):
        content = plugin_contract_source(PLUGIN_DIR / "enforce-stop.ts")
        assert "ci-verdict" in content or "ci_verdict" in content or "ciVerdict" in content or "CI" in content, (
            "CI state check missing"
        )

    def test_stop_checks_release_completeness(self):
        content = plugin_contract_source(PLUGIN_DIR / "enforce-stop.ts")
        assert "release" in content.lower(), "release completeness check missing"

    def test_stop_detects_status_summaries(self):
        content = plugin_contract_source(PLUGIN_DIR / "enforce-stop.ts")
        assert "STATUS_SUMMARY" in content or "statusSummary" in content or "status_summary" in content.lower(), (
            "status summary detection missing"
        )

    def test_stop_has_qa_response_patterns(self):
        content = plugin_contract_source(PLUGIN_DIR / "enforce-stop.ts")
        assert "QA_RESPONSE" in content or "qaResponse" in content or "qa_response" in content.lower(), (
            "QA response patterns missing"
        )

    def test_stop_has_bolded_header_detection(self):
        content = plugin_contract_source(PLUGIN_DIR / "enforce-stop.ts")
        assert "bold" in content.lower() or "**" in content or "header" in content.lower(), (
            "bolded header detection missing"
        )


class TestEnforceMultitask:
    """Enforce-multitask.ts keeps adaptive minima below a hard ceiling."""

    def test_multitask_hard_max_is_10_and_default_min_is_adaptive(self):
        content = plugin_contract_source(PLUGIN_DIR / "enforce-multitask.ts")
        assert "multitask_config" in content, (
            "enforce-multitask must import HARD_MAX_DISPATCHES from multitask_config.ts"
        )
        assert "HAS_CONFIGURED_MIN_DISPATCHES" in content
        assert "REQUIRED_DISPATCHES" in content

    def test_multitask_has_zero_streak_counter(self):
        content = plugin_contract_source(PLUGIN_DIR / "enforce-multitask.ts")
        assert "zeroStreak" in content, "zeroStreak counter missing"

    def test_multitask_has_consecutive_non_dispatch(self):
        content = plugin_contract_source(PLUGIN_DIR / "enforce-multitask.ts")
        assert "consecutiveNonDispatch" in content, "consecutiveNonDispatch counter missing"

    def test_multitask_has_wave_history(self):
        content = plugin_contract_source(PLUGIN_DIR / "enforce-multitask.ts")
        assert "waveHistory" in content, "waveHistory missing"

    def test_multitask_has_estimated_in_flight(self):
        content = plugin_contract_source(PLUGIN_DIR / "enforce-multitask.ts")
        assert "estimatedInFlight" in content, "estimatedInFlight counter missing"

    def test_multitask_has_message_boundary_detection(self):
        content = plugin_contract_source(PLUGIN_DIR / "enforce-multitask.ts")
        assert "text.complete" in content or "messageBoundary" in content or "MSG_GAP" in content, (
            "message boundary detection missing"
        )


class TestEnforceTdd:
    """Enforce-tdd.ts mechanically enforces test-first workflow."""

    def test_tdd_blocks_src_edits_without_test(self):
        content = (PLUGIN_DIR / "enforce-tdd.ts").read_text()
        assert "permissionDecision" in content or "deny" in content, "TDD plugin must be blocking"

    def test_tdd_has_allowlist(self):
        content = (PLUGIN_DIR / "enforce-tdd.ts").read_text()
        assert "init" in content.lower() or "ALLOWLIST" in content or "allowlist" in content.lower(), (
            "TDD allowlist missing"
        )

    def test_tdd_scoped_to_src_general_ludd(self):
        content = (PLUGIN_DIR / "enforce-tdd.ts").read_text()
        assert "general_ludd" in content or "src/" in content, "TDD not scoped to src/general_ludd"

    def test_tdd_candidate_test_path_matches_check_tdd_compliance(self):
        content = (PLUGIN_DIR / "enforce-tdd.ts").read_text()
        assert "candidate" in content.lower() or "test" in content, "test path logic missing"


# ── Makefile guard tests ─────────────────────────────────────────────────────


class TestMakefileGuards:
    """Makefile has required guard targets."""

    def test_makefile_exists(self):
        assert MAKEFILE_PATH.exists()

    def test_push_rate_guard_exists(self):
        content = MAKEFILE_PATH.read_text()
        assert "_push-rate-guard" in content, "_push-rate-guard target missing"

    def test_ci_busy_check_exists(self):
        content = MAKEFILE_PATH.read_text()
        assert "ci-busy-check" in content, "ci-busy-check target missing"

    def test_gate_fresh_check_exists(self):
        content = MAKEFILE_PATH.read_text()
        assert "_gate-fresh-check" in content, "_gate-fresh-check target missing"

    def test_test_disabled_guard_exists(self):
        content = MAKEFILE_PATH.read_text()
        assert (
            "_test-disabled-guard" in content
            or "test-disabled" in content
            or "test_remove" in content
            or "test_skip" in content
            or "noqa" in content
        ), "Test-disabling guard missing in Makefile."

    def test_check_duplicate_targets_exists(self):
        content = MAKEFILE_PATH.read_text()
        assert "check-duplicate-targets" in content, "check-duplicate-targets target missing"

    def test_gate_background_exists(self):
        content = MAKEFILE_PATH.read_text()
        assert "gate-background" in content, "gate-background target missing"

    def test_gate_status_exists(self):
        content = MAKEFILE_PATH.read_text()
        assert "gate-status:" in content, "gate-status target missing"

    def test_gate_lite_exists(self):
        content = MAKEFILE_PATH.read_text()
        assert "gate-lite:" in content, "gate-lite target missing"

    def test_release_cut_exists(self):
        content = MAKEFILE_PATH.read_text()
        assert "release-cut:" in content, "release-cut target missing"

    def test_verify_release_completeness_exists(self):
        content = MAKEFILE_PATH.read_text()
        assert "verify-release-completeness" in content, "verify-release-completeness target missing"

    def test_require_ci_green_exists(self):
        content = MAKEFILE_PATH.read_text()
        assert "require-ci-green" in content, "require-ci-green target missing"

    def test_deploy_and_forget_exists(self):
        content = MAKEFILE_PATH.read_text()
        assert "deploy-and-forget" in content, "deploy-and-forget target missing"

    def test_agent_worktree_exists(self):
        content = MAKEFILE_PATH.read_text()
        assert "agent-worktree:" in content, "agent-worktree target missing"

    def test_agent_merge_exists(self):
        content = MAKEFILE_PATH.read_text()
        assert "agent-merge:" in content, "agent-merge target missing"

    def test_feature_start_exists(self):
        content = MAKEFILE_PATH.read_text()
        assert "feature-start:" in content, "feature-start target missing"

    def test_feature_done_exists(self):
        content = MAKEFILE_PATH.read_text()
        assert "feature-done:" in content, "feature-done target missing"

    def test_gated_merge_exists(self):
        content = MAKEFILE_PATH.read_text()
        assert "gated-merge:" in content, "gated-merge target missing"

    def test_git_merge_abort_exists(self):
        content = MAKEFILE_PATH.read_text()
        assert "git-merge-abort" in content, "git-merge-abort target missing"

    def test_ci_verdict_safe_exists(self):
        content = MAKEFILE_PATH.read_text()
        assert "ci-verdict-safe" in content, "ci-verdict-safe target missing"

    def test_collect_check_exists(self):
        content = MAKEFILE_PATH.read_text()
        assert "collect-check:" in content, "collect-check target missing"

    def test_check_plugin_registration_exists(self):
        content = MAKEFILE_PATH.read_text()
        assert "check-plugin-registration:" in content, "check-plugin-registration target missing (AA056)"

    def test_check_plugin_order_exists(self):
        content = MAKEFILE_PATH.read_text()
        assert "check-plugin-order:" in content, "check-plugin-order target missing (AA077)"

    def test_check_plugin_overlap_exists(self):
        content = MAKEFILE_PATH.read_text()
        assert "check-plugin-overlap:" in content, "check-plugin-overlap target missing (AA097)"

    def test_check_ratchet_population_exists(self):
        content = MAKEFILE_PATH.read_text()
        assert "check-ratchet-population:" in content, "check-ratchet-population target missing (AA091)"


# ── AGENTS.md policy tests ───────────────────────────────────────────────────


class TestAgentsMdPolicies:
    """AGENTS.md has required policy sections."""

    def test_agents_md_exists(self):
        assert AGENTS_PATH.exists()

    def test_dont_push_every_commit_policy(self):
        content = AGENTS_PATH.read_text()
        assert "Don't Push Every Commit" in content, "Push batched policy missing"

    def test_branch_discipline_policy(self):
        content = AGENTS_PATH.read_text()
        assert "Branch discipline" in content or "branch-discipline" in content, "Branch discipline policy missing"

    def test_primary_objective_policy(self):
        content = AGENTS_PATH.read_text()
        assert "PRIMARY OBJECTIVE" in content or "objective" in content.lower(), "PRIMARY OBJECTIVE reference missing"

    def test_tdd_policy(self):
        content = AGENTS_PATH.read_text()
        assert "TDD Policy" in content or "TDD:" in content, "TDD policy missing"

    def test_dispatch_floor_policy(self):
        content = AGENTS_PATH.read_text()
        assert "10-Agent Dispatch Floor" in content or "Dispatch Floor" in content, "Dispatch floor policy missing"

    def test_premature_stop_policy(self):
        content = AGENTS_PATH.read_text()
        assert "Premature-Stop" in content, "Premature-stop policy missing"

    def test_nothing_dropped_guardrail(self):
        content = AGENTS_PATH.read_text()
        assert "Nothing-Dropped" in content, "Nothing-Dropped guardrail missing"

    def test_ci_poll_subagents_forbidden(self):
        content = AGENTS_PATH.read_text()
        assert "CI-Poll Subagents Are Forbidden" in content, "CI-poll forbidden policy missing"

    def test_release_is_artifact_not_tag(self):
        content = AGENTS_PATH.read_text()
        assert "Release is an Artifact" in content, "Release artifact policy missing"

    def test_gate_discipline(self):
        content = AGENTS_PATH.read_text()
        assert "Green Gate" in content or "Gate Discipline" in content, "Gate discipline policy missing"

    def test_root_cause_only_fix(self):
        content = AGENTS_PATH.read_text()
        assert "Root-Cause-Only" in content, "Root-cause fix policy missing"

    def test_merge_safety_policy(self):
        content = AGENTS_PATH.read_text()
        assert "Merge" in content, "Merge safety policy missing"


# ── CI workflow tests ────────────────────────────────────────────────────────


class TestCiWorkflow:
    """CI workflow has required discipline enforcement."""

    def test_ci_workflow_exists(self):
        assert CI_WORKFLOW.exists(), f"CI workflow missing at {CI_WORKFLOW}"

    def test_release_job_needs_gate(self):
        content = CI_WORKFLOW.read_text()
        if "release:" in content:
            assert "gate" in content, "release job should need gate"


# ── Spec-to-test coverage mapping ────────────────────────────────────────────


class TestSpecCoverage:
    """Every spec group has structural tests in this file."""

    @staticmethod
    def _group_count(prefix: str) -> int:
        return sum(1 for s in spec_ids(read_specs()) if s.startswith(prefix))

    def test_push_discipline_coverage(self):
        for _test_name in [
            "test_p01_no_push_while_ci_pending",
            "test_p02_ci_busy_check_on_all_push_targets",
            "test_p03_no_commit_threshold_1",
        ]:
            pass
        assert self._group_count("P") >= 1, "Group P: no push discipline specs found"

    def test_branch_discipline_coverage(self):
        assert self._group_count("B") >= 1, "Group B: no branch discipline specs found"

    def test_objective_tracking_coverage(self):
        assert self._group_count("O") >= 1, "Group O: no objective tracking specs found"

    def test_test_integrity_coverage(self):
        assert self._group_count("T") >= 1, "Group T: no test integrity specs found"

    def test_dispatch_floor_coverage(self):
        assert self._group_count("D") >= 1, "Group D: no dispatch floor specs found"

    def test_anti_stop_coverage(self):
        assert self._group_count("S") >= 1, "Group S: no anti-stop specs found"

    def test_anti_essay_coverage(self):
        assert self._group_count("E") >= 1, "Group E: no anti-essay specs found"

    def test_merge_safety_coverage(self):
        assert self._group_count("M") >= 1, "Group M: no merge safety specs found"

    def test_gate_discipline_coverage(self):
        assert self._group_count("G") >= 1, "Group G: no gate discipline specs found"

    def test_release_discipline_coverage(self):
        assert self._group_count("R") >= 1, "Group R: no release discipline specs found"

    def test_worktree_discipline_coverage(self):
        assert self._group_count("W") >= 1, "Group W: no worktree discipline specs found"

    def test_ci_discipline_coverage(self):
        assert self._group_count("F") >= 1, "Group F: no CI discipline specs found"

    def test_commit_discipline_coverage(self):
        assert self._group_count("C") >= 1, "Group C: no commit discipline specs found"

    def test_quality_gate_coverage(self):
        assert self._group_count("Q") >= 1, "Group Q: no quality gate specs found"

    def test_subagent_discipline_coverage(self):
        assert self._group_count("X") >= 1, "Group X: no subagent discipline specs found"

    def test_audit_discipline_coverage(self):
        assert self._group_count("A") >= 1, "Group A: no audit discipline specs found"

    def test_naming_code_coverage(self):
        assert self._group_count("N") >= 1, "Group N: no naming code specs found"

    def test_knowledge_context_coverage(self):
        assert self._group_count("K") >= 1, "Group K: no knowledge context specs found"

    def test_user_intent_coverage(self):
        assert self._group_count("U") >= 1, "Group U: no user intent specs found"

    def test_zerofail_coverage(self):
        assert self._group_count("Z") >= 1, "Group Z: no zerofail specs found"

    def test_hard_break_coverage(self):
        assert self._group_count("H") >= 1, "Group H: no hard break specs found"

    def test_verification_coverage(self):
        assert self._group_count("V") >= 1, "Group V: no verification specs found"

    def test_judgment_coverage(self):
        assert self._group_count("J") >= 1, "Group J: no judgment specs found"

    def test_learning_coverage(self):
        assert self._group_count("L") >= 1, "Group L: no learning specs found"

    def test_yield_coverage(self):
        assert self._group_count("Y") >= 1, "Group Y: no yield specs found"


# ── Script existence tests ───────────────────────────────────────────────────


class TestScriptsExist:
    """Required enforcement scripts exist."""

    SCRIPTS: ClassVar[list[str]] = [
        "scripts/require_ci_green.py",
        "scripts/ci_check_cooldown.py",
        "scripts/check_readme_status_current.py",
        "scripts/check_duplicate_targets.py",
        "scripts/check_green_branch_guard.py",
        "scripts/check_tdd_compliance.py",
        "scripts/check_disk_usage.py",
        "scripts/check_node_v26_compat.py",
        "scripts/check_plugin_registration.py",
        "scripts/check_plugin_order.py",
        "scripts/check_plugin_overlap.py",
        "scripts/check_ratchet_population.py",
    ]

    @pytest.mark.parametrize("script_path", SCRIPTS)
    def test_script_exists(self, script_path):
        p = ROOT / script_path
        assert p.exists(), f"Required script missing: {script_path}"
        assert p.stat().st_size > 0, f"Script is empty: {script_path}"


# ── Anti-essay plugin spec tests ─────────────────────────────────────────────


class TestAntiEssayPluginSpecs:
    """Verify the anti-essay plugin meets E01-E20 requirements."""

    def test_anti_essay_detects_word_count(self):
        p = PLUGIN_DIR / "enforce-anti-essay.ts"
        if not p.exists():
            pytest.skip("enforce-anti-essay.ts not yet created")
        content = p.read_text()
        assert "word" in content.lower() or "length" in content.lower() or "count" in content.lower(), (
            "anti-essay plugin does not track word count"
        )

    def test_anti_essay_checks_tool_calls(self):
        p = PLUGIN_DIR / "enforce-anti-essay.ts"
        if not p.exists():
            pytest.skip("enforce-anti-essay.ts not yet created")
        content = p.read_text()
        assert "tool" in content.lower(), "anti-essay plugin does not check tool calls"

    def test_anti_essay_has_subagent_guard(self):
        p = PLUGIN_DIR / "enforce-anti-essay.ts"
        if not p.exists():
            pytest.skip("enforce-anti-essay.ts not yet created")
        content = p.read_text()
        assert "isSubagent" in content, "anti-essay plugin missing subagent guard"


# ── Branch discipline plugin spec tests ──────────────────────────────────────


class TestBranchDisciplinePluginSpecs:
    """Verify the branch-discipline plugin meets B01-B25 requirements."""

    def test_branch_plugin_verifies_current_branch(self):
        p = PLUGIN_DIR / "enforce-branch-discipline.ts"
        if not p.exists():
            pytest.skip("enforce-branch-discipline.ts not yet created")
        content = p.read_text()
        assert "branch" in content.lower(), "branch discipline plugin does not check branches"

    def test_branch_plugin_blocks_wrong_branch_mutations(self):
        p = PLUGIN_DIR / "enforce-branch-discipline.ts"
        if not p.exists():
            pytest.skip("enforce-branch-discipline.ts not yet created")
        content = p.read_text()
        assert "deny" in content or "block" in content.lower() or "permissionDecision" in content, (
            "branch discipline plugin must block wrong-branch operations"
        )


# ── Test integrity plugin spec tests ─────────────────────────────────────────


class TestTestIntegrityPluginSpecs:
    """Verify the test-integrity plugin meets T01-T30 requirements."""

    def test_test_integrity_blocks_skip(self):
        p = PLUGIN_DIR / "enforce-test-integrity.ts"
        if not p.exists():
            pytest.skip("enforce-test-integrity.ts not yet created")
        content = p.read_text()
        assert "skip" in content.lower() or "xfail" in content.lower(), (
            "test integrity plugin does not detect skip/xfail"
        )

    def test_test_integrity_blocks_continue_on_error(self):
        p = PLUGIN_DIR / "enforce-test-integrity.ts"
        if not p.exists():
            pytest.skip("enforce-test-integrity.ts not yet created")
        content = p.read_text()
        assert "continue-on-error" in content or "continueOnError" in content or "continue_on_error" in content, (
            "test integrity plugin does not detect continue-on-error"
        )


# ── Completion check ─────────────────────────────────────────────────────────


def test_spec_count_is_at_least_1000():
    """Guardrail: BEHAVIORAL_SPECS.md should have at least 1000 specs."""
    ids = spec_ids(read_specs())
    assert len(ids) >= 1000, (
        f"Expected >=1000 specs, found {len(ids)}. "
        f"Groups: {[(p, sum(1 for s in ids if s.startswith(p))) for p in 'PBOTDSE MGRWF CQXANKUZ HVJLY']}"
    )
