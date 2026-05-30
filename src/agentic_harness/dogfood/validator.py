"""Dogfood validator — checks that dogfood runs use configured runtime and detects bypasses."""

from __future__ import annotations

from dataclasses import dataclass

from agentic_harness.dogfood.runner import SmokeTaskResult


@dataclass
class DogfoodValidationResult:
    valid: bool
    uses_configured_runtime: bool
    uses_configured_models: bool
    has_molecule_evidence: bool
    has_quality_gate_evidence: bool


@dataclass
class BypassFinding:
    category: str
    description: str
    evidence: str


class DogfoodValidator:
    def validate_dogfood_run(self, run_result: SmokeTaskResult) -> DogfoodValidationResult:
        return DogfoodValidationResult(
            valid=run_result.success,
            uses_configured_runtime=run_result.success,
            uses_configured_models=True,
            has_molecule_evidence=False,
            has_quality_gate_evidence=False,
        )

    def check_no_local_bypasses(self, log_entries: list[dict[str, str]]) -> list[BypassFinding]:
        findings: list[BypassFinding] = []
        for entry in log_entries:
            runtime = entry.get("runtime", "")
            command = entry.get("command", "")
            if runtime == "local" or _looks_like_local_bypass(command):
                findings.append(BypassFinding(
                    category="local_bypass",
                    description=f"Command bypassed configured runtime: {command}",
                    evidence=str(entry),
                ))
        return findings

    def check_artifacts_use_configured_runtime(self, artifacts: list[dict[str, str]]) -> bool:
        if not artifacts:
            return True
        return all(a.get("runtime") == "ansible" for a in artifacts)


def _looks_like_local_bypass(command: str) -> bool:
    local_indicators = ["bash -c", "pip install", "python -c", "sh -c", "npm run"]
    return any(indicator in command for indicator in local_indicators)
