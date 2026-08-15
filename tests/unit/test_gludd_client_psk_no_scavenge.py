"""Behavioral unit tests for the PSK-scavenge fix in module_utils/gludd.py.

Proves that GluddClient._headers() does NOT fall back to the
``GLUDD_AUTH_PSK`` environment variable: a module that omits the psk
parameter gets NO auth headers, even when GLUDD_AUTH_PSK is set in the
process environment.

This is the test for the HIGH-severity scavenge vulnerability
described in docs/design/NEXT_RELEASE_BETA2_SPEC.md and
docs/design/specs/SPEC_SECURITY_WAVE1.md.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
MODULE_UTILS_PATH = (
    ROOT / "collections" / "ansible_collections" / "general_ludd" / "agent" / "plugins" / "module_utils" / "gludd.py"
)


def _load_module_utils() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_gludd_module_utils_psk_test", MODULE_UTILS_PATH)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load {MODULE_UTILS_PATH}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _make_client(psk: str = "") -> Any:
    mod = _load_module_utils()
    return mod.GluddClient(base_url="http://localhost:8000", psk=psk)


class TestGluddClientDoesNotScavengePskFromEnv:
    def test_no_auth_headers_when_psk_empty_and_env_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GLUDD_AUTH_PSK", "secret-admin-key-should-not-leak")
        client = _make_client(psk="")

        headers = client._headers()

        assert "Authorization" not in headers
        assert "X-PSK" not in headers
        assert "Content-Type" in headers
        assert "Accept" in headers

    def test_no_auth_headers_when_psk_empty_and_env_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("GLUDD_AUTH_PSK", raising=False)
        client = _make_client(psk="")

        headers = client._headers()

        assert "Authorization" not in headers
        assert "X-PSK" not in headers

    def test_auth_headers_present_when_psk_explicit(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("GLUDD_AUTH_PSK", raising=False)
        client = _make_client(psk="explicit-psk-value")

        headers = client._headers()

        assert headers.get("Authorization") == "Bearer explicit-psk-value"
        assert headers.get("X-PSK") == "explicit-psk-value"

    def test_explicit_psk_takes_precedence_over_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GLUDD_AUTH_PSK", "env-key-should-be-ignored")
        client = _make_client(psk="caller-supplied-key")

        headers = client._headers()

        assert headers.get("Authorization") == "Bearer caller-supplied-key"
        assert headers.get("X-PSK") == "caller-supplied-key"

    def test_no_auth_headers_when_psk_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GLUDD_AUTH_PSK", "env-key")
        client = _make_client(psk="")

        headers = client._headers()

        assert "Authorization" not in headers
        assert "X-PSK" not in headers
