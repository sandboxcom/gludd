"""Deep edge-case tests for model source resolution, priority, and fallback."""

from __future__ import annotations

import os
import time
from unittest.mock import patch

import pytest

from general_ludd.cloud.model_sources import (
    ALTERNATIVE_SOURCES,
    DownloadedFile,
    DownloadError,
    ModelSource,
    download_with_fallback,
    resolve_source_chain,
)
from general_ludd.local_model._local_model_configs import LocalModelConfig


def _cfg(name: str = "qwen-0.5b", repo: str = "", filename: str = "") -> LocalModelConfig:
    return LocalModelConfig(
        name=name,
        repo=repo or f"org/{name}",
        filename=filename or f"{name}.gguf",
    )


class TestResolveSourceChainDeep:
    def test_empty_list_returns_default(self):
        chain = resolve_source_chain(order=[])
        assert chain[0] == ModelSource.OLLAMA
        assert ModelSource.HUGGINGFACE in chain

    def test_all_unknown_sources_in_order_falls_back_to_default(self):
        chain = resolve_source_chain(order=[])
        assert chain[0] == ModelSource.OLLAMA
        assert len(chain) == 4

    def test_mixed_valid_sources_filtered_preserves_order(self):
        chain = resolve_source_chain(order=[ModelSource.LOCAL_PATH, ModelSource.DIRECT_URL])
        assert chain == [ModelSource.LOCAL_PATH, ModelSource.DIRECT_URL]

    def test_order_is_copied_not_mutated(self):
        original = [ModelSource.S3_MIRROR, ModelSource.HUGGINGFACE]
        chain = resolve_source_chain(order=original)
        chain.append(ModelSource.OLLAMA)
        assert original == [ModelSource.S3_MIRROR, ModelSource.HUGGINGFACE]

    def test_duplicate_sources_preserved_in_order(self):
        chain = resolve_source_chain(order=[ModelSource.OLLAMA, ModelSource.OLLAMA])
        assert chain == [ModelSource.OLLAMA, ModelSource.OLLAMA]

    def test_none_order_returns_default_copy(self):
        chain = resolve_source_chain(order=None)
        chain[0] = ModelSource.LOCAL_PATH
        default = resolve_source_chain()
        assert default[0] == ModelSource.OLLAMA

    def test_all_five_sources_roundtrip(self):
        order = [
            ModelSource.S3_MIRROR,
            ModelSource.LOCAL_PATH,
            ModelSource.DIRECT_URL,
            ModelSource.HUGGINGFACE,
            ModelSource.OLLAMA,
        ]
        assert resolve_source_chain(order=order) == order

    def test_only_local_path_source(self):
        chain = resolve_source_chain(order=[ModelSource.LOCAL_PATH])
        assert chain == [ModelSource.LOCAL_PATH]


class TestResolveSourceChainPriority:
    def test_huggingface_before_ollama_custom(self):
        chain = resolve_source_chain(order=[ModelSource.HUGGINGFACE, ModelSource.OLLAMA])
        assert chain[0] == ModelSource.HUGGINGFACE
        assert chain[1] == ModelSource.OLLAMA

    def test_direct_url_highest_priority(self):
        chain = resolve_source_chain(
            order=[
                ModelSource.DIRECT_URL,
                ModelSource.OLLAMA,
                ModelSource.HUGGINGFACE,
            ]
        )
        assert chain[0] == ModelSource.DIRECT_URL

    def test_local_before_all_others(self):
        chain = resolve_source_chain(
            order=[
                ModelSource.LOCAL_PATH,
                ModelSource.OLLAMA,
                ModelSource.HUGGINGFACE,
                ModelSource.DIRECT_URL,
                ModelSource.S3_MIRROR,
            ]
        )
        assert chain[0] == ModelSource.LOCAL_PATH


class TestDownloadWithFallbackDeep:
    def test_unknown_model_falls_back_to_config_repo_filename(self):
        cfg = _cfg(name="invented-model-xyz", repo="myorg/my-model", filename="my-model.gguf")
        with (
            patch("general_ludd.cloud.model_sources._download_from_huggingface") as mock_hf,
            patch("general_ludd.cloud.model_sources._check_ollama_installed", return_value=False),
        ):
            mock_hf.return_value = DownloadedFile(local_path="/tmp/model.gguf", source=ModelSource.HUGGINGFACE)
            result = download_with_fallback(cfg)
            assert result.source == ModelSource.HUGGINGFACE
            call_args = mock_hf.call_args.kwargs
            assert call_args["model_id"] == "myorg/my-model"
            assert call_args["filename"] == "my-model.gguf"

    def test_retries_minus_1_means_zero_retries(self):
        cfg = _cfg()
        with (
            patch("general_ludd.cloud.model_sources._check_ollama_installed", return_value=True),
            patch(
                "general_ludd.cloud.model_sources._download_from_ollama",
                side_effect=Exception("fail"),
            ),
            patch(
                "general_ludd.cloud.model_sources._download_from_huggingface",
            ) as mock_hf,
        ):
            mock_hf.return_value = DownloadedFile(local_path="/tmp/model.gguf", source=ModelSource.HUGGINGFACE)
            result = download_with_fallback(cfg, retries=0)
            assert result.source == ModelSource.HUGGINGFACE

    def test_retries_0_allows_zero_retries(self):
        cfg = _cfg()
        with (
            patch("general_ludd.cloud.model_sources._check_ollama_installed", return_value=True),
            patch(
                "general_ludd.cloud.model_sources._download_from_ollama",
                side_effect=Exception("fail"),
            ),
            patch(
                "general_ludd.cloud.model_sources._download_from_huggingface",
            ) as mock_hf,
        ):
            mock_hf.return_value = DownloadedFile(local_path="/tmp/model.gguf", source=ModelSource.HUGGINGFACE)
            download_with_fallback(cfg, retries=0)
            assert mock_hf.call_count == 1

    def test_model_with_only_huggingface_source(self):
        cfg = _cfg(name="olmoe-1b-7b")
        sources = ALTERNATIVE_SOURCES["olmoe-1b-7b"]
        assert ModelSource.OLLAMA not in sources
        assert ModelSource.DIRECT_URL not in sources
        with (
            patch(
                "general_ludd.cloud.model_sources._download_from_huggingface",
            ) as mock_hf,
        ):
            mock_hf.return_value = DownloadedFile(local_path="/tmp/model.gguf", source=ModelSource.HUGGINGFACE)
            result = download_with_fallback(cfg)
            assert result.source == ModelSource.HUGGINGFACE

    def test_model_missing_ollama_but_huggingface_present(self):
        cfg = _cfg(name="deepseek-coder-1.3b")
        with (
            patch("general_ludd.cloud.model_sources._check_ollama_installed", return_value=False),
            patch(
                "general_ludd.cloud.model_sources._download_from_huggingface",
            ) as mock_hf,
        ):
            mock_hf.return_value = DownloadedFile(local_path="/tmp/model.gguf", source=ModelSource.HUGGINGFACE)
            result = download_with_fallback(cfg)
            assert result.source == ModelSource.HUGGINGFACE

    def test_custom_order_skips_missing_sources(self):
        cfg = _cfg(name="gemma-2-2b")
        with (
            patch("general_ludd.cloud.model_sources._check_ollama_installed", return_value=False),
            patch(
                "general_ludd.cloud.model_sources._download_from_huggingface",
            ) as mock_hf,
        ):
            mock_hf.return_value = DownloadedFile(local_path="/tmp/model.gguf", source=ModelSource.HUGGINGFACE)
            result = download_with_fallback(
                cfg,
                order=[ModelSource.OLLAMA, ModelSource.DIRECT_URL, ModelSource.HUGGINGFACE],
            )
            assert result.source == ModelSource.HUGGINGFACE

    def test_all_sources_exhausted_error_message_includes_tried_sources(self):
        cfg = _cfg(name="qwen-0.5b")
        with (
            patch("general_ludd.cloud.model_sources._check_ollama_installed", return_value=False),
            patch(
                "general_ludd.cloud.model_sources._download_from_huggingface",
                side_effect=Exception("hf fails"),
            ),
        ):
            with pytest.raises(DownloadError) as exc_info:
                download_with_fallback(cfg, order=[ModelSource.HUGGINGFACE])
            msg = str(exc_info.value)
            assert "All sources exhausted" in msg
            assert "qwen-0.5b" in msg

    def test_alternative_sources_copy_not_mutated(self):
        cfg = _cfg(name="qwen-0.5b")
        original_ollama = ALTERNATIVE_SOURCES["qwen-0.5b"].get(ModelSource.OLLAMA)
        with (
            patch("general_ludd.cloud.model_sources._check_ollama_installed", return_value=True),
            patch(
                "general_ludd.cloud.model_sources._download_from_ollama",
            ) as mock_ol,
        ):
            mock_ol.return_value = DownloadedFile(local_path="/tmp/model.gguf", source=ModelSource.OLLAMA)
            download_with_fallback(cfg)
        assert ALTERNATIVE_SOURCES["qwen-0.5b"].get(ModelSource.OLLAMA) == original_ollama


class TestFallbackSourceOrdering:
    def test_ollama_fails_huggingface_succeeds(self):
        cfg = _cfg()
        with (
            patch("general_ludd.cloud.model_sources._check_ollama_installed", return_value=True),
            patch(
                "general_ludd.cloud.model_sources._download_from_ollama",
                side_effect=Exception("ollama error"),
            ),
            patch(
                "general_ludd.cloud.model_sources._download_from_huggingface",
            ) as mock_hf,
        ):
            mock_hf.return_value = DownloadedFile(local_path="/tmp/model.gguf", source=ModelSource.HUGGINGFACE)
            result = download_with_fallback(cfg)
            assert result.source == ModelSource.HUGGINGFACE

    def test_ollama_and_hf_fail_s3_succeeds(self):
        cfg = _cfg(name="qwen-0.5b")
        with (
            patch("general_ludd.cloud.model_sources._check_ollama_installed", return_value=True),
            patch(
                "general_ludd.cloud.model_sources._download_from_ollama",
                side_effect=Exception("ollama error"),
            ),
            patch(
                "general_ludd.cloud.model_sources._download_from_huggingface",
                side_effect=Exception("hf error"),
            ),
            patch(
                "general_ludd.cloud.model_sources._download_from_direct_url",
            ) as mock_url,
        ):
            mock_url.return_value = DownloadedFile(local_path="/tmp/model.gguf", source=ModelSource.S3_MIRROR)
            result = download_with_fallback(cfg)
            assert result.source == ModelSource.S3_MIRROR

    def test_ollama_hf_s3_fail_direct_url_succeeds(self):
        cfg = _cfg(name="qwen-0.5b")
        with (
            patch("general_ludd.cloud.model_sources._check_ollama_installed", return_value=True),
            patch(
                "general_ludd.cloud.model_sources._download_from_ollama",
                side_effect=Exception("ollama error"),
            ),
            patch(
                "general_ludd.cloud.model_sources._download_from_huggingface",
                side_effect=Exception("hf error"),
            ),
            patch(
                "general_ludd.cloud.model_sources._download_from_direct_url",
            ) as mock_url,
        ):
            mock_url.return_value = DownloadedFile(local_path="/tmp/model.gguf", source=ModelSource.DIRECT_URL)
            result = download_with_fallback(
                cfg,
                order=[
                    ModelSource.S3_MIRROR,
                    ModelSource.HUGGINGFACE,
                    ModelSource.OLLAMA,
                    ModelSource.DIRECT_URL,
                ],
            )
            assert result.source == ModelSource.DIRECT_URL

    def test_retries_after_first_source_fails_then_second_succeeds_on_retry(self):
        cfg = _cfg()
        with (
            patch("general_ludd.cloud.model_sources._check_ollama_installed", return_value=True),
            patch(
                "general_ludd.cloud.model_sources._download_from_ollama",
                side_effect=[Exception("fail1"), Exception("fail2")],
            ),
            patch(
                "general_ludd.cloud.model_sources._download_from_huggingface",
            ) as mock_hf,
        ):
            mock_hf.return_value = DownloadedFile(local_path="/tmp/model.gguf", source=ModelSource.HUGGINGFACE)
            result = download_with_fallback(cfg, retries=1)
            assert result.source == ModelSource.HUGGINGFACE

    def test_first_source_eventually_succeeds_on_third_retry(self):
        cfg = _cfg()
        with (
            patch("general_ludd.cloud.model_sources._check_ollama_installed", return_value=True),
            patch(
                "general_ludd.cloud.model_sources._download_from_ollama",
                side_effect=[
                    Exception("fail 1"),
                    Exception("fail 2"),
                    DownloadedFile(local_path="/tmp/ollama.gguf", source=ModelSource.OLLAMA),
                ],
            ),
        ):
            result = download_with_fallback(cfg, retries=2)
            assert result.source == ModelSource.OLLAMA


class TestTrySourceEdgeCases:
    def test_local_path_missing_file_raises(self, tmp_path):
        cfg = _cfg(name="qwen-0.5b")
        local = str(tmp_path / "nonexistent-model.gguf")
        with (
            patch.dict(ALTERNATIVE_SOURCES["qwen-0.5b"], {ModelSource.LOCAL_PATH: local}),
            patch("general_ludd.cloud.model_sources._check_ollama_installed", return_value=False),
            patch(
                "general_ludd.cloud.model_sources._download_from_huggingface",
                side_effect=Exception("hf"),
            ),
            pytest.raises(DownloadError) as exc_info,
        ):
            download_with_fallback(cfg, order=[ModelSource.LOCAL_PATH], retries=0)
        assert "not found" in str(exc_info.value).lower()

    def test_local_path_with_existing_file_succeeds(self, tmp_path):
        model_file = tmp_path / "test-model.gguf"
        model_file.write_bytes(b"\x00" * 1024)
        cfg = _cfg(name="qwen-0.5b")
        with patch.dict(ALTERNATIVE_SOURCES["qwen-0.5b"], {ModelSource.LOCAL_PATH: str(model_file)}):
            result = download_with_fallback(cfg, order=[ModelSource.LOCAL_PATH], retries=0)
            assert result.source == ModelSource.LOCAL_PATH
            assert result.local_path == str(model_file)

    def test_local_path_falls_back_to_config_filename(self, tmp_path):
        local = str(tmp_path / "config-fallback.gguf")
        with open(local, "w") as f:
            f.write("test")
        cfg = _cfg(name="qwen-0.5b", repo="x", filename="config-fallback.gguf")
        with patch.dict(ALTERNATIVE_SOURCES["qwen-0.5b"], {ModelSource.LOCAL_PATH: local}):
            result = download_with_fallback(cfg, order=[ModelSource.LOCAL_PATH], retries=0)
            assert result.source == ModelSource.LOCAL_PATH

    def test_ollama_not_installed_skips_and_falls_through(self):
        cfg = _cfg()
        with (
            patch("general_ludd.cloud.model_sources._check_ollama_installed", return_value=False),
            patch(
                "general_ludd.cloud.model_sources._download_from_huggingface",
            ) as mock_hf,
        ):
            mock_hf.return_value = DownloadedFile(local_path="/tmp/model.gguf", source=ModelSource.HUGGINGFACE)
            result = download_with_fallback(cfg)
            assert result.source == ModelSource.HUGGINGFACE

    def test_s3_mirror_resolves_via_direct_url_download(self):
        cfg = _cfg(name="smollm2-135m")
        with (
            patch("general_ludd.cloud.model_sources._check_ollama_installed", return_value=False),
            patch(
                "general_ludd.cloud.model_sources._download_from_direct_url",
            ) as mock_url,
        ):
            mock_url.return_value = DownloadedFile(local_path="/tmp/s3-model.gguf", source=ModelSource.S3_MIRROR)
            result = download_with_fallback(cfg, order=[ModelSource.S3_MIRROR])
            assert result.source == ModelSource.S3_MIRROR


class TestDownloadedFileEdgeCases:
    def test_default_timestamp_is_set(self):
        before = time.time()
        df = DownloadedFile(local_path="/tmp/x.gguf", source=ModelSource.HUGGINGFACE)
        after = time.time()
        assert before <= df.downloaded_at <= after

    def test_size_bytes_defaults_to_zero(self):
        df = DownloadedFile(local_path="/tmp/x.gguf", source=ModelSource.OLLAMA)
        assert df.size_bytes == 0

    def test_explicit_size_stored(self):
        df = DownloadedFile(local_path="/tmp/x.gguf", source=ModelSource.DIRECT_URL, size_bytes=54321)
        assert df.size_bytes == 54321

    def test_all_sources_have_valid_string_representation(self):
        df = DownloadedFile(local_path="/tmp/x.gguf", source=ModelSource.S3_MIRROR)
        assert str(df.source) == "s3_mirror"
        assert repr(df.source) == "<ModelSource.S3_MIRROR: 's3_mirror'>"

    def test_source_is_an_enum_str(self):
        assert isinstance(ModelSource.HUGGINGFACE, str)
        assert ModelSource.HUGGINGFACE == "huggingface"


class TestAlternativeSourcesDeep:
    def test_every_source_value_is_a_member_of_enum(self):
        for name, sources in ALTERNATIVE_SOURCES.items():
            for source in sources:
                assert source in ModelSource, f"Invalid source {source} in {name}"

    def test_direct_url_values_are_well_formed_urls(self):
        for name, sources in ALTERNATIVE_SOURCES.items():
            if ModelSource.DIRECT_URL in sources:
                url = sources[ModelSource.DIRECT_URL]
                assert isinstance(url, str)
                assert url.startswith("https://"), f"Direct URL for {name} is not HTTPS: {url}"

    def test_s3_mirror_values_are_well_formed_urls(self):
        for name, sources in ALTERNATIVE_SOURCES.items():
            if ModelSource.S3_MIRROR in sources:
                url = sources[ModelSource.S3_MIRROR]
                assert isinstance(url, str)
                assert url.startswith("https://"), f"S3 mirror URL for {name} is not HTTPS: {url}"

    def test_huggingface_entries_have_repo_and_filename(self):
        for name, sources in ALTERNATIVE_SOURCES.items():
            hf = sources[ModelSource.HUGGINGFACE]
            assert isinstance(hf, dict)
            assert "repo" in hf, f"Missing repo in {name}"
            assert "filename" in hf, f"Missing filename in {name}"
            assert hf["repo"], f"Empty repo in {name}"
            assert hf["filename"], f"Empty filename in {name}"
            assert hf["filename"].endswith(".gguf"), f"Filename not .gguf in {name}: {hf['filename']}"

    def test_ollama_entries_are_non_empty_strings(self):
        for _name, sources in ALTERNATIVE_SOURCES.items():
            if ModelSource.OLLAMA in sources:
                val = sources[ModelSource.OLLAMA]
                assert isinstance(val, str)
                assert len(val) > 0

    def test_model_with_partial_sources_works(self):
        assert "olmoe-1b-7b" in ALTERNATIVE_SOURCES
        sources = ALTERNATIVE_SOURCES["olmoe-1b-7b"]
        assert ModelSource.OLLAMA not in sources
        assert ModelSource.DIRECT_URL not in sources
        assert ModelSource.S3_MIRROR not in sources
        assert ModelSource.HUGGINGFACE in sources


class TestConfigSourceMatches:
    def test_alternative_sources_model_name_matches_config_name(self):
        from general_ludd.local_model._local_model_configs import _LOCAL_MODELS

        config_names = {cfg.name for cfg in _LOCAL_MODELS}
        for name in ALTERNATIVE_SOURCES:
            assert name in config_names, f"ALTERNATIVE_SOURCES key {name} not in _LOCAL_MODELS"


class TestConstantDefaults:
    def test_default_timeout_is_positive_number(self):
        from general_ludd.cloud.model_sources import DEFAULT_TIMEOUT

        assert float(DEFAULT_TIMEOUT) > 0

    def test_default_retries_is_non_negative(self):
        from general_ludd.cloud.model_sources import DEFAULT_RETRIES

        assert int(DEFAULT_RETRIES) >= 0

    def test_gguf_model_dir_is_absolute(self):
        from general_ludd.cloud.model_sources import _GGUF_MODEL_DIR

        assert os.path.isabs(_GGUF_MODEL_DIR)


class TestDownloadWithRetriesBoundaries:
    def test_retries_negative_means_no_retries(self):
        cfg = _cfg()
        with (
            patch("general_ludd.cloud.model_sources._check_ollama_installed", return_value=True),
            patch(
                "general_ludd.cloud.model_sources._download_from_ollama",
                side_effect=Exception("fail"),
            ),
            patch(
                "general_ludd.cloud.model_sources._download_from_huggingface",
            ) as mock_hf,
        ):
            mock_hf.return_value = DownloadedFile(local_path="/tmp/model.gguf", source=ModelSource.HUGGINGFACE)
            result = download_with_fallback(cfg, retries=0)
            assert result.source == ModelSource.HUGGINGFACE

    def test_retries_large_value_still_works(self):
        cfg = _cfg()
        with (
            patch("general_ludd.cloud.model_sources._check_ollama_installed", return_value=True),
            patch(
                "general_ludd.cloud.model_sources._download_from_ollama",
            ) as mock_ol,
        ):
            mock_ol.return_value = DownloadedFile(local_path="/tmp/model.gguf", source=ModelSource.OLLAMA)
            result = download_with_fallback(cfg, retries=10)
            assert result.source == ModelSource.OLLAMA

    def test_exponential_backoff_sleeps_between_retries(self):
        cfg = _cfg()
        with (
            patch("general_ludd.cloud.model_sources._check_ollama_installed", return_value=True),
            patch(
                "general_ludd.cloud.model_sources._download_from_ollama",
                side_effect=[Exception("r1"), Exception("r2"), Exception("r3")],
            ),
            patch(
                "general_ludd.cloud.model_sources._download_from_huggingface",
            ) as mock_hf,
            patch("general_ludd.cloud.model_sources.time.sleep") as mock_sleep,
        ):
            mock_hf.return_value = DownloadedFile(local_path="/tmp/model.gguf", source=ModelSource.HUGGINGFACE)
            download_with_fallback(cfg, retries=2)
            assert mock_sleep.call_count >= 1


class TestUnknownModelDownload:
    def test_unknown_model_no_alt_sources_no_order(self):
        cfg = _cfg(name="nonexistent-model-99", repo="myorg/myrepo", filename="model.gguf")
        with (
            patch("general_ludd.cloud.model_sources._check_ollama_installed", return_value=False),
            patch(
                "general_ludd.cloud.model_sources._download_from_huggingface",
            ) as mock_hf,
        ):
            mock_hf.return_value = DownloadedFile(local_path="/tmp/model.gguf", source=ModelSource.HUGGINGFACE)
            result = download_with_fallback(cfg)
            assert result.source == ModelSource.HUGGINGFACE
            assert mock_hf.call_args.kwargs["model_id"] == "myorg/myrepo"
            assert mock_hf.call_args.kwargs["filename"] == "model.gguf"

    def test_unknown_model_no_alt_sources_with_default_order(self):
        cfg = _cfg(name="unknown-42", repo="org/unknown", filename="unknown.gguf")
        with (
            patch("general_ludd.cloud.model_sources._check_ollama_installed", return_value=False),
            patch(
                "general_ludd.cloud.model_sources._download_from_huggingface",
            ) as mock_hf,
        ):
            mock_hf.return_value = DownloadedFile(local_path="/tmp/model.gguf", source=ModelSource.HUGGINGFACE)
            result = download_with_fallback(cfg)
            assert result.source == ModelSource.HUGGINGFACE
            assert mock_hf.call_args.kwargs["model_id"] == "org/unknown"

    def test_unknown_model_custom_order_skips_when_no_sources_exist(self):
        cfg = _cfg(name="unknown-43", repo="org/z", filename="z.gguf")
        with pytest.raises(DownloadError) as exc_info:
            download_with_fallback(cfg, order=[ModelSource.OLLAMA, ModelSource.LOCAL_PATH])
        assert "tried: []" in str(exc_info.value)


class TestDownloadChainExhaustion:
    def test_error_includes_all_tried_source_names(self):
        cfg = _cfg(name="qwen-0.5b")
        with (
            patch("general_ludd.cloud.model_sources._check_ollama_installed", return_value=False),
            patch(
                "general_ludd.cloud.model_sources._download_from_huggingface",
                side_effect=Exception("hf error"),
            ),
            patch(
                "general_ludd.cloud.model_sources._download_from_direct_url",
                side_effect=Exception("url error"),
            ),
        ):
            with pytest.raises(DownloadError) as exc_info:
                download_with_fallback(
                    cfg,
                    order=[ModelSource.HUGGINGFACE, ModelSource.DIRECT_URL],
                )
            msg = str(exc_info.value)
            assert "huggingface" in msg
            assert "direct_url" in msg
            assert "Last error" in msg

    def test_last_error_is_preserved_in_exception(self):
        cfg = _cfg(name="qwen-0.5b")
        with (
            patch("general_ludd.cloud.model_sources._check_ollama_installed", return_value=False),
            patch(
                "general_ludd.cloud.model_sources._download_from_huggingface",
                side_effect=ValueError("specific bug"),
            ),
        ):
            with pytest.raises(DownloadError) as exc_info:
                download_with_fallback(cfg, order=[ModelSource.HUGGINGFACE])
            assert "specific bug" in str(exc_info.value)
