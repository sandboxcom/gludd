"""Fixtures for the dogfood e2e harness."""
from __future__ import annotations

import socket
import tempfile
from collections.abc import Generator
from pathlib import Path

import pytest

from tests.e2e.dogfood._secrets import load_llm_keys


def _find_free_port() -> int:
    """Find a free TCP port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def repo_root() -> Path:
    """Absolute path to the repo root."""
    return Path(__file__).parent.parent.parent.parent


@pytest.fixture(scope="session")
def zai_creds(repo_root: Path) -> dict[str, str] | None:
    """Load zai credentials from .secrets/llm_keys.env.

    Returns None if absent -- dependent tests should skip live assertions.
    Never exposes key values in repr/logs.
    """
    return load_llm_keys(repo_root)


@pytest.fixture(scope="session")
def gateway_mode(zai_creds: dict[str, str] | None) -> str:
    """'live' if zai_creds present, else 'mock'."""
    return "live" if zai_creds is not None else "mock"


@pytest.fixture
def tmp_workspace() -> Generator[Path, None, None]:
    """A fresh temp directory; auto-deleted on teardown."""
    with tempfile.TemporaryDirectory(prefix="gludd-e2e-") as d:
        yield Path(d)


@pytest.fixture
def free_port() -> int:
    """A free TCP port."""
    return _find_free_port()
