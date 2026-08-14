# Azure Cost Lease Ownership

## Status

Implemented for the `0.1.0-beta.4` release train. Azure cost-state
mutations now validate the complete database-issued lease proof, while valid
workers retain the existing monotonic reconciliation path and transactional
outbox boundary.

## Problem

The beta gate exposed a contradictory state-transition test. It acquired a
15-minute database lease, advanced simulated time by hours, and extended only
the frozen Python claim object. The persisted expiry never changed, so the
repository correctly rejected the second mutation as stale. Trusting that
caller-side extension would let a worker invent ownership after another worker
could have taken over.

The same gap existed in the opposite direction: while the persisted lease was
active, `_lock_claim` ignored a claim whose `expires_at` field had been
changed. Owner and fencing token checks prevented cross-worker mutation, but the
documented opaque proof was not validated as one indivisible value.

## Ownership and expiry contract

- A claim is the exact tuple of prediction ID, prediction version, owner,
  fencing token, and persisted expiry returned by `claim_due`.
- Prediction ID, version, owner, token, and expiry must all match the locked
  database row. Copying a claim with an earlier or later expiry invalidates it.
- Both `now` and the claim expiry are timezone-aware. A naive deadline is
  rejected before SQL execution.
- A lease is valid only while `now < expires_at`. The exact expiry instant is
  stale, which matches `claim_due` allowing takeover when the database expiry
  is less than or equal to `now`.
- A successful takeover increments the durable fencing token. The expired
  owner cannot mutate state at or after takeover; the replacement owner can.
- State transitions may share one claim only while its persisted lease remains
  active. A caller needing more time must stop before expiry and acquire a new
  database-issued claim; mutating the dataclass is never renewal.
- Repository methods continue to flush without committing. State mutation and
  its deduplicated outbox event remain owned by the caller's transaction.
- Finality remains monotonic: `FINAL` requires `STABLE`, `ADJUSTED`
  requires `FINAL`, and stale ownership is rejected before either transition
  can publish an event.

## Practitioner and upstream evidence

A [2022 Stack Overflow fencing-token question](https://stackoverflow.com/questions/72083659/distributed-lock-using-fencing-token-for-preventing-concurrent-writes-to-a-net)
has remained a useful practitioner discussion of the exact failure mode: work
pauses, its lease expires, and the old worker resumes while another owner may
exist. The discussion emphasizes that the system accepting writes must enforce
the fencing token. Gludd therefore validates ownership at the database mutation
boundary rather than trusting the worker's local clock or object.

The still-open
[Redisson watchdog issue 5697](https://github.com/redisson/redisson/issues/5697),
reported in March 2024, describes long-running workers whose lock renewal was
delayed enough for another worker to enter. It shows why an assumed or
in-memory extension cannot be evidence of ownership. This slice deliberately
does not add implicit renewal; every accepted Gludd mutation must match durable
lease state.

A
[Kubernetes operator discussion from January 2023](https://discuss.kubernetes.io/t/leaderelections-failing-lease-unable-to-be-renewed-automatically/22738)
reports repeated leader churn when holders could acquire but not renew leases
long enough to do useful work. Kubernetes' current
[coordinated-leader-election contract](https://kubernetes.io/docs/concepts/cluster-administration/coordinated-leader-election/)
likewise treats the stored holder, renewal time, duration, and
optimistic-concurrency version as authoritative. Gludd uses the analogous
database owner, expiry, and monotonically increasing fencing token.

## Security and failure boundaries

Lease claims are authorization-adjacent data. A caller cannot gain more time by
changing `expires_at`, impersonate another owner, or guess a newer fencing
token. The query locks and validates one exact prediction row before any state
or outbox mutation. An ownership or persisted-expiry mismatch uses the existing
generic stale-lease error, so it does not reveal which proof component differed.
A malformed local expiry reports only the existing timezone-validation error
before SQL and reveals no persisted row state.

The timezone-aware transaction timestamp supplied by the reconciliation caller
remains the decision boundary. This change grants no Azure permission,
credential access, network destination, or new data visibility. It does not weaken immutable cost
observation identity or finality ordering.

## Resource and observability boundaries

Validation adds no query, retry loop, heartbeat, thread, process, port, daemon,
cache, or temporary artifact. The existing row-lock query gains one indexed-row
predicate, and already-expired claim objects fail before database I/O. Claim
batching remains capped at 1,000 and the caller still controls transaction
lifetime.

A stale or superseded mutation raises `StaleAzureCostLeaseError` immediately;
a malformed timestamp raises `ValueError` before SQL. Operations can count the
stable stale-lease error and retry through the normal due-claim cycle. The
repository does not spin, sleep, silently renew, or emit a misleading outbox
event.

## Zero-downtime rollout and rollback

The stricter check uses existing columns and requires no migration, backfill, or
wire-format change. Claims are process-local immutable values produced from the
same persisted row, so valid old and new workers can overlap during promotion.
New workers simply fail closed if application code has altered a proof.

Development is promoted only after focused coverage, the full gate, and CI are
green. Rollback reverts the repository predicate, regressions, task evidence,
and this contract together. Persisted rows, fencing tokens, observation data,
and outbox events need no conversion; expired work remains recoverable through
`claim_due`.

## Verification contract

The authoritative beta replay must first reproduce the stale transition after
an in-memory-only extension. Failing-first regressions then prove that earlier
and later forged expiries were previously ignored. The repaired suite must
cover valid multi-step transitions inside one lease, timezone validation, exact
expiry, wrong owner, wrong token, expired-owner takeover, monotonic finality,
and deduplicated outbox behavior.

The focused family runs with warnings treated as errors. Touched-source
aggregate coverage must be at least 85 percent and every touched source file
must retain at least 75 percent line and branch coverage. Ruff, strict mypy,
source docstrings, Markdown, feature-spec lint, task-ledger integrity,
collection, and the full release gate remain mandatory.
