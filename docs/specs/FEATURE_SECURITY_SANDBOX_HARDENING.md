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

The baseline was refreshed on development after commit `53d3085d`, including
the pending secure-state migration, with repository make targets. Generated
files are evidence snapshots, not allowlists.

- `make security-backlog-gate` reported `TOTAL=24`,
  `LANDED-VERIFIED=10`, and `OPEN=14`. The current target intentionally exits
  successfully when controls are open, so it is not a completion gate.
- `make sast` generated a fresh report with `SEVERITY.HIGH: 0`,
  `SEVERITY.MEDIUM: 0`, and `SEVERITY.LOW: 505`. B104, B108, B310, B323 and
  B608 are all zero; no finding was suppressed.
  The target still masks
  Bandit's finding exit status and is therefore an inventory, not a strict
  completion gate.
- `make pip-audit-gate` reported `No known vulnerabilities found`; the project
  package is not published on PyPI and is explicitly shown as unauditable.
  `make node-deps-audit` reported zero vulnerabilities. Neither result replaces
  source-level or runtime-boundary review.
- `uv.lock` resolves Pillow 12.3.0 and safehttpx 0.1.7. Pillow's security update,
  the pinned-IP outbound transport, and the Azure-pricing migration are landed,
  while decoder resource isolation remains open.
- `SandboxEnforcer` describes a fail-closed contract, while
  `security.sandboxes.SandboxBackend` and several backends explicitly fail
  open. The effective dispatch boundary is therefore not yet uniformly
  fail-closed.
- The current `_isolate_network()` changes a socket timeout but does not create
  an operating-system network boundary. Locked workloads SHALL reject this as
  unenforced, not report it as isolated.
- `make security-audit SECURITY_AUDIT_HEARTBEAT_SECS=10
  SECURITY_AUDIT_PHASE_TIMEOUT_SECS=300 SECURITY_AUDIT_VALIDATE_ONLY=0
  SECURITY_AUDIT_SUMMARY=.gate-logs/security-audit-current.json` completed in
  54.798 seconds. Secrets scanning emitted three 10-second heartbeats before
  passing; SAST, Python and Node dependency audit, and backlog inventory all
  streamed their phase boundaries. No phase failed or timed out.
- The generated SAST summary reported `baseline_available=false` and the backlog
  target still treats all 14 open controls as informational. Consequently the
  successful audit is accurate inventory evidence, not a security-completion
  gate. SEC-SBX-001 remains proposed until the acceptance rules below are green.

### 2.1 Implemented production-boundary slice (2026-08-01)

One production seam is now fail-closed: the daemon-wired
`EventLoop._dispatch_execute_job_isolated()` path. Daemon startup resolves the
validated `vm_sandbox.profile` (`locked` by default), pins its immutable policy
hash, and supplies the shared `DurableSandboxAttestationStore`. Before the agent
job runs, this seam requires a sandbox executor, permission spec, selected and
applied backend, successful backend verification, and a typed independent
`observe_runtime` result. It commits a tenant-partitioned allow or denial event
before returning the decision. Missing guarantees, missing observation, an
unsealed event, or audit-store failure prevents execution and releases any
created backend handle.

This is intentionally **not** a global-enforcement claim. Direct
`AgentDispatcher.dispatch_one()` callers, dynamic `/api/dispatch` handlers,
worker `/jobs/execute`, administrative code execution, other MCP/tool seams,
and explicitly unwired legacy `EventLoop` instances are not covered by this
slice. They remain open until they route through an equivalent durable admission
boundary. Current Firecracker/gVisor and process backends also lack the required
independent structured probe, so this seam truthfully records denial rather than
converting `available()` or `applied=True` into an allow. Full backend canaries,
escape tests, heartbeats, cleanup attestations, and policy promotion remain
open.

The pinned `ResolvedSandboxProfile` preserves the local ZDD invariant: an
in-flight dispatch cannot change policy hash while a replacement worker and
profile are prepared. Rollback currently means starting replacement workers on
a previously validated immutable profile and draining the rejected/new workers;
the shared version store, worker acknowledgements, atomic router switch, and
automated forward-only rollback required by SH-ZDD-001 are not yet implemented.
An invalid profile fails daemon construction before traffic changes.

The fail-closed choice follows the already-recorded long-lived operator reports:
[Bubblewrap #324](https://github.com/containers/bubblewrap/issues/324) shows
binary presence surviving while host namespace policy makes the sandbox
unusable; [nsjail #236](https://github.com/google/nsjail/issues/236) shows an OS
upgrade invalidating mount setup; and [Apple Developer Forums thread
661939](https://developer.apple.com/forums/thread/661939) documents the
unsupported/deprecated Seatbelt path. These are evidence for requiring observed
host state at admission, not new claims of backend completeness.

### 2.2 Closed audit controls D-08 and D-24 (2026-08-01)

The executable backlog gate now reports `LANDED-VERIFIED=10` and `OPEN=14`.
These are narrow control claims, not completion of SEC-SBX-001:

- D-08 validates Ansible extra vars before runner-file creation and before both
  templating paths. Configurable ceilings bound depth, item count, string/byte
  values and total bytes; safe YAML parsing/serialization is required; tags,
  directives, anchors, aliases, merge operators, cycles, shared containers,
  non-finite numbers, custom objects and non-string keys fail closed.
- D-24 drains MCP stderr concurrently into a redacted byte/line-bounded tail.
  Configurable limits have hard ceilings; total output, line-size and line-count
  breaches terminate the child promptly and expose only sanitized structured
  diagnostics. Shutdown boundedly finishes the drain and discards resolved
  secret values.

Both controls have red-first behavior tests and source-wiring regression probes.
The remaining table below therefore contains only the 14 controls still open.

### 2.3 Namespaced secure runtime state (2026-08-01)

The B108 migration routes runtime state through one configurable
`GLUDD_STATE_DIR` allocator. Project namespaces and their parents are owner-only
(mode 0700); state files are written mode 0600 with flush/fsync durability;
symlink, ownership and containment violations fail closed; cleanup targets only
the exact owned namespace. Callers no longer rely on global predictable paths.
Migration tests cover every former B108 call site, cross-project isolation,
unsafe roots and compatibility semantics. This closes the medium-severity SAST
inventory without a Bandit skip or suppression; it does not by itself close the
crash-reaper and PID-identity requirements in D-22 and D-23.

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

### 4.3 Long-lived lifecycle and multi-worker evidence

These reports constrain configuration delivery as well as backend choice:

- Firecracker issue
  [#923](https://github.com/firecracker-microvm/firecracker/issues/923), opened
  in 2019, describes the polling and stateful sequence required to configure a
  VM through its local API socket. Gludd SHALL compile one immutable,
  digest-addressed instance bundle before launch and attest the resulting
  machine; a partly configured VM SHALL never receive work.
- gVisor issue
  [#4768](https://github.com/google/gvisor/issues/4768), open since 2020,
  records an OCI overlay configuration that worked with `runc` but behaved
  differently with `runsc`, including host temporary-directory ownership and
  write failures. A portable policy SHALL compile backend-specific mounts and
  run an actual read/write/escape canary rather than infer compatibility from
  an OCI document.
- gVisor issue
  [#9918](https://github.com/google/gvisor/issues/9918), opened in 2024,
  records rootless UID mapping making a host-owned bind mount inaccessible.
  Identity mapping, bind ownership and intended access mode SHALL be explicit
  policy fields and independently observed after launch.
- Gunicorn issue
  [#1562](https://github.com/benoitc/gunicorn/issues/1562), opened in 2017,
  records reload detection gaps, including files or directories not watched and
  preloaded modules surviving worker replacement. Gludd SHALL never treat a
  file watcher, signal or process birth alone as proof that every worker loaded
  a security generation.
- Gunicorn issue
  [#1236](https://github.com/benoitc/gunicorn/issues/1236), open since 2016,
  records unspecified and worker-dependent keep-alive behavior during graceful
  shutdown. ZDD SHALL use generation-aware admission, bounded drain deadlines,
  durable in-flight ownership and explicit cleanup acknowledgement rather than
  assuming graceful shutdown completed.
- Gunicorn issue
  [#1299](https://github.com/benoitc/gunicorn/issues/1299), opened in 2016,
  records operators seeking per-worker memory protection for hostile archives;
  later operator experience in the thread moved enforcement to cgroups because
  Python-process accounting missed native-library consumption. Gludd SHALL
  enforce resource ceilings at the kernel or VM boundary and only use
  process-local metrics as additional evidence.

Forum reports are operational evidence, not acceptance evidence. Every derived
rule below still requires a reproducible local canary and a negative test on the
actual runtime and host generation.

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

The complete hierarchy SHALL also support a tenant layer between administrator
and user. The currently landed resolver does not yet implement that layer and
therefore does not satisfy the complete multi-tenant contract. Every resolved
leaf SHALL expose its source layer and narrowing history in the redacted dry-run
output.

### 5.1 Configuration semantics and authority

The YAML above is the minimum locked-profile example; the matrix below is the
normative complete control surface. Every named leaf SHALL be present in the
versioned schema even when its value is `deny`, an empty set or `disabled`.
Security behavior SHALL NOT depend on an undocumented environment variable or
backend default.

The scope abbreviations used in the matrix are:

- **A**: system administrator defines the signed envelope and compiled hard
  ceiling.
- **T/U/P/G/W**: tenant, user, project, agent and work-item layers may only
  narrow the parent value. `G` denotes agent to avoid ambiguity with **A**.
- **E**: a separately authenticated, time-bounded emergency widening may be
  requested. It never changes a built-in hard invariant and SHALL record
  requestor, approver, reason, diff, expiry and revocation.

Sets SHALL use canonical typed entries and intersection semantics; duplicate or
ambiguous entries fail. Numeric limits use explicit byte, millisecond, second or
token units, have finite locked defaults and compiled maximums, and reject
booleans, coercion, NaN, infinity and overflow. A lower resource maximum is a
narrowing. A stronger required isolation tier, shorter TTL, smaller allowlist,
more durable audit level or more restrictive access mode is also a narrowing.
No downstream layer may disable attestation, denial auditing, metadata denial,
ownership checks, redaction or cleanup verification.

### 5.2 Complete configurable-control matrix

| Control family and canonical policy keys | Locked baseline and non-bypassable invariant | Authorized scope and merge rule | Activation boundary | Independent evidence and failure behavior |
|---|---|---|---|---|
| Policy identity: `schema_version`, `policy_id`, `parent_version`, `posture`, `tenant_id`, `approval_ref` | Strict known schema; immutable canonical document and digest; `locked` default for untrusted work | A selects envelope; T/U/P/G/W intersect; E may approve a finite widening below compiled hard invariants | New signed generation; each work item pins one version | Durable canonical hash, signer, provenance and layer diff; parse, signature, parent or expiry failure denies before allocation |
| Backend: `backend.preference`, `minimum_strength`, `require_attestation`, `fallback`, `runtime_version`, `image_digest`, `kernel_digest`, `canary_ttl_seconds`, `devices` | Attestation required; fallback deny; no mutable/latest image; no host device except an explicit typed grant | A allowlists runtimes/images/devices; descendants remove choices or require greater strength | Replacement backend pool and worker generation | Runtime, image, kernel, jailer/namespace/VM identity and negative canary observations; missing guarantee denies and releases the handle |
| Filesystem: `filesystem.root`, `source`, `workspace`, `mounts`, `host_paths`, `max_bytes`, `max_inodes`, `max_file_bytes`, `read_only_toolchain` | Empty/private root; source and toolchain read-only; host paths empty; finite byte/inode/file bounds | A allowlists canonical mount roots and modes; descendants may remove, make read-only or reduce limits | New sandbox instance; never mutate mounts of an in-flight instance | Mount table, canonical descriptor-relative paths, ownership and quota probe; traversal, symlink, hard-link or quota ambiguity denies |
| Network: `network.mode`, `protocols`, `hosts`, `cidrs`, `ports`, `dns`, `redirects`, `proxy`, `max_connections`, `max_bytes`, `grant_ttl_seconds`, `deny_metadata` | Isolated loopback only; no egress; metadata and sibling ranges always denied without a typed exceptional capability | A defines destination envelope; T/U/P/G/W intersect destinations and budgets; E required for metadata-class access | Versioned proxy/firewall rules prepared before route switch | Namespace, firewall/proxy rule hash, DNS and redirect canary, connection and byte counters; partial rule application or rebinding denies/cancels |
| Execution identity: `process.uid_map`, `gid_map`, `supplementary_groups`, `capabilities`, `no_new_privileges`, `namespaces`, `syscall_profile`, `executables`, `environment` | Unique non-host identity; empty groups/capabilities/environment; no-new-privileges; reviewed syscall and executable allowlists | A defines maps/profiles/allowlists; descendants only remove entries or require more namespaces | New sandbox instance and executable digest | Observed UID/GID maps, capabilities, namespace IDs, seccomp/LSM hash, argv and executable digest; mismatch denies before exec |
| Process lifecycle: `process.max_pids`, `max_threads`, `open_files`, `cpu_seconds`, `wall_seconds`, `core_bytes`, `stack_bytes`, `output_bytes`, `cancel_grace_seconds`, `drain_seconds` | Every limit finite; core dumps disabled; descendants cannot outlive the namespaced owner; cancellation kills the complete tree | A sets ceilings and drain maximum; T/U/P/G/W reduce them | New work item; counters remain pinned for its lifetime | cgroup/VM counters, PID lineage and verified post-cancel absence; limit breach emits typed reason, bounded tail and cleanup result |
| Runtime state and leases: `state.root`, `worktree.root`, `namespace`, `lease_seconds`, `reaper_interval_seconds`, `pid_identity_fields`, `cleanup_budget_seconds` | Owner-only namespaced roots; symlinks and foreign ownership denied; PID identity includes start/boot/executable/owner; exact-scope cleanup only | A chooses approved canonical parents and maximum lease; descendants choose contained namespaces and shorter leases | Replacement worker may change parent; existing handles retain old allocation until verified release | Ownership manifest, lease epoch, PID identity and cleanup attestation; stale/forged/foreign state is quarantined and never signalled or deleted |
| Ingress and archives: `ingress.transport_bytes`, `decoded_bytes`, `depth`, `items`, `string_bytes`, `file_count`, `archive_ratio`, `request_seconds`, `content_types` | Finite limits at transport and decoded schema boundaries; unknown fields/types and ambiguous encodings denied before side effects | A sets protocol/type allowlist and hard ceilings; descendants lower budgets or remove types | Shared ingress generation plus pinned work-item schema version | Incremental counters at socket, decoder and model boundary; breach stops reads, creates no workspace/process and emits bounded denial |
| Model, tool and media budgets: `model.request_tokens`, `response_tokens`, `response_bytes`, `stream_chunks`, `stream_seconds`, `idle_seconds`, `tool_calls`, `fallback_attempts`, `spend`, `media_pixels`, `media_frames`, `media_ratio` | Every count, time and spend finite; no unbounded stream/fallback; hostile media decoded only in locked isolation | A sets provider/model/media envelope; descendants reduce limits and provider/tool sets; spend widening requires E | Gateway generation; each request pins one cumulative budget across fallback | Provider usage plus local byte/token/chunk/time counters and sandbox decoder metrics; breach cancels upstream and excludes oversized data from cache/events |
| Secrets: `secrets.mode`, `allowed_refs`, `audiences`, `max_ttl_seconds`, `idle_ttl_seconds`, `max_uses`, `delivery`, `revoke_on` | Brokered opaque references only; empty locked allowlist; no environment/argv/image delivery; revoke on every terminal path | A allowlists broker/mount/audience; T/U/P/G/W intersect exact refs and shorten TTL/use count; E cannot expose raw values in policy | Versioned broker scope with overlap only for verified rotation | Token audience/tenant/agent binding, use counter, expiry/revocation and redaction canary; unavailable audit or broker denies delivery |
| Authentication and administration: `auth.methods`, `mtls`, `session.absolute_ttl_seconds`, `idle_ttl_seconds`, `audiences`, `csrf`, `psk_rotation`, `admin_capabilities` | Auth required on mutation; secure cookie/token defaults; explicit admin capability; no ordinary PSK privilege inheritance | A defines identities/audiences/capabilities; descendants cannot mint authority; E uses independent approver | Shared identity generation and bounded dual-key overlap | Cross-worker issue/revoke/expiry/rotation probes; fixation, replay, stale key, wrong audience or missing CSRF denies and audits |
| Admission, concurrency and spend: `limits.identity`, `tenant`, `route`, `global`, `queue_depth`, `concurrency`, `retry_after_seconds`, `provider_spend`, `database_rows` | Shared bounded limits at every scope; unauthenticated mutation denied; retries consume the same parent budget | A sets global/tenant envelope; T/U/P/G/W reduce capacities and spend | Atomic shared-store generation; not process-local worker state | Cross-worker token/lease counters and provider reconciliation; exhaustion returns typed bounded retry/denial without duplicate acceptance |
| Database and migration: `database.wal_bytes`, `checkpoint_pages`, `busy_timeout_ms`, `disk_reserve_bytes`, `maintenance_io_bytes`, `maintenance_seconds`, `leader_lease_seconds`, `backup`, `migration_mode` | Finite WAL/disk/time budgets; one maintenance leader; backup/restore evidence; expand-contract default; destructive changes separately approved | A defines datastore bounds and migration authority; tenants/projects may only reduce quotas | Expand, migrate, verify, then contract after old-worker retirement | WAL/disk/reader/leader telemetry, schema digest and restore canary; lock, drift, disk pressure or failed migration leaves old schema usable |
| Audit and event transport: `audit.sink`, `durability`, `heartbeat_seconds`, `payload_bytes`, `retention_seconds`, `redaction_profile`, `include_denials`, `subscriber_lag_events` | Durable shared sink; denials/launch/limit/secret/cleanup events mandatory; bounded redacted payloads; process-local delivery insufficient | A defines minimum durability/retention and maximum payload/heartbeat; descendants may add events, shorten heartbeat or extend retention | Expand-compatible event schema before producer switch | Monotonic committed sequence, tenant partition, integrity seal, worker/subscriber cursor and lag; sink failure denies launch and lag pressure degrades safely |
| Supply chain: `supply_chain.allowed_digests`, `signers`, `sbom_required`, `advisory_policy`, `licenses`, `max_artifact_bytes`, `offline` | Digest and signature pinning; SBOM and advisory gate; no runtime install from untrusted metadata; finite artifact size | A owns signers/digest/license/advisory exceptions; descendants only reduce allowed artifacts; exceptions require E and expiry | New immutable image/toolchain generation | Signature, digest, SBOM, provenance and scanner/adjudication evidence; missing, mutable, expired or oversized artifact denies promotion |
| Delivery policy: `rollout.canary_count`, `shadow_seconds`, `worker_ack_timeout_seconds`, `max_unavailable`, `drain_seconds`, `rollback_window_seconds`, `failure_thresholds` | Prepare/verify/CAS-switch/drain; zero unsandboxed or unversioned executions; old artifact retained through rollback window | A defines safe minima/maxima; descendants may demand more canaries, longer shadow or stricter thresholds | Shared routing generation; never in-place mutation | Signed manifest, every-worker acknowledgement, continuous traffic probes, routing epoch and cleanup acknowledgements; threshold or split-brain freezes/rolls forward to a safe prior version |

### 5.3 Configuration source, introspection and compatibility contract

All supported sources SHALL feed the same parser: signed administrator file,
tenant/user/project files, agent/work-item request, CLI flags, API payload and
documented legacy environment adapters. Operators SHALL be able to disable any
source below the administrator layer. Source precedence, file ownership,
signature requirements and allowed roots SHALL themselves be administrator
configuration; remote URLs are forbidden as implicit configuration sources.

The redacted `config explain` and `config diff` interfaces SHALL report schema
version, effective value, unit, built-in default, compiled hard range, source
layer, narrowing chain, approval metadata, activation class and restart/drain
requirement for every leaf. Secret values, bearer material and raw untrusted
payloads SHALL never appear. An offline `config compile` SHALL produce the exact
signed canonical artifact and policy hash later loaded by workers.

Schema evolution SHALL declare minimum/maximum reader and writer versions plus
an explicit migration. A new binary SHALL read the current and immediately
previous immutable schema during a rolling deployment. Unknown keys never
silently disappear; removed keys fail with a replacement path until the
documented compatibility window closes. Legacy environment adapters SHALL emit
a bounded deprecation event and may only map to the same validation and
authority rules.

### 5.4 Configuration completeness acceptance

The schema is not complete until a mechanical inventory proves that every
security-relevant constant, environment read, daemon flag, backend option and
per-provider limit maps to exactly one matrix leaf or an explicit non-security
setting. The checker SHALL fail on undocumented duplicate knobs, conflicting
defaults, unit mismatches, unbounded values, values read after generation pinning
or code paths that bypass normalization. Property tests SHALL generate every
valid layer combination and prove monotonic narrowing; mutation tests SHALL show
that deleting any required validation or attestation changes a test result.

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

### 7.1 Immutable release manifest and state machine

Every candidate SHALL have one signed manifest containing at least:

```text
schema_version, generation, parent_generation, policy_hash, signer,
created_at, expires_at, compatible_reader_versions, compatible_writer_versions,
backend_runtime_digests, kernel_image_digests, rootfs_image_digests,
seccomp_lsm_hashes, firewall_proxy_hashes, secret_key_ids, event_schema_range,
database_schema_range, canary_plan_hash, rollback_generation
```

`secret_key_ids` are opaque identifiers, never secret values. The manifest and
all referenced artifacts SHALL be content addressed and retained until every
work item pinned to them is terminal, cleanup is attested, the event-retention
compatibility window closes and the configured rollback window expires.

The durable promotion state machine SHALL be:

```text
draft -> compiled -> canary -> shadow -> ready -> active -> draining -> retired
                       |          |        |         |
                       +----------+--------+---------+-> rejected
```

Transitions use compare-and-swap on the current routing generation and append a
committed event before externally visible routing changes. Replaying a
transition is idempotent. A controller crash SHALL resume from durable state,
reconcile actual workers/backends/firewall rules against the manifest and either
finish the same transition or reject it; it SHALL never infer completion from a
PID file or start time.

### 7.2 Prepare, verify, switch and drain protocol

**Prepare.** Compile the complete inherited policy; verify signatures, schema
compatibility and hard ceilings; stage database/event expand migrations; create
new backend pools, proxy/firewall rules, broker scopes and replacement Gunicorn
workers without changing the active routing epoch. No candidate shares a
mutable policy, socket path, state directory or writable image with the active
generation.

**Verify.** Run allow and denial canaries against every backend and worker,
including filesystem escape, metadata egress, syscall, resource exhaustion,
secret redaction, cross-tenant access, event visibility and cleanup. Workers
acknowledge the exact manifest and their independently observed runtime state in
the shared store. Shadow evaluation compares active and candidate decisions on
redacted request metadata without executing candidate-denied work or exposing
payloads to an unauthorized backend.

**Switch.** A single compare-and-swap publishes a new routing epoch only when
all required acknowledgements are fresh, every canary is green and shared
database/event schemas are compatible. New work pins the candidate generation;
accepted work already owned by the old generation remains there. Workers with a
missing, stale or different acknowledgement are removed from admission before
the switch. A Gunicorn HUP, file-watcher event, health endpoint or process count
is not an acknowledgement.

**Drain.** Stop new admission to the old generation while preserving durable
ownership of its in-flight work. Heartbeats expose work count, oldest age,
connections, leases, resource pressure and cleanup progress. At the bounded
drain deadline, compatible resumable work is checkpointed and re-admitted once;
non-resumable work receives a typed cancellation and complete descendant
cleanup. The old artifacts retire only after zero work, zero owned processes,
zero active leases and a committed cleanup event are independently observed.

### 7.3 Roll-forward rollback and automatic aborts

Rollback SHALL create a new generation whose content matches a previously
verified manifest; it SHALL not mutate the failed generation or move a routing
pointer backward without a new audit identity. The prior version is eligible
only when its binaries, database/event schemas, signatures, images and secret
identities remain compatible and unexpired. If reverting would weaken a newly
mandated security invariant, Gludd SHALL deny affected work until a safe version
is available rather than silently restore the weaker behavior.

The controller SHALL abort before switch, or initiate forward rollback after
switch, on configurable thresholds for any of these signals:

- missing/stale worker or backend acknowledgement;
- attestation, escape-canary, signature or schema-compatibility failure;
- policy-decision divergence outside an approved diff;
- event sink unavailability, sequence gap or subscriber lag beyond its bound;
- authentication, secret, firewall/proxy or database migration partial state;
- denial/error/timeout/cleanup-lag or resource-pressure threshold breach; or
- routing split brain, duplicate ownership or an unversioned execution attempt.

Threshold configuration can only become stricter downstream. Automatic
rollback shares the same canary, acknowledgement, switch and drain protocol as
forward promotion. If neither candidate nor prior generation can attest the
locked contract, admission closes while observability and cleanup remain
available.

### 7.4 Change-class rollout matrix

| Change class | Required preparation | Switch and overlap rule | Rollback/retirement proof |
|---|---|---|---|
| Policy/resource/rate limits | Compile full inherited document; run monotonicity and boundary canaries on every worker | New work pins new counters; old work retains old ceilings unless an emergency kill ceiling is explicitly non-grandfathered | Prior canonical policy remains signed/compatible; shared counters have no orphaned reservations |
| Backend, kernel, rootfs, seccomp or LSM | Build digest-pinned pool; run boot, allow, denial, escape, pressure and cleanup canaries | New pool receives traffic only after per-instance attestation; no mutable image or in-place syscall-policy replacement | Zero VMs/containers/processes/leases/socket paths for retired pool; old digest remains available through rollback window |
| Network proxy/firewall/DNS | Stage versioned rules in a disjoint chain/table and test allowed plus denied destinations | Atomically select complete rule set before candidate admission; never update individual rules under live work | Observed active rule hash matches generation; obsolete rule set has zero referenced sandboxes before removal |
| Secret, PSK, certificate or signing key | Create new identity/scope; validate audience, redaction and revocation; never copy values into manifest | Bounded dual-read/single-write overlap; new work receives new identity while old valid work drains | Old identity rejects after overlap; all derived credentials revoked and no secret appears in events/artifacts |
| Database or event schema | Expand first; verify backup/restore and old/new reader/writer compatibility | Old and new workers coexist only inside declared compatibility range; destructive contract is forbidden during rollback window | Restore canary, no old readers/writers, retained events migrated or expired, then contract in a later generation |
| Gunicorn worker/binary/config | Start a disjoint acknowledged worker generation with exact manifest and shared event transport | Atomic admission epoch; in-flight ownership stays durable and old workers have bounded connection/work drain | No old worker, child, listener ownership or uncommitted event remains; PID/start/boot/executable identity all match before cleanup |

### 7.5 ZDD executable invariants

Continuous-traffic tests SHALL inject a crash or timeout after every state
transition and external side effect. For each injection they SHALL prove:

- every accepted work item has exactly one durable owner and one immutable
  policy/backend generation from admission through cleanup;
- zero work executes without successful attestation or before its admission
  event commits, and a denial is observable while the suite is still running;
- valid in-flight work is neither lost nor duplicated, while newly prohibited
  work is denied with the intended policy reason;
- cross-worker event sequence, secret rotation, rate/spend accounting and
  cancellation remain coherent with at least two Gunicorn workers;
- rollout and rollback have finite configured deadlines and emit bounded
  heartbeats throughout, including when the controller dies; and
- active/retired resources reconcile to the manifest with no orphaned compute,
  firewall rule, credential, worktree, temporary root, process or lease.

The acceptance report SHALL include transition/event sequences, manifest and
artifact hashes, worker acknowledgements, canary results, traffic counts,
latency/error/denial deltas, injected failure points, rollback generation and
post-drain resource reconciliation. A green health endpoint or uninterrupted
TCP listener alone is not ZDD evidence.

## 8. Open D-07 through D-30 control requirements

The table maps exactly the 14 controls still reported `OPEN` by the 2026-08-01
`security-backlog-gate`. A row SHALL remain open until implementation tests and
a real regression probe are both present. Editing the ledger or changing the
test expectation is not remediation.

| Control | Priority | Required implementation | Executable acceptance |
|---|---|---|---|
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
| D-26 | P2 | Schedule bounded database maintenance using incremental vacuum where supported, a single leader, free-page/size thresholds, IO/time budgets and backup coordination. | Concurrent read/write availability meets the declared SLO during maintenance; file growth is reclaimed; interruption is recoverable and never runs N times under N workers. |
| D-30 | P1 | Enforce model request bytes/tokens, response bytes/tokens/chunks, stream duration, idle timeout, decompression ratio, tool-call count and cumulative fallback budget at the gateway. | Oversized buffered and streamed responses cancel upstream promptly, retain bounded diagnostics, never enter cache/DB/event payloads, and return a typed size error. |

### 8.1 D-09 Phase 1: bounded `JobSpec` ingress

`JobSpec` now rejects unknown fields and performs a resource walk before
Pydantic field coercion. The walk accepts JSON-compatible scalars and plain
built-in containers (with tuples retained for safe internal compatibility),
rejects cycles and non-string mapping keys, and bounds aggregate
collection items, nesting depth, and compact UTF-8 JSON bytes. `job_id` is a
filesystem-safe letter/digit/hyphen/underscore identifier, `queue` is an
identifier-like slug, and `playbook` is a relative POSIX path composed only of
safe segments; absolute, parent, backslash and ambiguous-whitespace forms fail
closed. Existing extensionless playbook names and safe nested playbook paths
remain valid. Byte accounting is incremental and rejects an over-limit scalar
before serializing the enclosing structure, so validation does not materialize
a second whole-payload copy.

The effective limits are pinned once when a worker generation imports the
schema. Invalid configuration aborts that generation; values cannot exceed the
compiled hard ceilings:

| Environment variable | Default | Accepted range |
|---|---:|---:|
| `GLUDD_JOB_INGRESS_MAX_DEPTH` | 16 | 2..64 |
| `GLUDD_JOB_INGRESS_MAX_COLLECTION_ITEMS` | 10000 | 16..100000 |
| `GLUDD_JOB_INGRESS_MAX_SERIALIZED_BYTES` | 1048576 | 256..8388608 |
| `GLUDD_JOB_INGRESS_MAX_IDENTIFIER_CHARS` | 128 | 16..256 |
| `GLUDD_JOB_INGRESS_MAX_PLAYBOOK_CHARS` | 255 | 16..1024 |
| `GLUDD_JOB_INGRESS_MAX_QUEUE_CHARS` | 128 | 8..256 |

ZDD changes use prepare/verify/switch/drain: start replacement Gunicorn workers
with the complete new environment, submit boundary canaries to every worker,
route new jobs only after all replacements are ready, and drain workers whose
in-flight jobs remain pinned to the prior limits. Rollback starts a fresh
generation with the previous immutable environment. Operators SHALL NOT mutate
these variables in a live worker or represent a mixed worker generation as one
policy version.

This application-level bound deliberately does not claim to cap bytes before
the ASGI server parses a request. The long-lived Starlette user discussion
[“Limit max request size” #1516](https://github.com/encode/starlette/discussions/1516)
records operator reports from 2020 through a 2023 follow-up that reverse-proxy
body limits alone still leave timeout and deployment gaps. Gludd therefore
requires both the separate transport/request-size control and this decoded
schema boundary; neither substitutes for the other.

D-09 remains **OPEN**. This phase does not yet provide a versioned schema and
policy hash, authenticated tenant/project ownership checks, cross-tenant ID
rejection, per-work-type time/resource/cost ceilings, a bounded denial audit, or
side-effect-free fuzz acceptance across every ingress route. Those controls and
the original table acceptance criteria must land before D-09 can be promoted.

### 8.2 D-13 Phase 1: bounded connection defaults

Every SQLite connection now installs validated, per-database
`journal_size_limit_bytes`, `wal_autocheckpoint_pages`, and `busy_timeout_ms`
settings. Defaults are respectively 64 MiB, 1000 pages, and 5000 ms; startup
rejects values outside 1 MiB..1 GiB, 1..100000 pages, and 1..60000 ms before
provisioning a database file. This removes the prior unbounded `-1` journal
retention setting and the non-configurable lock wait.

D-13 intentionally remains open. SQLite user reports show that
[unfinished readers can make a WAL grow past its auto-checkpoint threshold](https://sqlite.org/forum/info/915267efb1f68f9c525c32e3ae8ef4251285e1111c5f5c221fb348df50119640),
while [passive auto-checkpoints cannot restart or truncate it](https://sqlite.org/forum/forumpost/e37d976043a22458070ce00a4ae00dc6e49ef6dd34aa59e2c5ff7cf5fd543a93).
The remaining phase must add a single maintenance leader, active-reader and
checkpoint telemetry, disk-pressure admission control, bounded coordinated
restart/truncate checkpoints, backup exclusion, and crash/disk-exhaustion
acceptance tests before the control can be marked complete.

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

### 10.1 Pillow 12.3.0 locked; decoder isolation remains open

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

Pillow 12.3.0 is now pinned in the game extras and resolved lock, and the current
dependency audit is clean. That package update does not make hostile media safe
by itself. All untrusted image/video frames SHALL still decode in a locked
sandbox with input bytes, dimensions, pixels, frames, decompression ratio, CPU,
memory and wall-time limits. EPS/Ghostscript and viewer execution SHALL be
disabled unless separately granted. Regression fixtures SHALL cover each class
without embedding weaponized content in logs. The dependency-update slice is
landed; decoder isolation and adversarial resource-limit evidence remain OPEN.

### 10.2 Current dependency audit and historical regression controls

The refreshed Python audit reports `No known vulnerabilities found`, and the
Node audit reports zero vulnerabilities. The former `ansible-core`
`PYSEC-2026-3458` and `diskcache` `PYSEC-2026-2447` entries no longer appear in
the resolved-environment report. Their exploit-class controls remain mandatory:

- Gludd SHALL reject dependency specifications and git-option injection from
  untrusted Galaxy role metadata and avoid runtime installation of untrusted
  roles. A malicious `meta/requirements.yml` fixture SHALL not alter git
  configuration or execute code.
- Gludd SHALL not deserialize pickle from a directory writable by sandboxed or
  untrusted code. Use a safe typed format or authenticate the serialized bytes.
  A crafted pickle fixture SHALL remain inert even when hostile code shares the
  service UID.
- safehttpx 0.1.7 SHALL remain hash locked and covered by redirect, DNS
  rebinding, destination, deadline and response-byte tests; replacing or
  upgrading it requires the same behavioral contract and license review.

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
