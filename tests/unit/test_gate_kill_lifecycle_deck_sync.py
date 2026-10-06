"""Keep identity-verified gate-tree termination synchronized with the deck."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FEATURE = ROOT / "docs/features/GATE_RESOURCE_LIFECYCLE.md"
DECK = ROOT / "docs/presentation/deck/index.html"
CONTRACT_TOKEN = "gate-tree-termination"


def _deck_contract() -> str:
    """Return the one deck fragment that mirrors the gate-tree contract."""
    content = DECK.read_text(encoding="utf-8")
    opening = f'<div data-contract="{CONTRACT_TOKEN}">'
    assert content.count(opening) == 1
    return content.split(opening, 1)[1].split("</div>", 1)[0]


def test_feature_doc_pins_gate_tree_termination_and_practitioner_evidence() -> None:
    """The canonical contract retains ownership, bounds, evidence, and ZDD."""
    content = FEATURE.read_text(encoding="utf-8")

    for marker in (
        "#### Identity-verified gate-tree termination",
        "`gludd-gate-run-v1`",
        "legacy `{\"pid\", \"started_at\"}` lock",
        "deepest descendant first",
        "10-second `SIGTERM` grace",
        "one-second `SIGKILL` observation",
        "PID, start time, command, checkout root, and project namespace",
        "`=== GATE: ABORTED ===`",
        "`.gate-logs/gate-kill-evidence.json`",
        "`termination_failed`",
        "replacement gate",
        "Zero-downtime delivery",
        "passes 47/47 tests with warnings treated as errors",
        "`kill_owned_gate.py` at 91%",
        "python/cpython/issues/111873",
        "bazelbuild/bazel/issues/11910",
        "gitlab-org/gitlab-runner/-/issues/6189",
        "actions/runner/issues/3341",
    ):
        assert marker in content


def test_reveal_deck_mirrors_measured_gate_tree_boundaries() -> None:
    """The presentation cannot drift to PID-only or root-only termination."""
    content = DECK.read_text(encoding="utf-8")
    contract = _deck_contract()

    assert content.count("<section") == 51
    for marker in (
        "marked <code>gludd-gate-run-v1</code> lock",
        "legacy <code>{pid, started_at}</code> lock",
        "deepest descendant first",
        "<code>SIGTERM</code> &rarr; 10 s",
        "<code>SIGKILL</code> &rarr; 1 s",
        "PID + start + command + root + namespace",
        "ABORTED status + JSON evidence",
        "survivor retains <code>termination_failed</code>",
        "replacement gate",
        "47/47 warning-strict",
        "<code>kill_owned_gate.py</code> 91%",
        "docs/features/GATE_RESOURCE_LIFECYCLE.md",
    ):
        assert marker in contract


def test_gate_tree_sync_preserves_live_deck_tokens() -> None:
    """Lifecycle documentation must not resolve tracked build provenance."""
    content = DECK.read_text(encoding="utf-8")

    for token in (
        "{{VERSION}}",
        "{{TEST_COUNT}}",
        "{{ROLE_COUNT}}",
        "{{GIT_SHA}}",
        "{{GENERATED_AT}}",
    ):
        assert token in content
