"""Guard: integrity signature verification uses constant-time comparison.

A plain ``==`` on the HMAC hex digest leaks, through early-exit timing, how many
leading characters of a forged signature are correct — enough to forge a
signature byte-by-byte against an endpoint that reports verify pass/fail. Both
verify_signature and verify_openbao_signature must use hmac.compare_digest.

This is a source-level guard (timing itself is not reliably observable in a unit
test) plus a functional round-trip that proves the switch to compare_digest did
not break valid/tampered verification.
"""

from __future__ import annotations

import datetime
import inspect

import pytest

from general_ludd.integrity import scanner
from general_ludd.integrity.scanner import (
    ChangeRecord,
    sign_change,
    sign_change_openbao,
    verify_openbao_signature,
    verify_signature,
)


@pytest.fixture(autouse=True)
def _integrity_key(monkeypatch: pytest.MonkeyPatch) -> None:
    # Mirror TestIntegritySigning's fixture: set the key AND reset the module
    # cache so _get_integrity_key re-reads it.
    monkeypatch.setenv("GL_INTEGRITY_KEY", "test-signing-key-fixedvalue")
    monkeypatch.setattr(scanner, "_INTEGRITY_KEY", None, raising=False)


def _change() -> ChangeRecord:
    return ChangeRecord(
        file_path="/tmp/test.txt",
        change_type="modified",
        old_hash="abc123",
        new_hash="def456",
        detected_at=datetime.datetime.now().isoformat(),
        approved=False,
    )


def test_verify_signature_uses_compare_digest() -> None:
    src = inspect.getsource(verify_signature)
    assert "compare_digest" in src
    assert "== expected" not in src


def test_verify_openbao_signature_uses_compare_digest() -> None:
    src = inspect.getsource(verify_openbao_signature)
    assert "compare_digest" in src
    assert "== expected" not in src


def test_valid_signature_still_verifies_true() -> None:
    signed = sign_change(_change(), reason="ok", signer="admin")
    assert verify_signature(signed) is True


def test_tampered_signature_verifies_false() -> None:
    # Tamper the signature value itself (payload-agnostic): a single flipped hex
    # char must fail the constant-time compare regardless of which fields the
    # HMAC payload covers.
    signed = sign_change(_change(), reason="ok", signer="admin")
    sig = signed["signature"]
    signed["signature"] = ("0" if sig[0] != "0" else "1") + sig[1:]
    assert verify_signature(signed) is False


def test_missing_signature_field_verifies_false() -> None:
    # None signature must coerce to "" and compare false, not raise TypeError
    signed = sign_change(_change(), reason="ok", signer="admin")
    signed.pop("signature", None)
    assert verify_signature(signed) is False


def test_openbao_valid_round_trips() -> None:
    signed = sign_change_openbao(
        "/some/file.py", "alice", "approved", old_hash="aaa", new_hash="bbb"
    )
    assert verify_openbao_signature(signed) is True


def test_openbao_tampered_hash_verifies_false() -> None:
    signed = sign_change_openbao(
        "/some/file.py", "alice", "approved", old_hash="aaa", new_hash="bbb"
    )
    signed["new_hash"] = "tampered"
    assert verify_openbao_signature(signed) is False
