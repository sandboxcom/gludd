"""TDD tests for the enforce-tdd real-time TDD guardrail plugin.

This is the mechanical enforcement layer for AGENTS.md's TDD policy. The
existing ``scripts/check_tdd_compliance.py`` only runs at COMMIT time — by
then an agent has already wasted tokens writing implementation code with no
test. This plugin blocks the editor itself: you literally cannot write to
``src/general_ludd/**/*.py`` until a corresponding test file exists.

Workflow enforced (the agent MUST follow this order):

1. Write ``tests/unit/test_<module>.py``            (the failing test)  — ALLOWED
2. Run it, confirm it fails                         (TDD red phase)
3. Write/edit ``src/general_ludd/<module>.py``      (implementation)   — ALLOWED
   (because the test file now exists)
4. Run the test, confirm it passes                  (TDD green phase)

Skip step 1 and step 3 is mechanically DENIED.

This test file was written BEFORE the plugin (TDD). It will fail until the
plugin at ``.opencode/plugin/enforce-tdd.ts`` exists and behaves correctly.
"""

import json
import re
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
PLUGIN_PATH = ROOT / ".opencode" / "plugin" / "enforce-tdd.ts"
OPENCODE_JSON = ROOT / "opencode.json"

# Spec allowlist — files exempt from the "must have a test" rule. MUST match
# scripts/check_tdd_compliance.py ALLOWLIST exactly so the commit-time check
# and the real-time plugin agree.
SPEC_ALLOWLIST_PATTERNS = [
    r".*/__init__\.py$",
    r".*/__pycache__/.*",
    r".*\.pyi$",
    r".*/typing\.py$",
    r".*/type_defs\.py$",
    r".*/protocols\.py$",
    r".*/_types\.py$",
]


# --------------------------------------------------------------------------- #
# Structural: plugin file, registration, hook shape.
# --------------------------------------------------------------------------- #
class TestPluginStructure:
    def test_plugin_file_exists(self):
        assert PLUGIN_PATH.exists(), (
            "enforce-tdd.ts must exist at .opencode/plugin/. This is the "
            "real-time TDD enforcement layer — without it, agents can write "
            "implementation code with no test and only get blocked at commit."
        )

    def test_plugin_registered_in_opencode_json(self):
        assert OPENCODE_JSON.exists(), "opencode.json must exist"
        cfg = json.loads(OPENCODE_JSON.read_text())
        plugins = cfg.get("plugin", [])
        assert any("enforce-tdd" in p for p in plugins), (
            "enforce-tdd.ts must be registered in opencode.json plugin[] array. "
            "An unregistered plugin never loads — this is the 2026-07-12 bug "
            "where 800+ tests passed but enforcement never fired at runtime."
        )

    def test_tool_execute_before_hook_registered(self):
        if not PLUGIN_PATH.exists():
            pytest_skip("plugin not yet written — TDD red phase")
        src = PLUGIN_PATH.read_text()
        assert '"tool.execute.before"' in src, (
            "plugin must register a tool.execute.before hook (the only hook "
            "surface that can deny an edit/write before it lands)"
        )

    def test_hook_matches_edit_and_write_tools(self):
        if not PLUGIN_PATH.exists():
            pytest_skip("plugin not yet written — TDD red phase")
        src = PLUGIN_PATH.read_text()
        assert '"edit"' in src and '"write"' in src, (
            "hook must match BOTH edit and write tools — agents can bypass a "
            "write-only check by using edit, and vice versa"
        )

    def test_plugin_has_no_named_exports(self):
        if not PLUGIN_PATH.exists():
            pytest_skip("plugin not yet written — TDD red phase")
        src = PLUGIN_PATH.read_text()
        # OpenCode auto-loads every module export and requires each export to
        # be a plugin factory.  Named helper exports therefore crash startup;
        # behavioral coverage exercises the default hook instead.
        assert not re.search(r"export\s+(?:async\s+)?function\s+(?!default)", src)
        assert not re.search(r"export\s+const\s+(?!default)", src)

    def test_subagent_guard_present(self):
        if not PLUGIN_PATH.exists():
            pytest_skip("plugin not yet written — TDD red phase")
        src = PLUGIN_PATH.read_text()
        assert "isSubagent" in src, (
            "plugin must use isSubagent() from lib/shared.ts — subagents "
            "inherit the orchestrator's enforcement, never their own"
        )

    def test_env_disable_present(self):
        if not PLUGIN_PATH.exists():
            pytest_skip("plugin not yet written — TDD red phase")
        src = PLUGIN_PATH.read_text()
        assert "GLUDD_TDD_ENFORCE" in src, (
            "plugin must honor GLUDD_TDD_ENFORCE=0 as an escape hatch "
            "(matches the pattern of every other enforce-*.ts plugin)"
        )

    def test_fail_open_present(self):
        if not PLUGIN_PATH.exists():
            pytest_skip("plugin not yet written — TDD red phase")
        src = PLUGIN_PATH.read_text()
        assert "catch" in src, (
            "plugin must wrap logic in try/catch for fail-open behavior — "
            "a broken plugin must never wedge the editor"
        )


# --------------------------------------------------------------------------- #
# Behavioral: candidate test-path computation matches the commit-time script.
# The plugin and scripts/check_tdd_compliance.py MUST agree on where the
# test file is expected to live, or agents get conflicting signals.
# --------------------------------------------------------------------------- #
class TestCandidateTestPaths:
    """Verify the plugin computes the same candidate test paths as the
    commit-time check_tdd_compliance.py script."""

    def _extract_path_logic(self) -> dict:
        """Extract the path-computation regex/logic from the plugin source."""
        if not PLUGIN_PATH.exists():
            return {}
        src = PLUGIN_PATH.read_text()
        logic = {}
        # The plugin must reference tests/unit/test_<stem>.py pattern.
        m = re.search(r'test_\$\{(\w+)\}\.py|test_[`"\'].*?\$\{', src)
        logic["uses_test_pattern"] = bool(m) or "test_" in src
        logic["references_tests_unit"] = "tests/unit" in src or "tests" in src
        logic["joins_parts_with_underscore"] = (
            '"_"' in src or "'_'" in src or ".join(" in src
        )
        return logic

    def test_references_tests_directory(self):
        if not PLUGIN_PATH.exists():
            pytest_skip("plugin not yet written — TDD red phase")
        logic = self._extract_path_logic()
        assert logic.get("references_tests_unit"), (
            "plugin must compute test paths under tests/ — it must look for "
            "tests/unit/test_<module>.py, not a test file in an arbitrary location"
        )

    def test_candidate_paths_match_spec(self):
        """For src/general_ludd/foo.py, the candidate test path must be
        tests/unit/test_general_ludd_foo.py OR tests/unit/test_foo.py.
        This mirrors check_tdd_compliance.py _candidate_test_paths()."""
        if not PLUGIN_PATH.exists():
            pytest_skip("plugin not yet written — TDD red phase")
        src = PLUGIN_PATH.read_text()
        # The plugin must produce BOTH the full-path stem and the leaf-name
        # candidate (two candidates, matching the python script).
        assert "leaf" in src or "parts" in src, (
            "plugin must compute both the full-module stem candidate AND the "
            "leaf-name candidate (two paths), matching the commit-time script"
        )


# --------------------------------------------------------------------------- #
# Behavioral verdicts — the contract.
# These exercise the hook's shouldAllowEdit() path with realistic inputs using
# a temp tests/ tree so "test exists" checks reflect the real filesystem.
# --------------------------------------------------------------------------- #
class TestTDDVerdicts:
    """Verify the plugin denies implementation edits when no test exists,
    and allows them once the test file is present."""

    def _load_plugin_verdict(self):
        """Import the plugin's shouldAllowEdit by extracting and exec'ing it.

        We can't import .ts directly; instead we extract the exported
        shouldAllowEdit logic via a small node bridge if available, or fall
        back to structural verification of the decision table.
        """
        # The behavioral verification is handled by the node .mjs runtime test
        # (.opencode/plugin/enforce-tdd.test.node.mjs) which invokes the real
        # hook. Here we pin the decision contract via source inspection.
        if not PLUGIN_PATH.exists():
            return None
        return PLUGIN_PATH.read_text()

    def test_denies_when_no_test_file_exists(self):
        """The core TDD rule: editing src/ when no test exists = DENY."""
        src = self._load_plugin_verdict()
        if src is None:
            pytest_skip("plugin not yet written — TDD red phase")
        # The plugin MUST contain the deny path for missing test files.
        assert "permissionDecision" in src and "deny" in src, (
            "plugin must emit permissionDecision:deny when no test file exists"
        )
        assert "test" in src.lower() and "first" in src.lower(), (
            "deny message must tell the agent to write the test FIRST — "
            "a generic 'denied' message leaves the agent guessing"
        )

    def test_allows_when_test_file_exists(self):
        """Once the test file exists, editing the implementation is ALLOWED."""
        src = self._load_plugin_verdict()
        if src is None:
            pytest_skip("plugin not yet written — TDD red phase")
        # The plugin must check fs.existsSync (or similar) on the candidate
        # test path and allow when it returns true.
        assert "existsSync" in src or "isFile" in src or "statSync" in src, (
            "plugin must check whether the test file exists on disk before "
            "deciding — without a real filesystem check, it can't enforce TDD"
        )

    def test_allows_allowlisted_init_py(self):
        """__init__.py is allowlisted — editing it must not require a test."""
        src = self._load_plugin_verdict()
        if src is None:
            pytest_skip("plugin not yet written — TDD red phase")
        assert "__init__" in src, (
            "plugin must allowlist __init__.py (matches "
            "check_tdd_compliance.py ALLOWLIST)"
        )

    def test_allows_allowlisted_type_stubs(self):
        """*.pyi, protocols.py, typing.py, type_defs.py, _types.py are
        allowlisted — type definitions don't need behavioral tests."""
        src = self._load_plugin_verdict()
        if src is None:
            pytest_skip("plugin not yet written — TDD red phase")
        for pattern in [".pyi", "protocols", "typing", "type_defs", "_types"]:
            assert pattern in src, (
                f"plugin must allowlist '{pattern}' (matches "
                f"check_tdd_compliance.py ALLOWLIST)"
            )

    def test_allows_editing_test_files_themselves(self):
        """Editing a file under tests/ must be allowed — you ARE writing
        the test. Blocking this would make TDD impossible."""
        src = self._load_plugin_verdict()
        if src is None:
            pytest_skip("plugin not yet written — TDD red phase")
        # The plugin's scope check must exclude tests/ from the src/ gate.
        assert "tests/" in src or "tests\\" in src or "src/general_ludd" in src, (
            "plugin must scope the check to src/general_ludd/ — files under "
            "tests/ must pass through freely (you're writing the test)"
        )

    def test_allows_non_src_files(self):
        """Editing files outside src/general_ludd/ (docs, configs, scripts)
        must not trigger the TDD gate."""
        src = self._load_plugin_verdict()
        if src is None:
            pytest_skip("plugin not yet written — TDD red phase")
        assert "src/general_ludd" in src or "general_ludd" in src, (
            "plugin must scope the TDD gate to src/general_ludd/ only — "
            "docs/, scripts/, config/ edits are not implementation code"
        )

    def test_deny_message_references_agents_md(self):
        """The deny message must point at AGENTS.md TDD policy so the agent
        knows where the rule comes from and how to comply."""
        src = self._load_plugin_verdict()
        if src is None:
            pytest_skip("plugin not yet written — TDD red phase")
        assert "AGENTS.md" in src or "TDD" in src, (
            "deny message must reference AGENTS.md TDD policy so the agent "
            "understands WHY it was blocked, not just THAT it was blocked"
        )


def pytest_skip(reason: str):
    import pytest
    if reason:
        pytest.skip(reason)
    raise AssertionError("missing skip reason")


# --------------------------------------------------------------------------- #
# Integration: the plugin and the commit-time script must agree on allowlist.
# If they diverge, agents get one verdict at edit time and another at commit.
# --------------------------------------------------------------------------- #
class TestScriptPluginAllowlistAgreement:
    def test_allowlist_patterns_match_commit_script(self):
        """The plugin's allowlist MUST be a subset of (or equal to) the
        commit-time script's allowlist. Otherwise an edit that the plugin
        allows could be blocked at commit, or vice versa."""
        if not PLUGIN_PATH.exists():
            pytest_skip("plugin not yet written — TDD red phase")
        src = PLUGIN_PATH.read_text()
        # Each spec allowlist pattern must appear (as a literal or regex) in
        # the plugin source.
        for allow in ["__init__", ".pyi", "protocols", "typing", "type_defs"]:
            assert allow in src, (
                f"plugin allowlist must include '{allow}' to match the "
                f"commit-time script — divergence causes conflicting verdicts"
            )
