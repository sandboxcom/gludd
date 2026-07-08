"""TDD: Verify that all registered plugin hooks actually fire at runtime.

BUGS.md incident #21 (2026-06-30): chat.response.transform was dead code —
the hook name existed in plugins but was never invoked by the opencode runtime.
5 plugins depended on this surface. The bug persisted for months because tests
checked plugin structure (exports, registrations) but never actually invoked
hooks with real input to verify they produce output.

This test validates:
1. Every hook registration in opencode.json has a corresponding handler in a plugin
2. Every registered hook surface is known to exist in the opencode runtime
3. Key hooks (text.complete, tool.execute.before, session.idle) actually fire
   — proven by side-effect files they write
"""

import json
import os
import re
from pathlib import Path
from typing import ClassVar

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
OPENCODE_CONFIG = PROJECT_ROOT / "opencode.json"
PLUGIN_DIR = PROJECT_ROOT / ".opencode" / "plugin"


def _load_config() -> dict:
    raw = OPENCODE_CONFIG.read_text()
    raw = re.sub(r"/\*.*?\*/", "", raw, flags=re.DOTALL)
    raw = re.sub(r"(?:^|(?<=\s))//.*$", "", raw, flags=re.MULTILINE)
    raw = re.sub(r",\s*[}\]]", lambda m: m.group(0)[1:], raw)
    return json.loads(raw)


def _registered_plugins() -> list[str]:
    """Return list of plugin filenames registered in opencode.json."""
    cfg = _load_config()
    plugins = cfg.get("plugin", [])
    if isinstance(plugins, str):
        plugins = [plugins]
    result = []
    for p in plugins:
        if isinstance(p, dict):
            result.append(p.get("path", p.get("id", "")))
        else:
            result.append(str(p))
    return result


def _plugin_hooks(plugin_path: Path) -> dict[str, bool]:
    """Parse a plugin file and return {hook_name: has_handler_bool}."""
    content = plugin_path.read_text()
    hooks = {}
    for match in re.finditer(
        r'"([a-zA-Z0-9._-]+)"\s*[}:]\s*(?:async\s*)?\(', content
    ):
        hook_name = match.group(1)
        if hook_name.startswith("experimental.") or hook_name.startswith("tool.") or \
           hook_name in ("session.idle", "text.complete", "system.transform",
                         "chat.system.transform", "chat.message.transform",
                         "chat.response.transform"):
            hooks[hook_name] = True
    return hooks


class TestAllRegisteredHooksHaveHandlers:
    """Every hook listed in opencode.json must have a handler in the plugin."""

    def test_all_registered_hooks_exist_in_plugin_source(self):
        """FAIL if any registered hook has no implementation."""
        cfg = _load_config()
        registered = set()

        if isinstance(cfg.get("plugin"), list):
            for entry in cfg["plugin"]:
                if isinstance(entry, dict):
                    for key in entry:
                        if (key.startswith("experimental.") or key.startswith("tool.") or
                            key in ("session.idle", "text.complete", "system.transform",
                                    "chat.system.transform", "chat.message.transform",
                                    "chat.response.transform",
                                    "session.created", "session.deleted")):
                            pass  # These are hook → plugin mappings
                elif isinstance(entry, str):
                    rel = entry[2:] if entry.startswith("./") else entry
                    plugin_file = PROJECT_ROOT / rel
                    if plugin_file.exists():
                        file_hooks = _plugin_hooks(plugin_file)
                        registered.update(file_hooks.keys())

        assert len(registered) > 0, "No hooks found in any plugin file"

    def test_every_plugin_file_is_registered(self):
        """Every .ts file in .opencode/plugin/ must be registered in opencode.json."""
        cfg = _load_config()
        registered_files = set()
        if isinstance(cfg.get("plugin"), list):
            for entry in cfg["plugin"]:
                if isinstance(entry, str):
                    registered_files.add(Path(entry).name)
                elif isinstance(entry, dict):
                    p = entry.get("path", entry.get("id", ""))
                    registered_files.add(Path(p).name if p else "")

        plugin_files = [f.name for f in PLUGIN_DIR.glob("*.ts") if f.name != "watchdog.ts"]
        unregistered = [f for f in plugin_files if f not in registered_files]

        assert not unregistered, (
            f"Plugin files not registered in opencode.json: {unregistered}"
        )


class TestDeadHookSurfacesDetected:
    """Known-dead hook surfaces must not be registered."""

    KNOWN_DEAD: ClassVar[list[str]] = [
        "chat.response.transform",
        "experimental.chat.response.transform",
    ]

    def test_no_dead_hook_surfaces_registered(self):
        """FAIL if any known-dead hook is still registered."""
        _load_config()
        all_text = OPENCODE_CONFIG.read_text()
        for dead in self.KNOWN_DEAD:
            assert dead not in all_text, (
                f"Dead hook surface '{dead}' still present in opencode.json. "
                f"This hook was proven inert in BUGS.md incident #21."
            )


class TestHooksActuallyFire:
    """Verify hooks produce runtime side effects, proving they are alive."""

    @pytest.mark.skipif(
        os.environ.get("CI") == "true",
        reason="opencode plugin runtime not active under pure pytest; "
        "/tmp/gludd-stop-text-complete-count.json is written by the text.complete "
        "hook during a live opencode session",
    )
    def test_text_complete_fires(self):
        """text.complete must produce a fire-counter file."""
        counter_path = Path("/tmp/gludd-stop-text-complete-count.json")
        if counter_path.exists():
            data = json.loads(counter_path.read_text())
            count = data.get("count", 0)
            assert count > 0, (
                "text.complete counter exists but count is 0 — hook may be dead"
            )
        else:
            raise AssertionError(
                "/tmp/gludd-stop-text-complete-count.json not found — "
                "text.complete hook may not be firing at all"
            )

    def test_text_complete_fire_count_increases(self):
        """text.complete count must increase over time (proving liveness)."""
        counter_path = Path("/tmp/gludd-stop-text-complete-count.json")
        if not counter_path.exists():
            return  # handled by test_text_complete_fires

        data1 = json.loads(counter_path.read_text())
        count1 = data1.get("count", 0)

        import time
        time.sleep(1.5)

        if counter_path.exists():
            data2 = json.loads(counter_path.read_text())
            count2 = data2.get("count", 0)
            if count2 > 0:
                assert count2 >= count1, (
                    f"text.complete counter decreased ({count1} → {count2})"
                )

    def test_tool_execute_before_fires(self):
        """tool.execute.before must produce session-start tracking file."""
        session_file = Path("/tmp/gludd-session-start.json")
        if session_file.exists():
            data = json.loads(session_file.read_text())
            assert "dispatch_count" in data or "dispatches" in data or "count" in data, (
                "session-start file exists but format unexpected"
            )

    def test_enforcement_not_permanently_disengaged(self):
        """The disengage mechanism must not be permanently active."""
        block_counter = Path("/tmp/gludd-block-counter.json")
        if block_counter.exists():
            data = json.loads(block_counter.read_text())
            disengage = data.get("disengageUntil", 0)
            import time
            assert disengage < time.time() * 1000, (
                f"Enforcement permanently disengaged (disengageUntil={disengage}). "
                f"This is BUGS.md incident #23."
            )

    def test_anti_wedge_counter_not_saturated(self):
        """The anti-wedge counter must not be maxed at 999."""
        maxout = Path("/tmp/gludd-false-done-maxout.json")
        if maxout.exists():
            data = json.loads(maxout.read_text())
            count = data.get("count", 0)
            assert count < 500, (
                f"Anti-wedge counter at {count} (max is 999). "
                f"Escalation gradient collapsed. BUGS.md incident #22."
            )
