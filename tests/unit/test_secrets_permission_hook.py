"""PermissionSpec -> SecretsManager enforcement hook.

Pins the contract that an agent's PermissionSpec constrains which OpenBao paths
its SecretsManager may read/write/list/delete. ``permission_spec=None`` keeps
back-compat (no enforcement). A non-None spec gates every hvac-bound call via a
glob match against the ``secret:openbao`` capability's ``openbao_paths``
constraints, raising :class:`SecretPermissionDeniedError` (which carries the
SANITIZED allow-list so callers can introspect "what COULD I have read?").
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from general_ludd.secrets.config import OpenBaoConfig
from general_ludd.secrets.manager import (
    SecretAlias,
    SecretPermissionDeniedError,
    SecretsManager,
)
from general_ludd.security.permissions import (
    Capability,
    PermissionSpec,
    default_spec,
)


def _mock_client(store=None):
    # Use a shared dict so write side-effects are observable to the caller.
    # Do NOT copy: tests like test_write_allowed_when_write_in_actions rely on
    # the same dict reference being mutated by the create_or_update_secret mock.
    if store is None:
        store = {}
    client = MagicMock()
    client.is_authenticated.return_value = True

    def _read(path, mount_point="secret"):
        if path not in store:
            import hvac

            raise hvac.exceptions.InvalidPath(path)
        return {"data": {"data": store[path]}}

    def _create(path, secret, mount_point="secret"):
        store[path] = dict(secret)

    def _delete(path, mount_point="secret"):
        store.pop(path, None)

    def _list(path, mount_point="secret"):
        return {"data": {"keys": list(store.keys())}}

    client.secrets.kv.v2.read_secret_version.side_effect = _read
    client.secrets.kv.v2.create_or_update_secret.side_effect = _create
    client.secrets.kv.v2.delete_metadata_and_all_versions.side_effect = _delete
    client.secrets.kv.v2.list_metadata.side_effect = _list
    return client


def _build_spec(paths, actions):
    return PermissionSpec(
        agent_type="build",
        capabilities=[
            Capability(
                resource="secret:openbao",
                actions=list(actions),
                constraints={"openbao_paths": list(paths)},
            )
        ],
    )


class TestReadEnforcement:
    def test_read_secret_allowed_by_spec_passes(self):
        store = {"secret/data/gludd/build/foo": {"value": "bar"}}
        client = _mock_client(store)
        spec = _build_spec(["secret/data/gludd/build/*"], ["read"])
        mgr = SecretsManager(
            client=client,
            config=OpenBaoConfig(kv_mount="secret"),
            permission_spec=spec,
        )
        result = mgr.read_secret("secret/data/gludd/build/foo")
        assert result == {"value": "bar"}

    def test_read_secret_denied_by_spec_raises_permission_error(self):
        client = _mock_client({"secret/data/gludd/shared/llm_keys/x": {"k": "v"}})
        spec = _build_spec(["secret/data/gludd/build/*"], ["read"])
        mgr = SecretsManager(
            client=client,
            config=OpenBaoConfig(kv_mount="secret"),
            permission_spec=spec,
        )
        with pytest.raises(SecretPermissionDeniedError):
            mgr.read_secret("secret/data/gludd/shared/llm_keys/x")

    def test_glob_pattern_matches_subpaths(self):
        store = {"secret/data/gludd/build/nested/deep": {"v": "1"}}
        client = _mock_client(store)
        spec = _build_spec(["secret/data/gludd/build/*"], ["read"])
        mgr = SecretsManager(
            client=client,
            config=OpenBaoConfig(kv_mount="secret"),
            permission_spec=spec,
        )
        assert mgr.read_secret("secret/data/gludd/build/nested/deep") == {"v": "1"}


class TestWriteListDeleteEnforcement:
    def test_write_requires_write_action(self):
        client = _mock_client()
        spec = _build_spec(["secret/data/gludd/build/*"], ["read"])
        mgr = SecretsManager(
            client=client,
            config=OpenBaoConfig(kv_mount="secret"),
            permission_spec=spec,
        )
        with pytest.raises(SecretPermissionDeniedError):
            mgr.write_secret("secret/data/gludd/build/foo", {"x": "1"})

    def test_write_allowed_when_write_in_actions(self):
        store = {}
        client = _mock_client(store)
        spec = _build_spec(["secret/data/gludd/build/*"], ["read", "write"])
        mgr = SecretsManager(
            client=client,
            config=OpenBaoConfig(kv_mount="secret"),
            permission_spec=spec,
        )
        mgr.write_secret("secret/data/gludd/build/foo", {"x": "1"})
        assert store == {"secret/data/gludd/build/foo": {"x": "1"}}

    def test_delete_requires_delete_action(self):
        client = _mock_client({"secret/data/gludd/build/foo": {}})
        spec = _build_spec(["secret/data/gludd/build/*"], ["read"])
        mgr = SecretsManager(
            client=client,
            config=OpenBaoConfig(kv_mount="secret"),
            permission_spec=spec,
        )
        with pytest.raises(SecretPermissionDeniedError):
            mgr.delete_secret("secret/data/gludd/build/foo")


class TestBackcompatAndIntrospection:
    def test_no_spec_means_no_enforcement(self):
        store = {"secret/data/anywhere/x": {"v": "1"}}
        client = _mock_client(store)
        mgr = SecretsManager(
            client=client,
            config=OpenBaoConfig(kv_mount="secret"),
            permission_spec=None,
        )
        assert mgr.read_secret("secret/data/anywhere/x") == {"v": "1"}

    def test_permission_error_includes_allow_list_for_introspection(self):
        client = _mock_client()
        spec = _build_spec(
            ["secret/data/gludd/build/*", "secret/data/gludd/shared/llm_keys/*"],
            ["read"],
        )
        mgr = SecretsManager(
            client=client,
            config=OpenBaoConfig(kv_mount="secret"),
            permission_spec=spec,
        )
        with pytest.raises(SecretPermissionDeniedError) as ei:
            mgr.read_secret("secret/data/forbidden/x")
        msg = str(ei.value)
        assert "secret/data/gludd/build/*" in msg
        assert "secret/data/gludd/shared/llm_keys/*" in msg
        assert "secret/data/forbidden/x" in msg
        assert "build" in msg


class TestDefaultSpecs:
    def test_default_build_spec_can_read_build_paths(self):
        store = {"secret/data/gludd/build/cosign": {"v": "1"}}
        client = _mock_client(store)
        mgr = SecretsManager(
            client=client,
            config=OpenBaoConfig(kv_mount="secret"),
            permission_spec=default_spec("build"),
        )
        assert mgr.read_secret("secret/data/gludd/build/cosign") == {"v": "1"}

    def test_default_build_spec_cannot_read_outside_build(self):
        client = _mock_client({"secret/data/gludd/shared/llm_keys/x": {}})
        mgr = SecretsManager(
            client=client,
            config=OpenBaoConfig(kv_mount="secret"),
            permission_spec=default_spec("build"),
        )
        with pytest.raises(SecretPermissionDeniedError):
            mgr.read_secret("secret/data/gludd/shared/llm_keys/x")

    def test_default_primary_spec_can_read_any_gludd_path(self):
        store = {
            "secret/data/gludd/build/x": {"v": "1"},
            "secret/data/gludd/shared/llm_keys/y": {"v": "2"},
        }
        client = _mock_client(store)
        mgr = SecretsManager(
            client=client,
            config=OpenBaoConfig(kv_mount="secret"),
            permission_spec=default_spec("primary"),
        )
        assert mgr.read_secret("secret/data/gludd/build/x") == {"v": "1"}
        assert mgr.read_secret("secret/data/gludd/shared/llm_keys/y") == {"v": "2"}

    def test_default_subagent_spec_has_no_openbao_capability(self):
        client = _mock_client({"secret/data/gludd/build/x": {}})
        mgr = SecretsManager(
            client=client,
            config=OpenBaoConfig(kv_mount="secret"),
            permission_spec=default_spec("subagent"),
        )
        with pytest.raises(SecretPermissionDeniedError):
            mgr.read_secret("secret/data/gludd/build/x")


class TestResolveEnforcement:
    """resolve() (alias reads) must honour the same secret:openbao gate.

    Regression: resolve() historically skipped ``_enforce_permission``, so a
    SecretsManager scoped by a permission_spec still served alias reads whose
    underlying path was OUTSIDE the allow-list. An alias read IS a read of
    ``alias.path`` and must pass the identical ``action="read"`` gate that
    read_secret/write_secret/delete_secret/list_secrets already enforce.
    """

    def test_resolve_denied_by_spec_raises_permission_error(self):
        client = _mock_client(
            {"secret/data/gludd/shared/llm_keys/x": {"value": "sekret"}}
        )
        spec = _build_spec(["secret/data/gludd/build/*"], ["read"])
        mgr = SecretsManager(
            client=client,
            config=OpenBaoConfig(kv_mount="secret"),
            permission_spec=spec,
        )
        mgr.register_alias(
            SecretAlias(alias="llm", path="secret/data/gludd/shared/llm_keys/x")
        )
        with pytest.raises(SecretPermissionDeniedError):
            mgr.resolve("llm")

    def test_resolve_allowed_by_spec_returns_value(self):
        client = _mock_client({"secret/data/gludd/build/foo": {"value": "bar"}})
        spec = _build_spec(["secret/data/gludd/build/*"], ["read"])
        mgr = SecretsManager(
            client=client,
            config=OpenBaoConfig(kv_mount="secret"),
            permission_spec=spec,
        )
        mgr.register_alias(
            SecretAlias(alias="buildfoo", path="secret/data/gludd/build/foo")
        )
        assert mgr.resolve("buildfoo") == "bar"

    def test_resolve_no_spec_returns_value_unchanged(self):
        client = _mock_client({"secret/data/anywhere/x": {"value": "openval"}})
        mgr = SecretsManager(
            client=client,
            config=OpenBaoConfig(kv_mount="secret"),
            permission_spec=None,
        )
        mgr.register_alias(
            SecretAlias(alias="anyx", path="secret/data/anywhere/x")
        )
        assert mgr.resolve("anyx") == "openval"


class TestSTSIntegration:
    def test_sts_token_scopes_secret_manager(self):
        from general_ludd.security.sts import STSRegistry

        store = {
            "secret/data/gludd/build/allowed": {"v": "1"},
            "secret/data/gludd/shared/llm_keys/secret": {"v": "2"},
        }
        client = _mock_client(store)

        narrow_spec = _build_spec(["secret/data/gludd/build/*"], ["read"])
        registry = STSRegistry()
        token_id = registry.issue(agent_type="subagent", spec=narrow_spec)

        claim = registry.resolve(token_id)
        assert claim is not None
        mgr = SecretsManager(
            client=client,
            config=OpenBaoConfig(kv_mount="secret"),
            permission_spec=claim.spec,
        )
        assert mgr.read_secret("secret/data/gludd/build/allowed") == {"v": "1"}
        with pytest.raises(SecretPermissionDeniedError):
            mgr.read_secret("secret/data/gludd/shared/llm_keys/secret")
