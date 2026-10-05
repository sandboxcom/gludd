"""Red-team tests for daemon HTTP auth/scoping bugs.

Covers the CONFIRMED findings:
  1. Method-blind public allowlist — POST on a "public" path must NOT bypass auth.
  2. Public cross-project todo create — POST /api/todos with an unknown
     project_id must be rejected (422) when projects are registered; back-compat
     when none are registered.
  4. Skills SSRF + path traversal — sanitize_skill_name / is_path_within /
     is_safe_fetch_url unit-level guards plus router rejection paths.
  5. Path confinement — complexity/suggest-model/integrity-scan reject paths that
     escape the workspace/allowed roots.

All HTTP exercised via AsyncClient + ASGITransport (no real network, no real
lifespan). Default no-PSK posture stays open and is asserted explicitly so the
fixes don't silently break back-compat.
"""

from __future__ import annotations

import os

import pytest
from httpx import ASGITransport, AsyncClient

import general_ludd.daemon as daemon_mod
from general_ludd.daemon import create_daemon_app
from general_ludd.projects.manager import ProjectManager
from general_ludd.security.sanitize import (
    is_path_within,
    is_safe_fetch_url,
    sanitize_skill_name,
)


@pytest.fixture(autouse=True)
def _reset_daemon_state():
    if daemon_mod._daemon_state is None:
        daemon_mod._daemon_state = {"todos": [], "tick_metrics": {}, "quality_gate": {}}
    daemon_mod._daemon_state["todos"] = []
    daemon_mod._daemon_state["tick_metrics"] = {}
    # Ensure PSK/require-auth env never leaks between tests.
    for var in ("GLUDD_AUTH_PSK", "GLUDD_REQUIRE_AUTH"):
        os.environ.pop(var, None)
    yield
    for var in ("GLUDD_AUTH_PSK", "GLUDD_REQUIRE_AUTH"):
        os.environ.pop(var, None)


def _client(app):
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


# --------------------------------------------------------------------------- #
# FIX 1: method-aware public allowlist                                         #
# --------------------------------------------------------------------------- #
class TestMethodAwarePublicAllowlist:
    @pytest.mark.asyncio
    async def test_post_to_public_path_is_not_public_when_psk_set(self, monkeypatch):
        # With a PSK configured, a *mutating* verb on a path that is only
        # read-only-public must fall through to auth -> 401 without a token.
        monkeypatch.setenv("GLUDD_AUTH_PSK", "topsecret")
        app = create_daemon_app(tick_interval=0.01)
        async with _client(app) as client:
            resp = await client.post("/api/todos", json={"title": "x", "queue": "core"})
            assert resp.status_code == 401, resp.text

    @pytest.mark.asyncio
    async def test_get_public_path_stays_public_with_psk(self, monkeypatch):
        # The read-only verb on the same path stays public (no token needed).
        monkeypatch.setenv("GLUDD_AUTH_PSK", "topsecret")
        app = create_daemon_app(tick_interval=0.01)
        async with _client(app) as client:
            resp = await client.get("/api/todos")
            assert resp.status_code == 200, resp.text

    @pytest.mark.asyncio
    async def test_post_public_path_open_without_psk(self):
        # Default posture (no PSK): POST /api/todos stays OPEN for back-compat.
        app = create_daemon_app(tick_interval=0.01)
        async with _client(app) as client:
            resp = await client.post("/api/todos", json={"title": "x", "queue": "core"})
            assert resp.status_code == 201, resp.text

    @pytest.mark.asyncio
    async def test_post_public_path_with_valid_token_passes(self, monkeypatch):
        monkeypatch.setenv("GLUDD_AUTH_PSK", "topsecret")
        app = create_daemon_app(tick_interval=0.01)
        async with _client(app) as client:
            resp = await client.post(
                "/api/todos",
                json={"title": "x", "queue": "core"},
                headers={"Authorization": "Bearer topsecret"},
            )
            assert resp.status_code == 201, resp.text

    @pytest.mark.asyncio
    async def test_require_auth_fail_closed_on_post_public_path(self, monkeypatch):
        # GLUDD_REQUIRE_AUTH with no PSK: a mutating verb on a public path is
        # NOT public, so it must fail closed (503), while GET stays 200.
        monkeypatch.setenv("GLUDD_REQUIRE_AUTH", "1")
        app = create_daemon_app(tick_interval=0.01)
        async with _client(app) as client:
            get_resp = await client.get("/healthz")
            assert get_resp.status_code == 200
            post_resp = await client.post("/api/todos", json={"title": "x", "queue": "core"})
            assert post_resp.status_code == 503, post_resp.text


# --------------------------------------------------------------------------- #
# FIX 2: cross-project todo create                                            #
# --------------------------------------------------------------------------- #
class TestCrossProjectTodoCreate:
    @pytest.mark.asyncio
    async def test_unknown_project_id_rejected_when_projects_registered(self):
        app = create_daemon_app(tick_interval=0.01)
        pm = ProjectManager()
        proj = pm.add_project(name="alpha", weight=50.0)
        app.state._project_manager = pm
        async with _client(app) as client:
            resp = await client.post(
                "/api/todos",
                json={"title": "x", "project_id": "proj-does-not-exist"},
            )
            assert resp.status_code == 422, resp.text
            # And a known project_id is accepted.
            ok = await client.post(
                "/api/todos",
                json={"title": "y", "project_id": proj.project_id},
            )
            assert ok.status_code == 201, ok.text

    @pytest.mark.asyncio
    async def test_project_id_unconstrained_when_no_projects(self):
        # Back-compat: no ProjectManager seeded -> arbitrary project_id allowed.
        app = create_daemon_app(tick_interval=0.01)
        async with _client(app) as client:
            resp = await client.post(
                "/api/todos",
                json={"title": "x", "project_id": "anything-goes"},
            )
            assert resp.status_code == 201, resp.text

    @pytest.mark.asyncio
    async def test_null_project_id_always_allowed(self):
        app = create_daemon_app(tick_interval=0.01)
        pm = ProjectManager()
        pm.add_project(name="alpha", weight=50.0)
        app.state._project_manager = pm
        async with _client(app) as client:
            resp = await client.post("/api/todos", json={"title": "x"})
            assert resp.status_code == 201, resp.text


# --------------------------------------------------------------------------- #
# FIX 4: skills SSRF + path traversal (unit guards)                           #
# --------------------------------------------------------------------------- #
class TestSkillNameSanitization:
    @pytest.mark.parametrize(
        "bad",
        [
            "../../etc/passwd",
            "..",
            "foo/bar",
            "foo\\bar",
            "/abs",
            "",
            "  ",
            ".hidden..traverse",
        ],
    )
    def test_unsafe_skill_names_rejected(self, bad):
        assert sanitize_skill_name(bad) is None

    @pytest.mark.parametrize("good", ["skill_a", "my-skill", "Skill.v2", "abc123"])
    def test_safe_skill_names_accepted(self, good):
        assert sanitize_skill_name(good) == good


class TestFetchUrlAllowlist:
    @pytest.mark.parametrize(
        "bad",
        [
            "http://example.com/skill.md",  # not https
            "https://localhost/skill.md",
            "https://127.0.0.1/skill.md",
            "https://169.254.169.254/latest/meta-data/",  # cloud metadata
            "https://10.0.0.5/skill.md",  # RFC-1918
            "https://192.168.1.10/skill.md",
            "https://172.16.0.1/skill.md",
            "ftp://example.com/skill.md",
            "https:///skill.md",  # no host
            "not a url",
        ],
    )
    def test_disallowed_fetch_urls(self, bad):
        assert is_safe_fetch_url(bad) is False

    @pytest.mark.parametrize(
        "good",
        [
            "https://raw.githubusercontent.com/o/r/main/SKILL.md",
            "https://example.com/skill.md",
        ],
    )
    def test_allowed_fetch_urls(self, good):
        assert is_safe_fetch_url(good) is True


class TestPathWithin:
    def test_inside_is_within(self, tmp_path):
        root = tmp_path / "root"
        root.mkdir()
        inside = root / "sub" / "f.md"
        assert is_path_within(str(inside), str(root)) is True

    def test_root_is_within_itself(self, tmp_path):
        assert is_path_within(str(tmp_path), str(tmp_path)) is True

    def test_traversal_escapes(self, tmp_path):
        root = tmp_path / "root"
        root.mkdir()
        escape = root / ".." / "outside.md"
        assert is_path_within(str(escape), str(root)) is False

    def test_sibling_prefix_not_within(self, tmp_path):
        root = tmp_path / "root"
        root.mkdir()
        sibling = tmp_path / "root-evil" / "f.md"
        assert is_path_within(str(sibling), str(root)) is False


class TestSkillsFetchRouterGuards:
    @pytest.mark.asyncio
    async def test_skills_fetch_rejects_ssrf_url(self):
        # The router must reject a localhost/metadata URL BEFORE any httpx call
        # (so no real network is touched) with a 422.
        app = create_daemon_app(tick_interval=0.01)
        async with _client(app) as client:
            resp = await client.post(
                "/admin/skills/fetch",
                json={"url": "https://169.254.169.254/latest/meta-data/"},
            )
            assert resp.status_code == 422, resp.text

    @pytest.mark.asyncio
    async def test_skills_fetch_rejects_non_https(self):
        app = create_daemon_app(tick_interval=0.01)
        async with _client(app) as client:
            resp = await client.post(
                "/admin/skills/fetch",
                json={"url": "http://localhost:8080/skill.md"},
            )
            assert resp.status_code == 422, resp.text


# --------------------------------------------------------------------------- #
# FIX 5: path confinement on complexity / suggest-model / integrity scan      #
# --------------------------------------------------------------------------- #
class TestPathConfinement:
    @pytest.mark.asyncio
    async def test_complexity_rejects_path_outside_root(self):
        app = create_daemon_app(tick_interval=0.01)
        app.state._workspace_root = os.getcwd()
        async with _client(app) as client:
            resp = await client.post("/admin/code/complexity", json={"path": "/etc/passwd"})
            assert resp.status_code == 422, resp.text

    @pytest.mark.asyncio
    async def test_suggest_model_rejects_path_outside_root(self):
        app = create_daemon_app(tick_interval=0.01)
        app.state._workspace_root = os.getcwd()
        async with _client(app) as client:
            resp = await client.post("/admin/code/suggest-model", json={"path": "/etc/passwd"})
            assert resp.status_code == 422, resp.text

    @pytest.mark.asyncio
    async def test_complexity_accepts_path_inside_root(self, tmp_path):
        app = create_daemon_app(tick_interval=0.01)
        app.state._workspace_root = str(tmp_path)
        src = tmp_path / "mod.py"
        src.write_text("def f():\n    return 1\n")
        async with _client(app) as client:
            resp = await client.post("/admin/code/complexity", json={"path": str(src)})
            assert resp.status_code == 200, resp.text

    @pytest.mark.asyncio
    async def test_integrity_scan_rejects_path_outside_allowed_roots(self):
        app = create_daemon_app(tick_interval=0.01)
        async with _client(app) as client:
            resp = await client.post("/admin/integrity/scan", json={"paths": ["/etc"]})
            assert resp.status_code == 422, resp.text

    @pytest.mark.asyncio
    async def test_integrity_scan_default_paths_ok(self, tmp_path):
        # No paths supplied -> scans the allowed default roots (config_dir etc.).
        app = create_daemon_app(tick_interval=0.01, config_dir=str(tmp_path))
        async with _client(app) as client:
            resp = await client.post("/admin/integrity/scan", json={})
            assert resp.status_code == 200, resp.text

    @pytest.mark.asyncio
    async def test_integrity_scan_accepts_path_inside_config_dir(self, tmp_path):
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "f.txt").write_text("hi")
        app = create_daemon_app(tick_interval=0.01, config_dir=str(tmp_path))
        async with _client(app) as client:
            resp = await client.post("/admin/integrity/scan", json={"paths": [str(sub)]})
            assert resp.status_code == 200, resp.text

    @pytest.mark.asyncio
    async def test_integrity_approve_rejects_path_outside_allowed_roots(self):
        # AUTH: signing off on a change must refuse a path that escapes the
        # allowed roots, otherwise the endpoint signs/arbitrary-files.
        app = create_daemon_app(tick_interval=0.01)
        async with _client(app) as client:
            resp = await client.post(
                "/admin/integrity/approve",
                json={"path": "/etc/passwd", "signer": "admin", "reason": "x"},
            )
            assert resp.status_code == 422, resp.text

    @pytest.mark.asyncio
    async def test_integrity_reject_rejects_path_outside_allowed_roots(self):
        app = create_daemon_app(tick_interval=0.01)
        async with _client(app) as client:
            resp = await client.post(
                "/admin/integrity/reject",
                json={"path": "/etc/passwd", "signer": "admin", "reason": "x"},
            )
            assert resp.status_code == 422, resp.text

    @pytest.mark.asyncio
    async def test_gap_analysis_rejects_sprint_path_outside_allowed_roots(self):
        # repo_root is already confined; sprint_path must be too.
        app = create_daemon_app(tick_interval=0.01)
        async with _client(app) as client:
            resp = await client.post(
                "/admin/gap-analysis",
                json={"sprint_path": "/etc/passwd"},
            )
            assert resp.status_code == 422, resp.text
