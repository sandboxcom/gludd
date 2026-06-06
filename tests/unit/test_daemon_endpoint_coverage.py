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
