"""Tests for the EvidenceChecker guardrail."""

from general_ludd.review.evidence_checker import EvidenceChecker, EvidenceResult


class TestEvidenceResult:
    def test_evidence_result_fields(self):
        result = EvidenceResult(
            supported=True,
            claim="The sky is blue",
            sources=["doc.txt:1"],
            missing_sources=[],
        )
        assert result.supported is True
        assert result.claim == "The sky is blue"
        assert result.sources == ["doc.txt:1"]
        assert result.missing_sources == []


class TestCheckClaim:
    def test_claim_with_source_is_supported(self):
        checker = EvidenceChecker()
        result = checker.check_claim(
            "The function returns 42",
            sources=["src/main.py:10"],
        )
        assert result.supported is True
        assert result.claim == "The function returns 42"
        assert "src/main.py:10" in result.sources

    def test_claim_without_source_is_unsupported(self):
        checker = EvidenceChecker()
        result = checker.check_claim("The function returns 42", sources=[])
        assert result.supported is False
        assert result.claim == "The function returns 42"
        assert len(result.missing_sources) > 0

    def test_multiple_claims_checked(self):
        checker = EvidenceChecker()
        r1 = checker.check_claim("X is 5", sources=["file.py:1"])
        r2 = checker.check_claim("Y has 10 items", sources=[])
        assert r1.supported is True
        assert r2.supported is False


class TestAuditResponse:
    def test_exempt_patterns_not_flagged(self):
        checker = EvidenceChecker()
        response = "What is the value? I think maybe it could work. OK."
        results = checker.audit_response(response, tool_outputs=[])
        assert len(results) == 0

    def test_audit_response_finds_unsupported_claims(self):
        checker = EvidenceChecker()
        response = "The module has 5 classes. The project uses FastAPI."
        results = checker.audit_response(response, tool_outputs=[])
        assert len(results) > 0
        assert all(not r.supported for r in results)

    def test_audit_response_accepts_supported_claims(self):
        checker = EvidenceChecker()
        response = (
            "The module has 5 classes (see src/module.py:12). "
            "The project uses FastAPI per pyproject.toml:15."
        )
        results = checker.audit_response(response, tool_outputs=[])
        assert all(r.supported for r in results)

    def test_file_path_counts_as_source(self):
        checker = EvidenceChecker()
        result = checker.check_claim(
            "The config has 3 entries",
            sources=["config/settings.yaml"],
        )
        assert result.supported is True

    def test_url_counts_as_source(self):
        checker = EvidenceChecker()
        result = checker.check_claim(
            "opencode supports plugins",
            sources=["https://opencode.ai/docs/plugins"],
        )
        assert result.supported is True

    def test_line_number_reference_counts_as_source(self):
        checker = EvidenceChecker()
        result = checker.check_claim(
            "The function returns 42",
            sources=["src/main.py:42"],
        )
        assert result.supported is True
