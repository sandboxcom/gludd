"""Tests for skills daemon endpoints including remote fetch wiring."""
from __future__ import annotations

import tempfile
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from general_ludd.daemon import create_daemon_app


def _make_test_app(config_dir: str | None = None):
    tmpdir = config_dir or tempfile.mkdtemp()
    return create_daemon_app(tick_interval=0.01, config_dir=tmpdir)


class TestSkillsCatalogEndpoint:
    def test_catalog_endpoint_returns_skills(self):
        client = TestClient(_make_test_app())
        resp = client.get("/admin/skills/catalog")
        assert resp.status_code == 200
        data = resp.json()
        assert "skills" in data
        names = [s["name"] for s in data["skills"]]
        assert "mp-diagnose" in names
        assert "tdd-discipline" in names

    def test_catalog_search_finds_mattpocock(self):
        client = TestClient(_make_test_app())
        resp = client.post("/admin/skills/catalog/search", json={"query": "mattpocock"})
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["results"]) > 0

    def test_catalog_install_skill(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            client = TestClient(_make_test_app(config_dir=tmpdir))
            resp = client.post("/admin/skills/catalog/install", json={"name": "mp-tdd"})
            assert resp.status_code == 200
            assert resp.json()["name"] == "mp-tdd"


class TestSkillsFetchEndpoint:
    def test_fetch_from_url(self):
        skill_content = "---\nname: test-skill\ndescription: A test\n---\nBody.\n"
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = skill_content

        with tempfile.TemporaryDirectory() as tmpdir:
            client = TestClient(_make_test_app(config_dir=tmpdir))
            with patch("httpx.get", return_value=mock_response):
                resp = client.post("/admin/skills/fetch", json={"url": "https://example.com/test.md"})
            assert resp.status_code == 200
            assert resp.json()["url"] == "https://example.com/test.md"

    def test_fetch_missing_url_returns_422(self):
        client = TestClient(_make_test_app())
        resp = client.post("/admin/skills/fetch", json={})
        assert resp.status_code == 422

    def test_fetch_failed_download_returns_404(self):
        mock_response = MagicMock()
        mock_response.status_code = 404

        client = TestClient(_make_test_app())
        with patch("httpx.get", return_value=mock_response):
            resp = client.post("/admin/skills/fetch", json={"url": "https://example.com/nope.md"})
        assert resp.status_code == 404


class TestSkillsFetchGithubEndpoint:
    def test_fetch_github_skill(self):
        skill_content = "---\nname: diagnose\ndescription: Bug fixer\n---\nDebug.\n"
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = skill_content

        with tempfile.TemporaryDirectory() as tmpdir:
            client = TestClient(_make_test_app(config_dir=tmpdir))
            with patch("httpx.get", return_value=mock_response):
                resp = client.post(
                    "/admin/skills/fetch-github",
                    json={"repo": "mattpocock/skills", "path": "skills/engineering/diagnose"},
                )
            assert resp.status_code == 200
            assert resp.json()["name"] == "diagnose"

    def test_fetch_github_missing_params_returns_422(self):
        client = TestClient(_make_test_app())
        resp = client.post("/admin/skills/fetch-github", json={"repo": "mattpocock/skills"})
        assert resp.status_code == 422
