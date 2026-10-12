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

#### v0.1.2 native-build coordinate admission

The retained raw warnings from Molecule runs `37963738773` and `38189779992`
contain the same 866 transitive edges except for one importer coordinate:
`pyi_rth_pkgutil.py` lives beneath the native builder's six-character `mktemp`
suffix. The exact comparison reports one added and one removed edge. Both raw
reports remain unchanged; a separately derived canonical report supplies the
review receipt for digest
`fd140a96a39f531fc6bffa207ba54474e8d2fdece099bda0e163a47da2f160bd`.

The digest now canonicalizes only that suffix on the complete native-builder
runtime-hook path. It retains the Python minor version, hook filename, module,
import flags, and raw importer diagnostics. New dependencies, project-owned
imports, foreign roots, different hooks, and changed flags still reject the
candidate. The existing exact-delta review checker admits the canonical graph;
the change does not silently accept all warning digests or mutate the raw files.

[PyInstaller issue #1668](https://github.com/pyinstaller/pyinstaller/issues/1668),
opened in 2015 and reviewed on 2026-10-12, records unequal binaries from repeated
builds of one source tree. The official
[reproducible-build guidance](https://pyinstaller.org/en/stable/advanced-topics.html#creating-a-reproducible-build)
also distinguishes source inputs from build entropy. These findings motivate
measuring the exact difference; Gludd's two retained reports establish this
specific coordinate-only case. Stable warning admission is not a claim of
byte-identical executable output.

The repair is ZDD: it changes candidate validation only, allocates no daemon,
listener, worker, or durable application state, and keeps temporary build
directories isolated. Rollback restores the preceding auditor and policy while
the deployed artifact keeps serving; the preserved raw reports permit replay.

- The Linux executable is built in the digest-pinned Linux image and copied
  into the release output only after PyInstaller and package-update checks
  succeed.
- PyInstaller's warning file is retained and audited against an exact,
  version-pinned allowlist. Missing files, unknown syntax, stale entries, or
  actionable imports fail the build.
- The frozen core excludes the complete `ansible` module tree. PyInstaller may
  still report lazy Ansible imports from the core/EE adapter during analysis;
  only nine exact module/importer/flag edges are classified as
  `controller-runtime-boundary`, and each must remain below an active spec
  exclusion. No Ansible hidden import or payload is added to the executable.
- The aarch64 transitive warning graph is pinned to
  `fbfe1fa826751942260cf27b99aa32202ac603ab3730f7b7d2bda4b2d844c988`.
  This reviewed graph replaces the prior primary digest rather than becoming a
  permissive alternate; every unrelated edge, flag, importer, or digest change
  still fails closed.
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
runtime state. The warning audit runs before the binary leaves its temporary
container, so rollback discards the candidate and retained report without
touching the active artifact. The Lima build is single-container and namespaced;
classification adds no runtime import, process, or memory cost and keeps the
smaller core artifact separate from the independently bounded controller EE.

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

- Reviewed 2026-08-20: PyInstaller's
  [build-time warning guidance](https://pyinstaller.org/en/stable/when-things-go-wrong.html#build-time-messages)
  says missing-module reports can represent conditional or platform-specific
  imports and must be investigated rather than blindly bundled. The exact-edge
  controller classification follows that guidance while retaining a closed
  default for all other warnings.
- Reviewed 2026-08-20: Ansible Builder's
  [execution-environment documentation](https://docs.ansible.com/projects/builder/en/stable/)
  defines EEs as portable control nodes containing selected `ansible-core`,
  `ansible-runner`, collections, and Python/system dependencies. That upstream
  ownership model supports keeping those modules out of Gludd's frozen core.
- [PyInstaller practitioner issue #5891](https://github.com/pyinstaller/pyinstaller/issues/5891),
  opened in 2021 and reviewed 2026-08-20, shows a successful build accompanied
  by a large warning graph and user uncertainty about which missing modules are
  actionable. It motivates retaining exact importer/flag evidence and an
  architecture digest instead of suppressing warning classes wholesale.

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
