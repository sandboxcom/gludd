"""Tests for the ``pre-commit-check`` Makefile target (RP.23).

The target runs lint + collect-check + typecheck sequentially, failing on the
first error. It is a FAST pre-check intended to run before every commit
(per AGENTS.md OD.9 / OD.10). It must NOT invoke the full test suite.
"""

from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent.parent
MAKEFILE = ROOT / "Makefile"


def _content() -> str:
    assert MAKEFILE.exists(), "Makefile must exist"
    return MAKEFILE.read_text()


def _target_block(content: str) -> str:
    """Return the recipe lines of the ``pre-commit-check`` target."""
    assert "pre-commit-check:" in content, (
        "Makefile missing 'pre-commit-check:' target"
    )
    lines = content.splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.startswith("pre-commit-check:"))
    block: list[str] = []
    for ln in lines[start + 1 :]:
        if ln and not ln.startswith(("\t", " ")) and ln.strip():
            break
        block.append(ln)
    return "\n".join(block)


def test_target_exists():
    content = _content()
    assert "pre-commit-check:" in content, (
        "Makefile missing 'pre-commit-check:' target declaration"
    )


def test_target_references_lint():
    block = _target_block(_content())
    assert "lint" in block, (
        "pre-commit-check recipe must reference the 'lint' target"
    )


def test_target_references_collect_check():
    block = _target_block(_content())
    assert "collect-check" in block, (
        "pre-commit-check recipe must reference the 'collect-check' target"
    )


def test_target_references_typecheck():
    block = _target_block(_content())
    assert "typecheck" in block, (
        "pre-commit-check recipe must reference the 'typecheck' target"
    )


def test_target_does_not_run_full_test_suite():
    """pre-commit-check is a FAST pre-check; it must not invoke ``make test``."""
    block = _target_block(_content())
    # Reject any recipe line that invokes the full test target.
    for ln in block.splitlines():
        stripped = ln.strip()
        if stripped.startswith("#"):
            continue
        assert not (
            "$(MAKE) test" in stripped
            or "make test" in stripped
            or stripped.endswith(" test")
            or stripped == "test"
        ), (
            "pre-commit-check must NOT run the full test suite "
            "(it is a fast pre-check, not the gate)"
        )


def test_target_mentions_agents_md_directive():
    """Recipe surfaces the OD.10 provenance so future editors see the policy link."""
    block = _target_block(_content())
    assert "OD.10" in block or "AGENTS.md" in block, (
        "pre-commit-check recipe should reference AGENTS.md OD.10 "
        "(per task RP.23 requirement 3)"
    )


def test_target_emits_terminal_marker():
    """Target prints a PASSED marker so callers can detect success."""
    block = _target_block(_content())
    assert "PASSED" in block, (
        "pre-commit-check recipe should emit a terminal PASSED marker"
    )


@pytest.mark.parametrize("missing", ["lint", "collect-check", "typecheck"])
def test_all_three_prereqs_present(missing):
    """Parametrized guard: each prerequisite must appear in the recipe."""
    block = _target_block(_content())
    assert missing in block
