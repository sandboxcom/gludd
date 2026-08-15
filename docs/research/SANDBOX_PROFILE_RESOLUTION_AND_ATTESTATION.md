# Sandbox profile resolution and runtime attestation

**Feature:** `SEC-SBX-001` first executable slice
**Implementation status:** production dispatch admission wired; real backend probes remain open
**Last reviewed:** 2026-08-01

## Scope and completion boundary

This slice implements the policy and evidence seam required before Gludd can
truthfully route untrusted work to a sandbox:

- strict version-1 `locked`, `standard`, and `development` policy models;
- an immutable deny-by-default built-in registry;
- an explicit administrator envelope followed by monotonic user, project,
  agent, and work-item narrowing;
- canonical JSON and a stable SHA-256 policy identity;
- observed-state evaluation that denies an unapplied, unapproved, too-weak, or
  incomplete backend; and
- bounded, tenant-partitioned, integrity-checked attestation events committed
  to the shared audit database before the caller receives them.

This does **not** mark `SEC-SBX-001` implemented. Backend canaries, kernel/VM
probes, event notifications, policy promotion, and escape tests remain required by
[`FEATURE_SECURITY_SANDBOX_HARDENING.md`](../specs/FEATURE_SECURITY_SANDBOX_HARDENING.md).
In particular, a backend-provided `applied=True` value is not sufficient:
callers must populate `RuntimeSandboxObservation` from independently observed
host state.

## Production dispatch admission

The daemon now resolves the operator-selected `vm_sandbox.profile` once at
startup and wires that immutable value plus `DurableSandboxAttestationStore`
into `EventLoop._dispatch_execute_job_isolated()`. The boundary applies and
verifies the selected backend, requires its independent `observe_runtime`
probe, commits the tenant-partitioned decision, and only then invokes the
existing sandbox executor and agent job. A missing backend, missing permission
spec or executor, failed verification, absent/invalid observation, denial
decision, or unsealed/unavailable audit store blocks execution. Backend handles
are released on both allow completion and pre-execution denial.

Each dispatch holds one `ResolvedSandboxProfile` and policy hash for its entire
lifetime. A replacement worker can prepare another profile without mutating the
in-flight value, preserving the prepare/verify/switch/drain seam required for
later ZDD promotion work. The `locked` default remains unchanged; `standard`
and `development` are explicit validated configuration choices.

The long-lived Bubblewrap namespace failure documented below directly informs
the admission rule: binary detection or an `applied=True` handle never creates
guarantees. Until a backend supplies a typed independent probe, production
dispatch records a durable denial instead of falling through unsandboxed.

This slice covers only the daemon event-loop isolated-job boundary. Direct agent
dispatcher calls, dynamic HTTP dispatch, worker execution, administrative code,
and other tool/MCP paths still require equivalent integration; the feature spec
tracks those residual seams and does not claim global enforcement.

## Configuration and narrowing contract

`resolve_sandbox_profile()` selects `locked` when the caller omits a profile.
The locked built-in has network denial, no allowed destinations or secret
references, durable denial auditing, required attestation, and a denied
fallback. Unknown keys, type coercion, duplicate grants, contradictory rules,
invalid host/CIDR/port values, traversal-like secret references, and unbounded
resource values fail validation.

The separately supplied administrator mapping defines the approved envelope.
Every later layer is compared with its parent. It may reduce resource ceilings,
remove allowlist entries, strengthen access modes, require stronger isolation,
or turn a development audit fallback into denial. It may not add a destination,
secret, host path, executable, resource, weaker backend guarantee, or disabled
security control. An attempted widening raises `PolicyWideningError` with the
exact affected paths rather than silently accepting or partially applying it.

The normalized policy is serialized with sorted keys and compact separators.
Its SHA-256 digest becomes the immutable `policy_version` used by attestation,
so mapping insertion order cannot create a different identity.

## Durable attestation event contract

`evaluate_runtime_attestation()` compares the resolved policy with typed host
observations. Locked execution is denied when the selected backend is not in
the approved preference set, was not applied, is weaker than the required
isolation tier, has the wrong syscall profile, or lacks a required guarantee.
The event schema deliberately excludes arbitrary payloads, prompts, command
lines, environment variables, and secret values.

`DurableSandboxAttestationStore.append()` flushes the shared `audit_events` row
to obtain its monotonic database sequence, seals the canonical event with a
SHA-256 integrity digest, enforces a payload bound below the database maximum,
and commits before returning. A different Gunicorn worker can query newly
committed rows by sequence. Tenant and work-item identifiers are length-framed
and SHA-256 partitioned into the database entity key, preventing colliding work
IDs in different tenants from sharing a query partition. Readers reject schema
damage, row/payload sequence drift, identifier drift, and digest tampering.

The integrity canonicalizer sorts guarantee sets explicitly. Python set order
depends on process hash seeds; hashing an unsorted set produced an intermittent
cross-worker verification failure during coverage execution. The regression
test now exercises persistence through separate writer and reader store
instances.

## Mature mechanism research: Firecracker

Gludd should adapt Firecracker rather than build a hypervisor. Firecracker's
[production host setup](https://github.com/firecracker-microvm/firecracker/blob/main/docs/prod-host-setup.md)
requires the jailer or a stricter equivalent for production isolation. The
jailer supplies namespace/cgroup separation and privilege dropping, while the
operator must provide unique unprivileged UID/GID identities, immutable trusted
inputs, resource limits, bounded logs/serial output, and current kernel,
microcode, and guest images. The same guide states that Firecracker does not
filter guest egress; host firewall policy must prevent metadata and other
restricted destinations.

That evidence drives two resolver/attestor decisions:

1. `firecracker` may satisfy the virtual-machine strength tier, but only when
   the observation proves the rest of the requested guarantees.
2. Network denial is always an independent required guarantee. A microVM or
   jailer identity alone cannot attest network isolation.

## Long-lived operator evidence: disabled user namespaces

Bubblewrap issue
[#324](https://github.com/containers/bubblewrap/issues/324), opened in July
2019, records Flatpak workloads failing on a hardened Arch kernel because
unprivileged user namespace creation was disabled. The error recommended a
distribution-specific sysctl, while downgrading appeared to restore operation.
This is a durable example of why binary presence and version checks cannot
prove a sandbox is usable on the actual host.

Gludd therefore must retain these acceptance rules for the later backend slice:

- run a harmless namespace/capability canary on the active kernel and host
  policy before selecting Bubblewrap;
- attest the resulting namespace IDs and independently enforced syscall,
  filesystem, resource, and network controls;
- select only a fallback that preserves every guarantee; and
- durably deny the work item when no such fallback attests, rather than
  recommending a host-wide weakening such as enabling user namespaces.

## Zero-downtime delivery seam

Resolved policies are frozen values with deterministic hashes. Callers can pin
in-flight work to the returned `policy_version` while a new policy is prepared,
validated, canary-tested, and atomically promoted for new work. Attestation
events retain the exact version/hash, so mixed old/new workers remain auditable.
The version store, worker acknowledgements, atomic router switch, rollback, and
continuous-dispatch tests are intentionally not claimed by this slice.

## Focused evidence

```text
make test-files TESTFILES='tests/unit/test_sandbox_policy_profiles.py tests/unit/test_sandbox_runtime_attestation.py' PYTEST_ARGS=-q
make test-files TESTFILES='tests/unit/test_sandbox_policy_profiles.py tests/unit/test_sandbox_runtime_attestation.py' PYTEST_ARGS='-q --cov=general_ludd.security.policy.profiles --cov=general_ludd.security.sandboxes.attestation --cov-report=term-missing --cov-fail-under=85'
make lint-files FILES='src/general_ludd/security/policy/__init__.py src/general_ludd/security/policy/profiles.py src/general_ludd/security/sandboxes/attestation.py tests/unit/test_sandbox_policy_profiles.py tests/unit/test_sandbox_runtime_attestation.py'
make test-files TESTFILES='tests/unit/test_sandbox_dispatch_boundary.py tests/unit/test_sandbox_executor_dispatch.py tests/unit/test_sandbox_runtime_attestation.py' PYTEST_ARGS=-q
make test-files TESTFILES='tests/unit/test_sandbox_dispatch_boundary.py tests/unit/test_sandbox_runtime_attestation.py' PYTEST_ARGS='-q --cov=general_ludd.security.sandboxes.dispatch --cov-report=term-missing --cov-fail-under=85'
```
