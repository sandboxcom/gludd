"""E2E boot test: verify opencode starts cleanly with the full .opencode/
plugin suite in place.

Codified 2026-07-23 after a regression where invalid hook names
(``session.idle``, bare ``text.complete``) and auto-loaded ``_exports.ts``
companions crashed opencode at boot with
``TypeError: undefined is not an object (evaluating 'N.event')``.

What this test proves
---------------------
1. ``opencode serve`` boots and listens on a TCP port with every plugin in
   ``opencode.json`` loaded (the same plugin-loading path the TUI uses).
2. The boot log contains no fatal error patterns:
   - ``N.event`` / ``H.config`` / ``H.dispose`` (Plugin.add registry crash)
   - ``TypeError`` from the opencode bun runtime
   - ``failed to load plugin`` (auto-discovery picking up non-plugin files)
3. ``opencode --print-logs`` (the TUI boot path) also boots without those
   fatal patterns.
4. ``scripts/check_plugin_hooks.py`` reports no invalid hook names in any
   plugin's returned Hooks object.
5. Every plugin entry in ``opencode.json`` resolves to a file on disk whose
   default export is a function (the Plugin shape opencode expects).

Why ``opencode serve`` and not ``opencode run``
-----------------------------------------------
``opencode run`` in 1.17.9 has an unrelated upstream bug: it crashes with
the same ``N.event`` TypeError for ANY plugin, including the SDK's own
``example.js``. The TUI and ``serve`` use a different (working) plugin
loader path. This test targets the path users actually hit.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import signal
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from tests.e2e.state_isolation import build_state_environment, signal_process_group

pytestmark = pytest.mark.xdist_group("opencode-live")

ROOT = Path(__file__).resolve().parents[2]
OPENCODE_JSON = ROOT / "opencode.json"
PLUGIN_DIR = ROOT / ".opencode" / "plugin"
PLUGINS_DIR = ROOT / ".opencode" / "plugins"
HOOK_CHECKER = ROOT / "scripts" / "check_plugin_hooks.py"

# Patterns that indicate the boot-time plugin crash. Any one of these in the
# serve/TUI log is a hard failure.
#
# We deliberately do NOT match the generic ``TypeError: undefined is not an
# object`` — opencode 1.17.9 has several unrelated upstream bugs that throw
# the same generic TypeError (e.g. ``n.provider`` from ConfigHttpApi.providers
# when no provider credentials are configured). The plugin crash has a
# distinctive signature: the minified symbol is one of ``N.event`` /
# ``H.config`` / ``H.dispose`` AND the stack trace includes ``Plugin.add`` or
# ``PluginBoot``. Match THAT, not the generic TypeError.
FATAL_BOOT_PATTERNS = [
    # The minified property-access symbols from Plugin.add / PluginBoot.boot.
    re.compile(r"evaluating 'N\.event'"),
    re.compile(r"evaluating 'H\.config'"),
    re.compile(r"evaluating 'H\.dispose'"),
    # Plugin-loading failure: opencode couldn't even call the default export.
    re.compile(r"failed to load plugin"),
    # Stack-trace anchor: the boot crash always includes Plugin.add/PluginBoot
    # in the bun stack. Anchor on those names so a future regression with a
    # different minifier symbol is still caught.
    re.compile(r"Plugin\.add|PluginBoot\.boot"),
]

LISTEN_RE = re.compile(r"listening on http://127\.0\.0\.1:(\d+)")
BOOT_RE = re.compile(r"opencode server listening")

_STATE_ENV_FILENAMES = {
    "GLUDD_ADDITIVE_TASK_STATE": "additive-task.json",
    "GLUDD_ALIVE_PATH": "plugin-alive.json",
    "GLUDD_BLOCK_COUNTER_FILE": "block-counter.json",
    "GLUDD_BLOCK_REASON_FILE": "block-reason.json",
    "GLUDD_CI_CACHE_PATH": "ci-cache.json",
    "GLUDD_CI_POLL_STATE": "ci-poll.json",
    "GLUDD_COMMIT_LOCK_PATH": "commit.lock",
    "GLUDD_DIRECTIVE_STATE": "directives.json",
    "GLUDD_DISENGAGE_AUDIT_PATH": "disengage-audit.jsonl",
    "GLUDD_DISENGAGE_NEXT_PATH": "disengage-next.json",
    "GLUDD_DISENGAGE_PATH": "disengage.json",
    "GLUDD_DISPATCH_OUTCOMES_FILE": "dispatch-outcomes.json",
    "GLUDD_DISPATCH_STATE_FILE": "dispatch-state.json",
    "GLUDD_ENHANCEMENT_RATIO_STATE": "enhancement-ratio.json",
    "GLUDD_FALSE_DONE_BLOCKS_FILE": "false-done.json",
    "GLUDD_FORCE_DELEGATE_STATE": "force-delegate.json",
    "GLUDD_FORCE_DISPATCH_PATH": "force-dispatch.json",
    "GLUDD_GATE_REFRESH_LEASE_PATH": "gate-refresh.json",
    "GLUDD_LAST_TEST_RESULT_FILE": "last-test.json",
    "GLUDD_MAINTHREAD_STREAK_FILE": "mainthread-streak.json",
    "GLUDD_MODEL_UTIL_STATE": "model-util.json",
    "GLUDD_MULTITASK_DISPATCH_COUNT_FILE": "multitask-dispatch-count.json",
    "GLUDD_MULTITASK_STATE_FILE": "multitask.json",
    "GLUDD_PERSIST_STOP_BLOCK_FILE": "persist-stop.json",
    "GLUDD_POST_RESULTS_STATE_FILE": "post-results.json",
    "GLUDD_PUSH_STATE_FILE": "push-state.json",
    "GLUDD_READ_GRIND_FILE": "read-grind.json",
    "GLUDD_RELEASE_COMPLETENESS_FILE": "release-completeness.json",
    "GLUDD_RELEASE_DEADLINE_STATE": "release-deadline.json",
    "GLUDD_SESSION_STATE": "session-start.json",
    "GLUDD_STAGNANT_STATE": "stagnant.json",
    "GLUDD_STOP_CHALLENGE_FILE": "stop-challenge.jsonl",
    "GLUDD_STOP_STATE_FILE": "stop.json",
    "GLUDD_STOP_STATE_PATH": "stop-compat.json",
    "GLUDD_STOP_TEXT_COMPLETE_COUNT": "stop-text-count.json",
    "GLUDD_STOP_TOOL_COUNTS_FILE": "stop-tool-counts.json",
    "GLUDD_STREAK_FILE": "tool-streak.json",
    "GLUDD_SUBAGENT_MARKER_PREFIX": "subagent-",
    "GLUDD_TASK_DEADLINE_STATE": "task-deadlines.json",
    "GLUDD_TASK_DEADLINE_WARNINGS": "task-deadlines.log",
    "GLUDD_TASK_STALE_FILE": "task-stale.json",
    "GLUDD_TEXT_ONLY_STATE_FILE": "text-only.json",
    "GLUDD_TODOWRITE_STATE": "todowrite.json",
    "GLUDD_TODOWRITE_STATE_PATH": "todowrite-compat.json",
    "GLUDD_WATCHDOG_CI_FILE": "watchdog-ci.json",
}


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _has_opencode_binary() -> bool:
    try:
        subprocess.run(
            ["opencode", "--version"],
            capture_output=True, timeout=5, check=False,
        )
        return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _isolated_boot_environment(state_dir: Path) -> dict[str, str]:
    """Build a complete per-process state namespace for an OpenCode boot."""
    return build_state_environment(
        state_dir,
        _STATE_ENV_FILENAMES,
        extra={
            "GLUDD_HOT_MODULE_PREFIX": str(state_dir / "hot-"),
            "GLUDD_PROJECT_ROOT": str(ROOT),
            "OPENCODE_SERVER_PASSWORD": "test-only",  # pragma: allowlist secret
            "XDG_CACHE_HOME": str(state_dir / "xdg-cache"),
            "XDG_DATA_HOME": str(state_dir / "xdg-data"),
            "XDG_STATE_HOME": str(state_dir / "xdg-state"),
        },
    )


def _check_plugin_default_export(plugin_path: Path) -> subprocess.CompletedProcess[str]:
    """Run node to verify the plugin's default export is a function."""
    file_url = json.dumps("file://" + str(plugin_path))
    script = (
        "import(" + file_url + ").then(m => {"
        "  if (typeof m.default !== 'function')"
        "    throw new Error('default export is ' + typeof m.default);"
        "}).catch(e => { console.error(e.message); process.exit(1); });"
    )
    # Write to a temp .mjs so node treats the script as a real module entry
    # (eval mode rejects dynamic import of file:// URLs in some versions).
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".mjs", delete=False, dir=str(ROOT),
    ) as tf:
        tf.write(script)
        tmp_path = tf.name
    try:
        return subprocess.run(
            ["node", tmp_path],
            cwd=str(ROOT), capture_output=True, text=True, timeout=15,
        )
    finally:
        with contextlib.suppress(OSError):
            os.unlink(tmp_path)


def _boot_serve(port: int, timeout: float = 20.0) -> tuple[int, str]:
    """Start ``opencode serve`` and return (return code, stdout+stderr).

    Returns as soon as the listening line prints or after ``timeout`` seconds.
    """
    with tempfile.TemporaryDirectory(prefix="gludd-opencode-serve-") as state_dir_text:
        env = _isolated_boot_environment(Path(state_dir_text))
        with subprocess.Popen(
            [
                "opencode", "serve",
                "--print-logs",
                "--log-level", "ERROR",
                "--port", str(port),
                "--hostname", "127.0.0.1",
            ],
            cwd=str(ROOT),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
        ) as proc:
            deadline = time.time() + timeout
            buf: list[str] = []
            try:
                while time.time() < deadline:
                    if proc.poll() is not None:
                        rest = proc.stdout.read() if proc.stdout else ""
                        buf.append(rest)
                        break
                    line = proc.stdout.readline() if proc.stdout else ""
                    if not line:
                        time.sleep(0.1)
                        continue
                    buf.append(line)
                    if BOOT_RE.search(line) or LISTEN_RE.search(line):
                        break
            finally:
                signal_process_group(proc, signal.SIGTERM)
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    signal_process_group(proc, signal.SIGKILL)
                    proc.wait(timeout=5)
            return proc.returncode, "".join(buf)


def _boot_tui(timeout: float = 12.0) -> tuple[int, str]:
    """Start the default ``opencode`` (TUI) command, return (rc, output).

    The TUI needs a tty; we feed it stdin=closed so it exits or we kill it
    after ``timeout`` seconds. The goal is to capture the boot log.
    """
    with tempfile.TemporaryDirectory(prefix="gludd-opencode-tui-") as state_dir_text:
        env = _isolated_boot_environment(Path(state_dir_text))
        with subprocess.Popen(
            ["opencode", "--print-logs", "--log-level", "ERROR"],
            cwd=str(ROOT),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
        ) as proc:
            try:
                stdout, _ = proc.communicate(timeout=timeout)
                return proc.returncode, stdout or ""
            except subprocess.TimeoutExpired as exc:
                signal_process_group(proc, signal.SIGTERM)
                try:
                    stdout, _ = proc.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    signal_process_group(proc, signal.SIGKILL)
                    stdout, _ = proc.communicate()
                initial = exc.stdout or ""
                if isinstance(initial, bytes):
                    initial = initial.decode(errors="replace")
                return proc.returncode, str(initial) + (stdout or "")


def _assert_no_fatal_patterns(log: str, context: str) -> None:
    failures: list[str] = []
    for pat in FATAL_BOOT_PATTERNS:
        if pat.search(log):
            failures.append(pat.pattern)
    if failures:
        pytest.fail(
            "fatal boot pattern(s) detected in " + context + ": "
            + str(failures) + "\n--- log tail ---\n" + log[-2000:]
        )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _has_opencode_binary(), reason="opencode binary not on PATH")
def test_opencode_serve_boots_clean_with_full_plugin_suite() -> None:
    """The primary e2e: ``opencode serve`` boots with all plugins loaded.

    This is the canonical plugin-loading path the TUI uses internally.
    """
    port = _free_port()
    _rc, log = _boot_serve(port)
    assert BOOT_RE.search(log) or LISTEN_RE.search(log), (
        "opencode serve did not print a listening line within timeout.\n"
        "--- log tail ---\n" + log[-2000:]
    )
    _assert_no_fatal_patterns(log, "opencode serve")


def test_boot_environment_namespaces_every_mutable_state(tmp_path: Path) -> None:
    """Boots must not reuse a developer session's plugin or OpenCode state."""
    env = _isolated_boot_environment(tmp_path)

    isolated_paths = [env[key] for key in _STATE_ENV_FILENAMES]
    isolated_paths.extend(
        env[key]
        for key in ("GLUDD_HOT_MODULE_PREFIX", "XDG_CACHE_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME")
    )
    assert all(Path(value).is_relative_to(tmp_path) for value in isolated_paths)
    assert env["GLUDD_PROJECT_ROOT"] == str(ROOT)

    with pytest.raises(ValueError, match="escapes isolation root"):
        build_state_environment(tmp_path, {"GLUDD_BAD_STATE": "../ambient.json"})


def test_boot_helpers_fail_closed_on_missing_binary_and_fatal_log(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Boot preflights must report unavailable binaries and plugin crashes."""
    def missing_binary(*_args: object, **_kwargs: object) -> None:
        raise FileNotFoundError("opencode")

    monkeypatch.setattr(subprocess, "run", missing_binary)
    assert _has_opencode_binary() is False
    with pytest.raises(pytest.fail.Exception, match="fatal boot pattern"):
        _assert_no_fatal_patterns("failed to load plugin", "isolated boot")


def test_serve_helper_closes_an_early_exit_without_signalling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An already-exited server must have its pipes reaped without a broad kill."""
    process = MagicMock()
    process.__enter__.return_value = process
    process.poll.return_value = 7
    process.returncode = 7
    process.stdout.read.return_value = "early exit\n"

    def open_process(*_args: object, **_kwargs: object) -> MagicMock:
        return process

    monkeypatch.setattr(subprocess, "Popen", open_process)
    rc, log = _boot_serve(12345, timeout=0.01)

    assert (rc, log) == (7, "early exit\n")
    process.wait.assert_called_once_with(timeout=5)


def test_serve_helper_escalates_only_its_process_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A silent server gets TERM then KILL within its isolated process group."""
    process = MagicMock()
    process.__enter__.return_value = process
    process.poll.return_value = None
    process.returncode = -int(signal.SIGKILL)
    process.stdout.readline.return_value = ""
    process.wait.side_effect = [
        subprocess.TimeoutExpired(cmd="opencode serve", timeout=5),
        process.returncode,
    ]
    times = iter((0.0, 0.0, 2.0))
    sent: list[signal.Signals] = []

    def open_process(*_args: object, **_kwargs: object) -> MagicMock:
        return process

    def now() -> float:
        return next(times)

    def no_sleep(_seconds: float) -> None:
        return None

    def record_signal(_process: subprocess.Popen[str], sig: signal.Signals) -> None:
        sent.append(sig)

    monkeypatch.setattr(subprocess, "Popen", open_process)
    monkeypatch.setattr(time, "time", now)
    monkeypatch.setattr(time, "sleep", no_sleep)
    monkeypatch.setattr(sys.modules[__name__], "signal_process_group", record_signal)

    rc, log = _boot_serve(12345, timeout=1.0)

    assert (rc, log) == (-int(signal.SIGKILL), "")
    assert sent == [signal.SIGTERM, signal.SIGKILL]


def test_tui_helper_covers_clean_exit_and_bounded_escalation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TUI cleanup preserves output and escalates only after bounded waits."""
    clean_process = MagicMock()
    clean_process.__enter__.return_value = clean_process
    clean_process.returncode = 0
    clean_process.communicate.return_value = ("clean\n", "")

    def open_clean_process(*_args: object, **_kwargs: object) -> MagicMock:
        return clean_process

    monkeypatch.setattr(subprocess, "Popen", open_clean_process)
    assert _boot_tui(timeout=0.01) == (0, "clean\n")

    stuck_process = MagicMock()
    stuck_process.__enter__.return_value = stuck_process
    stuck_process.returncode = -int(signal.SIGKILL)
    stuck_process.communicate.side_effect = [
        subprocess.TimeoutExpired(cmd="opencode", timeout=0.01, output=b"partial\n"),
        subprocess.TimeoutExpired(cmd="opencode", timeout=5),
        ("tail\n", ""),
    ]
    sent: list[signal.Signals] = []

    def open_stuck_process(*_args: object, **_kwargs: object) -> MagicMock:
        return stuck_process

    def record_signal(_process: subprocess.Popen[str], sig: signal.Signals) -> None:
        sent.append(sig)

    monkeypatch.setattr(subprocess, "Popen", open_stuck_process)
    monkeypatch.setattr(sys.modules[__name__], "signal_process_group", record_signal)

    assert _boot_tui(timeout=0.01) == (
        -int(signal.SIGKILL),
        "partial\ntail\n",
    )
    assert sent == [signal.SIGTERM, signal.SIGKILL]


def test_state_helper_handles_empty_overrides_and_exited_process(
    tmp_path: Path,
) -> None:
    """Optional state inputs and already-dead process groups stay fail-safe."""
    assert build_state_environment(tmp_path, {}, base={}) == {}
    process = MagicMock()
    process.poll.return_value = 0
    signal_process_group(process, signal.SIGTERM)


@pytest.mark.skipif(not _has_opencode_binary(), reason="opencode binary not on PATH")
def test_opencode_tui_boots_clean_with_full_plugin_suite() -> None:
    """The user-facing path: ``opencode`` (default TUI command)."""
    _rc, log = _boot_tui()
    _assert_no_fatal_patterns(log, "opencode TUI")


def test_no_invalid_hook_names_in_plugins() -> None:
    """``scripts/check_plugin_hooks.py`` must report clean.

    Catches the regression class where a plugin returns a Hooks object
    containing a key that opencode 1.17.9's Plugin.add registry doesn't
    recognize (e.g. ``session.idle``, bare ``text.complete``).
    """
    result = subprocess.run(
        [sys.executable, str(HOOK_CHECKER)],
        cwd=str(ROOT), capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, (
        "invalid hook names detected:\n" + result.stdout + "\n" + result.stderr
    )


def test_opencode_json_plugin_entries_resolve_and_export_function() -> None:
    """Every entry in opencode.json ``plugin`` array must:
    1. Resolve to a file on disk.
    2. Have a default export that is a function (the Plugin shape).
    """
    cfg = json.loads(OPENCODE_JSON.read_text())
    plugins = cfg.get("plugin", [])
    assert plugins, "opencode.json has no plugin entries"
    failures: list[str] = []
    for entry in plugins:
        path_str = entry if isinstance(entry, str) else entry[0]
        plugin_path = (ROOT / path_str).resolve()
        if not plugin_path.is_file():
            failures.append(path_str + ": file not found at " + str(plugin_path))
            continue
        check = _check_plugin_default_export(plugin_path)
        if check.returncode != 0:
            err = (check.stderr or check.stdout).strip()
            failures.append(path_str + ": " + err)
    assert not failures, "plugin entry checks failed:\n" + "\n".join(failures)


def test_plugin_entry_audit_reports_missing_and_invalid_plugins(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Plugin preflight must aggregate missing paths and invalid exports."""
    config_path = tmp_path / "opencode.json"
    invalid_plugin = tmp_path / "invalid.ts"
    invalid_plugin.write_text("export default 42")
    config_path.write_text(json.dumps({"plugin": ["missing.ts", ["invalid.ts"]]}))

    def invalid_export(_plugin_path: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=["node"],
            returncode=1,
            stdout="",
            stderr="default export is number",
        )

    module = sys.modules[__name__]
    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setattr(module, "OPENCODE_JSON", config_path)
    monkeypatch.setattr(module, "_check_plugin_default_export", invalid_export)

    with pytest.raises(AssertionError, match=r"file not found.*default export is number"):
        test_opencode_json_plugin_entries_resolve_and_export_function()


def test_no_exports_ts_files_in_plugin_dir() -> None:
    """Companion ``_exports.ts`` files must NOT live in the plugin dir.

    opencode auto-discovers ``.ts`` files under ``.opencode/plugin/`` and
    tries to load each as a plugin. Companion files named ``*_exports.ts``
    have only named exports (no function default) and produce
    ``Plugin export is not a function`` errors at boot.
    """
    if not PLUGIN_DIR.is_dir():
        pytest.skip(".opencode/plugin/ not present")
    exports_files = sorted(PLUGIN_DIR.glob("*_exports.ts"))
    assert not exports_files, (
        "Companion _exports.ts files must live OUTSIDE .opencode/plugin/ "
        "(opencode auto-loads them and crashes on the missing default fn). "
        "Found: "
        + ", ".join(str(p.relative_to(ROOT)) for p in exports_files)
    )


def test_plugin_directory_audit_skips_when_plugin_directory_is_absent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A package without the optional auto-discovery directory is valid."""
    monkeypatch.setattr(sys.modules[__name__], "PLUGIN_DIR", tmp_path / "missing")
    with pytest.raises(pytest.skip.Exception, match="not present"):
        test_no_exports_ts_files_in_plugin_dir()
