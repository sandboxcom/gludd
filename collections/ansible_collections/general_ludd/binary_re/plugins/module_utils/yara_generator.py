"""YARA rule generation from PatternEntry records (NF.3 Binary RE).

Translates each :class:`PatternEntry` in the binary-RE pattern database into a
syntactically valid YARA rule. Byte patterns become YARA hex strings
(``{ AB CD }``); ASCII string markers become quoted strings. The generator is
deterministic and side-effect free — calling ``render_all()`` produces the same
YARA source every time for a given database snapshot.

Public API:

- :class:`YaraString`  — a single ``$id = ...`` declaration.
- :class:`YaraRule`    — the structured form (name, strings, condition, meta).
- :class:`YaraGenerator` — converts :class:`PatternEntry` → :class:`YaraRule`
  and renders the rule as source text.

The output targets YARA 4.x syntax (hex strings in braces, quoted ASCII strings,
``any of them`` condition for multi-string rules).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from plugins.module_utils.pattern_database import (
    PatternDatabase,
    PatternEntry,
)


def _sanitize_identifier(raw: str) -> str:
    """Convert an arbitrary string into a valid YARA identifier.

    YARA identifiers match ``[A-Za-z_][A-Za-z0-9_]*``. Hyphens and other
    punctuation are replaced with underscores; a leading digit is prefixed
    with ``_``.
    """
    cleaned = re.sub(r"[^A-Za-z0-9_]", "_", raw)
    if cleaned and cleaned[0].isdigit():
        cleaned = "_" + cleaned
    if not cleaned:
        cleaned = "unnamed"
    return cleaned


def _bytes_to_hex(data: bytes) -> str:
    """Render bytes as a YARA hex body (``AB CD EF``), uppercased, space-separated."""
    return " ".join(f"{b:02X}" for b in data)


def _escape_yara_string(value: str) -> str:
    """Escape a Python string for inclusion in a YARA double-quoted string."""
    # YARA string escapes: backslash + double-quote (the common cases). Other
    # control chars are expressed via hex strings at the caller layer, but
    # guard against stray newlines/tabs here too.
    escaped = value.replace("\\", "\\\\")
    escaped = escaped.replace('"', '\\"')
    escaped = escaped.replace("\n", "\\n")
    escaped = escaped.replace("\t", "\\t")
    return escaped


def _is_printable_ascii(value: str) -> bool:
    """True if every character is printable ASCII (no control chars)."""
    return all(32 <= ord(c) < 127 for c in value)


@dataclass(frozen=True)
class YaraString:
    """One ``$id = <value>`` declaration in a YARA rule's ``strings:`` block."""

    identifier: str
    kind: str  # "string" | "hex"
    value: str  # already-rendered form (escaped ASCII body, or hex body)


@dataclass(frozen=True)
class YaraRule:
    """Structured representation of a single YARA rule."""

    name: str
    strings: tuple[YaraString, ...]
    condition: str
    meta: dict[str, str] = field(default_factory=dict)


class YaraGenerator:
    """Convert :class:`PatternEntry` records into :class:`YaraRule` objects.

    The generator is deterministic: the same database snapshot produces the
    same rule text. Pass a custom :class:`PatternDatabase` (or any object
    exposing ``all_entries()``) to control which patterns are translated.
    """

    def __init__(self, database: PatternDatabase | None = None) -> None:
        self._database: PatternDatabase = (
            database if database is not None else PatternDatabase()
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def generate_for_entry(self, entry: PatternEntry) -> YaraRule:
        """Translate a single :class:`PatternEntry` into a :class:`YaraRule`.

        Raises ``ValueError`` if the entry has neither byte patterns nor
        string markers — YARA rules require at least one string.
        """
        if not entry.byte_patterns and not entry.string_markers:
            raise ValueError(
                f"PatternEntry {entry.id!r} has no byte patterns or string "
                f"markers; cannot generate a YARA rule"
            )

        strings = self._build_strings(entry)
        condition = self._build_condition(strings)
        meta = self._build_meta(entry)
        name = self._build_name(entry)

        return YaraRule(
            name=name,
            strings=tuple(strings),
            condition=condition,
            meta=meta,
        )

    def generate_all(self) -> list[YaraRule]:
        """Generate one :class:`YaraRule` per database entry (in DB order)."""
        rules: list[YaraRule] = []
        for entry in self._database.all_entries():
            if not entry.byte_patterns and not entry.string_markers:
                # Skip degenerate entries rather than raise — keeps
                # ``render_all()`` robust against future seed additions.
                continue
            rules.append(self.generate_for_entry(entry))
        return rules

    def render_rule(self, rule: YaraRule) -> str:
        """Render a single :class:`YaraRule` as YARA source text.

        Output ends with a trailing newline so concatenated rules stay
        separated by a blank line.
        """
        lines: list[str] = []
        lines.append(f"rule {rule.name}")
        lines.append("{")
        # --- meta -----------------------------------------------------
        lines.append("    meta:")
        for key in ("pattern_id", "name", "category", "severity",
                    "platform", "description", "references"):
            if key in rule.meta:
                lines.append(f'        {key} = "{_escape_yara_string(rule.meta[key])}"')
        lines.append("    strings:")
        for ys in rule.strings:
            if ys.kind == "hex":
                lines.append(f"        {ys.identifier} = {{ {ys.value} }}")
            else:
                lines.append(f'        {ys.identifier} = "{ys.value}"')
        lines.append("    condition:")
        lines.append(f"        {rule.condition}")
        lines.append("}")
        return "\n".join(lines) + "\n"

    def render_all(self) -> str:
        """Render every rule in the database as a single YARA source document."""
        chunks: list[str] = []
        for rule in self.generate_all():
            chunks.append(self.render_rule(rule))
        return "\n".join(chunks)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_name(self, entry: PatternEntry) -> str:
        # Prefix with the collection tag so rules are attributable on disk
        # and unlikely to collide with operator-authored rules.
        return "gludd_" + _sanitize_identifier(entry.id)

    def _build_strings(self, entry: PatternEntry) -> list[YaraString]:
        out: list[YaraString] = []
        s_idx = 1
        b_idx = 1
        for marker in entry.string_markers:
            if not marker:
                continue
            if not _is_printable_ascii(marker):
                # Non-printable marker → emit as hex instead of a quoted string.
                encoded = marker.encode("ascii", errors="ignore")
                if not encoded:
                    continue
                out.append(
                    YaraString(
                        identifier=f"$s{s_idx}",
                        kind="hex",
                        value=_bytes_to_hex(encoded),
                    )
                )
            else:
                out.append(
                    YaraString(
                        identifier=f"$s{s_idx}",
                        kind="string",
                        value=_escape_yara_string(marker),
                    )
                )
            s_idx += 1
        for pat in entry.byte_patterns:
            if not pat:
                continue
            out.append(
                YaraString(
                    identifier=f"$b{b_idx}",
                    kind="hex",
                    value=_bytes_to_hex(pat),
                )
            )
            b_idx += 1
        return out

    def _build_condition(self, strings: list[YaraString]) -> str:
        if len(strings) == 1:
            return strings[0].identifier
        # ``any of them`` is concise and robust to future pattern additions;
        # it also survives identifier renumbering without rewrite.
        return "any of them"

    def _build_meta(self, entry: PatternEntry) -> dict[str, str]:
        meta: dict[str, str] = {
            "pattern_id": entry.id,
            "name": entry.name,
            "category": entry.category.value,
            "severity": entry.severity.value,
            "platform": entry.platform.value,
            "description": entry.description,
        }
        if entry.references:
            meta["references"] = " | ".join(entry.references)
        return meta
