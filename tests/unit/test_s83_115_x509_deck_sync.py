"""Pin the S83.115 X.509 contract across its feature doc and reveal deck."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DECK = ROOT / "docs/presentation/deck/index.html"
FEATURE = ROOT / "docs/features/X509_CHAIN_VALIDATION.md"
TASKS = ROOT / "TASKS.md"


def _x509_slide() -> str:
    """Return only the reveal.js slide that mirrors the X.509 contract."""
    content = DECK.read_text(encoding="utf-8")
    return content.split('<section data-contract="s83-115-x509-chain-validation">', 1)[
        1
    ].split("</section>", 1)[0]


def test_reveal_deck_mirrors_the_fail_closed_x509_contract() -> None:
    """The presentation must retain the security and trust-boundary contract."""
    feature = FEATURE.read_text(encoding="utf-8")
    slide = _x509_slide()

    for marker in (
        "CSR proof-of-possession signature",
        "critical CA basic constraints",
        "verify_directly_issued_by",
        "unrecognized critical extensions fail closed",
        "caller-supplied trust boundary",
        "O(n)",
        "source-only release rollback",
    ):
        assert marker in feature

    for marker in (
        "S83.115",
        "Fail closed",
        "CSR proof-of-possession",
        "critical BasicConstraints",
        "signing key matches issuer",
        "verified signature",
        "caller-supplied trust boundary",
        "210/210 warning-strict",
        "95% aggregate",
        "90% branch coverage",
        "O(n)",
        "zero network access",
        "rolling replacement",
        "drain in-flight issuance",
        "source-only rollback",
        "docs/features/X509_CHAIN_VALIDATION.md",
    ):
        assert marker in slide


def test_task_closeout_names_the_measured_x509_evidence() -> None:
    """The ledger must close S83.115 only with its reproducible evidence."""
    task = next(
        line
        for line in TASKS.read_text(encoding="utf-8").splitlines()
        if "S83.115" in line
    )

    for marker in (
        "- [x] S83.115",
        "`cbda2a47b`",
        "210/210",
        "95%",
        "90% branch",
        "s83-115-x509-chain-validation",
        "status: completed",
    ):
        assert marker in task
