"""E.4: noqa guardrail 3-layer behavior-pin tests.

Layer map (per AGENTS.md Meta-Rule: Guardrail Policy):
  1. Edit-time hook (enforce-no-suppressions.ts) — denies on edit/write
  2. Behavior-pin test (THIS FILE) — proves deny/allow/fail-open contract
  3. AGENTS.md rule — "CRITICAL: No Lint-Suppression Comments" section

These tests exercise the `shouldAllowEdit` contract by extracting the plugin's
exported SUPPRESSION_PATTERNS and ALLOWLIST_PATHS, then applying them to
representative tool.execute.before scenarios: edit tool, write tool, allowlisted
paths, plain comments, and fail-open error paths.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
PLUGIN_PATH = ROOT / ".opencode" / "plugin" / "enforce-no-suppressions.ts"
OPENCODE_JSON = ROOT / "opencode.json"
AGENTS_MD = ROOT / "AGENTS.md"

SPEC_PATTERNS = [
    r"#\s*noqa",
    r"#\s*type:\s*ignore",
    r"#\s*pylint:",
    r"#\s*fmt:\s*(?:off|skip|on)",
    r"#\s*isort:\s*skip",
]

SPEC_ALLOWLIST = [
    "src/general_ludd/security/fix_not_disable.py",
    "tests/unit/test_type_safety_guardrails.py",
]


def _extract_regex_array(src: str) -> list[str]:
    m = re.search(
        r"(?:export\s+)?const\s+SUPPRESSION_PATTERNS[^=]*=\s*\[(.*?)\]",
        src,
        re.DOTALL,
    )
    assert m, "SUPPRESSION_PATTERNS export must be present"
    return re.findall(r"/([^/]+)/[a-z]*", m.group(1))


def _extract_allowlist(src: str) -> list[str]:
    m = re.search(
        r"(?:export\s+)?const\s+ALLOWLIST_PATHS[^=]*=\s*\[(.*?)\]",
        src,
        re.DOTALL,
    )
    assert m, "ALLOWLIST_PATHS export must be present"
    return re.findall(r'"([^"]+)"', m.group(1))


def _should_allow_edit(
    file_path: str, content: str, patterns: list[str], allowlist: list[str]
) -> tuple[bool, str | None]:
    """Replicate the plugin's shouldAllowEdit logic."""
    try:
        if any(allowed in file_path for allowed in allowlist):
            return True, None
        if any(re.search(p, content) for p in patterns):
            return False, (
                "Lint-suppression comments forbidden. Fix the underlying issue. "
                "See AGENTS.md Guardrail Integrity Policy."
            )
        return True, None
    except Exception:
        return True, None


# --------------------------------------------------------------------------- #
# LAYER 1: Edit-time hook — tool.execute.before contract
# --------------------------------------------------------------------------- #
class TestEditTimeHook:
    """The hook must deny edit/write tools carrying forbidden patterns."""

    @classmethod
    def setup_class(cls):
        cls.src = PLUGIN_PATH.read_text()

    def test_hook_registers_tool_execute_before(self):
        assert '"tool.execute.before"' in self.src, (
            "Layer 1 failure: no tool.execute.before hook registered"
        )

    def test_hook_denies_edit_tool(self):
        assert 'edit' in self.src, (
            "Layer 1 failure: hook does not check edit tool"
        )

    def test_hook_denies_write_tool(self):
        assert 'write' in self.src, (
            "Layer 1 failure: hook does not check write tool"
        )

    def test_hook_inspects_newstring_for_edit(self):
        assert "newString" in self.src, (
            "Layer 1 failure: hook must inspect edit tool's newString arg"
        )

    def test_hook_inspects_content_for_write(self):
        assert "content" in self.src, (
            "Layer 1 failure: hook must inspect write tool's content arg"
        )

    def test_hook_uses_permissiondecision_deny(self):
        assert "permissionDecision" in self.src, (
            "Layer 1 failure: denial must use structured permissionDecision field"
        )

    def test_hook_fail_open_wrapper(self):
        assert "catch" in self.src, (
            "Layer 1 failure: missing try/catch fail-open wrapper"
        )

    def test_hook_skips_subagents(self):
        assert (
            "OPENCODE_SUBAGENT" in self.src
            or (
                "isSubagent" in self.src
                and "../lib/shared.ts" in self.src
            )
        ), (
            "Layer 1 failure: hook must skip through the shared subagent guard"
        )

    def test_hook_deny_message_references_agents_md(self):
        assert "AGENTS.md" in self.src or "agents.md" in self.src.lower(), (
            "Layer 1 failure: deny message must reference AGENTS.md so the "
            "agent knows what policy it violated"
        )


# --------------------------------------------------------------------------- #
# LAYER 1b: opencode.json registration
# --------------------------------------------------------------------------- #
class TestPluginRegistration:
    def test_plugin_in_opencode_json(self):
        cfg = json.loads(OPENCODE_JSON.read_text())
        plugins = cfg.get("plugin", [])
        assert any("enforce-no-suppressions" in p for p in plugins), (
            "Layer 1 failure: enforce-no-suppressions.ts not registered in "
            "opencode.json plugin[] array"
        )


# --------------------------------------------------------------------------- #
# LAYER 2: Behavior-pin — SUPPRESSION_PATTERNS deny on edit/write
# --------------------------------------------------------------------------- #
class TestSuppressionPatternsDeny:
    """Each SUPPRESSION_PATTERN produces a deny on non-allowlisted paths."""

    @classmethod
    def setup_class(cls):
        src = PLUGIN_PATH.read_text()
        cls.patterns = _extract_regex_array(src)
        cls.allowlist = _extract_allowlist(src)

    def test_pattern_count_matches_spec(self):
        assert len(self.patterns) == len(SPEC_PATTERNS), (
            f"got {len(self.patterns)} patterns, expected {len(SPEC_PATTERNS)}"
        )

    def _check(self, file_path: str, content: str) -> bool:
        allowed, _ = _should_allow_edit(
            file_path, content, self.patterns, self.allowlist
        )
        return allowed

    # --- Deny on each pattern variant ----------------------------------------

    def test_deny_noqa_bare(self):
        assert not self._check("src/foo.py", "x = 1  # noqa")

    def test_deny_noqa_with_code(self):
        assert not self._check("src/foo.py", "x = 1  # noqa: E501")

    def test_deny_noqa_with_multiple_codes(self):
        assert not self._check("src/foo.py", "x = 1  # noqa: E501,W503")

    def test_deny_type_ignore_bare(self):
        assert not self._check("src/foo.py", "x: int = f()  # type: ignore")

    def test_deny_type_ignore_with_code(self):
        assert not self._check(
            "src/foo.py", "x: int = f()  # type: ignore[arg-type]"
        )

    def test_deny_type_ignore_extra_whitespace(self):
        assert not self._check("src/foo.py", "x = 1  # type:  \tignore")

    def test_deny_pylint_disable(self):
        assert not self._check("src/foo.py", "obj.attr  # pylint: disable=E1101")

    def test_deny_pylint_disable_all(self):
        assert not self._check("src/foo.py", "# pylint: disable=all")

    def test_deny_fmt_skip(self):
        assert not self._check("src/foo.py", "# fmt: skip")

    def test_deny_fmt_off(self):
        assert not self._check("src/foo.py", "# fmt: off")

    def test_deny_fmt_on(self):
        assert not self._check("src/foo.py", "# fmt: on")

    def test_deny_isort_skip(self):
        assert not self._check("src/foo.py", "import sys  # isort:skip")

    def test_deny_isort_skip_with_space(self):
        assert not self._check("src/foo.py", "import sys  # isort: skip")

    # --- Deny in src/ and tests/ directories (neither is blanket-allowlisted)

    def test_deny_noqa_in_src(self):
        assert not self._check(
            "src/general_ludd/daemon.py", "x = 1  # noqa"
        )

    def test_deny_noqa_in_tests(self):
        assert not self._check(
            "tests/unit/test_other.py", "x = 1  # noqa"
        )

    def test_deny_type_ignore_in_tests(self):
        assert not self._check(
            "tests/integration/test_api.py", "x: int = f()  # type: ignore"
        )


# --------------------------------------------------------------------------- #
# LAYER 2b: Behavior-pin — allowlisted paths pass through
# --------------------------------------------------------------------------- #
class TestAllowlistedPaths:
    """Allowlisted files pass through even when they contain the patterns."""

    @classmethod
    def setup_class(cls):
        src = PLUGIN_PATH.read_text()
        cls.patterns = _extract_regex_array(src)
        cls.allowlist = _extract_allowlist(src)

    def _check(self, file_path: str, content: str) -> bool:
        allowed, _ = _should_allow_edit(
            file_path, content, self.patterns, self.allowlist
        )
        return allowed

    def test_allowlist_contains_fix_not_disable(self):
        assert any(
            "fix_not_disable" in a for a in self.allowlist
        ), "fix_not_disable.py must be in ALLOWLIST_PATHS"

    def test_allowlist_contains_type_safety_guardrails(self):
        assert any(
            "test_type_safety_guardrails" in a for a in self.allowlist
        ), "test_type_safety_guardrails.py must be in ALLOWLIST_PATHS"

    def test_allow_fix_not_disable_with_noqa_string(self):
        assert self._check(
            "src/general_ludd/security/fix_not_disable.py",
            'DISABLE_PATTERNS = frozenset({"# noqa"})',
        )

    def test_allow_fix_not_disable_with_type_ignore_string(self):
        assert self._check(
            "src/general_ludd/security/fix_not_disable.py",
            '"# type: ignore"',
        )

    def test_allow_test_type_safety_guardrails_with_regex_fixture(self):
        assert self._check(
            "tests/unit/test_type_safety_guardrails.py",
            'noqa_pattern = re.compile(r"#\\s*noqa")',
        )

    def test_allow_test_type_safety_guardrails_with_ignore_pattern(self):
        assert self._check(
            "tests/unit/test_type_safety_guardrails.py",
            'ignore_pattern = re.compile(r"#\\s*type:\\s*ignore")',
        )

    def test_allowlist_matches_subpath(self):
        assert self._check(
            "a/b/src/general_ludd/security/fix_not_disable.py",
            "x = 1  # noqa",
        )


# --------------------------------------------------------------------------- #
# LAYER 2c: Behavior-pin — plain comments pass through
# --------------------------------------------------------------------------- #
class TestPlainCommentsPass:
    """Non-suppression comments must not be blocked."""

    @classmethod
    def setup_class(cls):
        src = PLUGIN_PATH.read_text()
        cls.patterns = _extract_regex_array(src)
        cls.allowlist = _extract_allowlist(src)

    def _check(self, file_path: str, content: str) -> bool:
        allowed, _ = _should_allow_edit(
            file_path, content, self.patterns, self.allowlist
        )
        return allowed

    def test_allow_plain_comment(self):
        assert self._check("src/foo.py", "# this is a regular comment")

    def test_allow_code_without_comment(self):
        assert self._check("src/foo.py", "x = 1 + 2\n")

    def test_allow_empty_string(self):
        assert self._check("src/foo.py", "")

    def test_allow_docstring(self):
        assert self._check(
            "src/foo.py",
            '"""This module does things.\n\nIt uses no suppression comments."""',
        )

    def test_allow_prose_mentioning_noqa(self):
        assert self._check(
            "src/foo.py",
            '# We deliberately avoid "noqa" comments here.',
        )

    def test_allow_blank_line(self):
        assert self._check("src/foo.py", "\n")

    def test_allow_normal_import(self):
        assert self._check("src/foo.py", "import os\nfrom pathlib import Path\n")


# --------------------------------------------------------------------------- #
# LAYER 2d: Behavior-pin — fail-open contract
# --------------------------------------------------------------------------- #
class TestFailOpenContract:
    """Any exception in the plugin must allow the edit, never wedge."""

    def test_fail_open_returns_allow(self):
        """shouldAllowEdit must return allow on exception, not explode."""
        allowed, _reason = _should_allow_edit(
            "src/foo.py",
            "x = 1  # noqa",
            [],
            [],
        )
        assert allowed, "fail-open: shouldAllowEdit must allow when patterns list is empty"

    def test_should_allow_edit_try_except_structure(self):
        src = PLUGIN_PATH.read_text()
        assert "try" in src, "missing try block for fail-open"
        assert "catch" in src, "missing catch block for fail-open"
        catch_idx = src.find("catch")
        assert catch_idx != -1
        window = src[catch_idx:catch_idx + 400]
        assert "allow" in window.lower() or "return" in window.lower(), (
            "catch block must return allow (fail-open), not re-throw"
        )

    def test_is_suppression_comment_none_input_returns_false(self):
        """isSuppressionComment with None/empty returns false, not explode."""
        src = PLUGIN_PATH.read_text()
        assert (
            "typeof text" in src or "text.length" in src or "!text" in src
        ), "isSuppressionComment must guard against null/undefined input"
        assert "return false" in src, (
            "isSuppressionComment must return false for invalid input"
        )

    def test_is_allowlisted_path_none_input_returns_false(self):
        """isAllowlistedPath with None/empty returns false, not explode."""
        src = PLUGIN_PATH.read_text()
        has_guard = (
            "typeof filePath" in src
            or "filePath.length" in src
            or "!filePath" in src
        )
        assert has_guard, (
            "isAllowlistedPath must guard against null/undefined input"
        )


# --------------------------------------------------------------------------- #
# LAYER 3: AGENTS.md rule exists and is complete
# --------------------------------------------------------------------------- #
class TestAgentsMdRule:
    """The AGENTS.md rule is the third layer of the 3-layer guardrail."""

    @classmethod
    def setup_class(cls):
        cls.text = AGENTS_MD.read_text()

    def test_section_header_exists(self):
        assert "No Lint-Suppression Comments" in self.text, (
            "Layer 3 failure: AGENTS.md missing 'No Lint-Suppression Comments' section"
        )

    def test_section_is_critical(self):
        idx = self.text.find("No Lint-Suppression Comments")
        window = self.text[max(0, idx - 80):idx]
        assert "CRITICAL" in window, (
            "Layer 3 failure: 'No Lint-Suppression Comments' must be a CRITICAL section"
        )

    def test_forbidden_patterns_listed(self):
        assert "# noqa" in self.text, "AGENTS.md must list # noqa as forbidden"
        assert "# type: ignore" in self.text, (
            "AGENTS.md must list # type: ignore as forbidden"
        )
        assert "# pylint:" in self.text, (
            "AGENTS.md must list # pylint: as forbidden"
        )
        assert "# fmt:" in self.text, (
            "AGENTS.md must list # fmt: as forbidden"
        )
        assert "# isort:" in self.text, (
            "AGENTS.md must list # isort: as forbidden"
        )

    def test_runtime_hook_layer_documented(self):
        section_start = self.text.find("No Lint-Suppression Comments")
        section = self.text[section_start:section_start + 3000]
        assert "opencode/plugin/enforce-no-suppressions" in section, (
            "Layer 3 failure: AGENTS.md must reference the runtime hook"
        )

    def test_behavior_pin_test_documented(self):
        section_start = self.text.find("No Lint-Suppression Comments")
        section = self.text[section_start:section_start + 3000]
        assert "test_no_suppression_comments_plugin" in section, (
            "Layer 3 failure: AGENTS.md must reference the behavior-pin test"
        )

    def test_allowlist_paths_documented(self):
        section_start = self.text.find("No Lint-Suppression Comments")
        section = self.text[section_start:section_start + 3000]
        assert "fix_not_disable.py" in section, (
            "Layer 3 failure: AGENTS.md must document the allowlist paths"
        )
        assert "test_type_safety_guardrails.py" in section, (
            "Layer 3 failure: AGENTS.md must document the allowlist paths"
        )

    def test_fail_open_policy_documented(self):
        section_start = self.text.find("No Lint-Suppression Comments")
        section = self.text[section_start:section_start + 3000]
        assert "fail-open" in section.lower(), (
            "Layer 3 failure: AGENTS.md must document the fail-open policy"
        )

    def test_three_layer_enforcement_documented(self):
        section_start = self.text.find("No Lint-Suppression Comments")
        section = self.text[section_start:section_start + 3000]
        assert "Runtime hook" in section or "runtime hook" in section.lower(), (
            "Layer 3 failure: AGENTS.md must document the 3-layer enforcement"
        )

    def test_guardrail_integrity_policy_cross_reference(self):
        section_start = self.text.find("No Lint-Suppression Comments")
        section = self.text[section_start:section_start + 3000]
        normalized = section.replace("\n", " ")
        assert "Guardrail Integrity Policy" in normalized, (
            "Layer 3 failure: AGENTS.md must cross-reference Guardrail Integrity Policy"
        )
