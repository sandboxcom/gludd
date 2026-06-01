"""Integration tests for local inference with small HuggingFace models.

Tests the local inference manager (llama.cpp/vllm) lifecycle.
These tests use mock processes since we can't install inference engines in CI.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from general_ludd.infra.local_inference import (
    LocalInferenceManager,
    LocalServerConfig,
)


class TestLocalInferenceLifecycle:
    @pytest.mark.asyncio
    async def test_start_llamacpp_server(self):
        manager = LocalInferenceManager()
        config = LocalServerConfig(
            engine="llamacpp",
            model_path="/models/tiny-ggml-model.gguf",
            host="localhost",
            port=8001,
            gpu_layers=0,
            context_size=2048,
        )
        server = manager.create_server(config)

        with patch("asyncio.create_subprocess_exec") as mock_proc:
            mock_process = MagicMock()
            mock_process.returncode = None
            mock_process.stdin = MagicMock()
            mock_process.stdout = MagicMock()
            mock_process.stderr = MagicMock()
            mock_proc.return_value = mock_process

            await manager.start_server(server.server_id)
            assert server.status == "running"
            assert server.is_running

    @pytest.mark.asyncio
    async def test_start_vllm_server(self):
        manager = LocalInferenceManager()
        config = LocalServerConfig(
            engine="vllm",
            model_name="sshleifer/tiny-gpt2",
            host="localhost",
            port=8002,
        )
        server = manager.create_server(config)

        with patch("asyncio.create_subprocess_exec") as mock_proc:
            mock_process = MagicMock()
            mock_process.returncode = None
            mock_process.stdin = MagicMock()
            mock_process.stdout = MagicMock()
            mock_process.stderr = MagicMock()
            mock_proc.return_value = mock_process

            await manager.start_server(server.server_id)
            assert server.status == "running"
            call_args = mock_proc.call_args[0]
            assert "vllm" in call_args
            assert "serve" in call_args

    @pytest.mark.asyncio
    async def test_stop_server_terminates_process(self):
        manager = LocalInferenceManager()
        config = LocalServerConfig(
            engine="llamacpp",
            model_path="/models/test.gguf",
            port=8003,
        )
        server = manager.create_server(config)

        mock_process = MagicMock()
        mock_process.returncode = None
        mock_process.terminate = MagicMock()
        mock_process.wait = AsyncMock(return_value=0)
        server.process = mock_process
        server.status = "running"

        await manager.stop_server(server.server_id)
        mock_process.terminate.assert_called_once()
        assert server.status == "stopped"


class TestLocalInferenceWithDownloadedModel:
    """Test the flow: search HF -> download -> start local server."""

    def test_build_llamacpp_command_with_downloaded_model(self, tmp_path):
        manager = LocalInferenceManager()
        model_path = tmp_path / "tiny-model.gguf"
        model_path.write_bytes(b"\x00" * 1024)

        config = LocalServerConfig(
            engine="llamacpp",
            model_path=str(model_path),
            port=8004,
            gpu_layers=0,
            context_size=2048,
        )

        server = manager.create_server(config)
        assert server.config.model_path == str(model_path)

        cmd = manager._build_command(config)
        assert cmd[0] == "python3"
        assert "-m" in cmd
        assert "llama_cpp.server" in cmd
        assert str(model_path) in cmd
        assert "--n_gpu_layers" in cmd
        assert "0" in cmd

    def test_build_vllm_command_with_model_name(self):
        manager = LocalInferenceManager()
        config = LocalServerConfig(
            engine="vllm",
            model_name="sshleifer/tiny-gpt2",
            port=8005,
        )

        cmd = manager._build_command(config)
        assert cmd[0] == "vllm"
        assert "serve" in cmd
        assert "sshleifer/tiny-gpt2" in cmd

    @pytest.mark.skipif(
        True,
        reason="Requires running llamacpp server - run manually",
    )
    @pytest.mark.asyncio
    async def test_inference_with_tiny_model(self):
        """Manual test: start llamacpp with tiny model and send a request.

        To run this test:
        1. Download a tiny GGUF model
        2. Start llama.cpp server with it
        3. Set the model path and run this test
        """
        manager = LocalInferenceManager()
        config = LocalServerConfig(
            engine="llamacpp",
            model_path="/path/to/tiny-model.gguf",
            port=8999,
            gpu_layers=0,
            context_size=2048,
        )
        server = manager.create_server(config)
        await manager.start_server(server.server_id)

        try:
            import json
            import urllib.request

            url = f"http://localhost:{config.port}/v1/chat/completions"
            data = json.dumps({
                "model": "tiny-model",
                "messages": [{"role": "user", "content": "Hello"}],
                "max_tokens": 50,
            }).encode()

            req = urllib.request.Request(
                url, data=data, headers={"Content-Type": "application/json"}
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                result = json.loads(resp.read().decode())
                assert "choices" in result
                assert len(result["choices"]) >= 1
                assert "message" in result["choices"][0]
        finally:
            await manager.stop_server(server.server_id)
