"""Deep tests for skills router endpoints — SSRF guard, error paths, filters."""

from __future__ import annotations

import os
import tempfile
from unittest.mock import MagicMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from general_ludd.daemon import create_daemon_app


def _make_test_app(config_dir: str | None = None):
    tmpdir = config_dir or tempfile.mkdtemp()
    with patch.dict(os.environ, {"GLUDD_ALLOW_NO_AUTH": "1"}):
        return create_daemon_app(tick_interval=0.01, config_dir=tmpdir)


class TestSkillsCatalogSearchFilters:
    def test_search_by_tags_returns_only_matching(self):
        client = TestClient(_make_test_app())
        resp = client.post(
            "/admin/skills/catalog/search",
            json={"tags": ["security"], "limit": 50},
        )
        assert resp.status_code == 200
        results = resp.json()["results"]
        for r in results:
            assert "security" in r["tags"]
        assert len(results) >= 1

    def test_search_by_category_returns_only_matching(self):
        client = TestClient(_make_test_app())
        resp = client.post(
            "/admin/skills/catalog/search",
            json={"category": "engineering", "limit": 50},
        )
        assert resp.status_code == 200
        results = resp.json()["results"]
        for r in results:
            assert r["category"] == "engineering"
        assert len(results) >= 1

    def test_search_by_query_and_tags_intersection(self):
        client = TestClient(_make_test_app())
        resp = client.post(
            "/admin/skills/catalog/search",
            json={"query": "tdd", "tags": ["testing"], "limit": 50},
        )
        assert resp.status_code == 200
        names = [r["name"] for r in resp.json()["results"]]
        assert any("tdd" in n.lower() for n in names)

    def test_search_limit_respected(self):
        client = TestClient(_make_test_app())
        resp = client.post(
            "/admin/skills/catalog/search",
            json={"limit": 2},
        )
        assert resp.status_code == 200
        assert len(resp.json()["results"]) <= 2

    def test_search_empty_query_returns_results(self):
        client = TestClient(_make_test_app())
        resp = client.post("/admin/skills/catalog/search", json={})
        assert resp.status_code == 200
        results = resp.json()["results"]
        assert len(results) >= 1

    def test_search_no_matching_query_returns_empty(self):
        client = TestClient(_make_test_app())
        resp = client.post(
            "/admin/skills/catalog/search",
            json={"query": "zzz_nonexistent_xyzzy_999"},
        )
        assert resp.status_code == 200
        assert resp.json()["results"] == []

    def test_search_no_matching_tags_returns_empty(self):
        client = TestClient(_make_test_app())
        resp = client.post(
            "/admin/skills/catalog/search",
            json={"tags": ["nonexistent_tag_xyz"]},
        )
        assert resp.status_code == 200
        assert resp.json()["results"] == []

    def test_search_paginates_with_limit_one(self):
        client = TestClient(_make_test_app())
        resp = client.post(
            "/admin/skills/catalog/search",
            json={"limit": 1},
        )
        assert resp.status_code == 200
        assert len(resp.json()["results"]) <= 1


class TestSkillsCatalogInstallErrors:
    def test_install_unknown_skill_returns_404(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            client = TestClient(_make_test_app(config_dir=tmpdir))
            resp = client.post(
                "/admin/skills/catalog/install",
                json={"name": "nonexistent_skill_xyzzy"},
            )
            assert resp.status_code == 404
            assert "not found" in resp.json()["detail"].lower()

    def test_install_empty_name(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            client = TestClient(_make_test_app(config_dir=tmpdir))
            resp = client.post("/admin/skills/catalog/install", json={"name": ""})
            assert resp.status_code == 404


class TestSkillsCatalogEndpoint:
    def test_catalog_endpoint_structure(self):
        client = TestClient(_make_test_app())
        resp = client.get("/admin/skills/catalog")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data["skills"], list)
        for s in data["skills"]:
            assert "name" in s
            assert "description" in s
            assert "source" in s
            assert "tags" in s
            assert "category" in s

    def test_catalog_endpoint_respects_hidden_limit_of_100(self):
        client = TestClient(_make_test_app())
        resp = client.get("/admin/skills/catalog")
        assert resp.status_code == 200
        assert len(resp.json()["skills"]) <= 100


class TestSkillsFetchSSRFGuard:
    def test_fetch_http_url_rejected_with_422(self):
        client = TestClient(_make_test_app())
        resp = client.post("/admin/skills/fetch", json={"url": "http://example.com/skill.md"})
        assert resp.status_code == 422
        assert "ssrf" in resp.json()["detail"].lower()

    def test_fetch_localhost_url_rejected_with_422(self):
        client = TestClient(_make_test_app())
        resp = client.post("/admin/skills/fetch", json={"url": "https://localhost/skill.md"})
        assert resp.status_code == 422
        assert "ssrf" in resp.json()["detail"].lower()

    def test_fetch_127_0_0_1_rejected_with_422(self):
        client = TestClient(_make_test_app())
        resp = client.post("/admin/skills/fetch", json={"url": "https://127.0.0.1/skill.md"})
        assert resp.status_code == 422
        assert "ssrf" in resp.json()["detail"].lower()

    def test_fetch_10_dot_private_ip_rejected_with_422(self):
        client = TestClient(_make_test_app())
        resp = client.post("/admin/skills/fetch", json={"url": "https://10.0.0.1/skill.md"})
        assert resp.status_code == 422
        assert "ssrf" in resp.json()["detail"].lower()

    def test_fetch_192_168_private_ip_rejected_with_422(self):
        client = TestClient(_make_test_app())
        resp = client.post("/admin/skills/fetch", json={"url": "https://192.168.1.1/skill.md"})
        assert resp.status_code == 422
        assert "ssrf" in resp.json()["detail"].lower()

    def test_fetch_empty_url_rejected_with_422(self):
        client = TestClient(_make_test_app())
        resp = client.post("/admin/skills/fetch", json={"url": ""})
        assert resp.status_code == 422
        assert "url required" in resp.json()["detail"].lower()

    def test_fetch_bare_hostname_ssrf_guard_rejects(self):
        client = TestClient(_make_test_app())
        resp = client.post("/admin/skills/fetch", json={"url": "https://metadata.google.internal/"})
        assert resp.status_code == 422


class TestSkillsFetchNetworkError:
    def test_fetch_transport_error_returns_404(self):
        client = TestClient(_make_test_app())
        with patch("httpx.get", side_effect=httpx.HTTPError("network error")):
            resp = client.post("/admin/skills/fetch", json={"url": "https://example.com/skill.md"})
        assert resp.status_code == 404

    def test_fetch_non_200_status_returns_404(self):
        mock_response = MagicMock()
        mock_response.status_code = 500
        client = TestClient(_make_test_app())
        with patch("httpx.get", return_value=mock_response):
            resp = client.post("/admin/skills/fetch", json={"url": "https://example.com/skill.md"})
        assert resp.status_code == 404


class TestSkillsFetchGithubErrors:
    def test_fetch_github_invalid_repo_no_slash_returns_422(self):
        client = TestClient(_make_test_app())
        resp = client.post(
            "/admin/skills/fetch-github",
            json={"repo": "invalid_repo_no_slash", "path": "some/skill"},
        )
        assert resp.status_code == 422

    def test_fetch_github_empty_path_returns_422(self):
        client = TestClient(_make_test_app())
        resp = client.post(
            "/admin/skills/fetch-github",
            json={"repo": "owner/repo", "path": ""},
        )
        assert resp.status_code == 422

    def test_fetch_github_empty_repo_returns_422(self):
        client = TestClient(_make_test_app())
        resp = client.post(
            "/admin/skills/fetch-github",
            json={"repo": "", "path": "some/skill"},
        )
        assert resp.status_code == 422

    def test_fetch_github_download_fails_returns_404(self):
        mock_response = MagicMock()
        mock_response.status_code = 404
        client = TestClient(_make_test_app())
        with patch("httpx.get", return_value=mock_response):
            resp = client.post(
                "/admin/skills/fetch-github",
                json={"repo": "owner/repo", "path": "skills/missing-skill"},
            )
        assert resp.status_code == 404

    def test_fetch_github_unsafe_skill_name_traversal_rejected(self):
        skill_content = "---\nname: ../../etc/cron.d/evil\ndescription: bad\n---\nBody.\n"
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = skill_content
        with tempfile.TemporaryDirectory() as tmpdir:
            client = TestClient(_make_test_app(config_dir=tmpdir))
            with patch("httpx.get", return_value=mock_response):
                resp = client.post(
                    "/admin/skills/fetch-github",
                    json={"repo": "owner/repo", "path": "skills/evil"},
                )
            assert resp.status_code == 422
            assert "unsafe" in resp.json()["detail"].lower()

    def test_fetch_github_skill_with_branch_parameter(self):
        skill_content = "---\nname: quiet-skill\ndescription: test\n---\nBody.\n"
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = skill_content
        with tempfile.TemporaryDirectory() as tmpdir:
            client = TestClient(_make_test_app(config_dir=tmpdir))
            with patch("httpx.get", return_value=mock_response):
                resp = client.post(
                    "/admin/skills/fetch-github",
                    json={
                        "repo": "owner/repo",
                        "path": "skills/quiet",
                        "branch": "develop",
                    },
                )
            assert resp.status_code == 200
            assert resp.json()["name"] == "quiet-skill"

    def test_fetch_github_transport_error_returns_404(self):
        client = TestClient(_make_test_app())
        with patch("httpx.get", side_effect=httpx.HTTPError("connection refused")):
            resp = client.post(
                "/admin/skills/fetch-github",
                json={"repo": "owner/repo", "path": "skills/something"},
            )
        assert resp.status_code == 404


class TestSkillsRequestModelDefaults:
    def test_search_request_defaults(self):
        from general_ludd.routers.skills import SkillCatalogSearchRequest

        req = SkillCatalogSearchRequest()
        assert req.query == ""
        assert req.tags is None
        assert req.category is None
        assert req.limit == 20

    def test_fetch_request_defaults(self):
        from general_ludd.routers.skills import SkillFetchRequest

        req = SkillFetchRequest()
        assert req.url == ""

    def test_fetch_github_request_defaults(self):
        from general_ludd.routers.skills import SkillFetchGithubRequest

        req = SkillFetchGithubRequest()
        assert req.repo == ""
        assert req.path == ""
        assert req.branch == "main"

    def test_catalog_install_request_defaults(self):
        from general_ludd.routers.skills import SkillCatalogInstallRequest

        req = SkillCatalogInstallRequest()
        assert req.name == ""


class TestSkillRenderRequestValidation:
    @pytest.mark.parametrize(
        "payload",
        (
            {},
            {"name": "one", "trigger": "two"},
            {"name": "one", "variables": {str(index): "x" for index in range(129)}},
        ),
    )
    def test_rejects_ambiguous_or_unbounded_input(self, payload: dict[str, object]) -> None:
        from general_ludd.routers.skills import SkillRenderRequest

        with pytest.raises((ValidationError, ValueError)):
            SkillRenderRequest(**payload)

    def test_accepts_trigger_selection_and_rejects_encoded_size_overflow(self) -> None:
        from general_ludd.routers.skills import SkillRenderRequest

        assert SkillRenderRequest(trigger="run review").trigger == "run review"
        with pytest.raises((ValidationError, ValueError)):
            SkillRenderRequest(name="one", variables={"payload": "x" * 65_537})

    def test_allowed_roots_include_each_configured_daemon_scope(self, tmp_path) -> None:
        from fastapi import FastAPI

        from general_ludd.routers.skills import _allowed_skill_roots

        app = FastAPI()
        app.state._project_gludd_dir = str(tmp_path / "project")

        assert _allowed_skill_roots(app) == [tmp_path / "project" / "skills"]
