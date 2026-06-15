"""Observability guardrails: "an unseen event is not an event."

User-mandated invariant (2026-06-15, codified in AGENTS.md "No unseen events"):
no long-running operation in this repo's tooling may run silently. Every long
phase must STREAM its output (``tee``) or emit a periodic HEARTBEAT / progress
marker, so a human (or agent) watching always sees forward motion. A process
whose progress cannot be observed is treated as broken.

These tests parse the Makefile and FAIL if a future change reintroduces a
silent long-running operation (the exact class of defect that produced a
16-minute black-box gate and a heartbeat-less CI poller).
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MAKEFILE = ROOT / "Makefile"


def _recipe(target: str) -> str:
    """Return the indented recipe body for a single Makefile target."""
    lines = MAKEFILE.read_text().splitlines()
    body: list[str] = []
    in_target = False
    for line in lines:
        if re.match(rf"^{re.escape(target)}:", line):
            in_target = True
            continue
        if in_target:
            # A new non-indented, non-blank line ends the recipe.
            if line and not line[0].isspace():
                break
            body.append(line)
    return "\n".join(body)


class TestNoUnseenEvents:
    def test_gate_test_phase_streams_via_tee(self) -> None:
        """The 16-min full-suite phase must tee so a running gate is observable."""
        body = _recipe("gate")
        assert "pytest tests/" in body, "gate must run the full suite"
        assert "tee /tmp/gludd-test-gate.txt" in body, (
            "gate test phase MUST tee its output to stdout — a backgrounded gate "
            "cannot be a silent black box (regression of the 16-min-silence defect)"
        )

    def test_gate_full_suite_is_never_silenced(self) -> None:
        body = _recipe("gate")
        for line in body.splitlines():
            if "pytest tests/" in line:
                assert "/dev/null" not in line, (
                    f"gate must not pipe the full suite to /dev/null: {line.strip()!r}"
                )

    def test_gate_emits_a_progress_marker_per_phase(self) -> None:
        """Each gate phase must print a stdout marker as it starts (heartbeat)."""
        body = _recipe("gate")
        markers = re.findall(r"\[gate .*?\] phase", body)
        assert len(markers) >= 5, (
            f"gate must emit a per-phase stdout progress marker (lint/typecheck/"
            f"collect/test/smoke); found {len(markers)}"
        )

    def test_ci_poll_loop_has_a_heartbeat(self) -> None:
        """The CI wait poller must print a heartbeat every cycle, not sleep silently."""
        body = _recipe("ci-wait-anon")
        assert "sleep" in body, "ci-wait-anon is expected to be a poll loop"
        assert "heartbeat" in body.lower(), (
            "ci-wait-anon MUST print a heartbeat each poll cycle — a silent "
            "sleep loop is an unseen event (regression of the silent-poller defect)"
        )

    def test_no_full_suite_pytest_to_devnull_anywhere(self) -> None:
        """No recipe anywhere may run the full suite silently to /dev/null."""
        offenders = [
            line.strip()
            for line in MAKEFILE.read_text().splitlines()
            if "pytest tests/" in line and "/dev/null" in line
        ]
        assert not offenders, f"full-suite pytest silenced to /dev/null: {offenders}"

    def test_smoke_in_gate_surfaces_failure_log(self) -> None:
        """A failed smoke phase must tail its log, not swallow it to /dev/null."""
        body = _recipe("gate")
        # The smoke invocation captures to a log; on failure it must surface a tail.
        assert "gludd-gate-smoke.log" in body, (
            "gate smoke output must be captured to a log file (not /dev/null) so "
            "failures are inspectable"
        )
        assert "tail -20 /tmp/gludd-gate-smoke.log" in body, (
            "gate must tail the smoke log on failure so the cause is visible"
        )
