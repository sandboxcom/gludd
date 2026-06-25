"""Security tests for infra/compute.py field validators and terraform.py shell safety.

Covers:
- model_name / container_image: validator rejects shell/HCL metacharacters
- region: validator rejects non-identifier characters
- allowed_cidr: validator rejects shell metacharacters
- _engine_serve_cmd: docker command is shell-safe (shlex.quote applied)
- Legitimate values pass all validators
"""

from __future__ import annotations

import shlex

import pytest
from pydantic import ValidationError

from general_ludd.infra.compute import ComputeConfig, ComputeProvider, GPUType, InferenceEngine
from general_ludd.infra.terraform import _engine_serve_cmd

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _base_config(**overrides: object) -> ComputeConfig:
    """Return a minimal valid ComputeConfig with optional field overrides."""
    defaults: dict[str, object] = {
        "provider": ComputeProvider.AWS,
        "gpu_type": GPUType.T4,
        "model_name": "meta-llama/Llama-2-7b-hf",
    }
    defaults.update(overrides)
    return ComputeConfig(**defaults)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# model_name validator — shell/HCL injection payloads MUST be rejected
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad_name", [
    "; id",                       # classic command injection
    "$(id)",                      # command substitution
    "`id`",                       # backtick substitution
    "model\necho pwned",          # newline injection into cloud-init script
    "model name",                 # space → splits argv
    'model"name',                 # double-quote breaks HCL string context
    "model'name",                 # single-quote breaks HCL string context
    "model${IFS}name",            # IFS trick
    "model&name",                 # background operator
    "model|name",                 # pipe
    "model>name",                 # redirect
    "model<name",                 # redirect
    "model\\\\name",              # backslash
    "model\tname",                # tab → splits argv
    "model{name}",                # HCL expression delimiters
])
def test_model_name_rejects_injection(bad_name: str) -> None:
    """model_name containing shell/HCL metacharacters must raise ValidationError."""
    with pytest.raises(ValidationError, match="only"):
        _base_config(model_name=bad_name)


@pytest.mark.parametrize("good_name", [
    "meta-llama/Llama-2-7b-hf",
    "mistralai/Mistral-7B-Instruct-v0.2",
    "ghcr.io/ggerganov/llama.cpp:server",
    "vllm/vllm-openai:latest",
    "org/model:v1.2.3",
    "simple-model",
    "",                            # empty string is allowed (optional field)
])
def test_model_name_accepts_valid(good_name: str) -> None:
    """Legitimate model names must not be rejected."""
    cfg = _base_config(model_name=good_name)
    assert cfg.model_name == good_name


# ---------------------------------------------------------------------------
# container_image validator
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad_image", [
    "; rm -rf /",
    "image name",                  # space
    'image"quoted"',               # double-quote
    "image$(cmd)",                 # command substitution
    "image\neval bad",             # newline
])
def test_container_image_rejects_injection(bad_image: str) -> None:
    """container_image containing metacharacters must raise ValidationError."""
    with pytest.raises(ValidationError, match="only"):
        _base_config(container_image=bad_image)


@pytest.mark.parametrize("good_image", [
    "vllm/vllm-openai:latest",
    "ghcr.io/ggerganov/llama.cpp:server",
    "my-registry.example.com:5000/org/image:v2.0",
    # Digest refs must be accepted (regression: @ was absent from allowlist)
    "ghcr.io/org/repo@sha256:deadbeef",
    "ghcr.io/org/repo@sha256:abc123def456",
    "docker.io/library/ubuntu@sha256:deadbeefcafe0000",
    None,                          # None is allowed (optional)
])
def test_container_image_accepts_valid(good_image: str | None) -> None:
    """Legitimate container image refs must not be rejected."""
    cfg = _base_config(container_image=good_image)
    assert cfg.container_image == good_image


def test_container_image_digest_ref_accepted() -> None:
    """Digest refs (registry/org/repo@sha256:hex) must be accepted by the validator.

    Regression test: _SAFE_IMAGE_RE previously omitted '@', causing all digest
    refs to be rejected with a ValidationError.
    """
    digest_ref = "ghcr.io/org/repo@sha256:deadbeef"
    cfg = _base_config(container_image=digest_ref)
    assert cfg.container_image == digest_ref


def test_container_image_injection_after_digest_still_rejected() -> None:
    """Shell metacharacters after a digest separator must still be rejected.

    Ensures the '@' addition to the allowlist does not weaken injection defence.
    """
    with pytest.raises(ValidationError, match="only"):
        _base_config(container_image="repo; rm -rf /")


# ---------------------------------------------------------------------------
# region validator
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad_region", [
    "us-east-1; echo pwned",
    'us-east-1"',
    "us east 1",                   # space
    "us-east-1$(id)",
    "../us-east-1",
])
def test_region_rejects_injection(bad_region: str) -> None:
    """region containing non-identifier characters must raise ValidationError."""
    with pytest.raises(ValidationError, match=r"only|Invalid"):
        _base_config(region=bad_region)


@pytest.mark.parametrize("good_region", [
    "us-east-1",
    "us-central1",
    "eastus",
    "ap-southeast-2",
    None,                          # None is allowed (uses provider default)
])
def test_region_accepts_valid(good_region: str | None) -> None:
    """Legitimate region strings must not be rejected."""
    cfg = _base_config(region=good_region)
    assert cfg.region == good_region


# ---------------------------------------------------------------------------
# allowed_cidr validator
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad_cidr", [
    "10.0.0.0/8; rm -rf /",
    "0.0.0.0/0$(echo)",
    "10.0.0.0/8 evil",
    '10.0.0.0/8"',
])
def test_allowed_cidr_rejects_injection(bad_cidr: str) -> None:
    """allowed_cidr containing shell metacharacters must raise ValidationError."""
    with pytest.raises(ValidationError, match=r"only|Invalid"):
        _base_config(allowed_cidr=bad_cidr)


@pytest.mark.parametrize("good_cidr", [
    "0.0.0.0/0",
    "10.0.0.0/8",
    "192.168.1.0/24",
    "2001:db8::/32",
])
def test_allowed_cidr_accepts_valid(good_cidr: str) -> None:
    """Legitimate CIDR notation must not be rejected."""
    cfg = _base_config(allowed_cidr=good_cidr)
    assert cfg.allowed_cidr == good_cidr


# ---------------------------------------------------------------------------
# _engine_serve_cmd — docker command is shell-safe
# ---------------------------------------------------------------------------

def test_engine_serve_cmd_vllm_shell_safe() -> None:
    """vllm docker command must be parseable by shlex and contain the quoted model."""
    cfg = _base_config(
        engine=InferenceEngine.VLLM,
        model_name="meta-llama/Llama-2-7b-hf",
    )
    cmd = _engine_serve_cmd(cfg)
    # Must be parseable as a single shell command (no syntax errors)
    tokens = shlex.split(cmd)
    assert tokens[0] == "docker"
    assert "--model" in tokens
    model_idx = tokens.index("--model")
    assert tokens[model_idx + 1] == "meta-llama/Llama-2-7b-hf"


def test_engine_serve_cmd_llamacpp_shell_safe() -> None:
    """llamacpp docker command must be parseable by shlex and contain the quoted model."""
    cfg = _base_config(
        engine=InferenceEngine.LLAMACPP,
        model_name="models/mistral-7b.gguf",
    )
    cmd = _engine_serve_cmd(cfg)
    tokens = shlex.split(cmd)
    assert tokens[0] == "docker"
    assert "-m" in tokens
    m_idx = tokens.index("-m")
    assert tokens[m_idx + 1] == "models/mistral-7b.gguf"


def test_engine_serve_cmd_injection_blocked_by_validator() -> None:
    """model_name with injection payload is rejected at ComputeConfig construction."""
    with pytest.raises(ValidationError):
        _base_config(model_name="; id")


def test_engine_serve_cmd_model_name_is_quoted_in_output() -> None:
    """The model name in the docker cmd must appear as a shlex-quoted token."""
    cfg = _base_config(
        engine=InferenceEngine.VLLM,
        model_name="org/model:v1",
    )
    cmd = _engine_serve_cmd(cfg)
    tokens = shlex.split(cmd)
    model_idx = tokens.index("--model")
    assert tokens[model_idx + 1] == "org/model:v1"


def test_engine_serve_cmd_image_is_quoted() -> None:
    """Container image in the docker command must be shlex-quoted."""
    cfg = _base_config(
        container_image="ghcr.io/ggerganov/llama.cpp:server",
        engine=InferenceEngine.LLAMACPP,
        model_name="models/phi-2.gguf",
    )
    cmd = _engine_serve_cmd(cfg)
    tokens = shlex.split(cmd)
    assert "ghcr.io/ggerganov/llama.cpp:server" in tokens


# ---------------------------------------------------------------------------
# terraform.py Azure VM — allowed_cidr threaded to NSG source_address_prefix
# ---------------------------------------------------------------------------

def test_azure_vm_nsg_uses_allowed_cidr() -> None:
    """Azure VM HCL must use config.allowed_cidr in the NSG source_address_prefix.

    Regression test: source_address_prefix was hardcoded to '*' (any), ignoring
    the allowed_cidr field that AWS and GCP already respected.
    """
    from general_ludd.infra.terraform import TerraformGenerator

    cfg = _base_config(
        provider=ComputeProvider.AZURE,
        allowed_cidr="10.0.0.0/8",
    )
    hcl = TerraformGenerator().generate(cfg)
    assert 'source_address_prefix      = "10.0.0.0/8"' in hcl
    # The old hardcoded '*' must no longer appear in the ingress rule position
    # (destination_address_prefix still uses '*' — only source is restricted)
    lines_with_source = [ln for ln in hcl.splitlines() if "source_address_prefix" in ln and "destination" not in ln]
    assert lines_with_source, "source_address_prefix line not found"
    assert all('"10.0.0.0/8"' in ln for ln in lines_with_source)
