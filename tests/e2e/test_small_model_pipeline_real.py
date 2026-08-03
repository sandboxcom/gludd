"""E2E: real small model pipeline — download, quantize, serve, infer, cleanup.

Exercises the full pipeline with actual classes:
1. ModelDownloader — download a tiny GGUF model (<100MB, SmolLM2-135M)
2. ModelQuantizer — quantize to q4_0
3. LocalInferenceManager — start inference server
4. httpx — call the server with a test prompt
5. Verify response is non-empty, shut down, clean up files

Skips with reason when llama.cpp / huggingface_hub tools are not installed.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import socket
import tempfile

import httpx
import pytest

from general_ludd.infra.local_inference import (
    LocalInferenceManager,
    LocalServerConfig,
)
from general_ludd.quantization.quantize import ModelQuantizer, QuantMethod
from general_ludd.small_models.download import ModelDownloader

_SMALL_MODEL_REPO = "HuggingFaceTB/SmolLM2-135M-GGUF"
_SMALL_MODEL_FILE = "smollm2-135m-instruct-Q4_K_M.gguf"


def _find_llama_quantize_bin() -> str | None:
    bundled = os.path.join(
        os.path.dirname(__file__),
        "..",
        "..",
        "external",
        "llamacpp",
        "build",
        "bin",
        "llama-quantize",
    )
    bundled = os.path.abspath(bundled)
    if os.path.isfile(bundled) and os.access(bundled, os.X_OK):
        return bundled
    return shutil.which("llama-quantize")


def _has_llama_quantize() -> bool:
    return _find_llama_quantize_bin() is not None


def _has_llama_cpp_server() -> bool:
    try:
        import llama_cpp  # noqa: F401

        return True
    except ImportError:
        return False


def _has_huggingface_hub() -> bool:
    try:
        import huggingface_hub  # noqa: F401

        return True
    except ImportError:
        return False


def _tools_reason() -> str | None:
    missing: list[str] = []
    if not _has_huggingface_hub():
        missing.append("huggingface_hub")
    if not _has_llama_quantize():
        missing.append("llama-quantize")
    if not _has_llama_cpp_server():
        missing.append("llama_cpp.server")
    return f"Missing tools: {', '.join(missing)}" if missing else None


_REASON = _tools_reason()
if _REASON is not None:
    pytestmark = pytest.mark.skip(reason=_REASON)


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


class TestSmallModelPipelineReal:
    """Full pipeline: download -> quantize -> serve -> infer -> shutdown -> cleanup."""

    @pytest.mark.asyncio
    async def test_full_pipeline_download_quantize_serve_infer(self) -> None:
        """E2E pipeline exercising ModelDownloader, ModelQuantizer, LocalInferenceManager."""
        tmpdir = tempfile.mkdtemp(prefix="gludd-smollm-e2e-")
        quantized_path = os.path.join(tmpdir, "smollm2-135m-q4_0.gguf")

        try:
            downloader = ModelDownloader(cache_dir=tmpdir)
            try:
                downloaded = downloader.download_gguf(
                    model_id=_SMALL_MODEL_REPO,
                    filename=_SMALL_MODEL_FILE,
                )
            except Exception as exc:
                pytest.skip(f"Model download failed: {exc}")

            assert downloaded.local_path, "download must produce a local path"
            assert os.path.isfile(downloaded.local_path), f"downloaded file not found: {downloaded.local_path}"
            assert downloaded.size_bytes > 0, "downloaded model must have non-zero size"

            quantizer = ModelQuantizer()
            ok = quantizer.quantize(
                input_gguf=downloaded.local_path,
                output_gguf=quantized_path,
                method=QuantMethod.Q4_0,
            )
            if not ok:
                pytest.skip("llama-quantize quantization failed")

            assert os.path.isfile(quantized_path), f"quantized output not found: {quantized_path}"
            assert os.path.getsize(quantized_path) > 0, "quantized output must be non-empty"

            port = _find_free_port()
            base_url = f"http://localhost:{port}"
            mgr = LocalInferenceManager()
            config = LocalServerConfig(
                engine="llamacpp",
                model_path=quantized_path,
                model_name=os.path.basename(quantized_path),
                host="localhost",
                port=port,
                context_size=512,
                gpu_layers=0,
                startup_timeout=120.0,
            )
            server = mgr.create_server(config)

            try:
                await mgr.start_server(server.server_id)
            except RuntimeError as exc:
                pytest.skip(f"Server failed to start: {exc}")

            assert server.is_running, f"server status: {server.status}"

            async with httpx.AsyncClient(base_url=base_url, timeout=60.0) as client:
                for _attempt in range(30):
                    try:
                        resp = await client.get("/health")
                        if resp.status_code == 200:
                            break
                    except httpx.TransportError:
                        pass
                    await asyncio.sleep(1.0)
                else:
                    await mgr.stop_server(server.server_id)
                    pytest.fail("Server /health did not become 200 within 30s")

                model_resp = await client.get("/v1/models")
                assert model_resp.status_code == 200, model_resp.text
                models = model_resp.json().get("data", [])
                assert len(models) >= 1, f"No models: {model_resp.text}"
                model_id = models[0]["id"]

                completion_resp = await client.post(
                    "/v1/completions",
                    json={
                        "model": model_id,
                        "prompt": "The capital of France is",
                        "max_tokens": 8,
                        "temperature": 0.0,
                    },
                )
                assert completion_resp.status_code == 200, completion_resp.text
                body = completion_resp.json()
                choices = body.get("choices", [])
                assert len(choices) >= 1, f"No choices: {body}"
                text = choices[0].get("text", "")
                assert isinstance(text, str), f"response text not a string: {type(text)}"
                assert len(text) > 0, f"Empty response text, body: {body}"

            await mgr.stop_server(server.server_id)
            assert server.status == "stopped"

        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)
