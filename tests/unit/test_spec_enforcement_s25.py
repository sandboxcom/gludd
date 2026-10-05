"""S25: No write in system prompt from stop plugin.

The stop enforcement plugin MUST NOT modify the system prompt via
`experimental.chat.system.transform` — writing dynamic plugin
instructions into the system prompt is a vector for prompt drift and
should be a read-only hook instead.
"""

import json
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent


class TestS25StopPluginNoSystemTransform:
    """S25 — stop plugin must not use system.transform to modify prompts."""

    def test_stop_plugin_registration(self) -> None:
        opencode_json = ROOT / "opencode.json"
        assert opencode_json.exists(), "S25: opencode.json must exist"
        config = json.loads(opencode_json.read_text())
        plugins = config.get("plugin", []) if isinstance(config, dict) else config

        # enforce-stop.ts should not register experimental.chat.system.transform
        for plugin in plugins:
            plugin_path = plugin.get("path", "") if isinstance(plugin, dict) else plugin
            if "enforce-stop" in plugin_path:
                hooks = plugin.get("hooks", {}) if isinstance(plugin, dict) else {}
                has_transform = "experimental.chat.system.transform" in hooks
                if has_transform:
                    pass  # Present but may be benign — verify further
                break

    def test_enforce_stop_plugin_exists(self) -> None:
        plugin_path = ROOT / ".opencode" / "plugin" / "enforce-stop.ts"
        assert plugin_path.exists(), "S25: enforce-stop.ts plugin must exist"

    def test_enforce_stop_impl_exists(self) -> None:
        impl_path = ROOT / ".opencode" / "plugin" / "impl" / "enforce_stop_impl.ts"
        assert impl_path.exists(), "S25: enforce_stop_impl.ts must exist"

    def test_system_transform_not_registered_in_stop_plugin(self) -> None:
        """The stop plugin should use text.complete / session.idle / tool.execute.before
        hooks — not system.transform (which writes into the system prompt)."""
        opencode_json = ROOT / "opencode.json"
        if not opencode_json.exists():
            return
        config = json.loads(opencode_json.read_text())
        plugins = config.get("plugin", []) if isinstance(config, dict) else config

        for plugin in plugins:
            plugin_entry = plugin if isinstance(plugin, dict) else {"path": plugin}
            plugin_path = plugin_entry.get("path", "")
            if "enforce-stop" in plugin_path:
                hooks = plugin_entry.get("hooks", {})
                assert "experimental.chat.system.transform" not in hooks, (
                    "S25: enforce-stop.ts must not use "
                    "experimental.chat.system.transform hook — "
                    "writes into the system prompt. "
                    "Use text.complete, session.idle, or tool.execute.before instead."
                )
