"""Tests for per-project secret scoping via _LazyProjectSecrets and ProjectSecretsManager."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from general_ludd.infra.deployment import SecretsResolver
from general_ludd.secrets.env import EnvSecretsManager
from general_ludd.secrets.project_secrets import ProjectSecretsManager


def _build_with_active_projects(base: object):
    from general_ludd.daemon import build_secrets_resolver

    class _FakeOpenBaoConfig:
        mode = None

    resolver = build_secrets_resolver(
        openbao_config=_FakeOpenBaoConfig(),
        env_overrides={"BUILTIN_KEY": "override-val"},
        projects_active=True,
    )
    return resolver


class TestProjectSecretsManagerScoping:
    def test_scoped_path_prepends_project_prefix(self):
        base = MagicMock()
        pm = ProjectSecretsManager(base_manager=base, project_id="myproj")
        scoped = pm._scoped_path("my-alias")
        assert scoped == "projects/myproj/my-alias"

    def test_scoped_path_rejects_traversal_in_path(self):
        base = MagicMock()
        pm = ProjectSecretsManager(base_manager=base, project_id="myproj")
        with pytest.raises(ValueError, match="escapes project scope"):
            pm._scoped_path("../../other-proj/secret")

    def test_ctor_rejects_project_id_with_slash(self):
        base = MagicMock()
        with pytest.raises(ValueError, match="must not contain"):
            ProjectSecretsManager(base_manager=base, project_id="my/proj")

    def test_ctor_rejects_project_id_with_dotdot(self):
        base = MagicMock()
        with pytest.raises(ValueError, match="must not contain"):
            ProjectSecretsManager(base_manager=base, project_id="..other")

    def test_write_secret_scopes_path(self):
        base = MagicMock()
        pm = ProjectSecretsManager(base_manager=base, project_id="proj-a")
        pm.write_secret("key1", {"value": "secret1"})
        base.write_secret.assert_called_once_with("projects/proj-a/key1", {"value": "secret1"})

    def test_read_secret_scopes_path(self):
        base = MagicMock()
        pm = ProjectSecretsManager(base_manager=base, project_id="proj-a")
        pm.read_secret("key1")
        base.read_secret.assert_called_once_with("projects/proj-a/key1")

    def test_delete_secret_scopes_path(self):
        base = MagicMock()
        pm = ProjectSecretsManager(base_manager=base, project_id="proj-a")
        pm.delete_secret("key1")
        base.delete_secret.assert_called_once_with("projects/proj-a/key1")

    def test_resolve_reads_scoped_then_falls_back_to_base(self):
        base = MagicMock()
        base.read_secret.return_value = {"value": "scoped-val"}
        pm = ProjectSecretsManager(base_manager=base, project_id="proj-a")
        result = pm.resolve("my-credential")
        assert result == "scoped-val"
        base.read_secret.assert_called_once_with("projects/proj-a/my-credential")
        base.resolve.assert_not_called()

    def test_resolve_falls_back_to_base_when_scoped_missing(self):
        base = MagicMock()
        base.read_secret.return_value = None
        base.resolve.return_value = "shared-fallback"
        pm = ProjectSecretsManager(base_manager=base, project_id="proj-a")
        result = pm.resolve("my-credential")
        assert result == "shared-fallback"
        base.read_secret.assert_called_once()
        base.resolve.assert_called_once_with("my-credential")

    def test_resolve_skips_read_secret_when_base_has_no_read_secret(self):
        base = MagicMock(spec=["resolve"])
        base.resolve.return_value = "env-fallback"
        pm = ProjectSecretsManager(base_manager=base, project_id="proj-a")
        result = pm.resolve("my-credential")
        assert result == "env-fallback"
        base.resolve.assert_called_once_with("my-credential")

    def test_resolve_scoped_value_not_dict_is_skipped(self):
        base = MagicMock()
        base.read_secret.return_value = "not-a-dict"
        base.resolve.return_value = "base-fallback"
        pm = ProjectSecretsManager(base_manager=base, project_id="proj-a")
        result = pm.resolve("my-credential")
        assert result == "base-fallback"

    def test_resolve_scoped_dict_value_not_string_is_skipped(self):
        base = MagicMock()
        base.read_secret.return_value = {"value": 42}
        base.resolve.return_value = "base-fallback"
        pm = ProjectSecretsManager(base_manager=base, project_id="proj-a")
        result = pm.resolve("my-credential")
        assert result == "base-fallback"


class TestLazyProjectSecretsIntegration:
    def test_resolve_without_project_id_passes_through(self):
        resolver = _build_with_active_projects(MagicMock())
        assert resolver.resolve("BUILTIN_KEY") == "override-val"

    def test_resolve_with_project_id_delegates_to_for_project(self):
        resolver = _build_with_active_projects(MagicMock())
        pm = resolver.for_project("my-project")
        assert isinstance(pm, ProjectSecretsManager)
        assert pm._project_id == "my-project"

    def test_resolve_with_project_id_via_resolver_uses_scoping(self):
        resolver = _build_with_active_projects(MagicMock())
        result = resolver.resolve("BUILTIN_KEY", project_id="proj-x")
        assert result == "override-val"

    def test_resolve_project_id_none_uses_base_directly(self):
        resolver = _build_with_active_projects(MagicMock())
        result = resolver.resolve("BUILTIN_KEY", project_id=None)
        assert result == "override-val"

    def test_resolve_project_id_empty_string_uses_base_directly(self):
        resolver = _build_with_active_projects(MagicMock())
        result = resolver.resolve("BUILTIN_KEY", project_id="")
        assert result == "override-val"

    def test_for_project_rejects_invalid_id(self):
        resolver = _build_with_active_projects(MagicMock())
        with pytest.raises(ValueError):
            resolver.for_project("bad/id")

    def test_projects_inactive_returns_envsecretsmanager(self):
        from general_ludd.daemon import build_secrets_resolver

        resolver = build_secrets_resolver(
            openbao_config=None,
            env_overrides={"TEST_KEY": "val"},
            projects_active=False,
        )
        assert isinstance(resolver, EnvSecretsManager)
        assert resolver.resolve("TEST_KEY") == "val"

    def test_projects_active_has_for_project(self):
        resolver = _build_with_active_projects(MagicMock())
        assert hasattr(resolver, "for_project")
        assert callable(resolver.for_project)


class TestSecretsResolverProtocolCompatibility:
    def test_protocol_accepts_project_id_keyword(self):
        resolver = MagicMock()
        resolver.resolve.return_value = "result"
        sr: SecretsResolver = resolver
        val = sr.resolve("alias", project_id=None)
        assert val == "result"

    def test_protocol_accepts_project_id_argument(self):
        resolver = MagicMock()
        resolver.resolve.return_value = "scoped"
        sr: SecretsResolver = resolver
        val = sr.resolve("alias", project_id="proj-c")
        assert val == "scoped"

    def test_protocol_resolve_without_project_id_still_works(self):
        resolver = MagicMock()
        resolver.resolve.return_value = "val"
        sr: SecretsResolver = resolver
        val = sr.resolve("alias")
        assert val == "val"
        resolver.resolve.assert_called_once_with("alias")
