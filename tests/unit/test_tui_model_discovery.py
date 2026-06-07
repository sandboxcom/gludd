"""Test model discovery integration in the TUI model services view."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path


class TestTUIWithDownloadedModels:
    def test_model_registry_lists_downloaded_models(self):
        from general_ludd.models.model_registry import ModelRegistry

        with tempfile.TemporaryDirectory() as tmpdir:
            reg = ModelRegistry(cache_dir=tmpdir)
            index_path = Path(tmpdir) / "model_index.json"
            sample = [
                {
                    "model_id": "meta-llama/Llama-3.2-1B",
                    "local_path": "/tmp/models/llama-3.2-1b",
                    "engine": "vllm",
                    "size_bytes": 2048000000,
                    "downloaded_at": 1717766400.0,
                },
                {
                    "model_id": "TheBloke/Mistral-7B-GGUF",
                    "local_path": "/tmp/models/mistral-7b.gguf",
                    "filename": "mistral-7b-q4.gguf",
                    "engine": "llamacpp",
                    "size_bytes": 4096000000,
                    "downloaded_at": 1717770000.0,
                },
            ]
            index_path.write_text(json.dumps(sample, indent=2))
            reg.refresh()
            models = reg.list_downloaded()
            assert len(models) == 2
            assert models[0].model_id == "meta-llama/Llama-3.2-1B"
            assert models[0].engine == "vllm"
            assert models[1].model_id == "TheBloke/Mistral-7B-GGUF"
            assert models[1].filename == "mistral-7b-q4.gguf"

    def test_empty_registry_returns_empty_list(self):
        from general_ludd.models.model_registry import ModelRegistry

        with tempfile.TemporaryDirectory() as tmpdir:
            reg = ModelRegistry(cache_dir=tmpdir)
            assert reg.list_downloaded() == []

    def test_build_model_table_includes_downloaded_models(self):
        from rich.table import Table

        from general_ludd.cli import _build_model_table
        from general_ludd.models.model_registry import DownloadedModel

        downloaded = [
            DownloadedModel(
                model_id="meta-llama/Llama-3.2-1B",
                local_path="/tmp/models/llama-3.2-1b",
                engine="vllm",
                size_bytes=2048000000,
                downloaded_at=1717766400.0,
            ),
        ]
        t = _build_model_table([], downloaded)
        assert isinstance(t, Table)
        assert "Downloaded Models" in t.title
        assert t.row_count == 1

    def test_build_model_table_splits_servers_and_downloaded(self):
        from rich.table import Table

        from general_ludd.cli import _build_model_table
        from general_ludd.infra.local_inference import LocalServer, LocalServerConfig
        from general_ludd.models.model_registry import DownloadedModel

        server = LocalServer(
            server_id="local-1",
            config=LocalServerConfig(engine="llamacpp", model_path="/models/test.gguf", port=8081),
        )
        downloaded = [
            DownloadedModel(
                model_id="test/Model",
                local_path="/tmp/test",
                engine="vllm",
                size_bytes=1000,
                downloaded_at=0.0,
            ),
        ]
        t = _build_model_table([server], downloaded)
        assert isinstance(t, Table)
        assert "Servers + Downloads" in t.title
        assert t.row_count == 2

    def test_build_model_status_msg_shows_counts(self):
        from general_ludd.cli import _build_model_status_msg
        from general_ludd.infra.local_inference import LocalServer, LocalServerConfig
        from general_ludd.models.model_registry import DownloadedModel

        servers = [
            LocalServer(
                server_id="local-1",
                config=LocalServerConfig(engine="vllm", model_name="test-model", port=8000),
            )
        ]
        downloaded = [
            DownloadedModel(
                model_id="test/Model",
                local_path="/tmp/test",
                engine="vllm",
                size_bytes=1000,
                downloaded_at=0.0,
            ),
        ]
        msg = _build_model_status_msg(servers, downloaded)
        assert "1 configured" in msg
        assert "1 downloaded" in msg

    def test_build_model_status_msg_only_servers(self):
        from general_ludd.cli import _build_model_status_msg
        from general_ludd.infra.local_inference import LocalServer, LocalServerConfig

        servers = [
            LocalServer(
                server_id="s1",
                config=LocalServerConfig(engine="vllm", model_name="m1", port=8000),
            ),
            LocalServer(
                server_id="s2",
                config=LocalServerConfig(engine="llamacpp", model_path="/tmp/m2.gguf", port=8081),
            ),
        ]
        msg = _build_model_status_msg(servers, [])
        assert "2 configured" in msg
        assert "downloaded" not in msg

    def test_build_model_status_msg_only_downloaded(self):
        from general_ludd.cli import _build_model_status_msg
        from general_ludd.models.model_registry import DownloadedModel

        downloaded = [
            DownloadedModel(
                model_id="test/Model",
                local_path="/tmp/test",
                engine="vllm",
                size_bytes=1000,
                downloaded_at=0.0,
            ),
        ]
        msg = _build_model_status_msg([], downloaded)
        assert "1 downloaded" in msg
        assert "configured" not in msg
