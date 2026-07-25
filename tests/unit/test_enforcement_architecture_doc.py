"""Verify docs/ENFORCEMENT_ARCHITECTURE.md exists and covers key architecture topics.

The architecture doc is the canonical reference for HOW the enforcement
plugin system works (hot-reload proxy, fail-open, subagent isolation, state
files, disable mechanisms). The registry doc covers WHAT each plugin blocks.
This test pins the architecture doc against topic drift — a future edit
that strips a section is caught at gate time.

Companion test: tests/unit/test_enforcement_registry.py (pins per-plugin
coverage in docs/ENFORCEMENT_PLUGIN_REGISTRY.md).
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
DOC = ROOT / "docs" / "ENFORCEMENT_ARCHITECTURE.md"


class TestDocumentExists:
    def test_doc_exists(self) -> None:
        assert DOC.exists(), (
            f"{DOC} not found. The enforcement architecture is undocumented. "
            "Create it covering: hot-reload proxy, plugin lifecycle, hook "
            "surfaces, shared helpers, fail-open, subagent isolation, state "
            "files, disable mechanisms, interaction diagram."
        )

    def test_doc_has_minimum_size(self) -> None:
        text = DOC.read_text()
        # The architecture doc covers ~10 major topics with code samples and
        # tables. Anything under 5KB is a stub, not architecture documentation.
        assert len(text) > 5000, (
            f"Architecture doc is only {len(text)} bytes — too short to cover "
            "the hot-reload pattern, hook surfaces, shared helpers, fail-open "
            "principle, subagent isolation, state files, disable mechanisms, "
            "and interaction diagram with any depth."
        )


class TestCoreArchitectureTopics:
    """The doc MUST cover each architectural pillar of the enforcement system."""

    def test_covers_hot_reload_proxy_pattern(self) -> None:
        text = DOC.read_text()
        assert "loadHotModule" in text, (
            "Architecture doc must document the loadHotModule() proxy pattern."
        )
        assert "defaultImpl" in text or "default" in text.lower(), (
            "Architecture doc must explain the compiled-in defaultImpl fallback."
        )

    def test_covers_plugin_lifecycle(self) -> None:
        text = DOC.read_text()
        # Registration → factory call → hook invocation
        assert "opencode.json" in text, (
            "Architecture doc must reference opencode.json plugin registration."
        )
        assert "factory" in text.lower(), (
            "Architecture doc must explain the plugin factory call lifecycle."
        )

    def test_covers_hook_surfaces(self) -> None:
        text = DOC.read_text()
        # The five primary hook surfaces.
        for hook in (
            "tool.execute.before",
            "tool.execute.after",
            "text.complete",
            "system.transform",
            "session.idle",
        ):
            assert hook in text, (
                f"Architecture doc must document the {hook} hook surface."
            )

    def test_covers_shared_helpers(self) -> None:
        text = DOC.read_text()
        # The four canonical shared.ts helpers.
        for helper in ("isSubagent", "reportAlive", "isDisengaged", "getProjectRoot"):
            assert helper in text, (
                f"Architecture doc must document the {helper}() shared helper."
            )

    def test_covers_fail_open_principle(self) -> None:
        text = DOC.read_text()
        assert "fail-open" in text.lower() or "fail open" in text.lower(), (
            "Architecture doc must document the fail-open principle."
        )

    def test_covers_subagent_isolation(self) -> None:
        text = DOC.read_text()
        assert "OPENCODE_SUBAGENT" in text, (
            "Architecture doc must document the OPENCODE_SUBAGENT env var guard."
        )

    def test_covers_state_files(self) -> None:
        text = DOC.read_text()
        assert "/tmp/gludd-" in text, (
            "Architecture doc must document the /tmp/gludd-*.json state file pattern."
        )

    def test_covers_disable_mechanisms(self) -> None:
        text = DOC.read_text()
        assert "GLUDD_" in text and "_ENFORCE" in text, (
            "Architecture doc must document the GLUDD_*_ENFORCE=0 disable pattern."
        )
        assert "disengage" in text.lower(), (
            "Architecture doc must document the bulk disengage mechanism."
        )


class TestInteractionDiagram:
    """The doc MUST include a text-based plugin interaction diagram."""

    def test_has_ascii_diagram(self) -> None:
        text = DOC.read_text()
        # A box-drawing or arrow-using diagram. We require at least one of
        # these characters in a code block.
        assert "```" in text, "Architecture doc must contain fenced code blocks."
        # Look for diagram-style characters: arrows or box-drawing.
        has_arrows = "│" in text or "→" in text or "->" in text
        has_boxes = "┌" in text or "+" in text
        assert has_arrows or has_boxes, (
            "Architecture doc must include a text-based plugin interaction "
            "diagram (box-drawing or arrow notation)."
        )


class TestReferences:
    """The doc MUST cross-reference the companion files and source of truth."""

    def test_references_shared_ts(self) -> None:
        text = DOC.read_text()
        assert "shared.ts" in text, (
            "Architecture doc must reference .opencode/lib/shared.ts."
        )

    def test_references_hot_reload_ts(self) -> None:
        text = DOC.read_text()
        assert "hot_reload.ts" in text, (
            "Architecture doc must reference .opencode/lib/hot_reload.ts."
        )

    def test_references_registry_doc(self) -> None:
        text = DOC.read_text()
        assert "ENFORCEMENT_PLUGIN_REGISTRY" in text, (
            "Architecture doc must cross-reference the per-plugin registry doc."
        )

    def test_references_agents_md(self) -> None:
        text = DOC.read_text()
        assert "AGENTS.md" in text, (
            "Architecture doc must reference AGENTS.md as the policy layer."
        )


class TestPluginCountAccuracy:
    """The doc MUST accurately state the active plugin count."""

    def test_states_28_or_more_plugins(self) -> None:
        text = DOC.read_text()
        # Match either "28 plugins", "28+", or "27 enforce-* plus watchdog"
        # style phrasings. The exact count evolves; "28+" is the floor.
        patterns = [
            r"28\+?\s+plugins",
            r"27\s+enforce-\*\.?\s*\+?\s*watchdog",
            r"27\s+enforce-.\*.\s+plugins\s+plus\s+watchdog",
        ]
        assert any(re.search(p, text, re.IGNORECASE) for p in patterns), (
            "Architecture doc must state the active plugin count (28+ or "
            "'27 enforce-* plus watchdog')."
        )
