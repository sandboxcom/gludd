# Permission System — OpenBao Integration

This document describes how an agent's `PermissionSpec` constrains which OpenBao
secrets its `SecretsManager` may read, write, list, or delete.

## Capability model

The `secret:openbao` capability resource is the integration point between
`general_ludd.security.permissions.PermissionSpec` and the hvac-backed
`general_ludd.secrets.manager.SecretsManager`:

```python
Capability(
    resource="secret:openbao",
    actions=["read", "write", "list", "delete"],   # any subset
    constraints={
        "openbao_paths": [
            "secret/data/gludd/build/*",
            "secret/data/gludd/shared/llm_keys/*",
        ],
    },
)
```

The capability carries:

- `actions`: the verbs the agent may invoke on the hvac client. A spec with
  `actions=["read"]` cannot `write_secret`, `delete_secret`, or `list_secrets`.
- `constraints["openbao_paths"]`: a list of glob patterns matched against the
  full OpenBao KV v2 path before the call is delegated to hvac.

## Path matching

Path allow-listing uses `fnmatch.fnmatchcase`, so `*` matches **across** `/`
separators. This is deliberate: callers expect shell-glob semantics for path
trees. So `secret/data/gludd/build/*` matches:

- `secret/data/gludd/build/cosign`
- `secret/data/gludd/build/cosign/key`
- `secret/data/gludd/build/nested/deep/path`

Matching is **case-sensitive** (`fnmatchcase`, not `fnmatch`) so paths cannot be
obfuscated by case variation.

## Enforcement point

`SecretsManager._enforce_permission(path, action)` is called BEFORE every
`read_secret`, `write_secret`, `delete_secret`, and `list_secrets` call. When
the spec denies access, it raises `SecretPermissionDeniedError`, which inherits
from `SecretsUnavailableError` so fail-closed callers automatically intercept
permission denials.

Backward compatibility: `permission_spec=None` (the default) means **no
enforcement**. Existing callers (admin PSK-authenticated daemon subsystems, the
internal integrity scanner, etc.) are unaffected.

## Introspection

The error carries the SANITIZED allow-list (glob patterns only — never secret
values) so a caller can ask "what paths COULD I have read?" without re-issuing
the request:

```python
try:
    mgr.read_secret("secret/data/forbidden/x")
except SecretPermissionDeniedError as exc:
    print(exc.path, exc.action, exc.agent_type, exc.allowed_patterns)
```

## STS scoping flow

A subagent carries NO `secret:openbao` capability by default. Secret access
must be granted explicitly via a short-lived security token:

1. **Mint.** The orchestrator mints an STS token bound to a narrow spec:
   ```python
   from general_ludd.security.sts import STSRegistry
   from general_ludd.security.permissions import Capability, PermissionSpec

   spec = PermissionSpec(
       agent_type="subagent",
       capabilities=[Capability(
           resource="secret:openbao",
           actions=["read"],
           constraints={"openbao_paths": ["secret/data/gludd/build/*"]},
       )],
   )
   token = STSRegistry().issue(agent_type="subagent", spec=spec, ttl_seconds=300)
   ```
2. **Present.** The subagent presents the token via `Authorization: Bearer <sts>`.
3. **Resolve.** The daemon resolves the token to its `STSClaim`, reconstructs a
   `SecretsManager(client=shared_client, permission_spec=claim.spec)` for the
   duration of the request, and discards it after.
4. **Enforce.** Every `read_secret` / `write_secret` / `delete_secret` /
   `list_secrets` call on that manager consults the spec.

An expired, revoked, or unknown token resolves to `None` — callers fail closed.

## Default specs

| agent_type | openbao_paths | actions |
|---|---|---|
| `build` | `secret/data/gludd/build/*` | `read` |
| `primary` | `secret/data/gludd/*` | `read` |
| `subagent` | _(none)_ | _(none)_ |

The `subagent` default is deliberately empty. Secret access for subagents is
**always** granted explicitly via STS, never inherited.
