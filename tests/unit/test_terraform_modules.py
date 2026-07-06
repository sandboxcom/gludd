"""Tests for infra/terraform static modules (Phase 1 of TERRAFORM_INFRA_STRUCTURE.md).

Scope:
- Every module dir has main.tf, variables.tf, outputs.tf.
- Every module main.tf parses as valid HCL (python-hcl2 if installed, else a
  structural assertion that it contains a `resource` or `module` block).
- `terraform validate` is run if the terraform binary is present; tests skip
  cleanly otherwise.
- Modules contain no hardcoded provider credentials (no access_key / secret_key
  / password literals).
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, cast

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
MODULES_DIR = REPO_ROOT / "infra" / "terraform" / "modules"

EXPECTED_MODULES = {"vllm-server", "llamacpp-server", "gpu-cost-watchdog", "network"}
REQUIRED_FILES = ("main.tf", "variables.tf", "outputs.tf")

# Credential attribute names that must never appear as literal HCL attributes.
# (Provider creds arrive via env / SecretsManager, never tfvars/HCL literals.)
_CREDENTIAL_PATTERNS = [
    re.compile(r'\baccess_key\s*=', re.IGNORECASE),
    re.compile(r'\bsecret_key\s*=', re.IGNORECASE),
    re.compile(r'\bsecret_access_key\s*=', re.IGNORECASE),
    re.compile(r'\bpassword\s*=', re.IGNORECASE),
    re.compile(r'\bapi_token\s*=', re.IGNORECASE),
    re.compile(r'\bprivate_key\s*=', re.IGNORECASE),
]


def _all_module_dirs() -> list[Path]:
    if not MODULES_DIR.is_dir():
        return []
    return sorted(p for p in MODULES_DIR.iterdir() if p.is_dir())


def _has_terraform_binary() -> bool:
    return shutil.which("terraform") is not None


def _try_import_hcl2():
    try:
        import hcl2
    except ImportError:
        return None
    return cast(Any, hcl2)


def _strip_comments(text: str) -> str:
    """Drop leading-`#` comment lines so docstrings mentioning `access_key`
    (e.g. the AWS creds note in examples) don't trip the credential check.
    Real credential literals are uncommented HCL.
    """
    return "\n".join(
        line for line in text.splitlines()
        if not line.lstrip().startswith("#")
    )


def _strip_var_refs(text: str) -> str:
    """Drop HCL attribute lines whose RHS is a ``var.<name>`` reference.

    A credential *literal* is a hardcoded string/secret embedded in the HCL
    body (``password = "hunter2"``).  # pragma: allowlist secret
    A credential *reference* is an attribute
    that pulls the value from a variable at apply time
    (``password = var.vsphere_password``) — that is the SAFE pattern, the
    whole point of the OpenBao/SecretsManager flow. The credential-scan
    regex matches the attribute name regardless of RHS, so we drop the
    safe-reference lines before scanning and let only true literals through.
    """
    return "\n".join(
        line for line in text.splitlines()
        if not re.search(r"=\s*var\.[A-Za-z_][A-Za-z0-9_]*\s*$", line)
    )


# ---------------------------------------------------------------------------
# Structural: every module has the required files.
# ---------------------------------------------------------------------------


class TestModuleStructure:
    def test_modules_dir_exists(self):
        assert MODULES_DIR.is_dir(), f"expected {MODULES_DIR} to exist"

    def test_expected_modules_present(self):
        actual = {p.name for p in _all_module_dirs()}
        missing = EXPECTED_MODULES - actual
        assert not missing, f"missing module dirs: {missing}"

    @pytest.mark.parametrize("module_name", sorted(EXPECTED_MODULES))
    def test_module_has_required_files(self, module_name: str):
        mod = MODULES_DIR / module_name
        assert mod.is_dir(), f"module dir missing: {mod}"
        for fname in REQUIRED_FILES:
            f = mod / fname
            assert f.is_file(), f"missing {fname} in module {module_name}"


# ---------------------------------------------------------------------------
# HCL validity: parse with hcl2 if available, else structural block check.
# ---------------------------------------------------------------------------


def _assert_structurally_valid_hcl(text: str, path: Path):
    """Fallback when python-hcl2 is not installed.

    Asserts the file has at least one top-level HCL block (`resource`, `module`,
    `variable`, `output`, `locals`, `data`, `provider`, or `terraform`).
    """
    block_pattern = re.compile(
        r'^(resource|module|variable|output|locals|data|provider|terraform)\b',
        re.MULTILINE,
    )
    assert block_pattern.search(text), (
        f"{path}: no top-level HCL block found (resource/module/variable/output/...)"
    )


class TestHclValidity:
    @pytest.mark.parametrize("module_name", sorted(EXPECTED_MODULES))
    def test_main_tf_parses(self, module_name: str):
        main_tf = MODULES_DIR / module_name / "main.tf"
        text = main_tf.read_text()
        hcl2 = _try_import_hcl2()
        if hcl2 is not None:
            import io

            ast = hcl2.load(io.StringIO(text))
            assert isinstance(ast, dict), f"{main_tf}: hcl2 did not return a dict"
            assert len(ast) > 0, f"{main_tf}: hcl2 parsed zero top-level blocks"
        else:
            _assert_structurally_valid_hcl(text, main_tf)

    @pytest.mark.parametrize("module_name", sorted(EXPECTED_MODULES))
    def test_variables_tf_parses(self, module_name: str):
        variables_tf = MODULES_DIR / module_name / "variables.tf"
        text = variables_tf.read_text()
        hcl2 = _try_import_hcl2()
        if hcl2 is not None:
            import io

            ast = hcl2.load(io.StringIO(text))
            assert isinstance(ast, dict), f"{variables_tf}: hcl2 did not return a dict"
        else:
            _assert_structurally_valid_hcl(text, variables_tf)

    @pytest.mark.parametrize("module_name", sorted(EXPECTED_MODULES))
    def test_outputs_tf_parses(self, module_name: str):
        outputs_tf = MODULES_DIR / module_name / "outputs.tf"
        text = outputs_tf.read_text()
        hcl2 = _try_import_hcl2()
        if hcl2 is not None:
            import io

            ast = hcl2.load(io.StringIO(text))
            assert isinstance(ast, dict), f"{outputs_tf}: hcl2 did not return a dict"
        else:
            _assert_structurally_valid_hcl(text, outputs_tf)


# ---------------------------------------------------------------------------
# terraform fmt -check + terraform validate (skipped if binary absent).
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _has_terraform_binary(), reason="terraform binary not installed")
class TestTerraformBinaryValidate:
    @pytest.mark.parametrize("module_name", sorted(EXPECTED_MODULES))
    def test_module_fmt_and_validate(self, module_name: str, tmp_path: Path):
        mod_src = MODULES_DIR / module_name
        # Copy into tmp_path so `terraform init` is hermetic and cached.
        mod_dst = tmp_path / module_name
        mod_dst.mkdir()
        for fname in REQUIRED_FILES:
            (mod_dst / fname).write_text((mod_src / fname).read_text())

        # fmt -check
        fmt = subprocess.run(
            ["terraform", "fmt", "-check", "-diff", str(mod_dst)],
            capture_output=True,
            text=True,
        )
        assert fmt.returncode == 0, (
            f"terraform fmt -check failed for {module_name}:\n"
            f"stdout:\n{fmt.stdout}\nstderr:\n{fmt.stderr}"
        )

        # init (no provider downloads expected: modules use only terraform_data)
        init = subprocess.run(
            ["terraform", "init", "-backend=false", "-input=false"],
            cwd=str(mod_dst),
            capture_output=True,
            text=True,
        )
        assert init.returncode == 0, (
            f"terraform init failed for {module_name}:\n"
            f"stdout:\n{init.stdout}\nstderr:\n{init.stderr}"
        )

        # validate
        validate = subprocess.run(
            ["terraform", "validate"],
            cwd=str(mod_dst),
            capture_output=True,
            text=True,
        )
        assert validate.returncode == 0, (
            f"terraform validate failed for {module_name}:\n"
            f"stdout:\n{validate.stdout}\nstderr:\n{validate.stderr}"
        )


# ---------------------------------------------------------------------------
# Security: no hardcoded provider credentials.
# ---------------------------------------------------------------------------


class TestNoHardcodedCredentials:
    @pytest.mark.parametrize("module_name", sorted(EXPECTED_MODULES))
    def test_no_credential_literals(self, module_name: str):
        mod = MODULES_DIR / module_name
        for fname in REQUIRED_FILES:
            f = mod / fname
            if not f.is_file():
                continue
            active = _strip_var_refs(_strip_comments(f.read_text()))
            for pat in _CREDENTIAL_PATTERNS:
                assert not pat.search(active), (
                    f"{f}: credential literal matched by {pat.pattern!r}"
                )

    def test_examples_are_placeholders_only(self):
        examples_dir = REPO_ROOT / "infra" / "terraform" / "examples"
        assert examples_dir.is_dir(), f"expected {examples_dir} to exist"
        for ex in examples_dir.glob("*.tfvars.example"):
            active = _strip_var_refs(_strip_comments(ex.read_text()))
            for pat in _CREDENTIAL_PATTERNS:
                assert not pat.search(active), (
                    f"{ex}: credential literal matched by {pat.pattern!r}"
                )


# ---------------------------------------------------------------------------
# Phase 4: thin stacks that compose the static modules — no inline resources.
# ---------------------------------------------------------------------------

STACKS_DIR = REPO_ROOT / "infra" / "terraform" / "stacks"
EXPECTED_STACKS = {
    "aws-vllm", "aws-llamacpp", "gcp-vllm", "gcp-llamacpp",
    "azure-vllm", "azure-llamacpp", "azure-container-app-vllm",
    "runpod-vllm", "vast-vllm", "vsphere-vllm",
}

# Stacks that contain provider-specific compute resource blocks (aws_instance,
# google_compute_instance, azurerm_linux_virtual_machine) alongside their module
# composition. These are the stacks that own the compute lifecycle; the rest
# remain thin (module-only).
STACKS_WITH_COMPUTE_RESOURCES = {
    "aws-vllm", "aws-llamacpp", "gcp-vllm", "gcp-llamacpp",
    "azure-vllm", "azure-llamacpp",
}

# Resource types that are allowed inline in stacks because they are provider-specific
# compute primitives (Phase 4 allows thin stacks to add compute resource wrappers
# once modules are composed).
_ALLOWED_INLINE_RESOURCE_TYPES = (
    "aws_instance",
    "google_compute_instance",
    "azurerm_linux_virtual_machine",
    "azurerm_network_interface",
)

# Matches `resource "type" "name"` — captures the resource type.
_INLINE_RESOURCE_TYPE_RE = re.compile(r'resource\s+"([^"]+)"\s+', re.MULTILINE)

# module source must point at (../|../../)modules/<engine>-server, where engine
# matches the stack-name suffix (the last `-`-delimited token). Both relative
# depths appear in-tree depending on whether the stack composes a sibling module
# dir or reaches up two levels from stacks/<name>/ to modules/.
_STACK_MODULE_SOURCE_RE = re.compile(
    r'source\s*=\s*"(?:\.\./|\.\./\.\./)modules/(vllm-server|llamacpp-server)"'
)
# Stacks must be thin: no inline `resource "..."` blocks.
_INLINE_RESOURCE_RE = re.compile(r'^\s*resource\s+"', re.MULTILINE)


class TestStacksPhase4:
    def test_stacks_dir_exists(self):
        assert STACKS_DIR.is_dir(), f"expected {STACKS_DIR} to exist"

    def test_expected_stacks_present(self):
        if not STACKS_DIR.is_dir():
            pytest.skip("stacks dir not present yet")
        actual = {p.name for p in STACKS_DIR.iterdir() if p.is_dir()}
        missing = EXPECTED_STACKS - actual
        assert not missing, f"missing stack dirs: {missing}"

    @pytest.mark.parametrize("stack_name", sorted(EXPECTED_STACKS))
    def test_stack_has_required_files(self, stack_name: str):
        stack = STACKS_DIR / stack_name
        assert stack.is_dir(), f"stack dir missing: {stack}"
        for fname in REQUIRED_FILES:
            f = stack / fname
            assert f.is_file(), f"missing {fname} in stack {stack_name}"

    @pytest.mark.parametrize("stack_name", sorted(EXPECTED_STACKS))
    def test_stack_main_tf_parses_as_hcl(self, stack_name: str):
        main_tf = STACKS_DIR / stack_name / "main.tf"
        assert main_tf.is_file(), f"missing {main_tf}"
        text = main_tf.read_text()
        hcl2 = _try_import_hcl2()
        if hcl2 is not None:
            import io

            ast = hcl2.load(io.StringIO(text))
            assert isinstance(ast, dict), f"{main_tf}: hcl2 did not return a dict"
            assert len(ast) > 0, f"{main_tf}: hcl2 parsed zero top-level blocks"
        else:
            _assert_structurally_valid_hcl(text, main_tf)

    @pytest.mark.parametrize("stack_name", sorted(EXPECTED_STACKS))
    def test_stack_main_tf_uses_correct_module_source(self, stack_name: str):
        main_tf = STACKS_DIR / stack_name / "main.tf"
        assert main_tf.is_file(), f"missing {main_tf}"
        text = main_tf.read_text()

        engine_suffix = stack_name.rsplit("-", 1)[-1]
        expected_module = "vllm-server" if engine_suffix == "vllm" else "llamacpp-server"

        m = _STACK_MODULE_SOURCE_RE.search(text)
        assert m is not None, (
            f"{main_tf}: no module source matching "
            f'"../modules/(vllm-server|llamacpp-server)" found'
        )
        assert m.group(1) == expected_module, (
            f"{main_tf}: module source {m.group(1)!r} does not match "
            f"stack engine suffix {engine_suffix!r} (expected {expected_module!r})"
        )

    @pytest.mark.parametrize("stack_name", sorted(EXPECTED_STACKS))
    def test_stack_main_tf_has_no_inline_resources(self, stack_name: str):
        main_tf = STACKS_DIR / stack_name / "main.tf"
        assert main_tf.is_file(), f"missing {main_tf}"
        text = main_tf.read_text()
        resource_types = _INLINE_RESOURCE_TYPE_RE.findall(text)
        if stack_name in STACKS_WITH_COMPUTE_RESOURCES:
            for rtype in resource_types:
                assert rtype in _ALLOWED_INLINE_RESOURCE_TYPES, (
                    f"{main_tf}: disallowed inline resource type {rtype!r}. "
                    f"Allowed compute resource types in stacks: {_ALLOWED_INLINE_RESOURCE_TYPES}"
                )
        else:
            assert not resource_types, (
                f"{main_tf}: stacks must be thin — found inline resource "
                f"types {resource_types} (Phase 4 violation)"
            )

    @pytest.mark.parametrize("stack_name", sorted(EXPECTED_STACKS))
    def test_stack_no_hardcoded_credentials(self, stack_name: str):
        stack = STACKS_DIR / stack_name
        if not stack.is_dir():
            pytest.skip(f"stack {stack_name} not present yet")
        for fname in REQUIRED_FILES:
            f = stack / fname
            if not f.is_file():
                continue
            active = _strip_var_refs(_strip_comments(f.read_text()))
            for pat in _CREDENTIAL_PATTERNS:
                assert not pat.search(active), (
                    f"{f}: credential literal matched by {pat.pattern!r}"
                )


# ---------------------------------------------------------------------------
# Phase 4: every TerraformGenerator provider method emits a module-style HCL,
# never an inline `resource "..."` block.
# ---------------------------------------------------------------------------

TERRAFORM_PY = REPO_ROOT / "src" / "general_ludd" / "infra" / "terraform.py"


def _build_cfg(provider, gpu_type, deploy_type="vm"):
    from general_ludd.infra.compute import ComputeConfig

    return ComputeConfig(
        provider=provider,
        gpu_type=gpu_type,
        model_name="test-model",
        allowed_cidr="127.0.0.1/32",
        deploy_type=deploy_type,
    )


class TestProviderMethodsPhase4:
    @pytest.mark.parametrize(
        ("provider", "gpu_type", "deploy_type"),
        [
            pytest.param(
                "aws", "t4", "vm",
                id="aws",
            ),
            pytest.param(
                "gcp", "l4", "vm",
                id="gcp",
            ),
            pytest.param(
                "azure", "t4", "vm",
                id="azure-vm",
            ),
            pytest.param(
                "azure", "t4", "containerapp",
                id="azure-container-app",
            ),
            pytest.param(
                "runpod", "l4", "vm",
                id="runpod",
            ),
            pytest.param(
                "vast_ai", "a100_80", "vm",
                id="vast",
            ),
            pytest.param(
                "vmware", "a100_80", "vm",
                id="vsphere",
            ),
        ],
    )
    def test_generate_emits_module_no_inline_resource(
        self, provider, gpu_type, deploy_type
    ):
        from general_ludd.infra.compute import ComputeConfig, ComputeProvider, GPUType
        from general_ludd.infra.terraform import TerraformGenerator

        cfg = ComputeConfig(
            provider=ComputeProvider(provider),
            gpu_type=GPUType(gpu_type),
            model_name="test-model",
            allowed_cidr="127.0.0.1/32",
            deploy_type=deploy_type,
        )
        hcl = TerraformGenerator().generate(cfg)

        assert "module" in hcl, (
            f"provider={provider} deploy_type={deploy_type}: emitted HCL "
            f"contains no `module` block (Phase 4 contract)"
        )
        assert not _INLINE_RESOURCE_RE.search(hcl), (
            f"provider={provider} deploy_type={deploy_type}: emitted HCL "
            f'contains an inline resource "..." block (Phase 4 violation)'
        )


# ---------------------------------------------------------------------------
# Phase 4 exit criterion: terraform.py contains no inline resource emission.
# ---------------------------------------------------------------------------


def test_phase4_exit_no_inline_resource_in_generator():
    """Literal Phase 4 exit criterion from §7 of TERRAFORM_INFRA_STRUCTURE.md:
    the generator source must not emit inline `resource "..."` blocks.
    """
    assert TERRAFORM_PY.is_file(), f"expected {TERRAFORM_PY} to exist"
    text = TERRAFORM_PY.read_text()
    assert not _INLINE_RESOURCE_RE.search(text), (
        f"{TERRAFORM_PY}: Phase 4 exit criterion failed — found an inline "
        f'resource "..." block in the generator source'
    )


# ---------------------------------------------------------------------------
# Phase 1 real modules: gpu-cost-watchdog renders a self-termination cloud-init
# and network exposes per-provider NSG/firewall primitives. Stacks compose both.
# (TERRAFORM_INFRA_STRUCTURE.md §5.)
# ---------------------------------------------------------------------------

WATCHDOG_MAIN = MODULES_DIR / "gpu-cost-watchdog" / "main.tf"
WATCHDOG_OUTPUTS = MODULES_DIR / "gpu-cost-watchdog" / "outputs.tf"
NETWORK_MAIN = MODULES_DIR / "network" / "main.tf"
NETWORK_OUTPUTS = MODULES_DIR / "network" / "outputs.tf"


class TestGpuCostWatchdogModule:
    def test_gpu_cost_watchdog_module_has_script_template(self):
        """main.tf renders /usr/local/bin/gpu-cost-watchdog.sh via cloud-init."""
        assert WATCHDOG_MAIN.is_file(), f"missing {WATCHDOG_MAIN}"
        text = WATCHDOG_MAIN.read_text()
        assert "/usr/local/bin/gpu-cost-watchdog.sh" in text, (
            f"{WATCHDOG_MAIN}: expected /usr/local/bin/gpu-cost-watchdog.sh "
            f"write_files entry not found"
        )
        # Cloud-init write_files wrapper is present.
        assert "write_files" in text, (
            f"{WATCHDOG_MAIN}: missing cloud-init write_files block"
        )
        # The self-termination call sites are present (at least AWS).
        assert "terminate-instances" in text, (
            f"{WATCHDOG_MAIN}: missing aws ec2 terminate-instances call"
        )

    def test_gpu_cost_watchdog_outputs_user_data_fragment(self):
        """outputs.tf exposes a user_data output for stack composition."""
        assert WATCHDOG_OUTPUTS.is_file(), f"missing {WATCHDOG_OUTPUTS}"
        text = WATCHDOG_OUTPUTS.read_text()
        assert re.search(r'output\s+"user_data"', text), (
            f"{WATCHDOG_OUTPUTS}: missing `output user_data` (cloud-init fragment)"
        )
        assert re.search(r'output\s+"script_path"', text), (
            f"{WATCHDOG_OUTPUTS}: missing `output script_path`"
        )


class TestNetworkModule:
    def test_network_module_has_per_provider_switch(self):
        """main.tf gates resources on var.cloud for each supported cloud."""
        assert NETWORK_MAIN.is_file(), f"missing {NETWORK_MAIN}"
        text = NETWORK_MAIN.read_text()
        for cloud in ("aws", "gcp", "azure", "vsphere"):
            pat = re.compile(rf'var\.cloud\s*==\s*"{cloud}"')
            assert pat.search(text), (
                f"{NETWORK_MAIN}: missing `var.cloud == \"{cloud}\"` "
                f"per-provider switch branch"
            )
        # Concrete primitive for each real provider.
        assert "aws_security_group" in text
        assert "google_compute_firewall" in text
        assert "azurerm_network_security_group" in text
        # runpod/vast are documented as N/A.
        assert "runpod" in text and "vast" in text

    def test_network_module_outputs_security_group_id(self):
        """outputs.tf exposes security_group_id + security_group_arn."""
        assert NETWORK_OUTPUTS.is_file(), f"missing {NETWORK_OUTPUTS}"
        text = NETWORK_OUTPUTS.read_text()
        assert re.search(r'output\s+"security_group_id"', text), (
            f"{NETWORK_OUTPUTS}: missing security_group_id output"
        )
        assert re.search(r'output\s+"security_group_arn"', text), (
            f"{NETWORK_OUTPUTS}: missing security_group_arn output"
        )


class TestStackComposition:
    """aws-vllm + gcp-vllm stacks compose network + gpu-cost-watchdog + vllm-server."""

    @pytest.mark.parametrize(
        "stack_name",
        ["aws-vllm", "gcp-vllm"],
    )
    def test_stack_composes_network_and_watchdog(self, stack_name: str):
        main_tf = STACKS_DIR / stack_name / "main.tf"
        assert main_tf.is_file(), f"missing {main_tf}"
        text = main_tf.read_text()

        assert re.search(r'source\s*=\s*"(?:\.\./|\.\./\.\./)modules/network"', text), (
            f"{main_tf}: stack does not compose the network module"
        )
        assert re.search(r'source\s*=\s*"(?:\.\./|\.\./\.\./)modules/gpu-cost-watchdog"', text), (
            f"{main_tf}: stack does not compose the gpu-cost-watchdog module"
        )
        # Sanity: vllm-server is still composed.
        assert re.search(r'source\s*=\s*"(?:\.\./|\.\./\.\./)modules/vllm-server"', text), (
            f"{main_tf}: stack lost the vllm-server module reference"
        )

    def test_aws_stack_composes_network_and_watchdog(self):
        main_tf = STACKS_DIR / "aws-vllm" / "main.tf"
        text = main_tf.read_text()
        assert re.search(r'modules/network"', text)
        assert re.search(r'modules/gpu-cost-watchdog"', text)

    def test_gcp_stack_composes_network_and_watchdog(self):
        main_tf = STACKS_DIR / "gcp-vllm" / "main.tf"
        text = main_tf.read_text()
        assert re.search(r'modules/network"', text)
        assert re.search(r'modules/gpu-cost-watchdog"', text)
