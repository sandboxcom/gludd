"""Structural contract tests for the CI pipeline medic role specification."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC = ROOT / "docs" / "design" / "CI_PIPELINE_MEDIC_ROLE.md"


def test_medic_advances_passing_fixes_to_exact_sha_ci_without_human_prompt() -> None:
    """Focused green evidence must immediately trigger the exact-SHA CI path."""
    text = SPEC.read_text(encoding="utf-8")
    marker = "### 2d. Automatic exact-SHA transition"
    assert marker in text

    contract = text.split(marker, 1)[1].split("\n### ", 1)[0]
    required = (
        "all known failures have focused passing evidence",
        "HEAD is clean and committed",
        "`make ci-push-committed-head`",
        "MUST NOT stop at a status-only report",
        "MUST NOT wait for a person",
        "[Exact-SHA GHA Signal](../CI_EXACT_SHA_SIGNAL.md)",
    )
    for statement in required:
        assert statement in contract
