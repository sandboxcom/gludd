"""Hosted OIDC contracts for the bounded Azure Container Apps live proof."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "azure-containerapp-live.yml"
MAKEFILE = ROOT / "Makefile"


def test_live_workflow_uses_protected_oidc_without_static_credentials() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "workflow_dispatch:" in workflow
    assert "schedule:" in workflow
    assert "pull_request:" not in workflow
    assert "push:" not in workflow
    assert "environment: azure-containerapp-live" in workflow
    assert "id-token: write" in workflow
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


def test_live_workflow_uses_catalog_bound_refs_not_candidate_commit() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    makefile = MAKEFILE.read_text(encoding="utf-8")

    assert "Resolve self-improve comparison refs" not in workflow
    assert "github.sha" not in workflow
    assert "HEAD~1" not in workflow
    assert "SELF_IMPROVE_BASELINE_REF:" not in workflow
    assert "SELF_IMPROVE_REFERENCE_REF:" not in workflow
    assert (
        "SELF_IMPROVE_CATALOG_TRUTH_BASELINE_REF := "
        "eac05dc88c03f14fbd7dd5f4c6d72943609d9e26"
    ) in makefile
    assert (
        "SELF_IMPROVE_CATALOG_TRUTH_REFERENCE_REF := "
        "80b381bd87f32487d784964ce93566e3b016b191"
    ) in makefile
    azure_recipe = makefile.split("azure-self-improve-live-proof:", 1)[1].split(
        "\n\n", 1
    )[0]
    assert "$(SELF_IMPROVE_CATALOG_TRUTH_BASELINE_REF)" in azure_recipe
    assert "$(SELF_IMPROVE_CATALOG_TRUTH_REFERENCE_REF)" in azure_recipe
    assert "TARGET: azure-containerapp-live" in workflow
