"""Core compute models for ephemeral GPU instances."""

from __future__ import annotations

import enum
import re
from datetime import UTC, datetime

from pydantic import BaseModel, Field, field_validator

# Allowed charset for model_name and container_image.
# Covers registry paths (ghcr.io/org/repo:tag), HuggingFace slugs (org/model),
# digest refs (ghcr.io/org/repo@sha256:abc…), and local image refs.
# Rejects shell/HCL metacharacters.
_SAFE_IMAGE_RE = re.compile(r"^[A-Za-z0-9._/@:-]+$")

# AWS/GCP/Azure region identifiers only ever contain letters, digits, and hyphens.
_SAFE_REGION_RE = re.compile(r"^[A-Za-z0-9-]+$")


class ComputeProvider(enum.StrEnum):
    AWS = "aws"
    AZURE = "azure"
    GCP = "gcp"
    QEMU = "qemu"
    RUNPOD = "runpod"
    VAST = "vast"
    VAST_AI = "vast_ai"
    LAMBDA_LABS = "lambda_labs"
    MODAL = "modal"
    COREWEAVE = "coreweave"
    DIGITAL_OCEAN = "digital_ocean"
    ORACLE = "oracle"
    VSPHERE = "vsphere"
    VMWARE = "vmware"
    KUBERNETES = "kubernetes"
    TOGETHER_AI = "together_ai"
    FIREWORKS_AI = "fireworks_ai"
    HUGGINGFACE = "huggingface"
    REPLICATE = "replicate"


class GPUType(enum.StrEnum):
    T4 = "t4"
    A10G = "a10g"
    L4 = "l4"
    A10 = "a10"
    RTX_4090 = "rtx_4090"
    RTX_6000_ADA = "rtx_6000_ada"
    A40 = "a40"
    L40S = "l40s"
    AMD_MI250 = "amd_mi250"
    A100_40 = "a100_40"
    A100_80 = "a100_80"
    H100 = "h100"
    H200 = "h200"


class InferenceEngine(enum.StrEnum):
    LLAMACPP = "llamacpp"
    VLLM = "vllm"


class ComputeConfig(BaseModel):
    provider: ComputeProvider
    gpu_type: GPUType
    gpu_count: int = 1
    engine: InferenceEngine = InferenceEngine.VLLM
    model_name: str = ""
    region: str | None = None
    spot: bool = True
    max_cost_usd: float = 10.0
    timeout_minutes: float = 60.0
    disk_size_gb: int = 100
    container_image: str | None = None
    api_key_alias: str | None = None
    deploy_type: str = "vm"
    # Azure VM login is key-only. Terraform expands this path locally; the
    # private key is never read or copied into state.
    ssh_public_key_path: str = "~/.ssh/id_ed25519.pub"
    # Optional price-sheet rate. When provided, the deployment TTL is shortened
    # so max_cost_usd remains a hard upper bound.
    hourly_rate_usd: float | None = None
    # Secure default: the inference endpoint (:8000) is UNAUTHENTICATED, so the
    # ingress CIDR defaults to loopback-only ("closed — configure me") rather than
    # the whole internet. Set allowed_cidr="0.0.0.0/0" explicitly for a public
    # endpoint. Interpolated into AWS/GCP/Azure ingress rules in terraform.py.
    allowed_cidr: str = "127.0.0.1/32"
    provider_auth_aliases: dict[str, str] | None = None
    guided_decoding_backend: str = "outlines"
    enable_structured_outputs: bool = True
    grammar_file: str | None = None
    # Secure default: TLS certificate verification is ON. This maps INVERTED
    # to the Terraform vsphere provider's `allow_unverified_ssl` argument
    # (verify_ssl=True -> allow_unverified_ssl=false). Set to False only to
    # opt in to unverified TLS against a vSphere endpoint with a self-signed
    # or otherwise untrusted certificate.
    vsphere_verify_ssl: bool = True
    # Workload-aware deployment: selects the serving profile (batch, realtime,
    # fine-tuning, speculative, embedding). Empty string = no workload override.
    workload_type: str = ""
    # Optional explicit overrides for the deployment optimizer knobs.
    # Keys: context_length, max_tokens, batch_size, tensor_parallel,
    # gpu_memory_utilization, quantization, threads, max_num_seqs,
    # enforce_eager, enable_prefix_caching, enable_chunked_prefill, kv_cache_dtype.
    deployment_profile: dict[str, object] | None = None

    @field_validator("workload_type")
    @classmethod
    def _validate_workload_type(cls, v: str) -> str:
        if v == "":
            return v
        allowed = {"batch_inference", "realtime_api", "fine_tuning",
                   "speculative_decoding", "embedding_generation"}
        if v not in allowed:
            raise ValueError(
                f"workload_type must be one of {allowed!r} or empty string, got {v!r}"
            )
        return v

    @field_validator("gpu_count")
    @classmethod
    def _gpu_count_min(cls, v: int) -> int:
        if v < 1:
            raise ValueError("gpu_count must be at least 1")
        return v

    @field_validator("max_cost_usd")
    @classmethod
    def _cost_positive(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("max_cost_usd must be positive")
        return v

    @field_validator("hourly_rate_usd")
    @classmethod
    def _hourly_rate_positive(cls, v: float | None) -> float | None:
        if v is not None and v <= 0:
            raise ValueError("hourly_rate_usd must be positive")
        return v

    @field_validator("timeout_minutes")
    @classmethod
    def _timeout_positive(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("timeout_minutes must be positive")
        return v

    @field_validator("disk_size_gb")
    @classmethod
    def _disk_min(cls, v: int) -> int:
        if v < 1:
            raise ValueError("disk_size_gb must be at least 1")
        return v

    @field_validator("model_name", "container_image")
    @classmethod
    def _safe_image_chars(cls, v: str | None) -> str | None:
        r"""Reject shell/HCL metacharacters in model_name and container_image.

        These values are interpolated into cloud-init scripts (run as root) and
        HCL template strings.  An attacker who controls either field can achieve
        host RCE.  The allowlist ``^[A-Za-z0-9._/@:-]+$`` covers every legitimate
        registry reference (including digest refs like ``ghcr.io/org/repo@sha256:…``)
        and HuggingFace slug while blocking all shell/HCL metacharacters
        (spaces, quotes, ``$``, ``\``, ``{``, ``}``, ``|``, etc.).
        """
        if v is None or v == "":
            return v
        if not _SAFE_IMAGE_RE.match(v):
            raise ValueError(
                f"Invalid characters in field value {v!r}: only "
                r"[A-Za-z0-9._/:-] are allowed (no shell/HCL metacharacters)"
            )
        return v

    @field_validator("region")
    @classmethod
    def _safe_region_chars(cls, v: str | None) -> str | None:
        """Reject non-identifier characters in region.

        Region is interpolated into HCL ``provider`` blocks.  Legitimate AWS,
        GCP, and Azure region names only contain letters, digits, and hyphens.
        """
        if v is None:
            return v
        if not _SAFE_REGION_RE.match(v):
            raise ValueError(
                f"Invalid characters in region {v!r}: only "
                "[A-Za-z0-9-] are allowed"
            )
        return v

    @field_validator("allowed_cidr")
    @classmethod
    def _safe_cidr(cls, v: str) -> str:
        """Validate CIDR notation. Accepts IPv4/IPv6 CIDRs (no shell metacharacters)."""
        if not re.match(r"^[0-9a-fA-F.:,/]+$", v):
            raise ValueError(
                f"Invalid characters in allowed_cidr {v!r}: only "
                "[0-9a-fA-F.:,/] are allowed"
            )
        return v

    @field_validator("ssh_public_key_path")
    @classmethod
    def _safe_ssh_public_key_path(cls, v: str) -> str:
        if not v or not re.match(r"^[A-Za-z0-9._~/-]+$", v):
            raise ValueError("ssh_public_key_path contains invalid characters")
        if ".." in v.split("/"):
            raise ValueError("ssh_public_key_path must not contain '..'")
        return v

    @field_validator("guided_decoding_backend", mode="before")
    @classmethod
    def _validate_guided_backend(cls, v: object) -> str:
        if not isinstance(v, str):
            return "outlines"
        v = v.strip()
        allowed = {"outlines", "xgrammar", "lm-format-enforcer", ""}
        if v not in allowed:
            raise ValueError(
                f"guided_decoding_backend must be one of {allowed!r}, got {v!r}"
            )
        return v

    @field_validator("grammar_file")
    @classmethod
    def _validate_grammar_file(cls, v: str | None) -> str | None:
        if v is None or v == "":
            return None
        v = v.strip()
        if not re.match(r"^[A-Za-z0-9._/@:-]+$", v):
            raise ValueError(
                f"Invalid characters in grammar_file {v!r}"
            )
        return v


class ComputeInstance(BaseModel):
    instance_id: str
    provider: ComputeProvider
    status: str = "pending"
    ip_address: str | None = None
    port: int = 8000
    gpu_type: GPUType
    endpoint_url: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    cost_incurred: float = 0.0

    @field_validator("instance_id", mode="before")
    @classmethod
    def _strip_and_require(cls, v: str) -> str:
        if isinstance(v, str):
            v = v.strip()
        if not v:
            raise ValueError("instance_id must not be empty")
        return v

    @field_validator("port")
    @classmethod
    def _port_range(cls, v: int) -> int:
        if not (1 <= v <= 65535):
            raise ValueError("port must be between 1 and 65535")
        return v

    @field_validator("cost_incurred")
    @classmethod
    def _cost_non_negative(cls, v: float) -> float:
        if v < 0:
            raise ValueError("cost_incurred must be non-negative")
        return v
