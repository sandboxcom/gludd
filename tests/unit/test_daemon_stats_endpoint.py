"""Tests for GET /admin/daemon/stats endpoint — PID, request/response counts, memory, uptime."""

from __future__ import annotations

import os
import time

import pytest
from httpx import ASGITransport, AsyncClient

from general_ludd.daemon import create_daemon_app


@pytest.fixture
def app(tmp_path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    return create_daemon_app(config_dir=str(config_dir))


@pytest.fixture
def transport(app):
    return ASGITransport(app=app)


class TestDaemonStatsEndpoint:
    @pytest.mark.asyncio
    async def test_stats_returns_required_fields(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/admin/daemon/stats")
            assert resp.status_code == 200
            data = resp.json()
            assert "pid" in data
            assert "requests_total" in data
            assert "responses_total" in data
            assert "memory_mb" in data
            assert "uptime_s" in data

    @pytest.mark.asyncio
    async def test_stats_pid_matches_process(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/admin/daemon/stats")
            data = resp.json()
            assert data["pid"] == os.getpid()

    @pytest.mark.asyncio
    async def test_stats_request_count_increments(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp1 = await client.get("/admin/daemon/stats")
            data1 = resp1.json()
            resp2 = await client.get("/admin/daemon/stats")
            data2 = resp2.json()
            assert data2["requests_total"] > data1["requests_total"]

    @pytest.mark.asyncio
    async def test_stats_memory_positive(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/admin/daemon/stats")
            data = resp.json()
            assert data["memory_mb"] > 0

    @pytest.mark.asyncio
    async def test_stats_uptime_positive(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/admin/daemon/stats")
            data = resp.json()
            assert data["uptime_s"] >= 0

    @pytest.mark.asyncio
    async def test_stats_uptime_increases(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp1 = await client.get("/admin/daemon/stats")
            data1 = resp1.json()
            time.sleep(0.1)
            resp2 = await client.get("/admin/daemon/stats")
            data2 = resp2.json()
            assert data2["uptime_s"] >= data1["uptime_s"]

    @pytest.mark.asyncio
    async def test_stats_responses_match_requests(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            await client.get("/healthz")
            await client.get("/healthz")
            resp = await client.get("/admin/daemon/stats")
            data = resp.json()
            assert data["requests_total"] >= 3
            assert data["responses_total"] >= 2
