"""S.10: Path confinement tests for routers/integrity.py.

D6/CA-R2: unconfined repo_root/path. Verify:
  - _confine_scan_paths rejects escapes (traversal, absolute outside root)
  - _confine_scan_paths returns resolved paths, not raw
  - _scan_roots includes os.getcwd() (workspace)
  - gap-analysis rejects escaped repo_root
  - gap-analysis default repo_root is always confined
"""

from __future__ import annotations

import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient

from general_ludd.daemon import create_daemon_app


@pytest.fixture
def app():
    return create_daemon_app(tick_interval=0.01)


async def _post(app, path, **kwargs):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.post(path, **kwargs)


# ── _scan_roots ────────────────────────────────────────────────────────


class TestScanRoots:
    def test_includes_current_working_directory(self):
        from general_ludd.routers.integrity import _scan_roots

        app = FastAPI()

        with patch.object(app.state, "_config_dir", None, create=True):
            roots = _scan_roots(app)

        cwd_real = os.path.realpath(os.getcwd())
        root_reals = [os.path.realpath(r) for r in roots]
        assert cwd_real in root_reals, (
            f"_scan_roots must include CWD {cwd_real}; got {root_reals}"
        )

    def test_returns_only_non_empty_roots(self):
        from general_ludd.routers.integrity import _scan_roots

        app = FastAPI()
        with patch.object(app.state, "_config_dir", None, create=True):
            roots = _scan_roots(app)
        assert all(roots), f"all roots must be non-empty; got {roots}"
        assert len(roots) >= 1


# ── _confine_scan_paths ────────────────────────────────────────────────


class TestConfineScanPaths:
    def test_rejects_path_traversal_escape(self, tmp_path):
        from general_ludd.routers.integrity import _confine_scan_paths

        app = FastAPI()
        with (
            patch.object(app.state, "_config_dir", str(tmp_path), create=True),
            pytest.raises(HTTPException, match="escapes"),
        ):
            _confine_scan_paths(app, ["../../../etc/passwd"])

    def test_rejects_absolute_escape(self, tmp_path):
        from general_ludd.routers.integrity import _confine_scan_paths

        app = FastAPI()
        with (
            patch.object(app.state, "_config_dir", str(tmp_path), create=True),
            pytest.raises(HTTPException, match="escapes"),
        ):
            _confine_scan_paths(app, ["/etc/passwd"])

    def test_allows_path_within_configured_root(self):
        from general_ludd.routers.integrity import _confine_scan_paths

        with tempfile.TemporaryDirectory() as tmp:
            sub = os.path.join(tmp, "subdir")
            os.makedirs(sub, exist_ok=True)

            app = FastAPI()
            with patch.object(app.state, "_config_dir", tmp, create=True):
                result = _confine_scan_paths(app, [sub])

        assert len(result) == 1
        assert os.path.isabs(result[0])
        assert os.path.realpath(result[0]) == os.path.realpath(sub)

    def test_returns_resolved_not_raw_paths(self):
        from general_ludd.routers.integrity import _confine_scan_paths

        with tempfile.TemporaryDirectory() as tmp:
            sub = os.path.join(tmp, "subdir")
            os.makedirs(sub, exist_ok=True)
            symlink = os.path.join(tmp, "link")
            os.symlink(sub, symlink)

            app = FastAPI()
            with patch.object(app.state, "_config_dir", tmp, create=True):
                result = _confine_scan_paths(app, [symlink])

        assert len(result) == 1
        result_real = os.path.realpath(result[0])
        expected_real = os.path.realpath(sub)
        assert result_real == expected_real, (
            f"must return realpath resolved; got {result_real}, expected {expected_real}"
        )

    def test_rejects_path_using_relative_dots(self, tmp_path):
        from general_ludd.routers.integrity import _confine_scan_paths

        app = FastAPI()
        with (
            patch.object(app.state, "_config_dir", str(tmp_path), create=True),
            pytest.raises(HTTPException, match="escapes"),
        ):
            _confine_scan_paths(app, ["foo/../../etc/passwd"])


# ── gap-analysis endpoint ──────────────────────────────────────────────


class TestGapAnalysisPathConfinement:
    @pytest.mark.asyncio
    async def test_rejects_escaped_repo_root(self, app):
        resp = await _post(app, "/admin/gap-analysis", json={
            "repo_root": "/etc/passwd",
        })
        assert resp.status_code == 422
        assert "escapes" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_rejects_path_traversal_repo_root(self, app):
        resp = await _post(app, "/admin/gap-analysis", json={
            "repo_root": "../../../etc",
        })
        assert resp.status_code == 422
        assert "escapes" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_default_repo_root_is_confined(self, app):
        fake_analyzer = MagicMock()
        fake_report = MagicMock()
        fake_report.total_gaps = 0
        fake_report.gaps = []
        fake_analyzer.analyze.return_value = fake_report

        with patch("general_ludd.routers.integrity.GapAnalyzer", return_value=fake_analyzer):
            resp = await _post(app, "/admin/gap-analysis", json={})
        assert resp.status_code == 200

        call_args = fake_analyzer.analyze.call_args
        repo_root = call_args.kwargs["repo_root"]
        assert repo_root != ".", "default repo_root must not be '.'"
        assert os.path.isabs(repo_root), f"repo_root must be absolute: {repo_root}"

    @pytest.mark.asyncio
    async def test_allows_repo_root_in_configured_scan_root(self, app):
        fake_analyzer = MagicMock()
        fake_report = MagicMock()
        fake_report.total_gaps = 5
        fake_report.gaps = []
        fake_analyzer.analyze.return_value = fake_report

        with tempfile.TemporaryDirectory() as tmp, patch.object(
            app.state,
            "_config_dir",
            tmp,
            create=True,
        ):
            resp = await _post(app, "/admin/gap-analysis", json={
                "repo_root": tmp,
            })
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_rejects_escaped_sprint_path(self, app):
        resp = await _post(app, "/admin/gap-analysis", json={
            "sprint_path": "/etc/shadow",
        })
        assert resp.status_code == 422
        assert "escapes" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_accepts_valid_sprint_path(self, app):
        fake_analyzer = MagicMock()
        fake_report = MagicMock()
        fake_report.total_gaps = 2
        fake_report.gaps = []
        fake_analyzer.analyze.return_value = fake_report

        path = os.path.expanduser("~/.config/gludd/sprint.yml")
        with patch("general_ludd.routers.integrity.GapAnalyzer", return_value=fake_analyzer):
            resp = await _post(app, "/admin/gap-analysis", json={
                "sprint_path": path,
                "repo_root": ".",
            })
        assert resp.status_code == 200


# ── selftest cwd sanity ────────────────────────────────────────────────


class TestSelfTestCwd:
    @pytest.mark.asyncio
    async def test_selftest_cwd_in_scan_roots(self, app):
        cwd = os.path.realpath(os.getcwd())
        molecule_dir = cwd + "/molecule/playbooks"
        if not os.path.isdir(molecule_dir):
            os.makedirs(molecule_dir, exist_ok=True)
            try:
                with patch("subprocess.run") as mock_run:
                    mock_proc = MagicMock()
                    mock_proc.returncode = 0
                    mock_proc.stderr = ""
                    mock_run.return_value = mock_proc
                    with patch("general_ludd.routers.integrity.BinaryPathResolver") as mock_resolver_class:
                        mock_resolver = MagicMock()
                        mock_resolver.is_available.return_value = False
                        mock_resolver_class.return_value = mock_resolver
                        resp = await _post(app, "/admin/selftest")
                assert resp.status_code == 200
            finally:
                import shutil
                shutil.rmtree(cwd + "/molecule", ignore_errors=True)
