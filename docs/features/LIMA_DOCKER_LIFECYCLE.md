# Namespaced Lima Docker lifecycle

Gludd owns an explicit `gludd-*` Lima namespace for local Linux artifact and
Molecule work. Startup and shutdown operate on a named, already-provisioned
instance; neither path creates, deletes, factory-resets, or force-stops a VM.

## Operator contract

Validate the shutdown inputs without changing runtime state:

```text
make lima-docker-stop LIMA_INSTANCE=gludd-docker LIMA_DOCKER_STOP_KILL_AFTER_SECS=10 LIMA_DOCKER_STOP_TIMEOUT_SECS=200 LIMA_DOCKER_VALIDATE_ONLY=1
```

After every owner of the engine has released its lease, use the same command
with `LIMA_DOCKER_VALIDATE_ONLY=0`. The target accepts only a safe `gludd-*`
name that resolves to one existing Lima instance. A stopped instance succeeds
idempotently. A running instance receives Lima's graceful stop, streamed in the
foreground under a shell-owned deadline. The target tracks the exact `limactl`
PID, sends TERM at the configured deadline, waits the configured grace period,
then sends KILL only to that still-live owned process. It always waits to reap
the child. Completion is accepted only after `limactl list` reports that exact
instance as `Stopped`.

Unexpected, missing, broad, or non-Gludd names fail closed. `Broken` and other
unknown states also fail closed for manual diagnosis. The target never adds
`--force`, never removes containers, and never deletes VM disks or instance
configuration.

## ZDD, rollback, and resources

Shutdown happens only after the workload's success/failure cleanup has run, so
the VM remains available during zero-downtime handoff and diagnostic capture.
The 200-second outer deadline bounds Lima's documented 180-second graceful
stop path and adds a ten-second TERM escalation window for the CLI process.
The owner loop uses only macOS/POSIX shell facilities (`kill`, `wait`, and a
one-second bounded sleep), creates no files, and installs a signal trap that
performs the same TERM-to-KILL cleanup on cancellation. Timeout or an unproven
final state returns nonzero and retains all instance data.

Rollback is the paired `lima-docker-start` target against the same explicit
instance. It starts only an existing VM and proves Docker engine readiness;
there is no recreate or data migration step. Operators should confirm no other
workstream owns the VM before either lifecycle transition.

## Upstream and practitioner evidence

Evidence reviewed 2026-08-20 and 2026-08-21:

- Lima's [official `limactl stop` reference](https://lima-vm.io/docs/reference/limactl_stop/)
  defines graceful stop and a separate force option, but no caller-selected
  timeout. Gludd therefore invokes the graceful form and supplies an outer
  bound while retaining post-stop state proof.
- A [March 2009 macOS timeout practitioner thread](https://stackoverflow.com/questions/601543/command-line-command-to-auto-kill-a-command-after-a-certain-amount-of-time)
  records that GNU `timeout` is not part of the base macOS toolset and that the
  common workaround requires installing Homebrew coreutils as `gtimeout`. An
  [April 4, 2023 portability discussion](https://www.reddit.com/r/bash/comments/12bf462/timeout_vs_gtimeout_macos/)
  records the same Linux/macOS command split. Gludd therefore owns the bounded
  child lifecycle instead of making an unrelated package manager dependency a
  prerequisite for shutting down its VM.
- A [July 2023 Lima practitioner discussion](https://github.com/lima-vm/lima/discussions/1666)
  records repeated stuck VMs and a graceful stop failing at 180 seconds; a
  November 2023 follow-up reproduces a VZ stop timeout. This supports visible
  progress, an explicit bound, and post-stop state proof.
- Lima [issue 5334, opened 2026-07-25](https://github.com/lima-vm/lima/issues/5334)
  requests a configurable graceful-stop timeout, confirming that current Lima
  does not expose this contract directly.
- Lima [issue 5087, opened 2026-06-06](https://github.com/lima-vm/lima/issues/5087)
  documents an orphaned VZ driver after shutdown and a manual force-stop
  recovery. Gludd deliberately does not automate that destructive recovery;
  ambiguous or broken state remains a fail-closed operator decision.
