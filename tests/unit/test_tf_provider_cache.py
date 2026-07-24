"""TDD for shared Terraform provider plugin cache (design doc §10 #3).

Closes the long-standing "open question": third-party providers (aws, google,
azurerm, kubernetes, vsphere, runpod, libvirt, qemu) were re-downloaded once per stack.
``infra/terraform/versions.tf`` is now the canonical version contract and
``TF_PLUGIN_CACHE_DIR`` shares each downloaded provider binary across every
stack. ``scripts/check_tf_provider_versions.py`` keeps the stacks in sync with
the contract.
"""

from __future__ import annotations

import importlib
from pathlib import Path

checker = importlib.import_module("check_tf_provider_versions")

REPO_ROOT = Path(__file__).resolve().parents[2]
TF_ROOT = REPO_ROOT / "infra" / "terraform"


def test_versions_tf_is_the_canonical_contract() -> None:
    """versions.tf declares every third-party provider used by the stacks."""
    contract = checker.parse_versions_tf(TF_ROOT / "versions.tf")
    expected = {
        "hashicorp/aws": "~> 5.0",
        "hashicorp/google": "~> 5.0",
        "hashicorp/azurerm": "~> 3.0",
        "hashicorp/kubernetes": "~> 2.31",
        "hashicorp/vsphere": "~> 2.8",
        "runpod/runpod": "~> 1.0",
        "dmacvicar/libvirt": "~> 0.7",
        "jvzq/qemu": "~> 0.1",
    }
    assert contract == expected


def test_every_stack_provider_matches_the_contract() -> None:
    """No stack pins a provider version that drifts from versions.tf."""
    stacks_dir = TF_ROOT / "stacks"
    stack_dirs = [d for d in stacks_dir.iterdir() if d.is_dir()]
    assert stack_dirs, "expected stack directories under infra/terraform/stacks"

    findings = checker.scan_stacks(stacks_dir, checker.parse_versions_tf(TF_ROOT / "versions.tf"))
    assert not findings, (
        "stack provider versions drift from infra/terraform/versions.tf:\n"
        + "\n".join(f"  {f}" for f in findings)
    )


def test_drift_is_detected(tmp_path: Path) -> None:
    """A stack pinning the wrong version is flagged as drift."""
    contract = {"hashicorp/aws": "~> 5.0"}
    bad = tmp_path / "aws-bad"
    bad.mkdir()
    (bad / "main.tf").write_text(
        'terraform {\n'
        '  required_providers {\n'
        '    aws = {\n'
        '      source  = "hashicorp/aws"\n'
        '      version = "~> 4.0"\n'
        '    }\n'
        '  }\n'
        '}\n'
    )
    findings = checker.scan_stacks(tmp_path, contract)
    assert len(findings) == 1
    assert "aws-bad" in findings[0].stack
    assert "~> 4.0" in findings[0].detail


def test_unpinned_provider_in_contract_is_drift(tmp_path: Path) -> None:
    """A stack using a contract provider but omitting the version is drift."""
    contract = {"hashicorp/aws": "~> 5.0"}
    bad = tmp_path / "aws-nover"
    bad.mkdir()
    (bad / "main.tf").write_text(
        'terraform {\n  required_providers {\n    aws = {\n      source = "hashicorp/aws"\n    }\n  }\n}\n'
    )
    findings = checker.scan_stacks(tmp_path, contract)
    assert len(findings) == 1
    assert "missing" in findings[0].detail.lower() or "unpinned" in findings[0].detail.lower()


def test_unknown_provider_is_not_drift(tmp_path: Path) -> None:
    """A provider absent from the contract is left to the trust-list gate."""
    contract = {"hashicorp/aws": "~> 5.0"}
    ok = tmp_path / "other"
    ok.mkdir()
    (ok / "main.tf").write_text(
        'terraform {\n'
        '  required_providers {\n'
        '    custom = {\n'
        '      source  = "somecorp/custom"\n'
        '      version = "~> 1.0"\n'
        '    }\n'
        '  }\n'
        '}\n'
    )
    findings = checker.scan_stacks(tmp_path, contract)
    assert findings == []
