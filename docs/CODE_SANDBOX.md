# AG.12 — Code Execution Sandbox Architecture

## Overview

A multi-layered sandbox for isolated code execution by AI agents, combining Ornith-style
filesystem containment with OS-level kernel enforcement (Landlock, bubblewrap, Seatbelt).

## Layer Model

```
┌──────────────────────────────────────────────┐
│  Layer 4: Timeout watchdog (rlimit + SIGALRM)│
│  Layer 3: Network egress (Landlock / bwrap)  │
│  Layer 2: Filesystem confinement (bind/jail) │
│  Layer 1: Process boundary (subprocess fork) │
└──────────────────────────────────────────────┘
```

All layers FAIL OPEN: a sandbox load failure logs loudly and dispatches with a
"no sandbox" warning rather than wedging the agent.

## Layer 1 — Process Boundary

Code execution happens in a subprocess — never in-process. The daemon forks,
applies sandbox restrictions in the child via `preexec_fn`, then `exec`s the
target. This is the same pattern used by `ornith_sandbox_preexec()`:

1. `prctl(PR_SET_NO_NEW_PRIVS, 1)` — prevent setuid escape (Landlock prerequisite)
2. `chdir(sandbox_dir)` — start in confined root
3. Apply per-backend restrictions (Landlock restrict, bwrap namespace, Seatbelt profile)
4. `exec` the agent binary

## Layer 2 — Filesystem Confinement

Mirrors the Ornith sandbox pattern (`OrnithSandbox` context manager) with three
isolation primitives, selected by OS capability.

### 2a. Temp-Dir Jail (Ornith pattern — universal fallback)

```python
with create_ornith_sandbox() as sandbox:
    subprocess.run(cmd, cwd=str(sandbox.temp_dir), preexec_fn=...)
```

- `tempfile.mkdtemp(prefix="agent-sandbox-")` — disposable working directory
- Agent CWD is the sandbox root; relative paths are confined automatically
- `confine_export_path()` — validates any output path is within an allowlisted
  export root (`ORNITH_EXPORT_ROOT`, `GLUDD_DATA_DIR`)
- Cleanup: `shutil.rmtree` on context exit or timeout kill
- Weakness: no kernel enforcement — a determined subprocess can `chdir("/")`
  and escape. Paired with Landlock/bubblewrap on Linux for kernel enforcement.

### 2b. Namespace Isolation (bubblewrap — Linux)

bwrap creates a new mount+pid+ipc namespace with explicit bind mounts:

- `--ro-bind /usr /usr` — shared libraries, read-only
- `--bind /tmp/agent-<id> /tmp/agent-<id>` — agent workspace, read-write
- `--unshare-all` — all namespaces; `--share-net` only when net caps exist
- `--die-with-parent` — namespace cleanup on agent exit
- No chroot escape possible: the namespace has no mount of `/` outside the binds

### 2c. Landlock Ruleset (Linux kernel 5.13+)

Per-process, unprivileged, irreversible access control:

- `Ruleset(handled_access_fs=READ_FILE|WRITE_FILE|...)`
- `rs.allow(path_prefix, access)` per-file-capability
- Once `landlock_restrict_self()` is called, rules CANNOT be relaxed
  (same model as Chrome/Firefox/OpenSSH sandboxing)
- Recovery requires `fork()` — child inherits parent restrictions, can add more

### Backend selection priority (Linux)

1. Landlock (kernel ≥ 5.13, per-process) — preferred
2. bubblewrap (bwrap on PATH, namespace jail) — secondary
3. AppArmor / SELinux (system-wide, defense-in-depth) — tertiary

## Layer 3 — Network Restrictions

| Backend | Mechanism | Granularity | Hostname-aware? |
|---------|-----------|-------------|-----------------|
| Landlock (ABI ≥ 6) | `allow_net(port=..., access=CONNECT_TCP)` | Port-level | No — needs seccomp/eBPF pairing |
| bubblewrap | `--unshare-net` (cut all) or `--share-net` (allow all) | Binary on/off | No — needs nft/iptables rules |
| Seatbelt | `(allow network-outbound (to (remote tcp "host:port")))` | Host:port | Yes (DNS resolved at enforcement time) |

**Hostname filtering gap:** Landlock and bubblewrap operate at the port/namespace
level, not on DNS names. An `allowed_hosts` constraint on Linux triggers a loud
log warning and requires operator-paired seccomp BPF filter or `nftables` rules
scoped to the agent's network namespace.

**Canonical pattern:** deny all network by default; allow only enumerated
`allowed_ports` at the OS level; hostname enforcement is layered via seccomp
when `allowed_hosts` is non-empty.

## Layer 4 — Timeout & Resource Enforcement

Three complementary mechanisms:

| Mechanism | Enforces | Basis |
|-----------|----------|-------|
| `subprocess.run(timeout=N)` | Wall clock | `ornith_sandboxed_run()` passes `timeout=300` |
| `RLIMIT_CPU` (seconds) | CPU time | `prlimit(RLIMIT_CPU, soft=ORNITH_SANDBOX_CPU_S)` |
| `RLIMIT_AS` (bytes) | Virtual memory | `prlimit(RLIMIT_AS, soft=ORNITH_SANDBOX_MEM_MB * 1024 * 1024)` |

Timeouts are configurable per agent type via env vars:
- `AGENT_SANDBOX_TIMEOUT_S` (default 300)
- `AGENT_SANDBOX_MEM_MB` (default 4096)
- `AGENT_SANDBOX_CPU_S` (default 300)

On `TimeoutExpired`: kill child, capture stdout/stderr, return `{"returncode": -1}`.
The watchdog daemon (`scripts/task_watchdog.py`) provides a second layer for
tasks that escape the subprocess timeout.

## Integration: apply/verify/release lifecycle

Every sandbox backend implements three methods (mirrors `SandboxBackend` Protocol
from `general_ludd.security.sandboxes`):

1. **apply(spec, target) → SandboxHandle** — translate PermissionSpec into OS artifact
   (Landlock ruleset, bwrap argv, Seatbelt profile); `applied=False` on failure
2. **verify(spec, handle) → list[Finding]** — re-read OS state, surface divergences
3. **release(handle) → None** — teardown (no-op for Landlock/irreversible backends)

The handle's `applied` flag is the dispatch gate: `applied=True` → execute in
sandbox; `applied=False` → dispatch with loud "UNSANDBOXED" warning.

## Cross-Platform Summary

| Platform | Backend | Filesystem | Network | Irreversible? |
|----------|---------|------------|---------|---------------|
| Linux 5.13+ | Landlock | LSM ruleset | Port-level (6.7+) | Yes (fork to escape) |
| Linux (any) | bubblewrap | Namespace binds | Binary on/off | Process-scoped |
| Linux (any) | AppArmor | Profile | Profile rules | No (unloadable) |
| macOS < 15.4 | Seatbelt | sandbox-exec | Host:port | Process-scoped |
| FreeBSD | jail(8) | chroot + jail | ipfw/jail rules | Per-jail |
| Windows | AppContainer | SID caps | Firewall rules | Per-SID |

**macOS 15.4+ note:** `sandbox-exec` is removed. The macOS host daemon becomes a
thin scheduler that SSHs agent work into a Linux VM (Tart/Lima). The macOS host
MUST NOT run agent subprocesses directly — no enforcement layer remains.
