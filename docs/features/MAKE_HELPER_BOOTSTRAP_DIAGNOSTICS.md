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

### Native Make parallel and inspection receiving after 98ba752

Review of committed 98ba75247503661346c2ba442cf52580ef937282 raised a
jobserver-loss hypothesis because the public recipe calls a macro containing
recursive Make. Actual GNU Make 3.81 did not reproduce it. Confined native -j2
creation and real registry execution completed without a jobserver warning, and
the registration child retained its native jobserver-fds/auth flags and unrelated
carrier. No production change, plus prefix, parser or new provider was justified.

For plain agent-worktree, native -n/-q/-t controls under ordinary and -e execution
return 0/1/0, preserving owned file bytes, mtimes and modes, all refs, current and
frozen-peer
indexes, source/checkpoint/lock state, registry absence and an owned observer
sentinel; no creation, registration or new producer starts. Dry-run recipe text
can contain a printed readiness command, so only an actual standalone readiness
line constitutes a result. Query can stop before a later body; its nonmutation
does not establish how a hypothetical plus-prefixed recipe would behave.

The complete new seven-case owner passes in 4.08s, observed 5.0s on 3.11.14:
worktree-creation-make-modes/worktree-creation-native-modes-red311-98ba752.json.
The retained run identifier contains red, but the native outcome is passed:
this is a disproved hypothesis, not a RED reproduction. The controls use the
actual existing creation source graph and confined real Git/registry fixture.
Only the test helper's optional Make argv and recorded child flags were added.
The original 98ba752 source, 511-case coverage receipts and normal collection
remain their original scope; new test receiving/review/collection is separate.

The complete changed 72-case creation owner plus all seven new controls then
passes on each installed runtime, with no production/configuration change:

| Runtime | Cases | Pytest | Observed | Native worktree-creation-make-modes run |
| --- | --- | --- | --- | --- |
| 3.11.14 | 79 passed | 91.53s | 93.4s | worktree-creation-modes-owner311-98ba752 |
| 3.12.15 | 79 passed | 92.04s | 94.1s | worktree-creation-modes-owner312-98ba752 |

Both exact sessions drained terminal 0 under the original 120/60 bounds, fatal
warnings, serial pytest and literal ID=. Source-only approval for 98ba752 is
separate from this additive packet's normal collection, commit and final proofs.
No unchanged 511-case production profile was replayed.

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

### Main receiving repairs on1eec791 (2026-10-24 UTC)

The first combined Main receiving is FAILED:11FAIL/672PASS/1conditionalSKIP,
187.11s/native188.4 at coverage-files/20261024T045750Z-89890-a32ca7af.json.
Its exact native trace has684pairs,11failure reports and no unfinished nodes.
No coverage report or green acceptance follows. Independent Main review also
requires changes; its negative artifact remains separate from later approval.

The intake plan omitted the existing workstream-register forwarding dependency
already fixed in approved98ba752 lines795-797. Nine original target/origin literal
registry round trips fail on actual Main. Receive that exact three-line hunk;
keep every UV argument, optional registry behavior, native status and assertion.
The other two failures require explicit AGENTS concurrency wording and the
historical worktree-locking gap/current bounded metadata-resolver record.
Do not alter the tests or claim that a create/register result proves locking.

A separate expected-image comparison initially consumed Make double dollars
through JavaScript replacement-string semantics. Native source bytes were
correct. Literal callback substitution fixes only the verifier; never change
correct source to match an altered expected image. Bounded raw reads likewise
avoid output truncation and presentation-diff formatting.

Before another combined replay, use existing test-files/run-watched on all11
unchanged failing cases, then retain the complete684-case union and both whole
profiles on each installed runtime. Compare every delegated existing owner in
the immutable changed-target packet, not only the new primary recipe. A file
roster or exact hunk count cannot establish that dependency completeness.
These repairs grant no final gate, exact-SHA CI, deployment or release proof.

### Corrected Main receiving accepted (2026-10-24 UTC)

The corrected frozen postimages on base1eec791 have complete32-selector receiving
on both installed runtimes:683PASS/1existingcontextSKIP each,311198.51s/native200.197
and312194.45s/native197.1. Native receipts are coverage-files/
20261024T051221Z-15508-5e241646.json and20261024T051650Z-36616-c3f094e5.json.
Each trace has1371events/684completed protocol pairs,0failure reports and
0unfinished nodes. The skipped linked-context node is not relabeled as passing.

Both full prune/registry audits pass93.6percent LINE/86.2BRANCH aggregate;
prune92.0/84.4 and registry99.0/95.8 retain85aggregate/75each for both metrics.
Native audits are coverage-20261024T051541Z.json andcoverage-20261024T052008Z.json.
The entire retired module separately passes all5 cases each runtime with
100percent lines/branches,8statements/2branches; receipts coverage-files/
20261024T051803Z-44963-d857ded1.json and20261024T052211Z-56838-bac9ccea.json
retain native3.8/4.3s and original90/60. No source/profile exclusion or test
assertion was changed. The first11FAIL/672PASS/1SKIP and negative review remain.

The repaired22-file content guard passes three hooks, strict staged lines and
zero new clones at worktree-lifecycle-main-staged/
worktree-lifecycle-main-repaired-staged311-1eec791.json, native13.8s.
This later receipt-only prose receives ordinary normal commit checks; it is
not attributed to that earlier guard or a new exact-SHA gate. Independent
repaired source review and fresh normal collection are separate owners.
The native state reader owns the eventual commit SHA and current index.

This source acceptance is not canonical full-gate, push CI, development/master
integration, deployment or release sign-off. All seven tasks remain open.
Reuse these receipts only for their unchanged code/tests/configuration/runtime
and scope; this receipt prose alone does not justify repeating accepted684 tests.

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

## Execution-only recursive registration after the D hosted warning

Build `40448994419`, job `122011474974` (unit-1a1), failed the unchanged
`test_native_parallel_creation_keeps_jobserver_for_registration` warning
assertion on source D `9b01c6296f2a24d39a95e0c6194bd118ac8824f8`.
Creation and native registration finished, but nested Make reported an
unavailable jobserver and a single-job fallback. The retained native context is
`/tmp/gludd-v012-D-failed-test-context-unit-1a1-20261027.log`. This actual hosted
failure is the regression evidence; the earlier 98ba752 local no-reproduction
and both 79-case receipts above retain their original scope.

The public recipe enters `_agent-worktree-create` through a macro call. Its
nested registration Make needs recursive-command classification at that outer
recipe boundary. The only production change prefixes the macro's first command
with `+` when the existing unconditional `_commit-native-mode` is `execute`.
Inspection keeps the original command prefix and native `-n/-q/-t` behavior.
The existing Foundation grammar, three mode bindings, literal inputs, native Git
checks, ownership and registration failure handling remain. No Python FD bridge,
lock change, token reader, new production parser or persistent process is added.

Confined fixtures now include the actual complete Foundation inspection provider
and all three native mode bindings. The original warning assertion and all
ordinary/`-e` inspection assertions are unchanged. An owned probe observes the
registration entry, then `execve`s the same installed Make with its original
recipe arguments and copied environment. Pipe channels require distinct open,
inheritable FIFO descriptors; FIFO-style channels require the actual accessible
FIFO path. The probe never reads or writes jobserver tokens. A serial control
detects channel absence. Matched parallel controls use the same entry and flags,
deliberately closing pipe descriptors only in the negative; native Make help
selects pipe style when the installed Make advertises that option. The original
parallel test retains its default native jobserver style. Copied environment
fields retain `repr=False`, and the probe records owned arguments and booleans.

The first local probe incorrectly required both anonymous-pipe endpoints to have
the same inode. macOS gave different endpoint inodes despite both descriptors
being open and inheritable. Preserve the failed 1-fail/8-pass diagnostic receipts:
`v012-jobserver-fd-red/v012-jobserver-fd-red311-9b01c62-20261027.json`
(24.67s/native26.122s) and
`v012-jobserver-fd-green/v012-jobserver-fd-green311-9b01c62-20261027.json`
(24.12s/native25.659s). These are probe-assumption failures, not production RED
or closed-channel evidence. Bounded native comparisons found open channels both
before and after the production line on installed GNU Make 3.81; the old local
no-reproduction remains valid. Private diagnostic files retain that comparison.

After correcting the platform assumption, the logical-Makefile AST node plus the
complete changed mode owner passes 11 cases on Python 3.11.14 in 27.25s/native 28.749s:
`v012-jobserver-fd-portable/v012-jobserver-fd-portable311-9b01c62-20261027.json`.
The exact session drained with native 0, an inactive passed status and 25 native
trace events/11 complete pairs, with 0 issues, unfinished nodes or failure reports.
This proves the active closed-FD detector and local execution/inspection behavior,
not a local reproduction of the hosted warning or a GNU Make 4 execution.

Complete receiving then ran the same four whole owners, with no selector filter:
`test_agent_worktree_creation_fail_closed.py`,
`test_agent_worktree_creation_make_modes.py`, `test_agent_worktree_targets.py`
and `test_makefile_layout.py`. Both serial runs consumed the frozen four-path
working snapshot on base D, after Root's native staged-content guard passed
with 0 new clone findings in 6.996s. That guard's exact native run is
`v012-J4-staged-content/v012-J4-staged-content311-9b01c62-20261027.json`.

| Runtime | Actual cases | Pytest | Native observed |
| --- | --- | --- | --- |
| Python 3.11.14 | 122 passed/0 skipped | 106.08s | 108.170s |
| Python 3.12.15 | 122 passed/0 skipped | 113.14s | 115.225s |

Exact native receiving receipts under `.gate-logs/observed` are
`v012-jobserver-receiving/v012-jobserver-receiving311-9b01c62-20261027.json`
and `v012-jobserver-receiving/v012-jobserver-receiving312-9b01c62-20261027.json`.
Both sessions drained; each exact-command status is inactive/passed/native 0.
Each actual trace has 247 events/122 complete pairs, with 0 issues, unfinished
nodes or failure reports. Both use fatal warnings, literal `ID=`, one pytest
worker, at most two nested native Make jobs and a 600/180 diagnostic boundary.
All 24 original function definitions and 104 assertion ASTs remain; the two test
owners now contain 27 functions and 118 assertions. Strict typing of both changed
test owners passes on both installed runtimes.

Independent SOURCE ONLY review approved the frozen pre-receipt-prose four-path
snapshot in `/tmp/gludd-v012-jobserver-J4-independent-source-review-20261027.md`.
The three code/test postimages remained unchanged throughout both runs and this
receipt edit. This is working-source receiving, not a future immutable-commit,
GNU Make 4 hosted replay, full gate, coverage or release certificate. Normal
hooks/collection and a fresh exact-source hosted warning replay remain pending;
the final receipt paragraph requires its separate bounded source review.

Root's accepted bounded public research retrieved the
[GNU POSIX jobserver contract](https://www.gnu.org/software/make/manual/html_node/POSIX-Jobserver.html)
and this [firsthand Make report from 2020](https://stackoverflow.com/questions/60702726/warning-jobserver-unavailable-using-j1-add-to-parent-make-rule).
The manual ties pipe-descriptor access to recursive-command classification and
explains that closed advertised descriptors indicate a missing recursive mark.
The practitioner reproduces the warning for a plain Make invocation, compares
`+` and `$(MAKE)`, and asks about forced execution under inspection flags. That
context supports keeping the execution-only boundary; it does not prove this
source fix or any installed Make version. Root's native retrieval receipt is
`v012-jobserver-native-public-context/v012-jobserver-native-public-context-20261027.json`.

This is control-plane ZDD: no deployment restart or cleanup policy changes.
The 180/60 local diagnostic boundary supplies no canonical 90/60 runtime,
21600-second full gate, 85% aggregate/75% every-file line and branch coverage,
exact-SHA hosted, development integration, master deployment or release proof.
