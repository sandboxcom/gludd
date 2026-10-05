# OS-Level Sandbox Backends

This document describes the per-OS sandboxing backends that translate a
[`PermissionSpec`](../../src/general_ludd/security/permissions.py) into an
OS-native enforcement artifact and verify that the kernel is actually
enforcing what the spec claims.

## Architecture

```text
                    ┌────────────────────────┐
                    │   PermissionSpec       │
                    │   (capabilities,       │
                    │    denied, agent_id)   │
                    └──────────┬─────────────┘
                               │
                               ▼
            ┌──────────────────────────────────┐
            │  detect.auto()                   │
            │  picks backend by host OS +      │
            │  available kernel feature        │
            └────────────────┬─────────────────┘
                             │
       ┌─────────────────────┼─────────────────────┐
       ▼                     ▼                     ▼
 ┌───────────┐         ┌───────────┐         ┌───────────┐
 │  apply    │ ──────▶ │  verify   │ ──────▶ │  release  │
 │ (render + │         │ (re-read  │         │ (unload + │
 │  load)    │         │  OS state │         │  cleanup) │
 └───────────┘         └───────────┘         └───────────┘
       │                     │
       ▼                     ▼
  SandboxHandle         list[Finding]
  (opaque token)        (ok / warn / fail)
```

The trust anchor is `verify()`. **Applying a sandbox without verifying that
the kernel is enforcing it is theater** — every `apply()` MUST be followed by
`verify()`, and every `Finding(severity="fail"|"warn")` MUST be logged loudly.

### Pipeline

1. **`apply(spec, target)`** translates the spec into an OS-native artifact
   (AppArmor profile, SELinux Type-Enforcement module, FreeBSD jail + pf
   anchor, macOS `sandbox.d` profile, or Windows AppContainer SID + ACLs),
   writes it to the canonical location, and invokes the OS loader. It returns
   an opaque [`SandboxHandle`](../../src/general_ludd/security/sandboxes/__init__.py).
2. **`verify(spec, handle)`** re-reads OS state and reports a list of
   [`Finding`](../../src/general_ludd/security/sandboxes/__init__.py) objects
   describing any divergence between the spec and what the kernel is actually
   enforcing.
3. **`release(handle)`** tears down the sandbox (unloads the profile, stops
   the jail, revokes the SID).

### Fail-open contract

Every backend FAILS OPEN: if loading the sandbox raises (missing tool,
permission denied, unsupported kernel), the backend logs loudly and returns a
`SandboxHandle` whose `applied` flag is `False`. The daemon continues to
dispatch the agent with a "no sandbox" warning rather than wedging. The
caller is responsible for surfacing this in the audit log + dispatch metrics.

This fail-open is a deliberate choice: gludd's value depends on the daemon
**continuing to run**. A sandbox bug that wedges the event loop is worse than
a sandbox that fails to enforce (because `verify()` will surface the
non-enforcement loudly, but a wedged daemon is silent).

## Per-OS backends

### Linux — AppArmor (`linux_apparmor.py`)

**Prerequisites:**
- `apparmor` kernel module loaded (`/sys/module/apparmor` present)
- `apparmor_parser`, `aa-status` userland tools installed (`apparmor-utils`)
- `/etc/apparmor.d/` writable by the daemon (run as root, or grant the
  daemon's service account write access to that directory)

**Apply:** writes a profile to `/etc/apparmor.d/gludd-<agent_id>` with `deny`
rules derived from `spec.denied` and `allow` rules from `spec.capabilities`,
then loads it via `apparmor_parser -r`. File-path constraints become
`path.prefix=` rules; net constraints become
`network inet stream, peer=<host>` rules.

**Inspect the active sandbox:**
```bash
aa-status --json | jq '.profiles'
cat /etc/apparmor.d/gludd-<agent_id>
aa-logprof
```

**Debug a denied operation:**
```bash
# tail the AppArmor denial log
dmesg | grep apparmor
tail -f /var/log/audit/audit.log | grep apparmor
```
Add the missing capability to the spec OR a `deny` rule to the spec if the
denial was correct.

### Linux — SELinux (`linux_selinux.py`)

**Prerequisites:**
- SELinux enabled (`selinuxenabled` exits 0; `sestatus` shows `enabled`)
- `checkmodule`, `semodule_package`, `semodule` userland tools
  (`policycoreutils`, `checkpolicy`, `selinux-policy-devel`)
- `semanage` for file-context verification

**Apply:** generates a Type-Enforcement policy (`gludd_<agent_id>.te`) +
file-context labels (`gludd_<agent_id>.fc`), compiles via
`checkmodule + semodule_package`, and installs via `semodule -i`.

**Inspect the active sandbox:**
```bash
semodule -l | grep gludd_
semanage fcontext -l | grep gludd_
ps -eZ | grep gludd_
```

**Debug a denied operation:**
```bash
ausearch -m avc -ts recent | grep gludd_
audit2allow -a
```

### FreeBSD — jail (`freebsd_jail.py`)

**Prerequisites:**
- `jail(8)` binary (stock FreeBSD install)
- `pfctl(8)` for egress anchors (if net capabilities are used)
- Root privileges (jail creation requires `CAP_SYS_ADMIN` equivalent)

**Apply:** invokes
`jail -c path=<chroot> host.hostname=gludd-<id> ip4=inherit devfs_ruleset=10`
with a `devfs.rules` rule limiting device access, plus a `pf.conf` anchor
limiting egress to granted host/port pairs.

File constraints select the jail chroot path. Net constraints become
`pass out quick proto tcp to "<host>" port <port>` rules in the jail's
`pf` anchor, followed by a `block out quick` default.

**Inspect the active sandbox:**
```bash
jls -n
pfctl -a gludd-<id> -s rules
cat /etc/devfs.rules
```

**Debug a denied operation:**
```bash
tcpdump -i pflog0
pfctl -a gludd-<id> -s info
```

### macOS — Seatbelt / `sandbox-exec` (`macos_seatbelt.py`) — ⚠️ DEPRECATED

**DEPRECATED since macOS 15.4.** Apple removed `sandbox-exec` from shipping
macOS in 15.4+ with no replacement for arbitrary sandbox profiles. The only
third-party option is the Endpoint Security entitlement, which Apple gates
to AV/EDR vendors (not available to gludd).

On macOS 15.4+:
- `detect.auto()` returns `None` and logs a loud warning.
- `SeatbeltBackend.apply()` returns `SandboxHandle(applied=False)` with
  `extra={"reason": "sandbox-exec deprecated on macOS 15.4+; no enforcement"}`.
- The daemon dispatches UNSANDBOXED with an audit-tagged warning.

On macOS < 15.4:
- `sandbox-exec` path works but logs a deprecation warning.
- Operators should plan migration BEFORE the 15.4 cutoff.

**Migration path for 15.4+:**
Operators MUST run untrusted agent work in a Linux VM. Supported runners:
- **Tart** (https://github.com/cirruslabs/tart) — Apple Silicon native
  (Virtualization.framework); fastest cold-start.
- **Lima** (https://github.com/lima-vm/lima) — cross-arch (qemu or vz);
  good for Intel Macs.
- **UTM** — GUI-first alternative.

The macOS host daemon becomes a thin scheduler that SSHes agent work into
the Linux guest where Landlock + bubblewrap enforce the spec.

**Prerequisites (macOS < 15.4 only):**
- `/usr/bin/sandbox-exec` on PATH

**Apply:** writes a `sandbox.d` profile under `/tmp/gludd-seatbelt/` with
- `(allow file-read* (subpath "<path>"))` for file-read caps
- `(allow file-write* (subpath "<path>"))` for file-write caps
- `(allow network-outbound (to (remote tcp "<host>:<port>")))` for net caps

then dry-runs `sandbox-exec -f <profile> /bin/true` to confirm the profile
compiles. The target is launched by the caller via
`sandbox-exec -f <profile> -- <cmd>`.

**Inspect the active sandbox:**
```bash
sandbox-exec -f /tmp/gludd-seatbelt/gludd-<id>.sb /bin/sh
# (no in-process introspection API — Seatbelt profiles are opaque post-launch)
```

**Debug a denied operation:**
```bash
log stream --predicate 'process == "<your-process>"' --info
```

### Windows — AppContainer + RestrictedToken (`windows_appcontainer.py`)

**Prerequisites:**
- Windows 8+ (AppContainer API)
- `pywin32` (`pip install pywin32`)
- `icacls.exe`, `netsh.exe` userland tools (stock Windows install)

**Apply:**
1. Creates an AppContainer SID via `CreateAppContainerProfile`.
2. Sets capability-based access on the target process token via
   `AdjustTokenGroups`.
3. Applies file ACLs via `icacls <dir> /inheritance:r /grant:r <sid>:(OI)(CI)F
   /deny Everyone:(OI)(CI)F` — only the AppContainer SID has access.
4. Installs Windows Firewall rules scoped to the AppContainer SID:
   - one `allow` rule per granted (host, port) pair
   - a final `block` rule denying all other outbound TCP.

**Inspect the active sandbox:**
```powershell
Get-AppContainerProfile
icacls <dir>
netsh advfirewall firewall show rule name="gludd-<id>-*"
```

**Debug a denied operation:**
```powershell
Get-EventLog -LogName Security -Newest 50 | Where-Object {$_.Message -like "*gludd*"}
```

## The "verify" loop is the trust anchor

`SandboxBackend.verify()` is the codified "does the system actually enforce
what the spec claims" check. **A spec → apply → assume-enforced pipeline is
theater.** The verify loop:

1. Re-reads the OS-level state (`aa-status --json`, `semodule -l`, `jls`,
   `sandbox-exec -f <profile> /bin/true`, `netsh ... show rule`).
2. Compares against the spec: every `deny` rule MUST be present in the loaded
   profile; every granted host/port MUST appear in the firewall/pf rules.
3. Emits a `Finding` for each divergence, tagged with `severity` (`ok`,
   `warn`, `fail`) and the originating `Capability`.

The daemon's dispatch path runs `verify()` after `apply()` and before the
agent's first tool call. Any `fail` finding logs loudly + the dispatch
metrics tag the agent as "unsandboxed" for the run.

## Auto-detection (`detect.py`)

`detect.auto()` picks the best backend for the host:

| Platform | Selection order |
|---|---|
| **Linux** | **Landlock** (pylandlock importable + kernel ABI > 0) → **bubblewrap** (`bwrap` on PATH) → **AppArmor** (defense-in-depth) → **SELinux** (defense-in-depth) → `None` (warn) |
| FreeBSD | `jail` present → JailBackend → `None` (warn) |
| macOS | `sandbox-exec` present AND macOS < 15.4 → SeatbeltBackend → `None` (loud warn about 15.4+ removal) |
| Windows | `pywin32` + AppContainer API → AppContainerBackend → `None` (warn) |

### Recommendation matrix (from permission/sandbox research survey)

| Use case | Recommended backend | Why |
|---|---|---|
| Per-agent sandbox (Linux) | **Landlock** | Per-process, unprivileged, fine-grained FS+net |
| Per-agent sandbox (Linux, no pylandlock) | **bubblewrap** | Namespace jail, unprivileged, no LSM needed |
| Host hardening (Linux) | AppArmor / SELinux | System-wide policy; complements per-agent sandbox |
| Per-agent sandbox (macOS < 15.4) | seatbelt (`sandbox-exec`) | Deprecated; plan migration |
| Per-agent sandbox (macOS 15.4+) | **Linux VM (Tart/Lima)** | No host-side option remains |
| Per-agent sandbox (FreeBSD) | `jail` + `pf` anchor | Native, root-required |
| Per-agent sandbox (Windows) | AppContainer + firewall | Native API |

When `auto()` returns `None`, the daemon dispatches the agent UNSANDBOXED and
logs a `no-sandbox-backend` warning. Deployments that require sandbox
enforcement MUST monitor for this warning.

## Wiring into agent dispatch

The event loop calls `apply()` before an agent's first tool call and
`release()` after it completes. The wrapper is wrapped in `try/except` so a
sandbox failure does not wedge the daemon — the agent is dispatched with a
"no sandbox" warning instead.

See [`event_loop/loop.py`](../../src/general_ludd/event_loop/loop.py)
`_dispatch_execute_job` for the wiring site.
