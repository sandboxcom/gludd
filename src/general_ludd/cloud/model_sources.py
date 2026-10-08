"""Resolve and download local-model artifacts from ordered fallback sources."""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from general_ludd.local_model._local_model_configs import LocalModelConfig

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = float(os.environ.get("GLUDD_MODEL_SOURCE_TIMEOUT", "30"))
DEFAULT_RETRIES = int(os.environ.get("GLUDD_MODEL_SOURCE_RETRIES", "1"))
HEALTH_CHECK_URL = os.environ.get(
    "GLUDD_MODEL_HEALTH_URL",
    "https://huggingface.co/api/health",
)

_GGUF_MODEL_DIR = os.environ.get(
    "GLUDD_GGUF_MODEL_DIR",
    os.path.join(os.path.expanduser("~"), ".cache", "general-ludd", "gguf"),
)


class ModelSource(StrEnum):
    """Identify a supported model artifact source."""

    HUGGINGFACE = "huggingface"
    OLLAMA = "ollama"
    DIRECT_URL = "direct_url"
    LOCAL_PATH = "local_path"
    S3_MIRROR = "s3_mirror"


class DownloadError(RuntimeError):
    """Raised when every requested model download path fails safely."""


@dataclass
class DownloadedFile:
    """Describe the local artifact produced by a successful download."""

    local_path: str
    source: ModelSource
    size_bytes: int = 0
    downloaded_at: float = field(default_factory=time.time)


ALTERNATIVE_SOURCES: dict[str, dict[ModelSource, object]] = {
    "qwen-0.5b": {
        ModelSource.HUGGINGFACE: {
            "repo": "bartowski/Qwen2.5-0.5B-Instruct-GGUF",
            "filename": "Qwen2.5-0.5B-Instruct-Q4_K_M.gguf",
        },
        ModelSource.OLLAMA: "qwen2.5:0.5b",
        ModelSource.DIRECT_URL: "https://huggingface.co/bartowski/Qwen2.5-0.5B-Instruct-GGUF/resolve/main/Qwen2.5-0.5B-Instruct-Q4_K_M.gguf",
        ModelSource.S3_MIRROR: os.environ.get(
            "GLUDD_S3_QWEN_05B_URL",
            "https://gludd-models.s3.us-east-1.amazonaws.com/Qwen2.5-0.5B-Instruct-Q4_K_M.gguf",
        ),
    },
    "tinyllama-1.1b": {
        ModelSource.HUGGINGFACE: {
            "repo": "bartowski/TinyLlama-1.1B-Chat-v1.0-GGUF",
            "filename": "TinyLlama-1.1B-Chat-v1.0-Q4_K_M.gguf",
        },
        ModelSource.OLLAMA: "tinyllama:latest",
        ModelSource.DIRECT_URL: "https://huggingface.co/bartowski/TinyLlama-1.1B-Chat-v1.0-GGUF/resolve/main/TinyLlama-1.1B-Chat-v1.0-Q4_K_M.gguf",
    },
    "smollm2-135m": {
        ModelSource.HUGGINGFACE: {
            "repo": "bartowski/SmolLM2-135M-Instruct-GGUF",
            "filename": "SmolLM2-135M-Instruct-Q4_K_M.gguf",
        },
        ModelSource.OLLAMA: "smollm2:135m",
        ModelSource.S3_MIRROR: os.environ.get(
            "GLUDD_S3_SMOLLM2_135M_URL",
            "https://gludd-models.s3.us-east-1.amazonaws.com/SmolLM2-135M-Instruct-Q4_K_M.gguf",
        ),
    },
    "smollm2-360m": {
        ModelSource.HUGGINGFACE: {
            "repo": "bartowski/SmolLM2-360M-Instruct-GGUF",
            "filename": "SmolLM2-360M-Instruct-Q4_K_M.gguf",
        },
        ModelSource.OLLAMA: "smollm2:360m",
    },
    "qwen2.5-coder-0.5b": {
        ModelSource.HUGGINGFACE: {
            "repo": "bartowski/Qwen2.5-Coder-0.5B-Instruct-GGUF",
            "filename": "Qwen2.5-Coder-0.5B-Instruct-Q4_K_M.gguf",
        },
        ModelSource.OLLAMA: "qwen2.5-coder:0.5b",
    },
    "smollm2-1.7b": {
        ModelSource.HUGGINGFACE: {
            "repo": "bartowski/SmolLM2-1.7B-Instruct-GGUF",
            "filename": "SmolLM2-1.7B-Instruct-Q4_K_M.gguf",
        },
        ModelSource.OLLAMA: "smollm2:1.7b",
    },
    "qwen2.5-coder-1.5b": {
        ModelSource.HUGGINGFACE: {
            "repo": "bartowski/Qwen2.5-Coder-1.5B-Instruct-GGUF",
            "filename": "Qwen2.5-Coder-1.5B-Instruct-Q4_K_M.gguf",
        },
        ModelSource.OLLAMA: "qwen2.5-coder:1.5b",
    },
    "qwen2.5-1.5b": {
        ModelSource.HUGGINGFACE: {
            "repo": "bartowski/Qwen2.5-1.5B-Instruct-GGUF",
            "filename": "Qwen2.5-1.5B-Instruct-Q4_K_M.gguf",
        },
        ModelSource.OLLAMA: "qwen2.5:1.5b",
    },
    "llama-3.2-1b": {
        ModelSource.HUGGINGFACE: {
            "repo": "bartowski/Llama-3.2-1B-Instruct-GGUF",
            "filename": "Llama-3.2-1B-Instruct-Q4_K_M.gguf",
        },
        ModelSource.OLLAMA: "llama3.2:1b",
    },
    "phi-3-mini-4k": {
        ModelSource.HUGGINGFACE: {
            "repo": "bartowski/Phi-3-mini-4k-instruct-GGUF",
            "filename": "Phi-3-mini-4k-instruct-Q4_K_M.gguf",
        },
        ModelSource.OLLAMA: "phi3:mini",
    },
    "phi-2": {
        ModelSource.HUGGINGFACE: {
            "repo": "bartowski/phi-2-GGUF",
            "filename": "phi-2-Q2_K.gguf",
        },
        ModelSource.OLLAMA: "phi:2.7b",
    },
    "gemma-2-2b": {
        ModelSource.HUGGINGFACE: {
            "repo": "bartowski/gemma-2-2b-it-GGUF",
            "filename": "gemma-2-2b-it-Q4_K_M.gguf",
        },
        ModelSource.OLLAMA: "gemma2:2b",
    },
    "deepseek-coder-1.3b": {
        ModelSource.HUGGINGFACE: {
            "repo": "bartowski/DeepSeek-Coder-1.3B-Instruct-GGUF",
            "filename": "DeepSeek-Coder-1.3B-Instruct-Q4_K_M.gguf",
        },
        ModelSource.OLLAMA: "deepseek-coder:1.3b",
    },
    "starcoder2-3b": {
        ModelSource.HUGGINGFACE: {
            "repo": "bartowski/StarCoder2-3B-Instruct-GGUF",
            "filename": "StarCoder2-3B-Instruct-Q4_K_M.gguf",
        },
        ModelSource.OLLAMA: "starcoder2:3b",
    },
    "codellama-7b": {
        ModelSource.HUGGINGFACE: {
            "repo": "TheBloke/CodeLlama-7B-Instruct-GGUF",
            "filename": "codellama-7b-instruct.Q4_K_M.gguf",
        },
        ModelSource.OLLAMA: "codellama:7b",
    },
    "qwen2.5-coder-3b": {
        ModelSource.HUGGINGFACE: {
            "repo": "bartowski/Qwen2.5-Coder-3B-Instruct-GGUF",
            "filename": "Qwen2.5-Coder-3B-Instruct-Q4_K_M.gguf",
        },
        ModelSource.OLLAMA: "qwen2.5-coder:3b",
    },
    "mistral-7b": {
        ModelSource.HUGGINGFACE: {
            "repo": "TheBloke/Mistral-7B-Instruct-v0.2-GGUF",
            "filename": "mistral-7b-instruct-v0.2.Q4_K_M.gguf",
        },
        ModelSource.OLLAMA: "mistral:7b",
    },
    "llama-3.2-3b": {
        ModelSource.HUGGINGFACE: {
            "repo": "bartowski/Llama-3.2-3B-Instruct-GGUF",
            "filename": "Llama-3.2-3B-Instruct-Q4_K_M.gguf",
        },
        ModelSource.OLLAMA: "llama3.2:3b",
    },
    "qwen2.5-3b": {
        ModelSource.HUGGINGFACE: {
            "repo": "bartowski/Qwen2.5-3B-Instruct-GGUF",
            "filename": "Qwen2.5-3B-Instruct-Q4_K_M.gguf",
        },
        ModelSource.OLLAMA: "qwen2.5:3b",
    },
    "phi-3.5-mini": {
        ModelSource.HUGGINGFACE: {
            "repo": "bartowski/Phi-3.5-mini-instruct-GGUF",
            "filename": "Phi-3.5-mini-instruct-Q4_K_M.gguf",
        },
        ModelSource.OLLAMA: "phi3.5:mini",
    },
    "qwen2.5-7b": {
        ModelSource.HUGGINGFACE: {
            "repo": "bartowski/Qwen2.5-7B-Instruct-GGUF",
            "filename": "Qwen2.5-7B-Instruct-Q4_K_M.gguf",
        },
        ModelSource.OLLAMA: "qwen2.5:7b",
    },
    "olmoe-1b-7b": {
        ModelSource.HUGGINGFACE: {
            "repo": "allenai/OLMoE-1B-7B-0125-Instruct-GGUF",
            "filename": "olmoe-1b-7b-0125-instruct.Q4_K_M.gguf",
        },
    },
    "internlm3-8b": {
        ModelSource.HUGGINGFACE: {
            "repo": "bartowski/internlm3-8b-instruct-GGUF",
            "filename": "internlm3-8b-instruct-Q4_K_M.gguf",
        },
        ModelSource.OLLAMA: "internlm3:8b",
    },
    "stablelm-3b": {
        ModelSource.HUGGINGFACE: {
            "repo": "bartowski/StableLM-3B-4E1T-Instruct-GGUF",
            "filename": "StableLM-3B-4E1T-Instruct-Q4_K_M.gguf",
        },
        ModelSource.OLLAMA: "stablelm:3b",
    },
}

_DEFAULT_SOURCE_ORDER: list[ModelSource] = [
    ModelSource.OLLAMA,
    ModelSource.S3_MIRROR,
    ModelSource.HUGGINGFACE,
    ModelSource.DIRECT_URL,
]


def resolve_source_chain(
    order: list[ModelSource] | None = None,
) -> list[ModelSource]:
    """Return a validated source order, falling back to the safe default."""
    chain = list(order) if order else list(_DEFAULT_SOURCE_ORDER)
    valid = [s for s in chain if s in ModelSource]
    if not valid:
        valid = list(_DEFAULT_SOURCE_ORDER)
    return valid


def _check_ollama_installed() -> bool:
    return shutil.which("ollama") is not None


def _check_url_reachable(url: str, timeout: float = 5.0) -> bool:
    try:
        import urllib.error
        import urllib.request

        req = urllib.request.Request(url, method="HEAD")
        with urllib.request.urlopen(req, timeout=timeout):
            pass
        return True
    except urllib.error.HTTPError as exc:
        exc.close()
        return False
    except Exception:
        return False


def health_check() -> bool:
    """Return whether either primary public model registry is reachable."""
    return _check_url_reachable("https://huggingface.co", timeout=5.0) or _check_url_reachable(
        "https://ollama.com", timeout=5.0
    )


def _download_from_huggingface(
    model_id: str,
    filename: str,
    cache_dir: str | None = None,
    timeout: float | None = None,
) -> DownloadedFile:
    from huggingface_hub import hf_hub_download

    timeout_val = timeout or DEFAULT_TIMEOUT
    os.environ.setdefault("HF_HUB_DOWNLOAD_TIMEOUT", str(timeout_val))

    local_path = hf_hub_download(
        repo_id=model_id,
        filename=filename,
        cache_dir=cache_dir,
    )

    size = Path(local_path).stat().st_size
    logger.info("Downloaded HF model %s/%s → %s (%.1f MB)", model_id, filename, local_path, size / 1e6)
    return DownloadedFile(local_path=str(local_path), source=ModelSource.HUGGINGFACE, size_bytes=size)


def _download_from_ollama(
    model_name: str,
) -> DownloadedFile:
    import subprocess
    import sys

    logger.info("Pulling ollama model: %s", model_name)
    try:
        result = subprocess.run(
            [sys.executable, "-m", "ollama", "pull", model_name],
            capture_output=True,
            text=True,
            timeout=300,
        )
        if result.returncode != 0 and "already exists" not in result.stderr.lower():
            raise DownloadError(f"ollama pull failed: {result.stderr.strip()}")

        dest = os.path.join(_GGUF_MODEL_DIR, f"{model_name.replace('/', '_')}.gguf")
        logger.info("Ollama model %s pulled successfully", model_name)
        return DownloadedFile(local_path=dest, source=ModelSource.OLLAMA)
    except subprocess.TimeoutExpired as exc:
        raise DownloadError(f"ollama pull timed out for {model_name}") from exc


def _download_from_direct_url(
    url: str,
    dest_dir: str | None = None,
    timeout: float | None = None,
) -> DownloadedFile:
    import urllib.error
    import urllib.parse
    import urllib.request

    filename = os.path.basename(urllib.parse.urlsplit(url).path)
    if not filename:
        raise DownloadError(f"Download URL has no filename: {url}")

    dest = Path(dest_dir or _GGUF_MODEL_DIR) / filename
    dest.parent.mkdir(parents=True, exist_ok=True)

    logger.info("Downloading from direct URL: %s → %s", url, dest)
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=dest.parent,
            prefix=f".{dest.name}.",
            suffix=".part",
            delete=False,
        ) as temp_file:
            temp_path = Path(temp_file.name)
            request = urllib.request.Request(url, method="GET")
            effective_timeout = timeout if timeout is not None else DEFAULT_TIMEOUT
            with urllib.request.urlopen(request, timeout=effective_timeout) as response:
                shutil.copyfileobj(response, temp_file)
            temp_file.flush()
            os.fsync(temp_file.fileno())
        os.replace(temp_path, dest)
    except urllib.error.HTTPError as exc:
        exc.close()
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
        raise
    except BaseException:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
        raise

    size = dest.stat().st_size
    return DownloadedFile(local_path=str(dest), source=ModelSource.DIRECT_URL, size_bytes=size)


def download_with_fallback(
    config: LocalModelConfig,
    order: list[ModelSource] | None = None,
    cache_dir: str | None = None,
    retries: int = DEFAULT_RETRIES,
    timeout: float | None = None,
) -> DownloadedFile:
    """Download a configured model from the first usable source.

    Args:
        config: Model identity and default Hugging Face artifact metadata.
        order: Optional source priority; the default favors local Ollama first.
        cache_dir: Optional destination or registry cache directory.
        retries: Additional attempts permitted for each source.
        timeout: Optional per-source timeout in seconds.

    Returns:
        Metadata for the downloaded local file.

    Raises:
        DownloadError: If every configured source and retry is exhausted.
    """
    sources = ALTERNATIVE_SOURCES.get(config.name, {}).copy()

    if not sources and not order:
        sources[ModelSource.HUGGINGFACE] = {
            "repo": config.repo,
            "filename": config.filename,
        }

    chain = resolve_source_chain(order)

    last_error: Exception | None = None

    for source in chain:
        if source not in sources:
            continue

        for attempt in range(retries + 1):
            try:
                return _try_source(
                    source=source,
                    source_config=sources[source],
                    config=config,
                    cache_dir=cache_dir,
                    timeout=timeout,
                )
            except Exception as exc:
                last_error = exc
                logger.warning(
                    "Source %s attempt %d/%d failed for %s: %s",
                    source.value,
                    attempt + 1,
                    retries + 1,
                    config.name,
                    exc,
                )
                if attempt < retries:
                    time.sleep(2**attempt)
                continue

    raise DownloadError(
        f"All sources exhausted for {config.name} "
        f"(tried: {[s.value for s in chain if s in sources]}). "
        f"Last error: {last_error}"
    )


def _try_source(
    source: ModelSource,
    source_config: object,
    config: LocalModelConfig,
    cache_dir: str | None = None,
    timeout: float | None = None,
) -> DownloadedFile:
    if source == ModelSource.OLLAMA:
        if not _check_ollama_installed():
            raise DownloadError("Ollama not installed")
        return _download_from_ollama(str(source_config))

    if source == ModelSource.HUGGINGFACE:
        if isinstance(source_config, dict):
            repo = str(source_config.get("repo", config.repo))
            filename = str(source_config.get("filename", config.filename))
        else:
            repo = config.repo
            filename = config.filename
        return _download_from_huggingface(
            model_id=repo,
            filename=filename,
            cache_dir=cache_dir,
            timeout=timeout,
        )

    if source == ModelSource.DIRECT_URL:
        return _download_from_direct_url(
            url=str(source_config),
            dest_dir=cache_dir,
            timeout=timeout,
        )

    if source == ModelSource.LOCAL_PATH:
        local = str(source_config) if isinstance(source_config, str) else config.filename
        path = Path(local)
        if not path.exists():
            raise DownloadError(f"Local model not found: {local}")
        return DownloadedFile(local_path=str(path), source=ModelSource.LOCAL_PATH, size_bytes=path.stat().st_size)

    if source == ModelSource.S3_MIRROR:
        return _download_from_direct_url(
            url=str(source_config),
            dest_dir=cache_dir,
            timeout=timeout,
        )

    raise DownloadError(f"Unknown source: {source}")
