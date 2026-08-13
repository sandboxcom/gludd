"""Structural tests for ssl/algorithms.py — algorithm evaluation and comparison."""

from __future__ import annotations

import pytest

from general_ludd.ssl.algorithms import (
    COMPLIANCE_STANDARDS,
    KNOWN_ALGORITHMS,
    AlgorithmEval,
    AlgorithmInfo,
    AlgorithmStatus,
    AlgorithmType,
    ComparisonResult,
    compare_algorithms,
    compliance_check,
    evaluate_algorithm,
)


class TestAlgorithmEnums:
    def test_status_values(self) -> None:
        assert AlgorithmStatus.CURRENT.value == "current"
        assert AlgorithmStatus.DEPRECATED.value == "deprecated"
        assert AlgorithmStatus.LEGACY.value == "legacy"

    def test_type_values(self) -> None:
        assert AlgorithmType.RSA.value == "rsa"
        assert AlgorithmType.EC.value == "ec"
        assert AlgorithmType.EDWARDS.value == "edwards"
        assert AlgorithmType.HASH.value == "hash"
        assert AlgorithmType.SYMMETRIC.value == "symmetric"


class TestAlgorithmInfo:
    def test_rsa_2048(self) -> None:
        algo = KNOWN_ALGORITHMS["RSA-2048"]
        assert algo.type == AlgorithmType.RSA
        assert algo.key_sizes == [2048]
        assert algo.security_bits == 112
        assert algo.status == AlgorithmStatus.CURRENT

    def test_ed25519(self) -> None:
        algo = KNOWN_ALGORITHMS["Ed25519"]
        assert algo.type == AlgorithmType.EDWARDS
        assert algo.security_bits == 128
        assert algo.status == AlgorithmStatus.CURRENT

    def test_md5_legacy(self) -> None:
        algo = KNOWN_ALGORITHMS["MD5"]
        assert algo.security_bits == 0
        assert algo.status == AlgorithmStatus.LEGACY
        assert algo.deprecation_date == "2008-01-01"

    def test_chacha_current(self) -> None:
        algo = KNOWN_ALGORITHMS["ChaCha20-Poly1305"]
        assert algo.type == AlgorithmType.SYMMETRIC
        assert algo.security_bits == 256


class TestComplianceStandards:
    def test_fips_exists(self) -> None:
        assert "FIPS-140-3" in COMPLIANCE_STANDARDS

    def test_fips_rsa_2048_ok(self) -> None:
        assert COMPLIANCE_STANDARDS["FIPS-140-3"]["RSA-2048"] is True

    def test_fips_md5_rejected(self) -> None:
        assert COMPLIANCE_STANDARDS["FIPS-140-3"]["MD5"] is False

    def test_pci_rc4_rejected(self) -> None:
        assert COMPLIANCE_STANDARDS["PCI-DSS"]["RC4"] is False

    def test_hipaa_sha256_ok(self) -> None:
        assert COMPLIANCE_STANDARDS["HIPAA"]["SHA-256"] is True


class TestEvaluateAlgorithm:
    def test_current_good_algorithm_scores_high(self) -> None:
        result = evaluate_algorithm("AES-256")
        assert result.score > 60
        assert result.algorithm.security_bits == 256
        assert len(result.warnings) == 0

    def test_legacy_algorithm_scores_low(self) -> None:
        result = evaluate_algorithm("MD5")
        assert result.score < 20
        assert len(result.warnings) >= 1
        assert any("legacy" in w.lower() for w in result.warnings)

    def test_every_legacy_algorithm_is_capped_at_twenty(self) -> None:
        legacy_names = [
            name
            for name, algorithm in KNOWN_ALGORITHMS.items()
            if algorithm.status == AlgorithmStatus.LEGACY
        ]
        assert legacy_names
        assert all(evaluate_algorithm(name).score <= 20 for name in legacy_names)

    def test_sha256_current(self) -> None:
        result = evaluate_algorithm("SHA-256")
        assert result.score >= 50

    def test_unknown_algorithm_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown algorithm"):
            evaluate_algorithm("FAKE-ALGO")

    def test_edwards_bonus(self) -> None:
        result = evaluate_algorithm("Ed25519")
        assert result.score >= 60

    def test_edge_case_security_bits_zero(self) -> None:
        result = evaluate_algorithm("MD5")
        assert result.score == 0
        assert any("no meaningful security" in w for w in result.warnings)

    def test_key_size_not_in_list_warns(self) -> None:
        result = evaluate_algorithm("RSA-2048", key_size=4096)
        assert any("not a standard key size" in w for w in result.warnings)


class TestCompareAlgorithms:
    def test_aes_vs_rc4(self) -> None:
        result = compare_algorithms("AES-256", "RC4")
        assert result.better == "AES-256"
        assert result.score_difference > 0

    def test_same_algorithm_equal(self) -> None:
        result = compare_algorithms("SHA-256", "SHA-256")
        assert result.better == "equal"
        assert result.score_difference == 0

    def test_edwards_vs_dsa(self) -> None:
        result = compare_algorithms("Ed25519", "DSA-1024")
        assert result.better == "Ed25519"

    def test_unknown_a_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown algorithm"):
            compare_algorithms("ZZZ", "AES-256")

    def test_unknown_b_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown algorithm"):
            compare_algorithms("AES-256", "ZZZ")


class TestComplianceCheck:
    def test_rsa_2048_fips(self) -> None:
        assert compliance_check("RSA-2048", "FIPS-140-3") is True

    def test_rc4_fips(self) -> None:
        assert compliance_check("RC4", "FIPS-140-3") is False

    def test_md5_fips(self) -> None:
        assert compliance_check("MD5", "FIPS-140-3") is False

    def test_sha256_soc2(self) -> None:
        assert compliance_check("SHA-256", "SOC2") is True

    def test_sha1_soc2(self) -> None:
        assert compliance_check("SHA-1", "SOC2") is False

    def test_unknown_standard_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown compliance standard"):
            compliance_check("AES-256", "FAKE-STANDARD")

    def test_unevaluated_algorithm_raises(self) -> None:
        with pytest.raises(ValueError, match="not evaluated"):
            compliance_check("ZZZ-999", "FIPS-140-3")


class TestDataclassFields:
    def test_algorithm_eval_fields(self) -> None:
        ev = AlgorithmEval(
            algorithm=KNOWN_ALGORITHMS["SHA-256"],
            score=85,
            warnings=[],
            recommendations=[],
        )
        assert ev.score == 85
        assert isinstance(ev.algorithm, AlgorithmInfo)

    def test_comparison_result_fields(self) -> None:
        cr = ComparisonResult(better="AES-256", reason="higher security", score_difference=30)
        assert cr.better == "AES-256"
        assert cr.score_difference == 30
