"""Overlap-aware 3-way merge primitive (stdlib-only, pure, deterministic).

Why this exists
---------------
gludd integrates the results of an agent / self-improvement worktree back into a
base repo. If it does that with a WHOLE-FILE copy, then when BOTH the base and
the worktree changed the same file, the copy silently REVERTS the base's change
— a real data-loss bug (the same class we just hit in the orchestration layer).

``safe_merge`` is the anti-clobber primitive:

* If only one side diverged from ``base``, take that side.
* If both sides diverged but to the SAME text, take it (convergent edit).
* If both sides diverged differently, do a line-level 3-way merge. When their
  changes touch disjoint regions, the result is a CLEAN merge containing BOTH
  edits — exactly what a blind copy would have lost. When they touch the same
  region, the result is FLAGGED ``conflict=True`` with git-style conflict
  markers. We NEVER silently pick one side over the other.

Implementation uses only :mod:`difflib` (``SequenceMatcher``) at the line level.
It is pure: no I/O, no globals, deterministic for a given input triple. The
file-oriented :func:`safe_merge_file` reads three paths, merges, and refuses to
write the destination on conflict.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass

__all__ = [
    "MergeResult",
    "detect_overlap",
    "safe_merge",
    "safe_merge_file",
]

# git-style conflict markers (7 chars, matching git's convention).
_CONFLICT_START = "<<<<<<<"
_CONFLICT_SEP = "======="
_CONFLICT_END = ">>>>>>>"


@dataclass(frozen=True)
class MergeResult:
    """Outcome of a 3-way merge.

    Attributes:
        text: The merged text. On conflict this contains git-style conflict
            markers and is NOT a resolved file — callers must not write it as
            if it were.
        conflict: True iff at least one region could not be auto-resolved.
        source: Provenance of the result, one of ``"base"`` (no change),
            ``"ours"`` / ``"theirs"`` (only that side changed), ``"identical"``
            (both made the same change), ``"merged"`` (clean 3-way merge), or
            ``"conflict"`` (had to fall back to conflict markers).
    """

    text: str
    conflict: bool
    source: str


def _splitlines(text: str) -> list[str]:
    """Split keeping line endings so a merge round-trips byte-for-byte."""
    return text.splitlines(keepends=True)


def detect_overlap(base_text: str, ours_text: str, theirs_text: str) -> bool:
    """Return True iff BOTH sides diverged from base to DIFFERENT text.

    This is the dangerous case a blind whole-file copy would clobber: copying
    either side over the other reverts the other's change. When only one side
    (or neither) changed, or both made the identical change, there is nothing
    to clobber and this returns False.
    """
    ours_changed = ours_text != base_text
    theirs_changed = theirs_text != base_text
    if not (ours_changed and theirs_changed):
        return False
    return ours_text != theirs_text


def safe_merge(base_text: str, ours_text: str, theirs_text: str) -> MergeResult:
    """3-way merge ``ours`` and ``theirs`` against common ancestor ``base``.

    Never silently discards a divergent edit:

    * only one side changed -> that side
    * both changed identically -> that text
    * both changed differently -> line-level 3-way merge; clean if regions are
      disjoint, otherwise ``conflict=True`` with conflict markers.
    """
    ours_changed = ours_text != base_text
    theirs_changed = theirs_text != base_text

    if not ours_changed and not theirs_changed:
        return MergeResult(text=base_text, conflict=False, source="base")
    if ours_changed and not theirs_changed:
        return MergeResult(text=ours_text, conflict=False, source="ours")
    if theirs_changed and not ours_changed:
        return MergeResult(text=theirs_text, conflict=False, source="theirs")
    if ours_text == theirs_text:
        # Both sides made the same edit — convergent, no clobber.
        return MergeResult(text=ours_text, conflict=False, source="identical")

    return _three_way_merge(base_text, ours_text, theirs_text)


def _three_way_merge(
    base_text: str, ours_text: str, theirs_text: str
) -> MergeResult:
    """Line-level 3-way merge of two genuinely divergent sides.

    Walks the regions of ``base`` using two ``SequenceMatcher`` opcode streams
    (base->ours and base->theirs). For each base region:

    * if neither side touched it -> keep base lines
    * if exactly one side touched it -> take that side's replacement
    * if both touched it identically -> take it
    * if both touched it differently -> emit a conflict hunk and flag conflict
    """
    base_lines = _splitlines(base_text)
    ours_lines = _splitlines(ours_text)
    theirs_lines = _splitlines(theirs_text)

    ours_ops = _opcodes(base_lines, ours_lines)
    theirs_ops = _opcodes(base_lines, theirs_lines)

    out: list[str] = []
    conflict = False
    i = 0  # cursor into base_lines
    n = len(base_lines)

    while i < n:
        o_start, o_end = _region_for(ours_ops, i)
        t_start, t_end = _region_for(theirs_ops, i)

        ours_touches = o_start is not None
        theirs_touches = t_start is not None

        if not ours_touches and not theirs_touches:
            out.append(base_lines[i])
            i += 1
            continue

        # Union the two affected base spans so overlapping edits are considered
        # together (and we never split a single side's change across hunks).
        span_start = i
        span_end = i + 1
        if o_start is not None and o_end is not None:
            span_start = min(span_start, o_start)
            span_end = max(span_end, o_end)
        if t_start is not None and t_end is not None:
            span_start = min(span_start, t_start)
            span_end = max(span_end, t_end)

        base_span = base_lines[span_start:span_end]
        ours_span = (
            _replacement(ours_ops, span_start, span_end, ours_lines)
            if ours_touches
            else base_span
        )
        theirs_span = (
            _replacement(theirs_ops, span_start, span_end, theirs_lines)
            if theirs_touches
            else base_span
        )

        if ours_span == theirs_span:
            out.extend(ours_span)
        elif not ours_touches:
            out.extend(theirs_span)
        elif not theirs_touches:
            out.extend(ours_span)
        elif ours_span == base_span:
            out.extend(theirs_span)
        elif theirs_span == base_span:
            out.extend(ours_span)
        else:
            conflict = True
            out.extend(_conflict_hunk(ours_span, theirs_span))

        i = span_end

    text = "".join(out)
    return MergeResult(
        text=text,
        conflict=conflict,
        source="conflict" if conflict else "merged",
    )


def _opcodes(
    base_lines: list[str], other_lines: list[str]
) -> list[tuple[str, int, int, int, int]]:
    matcher = difflib.SequenceMatcher(a=base_lines, b=other_lines, autojunk=False)
    return list(matcher.get_opcodes())


def _region_for(
    ops: list[tuple[str, int, int, int, int]], base_index: int
) -> tuple[int | None, int | None]:
    """If ``base_index`` falls inside a non-equal opcode, return its base span.

    Returns ``(base_start, base_end)`` when the index is inside a changed
    region (the replacement lines are resolved later via :func:`_replacement`),
    or ``(None, None)`` when the index is in an untouched region.
    """
    for tag, i1, i2, _j1, _j2 in ops:
        if tag == "equal":
            continue
        # 'delete'/'replace' have i1<i2; 'insert' has i1==i2 (zero-width at i1).
        if i1 <= base_index < i2 or (i1 == i2 == base_index):
            return i1, i2
    return None, None


def _replacement(
    ops: list[tuple[str, int, int, int, int]],
    span_start: int,
    span_end: int,
    other_lines: list[str],
) -> list[str]:
    """Resolve this side's text for the base span ``[span_start, span_end)``.

    Reconstructs the side's lines by replaying opcodes: for base positions
    inside the span, equal/replace/insert map to the side's corresponding
    ``other_lines`` slice; delete contributes nothing.
    """
    result: list[str] = []
    for tag, i1, i2, j1, j2 in ops:
        # Skip opcodes entirely outside the span (treat insert at boundary as
        # belonging to the region that contains its anchor base index).
        if i2 < span_start or i1 > span_end:
            continue
        if tag == "equal":
            lo = max(i1, span_start)
            hi = min(i2, span_end)
            if hi <= lo:
                continue
            offset = lo - i1
            length = hi - lo
            result.extend(other_lines[j1 + offset : j1 + offset + length])
        elif tag == "replace":
            if i1 >= span_start and i2 <= span_end:
                result.extend(other_lines[j1:j2])
        elif tag == "insert":
            if span_start <= i1 <= span_end:
                result.extend(other_lines[j1:j2])
        elif tag == "delete":
            # Deleted base lines contribute nothing to this side.
            continue
    return result


def _conflict_hunk(ours_span: list[str], theirs_span: list[str]) -> list[str]:
    """Build a git-style conflict hunk for two irreconcilable spans."""
    hunk: list[str] = []
    hunk.append(_CONFLICT_START + " ours\n")
    hunk.extend(_ensure_newline(ours_span))
    hunk.append(_CONFLICT_SEP + "\n")
    hunk.extend(_ensure_newline(theirs_span))
    hunk.append(_CONFLICT_END + " theirs\n")
    return hunk


def _ensure_newline(lines: list[str]) -> list[str]:
    """Guarantee each side's last line ends with a newline inside a hunk so the
    separator/marker lands on its own line even if a side lacked a trailing
    newline."""
    if not lines:
        return []
    out = list(lines)
    if not out[-1].endswith("\n"):
        out[-1] = out[-1] + "\n"
    return out


def safe_merge_file(
    base_path: str, ours_path: str, theirs_path: str, dest_path: str
) -> MergeResult:
    """File-oriented :func:`safe_merge`: read three files, merge, write dest.

    On a CLEAN merge the merged text is written to ``dest_path``. On CONFLICT
    the function REFUSES to write ``dest_path`` (returning the conflict result),
    so a conflict can never be silently materialized as a resolved file — that
    would re-introduce the very clobber this primitive exists to prevent.

    Raises ``FileNotFoundError`` / ``OSError`` if an input cannot be read.
    """
    base_text = _read(base_path)
    ours_text = _read(ours_path)
    theirs_text = _read(theirs_path)

    result = safe_merge(base_text, ours_text, theirs_text)
    if not result.conflict:
        with open(dest_path, "w", encoding="utf-8", newline="") as fh:
            fh.write(result.text)
    return result


def _read(path: str) -> str:
    with open(path, encoding="utf-8", newline="") as fh:
        return fh.read()
