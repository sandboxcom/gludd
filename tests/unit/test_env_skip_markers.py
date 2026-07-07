"""Tests for env-var-based skipif markers (requires_slurm, requires_postgres).

The markers let the local gate skip integration tests that need SLURM or
PostgreSQL without forcing every developer to install both services. Probe
logic (defined in tests/conftest.py):

    SLURM_AVAILABLE    = env SLURM_AVAILABLE=1  OR  shutil.which("sbatch")
    POSTGRES_AVAILABLE = env POSTGRES_AVAILABLE=1  OR  port 5432 open

The constants are evaluated at conftest import time, so env-dependent tests
re-import conftest in a fresh subprocess with a controlled environment.
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
TESTS_DIR = REPO_ROOT / "tests"


def _run_conftest_snippet(env_extra: dict[str, str], snippet: str) -> str:
    """Import tests/conftest.py in a fresh subprocess and return stripped stdout.

    SLURM_AVAILABLE / POSTGRES_AVAILABLE are cleared from the inherited
    environment so each call starts from the "unset" baseline.
    """
    env = dict(os.environ)
    env.pop("SLURM_AVAILABLE", None)
    env.pop("POSTGRES_AVAILABLE", None)
    env.update(env_extra)
    code = (
        f"import sys; sys.path.insert(0, {str(TESTS_DIR)!r}); "
        f"sys.path.insert(0, {str(REPO_ROOT / 'src')!r}); "
        f"{snippet}"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, (
        f"subprocess failed rc={result.returncode}\nstderr: {result.stderr}\nstdout: {result.stdout}"
    )
    return result.stdout.strip()


def test_slurm_available_detected_via_env() -> None:
    """SLURM_AVAILABLE=1 must force the SLURM_AVAILABLE constant True."""
    out = _run_conftest_snippet(
        {"SLURM_AVAILABLE": "1"},
        "import conftest; print(conftest.SLURM_AVAILABLE)",
    )
    assert out == "True"


def test_slurm_unavailable_skips() -> None:
    """Without the env var, SLURM_AVAILABLE tracks sbatch presence on PATH."""
    out = _run_conftest_snippet(
        {},
        "import conftest, shutil; "
        "print(conftest.SLURM_AVAILABLE == (shutil.which('sbatch') is not None))",
    )
    assert out == "True", f"expected SLURM_AVAILABLE == sbatch-present, got {out}"


def test_postgres_available_via_env() -> None:
    """POSTGRES_AVAILABLE=1 must force the POSTGRES_AVAILABLE constant True."""
    out = _run_conftest_snippet(
        {"POSTGRES_AVAILABLE": "1"},
        "import conftest; print(conftest.POSTGRES_AVAILABLE)",
    )
    assert out == "True"


def test_postgres_unavailable_skips() -> None:
    """Without the env var, POSTGRES_AVAILABLE tracks port 5432 openness."""
    out = _run_conftest_snippet(
        {},
        "import conftest; "
        "print(conftest.POSTGRES_AVAILABLE == conftest._port_open('127.0.0.1', 5432))",
    )
    assert out == "True", f"expected POSTGRES_AVAILABLE == port-open, got {out}"


def test_port_open_false_for_closed_port() -> None:
    """_port_open returns False when no listener is bound."""
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    closed_port = sock.getsockname()[1]
    sock.close()
    out = _run_conftest_snippet(
        {},
        f"import conftest; print(conftest._port_open('127.0.0.1', {closed_port}))",
    )
    assert out == "False"


def test_port_open_true_for_open_port() -> None:
    """_port_open returns True when a listener is bound."""
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    open_port = server.getsockname()[1]
    try:
        out = _run_conftest_snippet(
            {},
            f"import conftest; print(conftest._port_open('127.0.0.1', {open_port}))",
        )
    finally:
        server.close()
    assert out == "True"


def test_requires_slurm_marker_reason_mentions_env() -> None:
    """requires_slurm marker's reason must name SLURM_AVAILABLE for operator self-service."""
    out = _run_conftest_snippet(
        {},
        "import conftest; print(conftest.requires_slurm.mark.kwargs.get('reason', ''))",
    )
    assert "SLURM_AVAILABLE" in out


def test_requires_postgres_marker_reason_mentions_env() -> None:
    """requires_postgres marker's reason must name POSTGRES_AVAILABLE."""
    out = _run_conftest_snippet(
        {},
        "import conftest; print(conftest.requires_postgres.mark.kwargs.get('reason', ''))",
    )
    assert "POSTGRES_AVAILABLE" in out
