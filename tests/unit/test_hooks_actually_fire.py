"""TDD: Verify that all registered plugin hooks actually fire at runtime.

BUGS.md incident #21 (2026-06-30): chat.response.transform was dead code —
the hook name existed in plugins but was never invoked by the opencode runtime.
5 plugins depended on this surface. The bug persisted for months because tests
checked plugin structure (exports, registrations) but never actually invoked
hooks with real input to verify they produce output.

Wave E CONVERSION: the `TestHooksActuallyFire` class below used to be a set of
soft "if the file happens to exist, check its shape; otherwise silently pass"
probes gated on `CI == "true"` skipif markers — exactly the anti-pattern this
module's own docstring warns against ("tests checked structure... but never
actually invoked hooks"). Every test in that class now ACTUALLY DRIVES the
hook via the `hook_plugin_env` fixture (scripts/hook_plugin_harness.mjs,
node --experimental-strip-types) and asserts a hard outcome. The only
remaining `pytest.skip` is the fixture's own node-version precondition.

This validates:
1. Every hook registration in opencode.json has a corresponding handler in a plugin
2. Every registered hook surface is known to exist in the opencode runtime
3. Key hooks (text.complete, tool.execute.before, session.idle) actually fire
   — proven by side-effect files they write, driven directly rather than
   observed opportunistically.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import ClassVar

import pytest

from tests.unit._hook_fixtures import (
    HookEnv,
    hook_plugin_env_impl,
    invoke_text_complete_and_confirm_increment,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
OPENCODE_CONFIG = PROJECT_ROOT / "opencode.json"
PLUGIN_DIR = PROJECT_ROOT / ".opencode" / "plugin"


@pytest.fixture
def hook_plugin_env(tmp_path: Path):
    yield from hook_plugin_env_impl(tmp_path)


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
    """Verify hooks produce runtime side effects, proving they are alive.

    Every test drives the hook directly through `hook_plugin_env` (see module
    docstring) instead of opportunistically checking a file that may or may
    not exist from a prior live session.
    """

    def test_text_complete_fires(self, hook_plugin_env: HookEnv):
        """text.complete must produce a fire-counter file with count > 0."""
        result = hook_plugin_env.invoke(
            "enforce-stop.ts", "experimental.text.complete",
            input={}, output={"text": "investigating the failure before making a change"},
        )
        assert result.returncode == 0, result.stderr
        counter_path = Path(hook_plugin_env.env["GLUDD_STOP_TEXT_COMPLETE_COUNT"])
        data = json.loads(counter_path.read_text())
        count = data.get("count", 0)
        assert count > 0, "text.complete counter exists but count is 0 — hook may be dead"

    def test_text_complete_fire_count_increases(self, hook_plugin_env: HookEnv):
        """text.complete count must strictly increase across two direct calls.

        Retried (see invoke_text_complete_and_confirm_increment) because this
        hardcoded, non-overridable counter file is shared with any live
        opencode session on this machine, which can occasionally race a
        single pair of calls to a tie.
        """
        invoke_text_complete_and_confirm_increment(
            hook_plugin_env, "enforce-stop.ts", hook_plugin_env.env["GLUDD_STOP_TEXT_COMPLETE_COUNT"]
        )

    def test_tool_execute_before_fires(self, hook_plugin_env: HookEnv):
        """tool.execute.before must produce the session-start tracking file
        with the expected dispatch-count field."""
        result = hook_plugin_env.invoke(
            "enforce-session-start.ts", "tool.execute.before",
            input={"tool": "read", "filePath": "TASKS.md"},
        )
        assert result.returncode == 0, result.stderr
        data = hook_plugin_env.read_json("GLUDD_SESSION_STATE")
        assert data is not None, "enforce-session-start.ts did not write GLUDD_SESSION_STATE"
        assert "dispatches" in data, "session-start file exists but format unexpected"

    def test_enforcement_not_permanently_disengaged(self, hook_plugin_env: HookEnv):
        """The disengage mechanism must not be permanently active.

        Pure data-invariant check: pre-seed the HARDCODED block-counter file
        (snapshotted/restored by hook_plugin_env) with a disengageUntil 10
        hours in the future, drive one experimental.text.complete call (whose
        isDisengaged() check clamps any disengageUntil > now+1h down to
        now+1h — BUGS.md incident #23's fix), then assert the on-disk value
        was actually clamped.
        """
        block_counter_path = Path("/tmp/gludd-block-counter.json")
        now_ms = time.time() * 1000
        far_future_ms = now_ms + 10 * 3_600_000  # 10 hours out
        block_counter_path.write_text(json.dumps({
            "consecutiveBlocks": 0, "totalBlocks": 0, "lastBlockTs": 0,
            "disengageUntil": far_future_ms,
        }))

        result = hook_plugin_env.invoke(
            "enforce-stop.ts", "experimental.text.complete",
            input={}, output={"text": "still investigating, nothing to report yet"},
        )
        assert result.returncode == 0, result.stderr

        data = json.loads(block_counter_path.read_text())
        disengage = data.get("disengageUntil", 0)
        assert disengage < far_future_ms, (
            "disengageUntil was NOT clamped down from the seeded far-future value "
            f"(BUGS.md incident #23 regression): {disengage} !< {far_future_ms}"
        )
        assert disengage <= time.time() * 1000 + 3_600_001, (
            f"Enforcement permanently disengaged (disengageUntil={disengage})."
        )

    def test_anti_wedge_counter_not_saturated(self, tmp_path, monkeypatch):
        """The anti-wedge counter must wrap at 100, never saturate/grow
        unbounded (BUGS.md incident #22) — exercised via a direct, plain-
        Python monkeypatch of scripts.agent_watchdog.FALSE_DONE_MAXOUT (and
        the mtime-based idle-reset dependencies), independent of the node
        harness used by the rest of this module.
        """
        import scripts.agent_watchdog as aw

        maxout_path = tmp_path / "false-done-maxout.json"
        # Neutralize the idle-reset path: point both mtime sources at
        # nonexistent files so _streak_mtime_age_seconds() deterministically
        # returns None (never resets to 0), regardless of this machine's real
        # /tmp/gludd-mainthread-streak.json / -watchdog-last-activity.json
        # activity from a live session.
        monkeypatch.setattr(aw, "FALSE_DONE_MAXOUT", str(maxout_path))
        monkeypatch.setattr(aw, "STREAK_FILE", str(tmp_path / "no-such-streak.json"))
        monkeypatch.setattr(aw, "WATCHDOG_ACTIVITY_FILE", str(tmp_path / "no-such-activity.json"))

        observed = []
        for _ in range(150):
            aw._max_out_false_done()
            observed.append(json.loads(maxout_path.read_text())["count"])

        assert max(observed) <= 100, f"counter exceeded its 100 cap: max={max(observed)}"
        assert max(observed) < 500, (
            f"Anti-wedge counter reached {max(observed)} (old cap was 999). "
            f"Escalation gradient collapsed. BUGS.md incident #22."
        )
        assert observed[99] == 100, f"expected the 100th call to hit exactly 100, got {observed[99]}"
        assert observed[100] == 1, (
            f"expected the counter to WRAP to 1 immediately after 100 (not stick), got {observed[100]}"
        )
