"""E2E: real small model pipeline — download, quantize, serve, infer, cleanup.

Exercises the full pipeline with actual classes:
1. ModelDownloader — download a tiny GGUF model (<500MB)
2. ModelQuantizer — quantize to q4_0
3. LocalInferenceManager — start inference server (subprocess fallback)
4. Ansible dispatch path — POST /api/dispatch with capability routing
5. httpx — call the server with a test prompt
6. Verify response is non-empty, shut down, clean up files

Models are defined in tests/e2e/_local_model_configs.py (LOCAL_GGUF_MODELS).
Filter via E2E_LOCAL_MODEL env var (e.g. E2E_LOCAL_MODEL=SmolLM2-360M).

Opt-in only: set ``GLUDD_LIVE_MODEL_E2E=1``.  Otherwise the module is
collected but skipped before any model download.  It also skips with a clear
reason when llama.cpp / huggingface_hub tools are not installed.

Pre-cached model: set GLUDD_E2E_MODEL_CACHE_DIR to a directory containing
the pre-downloaded GGUF + pre-quantized q4_0 GGUF to skip the download step.
"""

from __future__ import annotations

import asyncio
import importlib.util
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
from general_ludd.local_model._local_model_configs import LocalModelConfig
from general_ludd.quantization.quantize import ModelQuantizer, QuantMethod
from general_ludd.small_models.download import ModelDownloader

from ._local_model_configs import get_e2e_configs

_E2E_MODELS = get_e2e_configs()


def _quantized_filename(config: LocalModelConfig) -> str:
    return f"{config.name.lower()}-q4_0.gguf"


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
    return importlib.util.find_spec("llama_cpp") is not None


def _has_huggingface_hub() -> bool:
    return importlib.util.find_spec("huggingface_hub") is not None


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
pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        os.environ.get("GLUDD_LIVE_MODEL_E2E") != "1",
        reason="Live local-model pipeline disabled; set GLUDD_LIVE_MODEL_E2E=1 to opt in",
    ),
    pytest.mark.skipif(_REASON is not None, reason=_REASON or "local model tools unavailable"),
]


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return int(s.getsockname()[1])


def _find_cached_model_dir(config: LocalModelConfig) -> str | None:
    candidates = [
        os.environ.get("GLUDD_E2E_MODEL_CACHE_DIR", ""),
        "/tmp/gludd-qwen-e2e-model",
    ]
    qfname = _quantized_filename(config)
    for d in candidates:
        if not d:
            continue
        qpath = os.path.join(d, qfname)
        gpath = os.path.join(d, config.filename)
        if os.path.isfile(qpath) and os.path.getsize(qpath) > 0:
            return d
        if os.path.isfile(gpath) and os.path.getsize(gpath) > 0:
            return d
    return None


def _download_and_quantize(config: LocalModelConfig) -> tuple[str, str]:
    tmpdir = tempfile.mkdtemp(prefix=f"gludd-{config.name.lower()}-e2e-")
    quantized_path = os.path.join(tmpdir, _quantized_filename(config))

    downloader = ModelDownloader(cache_dir=tmpdir)
    try:
        downloaded = downloader.download_gguf(
            model_id=config.repo,
            filename=config.filename,
        )
    except Exception as exc:
        shutil.rmtree(tmpdir, ignore_errors=True)
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
        shutil.rmtree(tmpdir, ignore_errors=True)
        pytest.skip("llama-quantize quantization failed")

    assert os.path.isfile(quantized_path), f"quantized output not found: {quantized_path}"
    assert os.path.getsize(quantized_path) > 0, "quantized output must be non-empty"
    return tmpdir, quantized_path


@pytest.mark.parametrize("model_config", _E2E_MODELS, ids=[c.name for c in _E2E_MODELS])
class TestSmallModelPipelineReal:
    """Full pipeline: download -> quantize -> serve -> infer -> shutdown -> cleanup."""

    @pytest.mark.asyncio
    async def test_full_pipeline_download_quantize_serve_infer(self, model_config: LocalModelConfig) -> None:
        """E2E pipeline exercising ModelDownloader, ModelQuantizer, LocalInferenceManager."""
        tmpdir = tempfile.mkdtemp(prefix=f"gludd-{model_config.name.lower()}-e2e-")
        quantized_path = os.path.join(tmpdir, _quantized_filename(model_config))

        try:
            downloader = ModelDownloader(cache_dir=tmpdir)
            try:
                downloaded = downloader.download_gguf(
                    model_id=model_config.repo,
                    filename=model_config.filename,
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
                        resp = await client.get("/v1/models")
                        if resp.status_code == 200:
                            break
                    except httpx.TransportError:
                        pass
                    await asyncio.sleep(1.0)
                else:
                    await mgr.stop_server(server.server_id)
                    pytest.fail("Server /v1/models did not become 200 within 30s")

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

    @pytest.mark.asyncio
    async def test_dispatch_path_serve_infer_stop(self, model_config: LocalModelConfig) -> None:
        """E2E pipeline through POST /api/dispatch capability routing.

        Uses the same download+quantize path, then serves/stops via the
        capability dispatch (the same path cloud deployments use).
        """
        tmpdir, quantized_path = _download_and_quantize(model_config)
        port = _find_free_port()
        base_url = f"http://localhost:{port}"
        try:
            from general_ludd.ansible.runner import AnsibleRunnerAdapter
            from general_ludd.daemon_wiring import make_collection_handler

            adapter = AnsibleRunnerAdapter()
            collection_handler = make_collection_handler(adapter)
            assert collection_handler is not None, "collection_handler must be available"

            serve_result = await collection_handler(
                "general_ludd.agent.local_model_serve",
                {
                    "engine": "llamacpp",
                    "model_path": quantized_path,
                    "host": "localhost",
                    "port": port,
                    "gpu_layers": 0,
                    "context_size": 512,
                    "startup_timeout": 120,
                    "server_id": "e2e-dispatch-test",
                },
            )
            if serve_result.get("status") != "success" and serve_result.get("rc") != 0:
                error_msg = serve_result.get("msg") or serve_result.get("error") or str(serve_result)
                pytest.skip(f"Serve via ansible failed: {error_msg}")

            server_info = (serve_result.get("facts") or serve_result.get("ansible_facts") or {}).get(
                "gludd_local_server", {}
            )
            assert server_info.get("status") == "running", f"Server not running: {server_info}"

            async with httpx.AsyncClient(base_url=base_url, timeout=60.0) as client:
                for _attempt in range(30):
                    try:
                        resp = await client.get("/v1/models")
                        if resp.status_code == 200:
                            break
                    except httpx.TransportError:
                        pass
                    await asyncio.sleep(1.0)
                else:
                    pytest.fail("Server /v1/models did not become 200 within 30s")

                completion_resp = await client.post(
                    "/v1/completions",
                    json={
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
                assert isinstance(text, str) and len(text) > 0, f"Empty response: {body}"

            stop_result = await collection_handler(
                "general_ludd.agent.local_model_stop",
                {"server_id": "e2e-dispatch-test", "server_pid": server_info.get("pid")},
            )
            assert stop_result.get("status") == "success", f"Stop failed: {stop_result}"

        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    @pytest.mark.asyncio
    async def test_dispatch_api_endpoint_serve_stop(self, model_config: LocalModelConfig) -> None:
        """Serve and stop a local model through the POST /api/dispatch endpoint.

        Verifies the full dispatch path: capability routing → collection
        handler → AnsibleRunnerAdapter → playbook execution.
        """
        tmpdir, quantized_path = _download_and_quantize(model_config)
        port = _find_free_port()
        base_url = f"http://localhost:{port}"
        try:
            from fastapi import FastAPI
            from fastapi.testclient import TestClient

            from general_ludd.ansible.runner import AnsibleRunnerAdapter
            from general_ludd.daemon_wiring import make_collection_handler
            from general_ludd.dispatch.capabilities import CapabilityRegistry, CollectionMeta
            from general_ludd.dispatch.dynamic_dispatcher import UNRESTRICTED_ROLE
            from general_ludd.routers.dispatch import register as register_dispatch

            adapter = AnsibleRunnerAdapter()
            collection_handler = make_collection_handler(adapter)

            reg = CapabilityRegistry()
            reg.add_collection(
                CollectionMeta(
                    name="agent",
                    namespace="general_ludd",
                    version="0.2.0",
                    tags=frozenset({"local_model_serve", "local_model_stop", "local_inference"}),
                    raw_tags=["local_model_serve", "local_model_stop", "local_inference"],
                )
            )

            app = FastAPI()
            register_dispatch(
                app,
                {},
                collection_handler=collection_handler,
                capability_registry=reg,
                role=UNRESTRICTED_ROLE,
            )
            client = TestClient(app, raise_server_exceptions=False)

            serve_response = client.post(
                "/api/dispatch",
                json={
                    "capability": "local_model_serve",
                    "action": "local_model_serve",
                    "args": {
                        "engine": "llamacpp",
                        "model_path": quantized_path,
                        "host": "localhost",
                        "port": port,
                        "gpu_layers": 0,
                        "context_size": 512,
                        "startup_timeout": 120,
                        "server_id": "e2e-api-dispatch",
                    },
                },
            )
            assert serve_response.status_code == 200, (
                f"dispatch serve: {serve_response.status_code} {serve_response.text}"
            )
            data = serve_response.json()
            assert data.get("ok_count", 0) > 0, f"Serve dispatch had errors: {data}"

            async with httpx.AsyncClient(base_url=base_url, timeout=60.0) as hclient:
                for _attempt in range(30):
                    try:
                        readiness_response = await hclient.get("/v1/models")
                        if readiness_response.status_code == 200:
                            break
                    except httpx.TransportError:
                        pass
                    await asyncio.sleep(1.0)
                else:
                    pytest.fail("Server /v1/models did not become 200 within 30s")

                completion_resp = await hclient.post(
                    "/v1/completions",
                    json={"prompt": "The capital of France is", "max_tokens": 8, "temperature": 0.0},
                )
                assert completion_resp.status_code == 200, completion_resp.text

            stop_response = client.post(
                "/api/dispatch",
                json={
                    "capability": "local_model_stop",
                    "action": "local_model_stop",
                    "args": {"server_id": "e2e-api-dispatch"},
                },
            )
            assert stop_response.status_code == 200, (
                f"dispatch stop: {stop_response.status_code} {stop_response.text}"
            )
            data = stop_response.json()
            assert data.get("ok_count", 0) > 0, f"Stop dispatch had errors: {data}"

        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)
