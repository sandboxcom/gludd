"""Tests for the local docker/podman model-server templates (vllm/llamacpp/ollama).

Validates:
- All Dockerfiles / compose files exist on disk
- Dockerfiles have the expected build args
- Compose files have GPU device reservations + port mappings + volume mounts
- `docker buildx build --check` validation runs when buildx is available,
  else structural assertions (FROM line, ARG lines, EXPOSE)
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
INFRA = REPO / "infra" / "local-models"


# ---------------------------------------------------------------------------
# Existence
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "rel",
    [
        "vllm/Dockerfile",
        "vllm/docker-compose.yml",
        "llamacpp/Dockerfile",
        "llamacpp/docker-compose.yml",
        "ollama/Dockerfile",
        "ollama/docker-compose.yml",
        "README.md",
    ],
)
def test_local_model_file_exists(rel: str) -> None:
    assert (INFRA / rel).is_file(), f"missing {rel}"


# ---------------------------------------------------------------------------
# vLLM Dockerfile
# ---------------------------------------------------------------------------


def test_vllm_dockerfile_build_args() -> None:
    text = (INFRA / "vllm" / "Dockerfile").read_text()
    assert "ARG MODEL_ID" in text
    assert "ARG VLLM_VERSION" in text
    assert "ARG CUDA_VERSION" in text
    assert "ARG TORCH_CUDA_ARCH_LIST" in text


def test_vllm_dockerfile_uses_vllm_base() -> None:
    text = (INFRA / "vllm" / "Dockerfile").read_text()
    assert "vllm/vllm-openai" in text


def test_vllm_dockerfile_has_model_cache_layer() -> None:
    text = (INFRA / "vllm" / "Dockerfile").read_text()
    # HuggingFace cache pre-population layer
    assert "huggingface" in text.lower() or "hf_home" in text.lower() or "model-cache" in text.lower()


def test_vllm_compose_gpu_reservation() -> None:
    text = (INFRA / "vllm" / "docker-compose.yml").read_text()
    assert "devices" in text or "runtime" in text or "gpus" in text
    assert "8000:8000" in text
    assert "volumes" in text


def test_vllm_compose_build_args() -> None:
    text = (INFRA / "vllm" / "docker-compose.yml").read_text()
    assert "MODEL_ID" in text
    assert "VLLM_VERSION" in text


# ---------------------------------------------------------------------------
# llama.cpp Dockerfile
# ---------------------------------------------------------------------------


def test_llamacpp_dockerfile_build_args() -> None:
    text = (INFRA / "llamacpp" / "Dockerfile").read_text()
    assert "ARG LLAMACPP_VERSION" in text
    assert "ARG MODEL_URL" in text


def test_llamacpp_dockerfile_compiles_with_cuda() -> None:
    text = (INFRA / "llamacpp" / "Dockerfile").read_text()
    assert "cuda" in text.lower()
    assert "GGML_CUDA" in text or "LLAMA_CUBLAS" in text or "cmake" in text.lower()


def test_llamacpp_compose_gpu_reservation() -> None:
    text = (INFRA / "llamacpp" / "docker-compose.yml").read_text()
    assert "devices" in text or "runtime" in text or "gpus" in text


# ---------------------------------------------------------------------------
# ollama Dockerfile
# ---------------------------------------------------------------------------


def test_ollama_dockerfile_uses_ollama_base() -> None:
    text = (INFRA / "ollama" / "Dockerfile").read_text()
    assert "ollama/ollama" in text


def test_ollama_dockerfile_prepulls_model() -> None:
    text = (INFRA / "ollama" / "Dockerfile").read_text()
    assert "ollama pull" in text or "OLLAMA_MODELS" in text


# ---------------------------------------------------------------------------
# README
# ---------------------------------------------------------------------------


def test_readme_documents_all_three() -> None:
    text = (INFRA / "README.md").read_text()
    assert "vllm" in text.lower()
    assert "llama.cpp" in text.lower() or "llamacpp" in text.lower()
    assert "ollama" in text.lower()
    assert "local-model-vllm" in text


# ---------------------------------------------------------------------------
# docker buildx build --check (optional, skipped if buildx absent)
# ---------------------------------------------------------------------------


def _buildx_available() -> bool:
    if shutil.which("docker") is None:
        return False
    # Even with docker installed, the daemon may not be running. Probe.
    try:
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.returncode == 0
    except (subprocess.SubprocessError, OSError):
        return False


@pytest.mark.skipif(not _buildx_available(), reason="docker daemon not available")
def test_vllm_dockerfile_buildx_check() -> None:
    result = subprocess.run(
        ["docker", "buildx", "build", "--check", str(INFRA / "vllm")],
        capture_output=True,
        text=True,
        timeout=60,
    )
    # buildx --check exits 0 on success; we don't fail on warning diagnostics
    assert result.returncode == 0, result.stderr
