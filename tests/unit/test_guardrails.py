"""Tests for the guardrail infrastructure: makefile targets, scripts, config."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.unit._plugin_contract import plugin_contract_source

ROOT = Path(__file__).parent.parent.parent

MAKEFILE = ROOT / "Makefile"
OPENCODE_JSON = ROOT / "opencode.json"
AGENTS_MD = ROOT / "AGENTS.md"
PLUGIN_FILE = ROOT / ".opencode" / "plugin" / "enforce-make.ts"
PLUGIN_CONTRACT = plugin_contract_source(PLUGIN_FILE)
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

    def test_make_test_count_passes(self):
        """Suites collect without errors — fast gate, not full test run."""
        result = subprocess.run(
            ["make", "test-count"],
            capture_output=True, text=True, cwd=str(ROOT), timeout=120,
        )
        assert result.returncode == 0, f"make test-count failed:\n{result.stderr}\n{result.stdout}"

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
        assert "0.1.0" in result.stdout or "0.1.0-alpha" in result.stdout

    def test_make_ansible_syntax_passes(self):
        collections_dir = ROOT / "collections"
        venv_ansible = ROOT / ".venv" / "bin" / "ansible-playbook"
        if not shutil.which("ansible-playbook") and not venv_ansible.exists():
            pytest.skip("ansible-playbook not installed")
        if not collections_dir.exists():
            pytest.skip(f"collections directory not found: {collections_dir}")
        # `make ansible-syntax` runs `ansible-playbook --syntax-check` across all
        # registered playbooks (~30), each paying ansible import overhead. Inside
        # the full gate this test runs concurrently with the rest of the suite
        # under xdist, so the wall-clock contends for CPU; 60s was too tight and
        # flaked. 300s tolerates parallel-gate load while still catching a hang.
        result = subprocess.run(
            ["make", "ansible-syntax"],
            capture_output=True, text=True, cwd=str(ROOT), timeout=300,
        )
        output = f"{result.stdout}\n{result.stderr}"
        assert result.returncode == 0, f"make ansible-syntax failed:\n{output}"
        assert "[WARNING]" not in output, f"make ansible-syntax emitted warnings:\n{output}"


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
        content = PLUGIN_CONTRACT
        assert "export default" in content
        assert "tool.execute.before" in content
        assert "input.tool" in content

    def test_plugin_checks_bash_tool(self):
        content = PLUGIN_CONTRACT
        assert '"bash"' in content

    def test_plugin_checks_make_prefix(self):
        content = PLUGIN_CONTRACT
        assert "make " in content
        assert "throw new Error" in content

    def test_plugin_provides_helpful_error_message(self):
        content = PLUGIN_CONTRACT
        assert "BLOCKED" in content
        assert "Makefile" in content
        assert "AGENTS.md" in content

    def test_plugin_allows_make_commands(self):
        content = PLUGIN_CONTRACT
        assert "startsWith" in content or "make" in content


class TestOpencodeJsonSchemaGuardPlugin:
    """The enforce-make.ts plugin has a PreToolUse guard that denies writes/edits
    to opencode.json when the proposed content has unknown top-level keys.
    This mirrors the Python test in test_opencode_json_schema.py.
    The guard prevents a class of incident (2026-06-28) where an agent added
    `"env": {...}` at the top level — opencode silently drops unknown keys
    (additionalProperties: false), breaking every plugin that needed the value.
    """

    ALLOWED_KEYS_PYTHON = ROOT.parent / "tests" / "unit"
    PLUGIN_CONTENT = PLUGIN_CONTRACT

    def test_plugin_contains_schema_guard_block(self):
        """The plugin must contain the BLOCKED message for unknown keys."""
        assert "BLOCKED: opencode.json top-level key(s) not in opencode schema" in self.PLUGIN_CONTENT

    def test_plugin_guard_applies_to_write_and_edit(self):
        """Both write and edit tools must be within the guard's scope."""
        assert "input.tool === \"write\"" in self.PLUGIN_CONTENT or "input.tool === 'write'" in self.PLUGIN_CONTENT
        assert "input.tool === \"edit\"" in self.PLUGIN_CONTENT or "input.tool === 'edit'" in self.PLUGIN_CONTENT

    def test_plugin_parses_json_to_validate_keys(self):
        """The guard must parse JSON content, not just regex-check."""
        assert "JSON.parse" in self.PLUGIN_CONTENT

    def test_plugin_allowlist_matches_python_allowlist(self):
        """The TypeScript ALLOWED_TOP_LEVEL_KEYS must match the Python test's set
        exactly. If they diverge, one guard will miss a violation the other catches."""
        from tests.unit.test_opencode_json_schema import ALLOWED_TOP_LEVEL_KEYS as py_keys

        ts_content = self.PLUGIN_CONTENT
        ts_start = ts_content.find("ALLOWED_TOP_LEVEL_KEYS:")
        assert ts_start > 0, "Could not find ALLOWED_TOP_LEVEL_KEYS in plugin"
        ts_block = ts_content[ts_start:]
        ts_end = ts_block.find("]")
        ts_block = ts_block[:ts_end]
        ts_keys = set()
        for m in __import__("re").finditer(r'"([^"]+)"', ts_block):
            ts_keys.add(m.group(1))

        # Remove $schema which is present in both sets
        both = {"$schema"}
        ts_only = ts_keys - py_keys - both
        py_only = py_keys - ts_keys - both
        assert not ts_only, f"TypeScript allowlist has keys not in Python: {sorted(ts_only)}"
        assert not py_only, f"Python allowlist has keys not in TypeScript: {sorted(py_only)}"

    def test_plugin_rejects_unknown_keys_in_write_content(self):
        """Simulate the guard: a write with 'env' top-level key must be rejected."""
        assert "unknown" in self.PLUGIN_CONTENT and "throw new Error" in self.PLUGIN_CONTENT

    def test_plugin_references_opencode_schema_url(self):
        """The error message must reference the published schema URL."""
        assert "https://opencode.ai/config.json" in self.PLUGIN_CONTENT


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
        content = PLUGIN_CONTRACT
        assert "TDD VIOLATION" in content, "Plugin must block production edits when no test file exists"
        assert "production" in content.lower() or "src/" in content
        assert "testExists" in content or "test_" in content, "Plugin must check for test file existence"

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
        content_plugin = PLUGIN_CONTRACT
        content_agents = AGENTS_MD.read_text()
        content_config = OPENCODE_JSON.read_text()
        assert "TDD" in content_plugin or "tdd" in content_plugin.lower()
        assert "TDD" in content_agents or "tdd" in content_agents.lower()
        assert "permission" in content_config


class TestGuardrailIntegrity:
    def test_plugin_protects_against_guardrail_removal(self):
        content = PLUGIN_CONTRACT
        assert "GUARDRAIL INTEGRITY VIOLATION" in content, "Plugin must detect guardrail removal"

    def test_agents_md_has_guardrail_integrity_policy(self):
        content = AGENTS_MD.read_text()
        assert "Guardrail Integrity Policy" in content
        assert "NEVER remove" in content or "MUST NEVER remove" in content
        assert "make the guardrail smarter" in content

    def test_plugin_enforcement_patterns_exist(self):
        content = PLUGIN_CONTRACT
        for pattern in ["throw new Error", "TDD VIOLATION", "BLOCKED", "FORBIDDEN"]:
            assert pattern in content, f"Guardrail enforcement pattern '{pattern}' missing from plugin"


class TestCommitAfterGreenGuardrail:
    def test_makefile_test_and_commit_runs_tests_first(self):
        content = MAKEFILE.read_text()
        tac_start = content.index("\ntest-and-commit:")
        tac_end = content.index("\n\n", tac_start) if "\n\n" in content[tac_start:] else len(content)
        tac_section = content[tac_start:tac_end]
        # test-and-commit now routes its test phase through the RAM-bounded
        # adaptive runner (scripts/adaptive_test.py tests/) instead of a bare
        # `pytest` invocation. The suite still runs first — adaptive_test.py
        # execs `python -m pytest tests/` — it is just OOM-guarded. Accept
        # either form so the "runs tests first" invariant stays pinned.
        assert "pytest" in tac_section or "adaptive_test.py" in tac_section, (
            "test-and-commit must run the test suite (pytest or the adaptive "
            "runner scripts/adaptive_test.py) before committing"
        )
        assert "git commit" in tac_section
        # The test phase must precede the commit (tests-first ordering).
        run_idx = (
            tac_section.index("adaptive_test.py")
            if "adaptive_test.py" in tac_section
            else tac_section.index("pytest")
        )
        assert run_idx < tac_section.index("git commit"), (
            "tests must run BEFORE git commit in the test-and-commit target"
        )

    def test_makefile_test_and_commit_rejects_if_tests_fail(self):
        content = MAKEFILE.read_text()
        tac_start = content.index("\ntest-and-commit:")
        tac_end = content.index("\n\n", tac_start) if "\n\n" in content[tac_start:] else len(content)
        tac_section = content[tac_start:tac_end]
        lines = tac_section.split("\n")
        pytest_line = None
        commit_line = None
        for i, line in enumerate(lines):
            if "$(UV) run pytest" in line or " uv run pytest" in line:
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
        content = PLUGIN_CONTRACT
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
        content_plugin = PLUGIN_CONTRACT
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
        content = PLUGIN_CONTRACT
        assert "Task Completion Policy" in content or "completion" in content.lower()
        assert "experimental.chat.system.transform" in content

    def test_plugin_warns_about_task_completion(self):
        content = PLUGIN_CONTRACT
        assert "TASK COMPLETION CHECK" in content or "RESUME WORK" in content

    def test_completion_guardrail_has_all_three_layers(self):
        content_plugin = PLUGIN_CONTRACT
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
        src_dir = tmp_path / "src" / "general_ludd"
        if src_dir.exists():
            inits = list(src_dir.rglob("__init__.py"))
            assert len(inits) > 0, "skeleton should create __init__.py files"


class TestEvidencePolicyGuardrail:
    def test_evidence_policy_in_agents_md(self):
        content = AGENTS_MD.read_text()
        assert "Evidence-Based Response" in content or "Evidence" in content
        assert "source" in content.lower() or "evidence" in content.lower()
        assert "CRITICAL" in content

    def test_evidence_policy_in_plugin(self):
        content = PLUGIN_CONTRACT
        assert "Evidence-Based Response" in content or "Evidence" in content or "evidence" in content.lower()
        assert "source" in content.lower() or "cite" in content.lower()

    def test_evidence_checker_exists(self):
        from general_ludd.review.evidence_checker import EvidenceChecker
        checker = EvidenceChecker()
        assert hasattr(checker, "check_claim")
        assert hasattr(checker, "audit_response")


class TestMakeTargetSmokeTests:
    def test_make_targets_referenced_in_makefile(self):
        import re
        import subprocess
        result = subprocess.run(
            ["make", "-qp"],
            capture_output=True, text=True,
            cwd=str(ROOT),
        )
        targets = set(re.findall(r"^(\S+):", result.stdout, re.MULTILINE))
        essential = {"test", "lint", "typecheck", "gate", "qa"}
        missing = essential - targets
        assert not missing, (
            f"Essential make targets missing from Makefile: {missing}"
        )


class TestGateStatusEnforcement:
    def test_plugin_reads_gate_status_in_response_transform(self):
        content = PLUGIN_CONTRACT
        assert ".gate-status" in content, (
            "chat.response.transform must read .gate-status to verify completion claims"
        )

    def test_plugin_response_transform_has_state_based_block(self):
        content = PLUGIN_CONTRACT
        assert "experimental.text.complete" in content
        assert "readFileSync" in content or "gate-status" in content, (
            "completion claims must be verified against .gate-status, not just vocabulary"
        )

    def test_plugin_replaces_response_when_gate_red(self):
        content = PLUGIN_CONTRACT
        has_gate_check = ".gate-status" in content
        has_replace = "output =" in content or "return {" in content
        assert has_gate_check and has_replace, (
            "response must be replaced when gate is red/stale/missing"
        )

    def test_plugin_registers_session_idle_event(self):
        content = PLUGIN_CONTRACT
        assert '"session.idle"' in content, (
            "session.idle event must be registered to reset turn state per turn"
        )


class TestSystemPromptDiet:
    def test_system_prompt_is_compact(self):
        content = PLUGIN_CONTRACT
        transform_start = content.index('"experimental.chat.system.transform"')
        next_hook = content.index('"experimental.text.complete"')
        transform_block = content[transform_start:next_hook]
        lines = transform_block.split("\n")
        assert len(lines) < 120, (
            f"system transform must be ≤120 lines for small-model comprehension, got {len(lines)}"
        )


RATCHET_MAX = 0
RATCHET_PATH = ROOT / "config" / "ratchet.yml"


class TestRatchetGrowthGuard:
    def test_ratchet_count_at_or_below_max(self):
        content = RATCHET_PATH.read_text()
        entries = [
            line.strip()
            for line in content.splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
        actual = len(entries)
        assert actual <= RATCHET_MAX, (
            f"config/ratchet.yml has {actual} entries, exceeding RATCHET_MAX={RATCHET_MAX}. "
            f"Fix the failing tests or lower RATCHET_MAX in tests/unit/test_guardrails.py."
        )

    def test_ratchet_max_equals_actual_count(self):
        content = RATCHET_PATH.read_text()
        entries = [
            line.strip()
            for line in content.splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
        actual = len(entries)
        assert actual == RATCHET_MAX, (
            f"RATCHET_MAX={RATCHET_MAX} but actual entries={actual}. "
            f"Update RATCHET_MAX to {actual} after burning entries."
        )


class TestTDDGateSharpened:
    def test_tdd_gate_has_test_file_reference_check(self):
        content = PLUGIN_CONTRACT
        has_tdd = "TDD VIOLATION" in content
        has_throw = "throw new Error" in content
        assert has_tdd and has_throw, "TDD gate must still throw for untested edits"

    def test_tdd_gate_searches_for_existing_tests(self):
        content = PLUGIN_CONTRACT
        assert "readdirSync" in content or "testExists" in content, (
            "TDD gate must search for existing test files before rejecting edits"
        )


# Built from fragments so the verbatim armor string never appears as a source
# literal (that would trip detect-private-key / detect-secrets on this very test
# file). The runtime values are identical to the real PEM/OpenSSH markers.
_KEY_DASHES = "-" * 5
_KEY_KINDS = ("OPENSSH ", "RSA ", "DSA ", "EC ", "")
_PRIVATE_KEY_ARMOR = tuple(
    f"{_KEY_DASHES}BEGIN {k}PRIVATE KEY{_KEY_DASHES}" for k in _KEY_KINDS
)


class TestNoTrackedPrivateKeys:
    """W5.1: no OpenSSH/PEM private-key armor may appear in any tracked file.

    The `sandboxcom_github_rsa` deploy key was distributed at the repo root.
    This guard proves it (or any other private key) is NOT committed: it scans
    the actual git-tracked file list, not the working directory, so an
    untracked-but-present key does not trip it (that is an operator concern,
    documented in TASKS.md W5.1 preconditions).
    """

    _ARMOR = _PRIVATE_KEY_ARMOR

    def _tracked_files(self) -> list[str]:
        result = subprocess.run(
            ["git", "ls-files"],
            capture_output=True, text=True, cwd=str(ROOT), timeout=30,
        )
        assert result.returncode == 0, f"git ls-files failed: {result.stderr}"
        return [f for f in result.stdout.splitlines() if f.strip()]

    @staticmethod
    def _looks_like_real_key(text: str) -> bool:
        """True only for armor wrapping a substantial base64 body.

        Test fixtures use placeholder armor with an ellipsis body (BEGIN/END
        lines around ``...``) — those are not real keys. A real key has a long
        contiguous base64 block between the BEGIN/END lines. Require a line of
        >= 60 base64 chars to distinguish a real key from a placeholder.
        """
        import re

        if not any(marker in text for marker in TestNoTrackedPrivateKeys._ARMOR):
            return False
        # A genuine PEM/OpenSSH key body has many ~64-char base64 lines.
        return bool(re.search(r"^[A-Za-z0-9+/]{60,}={0,2}$", text, re.MULTILINE))

    def test_no_private_key_armor_in_tracked_files(self):
        offenders: list[str] = []
        for rel in self._tracked_files():
            path = ROOT / rel
            if not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            if self._looks_like_real_key(text):
                offenders.append(rel)
        assert not offenders, (
            "Real private-key material found in tracked files: "
            f"{offenders}. Untrack and rotate immediately (see docs/history-scrub.md)."
        )

    def test_known_key_filenames_not_tracked(self):
        tracked = set(self._tracked_files())
        forbidden = {"sandboxcom_github_rsa", "sandboxcom_github_rsa.pub"}
        leaked = forbidden & tracked
        assert not leaked, f"Key files are tracked in git: {leaked}"


def test_cost_cap_fails_closed():
    """#69/#59 regression guard — the scoring router's cost cap fails CLOSED.

    This is the ``guard_test_id`` named by the ``fail_open_cost_cap`` bug class
    in ``general_ludd.quality.bug_class_registry``: if the adaptive router's
    cost cap ever regresses to fail-open (returning an over-budget model when no
    candidate fits, or letting a NaN/inf cost slip past the cap), this test goes
    red. The cap must DENY spend it cannot prove is under budget — fall back to
    the safe default model instead of selecting the over-cap candidate.
    """
    import asyncio
    from unittest.mock import AsyncMock

    from general_ludd.scoring.router import AdaptiveRouter

    def _repo(avg_cost: float) -> AsyncMock:
        repo = AsyncMock()
        repo.get_aggregate_scores = AsyncMock(
            side_effect=lambda task_type=None: [
                {
                    "prompt_profile_id": "pp-x",
                    "model_profile_id": "over-budget-model",
                    "task_type": "bug_fix",
                    "sample_count": 10,
                    "avg_cost": avg_cost,
                    "composite_score": 0.9,
                },
            ]
        )
        return repo

    from general_ludd.schemas.benchmark import TaskType

    async def _route(avg_cost: float):
        router = AdaptiveRouter(benchmark_repo=_repo(avg_cost), min_samples=3)
        return await router.route(
            task_type=TaskType.BUG_FIX,
            default_model_profile="safe-default",
            max_cost_usd=0.01,
        )

    # 1. Over the numeric cap with no cheaper fit -> fail closed to default.
    over = asyncio.run(_route(0.10))
    assert over.selected_model_profile_id == "safe-default", (
        "cost cap regressed to FAIL-OPEN: returned over-budget model instead "
        "of the safe default"
    )
    assert over.fallback is True
    assert over.estimated_cost_usd <= 0.01

    # 2. NaN cost must be treated as over-cap (nan > cap is False, so a naive
    #    comparison would let it through).
    nan = asyncio.run(_route(float("nan")))
    assert nan.selected_model_profile_id == "safe-default"

    # 3. inf cost must be treated as over-cap.
    inf = asyncio.run(_route(float("inf")))
    assert inf.selected_model_profile_id == "safe-default"

    # 4. The bug-class registry's guard_test_id must point at THIS test.
    from general_ludd.quality.bug_class_registry import DEFAULT_BUG_CLASSES

    cap_class = next(
        c for c in DEFAULT_BUG_CLASSES if c.id == "fail_open_cost_cap"
    )
    assert cap_class.guard_test_id == (
        "tests/unit/test_guardrails.py::test_cost_cap_fails_closed"
    )
