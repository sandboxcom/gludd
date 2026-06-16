"""Cross-cycle de-duplication for self-improvement proposals (F8 companion).

The gap analysis in ``SelfImprovementHarness.run_gap_analysis`` is deterministic:
the same missing-test or dead-code finding re-appears on *every* cycle until the
underlying gap is actually closed.  Persisting the proposal each time would file
the identical todo over and over — the runaway behaviour the F8 human-gate and
open-cap guard against, made worse by duplicates that each consume a cap slot.

``SelfImproveDeduplicator`` collapses a proposal to a stable *signature* and
drops any proposal whose signature is already open.  It is a pure helper: the
caller supplies the signatures of currently-open self-improve todos (derived
however it likes — e.g. from a DB query) and gets back only the genuinely-new
proposals.

Signature contract
------------------
A proposal's identity is ``(gap_type, source_file)`` — the same gap on the same
file is "the same work" regardless of how its human-readable title is phrased.
When ``gap_type``/``source_file`` are absent the title is used as a fallback so
two unrelated proposals never accidentally collapse together.
"""

from __future__ import annotations

from typing import Any


def proposal_signature(proposal: dict[str, Any]) -> str:
    """Return a stable identity string for a self-improve proposal.

    Uses ``gap_type`` + ``source_file`` when present (the harness emits both via
    ``generate_fix_todos``); otherwise falls back to the title so distinct
    proposals never collide.
    """
    gap_type = str(proposal.get("gap_type", "") or "").strip()
    source_file = str(proposal.get("source_file", "") or "").strip()
    if gap_type or source_file:
        return f"{gap_type}::{source_file}"
    return f"title::{str(proposal.get('title', '') or '').strip()}"


class SelfImproveDeduplicator:
    """Suppress re-filing of self-improve proposals already open.

    Args:
        open_signatures: Signatures (see :func:`proposal_signature`) of
                         self-improve todos already open in the system.  May be
                         omitted and supplied per-call instead.
    """

    def __init__(self, open_signatures: set[str] | None = None) -> None:
        self._open: set[str] = set(open_signatures or set())

    def is_duplicate(self, proposal: dict[str, Any]) -> bool:
        """True if ``proposal`` matches an already-open signature."""
        return proposal_signature(proposal) in self._open

    def filter_new(
        self,
        proposals: list[dict[str, Any]],
        open_signatures: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Return only proposals not already open (and not duplicated *within*
        the batch).

        The first occurrence of each signature in ``proposals`` is kept; later
        occurrences with the same signature are dropped, so a batch that itself
        contains duplicates is also collapsed.

        Args:
            proposals:        Generated self-improve proposal payloads.
            open_signatures:  Optional override/augmentation of the open set for
                              this call (unioned with the constructor's set).

        Returns:
            The de-duplicated, genuinely-new proposals in original order.
        """
        seen: set[str] = set(self._open)
        if open_signatures:
            seen |= set(open_signatures)
        fresh: list[dict[str, Any]] = []
        for proposal in proposals:
            sig = proposal_signature(proposal)
            if sig in seen:
                continue
            seen.add(sig)
            fresh.append(proposal)
        return fresh
