#!/usr/bin/env python3
"""Live boot smoke test for opencode + full .opencode plugin suite.

Codified 2026-07-23 after a regression where invalid hook names crashed
opencode at boot. This is the bash-equivalent of the manual verification:

    opencode serve --port <N> &
    sleep 4
    curl http://127.0.0.1:<N>/api/agent   # triggers PluginBoot.boot
    grep -E "N\\.event|H\\.config|H\\.dispose|failed to load plugin" log

Exit codes:
    0  — opencode booted, /api/agent returned 200, no fatal patterns
    1  — fatal boot pattern detected OR /api/agent returned non-200
    2  — opencode binary not found on PATH (skip, not fail)
    3  — opencode failed to listen OR /api/agent request failed

What this catches:
    - Plugins that crash opencode at boot (the original session.idle bug
      crashed the TUI/run path; serve is more resilient but still
      surfaces plugin-load errors in its log).
    - Plugins that break the /api/agent response (the agent list is the
      first endpoint that exercises the full plugin pipeline).
    - Config-level failures (opencode.json schema issues, missing files).

What this does NOT catch:
    - The ``opencode run`` upstream bug (1.17.9 crashes with ANY plugin
      including the SDK's own example.js — that's an opencode bug, not
      ours). The TUI and serve paths that users actually use work.

Runs <=8 seconds. No pytest dependency, safe for the ``make gate`` hot path.
"""
from __future__ import annotations

import os
import re
import select
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Same patterns as tests/e2e/test_opencode_boot_e2e.py — kept in sync
# deliberately. The plugin crash has a distinctive minified-symbol
# signature; do NOT match the generic TypeError (opencode 1.17.9 has
# unrelated upstream TypeErrors, e.g. n.provider when no creds set).
FATAL_PATTERNS = [
    re.compile(p) for p in (
        r"evaluating 'N\.event'",
        r"evaluating 'H\.config'",
        r"evaluating 'H\.dispose'",
        r"failed to load plugin",
        r"Plugin\.add|PluginBoot\.boot",
    )
]

LISTEN_RE = re.compile(r"listening on http://127\.0\.0\.1:(\d+)")
BOOT_TIMEOUT_S = 12.0


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _read_with_timeout(pipe, timeout_s: float) -> str:
    """Read one line from pipe with a hard timeout. Returns '' on timeout."""
    try:
        ready, _, _ = select.select([pipe], [], [], timeout_s)
        if not ready:
            return ""
        return pipe.readline()
    except (OSError, ValueError):
        return ""


def main() -> int:
    if shutil.which("opencode") is None:
        print("SKIP: opencode binary not on PATH")
        return 2

    port = _free_port()
    env = os.environ.copy()
    # Deliberately do NOT set OPENCODE_SERVER_PASSWORD — the server then
    # runs unsecured on 127.0.0.1, which is what we want for the smoke
    # test (no auth header needed to hit /api/agent).

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

    deadline = time.time() + BOOT_TIMEOUT_S
    buf: list[str] = []
    listened = False
    try:
        while time.time() < deadline:
            if proc.poll() is not None:
                rest = proc.stdout.read() if proc.stdout else ""
                buf.append(rest)
                break
            line = _read_with_timeout(proc.stdout, 0.2)
            if not line:
                continue
            buf.append(line)
            if LISTEN_RE.search(line):
                listened = True
                # Drain any remaining boot output for 1.0s after listening.
                drain_deadline = time.time() + 1.0
                while time.time() < drain_deadline:
                    if proc.poll() is not None:
                        break
                    drain_line = _read_with_timeout(proc.stdout, 0.1)
                    if not drain_line:
                        continue
                    buf.append(drain_line)
                break
    except Exception as e:
        log = "".join(buf)
        print("FAIL: exception during boot: " + str(e))
        print("--- boot log ---")
        print(log[-1500:])
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            proc.kill()
        return 3

    log = "".join(buf)

    hits = [p.pattern for p in FATAL_PATTERNS if p.search(log)]
    if hits:
        print("FAIL: fatal boot pattern(s) detected at boot: " + str(hits))
        print("--- boot log ---")
        print(log[-1500:])
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        return 1

    if not listened:
        print("FAIL: opencode did not print a listening line within "
              + str(BOOT_TIMEOUT_S) + "s")
        print("--- boot log ---")
        print(log[-1500:])
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        return 3

    # Exercise the plugin-loading path: hit /api/agent, which triggers
    # PluginBoot.boot (the exact stack frame where the original N.event
    # crash occurred). A boot log alone does NOT prove plugins load — the
    # crash fires lazily on the first agent request. Server is STILL
    # RUNNING for this phase.
    request_failed = False
    try:
        with urllib.request.urlopen(
            "http://127.0.0.1:{}/api/agent".format(port), timeout=5,
        ) as resp:
            status = resp.status
            body = resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        request_failed = True
        status = 0
        body = ""
        request_err = str(e)

    # Drain any log output generated by the request (plugin load, etc.).
    request_drain_deadline = time.time() + 2.0
    while time.time() < request_drain_deadline:
        if proc.poll() is not None:
            rest = proc.stdout.read() if proc.stdout else ""
            if rest:
                buf.append(rest)
            break
        drain_line = _read_with_timeout(proc.stdout, 0.1)
        if not drain_line:
            continue
        buf.append(drain_line)

    # Now safe to terminate.
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)

    log = "".join(buf)

    if request_failed:
        print("FAIL: /api/agent request failed: " + request_err)
        print("--- boot+request log ---")
        print(log[-2000:])
        return 3

    if status != 200:
        print("FAIL: /api/agent returned HTTP " + str(status))
        print("--- response ---")
        print(body[:500])
        print("--- log ---")
        print(log[-1500:])
        return 1

    # Final check: scan the FULL log (boot + request) for crash patterns.
    hits = [p.pattern for p in FATAL_PATTERNS if p.search(log)]
    if hits:
        print("FAIL: fatal pattern(s) detected after /api/agent: " + str(hits))
        print("--- full log ---")
        print(log[-2000:])
        return 1

    agent_count = body.count('"id":"')
    print("PASS: opencode booted, /api/agent returned HTTP "
          + str(status) + " (" + str(agent_count) + " agents), "
          "no fatal plugin patterns (port {})".format(port))
    return 0


if __name__ == "__main__":
    sys.exit(main())
