from __future__ import annotations

import pytest

from general_ludd.ssl.compliance import (
    FIPS_140_3,
    HIPAA,
    ISO_27001,
    PCI_DSS,
    SOC2,
    ComplianceProfile,
    ComplianceResult,
    FedRAMP,
    check_compliance,
    get_profile,
    list_profiles,
)


class TestProfiles:
    def test_all_six_profiles_exist(self) -> None:
        names = list_profiles()
        assert len(names) == 6
        assert "FIPS_140_3" in names
        assert "SOC2" in names
        assert "HIPAA" in names
        assert "PCI_DSS" in names
        assert "FedRAMP" in names
        assert "ISO_27001" in names

    def test_fips_140_3_defaults(self) -> None:
        p = FIPS_140_3
        assert p.name == "FIPS_140_3"
        assert p.minimum_key_size == 2048
        assert "RSA" in p.allowed_algorithms
        assert "ECDSA" in p.allowed_algorithms
        assert "Ed25519" in p.allowed_algorithms
        assert "digitalSignature" in p.required_key_usage
        assert "keyEncipherment" in p.required_key_usage
        assert p.version == "1.0"
        assert isinstance(p.description, str) and len(p.description) > 0

    def test_soc2_defaults(self) -> None:
        p = SOC2
        assert p.name == "SOC2"
        assert p.minimum_key_size == 2048
        assert "RSA" in p.allowed_algorithms
        assert "ECDSA" in p.allowed_algorithms
        assert p.required_key_usage == ["digitalSignature"]
        assert p.version == "1.0"

    def test_hipaa_defaults(self) -> None:
        p = HIPAA
        assert p.name == "HIPAA"
        assert p.minimum_key_size == 2048
        assert "RSA" in p.allowed_algorithms
        assert "ECDSA" in p.allowed_algorithms
        assert "AES-256" in p.allowed_algorithms
        assert "digitalSignature" in p.required_key_usage
        assert "keyEncipherment" in p.required_key_usage
        assert "dataEncipherment" in p.required_key_usage
        assert len(p.required_key_usage) == 3

    def test_pci_dss_defaults(self) -> None:
        p = PCI_DSS
        assert p.name == "PCI_DSS"
        assert p.minimum_key_size == 2048
        assert "RSA" in p.allowed_algorithms
        assert "ECDSA" in p.allowed_algorithms
        assert "digitalSignature" in p.required_key_usage
        assert "keyEncipherment" in p.required_key_usage

    def test_fedramp_defaults(self) -> None:
        p = FedRAMP
        assert p.name == "FedRAMP"
        assert p.minimum_key_size == 2048
        assert "RSA" in p.allowed_algorithms
        assert "ECDSA" in p.allowed_algorithms
        assert "Ed25519" in p.allowed_algorithms
        assert "digitalSignature" in p.required_key_usage
        assert "keyEncipherment" in p.required_key_usage

    def test_iso_27001_defaults(self) -> None:
        p = ISO_27001
        assert p.name == "ISO_27001"
        assert p.minimum_key_size == 2048
        assert "RSA" in p.allowed_algorithms
        assert p.required_key_usage == ["digitalSignature"]

    def test_all_profiles_version_1_0(self) -> None:
        for name in list_profiles():
            p = get_profile(name)
            assert p.version == "1.0", f"{name} version mismatch"

    def test_all_profiles_min_2048_key(self) -> None:
        for name in list_profiles():
            p = get_profile(name)
            assert p.minimum_key_size >= 2048, f"{name} key size too low"


class TestComplianceCheck:
    @staticmethod
    def _make_cert(**overrides: object) -> dict:
        defaults: dict = {
            "algorithm": "RSA",
            "key_size": 4096,
            "key_usage": ["digitalSignature", "keyEncipherment"],
            "extended_key_usage": ["serverAuth"],
            "issuer": {"CN": "Test CA"},
            "subject": {"CN": "test.example.com"},
            "not_valid_before": "2026-01-01T00:00:00Z",
            "not_valid_after": "2027-01-01T00:00:00Z",
            "sans": ["test.example.com"],
        }
        defaults.update(overrides)
        return defaults

    def test_compliant_rsa_4096_passes_all_profiles(self) -> None:
        cert = self._make_cert(
            algorithm="RSA",
            key_size=4096,
            key_usage=["digitalSignature", "keyEncipherment", "dataEncipherment"],
        )
        for name in list_profiles():
            profile = get_profile(name)
            result = check_compliance(cert, profile)
            assert result.compliant, f"{name}: violations={result.violations}"

    def test_weak_key_size_violation(self) -> None:
        cert = self._make_cert(algorithm="RSA", key_size=1024)
        result = check_compliance(cert, FIPS_140_3)
        assert not result.compliant
        assert any("Key size" in v and "1024" in v for v in result.violations)

    def test_wrong_algorithm_violation(self) -> None:
        cert = self._make_cert(algorithm="DSA", key_size=2048)
        result = check_compliance(cert, HIPAA)
        assert not result.compliant
        assert any("DSA" in v for v in result.violations)

    def test_missing_key_usage_violation(self) -> None:
        cert = self._make_cert(algorithm="RSA", key_size=2048, key_usage=["digitalSignature"])
        result = check_compliance(cert, FIPS_140_3)
        assert not result.compliant
        assert any("keyEncipherment" in v for v in result.violations)

    def test_missing_all_key_usage_violation(self) -> None:
        cert = self._make_cert(algorithm="RSA", key_size=2048, key_usage=[])
        result = check_compliance(cert, HIPAA)
        assert not result.compliant
        assert len(result.violations) >= 3

    def test_ecdsa_p256_compliant_with_fips(self) -> None:
        cert = self._make_cert(
            algorithm="ECDSA",
            key_size=256,
            key_usage=["digitalSignature", "keyEncipherment"],
        )
        result = check_compliance(cert, FIPS_140_3)
        assert result.compliant

    def test_ed25519_compliant_with_fips(self) -> None:
        cert = self._make_cert(
            algorithm="Ed25519",
            key_size=256,
            key_usage=["digitalSignature", "keyEncipherment"],
        )
        result = check_compliance(cert, FIPS_140_3)
        assert result.compliant

    def test_empty_algorithm_field_handled(self) -> None:
        cert = self._make_cert(algorithm="")
        result = check_compliance(cert, SOC2)
        assert result.compliant

    def test_missing_key_size_defaults_to_zero(self) -> None:
        cert = {
            "algorithm": "RSA",
            "key_usage": ["digitalSignature"],
            "issuer": {"CN": "CA"},
            "subject": {"CN": "test"},
            "not_valid_before": "2026-01-01",
            "not_valid_after": "2027-01-01",
            "sans": [],
            "extended_key_usage": [],
        }
        result = check_compliance(cert, SOC2)
        assert not result.compliant
        assert any("Key size" in v for v in result.violations)

    def test_wildcard_san_produces_warning(self) -> None:
        cert = self._make_cert(
            algorithm="RSA",
            key_size=4096,
            key_usage=["digitalSignature", "keyEncipherment"],
            sans=["*.example.com"],
        )
        result = check_compliance(cert, SOC2)
        assert result.compliant
        assert any("Wildcard" in w for w in result.warnings)

    def test_rsa_1024_produces_recommendation(self) -> None:
        cert = self._make_cert(algorithm="RSA", key_size=1024, key_usage=["digitalSignature"])
        result = check_compliance(cert, SOC2)
        assert not result.compliant
        assert any("upgrade" in r.lower() for r in result.recommendations)

    def test_ecdsa_192_produces_warning(self) -> None:
        cert = self._make_cert(
            algorithm="ECDSA",
            key_size=192,
            key_usage=["digitalSignature"],
        )
        result = check_compliance(cert, SOC2)
        assert any("ECDSA key size" in w for w in result.warnings)

    def test_profile_referenced_in_result(self) -> None:
        cert = self._make_cert()
        result = check_compliance(cert, PCI_DSS)
        assert result.profile is PCI_DSS
        assert result.profile.name == "PCI_DSS"

    def test_result_structure_defaults(self) -> None:
        cert = self._make_cert()
        result = check_compliance(cert, SOC2)
        assert result.compliant
        assert result.violations == []
        assert result.warnings == []
        assert result.recommendations == []


class TestGetProfile:
    def test_known_profile_returns_profile(self) -> None:
        p = get_profile("FIPS_140_3")
        assert isinstance(p, ComplianceProfile)
        assert p.name == "FIPS_140_3"

    def test_unknown_profile_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="Unknown compliance profile"):
            get_profile("NONEXISTENT")

    def test_case_sensitive_lookup(self) -> None:
        with pytest.raises(ValueError):
            get_profile("fips_140_3")

    def test_all_six_profiles_lookup_returns_correct_type(self) -> None:
        for name in ["FIPS_140_3", "SOC2", "HIPAA", "PCI_DSS", "FedRAMP", "ISO_27001"]:
            p = get_profile(name)
            assert p.name == name
            assert isinstance(p, ComplianceProfile)


class TestListProfiles:
    def test_returns_list_of_strings(self) -> None:
        names = list_profiles()
        assert isinstance(names, list)
        assert all(isinstance(n, str) for n in names)

    def test_returns_all_six_names(self) -> None:
        names = list_profiles()
        assert sorted(names) == sorted([
            "FIPS_140_3", "SOC2", "HIPAA", "PCI_DSS", "FedRAMP", "ISO_27001",
        ])

    def test_no_duplicates(self) -> None:
        names = list_profiles()
        assert len(names) == len(set(names))

    def test_result_is_new_list(self) -> None:
        a = list_profiles()
        b = list_profiles()
        assert a == b
        assert a is not b


class TestComplianceResult:
    def test_default_fields(self) -> None:
        result = ComplianceResult(profile=FIPS_140_3, compliant=True)
        assert result.violations == []
        assert result.warnings == []
        assert result.recommendations == []

    def test_with_violations(self) -> None:
        result = ComplianceResult(
            profile=SOC2,
            compliant=False,
            violations=["weak key"],
            warnings=["wildcard"],
            recommendations=["upgrade"],
        )
        assert not result.compliant
        assert result.violations == ["weak key"]
        assert result.warnings == ["wildcard"]
        assert result.recommendations == ["upgrade"]
