# Python 3.14 and Linux Artifact Gate Compatibility

## Hosted hook coordinate and architecture boundary (2026-10-27 UTC)

Exact diagnostic source9b01c6296f2a24d39a95e0c6194bd118ac8824f8 is observed in
Build40448994419, attempt1, workflow_dispatch. Linux job122011474722 completes
step7 Build executable successfully in78s, then fails step8 Audit Linux
PyInstaller warning graph in1s. The warning artifact upload succeeds separately.
Binary/daemon/tar/deb/RPM/artifact smokes are skipped and remain unproved.

### Complete retained inputs and actual cause

Root's native artifact owner downloads both complete raw reports. Its first
BEFORE lookup uses the Build artifact name and finds zero matches, failing in
1.092s. Selecting the actual molecule-linux-pyinstaller-warning-shard-1 name
then succeeds native0/11.076s; exact session47615 is drained and Root verifies
its native observation. The existing
auditor parses the exact AFTER artifact into961 distinct edges:22 reviewed
project imports,915 transitive edges,21 active spec exclusions and3 hook-provided
runtime edges. Its only failures are the unaccepted transitive digest and bounded
diagnostics. Static conditional/optional flags do not establish a runtime defect
or authorize ignoring an edge.

| Native retained input | Raw bytes | Raw SHA256 | Transitive SHA256 / count |
| --- | --- | --- | --- |
| Molecule40340580626, accepted BEFORE |60619 |c4993714ffff3670bb200f1006d09296bbac4df31256af3e05e6ea74739456fe |628561c8b618e40d24eb8a18bc06e7853b5b13a61996b3a47c78b88887932b95 /915 |
| Build40448994419, rejected AFTER |60618 |61bf17c1fb396b91d56e44e7029e24834967483a74deaee523fa080f401fad50 |f3ba54937639296ef135b1218f0c53a3cea6fc647f801b6927840eb26f4b1962 /915 |

The native scripts.compare_pyinstaller_warning_graphs producer reuses the active
spec and production project audit. Its native receipt records both raw artifact
hashes and normalized graph digests. The producer requires an accepted BEFORE
graph and retains the candidate project audit; its complete delta has exactly
one addition and one removal: missing pyimod02_importers, delayed, moves from
/tmp/gludd-linux-native-build.J1fxKQ/lib/python3.12/site-packages/PyInstaller/hooks/rthooks/pyi_rth_pkgutil.py
to /home/runner/work/gludd/gludd/.venv/lib/python3.12/site-packages/PyInstaller/hooks/rthooks/pyi_rth_pkgutil.py.
The module, flags, Python minor and hook are unchanged; no dependency/profile or
runtime-module semantic change is found in this complete delta.

The native producer's unchanged JSON output is copied byte-for-byte to
config/pyinstaller-warning-reviews/x86_64-f3ba54937639296ef135b1218f0c53a3cea6fc647f801b6927840eb26f4b1962.json.
The policy adds only that x86_64 alternate, preserves both primary architecture
digests and every existing alternate, and keeps the normalizer unchanged.
The existing review checker accepts its complete exact-delta receipt.
An initial private Make reader fails parsing before any analysis executes;
correcting only its command serialization allows the bounded native diagnosis.
That private input failure supplies no product or performance evidence.
An extra private Ruff-format check suggests changes only in original D blocks.
No such formatter is configured by the native hooks; those sources stay exact.
The required trailing-whitespace/EOF and Ruff-lint guards retain their authority.

### Separate architecture defect and native failing-first controls

Source inspection finds that _parse_allowlist validates all alternate-map keys
and digest lists but unions every architecture into its returned accepted set.
That defect is distinct from the hosted coordinate mismatch. The minimal repair
retains every original map validation and updates the returned set only when its
canonical map key equals the normalized requested architecture.

Four new native CLI controls cover both cross-architecture directions and the
amd64/arm64 aliases. Actual RED is4FAIL/8PASS in1.25s, native2/2.637s.
The unchanged companion cases cover same-architecture aliases, unknown digests,
and invalid foreign digest lists/keys. After the one-condition repair, all12
pass in1.04s, native0/2.475s, before the policy is changed.
Two further cases exercise the actual checked policy for this hosted digest:
x86_64 must accept it and aarch64 must refuse it. Before the policy addition,
they produce1FAIL/1PASS in0.66s, native2/2.047s. With only the reviewed x86_64
alternate and native receipt added, both pass in0.54s, native0/1.843s.
All original135 assertions and53 functions remain exact before the additive
controls; no ambient environment is rendered. Complete receiving remains separate.

The installed Python3.11.14 receipts under this checkout's .gate-logs/observed
retain these separate scopes:
v012-linux-warning-architecture-red/v012-linux-warning-architecture-red311-9b01c62-20261027.json;
v012-linux-warning-architecture-green/v012-linux-warning-architecture-green311-9b01c62-20261027.json;
v012-linux-warning-policy-red/v012-linux-warning-policy-red311-9b01c62-20261027.json;
v012-linux-warning-policy-green/v012-linux-warning-policy-green311-9b01c62-20261027.json.
Exact sessions26452,58336,33849 and77019 are drained and observed-status verifies their
actual terminal exits. The logical-Make reader first passes1/18.94s,
native0/20.216s at v012-linux-warning-ast/v012-linux-warning-ast311-9b01c62-20261027.json,
session40190 drained. Trace summaries retain complete pairs, original failures,
zero issues and zero unfinished cases; summary success is not test success.

### Practitioner context and remaining authority

Root's sole public research owner retrieves both sources with HTTP200 on
2026-10-27, native0/0.990s, using bounded curl without redirects or retries.
PyInstaller's [build-time guidance](https://pyinstaller.org/en/stable/when-things-go-wrong.html#build-time-messages)
describes conditional platform imports and ambiguity between package attributes
and submodules. It supports exact manual review while retaining the runtime
smokes; it does not authorize ignoring a warning class.

Firsthand user anarcat opens [issue #1668, builds not reproducible](https://github.com/pyinstaller/pyinstaller/issues/1668)
on2015-11-12; it closes on2015-11-20. The report demonstrates PYZ module ordering
changing with PyInstaller3.1dev/Python3.4. That historical reproducibility case
is not evidence for the current915-edge hook-coordinate cause or a current
upstream bug. The complete native comparison and owned REDs establish these
repository boundaries.

Root retains raw primary HTML and issue API JSON under
/tmp/gludd-v012-pyi-primary-20261027.html and
/tmp/gludd-v012-pyi-issue1668-20261027.json. Research receipt
v012-pyinstaller-warning-public-context/v012-pyinstaller-warning-public-context-20261027.json
belongs to Main's .gate-logs/observed, separate from this peer's pytest receipts.
Exact session94321 is drained and Root verifies its native command/status.
Research retrieval grants no warning-policy acceptance, gate or release proof.

The repair changes candidate admission only. It starts no daemon or listener,
replaces no serving artifact, and preserves ZDD, the actual spec, locked
Python3.12.14/PyInstaller6.22.3 composition,22 project edges,21 exclusions,
3 runtime edges, original fatal warnings, coverage profiles and85/75 LINE AND
BRANCH floors. Rollback restores the prior source/policy/receipt while the
existing deployment continues to serve.

### Complete thirteen-owner receiving and native coverage

The complete deduplicated union has13 owners: three coverage owners, nine
ordinary consumers and Root's separate release-readiness consumer. The final
eight source/configuration paths remain frozen throughout all receiving.
No production, test, policy, receipt or baseline edit follows either coverage run.

| Runtime | Three coverage owners | Nine ordinary owners | Root readiness owner | Distinct total |
| --- | --- | --- | --- | --- |
| Python3.11.14 |118 PASS,8.18s/native10.406s |352 PASS,95.95s/native97.530s |116 PASS,18.55s/native20.269s |586 PASS |
| Python3.12.15 |118 PASS,11.34s/native14.385s |352 PASS,161.80s/native163.411s |115 PASS/1 original skip,16.32s/native17.832s |585 PASS/1 original skip |

The three complete coverage owners are tests/unit/test_audit_pyinstaller_warnings.py,
tests/unit/test_check_pyinstaller_warning_reviews.py and
tests/unit/test_compare_pyinstaller_warning_graphs.py. The existing
config/coverage_pyinstaller_warning_audit.ini remains unchanged and measures all
three complete production files. Both native audits pass85aggregate/75each-file
for LINE AND BRANCH separately:

| Production file | Covered lines, BOTH | Covered branches, BOTH |
| --- | --- | --- |
| scripts/audit_pyinstaller_warnings.py |334/365,91.5% |153/184,83.2% |
| scripts/check_pyinstaller_warning_reviews.py |178/185,96.2% |84/88,95.5% |
| scripts/compare_pyinstaller_warning_graphs.py |91/92,98.9% |7/8,87.5% |
| Aggregate |603/642,93.9% |244/280,87.1% |

Native coverage receipts under .gate-logs/observed/coverage-files are
20261027T053338Z-36222-ddef8131.json and20261027T053511Z-44304-49718d0d.json.
Reports are .gate-logs/v012-linux-warning-coverage311.json and
.gate-logs/v012-linux-warning-coverage312.json; native audit reports are
.gate-logs/coverage-20261027T053348Z.json and
.gate-logs/coverage-20261027T053524Z.json. Exact sessions60940/94424 are drained.
Each trace has239events/118 complete pairs, zero issues, unfinished cases or
failure reports. One report reader initially refuses its absolute /private/tmp
input; the corrected checkout-relative path reads the same native report.
This reader refusal supplies no test or coverage failure.

The nine complete ordinary consumers under tests/unit are
test_ci_hosted_failure_tooling.py, test_molecule_binary_smoke_linux.py,
test_ci_await_exact.py, test_file_line_remediation_plan.py,
test_oversized_document_splits.py, test_markdown_docs_deep.py,
test_guardrails.py, test_secrets_baseline_compaction.py and
test_audit_public_secrets.py. Both352-case receipts are under
.gate-logs/observed/v012-linux-warning-consumers/:
v012-linux-warning-consumers311-9b01c62-20261027.json and
v012-linux-warning-consumers312-9b01c62-20261027.json.
Sessions48408/86525 are drained; observed-status verifies inactive/passed/exit0.
Each trace has707events/352 complete pairs and zero issues, unfinished cases
or failure reports. Native600total/180quiet, original per-case bounds,
fatal warnings, installed runtimes and literal ID= remain.

Root separately owns the complete tests/unit/test_release_readiness.py on both
runtimes. Its receipts are under
.gate-logs/observed/v012-linux-readiness-receiving/:
v012-linux-readiness-receiving311-9b01c62-20261027.json and
v012-linux-readiness-receiving312-9b01c62-20261027.json.
Root drains and verifies both; each trace has235events/116 complete pairs and
zero issues, unfinished cases or failure reports. The original invoking-worktree
native mode0 control executes on3.11; its existing3.11 version guard alone
skips it on3.12. This is whole-consumer acceptance, not a live release-readiness
success or an artifact/deployment verdict.

Native literal-roster inspection confirms both baseline consumers already
belong to combined329.txt and neither belongs to focus128.txt. The baseline
follow-up adds no changed test owner or focus entry; existing topology and
input caps remain.
Coverage and ordinary receiving run sequentially within one reserved width1;
native child bounds remain. Root's readiness and publisher cohorts have separate
reservations. No trace duration is a CPU measurement or performance claim.

### Native staged admission and public baseline review

The first seven-path staged guard fails native2/3.874s on six locations containing
known public graph/raw hashes: policy line19, new receipt lines4/5/10/11 and
audit unit line1118. Its failed receipt remains under
.gate-logs/observed/v012-linux-warning-staged-content/:
v012-linux-warning-staged-content311-9b01c62-20261027.json.

Root uses the existing native baseline-refresh owner with all eight scanner
workers reserved exclusively. It passes native0/51.491s; receipt
.gate-logs/observed/v012-linux-secret-baseline-refresh/v012-linux-secret-baseline-refresh311-9b01c62-20261027.json.
The fixed native review_findings API reviews only the six exact public identities
with is_secret=False/is_verified=False. This direct API call has no run-watched
receipt. The inventory adds exactly6/removes0, retains all1357 prior findings'
semantic metadata/labels and changes38 line coordinates. Plugin/filter/policy
settings stay unchanged except the supported owned temporary-baseline context.
No broad hash rule, pragma, unknown-finding acceptance or scanner waiver is added.

The resulting .secrets.baseline is the eighth source path. Independent source
review approves the seven original deltas and the separate baseline supplement,
including exact public values and native finding identities. Root's final
eight-path staged guard passes native0/8.517s at
.gate-logs/observed/v012-linux-warning-staged-content/v012-linux-warning-staged-content311-9b01c62-baseline-reviewed-20261027.json.
Exact sessions20643/81994 are drained; Root verifies their actual failed/passed
observations. Native baseline and task-registration checks also pass.

The seven-path review is retained at
/tmp/gludd-v012-linux-warning-source-review-20261027.md and the independent
baseline supplement at /tmp/gludd-v012-linux-secrets-baseline-source-supplement-20261027.md.
Those approvals and the eight-path guard precede this terminal-receipt-only
prose update. Root must bind the bounded final prose review, Markdown lint,
restaged content guard and normal collection/commit before source delivery.

This three-file profile does not certify global/E2E coverage or the canonical
21600-second full gate. Fresh exact-SHA hosted Linux executable/daemon/package
smokes, reviewed integration/promotion, ZDD deployment and signed stable release
proof remain required. The retained-report audit and green test owners supply
none of those distinct outcomes.

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
