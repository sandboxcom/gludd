# Non-Ephemeral Account Creation — Decision & Rationale

**Status: Implemented as 501 (not yet supported). Ephemeral-only by design.**

## Current behavior

`POST /api/account/create` with `ephemeral=false` returns HTTP 501 with the
message:

> non-ephemeral account creation is not implemented; pass ephemeral=true
> to provision a scoped, auto-cleaned account

The `gludd account create` CLI subcommand surfaces this to operators identically.

## Why ephemeral-only

The `EphemeralAccountManager` (in `src/general_ludd/account/ephemeral.py`)
creates short-lived cloud accounts (AWS IAM user, GCP service account, Azure
service principal) with three properties that persistent accounts lack:

1. **Budget-scoped.** Every ephemeral account carries a `budget_limit` (default
   $10 USD), enforced via the provider's native billing policy. A runaway job
   cannot exceed its cap. A persistent account with no budget cap can burn
   unbounded funds.

2. **Auto-deleted after use.** When the workload that requested the account
   completes, `maybe_delete_ephemeral_after_task()` tears it down. The event
   loop's reconcile phase triggers this automatically. A persistent account
   survives the workload — an operator must remember to delete it.

3. **Retention-gated.** If the task somehow never completes (daemon crash,
   event-loop restart), the `cleanup_expired()` sweep runs periodically and
   deletes any account past its `retention_period_hours` (default 24h). An
   operator never needs to "check if that AWS IAM user is still needed."

These three properties make ephemeral accounts the safe default: every
credential has a finite lifetime, a finite budget, and a deterministic
teardown path. No operator action is required to prevent credential sprawl.

## What a persistent account API would need (not yet designed)

Adding non-ephemeral support requires:

- **A persistent credential store** — the current registry is a JSON file on
  disk (`~/.local/share/general-ludd/ephemeral-accounts.json`). Persistent
  accounts need DB-backed storage with encryption-at-rest for the secret key.

- **Lifecycle management** — a persistent account has no automatic teardown.
  At minimum: rotation policy (rotate every N days), usage audit (who used
  this key for what?), and a human-confirmed deletion path with a grace period.

- **Budget enforcement** — without auto-delete, the budget cannot be enforced
  at the deployment layer. It must be enforced at the provider level (budget
  alerts, hard caps) AND surfaced in the daemon's cost accounting so an
  operator can see cumulative spend per persistent account.

- **Access control** — a persistent credential is a long-lived secret. It must
  be scoped to a specific project or tenant, never shared across projects.
  This requires integrating with the STS / capability lattice.

- **Compliance surface** — persistent accounts need audit logging (who created,
  who used, when rotated, when deleted) to satisfy SOC 2 / ISO 27001 evidence
  requirements. Ephemeral accounts need none of this because they self-delete.

None of these are implemented. The 501 is honest: the feature doesn't exist,
and calling it "done" with a stub that stores a key in a JSON file would be
a false claim.

## Decision

**Persistent accounts remain unimplemented until the five requirements above
are met.** The 501 is not a bug — it is the correct response for a feature
that has not been designed. When the requirements are addressed (likely gated
on a project that needs them), the implementation path is:

1. DB migration adding a `persistent_accounts` table with encrypted `secret_key`.
2. `PersistentAccountManager` class (mirrors `EphemeralAccountManager` but
   without auto-delete / retention, with rotation support).
3. New endpoints: `PUT /api/account/rotate`, `GET /api/account/list`.
4. Cost-accounting integration: per-persistent-account spend tracked in the
   accounting ledger.
5. Compliance audit-log entries for create / rotate / delete.

Until then, operations that need a cloud principal use `ephemeral=true` and
let the manager handle teardown.
