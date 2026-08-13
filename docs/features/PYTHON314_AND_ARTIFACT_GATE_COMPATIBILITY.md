# Python 3.14 and Linux Artifact Gate Compatibility

Status: beta4 release contract

## Purpose

The beta4 gate must distinguish intentional runtime changes from product defects
and must exercise the same Linux artifact that is eligible for release. This
specification covers Python 3.14 event-loop behavior, deterministic async
resource ownership, fail-closed password-hash errors, and the Linux
PyInstaller/Molecule acceptance path.

## Behavioral contract

### Python 3.14 async lifecycle

- Code and tests must not rely on `asyncio.get_event_loop()` implicitly
  creating a loop when no loop is running.
- `asyncio.create_task()` is valid only inside a running loop. When a caller
  intentionally owns a non-running loop, it must call `loop.create_task()`,
  run or cancel the task, and close the loop.
- Every created coroutine, task, SQLAlchemy engine, and database session has an
  explicit owner and deterministic close/dispose behavior. Warning suppression
  is not an acceptable substitute.
- AST checks dedent inspected source before parsing so nested async callables
  are evaluated without changing their semantics.

### Fail-closed public errors and audit values

- Invalid or corrupt Argon2 encodings are normalized to the project's bounded
  `Argon2Error`; a password mismatch remains a normal false result.
- Audit detail mappings are copied at the event boundary so later caller
  mutation cannot rewrite recorded evidence.
- Async repository tests model the real SQLAlchemy API: `AsyncSession.add()`
  is synchronous, while database execution and commit operations are awaited.

### Linux release artifact

- The Linux executable is built in the digest-pinned Linux image and copied
  into the release output only after PyInstaller and package-update checks
  succeed.
- PyInstaller's warning file is retained and audited against an exact,
  version-pinned allowlist. Missing files, unknown syntax, stale entries, or
  actionable imports fail the build.
- The `binary_smoke_linux` Molecule scenario uses Ubuntu 24.04 through an
  explicitly installed Docker driver and boots Python before Ansible modules.
- The smoke run exercises version/help/project-path resolution, authenticated
  daemon startup, `/healthz`, job submission, invalid CLI input, and occupied
  port behavior.
- Molecule dependency state and Docker configuration live under a
  scenario-scoped temporary directory. Tracked scenarios are preserved, and
  cleanup destroys runtime resources without invoking Molecule's destructive
  reset path.
- Lima and Podman machines use the project namespaces `gludd-docker` and
  `gludd`. Their diagnostic and pull targets provide non-mutating
  validate-only examples in the Make target contract.
- Legacy `podman-machine-default` cleanup is opt-in, refuses any other name
  or a running VM, and emits a bounded heartbeat. Disk protection measures the
  checkout volume's absolute free space and fails below 8 GiB without a bypass,
  while leaving other projects' namespaced data untouched.

## Zero-downtime deployment boundary

Artifact construction and smoke validation happen before promotion. A failed
build, warning audit, package-update simulation, or Linux smoke test leaves the
currently deployed release and tag untouched. The release workflow may promote
only the already-validated artifact; cleanup is limited to project-namespaced
runtime state.

## Verification requirements

- The exact gate regressions and their adjacent suites must pass without
  warnings.
- `make check-make-target-contract` and
  `make check-duplicate-targets` must pass after target changes.
- Aggregate coverage remains at least 85 percent, with every measured source
  file at least 75 percent.
- The authoritative release gate, remote pipeline, release completeness check,
  and deployed smoke check must all be green before beta4 is called deployed.

## Practitioner evidence

These contracts address long-lived reports from real users:

- [CPython issue #126353](https://github.com/python/cpython/issues/126353)
  records the Python 3.14 removal of implicit event-loop creation and community
  concern about its broad impact. A scan cited in the discussion found 1,904
  uses across 391 popular packages, supporting an explicit-loop regression
  contract rather than assuming the old behavior.
- [molecule-plugins discussion #135](https://github.com/ansible-community/molecule-plugins/discussions/135)
  reports a working Docker scenario changing overnight to "docker driver is
  not installed" after dependency changes. That history motivates declaring
  the driver in both development dependency sets and locking it.
- [PyInstaller issue #3452](https://github.com/pyinstaller/pyinstaller/issues/3452)
  spans years of reports where executables fail with missing modules or
  libraries despite source-environment success. It supports preserving and
  auditing the warning graph and running the built binary on its target OS
  before release.
- [Podman issue #15742](https://github.com/containers/podman/issues/15742)
  documents a long-lived default-machine state where removed connections and
  failed cleanup left a VM blocking reinitialization. It supports exact-name,
  stopped-state validation and observable bounded cleanup during migration to
  project-owned machine names.
- [Starlette discussion #2067](https://github.com/Kludex/starlette/discussions/2067)
  identifies failed teardown and cross-loop shared state as recurring lifespan
  footguns, including under TestClient. It supports assigning the model gateway
  and its injected SQLite-backed response cache to one application lifespan,
  closing them deterministically at shutdown, and testing explicit ownership.
- [CPython issue #105539](https://github.com/python/cpython/issues/105539)
  led Python 3.13 to emit ResourceWarning when SQLite connections are not closed
  explicitly. It supports treating the warning as a lifecycle defect and making
  gateway close idempotent instead of relying on garbage collection.
- [CPython issue #105288](https://github.com/python/cpython/issues/105288)
  reproduces an asyncio subprocess cleanup hang caused by exit waiters not being
  awakened after process exit. It supports a bounded terminate-then-kill path
  with one main-thread wait owner and an observable watchdog, rather than
  re-entrant signal-handler waits or abandoned child processes.
