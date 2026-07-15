"""Internationalization (i18n) data and helpers.

Covers spec section 4.5 (I18n):
- Pseudolocalization for testing UI string expansion
- Gettext .po/.pot file parsing and serialization
- ICU MessageFormat placeholder extraction
- i18n linting: detection of hardcoded user-facing strings

Pure-Python knowledge module; no external dependencies (no babel/gettext).
"""

from __future__ import annotations

import re
from typing import Literal, TypedDict

# ── Pseudolocalization ─────────────────────────────────────────────────────


PseudoMethod = Literal["accent", "bracket"]


PSEUDO_ACCENT_MAP: dict[str, str] = {
    "a": "\u00e1", "b": "\u044c", "c": "\u00e7", "d": "\u00f0",
    "e": "\u00e9", "f": "\u0192", "g": "\u011d", "h": "\u0125",
    "i": "\u00ee", "j": "\u0135", "k": "\u0137", "l": "\u013c",
    "m": "\u1e3f", "n": "\u00f1", "o": "\u00f6", "p": "\u00fe",
    "q": "\u051b", "r": "\u0157", "s": "\u015b", "t": "\u0163",
    "u": "\u00fb", "v": "\u1e7d", "w": "\u0175", "x": "\u00fd",
    "y": "\u00ff", "z": "\u017e",
    "A": "\u00c0", "B": "\u03b2", "C": "\u00c7", "D": "\u00d0",
    "E": "\u00c9", "F": "\u0191", "G": "\u011c", "H": "\u0124",
    "I": "\u00ce", "J": "\u0134", "K": "\u0136", "L": "\u013b",
    "M": "\u1e3e", "N": "\u00d1", "O": "\u00d6", "P": "\u00de",
    "Q": "\u051a", "R": "\u0156", "S": "\u015a", "T": "\u0162",
    "U": "\u00db", "V": "\u1e7c", "W": "\u0174", "X": "\u00dd",
    "Y": "\u0178", "Z": "\u017d",
}

_PSEUDO_PLACEHOLDER_RE = re.compile(
    r"\{[^}]*\}|%[sdifg]|%\([^)]*\)[sdifg]|\\\{[^}]*\\\}"
)


def pseudolocalize(text: str, method: str = "accent") -> str:
    """Pseudolocalize text for i18n readiness testing.

    Methods:
    - 'accent': substitute ASCII letters with accented equivalents to
      reveal hardcoded lengths and font-coverage gaps.
    - 'bracket': wrap the text in brackets to detect truncation.

    Format placeholders ({name}, %s, %d) are preserved.
    """
    if not text:
        return ""

    if method == "bracket":
        return f"[{text}]"

    out: list[str] = []
    idx = 0
    while idx < len(text):
        match = _PSEUDO_PLACEHOLDER_RE.match(text, idx)
        if match:
            out.append(match.group(0))
            idx = match.end()
            continue
        ch = text[idx]
        out.append(PSEUDO_ACCENT_MAP.get(ch, ch))
        idx += 1
    return "".join(out)


# ── Gettext .po file parsing ────────────────────────────────────────────────


class PoEntry(TypedDict):
    """A single gettext .po entry."""

    msgid: str
    msgstr: str
    references: list[str]
    flags: list[str]


_MSGID_RE = re.compile(r'^msgid\s+"((?:[^"\\]|\\.)*)"\s*$')
_MSGSTR_RE = re.compile(r'^msgstr\s+"((?:[^"\\]|\\.)*)"\s*$')
_REF_RE = re.compile(r"^#:\s*(.+)$")
_FLAG_RE = re.compile(r"^#,\s*(.+)$")
_CONT_RE = re.compile(r'^"((?:[^"\\]|\\.)*)"\s*$')


def _unescape(s: str) -> str:
    return s.replace('\\"', '"').replace("\\n", "\n").replace(
        "\\t", "\t"
    ).replace("\\\\", "\\")


def _escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"').replace(
        "\n", "\\n"
    ).replace("\t", "\\t")


def parse_po(content: str) -> list[PoEntry]:
    """Parse gettext .po file content into a list of entries.

    Skips the empty-msgid header entry. Each entry has:
    msgid, msgstr, references, flags.
    """
    if not content:
        return []

    entries: list[PoEntry] = []
    current: PoEntry | None = None
    pending_refs: list[str] = []
    pending_flags: list[str] = []
    pending_msgstr_continuation = False

    for line in content.splitlines():
        stripped = line.rstrip()

        if not stripped:
            if current is not None and current["msgid"]:
                entries.append(current)
            current = None
            pending_refs = []
            pending_flags = []
            pending_msgstr_continuation = False
            continue

        ref_match = _REF_RE.match(stripped)
        if ref_match:
            refs = [r.strip() for r in ref_match.group(1).split()]
            if current is not None and current["msgid"]:
                current["references"].extend(refs)
            else:
                pending_refs.extend(refs)
            continue

        flag_match = _FLAG_RE.match(stripped)
        if flag_match:
            flags = [f.strip() for f in flag_match.group(1).split(",")]
            if current is not None and current["msgid"]:
                current["flags"].extend(flags)
            else:
                pending_flags.extend(flags)
            continue

        msgid_match = _MSGID_RE.match(stripped)
        if msgid_match:
            if current is not None and current["msgid"]:
                entries.append(current)
            current = {
                "msgid": _unescape(msgid_match.group(1)),
                "msgstr": "",
                "references": list(pending_refs),
                "flags": list(pending_flags),
            }
            pending_refs = []
            pending_flags = []
            pending_msgstr_continuation = False
            continue

        msgstr_match = _MSGSTR_RE.match(stripped)
        if msgstr_match and current is not None:
            current["msgstr"] = _unescape(msgstr_match.group(1))
            pending_msgstr_continuation = True
            continue

        cont_match = _CONT_RE.match(stripped)
        if cont_match and current is not None:
            piece = _unescape(cont_match.group(1))
            if pending_msgstr_continuation:
                current["msgstr"] += piece
            elif current["msgid"]:
                current["msgid"] += piece
            continue

        ref_match_no_current = _REF_RE.match(stripped)
        if ref_match_no_current and current is None:
            continue

    if current is not None and current["msgid"]:
        entries.append(current)

    return entries


def serialize_po(entries: list[PoEntry]) -> str:
    """Serialize a list of PoEntry dicts back into .po file text."""
    lines: list[str] = []
    lines.append("# Generated by general_ludd.language.i18n_data")
    lines.append('msgid ""')
    lines.append('msgstr ""')
    lines.append('"Content-Type: text/plain; charset=UTF-8\\n"')
    lines.append("")

    for entry in entries:
        if entry["references"]:
            lines.append("#: " + " ".join(entry["references"]))
        if entry["flags"]:
            lines.append("#, " + ",".join(entry["flags"]))
        lines.append(f'msgid "{_escape(entry["msgid"])}"')
        lines.append(f'msgstr "{_escape(entry["msgstr"])}"')
        lines.append("")

    return "\n".join(lines)


# ── ICU MessageFormat placeholder extraction ────────────────────────────────


_ICU_PLACEHOLDER_RE = re.compile(r"\{([^{}]*?)[\},]")


def extract_icu_placeholders(message: str) -> list[str]:
    """Extract {name} placeholder names from an ICU MessageFormat string.

    Handles both simple ({name}) and typed ({count, plural, ...}) forms.
    Returns names in order of first appearance, deduplicated.
    """
    if not message:
        return []

    seen: set[str] = set()
    out: list[str] = []
    for match in _ICU_PLACEHOLDER_RE.finditer(message):
        name = match.group(1).strip()
        if name and name not in seen:
            seen.add(name)
            out.append(name)
    return out


# ── i18n linting: hardcoded string detection ───────────────────────────────


class StringFinding(TypedDict):
    """A single lint finding for a hardcoded user-facing string."""

    line: int
    string: str
    issue: str


_GETTEXT_CALL_RE = re.compile(
    r"(?:_\(|gettext\(|ngettext\(|pgettext\()\s*[\"']"
)

_HARDCODED_STRING_RE = re.compile(
    r'(?<![\w.])["\']([A-Z][a-zA-Z][^"\']{4,})["\']'
)

_MIN_STRING_LEN = 6


def find_untranslated_strings(
    source: str, language: str = "python"
) -> list[StringFinding]:
    """Find user-facing hardcoded strings not wrapped in gettext.

    A string is flagged if:
    - It is a double-quoted literal
    - It starts with an uppercase letter and a lowercase letter
    - It is at least 6 characters long (filters short identifiers)
    - It does NOT appear inside a _(), gettext(), ngettext(), or pgettext() call

    Returns one finding per string occurrence with line number and content.
    """
    if not source:
        return []

    findings: list[StringFinding] = []
    gettext_spans: list[tuple[int, int]] = []
    for m in _GETTEXT_CALL_RE.finditer(source):
        line_start = source.rfind("\n", 0, m.start()) + 1
        line_end_idx = source.find("\n", m.end())
        if line_end_idx == -1:
            line_end_idx = len(source)
        gettext_spans.append((line_start, line_end_idx))

    for match in _HARDCODED_STRING_RE.finditer(source):
        s = match.group(1)
        if len(s) < _MIN_STRING_LEN:
            continue
        if " " not in s:
            continue
        in_gettext = any(
            start <= match.start() <= end for start, end in gettext_spans
        )
        if in_gettext:
            continue
        line = source.count("\n", 0, match.start()) + 1
        findings.append({
            "line": line,
            "string": s,
            "issue": "Hardcoded user-facing string not wrapped in gettext()",
        })

    return findings
