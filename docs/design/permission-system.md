# Permission System

> Status: design doc / reference. Implements the contract surface defined in `src/general_ludd/security/permissions.py` and `src/general_ludd/security/sts.py`. The shipped defaults live in code (`_build_default_spec`, `_primary_default_spec`, `_subagent_default_spec`); per-agent YAML overrides are loaded from `config/permissions/*.yml` when present.

## 1. Overview

The permission system gives every agent in the general-ludd daemon a typed, immutable description of what it is allowed to do — a `PermissionSpec` — and lets it mint short-lived security tokens (STS) that delegate a **same-or-fewer** subset of its own authority to a subagent. Each spec enumerates `Capability` grants over named resources (`secret:openbao`, `file:`, `net:`), each scoped by backend-specific constraints (path globs, host lists, port lists). Specs are parsed from YAML at the system level by `PermissionSpecParser`, validated against the `_RESOURCE_CONSTRAINTS` registry, and consulted at runtime — `SecretsManager` checks the active spec before every hvac call, and `StsIssuer.issue()` refuses to mint a token whose spec is not a subset of the issuer's spec. Every issue/use/expiry/revoke event is written to the `StsAuditLog` for forensic review.

The authority chain is **strictly narrowing**: a `build` agent can read only `secret/data/gludd/build/*`; a `primary` agent can read `secret/data/gludd/*`; a `subagent` has **no** secret capability by default and must receive one via an STS token whose spec is a subset of its issuer's.

## 2. YAML Format

Specs are authored as YAML and parsed by `PermissionSpecParser.parse` / `parse_file`. The canonical shape (mirroring `config/permissions/primary.yml` / the `_primary_default_spec` factory) is:

```yaml
# Schema version. Currently 1. The parser defaults to 1 when omitted.
version: 1

# The agent type this spec binds to ("build" / "primary" / "subagent" or a custom type). Used by default_spec() and surfaced on STS tokens.
agent_type: primary

# The agent_id of the issuer, when this spec arrived via an STS token. null for top-level specs loaded from config/permissions/*.yml.
parent_agent_id: null

# Hard ceiling on ttl_seconds for any STS token minted FROM this spec. StsIssuer.issue() clamps the requested ttl to this value.
max_sts_ttl_seconds: 3600

# Delegation policy. Only "same_or_fewer" is currently implemented: an STS token's spec MUST be a subset of the issuer's spec (see §5).
max_subagent_permissions: "same_or_fewer"

# Positive grants. Each capability is (resource, actions, constraints). A capability is meaningless without at least one action and the required constraint keys for its resource family (see §3, §4).
capabilities:
  - resource: secret:openbao
    actions: ["read"]
    constraints:
      # Glob list — fnmatch.fnmatchcase semantics. "*" matches across "/".
      openbao_paths:
        - "secret/data/gludd/*"

  - resource: file:repo
    actions: ["read", "write"]
    constraints:
      # The subject may only touch paths under this prefix.
      path_prefix: "/repo/"

  - resource: net:egress:llm_api
    actions: ["connect"]
    constraints:
      allowed_hosts: ["api.anthropic.com"]
      allowed_ports: [443]

# Negative grants. A capability listed here is explicitly revoked even if a broader capability in `capabilities` would otherwise permit it. The validator rejects any (resource, action) pair that appears in BOTH lists (see §4) — denied is for carving exceptions, not for redundant restatement.
denied: []
```

### Field reference

| Field | Type | Default | Meaning |
|---|---|---|---|
| `version` | `int` | `1` | Schema version. |
| `agent_type` | `str` | *(required)* | The agent type this spec binds. |
| `parent_agent_id` | `str \| null` | `null` | Issuer's agent_id when the spec arrived via STS. |
| `capabilities` | `list[Capability]` | `[]` | Positive grants. |
| `capabilities[].resource` | `str` | *(required)* | Dotted resource name (`secret:openbao`, `file:repo`, `net:egress:llm_api`). |
| `capabilities[].actions` | `list[str]` | `[]` | Verbs (`read`, `write`, `list`, `delete`, `connect`, …). Order insignificant. |
| `capabilities[].constraints` | `dict[str, Any]` | `{}` | Backend-specific scope; required keys per family (§3). |
| `denied` | `list[Capability]` | `[]` | Negative grants (same shape as `capabilities`). |
| `max_sts_ttl_seconds` | `int` | `3600` | Ceiling on STS ttl minted from this spec. |
| `max_subagent_permissions` | `str` | `"same_or_fewer"` | Delegation policy. |

## 3. Resource Vocabulary

The `_RESOURCE_CONSTRAINTS` registry in `permissions.py` declares which constraint keys each resource family requires. A capability whose `resource` does not start with one of these prefixes is rejected by `validate()` as an unknown resource type.

| Resource prefix | Required constraint key(s) | Example |
|---|---|---|
| `file:` | `path_prefix` | `{"path_prefix": "/tmp/gludd/"}` |
| `net:` | `allowed_hosts` **and/or** `allowed_ports` (at least one) | `{"allowed_hosts": ["api.anthropic.com"]}` |
| `secret:openbao` | `openbao_paths` | `{"openbao_paths": ["secret/data/gludd/build/*"]}` |

Notes:
- `net:egress:llm_api` is a commonly-used narrower tag under the `net:` family — it carries the same `allowed_hosts` / `allowed_ports` constraints but identifies outbound LLM API traffic specifically. Any resource starting with `net:` is validated by the same rule.
- For `net:` the validator requires **at least one** of `allowed_hosts` / `allowed_ports` (a `net:` capability with neither is meaningless).
- For `file:` the narrowness relation (§5) uses `path_prefix.startswith()` — a longer prefix is a narrower scope.
- For `secret:openbao`, `openbao_paths` is a glob list matched with `fnmatch.fnmatchcase` (so `*` matches across `/`), allowing patterns like `secret/data/gludd/build/*` to cover a subtree.

## 4. Parsing & Validation

`PermissionSpecParser` exposes three static methods:

- **`parse(yaml_str: str) -> PermissionSpec`** — `yaml.safe_load` the string and build a frozen `PermissionSpec`. Missing fields fall back to the defaults listed in §2. Unknown fields are silently dropped (forward-compat).
- **`parse_file(path: str \| Path) -> PermissionSpec`** — read the file and delegate to `parse`.
- **`validate(spec: PermissionSpec) -> list[str]`** — return a list of human-readable error strings; empty list means the spec is well-formed.

### Validation rules (each produces one error string)

1. **Unknown resource type.** A capability whose `resource` does not start with any key in `_RESOURCE_CONSTRAINTS`. Error names the sorted known families so the author can correct the typo.
2. **Empty actions.** A capability with `actions == []`. Every capability must declare at least one action.
3. **Missing required constraint.**
   - `file:` capability without `path_prefix`.
   - `secret:openbao` capability without `openbao_paths`.
   - `net:` capability with **neither** `allowed_hosts` nor `allowed_ports`.
4. **Overlap between `capabilities` and `denied`.** If the same `resource` appears in both lists AND their `actions` sets intersect, the spec is self-contradictory. The error names the resource and the overlapping actions. (`denied` exists to carve exceptions out of a broader grant; an identical (resource, action) pair in both lists is a redundant restatement, not an exception.)

`validate` does **not** check the subset relation (§5) — that is the job of `is_subset` / `StsIssuer.issue`, because subset is a relation between two specs, not a property of one.

## 5. The Subset Relation (formal definition)

The subset relation is the core of **same-or-fewer** delegation. It is implemented by `PermissionSpecParser.is_subset(requested, issuer) -> bool` and enforced inside `StsIssuer.issue()` (which raises `PermissionDeniedError` on violation).

### Definition

`requested ⊆ issuer` **iff** for every capability `r` in `requested.capabilities`, there exists a capability `i` in `issuer.capabilities` such that:

1. **Resource match.** `r.resource == i.resource` (exact string equality).
2. **Action subset.** `set(r.actions) ⊆ set(i.actions)`.
3. **Constraint narrowness.** `r.constraints` is at least as narrow as `i.constraints`, where narrowness is defined per resource family:

   - **`file:`** — `r.path_prefix.startswith(i.path_prefix)`. A longer prefix is a narrower scope. (The issuer must have a non-empty `path_prefix` of type `str`; otherwise the relation is false.)
   - **`net:`** — for each of `allowed_hosts`, `allowed_ports` that is present and non-empty in `r`, `set(r.<key>) ⊆ set(i.<key>)`. An empty/absent constraint in `r` is unconstraining (treated as satisfying the relation for that key); a present constraint must be fully covered by the issuer.
   - **`secret:openbao`** — `set(r.openbao_paths) ⊆ set(i.openbao_paths)`. The subject's path globs must be a subset of the issuer's path globs (exact pattern strings; subset is on the pattern set, not on the set of paths the patterns would match).

If any of (1), (2), (3) fails for any capability in `requested`, the relation is false. A capability in `requested` whose resource does not appear in `issuer.capabilities` at all makes the relation false.

### Concrete examples

**PASS.** Issuer = `primary` default spec (read on `secret/data/gludd/*`).
Subject requests read on `secret/data/gludd/build/*`:

```python
issuer = default_spec("primary")
#   Capability(resource="secret:openbao", actions=["read"],
#              constraints={"openbao_paths": ["secret/data/gludd/*"]})
subject = PermissionSpec(
    agent_type="subagent",
    capabilities=[Capability(
        resource="secret:openbao",
        actions=["read"],
        constraints={"openbao_paths": ["secret/data/gludd/build/*"]},
    )],
)
PermissionSpecParser.is_subset(subject, issuer)  # -> True
```

`"secret/data/gludd/build/*"` is a member of the issuer's path set `{"secret/data/gludd/build/*"} ⊆ {"secret/data/gludd/*"}`? No — the **pattern strings** are compared as a set, and `"secret/data/gludd/build/*"` is not literally in `{"secret/data/gludd/*"}`. So this example **FAILS** the literal-subset rule. For the request to pass, the issuer's `openbao_paths` must **literally contain** every pattern the subject requests. (Path-glob semantic narrowing is delegated to the `SecretsManager._enforce_permission` runtime check — `is_subset` compares the **declared pattern set**, not the universe of paths the patterns match.)

A correct PASS example is therefore: issuer declares `openbao_paths = ["secret/data/gludd/*", "secret/data/gludd/build/*"]` and subject requests only `["secret/data/gludd/build/*"]`.

**FAIL.** Issuer allows `path_prefix = "/tmp/gludd/"` on `file:scratch`. Subject asks for `path_prefix = "/tmp/"`:

```python
issuer_cap = Capability(
    resource="file:scratch",
    actions=["read", "write"],
    constraints={"path_prefix": "/tmp/gludd/"},
)
subject_cap = Capability(
    resource="file:scratch",
    actions=["read"],
    constraints={"path_prefix": "/tmp/"},
)
```

`"/tmp/".startswith("/tmp/gludd/")` is `False` — `/tmp/` is **wider** than `/tmp/gludd/`, so the subject is requesting files outside the issuer's grant. `is_subset` returns `False`; `StsIssuer.issue()` raises `PermissionDeniedError("subject spec requests capabilities not held by issuer")`.

## 6. STS Token Lifecycle

`StsIssuer` mints and tracks `StsToken` instances. Each token carries a `PermissionSpec` (the subject's), the issuer/subject agent ids, an issued-at and expires-at epoch, and bookkeeping fields (`last_used_at`, `use_count`).

### `StsIssuer.issue(...)`

```python
def issue(
    issuer_spec: PermissionSpec,
    subject_spec_request: PermissionSpec,
    issuer_id: str,
    subject_id: str,
    ttl_seconds: int,
) -> StsToken
```

1. **Subset check.** Calls `PermissionSpecParser.is_subset(subject_spec_request, issuer_spec)`. On `False`, raises `PermissionDeniedError`. This is the structural guarantee that STS delegation is always same-or-fewer.
2. **TTL cap.** `capped_ttl = min(ttl_seconds, issuer_spec.max_sts_ttl_seconds)`. The issuer cannot mint a token that outlives its own declared ceiling (default 3600s).
3. **Mint.** Generates a fresh `token_id = uuid.uuid4().hex`, stamps `issued_at = now`, `expires_at = now + capped_ttl`, and stores the token in the issuer's in-memory registry.

### `StsIssuer.validate(token, required_capability) -> bool`

Returns `True` only when **all** hold:

- `now < token.expires_at` (not expired).
- `token.spec.capability_for(required_capability.resource)` is not `None`.
- `set(required_capability.actions) ⊆ set(cap.actions)`.

Used by callers that need to assert a specific capability is held by a presented token before performing a gated action.

### `StsIssuer.record_use(token_id)`

Bumps `use_count` by one and sets `last_used_at = now` on the stored token. Returns silently if the token is unknown (defensive — a use report for an already-expired/revoked token is logged via the audit log, not a crash).

### `StsIssuer.get_token(token_id) -> StsToken \| None`

Direct registry lookup (no expiry check). Used by admin endpoints to inspect a token's metadata.

### Revocation

Revocation is performed by the daemon admin endpoint (see §9): it sets the token's `expires_at` to `now`, which makes subsequent `validate()` calls return `False` and `record_use()` a no-op. The audit log records the expiry event (§7). There is no "delete" — the token record is retained for forensics; it simply becomes unresolvable.

> Note: the parallel `STSRegistry` class in the same module provides a lower-level mint/resolve/revoke API used by the daemon's bearer-token `Authorization` header path. `StsIssuer` is the agent-delegation surface and is the one that enforces the subset relation.

## 7. Audit Log

`StsAuditLog` is an in-memory append-only log of STS lifecycle events. Every issued/used/expired token produces one structured event; the daemon admin endpoint additionally records explicit revocations as expiry events.

### Methods

- **`record_issue(token: StsToken) -> None`** — append an `issued` event with the token's ids and `issued_at` timestamp.
- **`record_use(token_id, capability: Capability, target: str) -> None`** — append a `used` event naming the capability resource and the target path / host the token was used against. `target` is the operative datum for forensics (e.g. the OpenBao path read, the host connected to).
- **`record_expiry(token_id) -> None`** — append an `expired` event (covers both natural TTL expiry and admin-driven revocation).
- **`query(agent_id=None, since=None, capability=None) -> list[dict]`** — filter the event stream. `agent_id` matches either `subject_agent_id` or `issuer_agent_id`; `since` is an epoch-seconds lower bound on `at`; `capability` filters on the event's `capability` field (the resource string). Returns a list of event dicts in insertion order.

### Event shape

```python
{
    "event": "issued" | "used" | "expired",
    "token_id": str,
    "issuer_agent_id": str | None,
    "subject_agent_id": str | None,
    "capability": str | None,      # resource name, for "used" events
    "target": str | None,          # path / host, for "used" events
    "at": float,                   # epoch seconds
}
```

The audit log is the forensic record for "which agent did what with which token." It is consulted by the admin security endpoint and is the only place where the full history of a token's use is reconstructable — the live `StsToken` only carries aggregate counters (`use_count`, `last_used_at`).

## 8. OpenBao Integration

The `secret:openbao` capability is the integration point with hvac / OpenBao. `SecretsManager` accepts an optional `permission_spec: PermissionSpec` in its constructor; when set, every read/write/list/delete call passes through `_enforce_permission(path, action)` **before** the hvac delegation.

### Contract

`_enforce_permission(path, action)` raises `SecretPermissionDeniedError` unless:

1. The spec has a `secret:openbao` capability (`spec.capability_for("secret:openbao")`).
2. `action` is in that capability's `actions` list.
3. At least one glob pattern in `constraints["openbao_paths"]` matches `path` via `fnmatch.fnmatchcase` (so `*` matches across `/`).

When no spec is attached (`permission_spec=None`), enforcement is a no-op — this preserves back-compat for the admin PSK path and internal daemon subsystems that predate the permission system. **Production agents must always attach a spec** so the fail-closed path is active.

### Error

`SecretPermissionDeniedError` inherits from `SecretsUnavailableError` so existing fail-closed callers intercept it correctly — but it must NOT be retried as if the backend were down. The spec is the authority, not the backend. The error carries the **sanitized** allow-list (glob patterns only, never secret values) so the caller can introspect "what paths COULD I have read?" without re-issuing the request or escalating privileges.

### Wiring

The daemon reconstructs a `SecretsManager` scoped to the resolved STS token's spec for the duration of each request. This means a `subagent` (which has no `secret:openbao` capability by default) cannot read any secret until its issuer mints an STS token carrying an `openbao_paths` grant that is a subset of the issuer's own grant (§5). The build/primary/subagent default specs in `permissions.py` are the single source of truth for the broadest grant each agent type starts with.

## 9. Forward References

- **OS-level enforcement.** The capability specs described here are a declarative intent. LSM backends (AppArmor on Linux, sandbox-exec on macOS, SELinux where available) ENFORCE them at the OS level so that a compromised agent cannot bypass the daemon's in-process checks. See `src/general_ludd/security/sandboxes/` (parallel task).
- **CLI surface.** The `gludd perm ...` subcommands (parse/validate a YAML spec, mint an STS token, query the audit log) are documented in the parallel CLI task.
- **Daemon endpoints.** STS issue/resolve/revoke and audit-log query are exposed as PSK-gated, admin-only HTTP endpoints in `src/general_ludd/routers/security.py`.
- **Persistence.** `StsIssuer` and `StsAuditLog` are in-memory in the current implementation. A clustered deployment would back them with the database (the parallel `STSRegistry` class already notes this as the upgrade path).
