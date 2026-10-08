# Managed-Process Registry Lifecycle

## Status and problem

S29 makes the managed-process registry usable for children started after daemon
startup. The daemon deliberately seals the registry before serving requests,
but the native Ansible supervisor previously called unrestricted `register()`
and `deregister()` after that point. Both calls were rejected, their exceptions
were swallowed, and `/admin/processes` could remain empty while a real owned
child was running.

The correction preserves the seal. It adds a narrower owner-lease path for a
caller that already holds the started process handle, automatic bounded stale
pruning for admin snapshots, and content-free lifecycle counters.

## Runtime contract

`ProcessRegistry.lease_owned_process()` is the only post-seal registration path.
It applies these rules:

1. The caller supplies a concrete started-process handle, not a bare PID.
2. The handle must expose a positive integer PID.
3. The registry captures the operating-system process creation time and reads it
   again before admission. Missing or changed identity fails closed.
4. Before admission, at most the configured registry capacity is inspected for
   stale or PID-reused records.
5. A live duplicate PID and a full live registry are rejected.
6. The returned lease can remove only the exact record object it created. An old
   lease cannot remove a later record for a recycled PID.
7. The process owner releases the lease in `finally`, after joining or
   terminating its child. Release is idempotent.

The unrestricted `register()`, `deregister()`, and `reap()` methods remain
rejected after `seal()`. This preserves the original mutation boundary for
callers that do not own a runtime process handle.

The default capacity is 256 records. There is no timer, watcher thread, polling
task, persistent file, or unbounded queue. `active_snapshot()` performs at most
256 identity probes and removes only records whose PID and creation-time pair no
longer matches. `GET /admin/processes` uses that snapshot while preserving its
existing response shape.

## Observability

`GET /admin/processes/metrics` returns integer-only, content-free state:

- `capacity` and `current` show the fixed bound and present occupancy.
- `owner_leases_acquired_total` counts admitted owner leases.
- `owner_leases_released_total` counts exact-record releases.
- `stale_records_pruned_total` counts exited or identity-mismatched records.
- `capacity_rejections_total` counts admission refusals at the hard bound.

Acquisition, exact release, stale pruning, and degraded acquisition/release also
emit structured log event names. Commands, project identifiers, and exception
messages are not placed in the event name or counter labels.

## Upstream and practitioner evidence

Python's official
[multiprocessing process-object documentation](https://docs.python.org/3/library/multiprocessing.html#process-objects)
assigns lifecycle operations such as start, join, terminate, and close to the
process that created the child. The lease follows that ownership model: the
native Ansible supervisor retains the concrete process handle and owns cleanup
in its `finally` block.

The official
[psutil process documentation](https://psutil.readthedocs.io/en/stable/#processes)
warns that operating-system PIDs are reusable and describes process identity in
terms of PID plus creation time. The registry uses that pair before exposing or
signalling any process.

The long-lived practitioner report
[psutil issue #711](https://github.com/giampaolo/psutil/issues/711), opened in
2015, records that a live process's reported creation time can shift when Linux
wall-clock time is adjusted. That report rules out treating `create_time()` as
an infallible lifetime token. This feature therefore uses it conservatively: a
mismatch removes management authority and increments the stale-prune counter.
It may temporarily reduce visibility after a clock adjustment, but it cannot
authorize a signal to an uncertain or recycled PID. Owner-driven `finally`
cleanup remains the primary path; identity pruning is the bounded safety net.

## Security and failure behavior

- Signalling still requires registry membership, an allow-listed signal, and a
  fresh PID/creation-time match.
- Owner admission reads identity twice around record construction and refuses an
  exited or replaced process.
- Snapshot maintenance is removal-only and remains safe after sealing.
- Capacity exhaustion never evicts a live process to make room. Admission fails
  visibly while child execution continues, preserving the existing job result.
- Lease release compares record object identity under the registry lock, closing
  the old-lease/new-PID race.
- Registry counters contain no commands, tenant identifiers, or error text.

## Zero-downtime delivery and rollback

This is an additive in-memory lifecycle change with no database, configuration,
or persistent-data migration. During a rolling deployment, old daemon processes
keep their existing behavior and new daemon processes expose the additional
metrics route. The existing `/admin/processes` JSON shape is unchanged. A mixed
fleet therefore remains wire-compatible.

Roll out by canarying one daemon and verifying that acquired records appear while
the Ansible child is live, then that either release or stale-prune increments
after exit and `current` returns to zero. Capacity rejections should remain zero
under normal load.

Rollback is a code-only revert. Existing leases are process-local and disappear
when the owning daemon exits; there is no state conversion to undo. Let in-flight
Ansible runs finish or terminate through their normal supervisor before replacing
the canary. Reverting loses post-seal process visibility but does not change job
execution, child termination, database state, or the established signal safety
checks.

## Acceptance

The focused lifecycle node must first fail against the pre-feature registry and
then pass with the implementation. Final acceptance runs the integration and
legacy registry/router/runner suites, warnings-as-errors lint and type checks,
the dedicated line-plus-branch coverage profile at 85% aggregate and 75% per
file, Markdown lint, and collection with zero errors.
