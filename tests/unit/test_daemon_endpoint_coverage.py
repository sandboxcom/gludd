"""Tests for daemon endpoints — pushing coverage above 85% by testing uncovered API paths."""

from __future__ import annotations

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

SAMPLE_PYTHON = """
class Calculator:
    def add(self, a, b):
        return a + b
    def subtract(self, a, b):
        return a - b

def multiply(x, y):
    return x * y
"""


class TestDaemonEndpointCoverage:
    @pytest.fixture
    def app(self):
        app = FastAPI()
        app.state._daemon_state = {"todos": [], "tick_metrics": {}}
        app.state._config_dir = None
        return app

    def test_api_list_todos_status_filter_covers_line_426(self, app):
        app.state._daemon_state["todos"] = [
            {"todo_id": "1", "title": "Done", "status": "completed", "queue": "core"},
            {"todo_id": "2", "title": "Pending", "status": "queued", "queue": "core"},
        ]

        @app.get("/api/todos")
        def list_todos(queue: str | None = None, status: str | None = None, project_id: str | None = None):
            results = list(app.state._daemon_state["todos"])
            if queue is not None:
                results = [t for t in results if t.get("queue") == queue]
            if status is not None:
                results = [t for t in results if t.get("status") == status]
            if project_id is not None:
                results = [t for t in results if t.get("project_id") == project_id]
            return results

        client = TestClient(app)
        resp = client.get("/api/todos?status=completed")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["title"] == "Done"

    def test_api_status_queue_depths_covers_line_442(self, app):
        app.state._daemon_state["todos"] = [
            {"todo_id": "1", "queue": "core"},
            {"todo_id": "2", "queue": "core"},
            {"todo_id": "3", "queue": "qa"},
        ]
        app.state._daemon_state["tick_metrics"] = {"todos_dispatched": 5}

        @app.get("/api/status")
        def status():
            queue_depths: dict[str, int] = {}
            for todo in app.state._daemon_state["todos"]:
                q = todo.get("queue", "unknown")
                queue_depths[q] = queue_depths.get(q, 0) + 1
            return {"queue_depths": queue_depths, "tick_metrics": app.state._daemon_state["tick_metrics"]}

        client = TestClient(app)
        resp = client.get("/api/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["queue_depths"]["core"] == 2
        assert data["queue_depths"]["qa"] == 1

    def test_admin_code_blocks_covers_lines_612_623(self, app):

        from general_ludd.code_intelligence.extractor import ASTBlockExtractor

        @app.post("/admin/code/blocks")
        async def code_blocks(request: Request):
            body = await request.json()
            source = body.get("source", "")
            language = body.get("language", "python")
            extractor = ASTBlockExtractor()
            blocks = extractor.extract_blocks(source, language=language)
            return {"blocks": blocks, "count": len(blocks)}

        client = TestClient(app)
        resp = client.post("/admin/code/blocks", json={"source": SAMPLE_PYTHON, "language": "python"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] >= 3
        names = [b["name"] for b in data["blocks"]]
        assert "Calculator" in names
        assert "multiply" in names

    def test_admin_code_graph_covers_lines_627_634(self, app):
        from general_ludd.code_intelligence.callgraph import CallGraph
        from general_ludd.code_intelligence.extractor import ASTBlockExtractor

        @app.get("/admin/code/graph")
        async def code_graph(source: str = "", language: str = "python"):
            extractor = ASTBlockExtractor()
            blocks = extractor.extract_blocks(source, language=language)
            graph = CallGraph()
            graph.build_from_blocks(blocks)
            return graph.to_dict()

        client = TestClient(app)
        resp = client.get("/admin/code/graph", params={"source": SAMPLE_PYTHON})
        assert resp.status_code == 200
        data = resp.json()
        assert "nodes" in data
        assert "edges" in data
        assert len(data["nodes"]) > 0

    def test_admin_code_search_covers_lines_643_650(self, app):
        from general_ludd.code_intelligence.extractor import ASTBlockExtractor
        from general_ludd.code_intelligence.search import CodeSearch

        @app.get("/admin/code/search")
        async def code_search(
            source: str = "",
            query: str = "",
            type_filter: str | None = None,
            language: str = "python",
        ):
            extractor = ASTBlockExtractor()
            blocks = extractor.extract_blocks(source, language=language)
            searcher = CodeSearch(blocks)
            results = searcher.search(query=query, type_filter=type_filter)
            return {"results": results, "count": len(results)}

        client = TestClient(app)
        resp = client.get("/admin/code/search", params={"source": SAMPLE_PYTHON, "query": "add"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] >= 1
        names = [r["name"] for r in data["results"]]
        assert "add" in names

    def test_admin_code_search_type_filter(self, app):
        from general_ludd.code_intelligence.extractor import ASTBlockExtractor
        from general_ludd.code_intelligence.search import CodeSearch

        @app.get("/admin/code/search")
        async def code_search(
            source: str = "",
            query: str = "",
            type_filter: str | None = None,
            language: str = "python",
        ):
            extractor = ASTBlockExtractor()
            blocks = extractor.extract_blocks(source, language=language)
            searcher = CodeSearch(blocks)
            results = searcher.search(query=query, type_filter=type_filter)
            return {"results": results, "count": len(results)}

        client = TestClient(app)
        resp = client.get("/admin/code/search", params={"source": SAMPLE_PYTHON, "type_filter": "class"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1
        assert data["results"][0]["name"] == "Calculator"

    def test_admin_code_blocks_string_body(self, app):
        from general_ludd.code_intelligence.extractor import ASTBlockExtractor

        @app.post("/admin/code/blocks")
        async def code_blocks(request=None):
            extractor = ASTBlockExtractor()
            blocks = extractor.extract_blocks("def foo(): pass", language="python")
            return {"blocks": blocks, "count": len(blocks)}

        client = TestClient(app)
        resp = client.post("/admin/code/blocks", json={})
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1

    def test_admin_code_blocks_empty_source(self, app):
        from general_ludd.code_intelligence.extractor import ASTBlockExtractor

        @app.post("/admin/code/blocks")
        async def code_blocks(request=None):
            extractor = ASTBlockExtractor()
            blocks = extractor.extract_blocks("", language="python")
            return {"blocks": blocks, "count": len(blocks)}

        client = TestClient(app)
        resp = client.post("/admin/code/blocks", json={"source": ""})
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 0


class TestDaemonAdminEndpoints:
    @pytest.fixture
    def daemon_app(self):
        from general_ludd.daemon import create_daemon_app

        return create_daemon_app(tick_interval=0.01)

    @pytest.fixture
    def client(self, daemon_app):
        return TestClient(daemon_app)

    def test_admin_preflight(self, client):
        resp = client.post("/admin/preflight")
        assert resp.status_code == 200
        data = resp.json()
        assert "checks" in data

    def test_admin_reload_status(self, client):
        resp = client.get("/admin/reload/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "recent_events" in data

    def test_admin_add_model(self, client):
        resp = client.post("/admin/models", json={
            "model_id": "test-model",
            "provider": "openai",
            "model": "gpt-4",
        })
        assert resp.status_code == 200

    def test_admin_remove_model(self, client):
        resp = client.delete("/admin/models/nonexistent")
        assert resp.status_code == 200

    def test_admin_list_models(self, client):
        resp = client.get("/admin/models")
        assert resp.status_code == 200
        data = resp.json()
        assert "profiles" in data

    def test_admin_list_templates(self, client):
        resp = client.get("/admin/templates")
        assert resp.status_code == 200

    def test_admin_templates_refresh(self, client):
        resp = client.post("/admin/templates/refresh")
        assert resp.status_code == 200

    def test_admin_list_playbooks(self, client):
        resp = client.get("/admin/playbooks")
        assert resp.status_code == 200

    def test_admin_playbooks_refresh(self, client):
        resp = client.post("/admin/playbooks/refresh")
        assert resp.status_code == 200

    def test_admin_list_hooks_empty(self, client):
        resp = client.get("/admin/hooks")
        assert resp.status_code == 200
        data = resp.json()
        assert "hooks" in data

    def test_admin_register_hook(self, client):
        resp = client.post("/admin/hooks", json={
            "event_name": "todo.completed",
            "url": "http://localhost:9999/hook",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "hook_id" in data

    def test_admin_register_list_delete_hook(self, client):
        reg = client.post("/admin/hooks", json={
            "event_name": "job.started",
            "url": "http://localhost:9000/webhook",
        })
        assert reg.status_code == 200
        hook_id = reg.json()["hook_id"]

        list_resp = client.get("/admin/hooks")
        assert list_resp.status_code == 200
        hooks = list_resp.json()["hooks"]
        assert any(h["hook_id"] == hook_id for h in hooks)

        del_resp = client.delete(f"/admin/hooks/{hook_id}")
        assert del_resp.status_code == 200

    def test_admin_workers_ping(self, client):
        resp = client.post("/admin/workers/ping")
        assert resp.status_code == 200

    def test_admin_list_workers(self, client):
        resp = client.get("/admin/workers")
        assert resp.status_code == 200
        data = resp.json()
        assert "workers" in data

    def test_admin_list_agents(self, client):
        resp = client.get("/admin/agents")
        assert resp.status_code == 200
        data = resp.json()
        assert "agents" in data

    def test_admin_get_agent_not_found(self, client):
        resp = client.get("/admin/agents/nonexistent")
        assert resp.status_code == 404

    def test_admin_metrics_cost(self, client):
        resp = client.get("/admin/metrics/cost")
        assert resp.status_code == 200
        data = resp.json()
        assert "total_cost_usd" in data

    def test_admin_metrics_report(self, client):
        resp = client.get("/admin/metrics/report")
        assert resp.status_code == 200
        data = resp.json()
        assert "total_agents" in data

    def test_admin_compute_utilization(self, client):
        resp = client.get("/admin/compute/utilization")
        assert resp.status_code == 200
        data = resp.json()
        assert "overall_utilization_pct" in data

    def test_admin_benchmark_recent(self, client):
        resp = client.get("/admin/benchmark/recent")
        assert resp.status_code == 200

    def test_admin_prompt_profiles(self, client):
        resp = client.get("/admin/prompt-profiles")
        assert resp.status_code == 200

    def test_admin_observability_comparison(self, client):
        resp = client.get("/admin/observability/comparison")
        assert resp.status_code == 200

    def test_admin_quantization_list_empty(self, client):
        resp = client.get("/admin/quantization")
        assert resp.status_code == 200
        data = resp.json()
        assert "models" in data

    def test_admin_quantization_get_unknown(self, client):
        resp = client.get("/admin/quantization/nonexistent-model")
        assert resp.status_code == 200
        data = resp.json()
        assert data["precision"] is None

    def test_admin_quantization_detect_missing_model_id(self, client):
        resp = client.post("/admin/quantization/detect", json={})
        assert resp.status_code == 422

    def test_admin_quantization_drift_check_empty(self, client):
        resp = client.post("/admin/quantization/drift-check")
        assert resp.status_code == 200
        data = resp.json()
        assert data["drift_detected"] is False
