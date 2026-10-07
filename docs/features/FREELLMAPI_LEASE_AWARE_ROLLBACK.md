# FreeLLMAPI Lease-Aware Rollback

## Outcome

FreeLLMAPI bridge generations now retain every artifact used by in-flight work,
even after later promotions make that generation neither active nor immediately
previous. Rollback changes the generation assigned to new work without moving or
cancelling an existing lease.

This closes the executable ZDD boundary described by S83.163. It does not admit a
new upstream artifact, change the current promotion hold, start a sidecar, or move
task scheduling out of Gludd.

## Ownership contract

`FreeLLMAPIRollbackWorkflow` owns a bounded map from a content-free lease digest
to the immutable generation selected when the lease starts.

| Operation | Generation effect | Cleanup effect |
|---|---|---|
| `acquire_lease` | Pins the current generation | Protects every artifact in that generation |
| `promote` | Routes later leases to green | Keeps active leases on their original generation |
| `rollback` | Routes later leases back to blue | Keeps in-flight green leases valid and protected |
| `release_lease` | Removes one exact ownership record | Makes an otherwise old generation eligible |
| `lease` context | Acquires before work and releases in `finally` | Prevents exception-path ownership leaks |

Lease identities must be lowercase `sha256:` URIs. Errors never reflect the
identity, prompt, model output, endpoint, provider body, or task content. Duplicate
acquisition and unknown release fail closed. The registry defaults to 1,024 active
leases and refuses the next lease at capacity instead of growing without bound.
`active_lease_count` supplies content-free lifecycle telemetry.

## ZDD and rollback

Blue/green assignment is a pointer change for new work. Work that began on blue
finishes on blue after promotion; work that began on green finishes on green after
rollback. Artifact cleanup takes the union of the active generation, immediately
previous generation, and every leased generation. It cannot reclaim bytes still
needed by an in-flight invocation.

The context-managed API is the normal integration boundary:

```python
with workflow.lease(lease_digest) as generation:
    invoke_generation(generation)
```

`finally` release preserves exact ownership when invocation raises or is
cancelled. Reverting this source change requires no schema migration or service
restart, but an operator must first drain leases created by the newer process.
The state is process-local and contains only immutable digests and generation
metadata.

## Practitioner evidence

These reports are not treated as proof about Gludd; they are long-lived operator
evidence for the failure mode the tests reproduce.

- [Envoy issue #2776](https://github.com/envoyproxy/envoy/issues/2776), opened in
  2018, reports requests losing their connection during hot restart when the old
  process was ended before its drain completed. Gludd therefore never equates a
  successful generation switch with permission to reclaim the prior generation.
- [Envoy issue #7841](https://github.com/envoyproxy/envoy/issues/7841), open since
  2019, distinguishes hot-reload draining from shutdown that reset active
  connections. Gludd models routing new leases and draining owned leases as
  separate transitions.
- [Gunicorn issue #3397](https://github.com/benoitc/gunicorn/issues/3397), opened
  in 2025, reports dropped requests during Kubernetes updates even though workers
  finished their current request. The report reinforces that lifecycle safety
  requires explicit ownership until all accepted work has drained, not a timer or
  process-state guess.

## Verification

The focused tests cover promotion, rollback, exception cleanup, duplicate and
unknown ownership, malformed identifiers, capacity exhaustion, artifact
protection, and post-release cleanup eligibility. Coverage is measured with
branch accounting and must remain at least 85% for this module, with the project
floor of 75% still applying to every measured file.
