"""Tests for routers/hardware.py."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from general_ludd.hardware.survey import GpuInfo, HardwareInventory
from general_ludd.routers.hardware import can_run_model, register


class TestCanRunModel:
    def test_gpu_vram_sufficient(self):
        gpu = GpuInfo(name="A100", vram_gb=40.0, index=0, backend="nvidia")
        inv = HardwareInventory(gpus=[gpu], total_ram_gb=64.0, disk_free_gb=100.0, cpu_cores=8)
        result = can_run_model(inv, "llama3-70b")
        assert result["can_run"] is True
        assert "GPU VRAM" in str(result["reason"])

    def test_cpu_offload_sufficient(self):
        inv = HardwareInventory(gpus=[], total_ram_gb=32.0, disk_free_gb=100.0, cpu_cores=8)
        result = can_run_model(inv, "mistral-7b")
        assert result["can_run"] is True
        assert result.get("cpu_offload") is True
        assert "CPU offload" in str(result["reason"])

    def test_cannot_run_no_gpu_and_insufficient_ram(self):
        inv = HardwareInventory(gpus=[], total_ram_gb=2.0, disk_free_gb=100.0, cpu_cores=4)
        result = can_run_model(inv, "mixtral-8x7b")
        assert result["can_run"] is False
        assert result["required_vram_gb"] == 48.0
        assert "Need" in str(result["reason"])

    def test_cannot_run_small_gpu_for_big_model(self):
        gpu = GpuInfo(name="T4", vram_gb=4.0, index=0, backend="nvidia")
        inv = HardwareInventory(gpus=[gpu], total_ram_gb=4.0, disk_free_gb=100.0, cpu_cores=4)
        result = can_run_model(inv, "mixtral-8x7b")
        assert result["can_run"] is False

    def test_unknown_model_defaults_to_4gb(self):
        gpu = GpuInfo(name="A100", vram_gb=40.0, index=0, backend="nvidia")
        inv = HardwareInventory(gpus=[gpu], total_ram_gb=64.0, disk_free_gb=100.0, cpu_cores=8)
        result = can_run_model(inv, "nonexistent-model")
        assert result["can_run"] is True
        assert result["required_vram_gb"] == 4.0

    def test_returns_model_in_result(self):
        inv = HardwareInventory(gpus=[], total_ram_gb=32.0, disk_free_gb=100.0, cpu_cores=8)
        result = can_run_model(inv, "gemma2-9b")
        assert result["model"] == "gemma2-9b"

    def test_case_insensitive_model_lookup(self):
        gpu = GpuInfo(name="A100", vram_gb=40.0, index=0, backend="nvidia")
        inv = HardwareInventory(gpus=[gpu], total_ram_gb=64.0, disk_free_gb=100.0, cpu_cores=8)
        result = can_run_model(inv, "LLAMA3-8B")
        assert result["can_run"] is True


class TestHardwareRouter:
    @pytest.fixture
    def app(self) -> FastAPI:
        return FastAPI()

    def test_inventory_returns_503_when_no_inventory(self, app):
        register(app, {})
        client = TestClient(app)
        resp = client.get("/admin/hardware/inventory")
        assert resp.status_code == 503
        assert "not yet available" in resp.json()["detail"]

    def test_inventory_returns_data(self, app):
        gpu = GpuInfo(name="M1 Max", vram_gb=24.0, index=0, backend="metal")
        inv = HardwareInventory(gpus=[gpu], total_ram_gb=32.0, disk_free_gb=100.0, cpu_cores=10)
        register(app, {})
        app.state._hardware_inventory = inv
        client = TestClient(app)
        resp = client.get("/admin/hardware/inventory")
        assert resp.status_code == 200
        data = resp.json()
        assert data["cpu_cores"] == 10
        assert data["total_ram_gb"] == 32.0
        assert len(data["gpus"]) == 1

    def test_model_fit_returns_503_when_no_inventory(self, app):
        register(app, {})
        client = TestClient(app)
        resp = client.get("/admin/hardware/model-fit?model=llama3-8b")
        assert resp.status_code == 503

    def test_model_fit_returns_result(self, app):
        gpu = GpuInfo(name="A100", vram_gb=40.0, index=0, backend="nvidia")
        inv = HardwareInventory(gpus=[gpu], total_ram_gb=64.0, disk_free_gb=100.0, cpu_cores=8)
        register(app, {})
        app.state._hardware_inventory = inv
        client = TestClient(app)
        resp = client.get("/admin/hardware/model-fit?model=llama3-8b")
        assert resp.status_code == 200
        data = resp.json()
        assert data["can_run"] is True
        assert data["model"] == "llama3-8b"

    def test_model_fit_model_too_long(self, app):
        register(app, {})
        inv = HardwareInventory(gpus=[], total_ram_gb=16.0, disk_free_gb=10.0, cpu_cores=4)
        app.state._hardware_inventory = inv
        client = TestClient(app)
        resp = client.get("/admin/hardware/model-fit?model=" + "a" * 65)
        assert resp.status_code == 422
