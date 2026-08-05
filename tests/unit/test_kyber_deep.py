"""Deep Kyber ML-KEM tests: NTT, polynomial arithmetic, keygen,
encapsulate, decapsulate, FO transform, wrong-ciphertext rejection,
compression roundtrip, and all three parameter sets.

Pure-Python, stdlib only.
"""

from __future__ import annotations

import pytest

from general_ludd.algorithms.kyber import (
    PARAMS_512,
    PARAMS_768,
    PARAMS_1024,
    KyberError,
    compress,
    decapsulate,
    decapsulate_512,
    decapsulate_768,
    decapsulate_1024,
    decompress,
    encapsulate,
    encapsulate_512,
    encapsulate_768,
    encapsulate_1024,
    inv_ntt,
    keygen,
    keygen_512,
    keygen_768,
    keygen_1024,
    ntt,
    poly_add,
    poly_from_msg,
    poly_mul,
    poly_sub,
    poly_to_msg,
)

_ZERO = tuple([0] * 256)
_ONES = tuple([1] * 256)


class TestNTT:
    def test_ntt_roundtrip(self) -> None:
        a = tuple(i % 3329 for i in range(256))
        assert inv_ntt(ntt(a)) == a

    def test_ntt_zero_roundtrip(self) -> None:
        assert inv_ntt(ntt(_ZERO)) == _ZERO

    def test_ntt_linearity(self) -> None:
        a = tuple(i % 3329 for i in range(256))
        b = tuple((i * 7) % 3329 for i in range(256))
        lhs = ntt(poly_add(a, b))
        rhs = poly_add(ntt(a), ntt(b))
        assert lhs == rhs

    def test_ntt_of_one_preserved(self) -> None:
        a = tuple([1] + [0] * 255)
        ntt_a = ntt(a)
        inv_a = inv_ntt(ntt_a)
        assert inv_a == a


class TestPolyArithmetic:
    def test_poly_add_identity(self) -> None:
        a = tuple((i * 3) % 3329 for i in range(256))
        assert poly_add(a, _ZERO) == a

    def test_poly_add_commutative(self) -> None:
        a = tuple((i * 3) % 3329 for i in range(256))
        b = tuple((i * 7) % 3329 for i in range(256))
        assert poly_add(a, b) == poly_add(b, a)

    def test_poly_sub_self_zero(self) -> None:
        a = tuple((i * 3) % 3329 for i in range(256))
        assert poly_sub(a, a) == _ZERO

    def test_poly_mul_identity(self) -> None:
        a = tuple((i * 3) % 3329 for i in range(256))
        assert poly_mul(a, _ONES) == a

    def test_poly_mul_zero(self) -> None:
        a = tuple((i * 3) % 3329 for i in range(256))
        assert poly_mul(a, _ZERO) == _ZERO

    def test_poly_mul_commutative(self) -> None:
        a = tuple((i * 3) % 3329 for i in range(256))
        b = tuple((i * 5 + 1) % 3329 for i in range(256))
        assert poly_mul(a, b) == poly_mul(b, a)


class TestSerialization:
    def test_poly_to_from_msg_roundtrip(self) -> None:
        msg = b"Hello, Post-Quantum World! 32bytes"
        assert poly_to_msg(poly_from_msg(msg)) == msg

    def test_poly_from_msg_all_zero(self) -> None:
        result = poly_from_msg(b"\x00" * 32)
        assert result == _ZERO

    def test_poly_from_msg_all_one(self) -> None:
        result = poly_from_msg(b"\xff" * 32)
        for c in result:
            assert c == (-((3329 + 1) // 2)) % 3329


class TestCompression:
    def test_compress_decompress_roundtrip_d10(self) -> None:
        a = tuple(i % 3329 for i in range(256))
        for d in (4, 5, 10, 11):
            compressed = compress(a, d)
            decompressed = decompress(compressed, d)
            for x, y in zip(a, decompressed, strict=False):
                assert abs(x - y) <= 3329 // (1 << (d + 1)) + 2

    def test_compress_du_values(self) -> None:
        a = tuple(i % 3329 for i in range(256))
        c10 = compress(a, 10)
        c11 = compress(a, 11)
        assert all(0 <= x < (1 << 10) for x in c10)
        assert all(0 <= x < (1 << 11) for x in c11)


class TestKeygen:
    def test_keygen_512_produces_valid_pk_sk(self) -> None:
        pk, sk = keygen_512()
        assert len(pk) == PARAMS_512.pk_bytes
        assert len(sk) == PARAMS_512.sk_bytes + PARAMS_512.pk_bytes + 64

    def test_keygen_768_produces_valid_pk_sk(self) -> None:
        pk, sk = keygen_768()
        assert len(pk) == PARAMS_768.pk_bytes
        assert len(sk) == PARAMS_768.sk_bytes + PARAMS_768.pk_bytes + 64

    def test_keygen_1024_produces_valid_pk_sk(self) -> None:
        pk, sk = keygen_1024()
        assert len(pk) == PARAMS_1024.pk_bytes
        assert len(sk) == PARAMS_1024.sk_bytes + PARAMS_1024.pk_bytes + 64

    def test_keygen_produces_different_keys(self) -> None:
        pk1, sk1 = keygen_512()
        pk2, sk2 = keygen_512()
        assert pk1 != pk2
        assert sk1 != sk2

    def test_keygen_pk_not_in_sk_raw(self) -> None:
        pk, sk = keygen_512()
        assert pk != sk[: len(pk)]


class TestEncapsulateDecapsulate:
    def test_encaps_decaps_roundtrip_512(self) -> None:
        pk, sk = keygen_512()
        ct, ss = encapsulate_512(pk)
        ss2 = decapsulate_512(ct, sk)
        assert ss == ss2
        assert len(ss) == 32

    def test_encaps_decaps_roundtrip_768(self) -> None:
        pk, sk = keygen_768()
        ct, ss = encapsulate_768(pk)
        ss2 = decapsulate_768(ct, sk)
        assert ss == ss2
        assert len(ss) == 32

    def test_encaps_decaps_roundtrip_1024(self) -> None:
        pk, sk = keygen_1024()
        ct, ss = encapsulate_1024(pk)
        ss2 = decapsulate_1024(ct, sk)
        assert ss == ss2
        assert len(ss) == 32

    def test_ciphertext_length_512(self) -> None:
        pk, _sk = keygen_512()
        ct, _ss = encapsulate_512(pk)
        expected = 32 * PARAMS_512.du * PARAMS_512.k + 32 * PARAMS_512.dv
        assert len(ct) == expected

    def test_ciphertext_length_768(self) -> None:
        pk, _sk = keygen_768()
        ct, _ss = encapsulate_768(pk)
        expected = 32 * PARAMS_768.du * PARAMS_768.k + 32 * PARAMS_768.dv
        assert len(ct) == expected

    def test_encaps_randomness(self) -> None:
        pk, _sk = keygen_512()
        ct1, ss1 = encapsulate_512(pk)
        ct2, ss2 = encapsulate_512(pk)
        assert ct1 != ct2
        assert ss1 != ss2

    def test_different_keys_different_secrets(self) -> None:
        pk1, _sk1 = keygen_512()
        pk2, _sk2 = keygen_512()
        _ct, ss1 = encapsulate_512(pk1)
        ss2 = encapsulate_512(pk2)[1]
        assert ss1 != ss2

    def test_wrong_ciphertext_different_ss(self) -> None:
        pk, sk = keygen_512()
        ct1, _ = encapsulate_512(pk)
        ct2, _ = encapsulate_512(pk)
        ss1 = decapsulate_512(ct1, sk)
        ss2 = decapsulate_512(ct2, sk)
        assert ss1 != ss2

    def test_decaps_with_wrong_sk(self) -> None:
        pk1, _sk1 = keygen_512()
        _pk2, sk2 = keygen_512()
        ct, ss = encapsulate_512(pk1)
        ss2 = decapsulate_512(ct, sk2)
        assert ss != ss2

    def test_truncated_ciphertext(self) -> None:
        pk, sk = keygen_512()
        ct, _ = encapsulate_512(pk)
        truncated = ct[:-10]
        with pytest.raises((KyberError, IndexError, Exception)):
            decapsulate_512(truncated, sk)

    def test_multiple_encaps_same_pk(self) -> None:
        pk, sk = keygen_512()
        secrets = set()
        for _ in range(10):
            ct, ss = encapsulate_512(pk)
            ss2 = decapsulate_512(ct, sk)
            assert ss == ss2
            secrets.add(ss)
        assert len(secrets) == 10

    def test_generic_keygen_encaps_decaps(self) -> None:
        for p, enc_fn, dec_fn in [
            (PARAMS_512, encapsulate, decapsulate),
            (PARAMS_768, encapsulate, decapsulate),
            (PARAMS_1024, encapsulate, decapsulate),
        ]:
            pk, sk = keygen(p)
            ct, ss = enc_fn(pk, p)
            ss2 = dec_fn(ct, sk, p)
            assert ss == ss2


class TestSharedSecretProperties:
    def test_ss_is_32_bytes(self) -> None:
        pk, _sk = keygen_512()
        _ct, ss = encapsulate_512(pk)
        assert len(ss) == 32
        assert isinstance(ss, bytes)

    def test_ss_has_entropy(self) -> None:
        pk, _sk = keygen_512()
        seen = set()
        for _ in range(50):
            _ct, ss = encapsulate_512(pk)
            assert ss not in seen
            seen.add(ss)

    def test_ss_not_zero(self) -> None:
        pk, _sk = keygen_512()
        _ct, ss = encapsulate_512(pk)
        assert ss != b"\x00" * 32

    def test_ss_not_repeated_under_many_keys(self) -> None:
        secrets: list[bytes] = []
        for _ in range(20):
            pk, _sk = keygen_512()
            _ct, ss = encapsulate_512(pk)
            secrets.append(ss)
        assert len(set(secrets)) == len(secrets)


class TestMatrixVector:
    def test_gen_matrix_deterministic(self) -> None:
        from general_ludd.algorithms.kyber import _gen_matrix

        seed = b"test-seed-" + b"\x00" * 23
        mat1 = _gen_matrix(seed, 2)
        mat2 = _gen_matrix(seed, 2)
        for i in range(2):
            for j in range(2):
                assert mat1[i][j] == mat2[i][j]

    def test_gen_matrix_different_seeds(self) -> None:
        from general_ludd.algorithms.kyber import _gen_matrix

        mat1 = _gen_matrix(b"seed-AAAA" + b"\x00" * 23, 2)
        mat2 = _gen_matrix(b"seed-BBBB" + b"\x00" * 23, 2)
        differs = False
        for i in range(2):
            for j in range(2):
                if mat1[i][j] != mat2[i][j]:
                    differs = True
        assert differs
