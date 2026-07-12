"""Unit tests for the SSL certificate agent flow.

Covers role invocation via ansible-runner, model_call integration,
and artifact output structure.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from general_ludd.ssl_agent import (
    SSLCertAgent,
    SSLCertAgentResult,
    ssl_agent_flow,
)


class TestSSLCertAgent:
    def test_agent_instantiation(self) -> None:
        agent = SSLCertAgent()
        assert agent is not None
        assert hasattr(agent, "model_call")
        assert hasattr(agent, "run")

    def test_model_call_increments_counter(self) -> None:
        agent = SSLCertAgent()
        assert agent._model_call_count == 0

        agent.model_call("test prompt", {"common_name": "test.example.com"})
        assert agent._model_call_count == 1

        agent.model_call("another prompt", {"common_name": "another.example.com"})
        assert agent._model_call_count == 2

    def test_model_call_returns_structured_response(self) -> None:
        agent = SSLCertAgent()
        response = agent.model_call(
            "Analyze certificate for example.com",
            {"common_name": "example.com", "fields": {}},
        )

        assert "response" in response
        assert "prompt" in response
        assert "call_number" in response
        assert response["prompt"] == "Analyze certificate for example.com"
        assert response["call_number"] == 1
        assert "example.com" in response["response"]

    def test_model_call_handles_none_cert_data(self) -> None:
        agent = SSLCertAgent()
        response = agent.model_call("general analysis", None)

        assert response["response"]
        assert "unknown" in response["response"].lower()

    def test_run_returns_expected_structure(self) -> None:
        agent = SSLCertAgent()
        result = agent.run("unittest.example.com", "rsa-2048")

        assert "agent_result" in result
        assert "model_analysis" in result
        assert "artifact_count" in result
        assert "artifacts" in result
        assert isinstance(result["agent_result"], SSLCertAgentResult)
        assert result["artifact_count"] == 5

    def test_run_artifact_names_are_correct(self) -> None:
        agent = SSLCertAgent()
        result = agent.run("artifact.example.com", "rsa-2048")

        artifacts = result["artifacts"]
        assert "public_key.pem" in artifacts
        assert "private_key.pem" in artifacts
        assert "csr.pem" in artifacts
        assert "ca_cert.pem" in artifacts
        assert "leaf_cert.pem" in artifacts

    def test_run_calls_model_call(self) -> None:
        agent = SSLCertAgent()
        result = agent.run("call-counter.example.com", "rsa-2048")

        assert agent._model_call_count == 1
        assert "model_analysis" in result
        assert result["model_analysis"]["call_number"] == 1

    def test_run_with_ecdsa_key(self) -> None:
        agent = SSLCertAgent()
        result = agent.run("ecdsa-unit.example.com", "ecdsa-p256")

        agent_result = result["agent_result"]
        assert agent_result.key_pair.key_type == "ecdsa-p256"

    def test_run_with_ed25519_key(self) -> None:
        agent = SSLCertAgent()
        result = agent.run("ed25519-unit.example.com", "ed25519")

        agent_result = result["agent_result"]
        assert agent_result.key_pair.key_type == "ed25519"


class TestSSLCertAgentResult:
    def test_agent_result_fields(self) -> None:
        result = ssl_agent_flow("fields.example.com", "rsa-2048")

        assert result.common_name == "fields.example.com"
        assert result.key_pair is not None
        assert result.cert_fields is not None
        assert result.algorithm_eval is not None
        assert isinstance(result.compliance_results, list)
        assert isinstance(result.ca_jurisdictions, list)
        assert isinstance(result.chain_verified, bool)
        assert isinstance(result.artifacts, dict)

    def test_agent_result_chain_verified_is_true(self) -> None:
        result = ssl_agent_flow("chain-true.example.com", "rsa-2048")
        assert result.chain_verified is True

    def test_agent_result_artifacts_are_bytes(self) -> None:
        result = ssl_agent_flow("artifacts-bytes.example.com", "rsa-2048")
        for key, value in result.artifacts.items():
            assert isinstance(value, bytes), f"{key} is not bytes"
            assert len(value) > 0, f"{key} is empty"


class TestRoleInvocationModelCall:
    def test_model_call_with_mocked_llm(self) -> None:
        agent = SSLCertAgent()
        mock_llm = MagicMock(return_value={
            "response": "CERTIFICATE ANALYSIS: The certificate uses RSA-2048 with SHA-256 signature.",
            "prompt": "analyze cert",
            "call_number": 1,
        })

        with patch.object(agent, "model_call", mock_llm):
            agent.run("mock-llm.example.com", "rsa-2048")
            mock_llm.assert_called_once()

    def test_model_call_receives_prompt_with_cert_data(self) -> None:
        agent = SSLCertAgent()
        captured_prompt: dict[str, str] = {}

        def capture(prompt: str, cert_data: dict | None = None) -> dict:
            captured_prompt["prompt"] = prompt
            captured_prompt["cn"] = (
                cert_data.get("common_name", "none") if cert_data else "none"
            )
            return {"response": "ok", "prompt": prompt, "call_number": 1}

        with patch.object(agent, "model_call", capture):
            agent.run("capture.example.com", "rsa-2048")

        assert "certificate" in captured_prompt["prompt"].lower()
        assert captured_prompt["cn"] == "capture.example.com"

    def test_multiple_runs_increment_counter(self) -> None:
        agent = SSLCertAgent()
        agent.run("first.example.com", "rsa-2048")
        agent.run("second.example.com", "rsa-2048")
        agent.run("third.example.com", "ecdsa-p256")

        assert agent._model_call_count == 3


class TestArtifactOutputStructure:
    def test_public_key_is_valid_pem(self) -> None:
        result = ssl_agent_flow("pem-test.example.com", "rsa-2048")
        pub_key = result.artifacts["public_key.pem"]
        assert pub_key.startswith(b"-----BEGIN PUBLIC KEY-----")
        assert pub_key.endswith(b"-----END PUBLIC KEY-----\n")

    def test_private_key_is_valid_pem(self) -> None:
        result = ssl_agent_flow("priv-test.example.com", "rsa-2048")
        priv_key = result.artifacts["private_key.pem"]
        assert priv_key.startswith(b"-----BEGIN PRIVATE KEY-----")
        assert priv_key.endswith(b"-----END PRIVATE KEY-----\n")

    def test_csr_is_valid_pem(self) -> None:
        result = ssl_agent_flow("csr-test.example.com", "rsa-2048")
        csr = result.artifacts["csr.pem"]
        assert csr.startswith(b"-----BEGIN CERTIFICATE REQUEST-----")

    def test_ca_cert_is_valid_pem(self) -> None:
        result = ssl_agent_flow("ca-test.example.com", "rsa-2048")
        ca_cert = result.artifacts["ca_cert.pem"]
        assert ca_cert.startswith(b"-----BEGIN CERTIFICATE-----")

    def test_leaf_cert_is_valid_pem(self) -> None:
        result = ssl_agent_flow("leaf-test.example.com", "rsa-2048")
        leaf_cert = result.artifacts["leaf_cert.pem"]
        assert leaf_cert.startswith(b"-----BEGIN CERTIFICATE-----")

    def test_all_artifacts_non_empty(self) -> None:
        result = ssl_agent_flow("nonempty.example.com", "rsa-2048")
        for key, value in result.artifacts.items():
            assert len(value) > 0, f"Artifact {key} is empty"

    def test_artifact_count_matches(self) -> None:
        result = ssl_agent_flow("count.example.com", "rsa-2048")
        assert len(result.artifacts) == 5

    def test_ecdsa_artifacts(self) -> None:
        result = ssl_agent_flow("ecdsa-art.example.com", "ecdsa-p256")
        assert len(result.artifacts) == 5
        assert result.artifacts["public_key.pem"].startswith(
            b"-----BEGIN PUBLIC KEY-----"
        )

    def test_ed25519_artifacts(self) -> None:
        result = ssl_agent_flow("ed25519-art.example.com", "ed25519")
        assert len(result.artifacts) == 5
        assert result.artifacts["public_key.pem"].startswith(
            b"-----BEGIN PUBLIC KEY-----"
        )


class TestAgentFlowEdgeCases:
    def test_flow_with_long_common_name(self) -> None:
        result = ssl_agent_flow(
            "very-long-domain-name-that-is-still-valid.example.com",
            "rsa-2048",
        )
        assert result.common_name == "very-long-domain-name-that-is-still-valid.example.com"
        assert result.chain_verified is True

    def test_flow_artifact_key_order_predictable(self) -> None:
        result = ssl_agent_flow("order.example.com", "rsa-2048")
        keys = list(result.artifacts.keys())
        assert "public_key.pem" in keys
        assert "private_key.pem" in keys
        assert "csr.pem" in keys
        assert "ca_cert.pem" in keys
        assert "leaf_cert.pem" in keys

    def test_flow_with_fips_only_profile(self) -> None:
        result = ssl_agent_flow(
            "fips-only.example.com",
            "rsa-2048",
            profiles=["fips"],
        )
        assert len(result.compliance_results) == 1
        assert result.compliance_results[0].profile == "fips"
        assert result.compliance_results[0].passed is True

    def test_flow_defaults_produce_valid_output(self) -> None:
        result = ssl_agent_flow("defaults.example.com")
        assert result.common_name == "defaults.example.com"
        assert result.key_pair.key_type == "rsa-2048"
        assert len(result.compliance_results) == 2
        assert len(result.ca_jurisdictions) == 2
