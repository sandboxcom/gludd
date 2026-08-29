"""Pin ASN.1 DER support to the security collection runtime."""

from __future__ import annotations

from pathlib import Path


def test_asn1_der_runtime_is_owned_by_security_collection() -> None:
    """The collection FQCN must provide the supported DER implementation."""
    from ansible_collections.general_ludd.security.plugins.module_utils.asn1 import (
        encode_der,
        parse_der,
    )

    encoded = encode_der({"type": "INTEGER", "value": 42})

    assert parse_der(encoded)["value"] == 42


def test_asn1_der_runtime_is_absent_from_core_package() -> None:
    """Core must not retain a duplicate or compatibility copy of ASN.1 code."""
    repository_root = Path(__file__).resolve().parents[2]
    core_module = repository_root / "src/general_ludd/ssl/asn1.py"
    core_init = (repository_root / "src/general_ludd/ssl/__init__.py").read_text(
        encoding="utf-8"
    )

    assert not core_module.exists()
    assert "general_ludd.ssl.asn1" not in core_init
