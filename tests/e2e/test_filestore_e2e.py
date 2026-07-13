"""E2E tests for the filestore router.

Exercises FileStore CRUD operations through the registered FastAPI endpoints:
  - GET /admin/filestore/list
  - GET /admin/filestore/read
  - POST /admin/filestore/write
  - DELETE /admin/filestore/remove
  - POST /admin/filestore/bootstrap
  - GET /admin/filestore/binaries
"""

from __future__ import annotations

from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from general_ludd.routers.filestore import register


@pytest.fixture
def filestore_app(tmp_path):
    """FastAPI app with filestore routes registered."""
    app = FastAPI()
    register(app, {})
    return TestClient(app)


class TestFilestoreList:
    def test_list_root_returns_entries(self, filestore_app, tmp_path):
        with patch("general_ludd.routers.filestore.FileStore") as mock_fs:
            store_instance = Mock()
            store_instance.list_dir.return_value = [
                {"name": "config", "is_dir": True},
                {"name": "README.md", "is_dir": False},
            ]
            mock_fs.return_value = store_instance
            resp = filestore_app.get("/admin/filestore/list", params={"path": "/"})
            assert resp.status_code == 200
            data = resp.json()
            assert "entries" in data
            assert data["count"] == 2
            assert data["path"] != ""

    def test_list_empty_dir(self, filestore_app):
        with patch("general_ludd.routers.filestore.FileStore") as mock_fs:
            store_instance = Mock()
            store_instance.list_dir.return_value = []
            mock_fs.return_value = store_instance
            resp = filestore_app.get("/admin/filestore/list", params={"path": "/empty"})
            assert resp.status_code == 200
            data = resp.json()
            assert data["entries"] == []
            assert data["count"] == 0

    def test_list_subdirectory(self, filestore_app):
        with patch("general_ludd.routers.filestore.FileStore") as mock_fs:
            store_instance = Mock()
            store_instance.list_dir.return_value = [
                {"name": "file1.txt", "is_dir": False},
            ]
            mock_fs.return_value = store_instance
            resp = filestore_app.get(
                "/admin/filestore/list", params={"path": "/projects/gludd"}
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["path"] != ""
            assert len(data["entries"]) == 1


class TestFilestoreRead:
    def test_read_text_file(self, filestore_app):
        with patch("general_ludd.routers.filestore.FileStore") as mock_fs:
            store_instance = Mock()
            store_instance.exists.return_value = True
            store_instance.is_dir.return_value = False
            store_instance.read_text.return_value = "hello world"
            mock_fs.return_value = store_instance
            resp = filestore_app.get("/admin/filestore/read", params={"path": "/test.txt"})
            assert resp.status_code == 200
            data = resp.json()
            assert data["is_dir"] is False
            assert data["content"] == "hello world"

    def test_read_directory_returns_list(self, filestore_app):
        with patch("general_ludd.routers.filestore.FileStore") as mock_fs:
            store_instance = Mock()
            store_instance.exists.return_value = True
            store_instance.is_dir.return_value = True
            store_instance.list_dir.return_value = [
                {"name": "child.txt", "is_dir": False},
            ]
            mock_fs.return_value = store_instance
            resp = filestore_app.get("/admin/filestore/read", params={"path": "/dir"})
            assert resp.status_code == 200
            data = resp.json()
            assert data["is_dir"] is True
            assert "entries" in data

    def test_read_nonexistent_path(self, filestore_app):
        with patch("general_ludd.routers.filestore.FileStore") as mock_fs:
            store_instance = Mock()
            store_instance.exists.return_value = False
            mock_fs.return_value = store_instance
            resp = filestore_app.get("/admin/filestore/read", params={"path": "/nonexistent"})
            assert resp.status_code == 200
            data = resp.json()
            assert "error" in data

    def test_read_binary_file_handled(self, filestore_app):
        with patch("general_ludd.routers.filestore.FileStore") as mock_fs:
            store_instance = Mock()
            store_instance.exists.return_value = True
            store_instance.is_dir.return_value = False
            store_instance.read_text.side_effect = Exception("binary")
            mock_fs.return_value = store_instance
            resp = filestore_app.get("/admin/filestore/read", params={"path": "/image.png"})
            assert resp.status_code == 200
            data = resp.json()
            assert data.get("binary") is True or "error" not in data


class TestFilestoreWrite:
    def test_write_text_content(self, filestore_app):
        with patch("general_ludd.routers.filestore.FileStore") as mock_fs:
            store_instance = Mock()
            store_instance.write_text.return_value = None
            mock_fs.return_value = store_instance
            resp = filestore_app.post(
                "/admin/filestore/write",
                json={"path": "/new_file.txt", "content": "some content"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["success"] is True

    def test_write_empty_path(self, filestore_app):
        resp = filestore_app.post(
            "/admin/filestore/write",
            json={"path": "", "content": "data"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is False or "error" in data

    def test_write_large_content_rejected(self, filestore_app):
        large_content = "x" * (11 * 1024 * 1024)
        resp = filestore_app.post(
            "/admin/filestore/write",
            json={"path": "/large.txt", "content": large_content},
        )
        assert resp.status_code == 413


class TestFilestoreRemove:
    def test_remove_existing_file(self, filestore_app):
        with patch("general_ludd.routers.filestore.FileStore") as mock_fs:
            store_instance = Mock()
            store_instance.exists.return_value = True
            store_instance.remove.return_value = None
            mock_fs.return_value = store_instance
            resp = filestore_app.delete(
                "/admin/filestore/remove",
                params={"path": "/to_delete.txt"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["success"] is True

    def test_remove_nonexistent_file(self, filestore_app):
        with patch("general_ludd.routers.filestore.FileStore") as mock_fs:
            store_instance = Mock()
            store_instance.exists.return_value = False
            mock_fs.return_value = store_instance
            resp = filestore_app.delete(
                "/admin/filestore/remove",
                params={"path": "/gone.txt"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert "error" in data

    def test_remove_empty_path(self, filestore_app):
        resp = filestore_app.delete(
            "/admin/filestore/remove",
            params={"path": ""},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("success") is False or "error" in data


class TestFilestoreBootstrap:
    def test_bootstrap_unknown_binary_returns_error(self, filestore_app):
        with patch("general_ludd.routers.filestore.FileStore") as mock_fs:
            store_instance = Mock()
            boot_instance = Mock()
            boot_instance.get_known_versions.return_value = {"openbao": "1.0.0"}
            mock_fs.return_value = store_instance
            with patch("general_ludd.routers.filestore.BinaryBootstrapper") as mock_boot:
                mock_boot.return_value = boot_instance
                resp = filestore_app.post(
                    "/admin/filestore/bootstrap",
                    json={"binary": "unknown-tool"},
                )
                assert resp.status_code == 200
                data = resp.json()
                assert data["success"] is False
                assert "error" in data
                assert "known" in data

    def test_bootstrap_openbao(self, filestore_app):
        with patch("general_ludd.routers.filestore.FileStore") as mock_fs:
            store_instance = Mock()
            boot_instance = Mock()
            boot_instance.download_openbao = AsyncMock(return_value=True)
            boot_instance.check_openbao_in_store.return_value = True
            mock_fs.return_value = store_instance
            with patch("general_ludd.routers.filestore.BinaryBootstrapper") as mock_boot:
                mock_boot.return_value = boot_instance
                resp = filestore_app.post(
                    "/admin/filestore/bootstrap",
                    json={"binary": "openbao"},
                )
                assert resp.status_code == 200
                data = resp.json()
                assert data["success"] is True
                assert data["binary"] == "openbao"


class TestFilestoreBinaries:
    def test_list_binaries(self, filestore_app):
        with patch("general_ludd.routers.filestore.FileStore") as mock_fs:
            store_instance = Mock()
            boot_instance = Mock()
            boot_instance.list_binaries.return_value = ["openbao", "opentofu"]
            mock_fs.return_value = store_instance
            with patch("general_ludd.routers.filestore.BinaryBootstrapper") as mock_boot:
                mock_boot.return_value = boot_instance
                resp = filestore_app.get("/admin/filestore/binaries")
                assert resp.status_code == 200
                data = resp.json()
                assert "binaries" in data
                assert data["count"] >= 2

    def test_list_binaries_empty(self, filestore_app):
        with patch("general_ludd.routers.filestore.FileStore") as mock_fs:
            store_instance = Mock()
            boot_instance = Mock()
            boot_instance.list_binaries.return_value = []
            mock_fs.return_value = store_instance
            with patch("general_ludd.routers.filestore.BinaryBootstrapper") as mock_boot:
                mock_boot.return_value = boot_instance
                resp = filestore_app.get("/admin/filestore/binaries")
                assert resp.status_code == 200
                data = resp.json()
                assert data["count"] == 0
