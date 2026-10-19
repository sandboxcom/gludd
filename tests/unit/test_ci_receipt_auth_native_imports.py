"""Native package and flat imports preserve real receipt authentication."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

_PROBE = r"""
import hashlib
import hmac
import importlib
import json
import os
import stat
import sys
from pathlib import Path

mode, source, key_name, action = sys.argv[1:]
source = Path(source)
key_path = Path(key_name)
assert sys.flags.ignore_environment == 1
assert sys.flags.optimize == 0
assert "pytest" not in sys.modules
assert "conftest" not in sys.modules
module_name = "scripts.ci_receipt_auth" if mode == "package" else "ci_receipt_auth"
sibling_name = "scripts.gate_status_attestation" if mode == "package" else "gate_status_attestation"
other_sibling = "gate_status_attestation" if mode == "package" else "scripts.gate_status_attestation"
auth = importlib.import_module(module_name)
gate = importlib.import_module(sibling_name)
assert Path(auth.__file__).resolve() == source / "scripts/ci_receipt_auth.py"
assert Path(gate.__file__).resolve() == source / "scripts/gate_status_attestation.py"
assert auth._read_key is gate._read_key
assert auth._read_key.__module__ == sibling_name
assert other_sibling not in sys.modules
if mode == "package":
    assert source / "scripts" not in {Path(value).resolve() for value in sys.path}
assert "pytest" not in sys.modules
if action == "refuse":
    before = key_path.read_bytes()
    try:
        auth.load_gate_receipt_auth_context(key_path, issued_at=1000)
    except ValueError as error:
        assert str(key_path) not in str(error)
        assert before.hex() not in str(error)
    else:
        raise AssertionError("unsafe native key was accepted")
    assert key_path.read_bytes() == before
    print("native-auth-refused")
else:
    context = auth.load_gate_receipt_auth_context(key_path, issued_at=1000)
    raw_key = key_path.read_bytes()
    assert context.signer.key == bytes.fromhex(raw_key.decode("ascii").strip())
    assert len(context.signer.key) == 32
    metadata = key_path.stat()
    assert stat.S_ISREG(metadata.st_mode)
    assert stat.S_IMODE(metadata.st_mode) == 0o600
    if hasattr(os, "getuid"):
        assert metadata.st_uid == os.getuid()
    later = auth.load_gate_receipt_auth_context(key_path, issued_at=1100)
    assert later.signer.key == context.signer.key
    assert later.signer.signer_id == context.signer.signer_id
    assert key_path.read_bytes() == raw_key
    for kind in ("pass", "failure"):
        envelope = auth.create_receipt_envelope(
            receipt_kind=kind, action_digest="a" * 64,
            content_sha256="b" * 64, signer=context.signer,
        )
        unsigned = {name: value for name, value in envelope.items() if name != "signature"}
        encoded = json.dumps(
            unsigned, allow_nan=False, ensure_ascii=True, separators=(",", ":"), sort_keys=True,
        ).encode("ascii")
        assert envelope["signature"] == hmac.new(
            context.signer.key, encoded, hashlib.sha256,
        ).hexdigest()
        arguments = dict(
            expected_kind=kind, expected_action_digest="a" * 64,
            expected_content_sha256="b" * 64,
        )
        result = auth.authenticate_receipt_envelope(
            envelope, trust_policy=later.trust_policy, **arguments,
        )
        assert result.verified and result.status == "verified"
        tampered = dict(envelope, content_sha256="c" * 64)
        refused = auth.authenticate_receipt_envelope(
            tampered, trust_policy=later.trust_policy, **arguments,
        )
        assert not refused.verified and refused.status == "tampered"
        expired = auth.ReceiptTrustPolicy(
            {context.signer.signer_id: context.signer.key},
            envelope["expires_at"] + 1,
        )
        refused = auth.authenticate_receipt_envelope(
            envelope, trust_policy=expired, **arguments,
        )
        assert not refused.verified and refused.status == "expired"
        revoked = auth.ReceiptTrustPolicy(
            {context.signer.signer_id: context.signer.key}, 1100,
            frozenset({context.signer.signer_id}),
        )
        refused = auth.authenticate_receipt_envelope(
            envelope, trust_policy=revoked, **arguments,
        )
        assert not refused.verified and refused.status == "revoked"
    assert context.signer.key.hex() not in repr(context)
    assert str(key_path) not in repr(context)
    print(json.dumps({"signer_id": context.signer.signer_id, "key_reader": sibling_name}))
"""


def _run_probe(mode: str, key_path: Path, *, action: str = "authenticate") -> subprocess.CompletedProcess[str]:
    """Run the selected interpreter without inherited Python bootstrap paths."""
    directory = ROOT if mode == "package" else ROOT / "scripts"
    return subprocess.run(
        [sys.executable, "-E", "-W", "error", "-c", _PROBE, mode, str(ROOT), str(key_path), action],
        cwd=directory, check=False, capture_output=True, text=True, timeout=20,
    )


@pytest.mark.parametrize("mode", ["package", "flat"])
def test_fresh_native_import_reuses_actual_key_reader_and_signed_receipts(tmp_path: Path, mode: str) -> None:
    key_path = tmp_path / "private" / "receipt.key"
    first = _run_probe(mode, key_path)
    assert first.returncode == 0, first.stdout + first.stderr
    assert not first.stderr
    original_key = key_path.read_bytes()
    second = _run_probe(mode, key_path)
    assert second.returncode == 0, second.stdout + second.stderr
    assert not second.stderr
    assert first.stdout == second.stdout
    assert key_path.read_bytes() == original_key
    assert str(key_path) not in first.stdout
    assert bytes.fromhex(original_key.decode("ascii").strip()).hex() not in first.stdout


@pytest.mark.parametrize("mode", ["package", "flat"])
@pytest.mark.parametrize("unsafe", ["readable", "symlink", "short"])
def test_fresh_native_import_refuses_unsafe_keys_without_mutating_targets(
    tmp_path: Path, mode: str, unsafe: str,
) -> None:
    target = tmp_path / "target.key"
    target.write_bytes((bytes(range(32)).hex() + "\n").encode("ascii"))
    target.chmod(0o600)
    key_path = target
    if unsafe == "readable":
        target.chmod(0o644)
    elif unsafe == "symlink":
        key_path = tmp_path / "alias.key"
        key_path.symlink_to(target)
    else:
        target.write_text("00\n", encoding="ascii")
    before = target.read_bytes(), target.stat().st_mode
    result = _run_probe(mode, key_path, action="refuse")
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout == "native-auth-refused\n"
    assert not result.stderr
    assert (target.read_bytes(), target.stat().st_mode) == before
    assert key_path.is_symlink() == (unsafe == "symlink")
