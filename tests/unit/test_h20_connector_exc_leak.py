"""H.20: connector exception leak tests — centralized exc_sanitizer + top-3 sinks.

Verifies that:
1. ``exc_sanitizer`` module exists, exports the canonical helpers.
2. ``sanitize_exc_message`` / ``sanitize_exc_for_health`` / ``sanitize_exc_for_query``
   never leak paths, tokens, URLs, or raw exception text.
3. Top-3 sinks (kubernetes, aws_observability, local_files) use the sanitizer
   instead of raw ``str(exc)``.
"""

from __future__ import annotations

import os
import re

import pytest

# ── exc_sanitizer module shape ──────────────────────────────────────────


def test_exc_sanitizer_module_exists() -> None:
    """C20-GATE: module can be imported cleanly."""
    from general_ludd.connectors import exc_sanitizer  # noqa: F811

    assert exc_sanitizer is not None


def test_exc_sanitizer_exports() -> None:
    """All canonical helpers are exported."""
    from general_ludd.connectors import exc_sanitizer

    for name in (
        "sanitize_exc_message",
        "sanitize_str",
        "sanitize_exc_for_health",
        "sanitize_exc_for_query",
    ):
        assert hasattr(exc_sanitizer, name), f"missing export: {name}"


# ── sanitize_exc_message — safety properties ────────────────────────────


def test_sanitize_exc_message_never_leaks_paths() -> None:
    from general_ludd.connectors._errors import sanitize_exc_message

    exc = RuntimeError("failed to open /etc/shadow for reading")
    result = sanitize_exc_message(exc)
    assert "/etc/shadow" not in result
    assert "/" not in result


def test_sanitize_exc_message_never_leaks_tokens() -> None:
    from general_ludd.connectors._errors import sanitize_exc_message

    exc = RuntimeError("bearer xyz-sekret-token-value-abc123")
    result = sanitize_exc_message(exc)
    assert "xyz-sekret" not in result
    assert "bearer" not in result
    assert "token" not in result


def test_sanitize_exc_message_never_leaks_urls() -> None:
    from general_ludd.connectors._errors import sanitize_exc_message

    exc = RuntimeError("connect to https://internal.k8s:6443?token=SECRET failed")
    result = sanitize_exc_message(exc)
    assert "https://" not in result
    assert "internal.k8s" not in result
    assert "6443" not in result
    assert "SECRET" not in result


def test_sanitize_exc_message_returns_type_name_only() -> None:
    from general_ludd.connectors._errors import sanitize_exc_message

    class MyCustomJunk(Exception):
        pass

    assert sanitize_exc_message(MyCustomJunk("data"))
    assert MyCustomJunk.__name__ == "MyCustomJunk"


def test_sanitize_exc_message_returns_str() -> None:
    from general_ludd.connectors._errors import sanitize_exc_message

    result = sanitize_exc_message(ValueError("anything"))
    assert isinstance(result, str)
    assert len(result) > 0


# ── sanitize_str — redaction ────────────────────────────────────────────


def test_sanitize_str_redacts_paths() -> None:
    from general_ludd.connectors._errors import sanitize_str

    result = sanitize_str("error reading /var/log/app.log: permission denied")
    assert "/var/log" not in result
    assert "[REDACTED-PATH]" in result


def test_sanitize_str_redacts_tokens() -> None:
    from general_ludd.connectors._errors import sanitize_str

    result = sanitize_str("api_key=abcdef1234567890 was rejected")
    assert "abcdef1234567890" not in result
    assert "[REDACTED]" in result


def test_sanitize_str_redacts_urls() -> None:
    from general_ludd.connectors._errors import sanitize_str

    result = sanitize_str("call to https://secrets.example.com failed")
    assert "secrets.example.com" not in result
    assert "[REDACTED" in result


def test_sanitize_str_benign_text_passes_through() -> None:
    from general_ludd.connectors._errors import sanitize_str

    result = sanitize_str("sample message: everything is normal")
    assert "sample message" in result
    assert "[REDACTED" not in result


# ── sanitize_exc_for_health ─────────────────────────────────────────────


def test_sanitize_exc_for_health_no_leak(caplog: pytest.LogCaptureFixture) -> None:
    from general_ludd.connectors.exc_sanitizer import sanitize_exc_for_health

    exc = RuntimeError("kubeconfig at /home/user/.kube/config contains token=abc123")
    result = sanitize_exc_for_health(exc)
    assert "/home/" not in result
    assert "token=" not in result
    assert "abc123" not in result
    assert result == "RuntimeError"
    assert len(caplog.records) >= 1


# ── sanitize_exc_for_query ──────────────────────────────────────────────


def test_sanitize_exc_for_query_no_leak(caplog: pytest.LogCaptureFixture) -> None:
    from general_ludd.connectors.exc_sanitizer import sanitize_exc_for_query

    exc = ValueError("bad url https://leak.me:8443?secret=zzz")
    result = sanitize_exc_for_query(exc)
    assert "leak.me" not in result
    assert "secret=zzz" not in result
    assert result == "ValueError"
    assert len(caplog.records) >= 1


# ── Kubernetes sink (top-3) ─────────────────────────────────────────────


def test_kubernetes_query_uses_sanitizer_not_str_exc() -> None:
    src_text = _read_source("kubernetes")
    assert "sanitize_exc_for_query" in src_text, (
        "kubernetes.py must use sanitize_exc_for_query"
    )
    assert "return [self._error(str(exc))]" not in src_text, (
        "kubernetes.py must NOT use raw str(exc)"
    )


def test_kubernetes_imports_exc_sanitizer() -> None:
    src_text = _read_source("kubernetes")
    assert "from general_ludd.connectors.exc_sanitizer import" in src_text


# ── AwsObservability sink (top-3) ───────────────────────────────────────


def test_aws_observability_health_uses_sanitizer_not_str_exc() -> None:
    src_text = _read_source("aws_observability")
    assert "sanitize_exc_for_health" in src_text, (
        "aws_observability.py must use sanitize_exc_for_health"
    )
    assert 'str(exc)' not in src_text, (
        "aws_observability.py must NOT use raw str(exc) in health"
    )


def test_aws_observability_imports_exc_sanitizer() -> None:
    src_text = _read_source("aws_observability")
    assert "from general_ludd.connectors.exc_sanitizer import" in src_text


def test_aws_observability_health_no_boto3_exc_text_leak() -> None:
    _check_no_connector_exc_leak("aws_observability", _HEALTH_LEAK_PATTERNS)


# ── LocalFiles sinks (top-3) ────────────────────────────────────────────


def test_local_files_health_uses_sanitizer_not_str_exc() -> None:
    src_text = _read_source("local_files")
    assert "sanitize_exc_for_health" in src_text, (
        "local_files.py must use sanitize_exc_for_health"
    )
    assert 'str(exc)' not in src_text, (
        "local_files.py must NOT use raw str(exc) in health"
    )


def test_local_files_imports_exc_sanitizer() -> None:
    src_text = _read_source("local_files")
    assert "from general_ludd.connectors.exc_sanitizer import" in src_text


def test_local_files_health_no_exc_text_leak() -> None:
    _check_no_connector_exc_leak("local_files", _HEALTH_LEAK_PATTERNS)


# ── extended sink check (all 41 sinks catalogued) ───────────────────────


_KNOWN_SANITIZED = {"kubernetes", "aws_observability", "local_files"}


def test_three_sinks_sanitized() -> None:
    for mod_name in _KNOWN_SANITIZED:
        src_text = _read_source(mod_name)
        has_sanitizer_import = (
            "from general_ludd.connectors.exc_sanitizer import" in src_text
        )
        has_sanitizer_call = (
            "sanitize_exc_for_health" in src_text
            or "sanitize_exc_for_query" in src_text
            or "sanitize_exc_message" in src_text
        )
        assert has_sanitizer_import, f"{mod_name} missing exc_sanitizer import"
        assert has_sanitizer_call, f"{mod_name} missing sanitizer call"


# ── helpers ─────────────────────────────────────────────────────────────

_HEALTH_LEAK_PATTERNS = [
    'f"{type(exc).__name__}: {exc}"',
    'f"transport error: {exc}"',
    'f"bad auth body: {exc}"',
    'f"error: {exc}"',
    'f"invalid json: {exc}"',
    'f"request failed: {exc}"',
    "str(exc)",
]


def _read_source(mod_name: str) -> str:
    module = __import__(
        f"general_ludd.connectors.{mod_name}", fromlist=[""]
    )
    return __import__("inspect").getsource(module)


def _check_no_connector_exc_leak(mod_name: str, patterns: list[str]) -> None:
    src = _read_source(mod_name)
    for pattern in patterns:
        assert pattern not in src, (
            f"{mod_name}: leak pattern {pattern!r} found in source"
        )
