"""Unit tests for local_model_check health probes."""

from pathlib import Path

import pytest

from general_ludd.health import local_model_check


class TestAsyncImportModule:
    @pytest.mark.asyncio
    async def test_importable_module(self):
        result = await local_model_check._async_import_module("os")
        assert result is True

    @pytest.mark.asyncio
    async def test_missing_module(self):
        result = await local_model_check._async_import_module("definitely_not_a_real_module_12345")
        assert result is False


class TestHasLocalGguf:
    @pytest.mark.asyncio
    async def test_no_cache_dir(self, tmp_path: Path):
        fake = tmp_path / "no_such_cache"
        result = await local_model_check._has_local_gguf(str(fake))
        assert result is False

    @pytest.mark.asyncio
    async def test_finds_gguf(self, tmp_path: Path):
        cache = tmp_path / "models"
        cache.mkdir()
        (cache / "model.gguf").write_bytes(b"data")
        result = await local_model_check._has_local_gguf(str(cache))
        assert result is True


class TestMemoryPressure:
    @pytest.mark.asyncio
    async def test_returns_keys(self):
        result = await local_model_check._memory_pressure()
        assert {"system_total_mb", "system_available_mb", "system_used_pct", "process_rss_mb"} <= set(result)


class TestLocalModelHealthCheck:
    @pytest.mark.asyncio
    async def test_health_check_structure(self):
        result = await local_model_check.local_model_health_check()
        assert "model_exists" in result
        assert "llama_cpp_available" in result
        assert "memory" in result
