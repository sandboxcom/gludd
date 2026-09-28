"""Verify docs/ENFORCEMENT_PLUGIN_REGISTRY.md exists and covers every active plugin.

The registry is the operator-facing reference for the enforcement layer. It
MUST list every plugin named in `opencode.json`'s `plugin` array, and each
entry MUST mention a disable mechanism (an env var or an explicit note that
the plugin is hard-coded ON).

A plugin that ships without documentation in the registry is a policy gap —
operators cannot disable a guardrail they cannot see. This test makes that
gap structurally impossible.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent.parent
OPENCODE_JSON = ROOT / "opencode.json"
REGISTRY_DOC = ROOT / "docs" / "ENFORCEMENT_PLUGIN_REGISTRY.md"


def _load_plugin_paths() -> list[str]:
    """Return the list of plugin paths declared in opencode.json."""
    data = json.loads(OPENCODE_JSON.read_text())
    plugins = data.get("plugin", [])
    assert isinstance(plugins, list), "opencode.json `plugin` must be a list"
    assert plugins, "opencode.json declares no plugins"
    return plugins


def _plugin_basename(path: str) -> str:
    """`./.opencode/plugin/enforce-make.ts` -> `enforce-make.ts`."""
    return Path(path).name


def _plugin_stem(path: str) -> str:
    """`./.opencode/plugin/enforce-make.ts` -> `enforce-make`."""
    return Path(path).stem


PLUGINS = _load_plugin_paths()
PLUGIN_BASENAMES = [_plugin_basename(p) for p in PLUGINS]


def _registry_table_plugins(text: str) -> list[tuple[int, str]]:
    """Return numbered plugin rows from the operator registry table."""
    return [
        (int(number), basename)
        for number, basename in re.findall(
            r"^\|\s*(\d+)\s*\|\s*`([^`]+)`\s*\|",
            text,
            re.MULTILINE,
        )
    ]


class TestRegistryDocumentExists:
    """The registry file itself must exist and be non-trivial."""

    def test_registry_doc_exists(self) -> None:
        assert REGISTRY_DOC.exists(), (
            f"{REGISTRY_DOC} not found. Operators have no plugin reference. "
            "Create it per AGENTS.md guardrail-pattern skill."
        )

    def test_registry_doc_has_minimum_size(self) -> None:
        text = REGISTRY_DOC.read_text()
        # Each of 28 plugins contributes ~1 row + ~50 chars of prose; 28 rows
        # alone is ~3KB. Anything under 2KB is a stub, not a registry.
        assert len(text) > 2000, (
            f"Registry doc is only {len(text)} bytes — too short to cover "
            f"{len(PLUGINS)} plugins with hooks + disable env vars."
        )

    def test_registry_doc_has_table_header(self) -> None:
        text = REGISTRY_DOC.read_text()
        # Markdown table with the four documented columns.
        assert "| Plugin |" in text, "Registry must contain a plugin table"
        assert "| Hook" in text, "Registry table must document hooks"
        assert "| What it blocks" in text, (
            "Registry table must document what each plugin blocks"
        )
        assert "| Disable" in text, (
            "Registry table must document the disable env var"
        )


class TestRegistryCoversEveryPlugin:
    """Every plugin in opencode.json MUST appear in the registry."""

    @pytest.mark.parametrize("basename", PLUGIN_BASENAMES)
    def test_plugin_appears_in_registry(self, basename: str) -> None:
        text = REGISTRY_DOC.read_text()
        # Match either the basename (`enforce-make.ts`) or the stem
        # (`enforce-make`), as either form is acceptable in prose.
        stem = _plugin_stem(basename)
        assert basename in text or f"`{stem}`" in text or re.search(
            rf"\b{re.escape(stem)}\b", text
        ), (
            f"Plugin '{basename}' is declared in opencode.json but is NOT "
            f"documented in {REGISTRY_DOC}. Add a row to the registry table."
        )


class TestRegistryDocumentsDisableMechanism:
    """Each plugin entry MUST mention how to disable it."""

    @pytest.mark.parametrize("basename", PLUGIN_BASENAMES)
    def test_plugin_entry_mentions_disable(self, basename: str) -> None:
        text = REGISTRY_DOC.read_text()
        stem = _plugin_stem(basename)
        # Find the row/section for this plugin. We accept either a markdown
        # table row or a section header.
        # The pattern matches a backtick-wrapped plugin name followed (on the
        # same or subsequent lines) by either GLUDD_*_ENFORCE / *_ENABLED=0
        # or an explicit "hard-coded" note.
        plugin_section = _extract_plugin_section(text, stem)
        assert plugin_section is not None, (
            f"Could not locate a documented section for plugin '{stem}'."
        )
        has_env = bool(
            re.search(
                r"GLUDD_[A-Z0-9_]+(?:_ENFORCE|_ENABLED|_DISABLED)?\s*=\s*0",
                plugin_section,
            )
        )
        has_hardcoded_note = "hard-coded" in plugin_section.lower() or "no env disable" in plugin_section.lower()
        assert has_env or has_hardcoded_note, (
            f"Plugin '{stem}' is documented but its disable mechanism is not. "
            "Every entry must name a `GLUDD_*_ENFORCE=0` env var OR explicitly "
            "note that the plugin is hard-coded ON / has no env disable."
        )


def _extract_plugin_section(text: str, stem: str) -> str | None:
    r"""Return the slice of `text` that documents the given plugin stem.

    Handles two layouts:
      1. Markdown table row starting with `| <n> | \`<stem>.ts\` | ... |`
      2. Markdown section header `### \`<stem>\`` or `## <stem>`
    Falls back to "the 400 chars after the first mention of the stem" so the
    disable-mechanism check has something to scan.
    """
    # Table row form: grab the whole row.
    table_row_re = re.compile(
        r"^\|[^\n]*?" + re.escape(stem) + r"[^\n]*$",
        re.MULTILINE,
    )
    m = table_row_re.search(text)
    if m:
        return m.group(0)

    # Section header form: grab until the next header of equal or greater rank.
    header_re = re.compile(
        r"^(#{1,6})\s*.*?" + re.escape(stem) + r"[^\n]*$",
        re.MULTILINE,
    )
    m = header_re.search(text)
    if m:
        rank = len(m.group(1))
        rest = text[m.end():]
        next_header = re.search(rf"^#{{1,{rank}}}\s", rest, re.MULTILINE)
        return rest[: next_header.start() if next_header else 400]

    # Fallback: 400 chars after first stem mention.
    idx = text.find(stem)
    if idx == -1:
        return None
    return text[idx:idx + 400]


class TestRegistryMatchesOpencodeJsonExactly:
    """The numbered registry table must exactly mirror opencode.json."""

    def test_total_plugin_count_matches(self) -> None:
        text = REGISTRY_DOC.read_text()
        advertised_counts = re.findall(
            r"Total:\s*(\d+)\s+active\s+plugins",
            text,
            re.IGNORECASE,
        )
        assert len(advertised_counts) == 1, (
            "Registry must advertise a total plugin count in the form "
            "'Total: N active plugins' exactly once."
        )
        advertised = int(advertised_counts[0])
        actual = len(PLUGINS)
        assert advertised == actual, (
            f"Registry advertises {advertised} plugins but opencode.json "
            f"declares {actual}. Update the 'Total:' line."
        )

    def test_numbered_table_is_an_exact_ordered_registry(self) -> None:
        rows = _registry_table_plugins(REGISTRY_DOC.read_text())

        assert len(PLUGIN_BASENAMES) == len(set(PLUGIN_BASENAMES)), (
            "opencode.json must not register the same plugin basename twice"
        )
        assert [number for number, _basename in rows] == list(
            range(1, len(PLUGIN_BASENAMES) + 1)
        ), "Registry row numbers must be contiguous and cover every active plugin"
        assert [basename for _number, basename in rows] == PLUGIN_BASENAMES, (
            "Registry rows must exactly match opencode.json order; duplicates, "
            "prose-only mentions, stale entries, and undocumented additions are invalid"
        )

    @pytest.mark.parametrize(
        ("path", "expected"),
        [
            ("./.opencode/plugin/enforce-example.ts", "enforce-example"),
            ("./.opencode/plugin/enforce-example.mjs", "enforce-example"),
        ],
    )
    def test_plugin_stem_is_extension_agnostic(
        self,
        path: str,
        expected: str,
    ) -> None:
        assert _plugin_stem(path) == expected
