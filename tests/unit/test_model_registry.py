"""Verbose unit tests for ModelRegistry."""

import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from general_ludd.models.model_registry import DownloadedModel, ModelRegistry, ModelSearchResult


class TestModelSearchResult:
    def test_defaults(self):
        r = ModelSearchResult(model_id="test/model")
        assert r.model_id == "test/model"
        assert r.downloads == 0
        assert r.tags == []

    def test_with_values(self):
        r = ModelSearchResult(model_id="meta/llama", downloads=1000, tags=["text-generation"])
        assert r.downloads == 1000
        assert "text-generation" in r.tags


class TestDownloadedModel:
    def test_defaults(self):
        d = DownloadedModel(model_id="test/model", local_path="/tmp/model")
        assert d.engine == "vllm"
        assert d.size_bytes == 0
        assert d.filename is None


class TestModelRegistryUnit:
    def test_init_creates_cache_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cache = os.path.join(tmpdir, "models")
            ModelRegistry(cache_dir=cache)
            assert Path(cache).exists()

    def test_search_calls_hf_api(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            reg = ModelRegistry(cache_dir=tmpdir)
            mock_model = MagicMock()
            mock_model.id = "test/model"
            mock_model.author = "test"
            mock_model.downloads = 500
            mock_model.tags = ["text-generation"]
            mock_model.pipeline_tag = "text-generation"
            mock_model.library_name = "transformers"

            with patch.object(reg, "_get_api") as mock_api:
                api = MagicMock()
                api.list_models.return_value = [mock_model]
                mock_api.return_value = api
                results = reg.search(query="llama", limit=5)

            assert len(results) == 1
            assert results[0].model_id == "test/model"
            assert results[0].downloads == 500

    def test_search_with_tags(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            reg = ModelRegistry(cache_dir=tmpdir)
            with patch.object(reg, "_get_api") as mock_api:
                api = MagicMock()
                api.list_models.return_value = []
                mock_api.return_value = api
                reg.search(query="llama", tags=["text-generation", "pytorch"])
                api.list_models.assert_called_once()

    def test_get_model_info(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            reg = ModelRegistry(cache_dir=tmpdir)
            mock_info = MagicMock()
            mock_info.id = "meta/llama"
            mock_info.author = "meta"
            mock_info.downloads = 10000
            mock_info.tags = ["text-generation"]
            mock_info.pipeline_tag = "text-generation"
            mock_info.library_name = "transformers"
            mock_info.last_modified = None

            with patch.object(reg, "_get_api") as mock_api:
                api = MagicMock()
                api.model_info.return_value = mock_info
                mock_api.return_value = api
                info = reg.get_model_info("meta/llama")

            assert info["model_id"] == "meta/llama"
            assert info["downloads"] == 10000

    def test_list_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            reg = ModelRegistry(cache_dir=tmpdir)
            with patch.object(reg, "_get_api") as mock_api:
                api = MagicMock()
                api.list_repo_files.return_value = ["config.json", "model.safetensors", "tokenizer.json"]
                mock_api.return_value = api
                files = reg.list_files("meta/llama")
            assert "config.json" in files
            assert len(files) == 3

    def test_download_with_filename(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            reg = ModelRegistry(cache_dir=tmpdir)
            with patch("huggingface_hub.hf_hub_download") as mock_dl:
                model_path = os.path.join(tmpdir, "model.gguf")
                Path(model_path).write_text("fake model data")
                mock_dl.return_value = model_path
                result = reg.download("test/model", filename="model.gguf", engine="llamacpp")

            assert result.model_id == "test/model"
            assert result.filename == "model.gguf"
            assert result.engine == "llamacpp"
            assert result.size_bytes > 0
            assert reg.get_downloaded("test/model") is not None

    def test_download_full_snapshot(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            reg = ModelRegistry(cache_dir=tmpdir)
            with patch("huggingface_hub.snapshot_download") as mock_dl:
                snap_dir = os.path.join(tmpdir, "snapshot")
                os.makedirs(snap_dir)
                Path(os.path.join(snap_dir, "config.json")).write_text("{}")
                mock_dl.return_value = snap_dir
                result = reg.download("test/model", engine="vllm")

            assert result.model_id == "test/model"
            assert result.engine == "vllm"

    def test_list_downloaded(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            reg = ModelRegistry(cache_dir=tmpdir)
            with patch("huggingface_hub.snapshot_download") as mock_dl:
                snap_dir = os.path.join(tmpdir, "s1")
                os.makedirs(snap_dir)
                mock_dl.return_value = snap_dir
                reg.download("model/a")

                snap_dir2 = os.path.join(tmpdir, "s2")
                os.makedirs(snap_dir2)
                mock_dl.return_value = snap_dir2
                reg.download("model/b")

            downloaded = reg.list_downloaded()
            assert len(downloaded) == 2
            ids = [d.model_id for d in downloaded]
            assert "model/a" in ids
            assert "model/b" in ids

    def test_remove_downloaded(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            reg = ModelRegistry(cache_dir=tmpdir)
            with patch("huggingface_hub.snapshot_download") as mock_dl:
                snap_dir = os.path.join(tmpdir, "s1")
                os.makedirs(snap_dir)
                mock_dl.return_value = snap_dir
                reg.download("test/model")

            assert reg.get_downloaded("test/model") is not None
            reg.remove_downloaded("test/model")
            assert reg.get_downloaded("test/model") is None

    def test_index_persistence(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            reg1 = ModelRegistry(cache_dir=tmpdir)
            with patch("huggingface_hub.snapshot_download") as mock_dl:
                snap_dir = os.path.join(tmpdir, "s1")
                os.makedirs(snap_dir)
                mock_dl.return_value = snap_dir
                reg1.download("persistent/model")

            reg2 = ModelRegistry(cache_dir=tmpdir)
            assert reg2.get_downloaded("persistent/model") is not None

    def test_remove_nonexistent_is_noop(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            reg = ModelRegistry(cache_dir=tmpdir)
            reg.remove_downloaded("nonexistent")

    def test_hf_token_from_env(self):
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(os.environ, {"HF_TOKEN": "test-token-123"}):
            reg = ModelRegistry(cache_dir=tmpdir)
            assert reg._hf_token == "test-token-123"

    def test_hf_token_from_constructor(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            reg = ModelRegistry(cache_dir=tmpdir, hf_token="explicit-token")
            assert reg._hf_token == "explicit-token"

    def test_search_empty_results(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            reg = ModelRegistry(cache_dir=tmpdir)
            with patch.object(reg, "_get_api") as mock_api:
                api = MagicMock()
                api.list_models.return_value = []
                mock_api.return_value = api
                results = reg.search(query="nonexistent-model-xyz")
            assert results == []

    def test_search_with_author_filter(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            reg = ModelRegistry(cache_dir=tmpdir)
            with patch.object(reg, "_get_api") as mock_api:
                api = MagicMock()
                api.list_models.return_value = []
                mock_api.return_value = api
                reg.search(query="llama", author="meta")
                call_kwargs = api.list_models.call_args[1]
                assert call_kwargs["author"] == "meta"


def test_download_unpinned_warns(caplog):
    import logging

    # xdist-order pollution: any test that runs the real daemon lifespan
    # against SQLite (e.g. `with TestClient(app) as client:`) triggers
    # alembic/env.py's fileConfig(alembic.ini), which defaults to
    # disable_existing_loggers=True. alembic.ini's [loggers] only lists
    # root/sqlalchemy/alembic, so this already-created logger gets
    # `.disabled = True` for the rest of the xdist worker — and
    # `Logger.isEnabledFor()` short-circuits on `.disabled` before ever
    # checking level/propagate, so restoring only propagate wouldn't fix it.
    logging.getLogger("general_ludd.models.model_registry").disabled = False
    logging.getLogger("general_ludd.models.model_registry").propagate = True

    with tempfile.TemporaryDirectory() as tmpdir:
        reg = ModelRegistry(cache_dir=tmpdir)
        with patch("huggingface_hub.snapshot_download") as mock_dl:
            snap_dir = os.path.join(tmpdir, "snap")
            os.makedirs(snap_dir)
            mock_dl.return_value = snap_dir
            with caplog.at_level(logging.WARNING, logger="general_ludd.models.model_registry"):
                result = reg.download("test/model")
        assert mock_dl.call_args.kwargs["revision"] is None
        assert result.revision is None
        assert any("without a pinned revision" in r.getMessage() for r in caplog.records)


class TestModelRegistryRevision:
    """B615 supply-chain fix: download() forwards a pinned revision and warns when unpinned."""

    def test_download_filename_forwards_revision(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            reg = ModelRegistry(cache_dir=tmpdir)
            with patch("huggingface_hub.hf_hub_download") as mock_dl:
                model_path = os.path.join(tmpdir, "model.gguf")
                Path(model_path).write_text("fake")
                mock_dl.return_value = model_path
                result = reg.download("test/model", filename="model.gguf", revision="abc123")
            assert mock_dl.call_args.kwargs["revision"] == "abc123"
            assert result.revision == "abc123"

    def test_download_snapshot_forwards_revision(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            reg = ModelRegistry(cache_dir=tmpdir)
            with patch("huggingface_hub.snapshot_download") as mock_dl:
                snap_dir = os.path.join(tmpdir, "snap")
                os.makedirs(snap_dir)
                mock_dl.return_value = snap_dir
                result = reg.download("test/model", revision="v1.0")
            assert mock_dl.call_args.kwargs["revision"] == "v1.0"
            assert result.revision == "v1.0"

    def test_download_pinned_does_not_warn(self, caplog):
        import logging

        with tempfile.TemporaryDirectory() as tmpdir:
            reg = ModelRegistry(cache_dir=tmpdir)
            with patch("huggingface_hub.snapshot_download") as mock_dl:
                snap_dir = os.path.join(tmpdir, "snap")
                os.makedirs(snap_dir)
                mock_dl.return_value = snap_dir
                with caplog.at_level(logging.WARNING, logger="general_ludd.models.model_registry"):
                    reg.download("test/model", revision="deadbeef")
            assert not any("without a pinned revision" in r.getMessage() for r in caplog.records)

    def test_revision_persisted_and_reloaded(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            reg1 = ModelRegistry(cache_dir=tmpdir)
            with patch("huggingface_hub.snapshot_download") as mock_dl:
                snap_dir = os.path.join(tmpdir, "snap")
                os.makedirs(snap_dir)
                mock_dl.return_value = snap_dir
                reg1.download("persist/model", revision="rev42")
            reg2 = ModelRegistry(cache_dir=tmpdir)
            dm = reg2.get_downloaded("persist/model")
            assert dm is not None
            assert dm.revision == "rev42"

    def test_load_index_tolerates_missing_revision(self):
        import json as _json

        with tempfile.TemporaryDirectory() as tmpdir:
            reg = ModelRegistry(cache_dir=tmpdir)
            # Legacy index without a `revision` key (and an unknown future key).
            legacy = [
                {
                    "model_id": "legacy/model",
                    "local_path": "/tmp/legacy",
                    "filename": None,
                    "engine": "vllm",
                    "size_bytes": 0,
                    "downloaded_at": 0.0,
                    "future_field": "ignored",
                }
            ]
            reg._index_path().write_text(_json.dumps(legacy))
            reg.refresh()
            dm = reg.get_downloaded("legacy/model")
            assert dm is not None
            assert dm.revision is None


class TestModelRegistryRefresh:
    def test_refresh_reloads_index(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            reg = ModelRegistry(cache_dir=tmpdir)
            with patch("huggingface_hub.snapshot_download") as mock_dl:
                snap_dir = os.path.join(tmpdir, "s1")
                os.makedirs(snap_dir)
                mock_dl.return_value = snap_dir
                reg.download("test/model")
            assert reg.get_downloaded("test/model") is not None
            reg._downloaded.clear()
            assert reg.get_downloaded("test/model") is None
            reg.refresh()
            assert reg.get_downloaded("test/model") is not None

    def test_refresh_clears_in_memory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            reg = ModelRegistry(cache_dir=tmpdir)
            reg._downloaded["manual"] = DownloadedModel(model_id="manual", local_path="/tmp")
            reg.refresh()
            assert reg.get_downloaded("manual") is None
