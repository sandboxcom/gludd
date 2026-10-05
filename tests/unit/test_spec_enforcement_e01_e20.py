"""E01/E03/E11/E20: Anti-essay enforcement plugin tests.

Verify that enforce-anti-essay.ts exists, detects essay-length
responses, and is default ON (not advisory). E-series specs cover
word-count heuristics, adaptive thresholds, metadata-absence detection,
and enforcement blocking of prose-heavy responses when work is pending.
"""

from pathlib import Path

ROOT = Path(__file__).parent.parent.parent


class TestE01E03E11E20AntiEssayEnforcement:
    """E01-E20: anti-essay guard is plugin-enforced and default ON."""

    def test_anti_essay_plugin_exists(self) -> None:
        plugin_path = ROOT / ".opencode" / "plugin" / "enforce-anti-essay.ts"
        assert plugin_path.exists(), "E01: enforce-anti-essay.ts plugin must exist for essay detection"

    def test_anti_essay_plugin_has_text_complete_hook(self) -> None:
        plugin_path = ROOT / ".opencode" / "plugin" / "enforce-anti-essay.ts"
        if not plugin_path.exists():
            return
        content = plugin_path.read_text()
        has_hook = "text.complete" in content or "textComplete" in content or "experimental.text.complete" in content
        assert has_hook, "E01: enforce-anti-essay.ts must register a text.complete hook"

    def test_anti_essay_is_not_purely_advisory(self) -> None:
        plugin_path = ROOT / ".opencode" / "plugin" / "enforce-anti-essay.ts"
        if not plugin_path.exists():
            return
        content = plugin_path.read_text()
        has_block = any(keyword in content for keyword in ["permissionDecision", "deny", "throw", "block", "inject"])
        assert has_block, (
            "E20: enforce-anti-essay.ts must be BLOCKING (not advisory). "
            "It must contain a deny/throw/block/inject mechanism."
        )

    def test_anti_essay_has_word_count_heuristic(self) -> None:
        plugin_path = ROOT / ".opencode" / "plugin" / "enforce-anti-essay.ts"
        if not plugin_path.exists():
            return
        content = plugin_path.read_text()
        has_word = "word" in content.lower() or "length" in content.lower() or "count" in content.lower()
        assert has_word, "E03/E12: enforce-anti-essay.ts must have a word-count or length-based heuristic"

    def test_anti_essay_plugin_registered_in_opencode_json(self) -> None:
        import json

        opencode_json = ROOT / "opencode.json"
        if not opencode_json.exists():
            return
        config = json.loads(opencode_json.read_text())
        plugins = config.get("plugin", []) if isinstance(config, dict) else config

        anti_essay_paths = [p.get("path", "") if isinstance(p, dict) else p for p in plugins]
        found = any("enforce-anti-essay" in p for p in anti_essay_paths)
        assert found, "E11: enforce-anti-essay.ts must be registered in opencode.json"

    def test_anti_essay_has_subagent_guard(self) -> None:
        plugin_path = ROOT / ".opencode" / "plugin" / "enforce-anti-essay.ts"
        if not plugin_path.exists():
            return
        content = plugin_path.read_text()
        has_guard = "isSubagent" in content
        assert has_guard, (
            "E01: enforce-anti-essay.ts must use the shared subagent isolation "
            "guard per enforcement plugin policy"
        )

    def test_anti_essay_disabled_via_env_var(self) -> None:
        plugin_path = ROOT / ".opencode" / "plugin" / "enforce-anti-essay.ts"
        if not plugin_path.exists():
            return
        content = plugin_path.read_text()
        has_disable = "GLUDD_ANTI_ESSAY_ENFORCE" in content
        assert has_disable, "E20: enforce-anti-essay.ts must support GLUDD_ANTI_ESSAY_ENFORCE=0 to disable"
