"""SSL certificate agent flow orchestration.

End-to-end flow: mint cert → analyze → check compliance → research CA → verify chain.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from general_ludd.ssl_agent.cert_manager import (
    CAJurisdiction,
    CertificateFields,
    ComplianceProfile,
    ComplianceResult,
    KeyPair,
    algorithm_evaluate,
    ca_jurisdiction_lookup,
    compliance_check,
    generate_ca_chain,
    generate_csr,
    generate_key_pair,
    self_sign_cert,
)


@dataclass
class SSLCertAgentResult:
    common_name: str
    key_pair: KeyPair
    cert_fields: CertificateFields
    algorithm_eval: dict[str, Any]
    compliance_results: list[ComplianceResult] = field(default_factory=list)
    ca_jurisdictions: list[CAJurisdiction] = field(default_factory=list)
    chain_verified: bool = False
    artifacts: dict[str, bytes] = field(default_factory=dict)


def ssl_agent_flow(
    common_name: str,
    key_type: str = "rsa-2048",
    validity_days: int = 365,
    profiles: list[str] | None = None,
    ca_names: list[str] | None = None,
) -> SSLCertAgentResult:
    if profiles is None:
        profiles = ["fips", "pci"]
    if ca_names is None:
        ca_names = ["letsencrypt", "digicert"]

    key_pair = generate_key_pair(key_type)

    csr_data = generate_csr(common_name, key_pair)

    cert_fields = self_sign_cert(csr_data, key_pair, validity_days)

    algo_eval = algorithm_evaluate(key_type)

    ca_chain = generate_ca_chain(
        f"Test-CA-for-{common_name}", common_name
    )

    compliance_results: list[ComplianceResult] = []
    for profile_name in profiles:
        leaf_cert_pem = ca_chain.get("leaf_cert_pem", b"")
        compliance_results.append(
            compliance_check(
                leaf_cert_pem,
                ComplianceProfile(profile_name),
            )
        )

    jurisdictions: list[CAJurisdiction] = []
    for ca_name in ca_names:
        juris = ca_jurisdiction_lookup(ca_name)
        if juris is not None:
            jurisdictions.append(juris)

    artifacts: dict[str, bytes] = {
        "public_key.pem": key_pair.public_pem,
        "private_key.pem": key_pair.private_pem,
        "csr.pem": csr_data.csr_pem,
    }
    if "ca_cert_pem" in ca_chain:
        artifacts["ca_cert.pem"] = ca_chain["ca_cert_pem"]
    if "leaf_cert_pem" in ca_chain:
        artifacts["leaf_cert.pem"] = ca_chain["leaf_cert_pem"]

    return SSLCertAgentResult(
        common_name=common_name,
        key_pair=key_pair,
        cert_fields=cert_fields,
        algorithm_eval={
            "algorithm": algo_eval.algorithm,
            "strength": algo_eval.strength,
            "is_recommended": algo_eval.is_recommended,
        },
        compliance_results=compliance_results,
        ca_jurisdictions=jurisdictions,
        chain_verified=ca_chain["chain_valid"],
        artifacts=artifacts,
    )


class SSLCertAgent:
    def __init__(self) -> None:
        self._model_call_count = 0

    def model_call(self, prompt: str, cert_data: dict[str, Any] | None = None) -> dict[str, Any]:
        self._model_call_count += 1
        return {
            "response": (
                f"Analyzed certificate for "
                f"{cert_data.get('common_name', 'unknown') if cert_data else 'unknown'}"
            ),
            "prompt": prompt,
            "call_number": self._model_call_count,
        }

    def run(self, common_name: str, key_type: str = "rsa-2048") -> dict[str, Any]:
        result = ssl_agent_flow(common_name, key_type)

        analysis_prompt = (
            f"Analyze the following SSL certificate: subject={result.cert_fields.subject_cn}, "
            f"issuer={result.cert_fields.issuer_cn}, "
            f"algorithm={result.algorithm_eval['algorithm']}"
        )
        analysis = self.model_call(
            analysis_prompt,
            {"common_name": common_name, "fields": result.cert_fields},
        )

        return {
            "agent_result": result,
            "model_analysis": analysis,
            "artifact_count": len(result.artifacts),
            "artifacts": list(result.artifacts.keys()),
        }
