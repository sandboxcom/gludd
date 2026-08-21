# Feature: Sandbox — Multi-Backend Isolated Agent Execution

**Status: COMPLETE** | **Completed: 2026-08-03** | **Created: 2026-08-03** | **Target: v0.1.0-beta.3**

## 1. Overview

The sandbox collection (`general_ludd.sandbox`) provides four isolation backends
for agent tool-execution: process-level (Landlock/Bubblewrap/Seatbelt/AppArmor/
SELinux/FreeBSD jail), container-based (Docker/Kubernetes), userspace-kernel
(gVisor/runsc), and hardware-virtualized (Firecracker microVM). All backends
share a common contracts layer with configurable resource limits, network policy,
and security policy enforcement. Backend routing is capability-aware via the
CapabilityRouter and auto-detection chain.

## 2. Architecture

- **Ansible collection** (`collections/ansible_collections/general_ludd/sandbox/`):
  galaxy.yml with 10 model_capabilities + 4 role_capabilities (20 tags).
- **Backends** (`src/general_ludd/security/sandboxes/`):
  - Process: Landlock (Linux), Bubblewrap (Linux), Seatbelt (macOS), AppArmor (Linux),
    SELinux (Linux), FreeBSD jail, Windows AppContainer
  - Container: Docker/Kubernetes
  - VM: Firecracker microVM (KVM), gVisor/runsc (userspace kernel)
- **Contracts** (`src/general_ludd/sandbox/contracts.py`): typed SandboxConfig,
  SandboxResult, isolation levels, resource limits, network policy.
- **Backend protocol** (`SandboxBackend`): apply → dispatch → verify → release lifecycle.
- **State root** (`src/general_ludd/sandbox/state_root.py`): per-project, BLAKE2-namespaced,
  owner-only (`0700`), canonical containment-gated runtime sandbox state.
- **Capability router**: capability-based dispatch resolving SandboxConfig to concrete
  backend instances; auto-detection chain (Firecracker → gVisor → container → process).

## 3. Backends

| Backend | Platform | Isolation | Backend File |
|---------|----------|-----------|-------------|
| Landlock | Linux | In-kernel unprivileged | `sandbox_backends.py` |
| Bubblewrap | Linux | User-namespace container | `sandbox_bubblewrap.py` |
| Seatbelt | macOS | Mandatory sandbox profile | `sandbox_macos_seatbelt.py` |
| AppArmor | Linux | LSM profile | `sandbox_apparmor.py` |
| SELinux | Linux | LSM TE/FC policy | `sandbox_selinux.py` |
| FreeBSD jail | FreeBSD | OS-level jail | `sandbox_freebsd_jail.py` |
| AppContainer | Windows | Windows sandbox capability SID | `sandbox_appcontainer.py` |
| Docker | Any | Container runtime | `sandbox_backend_implementations.py` |
| Firecracker | Linux (KVM) | Hardware-virtualized microVM | `vm/firecracker_backend.py` |
| gVisor/runsc | Linux | Userspace application kernel | `vm/gvisor_backend.py` |

## 4. Contracts

The contract layer (`sandbox/contracts.py`, ~400 lines) defines:

| Category | Types |
|----------|-------|
| **Isolation** | `IsolationLevel` (process, container, vm_hardware, vm_userspace), `isolation_rank()` |
| **Config** | `SandboxConfig` (backend, image_path, resource_limits, network_policy, security_policy, state_dir) |
| **Protocol** | `SandboxBackend` (apply/verify/release/dispatch), `SandboxHandle` |
| **Results** | `SandboxResult` (exit_code, stdout, stderr, wall_clock_ms, peak_rss_kb) |
| **Validation** | `validate_config()`, preset configurations (locked/standard/development) |

## 5. Firecracker microVM (Unikernel spec)

The `FEATURE_UNIKERNEL_SANDBOX.md` spec is CLOSED (P1-P6 complete):

- `FirecrackerBackend`: boots microVM via Firecracker REST API, dispatches commands over virtio-vsock
- `GvisorBackend`: runs `runsc run` with OCI bundle
- `image_builder.py`: Alpine + gludd + deps rootfs; cached at `~/.cache/gludd/sandbox/`
- `agent_executor.py`: binary inside microVM — receives SandboxTarget over vsock, executes, returns ProcessResult
- `VMSandboxManager`: VM lifecycle (PENDING→BOOTING→RUNNING→EXECUTING→STOPPED/FAILED), VMMetrics
- `auto_detect()` chain: Firecracker (if /dev/kvm + binary) → gVisor (if runsc) → container → process

## 6. State Root

The `FEATURE_SANDBOX_STATE_ROOT.md` spec is IMPLEMENTED:

- `GLUDD_SANDBOX_STATE_DIR` optional override (must be absolute, canonical, owner-matched)
- Per-project namespace: readable slug + BLAKE2 digest of canonical root
- Base/project/backend/run dirs: `0700`, single-component grammar, digest-suffixed untrusted IDs
- Canonical containment check before access/removal; symlinks rejected
- Per-run directories for gVisor/Firecracker; project-scoped for SELinux/Seatbelt/FreeBSD jail
- Deterministic cleanup: `cleanup_path` → `cleanup_backend` → `cleanup_project` (all idempotent)
- Zero-downtime worker replacement supported (retains exact SandboxState allocation)

## 7. Completion Evidence

- **Collection**: `collections/ansible_collections/general_ludd/sandbox/` — galaxy.yml (10 model_capabilities + 4 role_capabilities, 20 tags), molecule scenario
- **Contracts**: `sandbox/contracts.py` (26 tests — isolation levels, config validation, backend protocol, preset postures)
- **Backends**: 10 backends (Landlock, Bubblewrap, Seatbelt, AppArmor, SELinux, FreeBSD jail, AppContainer, Docker, Firecracker, gVisor) — all with unit tests
- **Firecracker/GVisor**: 227+ unit tests + 31 router integration tests + 44 contracts tests + 52 VM integration tests
- **State root**: `sandbox/state_root.py` — implemented, tested (11 target tests + 11 lifecycle tests)
- **Capability router**: 15 verification tests PASS (collection discovery, capability tags, cross-collection isolation)
- **Daemon dispatch**: wired via POST /api/dispatch capability=sandbox via CapabilityRouter
- **Total sandbox tests**: 35 test files covering all backends + contracts + state + policy + dispatch
- **lint**: PASS 0
- **gate-lite**: ALL GREEN (baseline from S69, 4682/4682)

## 8. Auto-jail owner lifecycle

Reviewed 2026-08-20. `SandboxEnforcer` owns every jail it creates when no
external `jail_dir` is supplied. Explicit `close()` invokes the same idempotent
finalizer used when the enforcer owner is released; externally supplied jails
remain unowned and are never removed. Success, path-rejection, fail-open, and
failed-readiness paths therefore leave no implicit `TemporaryDirectory`
cleanup for Python or the test harness.

The long-lived [CPython issue 22427, opened September 17, 2014](https://bugs.python.org/issue22427)
records `TemporaryDirectory` emitting `ResourceWarning` during garbage
collection and explains that nested finalizer deletion order is unreliable.
Gludd keeps the mature standard-library resource but registers cleanup on the
application owner, rather than relying on the nested temporary object's
warning-producing fallback or suppressing the warning.

This change is ZDD-safe: old and new workers own disjoint, namespaced jail
directories, and rolling replacement does not change job or wire formats.
Rollback is code-only; an old worker retains responsibility for its own jail.
The fix creates no process or client, adds no polling, and preserves the
existing bounded executor. Regression coverage forces owner collection and
asserts that the jail is gone, while the full sandbox/security workflow runs
with warnings treated as errors without test-side cleanup.
