"""Hosted OIDC contracts for the bounded Azure Container Apps live proof."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "azure-containerapp-live.yml"


def test_live_workflow_uses_protected_oidc_without_static_credentials() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "workflow_dispatch:" in workflow
    assert "schedule:" in workflow
    assert "pull_request:" not in workflow
    assert "push:" not in workflow
    assert "environment: azure-containerapp-live" in workflow
    assert "id-token: write" in workflow
    assert "attestations: write" in workflow
    assert "artifact-metadata: write" in workflow
    assert "contents: read" in workflow
    assert "cancel-in-progress: false" in workflow
    assert "vars.AZURE_CONTAINERAPP_LIVE_ENABLED == 'true'" in workflow
    assert "secrets." not in workflow
    assert "AZURE_CLIENT_SECRET" not in workflow
    assert "ARM_CLIENT_SECRET" not in workflow


def test_live_workflow_materializes_one_private_short_lived_assertion() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "actions/github-script@3a2844b7e9c422d3c10d287c895573f7108da1b3" in workflow
    assert "core.getIDToken('api://AzureADTokenExchange')" in workflow
    assert "process.env.RUNNER_TEMP" in workflow
    assert "gludd-azure-oidc.jwt" in workflow
    assert "mode: 0o600" in workflow
    assert "flag: 'wx'" in workflow
    assert "core.setSecret(token)" in workflow
    assert "fs.rmSync(tokenPath" in workflow
    assert "if: always()" in workflow
    assert "fetch-depth: 0" in workflow


def test_live_workflow_publishes_attested_environment_receipt_before_azure_oidc() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    guard = workflow.index("make azure-containerapp-environment-guard")
    attest = workflow.index("actions/attest@")
    upload = workflow.index("actions/upload-artifact@")
    azure_oidc = workflow.index("core.getIDToken('api://AzureADTokenExchange')")

    assert guard < attest < upload < azure_oidc
    assert (
        "actions/attest@1e69f48acb82d1966a394da916b4c1698aa569d6 # v4.2.2"
        in workflow
    )
    assert (
        "actions/upload-artifact@cf430e030ddbb5b0abf93d22962f4752f3646cd9"
        " # v7.0.2" in workflow
    )
    receipt_path = (
        "${{ runner.temp }}/${{ env.AZURE_CONTAINERAPP_ENVIRONMENT_RECEIPT_NAME }}"
    )
    assert f"subject-path: {receipt_path}" in workflow
    assert f"path: {receipt_path}" in workflow
    assert "archive: false" in workflow
    assert "if-no-files-found: error" in workflow
    assert "retention-days: 30" in workflow
    assert "continue-on-error" not in workflow


def test_live_workflow_receipt_name_is_unique_per_source_and_attempt() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "AZURE_CONTAINERAPP_ENVIRONMENT_RECEIPT_NAME:" in workflow
    assert "${{ github.sha }}" in workflow
    assert "${{ github.run_id }}" in workflow
    assert "${{ github.run_attempt }}" in workflow
    assert "gludd-azure-environment-protection-" in workflow


def test_live_workflow_calls_only_the_bounded_make_contract() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "make azure-self-improve-live-proof" in workflow
    assert "make azure-containerapp-live-proof" not in workflow
    assert "AZURE_CONTAINERAPP_LIVE_PROOF_AUTH_MODE: workload_identity" in workflow
    assert "AZURE_CONTAINERAPP_LIVE_PROOF_LIVE: '1'" in workflow
    assert "AZURE_CONTAINERAPP_LIVE_PROOF_MAX_COST_USD: '5'" in workflow
    assert "AZURE_CONTAINERAPP_LIVE_PROOF_TTL_MINUTES: '60'" in workflow
    assert "AZURE_CONTAINERAPP_LIVE_PROOF_RETENTION_PRESET: always_destroy" in workflow
    assert 'SELF_IMPROVE_MODEL_PATH: ""' in workflow
    assert re.search(r"timeout-minutes:\s+75\b", workflow)


def test_live_workflow_supplies_self_improve_comparison_refs() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "id: self-improve-refs" in workflow
    assert 'echo "reference=${{ github.sha }}"' in workflow
    assert "git rev-parse HEAD~1" in workflow
    assert "SELF_IMPROVE_BASELINE_REF: ${{ steps.self-improve-refs.outputs.baseline }}" in workflow
    assert "SELF_IMPROVE_REFERENCE_REF: ${{ steps.self-improve-refs.outputs.reference }}" in workflow
    assert "TARGET: azure-containerapp-live" in workflow
