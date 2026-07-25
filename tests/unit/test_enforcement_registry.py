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
        stem = basename.removesuffix(".ts")
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
        stem = basename.removesuffix(".ts")
        # Find the row/section for this plugin. We accept either a markdown
        # table row or a section header.
        # The pattern matches a backtick-wrapped plugin name followed (on the
        # same or subsequent lines) by either GLUDD_*_ENFORCE / *_ENABLED=0
        # or an explicit "hard-coded" note.
        plugin_section = _extract_plugin_section(text, stem)
        assert plugin_section is not None, (
            f"Could not locate a documented section for plugin '{stem}'."
        )
        has_env = bool(re.search(r"GLUDD_[A-Z_]+(?:_ENFORCE|_ENABLED|_DISABLED)?\s*=\s*0", plugin_section))
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


class TestRegistryMatchesOcopencodeJsonCount:
    """The plugin count advertised in the registry must match reality."""

    def test_total_plugin_count_matches(self) -> None:
        text = REGISTRY_DOC.read_text()
        m = re.search(r"Total:\s*(\d+)\s+active\s+plugins", text, re.IGNORECASE)
        assert m, (
            "Registry must advertise a total plugin count in the form "
            "'Total: N active plugins'."
        )
        advertised = int(m.group(1))
        actual = len(PLUGINS)
        assert advertised == actual, (
            f"Registry advertises {advertised} plugins but opencode.json "
            f"declares {actual}. Update the 'Total:' line."
        )
