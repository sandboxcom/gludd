"""TDD for shared Terraform provider plugin cache (design doc §10 #3).

Closes the long-standing "open question": third-party providers (aws, google,
azurerm, azapi, kubernetes, vsphere, runpod, libvirt, qemu) were re-downloaded once per stack.
``infra/terraform/versions.tf`` is now the canonical version contract and
``TF_PLUGIN_CACHE_DIR`` shares each downloaded provider binary across every
stack. ``scripts/check_tf_provider_versions.py`` keeps the stacks in sync with
the contract.
"""

from __future__ import annotations

import ast
import importlib
import os
import subprocess
from pathlib import Path

import pytest
from scripts.makefile_layout import compose_makefile

checker = importlib.import_module("check_tf_provider_versions")

REPO_ROOT = Path(__file__).resolve().parents[2]
TF_ROOT = REPO_ROOT / "infra" / "terraform"


@pytest.mark.parametrize("relative_path", [
    "tests/e2e/test_terraform_all_stacks_valid.py",
    "tests/e2e/test_iam_smoke.py",
    "tests/unit/test_terraform_validate.py",
    "tests/unit/test_terraform_modules.py",
    "tests/unit/test_opa_policies.py",
    "tests/unit/test_onboard_aws.py",
])
def test_provider_consumers_reuse_the_owned_environment_boundary(relative_path: str) -> None:
    tree = ast.parse((REPO_ROOT / relative_path).read_text(encoding="utf-8"))
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
    assert sum(isinstance(call.func, ast.Name) and call.func.id == "terraform_test_environment"
               for call in calls) == 1


def test_e2e_terraform_copy_omits_provider_materializations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tests.e2e import test_terraform_all_stacks_valid as e2e

    source = tmp_path / "source"
    stack = source / "stacks" / "example"
    module = source / "modules" / "example"
    for directory in (stack, module):
        directory.mkdir(parents=True)
        (directory / "main.tf").write_text("# source input\n", encoding="utf-8")
        initialized = directory / ".terraform"
        initialized.mkdir()
        (initialized / "provider").write_bytes(b"generated provider")
    cache = source / ".plugin-cache"
    cache.mkdir()
    provider = cache / "provider"
    provider.write_bytes(b"cached provider")
    (cache / "provider-link").symlink_to(provider)
    monkeypatch.setattr(e2e, "STACKS_DIR", source / "stacks")

    result = e2e._copy_terraform_tree(tmp_path / "copy")

    assert result == tmp_path / "copy" / "terraform" / "stacks"
    assert (result / "example" / "main.tf").read_text() == "# source input\n"
    assert (result.parent / "modules" / "example" / "main.tf").read_text() == "# source input\n"
    assert not (result.parent / ".plugin-cache").exists()
    assert not list(result.parent.rglob(".terraform"))
    assert provider.read_bytes() == b"cached provider"
    assert (cache / "provider-link").is_symlink()


def test_e2e_infra_command_keeps_owned_cache_and_native_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tests.e2e import test_terraform_all_stacks_valid as e2e

    owned = tmp_path / "owned-cache"
    fallback = tmp_path / "checkout-cache"
    monkeypatch.setenv("TF_PLUGIN_CACHE_DIR", str(owned))
    monkeypatch.setattr(e2e, "TF_PLUGIN_CACHE", fallback)
    before = dict(os.environ)
    captured: dict[str, object] = {}
    expected = subprocess.CompletedProcess(["terraform", "validate", "-json"], 7, "out", "err")

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured.update(command=command, **kwargs)
        return expected

    monkeypatch.setattr(subprocess, "run", run)
    result = e2e._run_infra("terraform", ["validate", "-json"], tmp_path, timeout=17)

    assert result is expected
    environment = captured.pop("env")
    assert captured == {
        "command": ["terraform", "validate", "-json"],
        "capture_output": True, "text": True, "cwd": str(tmp_path), "timeout": 17,
    }
    assert isinstance(environment, dict)
    assert environment["TF_PLUGIN_CACHE_DIR"] == str(owned)
    assert environment["TF_IN_AUTOMATION"] == "true"
    assert all(environment[key] == value for key, value in before.items()
               if key not in {"TF_PLUGIN_CACHE_DIR", "TF_IN_AUTOMATION"})
    assert owned.is_dir()
    assert not fallback.exists()
    assert dict(os.environ) == before


def test_tf_clean_preserves_tracked_cache_sentinel() -> None:
    makefile = compose_makefile(REPO_ROOT / "Makefile")
    recipe = makefile.split("\ntf-clean:\n", 1)[1].split("\n\n", 1)[0]

    assert "mkdir -p $(TF_PLUGIN_CACHE)" in recipe
    assert "find $(TF_PLUGIN_CACHE) -mindepth 1 ! -name .gitkeep" in recipe
    assert "\trm -rf $(TF_PLUGIN_CACHE)\n" not in recipe
    assert "touch $(TF_PLUGIN_CACHE)/.gitkeep" not in recipe


def test_versions_tf_is_the_canonical_contract() -> None:
    """versions.tf declares every third-party provider used by the stacks."""
    contract = checker.parse_versions_tf(TF_ROOT / "versions.tf")
    expected = {
        "hashicorp/aws": "~> 5.0",
        "hashicorp/google": "~> 5.0",
        "hashicorp/azurerm": "~> 4.55",
        "Azure/azapi": "~> 2.0",
        "hashicorp/kubernetes": "~> 2.31",
        "vmware/vsphere": "~> 2.8",
        "runpod/runpod": "~> 1.0",
        "dmacvicar/libvirt": "~> 0.7",
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


def test_azurerm_contract_supports_container_apps_gpu_profiles() -> None:
    """AzureRM 4.55 introduced validation for Consumption-GPU profile SKUs."""
    contract = checker.parse_versions_tf(TF_ROOT / "versions.tf")
    assert contract["hashicorp/azurerm"] == "~> 4.55"


def test_azapi_contract_can_omit_serverless_gpu_capacity_fields() -> None:
    """AzAPI owns the GPU environment until AzureRM stops serializing counts."""
    contract = checker.parse_versions_tf(TF_ROOT / "versions.tf")
    assert contract["Azure/azapi"] == "~> 2.0"


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
