"""Pure-Python diff engine: Myers diff, patience diff, 3-way merge,
patch apply, conflict markers, and unified-format output.

No external dependencies. The algorithms return typed dataclass results
suitable for programmatic consumption or human-readable formatting.
"""

from __future__ import annotations

import itertools
import typing as t
from dataclasses import dataclass, field

__all__ = [
    "ApplyError",
    "Conflict",
    "DiffEngine",
    "DiffEngineError",
    "DiffHunk",
    "EditOp",
    "HunkLine",
    "MergeResult",
    "PatchResult",
]

# ── types ────────────────────────────────────────────────────────────────────


class EditOp(t.NamedTuple):
    """A single edit operation: delete ``count`` lines at *old_start*,
    insert ``count`` lines at *new_start*."""

    old_start: int
    old_count: int
    new_start: int
    new_count: int


class HunkLine(t.NamedTuple):
    """One line inside a unified-format hunk."""

    kind: t.Literal["context", "add", "remove"]
    text: str


@dataclass
class DiffHunk:
    """A contiguous hunk of changes."""

    old_start: int
    old_count: int
    new_start: int
    new_count: int
    lines: list[HunkLine] = field(default_factory=list)


@dataclass
class Conflict:
    """A merge conflict region."""

    ours_start: int
    ours_end: int
    theirs_start: int
    theirs_end: int
    base_lines: list[str] = field(default_factory=list)
    ours_lines: list[str] = field(default_factory=list)
    theirs_lines: list[str] = field(default_factory=list)


@dataclass
class MergeResult:
    """Result of a 3-way merge."""

    merged: list[str]
    conflicts: list[Conflict]
    success: bool


@dataclass
class PatchResult:
    """Result of applying a patch."""

    applied: list[str]
    succeeded: int
    failed: int
    rejects: list[tuple[int, str]]  # (hunk_index, reason)


# ── errors ───────────────────────────────────────────────────────────────────


class DiffEngineError(Exception):
    """Base exception for the diff engine."""


class ApplyError(DiffEngineError):
    """A patch hunk could not be applied."""


# ── core algorithms ──────────────────────────────────────────────────────────


def _lcs_matrix(a: list[str], b: list[str]) -> list[list[int]]:
    """Standard O(n*m) LCS table."""
    n, m = len(a), len(b)
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        ai = a[i - 1]
        row_cur = dp[i]
        row_prev = dp[i - 1]
        for j in range(1, m + 1):
            if ai == b[j - 1]:
                row_cur[j] = row_prev[j - 1] + 1
            else:
                up = row_prev[j]
                left = row_cur[j - 1]
                row_cur[j] = up if up >= left else left
    return dp


def _backtrack_lcs(dp: list[list[int]], a: list[str], b: list[str], i: int, j: int) -> list[tuple[int, int]]:
    """Return list of (i_idx, j_idx) pairs forming the LCS."""
    result: list[tuple[int, int]] = []
    while i > 0 and j > 0:
        if a[i - 1] == b[j - 1]:
            result.append((i - 1, j - 1))
            i -= 1
            j -= 1
        elif dp[i - 1][j] >= dp[i][j - 1]:
            i -= 1
        else:
            j -= 1
    result.reverse()
    return result


def _myers_diff(a: list[str], b: list[str]) -> list[EditOp]:
    """Compute the shortest edit script (Myers algorithm).

    Returns a minimal list of EditOp instances that transform *a* into *b*.
    """
    n, m = len(a), len(b)
    max_d = n + m + 1
    v: dict[int, int] = {}
    v[1] = 0
    trace: list[dict[int, int]] = []

    for d in range(max_d):
        trace.append(dict(v))
        for k in range(-d, d + 1, 2):
            go_down = (k == -d) or (k != d and v.get(k - 1, -1) < v.get(k + 1, -1))
            if go_down:
                x = v.get(k + 1, -1)
                k + 1
            else:
                x = v.get(k - 1, -1) + 1
                k - 1

            y = x - k
            while x < n and y < m and a[x] == b[y]:
                x += 1
                y += 1
            v[k] = x
            if x >= n and y >= m:
                return _reconstruct_edit_script(trace, a, b, d, k, n, m)

    return []


def _reconstruct_edit_script(
    trace: list[dict[int, int]],
    a: list[str],
    b: list[str],
    d: int,
    end_k: int,
    n: int,
    m: int,
) -> list[EditOp]:
    """Reconstruct the edit script from the Myers trace."""
    x, y = n, m
    edits: list[EditOp] = []
    for dist in range(d, -1, -1):
        v = trace[dist]
        k = x - y

        go_down = (k == -dist) or (k != dist and v.get(k - 1, -1) < v.get(k + 1, -1))
        prev_k = k + 1 if go_down else k - 1

        prev_x = v.get(prev_k, 0)
        prev_y = prev_x - prev_k

        while x > prev_x and y > prev_y:
            x -= 1
            y -= 1

        if dist > 0:
            if x == prev_x:
                edits.append(EditOp(old_start=prev_x, old_count=0, new_start=prev_y, new_count=y - prev_y))
            else:
                edits.append(EditOp(old_start=prev_x, old_count=x - prev_x, new_start=prev_y, new_count=0))

        if dist > 0:
            x, y = prev_x, prev_y

    edits.reverse()
    return edits


def _patience_lcs(a: list[str], b: list[str]) -> list[tuple[int, int]]:
    """Patience-diff unique-line matching, falling back to standard LCS."""
    b_pos: dict[str, list[int]] = {}
    for j, line in enumerate(b):
        b_pos.setdefault(line, []).append(j)

    a_uniq: dict[str, list[int]] = {}
    for i, line in enumerate(a):
        indices = b_pos.get(line, [])
        if len(indices) == 1:
            a_uniq.setdefault(line, []).append(i)

    pairs: list[tuple[int, int]] = []
    for line, i_list in a_uniq.items():
        j = b_pos[line][0]
        for i in i_list:
            pairs.append((i, j))

    pairs.sort()

    tails: list[int] = []
    prev: list[tuple[int, int] | None] = []
    for _i, j in pairs:
        lo, hi = 0, len(tails)
        while lo < hi:
            mid = (lo + hi) // 2
            if tails[mid] < j:
                lo = mid + 1
            else:
                hi = mid
        if lo == len(tails):
            tails.append(j)
            prev.append(pairs[lo - 1] if lo > 0 else None)
        else:
            tails[lo] = j
            prev.append(pairs[lo - 1] if lo > 0 else None)

    patience_matches: list[tuple[int, int]] = []
    idx = len(tails) - 1 if tails else -1
    for p in reversed(pairs):
        if idx >= 0 and p[1] == tails[idx]:
            patience_matches.append(p)
            idx -= 1
    patience_matches.reverse()

    if not patience_matches:
        dp = _lcs_matrix(a, b)
        return _backtrack_lcs(dp, a, b, len(a), len(b))

    result: list[tuple[int, int]] = []
    pm_iter = iter(patience_matches)
    pi, pj = next(pm_iter, (-1, -1))
    if pi == -1:
        return result

    if pi > 0 or pj > 0:
        result.extend(_patience_lcs(a[:pi], b[:pj]))

    result.append((pi, pj))

    prev_i, prev_j = pi, pj
    for pi, pj in pm_iter:
        if pi > prev_i + 1 and pj > prev_j + 1:
            result.extend(_patience_lcs(a[prev_i + 1 : pi], b[prev_j + 1 : pj]))
        result.append((pi, pj))
        prev_i, prev_j = pi, pj

    if prev_i + 1 < len(a) and prev_j + 1 < len(b):
        result.extend(_patience_lcs(a[prev_i + 1 :], b[prev_j + 1 :]))

    return result


def _edits_from_lcs(lcs: list[tuple[int, int]], n: int, m: int) -> list[EditOp]:
    """Convert an LCS match list into EditOp instances."""
    ops: list[EditOp] = []
    last_i, last_j = 0, 0
    for i, j in itertools.chain(lcs, [(n, m)]):
        if i > last_i or j > last_j:
            ops.append(
                EditOp(
                    old_start=last_i,
                    old_count=i - last_i,
                    new_start=last_j,
                    new_count=j - last_j,
                )
            )
        elif i > last_i:
            ops.append(
                EditOp(
                    old_start=last_i,
                    old_count=i - last_i,
                    new_start=last_j,
                    new_count=0,
                )
            )
        elif j > last_j:
            ops.append(
                EditOp(
                    old_start=last_i,
                    old_count=0,
                    new_start=last_j,
                    new_count=j - last_j,
                )
            )
        last_i = i + (0 if i >= n else 1)
        last_j = j + (0 if j >= m else 1)

    result: list[EditOp] = []
    for op in ops:
        if op.old_count == 0 and op.new_count == 0:
            continue
        result.append(op)
    return result


# ── public API ───────────────────────────────────────────────────────────────


class DiffEngine:
    """Stateless diff engine: Myers diff, patience diff, 3-way merge,
    patch application, conflict markers, unified-format output."""

    # ── diff algorithms ──────────────────────────────────────────────────

    @staticmethod
    def myers_diff(a: list[str], b: list[str]) -> list[EditOp]:
        """Shortest edit script via Myers diff."""
        return _myers_diff(a, b)

    @staticmethod
    def patience_diff(a: list[str], b: list[str]) -> list[EditOp]:
        """Patience-diff edit script (better for moved/unique lines)."""
        lcs = _patience_lcs(a, b)
        return _edits_from_lcs(lcs, len(a), len(b))

    @staticmethod
    def diff(
        a: list[str],
        b: list[str],
        algorithm: t.Literal["myers", "patience"] = "myers",
    ) -> list[EditOp]:
        """Compute edit operations with the chosen algorithm."""
        if algorithm == "patience":
            return DiffEngine.patience_diff(a, b)
        return DiffEngine.myers_diff(a, b)

    # ── unified format ──────────────────────────────────────────────────

    @staticmethod
    def unified_diff(
        a: list[str],
        b: list[str],
        from_file: str = "a",
        to_file: str = "b",
        context: int = 3,
        algorithm: t.Literal["myers", "patience"] = "myers",
    ) -> str:
        """Produce unified-format diff output as a string."""
        return DiffEngine._format_unified(a, b, from_file, to_file, context, algorithm)

    @staticmethod
    def unified_diff_hunks(
        a: list[str],
        b: list[str],
        context: int = 3,
        algorithm: t.Literal["myers", "patience"] = "myers",
    ) -> list[DiffHunk]:
        """Return structured DiffHunk objects instead of text."""
        ops = DiffEngine.diff(a, b, algorithm)
        lines = DiffEngine._ops_to_hunk_lines(a, b, ops)
        return DiffEngine._group_hunks(lines, context, len(a), len(b))

    # ── 3-way merge ─────────────────────────────────────────────────────

    @staticmethod
    def merge3(
        base: list[str],
        ours: list[str],
        theirs: list[str],
    ) -> MergeResult:
        """Three-way merge of *ours* and *theirs* against a common *base*.

        Returns a MergeResult with the merged lines and any conflicts.
        """
        edits_ours = DiffEngine.myers_diff(base, ours)
        edits_theirs = DiffEngine.myers_diff(base, theirs)
        return DiffEngine._three_way_merge(base, ours, theirs, edits_ours, edits_theirs)

    # ── patch application ───────────────────────────────────────────────

    @staticmethod
    def apply_patch(
        original: list[str],
        patch_text: str,
        fuzz: int = 0,
    ) -> PatchResult:
        """Apply a unified-format patch to *original*.

        Returns a PatchResult with the transformed lines and per-hunk outcome.
        """
        return DiffEngine._apply_unified_patch(original, patch_text, fuzz)

    @staticmethod
    def apply_edits(original: list[str], edits: list[EditOp]) -> list[str]:
        """Directly apply a sequence of EditOp items to *original*."""
        result: list[str] = []
        for op in edits:
            result.extend(original[op.old_start : op.old_start + op.old_count])
        # For a proper apply, we rebuild from old→new using the edit ops.
        return DiffEngine._reconstruct_from_edits(original, edits)

    # ── conflict markers ────────────────────────────────────────────────

    @staticmethod
    def format_conflict(conflict: Conflict) -> str:
        """Format a single Conflict as a standard <<<===>>> block."""
        lines: list[str] = []
        lines.append(f"<<<<<<< ours  (lines {conflict.ours_start}-{conflict.ours_end})")
        lines.extend(conflict.ours_lines)
        lines.append("=======")
        lines.append(f"base  (lines {conflict.ours_start}-{conflict.ours_end})")
        lines.extend(conflict.base_lines)
        lines.append("=======")
        lines.extend(conflict.theirs_lines)
        lines.append(f">>>>>>> theirs  (lines {conflict.theirs_start}-{conflict.theirs_end})")
        return "\n".join(lines)

    # ── internals ───────────────────────────────────────────────────────

    @staticmethod
    def _format_unified(
        a: list[str],
        b: list[str],
        from_file: str,
        to_file: str,
        context: int,
        algorithm: t.Literal["myers", "patience"],
    ) -> str:
        ops = DiffEngine.diff(a, b, algorithm)
        lines = DiffEngine._ops_to_hunk_lines(a, b, ops)
        hunks = DiffEngine._group_hunks(lines, context, len(a), len(b))

        parts: list[str] = [f"--- {from_file}", f"+++ {to_file}"]
        for h in hunks:
            old_range = h.old_count if h.old_count else 1
            new_range = h.new_count if h.new_count else 1
            parts.append(f"@@ -{h.old_start + 1},{old_range} +{h.new_start + 1},{new_range} @@")
            for hl in h.lines:
                prefix = {"context": " ", "add": "+", "remove": "-"}[hl.kind]
                parts.append(f"{prefix}{hl.text}")
        return "\n".join(parts)

    @staticmethod
    def _ops_to_hunk_lines(
        a: list[str],
        b: list[str],
        ops: list[EditOp],
    ) -> list[HunkLine]:
        lines: list[HunkLine] = []
        old_idx, new_idx = 0, 0
        i, j = 0, 0
        for op in ops:
            while old_idx < op.old_start:
                txt = a[old_idx] if old_idx < len(a) else ""
                lines.append(HunkLine("context", txt.rstrip("\n")))
                old_idx += 1
                new_idx += 1
                i += 1
                j += 1

            for _ in range(op.old_count):
                txt = a[old_idx] if old_idx < len(a) else ""
                lines.append(HunkLine("remove", txt.rstrip("\n")))
                old_idx += 1
                i += 1
            for _ in range(op.new_count):
                txt = b[new_idx] if new_idx < len(b) else ""
                lines.append(HunkLine("add", txt.rstrip("\n")))
                new_idx += 1
                j += 1

        while old_idx < len(a) or new_idx < len(b):
            if old_idx < len(a) and new_idx < len(b):
                lines.append(HunkLine("context", a[old_idx].rstrip("\n")))
                old_idx += 1
                new_idx += 1
            elif old_idx < len(a):
                lines.append(HunkLine("remove", a[old_idx].rstrip("\n")))
                old_idx += 1
            else:
                lines.append(HunkLine("add", b[new_idx].rstrip("\n")))
                new_idx += 1

        return lines

    @staticmethod
    def _group_hunks(
        lines: list[HunkLine],
        context: int,
        old_len: int,
        new_len: int,
    ) -> list[DiffHunk]:
        if not lines:
            return []

        changed = [i for i, hl in enumerate(lines) if hl.kind != "context"]
        if not changed:
            return []

        hunks: list[DiffHunk] = []

        first_chg = changed[0] if changed else 0
        max(0, first_chg - context)

        idx = 0
        while idx < len(changed):
            chg_start = changed[idx]
            while idx < len(changed) - 1 and changed[idx + 1] - changed[idx] <= context * 2 + 1:
                idx += 1
            chg_end = changed[idx]

            seg_start = max(0, chg_start - context)
            seg_end = min(len(lines), chg_end + context + 1)

            hunk_lines = lines[seg_start:seg_end]
            old_cnt = sum(1 for hl in hunk_lines if hl.kind in ("context", "remove"))
            new_cnt = sum(1 for hl in hunk_lines if hl.kind in ("context", "add"))

            old_start_line = seg_start
            new_start_line = seg_start

            hunks.append(
                DiffHunk(
                    old_start=old_start_line,
                    old_count=old_cnt,
                    new_start=new_start_line,
                    new_count=new_cnt,
                    lines=hunk_lines,
                )
            )
            idx += 1

        return hunks

    @staticmethod
    def _three_way_merge(
        base: list[str],
        ours: list[str],
        theirs: list[str],
        edits_ours: list[EditOp],
        edits_theirs: list[EditOp],
    ) -> MergeResult:
        merged: list[str] = []
        conflicts: list[Conflict] = []

        lcs_ours = _backtrack_lcs(_lcs_matrix(base, ours), base, ours, len(base), len(ours))
        lcs_theirs = _backtrack_lcs(_lcs_matrix(base, theirs), base, theirs, len(base), len(theirs))

        ours_map: dict[int, int] = {bi: oi for bi, oi in lcs_ours}
        theirs_map: dict[int, int] = {bi: ti for bi, ti in lcs_theirs}

        bi = 0
        oi = 0
        ti = 0

        while bi < len(base):
            if bi in ours_map and bi in theirs_map:
                merged.append(base[bi])
                oi = ours_map[bi] + 1
                ti = theirs_map[bi] + 1
                bi += 1
            elif bi in ours_map:
                ours_map[bi]
                start_ti = ti
                while ti < len(theirs) and (bi not in theirs_map or theirs_map[bi] != ti):
                    ti += 1
                if ti < len(theirs):
                    conflict_start = len(merged)
                    c_ours: list[str] = []
                    c_theirs: list[str] = theirs[start_ti:ti]
                    conflicts.append(
                        Conflict(
                            ours_start=conflict_start,
                            ours_end=conflict_start + len(c_ours),
                            theirs_start=conflict_start,
                            theirs_end=conflict_start + len(c_theirs),
                            ours_lines=c_ours,
                            theirs_lines=c_theirs,
                        )
                    )
                    merged.extend(c_theirs)
                ti = theirs_map.get(bi, ti)
                merged.append(base[bi])
                oi = ours_map[bi] + 1
                if bi in theirs_map:
                    ti = theirs_map[bi] + 1
                bi += 1
            elif bi in theirs_map:
                theirs_map[bi]
                start_oi = oi
                while oi < len(ours) and (bi not in ours_map or ours_map[bi] != oi):
                    oi += 1
                if oi < len(ours):
                    conflict_start = len(merged)
                    c_ours = ours[start_oi:oi]
                    c_theirs = []
                    conflicts.append(
                        Conflict(
                            ours_start=conflict_start,
                            ours_end=conflict_start + len(c_ours),
                            theirs_start=conflict_start,
                            theirs_end=conflict_start + len(c_theirs),
                            ours_lines=c_ours,
                            theirs_lines=c_theirs,
                        )
                    )
                    merged.extend(c_ours)
                oi = ours_map.get(bi, oi)
                merged.append(base[bi])
                ti = theirs_map[bi] + 1
                if bi in ours_map:
                    oi = ours_map[bi] + 1
                bi += 1
            else:
                region_ours_start = oi
                region_base_start = bi
                next_bi = bi
                while next_bi < len(base) and next_bi not in ours_map and next_bi not in theirs_map:
                    next_bi += 1
                region_ours_end = (
                    ours_map.get(next_bi, len(ours)) if next_bi < len(base) and next_bi in ours_map else len(ours)
                )
                region_theirs_end = (
                    theirs_map.get(next_bi, len(theirs))
                    if next_bi < len(base) and next_bi in theirs_map
                    else len(theirs)
                )
                region_theirs_start = ti

                ours_chunk = ours[region_ours_start:region_ours_end]
                theirs_chunk = theirs[region_theirs_start:region_theirs_end]

                if ours_chunk == theirs_chunk:
                    merged.extend(ours_chunk)
                    oi = region_ours_end
                    ti = region_theirs_end
                    bi = next_bi
                else:
                    conflict_start = len(merged)
                    if not ours_chunk and not theirs_chunk:
                        pass
                    else:
                        merged.extend(ours_chunk)
                        conflicts.append(
                            Conflict(
                                ours_start=conflict_start,
                                ours_end=conflict_start + len(ours_chunk),
                                theirs_start=conflict_start,
                                theirs_end=conflict_start + len(theirs_chunk),
                                base_lines=base[region_base_start:next_bi],
                                ours_lines=ours_chunk,
                                theirs_lines=theirs_chunk,
                            )
                        )
                    oi = region_ours_end
                    ti = region_theirs_end
                    bi = next_bi

        while oi < len(ours):
            merged.append(ours[oi])
            oi += 1
        while ti < len(theirs):
            merged.append(theirs[ti])
            ti += 1

        return MergeResult(merged=merged, conflicts=conflicts, success=len(conflicts) == 0)

    @staticmethod
    def _reconstruct_from_edits(
        original: list[str],
        edits: list[EditOp],
    ) -> list[str]:
        result: list[str] = []
        old_idx = 0
        for op in edits:
            while old_idx < op.old_start and old_idx < len(original):
                result.append(original[old_idx])
                old_idx += 1
            old_idx += op.old_count
        while old_idx < len(original):
            result.append(original[old_idx])
            old_idx += 1
        return result

    @staticmethod
    def _parse_unified_patch(patch_text: str) -> list[DiffHunk]:
        lines = patch_text.splitlines()
        hunks: list[DiffHunk] = []
        current: DiffHunk | None = None
        for line in lines:
            if line.startswith("@@"):
                parts = line.split(" ")
                if len(parts) >= 3:
                    old_part = parts[1][1:]
                    new_part = parts[2][1:]
                    old_comma = old_part.split(",")
                    new_comma = new_part.split(",")
                    old_start = int(old_comma[0]) - 1
                    old_count = int(old_comma[1]) if len(old_comma) > 1 else 1
                    new_start = int(new_comma[0]) - 1
                    new_count = int(new_comma[1]) if len(new_comma) > 1 else 1
                    current = DiffHunk(old_start, old_count, new_start, new_count)
                    hunks.append(current)
            elif current is not None:
                if line.startswith("+"):
                    current.lines.append(HunkLine("add", line[1:]))
                elif line.startswith("-"):
                    current.lines.append(HunkLine("remove", line[1:]))
                elif line.startswith(" "):
                    current.lines.append(HunkLine("context", line[1:]))
        return hunks

    @staticmethod
    def _apply_unified_patch(
        original: list[str],
        patch_text: str,
        fuzz: int,
    ) -> PatchResult:
        hunks = DiffEngine._parse_unified_patch(patch_text)
        result = list(original)
        succeeded = 0
        failed = 0
        rejects: list[tuple[int, str]] = []

        offset = 0
        for idx, hunk in enumerate(hunks):
            applied = False
            search_start = hunk.old_start + offset
            search_end = min(search_start + len(original) + fuzz, len(result))

            for pos in range(search_start, search_end):
                match = True
                ctx_idx = pos
                for hl in hunk.lines:
                    if hl.kind == "context" or hl.kind == "remove":
                        if ctx_idx >= len(result) or result[ctx_idx] != hl.text:
                            match = False
                            break
                        ctx_idx += 1
                if match:
                    inp = pos
                    repl: list[str] = []
                    for hl in hunk.lines:
                        if hl.kind == "context":
                            repl.append(result[inp])
                            inp += 1
                        elif hl.kind == "add":
                            repl.append(hl.text)
                        elif hl.kind == "remove":
                            inp += 1
                    result[pos : pos + sum(1 for hl in hunk.lines if hl.kind != "add")] = repl
                    applied = True
                    break

            if applied:
                succeeded += 1
            else:
                failed += 1
                rejects.append((idx, f"hunk {idx + 1} failed at line {hunk.old_start + 1}"))

        return PatchResult(result, succeeded, failed, rejects)
