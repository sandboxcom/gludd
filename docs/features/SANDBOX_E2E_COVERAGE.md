# Sandbox End-to-End Coverage

## Contract

Gludd's sandbox E2E suite is an executable inventory of every concrete backend
under `general_ludd.security.sandboxes`. It discovers backend classes from the
installed package and compares that result with the test matrix. A new backend
therefore fails the suite until its cross-platform import, runtime protocol,
availability probe, and platform-specific enforcement path are represented.

The beta4 matrix contains nine backends:

| Boundary | Backends | Evidence on every host | Host-gated evidence |
| --- | --- | --- | --- |
| Hardware or application kernel | Firecracker, gVisor | import, protocol, availability, unavailable attestation, idempotent release | VM image and lifecycle suites on an equipped Linux host |
| Linux process or namespace | Landlock, bubblewrap | import, protocol, availability | real lifecycle and outside-path denial |
| Linux system policy | AppArmor, SELinux | import, protocol, availability | real apply, verify, and release when the toolchain is active |
| Native OS policy | FreeBSD jail, macOS Seatbelt, Windows AppContainer | import, protocol, availability | real lifecycle on the matching supported OS |

An unavailable toolchain is not reported as successful enforcement. VM
backends must return `applied=False`, preserve a reason, produce no `ok`
verification finding, and tolerate repeated cleanup. The auto-detector may
return `None`, but any selected class must be in the discovered matrix, conform
to `SandboxBackend`, and still report itself available.

## Security assertions

The live Linux bubblewrap probe verifies both sides of the workspace contract:
an allowed file can be read, a new file can be written, and neither a direct
outside path nor a symlink inside the workspace that resolves outside can be
read. The Seatbelt probe checks an explicit outside denial. The Landlock probe
runs in a child process because its restriction is irreversible and verifies
that an inside file remains readable while `/etc/passwd` is denied.

Platform skips are capability statements, not passes. The portable discovery,
protocol, probe, detector, and negative-attestation cases always run. CI must
retain Linux coverage for the kernel-enforcement cases and macOS coverage for
the supported-host availability behavior. A release claim must identify any
live backend case skipped because its host prerequisite was absent.

## Operator and practitioner evidence

The following long-lived reports were reviewed on 2026-08-20:

- [bubblewrap issue #198](https://github.com/containers/bubblewrap/issues/198),
  open since 2017, shows that an installed binary can still fail to create a
  user namespace under a host `hidepid` policy and can leave processes behind.
  Gludd consequently keeps a real smoke probe, bounded subprocess timeouts,
  and explicit lifecycle cleanup instead of treating `which bwrap` as proof.
- [Firecracker issue #1111](https://github.com/firecracker-microvm/firecracker/issues/1111),
  opened in 2019, records `/dev/kvm` appearing readable and writable inside a
  nested environment while the VMM still cannot open a usable KVM device.
  Static availability is only admission to the real boot and verification
  suites; it is never a successful sandbox attestation by itself.
- [gVisor issue #9918](https://github.com/google/gvisor/issues/9918), opened in
  2024, documents rootless UID mapping that makes host-owned files inaccessible
  despite matching numeric UIDs. The E2E contract therefore exercises the OCI
  bundle lifecycle separately from binary detection and treats failed apply as
  a negative attestation.
- [Apple Developer Forums thread 661939](https://developer.apple.com/forums/thread/661939),
  active from 2020 through 2025, includes Apple guidance that custom Seatbelt
  policy is unsupported for third-party products and recommends virtualization
  for untrusted programs. Gludd keeps Seatbelt host-gated and requires a Linux
  VM boundary when the supported macOS capability is absent.

## Verification commands

Use the narrow suite while developing, then the VM and release-image lanes,
and finally the complete E2E gate:

```text
make test-specific TESTFILE=tests/e2e/test_sandbox_backends_e2e.py
make test-vm
make verify-sandbox-image
make test-e2e
```

Each command streams pytest or image-verification progress and has a bounded
timeout at its process boundary. Sandbox runtime paths use Gludd's project
namespace; tests use per-case temporary directories and never reuse another
project's VM sockets, OCI bundles, or policy files.

## ZDD, observability, resources, and rollback

The suite is additive and requires no state migration. For zero-downtime
delivery, build a beta4 worker and its sandbox images, run the portable and
equipped-host lanes, start replacement workers, require a harmless apply and
verify attestation, route new tasks only to verified workers, drain the prior
workers, and then release their sandboxes. An `applied=False`, `fail` finding,
missing image, timeout, or leaked process blocks promotion for a profile that
requires isolation.

Tests cap every direct sandbox subprocess at 10 or 30 seconds. Firecracker and
gVisor resource limits remain covered by `make test-vm`; operators should pair
that evidence with `make ps` and `make tmp-gludd-usage` before and after the
lane. Logs include backend names, return codes, bounded stderr, and explicit
skip reasons without exposing sandbox tokens or outside-file contents.

Rollback routes new work to the previous verified immutable build, drains the
beta4 workers, and releases their namespaced runtime state. It must never turn
a required sandbox profile into an unsandboxed dispatch merely to preserve
availability.
