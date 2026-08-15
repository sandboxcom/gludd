from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from general_ludd.db.models import Base
from general_ludd.small_models import ModelDownloader

PSK = "test-psk-sm"
AUTH = {"Authorization": f"Bearer {PSK}"}


def _make_dummy_file(cache_dir: str, model_id: str) -> str:
    dirname = "models--" + model_id.replace("/", "--")
    model_dir = Path(cache_dir) / dirname
    model_dir.mkdir(parents=True, exist_ok=True)
    file_path = model_dir / "pytorch_model.bin"
    file_path.write_text("dummy model data\n" * 100)
    return str(file_path)


async def _make_app(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    monkeypatch.setenv("GLUDD_AUTH_PSK", PSK)
    from general_ludd.daemon import create_daemon_app

    app = create_daemon_app(tick_interval=1.0)
    app.state._session_factory = factory

    transport = ASGITransport(app=app)
    client = AsyncClient(transport=transport, base_url="http://test")
    return engine, factory, client, app


class TestSmallModelsDownload:
    @pytest.mark.asyncio
    async def test_download_huggingface(self, monkeypatch):
        engine, _factory, client, app = await _make_app(monkeypatch)
        with tempfile.TemporaryDirectory() as tmpdir:
            monkeypatch.setattr(app.state, "_model_downloader", ModelDownloader(cache_dir=tmpdir))
            dummy_path = _make_dummy_file(tmpdir, "microsoft/phi-2")

            def _fake_download(*_a, **_kw):
                return dummy_path

            monkeypatch.setattr("huggingface_hub.hf_hub_download", _fake_download)
            monkeypatch.setattr("huggingface_hub.snapshot_download", _fake_download)

            try:
                resp = await client.post(
                    "/admin/models/local/download",
                    json={"model_id": "microsoft/phi-2", "source": "huggingface", "filename": "pytorch_model.bin"},
                    headers=AUTH,
                )
                assert resp.status_code == 200, resp.text
                data = resp.json()
                assert data["downloaded"] is True
                assert data["model_id"] == "microsoft/phi-2"
                assert data["source"] == "huggingface"
                assert "profile_key" in data
                assert "local_path" in data
                assert data["local_path"] == dummy_path
                assert data["size_bytes"] > 0
            finally:
                await client.aclose()
                await engine.dispose()

    @pytest.mark.asyncio
    async def test_download_ollama(self, monkeypatch):
        engine, _factory, client, app = await _make_app(monkeypatch)
        with tempfile.TemporaryDirectory() as tmpdir:
            monkeypatch.setattr(app.state, "_model_downloader", ModelDownloader(cache_dir=tmpdir))
            dummy_path = _make_dummy_file(tmpdir, "llama3.2:3b")

            def _fake_snapshot(*_a, **_kw):
                return str(Path(dummy_path).parent)

            monkeypatch.setattr("huggingface_hub.snapshot_download", _fake_snapshot)

            try:
                resp = await client.post(
                    "/admin/models/local/download",
                    json={"model_id": "llama3.2:3b", "source": "ollama"},
                    headers=AUTH,
                )
                assert resp.status_code == 200, resp.text
                data = resp.json()
                assert data["source"] == "ollama"
                assert "local_path" in data
                assert data["local_path"] == _fake_snapshot()
                assert data["size_bytes"] > 0
            finally:
                await client.aclose()
                await engine.dispose()

    @pytest.mark.asyncio
    async def test_download_writes_file_to_disk(self, monkeypatch):
        engine, _factory, client, app = await _make_app(monkeypatch)
        with tempfile.TemporaryDirectory() as tmpdir:
            monkeypatch.setattr(app.state, "_model_downloader", ModelDownloader(cache_dir=tmpdir))
            dummy_path = _make_dummy_file(tmpdir, "test-org/test-model")

            def _fake_download(*_a, **_kw):
                return dummy_path

            monkeypatch.setattr("huggingface_hub.hf_hub_download", _fake_download)
            monkeypatch.setattr("huggingface_hub.snapshot_download", _fake_download)

            try:
                resp = await client.post(
                    "/admin/models/local/download",
                    json={
                        "model_id": "test-org/test-model",
                        "source": "huggingface",
                        "filename": "pytorch_model.bin",
                    },
                    headers=AUTH,
                )
                assert resp.status_code == 200, resp.text
                data = resp.json()
                assert data["downloaded"] is True
                local_path = data["local_path"]
                assert isinstance(local_path, str)
                assert os.path.isfile(local_path), f"Expected file at {local_path} but it does not exist"
                file_size = os.path.getsize(local_path)
                assert file_size > 0
                assert data["size_bytes"] == file_size
            finally:
                await client.aclose()
                await engine.dispose()

    @pytest.mark.asyncio
    async def test_download_missing_model_id_422(self, monkeypatch):
        engine, _factory, client, _app = await _make_app(monkeypatch)
        try:
            resp = await client.post(
                "/admin/models/local/download",
                json={"source": "huggingface"},
                headers=AUTH,
            )
            assert resp.status_code == 422, resp.text
            assert "model_id" in resp.text
        finally:
            await client.aclose()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_download_invalid_source_422(self, monkeypatch):
        engine, _factory, client, _app = await _make_app(monkeypatch)
        try:
            resp = await client.post(
                "/admin/models/local/download",
                json={"model_id": "phi-2", "source": "invalid"},
                headers=AUTH,
            )
            assert resp.status_code == 422, resp.text
        finally:
            await client.aclose()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_download_no_auth_returns_401(self, monkeypatch):
        engine, _factory, client, _app = await _make_app(monkeypatch)
        try:
            resp = await client.post(
                "/admin/models/local/download",
                json={"model_id": "phi-2"},
            )
            assert resp.status_code == 401, resp.text
        finally:
            await client.aclose()
            await engine.dispose()


class TestSmallModelsQuantize:
    @pytest.mark.asyncio
    async def test_quantize_q4(self, monkeypatch):
        engine, _factory, client, _app = await _make_app(monkeypatch)
        with tempfile.TemporaryDirectory() as tmpdir:
            monkeypatch.setenv("GLUDD_MODELS_DIR", tmpdir)
            model_dir = Path(tmpdir) / "phi-2"
            model_dir.mkdir(parents=True, exist_ok=True)
            gguf_path = model_dir / "phi-2-f16.gguf"
            gguf_path.write_text("dummy gguf data\n" * 10)

            monkeypatch.setattr(
                "general_ludd.quantization.quantize.ModelQuantizer.quantize",
                lambda *a, **kw: True,
            )
            try:
                resp = await client.post(
                    "/admin/models/local/quantize",
                    json={"model_id": "phi-2", "method": "q4_k_m"},
                    headers=AUTH,
                )
                assert resp.status_code == 200, resp.text
                data = resp.json()
                assert data["quantized"] is True
                assert data["method"] == "q4_k_m"
                assert "digest" in data
            finally:
                await client.aclose()
                await engine.dispose()

    @pytest.mark.asyncio
    async def test_quantize_q8(self, monkeypatch):
        engine, _factory, client, _app = await _make_app(monkeypatch)
        with tempfile.TemporaryDirectory() as tmpdir:
            monkeypatch.setenv("GLUDD_MODELS_DIR", tmpdir)
            model_dir = Path(tmpdir) / "phi-2"
            model_dir.mkdir(parents=True, exist_ok=True)
            gguf_path = model_dir / "phi-2-f16.gguf"
            gguf_path.write_text("dummy gguf data\n" * 10)

            monkeypatch.setattr(
                "general_ludd.quantization.quantize.ModelQuantizer.quantize",
                lambda *a, **kw: True,
            )
            try:
                resp = await client.post(
                    "/admin/models/local/quantize",
                    json={"model_id": "phi-2", "method": "q8_0"},
                    headers=AUTH,
                )
                assert resp.status_code == 200, resp.text
                assert resp.json()["method"] == "q8_0"
            finally:
                await client.aclose()
                await engine.dispose()

    @pytest.mark.asyncio
    async def test_quantize_missing_model_id_422(self, monkeypatch):
        engine, _factory, client, _app = await _make_app(monkeypatch)
        try:
            resp = await client.post(
                "/admin/models/local/quantize",
                json={"method": "q4_k_m"},
                headers=AUTH,
            )
            assert resp.status_code == 422, resp.text
        finally:
            await client.aclose()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_quantize_invalid_method_422(self, monkeypatch):
        engine, _factory, client, _app = await _make_app(monkeypatch)
        try:
            resp = await client.post(
                "/admin/models/local/quantize",
                json={"model_id": "phi-2", "method": "fp8"},
                headers=AUTH,
            )
            assert resp.status_code == 422, resp.text
        finally:
            await client.aclose()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_quantize_no_auth_returns_401(self, monkeypatch):
        engine, _factory, client, _app = await _make_app(monkeypatch)
        try:
            resp = await client.post(
                "/admin/models/local/quantize",
                json={"model_id": "phi-2", "method": "q4_k_m"},
            )
            assert resp.status_code == 401, resp.text
        finally:
            await client.aclose()
            await engine.dispose()


class TestSmallModelsEvaluate:
    @pytest.mark.asyncio
    async def test_evaluate_all_pass(self, monkeypatch):
        engine, _factory, client, _app = await _make_app(monkeypatch)
        try:
            resp = await client.post(
                "/admin/models/local/evaluate",
                json={
                    "model_id": "phi-2",
                    "task_kind": "context_compaction",
                    "total_cases": 25,
                    "passed_cases": 25,
                },
                headers=AUTH,
            )
            assert resp.status_code == 200, resp.text
            data = resp.json()
            assert data["evaluated"] is True
            assert data["evidence"]["passed"] is True
            assert data["evidence"]["total_cases"] == 25
        finally:
            await client.aclose()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_evaluate_partial_fail(self, monkeypatch):
        engine, _factory, client, _app = await _make_app(monkeypatch)
        try:
            resp = await client.post(
                "/admin/models/local/evaluate",
                json={
                    "model_id": "tinyllama",
                    "task_kind": "bounded_enumeration",
                    "total_cases": 25,
                    "passed_cases": 22,
                },
                headers=AUTH,
            )
            assert resp.status_code == 200, resp.text
            data = resp.json()
            assert data["evidence"]["passed"] is False
            assert data["evidence"]["passed_cases"] == 22
        finally:
            await client.aclose()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_evaluate_missing_model_id_422(self, monkeypatch):
        engine, _factory, client, _app = await _make_app(monkeypatch)
        try:
            resp = await client.post(
                "/admin/models/local/evaluate",
                json={"task_kind": "context_compaction"},
                headers=AUTH,
            )
            assert resp.status_code == 422, resp.text
        finally:
            await client.aclose()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_evaluate_missing_task_kind_422(self, monkeypatch):
        engine, _factory, client, _app = await _make_app(monkeypatch)
        try:
            resp = await client.post(
                "/admin/models/local/evaluate",
                json={"model_id": "phi-2"},
                headers=AUTH,
            )
            assert resp.status_code == 422, resp.text
        finally:
            await client.aclose()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_evaluate_unknown_task_kind_422(self, monkeypatch):
        engine, _factory, client, _app = await _make_app(monkeypatch)
        try:
            resp = await client.post(
                "/admin/models/local/evaluate",
                json={"model_id": "phi-2", "task_kind": "nonexistent"},
                headers=AUTH,
            )
            assert resp.status_code == 422, resp.text
        finally:
            await client.aclose()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_evaluate_no_auth_returns_401(self, monkeypatch):
        engine, _factory, client, _app = await _make_app(monkeypatch)
        try:
            resp = await client.post(
                "/admin/models/local/evaluate",
                json={"model_id": "phi-2", "task_kind": "context_compaction"},
            )
            assert resp.status_code == 401, resp.text
        finally:
            await client.aclose()
            await engine.dispose()


class TestSmallModelsEvidence:
    @pytest.mark.asyncio
    async def test_evidence_for_model(self, monkeypatch):
        engine, _factory, client, _app = await _make_app(monkeypatch)
        try:
            await client.post(
                "/admin/models/local/evaluate",
                json={"model_id": "phi-2", "task_kind": "context_compaction"},
                headers=AUTH,
            )
            resp = await client.get(
                "/admin/models/local/evidence",
                params={"model_id": "phi-2"},
                headers=AUTH,
            )
            assert resp.status_code == 200, resp.text
            data = resp.json()
            assert data["model_id"] == "phi-2"
            assert len(data["evidence"]) == 1
        finally:
            await client.aclose()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_evidence_empty_for_unknown(self, monkeypatch):
        engine, _factory, client, _app = await _make_app(monkeypatch)
        try:
            resp = await client.get(
                "/admin/models/local/evidence",
                params={"model_id": "nonexistent"},
                headers=AUTH,
            )
            assert resp.status_code == 200, resp.text
            assert resp.json()["evidence"] == []
        finally:
            await client.aclose()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_evidence_all(self, monkeypatch):
        engine, _factory, client, _app = await _make_app(monkeypatch)
        try:
            resp = await client.get(
                "/admin/models/local/evidence",
                headers=AUTH,
            )
            assert resp.status_code == 200, resp.text
        finally:
            await client.aclose()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_evidence_no_auth_returns_401(self, monkeypatch):
        engine, _factory, client, _app = await _make_app(monkeypatch)
        try:
            resp = await client.get("/admin/models/local/evidence")
            assert resp.status_code == 401, resp.text
        finally:
            await client.aclose()
            await engine.dispose()


class TestSmallModelsServe:
    @pytest.mark.asyncio
    async def test_serve_creates_server(self, monkeypatch):
        engine, _factory, client, _app = await _make_app(monkeypatch)
        try:
            resp = await client.post(
                "/admin/models/local/serve",
                json={"model_id": "phi-2", "port": 9090, "startup_timeout": 0},
                headers=AUTH,
            )
            assert resp.status_code == 200, resp.text
            data = resp.json()
            assert "server_id" in data
            assert data["model_id"] == "phi-2"
            assert data["status"] == "stopped"
        finally:
            await client.aclose()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_serve_missing_model_id_422(self, monkeypatch):
        engine, _factory, client, _app = await _make_app(monkeypatch)
        try:
            resp = await client.post(
                "/admin/models/local/serve",
                json={"port": 9090},
                headers=AUTH,
            )
            assert resp.status_code == 422, resp.text
        finally:
            await client.aclose()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_serve_invalid_port_422(self, monkeypatch):
        engine, _factory, client, _app = await _make_app(monkeypatch)
        try:
            resp = await client.post(
                "/admin/models/local/serve",
                json={"model_id": "phi-2", "port": 80},
                headers=AUTH,
            )
            assert resp.status_code == 422, resp.text
        finally:
            await client.aclose()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_serve_no_auth_returns_401(self, monkeypatch):
        engine, _factory, client, _app = await _make_app(monkeypatch)
        try:
            resp = await client.post(
                "/admin/models/local/serve",
                json={"model_id": "phi-2", "port": 9090},
            )
            assert resp.status_code == 401, resp.text
        finally:
            await client.aclose()
            await engine.dispose()


class TestSmallModelsStatus:
    @pytest.mark.asyncio
    async def test_status_active(self, monkeypatch):
        engine, _factory, client, _app = await _make_app(monkeypatch)
        try:
            await client.post(
                "/admin/models/local/serve",
                json={"model_id": "phi-2", "port": 9090, "startup_timeout": 0},
                headers=AUTH,
            )
            resp = await client.get("/admin/models/local/status", headers=AUTH)
            assert resp.status_code == 200, resp.text
            data = resp.json()
            assert data["total"] == 1
            assert len(data["servers"]) == 1
            assert data["servers"][0]["model_name"] == "phi-2"
        finally:
            await client.aclose()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_status_empty(self, monkeypatch):
        engine, _factory, client, _app = await _make_app(monkeypatch)
        try:
            resp = await client.get("/admin/models/local/status", headers=AUTH)
            assert resp.status_code == 200, resp.text
            assert resp.json()["total"] == 0
        finally:
            await client.aclose()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_status_no_auth_returns_401(self, monkeypatch):
        engine, _factory, client, _app = await _make_app(monkeypatch)
        try:
            resp = await client.get("/admin/models/local/status")
            assert resp.status_code == 401, resp.text
        finally:
            await client.aclose()
            await engine.dispose()


class TestSmallModelsRollout:
    @pytest.mark.asyncio
    async def test_rollout_with_evidence(self, monkeypatch):
        engine, _factory, client, _app = await _make_app(monkeypatch)
        try:
            await client.post(
                "/admin/models/local/evaluate",
                json={"model_id": "phi-2", "task_kind": "context_compaction"},
                headers=AUTH,
            )
            resp = await client.post(
                "/admin/models/rollout",
                json={
                    "model_id": "phi-2",
                    "target": "canary",
                    "task_kind": "context_compaction",
                },
                headers=AUTH,
            )
            assert resp.status_code == 200, resp.text
            data = resp.json()
            assert data["status"] == "initiated"
            assert data["target"] == "canary"
            assert data["has_evidence"] is True
            assert "rollout_id" in data
        finally:
            await client.aclose()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_rollout_without_evidence_412(self, monkeypatch):
        engine, _factory, client, _app = await _make_app(monkeypatch)
        try:
            resp = await client.post(
                "/admin/models/rollout",
                json={
                    "model_id": "phi-2",
                    "target": "full",
                    "task_kind": "context_compaction",
                },
                headers=AUTH,
            )
            assert resp.status_code == 412, resp.text
            assert "evidence" in resp.text
        finally:
            await client.aclose()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_rollout_without_task_kind_allowed(self, monkeypatch):
        engine, _factory, client, _app = await _make_app(monkeypatch)
        try:
            resp = await client.post(
                "/admin/models/rollout",
                json={"model_id": "phi-2", "target": "local"},
                headers=AUTH,
            )
            assert resp.status_code == 200, resp.text
            data = resp.json()
            assert data["has_evidence"] is False
        finally:
            await client.aclose()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_rollout_missing_model_id_422(self, monkeypatch):
        engine, _factory, client, _app = await _make_app(monkeypatch)
        try:
            resp = await client.post(
                "/admin/models/rollout",
                json={"target": "local"},
                headers=AUTH,
            )
            assert resp.status_code == 422, resp.text
        finally:
            await client.aclose()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_rollout_invalid_target_422(self, monkeypatch):
        engine, _factory, client, _app = await _make_app(monkeypatch)
        try:
            resp = await client.post(
                "/admin/models/rollout",
                json={"model_id": "phi-2", "target": "production"},
                headers=AUTH,
            )
            assert resp.status_code == 422, resp.text
        finally:
            await client.aclose()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_rollout_no_auth_returns_401(self, monkeypatch):
        engine, _factory, client, _app = await _make_app(monkeypatch)
        try:
            resp = await client.post(
                "/admin/models/rollout",
                json={"model_id": "phi-2", "target": "local"},
            )
            assert resp.status_code == 401, resp.text
        finally:
            await client.aclose()
            await engine.dispose()


class TestSmallModelsRecommend:
    @pytest.mark.asyncio
    async def test_recommend_ranked_by_pass_ratio(self, monkeypatch):
        engine, _factory, client, _app = await _make_app(monkeypatch)
        try:
            await client.post(
                "/admin/models/local/evaluate",
                json={"model_id": "phi-2", "task_kind": "context_compaction", "total_cases": 25, "passed_cases": 25},
                headers=AUTH,
            )
            await client.post(
                "/admin/models/local/evaluate",
                json={
                    "model_id": "tinyllama",
                    "task_kind": "context_compaction",
                    "total_cases": 25,
                    "passed_cases": 20,
                },
                headers=AUTH,
            )
            await client.post(
                "/admin/models/local/evaluate",
                json={
                    "model_id": "llama3.2:3b",
                    "task_kind": "bounded_enumeration",
                    "total_cases": 25,
                    "passed_cases": 25,
                },
                headers=AUTH,
            )
            resp = await client.get("/admin/models/recommend", params={"task": "context_compaction"}, headers=AUTH)
            assert resp.status_code == 200, resp.text
            data = resp.json()
            assert data["task"] == "context_compaction"
            assert data["total"] == 2
            recs = data["recommendations"]
            assert len(recs) == 2
            assert recs[0]["model_id"] == "phi-2"
            assert recs[0]["passed"] is True
            assert recs[0]["pass_ratio"] == 1.0
            assert "cost" in recs[0]
            assert "inference_usd_per_hour" in recs[0]["cost"]
            assert recs[1]["model_id"] == "tinyllama"
            assert recs[1]["passed"] is False
        finally:
            await client.aclose()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_recommend_empty_for_unknown_task(self, monkeypatch):
        engine, _factory, client, _app = await _make_app(monkeypatch)
        try:
            resp = await client.get("/admin/models/recommend", params={"task": "unknown_task"}, headers=AUTH)
            assert resp.status_code == 200, resp.text
            data = resp.json()
            assert data["task"] == "unknown_task"
            assert data["recommendations"] == []
            assert data["total"] == 0
        finally:
            await client.aclose()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_recommend_no_auth_returns_401(self, monkeypatch):
        engine, _factory, client, _app = await _make_app(monkeypatch)
        try:
            resp = await client.get("/admin/models/recommend", params={"task": "context_compaction"})
            assert resp.status_code == 401, resp.text
        finally:
            await client.aclose()
            await engine.dispose()


class TestSmallModelsTasks:
    @pytest.mark.asyncio
    async def test_tasks_for_model(self, monkeypatch):
        engine, _factory, client, _app = await _make_app(monkeypatch)
        try:
            await client.post(
                "/admin/models/local/evaluate",
                json={"model_id": "phi-2", "task_kind": "context_compaction", "total_cases": 25, "passed_cases": 25},
                headers=AUTH,
            )
            await client.post(
                "/admin/models/local/evaluate",
                json={"model_id": "phi-2", "task_kind": "bounded_enumeration", "total_cases": 25, "passed_cases": 22},
                headers=AUTH,
            )
            resp = await client.get("/admin/models/tasks", params={"model": "phi-2"}, headers=AUTH)
            assert resp.status_code == 200, resp.text
            data = resp.json()
            assert data["model_id"] == "phi-2"
            assert data["total"] == 2
            task_kinds = {t["task_kind"] for t in data["tasks"]}
            assert task_kinds == {"bounded_enumeration", "context_compaction"}
            for t in data["tasks"]:
                assert "passed_cases" in t
                assert "total_cases" in t
                assert "passed" in t
                assert "role" in t
        finally:
            await client.aclose()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_tasks_empty_for_unknown_model(self, monkeypatch):
        engine, _factory, client, _app = await _make_app(monkeypatch)
        try:
            resp = await client.get("/admin/models/tasks", params={"model": "nonexistent"}, headers=AUTH)
            assert resp.status_code == 200, resp.text
            data = resp.json()
            assert data["model_id"] == "nonexistent"
            assert data["tasks"] == []
            assert data["total"] == 0
        finally:
            await client.aclose()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_tasks_deduplicates_duplicate_evidence(self, monkeypatch):
        engine, _factory, client, _app = await _make_app(monkeypatch)
        try:
            await client.post(
                "/admin/models/local/evaluate",
                json={"model_id": "phi-2", "task_kind": "context_compaction", "total_cases": 25, "passed_cases": 25},
                headers=AUTH,
            )
            await client.post(
                "/admin/models/local/evaluate",
                json={"model_id": "phi-2", "task_kind": "context_compaction", "total_cases": 30, "passed_cases": 28},
                headers=AUTH,
            )
            resp = await client.get("/admin/models/tasks", params={"model": "phi-2"}, headers=AUTH)
            assert resp.status_code == 200, resp.text
            data = resp.json()
            assert data["total"] == 1
        finally:
            await client.aclose()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_tasks_no_auth_returns_401(self, monkeypatch):
        engine, _factory, client, _app = await _make_app(monkeypatch)
        try:
            resp = await client.get("/admin/models/tasks", params={"model": "phi-2"})
            assert resp.status_code == 401, resp.text
        finally:
            await client.aclose()
            await engine.dispose()


class TestSmallModelsCost:
    @pytest.mark.asyncio
    async def test_cost_endpoint_returns_estimates(self, monkeypatch):
        engine, _factory, client, _app = await _make_app(monkeypatch)
        try:
            resp = await client.get(
                "/admin/models/cost",
                params={"model": "phi-2"},
                headers=AUTH,
            )
            assert resp.status_code == 200, resp.text
            data = resp.json()
            assert data["model_id"] == "phi-2"
            assert "inference" in data
            assert "download" in data
            assert "quantize" in data
            assert "off_peak" in data
            assert "scheduling" in data
            assert "estimated_usd_per_hour" in data["inference"]
            assert "size_gb" in data["download"]
            assert "estimated_cost_usd" in data["quantize"]
            assert isinstance(data["off_peak"]["is_off_peak_now"], bool)
        finally:
            await client.aclose()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_cost_endpoint_no_auth_returns_401(self, monkeypatch):
        engine, _factory, client, _app = await _make_app(monkeypatch)
        try:
            resp = await client.get("/admin/models/cost", params={"model": "phi-2"})
            assert resp.status_code == 401, resp.text
        finally:
            await client.aclose()
            await engine.dispose()


class TestSmallModelsBenchmark:
    @pytest.mark.asyncio
    async def test_benchmark_runs_evaluation_and_stores_evidence(self, monkeypatch):
        mock_results = {
            "results": {
                "mmlu": {"acc,none": 0.55, "acc_norm,none": 0.57},
                "hellaswag": {"acc,none": 0.72, "acc_norm,none": 0.74},
            }
        }
        mock_lm_eval = MagicMock()
        mock_lm_eval.simple_evaluate = MagicMock(return_value=mock_results)

        monkeypatch.setitem(sys.modules, "lm_eval", mock_lm_eval)
        monkeypatch.setattr(
            "general_ludd.small_models.lm_eval_runner._try_import_lm_eval",
            lambda: True,
        )

        engine, _factory, client, _app = await _make_app(monkeypatch)
        try:
            resp = await client.post(
                "/admin/models/local/evaluate",
                json={
                    "model_id": "gpt2",
                    "benchmark": True,
                    "tasks": ["mmlu", "hellaswag"],
                    "limit": 10,
                },
                headers=AUTH,
            )
            assert resp.status_code == 200, resp.text
            data = resp.json()
            assert data["evaluated"] is True
            assert data["benchmark"] is True
            assert data["model_id"] == "gpt2"
            assert data["tasks_run"] == ["mmlu", "hellaswag"]
            assert data["scores"] == {"mmlu": 0.55, "hellaswag": 0.72}
            assert len(data["evidence"]) == 2
            for entry in data["evidence"]:
                assert entry["collection"] == "general_ludd.agent"
                assert entry["suite_id"] == "lm-eval-runner"
                assert "evidence_digest" in entry
                assert entry["task_kind"] in ("mmlu", "hellaswag")
                if entry["task_kind"] == "mmlu":
                    assert entry["passed_cases"] == 1
                else:
                    assert entry["passed_cases"] == 1
        finally:
            del sys.modules["lm_eval"]
            await client.aclose()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_benchmark_defaults_tasks_when_not_specified(self, monkeypatch):
        mock_results = {
            "results": {
                "mmlu": {"acc,none": 0.55},
                "gsm8k": {"acc,none": 0.20},
                "hellaswag": {"acc,none": 0.72},
                "arc_easy": {"acc,none": 0.60},
                "arc_challenge": {"acc,none": 0.40},
                "truthfulqa_mc2": {"acc,none": 0.35},
            }
        }
        mock_lm_eval = MagicMock()
        mock_lm_eval.simple_evaluate = MagicMock(return_value=mock_results)

        monkeypatch.setitem(sys.modules, "lm_eval", mock_lm_eval)
        monkeypatch.setattr(
            "general_ludd.small_models.lm_eval_runner._try_import_lm_eval",
            lambda: True,
        )

        engine, _factory, client, _app = await _make_app(monkeypatch)
        try:
            resp = await client.post(
                "/admin/models/local/evaluate",
                json={"model_id": "gpt2", "benchmark": True},
                headers=AUTH,
            )
            assert resp.status_code == 200, resp.text
            data = resp.json()
            assert len(data["evidence"]) == 6
        finally:
            del sys.modules["lm_eval"]
            await client.aclose()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_benchmark_not_available_returns_empty(self, monkeypatch):
        engine, _factory, client, _app = await _make_app(monkeypatch)
        try:
            resp = await client.post(
                "/admin/models/local/evaluate",
                json={"model_id": "gpt2", "benchmark": True},
                headers=AUTH,
            )
            assert resp.status_code == 200, resp.text
            data = resp.json()
            assert data["evaluated"] is True
            assert data["scores"] == {}
            assert data["evidence"] == []
        finally:
            await client.aclose()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_benchmark_stores_results_in_capability_store(self, monkeypatch):
        mock_results = {"results": {"mmlu": {"acc,none": 0.55}}}
        mock_lm_eval = MagicMock()
        mock_lm_eval.simple_evaluate = MagicMock(return_value=mock_results)

        monkeypatch.setitem(sys.modules, "lm_eval", mock_lm_eval)
        monkeypatch.setattr(
            "general_ludd.small_models.lm_eval_runner._try_import_lm_eval",
            lambda: True,
        )

        engine, _factory, client, _app = await _make_app(monkeypatch)
        try:
            await client.post(
                "/admin/models/local/evaluate",
                json={"model_id": "gpt2", "benchmark": True, "tasks": ["mmlu"]},
                headers=AUTH,
            )

            resp = await client.get(
                "/admin/models/local/evidence",
                params={"model_id": "gpt2"},
                headers=AUTH,
            )
            assert resp.status_code == 200
            data = resp.json()
            assert len(data["evidence"]) == 1
            assert data["evidence"][0]["suite_id"] == "lm-eval-runner"
        finally:
            del sys.modules["lm_eval"]
            await client.aclose()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_benchmark_no_auth_returns_401(self, monkeypatch):
        engine, _factory, client, _app = await _make_app(monkeypatch)
        try:
            resp = await client.post(
                "/admin/models/local/evaluate",
                json={"model_id": "gpt2", "benchmark": True},
            )
            assert resp.status_code == 401, resp.text
        finally:
            await client.aclose()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_benchmark_passed_and_failed_evidence(self, monkeypatch):
        mock_results = {
            "results": {
                "mmlu": {"acc,none": 0.25},
                "hellaswag": {"acc,none": 0.80},
            }
        }
        mock_lm_eval = MagicMock()
        mock_lm_eval.simple_evaluate = MagicMock(return_value=mock_results)

        monkeypatch.setitem(sys.modules, "lm_eval", mock_lm_eval)
        monkeypatch.setattr(
            "general_ludd.small_models.lm_eval_runner._try_import_lm_eval",
            lambda: True,
        )

        engine, _factory, client, _app = await _make_app(monkeypatch)
        try:
            resp = await client.post(
                "/admin/models/local/evaluate",
                json={
                    "model_id": "gpt2",
                    "benchmark": True,
                    "tasks": ["mmlu", "hellaswag"],
                },
                headers=AUTH,
            )
            assert resp.status_code == 200, resp.text
            data = resp.json()
            for entry in data["evidence"]:
                if entry["task_kind"] == "mmlu":
                    assert entry["passed_cases"] == 0
                    assert entry["passed"] is False
                else:
                    assert entry["passed_cases"] == 1
                    assert entry["passed"] is True
        finally:
            del sys.modules["lm_eval"]
            await client.aclose()
            await engine.dispose()
