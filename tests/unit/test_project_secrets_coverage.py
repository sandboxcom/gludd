from __future__ import annotations

from unittest.mock import MagicMock

from general_ludd.secrets.project_secrets import ProjectSecretsManager


class TestInit:
    def test_sets_base_and_project_id(self):
        base = MagicMock()
        mgr = ProjectSecretsManager(base, "proj-42")
        assert mgr._base is base
        assert mgr._project_id == "proj-42"


class TestScopedPath:
    def test_scoped_path(self):
        base = MagicMock()
        mgr = ProjectSecretsManager(base, "abc")
        assert mgr._scoped_path("my/key") == "projects/abc/my/key"


class TestWriteSecret:
    def test_calls_base_with_scoped_path(self):
        base = MagicMock()
        mgr = ProjectSecretsManager(base, "p1")
        value = {"token": "xyz"}
        mgr.write_secret("creds", value)
        base.write_secret.assert_called_once_with("projects/p1/creds", value)


class TestReadSecret:
    def test_calls_base_and_returns_result(self):
        base = MagicMock()
        base.read_secret.return_value = {"key": "val"}
        mgr = ProjectSecretsManager(base, "p2")
        result = mgr.read_secret("path")
        base.read_secret.assert_called_once_with("projects/p2/path")
        assert result == {"key": "val"}


class TestReadSecretReturnsNone:
    def test_returns_none_when_base_returns_none(self):
        base = MagicMock()
        base.read_secret.return_value = None
        mgr = ProjectSecretsManager(base, "p3")
        assert mgr.read_secret("missing") is None


class TestResolve:
    def test_calls_base_and_returns_result(self):
        base = MagicMock()
        base.resolve.return_value = "resolved-value"
        mgr = ProjectSecretsManager(base, "p4")
        result = mgr.resolve("my-alias")
        base.resolve.assert_called_once_with("my-alias")
        assert result == "resolved-value"


class TestResolveReturnsNone:
    def test_returns_none_when_base_returns_none(self):
        base = MagicMock()
        base.resolve.return_value = None
        mgr = ProjectSecretsManager(base, "p5")
        assert mgr.resolve("nope") is None


class TestDeleteSecret:
    def test_calls_base_with_scoped_path(self):
        base = MagicMock()
        mgr = ProjectSecretsManager(base, "p6")
        mgr.delete_secret("old/key")
        base.delete_secret.assert_called_once_with("projects/p6/old/key")
