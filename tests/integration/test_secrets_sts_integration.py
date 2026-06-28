"""End-to-end: STS token narrows secret access for the duration of a request.

Seeds two secrets in a mock OpenBao KV store, then constructs two SecretsManagers
against the same store:

1. A "build" agent using ``default_spec("build")`` — can read both
   ``secret/data/gludd/build/*`` and is DENIED ``secret/data/gludd/shared/*``.
2. A "subagent" carrying an STS token whose spec whitelists ONLY the build
   subtree — same effective access for the whitelisted path, and denial on
   the shared path.

The contract pinned: STS narrowing is ADDITIVE with the default spec model —
a narrow STS spec scopes the manager exactly to its ``openbao_paths`` list,
regardless of how broad the agent type's default would be.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from general_ludd.secrets.config import OpenBaoConfig
from general_ludd.secrets.manager import (
    SecretPermissionDeniedError,
    SecretsManager,
)
from general_ludd.security.permissions import (
    Capability,
    PermissionSpec,
    default_spec,
)
from general_ludd.security.sts import STSRegistry


def _seeded_client():
    store = {
        "secret/data/gludd/build/cosign": {"password": "hunter2"},
        "secret/data/gludd/shared/llm_keys/anthropic": {"key": "sk-AAAA"},
    }
    client = MagicMock()
    client.is_authenticated.return_value = True

    def _read(path, mount_point="secret"):
        if path not in store:
            import hvac
            raise hvac.exceptions.InvalidPath(path)
        return {"data": {"data": store[path]}}

    client.secrets.kv.v2.read_secret_version.side_effect = _read
    return client, store


class TestEndToEndScoping:
    def test_build_agent_reads_build_path_not_shared(self):
        client, _ = _seeded_client()
        mgr = SecretsManager(
            client=client,
            config=OpenBaoConfig(kv_mount="secret"),
            permission_spec=default_spec("build"),
        )
        assert mgr.read_secret("secret/data/gludd/build/cosign") == {"password": "hunter2"}
        with pytest.raises(SecretPermissionDeniedError):
            mgr.read_secret("secret/data/gludd/shared/llm_keys/anthropic")

    def test_primary_agent_reads_both_paths(self):
        client, _ = _seeded_client()
        mgr = SecretsManager(
            client=client,
            config=OpenBaoConfig(kv_mount="secret"),
            permission_spec=default_spec("primary"),
        )
        assert mgr.read_secret("secret/data/gludd/build/cosign") == {"password": "hunter2"}
        assert mgr.read_secret("secret/data/gludd/shared/llm_keys/anthropic") == {"key": "sk-AAAA"}

    def test_subagent_with_narrow_sts_reads_only_whitelisted(self):
        client, _ = _seeded_client()
        narrow_spec = PermissionSpec(
            agent_type="subagent",
            capabilities=[
                Capability(
                    resource="secret:openbao",
                    actions=["read"],
                    constraints={"openbao_paths": ["secret/data/gludd/build/*"]},
                )
            ],
        )
        registry = STSRegistry()
        token = registry.issue(agent_type="subagent", spec=narrow_spec, ttl_seconds=60)
        claim = registry.resolve(token)
        assert claim is not None
        assert claim.spec is narrow_spec

        mgr = SecretsManager(
            client=client,
            config=OpenBaoConfig(kv_mount="secret"),
            permission_spec=claim.spec,
        )
        # Whitelisted path readable.
        assert mgr.read_secret("secret/data/gludd/build/cosign") == {"password": "hunter2"}
        # Shared path denied even though the underlying store has it.
        with pytest.raises(SecretPermissionDeniedError):
            mgr.read_secret("secret/data/gludd/shared/llm_keys/anthropic")

    def test_sts_token_expiry_rejects_resolve(self):
        _client, _ = _seeded_client()
        registry = STSRegistry()
        # Issue with ttl=0 so it is expired immediately.
        token = registry.issue(
            agent_type="subagent",
            spec=default_spec("subagent"),
            ttl_seconds=0,
        )
        # Resolve must return None — the token is expired.
        assert registry.resolve(token) is None
