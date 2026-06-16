"""Merge-conflict triage controller: classify conflict hunks and recommend a
deterministic resolution strategy per hunk, then a file-level verdict.

When an agent's branch fails to merge cleanly, ``git`` leaves conflict markers
in the working tree::

    <<<<<<< ours
    a = 1
    =======
    a = 2
    >>>>>>> theirs

Detecting *that* a conflict exists is cheap (``"CONFLICT" in git output``), and
that is all :meth:`general_ludd.git_automation.repo.GitRepo.merge_branch` does
today.  The hard, high-value question is *what to do about it*: which hunks are
mechanical (one side added lines, the other side is empty; both sides are
byte-identical; only whitespace differs; both sides are import lines that can be
unioned) versus which represent a true semantic divergence that must escalate to
a human / a stronger model.

This controller answers that question **deterministically and without side
effects**:

  - **Parse, don't guess.**  Conflict blocks are parsed out of the raw file
    content by their git markers into structured :class:`ConflictHunk` objects.
    Lines outside any conflict block are ignored.
  - **Classify each hunk** into a :class:`ConflictKind` from the literal hunk
    contents only — no clock, no network, no model call.
  - **Recommend a strategy** (:class:`ResolutionStrategy`) per hunk with a
    rationale and a confidence in ``[0, 1]``.  Anything the controller is not
    confident is mechanically safe is escalated, never silently merged.
  - **Aggregate a file verdict.**  A file is ``auto_resolvable`` only when
    *every* hunk has a non-escalation strategy; one ambiguous hunk forces the
    whole file to escalate (fail-closed).

The controller is pure: identical inputs always yield identical plans, so the
result can be cached, replayed, and unit-tested exhaustively.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

__all__ = [
    "ConflictHunk",
    "ConflictKind",
    "FileResolutionPlan",
    "HunkResolution",
    "MergeConflictController",
    "ResolutionStrategy",
]

_OURS_MARKER = "<<<<<<<"
_SEP_MARKER = "======="
_THEIRS_MARKER = ">>>>>>>"
# A base marker (`|||||||`) appears only in diff3 conflict style, between the
# `<<<<<<<` and `=======` markers.  We tolerate it by treating everything from
# the base marker up to `=======` as base context that is dropped from the
# "ours" side (mirroring git's 2-way view of the conflict).
_BASE_MARKER = "|||||||"


class ConflictKind(StrEnum):
    """Classification of a single conflict hunk, from its contents only."""

    #: One side is empty and the other added lines (a clean add/remove split).
    ADD_ON_ONE_SIDE = "add_on_one_side"
    #: Both sides are byte-identical (a spurious conflict; either side is fine).
    IDENTICAL = "identical"
    #: The two sides differ only by leading/trailing whitespace per line.
    WHITESPACE_ONLY = "whitespace_only"
    #: Both sides consist solely of import lines that can be set-unioned.
    IMPORT_BLOCK = "import_block"
    #: A genuine content divergence with no mechanical resolution.
    SEMANTIC = "semantic"


class ResolutionStrategy(StrEnum):
    """The recommended action for a hunk."""

    TAKE_OURS = "take_ours"
    TAKE_THEIRS = "take_theirs"
    TAKE_EITHER = "take_either"
    TAKE_UNION = "take_union"
    ESCALATE = "escalate"


@dataclass(frozen=True)
class ConflictHunk:
    """One ``<<<<<<< / ======= / >>>>>>>`` block parsed from a file.

    Attributes:
        ours: The lines on the local ("ours") side, in order, newlines stripped.
        theirs: The lines on the incoming ("theirs") side, in order.
        start_line: 1-based line number of the ``<<<<<<<`` marker in the file.
    """

    ours: tuple[str, ...]
    theirs: tuple[str, ...]
    start_line: int


@dataclass(frozen=True)
class HunkResolution:
    """The controller's decision for a single hunk.

    Attributes:
        hunk: The hunk this resolution applies to.
        kind: How the hunk was classified.
        strategy: The recommended action.
        confidence: Confidence in ``[0.0, 1.0]`` that ``strategy`` is safe to
            apply automatically.  Escalations always carry ``0.0``.
        rationale: Human-readable explanation of the decision.
        resolved_lines: The concrete line list a mechanical strategy would
            write.  ``None`` for :attr:`ResolutionStrategy.ESCALATE`.
    """

    hunk: ConflictHunk
    kind: ConflictKind
    strategy: ResolutionStrategy
    confidence: float
    rationale: str
    resolved_lines: tuple[str, ...] | None = None


@dataclass(frozen=True)
class FileResolutionPlan:
    """File-level aggregate of per-hunk resolutions.

    Attributes:
        path: The path the plan was computed for (echoed from the caller).
        resolutions: One :class:`HunkResolution` per conflict hunk, in file
            order.
        auto_resolvable: ``True`` only when every hunk has a non-escalation
            strategy.  A single ambiguous hunk makes the whole file escalate.
    """

    path: str
    resolutions: list[HunkResolution] = field(default_factory=list)

    @property
    def auto_resolvable(self) -> bool:
        """``True`` iff no hunk requires escalation (fail-closed on any doubt)."""
        return len(self.resolutions) > 0 and all(
            r.strategy is not ResolutionStrategy.ESCALATE for r in self.resolutions
        )

    @property
    def escalation_count(self) -> int:
        """Number of hunks that could not be resolved mechanically."""
        return sum(
            1 for r in self.resolutions if r.strategy is ResolutionStrategy.ESCALATE
        )


class MergeConflictController:
    """Parse, classify, and recommend resolutions for git conflict markers.

    The controller holds no state; every method is pure and deterministic.
    """

    @staticmethod
    def parse_hunks(content: str) -> list[ConflictHunk]:
        """Extract conflict hunks from raw file ``content``.

        Recognises both the default 2-way conflict style and the diff3 style
        (where a ``|||||||`` base section precedes ``=======``); base lines are
        dropped so the result is always the 2-way ours/theirs view.

        Args:
            content: The full text of a conflicted file (``\\n``-joined).

        Returns:
            Conflict hunks in file order.  Empty when there are no markers.
            Unterminated blocks (a ``<<<<<<<`` with no closing ``>>>>>>>``) are
            ignored rather than guessed at.
        """
        lines = content.split("\n")
        hunks: list[ConflictHunk] = []
        i = 0
        n = len(lines)
        while i < n:
            if not lines[i].startswith(_OURS_MARKER):
                i += 1
                continue
            start_line = i + 1  # 1-based marker line.
            ours: list[str] = []
            theirs: list[str] = []
            i += 1
            # Collect "ours" up to the separator (or a diff3 base marker).
            in_base = False
            saw_sep = False
            saw_end = False
            while i < n:
                line = lines[i]
                if line.startswith(_OURS_MARKER):
                    # A nested/restarted conflict marker => the current block is
                    # malformed; bail out of it without emitting.
                    break
                if line.startswith(_BASE_MARKER):
                    in_base = True
                    i += 1
                    continue
                if line.startswith(_SEP_MARKER):
                    saw_sep = True
                    in_base = False
                    i += 1
                    break
                if not in_base:
                    ours.append(line)
                i += 1
            if not saw_sep:
                # No separator before EOF or a restarted marker: malformed.
                continue
            # Collect "theirs" up to the end marker.
            while i < n:
                line = lines[i]
                if line.startswith(_THEIRS_MARKER):
                    saw_end = True
                    i += 1
                    break
                if line.startswith(_OURS_MARKER):
                    break
                theirs.append(line)
                i += 1
            if not saw_end:
                continue
            hunks.append(
                ConflictHunk(
                    ours=tuple(ours),
                    theirs=tuple(theirs),
                    start_line=start_line,
                )
            )
        return hunks

    def classify(self, hunk: ConflictHunk) -> ConflictKind:
        """Classify a hunk from its contents only (deterministic)."""
        ours = hunk.ours
        theirs = hunk.theirs

        if list(ours) == list(theirs):
            return ConflictKind.IDENTICAL

        if not ours or not theirs:
            return ConflictKind.ADD_ON_ONE_SIDE

        # Whitespace-only difference: same number of lines, each pair equal once
        # leading/trailing whitespace is stripped (and at least one pair really
        # differs by whitespace, else it would be IDENTICAL above).
        if len(ours) == len(theirs) and all(
            o.strip() == t.strip() for o, t in zip(ours, theirs, strict=True)
        ):
            return ConflictKind.WHITESPACE_ONLY

        if self._is_import_block(ours) and self._is_import_block(theirs):
            return ConflictKind.IMPORT_BLOCK

        return ConflictKind.SEMANTIC

    def resolve_hunk(self, hunk: ConflictHunk) -> HunkResolution:
        """Decide a resolution for a single hunk.

        Mechanical kinds get a concrete strategy + ``resolved_lines``; anything
        the controller cannot prove safe is escalated with confidence ``0.0``.
        """
        kind = self.classify(hunk)

        if kind is ConflictKind.IDENTICAL:
            return HunkResolution(
                hunk=hunk,
                kind=kind,
                strategy=ResolutionStrategy.TAKE_EITHER,
                confidence=1.0,
                rationale="Both sides are byte-identical; either may be kept.",
                resolved_lines=hunk.ours,
            )

        if kind is ConflictKind.ADD_ON_ONE_SIDE:
            if hunk.ours and not hunk.theirs:
                return HunkResolution(
                    hunk=hunk,
                    kind=kind,
                    strategy=ResolutionStrategy.TAKE_OURS,
                    confidence=0.9,
                    rationale="Theirs is empty; ours adds lines on a clean split.",
                    resolved_lines=hunk.ours,
                )
            return HunkResolution(
                hunk=hunk,
                kind=kind,
                strategy=ResolutionStrategy.TAKE_THEIRS,
                confidence=0.9,
                rationale="Ours is empty; theirs adds lines on a clean split.",
                resolved_lines=hunk.theirs,
            )

        if kind is ConflictKind.WHITESPACE_ONLY:
            return HunkResolution(
                hunk=hunk,
                kind=kind,
                strategy=ResolutionStrategy.TAKE_OURS,
                confidence=0.8,
                rationale=(
                    "Sides differ only in leading/trailing whitespace; keeping "
                    "ours preserves local formatting."
                ),
                resolved_lines=hunk.ours,
            )

        if kind is ConflictKind.IMPORT_BLOCK:
            union = self._union_imports(hunk.ours, hunk.theirs)
            return HunkResolution(
                hunk=hunk,
                kind=kind,
                strategy=ResolutionStrategy.TAKE_UNION,
                confidence=0.75,
                rationale=(
                    "Both sides are import lines; the deduplicated, sorted union "
                    "preserves every import from each side."
                ),
                resolved_lines=union,
            )

        return HunkResolution(
            hunk=hunk,
            kind=ConflictKind.SEMANTIC,
            strategy=ResolutionStrategy.ESCALATE,
            confidence=0.0,
            rationale=(
                "Sides diverge semantically with no mechanically safe merge; "
                "escalate to a human or a stronger model."
            ),
            resolved_lines=None,
        )

    def plan_file(self, path: str, content: str) -> FileResolutionPlan:
        """Parse, classify, and resolve every hunk in a conflicted file.

        Args:
            path: The file path (echoed into the plan for caller bookkeeping).
            content: The conflicted file's full text.

        Returns:
            A :class:`FileResolutionPlan`.  ``auto_resolvable`` is ``True`` only
            when there is at least one hunk and none escalate.
        """
        resolutions = [self.resolve_hunk(h) for h in self.parse_hunks(content)]
        return FileResolutionPlan(path=path, resolutions=resolutions)

    # -- helpers ---------------------------------------------------------------

    @staticmethod
    def _is_import_block(lines: tuple[str, ...]) -> bool:
        """True when every non-blank line is a Python ``import``/``from`` line."""
        non_blank = [ln for ln in lines if ln.strip()]
        if not non_blank:
            return False
        return all(
            ln.lstrip().startswith(("import ", "from ")) for ln in non_blank
        )

    @staticmethod
    def _union_imports(
        ours: tuple[str, ...], theirs: tuple[str, ...]
    ) -> tuple[str, ...]:
        """Deduplicated, deterministically-sorted union of import lines.

        Blank lines are dropped; the remaining lines are de-duplicated by their
        exact text and sorted so the output is stable regardless of input order.
        """
        seen: dict[str, None] = {}
        for ln in (*ours, *theirs):
            if ln.strip():
                seen.setdefault(ln, None)
        return tuple(sorted(seen))
