"""E2E test: download GGUF model, verify hash, run inference, clean up.

Downloads a small GGUF model (Qwen2.5-0.5B Q4_K_M, ~395 MB) to a temp dir,
verifies the SHA256 hash against KnownModels, optionally runs a single
llama-cpp-python inference, then cleans up.

Skips when HF_TOKEN is not set, the network is unreachable, or
huggingface_hub / llama-cpp-python are not installed.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

import pytest

from general_ludd.small_models.download import ModelDownloader
from general_ludd.small_models.model_hash_db import KnownModels, _sha256_file


def _has_hf_token() -> bool:
    return bool(os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN"))


def _has_huggingface_hub() -> bool:
    try:
        import huggingface_hub  # noqa: F401

        return True
    except ImportError:
        return False


def _has_network() -> bool:
    try:
        import urllib.request

        urllib.request.urlopen("https://huggingface.co", timeout=5)
        return True
    except Exception:
        return False


def _has_llama_cpp() -> bool:
    try:
        import llama_cpp  # noqa: F401

        return True
    except ImportError:
        return False


def _combined_skip_reason() -> str | None:
    missing: list[str] = []
    if not _has_huggingface_hub():
        missing.append("huggingface_hub not installed")
    if not _has_hf_token():
        missing.append("HF_TOKEN not set")
    if not _has_network():
        missing.append("network unreachable")
    return "; ".join(missing) if missing else None


_SKIP_REASON = _combined_skip_reason()
_MODEL_ID = "Qwen/Qwen2.5-0.5B-GGUF"
_MODEL_FILE = "qwen2.5-0.5b-q4_k_m.gguf"


class TestGGUFDownloadE2E:
    @pytest.fixture(autouse=True)
    def _temp_dir(self, monkeypatch):
        self._tmpdir = tempfile.mkdtemp(prefix="gludd-test-gguf-")
        monkeypatch.setenv("HF_HUB_CACHE", self._tmpdir)
        yield
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    # ------------------------------------------------------------------
    # download + hash verification
    # ------------------------------------------------------------------

    @pytest.mark.e2e
    @pytest.mark.skipif(_SKIP_REASON is not None, reason=_SKIP_REASON or "")
    def test_download_gguf_and_verify_hash(self):
        """Download the GGUF file and verify its SHA256 against KnownModels."""
        downloader = ModelDownloader(cache_dir=self._tmpdir, timeout=120.0)
        result = downloader.download_gguf(
            model_id=_MODEL_ID,
            filename=_MODEL_FILE,
        )

        assert result.model_id == _MODEL_ID
        assert result.filename == _MODEL_FILE
        assert result.source.value == "gguf"
        assert Path(result.local_path).is_file()
        assert Path(result.local_path).stat().st_size > 0

        actual_sha = _sha256_file(result.local_path)
        known = KnownModels.get(_MODEL_ID)
        assert known is not None, f"No KnownModels entry for {_MODEL_ID}"

        expected_hash: str | None = None
        for fh in known:
            if fh.filename == _MODEL_FILE:
                expected_hash = fh.sha256
                break

        assert expected_hash is not None, f"No known hash for {_MODEL_FILE} in {_MODEL_ID}"
        assert actual_sha == expected_hash, f"Hash mismatch: expected {expected_hash[:16]}..., got {actual_sha[:16]}..."

    # ------------------------------------------------------------------
    # download + llama-cpp-python inference
    # ------------------------------------------------------------------

    @pytest.mark.e2e
    @pytest.mark.skipif(not _has_llama_cpp(), reason="llama-cpp-python not installed")
    @pytest.mark.skipif(_SKIP_REASON is not None, reason=_SKIP_REASON or "")
    def test_download_and_infer_with_llama_cpp(self):
        """Download GGUF, load with llama-cpp-python, run inference, verify output."""
        from llama_cpp import Llama

        downloader = ModelDownloader(cache_dir=self._tmpdir, timeout=120.0)
        result = downloader.download_gguf(
            model_id=_MODEL_ID,
            filename=_MODEL_FILE,
        )

        llm = Llama(
            model_path=result.local_path,
            n_ctx=128,
            n_threads=1,
            verbose=False,
        )

        output = llm("Q: What is 2+2?\nA:", max_tokens=16, echo=False, temperature=0)
        assert isinstance(output, dict)
        choices = output.get("choices", [])
        assert len(choices) > 0
        text = choices[0].get("text", "")
        assert isinstance(text, str) and len(text.strip()) > 0

    # ------------------------------------------------------------------
    # cleanup: temp dir is removed after this test class
    # ------------------------------------------------------------------

    @pytest.mark.e2e
    @pytest.mark.skipif(_SKIP_REASON is not None, reason=_SKIP_REASON or "")
    def test_temp_dir_exists_during_download(self):
        """Sanity: temp dir exists and files are written there during download."""
        downloader = ModelDownloader(cache_dir=self._tmpdir, timeout=120.0)
        result = downloader.download_gguf(
            model_id=_MODEL_ID,
            filename=_MODEL_FILE,
        )

        assert Path(self._tmpdir).is_dir()
        assert Path(result.local_path).is_file()
        assert self._tmpdir in str(Path(result.local_path))

    @pytest.mark.e2e
    def test_temp_dir_cleaned_up_after_test(self):
        """Sanity: temp dir is cleaned between fixtures. This test does NOT download."""
        d = tempfile.mkdtemp(prefix="gludd-test-gguf-cleanup-")
        assert Path(d).is_dir()
        shutil.rmtree(d, ignore_errors=True)
        assert not Path(d).exists()
