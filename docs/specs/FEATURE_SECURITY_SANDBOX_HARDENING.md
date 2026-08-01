# Feature: Configurable Security and Sandbox Hardening

**Spec ID:** SEC-SBX-001  
**Status:** Proposed — implementation and acceptance gates are open  
**Created:** 2026-08-01  
**Target:** development after all requirements below pass  
**Supersedes completion claims in:** `FEATURE_UNIKERNEL_SANDBOX.md`

## 1. Purpose and completion honesty

Gludd SHALL provide a versioned, deny-by-default security policy that compiles
the same capability intent into the strongest usable isolation backend on each
host. Operators SHALL be able to configure every workload-relevant boundary,
but a task-, project-, or agent-level override SHALL only narrow an effective
policy unless a separately authenticated administrator approves and audits a
widening.

This specification does not claim that the controls are implemented. It records
the observed 2026-08-01 audit baseline, converts every open item into a testable
requirement, and defines the evidence required before the status can change.
Scanner success, a backend's `available()` result, or creation of a policy file
is not evidence that a boundary was enforced.

The first executable profile-resolution and durable-attestation slice, its
explicit limitations, mature Firecracker research, and long-lived Bubblewrap
operator evidence are documented in
[`SANDBOX_PROFILE_RESOLUTION_AND_ATTESTATION.md`](../research/SANDBOX_PROFILE_RESOLUTION_AND_ATTESTATION.md).

## 2. Observed baseline

The baseline was measured on development commit `1adcb2de` with repository make
targets. Generated files are evidence snapshots, not allowlists.

- `make security-backlog-gate` reported `TOTAL=24`,
  `LANDED-VERIFIED=8`, and `OPEN=16`. The current target intentionally exits
  successfully when controls are open, so it is not a completion gate.
- `make sast` generated `dist/sast-report.json` with `SEVERITY.HIGH: 1`,
  `SEVERITY.MEDIUM: 68`, and `SEVERITY.LOW: 508`. The target currently masks
  Bandit's exit status and is informational only.
- `make pip-audit` reported `ansible-core 2.21.0` as
  `PYSEC-2026-3458` and `diskcache 5.6.3` as `PYSEC-2026-2447`.
- `uv.lock` resolves Pillow 12.2.0. The current dependency scanner did not
  report the security changes shipped by Pillow 12.3.0; that absence SHALL NOT
  be interpreted as safety.
- `SandboxEnforcer` describes a fail-closed contract, while
  `security.sandboxes.SandboxBackend` and several backends explicitly fail
  open. The effective dispatch boundary is therefore not yet uniformly
  fail-closed.
- The current `_isolate_network()` changes a socket timeout but does not create
  an operating-system network boundary. Locked workloads SHALL reject this as
  unenforced, not report it as isolated.

## 3. Threat model and trust boundaries

### 3.1 Protected assets

- Host, daemon, sibling agents, other projects, and cloud metadata services.
- Source, worktrees, credentials, model tokens, signing keys, and audit records.
- Provider budgets, compute quotas, databases, model gateways, and MCP servers.
- Availability: CPU, memory, PIDs, disk, file descriptors, network, output, and
  model-token budgets.

### 3.2 Adversary capabilities

The design SHALL assume that prompts, repositories, dependencies, model output,
images, archives, media, MCP responses, Ansible variables, and generated code
may be malicious. A hostile task may execute arbitrary native code inside its
assigned sandbox and may attempt path traversal, symlink races, DNS rebinding,
fork bombs, output floods, deserialization, side channels, or credential theft.

The host kernel, selected isolation runtime, policy compiler, and a minimal
credential broker form the trusted computing base. Hardware side channels and a
fully compromised host kernel are not solved here; locked multi-tenant hosts
SHALL apply vendor microcode and kernel mitigations and SHALL NOT share one
Firecracker process between tenants.

## 4. Mature isolation building blocks

Gludd SHALL compose maintained isolation projects. It SHALL NOT implement a new
hypervisor, userspace kernel, namespace jail, syscall-filter engine, or image
decoder.

| Building block | Required use | Security constraint |
|---|---|---|
| Firecracker | Preferred locked Linux backend when KVM is available | Launch through `jailer`, unique UID/GID per tenant, cgroup v2 limits, immutable digest-pinned kernel/rootfs, host firewall, and no guest metadata by default. Firecracker does not filter guest egress. |
| gVisor `runsc` | Locked Linux fallback when KVM is unavailable | OCI bundle with rootless/container isolation, cgroup limits, container network policy, no host networking, no directfs unless explicitly justified, and compatibility preflight. |
| nsjail | Preferred process-isolation fallback on supported Linux hosts | User, mount, PID, IPC, network, UTS and cgroup namespaces; read-only root; Kafel/seccomp policy; rlimits; no ambient capabilities. |
| bubblewrap | Minimal Linux filesystem/namespace primitive when nsjail is unavailable | Empty mount namespace, explicit read-only binds, private PID/session/network, nested user namespaces disabled, and an independent seccomp/LSM layer. Bubblewrap alone is not a complete policy. |
| Landlock | Unprivileged Linux filesystem and port-scoped defense in depth | Compile only rights supported by the detected ABI. Hostname policy requires an egress proxy/firewall because Landlock network rules are port-scoped. |
| seccomp-BPF | Syscall attack-surface reduction inside Linux backends | Architecture-specific allowlist generated from a reviewed profile. Seccomp is a component, never the only sandbox. |
| macOS Virtualization framework or managed Linux VM | Required boundary for untrusted locked workloads on macOS | Deprecated Seatbelt profiles MAY be a development defense, but SHALL NOT satisfy locked isolation. |
| Windows AppContainer plus Job Object and firewall rules | Windows backend | Restricted token/capabilities, isolated filesystem identity, process/memory/CPU limits, and explicit egress policy SHALL all verify. |
| FreeBSD jail plus Capsicum/rctl/pf | FreeBSD backend | Jail creation alone SHALL NOT satisfy filesystem, syscall, resource, or egress controls. |

### 4.1 Primary research

- Firecracker's [production host setup](https://github.com/firecracker-microvm/firecracker/blob/main/docs/prod-host-setup.md)
  requires the jailer (or stricter equivalent), per-instance privilege
  separation and resource controls, and states that host firewalling must
  enforce egress.
- Firecracker's [getting-started prerequisites](https://github.com/firecracker-microvm/firecracker/blob/main/docs/getting-started.md)
  require Linux KVM and read/write access to `/dev/kvm`; auto-selection SHALL
  never pretend Firecracker is native on macOS or a non-KVM host.
- The [gVisor security model](https://gvisor.dev/docs/architecture_guide/security/)
  documents its application-kernel boundary and also delegates denial-of-service
  and network-policy enforcement to host cgroups and container policy.
- The [bubblewrap security documentation](https://github.com/containers/bubblewrap)
  calls it a low-level construction tool whose security depends on its caller's
  arguments. Gludd SHALL own and test that complete argument policy.
- [nsjail](https://github.com/google/nsjail) already combines Linux namespaces,
  cgroups, rlimits, chroot/pivot-root, and seccomp-BPF; it is preferred to a
  custom subprocess wrapper for this fallback tier.
- Linux [Landlock documentation](https://www.kernel.org/doc/html/latest/userspace-api/landlock.html)
  requires explicit denied-by-default rights and documents ABI-specific gaps.
- Linux [seccomp documentation](https://www.kernel.org/doc/html/v5.9/userspace-api/seccomp_filter.html)
  explicitly says syscall filtering is not a sandbox and must be composed with
  other hardening.

### 4.2 Long-lived user and operator evidence

- Bubblewrap issue [#324](https://github.com/containers/bubblewrap/issues/324),
  opened in 2019, records real deployments failing when unprivileged user
  namespaces are disabled. Gludd SHALL preflight namespace creation and select
  a verified fallback; binary presence alone is insufficient.
- nsjail issue [#236](https://github.com/google/nsjail/issues/236), opened in
  2024, records an Ubuntu 24 upgrade breaking mount setup through AppArmor user
  namespace policy. Backend verification SHALL execute a harmless escape-denial
  probe on the actual host image after OS upgrades.
- Bubblewrap issue [#710](https://github.com/containers/bubblewrap/issues/710)
  discusses preventing nested user namespaces without breaking legitimate
  application self-sandboxing. Profiles SHALL make this an explicit,
  backend-tested choice rather than an undocumented flag.
- Apple Developer Forums thread
  [661939](https://developer.apple.com/forums/thread/661939), active since
  2020, documents that `sandbox-exec` is deprecated and its public APIs are no
  longer supported. Locked macOS execution SHALL use a VM boundary or reject
  the task; Seatbelt availability is not a supported-security guarantee.

## 5. Versioned configuration contract

### SH-CONFIG-001

Gludd SHALL expose one strict, versioned policy schema. Unknown keys, duplicate
keys, unbounded values, unsupported backend guarantees, and contradictory rules
SHALL fail validation before dispatch. The normalized effective policy and its
SHA-256 hash SHALL be available through a dry-run command without exposing
secrets.

Minimum configuration shape:

```yaml
security:
  schema_version: 1
  posture: locked              # locked | standard | development
  profile: untrusted-code
  backend:
    preference: [firecracker, gvisor, nsjail, bubblewrap]
    minimum_strength: application-kernel
    require_attestation: true
    fallback: deny             # deny | audit; audit forbidden for locked
  filesystem:
    workspace: read-write
    source: read-only
    host_paths: []
    max_bytes: 1073741824
    max_inodes: 100000
  network:
    mode: deny                 # deny | allowlist | proxy
    hosts: []
    cidrs: []
    ports: []
    max_connections: 32
    max_bytes: 104857600
    deny_metadata: true
  process:
    executable_allowlist: []
    syscall_profile: untrusted-code-v1
    max_pids: 64
    no_new_privileges: true
  resources:
    cpu_quota: 1.0
    cpu_seconds: 300
    wall_seconds: 360
    memory_bytes: 536870912
    output_bytes: 1000000
    open_files: 256
  secrets:
    mode: brokered
    max_ttl_seconds: 900
    allowed_refs: []
  audit:
    sink: durable
    heartbeat_seconds: 10
    include_denials: true
```

Built-in profiles SHALL be immutable and documented:

- `locked`: untrusted multi-tenant work; no fail-open behavior; VM or
  application-kernel minimum; no network and no secrets unless explicitly
  granted.
- `standard`: single-tenant automation; verified process isolation MAY be used;
  unavailable required capabilities still deny dispatch.
- `development`: local trusted work; audit-only fallback MAY be opted into with
  an interactive warning and durable event. It SHALL never be the daemon or CI
  default.

Configuration precedence SHALL be built-in, system administrator, user,
project, agent, then work item. Each layer after the administrator layer SHALL
be intersected with its parent. A widening SHALL require a separate approval
capability, reason, expiry, approver identity, and audit event. Environment
variables SHALL not silently bypass policy files.

## 6. Enforced boundaries

### SH-SBX-001

Before starting a workload, the planner SHALL compute requested guarantees,
backend capabilities, missing guarantees, and the exact effective policy. The
attestor SHALL prove the backend, policy hash, namespaces/VM identity, cgroup,
filesystem mounts, network policy, UID/GID, and syscall profile from observed
runtime state. Locked dispatch SHALL proceed only when all required guarantees
attest. `applied=False`, a warning, or a socket timeout SHALL always deny.

Backend fallback SHALL be monotonic: a fallback may preserve or strengthen all
requested guarantees, never weaken them. The fallback reason and capability
diff SHALL be emitted before execution. Auto-detection SHALL perform an actual
canary, not only import/binary/path checks, and SHALL cache the result with host
kernel, runtime, policy and image versions in the cache key.

### SH-FS-001

Each work item SHALL receive a new namespaced root with an empty/private base,
read-only source and toolchain mounts, and a bounded writable scratch/workspace.
Host home, SSH agents, Docker/Podman sockets, cloud config, `/proc` sibling
processes, devices, and unrelated worktrees SHALL be absent by default.

All grants SHALL be descriptor-relative or canonicalized after every symlink
hop and checked again at use time. Tests SHALL cover symlink swaps, hard links,
`..`, Unicode normalization, mount traversal, `/proc/*/fd`, archive traversal,
case-insensitive filesystems, and TOCTOU. Disk byte, inode and file-size limits
SHALL be kernel enforced where possible. Cleanup SHALL be scoped by namespace
and ownership; it SHALL never remove global `/tmp`, shared pytest roots, or
another project.

### SH-NET-001

The default network namespace SHALL have loopback only. An allowlist grant SHALL
compile through a broker or host firewall to protocol, destination hostname,
resolved IP/CIDR, port, connection count, byte budget and expiry. DNS responses
SHALL be checked against denied address classes on every resolution; redirects
and proxy CONNECT targets SHALL be revalidated.

Cloud metadata, link-local, loopback-to-host, RFC1918, Unix sockets, multicast,
raw sockets, packet sockets and sibling sandbox ranges SHALL be denied unless a
narrow capability explicitly permits them. Landlock's port-only rules SHALL not
be represented as hostname enforcement. Tests SHALL include DNS rebinding,
IPv4-mapped IPv6, redirect-to-metadata, proxy bypass, alternate numeric address
forms, and connection/byte floods.

### SH-PROC-001

Sandboxed work SHALL run as a unique non-host UID/GID with no-new-privileges,
empty supplementary groups, zero ambient capabilities, a minimal environment,
private PID/IPC/UTS namespaces where supported, and a reviewed seccomp profile.
No shell interpretation SHALL occur; commands SHALL use typed argument vectors,
absolute verified executables, and a centralized executor.

Process count, threads, CPU time, wall time, open descriptors, core dumps,
locked memory, stack, output and child lifetime SHALL be bounded. PID 1 in a
namespace SHALL reap children. Cancellation and daemon shutdown SHALL terminate
the entire namespaced/cgroup process tree and verify absence before release.

### SH-SECRET-001

No host environment or credential directory SHALL be inherited. Secrets SHALL
be referenced by opaque IDs and delivered just in time through a broker over an
authenticated channel, with least-privilege scope, tenant and agent binding,
absolute and idle TTLs, use count, revocation, and audit. Secrets SHALL not be
placed in command lines, logs, model prompts, exceptions, snapshots, images, or
durable sandbox files.

Child scope SHALL be the intersection of parent scope and declared need.
Credential revocation SHALL occur on completion, cancellation, timeout, crash,
hibernation and policy change. Redaction SHALL run before every audit/log/event
sink, and tests SHALL use canary credentials to prove no leakage in stdout,
stderr, traces, core files, databases, process listings, or artifacts.

### SH-RESOURCE-001

Every profile SHALL bound CPU quota/weight/time, memory and swap, PIDs, disk
bytes/inodes/IOPS, network bytes/connections, open files, stdout/stderr, event
payloads, database writes, model input/output tokens, provider spend, retries,
and concurrency. Limits SHALL be enforced at the lowest available layer and
observed independently.

Exhaustion SHALL fail the work item with a typed reason, cancel descendants,
preserve a bounded diagnostic tail, and release capacity. Adaptive compute may
adjust within the approved envelope, but it SHALL never expand a security or
spend ceiling autonomously.

### SH-AUDIT-001

The daemon SHALL durably emit a monotonic event before launch, on every policy
decision/denial/fallback, at bounded heartbeats, at limit pressure, on secret
use, on cancellation, and after verified cleanup. Cross-worker Gunicorn events
SHALL use the shared durable transport; process-local subscribers are not
sufficient.

Each event SHALL contain at least:

```text
schema_version, event_id, sequence, timestamp, project_id, work_item_id,
agent_id, tenant_id, policy_version, policy_hash, requested_profile,
effective_profile, requested_backend, effective_backend, backend_version,
image_digest, decision, reason_code, missing_guarantees, resource_snapshot,
parent_event_id, correlation_id
```

Secret values and raw untrusted payloads SHALL never be event fields. Events
SHALL be append-only, size bounded, tamper evident, tenant partitioned, retained
by policy, and queryable while the test or task is still running. A subscriber
SHALL be able to act on a failure without waiting for suite completion.

## 7. Zero-downtime delivery

### SH-ZDD-001

Security configuration, policies, seccomp programs, firewall rules, sandbox
images and secret scopes SHALL be versioned immutable artifacts. A change SHALL
compile, schema-validate, run backend canaries, and execute negative escape
tests before it becomes eligible for promotion.

Promotion SHALL use prepare/verify/atomic-switch/drain:

1. Build and verify a new version without modifying the active version.
2. Publish the version and policy hash to the shared durable store.
3. Require all healthy Gunicorn workers to acknowledge load and validation.
4. Atomically route new work to the new version while in-flight work remains
   pinned to its original version and audit hash.
5. Drain the old version, verify cleanup, then remove it.

An invalid version, missing worker acknowledgement, failed attestation or
partial firewall update SHALL leave the old version active. Rollback SHALL be
the same forward-only promotion mechanism to a prior immutable version. Tests
SHALL continuously dispatch during valid reload, invalid reload, worker restart,
rollback, and mixed-duration workloads and prove no request executes
unsandboxed, twice, or under an unrecorded policy.

## 8. Open D-07 through D-30 control requirements

The table maps exactly the 16 controls reported `OPEN` by the 2026-08-01
`security-backlog-gate`. A row SHALL remain open until implementation tests and
a real regression probe are both present. Editing the ledger or changing the
test expectation is not remediation.

| Control | Priority | Required implementation | Executable acceptance |
|---|---|---|---|
| D-08 | P1 | Parse Ansible extra vars into a strict typed schema with unknown-key rejection, operator/tag denial, depth/item/string/byte limits, and safe scalar coercion. | Property tests and malicious YAML/JSON fixtures prove no Python/YAML object construction, expression evaluation, or cap bypass; valid scalar/list/map inputs round-trip. |
| D-09 | P1 | Authenticate and schema-validate a versioned `JobSpec` before allocating a workspace; reject unknown fields, duplicate IDs, cross-tenant IDs, invalid time/cost/resource limits, and oversized payloads. | Fuzzed deserialization is side-effect free on rejection; duplicate jobs return conflict; malformed jobs create no files/processes/events except a bounded denial audit. |
| D-11 | P2 | Add shared per-identity, tenant, route and global token-bucket limits to todo creation, plus tenant row/spend ceilings. Unauthenticated mutation remains denied. | Multi-worker tests cannot multiply the configured rate; excess returns 429 with bounded `Retry-After`; recovery does not lose accepted writes. |
| D-12 | P1 | Protect `/admin/code/*` with explicit admin capability, shared rate/concurrency/spend limits, bounded input/output, CSRF protection where browser reachable, and sandboxed execution. | Unauthenticated and ordinary PSK users are denied; concurrent workers share one limit; malicious code cannot escape the selected locked profile. |
| D-13 | P1 | Configure bounded WAL size, checkpoint thresholds, busy timeout and disk-pressure behavior per database; coordinate checkpoint/backup/maintenance through a single leader. | Sustained concurrent writes remain available while WAL stays within the declared bound; crash/restart recovers committed rows; disk exhaustion fails closed. |
| D-15 | P1 | Validate OpenBao mount/path aliases, reject traversal, mint scope as parent/child intersection, use per-agent short TTL/use limits, and revoke on every terminal path. | A child reads only its exact tenant/agent path; sibling, parent-only, `sys`, traversal and stale-token reads fail and emit redacted events. |
| D-16 | P1 | Enforce session absolute TTL, idle TTL, rotation, revocation and audience across all workers from shared state. Cookie/token defaults are secure and clock skew bounded. | Expiry and revocation take effect across workers without restart; fixation/replay/audience tests fail; a ZDD key rotation preserves only valid sessions. |
| D-17 | P1 | Automate daemon-worker PSK rotation with versioned identities, short overlap, atomic promotion, rollback, and removal of the old key. Prefer workload identity/mTLS where available. | Live two-worker rotation has no lost event, accepts only current/overlap keys in the declared window, then rejects the old key everywhere. |
| D-19 | P1 | Add migration plan/dry-run, schema drift check, backup/restore evidence, lock/timeout budget, transactional or expand-contract steps, and destructive-operation approval. | Production-shaped snapshots plan cleanly; injected failure leaves old schema/data usable; rolling old/new binaries pass expand-contract compatibility. |
| D-20 | P1 | Parse, normalize, compile, attest and shadow-evaluate config before an atomic shared-worker switch; keep immutable prior versions for rollback. | Valid/invalid reload and rollback run under continuous traffic with no partial worker state, secret disclosure, downtime, or fail-open dispatch. |
| D-21 | P1 | Own every worktree through a namespace/lease; cleanup in normal/failure/cancel paths and reconcile expired leases after crashes without touching active or foreign worktrees. | Kill-at-every-phase tests leave no owned worktree/process but preserve concurrent project worktrees and tracked user changes. |
| D-22 | P1 | Use private mode-0700 per-run temp roots with ownership manifests, bounded size/age and exact cleanup on exit/signals/crash via a scoped reaper. | Crash/restart tests remove only expired owned roots; global `/tmp`, shared pytest roots and another project remain byte-for-byte unchanged. |
| D-23 | P1 | PID records include PID, start time, boot ID, namespace, executable identity, owner and lease; stale cleanup verifies all fields before signalling or unlinking. | PID reuse, forged file, dead parent, live foreign process and reboot fixtures fail closed; only the exact expired project tree is reaped. |
| D-24 | P1 | Stream MCP stderr through a byte-bounded ring buffer with per-line/event bounds, redaction, backpressure, truncation counters and durable early failure events. | An infinite stderr producer remains within memory/disk limits, is cancelled at policy limit, exposes a bounded diagnostic tail, and does not delay failure detection. |
| D-26 | P2 | Schedule bounded database maintenance using incremental vacuum where supported, a single leader, free-page/size thresholds, IO/time budgets and backup coordination. | Concurrent read/write availability meets the declared SLO during maintenance; file growth is reclaimed; interruption is recoverable and never runs N times under N workers. |
| D-30 | P1 | Enforce model request bytes/tokens, response bytes/tokens/chunks, stream duration, idle timeout, decompression ratio, tool-call count and cumulative fallback budget at the gateway. | Oversized buffered and streamed responses cancel upstream promptly, retain bounded diagnostics, never enter cache/DB/event payloads, and return a typed size error. |

## 9. Bandit remediation specification

### SH-BANDIT-001

The baseline SHALL be ratcheted from the generated JSON by rule, severity and
file. No blanket skips, `nosec`, masked exit code, or test deletion may satisfy
the gate. A finding may be adjudicated only with an owner, exploitability proof,
expiry, compensating control, and regression test; expired adjudications fail.

| Priority | Current categories | Required outcome and test |
|---|---|---|
| P0 | B324 | Remove the security-relevant MD5 use or replace it with SHA-256. A non-security stable fingerprint still SHALL use an explicit safe abstraction and collision test, not suppression. Final count: zero high findings. |
| P1 | B314, B318, B405, B408 | Parse untrusted XML with `defusedxml` or an equivalently maintained hardened parser; disable entities/network/DTD and bound bytes, depth and nodes. Entity expansion and external-reference fixtures SHALL fail within resource limits. |
| P1 | B608 | Parameterize database identifiers/values through reviewed builders; malicious quotes/comments/unions SHALL remain data and cross-tenant queries SHALL fail. |
| P1 | B104 | Bind loopback by default. Non-loopback bind requires explicit config, authentication, TLS, audience and startup audit; tests prove the default listener is not externally reachable. |
| P1 | B108 | Replace predictable/global temp paths with private namespaced directories and descriptor-safe creation; symlink/race/cross-project cleanup tests SHALL pass. |
| P1 | B310 | Permit only explicit URL schemes and destinations, disable implicit file/custom schemes and revalidate redirects/DNS through the SSRF policy. |
| P1 | B323 | Require maintained TLS APIs, hostname/certificate verification, TLS 1.2 or stronger, and configured trust roots; downgrade and invalid-chain tests fail. |
| P2 | B404, B603, B607 | Route subprocesses through the centralized sandbox executor using list argv, absolute verified binaries, clean env, timeout/output/process limits and no shell. Each call site SHALL have untrusted-argument tests. |
| P2 | B101 | Replace runtime/security assertions with typed exceptions and explicit validation. Optimized Python mode SHALL preserve the same denial behavior. |
| P2 | B105 | Remove real embedded credentials and rename/structure benign sentinel examples so they cannot be mistaken for secrets; secret canaries SHALL never be committed or logged. |
| P2 | B311 | Use `secrets` for tokens/nonces/security choices; deterministic PRNGs MAY remain only behind a clearly non-security interface with reproducibility tests. |
| P2 | B110, B112 | Replace silent exception pass/continue with narrow exceptions, typed outcomes and bounded redacted audit events; cancellation/system-exit semantics remain intact. |

The future `sast-gate` SHALL initially prevent any count increase per category,
then require zero high and zero medium findings before this feature is marked
implemented. Every low finding SHALL be fixed or time-bounded and test-backed;
an informational Bandit exit is not a gate.

## 10. Dependency and media-decoder requirements

### SH-DEP-001

Dependency resolution SHALL be reproducible, hash locked, SBOM-attested and
scanned against multiple advisory aliases. A scanner that lacks a new advisory
SHALL not override upstream security release notes.

### 10.1 Pillow 12.2.0 to Pillow 12.3.0

The official [Pillow 12.3.0 security notes](https://pillow.readthedocs.io/en/stable/releasenotes/12.3.0.html)
describe security fixes absent from the current 12.2.0 lock:

- bounded PDF stream decompression;
- `CVE-2026-55798` Windows viewer command injection;
- an EPS `BeginBinary` negative-count infinite loop;
- excessive JPEG2000 component memory accumulation;
- a McIdas out-of-bounds read;
- a TGA run-length out-of-bounds read;
- out-of-bounds writes in large rank filters, crop/paste/alpha-composite paths,
  and mismatched `ImageCmsTransform` modes;
- `CVE-2026-54059`, `CVE-2026-54060`, and `CVE-2026-55379` FontFile
  decompression bombs; and
- `CVE-2026-55380` GD decompression bomb.

Remediation SHALL pin Pillow 12.3.0 or newer in the resolved lock and verify the
wheel hashes/SBOM. All untrusted image/video frames SHALL still decode in a
locked sandbox with input bytes, dimensions, pixels, frames, decompression
ratio, CPU, memory and wall-time limits. EPS/Ghostscript and viewer execution
SHALL be disabled unless separately granted. Regression fixtures SHALL cover
each class without embedding weaponized content in logs. Until the upgrade and
decoder tests land, this requirement is OPEN.

### 10.2 Current pip-audit findings

- `ansible-core 2.21.0` / `PYSEC-2026-3458`: Gludd SHALL reject dependency
  specifications and git-option injection from untrusted Galaxy role metadata,
  avoid runtime install of untrusted roles, and upgrade to the first stable
  fixed compatible release. A malicious `meta/requirements.yml` fixture SHALL
  not alter git configuration or execute code.
- `diskcache 5.6.3` / `PYSEC-2026-2447`: Gludd SHALL not deserialize pickle
  from a directory writable by sandboxed or untrusted code. Replace the default
  serializer/cache or cryptographically authenticate a safe typed format. A
  crafted pickle fixture SHALL remain inert. Directory permissions alone do not
  satisfy this control when hostile code can share the service UID.

Advisory ignores SHALL match all aliases, include a threat-model proof and
expiry, and remain red in the strict gate if the compensating-control test is
missing. These findings are not fixed by this specification.

## 11. Architecture and migration

The implementation SHALL preserve one policy model and use thin adapters for
mature runtimes:

- `security/policy/`: strict schema, normalization, parent-child intersection,
  policy hashing, version store and approval records.
- `security/sandboxes/planner.py`: capability/strength planning and monotonic
  backend selection.
- `security/sandboxes/attestation.py`: observed-state probes and capability
  evidence; no backend self-assertion.
- backend adapters: argument/config compilation only; no custom isolation
  engine.
- `security/credentials/broker.py`: short-lived scoped secret delivery and
  revocation.
- shared event transport: durable policy, denial, heartbeat and cleanup events
  consumed across Gunicorn workers.

Migration SHALL be reversible and zero-downtime:

1. Inventory every dispatch entry point and prove each routes through the
   policy planner; reject uncovered entry points in CI.
2. Land schema and audit-only decisions with no enforcement claims. Compare
   requested and observable backend guarantees.
3. Enable `locked` for CI/red-team fixtures and opt-in projects. Fix
   incompatibilities; do not weaken the profile silently.
4. Make `standard` the existing-install default and `locked` the new
   untrusted-project default. Convert legacy `fail_open` only to an explicit
   development audit mode with warnings and expiry.
5. Require locked mode for model-generated code, downloaded repositories,
   untrusted media, MCP tools, and multi-tenant execution.
6. Remove legacy direct execution after telemetry proves no caller remains.

Each phase SHALL include rollback to the prior immutable policy version and a
mixed old/new binary compatibility test. Database and event schema changes use
expand/migrate/contract; contract occurs only after all active workers and
retained events no longer require the old shape.

## 12. Test strategy

- Unit/property tests: schema bounds, policy intersection, path normalization,
  IP/CIDR/DNS classification, secret redaction, resource arithmetic and event
  serialization.
- Backend contract tests: the same allow/deny corpus runs against every claimed
  backend. Unsupported guarantees are explicit skips only for a profile that
  does not require them; locked missing guarantees fail.
- Integration tests: real Firecracker+jailer, gVisor, nsjail,
  bubblewrap+Landlock+seccomp, Windows AppContainer, FreeBSD jail and macOS VM
  runners on matching CI hosts.
- Red-team tests: filesystem escape, namespace escape, syscall abuse, metadata
  SSRF, DNS rebinding, fork/output/disk bombs, secret canaries, stale leases,
  malicious XML/images/cache bytes and cross-tenant event access.
- ZDD tests: continuous dispatch during policy/image/secret rotation, worker
  restart, invalid update and rollback.
- Performance tests: cold/warm startup, policy compilation, steady CPU/memory
  overhead and cleanup latency. Performance budgets MAY select a stronger
  efficient backend but SHALL never relax a boundary.

## 13. Executable acceptance gates

### SH-ACCEPT-001

This feature SHALL remain Proposed until all required make targets exist in
`config/make_target_contract.json`, their behavioral examples pass, and a clean
development commit produces all of this evidence:

```text
make test-specific TESTFILE=tests/unit/test_security_sandbox_hardening_spec.py
make lint-specs
make security-backlog-strict EXPECT_OPEN=0
make sast-gate MAX_HIGH=0 MAX_MEDIUM=0 MAX_UNADJUDICATED_LOW=0
make pip-audit-gate
make sandbox-contract PROFILE=locked BACKEND=firecracker
make sandbox-contract PROFILE=locked BACKEND=gvisor
make sandbox-contract PROFILE=standard BACKEND=nsjail
make sandbox-contract PROFILE=standard BACKEND=bubblewrap-landlock-seccomp
make sandbox-zdd-test PROFILE=locked WORKERS=2
make check-make-target-contract
make gate-audit
make gate
```

`security-backlog-strict` SHALL fail on any `OPEN` item; the current
informational `security-backlog-gate` is insufficient. `sast-gate` SHALL parse
Bandit JSON and propagate failure rather than relying on the current masked
target. `sandbox-contract` SHALL run behavioral denial probes against the actual
backend and host, not mocks alone.

The final test suite SHALL maintain at least 85% aggregate coverage and no less
than 75% coverage in each individual source file. New security-policy and
sandbox modules SHALL target branch coverage as well as line coverage. All
warnings, informational package-update notices and skips SHALL be resolved or
represented by a scoped, expiring, test-backed platform limitation.

No status change is permitted without exact gate output, commit hash, policy
artifact hashes, backend/runtime versions, and retained audit-event evidence.
