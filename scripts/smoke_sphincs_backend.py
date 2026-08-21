#!/usr/bin/env python3
"""Bounded public-API smoke for Gludd's packaged SPHINCS+ backend."""

from __future__ import annotations

from general_ludd.algorithms.sphincs_plus import slh_keygen, slh_sign, slh_verify

_ALGORITHM = "sphincs_shake_256s_simple"
_MESSAGE = b"gludd-packaged-sphincs-backend"


def main() -> int:
    """Exercise one keygen/sign/verify round trip without external resources."""
    public_key, secret_key = slh_keygen()
    signature = slh_sign(_MESSAGE, secret_key)
    if not slh_verify(_MESSAGE, signature, public_key) or slh_verify(
        _MESSAGE + b"-tampered", signature, public_key
    ):
        raise RuntimeError("SPHINCS+ backend round trip failed")

    print(
        "SPHINCS_BACKEND_SMOKE_PASS "
        f"algorithm={_ALGORITHM} public_key_bytes={len(public_key)} "
        f"signature_bytes={len(signature)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
