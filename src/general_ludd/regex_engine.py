"""Regex engine with compilation, matching, groups, and safety analysis.

Built on Python ``re`` for correctness but adds safety analysis that warns or
aborts on patterns known to cause exponential backtracking.
"""

from __future__ import annotations

import re
import typing as t

__all__ = [
    "BacktrackingDanger",
    "CatastrophicBacktrackingError",
    "CompiledPattern",
    "MatchResult",
    "RegexEngine",
    "RegexEngineError",
]


class RegexEngineError(Exception):
    """Base exception for the regex engine."""


class CatastrophicBacktrackingError(RegexEngineError):
    """Raised when a pattern contains a likely catastrophic-backtracking construct."""


class BacktrackingDanger(t.NamedTuple):
    """Describes a dangerous construct found in a pattern."""

    construct: str
    location: int
    reason: str


class MatchResult:
    """Result of a successful regex match."""

    __slots__ = (
        "_end",
        "_groupdict",
        "_groups",
        "_match",
        "_span",
        "_start",
        "_string",
    )

    def __init__(self, match: re.Match[str] | None, string: str) -> None:
        """Capture a native match and the searched input string."""
        self._match = match
        self._string = string
        if match is not None:
            self._groups: tuple[str | None, ...] = match.groups()
            self._groupdict: dict[str, str | None] = match.groupdict()
            self._span: tuple[int, int] = match.span()
            self._start: int = match.start()
            self._end: int = match.end()
        else:
            self._groups = ()
            self._groupdict = {}
            self._span = (-1, -1)
            self._start = -1
            self._end = -1

    @property
    def matched(self) -> bool:
        """Return whether the underlying regular expression matched."""
        return self._match is not None

    @property
    def string(self) -> str:
        """Return the original searched string."""
        return self._string

    @property
    def groups(self) -> tuple[str | None, ...]:
        """Return all captured positional groups."""
        return self._groups

    def group(self, index: int | str = 0) -> str | None:
        """Return one captured group, or ``None`` when unmatched."""
        if self._match is None:
            return None
        return self._match.group(index)

    @property
    def groupdict(self) -> dict[str, str | None]:
        """Return captured named groups."""
        return self._groupdict

    @property
    def span(self) -> tuple[int, int]:
        """Return the matched half-open span, or ``(-1, -1)``."""
        return self._span

    @property
    def start(self) -> int:
        """Return the start offset, or ``-1`` when unmatched."""
        return self._start

    @property
    def end(self) -> int:
        """Return the end offset, or ``-1`` when unmatched."""
        return self._end

    def __bool__(self) -> bool:
        """Treat a result as true exactly when it matched."""
        return self.matched

    def __repr__(self) -> str:
        """Return a bounded diagnostic representation."""
        status = "matched" if self.matched else "not matched"
        return f"MatchResult({status}, span={self._span})"


class CompiledPattern:
    """A compiled regular expression that may be reused for multiple matches."""

    __slots__ = ("_dangers", "_flags", "_pattern", "_re", "_source")

    def __init__(
        self,
        pattern: str,
        flags: re.RegexFlag = re.NOFLAG,
    ) -> None:
        """Compile ``pattern`` and retain its static safety findings."""
        self._source = pattern
        self._flags = flags
        self._dangers = _analyze_backtracking_dangers(pattern)
        self._pattern = pattern
        self._re = re.compile(pattern, flags)

    @property
    def source(self) -> str:
        """Return the original regular-expression source."""
        return self._source

    @property
    def dangers(self) -> list[BacktrackingDanger]:
        """Return a copy of detected backtracking dangers."""
        return list(self._dangers)

    @property
    def has_dangers(self) -> bool:
        """Return whether static analysis found a danger."""
        return len(self._dangers) > 0

    def match(self, text: str, pos: int | None = None, endpos: int | None = None) -> MatchResult:
        """Match from the beginning of the requested input window."""
        if pos is not None or endpos is not None:
            m = self._re.match(text, pos or 0, endpos or len(text))
        else:
            m = self._re.match(text)
        return MatchResult(m, text)

    def search(self, text: str, pos: int | None = None, endpos: int | None = None) -> MatchResult:
        """Search within the requested input window."""
        if pos is not None or endpos is not None:
            m = self._re.search(text, pos or 0, endpos or len(text))
        else:
            m = self._re.search(text)
        return MatchResult(m, text)

    def findall(self, text: str, pos: int = 0, endpos: int | None = None) -> list[t.Any]:
        """Return every non-overlapping match in the input window."""
        return self._re.findall(text, pos, endpos or len(text))

    def finditer(self, text: str, pos: int = 0, endpos: int | None = None) -> t.Iterator[MatchResult]:
        """Yield wrapped matches from the input window."""
        for m in self._re.finditer(text, pos, endpos or len(text)):
            yield MatchResult(m, text)

    def split(self, text: str, maxsplit: int = 0) -> list[str]:
        """Split text at pattern matches."""
        return self._re.split(text, maxsplit)

    def sub(self, repl: str | t.Callable[[re.Match[str]], str], text: str, count: int = 0) -> str:
        """Replace pattern matches and return the resulting string."""
        return self._re.sub(repl, text, count)

    def subn(self, repl: str | t.Callable[[re.Match[str]], str], text: str, count: int = 0) -> tuple[str, int]:
        """Replace matches and return the string and replacement count."""
        return self._re.subn(repl, text, count)

    @property
    def groups(self) -> int:
        """Return the number of capturing groups."""
        return self._re.groups

    @property
    def groupindex(self) -> t.Mapping[str, int]:
        """Return the named-group index mapping."""
        return self._re.groupindex

    def __repr__(self) -> str:
        """Return a diagnostic representation without matched input data."""
        return f"CompiledPattern({self._source!r}, flags={self._flags!r})"


class RegexEngine:
    r"""Regex engine with catastrophic-backtracking detection.

    Usage::

        engine = RegexEngine()
        cp = engine.compile(r"(?P<word>\\w+)")
        result = cp.match("hello world")
        assert result.matched
    """

    __slots__ = ("_strict", "_timeout_ms")

    def __init__(self, strict: bool = False, timeout_ms: int = 5000) -> None:
        """Configure strict static rejection and the reserved timeout value."""
        self._strict = strict
        self._timeout_ms = timeout_ms

    def compile(
        self,
        pattern: str,
        flags: re.RegexFlag = re.NOFLAG,
    ) -> CompiledPattern:
        """Compile a pattern and optionally reject static safety findings."""
        cp = CompiledPattern(pattern, flags)
        if self._strict and cp.has_dangers:
            raise CatastrophicBacktrackingError(
                f"Pattern {pattern!r} contains dangerous constructs: "
                + "; ".join(f"{d.construct} at pos {d.location}: {d.reason}" for d in cp.dangers)
            )
        return cp

    def match(self, pattern: str, text: str, flags: re.RegexFlag = re.NOFLAG) -> MatchResult:
        """Compile and match a pattern from the beginning of text."""
        return self.compile(pattern, flags).match(text)

    def search(self, pattern: str, text: str, flags: re.RegexFlag = re.NOFLAG) -> MatchResult:
        """Compile and search for a pattern within text."""
        return self.compile(pattern, flags).search(text)

    def is_safe(self, pattern: str) -> bool:
        """Return whether static analysis finds no backtracking danger."""
        cp = self.compile(pattern)
        return not cp.has_dangers

    def check_pattern(self, pattern: str) -> list[BacktrackingDanger]:
        """Return all static backtracking findings for a pattern."""
        return self.compile(pattern).dangers


# ---------------------------------------------------------------------------
# Catastrophic-backtracking analyser
# ---------------------------------------------------------------------------

# Known-dangerous constructs that cause exponential backtracking:
# 1. Nested quantifiers: (a+)+, (a*)*, (a+)*
# 2. Alternating groups where both branches can match the same prefix:
#    (a|a)+, (ab|a)+
# 3. Repeated groups with overlapping suffixes: (.*a){2,}


def _analyze_backtracking_dangers(pattern: str) -> list[BacktrackingDanger]:
    dangers: list[BacktrackingDanger] = []

    _check_nested_quantifiers(pattern, dangers)
    _check_alternation_overlap(pattern, dangers)

    return dangers


def _check_nested_quantifiers(pattern: str, dangers: list[BacktrackingDanger]) -> None:
    idx = 0
    while idx < len(pattern):
        ch = pattern[idx]
        if ch == "(":
            idx = _scan_group(pattern, idx, dangers)
        elif ch == "[":
            idx = _skip_char_class(pattern, idx)
        elif ch == "\\":
            idx += 2
        else:
            idx += 1


def _scan_group(pattern: str, start: int, dangers: list[BacktrackingDanger]) -> int:
    i = start + 1
    depth = 1
    group_content: list[str] = []

    if i < len(pattern) and pattern[i] == "?":
        group_content.append("?")
        i += 1
        if i < len(pattern) and pattern[i] in ":!=<!":
            group_content.append(pattern[i])
            i += 1
        elif i < len(pattern) and pattern[i] == "P":
            pts = i
            while i < len(pattern) and pattern[i] != ">":
                i += 1
            group_content.append(pattern[pts : i + 1])

    while i < len(pattern) and depth > 0:
        ch = pattern[i]
        if ch == "(":
            depth += 1
            group_content.append(ch)
        elif ch == ")":
            depth -= 1
            if depth == 0:
                break
            group_content.append(ch)
        elif ch == "[":
            i = _skip_char_class(pattern, i)
            group_content.append("[...]")
            continue
        elif ch == "\\":
            group_content.append(pattern[i : i + 2])
            i += 2
            continue
        else:
            group_content.append(ch)
        i += 1

    if depth > 0:
        return i

    group_str = "".join(group_content)

    if i + 1 < len(pattern) and pattern[i + 1] in "*+":
        outer_quantifier = pattern[i + 1]
        if _contains_quantified_branch(group_str):
            reasons = [
                r"(a+)+ is the canonical exponential-backtracking pattern",
                r"(a*)* matches the empty string infinitely many ways",
            ]
            reason = reasons[0] if "+" in group_str else (reasons[1] if "*" in group_str else "nested quantifier")
            dangers.append(
                BacktrackingDanger(
                    construct=f"(...){outer_quantifier}",
                    location=start,
                    reason=f"{reason}: group contains a quantified branch",
                )
            )

    return i + 1


def _contains_quantified_branch(group_content: str) -> bool:
    i = 0
    while i < len(group_content):
        ch = group_content[i]
        if ch in "?:" and i + 1 < len(group_content):
            i += 1
            continue
        if ch == "(":
            j = i + 1
            depth = 1
            while j < len(group_content) and depth > 0:
                if group_content[j] == "(":
                    depth += 1
                elif group_content[j] == ")":
                    depth -= 1
                j += 1
            inner_end = j - 1 if depth == 0 else j
            if _contains_quantified_branch(group_content[i + 1 : inner_end]):
                return True
            if j < len(group_content) and group_content[j] in "*+":
                return True
            i = j
        elif ch == "[":
            j = i + 1
            while j < len(group_content) and group_content[j] != "]":
                if group_content[j] == "\\":
                    j += 2
                else:
                    j += 1
            if j + 1 < len(group_content) and group_content[j + 1] in "*+":
                return True
            i = j + 1
        elif ch in "*+" and i > 0:
            prev = group_content[i - 1]
            if prev not in "\\":
                return True
            i += 1
        else:
            i += 1
    return False


def _check_alternation_overlap(pattern: str, dangers: list[BacktrackingDanger]) -> None:
    i = 0
    while i < len(pattern):
        ch = pattern[i]
        if ch == "(":
            depth = 1
            j = i + 1
            if j < len(pattern) and pattern[j] == "?":
                _skip_group(pattern, i)
                i = _find_close(pattern, i)
                continue
            pipe_positions: list[int] = []
            while j < len(pattern) and depth > 0:
                if pattern[j] == "(":
                    depth += 1
                elif pattern[j] == ")":
                    depth -= 1
                    if depth > 0:
                        j += 1
                        continue
                    break
                elif pattern[j] == "[":
                    j = _skip_char_class(pattern, j)
                    continue
                elif pattern[j] == "|" and depth == 1:
                    pipe_positions.append(j)
                j += 1
            if not pipe_positions:
                i = j + 1 if j < len(pattern) else j
                continue
            if j < len(pattern) and j + 1 < len(pattern) and pattern[j + 1] in "*+":
                _check_pipe_overlaps(pattern, i, pipe_positions, j, dangers)
            i = j + 1 if j < len(pattern) else j
        elif ch == "[":
            i = _skip_char_class(pattern, i)
        elif ch == "\\":
            i += 2
        else:
            i += 1


def _skip_char_class(pattern: str, start: int) -> int:
    i = start + 1
    if i < len(pattern) and pattern[i] == "^":
        i += 1
    if i < len(pattern) and pattern[i] == "]":
        i += 1
    while i < len(pattern) and pattern[i] != "]":
        if pattern[i] == "\\":
            i += 2
        else:
            i += 1
    return i + 1


def _skip_group(pattern: str, start: int) -> int:
    i = start + 1
    depth = 1
    while i < len(pattern) and depth > 0:
        ch = pattern[i]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == "[":
            i = _skip_char_class(pattern, i)
            continue
        elif ch == "\\":
            i += 2
            continue
        i += 1
    return i


def _find_close(pattern: str, start: int) -> int:
    i = start + 1
    depth = 1
    while i < len(pattern) and depth > 0:
        ch = pattern[i]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == "[":
            i = _skip_char_class(pattern, i)
            continue
        elif ch == "\\":
            i += 2
            continue
        i += 1
    return i


def _check_pipe_overlaps(
    pattern: str,
    group_start: int,
    pipes: list[int],
    group_close: int,
    dangers: list[BacktrackingDanger],
) -> None:
    branches: list[str] = []
    start = group_start + 1
    for p in pipes:
        branches.append(pattern[start:p])
        start = p + 1
    branches.append(pattern[start:group_close])

    for a_idx, a in enumerate(branches):
        for b_idx, b in enumerate(branches):
            if a_idx >= b_idx:
                continue
            if _overlaps(a, b):
                dangers.append(
                    BacktrackingDanger(
                        construct=f"({a}|{b})",
                        location=group_start,
                        reason="alternating branches with overlapping prefixes can cause exponential backtracking",
                    )
                )
                return


def _overlaps(a: str, b: str) -> bool:
    min_len = min(len(a), len(b))
    if min_len == 0:
        return False
    for i in range(1, min_len + 1):
        if a[:i] == b[:i]:
            return True
    return bool(a.startswith(b) or b.startswith(a))
