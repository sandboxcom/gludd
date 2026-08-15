# Feature: STS Tokens for Subagents

**Status: CLOSED** | **Created: 2026-07-14** | **Closed: 2026-08-02** | **Target: v0.1.0-beta.2**
**Evidence: 411/411 tests pass (contracts 40, store 13, minter 14, revoker 17, daemon 15, E2E 14, + molecules/integration/e2e lifecycle). HEAD `558e661c`.**

## 1. Overview

Every subagent receives a per-agent STS token minted via OpenBao AppRole.
Tokens are capability-narrowed (intersection of parent's lattice), ephemeral
(TTL-bounded), revivable on hydration, and audit-logged.

## 2. Key Constraints

- Token permissions MUST be subset of parent's (capability non-escalation)
- One token per subagent — no sharing
- Token does NOT survive process restart unassisted (revived from DB-stored scope)
- Audit events for mint, use, renew, revoke

## 3. Architecture

### TokenMinter (`src/general_ludd/sts/minter.py`)
- Input: agent_id, parent_agent_id, capability scope (from CapabilityLattice)
- Calls `SecretsManager.setup_approle(role_name=f"agent-{agent_id}")` → gets role_id + secret_id
- Maps capability lattice actions → OpenBao policy paths
- Creates AppRole token with per-agent policy

### CapabilityNarrowing (`src/general_ludd/sts/narrowing.py`)
- Consumes parent's CapabilityLattice → produces child scope
- Intersection: child_actions = parent_actions ∩ child_native_actions
- Converts ToolAction set → OpenBao policy HCL fragment

### TokenStore (`src/general_ludd/sts/store.py`)
- DB model: AgentTokenModel (new alembic migration)
- Fields: token_id, agent_id (indexed), parent_agent_id, role_name, scope_hash, created_at, expires_at, revoked_at, hydration_count
- Never stores secret_id — lives only in OpenBao

### TokenReviver (`src/general_ludd/sts/reviver.py`)
- On hydration re-entry: reads stored AgentTokenModel → calls `SecretsManager.rotate_approle_secret_id()` → fresh secret_id
- Integration: `AgentDispatcher.resume_project()` → HibernationController

### TokenRevoker (`src/general_ludd/sts/revoker.py`)
- On agent death/completion: `SecretsManager._client.auth.approle.delete_role()` → destroys AppRole + tokens
- Caller: dispatch_one finally block

### SubagentTokenInjector (`src/general_ludd/sts/injector.py`)
- At dispatch time: mints token, stores via TokenStore, injects GLUDD_STS_ROLE_ID + GLUDD_STS_SECRET_ID env vars
- Reuses existing bind_tools_on_dispatch injection pattern

## 4. Token Lifecycle

```text
mint(dispatch) → use(agent runtime) → expire(TTL) | revoke(completion/death)
→ revive(hydration: fresh secret_id, same role) → revoke(dehydration)
```

## 5. OpenBao Integration

- Auth: AppRole (existing `SecretsManager.setup_approle`)
- Role config: `secret_id_ttl=24h`, `token_ttl=1h`
- Per-agent policy: auto-generated from capability scope, bound to AppRole
- kv-v2 mount: per-agent path prefix `agents/{agent_id}/`
- Uses hvac (already in pyproject.toml)

## 6. Database Schema

New model `AgentTokenModel`:
- `token_id: str` PK, `agent_id: str` indexed, `parent_agent_id: str`
- `role_name: str`, `role_id: str` (not secret)
- `scope_hash: str`, `scope_actions: text` (JSON list)
- `created_at, expires_at, revoked_at: datetime?`
- `hydration_count: int` default 0
- New alembic migration required

## 7. Audit

Reuses existing `StsAuditModel` (db/models.py:823). New event types: `mint`,
`use`, `renew`, `revoke`, `revive`.

## 8. Implementation Plan

| Phase | Scope |
|-------|-------|
| P1 | TokenMinter + TokenStore + AgentTokenModel migration — basic mint-on-dispatch |
| P2 | CapabilityNarrowing — lattice-to-policy mapping |
| P3 | TokenReviver integration with HibernationController |
| P4 | TokenRevoker + full audit event pipeline |

## 9. Files

| Action | Path |
|--------|------|
| Create | `src/general_ludd/sts/__init__.py`, `minter.py`, `narrowing.py`, `store.py`, `reviver.py`, `revoker.py`, `injector.py` |
| Modify | `agents/dispatcher.py` (inject at dispatch_one) |
| Modify | `agents/hibernation.py` (revive hook) |
| Modify | `db/models.py` (AgentTokenModel) |
| Create | `alembic/versions/xxxx_agent_token_model.py` (migration) |
| Create | `tests/unit/sts/test_minter.py`, `test_narrowing.py`, `test_store.py` |
| Create | `tests/integration/sts/test_openbao_integration.py` |
| Create | `tests/e2e/test_token_lifecycle.py` |

## 10. Dependencies

`hvac>=2.3.0` — already in pyproject.toml. No new deps.

## 11. Test Plan

- **Unit**: minter produces valid AppRole creds (mock hvac), narrowing produces subset scopes, store round-trips
- **Integration**: against real OpenBao — mint token, read scoped kv-v2, confirm denied on out-of-scope path
- **E2E**: dispatch_one → token in env → agent uses it → complete → token revoked
