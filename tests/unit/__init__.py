"""Unit tests for the agentic harness."""

from general_ludd import __version__


def test_version_exists() -> None:
    assert __version__ == "0.1.0-alpha.5"
