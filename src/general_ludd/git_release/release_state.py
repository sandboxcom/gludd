"""Zero-downtime release state machine (spec GRC-001 §7, §8, GRC-SEC-004).

The state machine encodes the ZDD protocol edges:

    DISCOVER -> PLAN -> BUILD_ONCE -> VERIFY_OFFLINE -> STAGE
             -> CANARY -> PROMOTE -> VERIFY_RELEASE_PAGE -> RELEASED
                           |              |
                           +--> ROLLBACK <-+

Each forward transition is gated by spec-mandated preconditions:

- BUILD_ONCE pins the source SHA; a moving source ref blocks every later
  stage (GRC-SEC-004 "fail closed", §8 "Source ref moves").
- VERIFY_OFFLINE requires non-empty gate evidence (GRC-SEC-004 "unavailable
  gate evidence" row).
- STAGE requires the consumed artifact digest to match the pinned build digest
  (GRC-ZDD-001 "Build once, promote by digest").
- CANARY/PROMOTE require a passed health gate (GRC-ZDD-003).
- VERIFY_RELEASE_PAGE requires the remote release page to be proven complete
  (GRC-ZDD-005).
- RELEASED is terminal: a shipped release cannot transition out (a new release
  gets a new verdict).

Rollback (spec §8 "Canary regression") is allowed from CANARY and PROMOTE and
restores the prior known-good artifact digest recorded when the canary was
entered. Rollback from RELEASED is forbidden — recovery is a fresh plan.
"""

from __future__ import annotations

from collections.abc import Sequence
from enum import StrEnum

__all__ = [
    "AdvanceResult",
    "ReleaseState",
    "ReleaseStateMachine",
    "TransitionError",
]


class ReleaseState(StrEnum):
    """Lifecycle states for the ZDD protocol (spec §7)."""

    DISCOVER = "discover"
    PLAN = "plan"
    BUILD_ONCE = "build_once"
    VERIFY_OFFLINE = "verify_offline"
    STAGE = "stage"
    CANARY = "canary"
    PROMOTE = "promote"
    VERIFY_RELEASE_PAGE = "verify_release_page"
    RELEASED = "released"
    ROLLBACK = "rollback"


# Spec §7 — the only legal forward edges. CANARY and PROMOTE may also fall
# through to ROLLBACK. RELEASED is terminal.
_ALLOWED_FORWARD: dict[ReleaseState, frozenset[ReleaseState]] = {
    ReleaseState.DISCOVER: frozenset({ReleaseState.PLAN}),
    ReleaseState.PLAN: frozenset({ReleaseState.BUILD_ONCE}),
    ReleaseState.BUILD_ONCE: frozenset({ReleaseState.VERIFY_OFFLINE}),
    ReleaseState.VERIFY_OFFLINE: frozenset({ReleaseState.STAGE}),
    ReleaseState.STAGE: frozenset({ReleaseState.CANARY}),
    ReleaseState.CANARY: frozenset({ReleaseState.PROMOTE, ReleaseState.ROLLBACK}),
    ReleaseState.PROMOTE: frozenset({ReleaseState.VERIFY_RELEASE_PAGE, ReleaseState.ROLLBACK}),
    ReleaseState.VERIFY_RELEASE_PAGE: frozenset({ReleaseState.RELEASED}),
    ReleaseState.RELEASED: frozenset(),
    ReleaseState.ROLLBACK: frozenset(),
}


class TransitionError(RuntimeError):
    """Raised when a transition is structurally impossible (wrong edge, etc.)."""


class AdvanceResult:
    """Outcome of an :meth:`ReleaseStateMachine.advance` call.

    ``blocked`` is True when a precondition failed and the state machine held
    its position; ``reasons`` carries the spec reason codes (e.g.
    ``GRC-SEC-004``). A raised :class:`TransitionError` means the requested
    edge itself is not in the spec graph.
    """

    __slots__ = ("blocked", "reasons", "state")

    def __init__(self, *, blocked: bool, reasons: Sequence[str], state: ReleaseState) -> None:
        self.blocked = blocked
        self.reasons = list(reasons)
        self.state = state


class ReleaseStateMachine:
    """Drive a single release through the ZDD protocol.

    The machine is constructed with the pinned source SHA and the artifact
    digest produced by the single hermetic build. ``advance`` mutates state
    only when every spec precondition holds; otherwise it returns a blocked
    :class:`AdvanceResult` and leaves ``state`` unchanged.
    """

    def __init__(self, *, source_sha: str, artifact_digest: str) -> None:
        if not source_sha or not artifact_digest:
            raise ValueError("source_sha and artifact_digest are required")
        self._source_sha = source_sha
        self._artifact_digest = artifact_digest
        self.state: ReleaseState = ReleaseState.DISCOVER
        # Digest currently being served. Updated on STAGE (new build promoted)
        # and on ROLLBACK (prior known-good restored).
        self._serving_digest: str | None = None
        # Prior known-good digest captured when entering CANARY. Rollback
        # restores this value (GRC-ZDD-001, §8 "Canary regression").
        self._prior_digest: str | None = None

    # -- read-only properties ------------------------------------------------

    @property
    def serving_digest(self) -> str | None:
        return self._serving_digest

    @property
    def source_sha(self) -> str:
        return self._source_sha

    # -- forward transitions -------------------------------------------------

    def advance(
        self,
        *,
        target: ReleaseState,
        gate_evidence: Sequence[tuple[str, str, str]] | None = None,
        artifact_digest: str | None = None,
        observed_source_sha: str | None = None,
        health_gate_passed: bool = False,
        prior_digest: str | None = None,
        release_page_proven: bool = False,
    ) -> AdvanceResult:
        """Move to ``target`` if the spec preconditions hold.

        Returns a blocked :class:`AdvanceResult` (and leaves ``state``
        unchanged) when a precondition fails. Raises :class:`TransitionError`
        when the edge itself is not in the spec graph (wrong source state,
        same-state transition, or terminal state).
        """
        if target is self.state:
            raise TransitionError(f"already in {target.value}; no self-transition")

        allowed = _ALLOWED_FORWARD.get(self.state, frozenset())
        if target not in allowed:
            raise TransitionError(
                f"transition {self.state.value} -> {target.value} is not in spec §7",
            )

        # GRC-SEC-004: a moving source SHA blocks every stage after BUILD_ONCE.
        if observed_source_sha is not None and observed_source_sha != self._source_sha:
            return AdvanceResult(
                blocked=True,
                reasons=["GRC-SEC-004", "source-sha-moved"],
                state=self.state,
            )

        # Per-target preconditions (spec §7, GRC-ZDD-001..005).
        if target is ReleaseState.VERIFY_OFFLINE:
            if not gate_evidence:
                return AdvanceResult(
                    blocked=True,
                    reasons=["GRC-SEC-004", "missing-gate-evidence"],
                    state=self.state,
                )
        elif target is ReleaseState.STAGE:
            if artifact_digest is None or artifact_digest != self._artifact_digest:
                return AdvanceResult(
                    blocked=True,
                    reasons=["GRC-ZDD-001", "artifact-digest-mismatch"],
                    state=self.state,
                )
        elif target is ReleaseState.CANARY:
            if not health_gate_passed:
                return AdvanceResult(
                    blocked=True,
                    reasons=["GRC-ZDD-003", "health-gate-not-passed"],
                    state=self.state,
                )
            # Capture the prior known-good digest so rollback can restore it.
            if prior_digest is None:
                return AdvanceResult(
                    blocked=True,
                    reasons=["GRC-ZDD-001", "missing-prior-digest"],
                    state=self.state,
                )
            self._prior_digest = prior_digest
            self._serving_digest = self._artifact_digest
        elif target is ReleaseState.PROMOTE:
            if not health_gate_passed:
                return AdvanceResult(
                    blocked=True,
                    reasons=["GRC-ZDD-003", "health-gate-not-passed"],
                    state=self.state,
                )
        elif target is ReleaseState.VERIFY_RELEASE_PAGE and not release_page_proven:
            return AdvanceResult(
                blocked=True,
                reasons=["GRC-ZDD-005", "release-page-not-proven"],
                state=self.state,
            )

        self.state = target
        if target is ReleaseState.RELEASED:
            # Final state: serving the new build at 100% with proven page.
            self._serving_digest = self._artifact_digest
        return AdvanceResult(blocked=False, reasons=[], state=self.state)

    # -- rollback (spec §8 "Canary regression") ------------------------------

    def rollback(self, *, reason: str) -> None:
        """Roll back to the prior known-good digest.

        Allowed from CANARY and PROMOTE. Forbidden from RELEASED (recovery is
        a fresh plan) and from any state without a captured prior digest.
        """
        if self.state is ReleaseState.RELEASED:
            raise TransitionError("RELEASED is terminal; recovery is a new release plan")
        if self.state not in (ReleaseState.CANARY, ReleaseState.PROMOTE):
            raise TransitionError(
                f"rollback not permitted from {self.state.value}",
            )
        if self._prior_digest is None:
            raise TransitionError("no prior digest captured; cannot roll back")
        self.state = ReleaseState.ROLLBACK
        self._serving_digest = self._prior_digest
        # reason is recorded for observability; callers may emit GRC event
        # `release.rollback` with this string (spec §9).
        _ = reason
