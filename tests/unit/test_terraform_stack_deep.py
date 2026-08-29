"""Deep Terraform stack validation tests.

Validates every stack under infra/terraform/stacks/ for structural
correctness: required files, provider config, variable definitions,
output definitions, backend config, module references, and cross-stack
consistency rules.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
STACKS_DIR = REPO_ROOT / "infra" / "terraform" / "stacks"
MODULES_DIR = REPO_ROOT / "infra" / "terraform" / "modules"
VERSIONS_TF = REPO_ROOT / "infra" / "terraform" / "versions.tf"

STACK_NAMES: list[str] = sorted(d.name for d in STACKS_DIR.iterdir() if d.is_dir() and not d.name.startswith("."))

REQUIRED_FILES: tuple[str, ...] = ("main.tf", "backend.tf", "variables.tf", "outputs.tf")

PROVIDER_STACK_MAP: dict[str, str] = {
    "aws": "hashicorp/aws",
    "azure": "hashicorp/azurerm",
    "azure-container-app": "hashicorp/azurerm",
    "gcp": "hashicorp/google",
    "kubernetes": "hashicorp/kubernetes",
    "vsphere": "vmware/vsphere",
    "runpod": "runpod/runpod",
    "qemu": "dmacvicar/libvirt",
}

COMMON_VARIABLES: dict[str, dict[str, Any]] = {
    "max_cost_usd": {"type": "number"},
    "timeout_minutes": {"type": "number"},
    "region": {"type": "string"},
}

# Stacks that reference an inference server module vs deploy module
ENGINE_MODULE_MAP: dict[str, str] = {
    "aws-vllm": "../../modules/vllm-server",
    "aws-llamacpp": "../../modules/llamacpp-server",
    "gcp-vllm": "../../modules/vllm-server",
    "gcp-llamacpp": "../../modules/llamacpp-server",
    "azure-vllm": "../../modules/vllm-server",
    "azure-llamacpp": "../../modules/llamacpp-server",
    "vsphere-vllm": "../../modules/vllm-server",
    "vsphere-llamacpp": "../../modules/llamacpp-server",
    "runpod-vllm": "../../modules/vllm-server",
    "runpod-llamacpp": "../../modules/llamacpp-server",
    "qemu-vllm": "../../modules/vllm-server",
    "qemu-llamacpp": "../../modules/llamacpp-server",
    "vast-vllm": "../../modules/vllm-server",
    "vast-llamacpp": "../../modules/llamacpp-server",
    "kubernetes-vllm": "../../modules/kubernetes-deploy",
    "kubernetes-llamacpp": "../../modules/kubernetes-deploy",
    "azure-container-app-vllm": "../../modules/azure-container-app-vllm",
    "azure-container-app-llamacpp": "../../modules/llamacpp-server",
}

OUTPUT_ID_VARIANTS: tuple[str, ...] = (
    "instance_id",
    "deployment_name",
    "compute_instance_id",
)

OUTPUT_URL_VARIANTS: tuple[str, ...] = (
    "base_url",
    "inference_url",
    "service_endpoint",
)


def _stack_path(stack_name: str, *parts: str) -> Path:
    return STACKS_DIR.joinpath(stack_name, *parts)


def _read_tf(stack_name: str, file_name: str) -> str:
    path = _stack_path(stack_name, file_name)
    if not path.is_file():
        return ""
    return path.read_text()


def _read_versions_tf() -> str:
    return VERSIONS_TF.read_text()


def _parse_variable_blocks(tf_text: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for m in re.finditer(r'variable\s+"(\w+)"\s*\{(.*?)\n\}', tf_text, re.DOTALL):
        result[m.group(1)] = m.group(2)
    return result


def _parse_output_blocks(tf_text: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for m in re.finditer(r'output\s+"(\w+)"\s*\{(.*?)\n\}', tf_text, re.DOTALL):
        result[m.group(1)] = m.group(2)
    return result


def _parse_required_providers(tf_text: str) -> dict[str, tuple[str, str]]:
    result: dict[str, tuple[str, str]] = {}
    block_m = re.search(r"required_providers\s*\{(.*?)\n\s*\}", tf_text, re.DOTALL)
    if not block_m:
        return result
    for m in re.finditer(r"(\w+)\s*=\s*\{(.*?)\n\s*\}", block_m.group(1), re.DOTALL):
        alias = m.group(1)
        inner = m.group(2)
        src_m = re.search(r'source\s*=\s*"([^"]+)"', inner)
        ver_m = re.search(r'version\s*=\s*"([^"]+)"', inner)
        source = src_m.group(1) if src_m else ""
        version = ver_m.group(1) if ver_m else ""
        result[source] = (version, alias)
    return result


def _parse_canonical_providers() -> dict[str, str]:
    tf_text = _read_versions_tf()
    result: dict[str, str] = {}
    for m in re.finditer(
        r'(\w+)\s*=\s*\{[^}]*source\s*=\s*"([^"]+)"[^}]*version\s*=\s*"([^"]+)"',
        tf_text,
        re.DOTALL,
    ):
        result[m.group(2)] = m.group(3)
    return result


def _infer_provider_key(stack_name: str) -> str | None:
    for key, _source in PROVIDER_STACK_MAP.items():
        if stack_name.startswith(key):
            return key
    return None


def _parse_module_refs(tf_text: str) -> list[tuple[str, str]]:
    refs: list[tuple[str, str]] = []
    for m in re.finditer(r'module\s+"(\w+)"\s*\{[^}]*source\s*=\s*"([^"]+)"', tf_text, re.DOTALL):
        refs.append((m.group(1), m.group(2)))
    return refs


# ═══════════════════════════════════════════════════════════════════════════
# Structural tests — every stack has the required file set
# ═══════════════════════════════════════════════════════════════════════════


class TestStackFileStructure:
    @pytest.mark.parametrize("stack_name", STACK_NAMES)
    def test_stack_has_all_required_files(self, stack_name: str) -> None:
        for fname in REQUIRED_FILES:
            path = _stack_path(stack_name, fname)
            assert path.is_file(), f"{stack_name}: missing required file '{fname}'"

    @pytest.mark.parametrize("stack_name", STACK_NAMES)
    def test_main_tf_is_non_empty(self, stack_name: str) -> None:
        content = _read_tf(stack_name, "main.tf")
        assert len(content.strip()) > 0, f"{stack_name}: main.tf is empty"

    @pytest.mark.parametrize("stack_name", STACK_NAMES)
    def test_variables_tf_is_non_empty(self, stack_name: str) -> None:
        content = _read_tf(stack_name, "variables.tf")
        assert len(content.strip()) > 0, f"{stack_name}: variables.tf is empty"

    @pytest.mark.parametrize("stack_name", STACK_NAMES)
    def test_variables_tf_only_contains_variable_blocks(self, stack_name: str) -> None:
        content = _read_tf(stack_name, "variables.tf")
        lines = [L.strip() for L in content.splitlines() if L.strip() and not L.strip().startswith("#")]
        for line in lines:
            if line.startswith("variable ") or line.startswith("type ") or line.startswith("description "):
                continue
            if line.startswith("default ") or line.startswith("sensitive "):
                continue
            if line.startswith("validation ") or line.startswith("condition "):
                continue
            if line.startswith("error_message "):
                continue
            if line.startswith('"') or line.startswith("],"):
                continue
            if line in ("}", "{", "}"):
                continue
            assert line.startswith("variable "), f"{stack_name}: variables.tf has non-variable content: {line!r}"

    @pytest.mark.parametrize("stack_name", STACK_NAMES)
    def test_outputs_tf_only_contains_output_blocks(self, stack_name: str) -> None:
        content = _read_tf(stack_name, "outputs.tf")
        lines = [L.strip() for L in content.splitlines() if L.strip() and not L.strip().startswith("#")]
        for line in lines:
            if (
                line.startswith("output ")
                or line.startswith("description ")
                or line.startswith("value ")
                or line.startswith("sensitive ")
            ):
                continue
            if line.strip() in ("}", "{", "}"):
                continue
            assert line.startswith("output "), f"{stack_name}: outputs.tf has non-output content: {line!r}"


# ═══════════════════════════════════════════════════════════════════════════
# Provider configuration tests
# ═══════════════════════════════════════════════════════════════════════════


class TestProviderConfig:
    def test_canonical_versions_tf_has_all_known_providers(self) -> None:
        canonical = _parse_canonical_providers()
        for source in PROVIDER_STACK_MAP.values():
            assert source in canonical, f"versions.tf missing provider source: {source}"

    def test_canonical_versions_tf_has_required_version(self) -> None:
        content = _read_versions_tf()
        assert "required_version" in content, "versions.tf missing required_version"
        m = re.search(r'required_version\s*=\s*"([^"]+)"', content)
        assert m is not None, "versions.tf: cannot parse required_version string"

    @pytest.mark.parametrize("stack_name", STACK_NAMES)
    def test_stack_tf_has_required_version(self, stack_name: str) -> None:
        content = _read_tf(stack_name, "main.tf")
        if stack_name.startswith("vast-"):
            assert "required_version" in content, f"{stack_name}: vast-ai stack must declare required_version"
        else:
            assert "required_version" in content or "required_providers" in content, (
                f"{stack_name}: main.tf missing terraform block"
            )

    @pytest.mark.parametrize("stack_name", STACK_NAMES)
    def test_stack_required_providers_match_canonical(self, stack_name: str) -> None:
        content = _read_tf(stack_name, "main.tf")
        canonical = _parse_canonical_providers()
        stack_providers = _parse_required_providers(content)
        if not stack_providers:
            return
        for source, (version, _alias) in stack_providers.items():
            if source not in canonical:
                continue
            assert version == canonical[source], (
                f"{stack_name}: provider {source} version {version!r} does not match "
                f"canonical version {canonical[source]!r} from versions.tf"
            )

    @pytest.mark.parametrize("stack_name", STACK_NAMES)
    def test_stack_provider_has_source_and_version(self, stack_name: str) -> None:
        content = _read_tf(stack_name, "main.tf")
        providers = _parse_required_providers(content)
        if not providers:
            return
        for source, (version, _alias) in providers.items():
            assert source, f"{stack_name}: provider has empty source"
            assert version, f"{stack_name}: provider {source} missing version constraint"

    def test_vast_ai_stacks_have_no_required_providers(self) -> None:
        for sname in ("vast-vllm", "vast-llamacpp"):
            content = _read_tf(sname, "main.tf")
            providers = _parse_required_providers(content)
            assert not providers, f"{sname}: vast-ai has no official provider; should not declare one"


# ═══════════════════════════════════════════════════════════════════════════
# Variable definition tests
# ═══════════════════════════════════════════════════════════════════════════


class TestVariableDefinitions:
    @pytest.mark.parametrize("stack_name", STACK_NAMES)
    def test_all_variables_have_description(self, stack_name: str) -> None:
        vars_tf = _read_tf(stack_name, "variables.tf")
        variables = _parse_variable_blocks(vars_tf)
        for var_name, block in variables.items():
            assert "description" in block, f"{stack_name}: variable {var_name!r} missing description"

    @pytest.mark.parametrize("stack_name", STACK_NAMES)
    def test_all_variables_have_type_declaration(self, stack_name: str) -> None:
        vars_tf = _read_tf(stack_name, "variables.tf")
        variables = _parse_variable_blocks(vars_tf)
        for var_name, block in variables.items():
            assert "type" in block, f"{stack_name}: variable {var_name!r} missing type declaration"

    @pytest.mark.parametrize("stack_name", STACK_NAMES)
    def test_common_variables_present(self, stack_name: str) -> None:
        if stack_name.startswith("kubernetes-"):
            return
        vars_tf = _read_tf(stack_name, "variables.tf")
        variables = _parse_variable_blocks(vars_tf)
        for var_name in COMMON_VARIABLES:
            assert var_name in variables, f"{stack_name}: missing required variable {var_name!r}"

    @pytest.mark.parametrize("stack_name", STACK_NAMES)
    def test_no_empty_default_values_without_type(self, stack_name: str) -> None:
        vars_tf = _read_tf(stack_name, "variables.tf")
        variables = _parse_variable_blocks(vars_tf)
        for var_name, block in variables.items():
            default_m = re.search(r"default\s*=", block)
            if default_m:
                val_part = block[default_m.end() :].strip()
                if val_part == '""':
                    assert "type" in block and "string" in block, (
                        f"{stack_name}: variable {var_name!r} has empty default without string type"
                    )

    @pytest.mark.parametrize("stack_name", STACK_NAMES)
    def test_model_variable_is_required(self, stack_name: str) -> None:
        vars_tf = _read_tf(stack_name, "variables.tf")
        variables = _parse_variable_blocks(vars_tf)
        model_vars = {"model", "model_name"}
        found = model_vars & set(variables)
        if not found:
            return
        var_name = next(iter(found))
        block = variables[var_name]
        assert "default" not in block, f"{stack_name}: {var_name!r} is a required field and should not have a default"


# ═══════════════════════════════════════════════════════════════════════════
# Output definition tests
# ═══════════════════════════════════════════════════════════════════════════


class TestOutputDefinitions:
    @pytest.mark.parametrize("stack_name", STACK_NAMES)
    def test_standard_outputs_present(self, stack_name: str) -> None:
        outputs_tf = _read_tf(stack_name, "outputs.tf")
        main_tf = _read_tf(stack_name, "main.tf")
        parsed = _parse_output_blocks(outputs_tf)
        parsed.update(_parse_output_blocks(main_tf))
        id_match = set(OUTPUT_ID_VARIANTS) & set(parsed)
        url_match = set(OUTPUT_URL_VARIANTS) & set(parsed)
        assert id_match, f"{stack_name}: missing an instance-id-type output ({OUTPUT_ID_VARIANTS})"
        assert url_match, f"{stack_name}: missing a url-type output ({OUTPUT_URL_VARIANTS})"

    @pytest.mark.parametrize("stack_name", STACK_NAMES)
    def test_all_outputs_have_description(self, stack_name: str) -> None:
        outputs_tf = _read_tf(stack_name, "outputs.tf")
        parsed = _parse_output_blocks(outputs_tf)
        for out_name, block in parsed.items():
            assert "description" in block, f"{stack_name}: output {out_name!r} missing description"

    @pytest.mark.parametrize("stack_name", STACK_NAMES)
    def test_all_outputs_have_value(self, stack_name: str) -> None:
        outputs_tf = _read_tf(stack_name, "outputs.tf")
        parsed = _parse_output_blocks(outputs_tf)
        for out_name, block in parsed.items():
            assert "value" in block, f"{stack_name}: output {out_name!r} missing value"


# ═══════════════════════════════════════════════════════════════════════════
# Backend configuration tests
# ═══════════════════════════════════════════════════════════════════════════


class TestBackendConfig:
    @pytest.mark.parametrize("stack_name", STACK_NAMES)
    def test_backend_tf_has_terraform_block(self, stack_name: str) -> None:
        content = _read_tf(stack_name, "backend.tf")
        assert "terraform" in content, f"{stack_name}: backend.tf missing terraform block"

    @pytest.mark.parametrize("stack_name", STACK_NAMES)
    def test_backend_is_http_backend(self, stack_name: str) -> None:
        content = _read_tf(stack_name, "backend.tf")
        assert 'backend "http"' in content, f"{stack_name}: backend.tf must use http backend"

    @pytest.mark.parametrize("stack_name", STACK_NAMES)
    def test_backend_declares_address(self, stack_name: str) -> None:
        content = _read_tf(stack_name, "backend.tf")
        m = re.search(r'address\s*=\s*"([^"]+)"', content)
        assert m is not None, f"{stack_name}: backend.tf missing address"
        assert "localhost:8400" in m.group(1), (
            f"{stack_name}: backend address must point to gludd daemon on localhost:8400"
        )

    @pytest.mark.parametrize("stack_name", STACK_NAMES)
    def test_backend_declares_lock_and_unlock(self, stack_name: str) -> None:
        content = _read_tf(stack_name, "backend.tf")
        assert "lock_address" in content, f"{stack_name}: backend.tf missing lock_address"
        assert "unlock_address" in content, f"{stack_name}: backend.tf missing unlock_address"

    @pytest.mark.parametrize("stack_name", STACK_NAMES)
    def test_backend_address_contains_stack_name(self, stack_name: str) -> None:
        content = _read_tf(stack_name, "backend.tf")
        assert stack_name in content, f"{stack_name}: backend.tf address should reference the stack name"

    def test_all_backend_tf_files_are_parseable_blocks(self) -> None:
        for sname in STACK_NAMES:
            content = _read_tf(sname, "backend.tf")
            assert content.count("{") == content.count("}"), f"{sname}: backend.tf has unbalanced braces"


# ═══════════════════════════════════════════════════════════════════════════
# Module reference validity tests
# ═══════════════════════════════════════════════════════════════════════════


class TestModuleReferences:
    @pytest.mark.parametrize("stack_name", STACK_NAMES)
    def test_all_module_sources_map_to_existing_dirs(self, stack_name: str) -> None:
        content = _read_tf(stack_name, "main.tf")
        refs = _parse_module_refs(content)
        for mod_name, mod_source in refs:
            if not mod_source.startswith("../../modules/"):
                continue
            module_rel = mod_source[len("../../modules/") :]
            module_path = MODULES_DIR / module_rel
            assert module_path.is_dir(), (
                f"{stack_name}: module {mod_name!r} source {mod_source!r} does not resolve to a directory"
            )

    @pytest.mark.parametrize("stack_name", STACK_NAMES)
    def test_stack_references_correct_engine_module(self, stack_name: str) -> None:
        content = _read_tf(stack_name, "main.tf")
        expected = ENGINE_MODULE_MAP.get(stack_name)
        if expected is None:
            return
        assert expected in content, f"{stack_name}: must reference engine module {expected!r}"

    @pytest.mark.parametrize("stack_name", STACK_NAMES)
    def test_non_vast_stacks_have_gpu_watchdog_module(self, stack_name: str) -> None:
        content = _read_tf(stack_name, "main.tf")
        if stack_name.startswith("vast-"):
            return
        assert 'source = "../../modules/gpu-cost-watchdog"' in content, (
            f"{stack_name}: must reference gpu-cost-watchdog module"
        )


# ═══════════════════════════════════════════════════════════════════════════
# Cross-stack consistency tests
# ═══════════════════════════════════════════════════════════════════════════


class TestCrossStackConsistency:
    def test_vllm_and_llamacpp_pair_mirrored_stacks(self) -> None:
        pairs = [
            ("aws-vllm", "aws-llamacpp"),
            ("gcp-vllm", "gcp-llamacpp"),
            ("azure-vllm", "azure-llamacpp"),
            ("vsphere-vllm", "vsphere-llamacpp"),
            ("runpod-vllm", "runpod-llamacpp"),
            ("kubernetes-vllm", "kubernetes-llamacpp"),
            ("qemu-vllm", "qemu-llamacpp"),
            ("vast-vllm", "vast-llamacpp"),
            ("azure-container-app-vllm", "azure-container-app-llamacpp"),
        ]
        for vllm_name, llama_name in pairs:
            vllm_keys = set(_parse_output_blocks(_read_tf(vllm_name, "outputs.tf")))
            llama_keys = set(_parse_output_blocks(_read_tf(llama_name, "outputs.tf")))
            bonus = (
                vllm_keys
                - llama_keys
                - {
                    "instance_resource_id",
                    "resource_group_name",
                    "workload_profile_type",
                    "instance_ip",
                    "endpoint_url",
                }
            )
            assert not bonus, f"{vllm_name} has extra outputs vs {llama_name}: {bonus}"
            missing = llama_keys - vllm_keys
            assert not missing, f"{llama_name} has extra outputs vs {vllm_name}: {missing}"

    def test_no_duplicate_stack_directories(self) -> None:
        dirs = sorted(d.name for d in STACKS_DIR.iterdir() if d.is_dir())
        assert len(dirs) == len(set(dirs)), "duplicate stack directories detected"

    def test_stack_names_follow_naming_convention(self) -> None:
        for sname in STACK_NAMES:
            assert re.match(r"^[a-z][a-z0-9-]+$", sname), (
                f"{sname!r}: stack name must be lowercase letters, digits, hyphens only"
            )

    def test_every_stack_has_corresponding_backend_tf(self) -> None:
        for sname in STACK_NAMES:
            backend_tf = _stack_path(sname, "backend.tf")
            assert backend_tf.is_file(), f"{sname}: missing backend.tf"

    @pytest.mark.parametrize("stack_name", STACK_NAMES)
    def test_stack_main_tf_has_balanced_braces(self, stack_name: str) -> None:
        content = _read_tf(stack_name, "main.tf")
        assert content.count("{") == content.count("}"), f"{stack_name}: main.tf has unbalanced braces"

    @pytest.mark.parametrize("stack_name", STACK_NAMES)
    def test_stack_variables_have_balanced_braces(self, stack_name: str) -> None:
        content = _read_tf(stack_name, "variables.tf")
        assert content.count("{") == content.count("}"), f"{stack_name}: variables.tf has unbalanced braces"

    @pytest.mark.parametrize("stack_name", STACK_NAMES)
    def test_stack_outputs_have_balanced_braces(self, stack_name: str) -> None:
        content = _read_tf(stack_name, "outputs.tf")
        assert content.count("{") == content.count("}"), f"{stack_name}: outputs.tf has unbalanced braces"

    def test_versions_tf_has_balanced_braces(self) -> None:
        content = _read_versions_tf()
        assert content.count("{") == content.count("}"), "versions.tf has unbalanced braces"

    @pytest.mark.parametrize("stack_name", STACK_NAMES)
    def test_cloud_vm_stacks_have_use_spot_variable(self, stack_name: str) -> None:
        if stack_name.startswith(("kubernetes-", "azure-container-app-", "vast-", "qemu-", "runpod-", "vsphere-")):
            return
        vars_tf = _read_tf(stack_name, "variables.tf")
        variables = _parse_variable_blocks(vars_tf)
        assert "use_spot" in variables, f"{stack_name}: cloud VM stack must declare use_spot variable"
        block = variables["use_spot"]
        assert "bool" in block and "type" in block, f"{stack_name}: use_spot must be type bool"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
