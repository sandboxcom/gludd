"""Noise revision 34 interoperability vectors.

The committed test data is the applicable ``25519_AESGCM_SHA256`` subset of
Cacophony's Noise corpus at commit 18b7348c54fd61fcd0c220298883de0d09c8364d.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import cast

import pytest
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

from general_ludd.algorithms.noise_protocol import KeyPair, create_noise_session

_FIXTURE_PATH = (
    Path(__file__).parents[1]
    / "fixtures"
    / "noise"
    / "cacophony_rev34_25519_aesgcm_sha256.json"
)
_DOCUMENT = cast(dict[str, object], json.loads(_FIXTURE_PATH.read_text(encoding="utf-8")))
_VECTORS = cast(list[dict[str, object]], _DOCUMENT["vectors"])
_HANDSHAKE_MESSAGE_COUNTS = {"NN": 2, "KN": 2, "NK": 2, "KK": 2, "XX": 3, "IK": 2, "IN": 2}


def _key_pair(private_hex: str) -> KeyPair:
    private = bytes.fromhex(private_hex)
    key = X25519PrivateKey.from_private_bytes(private)
    return KeyPair(private=private, public=key.public_key().public_bytes_raw())


def _optional_key_pair(vector: dict[str, object], name: str) -> KeyPair | None:
    value = vector.get(name)
    return _key_pair(cast(str, value)) if value is not None else None


def _optional_public_key(vector: dict[str, object], name: str) -> bytes | None:
    value = vector.get(name)
    return bytes.fromhex(cast(str, value)) if value is not None else None


@pytest.mark.parametrize(
    "vector", _VECTORS, ids=[cast(str, vector["protocol_name"]) for vector in _VECTORS]
)
def test_aesgcm_sha256_revision_34_vector(
    vector: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Match every supported handshake and transport vector byte-for-byte."""
    protocol_name = cast(str, vector["protocol_name"])
    pattern = protocol_name.split("_")[1]
    generated: Iterator[KeyPair] = iter(
        (
            _key_pair(cast(str, vector["init_ephemeral"])),
            _key_pair(cast(str, vector["resp_ephemeral"])),
        )
    )
    monkeypatch.setattr(KeyPair, "generate", staticmethod(lambda: next(generated)))

    initiator = create_noise_session(
        pattern,
        True,
        prologue=bytes.fromhex(cast(str, vector["init_prologue"])),
        local_static=_optional_key_pair(vector, "init_static"),
        remote_static=_optional_public_key(vector, "init_remote_static"),
    )
    responder = create_noise_session(
        pattern,
        False,
        prologue=bytes.fromhex(cast(str, vector["resp_prologue"])),
        local_static=_optional_key_pair(vector, "resp_static"),
        remote_static=_optional_public_key(vector, "resp_remote_static"),
    )

    messages = cast(list[list[str]], vector["messages"])
    handshake_count = _HANDSHAKE_MESSAGE_COUNTS[pattern]
    for index, (payload_hex, ciphertext_hex) in enumerate(messages[:handshake_count]):
        writer, reader = (
            (initiator, responder) if index % 2 == 0 else (responder, initiator)
        )
        payload = bytes.fromhex(payload_hex)
        message = writer.write_message(payload)
        assert message == bytes.fromhex(ciphertext_hex)
        assert reader.read_message(message) == payload

    expected_hash = bytes.fromhex(cast(str, vector["handshake_hash"]))
    assert initiator._handshake_hash() == expected_hash
    assert responder._handshake_hash() == expected_hash

    initiator_send, initiator_receive = initiator.split()
    responder_receive, responder_send = responder.split()
    for index, (payload_hex, ciphertext_hex) in enumerate(
        messages[handshake_count:], start=handshake_count
    ):
        sender, receiver = (
            (initiator_send, responder_receive)
            if index % 2 == 0
            else (responder_send, initiator_receive)
        )
        payload = bytes.fromhex(payload_hex)
        ciphertext = sender.encrypt_with_ad(b"", payload)
        assert ciphertext == bytes.fromhex(ciphertext_hex)
        assert receiver.decrypt_with_ad(b"", ciphertext) == payload
