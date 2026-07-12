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
"""
import re
from pathlib import Path

# ── Config ──────────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
PLUGIN_DIRS = [
    PROJECT_ROOT / ".opencode" / "plugin",
    PROJECT_ROOT / ".opencode" / "plugins",
]


# ── Known nag strings injected by text.complete hooks ───────────────────────
# Update this list whenever a new nag/injection string is added to a
# text.complete hook. This is the baseline the test verifies against.
KNOWN_NAG_STRINGS: list[str] = [
    # enforce-stop.ts text.complete
    "DELEGATE-FIRST",  # prepended nag when streak > threshold
    "FALSE-DONE CLAIM BLOCKED",
    "DISPATCH A TOOL CALL",
    "TEXT BLOCKED — RATCHET HAS PENDING ENTRIES",
    "QA RESPONSE SUMMARY BLOCKED",
    "HARD STOP — STATE-BASED BLOCK: local work pending",
    # enforce-floor.ts text.complete
    "REFILL NEEDED",  # subagent pool drain nag
    # enforce-multitask.ts text.complete
    "MUST DISPATCH",  # zero-streak enforcement
    "DISPATCH SUBAGENTS NOW",  # 0 estimated in-flight
    # enforce-make.ts text.complete
    "GATE IS RED — RESPONSE BLOCKED",
    "STOP-PATTERN DETECTED — RESPONSE REPLACED",
    "TEXT BLOCKED — PENDING WORK EXISTS",
    "CATCH-ALL BLOCK — PENDING WORK REMAINS",
    # enforce-enhancement-ratio.ts text.complete
    "ENHANCEMENT RATIO VIOLATION",  # console.warn
]


# ── Regex to match a text.complete hook registration ─────────────────────────
_TEXTHOOK_RE = re.compile(
    r'(?:"text\.complete"|"experimental\.text\.complete")\s*:\s*async\s*\(',
)

# ── Regex to detect OPENCODE_SUBAGENT guard ─────────────────────────────────
_SUBAGENT_GUARD_RE = re.compile(
    r'process\.env\.OPENCODE_SUBAGENT\s*===\s*"1"',
)


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

    def test_all_text_complete_hooks_have_subagent_guard(self):
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

    def test_known_nag_strings_found_in_plugins(self):
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

    def test_no_unexpected_nags_in_text_complete_hooks(self):
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

    def test_plugin_count_matches_expected(self):
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

    def test_diagnostic_text_complete_hook_inventory(self):
        """Print full inventory of text.complete hooks and their guards."""
        plugin_files = self._collect_plugin_files()
        inventory: list[dict] = []
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
        for entry in sorted(inventory, key=lambda e: e["file"]):
            guard = "PASS" if entry["has_guard"] else "FAIL"
            print(f"    {entry['file']}:{entry['line']} "
                  f"guard={guard} nag_strings={entry['nag_strings']}")
        assert True
