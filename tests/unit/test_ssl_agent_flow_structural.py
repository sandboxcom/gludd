"""Structural tests for ssl_agent/agent_flow.py — SSL certificate agent flow."""

from __future__ import annotations

from general_ludd.ssl_agent.agent_flow import (
    SSLCertAgent,
    SSLCertAgentResult,
    ssl_agent_flow,
)


class TestSSLCertAgentResult:
    def test_minimal_construction(self):
        from general_ludd.ssl_agent.cert_manager import KeyPair
        key = KeyPair(
            private_pem=b"private",
            public_pem=b"public",
            key_type="rsa-2048",
        )
        from general_ludd.ssl_agent.cert_manager import CertificateFields
        cert = CertificateFields(
            subject_cn="example.com",
            issuer_cn="Test-CA",
        )
        result = SSLCertAgentResult(
            common_name="example.com",
            key_pair=key,
            cert_fields=cert,
            algorithm_eval={"algorithm": "rsa-2048", "strength": 112, "is_recommended": True},
        )
        assert result.common_name == "example.com"
        assert result.key_pair == key
        assert result.cert_fields == cert
        assert result.compliance_results == []
        assert result.ca_jurisdictions == []
        assert result.chain_verified is False
        assert result.artifacts == {}


class TestSslAgentFlow:
    def test_returns_result_for_default_args(self):
        result = ssl_agent_flow("test.example.com")
        assert isinstance(result, SSLCertAgentResult)
        assert result.common_name == "test.example.com"

    def test_custom_key_type(self):
        result = ssl_agent_flow("custom.com", key_type="ed25519")
        assert result.common_name == "custom.com"

    def test_has_artifacts(self):
        result = ssl_agent_flow("artifact.example.com")
        assert len(result.artifacts) >= 3
        assert "public_key.pem" in result.artifacts
        assert "private_key.pem" in result.artifacts
        assert "csr.pem" in result.artifacts

    def test_default_compliance_profiles_run(self):
        result = ssl_agent_flow("compliance.example.com")
        assert len(result.compliance_results) >= 2

    def test_algorithm_eval_populated(self):
        result = ssl_agent_flow("algo.example.com")
        assert "algorithm" in result.algorithm_eval
        assert "strength" in result.algorithm_eval
        assert "is_recommended" in result.algorithm_eval


class TestSSLCertAgent:
    def test_construction(self):
        agent = SSLCertAgent()
        assert agent._model_call_count == 0

    def test_model_call_increments_counter(self):
        agent = SSLCertAgent()
        result = agent.model_call("test prompt")
        assert agent._model_call_count == 1
        assert "response" in result
        assert result["call_number"] == 1

    def test_model_call_with_cert_data(self):
        agent = SSLCertAgent()
        result = agent.model_call("analyze", {"common_name": "example.com"})
        assert "example.com" in result["response"]

    def test_run_returns_dict(self):
        agent = SSLCertAgent()
        result = agent.run("test.example.com")
        assert "agent_result" in result
        assert "model_analysis" in result
        assert isinstance(result["artifact_count"], int)
        assert isinstance(result["artifacts"], list)

    def test_run_increments_model_calls(self):
        agent = SSLCertAgent()
        agent.run("test.example.com")
        assert agent._model_call_count >= 1
