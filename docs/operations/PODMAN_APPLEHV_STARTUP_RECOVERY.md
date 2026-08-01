# Podman 5.8 AppleHV startup recovery

Last researched: 2026-08-01

This runbook covers macOS hosts where a freshly initialized AppleHV machine
times out during `podman machine start` and remains `Currently starting`. Its
first objective is to preserve evidence and every unrelated Podman machine.
Deleting and recreating the affected machine is a last resort, not diagnosis.

The Podman commands below name the upstream primitives that an operator needs.
Repository agents may execute them only through explicit `make` targets, in
accordance with this project's Make-only contract.

## What the status does and does not prove

`Currently starting` is a persisted lifecycle state, not a root-cause message.
Upstream issue
[#16945](https://github.com/podman-container-tools/podman/issues/16945)
shows the state surviving a host reboot while no VM is running because the
machine JSON still contains `"Starting": true`. The issue used the older QEMU
provider, but an AppleHV user later reported the same field under
`~/.config/containers/podman/machine/applehv/`.

There is also a version-specific reason not to wrap Podman 5.8 startup in a
hard timeout. Podman's
[6.1.0-RC1 release notes](https://github.com/podman-container-tools/podman/releases/tag/v6.1.0-rc1)
say that release fixes macOS machines being left inconsistent when
`podman machine start` is interrupted by a signal. Therefore, if a test runner
kills the 5.8 command at its deadline, the timeout can create the persistent
state that obscures the original failure. This is an evidence-backed working
hypothesis, not proof that every stuck machine has that cause.

## Safety invariants

- Always require and print the exact `MACHINE` name before an action.
- Snapshot all machines and connections before and after recovery.
- Stream debug output and let `machine start` succeed or fail naturally. A
  supervisor may emit heartbeats and alert on elapsed time, but must not send a
  timeout signal to Podman 5.8.
- Never use `podman machine reset`: the official documentation says it removes
  **all** machines, configuration, disk images, and cached images.
- Never omit the name from `machine stop` or `machine rm`, and never use an
  all-machines flag or a wildcard.
- Never use `killall` for `podman`, `vfkit`, or `gvproxy`, and never delete an
  entire Podman configuration or data directory. Those actions can affect
  unrelated machines.
- Do not change `XDG_CONFIG_HOME` during diagnosis. The 5.8 machine-list
  documentation warns that doing so while machines run can cause unexpected
  behavior.
- Do not change the default connection until a replacement machine has passed
  an explicit health check.

## Capture evidence before recovery

Set a literal machine name; do not rely on `podman-machine-default` being the
intended target.

```console
$ MACHINE=gludd-e2e
$ podman version
$ podman machine list --all-providers --format json
$ podman machine info --format json
$ podman machine inspect "$MACHINE"
$ podman system connection list
```

Record the macOS version and build, CPU architecture, installation source, free
disk space, and the resolved paths and versions of `podman`, `vfkit`, and
`gvproxy`. Record only process rows that match the exact machine name, its
inspect-derived socket, or its SSH port. A process name alone is not enough to
justify stopping it.

Run one observed start without `timeout`, `--quiet`, stderr suppression, or
`|| true`. Capture stdout and stderr continuously with timestamps while a
separate observer emits a heartbeat.

```console
$ podman --log-level=debug machine start "$MACHINE"
```

The repository's existing `make podman-up` is not an incident diagnostic: it
currently suppresses `machine init` and `machine start` stderr and converts
their failures to success. `make podman-resize` removes
`podman-machine-default`, so it is also outside this recovery path.

## Classify the failing phase

| Last reliable evidence | Likely fault domain | Next safe check |
|---|---|---|
| Inspect/list says starting, but there is no machine-scoped `vfkit`/`gvproxy` process, SSH listener, or API socket after the original command exited | Stale lifecycle metadata | Try a named stop once, re-inspect, then use the canary test below |
| Debug output reports `vfkit exited`, a missing helper, a `dyld` library error, or a helper path from a different installation | Host installation/helper mismatch | Compare architecture, package source, and all helper paths; do not delete any VM |
| `vfkit` remains alive but the inspect-derived SSH port never accepts a connection | Guest boot, ignition, mount, resource, or Virtualization.framework failure | Preserve full debug output and VM-specific logs; test a minimal named canary |
| `gvproxy` fails or the inspect-derived API socket never appears | Host-side user-mode networking/API forwarding | Validate the exact helper path and socket owner; avoid broad process kills |
| Machine SSH works but `podman info` does not | System connection or API-forwarding problem, not VM boot | Compare the connection URI, identity, and default marker with `machine inspect` |

Podman 5.8 documents that non-WSL machine providers depend on `gvproxy`, so a
network-helper failure belongs to the host/provider path even when the VM image
itself is fresh.

## Non-destructive recovery ladder

Stop as soon as a step produces a healthy, explicitly addressed machine.

1. **Try the supported, machine-scoped stop once.** Run
   `podman machine stop "$MACHINE"`, then inspect it again. If stop says no VM
   is running or cannot clear the state, preserve the output; do not compensate
   with a broad kill.

2. **Eliminate installation ambiguity without touching machines.** Podman's
   macOS installation page recommends its signed installer and explicitly does
   not recommend Homebrew because Podman cannot guarantee that package's
   stability. Inventory mixed `/opt/podman`, `/opt/homebrew`, `/usr/local`, and
   Podman Desktop helper paths. Repair only the Podman installation after the
   inventory is saved; do not uninstall Homebrew itself.

3. **Use a separately named minimal canary.** This is allowed only when no other
   Podman VM is actually running, because the 5.8 start documentation permits
   only one active managed VM. Confirm that `machine info` reports `applehv`,
   then initialize a unique name using the same provider and small resources:

   ```console
   $ podman machine init --cpus 1 --memory 2048 --disk-size 20 gludd-applehv-canary-20260801
   $ podman --log-level=debug machine start gludd-applehv-canary-20260801
   ```

   Podman 5.8 does not have the newer `machine init --provider` switch; it uses
   the configured provider. Recheck all system connections afterward and do
   not make the canary default implicitly. If Podman refuses to start the
   canary because it still considers the affected machine active or starting,
   preserve that output and stop this step. Do not force-clear global state.

4. **Interpret the canary, rather than immediately removing it.** If it fails at
   the same phase, the fault is host/provider-wide; deleting the original VM
   cannot help. If it starts, the fault is isolated to the original machine's
   metadata, disk, ignition, or configuration. Keep the original for forensics
   until its data-retention decision is explicit.

5. **Upgrade only with a recorded reason.** Check the latest stable patch in
   the 5.8 line first. As of the research date, the signal-interruption fix is
   documented in 6.1.0-RC1, not a stable 6.1 release, so do not silently put a
   release candidate into production. After an approved upgrade, repeat the
   observed start without a hard timeout.

6. **Remove only the affected machine, with confirmation, as the final step.**
   The official `machine rm` documentation says removal deletes that VM's
   generated connection, ignition file, and image. Snapshot the all-provider
   list and connections, retain needed evidence or data, and use the literal
   name without `--force` so Podman displays the exact files before approval:

   ```console
   $ podman machine rm "$MACHINE"
   ```

   Reinitialize the same explicit name only after confirming that every
   unrelated machine and connection is unchanged.

After the incident artifact is accepted, stop and remove the exact canary name
with normal confirmation. Verify the all-provider and connection snapshots
again so the diagnostic VM does not consume disk or remain as an accidental
default.

## Community workarounds are evidence, not automation

An exact-symptom
[AppleHV user write-up](https://camillehdl.dev/podman-machine-stuck-currently-starting/)
and a later
[GitHub Gist](https://gist.github.com/daubac402/5db800f6af56b7fd9b204a162d4b244b)
recommend manually changing `"Starting": true` to `false` in the machine JSON.
That explains how the status can persist, but it does not repair the vfkit,
guest boot, gvproxy, or API-forwarding failure that interrupted startup. Gludd
must never edit Podman's private JSON automatically. An operator considering it
must first prove no process owns the machine, preserve an exact backup, and
accept that the edit is unsupported state surgery.

A
[Stack Overflow AppleHV/vfkit thread](https://stackoverflow.com/questions/79488189/error-starting-podman-machine-error-vfkit-exited-unexpectedly-with-exit-code-1)
reports that reinstalling and recreating the machine did not help, while
removing a conflicting Homebrew installation did. This is useful evidence for
checking mixed helper paths. It is not justification to uninstall all Homebrew
packages; the narrower action is to establish one coherent Podman distribution
and preserve unrelated software and VMs.

## Gludd acceptance evidence

A recovery is complete only when the artifact contains all of the following:

- the before/after `machine list --all-providers --format json` snapshots;
- before/after system connection snapshots;
- complete, streamed debug output from the affected machine and canary;
- explicit proof that the healthy target's SSH and API connection respond;
- an unchanged set and state of unrelated machines;
- no `machine reset`, broad process kill, directory deletion, or unscoped
  machine removal in the event log.

Future Gludd automation should require `MACHINE` and an artifact directory,
stream every phase, separate elapsed-time alerts from cancellation, redact
secrets, and compare before/after snapshots mechanically. A failed startup must
remain failed; stderr and exit status must never be suppressed.

## Primary sources

- [Podman 5.8 machine list](https://docs.podman.io/en/v5.8.0/markdown/podman-machine-list.1.html)
- [Podman 5.8 machine init](https://docs.podman.io/en/v5.8.0/markdown/podman-machine-init.1.html)
- [Podman machine inspect](https://docs.podman.io/en/stable/markdown/podman-machine-inspect.1.html)
- [Podman machine start](https://docs.podman.io/en/v5.8.2/markdown/podman-machine-start.1.html)
- [Podman machine removal](https://docs.podman.io/en/v5.3.2/markdown/podman-machine-rm.1.html)
- [Podman machine reset warning](https://docs.podman.io/en/stable/markdown/podman-machine-reset.1.html)
- [Official macOS installation guidance](https://podman.io/docs/installation#macos)
- [Upstream stale-starting bug #16945](https://github.com/podman-container-tools/podman/issues/16945)
- [Upstream 6.1.0-RC1 release notes](https://github.com/podman-container-tools/podman/releases/tag/v6.1.0-rc1)
