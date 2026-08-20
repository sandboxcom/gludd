"""Terraform config generation for ephemeral GPU compute."""

from __future__ import annotations

import ipaddress
import os
import shlex
import shutil
import textwrap
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from general_ludd.config.deployment_optimization import DeploymentOptimizationConfig

from general_ludd.infra.azure_accelerator import (
    effective_timeout_minutes,
    resolve_accelerator,
)
from general_ludd.infra.compute import ComputeConfig, ComputeProvider, GPUType, InferenceEngine
from general_ludd.infra.terraform_state import StateBackendSelector, render_backend_block

_AZURE_CONTAINER_APP_GPUS = {GPUType.T4, GPUType.A100_40, GPUType.A100_80}
_AZURE_CONTAINER_APP_MODULES = (
    "azure-container-app-vllm",
    "gpu-cost-watchdog",
)
_UNSPECIFIED_IPV4 = str(ipaddress.IPv4Address(0))
_LOOPBACK_IPV4 = str(ipaddress.IPv4Address("127.0.0.1"))


def _terraform_assets_root() -> Path:
    """Locate canonical Terraform assets in a source tree or installed wheel."""
    source_tree = Path(__file__).resolve().parents[3] / "infra" / "terraform"
    if source_tree.is_dir():
        return source_tree
    packaged = Path(__file__).resolve().parents[1] / "terraform"
    if packaged.is_dir():
        return packaged
    raise RuntimeError("Terraform assets are missing from this Gludd installation")


def _profile_integer(profile: dict[str, object], key: str, default: int) -> int:
    value = profile.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{key} must be an integer")
    return value


def _profile_float(profile: dict[str, object], key: str, default: float) -> float:
    value = profile.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{key} must be a number")
    result = float(value)
    if not 0 < result <= 1:
        raise ValueError(f"{key} must be between 0 and 1")
    return result


def _profile_boolean(profile: dict[str, object], key: str, default: bool) -> bool:
    value = profile.get(key, default)
    if not isinstance(value, bool):
        raise ValueError(f"{key} must be a boolean")
    return value


def _profile_string(profile: dict[str, object], key: str, default: str) -> str:
    value = profile.get(key, default)
    if not isinstance(value, str):
        raise ValueError(f"{key} must be a string")
    return value

# ---------------------------------------------------------------------------
# Security note — HCL string interpolation
# ---------------------------------------------------------------------------
# Values interpolated into the HCL templates below (model_name, container_image,
# region, engine.value, gpu_type.value) all originate from ComputeConfig fields
# that are validated by field_validators in compute.py *before* reaching this
# module.  The validators enforce strict allowlists:
#   model_name / container_image  →  ^[A-Za-z0-9._/@:-]+$
#   region                        →  ^[A-Za-z0-9-]+$
#   engine / gpu_type             →  StrEnum (fixed closed set)
# This provides defense-in-depth against HCL injection across the 19+ interpolation
# sites below.  A tfvars-based approach would eliminate the residual entirely and
# should be considered if the template surface grows further.

_AWS_GPU_TO_INSTANCE: dict[str, str] = {
    "t4": "g4dn.xlarge",
    "a10g": "g5.xlarge",
    "a100_80": "p4d.24xlarge",
    "a100_40": "p4d.24xlarge",
    "h100": "p5.48xlarge",
}

_AWS_GPU_TO_AMI_FILTER: dict[str, str] = {
    "t4": "Deep Learning AMI GPU CUDA_*",
    "a10g": "Deep Learning AMI GPU CUDA_*",
    "a100_80": "Deep Learning AMI GPU CUDA_*",
    "h100": "Deep Learning AMI GPU CUDA_*",
}

_GCP_GPU_TO_TYPE: dict[str, str] = {
    "l4": "nvidia-l4",
    "t4": "nvidia-tesla-t4",
    "a100_80": "nvidia-tesla-a100",
    "h100": "nvidia-h100-80gb",
}

_GCP_MACHINE_TYPES: dict[str, str] = {
    "l4": "g2-standard-4",
    "t4": "n1-standard-4",
    "a100_80": "a2-highgpu-1g",
    "h100": "a3-highgpu-1g",
}


def escape_tfvar_value(s: str) -> str:
    """Escape a Python string for use as a quoted tfvars/HCL string value.

    Wraps the value in double-quotes and escapes the characters that are
    significant inside an HCL string literal: backslash, double-quote,
    interpolation marker (``${``), and newline. The result is a single
    self-contained HCL string token — a stray ``"``/``}``/``${...}`` in a
    config field becomes a tfvars parse error (or a benign literal), never
    valid HCL structure.

    Phase 0 of TERRAFORM_INFRA_STRUCTURE.md §7 — defense-in-depth on top of
    the ComputeConfig field validators; the primary injection control under
    Option B is that tfvars carry values, not HCL.
    """
    # Order matters: backslash must be doubled first so we don't double the
    # backslashes introduced by the later escapes.
    escaped = s.replace("\\", "\\\\").replace('"', '\\"').replace("${", "\\${").replace("\n", "\\n")
    return f'"{escaped}"'


def _default_image(engine: InferenceEngine) -> str:
    if engine == InferenceEngine.LLAMACPP:
        return "ghcr.io/ggerganov/llama.cpp:server"
    return "vllm/vllm-openai:latest"


def _container_image(config: ComputeConfig) -> str:
    if config.container_image:
        return config.container_image
    return _default_image(config.engine)


def _inference_bind_host(config: ComputeConfig) -> str:
    """Choose loopback unless ingress policy explicitly permits remote clients."""
    cidrs = [item.strip() for item in config.allowed_cidr.split(",") if item.strip()]
    if not cidrs:
        raise ValueError("allowed_cidr must contain at least one network")
    try:
        networks = [ipaddress.ip_network(cidr, strict=False) for cidr in cidrs]
    except ValueError as exc:
        raise ValueError(f"invalid allowed_cidr network: {config.allowed_cidr!r}") from exc
    if all(network.is_loopback for network in networks):
        return _LOOPBACK_IPV4
    return _UNSPECIFIED_IPV4


def _engine_serve_cmd(config: ComputeConfig) -> str:
    """Return a shell-safe docker command string for the cloud-init script.

    Each user-supplied argument (model_name, container_image) is individually
    shlex.quote'd so that no value can break out of its argument position and
    inject shell commands.  This is the primary mitigation for the cloud-init
    RCE vector; the field_validators in compute.py are defense-in-depth.

    Workload-aware: when workload_type is set, engine-specific flags for
    tensor_parallel, max_model_len, max_num_seqs, enforce_eager, etc. are
    appended from the deployment profile.
    """
    image = _container_image(config)
    bind_host = _inference_bind_host(config)
    base_argv = [
        "docker",
        "run",
        "--gpus",
        "all",
        "-p",
        "8000:8000",
        shlex.quote(image),
    ]
    if config.engine == InferenceEngine.LLAMACPP:
        argv = [
            *base_argv,
            "-m",
            shlex.quote(config.model_name),
            "--host",
            bind_host,
            "--port",
            "8000",
        ]
        # Workload-aware flags for llama.cpp
        if config.workload_type:
            profile = config.deployment_profile or {}
            tp = profile.get("tensor_parallel")
            if tp and isinstance(tp, int) and tp > 1:
                argv.extend(["--tensor-split", str(tp)])
            ctx = profile.get("context_length")
            if ctx and isinstance(ctx, int) and ctx > 0:
                argv.extend(["--ctx-size", str(ctx)])
            batch = profile.get("batch_size")
            if batch and isinstance(batch, int):
                argv.extend(["--batch-size", str(batch)])
            threads = profile.get("threads")
            if threads and isinstance(threads, int) and threads > 0:
                argv.extend(["--threads", str(threads)])
    else:
        # vLLM — build serve command with model name first
        argv = [
            *base_argv,
            "--model",
            shlex.quote(config.model_name),
            "--host",
            bind_host,
            "--port",
            "8000",
        ]
        # Workload-aware flags for vLLM
        if config.workload_type:
            profile = config.deployment_profile or {}
            tp = profile.get("tensor_parallel")
            if tp and isinstance(tp, int) and tp > 1:
                argv.extend(["--tensor-parallel-size", str(tp)])
            ctx = profile.get("context_length")
            if ctx and isinstance(ctx, int) and ctx > 0:
                argv.extend(["--max-model-len", str(ctx)])
            seqs = profile.get("max_num_seqs")
            if seqs and isinstance(seqs, int):
                argv.extend(["--max-num-seqs", str(seqs)])
            gmu = profile.get("gpu_memory_utilization")
            if gmu and isinstance(gmu, (int, float)):
                argv.extend(["--gpu-memory-utilization", str(gmu)])
            eager = profile.get("enforce_eager")
            if eager is True:
                argv.append("--enforce-eager")
            quant = profile.get("quantization")
            if quant and isinstance(quant, str) and quant not in ("", "bf16", "fp16"):
                argv.extend(["--quantization", str(quant)])
    # Join with spaces; fixed tokens are already safe, user-supplied tokens are quoted.
    return " ".join(argv)


def _user_data_script(config: ComputeConfig) -> str:
    serve_cmd = _engine_serve_cmd(config)
    script = (
        "#!/bin/bash\n"
        "set -euxo pipefail\n"
        "\n"
        "# Pull and run inference server\n"
        f"{serve_cmd} &\n"
        "\n"
        "# Cost/TTL watchdog\n"
        f'echo "MAX_COST={config.max_cost_usd}" >> /etc/environment\n'
        f'echo "TIMEOUT_MIN={config.timeout_minutes}" >> /etc/environment\n'
    )
    if config.workload_type:
        script += f'echo "WORKLOAD_TYPE={config.workload_type}" >> /etc/environment\n'
    return script


def _override_apply(terraform_config: object | None) -> Callable[[str, object], object]:
    """Return a callable that resolves a field value with TerraformConfig override.

    If terraform_config is set and has a non-default/non-empty value for the
    given field, that value wins. Otherwise the compute default is returned.
    """

    def _resolve(key: str, compute_default: object) -> object:
        if terraform_config is None:
            return compute_default
        tcv = getattr(terraform_config, key, None)
        if tcv is None:
            return compute_default
        if isinstance(tcv, bool) and tcv is True:
            return tcv  # booleans: True is a valid override vs default True
        if isinstance(tcv, str) and tcv == "":
            return compute_default
        if isinstance(tcv, (int, float)) and tcv == 0:
            # 0 is a valid override for gpu_count=1 etc. — allow it.
            pass
        return tcv

    return _resolve


class TerraformGenerator:
    """Generate and materialize self-contained Terraform deployment roots."""

    def __init__(
        self,
        state_backend_selector: StateBackendSelector | None = None,
        deployment_optimization_config: DeploymentOptimizationConfig | None = None,
        terraform_config: object | None = None,
    ) -> None:
        """Configure optional backend selection and deployment overrides."""
        # Optional state-backend selector (design doc \u00a710 #2). When attached,
        # every generated main.tf is prepended with the appropriate
        # ``terraform { backend "..." {} }`` block. ``None`` preserves the
        # legacy local-state default (no backend block emitted).
        self._state_backend_selector = state_backend_selector
        self._deployment_optimization_config = deployment_optimization_config
        # User-configurable TerraformConfig (from general_ludd.config.user_config).
        # Fields set here act as overrides for ComputeConfig defaults in build_tfvars.
        self._terraform_config = terraform_config

    def generate(self, config: ComputeConfig) -> str:
        """Render a Terraform root for the requested compute configuration."""
        body = self._generate_body(config)
        if self._state_backend_selector is None:
            return body
        backend_cfg = self._state_backend_selector.select(config)
        return render_backend_block(backend_cfg) + "\n" + body

    def _generate_body(self, config: ComputeConfig) -> str:
        if config.provider == ComputeProvider.AZURE and config.deploy_type == "containerapp":
            return self._generate_azure_containerapp(config)
        dispatch = {
            ComputeProvider.AWS: self._generate_aws,
            ComputeProvider.GCP: self._generate_gcp,
            ComputeProvider.AZURE: self._generate_azure,
            ComputeProvider.RUNPOD: self._generate_runpod,
            ComputeProvider.VAST: self._generate_vast_ai,
            ComputeProvider.VAST_AI: self._generate_vast_ai,
            ComputeProvider.LAMBDA_LABS: self._generate_generic,
            ComputeProvider.MODAL: self._generate_generic,
            ComputeProvider.COREWEAVE: self._generate_generic,
            ComputeProvider.DIGITAL_OCEAN: self._generate_generic,
            ComputeProvider.ORACLE: self._generate_generic,
            ComputeProvider.VSPHERE: self._generate_vsphere,
            ComputeProvider.VMWARE: self._generate_vsphere,
            ComputeProvider.KUBERNETES: self._generate_kubernetes,
        }
        handler = dispatch.get(config.provider, self._generate_generic)
        return handler(config)

    # ------------------------------------------------------------------
    # tfvars emission — the Phase 1 (Option B) values channel.
    # ------------------------------------------------------------------
    # Every config-derived value that would otherwise be f-string-interpolated
    # into an HCL string literal MUST go through ``escape_tfvar_value`` here.
    # This is the structural injection control described in §4/§8 of the
    # design doc: tfvars carry values, not HCL, so a stray ``"``/``}``/``${``
    # in a config field becomes a tfvars parse error, not an arbitrary HCL
    # fragment. The inline ``_generate_*`` paths still exist for backwards
    # compatibility (Phase 4 removes them); the module-style ``_generate_vsphere``
    # path and any future module-style provider consume this method.

    def build_tfvars(self, config: ComputeConfig, hardware_preset: dict[str, object] | None = None) -> str:
        """Render a ``terraform.tfvars`` body for a ComputeConfig.

        All string values are passed through :func:`escape_tfvar_value` so the
        output is always a syntactically valid HCL tfvars file regardless of
        the characters in the config fields. Numeric values are emitted bare.

        User-configurable TerraformConfig overrides are applied on top of
        ComputeConfig defaults: a non-empty/non-default field in TerraformConfig
        wins over the corresponding ComputeConfig field.

        Workload-aware: emits workload_type, context_length, max_tokens,
        batch_size, tensor_parallel, gpu_memory_utilization, quantization,
        threads, max_num_seqs, enforce_eager, enable_prefix_caching,
        enable_chunked_prefill, and kv_cache_dtype when set.
        """
        _apply_override = _override_apply(self._terraform_config)
        _ci = _container_image(config)

        engine = config.engine.value
        prefix = f"{engine}_"
        lines: list[str] = [
            f"provider       = {escape_tfvar_value(config.provider.value)}",
            f"engine         = {escape_tfvar_value(config.engine.value)}",
            f"gpu_type       = {escape_tfvar_value(config.gpu_type.value)}",
            f"gpu_count      = {_apply_override('gpu_count', config.gpu_count)}",
            f"model_name     = {escape_tfvar_value(str(_apply_override('model_name', config.model_name)))}",
            f"container_image = {escape_tfvar_value(str(_apply_override('container_image', _ci)))}",
            f"disk_size_gb   = {_apply_override('disk_size_gb', config.disk_size_gb)}",
            f"max_cost_usd   = {escape_tfvar_value(str(_apply_override('max_cost_usd', config.max_cost_usd)))}",
            f"timeout_minutes = {escape_tfvar_value(str(_apply_override('timeout_minutes', config.timeout_minutes)))}",
            f"allowed_cidr   = {escape_tfvar_value(str(_apply_override('allowed_cidr', config.allowed_cidr)))}",
            f"extra_args      = {escape_tfvar_value(str(_apply_override('extra_args', '')))}",
            f"instance_type   = {escape_tfvar_value(str(_apply_override('instance_type', '')))}",
        ]
        region_val = _apply_override("region", config.region or "us-east-1")
        lines.append(f"region         = {escape_tfvar_value(str(region_val))}")

        # Workload-aware deployment tfvars.
        wt = config.workload_type or "batch_inference"
        lines.append(f"workload_type              = {escape_tfvar_value(wt)}")

        profile = config.deployment_profile or {}
        default_profile: dict[str, object] = {
            "context_length": 32768,
            "max_tokens": 4096,
            "batch_size": 256,
            "tensor_parallel": 0,
            "gpu_memory_utilization": 0.90,
            "quantization": "",
            "threads": 0,
            "max_num_seqs": 256,
            "enforce_eager": False,
            "enable_prefix_caching": True,
            "enable_chunked_prefill": True,
            "kv_cache_dtype": "auto",
        }
        for key, default_val in default_profile.items():
            val = profile.get(key, default_val)
            if isinstance(val, bool):
                lines.append(f"{prefix}{key} = {str(val).lower()}")
            elif isinstance(val, str):
                lines.append(f"{prefix}{key} = {escape_tfvar_value(val)}")
            else:
                lines.append(f"{prefix}{key} = {val}")

        if self._deployment_optimization_config is not None:
            d = self._deployment_optimization_config
            gpu = config.gpu_type.value
            raw_preset = d.get_preset(engine, gpu)
            if hardware_preset is not None:
                raw_preset.update(hardware_preset)
            for key, value in raw_preset.items():
                if key in ("vram_tier",):
                    continue
                tfvar_name = f"{prefix}{key}"
                if isinstance(value, bool):
                    lines.append(f"{tfvar_name} = {str(value).lower()}")
                elif isinstance(value, str):
                    lines.append(f"{tfvar_name} = {escape_tfvar_value(value)}")
                else:
                    lines.append(f"{tfvar_name} = {value}")
        # User-configurable overrides from TerraformConfig for inference feature flags.
        if self._terraform_config is not None:
            gdb = _apply_override(
                "guided_decoding_backend",
                getattr(config, "guided_decoding_backend", "outlines"),
            )
            lines.append(f"guided_decoding_backend    = {escape_tfvar_value(str(gdb))}")
            eso = _apply_override(
                "enable_structured_outputs",
                getattr(config, "enable_structured_outputs", True),
            )
            lines.append(f"enable_structured_outputs  = {str(eso).lower()}")
            grammar = _apply_override(
                "grammar_file",
                getattr(config, "grammar_file", None) or "",
            )
            if grammar:
                lines.append(f"grammar_file               = {escape_tfvar_value(str(grammar))}")
        return "\n".join(lines) + "\n"

    def build_azure_containerapp_tfvars(self, config: ComputeConfig, *, deployment_name: str) -> str:
        """Render only the declared inputs for the Azure GPU Container App stack."""
        self._validate_azure_containerapp(config)
        profile = config.deployment_profile or {}
        lines = [
            f"deployment_name = {escape_tfvar_value(deployment_name)}",
            f"region = {escape_tfvar_value(config.region or 'eastus')}",
            f"container_image = {escape_tfvar_value(_container_image(config))}",
            f"model_name = {escape_tfvar_value(config.model_name)}",
            f"gpu_type = {escape_tfvar_value(config.gpu_type.value)}",
            f"gpu_count = {config.gpu_count}",
            f"allowed_cidr = {escape_tfvar_value(config.allowed_cidr)}",
            f"max_cost_usd = {config.max_cost_usd}",
            f"timeout_minutes = {config.timeout_minutes}",
            f"vllm_context_length = {_profile_integer(profile, 'context_length', 4096)}",
            f"vllm_max_num_seqs = {_profile_integer(profile, 'max_num_seqs', 8)}",
            f"vllm_gpu_memory_utilization = {_profile_float(profile, 'gpu_memory_utilization', 0.90)}",
            f"vllm_enforce_eager = {str(_profile_boolean(profile, 'enforce_eager', False)).lower()}",
            "vllm_enable_prefix_caching = "
            f"{str(_profile_boolean(profile, 'enable_prefix_caching', True)).lower()}",
            "vllm_enable_chunked_prefill = "
            f"{str(_profile_boolean(profile, 'enable_chunked_prefill', True)).lower()}",
            "vllm_kv_cache_dtype = "
            f"{escape_tfvar_value(_profile_string(profile, 'kv_cache_dtype', 'auto'))}",
        ]
        quantization = _profile_string(profile, "quantization", "")
        lines.append(f"vllm_quantization = {escape_tfvar_value(quantization)}")
        return "\n".join(lines) + "\n"

    def materialize(
        self,
        config: ComputeConfig,
        destination: str | os.PathLike[str],
        *,
        deployment_name: str,
    ) -> Path:
        """Write an isolated, runnable Terraform root and return it."""
        destination_path = Path(destination)
        destination_path.mkdir(parents=True, exist_ok=True)
        if config.provider != ComputeProvider.AZURE:
            (destination_path / "main.tf").write_text(
                self.generate(config),
                encoding="utf-8",
            )
            return destination_path

        if not deployment_name or not all(
            char.islower() or char.isdigit() or char == "-"
            for char in deployment_name
        ):
            raise ValueError(
                "deployment_name must contain lowercase letters, digits, or '-'"
            )

        assets = _terraform_assets_root()

        if config.deploy_type == "containerapp":
            stack = assets / "stacks" / "azure-container-app-vllm"
            module_sources = {
                module_name: assets / "modules" / module_name
                for module_name in _AZURE_CONTAINER_APP_MODULES
            }
            if not stack.is_dir() or not all(
                module_source.is_dir() for module_source in module_sources.values()
            ):
                raise RuntimeError(
                    "Azure Container Apps Terraform stack assets are incomplete"
                )
            (destination_path / "main.tf").write_text(
                self.generate(config),
                encoding="utf-8",
            )
            (destination_path / "terraform.tfvars").write_text(
                self.build_azure_containerapp_tfvars(
                    config,
                    deployment_name=deployment_name,
                ),
                encoding="utf-8",
            )
            for filename in ("variables.tf", "outputs.tf"):
                shutil.copy2(stack / filename, destination_path / filename)
            for module_name, module_source in module_sources.items():
                shutil.copytree(
                    module_source,
                    destination_path / "modules" / module_name,
                    dirs_exist_ok=True,
                )
            return destination_path

        if config.deploy_type != "vm":
            raise ValueError(
                "Azure Terraform materialization supports vm or containerapp"
            )

        stack_name = (
            "azure-llamacpp"
            if config.engine == InferenceEngine.LLAMACPP
            else "azure-vllm"
        )
        modules_dir = destination_path / "modules"
        stack_dir = destination_path / "stacks" / stack_name
        shutil.copytree(assets / "modules", modules_dir, dirs_exist_ok=True)
        shutil.copytree(
            assets / "stacks" / stack_name,
            stack_dir,
            dirs_exist_ok=True,
        )

        accelerator = resolve_accelerator(config.gpu_type, config.gpu_count)
        timeout_minutes = effective_timeout_minutes(
            requested_timeout_minutes=config.timeout_minutes,
            max_cost_usd=config.max_cost_usd,
            hourly_rate_usd=config.hourly_rate_usd,
        )
        tfvars = "\n".join(
            (
                f"image               = {escape_tfvar_value(_container_image(config))}",
                f"gpus               = {config.gpu_count}",
                f"model               = {escape_tfvar_value(config.model_name)}",
                f"region              = {escape_tfvar_value(config.region or 'eastus')}",
                f"instance_type       = {escape_tfvar_value(accelerator.vm_size)}",
                f"extra_args          = {escape_tfvar_value('')}",
                f"max_cost_usd        = {config.max_cost_usd}",
                f"timeout_minutes     = {timeout_minutes}",
                f"allowed_cidr        = {escape_tfvar_value(config.allowed_cidr)}",
                f"ssh_public_key_path = {escape_tfvar_value(config.ssh_public_key_path)}",
                f"disk_size_gb        = {config.disk_size_gb}",
                f"use_spot            = {str(config.spot).lower()}",
                f"deployment_name     = {escape_tfvar_value(deployment_name)}",
            )
        )
        (stack_dir / "terraform.tfvars").write_text(
            tfvars + "\n",
            encoding="utf-8",
        )
        return stack_dir

    @staticmethod
    def _validate_azure_containerapp(config: ComputeConfig) -> None:
        if config.engine != InferenceEngine.VLLM:
            raise ValueError("Azure Container Apps GPU deployments currently require the vLLM engine")
        if config.gpu_type not in _AZURE_CONTAINER_APP_GPUS:
            supported = ", ".join(sorted(gpu.value for gpu in _AZURE_CONTAINER_APP_GPUS))
            raise ValueError(
                "Azure Container Apps serverless GPU supports only "
                f"{supported}; got {config.gpu_type.value}"
            )
        if config.gpu_count != 1:
            raise ValueError("Azure Container Apps serverless GPU requires gpu_count=1 per replica")

    def _generate_aws(self, config: ComputeConfig) -> str:
        # Phase 4 — module-style: emit a thin stack that composes
        # ./modules/<engine>-server. No inline resource blocks; all config
        # fields flow through tfvars (build_tfvars) as escaped values, never
        # interpolated into HCL structure.
        region = config.region or "us-east-1"
        return textwrap.dedent(f"""\
            terraform {{
              required_providers {{
                aws = {{
                  source  = "hashicorp/aws"
                  version = "~> 5.0"
                }}
              }}
            }}

            provider "aws" {{
              region = "{region}"
            }}

            module "vllm_server" {{
              source = "./modules/vllm-server"

              image           = var.image
              gpus            = var.gpus
              model           = var.model
              region          = var.region
              instance_type   = var.instance_type
              extra_args      = var.extra_args
              max_cost_usd    = var.max_cost_usd
              timeout_minutes = var.timeout_minutes
            }}

            output "instance_id" {{
              value = module.vllm_server.instance_id
            }}

            output "base_url" {{
              value = module.vllm_server.base_url
            }}

            # Legacy aliases — DeploymentManager.deploy() reads instance_ip /
            # endpoint_url from `terraform output -json`. Keep the reader
            # working through the Phase 4 transition without a deploy-side
            # change. Remove once deployment.py is updated to read the new
            # instance_id / base_url names directly.
            output "instance_ip" {{
              value = module.vllm_server.instance_id
            }}

            output "endpoint_url" {{
              value = module.vllm_server.base_url
            }}
        """)

    def _generate_gcp(self, config: ComputeConfig) -> str:
        # Phase 4 — module-style; provider-specific GPU→machine-type mapping
        # now lives in tfvars (build_tfvars), not in inline HCL resources.
        region = config.region or "us-central1"
        return textwrap.dedent(f"""\
            terraform {{
              required_providers {{
                google = {{
                  source  = "hashicorp/google"
                  version = "~> 5.0"
                }}
              }}
            }}

            provider "google" {{
              region = "{region}"
            }}

            module "vllm_server" {{
              source = "./modules/vllm-server"

              image           = var.image
              gpus            = var.gpus
              model           = var.model
              region          = var.region
              instance_type   = var.instance_type
              extra_args      = var.extra_args
              max_cost_usd    = var.max_cost_usd
              timeout_minutes = var.timeout_minutes
              guided_decoding_backend    = var.guided_decoding_backend
              enable_structured_outputs  = var.enable_structured_outputs
            }}

            output "instance_id" {{
              value = module.vllm_server.instance_id
            }}

            output "base_url" {{
              value = module.vllm_server.base_url
            }}

            # Legacy aliases — DeploymentManager.deploy() reads instance_ip /
            # endpoint_url from `terraform output -json`. Keep the reader
            # working through the Phase 4 transition without a deploy-side
            # change. Remove once deployment.py is updated to read the new
            # instance_id / base_url names directly.
            output "instance_ip" {{
              value = module.vllm_server.instance_id
            }}

            output "endpoint_url" {{
              value = module.vllm_server.base_url
            }}
        """)

    def _generate_azure(self, config: ComputeConfig) -> str:
        # Phase 4 — module-style; the azurerm_resource_group / virtual_network
        # / subnet / nsg / vm / nic / public_ip bodies now live behind the
        # module interface (./modules/vllm-server for vLLM). The generator
        # only composes provider + module block; values flow via tfvars.
        region = config.region or "eastus"
        return textwrap.dedent(f"""\
            terraform {{
              required_providers {{
                azurerm = {{
                  source  = "hashicorp/azurerm"
                  version = "~> 4.55"
                }}
              }}
            }}

            provider "azurerm" {{
              features {{}}
              location = "{region}"
            }}

            module "vllm_server" {{
              source = "./modules/vllm-server"

              image           = var.image
              gpus            = var.gpus
              model           = var.model
              region          = var.region
              instance_type   = var.instance_type
              extra_args      = var.extra_args
              max_cost_usd    = var.max_cost_usd
              timeout_minutes = var.timeout_minutes
              guided_decoding_backend    = var.guided_decoding_backend
              enable_structured_outputs  = var.enable_structured_outputs
            }}

            output "instance_id" {{
              value = module.vllm_server.instance_id
            }}

            output "base_url" {{
              value = module.vllm_server.base_url
            }}

            # Legacy aliases — DeploymentManager.deploy() reads instance_ip /
            # endpoint_url from `terraform output -json`. Keep the reader
            # working through the Phase 4 transition without a deploy-side
            # change. Remove once deployment.py is updated to read the new
            # instance_id / base_url names directly.
            output "instance_ip" {{
              value = module.vllm_server.instance_id
            }}

            output "endpoint_url" {{
              value = module.vllm_server.base_url
            }}
        """)

    def _generate_azure_containerapp(self, config: ComputeConfig) -> str:
        self._validate_azure_containerapp(config)
        stack_main = _terraform_assets_root() / "stacks" / "azure-container-app-vllm" / "main.tf"
        repository_hcl = stack_main.read_text(encoding="utf-8")
        for module_name in _AZURE_CONTAINER_APP_MODULES:
            repository_source = f'source = "../../modules/{module_name}"'
            if repository_source not in repository_hcl:
                raise RuntimeError(
                    f"Azure Container Apps stack is missing local module {module_name!r}"
                )
            repository_hcl = repository_hcl.replace(
                repository_source,
                f'source = "./modules/{module_name}"',
                1,
            )
        return repository_hcl

    def _generate_runpod(self, config: ComputeConfig) -> str:
        _modules = os.path.join(os.path.dirname(__file__), "..", "..", "..", "infra", "terraform", "modules")
        return textwrap.dedent(f"""\
            terraform {{
              required_providers {{
                runpod = {{
                  source  = "runpod/runpod"
                  version = "~> 1.0"
                }}
              }}
            }}

            provider "runpod" {{}}

            variable "image"          {{ default = "" }}
            variable "gpus"           {{ default = 1 }}
            variable "model"          {{ default = "" }}
            variable "region"        {{ default = "" }}
            variable "instance_type"  {{ default = "" }}
            variable "extra_args"     {{ default = "" }}
            variable "max_cost_usd"   {{ default = 5 }}
            variable "timeout_minutes"{{ default = 30 }}
            variable "guided_decoding_backend"   {{ default = "" }}
            variable "enable_structured_outputs" {{ default = false }}

            module "vllm_server" {{
              source = "{_modules}/vllm-server"

              image           = var.image
              gpus            = var.gpus
              model           = var.model
              region          = var.region
              instance_type   = var.instance_type
              extra_args      = var.extra_args
              max_cost_usd    = var.max_cost_usd
              timeout_minutes = var.timeout_minutes
              guided_decoding_backend    = var.guided_decoding_backend
              enable_structured_outputs  = var.enable_structured_outputs
            }}

            output "instance_id" {{
              value = module.vllm_server.instance_id
            }}

            output "base_url" {{
              value = module.vllm_server.base_url
            }}

            # Legacy aliases (see other providers for rationale).
            output "instance_ip" {{
              value = module.vllm_server.instance_id
            }}

            output "endpoint_url" {{
              value = module.vllm_server.base_url
            }}
        """)

    def _generate_vast_ai(self, config: ComputeConfig) -> str:
        # Phase 4 — module-style; vast-ai_instance resource body now lives
        # behind the module interface. Generator composes provider + module.
        return textwrap.dedent("""\
            terraform {
              required_providers {
                vast-ai = {
                  source  = "vast-ai/vast-ai"
                  version = "~> 1.0"
                }
              }
            }

            provider "vast-ai" {}

            module "vllm_server" {
              source = "./modules/vllm-server"

              image           = var.image
              gpus            = var.gpus
              model           = var.model
              region          = var.region
              instance_type   = var.instance_type
              extra_args      = var.extra_args
              max_cost_usd    = var.max_cost_usd
              timeout_minutes = var.timeout_minutes
              guided_decoding_backend    = var.guided_decoding_backend
              enable_structured_outputs  = var.enable_structured_outputs
            }

            output "instance_id" {
              value = module.vllm_server.instance_id
            }

            output "base_url" {
              value = module.vllm_server.base_url
            }

            # Legacy aliases (see other providers for rationale).
            output "instance_ip" {
              value = module.vllm_server.instance_id
            }

            output "endpoint_url" {
              value = module.vllm_server.base_url
            }
        """)

    def _generate_generic(self, config: ComputeConfig) -> str:
        # Phase 4 — module-style fallback for stub providers (lambda_labs,
        # modal, coreweave, digital_ocean, oracle). Same thin-stack shape as
        # the named providers: required_providers + provider block + one
        # module block. No inline resource HCL.
        provider_name = config.provider.value
        return textwrap.dedent(f"""\
            terraform {{
              required_providers {{
                {provider_name} = {{
                  source  = "{provider_name}/{provider_name}"
                  version = ">= 1.0"
                }}
              }}
            }}

            provider "{provider_name}" {{}}

            module "vllm_server" {{
              source = "./modules/vllm-server"

              image           = var.image
              gpus            = var.gpus
              model           = var.model
              region          = var.region
              instance_type   = var.instance_type
              extra_args      = var.extra_args
              max_cost_usd    = var.max_cost_usd
              timeout_minutes = var.timeout_minutes
              guided_decoding_backend    = var.guided_decoding_backend
              enable_structured_outputs  = var.enable_structured_outputs
            }}

            output "instance_id" {{
              value = module.vllm_server.instance_id
            }}

            output "base_url" {{
              value = module.vllm_server.base_url
            }}

            # Legacy aliases — DeploymentManager.deploy() reads instance_ip /
            # endpoint_url from `terraform output -json`. Keep the reader
            # working through the Phase 4 transition without a deploy-side
            # change. Remove once deployment.py is updated to read the new
            # instance_id / base_url names directly.
            output "instance_ip" {{
              value = module.vllm_server.instance_id
            }}

            output "endpoint_url" {{
              value = module.vllm_server.base_url
            }}
        """)

    def _generate_vsphere(self, config: ComputeConfig, **kwargs: str) -> str:
        image = _container_image(config)
        user_data = _user_data_script(config)
        datacenter = kwargs.get("datacenter", "DC0")
        cluster = kwargs.get("cluster", "Cluster0")
        datastore = kwargs.get("datastore", "datastore0")
        network = kwargs.get("network", "VM Network")

        return textwrap.dedent(f"""\
            terraform {{
              required_providers {{
                vsphere = {{
                      source  = "vmware/vsphere"
                      version = "~> 2.8"
                }}
              }}
            }}

            provider "vsphere" {{
              user                 = var.vsphere_user
              password             = var.vsphere_password
              vsphere_server       = var.vsphere_server
              allow_unverified_ssl = {str(not config.vsphere_verify_ssl).lower()}
            }}

            module "vllm_server" {{
              source = "../modules/vllm-server"

              datacenter       = "{datacenter}"
              cluster          = "{cluster}"
              datastore        = "{datastore}"
              network          = "{network}"
              gpu_type         = "{config.gpu_type.value}"
              gpu_count        = {config.gpu_count}
              disk_size_gb     = {config.disk_size_gb}
              engine           = "{config.engine.value}"
              model_name       = "{config.model_name}"
              container_image  = "{image}"
              max_cost_usd     = "{config.max_cost_usd}"
              timeout_minutes  = "{config.timeout_minutes}"
              guided_decoding_backend    = "{config.guided_decoding_backend}"
              enable_structured_outputs  = {str(config.enable_structured_outputs).lower()}
              user_data_script = <<-EOT
            {user_data}
              EOT
              allowed_cidr     = "{config.allowed_cidr}"
            }}

            output "instance_ip" {{
              value = module.vllm_server.instance_ip
            }}

            output "endpoint_url" {{
              value = module.vllm_server.endpoint_url
            }}
        """)

    def _generate_kubernetes(self, config: ComputeConfig) -> str:
        image = _container_image(config)
        engine = escape_tfvar_value(config.engine.value)
        model_name = escape_tfvar_value(config.model_name)

        return textwrap.dedent(f"""\
            terraform {{
              required_providers {{
                kubernetes = {{
                  source  = "hashicorp/kubernetes"
                  version = "~> 2.30"
                }}
              }}
            }}

            provider "kubernetes" {{}}

            module "inference_server" {{
              source = "../../infra/terraform/modules/kubernetes-deploy"

              image        = {escape_tfvar_value(image)}
              model_name   = {model_name}
              engine       = {engine}
              gpu_count    = {config.gpu_count}
              replicas     = 1
              service_port = 8000
            }}

            output "instance_ip" {{
              value = module.inference_server.service_endpoint
            }}

            output "endpoint_url" {{
              value = "http://${{module.inference_server.service_endpoint}}/v1"
            }}
        """)
