"""TDD tests for the no-lint-suppression-comments guardrail.

This guardrail prevents re-introduction of lint-suppression comments
(# noqa, # type: ignore, # pylint:, # fmt:skip, # isort:skip) into src/ and
tests/. The user reported that prior codification was advisory-only and allowed
regression; this test pins the BEHAVIOR (deny on match, allow on allowlist,
fail-open) by extracting the exported patterns from the TypeScript plugin
source and applying them to representative inputs.

The plugin cannot be imported into Python directly, so we read its source as
text, extract the exported SUPPRESSION_PATTERNS regex array and ALLOWLIST_PATHS,
translate the JS regex literals to Python re patterns, and exercise the
matcher against the spec's test cases. Structural assertions cover hook
registration, fail-open behavior, opencode.json registration, and the exact
deny message text required by AGENTS.md.
"""

import json
import re
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
PLUGIN_PATH = ROOT / ".opencode" / "plugin" / "enforce-no-suppressions.ts"
OPENCODE_JSON = ROOT / "opencode.json"

# Spec-defined forbidden patterns (the source of truth — the plugin MUST
# implement exactly these). Used to validate the extracted JS regexes.
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
    """Pull the SUPPRESSION_PATTERNS array literal bodies out of the plugin.

    Accepts either `export const SUPPRESSION_PATTERNS = [ ... ]` or a plain
    `const SUPPRESSION_PATTERNS = [ ... ]`. Returns the body of each regex
    literal (without the surrounding slashes or flags).
    """
    m = re.search(
        r"(?:export\s+)?const\s+SUPPRESSION_PATTERNS[^=]*=\s*\[(.*?)\]",
        src,
        re.DOTALL,
    )
    assert m, (
        "SUPPRESSION_PATTERNS named export must be present in "
        "enforce-no-suppressions.ts (per task spec — expose the matcher as a "
        "named export so this test can pin its behavior)."
    )
    body = m.group(1)
    # Match /pattern/flags — capture only the pattern body.
    return re.findall(r"/([^/]+)/[a-z]*", body)


def _extract_allowlist(src: str) -> list[str]:
    """Pull the ALLOWLIST_PATHS array literal entries out of the plugin."""
    m = re.search(
        r"(?:export\s+)?const\s+ALLOWLIST_PATHS[^=]*=\s*\[(.*?)\]",
        src,
        re.DOTALL,
    )
    assert m, "ALLOWLIST_PATHS named export must be present"
    return re.findall(r'"([^"]+)"', m.group(1))


def _is_suppression(text: str, patterns: list[str]) -> bool:
    """Re-implementation of the plugin matcher using extracted patterns."""
    return any(re.search(p, text) for p in patterns)


def _is_allowlisted(file_path: str, allowlist: list[str]) -> bool:
    return any(allow in file_path for allow in allowlist)


# --------------------------------------------------------------------------- #
# Structural: plugin file, registration, hook, named exports.
# --------------------------------------------------------------------------- #
class TestPluginStructure:
    def test_plugin_file_exists(self):
        assert PLUGIN_PATH.exists(), (
            "enforce-no-suppressions.ts must exist at .opencode/plugin/"
        )

    def test_plugin_registered_in_opencode_json(self):
        cfg = json.loads(OPENCODE_JSON.read_text())
        plugins = cfg.get("plugin", [])
        assert any("enforce-no-suppressions" in p for p in plugins), (
            "enforce-no-suppressions.ts must be registered in opencode.json "
            "plugin[] array (mirror enforce-make.ts registration)."
        )

    def test_tool_execute_before_hook_registered(self):
        src = PLUGIN_PATH.read_text()
        assert '"tool.execute.before"' in src, (
            "plugin must register a tool.execute.before hook"
        )

    def test_hook_matches_edit_and_write_tools(self):
        src = PLUGIN_PATH.read_text()
        # The hook body must check both `edit` and `write` tool names.
        assert "edit" in src and "write" in src, (
            "hook must match both edit and write tools"
        )

    def test_exports_named_matcher(self):
        src = PLUGIN_PATH.read_text()
        # The task spec requires the matcher be exposed as a named export.
        assert re.search(
            r"export\s+(async\s+)?function\s+isSuppression|"
            r"export\s+const\s+isSuppression",
            src,
        ), "plugin must export a named matcher function (isSuppression*)"

    def test_deny_message_text_present(self):
        src = PLUGIN_PATH.read_text()
        # The exact deny message text required by the task spec.
        assert "permissionDecision" in src, (
            "deny must use the permissionDecision field (clean deny, exit 0)"
        )
        assert "deny" in src, "deny decision must be present"
        assert "Lint-suppression" in src or "lint-suppression" in src.lower(), (
            "deny message must reference lint-suppression so the agent knows "
            "what it hit and where to look (AGENTS.md Guardrail Integrity Policy)"
        )

    def test_fail_open_present(self):
        src = PLUGIN_PATH.read_text()
        # Fail-open: a try/catch that returns allow on any throw. The task
        # spec mandates this so the editor is never wedged by a hook error.
        assert "catch" in src, (
            "plugin must wrap the matcher in try/catch for fail-open behavior"
        )

    def test_inspects_content_and_newstring_params(self):
        src = PLUGIN_PATH.read_text()
        # The hook must inspect the write tool's content arg AND the edit
        # tool's newString arg (different parameter names per tool).
        assert "content" in src.lower(), "must inspect write content arg"
        assert "newString" in src or "newstring" in src.lower(), (
            "must inspect edit newString arg"
        )


# --------------------------------------------------------------------------- #
# Behavioral: extracted patterns match the spec and produce correct verdicts.
# --------------------------------------------------------------------------- #
class TestExtractedPatterns:
    """Validate that the patterns exported by the plugin equal the spec."""

    def test_pattern_count_matches_spec(self):
        src = PLUGIN_PATH.read_text()
        patterns = _extract_regex_array(src)
        assert len(patterns) == len(SPEC_PATTERNS), (
            f"plugin exports {len(patterns)} patterns; spec requires "
            f"{len(SPEC_PATTERNS)} (noqa, type:ignore, pylint:, fmt:, isort:skip)"
        )

    def test_each_spec_pattern_is_present(self):
        src = PLUGIN_PATH.read_text()
        patterns = _extract_regex_array(src)
        for spec in SPEC_PATTERNS:
            # The plugin's pattern body must equal the spec regex (modulo
            # non-capturing group syntax differences).
            normalized = [p.replace("(?:", "(").replace("\\s", "\\s") for p in patterns]
            spec_norm = spec.replace("(?:", "(")
            assert spec_norm in normalized or any(
                spec.replace("(?:", "(") == p.replace("(?:", "(") for p in patterns
            ), (
                f"spec pattern {spec!r} not found in exported patterns {patterns!r}"
            )


class TestAllowlistExport:
    def test_allowlist_contains_required_paths(self):
        src = PLUGIN_PATH.read_text()
        allowlist = _extract_allowlist(src)
        for required in SPEC_ALLOWLIST:
            assert required in allowlist, (
                f"allowlist must contain {required!r} (contains string-literal "
                f"suppression patterns as DATA, not as suppression comments)"
            )


# --------------------------------------------------------------------------- #
# Behavioral verdicts — the 6+ test cases required by the task spec.
# Each case asserts the matcher's verdict on a representative input.
# --------------------------------------------------------------------------- #
class TestMatcherVerdicts:
    """Apply the plugin's exported patterns to spec test cases.

    These cases are the contract: deny on any forbidden pattern, allow on
    plain comments, allow when the file path is in the allowlist.
    """

    @classmethod
    def setup_class(cls):
        src = PLUGIN_PATH.read_text()
        cls.patterns = _extract_regex_array(src)
        cls.allowlist = _extract_allowlist(src)

    def _check(self, file_path: str, content: str) -> str:
        """Return 'deny' or 'allow' per the plugin's logic."""
        if _is_allowlisted(file_path, self.allowlist):
            return "allow"
        if _is_suppression(content, self.patterns):
            return "deny"
        return "allow"

    def test_deny_on_noqa(self):
        assert self._check("src/foo.py", "x = 1  # noqa") == "deny"

    def test_deny_on_noqa_with_code(self):
        assert self._check("src/foo.py", "x = 1  # noqa: E501") == "deny"

    def test_deny_on_type_ignore(self):
        assert self._check("src/foo.py", "x: int = f()  # type: ignore") == "deny"

    def test_deny_on_pylint_disable(self):
        assert self._check(
            "src/foo.py", "obj.attr  # pylint: disable=E1101"
        ) == "deny"

    def test_deny_on_fmt_skip(self):
        assert self._check("src/foo.py", "# fmt: skip") == "deny"

    def test_deny_on_fmt_off(self):
        assert self._check("src/foo.py", "# fmt: off") == "deny"

    def test_deny_on_isort_skip(self):
        assert self._check("src/foo.py", "import sys  # isort:skip") == "deny"

    def test_allow_on_plain_comment(self):
        assert self._check("src/foo.py", "# this is a regular comment") == "allow"

    def test_allow_on_code_without_comment(self):
        assert self._check("src/foo.py", "x = 1 + 2\n") == "allow"

    def test_allow_when_allowlisted_fix_not_disable(self):
        # fix_not_disable.py contains "# noqa" as a string literal inside a
        # frozenset (DATA, not a suppression comment). Must be allowlisted.
        assert self._check(
            "src/general_ludd/security/fix_not_disable.py",
            'DISABLE_PATTERNS = frozenset({"# noqa"})',
        ) == "allow"

    def test_allow_when_allowlisted_test_fixture(self):
        # The guardrail test file contains the patterns as test fixtures.
        assert self._check(
            "tests/unit/test_type_safety_guardrails.py",
            'noqa_pattern = re.compile(r"#\\s*noqa")',
        ) == "allow"

    def test_deny_in_tests_dir_on_noqa(self):
        # tests/ is in scope per AGENTS.md — only the two allowlisted files
        # escape, not the entire tests/ tree.
        assert self._check(
            "tests/unit/test_other.py", "x = 1  # noqa"
        ) == "deny"

    def test_deny_in_src_on_noqa(self):
        # src/ is in scope.
        assert self._check(
            "src/general_ludd/daemon.py", "x = 1  # noqa"
        ) == "deny"


# --------------------------------------------------------------------------- #
# Fail-open contract — any throw inside the hook returns allow, never wedges.
# --------------------------------------------------------------------------- #
class TestFailOpenContract:
    def test_fail_open_returns_allow_not_throw(self):
        src = PLUGIN_PATH.read_text()
        # The catch block must NOT re-throw and must surface an allow-shaped
        # decision (the task spec: "any throw/exception → return allow").
        # Asserting that catch is followed by an allow/permissionDecision allow
        # shape within a reasonable window.
        catch_idx = src.find("catch")
        assert catch_idx != -1, "must have a catch block for fail-open"
        window = src[catch_idx:catch_idx + 400]
        assert "allow" in window.lower(), (
            "catch block must return allow (fail-open), not re-throw. "
            f"Window after catch: {window!r}"
        )
