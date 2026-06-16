"""Red-team regression tests for router-level auth / SSRF / path confinement.

Covers the AUTH hardening batch:

  AUTH-1  daemon `_is_public` is (method, path)-aware: a public path is public
          only for safe read methods. POST /api/todos is NOT public.
  AUTH-2  POST /api/todos validates a non-null project_id against active
          projects -> 422; unconstrained when no active projects exist.
  AUTH-4  skill fetch is SSRF-guarded (https-only, literal-host deny, no DNS,
          follow_redirects=False) and the attacker-controlled skill name is
          sanitized before it touches the filesystem.
  AUTH-5  /admin/code/* and /admin/integrity/scan confine caller-supplied paths
          to the workspace root -> 422.

NO real network, NO real sockets, NO sleeps. httpx is fully mocked in the skill
fetcher; everything else runs over ASGITransport in-process.
"""

from __future__ import annotations

import inspect
import os
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

import general_ludd.daemon as daemon_mod
import general_ludd.skills.fetcher as fetcher_mod
from general_ludd.daemon import create_daemon_app
from general_ludd.routers.integrity import register as register_integrity
from general_ludd.routers.models import register as register_models
from general_ludd.routers.todos import register as register_todos
from general_ludd.security.auth import (
    is_path_within,
    is_safe_fetch_url,
    verify_psk,
)
from general_ludd.skills.fetcher import RemoteSkillFetcher, _safe_skill_filename


def _client(app):
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _todos_app():
    app = FastAPI()
    state = {"todos": []}
    app.state._session_factory = None
    app.state._project_manager = None
    register_todos(app, state)
    return app


# --- AUTH-1: method-aware public paths ---------------------------------------


class TestMethodAwarePublic:
    _SRC = inspect.getsource(daemon_mod)

    def test_source_is_method_aware(self):
        assert "_SAFE_METHODS" in self._SRC, (
            "AUTH-1: _is_public must consult the request method, not just path."
        )

    @pytest.mark.asyncio
    async def test_get_api_todos_public_with_psk(self):
        with patch.dict(os.environ, {"GLUDD_PSK": "x" * 16}):
            app = create_daemon_app(tick_interval=0.01)
        async with _client(app) as c:
            resp = await c.get("/api/todos")
        # GET on a public path is allowed without a token.
        assert resp.status_code != 401

    @pytest.mark.asyncio
    async def test_post_api_todos_requires_auth_with_psk(self):
        daemon_mod._daemon_state["todos"] = []
        with patch.dict(os.environ, {"GLUDD_PSK": "x" * 16}):
            app = create_daemon_app(tick_interval=0.01)
        async with _client(app) as c:
            resp = await c.post(
                "/api/todos",
                json={"title": "no-auth write attempt"},
            )
        # POST is a write -> NOT public -> auth gate fires (no token).
        assert resp.status_code == 401


# --- AUTH-2: project_id validation -------------------------------------------


class TestTodoProjectIdValidation:
    @pytest.mark.asyncio
    async def test_unconstrained_when_no_project_manager(self):
        app = _todos_app()
        async with _client(app) as c:
            resp = await c.post(
                "/api/todos",
                json={"title": "free todo", "project_id": "ANYTHING"},
            )
        assert resp.status_code == 201

    @pytest.mark.asyncio
    async def test_null_project_id_always_allowed(self):
        app = _todos_app()
        mgr = MagicMock()
        active = MagicMock()
        active.project_id = "PROJ-REAL"
        mgr.list_active.return_value = [active]
        app.state._project_manager = mgr
        async with _client(app) as c:
            resp = await c.post("/api/todos", json={"title": "no project"})
        assert resp.status_code == 201

    @pytest.mark.asyncio
    async def test_unknown_project_id_rejected_422(self):
        app = _todos_app()
        mgr = MagicMock()
        active = MagicMock()
        active.project_id = "PROJ-REAL"
        mgr.list_active.return_value = [active]
        app.state._project_manager = mgr
        async with _client(app) as c:
            resp = await c.post(
                "/api/todos",
                json={"title": "bad", "project_id": "PROJ-FAKE"},
            )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_known_active_project_id_accepted(self):
        app = _todos_app()
        mgr = MagicMock()
        active = MagicMock()
        active.project_id = "PROJ-REAL"
        mgr.list_active.return_value = [active]
        app.state._project_manager = mgr
        async with _client(app) as c:
            resp = await c.post(
                "/api/todos",
                json={"title": "good", "project_id": "PROJ-REAL"},
            )
        assert resp.status_code == 201


# --- AUTH-4: SSRF guard + skill name sanitization ----------------------------


class TestSafeFetchUrlUnit:
    def test_https_public_host_allowed(self):
        assert is_safe_fetch_url("https://raw.githubusercontent.com/a/b/main/SKILL.md")

    @pytest.mark.parametrize(
        "url",
        [
            "http://raw.githubusercontent.com/x",          # not https
            "https://localhost/x",                          # loopback name
            "https://127.0.0.1/x",                          # loopback ip
            "https://169.254.169.254/latest/meta-data/",    # cloud metadata
            "https://10.0.0.5/x",                           # RFC-1918
            "https://192.168.1.1/x",                        # RFC-1918
            "https://172.16.0.9/x",                         # RFC-1918
            "https://[::1]/x",                              # ipv6 loopback
            "https://metadata.google.internal/x",           # gcp metadata name
            "ftp://example.com/x",                          # wrong scheme
            "",                                              # empty
            "not a url",                                     # junk
        ],
    )
    def test_unsafe_urls_blocked(self, url):
        assert is_safe_fetch_url(url) is False

    def test_safe_fetch_url_does_no_dns(self):
        """The guard must never resolve a hostname (blocking DNS == hang risk).
        Patch socket.getaddrinfo/gethostbyname to explode and confirm an unknown
        host name does not trigger them."""
        import socket as socket_mod

        with patch.object(socket_mod, "getaddrinfo", side_effect=AssertionError("DNS in is_safe_fetch_url")), \
                patch.object(socket_mod, "gethostbyname", side_effect=AssertionError("DNS in is_safe_fetch_url")):
            # A non-IP hostname passes the IP checks without resolution.
            assert is_safe_fetch_url("https://example.com/SKILL.md") is True


class TestRemoteFetcherSSRF:
    def test_fetch_refuses_unsafe_url_without_network(self):
        fetcher = RemoteSkillFetcher()
        # If the SSRF guard fails open, httpx.get would be called — make it blow up.
        with patch.object(fetcher_mod.httpx, "get", side_effect=AssertionError("network on unsafe URL")):
            assert fetcher.fetch("https://169.254.169.254/latest/") is None
            assert fetcher.fetch("http://localhost/SKILL.md") is None

    def test_fetch_safe_url_disables_redirects(self):
        fetcher = RemoteSkillFetcher()
        resp = MagicMock()
        resp.status_code = 200
        resp.text = "---\nname: ok\ndescription: d\n---\n\nbody\n"
        with patch.object(fetcher_mod.httpx, "get", return_value=resp) as mock_get:
            skill = fetcher.fetch("https://raw.githubusercontent.com/a/b/main/SKILL.md")
        assert skill is not None
        _args, kwargs = mock_get.call_args
        assert kwargs.get("follow_redirects") is False, (
            "skill fetch must disable redirect following (SSRF bounce guard)."
        )


class TestSkillNameSanitization:
    @pytest.mark.parametrize(
        "name",
        ["../../etc/cron.d/evil", "/etc/passwd", "a/b", "..", ".", "foo/../bar"],
    )
    def test_malicious_names_rejected(self, name):
        assert _safe_skill_filename(name) is None

    def test_benign_name_kept(self):
        assert _safe_skill_filename("my-skill") == "my-skill"

    def test_install_refuses_traversal_name(self, tmp_path):
        fetcher = RemoteSkillFetcher()
        resp = MagicMock()
        resp.status_code = 200
        resp.text = "---\nname: ../../escape\ndescription: d\n---\n\nbody\n"
        with patch.object(fetcher_mod.httpx, "get", return_value=resp):
            result = fetcher.install(
                "https://raw.githubusercontent.com/a/b/main/SKILL.md",
                str(tmp_path),
            )
        assert result is None
        # Nothing escaped the target dir.
        escaped = tmp_path.parent / "escape.md"
        assert not escaped.exists()


# --- AUTH-5: code / integrity path confinement -------------------------------


def _models_app(tmp_workspace):
    app = FastAPI()
    app.state._workspace_root = str(tmp_workspace)
    register_models(app, {})
    return app


class TestCodePathConfinement:
    @pytest.mark.asyncio
    async def test_complexity_rejects_absolute_escape(self, tmp_path):
        app = _models_app(tmp_path)
        async with _client(app) as c:
            resp = await c.post("/admin/code/complexity", json={"path": "/etc/passwd"})
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_complexity_rejects_traversal(self, tmp_path):
        app = _models_app(tmp_path)
        async with _client(app) as c:
            resp = await c.post(
                "/admin/code/complexity",
                json={"path": "../../../../etc/passwd"},
            )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_complexity_allows_in_workspace(self, tmp_path):
        target = tmp_path / "mod.py"
        target.write_text("def f():\n    return 1\n")
        app = _models_app(tmp_path)
        async with _client(app) as c:
            resp = await c.post("/admin/code/complexity", json={"path": "mod.py"})
        assert resp.status_code == 200


class TestIntegrityScanConfinement:
    def _integrity_app(self, tmp_workspace):
        app = FastAPI()
        app.state._config_dir = str(tmp_workspace)
        register_integrity(app, {})
        return app

    @pytest.mark.asyncio
    async def test_scan_rejects_path_outside_roots(self, tmp_path):
        with patch.dict(os.environ, {"GLUDD_WORKSPACE": str(tmp_path)}):
            app = self._integrity_app(tmp_path)
            async with _client(app) as c:
                resp = await c.post(
                    "/admin/integrity/scan",
                    json={"paths": ["/etc"]},
                )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_scan_allows_path_inside_root(self, tmp_path):
        (tmp_path / "f.txt").write_text("x")
        with patch.dict(os.environ, {"GLUDD_WORKSPACE": str(tmp_path)}):
            app = self._integrity_app(tmp_path)
            async with _client(app) as c:
                resp = await c.post(
                    "/admin/integrity/scan",
                    json={"paths": [str(tmp_path)]},
                )
        assert resp.status_code == 200


# --- shared helper unit coverage ---------------------------------------------


class TestIsPathWithin:
    def test_relative_child_is_within(self, tmp_path):
        assert is_path_within(str(tmp_path), "sub/file.py") is True

    def test_absolute_escape_not_within(self, tmp_path):
        assert is_path_within(str(tmp_path), "/etc/passwd") is False

    def test_traversal_not_within(self, tmp_path):
        assert is_path_within(str(tmp_path), "../../../etc/passwd") is False


class TestVerifyPsk:
    def test_empty_rejected(self):
        assert verify_psk("", "secret") is False
        assert verify_psk("secret", "") is False

    def test_match(self):
        assert verify_psk("secret", "secret") is True

    def test_mismatch(self):
        assert verify_psk("secret", "other") is False
