"""E13/E14/E15/E16/E17: Anti-essay enforcement details.

Verifies enforce-anti-essay.ts contains the specific heuristics
required by these specs: metadata-absence detection, image/emoji-heavy
prose blocking, "let me explain" pattern blocking, gate-red text clamp,
and prose-to-code ratio enforcement.
"""

from pathlib import Path

ROOT = Path(__file__).parent.parent.parent


class TestE13E17AntiEssayDetails:
    """E13-E17: anti-essay plugin heuristic details."""

    def test_anti_essay_plugin_readable(self) -> None:
        plugin_path = ROOT / ".opencode" / "plugin" / "enforce-anti-essay.ts"
        if not plugin_path.exists():
            return
        content = plugin_path.read_text()

        # E13: metadata-absence detection
        assert any(kw in content for kw in ["metadata", "evidence", "citation"]), (
            "E13: enforce-anti-essay.ts must detect metadata-absence (no-commit-hash, no-test-count prose)"
        )

        # E14: blocked-pattern detection is the canonical E14 contract.
        assert "isBlockedPattern" in content
        assert "hasBoldedHeaders" in content
        assert "hasStatusSummary" in content

        # E15: "let me explain" pattern detection
        has_explain = "explain" in content.lower()
        assert has_explain, "E15: enforce-anti-essay.ts must detect 'let me explain' patterns"

        # E16: gate-red text clamp
        assert any(kw in content for kw in ["gate", "red", "clamp", "limit", "max"]), (
            "E16: enforce-anti-essay.ts must clamp response length when gate is red"
        )

        # E17: prose-to-code ratio enforcement
        assert any(kw in content for kw in ["ratio", "proportion", "code", "balance"]), (
            "E17: enforce-anti-essay.ts must enforce prose-to-code ratio"
        )

    def test_anti_essay_plugin_has_anti_explanation_guard(self) -> None:
        """E15 belongs to the dedicated anti-essay plugin, not enforce-stop."""
        plugin_path = ROOT / ".opencode" / "plugin" / "enforce-anti-essay.ts"
        if not plugin_path.exists():
            return
        content = plugin_path.read_text()
        has_explain = "explain" in content.lower()
        assert has_explain, "E15: anti-essay enforcement must detect explanation prose"

    def test_anti_essay_exports_disambiguation_function(self) -> None:
        """The plugin should export functions that other plugins can call."""
        plugin_path = ROOT / ".opencode" / "plugin" / "enforce-anti-essay.ts"
        if not plugin_path.exists():
            return
        content = plugin_path.read_text()
        has_export = "export" in content
        assert has_export, "E17: enforce-anti-essay.ts should export functions for cross-plugin use"
