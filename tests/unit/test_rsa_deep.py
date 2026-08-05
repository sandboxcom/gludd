"""Deep tests for RSA cryptosystem: keygen, encrypt/decrypt, CRT, PKCS#1 padding."""

from __future__ import annotations

import math
import os

import pytest

from general_ludd.algorithms.rsa import (
    DecryptionError,
    RSAError,
    RSAKey,
    _generate_prime,
    _i2osp,
    _is_probable_prime,
    _mod_inverse,
    _os2ip,
    decrypt,
    decrypt_crt,
    encrypt,
    generate_keypair,
    pkcs1_v15_decode,
    pkcs1_v15_encode,
)

# -- Miller-Rabin ------------------------------------------------------


def test_is_probable_prime_known_primes():
    """Verify known primes and composites."""
    assert _is_probable_prime(2)
    assert _is_probable_prime(3)
    assert _is_probable_prime(7)
    assert _is_probable_prime(8191)
    assert _is_probable_prime(6700417)
    assert not _is_probable_prime(0)
    assert not _is_probable_prime(1)
    assert not _is_probable_prime(4)
    assert not _is_probable_prime(91)
    assert not _is_probable_prime(6700417 * 6700419)


def test_is_probable_prime_carmichael_number():
    """Carmichael numbers are composite but pass Fermat; Miller-Rabin catches them."""
    assert not _is_probable_prime(561)


# ── Integer / octet conversions ──────────────────────────────────────


def test_i2osp_os2ip_roundtrip():
    x = 12345678901234567890
    assert _os2ip(_i2osp(x, 20)) == x


def test_i2osp_prepends_zeros():
    encoded = _i2osp(0xABCD, 4)
    assert len(encoded) == 4
    assert encoded == b"\x00\x00\xab\xcd"


# ── Modular inverse ──────────────────────────────────────────────────


def test_mod_inverse_basic():
    assert _mod_inverse(3, 11) == 4
    inv = _mod_inverse(65537, 1031806207)
    assert (65537 * inv) % 1031806207 == 1


def test_mod_inverse_no_inverse():
    with pytest.raises(RSAError, match="no modular inverse"):
        _mod_inverse(6, 9)


# ── Key generation ───────────────────────────────────────────────────


def test_generate_key_basic():
    key = generate_keypair(bits=512)
    assert key.n.bit_length() >= 511
    assert key.e == 65537
    assert key.is_private
    assert key.p is not None and key.q is not None
    assert key.p != key.q
    assert key.n == key.p * key.q


def test_generate_key_public_key_view():
    key = generate_keypair(bits=512)
    pub = key.public_key
    assert pub.n == key.n
    assert pub.e == key.e
    assert not pub.is_private


def test_generate_key_invalid_size():
    with pytest.raises(RSAError, match=">= 512"):
        generate_keypair(bits=256)
    with pytest.raises(RSAError, match="even"):
        generate_keypair(bits=513)


def test_generate_key_even_exponent():
    with pytest.raises(RSAError, match="odd"):
        generate_keypair(bits=512, e=2)


def test_generate_key_phi_invariant():
    """d x e = 1 mod phi(n)."""
    key = generate_keypair(bits=512)
    assert key.p is not None and key.q is not None and key.d is not None
    phi = (key.p - 1) * (key.q - 1)
    assert (key.d * key.e) % phi == 1


# ── PKCS#1 v1.5 padding ──────────────────────────────────────────────


def test_pkcs1_encode_decode_roundtrip():
    msg = b"Hello, RSA!"
    k = 256
    encoded = pkcs1_v15_encode(msg, k)
    assert len(encoded) == k
    assert encoded.startswith(b"\x00\x02")
    decoded = pkcs1_v15_decode(encoded, k)
    assert decoded == msg


def test_pkcs1_encode_message_too_long():
    k = 128
    msg = b"A" * (k - 10)
    with pytest.raises(RSAError, match="too long"):
        pkcs1_v15_encode(msg, k)


def test_pkcs1_decode_bad_leading_bytes():
    k = 128
    encoded = b"\x00\x01" + b"\x99" * (k - 5) + b"\x00hi"
    with pytest.raises(DecryptionError, match="leading bytes"):
        pkcs1_v15_decode(encoded, k)


def test_pkcs1_decode_separator_too_early():
    k = 20
    encoded = b"\x00\x02\x00" + b"Y" * (k - 3)
    with pytest.raises(DecryptionError, match="separator"):
        pkcs1_v15_decode(encoded, k)


def test_pkcs1_decode_wrong_length():
    encoded = b"\x00\x02" + b"\x99" * 30 + b"\x00DATA"
    with pytest.raises(DecryptionError, match="length"):
        pkcs1_v15_decode(encoded, 100)


# ── Encrypt / decrypt round-trip ─────────────────────────────────────


def test_encrypt_decrypt_roundtrip():
    key = generate_keypair(bits=1024)
    plaintext = b"A message to encrypt with RSA PKCS#1 v1.5."
    ciphertext = encrypt(key.public_key, plaintext)
    assert ciphertext != plaintext
    decrypted = decrypt(key, ciphertext)
    assert decrypted == plaintext


def test_encrypt_decrypt_empty_message():
    key = generate_keypair(bits=1024)
    ct = encrypt(key.public_key, b"")
    assert decrypt(key, ct) == b""


def test_encrypt_decrypt_max_message():
    key = generate_keypair(bits=1024)
    k = (key.n.bit_length() + 7) // 8
    plaintext = b"A" * (k - 11)
    ct = encrypt(key.public_key, plaintext)
    assert decrypt(key, ct) == plaintext


def test_decrypt_wrong_key():
    key1 = generate_keypair(bits=1024)
    key2 = generate_keypair(bits=1024)
    ct = encrypt(key1.public_key, b"secret")
    with pytest.raises(DecryptionError):
        decrypt(key2, ct)


# ── CRT decryption ───────────────────────────────────────────────────


def test_decrypt_crt_roundtrip():
    key = generate_keypair(bits=1024)
    plaintext = b"CRT-accelerated decryption test message."
    ct = encrypt(key.public_key, plaintext)
    decrypted = decrypt_crt(key, ct)
    assert decrypted == plaintext


def test_decrypt_crt_matches_basic():
    key = generate_keypair(bits=1024)
    k = (key.n.bit_length() + 7) // 8
    for msg in [b"", b"x", b"Hello World!", os.urandom(k - 11)]:
        ct = encrypt(key.public_key, msg)
        assert decrypt_crt(key, ct) == decrypt(key, ct)


def test_decrypt_crt_missing_p_q():
    key = RSAKey(n=77, e=7, d=43)
    with pytest.raises(RSAError, match="requires p"):
        decrypt_crt(key, b"\x00" * 3)


# ── Property tests ───────────────────────────────────────────────────


def test_encrypted_outputs_are_distinct():
    """Same plaintext encrypted twice produces different ciphertexts (random padding)."""
    key = generate_keypair(bits=1024)
    msg = b"distinct-ciphertext test"
    ct1 = encrypt(key.public_key, msg)
    ct2 = encrypt(key.public_key, msg)
    assert ct1 != ct2
    assert decrypt(key, ct1) == msg
    assert decrypt(key, ct2) == msg


def test_many_keys_independent():
    keys = [generate_keypair(bits=512) for _ in range(5)]
    for i in range(len(keys)):
        for j in range(i + 1, len(keys)):
            assert keys[i].n != keys[j].n


def test_prime_generation_coprime_to_e():
    p = _generate_prime(64, e=3)
    assert math.gcd(p - 1, 3) == 1


def test_sign_and_verify_like_property():
    """Sign via decrypt, verify via encrypt (RSA signature property)."""
    key = generate_keypair(bits=1024)
    k = (key.n.bit_length() + 7) // 8
    d = key.d
    assert d is not None
    message = b"RSA signature property test"
    encoded = pkcs1_v15_encode(message, k)
    sig_bytes = _i2osp(pow(_os2ip(encoded), d, key.n), k)
    recovered = _i2osp(pow(_os2ip(sig_bytes), key.e, key.n), k)
    recovered_msg = pkcs1_v15_decode(recovered, k)
    assert recovered_msg == message


def test_crt_tamper_resistant():
    """Decrypting ciphertext derived from a different key should fail."""
    sender_key = generate_keypair(bits=1024)
    attacker_key = generate_keypair(bits=1024)
    ct = encrypt(attacker_key.public_key, b"tamper test")
    with pytest.raises((DecryptionError, ValueError)):
        decrypt_crt(sender_key, ct)
