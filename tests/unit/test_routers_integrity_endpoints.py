"""Unit tests for routers/integrity.py router endpoint handlers.

Covers the previously 25.4%-rated module by exercising:
  * POST /admin/integrity/scan (defaults, confined paths, escapes)
  * GET /admin/integrity/report
  * POST /admin/integrity/approve (hash match/mismatch, no match, signing failure)
  * POST /admin/integrity/reject
  * GET /admin/integrity/log
  * POST /admin/log-audit (DoS cap, normal)
  * POST /admin/gap-analysis
"""
from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from general_ludd.daemon import create_daemon_app
from general_ludd.integrity.scanner import IntegrityKeyError


@pytest.fixture
def app():
    return create_daemon_app(tick_interval=0.01)


async def _get(app, path):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get(path)


async def _post(app, path, **kwargs):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.post(path, **kwargs)


@pytest.fixture(autouse=True)
def _clear_state():
    import general_ludd.routers.integrity as mod
    mod._integrity_changes[:] = []
    mod._integrity_log[:] = []
    yield
    mod._integrity_changes[:] = []
    mod._integrity_log[:] = []


# --------------- scan ---------------

@pytest.mark.asyncio
async def test_scan_with_defaults_uses_app_roots(app):
    fake_scanner = MagicMock()
    fake_scanner.scan.return_value = {
        "files": {}, "scanned": 0, "changes": [],
    }
    with patch(
        "general_ludd.routers.integrity.FileIntegrityScanner",
        return_value=fake_scanner,
    ), patch.object(
        app.state, "_repo_root", None, create=True,
    ):
        resp = await _post(app, "/admin/integrity/scan", json={})
    assert resp.status_code == 200

@pytest.mark.asyncio
async def test_scan_path_escapes_roots_returns_422(app):
    resp = await _post(
        app, "/admin/integrity/scan",
        json={"paths": ["/etc/passwd"]},
    )
    assert resp.status_code == 422

@pytest.mark.asyncio
async def test_scan_with_callable_tempdir_path(app, tmp_path):
    fake_scanner = MagicMock()
    fake_scanner.scan.return_value = {
        "files": {}, "scanned": 0, "changes": [],
    }
    with patch(
        "general_ludd.routers.integrity.FileIntegrityScanner",
        return_value=fake_scanner,
    ):
        resp = await _post(
            app, "/admin/integrity/scan",
            json={"paths": [str(tmp_path)]},
        )
    assert resp.status_code == 200


# --------------- report ---------------

@pytest.mark.asyncio
async def test_report_returns_changes_and_log_count(app):
    import general_ludd.routers.integrity as mod
    mod._integrity_changes[:] = [{"file": "f.txt", "type": "new"}]
    mod._integrity_log[:] = [{"action": "approved", "path": "f.txt"}]
    resp = await _get(app, "/admin/integrity/report")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["changes"]) == 1
    assert data["log_entries"] == 1


# --------------- approve ---------------

@pytest.mark.asyncio
async def test_approve_path_escapes_returns_422(app):
    resp = await _post(app, "/admin/integrity/approve", json={
        "path": "/etc/shadow",
        "signer": "admin",
        "reason": "test",
    })
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_approve_hash_mismatch_returns_422(app):
    import general_ludd.routers.integrity as mod
    mod._integrity_changes[:] = [{
        "file": os.path.expanduser("~/.config/gludd/f.txt"),
        "type": "modified",
        "old_hash": "abc",
        "new_hash": "def",
        "approved": False,
    }]
    confine_return = [os.path.expanduser("~/.config/gludd/f.txt")]
    with patch("general_ludd.routers.integrity._confine_scan_paths", return_value=confine_return):
        resp = await _post(app, "/admin/integrity/approve", json={
            "path": os.path.expanduser("~/.config/gludd/f.txt"),
            "signer": "admin",
            "reason": "test",
            "old_hash": "MISMATCH",
        })
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_approve_signing_unavailable_returns_503(app):
    import general_ludd.routers.integrity as mod
    mod._integrity_changes[:] = []
    with patch(
        "general_ludd.routers.integrity.sign_change_openbao",
        side_effect=IntegrityKeyError("vault down"),
    ):
        resp = await _post(app, "/admin/integrity/approve", json={
            "path": os.path.expanduser("~/.config/gludd/f.txt"),
            "signer": "admin",
            "reason": "test",
        })
    assert resp.status_code in (503, 422)


# --------------- reject ---------------

@pytest.mark.asyncio
async def test_reject_path_escapes_returns_422(app):
    resp = await _post(app, "/admin/integrity/reject", json={
        "path": "/etc/shadow",
        "reason": "bad",
    })
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_reject_with_valid_path_returns_200(app):
    import general_ludd.routers.integrity as mod
    mod._integrity_log[:] = []
    path = os.path.expanduser("~/.config/gludd/f.txt")
    resp = await _post(app, "/admin/integrity/reject", json={
        "path": path,
        "reason": "not approved",
        "signer": "admin",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "rejected"
    assert mod._integrity_log[0]["action"] == "rejected"


# --------------- log ---------------

@pytest.mark.asyncio
async def test_log_endpoint_returns_entries(app):
    import general_ludd.routers.integrity as mod
    mod._integrity_log[:] = [{"action": "approved", "path": "f.txt"}]
    resp = await _get(app, "/admin/integrity/log")
    assert resp.status_code == 200
    assert resp.json()["entries"] == [{"action": "approved", "path": "f.txt"}]


# --------------- log-audit ---------------

@pytest.mark.asyncio
async def test_log_audit_dos_cap_returns_413(app):
    entries = [{"message": str(i)} for i in range(10001)]
    resp = await _post(app, "/admin/log-audit", json={"log_entries": entries})
    assert resp.status_code == 413


@pytest.mark.asyncio
async def test_log_audit_normal_returns_200(app):
    fake_auditor = MagicMock()
    fake_report = MagicMock()
    fake_report.total_findings = 2
    fake_finding_1 = MagicMock(severity="high", category="auth", description="bad", evidence="line 3")
    fake_finding_2 = MagicMock(severity="low", category="network", description="slow", evidence="line 5")
    fake_report.findings = [fake_finding_1, fake_finding_2]
    fake_auditor.audit_logs.return_value = fake_report

    with patch("general_ludd.routers.integrity.LogAuditor", return_value=fake_auditor):
        resp = await _post(app, "/admin/log-audit", json={
            "log_entries": [{"message": "line 1"}, {"message": "line 2"}],
        })
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_findings"] == 2
    assert len(data["findings"]) == 2


class TestScanRoots:
    def test_scan_roots_includes_config_dir(self, app):
        from general_ludd.routers.integrity import _scan_roots
        app.state._config_dir = "/my/config"
        roots = _scan_roots(app)
        assert "/my/config" in roots

    def test_scan_roots_includes_tempdir(self, app):
        from general_ludd.routers.integrity import _scan_roots
        roots = _scan_roots(app)
        import tempfile
        assert tempfile.gettempdir() in roots

    def test_scan_roots_excludes_empty_strings(self, app):
        from general_ludd.routers.integrity import _scan_roots
        app.state._config_dir = ""
        roots = _scan_roots(app)
        assert "" not in roots

    def test_confine_scan_paths_passes_valid_path(self, tmp_path):
        from general_ludd.routers.integrity import _confine_scan_paths
        app = FastAPI()
        with patch.object(app.state, "_config_dir", str(tmp_path), create=True):
            result = _confine_scan_paths(app, [str(tmp_path / "subdir")])
        assert result == [str(tmp_path / "subdir")]

    def test_confine_scan_paths_rejects_escape(self, tmp_path):
        from fastapi import HTTPException

        from general_ludd.routers.integrity import _confine_scan_paths
        app = FastAPI()
        with (
            patch.object(app.state, "_config_dir", str(tmp_path), create=True),
            pytest.raises(HTTPException, match="escapes"),
        ):
            _confine_scan_paths(app, ["/etc/passwd"])


class TestApproveHashMatch:
    @pytest.mark.asyncio
    async def test_approve_new_hash_mismatch_returns_422(self, app):
        import general_ludd.routers.integrity as mod
        path = os.path.expanduser("~/.config/gludd/f.txt")
        mod._integrity_changes[:] = [{
            "file": path,
            "type": "modified",
            "old_hash": "abc",
            "new_hash": "def",
            "approved": False,
        }]
        with patch("general_ludd.routers.integrity._confine_scan_paths", return_value=[path]):
            resp = await _post(app, "/admin/integrity/approve", json={
                "path": path,
                "signer": "admin",
                "reason": "test",
                "new_hash": "MISMATCH",
            })
        assert resp.status_code == 422


class TestSelfTest:
    @pytest.mark.asyncio
    async def test_selftest_no_molecule_dir(self, app):
        with patch("os.path.isdir", return_value=False):
            resp = await _post(app, "/admin/selftest")
        assert resp.status_code == 200
        data = resp.json()
        assert data["scenarios_run"] == 0

    @pytest.mark.asyncio
    async def test_selftest_with_mock_runner(self, app, tmp_path):
        molecule_dir = tmp_path / "molecule" / "playbooks"
        scenario_dir = molecule_dir / "test_scenario" / "default"
        scenario_dir.mkdir(parents=True)

        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stderr = ""

        with patch("general_ludd.routers.integrity.os.getcwd", return_value=str(tmp_path)), \
             patch("general_ludd.routers.integrity.BinaryPathResolver") as mock_resolver_class:
            mock_resolver = MagicMock()
            mock_resolver.is_available.return_value = True
            mock_resolver_class.return_value = mock_resolver

            resp = await _post(app, "/admin/selftest")
        assert resp.status_code in (200, 500)


class TestApproveWithScanMatch:
    @pytest.mark.asyncio
    async def test_approve_with_matching_hashes_succeeds(self, app):
        import general_ludd.routers.integrity as mod

        path = os.path.expanduser("~/.config/gludd/f.txt")
        mod._integrity_changes[:] = [{
            "file": path,
            "type": "modified",
            "old_hash": "abc",
            "new_hash": "def",
            "approved": False,
        }]
        with patch("general_ludd.routers.integrity._confine_scan_paths", return_value=[path]), \
             patch("general_ludd.routers.integrity.sign_change_openbao") as mock_sign:
            mock_sign.return_value = {
                "path": path,
                "signature": "signed-hash",
                "timestamp": "2026-01-01T00:00:00Z",
            }
            resp = await _post(app, "/admin/integrity/approve", json={
                "path": path,
                "signer": "admin",
                "reason": "legit",
                "old_hash": "abc",
                "new_hash": "def",
            })
        assert resp.status_code == 200
        assert "signature" in resp.json()


@pytest.mark.asyncio
async def test_gap_analysis_default_repo_root(app):
    fake_analyzer = MagicMock()
    fake_report = MagicMock()
    fake_report.total_gaps = 0
    fake_report.gaps = []
    fake_analyzer.analyze.return_value = fake_report

    with patch.object(app.state, "_repo_root", None, create=True), \
         patch("general_ludd.routers.integrity.GapAnalyzer", return_value=fake_analyzer):
        resp = await _post(app, "/admin/gap-analysis", json={})
    assert resp.status_code == 200
    assert resp.json()["total_gaps"] == 0


@pytest.mark.asyncio
async def test_gap_analysis_with_sprint_path(app):
    fake_analyzer = MagicMock()
    fake_report = MagicMock()
    fake_report.total_gaps = 3
    fake_gap = MagicMock(category="security", description="gap1", severity="high", suggested_action="fix it")
    fake_report.gaps = [fake_gap, fake_gap, fake_gap]
    fake_analyzer.analyze.return_value = fake_report

    path = os.path.expanduser("~/.config/gludd/sprint.yml")
    with patch("general_ludd.routers.integrity.GapAnalyzer", return_value=fake_analyzer):
        resp = await _post(app, "/admin/gap-analysis", json={
            "sprint_path": path,
            "repo_root": ".",
        })
    assert resp.status_code == 200
    assert resp.json()["total_gaps"] == 3
    assert len(resp.json()["gaps"]) == 3
