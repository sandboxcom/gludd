# Secure sandbox runtime-state root

**Status:** Implemented on `enhance/sandbox-state-root`

## Status and scope

Implemented on `enhance/sandbox-state-root`. This contract covers host-side
runtime artifacts created by the SELinux, macOS Seatbelt, FreeBSD jail,
gVisor, and Firecracker backends. It deliberately does not change an isolated
guest's `/tmp`; bubblewrap continues to create an ephemeral `/tmp` directory
inside the sandbox.

The implementation uses the Python standard library and the repository's
existing `.gludd` project-root resolver. `platformdirs` was evaluated but is
not a declared project dependency, and its platform runtime paths do not add
the ownership, symlink, containment, or project-isolation checks required by
this threat model. Avoiding a new dependency also keeps the security path
available during early daemon startup.

## Operator contract

- `GLUDD_SANDBOX_STATE_DIR` optionally selects the parent directory. It must
  be absolute, canonical (no symlink components), and owned by the Gludd uid.
- Without the override, Gludd uses the canonical stdlib temporary directory
  beneath `gludd-sandbox-state-<uid>`.
- Every project gets a stable namespace composed of a readable project slug
  and a BLAKE2 digest of its canonical root. Projects with the same basename
  therefore cannot share state.
- The base, project, backend, and per-run directories are created as `0700`.
  A path owned by another uid is rejected before Gludd changes its mode.
- Backend and artifact components use a strict single-component grammar.
  Untrusted identifiers are normalized and digest-suffixed before use.
- Canonical containment is checked before access or removal. Symlinks in the
  target or its tree cause cleanup to fail closed rather than follow them.

## Lifecycle and zero-downtime behavior

Each gVisor and Firecracker instance gets a unique per-run directory. SELinux
policy builds and fallback FreeBSD jail roots are project-scoped; Seatbelt
profiles are project-scoped private files. Successful and partial backend
handles retain the exact `SandboxState` allocation, so release cleans the old
location even if a rolling worker receives a new environment afterward. This
supports zero-downtime worker replacement without cross-worker state deletion.

Cleanup is deterministic by contract:

1. `cleanup_path` removes only a contained non-project target.
2. `cleanup_backend` removes only the named backend subtree.
3. `cleanup_project` removes only the project namespace and leaves the
   operator-selected base intact.
4. Every cleanup operation is idempotent: the first removal returns true and
   subsequent calls return false.
5. Paths outside the namespace and any symlink-bearing target are rejected.

Backend release paths invoke this scoped cleanup after stopping the native
process/module. Spawn, socket-readiness, and REST-configuration failures also
clean the partially allocated runtime directory.

## Upstream and operator evidence

Official sources:

- Python documents that `tempfile` chooses the platform temporary location
  and creates temporary resources securely; Gludd adds the stronger stable,
  owner-only namespace around it:
  <https://docs.python.org/3/library/tempfile.html>.
- Python's `Path.resolve()` is the canonical path primitive used for
  containment checks:
  <https://docs.python.org/3/library/pathlib.html#pathlib.Path.resolve>.
- The considered `platformdirs.user_runtime_path` API is documented at
  <https://platformdirs.readthedocs.io/en/latest/api.html#platformdirs.api.PlatformDirs.user_runtime_path>.
- Firecracker's own getting-started guide tells operators to remove a previous
  API socket before startup, demonstrating that socket cleanup is a caller
  lifecycle responsibility:
  <https://github.com/firecracker-microvm/firecracker/blob/main/docs/getting-started.md>.
- Firecracker documents that one vsock UDS path cannot be multiplexed across
  VMs and must be overridden to avoid collisions:
  <https://github.com/firecracker-microvm/firecracker/blob/main/docs/vsock.md#unix-domain-socket-renaming>.

Long-lived user/forum reports:

- Firecracker issue #923 (opened in 2019) describes the polling and stateful
  interaction required around its AF_LOCAL API socket. Per-run directories
  and failure cleanup make that state explicit rather than leaving stale
  shared `/tmp` entries:
  <https://github.com/firecracker-microvm/firecracker/issues/923>.
- gVisor issue #9918 (discussion spanning 2024) records rootless runs failing
  on host files because uid ownership does not match the runtime mapping. The
  Gludd state root therefore verifies host ownership before use:
  <https://github.com/google/gvisor/issues/9918>.
- gVisor issue #4768 has remained open since 2020 and includes an operator
  report where shared host `/tmp` overlay state became root-owned and
  inaccessible. Gludd avoids shared backend roots and enforces `0700` per-run
  ownership:
  <https://github.com/google/gvisor/issues/4768>.

These reports are treated as operational evidence, not as substitutes for the
local security tests. `tests/unit/test_sandbox_state.py` pins all invariants and
the existing backend suites pin native command/lifecycle behavior.
