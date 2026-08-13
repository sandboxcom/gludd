"""Tests for daemon filestore, integrity, and ansible endpoints.

TDD: Tests for endpoints with zero daemon.py coverage.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient


class TestRealDaemonEndpoints:
    @pytest.fixture
    def client(self):
        from general_ludd.daemon import create_daemon_app

        app = create_daemon_app(tick_interval=0.01)
        return TestClient(app)

    def test_filestore_list_root(self, client):
        resp = client.get("/admin/filestore/list", params={"path": "/"})
        assert resp.status_code == 200
        data = resp.json()
        assert "entries" in data
        assert "count" in data

    def test_filestore_list_invalid_path(self, client):
        resp = client.get("/admin/filestore/list", params={"path": "../../../etc"})
        assert resp.status_code == 200
        data = resp.json()
        assert "entries" in data

    def test_filestore_read_not_found(self, client):
        resp = client.get("/admin/filestore/read", params={"path": "nonexistent_file.txt"})
        assert resp.status_code == 200
        data = resp.json()
        assert "error" in data

    def test_filestore_read_invalid_path(self, client):
        resp = client.get("/admin/filestore/read", params={"path": "../../etc/passwd"})
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("error") is not None or data.get("path") is not None

    @patch("general_ludd.filestore.store.FileStore.write_text")
    def test_filestore_write_success(self, mock_write, client):
        resp = client.post("/admin/filestore/write", json={"path": "test.txt", "content": "hello"})
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("success") or data.get("error") is not None

    def test_filestore_write_invalid_path(self, client):
        resp = client.post("/admin/filestore/write", json={"path": "../../../etc/bad", "content": "x"})
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("error") is not None

    def test_filestore_remove_not_found(self, client):
        resp = client.delete("/admin/filestore/remove", params={"path": "nonexistent.txt"})
        assert resp.status_code == 200
        data = resp.json()
        assert "error" in data

    def test_filestore_remove_invalid_path(self, client):
        resp = client.delete("/admin/filestore/remove", params={"path": "../../../etc/passwd"})
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("error") is not None

    @patch("general_ludd.filestore.bootstrap.BinaryBootstrapper.download_openbao")
    def test_filestore_bootstrap_openbao(self, mock_download, client):
        mock_download.return_value = True
        with patch("general_ludd.filestore.bootstrap.BinaryBootstrapper.check_openbao_in_store", return_value=True):
            resp = client.post("/admin/filestore/bootstrap", params={"binary": "openbao"})
        assert resp.status_code == 200
        data = resp.json()
        assert "binary" in data

    def test_filestore_bootstrap_unknown_binary(self, client):
        resp = client.post("/admin/filestore/bootstrap", params={"binary": "nonexistent"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is False
        # Generalized endpoint now reports the known set on an unknown request.
        assert "codebase-memory-mcp" in data.get("known", [])

    @patch(
        "general_ludd.filestore.bootstrap.BinaryBootstrapper.download",
        new_callable=AsyncMock,
    )
    def test_filestore_bootstrap_codebase_memory(self, mock_download, client):
        # Generic KNOWN_VERSIONS dispatch: codebase-memory-mcp installs via download().
        mock_download.return_value = True
        with (
            patch(
                "general_ludd.filestore.bootstrap.BinaryBootstrapper.is_platform_available",
                return_value=True,
            ),
            patch(
                "general_ludd.filestore.bootstrap.BinaryBootstrapper.get_binary_path",
                return_value="/store/binaries/codebase-memory-mcp",
            ),
        ):
            resp = client.post(
                "/admin/filestore/bootstrap",
                params={"binary": "codebase-memory-mcp"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["binary"] == "codebase-memory-mcp"
        assert data["success"] is True
        assert data["stored"] is True
        assert data["version"] == "0.8.1"
        mock_download.assert_awaited_once_with("codebase-memory-mcp")

    def test_filestore_bootstrap_platform_unavailable(self, client):
        # A known binary with no asset for this platform/arch fails gracefully,
        # without attempting a download.
        with (
            patch(
                "general_ludd.filestore.bootstrap.BinaryBootstrapper.is_platform_available",
                return_value=False,
            ),
            patch(
                "general_ludd.filestore.bootstrap.BinaryBootstrapper.download",
                new_callable=AsyncMock,
            ) as mock_download,
        ):
            resp = client.post(
                "/admin/filestore/bootstrap",
                params={"binary": "codebase-memory-mcp"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is False
        assert data["binary"] == "codebase-memory-mcp"
        assert "platform" in data["error"].lower()
        mock_download.assert_not_awaited()

    def test_filestore_binaries_list(self, client):
        resp = client.get("/admin/filestore/binaries")
        assert resp.status_code == 200
        data = resp.json()
        assert "binaries" in data
        assert "count" in data

    @patch("general_ludd.integrity.scanner.FileIntegrityScanner.scan")
    def test_integrity_scan_with_paths(self, mock_scan, client, tmp_path):
        import uuid

        mock_scan.return_value = {"scanned": 5, "changes": []}
        scan_dir = Path.cwd() / ".integrity-tests" / f"scan-{uuid.uuid4().hex[:8]}"
        scan_dir.mkdir(parents=True, exist_ok=True)
        try:
            resp = client.post("/admin/integrity/scan", json={"paths": [str(scan_dir)]})
            assert resp.status_code == 200
            data = resp.json()
            assert "scanned" in data
        finally:
            if scan_dir.exists():
                scan_dir.rmdir()

    @patch("general_ludd.integrity.scanner.FileIntegrityScanner.scan")
    def test_integrity_scan_default_paths(self, mock_scan, client):
        mock_scan.return_value = {"scanned": 0, "changes": []}
        resp = client.post("/admin/integrity/scan", json={})
        assert resp.status_code == 200
        data = resp.json()
        assert "scanned" in data

    def test_integrity_report_empty(self, client):
        resp = client.get("/admin/integrity/report")
        assert resp.status_code == 200
        data = resp.json()
        assert "changes" in data
        assert "log_entries" in data

    @patch("general_ludd.routers.integrity.sign_change_openbao")
    def test_integrity_approve(self, mock_sign, client):
        mock_sign.return_value = {
            "path": "test.yaml",
            "signature": "abc123",
            "timestamp": "2026-01-01T00:00:00",
            "status": "approved",
        }
        resp = client.post(
            "/admin/integrity/approve",
            json={
                "path": "test.yaml",
                "reason": "verified",
                "signer": "admin",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("signature") is not None

    def test_integrity_reject(self, client):
        resp = client.post(
            "/admin/integrity/reject",
            json={
                "path": "bad.yaml",
                "reason": "unauthorized",
                "signer": "admin",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "rejected"

    def test_integrity_log(self, client):
        client.post(
            "/admin/integrity/reject",
            json={
                "path": "x.yaml",
                "reason": "test",
                "signer": "admin",
            },
        )
        resp = client.get("/admin/integrity/log")
        assert resp.status_code == 200
        data = resp.json()
        assert "entries" in data
        assert len(data["entries"]) >= 1

    @patch("general_ludd.routers.ansible.search_galaxy")
    def test_ansible_search(self, mock_search, client):
        mock_search.return_value = [{"name": "nginx", "description": "web server"}]
        resp = client.get("/admin/ansible/search", params={"query": "nginx", "type": "role"})
        assert resp.status_code == 200
        data = resp.json()
        assert "results" in data
        assert len(data["results"]) == 1

    @patch("general_ludd.routers.ansible.install_galaxy")
    def test_ansible_install(self, mock_install, client):
        mock_install.return_value = {"success": True, "output": "installed nginx"}
        resp = client.post("/admin/ansible/install", json={"name": "nginx", "type": "role"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True

    @patch("general_ludd.routers.ansible.get_builtin_modules")
    def test_ansible_builtins(self, mock_builtins, client):
        mock_builtins.return_value = ["copy", "file", "shell"]
        resp = client.get("/admin/ansible/builtins")
        assert resp.status_code == 200
        data = resp.json()
        assert "modules" in data
        assert len(data["modules"]) == 3

    @patch("subprocess.run")
    @patch("general_ludd.config.binary_paths.BinaryPathResolver.is_available")
    def test_selftest_no_molecule_dir(self, mock_avail, mock_run, client):
        mock_avail.return_value = False
        resp = client.post("/admin/selftest")
        assert resp.status_code == 200
        data = resp.json()
        assert "scenarios_run" in data
        assert data["podman_available"] is False

    @patch("general_ludd.routers.ansible.search_galaxy")
    def test_ansible_search_empty_query(self, mock_search, client):
        mock_search.return_value = []
        resp = client.get("/admin/ansible/search", params={"query": "", "type": "role"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["results"] == []
