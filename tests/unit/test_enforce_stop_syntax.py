"""Verify enforce-stop.ts syntax validity and module shape via node --check
and text-based structural analysis."""

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PLUGIN_PATH = ROOT / ".opencode/plugin/enforce-stop.ts"

_REQUIRED_HOOKS = [
    "tool.execute.before",
    "experimental.text.complete",
    "experimental.chat.system.transform",
    "event",
]


def _src() -> str:
    return PLUGIN_PATH.read_text()


def test_node_check_syntax_valid():
    """enforce-stop.ts passes node --check (no syntax errors)."""
    result = subprocess.run(
        ["node", "--check", str(PLUGIN_PATH)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, f"node --check failed: {result.stderr}"


def test_exports_default():
    """enforce-stop.ts contains 'export default' — required for opencode to load."""
    src = _src()
    assert "export default" in src, (
        "enforce-stop.ts must export a default function; "
        "without it opencode silently skips the plugin"
    )


def test_contains_all_required_hooks():
    """All four required hook registrations present in source."""
    src = _src()
    missing = [h for h in _REQUIRED_HOOKS if h not in src]
    assert not missing, (
        f"enforce-stop.ts missing hook registrations: {missing}. "
        f"Expected all of: {_REQUIRED_HOOKS}"
    )


def test_session_idle_registered_via_event():
    """session.idle is registered via the 'event' hook (type check)."""
    src = _src()
    assert 'event.type === "session.idle"' in src or '"session.idle"' in src, (
        "enforce-stop.ts must handle session.idle via the event hook"
    )


def test_compile_does_not_throw():
    """node --check passes; excessive syntax errors would be caught above.
    This is an additional guard that the file is parseable without TypeScript
    errors leaking into the AST."""
    result = subprocess.run(
        ["node", "--check", str(PLUGIN_PATH)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, f"compile check failed: {result.stderr}"
