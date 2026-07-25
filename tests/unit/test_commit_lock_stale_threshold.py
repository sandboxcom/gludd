"""BP.19: commit-lock stale threshold reduced from 5min to 2min.

Rationale (BP.19): a crashed commit (OOM, SIGKILL, agent timeout) leaves a
stale lock file at ``/tmp/gludd-commit.lock``. The stale-detection path in
``enforce-commit-lock.ts`` breaks the lock once ``lockAge()`` exceeds
``STALE_THRESHOLD_MS``. The previous 5-minute threshold meant a crashed
commit blocked ALL subsequent commits for 5 minutes — a long window when
the orchestrator is trying to recover and ship follow-up work. Reducing it
to 2 minutes recovers faster while still preserving the race-prevention
guarantee: 2 minutes is well above the real wall-clock duration of any
``git commit`` operation (typically <5s), so a legitimately in-flight
commit is never falsely classified as stale.

This test pins:
  1. The ``STALE_THRESHOLD_MS`` constant exists.
  2. Its evaluated value is exactly ``120000`` (2 minutes), NOT ``300000`` (5 min).
  3. The stale-check branch actually references the constant.
  4. The rationale above is documented in the module docstring.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PLUGIN_PATH = ROOT / ".opencode/plugin" / "enforce-commit-lock.ts"

OLD_VALUE_MS = 300_000  # 5 minutes — the value BP.19 replaced
NEW_VALUE_MS = 120_000  # 2 minutes — the BP.19 target


def _plugin_src() -> str:
    assert PLUGIN_PATH.exists(), f"Plugin missing at {PLUGIN_PATH}"
    return PLUGIN_PATH.read_text()


def _eval_threshold_expr(src: str) -> int:
    """Extract the STALE_THRESHOLD_MS assignment and evaluate its arithmetic.

    Supports both expression form (``2 * 60 * 1000``) and literal form
    (``120000``). Arithmetic is evaluated via ``ast.literal_eval``-safe
    reduction of a BinOp tree — never ``eval()`` on raw source.
    """
    m = re.search(r"STALE_THRESHOLD_MS\s*=\s*([^;]+);", src)
    assert m is not None, "STALE_THRESHOLD_MS declaration not found"
    expr = m.group(1).strip()
    tree = ast.parse(expr, mode="eval")
    return _eval_node(tree.body)


def _eval_node(node: ast.AST) -> int:
    if isinstance(node, ast.Constant) and isinstance(node.value, int):
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mult):
        return _eval_node(node.left) * _eval_node(node.right)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _eval_node(node.left) + _eval_node(node.right)
    pytest.fail(f"Unsupported expression node in STALE_THRESHOLD_MS: {ast.dump(node)}")


class TestStaleThresholdConstant:
    def test_constant_exists(self) -> None:
        """Requirement 1: STALE_THRESHOLD_MS constant is declared."""
        src = _plugin_src()
        assert re.search(r"\bSTALE_THRESHOLD_MS\s*=", src), (
            "STALE_THRESHOLD_MS constant declaration not found in plugin source"
        )

    def test_value_is_120000_not_300000(self) -> None:
        """Requirement 2: value is 120000 ms (2 min), not 300000 ms (5 min)."""
        src = _plugin_src()
        value = _eval_threshold_expr(src)
        assert value == NEW_VALUE_MS, (
            f"STALE_THRESHOLD_MS evaluated to {value}; expected {NEW_VALUE_MS} (2 min). "
            f"BP.19 reduced this from {OLD_VALUE_MS} (5 min)."
        )
        assert value != OLD_VALUE_MS, (
            f"STALE_THRESHOLD_MS is still {OLD_VALUE_MS} (5 min); BP.19 not applied."
        )

    def test_value_is_2_minutes(self) -> None:
        """Sanity: 120000 ms == 2 minutes exactly."""
        src = _plugin_src()
        value = _eval_threshold_expr(src)
        assert value == 2 * 60 * 1000

    def test_not_five_minutes(self) -> None:
        """Negative pin: must not regress to the old 5-minute value."""
        src = _plugin_src()
        value = _eval_threshold_expr(src)
        assert value != 5 * 60 * 1000


class TestStaleCheckUsesConstant:
    def test_stale_check_references_constant(self) -> None:
        """Requirement 3: the stale-detection branch uses STALE_THRESHOLD_MS.

        The plugin must compare ``lockAge()`` against ``STALE_THRESHOLD_MS``
        (not a hardcoded literal) so future tuning happens in one place.
        """
        src = _plugin_src()
        pattern = r"age\s*>\s*STALE_THRESHOLD_MS"
        assert re.search(pattern, src), (
            "Stale check does not reference STALE_THRESHOLD_MS by name. "
            "Expected a comparison like `if (age > STALE_THRESHOLD_MS)`."
        )

    def test_stale_check_branch_releases_lock(self) -> None:
        """The stale branch must release + re-acquire, not just deny."""
        src = _plugin_src()
        assert "releaseLock" in src, "releaseLock() not found — stale lock never cleared"
        # Anchor on the USAGE site (the `age > STALE_THRESHOLD_MS` comparison),
        # not the declaration — releaseLock() follows the comparison branch.
        m = re.search(r"age\s*>\s*STALE_THRESHOLD_MS\s*\)\s*\{([^}]+)\}", src)
        assert m is not None, (
            "Could not locate the `if (age > STALE_THRESHOLD_MS)` branch body"
        )
        branch = m.group(1)
        assert "releaseLock" in branch, (
            "STALE_THRESHOLD_MS comparison branch does not call releaseLock() — "
            "stale lock is never broken."
        )

    def test_lock_age_function_exists(self) -> None:
        """lockAge() must exist to feed the stale comparison."""
        src = _plugin_src()
        assert re.search(r"function\s+lockAge\s*\(", src), (
            "lockAge() function not found — no way to compute lock age"
        )


class TestRationaleDocumented:
    def test_rationale_in_test_docstring(self) -> None:
        """Requirement 4: rationale documented in this module's docstring."""
        doc = __doc__ or ""
        assert "faster recovery" in doc.lower() or "recovers faster" in doc.lower(), (
            "Rationale (faster recovery) not documented in module docstring"
        )
        assert "race" in doc.lower(), (
            "Rationale must note that race prevention is preserved"
        )
        assert "crashed commit" in doc.lower() or "crash" in doc.lower(), (
            "Rationale must reference crashed-commit recovery scenario"
        )

    def test_plugin_has_2min_comment(self) -> None:
        """The plugin source documents the threshold in minutes for readability."""
        src = _plugin_src()
        m = re.search(r"STALE_THRESHOLD_MS\s*=\s*[^;]+;\s*//\s*(.+)", src)
        assert m is not None, (
            "STALE_THRESHOLD_MS lacks an inline `// <units>` comment"
        )
        comment = m.group(1).lower()
        assert "minute" in comment or "min" in comment, (
            f"Inline comment does not name time units: {m.group(1)!r}"
        )
