"""Structural tests for ssl_agent/agent_flow.py."""

from __future__ import annotations

from general_ludd.ssl_agent.agent_flow import SSLCertAgent
from general_ludd.ssl_agent.agent_flow import SSLCertAgentResult
from general_ludd.ssl_agent.agent_flow import ssl_agent_flow


def test_ssl_agent_flow_default_profiles():
    result = ssl_agent_flow("example.com")
    assert isinstance(result, SSLCertAgentResult)
    assert result.common_name == "example.com"
    assert result.chain_verified == True


def test_ssl_agent_flow_with_custom_key_type():
    result = ssl_agent_flow("example.org", key_type="ecdsa-p256")
    assert result.common_name == "example.org"
    assert result.algorithm_eval["algorithm"] == "ecdsa-p256"


def test_ssl_agent_flow_artifacts():
    result = ssl_agent_flow("test.example.com")
    assert "public_key.pem" in result.artifacts
    assert "private_key.pem" in result.artifacts
    assert "csr.pem" in result.artifacts
    assert "ca_cert.pem" in result.artifacts
    assert "leaf_cert.pem" in result.artifacts
    assert len(result.artifacts) >= 5


def test_ssl_agent_flow_compliance_results():
    result = ssl_agent_flow("test.example.com", profiles=["fips", "pci"])
    assert len(result.compliance_results) == 2


def test_ssl_agent_flow_ca_jurisdictions():
    result = ssl_agent_flow("test.example.com", ca_names=["letsencrypt", "digicert", "globalsign"])
    assert len(result.ca_jurisdictions) >= 1


def test_ssl_agent_flow_algorithm_eval():
    result = ssl_agent_flow("example.com", key_type="rsa-2048")
    assert "algorithm" in result.algorithm_eval
    assert "strength" in result.algorithm_eval
    assert "is_recommended" in result.algorithm_eval


def test_ssl_cert_agent_init():
    agent = SSLCertAgent()
    assert agent._model_call_count == 0


def test_ssl_cert_agent_model_call():
    agent = SSLCertAgent()
    result = agent.model_call("analyze", {"common_name": "example.com"})
    assert agent._model_call_count == 1
    assert "example.com" in result["response"]
    assert result["prompt"] == "analyze"
    assert result["call_number"] == 1


def test_ssl_cert_agent_model_call_no_cert_data():
    agent = SSLCertAgent()
    result = agent.model_call("prompt only")
    assert "unknown" in result["response"]


def test_ssl_cert_agent_run():
    agent = SSLCertAgent()
    result = agent.run("example.com", key_type="rsa-2048")
    assert isinstance(result["agent_result"], SSLCertAgentResult)
    assert "model_analysis" in result
    assert result["artifact_count"] >= 5
    assert "public_key.pem" in result["artifacts"]
