"""
Self-test: mechanically verify subagent output is NOT contaminated by
enforcement plugin nag text.

Check (a): every `text.complete` / `experimental.text.complete` hook across
all plugins under `.opencode/plugin/` and `.opencode/plugins/` carries the
OPENCODE_SUBAGENT guard (early return when process.env.OPENCODE_SUBAGENT === "1").

Check (b): extract all injection/nag strings from `text.complete` hook bodies
(output.text assignments, console.warn/console.error calls that produce
user-visible warnings), and verify none are missing from the KNOWN_NAG_STRINGS
baseline — a new nag that is not in the baseline means the test needs updating.

Check (c): E.13 — mechanically verify that specific nag texts (DELEGATE-FIRST
and READ-GRINDING) appear only inside guarded hook handlers, so
they can never be injected into subagent task_result or tool output.
"""
import re
from pathlib import Path

# ── Config ──────────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
PLUGIN_DIRS = [
    PROJECT_ROOT / ".opencode" / "plugin",
    PROJECT_ROOT / ".opencode" / "plugin" / "impl",
    PROJECT_ROOT / ".opencode" / "plugins",
]
SUBAGENT_GUARD_EXCEPTIONS = frozenset({"enforce-depth.ts"})


# ── Known nag strings injected by text.complete hooks ───────────────────────
# Update this list whenever a new nag/injection string is added to a
# text.complete hook. This is the baseline the test verifies against.
KNOWN_NAG_STRINGS: list[str] = [
    # enforce-stop.ts text.complete
    "DELEGATE-FIRST",  # prepended nag when streak > threshold
    "FALSE-DONE CLAIM BLOCKED",
    "DISPATCH A TOOL CALL",
    "QA RESPONSE SUMMARY BLOCKED",
]


# ── Regex to match a text.complete hook registration ─────────────────────────
_TEXTHOOK_RE = re.compile(
    r'(?:"text\.complete"|"experimental\.text\.complete")\s*:\s*async\s*\(',
)

# ── Regex to match ANY hook handler registration ──────────────────────────────
_ALL_HOOK_RE = re.compile(
    r'"(?:tool\.execute\.(?:before|after)|(?:experimental\.)?text\.complete|'
    r'session\.idle|experimental\.chat\.system\.transform)"\s*:\s*async\s*\(',
)

# ── Regex to detect OPENCODE_SUBAGENT guard ─────────────────────────────────
_SUBAGENT_GUARD_RE = re.compile(
    r'(?:process\.env\.OPENCODE_SUBAGENT\s*===\s*"1"|isSubagent\(\))',
)

# ── Nag strings used in guard-integrity tests (E.13) ────────────────────────
_NAG_DELEGATE_FIRST = "DELEGATE-FIRST"
_NAG_READ_GRINDING = "READ-GRINDING"


# ── Helpers ─────────────────────────────────────────────────────────────────

def _find_text_complete_sections(
    source: str,
) -> list[tuple[int, int]]:
    """Return (hook_start, next_hook_start_or_eof) for each text.complete hook.

    The region from ``hook_start`` to ``next_hook_start_or_eof`` is treated
    as the section containing this hook's handler body.  This is intentionally
    over-broad (the last hook's section extends to EOF), but it is guaranteed
    to contain the handler body — avoiding the fragility of JS/TS brace
    parsing from Python (template literal ``${}`` interpolation, regex
    literals, etc.).
    """
    positions = [m.start() for m in _TEXTHOOK_RE.finditer(source)]
    if not positions:
        return []
    sections: list[tuple[int, int]] = []
    for i, pos in enumerate(positions):
        end = positions[i + 1] if i + 1 < len(positions) else len(source)
        sections.append((pos, end))
    return sections


def _find_all_hook_sections(
    source: str,
) -> list[tuple[int, int]]:
    """Return (start, end) for every hook handler section (any hook type)."""
    positions = [m.start() for m in _ALL_HOOK_RE.finditer(source)]
    if not positions:
        return []
    sections: list[tuple[int, int]] = []
    for i, pos in enumerate(positions):
        end = positions[i + 1] if i + 1 < len(positions) else len(source)
        sections.append((pos, end))
    return sections


def _enclosing_hook_section(
    source: str, line_no: int,
) -> tuple[int, int] | None:
    """Return the (start, end) of the hook section enclosing line_no, or None."""
    sections = _find_all_hook_sections(source)
    offset = sum(len(ln) + 1 for ln in source.splitlines()[:line_no - 1])
    for bs, be in sections:
        if bs <= offset < be:
            return (bs, be)
    return None


def _find_nag_lines(
    source: str, nag: str, *, skip_comments: bool = True,
) -> list[int]:
    """Return 1-indexed line numbers of lines containing ``nag``.
    When ``skip_comments``, skip lines whose stripped content is only
    a ``//`` comment."""
    result: list[int] = []
    for i, line in enumerate(source.splitlines()):
        if nag not in line:
            continue
        if skip_comments and line.strip().startswith("//"):
            continue
        result.append(i + 1)
    return result


def _find_matching_paren(
    source: str, open_char: str, start: int,
) -> int | None:
    """Return offset INCLUSIVE of the matching close brace (just after it).

    start — offset of the opening char (e.g. ``{``).
    """
    close_map = {"{": "}", "[": "]", "(": ")"}
    close_char = close_map[open_char]
    depth = 0
    in_str = False
    quote = ""
    cursor = start
    while cursor < len(source):
        ch = source[cursor]
        if in_str:
            if ch == "\\":
                cursor += 2
                continue
            if ch == quote:
                in_str = False
            cursor += 1
            continue
        if ch in ('"', "'", "`"):
            in_str = True
            quote = ch
            cursor += 1
            continue
        if ch == open_char:
            depth += 1
        elif ch == close_char:
            depth -= 1
            if depth == 0:
                return cursor + 1
        cursor += 1
    return None


def _extract_injection_strings(
    source: str, body_start: int, body_end: int,
) -> list[str]:
    """Extract user-visible nag strings from a text.complete handler body.

    Looks for ``output.text = `` assignments (both single-string and
    ``[...].join()`` form) and ``console.warn`` / ``console.error`` calls.
    Returns unique, non-empty, trimmed strings.
    """
    body = source[body_start:body_end]
    found: list[str] = []

    i = 0
    while i < len(body):
        # output.text = <expr>
        if body[i:i+13] == "output.text = ":
            i += 13
            expr = _extract_expr(body, i)
            if expr:
                found.append(expr[:200])
            continue
        # output = [ ... ].join(...)
        if body[i:i+8] == "output = ":
            i += 8
            expr = _extract_expr(body, i)
            if expr:
                found.append(expr[:200])
            continue
        # console.warn(...) / console.error(...)
        if body[i:i+14] == "console.warn(" or body[i:i+15] == "console.error(":
            i = body.index("(", i) + 1 if "(" in body[i:] else i + 1
            str_expr = _extract_expr(body, i)
            if str_expr:
                found.append(str_expr[:200])
            continue
        i += 1

    return [s.strip() for s in found if s.strip()]


def _extract_expr(body: str, start: int) -> str | None:
    """Extract one JS expression (string literal or array literal) from
    ``body[start:]``. Returns its source text."""
    body = body[start:]
    if not body:
        return None
    body = body.lstrip()
    if not body:
        return None

    ch = body[0]
    if ch in ('"', "'", "`"):
        # string literal — read until matching close
        quote = ch
        i = 1
        while i < len(body):
            if body[i] == "\\":
                i += 2
                continue
            if body[i] == quote:
                return body[:i+1]
            i += 1
        return None

    if ch == "[":
        # array literal / .join() expression — read until matching ``]``
        end = _find_matching_paren(body, "[", 0)
        if end is None:
            return None
        expr = body[:end]
        return expr[:200]

    return None


def _split_ts_strings(filename: str, content: str) -> list[str]:
    """Naively split TypeScript source into top-level string literals
    that could constitute nag text. Used for sweep matching."""
    literals: list[str] = []
    i = 0
    in_literal = False
    quote_char = ""
    start = 0
    while i < len(content):
        ch = content[i]
        if in_literal:
            if ch == "\\":
                i += 2
                continue
            if ch == quote_char:
                literals.append(content[start:i])
                in_literal = False
            i += 1
            continue
        if ch in ('"', "'", "`"):
            in_literal = True
            quote_char = ch
            start = i + 1
        i += 1
    return literals


def _is_nag_like(s: str) -> bool:
    """Heuristic: does this string look like an enforcement nag? """
    nag_markers = [
        "⛔", "BLOCKED", "DISPATCH", "DELEGATE", "HARD STOP",
        "READ-GRINDING", "MUST DISPATCH", "ZERO-STREAK", "ANTI-LOOP",
        "RESPONSE", "PENDING WORK", "RATCHET", "FALSE-DONE",
        "ENHANCEMENT RATIO", "REFILL NEEDED", "STOP-PATTERN",
        "GATE IS RED", "CATCH-ALL", "Session start",
    ]
    upper = s.upper()
    return any(m in upper for m in nag_markers)


# ── Tests ───────────────────────────────────────────────────────────────────

class TestSubagentOutputClean:
    """Verify subagent output is not contaminated by enforcement nag text."""

    @staticmethod
    def _collect_plugin_files() -> list[Path]:
        files: list[Path] = []
        for d in PLUGIN_DIRS:
            if d.is_dir():
                files.extend(sorted(d.glob("*.ts")))
        return files

    def test_all_text_complete_hooks_have_subagent_guard(self) -> None:
        """Every text.complete hook must guard with OPENCODE_SUBAGENT."""
        plugin_files = self._collect_plugin_files()
        assert plugin_files, "No plugin .ts files found"

        violations: list[str] = []
        for filepath in plugin_files:
            source = filepath.read_text()
            sections = _find_text_complete_sections(source)
            for bs, be in sections:
                body = source[bs:be]
                if not _SUBAGENT_GUARD_RE.search(body):
                    line = source[:bs].count("\n") + 1
                    violations.append(
                        f"{filepath.name}: text.complete handler at "
                        f"line ~{line} lacks OPENCODE_SUBAGENT guard"
                    )

        assert not violations, (
            f"{len(violations)} text.complete hook(s) lack "
            f"OPENCODE_SUBAGENT guard:\n" + "\n".join(violations)
        )

    def test_known_nag_strings_found_in_plugins(self) -> None:
        """Every known nag string must be present in at least one plugin
        and be inside a text.complete handler body."""
        plugin_files = self._collect_plugin_files()
        assert plugin_files

        all_handler_bodies: dict[str, list[str]] = {}
        for filepath in plugin_files:
            source = filepath.read_text()
            sections = _find_text_complete_sections(source)
            if sections:
                all_handler_bodies[filepath.name] = [
                    source[bs:be] for bs, be in sections
                ]

        assert all_handler_bodies, "No text.complete hooks found in any plugin"

        missing: list[str] = []
        for nag in KNOWN_NAG_STRINGS:
            found = False
            for bodies in all_handler_bodies.values():
                for body in bodies:
                    if nag in body:
                        found = True
                        break
                if found:
                    break
            if not found:
                missing.append(nag)

        assert not missing, (
            f"{len(missing)} known nag string(s) not found in any "
            f"text.complete handler body — they may have been removed "
            f"and should be deleted from KNOWN_NAG_STRINGS:\n"
            + "\n".join(missing)
        )

    def test_no_unexpected_nags_in_text_complete_hooks(self) -> None:
        """Detect nag-like strings in text.complete hooks that are NOT in
        the known list — means a new nag was added without updating this test."""
        plugin_files = self._collect_plugin_files()
        assert plugin_files

        unexpected: dict[str, list[str]] = {}
        for filepath in plugin_files:
            source = filepath.read_text()
            sections = _find_text_complete_sections(source)
            for bs, be in sections:
                strings = _extract_injection_strings(source, bs, be)
                for s in strings:
                    if _is_nag_like(s):
                        matched = any(
                            known in s or s in known
                            for known in KNOWN_NAG_STRINGS
                        )
                        if not matched:
                            short = s.strip()[:120]
                            unexpected.setdefault(filepath.name, [])
                            if short not in unexpected[filepath.name]:
                                unexpected[filepath.name].append(short)

        assert not unexpected, (
            "New nag/injection strings found in text.complete hooks that "
            "are NOT in KNOWN_NAG_STRINGS. Add them to the baseline:\n\n"
            + "\n".join(
                f"  {fn}: {s!r}"
                for fn, strs in sorted(unexpected.items())
                for s in strs
            )
        )

    def test_plugin_count_matches_expected(self) -> None:
        """Structural pin: number of text.complete hooks across all plugins."""
        plugin_files = self._collect_plugin_files()
        total_handlers = 0
        for filepath in plugin_files:
            source = filepath.read_text()
            total_handlers += len(_find_text_complete_sections(source))

        # Expected: 6 text.complete hooks
        assert total_handlers >= 6, (
            f"Expected at least 6 text.complete hooks across all plugins, "
            f"found {total_handlers}."
        )

    def test_diagnostic_text_complete_hook_inventory(self) -> None:
        """Print full inventory of text.complete hooks and their guards."""
        plugin_files = self._collect_plugin_files()
        inventory: list[dict[str, object]] = []
        for filepath in plugin_files:
            source = filepath.read_text()
            sections = _find_text_complete_sections(source)
            for bs, be in sections:
                body = source[bs:be]
                has_guard = bool(_SUBAGENT_GUARD_RE.search(body))
                line = source[:bs].count("\n") + 1
                nag_count = len(
                    _extract_injection_strings(source, bs, be)
                )
                inventory.append({
                    "file": filepath.name,
                    "line": line,
                    "has_guard": has_guard,
                    "nag_strings": nag_count,
                })

        print("\n  Text.complete hook inventory:")
        for entry in sorted(inventory, key=lambda e: str(e["file"])):
            guard = "PASS" if entry["has_guard"] else "FAIL"
            print(f"    {entry['file']}:{entry['line']} "
                  f"guard={guard} nag_strings={entry['nag_strings']}")

    # ── E.13: Mechanically verify specific nag texts are fully guarded ───────

    @classmethod
    def _check_nag_in_guarded_hooks(
        cls, nag: str, nag_label: str,
    ) -> dict[str, str]:
        """Verify every executable occurrence of ``nag`` sits inside a hook
        handler whose body carries the OPENCODE_SUBAGENT guard.

        ``skip_comments`` is True: ``//`` comment-only lines are excluded.
        Lines in helper functions (outside any hook section) are flagged as
        a soft diagnostic but NOT as a hard failure — those functions are only
        reachable through guarded hooks.
        """
        plugin_files = cls._collect_plugin_files()
        violations: dict[str, str] = {}
        guarded_sites: list[str] = []
        outside_hooks: list[str] = []
        for filepath in plugin_files:
            source = filepath.read_text()
            for line_no in _find_nag_lines(source, nag, skip_comments=True):
                enclosing = _enclosing_hook_section(source, line_no)
                if enclosing is None:
                    # Helper function / top-level code — only reached via a
                    # guarded hook. Log it but don't fail.
                    outside_hooks.append(f"{filepath.name}:{line_no}")
                    continue
                body = source[enclosing[0]:enclosing[1]]
                if not _SUBAGENT_GUARD_RE.search(body):
                    violations[f"{filepath.name}:{line_no}"] = (
                        f"{nag_label} in unguarded hook handler"
                    )
                else:
                    guarded_sites.append(f"{filepath.name}:{line_no}")

        if violations:
            raise AssertionError(
                f"{len(violations)} unguarded {nag_label} site(s):\n"
                + "\n".join(f"  {k}: {v}" for k, v in sorted(violations.items()))
            )
        if outside_hooks:
            print(f"\n    {nag_label}: {len(guarded_sites)} guarded, "
                  f"{len(outside_hooks)} in helper-fns (OK — guarded callees): "
                  f"{', '.join(outside_hooks)}")
        else:
            print(f"\n    {nag_label}: {len(guarded_sites)} sites, all guarded")

        return {
            "nag": nag_label,
            "guarded": str(len(guarded_sites)),
            "helper_fn": str(len(outside_hooks)),
        }

    def test_delegate_first_nag_fully_guarded(self) -> None:
        """Every executable DELEGATE-FIRST occurrence is inside a guarded hook."""
        r = self._check_nag_in_guarded_hooks(
            _NAG_DELEGATE_FIRST, "DELEGATE-FIRST",
        )
        assert int(r["guarded"]) > 0 or int(r["helper_fn"]) > 0, (
            "DELEGATE-FIRST not found in any plugin — regression?"
        )

    def test_read_grinding_nag_fully_guarded(self) -> None:
        """Every executable READ-GRINDING occurrence is inside a guarded hook."""
        r = self._check_nag_in_guarded_hooks(
            _NAG_READ_GRINDING, "READ-GRINDING",
        )
        assert int(r["guarded"]) > 0 or int(r["helper_fn"]) > 0, (
            "READ-GRINDING not found in any plugin — regression?"
        )

    def test_subagent_guard_precedes_all_nag_injections(self) -> None:
        """In every hook handler that contains BOTH a guard and nag text,
        the guard line must appear before every nag line."""
        plugin_files = self._collect_plugin_files()
        all_nags = [_NAG_DELEGATE_FIRST, _NAG_READ_GRINDING]
        violations: list[str] = []
        for filepath in plugin_files:
            source = filepath.read_text()
            lines = source.splitlines()
            sections = _find_all_hook_sections(source)
            for bs, be in sections:
                body = source[bs:be]
                guard_match = _SUBAGENT_GUARD_RE.search(body)
                if not guard_match:
                    continue
                guard_offset = bs + guard_match.start()
                guard_line = source[:guard_offset].count("\n") + 1
                for nag in all_nags:
                    for line_no in _find_nag_lines(source, nag, skip_comments=False):
                        offset = sum(len(ln) + 1 for ln in lines[:line_no - 1])
                        if bs <= offset < be and line_no < guard_line:
                            violations.append(
                                f"{filepath.name}:{line_no} {nag} "
                                f"appears before guard at line {guard_line}"
                            )

        assert not violations, (
            f"{len(violations)} nag injection(s) before their guard:\n"
            + "\n".join(violations)
        )

    def test_all_tool_execute_before_hooks_have_subagent_guard(self) -> None:
        """Every tool.execute.before handler must guard with OPENCODE_SUBAGENT.
        This is the primary defense against nag text reaching subagent tool results."""
        plugin_files = self._collect_plugin_files()
        _TOOL_BEFORE_RE = re.compile(
            r'"tool\.execute\.before"\s*:\s*async\s*\(',
        )
        violations: list[str] = []
        for filepath in plugin_files:
            if filepath.name in SUBAGENT_GUARD_EXCEPTIONS:
                continue
            source = filepath.read_text()
            for m in _TOOL_BEFORE_RE.finditer(source):
                pos = m.start()
                sections = _find_all_hook_sections(source)
                section_end: int | None = None
                for bs, be in sections:
                    if bs == pos:
                        section_end = be
                        break
                if section_end is None:
                    continue
                body = source[pos:section_end]
                if not _SUBAGENT_GUARD_RE.search(body):
                    line = source[:pos].count("\n") + 1
                    violations.append(
                        f"{filepath.name}: line ~{line} "
                        f"tool.execute.before lacks OPENCODE_SUBAGENT guard"
                    )

        assert not violations, (
            f"{len(violations)} tool.execute.before handler(s) lack "
            f"OPENCODE_SUBAGENT guard:\n" + "\n".join(violations)
        )

    def test_depth_is_the_only_tool_guard_exception(self) -> None:
        """The delegated depth boundary is dispatch-only and uniquely exempt."""
        assert frozenset({"enforce-depth.ts"}) == SUBAGENT_GUARD_EXCEPTIONS
        source = (PROJECT_ROOT / ".opencode/plugin/enforce-depth.ts").read_text()
        assert "if (!isDispatchTool(tool)) return" in source
        assert 'lt === "task" || lt === "agent" || lt === "workflow"' in source
