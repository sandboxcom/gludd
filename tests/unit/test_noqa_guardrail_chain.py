"""Integration test: prove the full noqa-guardrail 3-layer chain is intact.

Layer 1: AGENTS.md policy — forbids 5 suppression patterns.
Layer 2: enforce-no-suppressions.ts plugin — blocks edit/write with those patterns.
Layer 3: test_no_suppression_comments_plugin.py — behavioral pin + gate scan.

This file is the meta-test demanded by E4 — every test here asserts
one link in the chain so a broken link is a gate failure.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
AGENTS_MD = ROOT / "AGENTS.md"
PLUGIN = ROOT / ".opencode" / "plugin" / "enforce-no-suppressions.ts"
BEHAVIOR_TEST = ROOT / "tests" / "unit" / "test_no_suppression_comments_plugin.py"
GATE_TEST = ROOT / "tests" / "unit" / "test_type_safety_guardrails.py"
OPENCODE_JSON = ROOT / "opencode.json"

# ── Layer 1: AGENTS.md policy ──────────────────────────────────────────────

FIVE_PATTERNS = [
    "noqa",
    "type: ignore",
    "pylint:",
    "fmt:",
    "isort:",
]

THREE_LAYER_PHRASES = [
    "Runtime hook",
    "Behavior pin",
    "Repo-wide scan",
]

ALLOWLISTED_FILES = [
    "src/general_ludd/security/fix_not_disable.py",
    "tests/unit/test_type_safety_guardrails.py",
]


def test_agents_md_has_noqa_section() -> None:
    agents_text = AGENTS_MD.read_text()
    assert "No Lint-Suppression Comments" in agents_text, (
        "AGENTS.md must contain the 'No Lint-Suppression Comments' section"
    )


def test_agents_md_lists_all_five_patterns() -> None:
    agents_text = AGENTS_MD.read_text()
    for pattern in FIVE_PATTERNS:
        assert pattern in agents_text, (
            f"AGENTS.md must forbid '{pattern}'"
        )


def test_agents_md_describes_three_layer_enforcement() -> None:
    agents_text = AGENTS_MD.read_text()
    for phrase in THREE_LAYER_PHRASES:
        assert phrase in agents_text, (
            f"AGENTS.md must reference '{phrase}' enforcement layer"
        )


def test_agents_md_lists_allowlisted_files() -> None:
    agents_text = AGENTS_MD.read_text()
    for path in ALLOWLISTED_FILES:
        assert path in agents_text, (
            f"AGENTS.md must list allowlisted file: {path}"
        )


# ── Layer 2: Plugin enforcement ────────────────────────────────────────────


def test_plugin_exists() -> None:
    assert PLUGIN.is_file(), "enforce-no-suppressions.ts must exist"


def test_plugin_is_registered_in_opencode_json() -> None:
    opencode_text = OPENCODE_JSON.read_text()
    assert "enforce-no-suppressions.ts" in opencode_text, (
        "enforce-no-suppressions.ts must be registered in opencode.json"
    )


def test_plugin_has_suppression_patterns() -> None:
    plugin_text = PLUGIN.read_text()
    assert "SUPPRESSION_PATTERNS" in plugin_text, (
        "Plugin must export SUPPRESSION_PATTERNS"
    )


def test_plugin_has_allowlist_paths() -> None:
    plugin_text = PLUGIN.read_text()
    assert "ALLOWLIST_PATHS" in plugin_text, (
        "Plugin must export ALLOWLIST_PATHS"
    )


def _extract_array_block(text: str, var_name: str) -> str:
    idx = text.index(var_name)
    tail = text[idx:]
    eq = tail.index("=")
    after_eq = tail[eq + 1:]
    open_b = after_eq.index("[")
    close = after_eq.index("]")
    return after_eq[open_b:close]


def test_plugin_covers_all_five_patterns() -> None:
    plugin_text = PLUGIN.read_text()
    patterns_block = _extract_array_block(plugin_text, "SUPPRESSION_PATTERNS")

    for pattern_kw in FIVE_PATTERNS:
        regex_variant = pattern_kw.replace(": ", ":\\s*")
        assert pattern_kw in patterns_block or regex_variant in patterns_block, (
            f"Plugin SUPPRESSION_PATTERNS must match '{pattern_kw}'"
        )


def test_plugin_allowlist_matches_policy() -> None:
    plugin_text = PLUGIN.read_text()
    allowlist_block = _extract_array_block(plugin_text, "ALLOWLIST_PATHS")

    for path in ALLOWLISTED_FILES:
        assert path in allowlist_block, (
            f"Plugin ALLOWLIST_PATHS must include: {path}"
        )


def test_plugin_has_fail_open_guard() -> None:
    plugin_text = PLUGIN.read_text()
    assert "catch" in plugin_text and "return" in plugin_text, (
        "Plugin must be fail-open (try/catch with return)"
    )


def test_plugin_uses_permission_decision_deny() -> None:
    plugin_text = PLUGIN.read_text()
    assert 'permissionDecision' in plugin_text, (
        "Plugin must return structured deny with permissionDecision"
    )
    assert '"deny"' in plugin_text, (
        "Plugin must set permissionDecision to 'deny'"
    )


# ── Layer 3: Behavioral pin test ───────────────────────────────────────────


def test_behavioral_test_exists() -> None:
    assert BEHAVIOR_TEST.is_file(), (
        "Behavioral test for suppression plugin must exist"
    )


def test_behavioral_test_covers_all_five_patterns() -> None:
    test_text = BEHAVIOR_TEST.read_text()
    for pattern_kw in FIVE_PATTERNS:
        assert pattern_kw in test_text, (
            f"Behavioral test must cover '{pattern_kw}'"
        )


def test_behavioral_test_has_deny_tests() -> None:
    test_text = BEHAVIOR_TEST.read_text()
    assert "test_deny_on" in test_text, (
        "Behavioral test must have deny-on tests"
    )


def test_behavioral_test_has_allow_tests() -> None:
    test_text = BEHAVIOR_TEST.read_text()
    assert "test_allow" in test_text, (
        "Behavioral test must have allow tests for non-suppression comments"
    )


def test_behavioral_test_has_fail_open_test() -> None:
    test_text = BEHAVIOR_TEST.read_text()
    assert "fail_open" in test_text.lower(), (
        "Behavioral test must verify fail-open contract"
    )


def test_behavioral_test_has_structural_pin() -> None:
    test_text = BEHAVIOR_TEST.read_text()
    assert "TestPluginStructure" in test_text, (
        "Behavioral test must have structural pin tests (TestPluginStructure)"
    )


# ── Layer 3: Gate-level scan test ──────────────────────────────────────────


def test_gate_scan_test_exists() -> None:
    assert GATE_TEST.is_file(), (
        "Gate-level scan test for # noqa must exist"
    )


def test_gate_scan_checks_src_for_noqa() -> None:
    gate_text = GATE_TEST.read_text()
    assert "test_no_noqa_comments" in gate_text, (
        "Gate test must have test_no_noqa_comments"
    )


def test_gate_scan_uses_hard_assert_not_warning() -> None:
    gate_text = GATE_TEST.read_text()
    assert "assert not violations" in gate_text, (
        "Gate scan must use hard assert, not warnings.warn"
    )
    idx = gate_text.index("test_no_noqa_comments")
    func_body = gate_text[idx:]
    next_def = func_body.find("\ndef ", func_body.index("\n") + 1)
    body_only = func_body[:next_def] if next_def != -1 else func_body
    assert "warnings.warn" not in body_only, (
        "Gate scan test function must NOT use advisory warnings.warn — "
        "it must be a hard gate. The docstring atop the file may mention it "
        "descriptively, but the test body itself must assert."
    )


def test_gate_scan_allowlists_fix_not_disable() -> None:
    gate_text = GATE_TEST.read_text()
    assert "fix_not_disable" in gate_text, (
        "Gate scan must allowlist fix_not_disable.py"
    )


# ── End-to-end: source tree is clean ───────────────────────────────────────


def test_src_tree_has_no_live_suppression_comments() -> None:
    noqa = re.compile(r"#\s*noqa")
    type_ignore = re.compile(r"#\s*type:\s*ignore")
    pylint_disable = re.compile(r"#\s*pylint:")
    fmt_suppress = re.compile(r"#\s*fmt:\s*(?:off|skip|on)")
    isort_skip = re.compile(r"#\s*isort:\s*skip")

    violations: list[str] = []
    for py_file in (ROOT / "src").rglob("*.py"):
        rel = str(py_file.relative_to(ROOT))
        if rel == "src/general_ludd/security/fix_not_disable.py":
            continue
        for i, line in enumerate(py_file.read_text().splitlines(), 1):
            if noqa.search(line):
                violations.append(f"{rel}:{i} [noqa]: {line.strip()}")
            if type_ignore.search(line):
                violations.append(f"{rel}:{i} [type:ignore]: {line.strip()}")
            if pylint_disable.search(line):
                violations.append(f"{rel}:{i} [pylint:]: {line.strip()}")
            if fmt_suppress.search(line):
                violations.append(f"{rel}:{i} [fmt:]: {line.strip()}")
            if isort_skip.search(line):
                violations.append(f"{rel}:{i} [isort:]: {line.strip()}")

    assert not violations, (
        f"Found {len(violations)} suppression comments in src/:\n"
        + "\n".join(violations)
    )
