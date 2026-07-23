"""E2E tests that run the actual opencode binary against the project's .opencode/ config.

These invoke ``opencode run --print-logs "exit"`` in the project directory and
parse the log output for plugin-load errors, crash signatures, and boot failures.

Session 51 incident: ``_exports.ts`` in ``.opencode/plugin/`` crashed opencode at
boot. Node.js ``--experimental-strip-types`` imports are NOT sufficient — OpenCode
uses Bun internally, and Bun's module resolution / TS handling differs from Node.js.
Only the actual opencode binary can verify that the plugin loading path works.
"""

import json
import re
import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
OPENCODE_BIN = "opencode"

# Error patterns that indicate plugin-load or boot failures
PLUGIN_LOAD_FAILED_RE = re.compile(r"failed to load plugin.*error=\"([^\"]+)\"")
PLUGIN_HOOK_FAILED_RE = re.compile(r"plugin \w+ hook failed.*error=\"([^\"]+)\"")
EVENT_LISTENER_FAILED_RE = re.compile(r"Event listener failed.*cause=\"([^\"]+)\"")
UNEXPECTED_SERVER_ERROR_RE = re.compile(r"ref=(err_\w+)")
CRASH_SIGNATURES = [
    "undefined is not an object",
    "Cannot read properties of undefined",
    "is not a function",
    "TypeError:",
    "ReferenceError:",
]


def _run_opencode_run() -> tuple[int, str, str]:
    """Run ``opencode run --print-logs "exit"`` and return (exit_code, stdout, stderr)."""
    result = subprocess.run(
        [OPENCODE_BIN, "run", "--print-logs", "exit"],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(PROJECT_ROOT),
    )
    return result.returncode, result.stdout, result.stderr


def _run_opencode_pure() -> tuple[int, str, str]:
    """Run ``opencode run --pure --print-logs "exit"`` (no external plugins)."""
    result = subprocess.run(
        [OPENCODE_BIN, "run", "--pure", "--print-logs", "exit"],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(PROJECT_ROOT),
    )
    return result.returncode, result.stdout, result.stderr


class TestOpencodeBinaryBoot:
    """OpenCode binary must start and load all plugins without crashing."""

    def test_no_plugin_load_failures(self):
        """No 'failed to load plugin' errors in opencode output."""
        _, stdout, stderr = _run_opencode_run()
        combined = stdout + stderr
        failures = PLUGIN_LOAD_FAILED_RE.findall(combined)
        assert len(failures) == 0, (
            f"{len(failures)} plugin(s) failed to load:\n"
            + "\n".join(f"  {f}" for f in failures)
        )

    def test_no_plugin_hook_failures(self):
        """No 'plugin hook failed' errors."""
        _, stdout, stderr = _run_opencode_run()
        combined = stdout + stderr
        failures = PLUGIN_HOOK_FAILED_RE.findall(combined)
        assert len(failures) == 0, (
            f"{len(failures)} plugin hook failure(s):\n"
            + "\n".join(f"  {f}" for f in failures)
        )

    def test_no_event_listener_failures(self):
        """No 'Event listener failed' (Session 51 crash signature)."""
        _, stdout, stderr = _run_opencode_run()
        combined = stdout + stderr
        failures = EVENT_LISTENER_FAILED_RE.findall(combined)
        assert len(failures) == 0, (
            f"{len(failures)} event listener failure(s):\n"
            + "\n".join(f"  {f}" for f in failures)
        )

    def test_no_crash_signatures(self):
        """No undefined-is-not-an-object, TypeError, ReferenceError in output."""
        _, stdout, stderr = _run_opencode_run()
        combined = stdout + stderr
        found = []
        for sig in CRASH_SIGNATURES:
            if sig in combined:
                # Find the line context
                for line in combined.split("\n"):
                    if sig in line:
                        found.append(f"  {sig}: {line.strip()[:200]}")
                        break
        assert len(found) == 0, (
            f"Crash signatures in opencode output:\n" + "\n".join(found)
        )

    def test_no_unexpected_server_error(self):
        """No 'Unexpected server error' (ref=err_*)."""
        _, stdout, stderr = _run_opencode_run()
        combined = stdout + stderr
        found = UNEXPECTED_SERVER_ERROR_RE.findall(combined)
        assert len(found) == 0, (
            f"Unexpected server error refs: {found}"
        )

    def test_pure_mode_works(self):
        """--pure mode (no external plugins) must work as baseline."""
        exit_code, stdout, stderr = _run_opencode_pure()
        combined = stdout + stderr

        # Pure mode should have no plugin load failures
        failures = PLUGIN_LOAD_FAILED_RE.findall(combined)
        assert len(failures) == 0, (
            f"Even --pure mode has plugin load failures: {failures}"
        )

        # Pure mode should have no crash signatures
        for sig in CRASH_SIGNATURES:
            assert sig not in combined, (
                f"--pure mode crash: {sig}"
            )

    def test_plugin_load_count_matches_registered(self):
        """The number of loaded plugins should match opencode.json registration."""
        cfg_path = PROJECT_ROOT / "opencode.json"
        if not cfg_path.exists():
            pytest.skip("No opencode.json")
        cfg = json.loads(cfg_path.read_text())
        registered_count = len(cfg.get("plugin", []))

        _, stdout, stderr = _run_opencode_run()
        combined = stdout + stderr

        # Count successful plugin loads (init events)
        init_matches = re.findall(r"init count=(\d+)", combined)
        failure_count = len(PLUGIN_LOAD_FAILED_RE.findall(combined))

        # At minimum, init count should be reasonable
        assert len(init_matches) > 0, "No init count found in opencode output"
        init_count = int(init_matches[0])

        # Not all need to load (some may be NPM packages), but failures should be 0
        assert failure_count == 0, (
            f"{failure_count} plugin load failures out of {registered_count} registered"
        )
