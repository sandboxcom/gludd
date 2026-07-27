"""Structural pin: ci-await codification is complete across all layers.

Verifies that the ci-await target and its policy enforcement are codified:
  - AGENTS.md section headers, rules 1 + 5, and the incident mention
  - enforce-no-wait.ts CI_POLL_DISPATCH_PATTERNS
  - Makefile target existence and script reference
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
AGENTS_MD = REPO_ROOT / "AGENTS.md"
MAKEFILE = REPO_ROOT / "Makefile"
ENFORCE_NO_WAIT_TS = REPO_ROOT / ".opencode" / "plugin" / "enforce-no-wait.ts"


# ── AGENTS.md structural assertions ──────────────────────────────────────


def _agents_text() -> str:
    return AGENTS_MD.read_text()


def test_agents_md_ci_await_in_ci_poll_forbidden_section() -> None:
    """AGENTS.md 'CI-Poll Subagents Are Forbidden' section line 2848
    references ci-await."""
    text = _agents_text()
    # The forbidden-subagents section MUST contain the word ci-await.
    assert "ci-await" in text, "AGENTS.md must contain 'ci-await'"


def test_agents_md_rule_1_mentions_ci_await() -> None:
    """AGENTS.md rule 1 in the CI-poll section explicitly names ci-await
    as a forbidden dispatch target."""
    text = _agents_text()
    assert 'NEVER dispatch a "poll CI until terminal' in text
    assert "make ci-await" in text
    assert "must NEVER be dispatched to a subagent" in text


def test_agents_md_rule_5_mentions_ci_await() -> None:
    """AGENTS.md rule 5 says ci-await is for release-cut only."""
    text = _agents_text()
    assert "`make ci-wait` and `make ci-await` are for release-cut only" in text
    assert "`ci-await` uses a 3600s default timeout" in text


# ── enforce-no-wait.ts assertions ────────────────────────────────────────


def _no_wait_text() -> str:
    return ENFORCE_NO_WAIT_TS.read_text()


def test_enforce_no_wait_ts_ci_poll_patterns_include_ci_await() -> None:
    """CI_POLL_DISPATCH_PATTERNS in enforce-no-wait.ts contains a regex
    that matches 'make ci-await' / 'ci-await'."""
    text = _no_wait_text()
    assert r"\bmake\s+ci-await\b" in text
    assert r"\bci-await\b" in text


# ── Makefile assertions ──────────────────────────────────────────────────


def _makefile_text() -> str:
    return MAKEFILE.read_text()


def test_makefile_ci_await_target_exists() -> None:
    """ci-await is declared as a Makefile target."""
    lines = [line for line in _makefile_text().splitlines() if line.startswith("ci-await:")]
    assert len(lines) >= 1, "Makefile must declare a ci-await target"


def test_makefile_ci_await_target_uses_script() -> None:
    """ci-await target invokes scripts/ci_await.py."""
    text = _makefile_text()
    assert "scripts/ci_await.py" in text, "ci-await target must use scripts/ci_await.py"
