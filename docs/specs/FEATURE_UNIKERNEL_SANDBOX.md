# Feature: Unikernel/NanoVM Sandboxed Agent Execution

**Status: COMPLETE** (P1-P6 complete, 227+ unit tests + 31 router integration tests + 44 contracts tests + 15 capability-router verification tests; wiring done via `SandboxCapabilityRouter._BACKENDS` and `_auto_detect()`; capability dispatch wired via galaxy.yml + capability registry; lint green; gate-lite PASS) | **Completed: 2026-08-03** | **Created: 2026-07-14** | **Target: v0.1.0-beta.2**
**P3 progress (2026-07-15):** VM lifecycle manager wired into the daemon-dispatch
surface. ``src/general_ludd/security/sandboxes/vm/lifecycle.py`` adds
``VMSandboxManager`` (boot/dispatch/verify/release/list/observe), ``VMInstance``
state machine (PENDING → BOOTING → RUNNING → EXECUTING → STOPPED/FAILED), and
``VMMetrics`` (boot_ms, dispatch_count, peak_rss_kb, last_verify_findings,
total_dispatch_ms) for the observability layer. 23 unit tests in
``tests/unit/test_vm_lifecycle.py``. Pre-built CI images + real runsc wiring
remain.
**P6 progress (2026-07-15):** Full-lifecycle integration tests landed —
``tests/integration/sandboxes/test_vm_sandbox_integration.py`` (52 tests):
boot→dispatch→verify→release for both backends, error recovery, multi-instance
isolation, observability aggregation, real AgentExecutor subprocess dispatch,
real AF_UNIX Firecracker REST round-trips, and rapid-cycle stress. Fixed a
lifecycle bug the suite caught: ``VMSandboxManager.dispatch`` left instances
stuck in EXECUTING — now restores RUNNING in a try/finally.

## 1. Overview

Tighten the gludd sandboxing stack by running agent tool-execution inside
Firecracker microVMs or gVisor application kernels, replacing the current
process-level (Landlock/bubblewrap/Seatbelt) isolation with hardware-virtualized
or userspace-kernel boundaries.

## 2. Viability Analysis

Agents need: CPython + native extensions, ansible (subprocess/fork), git, outbound API.

| Approach | Python | Ansible | Git | Network | Verdict |
|----------|--------|---------|-----|---------|---------|
| Firecracker | Full | Full | Yes | Yes | BEST — KVM, <5MiB overhead, <125ms boot, AWS-proven |
| gVisor | Full | Full | Yes | Yes | GOOD — userspace kernel, no KVM needed |
| Unikraft | Limited | No | No | Yes | Early; Python not production-grade for ansible |
| OSv | No .so | No | No | Yes | Dead (last release Dec 2022) |

**Decision**: Firecracker primary (strongest isolation). gVisor fallback when KVM
unavailable (Docker/K8s). Existing Landlock/bubblewrap is the lowest-common-denominator.

## 3. Architecture

New module: `src/general_ludd/security/sandboxes/vm/`

- `firecracker_backend.py` — `FirecrackerBackend(SandboxBackend)`: boots microVM
  via Firecracker REST API.
- `gvisor_backend.py` — `GvisorBackend(SandboxBackend)`: runs `runsc run` with OCI bundle.
- `image_builder.py` — builds rootfs image (Alpine + gludd + deps). Cached at
  `~/.cache/gludd/sandbox/`. Publication stages a complete tree beside the
  destination, then holds a destination-derived cross-process `FileLock` while
  displacing the prior tree and atomically renaming the stage. A destination
  removed by an external cleanup between observation and displacement is benign;
  any later publication failure restores a tree this process actually displaced.
- `agent_executor.py` — binary inside microVM: receives SandboxTarget over
  virtio-vsock, executes command, returns ProcessResult.

`SandboxConfig` gains: `backend: Literal["auto","firecracker","gvisor","process"]`,
`image_path`, `vsock_port`.

`detect.py::auto()` adds Firecracker (if /dev/kvm + binary present), gVisor (if runsc
present), then existing chain.

## 4. Implementation Plan

| Phase | Scope | Duration |
|-------|-------|----------|
| P1 | Prototype Firecracker rootfs + boot/kill cycle. Benchmark vs Landlock. | 2-3 weeks |
| P2 | FirecrackerBackend apply/verify/release. agent_executor.py. image_builder.py. Auto-detect chain. | 2-3 weeks |
| P3 | Pre-built images in CI. Wire into daemon dispatch. Observability. GvisorBackend. | 1-2 weeks |
| P4 | VM lifecycle manager (`VMSandboxManager`, `VMInstance` state machine, `VMMetrics`). | done |
| P5 | Error recovery, multi-instance isolation, observability aggregation. | done |
| P6 | Full-lifecycle integration tests (`tests/integration/sandboxes/test_vm_sandbox_integration.py`, 52 tests) + lifecycle bug fix (EXECUTING→RUNNING restore in try/finally). | done |

## 5. Files

| Action | Path |
|--------|------|
| Create | `src/general_ludd/security/sandboxes/vm/__init__.py` |
| Create | `src/general_ludd/security/sandboxes/vm/firecracker_backend.py` |
| Create | `src/general_ludd/security/sandboxes/vm/gvisor_backend.py` |
| Create | `src/general_ludd/security/sandboxes/vm/image_builder.py` |
| Create | `src/general_ludd/security/sandboxes/vm/agent_executor.py` |
| Modify | `src/general_ludd/security/sandboxes/detect.py` |
| Modify | `src/general_ludd/sandbox/enforcer.py` |
| Modify | `Makefile` (build-sandbox-image, verify-sandbox-image) |
| Create | `tests/unit/test_vm_sandbox_backends.py` |
| Create | `tests/bench/test_vm_sandbox_overhead.py` |

## 6. Dependencies

Host: `firecracker` (v1.8+), `runsc` (gVisor), `kvm` kernel module.
Python: `aiohttp` (existing), `pyroute2` (tap device setup).

## 7. Test Plan

- Unit: backends with mock Firecracker API
- Bench: 100-agent dispatch loop — Firecracker vs Landlock
- Integration: daemon → FirecrackerBackend → execute → verify → release
- Regression: existing Landlock/bubblewrap backends still pass

## 8. Atomic publication evidence

Directory publication needs synchronization in addition to rename semantics.
The long-lived Stack Overflow discussion
[“os.link() vs. os.rename() vs. os.replace() for writing atomic files”](https://stackoverflow.com/questions/60369291/os-link-vs-os-rename-vs-os-replace-for-writing-atomic-write-files-what)
records that another process can still invalidate an unsynchronized temporary
path and recommends an explicit lock. The older
[atomic file replacement discussion](https://stackoverflow.com/questions/7645338/how-to-do-atomic-file-replacement)
supports staging on the same filesystem followed by `os.replace`. Gludd combines
those mechanisms and bounds lock acquisition to 30 seconds; it never deletes a
live destination recursively.
