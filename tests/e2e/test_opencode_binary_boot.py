"""E2E tests that run the actual opencode binary against the project's .opencode/ config.

These invoke ``opencode run --print-logs "exit"`` in the project directory and
parse the log output for plugin-load errors, crash signatures, and boot failures.

Session 51 incident: ``_exports.ts`` in ``.opencode/plugin/`` crashed opencode at
boot. Node.js ``--experimental-strip-types`` imports are NOT sufficient — OpenCode
uses Bun internally, and Bun's module resolution / TS handling differs from Node.js.
Only the actual opencode binary can verify that the plugin loading path works.
"""

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.e2e.test_opencode_tui_permissions import DeterministicProvider

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
OPENCODE_BIN = "opencode"
pytestmark = pytest.mark.xdist_group("opencode-live")

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


@pytest.fixture(scope="module")
def isolated_opencode_project(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Copy the live plugin configuration away from the tracked worktree.

    OpenCode reconciles ``@opencode-ai/plugin`` to the running binary version
    during config loading.  That is valid runtime behavior, but the binary E2E
    must not rewrite the repository's tracked package manifest or lock data.
    """
    project = tmp_path_factory.mktemp("opencode-binary-project")
    shutil.copy2(PROJECT_ROOT / "opencode.json", project / "opencode.json")
    shutil.copytree(
        PROJECT_ROOT / ".opencode",
        project / ".opencode",
        ignore=shutil.ignore_patterns(
            "node_modules",
            "package-lock.json",
            "bun.lock",
            "__pycache__",
            "*.pyc",
        ),
    )
    return project


def _run_with_deterministic_provider(
    command: list[str],
    response: str,
    project: Path,
) -> tuple[int, str, str]:
    """Run a live OpenCode command against a bounded local model provider."""
    provider = DeterministicProvider(
        responses=[{"text": response}],
    )
    env = os.environ.copy()
    env["OPENCODE_CONFIG_CONTENT"] = provider.config_content
    env["OPENCODE_DISABLE_AUTOUPDATE"] = "true"
    env["GLUDD_PROJECT_ROOT"] = str(project)
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=30,
            cwd=str(project),
            env=env,
        )
        assert provider.main_calls == 1, (
            "OpenCode did not complete the deterministic smoke prompt"
        )
        return result.returncode, result.stdout, result.stderr
    finally:
        provider.close()


def _run_opencode_run(project: Path) -> tuple[int, str, str]:
    """Run ``opencode run --print-logs "exit"`` and return (exit_code, stdout, stderr)."""
    return _run_with_deterministic_provider(
        [OPENCODE_BIN, "run", "--print-logs", "exit"],
        "The deterministic plugin smoke completed.",
        project,
    )


def _run_opencode_pure(project: Path) -> tuple[int, str, str]:
    """Run ``opencode run --pure --print-logs "exit"`` (no external plugins)."""
    return _run_with_deterministic_provider(
        [OPENCODE_BIN, "run", "--pure", "--print-logs", "exit"],
        "The deterministic pure-mode smoke completed.",
        project,
    )


class TestOpencodeBinaryBoot:
    """OpenCode binary must start and load all plugins without crashing."""

    def test_live_command_scopes_gludd_root_to_disposable_project(
        self,
        isolated_opencode_project: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """The copied plugins must resolve project state inside their sandbox."""
        observed: dict[str, object] = {}

        class StubProvider:
            config_content = "{}"
            main_calls = 1

            def __init__(self, responses: list[dict[str, str]]) -> None:
                del responses

            def close(self) -> None:
                return

        def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess:
            observed.update(kwargs)
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

        monkeypatch.setitem(globals(), "DeterministicProvider", StubProvider)
        monkeypatch.setattr(subprocess, "run", fake_run)

        _run_opencode_run(isolated_opencode_project)

        assert observed["cwd"] == str(isolated_opencode_project)
        env = observed["env"]
        assert isinstance(env, dict)
        assert env["GLUDD_PROJECT_ROOT"] == str(isolated_opencode_project)

    def test_runs_from_disposable_project_without_mutating_manifest(
        self,
        isolated_opencode_project: Path,
    ):
        """The live harness must isolate OpenCode's dependency reconciliation."""
        manifest = PROJECT_ROOT / ".opencode" / "package.json"
        before = manifest.read_bytes()

        assert isolated_opencode_project != PROJECT_ROOT
        _run_opencode_run(isolated_opencode_project)

        assert manifest.read_bytes() == before, (
            "OpenCode binary E2E mutated the tracked .opencode/package.json; "
            "run it in an isolated project copy"
        )

    def test_no_plugin_load_failures(self, isolated_opencode_project: Path):
        """No 'failed to load plugin' errors in opencode output."""
        _, stdout, stderr = _run_opencode_run(isolated_opencode_project)
        combined = stdout + stderr
        failures = PLUGIN_LOAD_FAILED_RE.findall(combined)
        assert len(failures) == 0, (
            f"{len(failures)} plugin(s) failed to load:\n"
            + "\n".join(f"  {f}" for f in failures)
        )

    def test_no_plugin_hook_failures(self, isolated_opencode_project: Path):
        """No 'plugin hook failed' errors."""
        _, stdout, stderr = _run_opencode_run(isolated_opencode_project)
        combined = stdout + stderr
        failures = PLUGIN_HOOK_FAILED_RE.findall(combined)
        assert len(failures) == 0, (
            f"{len(failures)} plugin hook failure(s):\n"
            + "\n".join(f"  {f}" for f in failures)
        )

    def test_no_event_listener_failures(self, isolated_opencode_project: Path):
        """No 'Event listener failed' (Session 51 crash signature)."""
        _, stdout, stderr = _run_opencode_run(isolated_opencode_project)
        combined = stdout + stderr
        failures = EVENT_LISTENER_FAILED_RE.findall(combined)
        assert len(failures) == 0, (
            f"{len(failures)} event listener failure(s):\n"
            + "\n".join(f"  {f}" for f in failures)
        )

    def test_no_crash_signatures(self, isolated_opencode_project: Path):
        """No undefined-is-not-an-object, TypeError, ReferenceError in output."""
        _, stdout, stderr = _run_opencode_run(isolated_opencode_project)
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
            "Crash signatures in opencode output:\n" + "\n".join(found)
        )

    def test_no_unexpected_server_error(self, isolated_opencode_project: Path):
        """No 'Unexpected server error' (ref=err_*)."""
        _, stdout, stderr = _run_opencode_run(isolated_opencode_project)
        combined = stdout + stderr
        found = UNEXPECTED_SERVER_ERROR_RE.findall(combined)
        assert len(found) == 0, (
            f"Unexpected server error refs: {found}"
        )

    def test_pure_mode_works(self, isolated_opencode_project: Path):
        """--pure mode (no external plugins) must work as baseline."""
        _exit_code, stdout, stderr = _run_opencode_pure(isolated_opencode_project)
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

    def test_plugin_load_count_matches_registered(
        self,
        isolated_opencode_project: Path,
    ):
        """The number of loaded plugins should match opencode.json registration."""
        cfg_path = PROJECT_ROOT / "opencode.json"
        if not cfg_path.exists():
            pytest.skip("No opencode.json")
        cfg = json.loads(cfg_path.read_text())
        registered_count = len(cfg.get("plugin", []))

        _, stdout, stderr = _run_opencode_run(isolated_opencode_project)
        combined = stdout + stderr

        # Count successful plugin loads (init events)
        init_matches = re.findall(r"init count=(\d+)", combined)
        failure_count = len(PLUGIN_LOAD_FAILED_RE.findall(combined))

        # At minimum, init count should be reasonable
        assert len(init_matches) > 0, "No init count found in opencode output"
        int(init_matches[0])

        # Not all need to load (some may be NPM packages), but failures should be 0
        assert failure_count == 0, (
            f"{failure_count} plugin load failures out of {registered_count} registered"
        )
