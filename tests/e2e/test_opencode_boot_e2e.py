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
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

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


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _has_opencode_binary() -> bool:
    try:
        subprocess.run(
            ["opencode", "--version"],
            capture_output=True, timeout=5, check=False,
        )
        return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _check_plugin_default_export(plugin_path: Path) -> subprocess.CompletedProcess:
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
    import tempfile
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


def _boot_serve(port: int, timeout: float = 20.0) -> tuple[subprocess.Popen, str]:
    """Start ``opencode serve`` and return (proc, stdout+stderr).

    Returns as soon as the listening line prints or after ``timeout`` seconds.
    """
    env = os.environ.copy()
    env["OPENCODE_SERVER_PASSWORD"] = "test-only"  # pragma: allowlist secret
    proc = subprocess.Popen(
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
    )
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
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
    return proc, "".join(buf)


def _boot_tui(timeout: float = 12.0) -> tuple[int, str]:
    """Start the default ``opencode`` (TUI) command, return (rc, output).

    The TUI needs a tty; we feed it stdin=closed so it exits or we kill it
    after ``timeout`` seconds. The goal is to capture the boot log.
    """
    proc = subprocess.Popen(
        ["opencode", "--print-logs", "--log-level", "ERROR"],
        cwd=str(ROOT),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        stdout, _ = proc.communicate(timeout=timeout)
        return proc.returncode, stdout or ""
    except subprocess.TimeoutExpired as exc:
        proc.terminate()
        try:
            stdout, _ = proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
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
    _proc, log = _boot_serve(port)
    assert BOOT_RE.search(log) or LISTEN_RE.search(log), (
        "opencode serve did not print a listening line within timeout.\n"
        "--- log tail ---\n" + log[-2000:]
    )
    _assert_no_fatal_patterns(log, "opencode serve")


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
