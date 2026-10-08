# Worker liveness lease

## Outcome

The daemon no longer treats a historical worker registration as indefinite
permission to send the daemon PSK. Each accepted registration owns a 30-second,
process-local liveness lease. Registration, heartbeat, rolling re-registration,
and a successful operator ping renew that lease.

When a PSK-bearing reload or model-update broadcast finds an expired lease, the
daemon first performs an unauthenticated `GET /healthz`. Only HTTP 200 renews the
lease and permits the credentialed POST. A timeout, transport error, non-200
response, unsafe address, replaced registry entry, or exhausted probe budget
fails closed and the PSK is not sent.

## Request boundary

The checks execute in this order:

1. Require the configured worker allowlist match, when an allowlist exists.
2. Re-run the shared HTTPS and SSRF address policy at send time.
3. Accept an unexpired monotonic lease.
4. Otherwise, re-run the address policy and issue a public `/healthz` probe.
5. Renew only the still-current registry object after HTTP 200.
6. Send the existing authenticated reload or model-sync request.

The health request has no authorization headers, disables redirects, verifies
TLS, and has a maximum one-second timeout. The serial probe wave has one shared
10-second monotonic deadline. The registry accepts at most 64 distinct worker
identities. It does not create probe threads or processes, so degraded workers
cannot multiply daemon execution resources.

At the 64-worker limit, an existing identity can still re-register with a new
safe address. This supports zero-downtime rolling replacement: the new registry
object atomically replaces the old one and receives a new lease, while a probe
that was already in flight for the old object cannot renew the replacement.

## Why a monotonic lease

Python documents `time.monotonic()` as unaffected by system-clock updates and
recommends `monotonic_ns()` when integer nanoseconds avoid floating-point
precision loss. Lease age and the shared deadline therefore use injected
`monotonic_ns`; wall time remains only for the existing human-facing
`last_seen` field. See the [Python time documentation][python-monotonic].

Kubernetes distinguishes liveness from readiness, recommends a dedicated,
low-cost HTTP health endpoint, defaults probe timeouts to one second, and warns
that `exec` probes create processes. The daemon follows that resource model with
an HTTP `/healthz` check and no per-worker process creation. This check decides
whether a secret may be sent; it does not restart or evict a worker. See
[Kubernetes probe guidance][kubernetes-probes].

Celery documents heartbeat-driven online/offline state and separately uses a
mailbox for broadcast control. A long-running user report, [Celery issue
\#4758][celery-4758], describes workers whose event heartbeats disappeared while
control/status calls still reported them online; the report involved
Kubernetes, NAT, broker queues, and multi-hour clock drift. That field evidence
is why Gludd does not equate an old registration or an indirect heartbeat stream
with present reachability at the instant credentials leave the daemon. A direct,
bounded health check closes that stale-registration window. Celery's current
[monitoring documentation][celery-monitoring] remains useful context for the
separation between worker events and broadcast control.

## Zero-downtime and resource behavior

- Fresh workers stay on the existing fast path; no probe is added to every
  broadcast.
- A successful health probe renews the lease, so all broadcasts inside the next
  lease interval avoid another probe.
- Rolling re-registration is accepted even when all 64 identity slots are in
  use and immediately renews the replacement's lease.
- Failed probes do not unregister workers. A later heartbeat, ping, or
  re-registration can recover delivery without restarting the daemon.
- Broadcast results identify lease rejection as `liveness check failed`, giving
  operators an observable, per-worker outcome without exposing the PSK.
- The existing wall-clock stale cleanup remains independent. Lease expiry blocks
  a secret-bearing send; cleanup controls eventual registry reclamation.

## Configuration and rollback

Enforcement is enabled by default. Set
`GLUDD_WORKER_LIVENESS_ENFORCE=0` only as an emergency compatibility rollback.
That value restores the legacy behavior for PSK-bearing broadcasts while the
allowlist, HTTPS, SSRF, TLS-verification, and redirect guards remain active.
Unset the variable or set it to any value other than `0` to re-enable the lease.

The clock and GET/POST transports are constructor-injected for deterministic
tests. Production defaults remain `time.monotonic_ns`, `httpx.get`, and
`httpx.post`.

## Verification

The focused contract covers successful renewal, timeout failure without a PSK
POST, explicit rollback, operator-ping renewal, send-time SSRF revalidation,
the 64-worker rolling-re-registration boundary, and the one-second/per-wave
budgets. The dedicated coverage configuration measures the complete broadcaster
module with an 85% aggregate floor and a 75% individual-file floor.

[python-monotonic]: https://docs.python.org/3/library/time.html#time.monotonic_ns
[kubernetes-probes]: https://kubernetes.io/docs/concepts/workloads/pods/probes/
[celery-4758]: https://github.com/celery/celery/issues/4758
[celery-monitoring]: https://docs.celeryq.dev/en/stable/userguide/monitoring.html#worker-events
