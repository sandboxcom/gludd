"""Tests for /admin/code/complexity and /admin/code/suggest-model daemon endpoints."""

from __future__ import annotations

import os
import tempfile

from fastapi.testclient import TestClient

SIMPLE_ENDPOINT_CODE = '''
def add(a, b):
    return a + b
'''

COMPLEX_ENDPOINT_CODE = '''
class Processor:
    def run(self, data):
        for item in data:
            if item:
                if item.get("type") == "A":
                    self._handle_a(item)
                elif item.get("type") == "B":
                    self._handle_b(item)
                else:
                    self._handle_default(item)
    def _handle_a(self, item):
        return item
    def _handle_b(self, item):
        return item
    def _handle_default(self, item):
        return item
'''


class TestCodeComplexityEndpoint:
    def test_complexity_simple_file(self) -> None:
        from general_ludd.daemon import create_daemon_app

        app = create_daemon_app()
        client = TestClient(app)

        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write(SIMPLE_ENDPOINT_CODE)
            f.flush()
            path = f.name
        try:
            resp = client.post("/admin/code/complexity", json={"path": path})
            assert resp.status_code == 200
            data = resp.json()
            assert "score" in data
            assert "suggested_task_type" in data
            assert data["score"]["loc"] > 0
            assert data["score"]["function_count"] >= 1
            assert data["suggested_task_type"] in ("feature", "refactor", "bug_fix", "security_fix")
        finally:
            os.unlink(path)

    def test_complexity_complex_file(self) -> None:
        from general_ludd.daemon import create_daemon_app

        app = create_daemon_app()
        client = TestClient(app)

        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write(COMPLEX_ENDPOINT_CODE)
            f.flush()
            path = f.name
        try:
            resp = client.post("/admin/code/complexity", json={"path": path})
            assert resp.status_code == 200
            data = resp.json()
            assert data["score"]["cyclomatic_complexity"] > 3
            assert data["score"]["class_count"] >= 1
        finally:
            os.unlink(path)

    def test_complexity_nonexistent_file(self) -> None:
        from general_ludd.daemon import create_daemon_app

        app = create_daemon_app()
        client = TestClient(app)

        resp = client.post("/admin/code/complexity", json={"path": "/nonexistent/file.py"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["score"]["loc"] == 0


class TestSuggestModelEndpoint:
    def test_suggest_model_simple(self) -> None:
        from general_ludd.daemon import create_daemon_app

        app = create_daemon_app()
        client = TestClient(app)

        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write(SIMPLE_ENDPOINT_CODE)
            f.flush()
            path = f.name
        try:
            resp = client.post("/admin/code/suggest-model", json={"path": path})
            assert resp.status_code == 200
            data = resp.json()
            assert "path" in data
            assert "complexity" in data
            assert "suggested_task_type" in data
            assert "model_recommendation" in data
            assert data["suggested_task_type"] == "feature"
            rec = data["model_recommendation"]
            assert "selected_model_profile_id" in rec
            assert "fallback" in rec
        finally:
            os.unlink(path)

    def test_suggest_model_complex(self) -> None:
        from general_ludd.daemon import create_daemon_app

        app = create_daemon_app()
        client = TestClient(app)

        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write(COMPLEX_ENDPOINT_CODE)
            f.flush()
            path = f.name
        try:
            resp = client.post("/admin/code/suggest-model", json={"path": path})
            assert resp.status_code == 200
            data = resp.json()
            assert data["suggested_task_type"] in ("feature", "refactor", "security_fix", "bug_fix")
            assert data["complexity"]["cyclomatic_complexity"] > 3
        finally:
            os.unlink(path)

    def test_suggest_model_nonexistent_file(self) -> None:
        from general_ludd.daemon import create_daemon_app

        app = create_daemon_app()
        client = TestClient(app)

        resp = client.post("/admin/code/suggest-model", json={"path": "/nonexistent/file.py"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["complexity"]["loc"] == 0
        assert data["model_recommendation"]["fallback"] is True
