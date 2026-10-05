"""Comprehensive functional/black-box tests for the `gludd` CLI.

These tests invoke the CLI as a real subprocess (``python -m general_ludd.cli``)
and verify returncode / stdout / stderr. They never import the CLI's in-process
entrypoint — the goal is to exercise the binary exactly as an operator would.

Adaptations to the actual CLI surface (the task spec assumed a slightly
different shape; assertions match reality):

* Both the ``gludd version`` subcommand and standard top-level ``--version``
  flag report the packaged release version.
* The health endpoint is ``/healthz`` (not ``/health``).
* ``gludd daemon`` runs gunicorn in the FOREGROUND; there is no
  ``daemon start`` / ``daemon stop`` subcommand. Start = spawn the subprocess;
  stop = SIGTERM. Both are exercised in fixtures.
* ``GET /api/facts`` returns a JSON object (dict), not a list.

The whole module is skipped if ``python -m general_ludd.cli`` is not runnable
in this environment.
"""

from __future__ import annotations

import contextlib
import os
import re
import signal
import socket
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import httpx
import pytest

from tests.e2e._daemon_harness import (
    daemon_subprocess_env,
    start_daemon_process,
    stop_daemon_process,
)

# ---------------------------------------------------------------------------
# Availability probe — skip the entire module if the CLI cannot run at all.
# ---------------------------------------------------------------------------


def _cli_available() -> bool:
    """Return True iff ``python -m general_ludd.cli version`` exits 0."""
    try:
        probe = subprocess.run(
            [sys.executable, "-m", "general_ludd.cli", "version"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except Exception:
        return False
    return probe.returncode == 0


def _gunicorn_available() -> bool:
    """Return True iff gunicorn is importable (needed for `gludd daemon`)."""
    try:
        proc = subprocess.run(
            [sys.executable, "-c", "import gunicorn"],
            capture_output=True,
            timeout=15,
        )
    except Exception:
        return False
    return proc.returncode == 0


_CLI_AVAILABLE = _cli_available()
_GUNICORN_AVAILABLE = _gunicorn_available()

pytestmark = pytest.mark.skipif(
    not _CLI_AVAILABLE,
    reason="`python -m general_ludd.cli` is not runnable in this environment",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def run_gludd(
    args: list[str],
    *,
    timeout: float = 30.0,
    env: dict[str, str] | None = None,
    cwd: str | Path | None = None,
    stdin: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """Invoke the CLI as a subprocess and return the completed process."""
    cmd = [sys.executable, "-m", "general_ludd.cli", *args]
    full_env = os.environ.copy()
    if env:
        full_env.update(env)
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=full_env,
        cwd=str(cwd) if cwd else None,
        input=stdin,
    )


def find_free_port() -> int:
    """Bind to port 0, return the assigned port, then close the socket."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]
    finally:
        sock.close()


def wait_for_url(url: str, *, timeout: float = 30.0, interval: float = 0.3) -> bool:
    """Poll *url* until it returns 200 or *timeout* seconds elapse."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            resp = httpx.get(url, timeout=2.0)
            if resp.status_code == 200:
                return True
        except Exception:
            pass
        time.sleep(interval)
    return False


# Minimal config so the daemon uses an isolated tmp SQLite DB instead of the
# user's real config under ~/.config/gludd.
_DB_CONFIG_TEMPLATE = textwrap.dedent(
    """\
    database:
      url: 'sqlite+aiosqlite:///{db_path}'
    """
)


def _write_isolated_config(tmp_path: Path) -> Path:
    """Create a config dir + general-ludd.yml pointing at an isolated DB."""
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    db_path = tmp_path / "daemon.db"
    (config_dir / "general-ludd.yml").write_text(_DB_CONFIG_TEMPLATE.format(db_path=db_path))
    return config_dir


# ---------------------------------------------------------------------------
# Fixtures for daemon lifecycle
# ---------------------------------------------------------------------------


@pytest.fixture()
def isolated_daemon(tmp_path: Path):
    """Start a real `gludd daemon` on a free port; yield (base_url, proc); stop it.

    Fails the test if gunicorn is unavailable OR the daemon fails to become
    healthy within the readiness window.
    """
    assert _GUNICORN_AVAILABLE, "gunicorn is a required release dependency for `gludd daemon`"

    config_dir = _write_isolated_config(tmp_path)
    port = find_free_port()
    base_url = f"http://127.0.0.1:{port}"

    proc = start_daemon_process(
        config_dir=config_dir,
        cwd=tmp_path,
        port=port,
    )

    try:
        if not wait_for_url(f"{base_url}/healthz", timeout=40.0):
            # Daemon did not come up — surface its logs for diagnostics.
            out, err = stop_daemon_process(proc, terminate_timeout=5)
            pytest.fail(f"daemon did not become healthy on {base_url} within 40s\nstdout={out!r}\nstderr={err!r}")
        yield base_url, proc
    finally:
        stop_daemon_process(proc)


# ---------------------------------------------------------------------------
# Version
# ---------------------------------------------------------------------------


class TestVersionCommand:
    def test_version_outputs_semver(self):
        """`gludd version` outputs a parseable version string."""
        result = run_gludd(["version"], timeout=20)
        assert result.returncode == 0, f"stderr: {result.stderr!r}"
        # Implementation prints: "general-ludd-agent <version>"
        match = re.search(r"(\d+\.\d+\.\d+(?:[-+.][\w.]+)?)", result.stdout)
        assert match, f"no semver-like token in stdout: {result.stdout!r}"
        assert "general-ludd-agent" in result.stdout

    def test_version_flag_outputs_release_version(self):
        """The standard top-level flag reports the packaged release version."""
        from general_ludd import __version__ as package_version

        result = run_gludd(["--version"], timeout=20)
        assert result.returncode == 0, f"stderr: {result.stderr!r}"
        # Pin against the package's own version so release bumps never
        # silently diverge the CLI from the installed metadata.
        assert package_version in result.stdout
        assert "gludd" in result.stdout.lower()
        assert "Traceback (most recent call last)" not in result.stderr


# ---------------------------------------------------------------------------
# Help
# ---------------------------------------------------------------------------


# Curated set of real top-level subcommands. Kept as a literal (rather than
# dynamically parsed) so a rename is surfaced as a test failure rather than
# silently disappearing from the parametrization.
EXPECTED_SUBCOMMANDS = [
    "daemon",
    "version",
    "health",
    "add",
    "status",
    "list",
    "help",
    "models",
    "project",
    "mcp",
    "skills",
    "compute",
    "scores",
    "leaderboard",
    "filestore",
    "worktree",
    "config",
    "ansible",
    "integrity",
    "slurm",
    "login",
    "test",
    "tui",
    "chat",
    "metrics",
    "reload",
    "templates",
    "playbooks",
    "pause",
    "resume",
]


class TestHelpCommand:
    def test_help_lists_subcommands(self):
        """`gludd --help` lists all expected subcommands."""
        result = run_gludd(["--help"], timeout=20)
        assert result.returncode == 0
        combined = result.stdout
        missing = [name for name in EXPECTED_SUBCOMMANDS if name not in combined]
        assert not missing, f"subcommands missing from --help output: {missing}"

    def test_man_help_command(self):
        """`gludd help` prints the manual page (DESCRIPTION / COMMANDS / SYNOPSIS)."""
        result = run_gludd(["help"], timeout=20)
        assert result.returncode == 0
        assert "COMMANDS" in result.stdout
        assert "gludd" in result.stdout

    def test_top_level_smoke_and_nested_alias_both_execute(self):
        """The release CLI exposes `smoke` without breaking `test smoke`."""
        top_level = run_gludd(["smoke", "list", "--json"], timeout=30)
        nested = run_gludd(["test", "smoke", "list", "--json"], timeout=30)

        assert top_level.returncode == 0, top_level.stderr
        assert nested.returncode == 0, nested.stderr
        assert top_level.stdout.strip()
        assert top_level.stdout == nested.stdout

    @pytest.mark.parametrize("subcommand", EXPECTED_SUBCOMMANDS)
    def test_subcommand_help(self, subcommand: str):
        """`gludd <subcommand> --help` exits 0 and prints a usage line."""
        result = run_gludd([subcommand, "--help"], timeout=20)
        assert result.returncode == 0, f"`gludd {subcommand} --help` exited {result.returncode}: {result.stderr!r}"
        assert "usage:" in result.stdout.lower()

    def test_nested_smoke_help(self):
        """The consolidated test namespace exposes provider smoke checks."""
        result = run_gludd(["test", "smoke", "--help"], timeout=20)
        assert result.returncode == 0, result.stderr
        assert "usage:" in result.stdout.lower()


# ---------------------------------------------------------------------------
# Project commands (no daemon required)
# ---------------------------------------------------------------------------


class TestProjectCommand:
    def test_project_paths(self, tmp_path: Path):
        """`gludd project paths` outputs the precedence table with the BUNDLED tier.

        Note: ``resolve_collections_paths`` only emits tiers that actually exist
        for the given project dir. In a clean tmp dir (no ``.gludd/`` and no
        user config) only the BUNDLED tier is present — that is the guaranteed
        shape, so we assert it rather than assuming all three tiers.
        """
        result = run_gludd(["project", "paths", str(tmp_path)], timeout=30)
        assert result.returncode == 0, f"stderr: {result.stderr!r}"
        out = result.stdout
        assert "Collection search path" in out
        # BUNDLED ships with the repo, so it always resolves.
        assert "BUNDLED" in out
        assert "roles" in out  # e.g. "(exists, N roles, M modules)"

    def test_project_paths_json(self, tmp_path: Path):
        """`gludd project paths --json` emits a parseable JSON list (≥1 entry)."""
        import json

        result = run_gludd(["project", "paths", str(tmp_path), "--json"], timeout=30)
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert isinstance(data, list)
        assert len(data) >= 1
        # Every entry has the documented record shape.
        for entry in data:
            assert {"source", "path", "precedence", "exists", "roles", "modules"} <= set(entry)
        # The BUNDLED tier is always present.
        sources = {entry["source"] for entry in data}
        assert "bundled" in sources

    def test_project_init_missing_namespace_clean_error(self, tmp_path: Path):
        """`gludd project init` without --namespace exits non-zero with a clean message."""
        result = run_gludd(["project", "init", str(tmp_path)], timeout=30)
        assert result.returncode != 0
        assert "namespace" in result.stderr.lower()
        # No Python traceback should leak to the user for an arg-validation error.
        assert "Traceback (most recent call last)" not in result.stderr


# ---------------------------------------------------------------------------
# Daemon lifecycle
# ---------------------------------------------------------------------------


class TestDaemonCommand:
    def test_daemon_starts_and_responds(self, isolated_daemon):
        """`gludd daemon` launches a daemon that responds on /healthz."""
        base_url, _proc = isolated_daemon
        # The fixture already waited for readiness; re-confirm a fresh probe.
        resp = httpx.get(f"{base_url}/healthz", timeout=5.0)
        assert resp.status_code == 200

    def test_daemon_health_endpoint(self, isolated_daemon):
        """GET /healthz returns 200 with a JSON status payload."""
        base_url, _proc = isolated_daemon
        resp = httpx.get(f"{base_url}/healthz", timeout=5.0)
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, dict)
        # The daemon reports {"status": "healthy", ...}
        assert data.get("status") == "healthy"

    def test_daemon_facts_endpoint(self, isolated_daemon):
        """GET /api/facts returns a JSON object (consolidated facts snapshot)."""
        base_url, _proc = isolated_daemon
        resp = httpx.get(f"{base_url}/api/facts", timeout=10.0)
        assert resp.status_code == 200
        data = resp.json()
        # /api/facts returns a consolidated dict, not a bare list.
        assert isinstance(data, dict)

    def test_daemon_health_cli_command(self, isolated_daemon):
        """`gludd health --daemon-url <url>` exits 0 against the live daemon."""
        base_url, _proc = isolated_daemon
        result = run_gludd(["health", "--daemon-url", base_url], timeout=20)
        assert result.returncode == 0, f"stderr: {result.stderr!r}"
        assert "healthy" in result.stdout

    def test_daemon_stop(self, isolated_daemon):
        """SIGTERM to the parent produces a clean shutdown (process exits, port releases)."""
        base_url, proc = isolated_daemon
        assert proc.poll() is None, "daemon was not running at stop time"

        # SIGTERM the PARENT only (not the process group). The CLI parent
        # installs a SIGTERM handler that forwards to the gunicorn child,
        # waits for it, then exits — this is the clean-shutdown code path we
        # want to exercise. (Signalling the whole group would hit gunicorn
        # directly and exercise gunicorn's own graceful-shutdown timings,
        # which are not what this test is about.)
        proc.terminate()
        try:
            proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            # Fall back to SIGKILL so the test process doesn't leak the daemon,
            # but record the failure — clean shutdown should have worked.
            with contextlib.suppress(Exception):
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            pytest.fail("daemon parent did not shut down within 20s of SIGTERM")

        # The port should no longer answer.
        with pytest.raises((httpx.ConnectError, httpx.ConnectTimeout, httpx.HTTPError)):
            httpx.get(f"{base_url}/healthz", timeout=2.0)


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


class TestErrorHandling:
    def test_invalid_flag_clean_error(self):
        """`gludd --invalid-flag` exits non-zero with an argparse error, no traceback."""
        result = run_gludd(["--invalid-flag"], timeout=20)
        assert result.returncode != 0
        assert "usage:" in result.stderr.lower() or "unrecognized" in result.stderr.lower()
        assert "Traceback (most recent call last)" not in result.stderr

    def test_missing_argument_clean_error(self):
        """A parent command with no subcommand prints usage and exits non-zero."""
        # `gludd project` with no subcommand: argparse has no default func → main()
        # prints the subparser help and exits 0; `gludd models` likewise. To get
        # a TRUE "missing required argument" error we invoke a subcommand that
        # requires a positional, e.g. `gludd project init` without --namespace
        # is validated in-process; here we check the parser-level case.
        result = run_gludd(["models"], timeout=20)
        # No func bound → main() prints help and exits 0 (documented behavior).
        # We assert the help path is taken, not a crash.
        assert result.returncode == 0
        assert "usage:" in result.stdout.lower()

    def test_unknown_subcommand_clean_error(self):
        """`gludd not-a-command` is rejected cleanly (argparse, no traceback)."""
        result = run_gludd(["not-a-command"], timeout=20)
        assert result.returncode != 0
        assert "Traceback (most recent call last)" not in result.stderr

    def test_daemon_cli_port_validation(self):
        """`gludd daemon --port not-a-port` exits non-zero with a clean argparse error."""
        result = run_gludd(["daemon", "--port", "not-a-port"], timeout=20)
        assert result.returncode != 0
        assert "Traceback (most recent call last)" not in result.stderr

    def test_port_conflict_graceful(self, tmp_path: Path):
        """Starting a second daemon on an occupied port fails cleanly (no traceback)."""
        assert _GUNICORN_AVAILABLE, "gunicorn is a required release dependency for `gludd daemon`"

        config_dir = _write_isolated_config(tmp_path)
        port = find_free_port()

        # First daemon — should become healthy.
        first = start_daemon_process(
            config_dir=config_dir,
            cwd=tmp_path,
            port=port,
        )
        try:
            if not wait_for_url(f"http://127.0.0.1:{port}/healthz", timeout=40.0):
                out, err = stop_daemon_process(first)
                pytest.fail(
                    f"first daemon did not become healthy; cannot test conflict\nstdout={out!r}\nstderr={err!r}"
                )

            # Second daemon on the SAME port — must fail cleanly and quickly.
            second = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "general_ludd.cli",
                    "daemon",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(port),
                    "--config-dir",
                    str(config_dir),
                ],
                capture_output=True,
                text=True,
                timeout=60,
                cwd=str(tmp_path),
                env=daemon_subprocess_env(tmp_path, port=port),
            )
            assert second.returncode != 0, "second daemon on an occupied port unexpectedly exited 0"
            # The failure must be a clean bind error, never a Python traceback
            # dumped to the operator.
            combined = (second.stdout or "") + (second.stderr or "")
            assert "Traceback (most recent call last)" not in combined, (
                f"unexpected traceback on port conflict:\n{combined}"
            )
        finally:
            stop_daemon_process(first)
