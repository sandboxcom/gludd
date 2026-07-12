from __future__ import annotations

from general_ludd.ssl.algorithms import (
    COMPLIANCE_STANDARDS,
    KNOWN_ALGORITHMS,
    AlgorithmStatus,
    AlgorithmType,
    ComparisonResult,
    compare_algorithms,
    compliance_check,
    evaluate_algorithm,
)


class TestKnownAlgorithms:
    def test_populated(self) -> None:
        assert len(KNOWN_ALGORITHMS) >= 30

    def test_has_rsa_entries(self) -> None:
        rsa_algos = [
            k
            for k, v in KNOWN_ALGORITHMS.items()
            if v.type == AlgorithmType.RSA
        ]
        assert len(rsa_algos) >= 3

    def test_has_ec_entries(self) -> None:
        ec_algos = [
            k
            for k, v in KNOWN_ALGORITHMS.items()
            if v.type == AlgorithmType.EC
        ]
        assert len(ec_algos) >= 3

    def test_has_edwards_entries(self) -> None:
        ed_algos = [
            k
            for k, v in KNOWN_ALGORITHMS.items()
            if v.type == AlgorithmType.EDWARDS
        ]
        assert len(ed_algos) >= 4

    def test_has_hash_entries(self) -> None:
        hash_algos = [
            k
            for k, v in KNOWN_ALGORITHMS.items()
            if v.type == AlgorithmType.HASH
        ]
        assert len(hash_algos) >= 7

    def test_legacy_algorithms_present(self) -> None:
        legacy = {
            k
            for k, v in KNOWN_ALGORITHMS.items()
            if v.status == AlgorithmStatus.LEGACY
        }
        assert "MD5" in legacy
        assert "RC4" in legacy
        assert "3DES" in legacy
        assert "SHA-1" in legacy
        assert "RSA-1024" in legacy

    def test_algorithm_info_structure(self) -> None:
        rsa_2048 = KNOWN_ALGORITHMS["RSA-2048"]
        assert rsa_2048.name == "RSA-2048"
        assert rsa_2048.type == AlgorithmType.RSA
        assert 2048 in rsa_2048.key_sizes
        assert rsa_2048.security_bits >= 112
        assert rsa_2048.status == AlgorithmStatus.CURRENT
        assert isinstance(rsa_2048.deprecation_date, str)


class TestEvaluateAlgorithm:
    def test_current_algorithm_high_score(self) -> None:
        result = evaluate_algorithm("Ed25519")
        assert result.score >= 80
        assert result.algorithm.status == AlgorithmStatus.CURRENT

    def test_legacy_algorithm_low_score(self) -> None:
        result = evaluate_algorithm("MD5")
        assert result.score <= 20
        assert result.algorithm.status == AlgorithmStatus.LEGACY

    def test_legacy_has_warnings(self) -> None:
        result = evaluate_algorithm("RC4")
        assert len(result.warnings) >= 1
        assert len(result.recommendations) >= 1

    def test_deprecated_has_migration_recommendation(self) -> None:
        result = evaluate_algorithm("SHA-1")
        has_migration = any("migrat" in r.lower() for r in result.recommendations)
        assert has_migration

    def test_unknown_algorithm_raises(self) -> None:
        try:
            evaluate_algorithm("NOT-A-REAL-ALGO")
            raise AssertionError("expected ValueError")
        except ValueError:
            pass

    def test_score_bounds(self) -> None:
        for name in KNOWN_ALGORITHMS:
            result = evaluate_algorithm(name)
            assert 0 <= result.score <= 100

    def test_high_security_bits_trend_to_higher_score(self) -> None:
        aes_128 = evaluate_algorithm("AES-128")
        aes_256 = evaluate_algorithm("AES-256")
        assert aes_256.score >= aes_128.score

    def test_edwards_current_gets_bonus(self) -> None:
        result = evaluate_algorithm("Ed25519")
        algs_same_bits = [
            v
            for v in KNOWN_ALGORITHMS.values()
            if v.security_bits == 128
            and v.status == AlgorithmStatus.CURRENT
            and v.type != AlgorithmType.EDWARDS
            and v.name != "SHA-256"
        ]
        for alg in algs_same_bits:
            eval_res = evaluate_algorithm(alg.name)
            assert result.score >= eval_res.score

    def test_zero_security_bits_scores_zero(self) -> None:
        result = evaluate_algorithm("MD5")
        assert result.score == 0
        result2 = evaluate_algorithm("RC4")
        assert result2.score == 0

    def test_key_size_mismatch_warning(self) -> None:
        result = evaluate_algorithm("AES-128", key_size=256)
        warning_texts = " ".join(result.warnings).lower()
        assert "key size" in warning_texts


class TestCompareAlgorithms:
    def test_ed25519_better_than_rsa_1024(self) -> None:
        result = compare_algorithms("Ed25519", "RSA-1024")
        assert result.better == "Ed25519"
        assert result.score_difference > 0

    def test_aes_256_better_than_aes_128(self) -> None:
        result = compare_algorithms("AES-256", "AES-128")
        assert result.better == "AES-256"
        assert result.score_difference > 0

    def test_equal_algorithms(self) -> None:
        result = compare_algorithms("SHA-256", "SHA3-256")
        assert result.better == "equal"
        assert result.score_difference == 0

    def test_unknown_first_raises(self) -> None:
        try:
            compare_algorithms("FAKE", "Ed25519")
            raise AssertionError("expected ValueError")
        except ValueError:
            pass

    def test_unknown_second_raises(self) -> None:
        try:
            compare_algorithms("Ed25519", "FAKE")
            raise AssertionError("expected ValueError")
        except ValueError:
            pass

    def test_returns_comparison_result_type(self) -> None:
        result = compare_algorithms("Ed25519", "RSA-1024")
        assert isinstance(result, ComparisonResult)
        assert isinstance(result.better, str)
        assert isinstance(result.reason, str)
        assert isinstance(result.score_difference, int)

    def test_current_beats_legacy(self) -> None:
        result = compare_algorithms("AES-256", "3DES")
        assert result.better == "AES-256"

    def test_reason_mentions_security_bits(self) -> None:
        result = compare_algorithms("AES-256", "AES-128")
        assert "security bits" in result.reason.lower()


class TestComplianceCheck:
    def test_fips_140_3_known(self) -> None:
        assert compliance_check("AES-256", "FIPS-140-3") is True
        assert compliance_check("MD5", "FIPS-140-3") is False

    def test_soc2_known(self) -> None:
        assert compliance_check("Ed25519", "SOC2") is True
        assert compliance_check("RC4", "SOC2") is False

    def test_hipaa_known(self) -> None:
        assert compliance_check("SHA-256", "HIPAA") is True
        assert compliance_check("SHA-1", "HIPAA") is False

    def test_pci_dss_known(self) -> None:
        assert compliance_check("RSA-2048", "PCI-DSS") is True
        assert compliance_check("3DES", "PCI-DSS") is False

    def test_all_standards_listed(self) -> None:
        expected = {"FIPS-140-3", "SOC2", "HIPAA", "PCI-DSS"}
        assert set(COMPLIANCE_STANDARDS.keys()) == expected

    def test_unknown_standard_raises(self) -> None:
        try:
            compliance_check("AES-256", "ISO-99999")
            raise AssertionError("expected ValueError")
        except ValueError:
            pass

    def test_unknown_algorithm_raises(self) -> None:
        try:
            compliance_check("NOT-REAL", "FIPS-140-3")
            raise AssertionError("expected ValueError")
        except ValueError:
            pass

    def test_case_insensitive_standard(self) -> None:
        assert compliance_check("AES-256", "fips-140-3") is True
        assert compliance_check("AES-256", "Hipaa") is True
        assert compliance_check("AES-256", "soc2") is True
        assert compliance_check("AES-256", "pci-dss") is True

    def test_all_current_rsa_fips_compliant(self) -> None:
        for name, info in KNOWN_ALGORITHMS.items():
            if (info.type == AlgorithmType.RSA
                    and info.status == AlgorithmStatus.CURRENT
                    and info.security_bits >= 112):
                    assert compliance_check(name, "FIPS-140-3") is True

    def test_edwards_algorithms_compliant(self) -> None:
        for standard in COMPLIANCE_STANDARDS:
            assert compliance_check("Ed25519", standard) is True
            assert compliance_check("Ed448", standard) is True
