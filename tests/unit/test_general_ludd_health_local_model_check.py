from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from general_ludd.health.local_model_check import (
    DEFAULT_MODEL_CACHE,
    _has_local_gguf,
    _memory_pressure,
    local_model_health_check,
)


class TestHasLocalGguf:
    @pytest.mark.asyncio
    async def test_no_cache_dir_returns_false(self) -> None:
        result = await _has_local_gguf("/nonexistent/path")
        assert result is False

    @pytest.mark.asyncio
    async def test_empty_dir_returns_false(self, tmp_path) -> None:
        result = await _has_local_gguf(str(tmp_path))
        assert result is False

    @pytest.mark.asyncio
    async def test_dir_with_gguf_returns_true(self, tmp_path) -> None:
        gguf = tmp_path / "model.gguf"
        gguf.write_text("mock data")
        result = await _has_local_gguf(str(tmp_path))
        assert result is True

    @pytest.mark.asyncio
    async def test_nested_gguf_returns_true(self, tmp_path) -> None:
        sub = tmp_path / "subdir"
        sub.mkdir()
        (sub / "model.gguf").write_text("data")
        result = await _has_local_gguf(str(tmp_path))
        assert result is True


class TestMemoryPressure:
    @pytest.mark.asyncio
    async def test_returns_dict_with_keys(self) -> None:
        result = await _memory_pressure()
        assert isinstance(result, dict)
        for key in ("system_total_mb", "system_available_mb", "system_used_pct", "process_rss_mb"):
            assert key in result, f"Missing key: {key}"

    @pytest.mark.asyncio
    async def test_values_are_numeric(self) -> None:
        result = await _memory_pressure()
        for value in result.values():
            assert isinstance(value, (int, float))


class TestLocalModelHealthCheck:
    @pytest.mark.asyncio
    async def test_returns_expected_keys(self) -> None:
        with (
            patch(
                "general_ludd.health.local_model_check._has_local_gguf",
                new_callable=AsyncMock,
            ) as mock_gguf,
            patch(
                "general_ludd.health.local_model_check._async_import_module",
                new_callable=AsyncMock,
            ) as mock_import,
            patch(
                "general_ludd.health.local_model_check._memory_pressure",
                new_callable=AsyncMock,
            ) as mock_mem,
        ):
            mock_gguf.return_value = True
            mock_import.return_value = True
            mock_mem.return_value = {"system_total_mb": 16000.0}

            result = await local_model_health_check()
            assert "model_exists" in result
            assert "llama_cpp_available" in result
            assert "memory" in result

    @pytest.mark.asyncio
    async def test_model_exists_true(self) -> None:
        with (
            patch(
                "general_ludd.health.local_model_check._has_local_gguf",
                new_callable=AsyncMock,
            ) as mock_gguf,
            patch(
                "general_ludd.health.local_model_check._async_import_module",
                new_callable=AsyncMock,
            ) as mock_import,
            patch(
                "general_ludd.health.local_model_check._memory_pressure",
                new_callable=AsyncMock,
            ) as mock_mem,
        ):
            mock_gguf.return_value = True
            mock_import.return_value = False
            mock_mem.return_value = {}

            result = await local_model_health_check()
            assert result["model_exists"] is True

    @pytest.mark.asyncio
    async def test_model_exists_false(self) -> None:
        with (
            patch(
                "general_ludd.health.local_model_check._has_local_gguf",
                new_callable=AsyncMock,
            ) as mock_gguf,
            patch(
                "general_ludd.health.local_model_check._async_import_module",
                new_callable=AsyncMock,
            ) as mock_import,
            patch(
                "general_ludd.health.local_model_check._memory_pressure",
                new_callable=AsyncMock,
            ) as mock_mem,
        ):
            mock_gguf.return_value = False
            mock_import.return_value = True
            mock_mem.return_value = {}

            result = await local_model_health_check()
            assert result["model_exists"] is False
            assert result["llama_cpp_available"] is True


class TestDefaultModelCache:
    def test_is_non_empty_string(self) -> None:
        assert isinstance(DEFAULT_MODEL_CACHE, str)
        assert len(DEFAULT_MODEL_CACHE) > 0
        assert "models" in DEFAULT_MODEL_CACHE
