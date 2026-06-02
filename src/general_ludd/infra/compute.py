"""Core compute models for ephemeral GPU instances."""

from __future__ import annotations

import enum
from datetime import UTC, datetime

from pydantic import BaseModel, Field


class ComputeProvider(enum.StrEnum):
    AWS = "aws"
    AZURE = "azure"
    GCP = "gcp"
    RUNPOD = "runpod"
    VAST_AI = "vast_ai"
    LAMBDA_LABS = "lambda_labs"
    MODAL = "modal"
    COREWEAVE = "coreweave"
    DIGITAL_OCEAN = "digital_ocean"
    ORACLE = "oracle"


class GPUType(enum.StrEnum):
    T4 = "t4"
    A10G = "a10g"
    L4 = "l4"
    A10 = "a10"
    RTX_4090 = "rtx_4090"
    RTX_6000_ADA = "rtx_6000_ada"
    A40 = "a40"
    L40S = "l40s"
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
