# Make helper bootstrap diagnostics

## Verified native worktree creation (S98.4,2026-10-23)

The three public creation targets previously continued after native Git add or
registration failure and printed ready with exit 0. The development variant did
not register its owner. The existing Make recipes now share one creation body:
pin the requested base and intended branch source, distinguish branch absence
from lookup failure, execute one native add, verify physical checkout/repository/
branch/SHA, then run the existing atomic registration owner before readiness.

Plain agent-worktree starts at HEAD and deliberately resumes a preexisting
branch at its pinned source. Explicit agent-worktree-base and agent-worktree-dev
also require an existing source to descend from the requested pinned base.
Development is created from master only when the native development ref is
conclusively absent. Failed queries and bootstrap/add/register errors retain
their actual diagnostics and nonzero status. No arbitrary error triggers fallback.
The caller's staged or untracked work and an unrelated frozen candidate remain
usable; no clean-tree, final-gate or capacity prerequisite was added.

BRANCH, BASE and ACTIVE_WORKSTREAM_REGISTRY are literal creation inputs. The
direct public goal guard rejects dollar evaluation before startup children.
Ordinary quotes remain literal through native registration. Ordinary environment
versus file defaults, -e environment precedence and command-line selection remain.
Custom aliases and target-specific contexts must supply literal data; the direct
goal controls do not establish arbitrary-wrapper evaluation protection. Keep one
guarded goal per invocation, quote whole assignments and split intentional goals.

Only verified creation plus successful registration prints WORKTREE_PATH and
ready. Success guidance grants no acceptance, gate, hosted or release proof.
Failure guidance names the actual phase, native status and retained path/source.
A registration failure can leave a valid checkout and branch. Inspect that state
before the narrow repair; do not retry creation blindly or force-remove, reset,
unlock or prune. Reconcile the original native error and ownership before using
the explicit workstream-register owner for the verified existing checkout.

The existing registry mutation now refuses to reassign an active selected branch
to a different physical path or an unverifiable recorded path. It checks under
its existing flock and preserves the original entry and peers on refusal. Renewal
at the same path, a symlink alias or the macOS /tmp alias remains valid. Explicit
inactive replacement remains valid. Atomic write failures already propagate:
no new API, lock, cache, readback or no-op-tool workaround was introduced. Release
of an existing logical owner remains an explicit lifecycle decision.

The safe contract examples deliberately exercise required-input refusal:

~~~sh
make agent-worktree BRANCH= ACTIVE_WORKSTREAM_REGISTRY=
make agent-worktree-base BRANCH= BASE= ACTIVE_WORKSTREAM_REGISTRY=
make agent-worktree-dev BRANCH= ACTIVE_WORKSTREAM_REGISTRY=
~~~

Each retains native Error 1/Make 2 and outcome=refused without mutation.
For actual creation select an owned branch, explicit base where applicable, the
intended registry and installed runtime; work in the reported verified checkout.
This target supplies no retirement, acceptance or release permission.

Native pre-change RED is 12 failures in 12.04s: add 53 and registration 47 were
hidden by ready 0, while development omitted registry execution. Receipt:
worktree-creation-red/worktree-creation-inputs-red311-4604.json.
The first 30-case core passes in 26.23s, observed 27.2s on 3.11.14:
worktree-creation-focus/worktree-creation-green311-4604.json.
An earlier unquoted fixture-tool path returned shell 126; that retained mixed
diagnostic supplies no registration-47 proof. The expanded pre-registry union
passes 164 cases in 77.23s, observed 78.8s:
worktree-creation-receive/worktree-creation-boundaries311-4604.json.

A second native RED proves all three targets reassign another active path:
worktree-creation-red/worktree-active-owner-red311-4604.json, 3 failures in 3.75s.
The atomic owner repair passes 11 native/unit controls in 5.53s, observed 6.6s:
worktree-creation-focus/worktree-active-owner-green311-4604.json.

Complete receiving retains all five canonical coverage_workstream_prune owners,
the complete changed creation/worktree/branch/UV owners, three managed callers,
confinement and two contract nodes. Functional source, tests and the full existing
two-file coverage profile remain unchanged across these serial native runs:

| Runtime | Cases | Pytest | Observed | Native coverage-files run |
| --- | --- | --- | --- | --- |
| 3.11.14 | 511 passed | 164.21s | 165.9s | 20261023T203752Z-49915-c14b1118 |
| 3.12.15 | 511 passed | 230.53s | 233.2s | 20261023T204142Z-68375-3781f29e |

Both native audits pass 93.6% aggregate lines/86.2% branches; registry 99.0/95.8
and pruning 92.0/84.4 each clear the unchanged 75% file floors. Audit artifacts
are coverage-20261023T204037Z.json and coverage-20261023T204536Z.json.
The target contract crossed 2500 lines after adding the three creation entries.
After the 3.11 owner drained, only those entries and the already changed register
variable list were compacted to 2498 lines, preserving parsed configuration.
Fresh contract/link receiving passes three nodes in 1.18s, observed 2.3s:
worktree-creation-metadata/worktree-creation-contract311-4604.json.
The 3.12 complete run consumes that final configuration; this metadata repair
does not justify another unchanged production-profile replay.

Fixtures retain the exact native rules, shared macro and guards, replacing only
the fixed temporary root with an owned fixture root. Real Git and atomic native
registration use an explicit private registry, never UV=true. Every native result
preserves the caller's index, staged/untracked bytes and peer refs, plus a real
locked frozen peer's source, index, checkpoint and lock record. Original lifecycle
assertions remain. Complete literal-rule/macro checks replace a 500-character
window; padded positives and missing/neighbor negatives retain the original
command assertion. All 15 same-value/origin unguarded Make witnesses actually
execute the owned marker; guarded controls retain no-evaluation and carriers.

The [GNU recipe-shell contract](https://www.gnu.org/software/make/manual/html_node/Execution.html)
explains explicit shell failure propagation. The
[Git worktree contract](https://git-scm.com/docs/git-worktree) documents private
linked metadata and shared common-directory refs, supporting native identity
checks. These primary bodies were reviewed from the accepted bounded research
receipt.

### Firsthand worktree ownership reports

The [worktree question](https://stackoverflow.com/questions/41545293/branch-is-already-checked-out-at-other-location-in-git-worktrees)
opened on 2017-01-09; the retrieved API reports last activity on 2025-09-07.
In the [highest-scoring answer](https://stackoverflow.com/a/41546512), torek
explains why an existing branch attachment is protected. Its pruning advice is
conditional on the old worktree actually having been removed; an existing
worktree retains ownership. It is not an accepted-answer or project permission.

[Tom Ellis's firsthand report](https://stackoverflow.com/a/76664209), dated
2023-07-11, describes a forgotten rebase still using the branch even though the
other worktree's apparent checkout did not explain the refusal. That author's
resolution applies to their own operation. For this repair, these reports
corroborate preserving native add failure and verifying ownership instead of
reporting READY from a superficial HEAD observation. They authorize no abort,
prune, forced attachment or metadata deletion here.

Actual question and all nine answer bodies were reviewed from the native
worktree-research-live-311-aa494265 receipt: two HTTP 200 responses, no additional
pages, question JSON line 3 and answers line 9. The
[earlier project record](BETA4_DUAL_TRACK_CI.md) predates this body review.
Linked commits and comments were not fetched, so no exact Git-version claim is
adopted from other answers. This is practitioner context; the native acceptance
and release proofs above retain their separate authority.

This is control-plane ZDD with no service restart. Independent review, normal
collection, the canonical full gate, unchanged 85/75 line and branch coverage,
and exact-SHA hosted/deployment proofs remain required. These are source-repair
receipts, not release completion.

## Quoted observed commands

`run-watched` preserves ordinary quoted child arguments, including quoted
`TESTFILES` and `PYTEST_ARGS` assignments passed to a child Make invocation.
Its admission guard evaluates command presence using GNU Make's `strip` and
`if` functions and sends only a fixed presence marker to the shell test. It
does not embed command text inside another shell-quoted emptiness test.
The existing child command, observer, streaming output, retained receipts,
timeouts, and exit propagation remain unchanged. No stderr is suppressed.

The previous guard could print a shell argument-count error while still
executing a successful child when `CMD` contained normal double-quoted
arguments. Actual-recipe regressions first reproduced that diagnostic, then
verified both single and double quotes, rejection of empty commands before
observation starts, and failed child codes 7 and 143 in terminal receipts.
GNU Make itself reports recipe failure as 2; the observer retains the child
code. A passing child never conceals an invalid admission check.

This bounded smoke uses the existing target and one test worker:

```text
make run-watched CMD='make test-files TESTFILES="tests/unit/test_run_watched_make_recipe.py" NPROC=1 GLUDD_XDIST_WORKERS=1 PYTEST_ARGS="-W error -q --tb=short"' OBSERVED_LABEL=make-helper-smoke RUN_ID=quoted-command STALL_SECS=30 MAX_SECS=120 LOG=.gate-logs/make-helper-smoke.log OBSERVED_ROOT=.gate-logs/observed OBSERVED_HEARTBEAT_SECS=10 OBSERVED_RETAIN_RUNS=20
```

Empty commands still fail before starting a child. This target does not certify
a release or replace the full gate and its unchanged 85/75 coverage floors.

## Read-only source version metadata

Packaging-name defaults previously expanded `VERSION` during Make parsing,
even when the requested target was only `git-show-commit`. The runtime import
allocated an empty `.venv` in a fresh checkout and emitted two application
import failures before the otherwise successful Git diagnostic.

`VERSION` now uses the existing offline `uv version --short` source-metadata
reader. It passes no replacement value or bump option, does not import
`general_ludd`, and does not need an installed application or a Python
interpreter. The native shell's Python 3.9 therefore does not need an added
TOML dependency. This is an existing tool interface, not a new version loader.
The metadata operation does not depend on recipe-local `UV` values used by
dependency-free diagnostics, and introduces no new `UV` override.

The reader binds its project explicitly to the current checkout, so an ambient
`UV_PROJECT` setting cannot substitute another project's version.

`DMG_NAME` and `WINDOWS_INSTALLER` use GNU Make's existing recursively expanded
`=` assignment, like the tarball default. Unrelated Git helpers and an external
`clean` dry run therefore do not evaluate version metadata at all, even in a
fresh Git fixture with no `pyproject.toml`, application source, or environment.
This fixes the unnecessary lookup rather than adding metadata to every fixture.

The existing command-line `VERSION` precedence, tarball name, macOS DMG name,
Windows installer name, and explicit installer-name overrides are preserved.
Explicit `make version` still uses the application runtime and exposes its
import failures. When version-dependent names are requested, missing, malformed,
or empty source metadata still fails with the original diagnostic and a Make
error; there is no silent fallback to an installed package's possibly stale
version. A dry run of an unrelated target preserves the leased environment's
sentinel and exact directory inventory.

The read-only behavioral example is:

```text
make git-show-commit C=HEAD
```

Regressions exercise the full actual Make entrypoint in a fresh temporary Git
checkout with an intentionally broken runtime module. The diagnostic must be
silent on stderr, leave `.venv` and `uv.lock` absent, and preserve the exact
source metadata bytes. The explicit runtime command must still fail. Additional
actual packaging-default cases check the precise project field rather than an
unrelated table's version, command-line precedence, and invalid source metadata.
The tiny packaging fixture extracts the lazy definitions without replacing
their behavior; its four invalid-metadata cases still request actual names and
must fail. Metadata-free helper fixtures instead prove no lookup is required.

The workflow-state, writable-cache, and dependency-profile contracts inspect
only the `VERSION` definition when pinning this offline, checkout-bound reader.
They reject runtime loading in that definition while retaining their existing
guard-goal, cache-sandbox, and no-sync assertions. The actual-recipe regressions
above still enforce lockfile and environment nonmutation; conforming the legacy
structural checks does not relax these behavioral checks or runtime validation.

Isolated launcher fixtures that invoke the full external Make entrypoint copy
the source `pyproject.toml` before invocation. They do not override `VERSION` or
weaken missing-metadata rejection. Duplicate-owner tests retain the live PID;
dry-run tests compare directory contents, and both retain unchanged metadata
without allocating an environment. The second sealed integration's two absent
fixture-metadata failures remain recorded rather than treated as gate success.

## Real owner execution with mixed Make goals

A goal-dependent global `override UV := echo` previously silenced every UV
recipe whenever any listed goal appeared. A mixed `worktree-state` and
`workstream-register` invocation could report success without registering an
owner, in either order. Standalone `git-checkout` also printed rather than ran
its required gate-mutation guard, allowing its body past a live gate lease.

The foundation now keeps the existing real `UV := uv` default for all goals and
prerequisites. Its existing exported `override UV_NO_SYNC := 1` prevents implicit
environment synchronization without replacing execution. Native command-line
UV selection, shared writable cache, chosen runtime, warning policy and every
resource/CI/release guard stay unchanged. There is no new runner or fallback.
Missing tools and failed owner children must fail visibly.

Recipe-local `UV=echo $(SYSTEM_PYTHON)` remains intentional for dependency-free
diagnostics only. The system interpreter remains pinned to `/usr/bin/python3`;
read-only source-version laziness and metadata-free inspection are unchanged.
The safe public cache-path example is:

```sh
make uv-cache-path GLUDD_UV_CACHE_DIR=/tmp/gludd-uv-cache-public-v2
```

Native fixtures first reproduced nine failures while four standalone read
controls passed. They exercise both mixed registration orders, actual owner
failure, active/idle gate guards before harmless checkout capture, selected UV
failure and no-runtime reads. They preserve exact owned source/index/ref bytes
and caller environment. An additional real-UV delegation control verifies the
global no-sync export defeats an explicit `UV_NO_SYNC=0` while retaining the
chosen toolchain; the original checkout-first multi-goal refusal remains intact.
Legacy list-membership tests retain every named target and their original
dependency-free recipe assertions while checking the global no-sync contract.

[GNU Make goals][make-goals] distinguishes explicit goals from prerequisites,
and its [override rules][make-override] explain the invocation-wide assignment.
The maintained [uv no-sync option][uv-no-sync] preserves command execution.
A [2016 multi-goal user report][make-multi-goal-forum] demonstrates both recipes
receiving a global `MAKECMDGOALS`-derived value; it is practitioner context, not
authority for suppressing guards. This change is control-plane zero-downtime:
no service restart, installed package change, gate/CI execution or release
authority. Rollback restores source through normal review and would restore the
known silent-owner defect. Focused proof never substitutes for the full gate,
exact-SHA hosted proof or unchanged 85/75 coverage floors.

## Literal search inputs and error propagation

The actual `search` recipe broke on quoted JSON patterns and paths, interpreted
backticks and dollar syntax, and reported invalid regexes as no matches. Six
new actual-recipe cases fail first while 12 cases pass. A further two cases
reproduce evaluation of Make-function text during automatic variable export.

The existing rg/grep backend now receives raw values through simply expanded,
namespaced environment carriers using GNU Make's `value` function. Original
recursive `PATTERN` and `SEARCH_PATH` variables are explicitly unexported;
otherwise environment construction could evaluate them through a second route.
Quoted shell parameter expansion preserves each value as one argument. No
custom parser, escaping engine, runner, environment or daemon is introduced.

Backend status 0 means matches, 1 means no matches, and greater than 1 remains
an error with its original stderr. The workspace path boundary, option boundary
and primary rg exclusions remain intact. The invocation's namespaced temporary
file is created by mktemp and removed by an exit/signal trap. All 20 complete
helper cases pass, preserving original metadata, override and runtime checks.

The safe public example supplies both documented inputs:

```sh
make search PATTERN=^search: SEARCH_PATH=make/95-collections-and-pipelines.mk
```

The promoted `make-search-literal-transport` family executes the complete helper
file before hosted fan-out. It does not replace the full gate or alter serving
state. Five routing/roster cases fail first, then the combined helper/promotion/
plan slice passes 58 cases. The actual complete early owner passes 624 cases in
34.27 seconds (35.1 observed), retaining 37 families and unchanged 90/60 bounds,
21 phases and 2850-second ceiling. All 218 contracts and the actual public
example pass. GNU Make's [raw value][make-raw-value] and
[export/unexport semantics][make-export] establish the expansion boundary;
[POSIX shell field splitting][shell-fields] defines quoted argument transport.
The maintained [rg manual][rg-manual] defines distinct result/error statuses.
The April 2015 [safe Make-variable transport report][search-forum] reproduces
embedded quotes splitting one value into multiple shell arguments. These are
existing tool semantics and practitioner findings, not proof of release success.

## Read-only native temporary-library inspection

`native-temp-artifact-inspect` uses the existing system `stat`, SHA-256,
`file`, `otool`, and `lsof` interfaces on one exact non-symlink library under
the configured user's temporary root. It prints per-phase progress and never
loads the library, deletes files, or grants cleanup authority. Raw namespaced
environment transport keeps literal Make-function text in filenames inert.
Twelve actual-recipe and contract regressions fail before the target exists,
then pass after wiring; the documented plan is read-only:

```sh
make native-temp-artifact-inspect NATIVE_TEMP_ARTIFACT_PATH=/Users/shawnwilson/tmp/.gludd-native-inspect-contract.dylib NATIVE_TEMP_ARTIFACT_VALIDATE_ONLY=1
```

Actual inspection of one old hash-named file identifies FFF's arm64
`libfff_c.dylib`, not OpenTUI. A single sample, matching UID, old timestamp,
or an empty open-file lookup does not establish every file's identity,
project ownership, or deletion authority. [Bun's long-lived extraction
report](https://github.com/oven-sh/bun/issues/29585) covers repeated embedded
native-library copies; [FFF's upstream resolver](https://github.com/dmtrKovalenko/fff/blob/main/packages/fff-bun/src/download.ts)
identifies the platform asset used for a retained reference. Unknown files and
all history stay protected; neither this diagnosis nor a plan is release proof.

## Keep internal guards out of the public Help interface

Terminal Build `38307021746`, exact source `c4f499fbd983c4c3a0fdd041ac6c05fcbc740ec5`,
exposes the original `test_internal_targets_not_in_help` failure in unit-2 after
237 neighboring cases pass. Native replay reproduces that same failure: an
explicit Help line advertises `_pull-before-push-guard` as a public command.

The narrow repair documents current-remote admission on its existing public
`git-push-sandboxcom` entry and removes only the private command's Help entry.
The guard, dependencies, current-remote checks, SSH handling and error behavior
are unchanged. The original test is unchanged. Its exact node now belongs to
`make-private-target-help-visibility` in the existing early compatibility owner;
the expensive complete deep-target cohort remains required downstream. New
mapping/roster tests fail first, followed by the exact executable-wiring test.
No new helper, Make target, command parser or environment is introduced.

The 2014 [private-target practitioner discussion][private-target-forum], updated
in 2022, distinguishes internal-target intent from variable scoping and records
bypassable naming workarounds. That is practitioner evidence, not an access
control guarantee. Gludd's underscore convention defines public Help visibility;
omitting a name from Help does not make a target uncallable or replace actual
push/branch/CI admission. The fix changes no serving process, has no migration
or service restart, and does not grant deletion or release authority.

Actual `make help` and all 218 variable-aware contracts pass. The early phase
retains warning-fatal one-worker execution, 90-second runtime/60-second quiet
bounds, all 21 phases, the 2850-second ceiling, full-gate replay and 85/75 floors.
All 52 Help, pull-guard, promotion and plan cases pass in 7.53 seconds; the actual
complete early owner executes 718 cases in 37.91 test seconds (38.8 observed),
with 40 promoted families. Strict typing, Ruff and Make/Markdown checks pass.
Focused green is not the replacement exact-SHA hosted proof.

## Research and resource boundary

Research reviewed on 2026-10-12:

- The [GNU Make conditional-function reference][make-if] defines functional
  presence testing; its [string-function reference][make-strip] defines
  whitespace normalization. The recipe uses those established facilities rather
  than adding a command parser or shell-escaping subsystem.
- A [Unix & Linux practitioner discussion from 2017-10-20][make-forum] records
  the long-lived confusion between Make expansion and shell parsing, including
  the same shell argument-count diagnostic. This feature's regression tests
  exercise the real recipe and runner rather than assuming shell quoting alone
  protects text expanded by Make.
- The [uv CLI reference][uv-version] provides a built-in source version reader;
  the [PyPA metadata specification][project-version] defines the static project
  version field. These established interfaces were selected after inspecting
  existing repository version helpers and the native interpreter boundary.
- A [packaging practitioner discussion begun in 2010][version-forum] records
  the bootstrap problem caused by importing application code before its runtime
  dependencies are installed. Its installed-distribution approaches address a
  different lifecycle from a fresh source checkout; this helper reads the
  checkout's metadata instead.
- GNU Make's [recursive-assignment][make-recursive] and
  [simple-assignment][make-simple] references distinguish use-time expansion
  from definition-time expansion. This change applies deferred expansion only
  to the two version-dependent packaging defaults; it adds no custom loader or
  persistent cache. A [practitioner discussion begun in 2011][lazy-forum],
  including an unused shell-command example added in 2021, records how eager
  assignments can execute work even when no target consumes their value.

ZDD is nonmutation of deployments: these helpers do not promote branches, tag,
publish, or restart services. The implementation adds no persistent process and
retains the existing observed-command owner, bounded lifecycle, heartbeat,
run-namespaced logs, atomic terminal receipt, and cleanup. Tests use isolated
temporary directories and at most one worker; their child commands never run
the full suite or a gate.

[make-if]: https://www.gnu.org/software/make/manual/html_node/Conditional-Functions.html
[make-strip]: https://www.gnu.org/software/make/manual/html_node/Text-Functions.html
[make-forum]: https://unix.stackexchange.com/questions/399451/makefile-sqare-brackets-built-in-variable-expansion-and-command-substitution
[uv-version]: https://docs.astral.sh/uv/reference/cli/#uv-version
[project-version]: https://packaging.python.org/en/latest/specifications/pyproject-toml/#version
[version-forum]: https://stackoverflow.com/questions/2058802/how-can-i-get-the-version-defined-in-setup-py-setuptools-in-my-package
[make-recursive]: https://www.gnu.org/software/make/manual/html_node/Recursive-Assignment.html
[make-simple]: https://www.gnu.org/software/make/manual/html_node/Simple-Assignment.html
[lazy-forum]: https://stackoverflow.com/questions/4879592/whats-the-difference-between-and-in-makefile
[make-raw-value]: https://www.gnu.org/s/make/manual/html_node/Value-Function.html
[make-export]: https://www.gnu.org/software/make/manual/html_node/Variables_002fRecursion.html
[shell-fields]: https://pubs.opengroup.org/onlinepubs/9799919799/utilities/V3_chap02.html#tag_19_06_05
[rg-manual]: https://github.com/BurntSushi/ripgrep/blob/master/crates/core/flags/doc/template.rg.1
[search-forum]: https://stackoverflow.com/questions/29906576/safely-pass-make-variable-to-shell-commands
[private-target-forum]: https://stackoverflow.com/questions/26063839/how-can-i-make-a-target-private-in-gnu-make-for-internal-use-only-or-how-to
[make-goals]: https://www.gnu.org/software/make/manual/html_node/Goals.html
[make-override]: https://www.gnu.org/s/make/manual/html_node/Override-Directive.html
[uv-no-sync]: https://docs.astral.sh/uv/reference/cli/#uv-run--no-sync
[make-multi-goal-forum]: https://stackoverflow.com/questions/36174028/makefile-set-variable-in-target

## No-project workstream ownership commands

The global no-sync export remains mandatory for project commands. Workstream
registration and removal are stdlib-only `uv run --no-project` operations;
inheriting `UV_NO_SYNC=1` there emits a native warning because the flag has no
effect outside project discovery. A command-line Make assignment cannot clear
the foundation's explicit override.

Only these two existing recipes set `UV_NO_SYNC=0` in their child environment,
before invoking the selected UV executable with the same `--no-project`
arguments. Project commands retain the global value `1`. Native tests exercise both
contexts, retain the selected toolchain and caller environment, and prove
registration/removal, errors and nonmutation of source, refs, index, lockfile and
virtual-environment inventory. The actual warning fails first; all 39 boundary
and registry/pruning cases then pass with fatal pytest warnings.

The 2025 [UV user report about misplaced project flags](https://github.com/astral-sh/uv/issues/12954)
illustrates why option scope matters: the maintainer identified arguments after
the script name as script arguments. This is a different reported trigger,
not evidence for the local warning. The maintained
[UV CLI reference](https://docs.astral.sh/uv/reference/cli/#uv-run--no-project)
defines no-project discovery, and its
[environment reference](https://docs.astral.sh/uv/configuration/environment/#uv_no_sync)
defines the no-sync equivalence. The repair changes the two native command
environments, with no dependency synchronization, package update, extra process,
service restart or cleanup-policy change. Full gate and exact-SHA release proof
remain separate requirements.
