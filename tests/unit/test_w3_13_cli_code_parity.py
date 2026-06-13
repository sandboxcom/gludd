"""W3.13 (M11): CLI code search/graph ↔ /admin/code/* endpoint parity.

Proves:
1. CLI _cmd_code_search hits /admin/code/search (not /admin/code-search)
2. CLI _cmd_code_graph hits /admin/code/graph (not /admin/code-graph)
3. CLI reads file content and sends it as `source` param when given a file path
4. Endpoints return correct results for fixture source code

TDD: write the test first.
"""
from __future__ import annotations

import argparse
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

# Fixture: minimal Python source with a function def for AST extraction
FIXTURE_SOURCE = """\
def hello(name: str) -> str:
    return f"Hello, {name}!"

def add(a: int, b: int) -> int:
    return a + b
"""


class TestCodeSearchEndpointHit:
    """CLI must send requests to /admin/code/search, not /admin/code-search."""

    def test_cmd_code_search_hits_correct_endpoint(self, tmp_path):
        """_cmd_code_search must call /admin/code/search with source param."""
        fixture_file = tmp_path / "test_code.py"
        fixture_file.write_text(FIXTURE_SOURCE)

        captured_url = []
        captured_params = {}

        class FakeResponse:
            status_code = 200
            def json(self):
                return {"results": [{"file": "test_code.py", "line": 1, "text": "def hello"}], "count": 1}

        def fake_get(url, params=None, **kwargs):
            captured_url.append(url)
            captured_params.update(params or {})
            return FakeResponse()

        from general_ludd.cli import _cmd_code_search

        args = argparse.Namespace(
            daemon_url="http://localhost:8000",
            query="hello",
            language="python",
            source=str(fixture_file),
        )

        with patch("httpx.get", side_effect=fake_get):
            _cmd_code_search(args)

        assert len(captured_url) == 1, "Expected exactly one HTTP call"
        url = captured_url[0]
        assert "/admin/code/search" in url, (
            f"CLI hit {url!r} instead of /admin/code/search — wrong endpoint"
        )

    def test_cmd_code_search_sends_file_contents_as_source(self, tmp_path):
        """When --source is a file path, CLI must read it and send contents."""
        fixture_file = tmp_path / "code.py"
        fixture_file.write_text(FIXTURE_SOURCE)

        captured_params = {}

        class FakeResponse:
            status_code = 200
            def json(self):
                return {"results": [], "count": 0}

        def fake_get(url, params=None, **kwargs):
            captured_params.update(params or {})
            return FakeResponse()

        from general_ludd.cli import _cmd_code_search

        args = argparse.Namespace(
            daemon_url="http://localhost:8000",
            query="hello",
            language="python",
            source=str(fixture_file),
        )

        with patch("httpx.get", side_effect=fake_get):
            _cmd_code_search(args)

        source_sent = captured_params.get("source", "")
        assert "def hello" in source_sent, (
            f"CLI sent source={source_sent[:80]!r} instead of file contents. "
            "It must read the file and send its text as the 'source' parameter."
        )


class TestCodeGraphEndpointHit:
    """CLI must send requests to /admin/code/graph, not /admin/code-graph."""

    def test_cmd_code_graph_hits_correct_endpoint(self, tmp_path):
        """_cmd_code_graph must call /admin/code/graph."""
        fixture_file = tmp_path / "graph_code.py"
        fixture_file.write_text(FIXTURE_SOURCE)

        captured_url = []

        class FakeResponse:
            status_code = 200
            def json(self):
                return {"nodes": [], "edges": []}

        def fake_get(url, params=None, **kwargs):
            captured_url.append(url)
            return FakeResponse()

        from general_ludd.cli import _cmd_code_graph

        args = argparse.Namespace(
            daemon_url="http://localhost:8000",
            language="python",
            source=str(fixture_file),
        )

        with patch("httpx.get", side_effect=fake_get):
            _cmd_code_graph(args)

        assert len(captured_url) == 1
        url = captured_url[0]
        assert "/admin/code/graph" in url, (
            f"CLI hit {url!r} instead of /admin/code/graph"
        )

    def test_cmd_code_graph_sends_file_contents_as_source(self, tmp_path):
        """When --source is a file path, CLI must send its contents."""
        fixture_file = tmp_path / "graph_code2.py"
        fixture_file.write_text(FIXTURE_SOURCE)

        captured_params = {}

        class FakeResponse:
            status_code = 200
            def json(self):
                return {"nodes": [], "edges": []}

        def fake_get(url, params=None, **kwargs):
            captured_params.update(params or {})
            return FakeResponse()

        from general_ludd.cli import _cmd_code_graph

        args = argparse.Namespace(
            daemon_url="http://localhost:8000",
            language="python",
            source=str(fixture_file),
        )

        with patch("httpx.get", side_effect=fake_get):
            _cmd_code_graph(args)

        source_sent = captured_params.get("source", "")
        assert "def hello" in source_sent, (
            f"CLI sent source={source_sent[:80]!r} instead of file contents."
        )


class TestCodeEndpointDirectly:
    """The endpoints themselves return valid results for fixture source."""

    def test_code_search_returns_results_for_fixture(self):
        """/admin/code/search returns blocks matching 'hello' in fixture source."""
        with patch(
            "general_ludd.ansible.runner.AnsibleRunnerAdapter",
            return_value=MagicMock(),
        ):
            from general_ludd.daemon import create_daemon_app

            app = create_daemon_app(tick_interval=300.0)

        with TestClient(app) as client:
            resp = client.get(
                "/admin/code/search",
                params={"source": FIXTURE_SOURCE, "query": "hello", "language": "python"},
            )
            assert resp.status_code == 200, f"Got {resp.status_code}: {resp.text}"
            data = resp.json()
            results = data.get("results", [])
            assert isinstance(results, list)
            assert data.get("count") is not None

    def test_code_graph_returns_nodes_for_fixture(self):
        """/admin/code/graph returns nodes for fixture source."""
        with patch(
            "general_ludd.ansible.runner.AnsibleRunnerAdapter",
            return_value=MagicMock(),
        ):
            from general_ludd.daemon import create_daemon_app

            app = create_daemon_app(tick_interval=300.0)

        with TestClient(app) as client:
            resp = client.get(
                "/admin/code/graph",
                params={"source": FIXTURE_SOURCE, "language": "python"},
            )
            assert resp.status_code == 200, f"Got {resp.status_code}: {resp.text}"
            data = resp.json()
            # Graph returns nodes/edges
            assert "nodes" in data or "edges" in data
