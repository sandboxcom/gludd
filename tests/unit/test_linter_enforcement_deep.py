"""Deep behavioral tests for linter and code quality enforcement plugins.

Covers three plugins across rule matching, boundary cases, nested patterns,
false positive avoidance, and env-var disable paths:

1. enforce-no-suppressions.ts — lint-suppression comment detection
2. enforce-tdd.ts — test-first guardrail (allowlist, candidate paths, disable)
3. enforce-make.ts — bash metacharacter detection, stop-pattern detection,
   completion-sounding phrases, command validation

Extracts exported patterns/constants from plugin source via regex mirroring,
exercises the matchers against spec-defined test vectors, and asserts on
the verdict contract (deny/allow) for each case.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
PLUGIN_DIR = ROOT / ".opencode" / "plugin"
IMPL_DIR = PLUGIN_DIR / "impl"

NO_SUPPRESSIONS_TS = PLUGIN_DIR / "enforce-no-suppressions.ts"
TDD_TS = PLUGIN_DIR / "enforce-tdd.ts"
MAKE_IMPL_TS = IMPL_DIR / "enforce_make_impl.ts"


# ── helpers ────────────────────────────────────────────────────────────────


def _extract_js_regex_array(src: str, var_name: str) -> list[str]:
    m = re.search(
        rf"(?:export\s+)?const\s+{var_name}[^=]*=\s*\[(.*?)\]",
        src,
        re.DOTALL,
    )
    if not m:
        return []
    return re.findall(r"/((?:\\.|[^/])*)/[a-z]*", m.group(1))


def _extract_js_string_array(src: str, var_name: str) -> list[str]:
    m = re.search(
        rf"(?:export\s+)?const\s+{var_name}[^=]*=\s*\[(.*?)\]",
        src,
        re.DOTALL,
    )
    if not m:
        return []
    return re.findall(r'"([^"]+)"', m.group(1))


def _eval_js_regex(js_pattern: str, text: str) -> bool:
    try:
        return re.search(js_pattern, text) is not None
    except re.error:
        return False


def _comment_fragments(text: str) -> list[str]:
    """Return hash comments while excluding quoted and triple-quoted data."""
    comments: list[str] = []
    quote: str | None = None
    escaped = False
    index = 0
    while index < len(text):
        if quote is not None:
            if len(quote) == 3:
                if text.startswith(quote, index):
                    quote = None
                    index += 3
                    continue
            else:
                character = text[index]
                if escaped:
                    escaped = False
                elif character == "\\":
                    escaped = True
                elif character == quote:
                    quote = None
            index += 1
            continue

        if text.startswith(('"""', "'''"), index):
            quote = text[index : index + 3]
            index += 3
            continue
        character = text[index]
        if character in {"'", '"'}:
            quote = character
            escaped = False
            index += 1
            continue
        if character == "#":
            newline = text.find("\n", index)
            end = len(text) if newline == -1 else newline
            comments.append(text[index:end])
            index = end
            continue
        index += 1
    return comments


# ════════════════════════════════════════════════════════════════════════════
# 1. enforce-no-suppressions — rule matching, boundaries, false positives
# ════════════════════════════════════════════════════════════════════════════


class TestNoSuppressionsRuleMatching:
    """Verify the five suppression patterns match exactly the spec's cases,
    including boundary conditions and whitespace variants."""

    @classmethod
    def setup_class(cls):
        src = NO_SUPPRESSIONS_TS.read_text()
        cls.patterns = _extract_js_regex_array(src, "SUPPRESSION_PATTERNS")
        cls.allowlist = _extract_js_string_array(src, "ALLOWLIST_PATHS")

    def _is_suppression(self, text: str) -> bool:
        return any(
            re.search(pattern, comment)
            for comment in _comment_fragments(text)
            for pattern in self.patterns
        )

    def _is_allowlisted(self, path: str) -> bool:
        return any(allow in path for allow in self.allowlist)

    # ── noqa variants ──

    def test_noqa_basic(self):
        assert self._is_suppression("x = 1  # noqa"), "basic # noqa must match"

    def test_noqa_with_code(self):
        assert self._is_suppression("x = 1  # noqa: E501"), "# noqa: E501 must match"

    def test_noqa_whitespace_variants(self):
        for variant in [
            "#  noqa",
            "#   noqa",
            "#\tnoqa",
            "# noqa",
            "#noqa",
        ]:
            assert self._is_suppression(variant), f"whitespace variant {variant!r} must match"

    def test_noqa_at_line_end_only(self):
        assert self._is_suppression("def long_function_name(arg1: int, arg2: str, arg3: float) -> None:  # noqa"), (
            "# noqa at end of long line must match"
        )

    # ── type: ignore variants ──

    def test_type_ignore_basic(self):
        assert self._is_suppression("x: int = f()  # type: ignore"), "basic # type: ignore"

    def test_type_ignore_with_bracket_code(self):
        assert self._is_suppression("x = g()  # type: ignore[return-value]"), "# type: ignore[code] must match"

    def test_type_ignore_whitespace_around_colon(self):
        assert self._is_suppression("x = 1  # type:  ignore"), "extra whitespace after colon must match"

    # ── pylint variants ──

    def test_pylint_disable(self):
        assert self._is_suppression("obj.attr  # pylint: disable=E1101"), "# pylint: disable= must match"

    def test_pylint_enable(self):
        assert self._is_suppression("# pylint: enable=W0612"), "# pylint: enable= must match"

    # ── fmt variants ──

    def test_fmt_skip(self):
        assert self._is_suppression("# fmt: skip")

    def test_fmt_off(self):
        assert self._is_suppression("# fmt: off")

    def test_fmt_on(self):
        assert self._is_suppression("# fmt: on")

    # ── isort variants ──

    def test_isort_skip(self):
        assert self._is_suppression("import sys  # isort:skip")

    def test_isort_skip_with_space(self):
        assert self._is_suppression("from . import foo  # isort: skip"), (
            "# isort: skip (with space before skip) must match"
        )

    # ── false positives ──

    def test_false_positive_no_quality_assurance(self):
        assert not self._is_suppression("# no quality assurance"), (
            "'no quality assurance' must NOT match # noqa pattern"
        )

    def test_false_positive_plain_comment(self):
        assert not self._is_suppression("# this is a regular comment about types"), (
            "plain comment must not match any suppression pattern"
        )

    def test_false_positive_code_without_comment(self):
        assert not self._is_suppression("x = 1 + 2\ny = 3 + 4\n"), "code without comments must not match"

    def test_false_positive_docstring(self):
        assert not self._is_suppression(
            '"""This function does something.\n\nIt has a docstring with # noqa in it but it is not a comment."""'
        ), "docstring containing # noqa must not match (no leading # on line)"

    def test_false_positive_hash_in_data(self):
        assert not self._is_suppression('HASH_PREFIX = "#noqa"'), (
            "string literal '#noqa' without leading # must not match"
        )

    # ── boundary: inline suppression in complex context ──

    def test_inline_inside_multiline_string(self):
        assert not self._is_suppression('"""\n# noqa is in a multiline string\n"""'), (
            "multiline string content must not match (no leading # at column 0)"
        )

    # ── allowlist paths ──

    def test_allowlist_fix_not_disable(self):
        assert self._is_allowlisted("src/general_ludd/security/fix_not_disable.py"), (
            "fix_not_disable.py must be in allowlist"
        )

    def test_allowlist_test_type_safety(self):
        assert self._is_allowlisted("tests/unit/test_type_safety_guardrails.py"), (
            "test_type_safety_guardrails.py must be in allowlist"
        )

    def test_non_allowlisted_src_denied(self):
        assert not self._is_allowlisted("src/general_ludd/daemon.py"), "daemon.py must NOT be in allowlist"


# ════════════════════════════════════════════════════════════════════════════
# 2. enforce-no-suppressions — hard-on policy and subagent isolation
# ════════════════════════════════════════════════════════════════════════════


class TestNoSuppressionsHardOn:
    """Verify environment variables cannot disable this hard guardrail."""

    def test_disable_env_var_absent(self):
        src = NO_SUPPRESSIONS_TS.read_text()
        assert "GLUDD_NO_SUPPRESSIONS_ENFORCE" not in src

    def test_matcher_remains_in_default_hook(self):
        src = NO_SUPPRESSIONS_TS.read_text()
        assert "shouldAllowEdit(filePath, text)" in src
        assert 'permissionDecision: "deny"' in src

    def test_subagent_guard_present(self):
        src = NO_SUPPRESSIONS_TS.read_text()
        assert "isSubagent" in src, "plugin must guard against subagent enforcement via isSubagent()"


# ════════════════════════════════════════════════════════════════════════════
# 3. enforce-tdd — allowlist patterns and boundary cases
# ════════════════════════════════════════════════════════════════════════════


class TestTDDAllowlistPatterns:
    """Verify the TDD plugin's allowlist correctly covers all exempt file types."""

    @classmethod
    def setup_class(cls):
        src = TDD_TS.read_text()
        cls.raw = src
        cls.patterns = _extract_js_regex_array(src, "ALLOWLIST_PATTERNS")

    def _matches_allowlist(self, path: str) -> bool:
        normalized = path.replace("\\", "/")
        return any(re.search(p, normalized) for p in self.patterns)

    def test_pycache_dir_is_allowlisted(self):
        assert self._matches_allowlist("src/general_ludd/__pycache__/foo.cpython-311.pyc")

    def test_pyi_stub_is_allowlisted(self):
        assert self._matches_allowlist("src/general_ludd/foo.pyi")

    def test_typing_py_is_allowlisted(self):
        assert self._matches_allowlist("src/general_ludd/typing.py")

    def test_type_defs_py_is_allowlisted(self):
        assert self._matches_allowlist("src/general_ludd/type_defs.py")

    def test_protocols_py_is_allowlisted(self):
        assert self._matches_allowlist("src/general_ludd/protocols.py")

    def test_types_module_is_allowlisted(self):
        assert self._matches_allowlist("src/general_ludd/_types.py")

    def test_init_py_is_allowlisted(self):
        init_allowed = "__init__.py" in self.raw and "isInitInEmptyDir" in self.raw
        assert init_allowed, "__init__.py must be handled via isInitInEmptyDir() or allowlist"

    def test_regular_source_is_NOT_allowlisted(self):
        assert not self._matches_allowlist("src/general_ludd/daemon.py"), "regular source file must NOT be allowlisted"


class TestTDDCandidatePathComputation:
    """Verify candidate test paths mirror check_tdd_compliance.py behavior."""

    def test_single_level_module_path(self):
        src = TDD_TS.read_text()
        assert "candidateTestPaths" in src or "candidates" in src, "plugin must compute candidate test paths"

    def test_src_prefix_stripping(self):
        src = TDD_TS.read_text()
        assert "src/" in src, "must strip src/ prefix when computing test path"

    def test_tests_unit_prefix_applied(self):
        src = TDD_TS.read_text()
        assert "tests/unit" in src or "tests" in src, "candidate paths must be under tests/unit/"

    def test_test_prefix_applied(self):
        src = TDD_TS.read_text()
        assert "test_" in src, "candidate filename must start with test_"

    def test_leaf_name_candidate_present(self):
        src = TDD_TS.read_text()
        assert "parts[parts.length - 1]" in src or "leaf" in src.lower(), (
            "plugin must compute leaf-name candidate (e.g. test_bar.py for src/general_ludd/foo/bar.py)"
        )


class TestTDDEnvVarDisable:
    """Verify the GLUDD_TDD_ENFORCE env var disable path."""

    def test_disable_env_var_referenced(self):
        src = TDD_TS.read_text()
        assert "GLUDD_TDD_ENFORCE" in src, "plugin must check GLUDD_TDD_ENFORCE env var"

    def test_disable_returns_early(self):
        src = TDD_TS.read_text()
        disable_idx = src.find("GLUDD_TDD_ENFORCE")
        assert disable_idx != -1
        window = src[disable_idx : disable_idx + 200]
        assert "return" in window, "env var disable must return early (skip enforcement)"

    def test_subagent_guard_present(self):
        src = TDD_TS.read_text()
        assert "isSubagent" in src, "plugin must guard against subagent enforcement"


# ════════════════════════════════════════════════════════════════════════════
# 4. enforce-make — bash metacharacter detection, edge cases
# ════════════════════════════════════════════════════════════════════════════


class TestBashMetacharacterDetection:
    """Verify shell metacharacter patterns from enforce_make_impl.ts."""

    @classmethod
    def setup_class(cls):
        cls.src = MAKE_IMPL_TS.read_text()

    def _get_meta_re(self) -> re.Pattern:
        m = re.search(r"SHELL_META_CHARS\s*=\s*/([^/]+)/", self.src)
        assert m, "SHELL_META_CHARS regex not found in source"
        return re.compile(m.group(1))

    def test_pipe_detected(self):
        meta = self._get_meta_re()
        assert meta.search("make test | tee log"), "| must be detected"

    def test_semicolon_detected(self):
        meta = self._get_meta_re()
        assert meta.search("make test; make lint"), "; must be detected"

    def test_double_ampersand_detected(self):
        meta = self._get_meta_re()
        assert meta.search("make test && make lint"), "&& must be detected (via &)"

    def test_dollar_detected(self):
        meta = self._get_meta_re()
        assert meta.search("echo $(cat file)"), "$ must be detected"

    def test_backtick_detected(self):
        meta = self._get_meta_re()
        assert meta.search("echo `date`"), "` must be detected"

    def test_backslash_detected(self):
        meta = self._get_meta_re()
        assert meta.search("make\\ test"), "\\ must be detected"

    def test_exclamation_detected(self):
        meta = self._get_meta_re()
        assert meta.search("!make test"), "! must be detected"

    def test_brace_detected(self):
        meta = self._get_meta_re()
        assert meta.search("echo {a,b}"), "{} must be detected (via {)"

    def test_pipe_inside_quotes_exempt(self):
        meta = self._get_meta_re()
        raw = 'make git-commit MSG="fix pipe | in message"'
        unquoted = re.sub(r"'[^']*'", "", re.sub(r'"[^"]*"', "", raw))
        assert not meta.search(unquoted), "metacharacters inside double quotes must be exempt (commit messages)"

    def test_parens_not_blocked(self):
        meta = self._get_meta_re()
        raw = 'make git-commit MSG="fix foo (see #123)"'
        unquoted = re.sub(r"'[^']*'", "", re.sub(r'"[^"]*"', "", raw))
        assert not meta.search(unquoted), "bare parens () must NOT be blocked (commit messages)"


class TestCompletionStopPatternDetection:
    """Verify completion-sounding pattern detection from enforce_make_impl.ts."""

    @classmethod
    def setup_class(cls):
        cls.src = MAKE_IMPL_TS.read_text()
        m = re.search(r"const\s+COMPLETION_SOUNDING\s*=\s*\[(.*?)\]", cls.src, re.DOTALL)
        assert m, "COMPLETION_SOUNDING array not found in source"
        cls.phrases = re.findall(r'"([^"]+)"', m.group(1))

    def _detected(self, text: str) -> bool:
        lower = text.lower()
        if "✅" in text:
            return True
        if any(p in lower for p in self.phrases):
            return not ("want me to" in lower or "should i" in lower or "shall i" in lower)
        return False

    def test_all_passed_detected(self):
        assert self._detected("All tests passed, committed."), "'all passed' must trigger"

    def test_all_done_detected(self):
        assert self._detected("All done. Everything is complete."), "'all done' must trigger"

    def test_ready_for_review_detected(self):
        assert self._detected("Ready for review."), "'ready for review' must trigger"

    def test_summary_detected(self):
        assert self._detected("Here is a summary of the changes."), "'summary' must trigger"

    def test_committed_detected(self):
        assert self._detected("Changes committed successfully."), "'committed' must trigger"

    def test_done_detected(self):
        assert self._detected("Done."), "'done' must trigger"

    def test_checkmark_detected(self):
        assert self._detected("Task ✅ completed."), "✅ must trigger"

    def test_should_i_negation(self):
        assert not self._detected("Should I continue with the next task?"), (
            "'should i' question must NOT trigger stop-pattern detection (negation)"
        )

    def test_want_me_to_negation(self):
        assert not self._detected("Want me to proceed with the remaining items?"), (
            "'want me to' question must NOT trigger stop-pattern detection"
        )

    def test_shall_i_negation(self):
        assert not self._detected("Shall I start the next wave?"), (
            "'shall i' question must NOT trigger stop-pattern detection"
        )

    def test_normal_text_not_detected(self):
        assert not self._detected("Running tests for the new feature implementation."), (
            "normal work output must not trigger stop-pattern detection"
        )

    def test_question_in_completion_context(self):
        assert not self._detected("All tasks complete. Should I start the next phase?"), (
            "question negation must override completion phrases"
        )


class TestMakeEnvVarDisable:
    """Verify make enforcement env var disable paths."""

    def test_make_enforce_env_var(self):
        src = MAKE_IMPL_TS.read_text()
        assert "GLUDD_MAKE_ENFORCE" in src, "plugin must check GLUDD_MAKE_ENFORCE env var"

    def test_make_enforce_default_on(self):
        src = MAKE_IMPL_TS.read_text()
        assert 'GLUDD_MAKE_ENFORCE !== "0"' in src or "GLUDD_MAKE_ENFORCE !== '0'" in src, (
            "GLUDD_MAKE_ENFORCE must default to ON (enforce unless explicitly set to '0')"
        )


class TestMakeCommandValidation:
    """Verify command validation rules from enforce_make_impl.ts."""

    @classmethod
    def setup_class(cls):
        cls.src = MAKE_IMPL_TS.read_text()

    def _extract_forbidden_builtins(self) -> list[str]:
        m = re.search(
            r"const\s+invalidPatterns\s*=\s*\[(.*?)\]",
            self.src,
            re.DOTALL,
        )
        if not m:
            return []
        literals = re.findall(r"/((?:\\.|[^/])*)/[a-z]*", m.group(1))
        return [
            match.group(1)
            for literal in literals
            if (match := re.fullmatch(r"\\b(\w+)\\b", literal))
        ]

    def test_forbidden_patterns_exist(self):
        patterns = self._extract_forbidden_builtins()
        assert len(patterns) > 0, "must define forbidden shell builtin patterns"

    def test_grep_is_forbidden(self):
        patterns = self._extract_forbidden_builtins()
        assert "grep" in patterns, "grep must be in forbidden builtins"

    def test_cat_is_forbidden(self):
        patterns = self._extract_forbidden_builtins()
        assert "cat" in patterns, "cat must be in forbidden builtins"

    def test_find_is_forbidden(self):
        patterns = self._extract_forbidden_builtins()
        assert "find" in patterns, "find must be in forbidden builtins"

    def test_python_is_forbidden(self):
        patterns = self._extract_forbidden_builtins()
        assert "python" in patterns, "python must be in forbidden builtins"

    def test_unknown_target_blocked(self):
        assert "makeTargetExists" in self.src, "must check target existence via makeTargetExists()"

    def test_long_running_foreground_gate_blocked(self):
        assert "isGate" in self.src or "lrTarget === 'gate'" in self.src, "foreground `make gate` must be blocked"

    def test_long_running_foreground_test_unit_blocked(self):
        assert "isTestUnit" in self.src or "lrTarget === 'test-unit'" in self.src, (
            "foreground `make test-unit` must be blocked"
        )

    def test_gate_concurrency_guard_present(self):
        assert "isGateAlreadyRunning" in self.src, "must guard against concurrent gate/test runs"

    def test_guardrail_integrity_check_present(self):
        assert "throw new Error" in self.src, "must actively block violations via thrown errors"

    def test_bash_must_start_with_make(self):
        src = self.src
        assert re.search(r"""\.startsWith\((["'])make \1\)""", src), (
            "must require command to start with 'make '"
        )

    def test_dispatch_tool_reset(self):
        src = self.src
        assert "dispatchCount" in src, "must track dispatch count for reset logic"


# ════════════════════════════════════════════════════════════════════════════
# 5. cross-plugin invariants
# ════════════════════════════════════════════════════════════════════════════


class TestCrossPluginInvariants:
    """Verify invariants that span multiple enforcement plugins."""

    def test_all_three_have_fail_open(self):
        for path, name in [
            (NO_SUPPRESSIONS_TS, "enforce-no-suppressions"),
            (TDD_TS, "enforce-tdd"),
            (MAKE_IMPL_TS, "enforce-make"),
        ]:
            src = path.read_text()
            assert "catch" in src, f"{name} must have try/catch for fail-open behavior"

    def test_all_three_have_subagent_guard(self):
        for path, name in [
            (NO_SUPPRESSIONS_TS, "enforce-no-suppressions"),
            (TDD_TS, "enforce-tdd"),
            (MAKE_IMPL_TS, "enforce-make"),
        ]:
            src = path.read_text()
            assert "isSubagent" in src or "OPENCODE_SUBAGENT" in src, f"{name} must guard against subagent enforcement"

    def test_all_three_use_permission_decision_for_deny(self):
        for path, name in [
            (NO_SUPPRESSIONS_TS, "enforce-no-suppressions"),
            (TDD_TS, "enforce-tdd"),
        ]:
            src = path.read_text()
            assert "permissionDecision" in src, f"{name} must use permissionDecision (clean deny, not throw)"
