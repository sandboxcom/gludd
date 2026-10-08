"""BP.7: LINT_TARGETS exempt from streak counter in enforce-delegate.ts.

Lint/typecheck/collect-check are quality-gate operations, not grinding.
They must reset the streak counter (like git shipping) and never be blocked.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PLUGIN_PATH = ROOT / ".opencode" / "plugin" / "enforce-delegate.ts"


def _src() -> str:
    return PLUGIN_PATH.read_text()


def _function_region(name: str, next_name: str) -> str:
    src = _src()
    start = src.index(f"function {name}")
    end = src.index(f"function {next_name}", start)
    return src[start:end]


REQUIRED_LINT_TARGETS = [
    "lint",
    "lint-fix",
    "typecheck",
    "collect-check",
    "test-count",
    "healthcheck",
    "smoke",
    "check-coverage-gaps",
]


class TestLintTargetSet:
    def test_file_exists(self):
        assert PLUGIN_PATH.is_file()

    def test_lint_targets_set_exists(self):
        src = _src()
        assert "LINT_TARGETS" in src
        assert "ReadonlySet<string>" in src

    def test_all_required_targets_in_set(self):
        src = _src()
        idx = src.find("LINT_TARGETS")
        assert idx > 0
        after = src[idx : idx + 500]
        for target in REQUIRED_LINT_TARGETS:
            assert f'"{target}"' in after, f"{target} missing from LINT_TARGETS"

    def test_lint_targets_count_is_8(self):
        src = _src()
        idx = src.find("LINT_TARGETS")
        after = src[idx : idx + 500]
        count = after.count('",\n') + 1
        assert count >= 8, f"expected >=8 lint targets, found {count}"


class TestIsLintTargetFunction:
    def test_is_lint_target_function_exists(self):
        src = _src()
        assert "function isLintTarget" in src

    def test_is_lint_target_uses_same_pattern_as_git_shipping(self):
        src = _src()
        idx = src.find("function isLintTarget")
        after = src[idx : idx + 200]
        assert "match(/(?:^|\\s)make\\s+(\\S+)/)" in after
        assert "LINT_TARGETS.has(m[1])" in after

    def test_is_lint_target_returns_false_on_no_match(self):
        src = _src()
        idx = src.find("function isLintTarget")
        after = src[idx : idx + 200]
        assert "return false" in after


class TestMainthreadBudgetBeforeExemption:
    def test_lint_target_exempt_in_before_hook(self):
        after = _function_region("mainthreadBudgetBefore", "mainthreadBudgetAfter")
        assert "isLintTarget(command)" in after

    def test_lint_exempt_returns_null_like_git_shipping(self):
        after = _function_region("mainthreadBudgetBefore", "mainthreadBudgetAfter")
        git_line = after.find("isGitShippingTarget(command)")
        lint_line = after.find("isLintTarget(command)) return null")
        assert git_line > 0
        assert lint_line > 0
        assert lint_line > git_line


class TestMainthreadBudgetAfterReset:
    def test_lint_target_resets_streak_in_after_hook(self):
        after = _function_region("mainthreadBudgetAfter", "_writeHeartbeat")
        lint_reset = after.find("isLintTarget(command)")
        assert lint_reset > 0

    def test_lint_target_reset_writes_zero_streak(self):
        after = _function_region("mainthreadBudgetAfter", "_writeHeartbeat")
        lint_idx = after.find("isLintTarget(command)")
        assert lint_idx > 0
        after_lint = after[lint_idx : lint_idx + 300]
        assert "writeStreak({ count: 0 })" in after_lint
        assert "saveReadGrindState(0, Date.now())" in after_lint


class TestLintTargetsDisjointFromGitShipping:
    def test_reasonably_disjoint_from_git_shipping(self):
        src = _src()
        git_idx = src.find("GIT_SHIPPING_TARGETS")
        lint_idx = src.find("LINT_TARGETS")
        assert git_idx > 0
        assert lint_idx > 0
        git_after = src[git_idx : git_idx + 500]
        for target in REQUIRED_LINT_TARGETS:
            assert f'"{target}"' not in git_after, f"{target} should not be in GIT_SHIPPING_TARGETS"


class TestLintTargetsBehavioral:
    """Python mirror of the isLintTarget function for behavioral testing."""

    LINT: frozenset[str] = frozenset(
        {
            "lint",
            "lint-fix",
            "typecheck",
            "collect-check",
            "test-count",
            "healthcheck",
            "smoke",
            "check-coverage-gaps",
        }
    )

    def _is_lint(self, command: str) -> bool:
        import re

        m = re.match(r"(?:^|\s)make\s+(\S+)", command)
        if not m:
            return False
        return m.group(1) in self.LINT

    def test_make_lint_matches(self):
        assert self._is_lint("make lint")

    def test_make_lint_fix_matches(self):
        assert self._is_lint("make lint-fix")

    def test_make_typecheck_matches(self):
        assert self._is_lint("make typecheck")

    def test_make_collect_check_matches(self):
        assert self._is_lint("make collect-check")

    def test_make_test_count_matches(self):
        assert self._is_lint("make test-count")

    def test_make_healthcheck_matches(self):
        assert self._is_lint("make healthcheck")

    def test_make_smoke_matches(self):
        assert self._is_lint("make smoke")

    def test_make_check_coverage_gaps_matches(self):
        assert self._is_lint("make check-coverage-gaps")

    def test_make_git_add_does_not_match(self):
        assert not self._is_lint("make git-add")

    def test_make_edit_does_not_match(self):
        assert not self._is_lint("edit something")

    def test_empty_string_returns_false(self):
        assert not self._is_lint("")

    def test_non_make_command_returns_false(self):
        assert not self._is_lint("python script.py")
