# Runtime Admission Throughput

The exact c9d2e98 hosted Build 38362521808 runtime admission failed at the
unchanged 90-second deadline on both Python 3.11 and 3.12. The 3.11 lane had
reached the separately isolated help check; the 3.12 lane reached 70% of the
first cohort. Preserve those failures and the entire current roster.

Research selected installed pytest-xdist 3.8.0 and the existing test-files,
tests.conftest shared-state hook, basetemp cleanup, and run-watched owners.
The official loadgroup documentation promises grouping by xdist_group marks:
[pytest-xdist distribution](https://pytest-xdist.readthedocs.io/en/stable/distribution.html).
An upstream user thread opened in January 2018 describes the long-lived
session-fixture boundary: every worker owns a separate pytest session, and
shared mutable resources require explicit isolation or synchronization:
[Session fixture report](https://github.com/pytest-dev/pytest-xdist/issues/271).
A January 2023 user report asks whether every group can execute concurrently;
maintainers instead explain use of fixtures and existing scheduling:
[Group scheduling report](https://github.com/pytest-dev/pytest-xdist/issues/981).
These reports motivate native verification; they are not proof of Gludd safety
or hosted speed improvement.

Native regression exposed an existing hook-order gap: xdist expands group
marks into node IDs in pytest_collection_modifyitems before the repository's
plain same-named hook runs. The canonical shared-state marker added by that
hook was therefore too late. A real exclusive-create shared-file fixture
passed on one worker but failed twice on two workers. Add tryfirst=True to
the existing repository hook, preserving its complete classification, legacy
marker normalization, unrelated-resource groups, and ratchet behavior.

The scheduling change keeps the first cohort at exactly two xdist workers
and --maxprocesses 2. The same fresh, sequential help-only test-files process
retains one worker and --maxprocesses 1. It preserves every test selection,
warning-fatal -W error, loadgroup routing, per-invocation basetemp, environment
selection, original crash/restart behavior, and both failure exits. There is
no new runner, allocator, daemon, cache, exclusion, deadline, warning filter,
or reduction of full-gate/coverage/exact-SHA/promotion obligations.

Resource boundary: resource_arbiter.py owns namespace/path derivation;
active_work_status.py imports collection/resource lease inspection and reports
the existing eight-worker ceiling. Its sampling is diagnostic, not an
enforcing global pytest allocation lease. xdist_trace_plugin.py similarly
observes PIDs, load, RSS and free disk; it does not allocate workers. This
patch adds no second allocator and does not claim a global-cap proof. Root
coordinates sibling work below the unchanged ceiling; this owner itself
has at most two concurrent xdist workers, with the help worker later.

Preliminary whole-roster diagnostic replays at native source 8e2772f:
one worker: 1,163 first-cohort cases in 66.89 pytest seconds, separate help
in 1.76 seconds; whole observed run 70.446 seconds, exit 0.
two workers: the same 1,163 cases in 46.52 seconds, separate help in 1.90
seconds; whole observed run 50.522 seconds, exit 0.
Each trace records exactly 1,163 START and FINISH events with zero failures
or unfinished nodes. Both preliminary replays predate the hook-order repair;
their green result is not sufficient isolation evidence. Retain them and
record the repaired-hook replay separately. All observations retain the
same 90-second maximum and 60-second stall bounds.

The five-case failing-first regression replay has two failures and three
passing controls: the original one-worker configuration violates the new
bounded scheduling expectation, and the native shared-file test reproduces
the original late-group race. The controls verify original child failure
propagation and warning-fatal native execution.

After the existing grouping hook repair, the actual owner passes all 1,163
first-cohort cases plus the separate help case under both installed runtimes:
Python 3.14 takes 49.995 observed seconds (46.30 + 1.82 pytest seconds), and
Python 3.11 takes 45.627 observed seconds (42.14 + 1.59 pytest seconds).
Each cohort trace contains exactly one START and FINISH per original node,
no failed or unfinished node, and the canonical enforcement group stays on
one worker while independent tests use both workers. The help process has
a distinct PID and runs afterward. Compared with the observed local 70.446
second baseline, Python 3.14 is about 29% faster; this is not a hosted result.

All five scheduling cases pass in both runtimes. All 78 related
isolation/ratchet/promotion/admission cases pass. The existing conftest coverage
profile passes 124 cases with two pre-existing conditional skips at 91%
combined coverage and unchanged 85/75 floors. Strict typing also exposed
inherited fixture annotations and dynamic-module attributes; behavior-preserving
annotations retain the actual monkeypatch targets rather than adding ignores.

Native timing is focused scheduling acceptance, not hosted speedup, a passing
canonical gate, exhaustive 85% aggregate/75% per-file coverage, completion,
or release authority. The exact successor hosted SHA must be measured with
the existing immutable ci-run-summary timing owner before claiming hosted
throughput improvement. Serving processes, listeners, deployment artifacts,
ZDD, and rollback authority are unchanged.

## Dependent boundary acceptance (2026-10-13)

Exact successor 6be0e00 fails the early hosted owner with three failures and
1164 passes in 49.03 seconds: stale EE interpreter/input-copy/lock expectations
and a native test parameter colliding with Playwright's context fixture.
Local canonical Python 3.11 reproduces all three failures. Boundary conformance
retains every original dependency/collection assertion, checks the current
public interpreter and smoke copies, and verifies bytes for all six inputs.
Renaming the test parameter leaves its values and assertions unchanged.

The complete current native owner then passes 1167 cases plus fresh help in
46.3 observed seconds, preserving the 90/60 bounds and original grouping.
These source receipts do not repair hosted evidence retroactively or establish
installed-image, full-gate or deployment acceptance. Once-per-publication
admission of this same maintained owner is a separate pipeline enhancement.

## Mandatory publication admission (2026-10-13)

The new internal runtime-publication owner reuses the complete existing
compatibility cohort through the existing watched runner. Normal batch
publication runs it exactly once after threshold admission and before Git
transport. Rejected thresholds incur no replay. All existing forced and seven
legacy NV transports share the same owner and explicitly pin execution mode;
they cannot turn the check into a validation-only plan. The normal installed
pre-push hook remains unchanged and does not duplicate this owner.

Automatic discovery uses the existing resource-arbiter namespace and canonical
ci-shards/python-3.11 environment. An explicit existing environment is also
supported. Missing/wrong-minor interpreters and missing Playwright fixtures
fail closed before observation or transport. No environment, package, resolver,
installer or service is created. Explicit recursive environment/no-sync and
no-download settings prevent outer .venv hints from selecting Python 3.14.

The original two-worker cohort, one-worker fresh help, fatal warnings, 90-second
maximum, 60-second quiet limit and 10-second heartbeat are unchanged. Retention
honours the existing caller setting. Source rejection keeps failure evidence;
transport failure never records success. Plan mode mutates nothing and never
grants gate or release authority.

Root's initial matrix has 28 failures and two unchanged nonpublication controls.
The final 35-case native Make/observer matrix passes on both Python versions,
including normal, forced/NV, child/transport/identity refusals and the real
contract validator. Root's actual automatic environment selection runs all
1167 existing cases plus fresh help successfully in 45.3 observed seconds.
Strict typing, Ruff, the 227-target contract, documented plan example and
zero-new-clone staged admission pass. Fixture-source final-LF conformance and
missing retention defaults were repaired without changing assertions.

The maintained [loadgroup contract](https://pytest-xdist.readthedocs.io/en/stable/distribution.html)
and [2018 session-fixture report](https://github.com/pytest-dev/pytest-xdist/issues/271)
support retaining the existing isolation rather than adding workers or a runner.
The [GitHub runtime discussion](https://github.com/orgs/community/discussions/25631)
supports keeping bounded, attributable execution before expensive publication.
These are source/control-plane receipts, not hosted speed, full gate, installed
controller or deployment proof. ZDD and the unchanged 85/75 requirements remain;
rollback is the isolated Make/contract/test/documentation commit.

## Concurrent proof publication (2026-10-13)

A real 44-second repair replay read the ledger before executing evidence.
Concurrent terminal-workflow observation added two runs, then the stale repair
write removed both (56 recorded runs became 54). Sequential native observations
restored them, but that recovery did not correct the shared writer race.

The existing ledger owner now reuses the pinned FileLock library already used
by item progress and CI receipts. All three native writers share one private,
stable sidecar and a two-second acquisition bound. GitHub fetches, evidence
execution and ancestry checks remain outside the short transaction. Inside it,
the writer reloads current state and publishes through the original atomic
replace owner. Repairs merge only their selected receipts; a changed selected
failure refuses stale evidence instead of overwriting newer failure, success or
repair data. Newly arriving failures remain open, even during an all-open repair.

Root's isolated canonical RED has eleven failures and four unchanged error
controls. The complete original/new ledger suite passes 82 cases on Python 3.11
and 3.14. Canonical coverage measures 93.11% combined, including 94.33% statements
and 90.15% branches, with the unchanged 85/75 floors and one production file
actually audited. Original immutable run IDs, exact-SHA ancestry, two-MiB size
limit, private permissions and atomic-file cleanup remain unchanged.

The complete race suite joins the existing early owner, retaining its two-worker
cohort, separate fresh help, 90/60 bounds and fatal warnings. A failing-first
promotion regression pins that complete wiring; no second runner is introduced.
Root's actual expanded owner then passes all 1182 cases plus fresh help in 47.1
observed seconds, under the same 90/60 limits. All 60 mapping/admission cases and
the 58-family manifest pass, alongside unchanged resource-ownership admission.
The maintained [FileLock guidance](https://py-filelock.readthedocs.io/en/latest/)
uses a separate lock file, and the long-lived
[2018 shared-JSON user report](https://stackoverflow.com/questions/50485037/loading-json-from-a-locked-file)
describes rereading current contents while holding the update lock. The regression
also refuses symlinked locks and preserves the sidecar identity between writers.

This repairs automatic proof-state ownership, not deployment acceptance. No
serving process, release closure, master promotion or image is changed. Full gate
and exact-SHA hosted acceptance remain pending. Rollback reverts the isolated
ledger, tests and early-owner wiring together, then re-observes native evidence;
it never restores an old JSON snapshot over newer observations.

## Interpreter-specific safe YAML bootstrap (2026-10-13)

Build 38423157704 at exact 9f677f92ce372b4aacb4185606d37d7fd4dfa8e7
passes all 1182 main-cohort cases on Python 3.12 in 82.74 pytest seconds,
then reaches the unchanged 90-second observed deadline during fresh help.
The prior safe-parser selection was ineffective: both root and unit pytest
bootstraps removed LibYAML classes unconditionally despite documenting a
Python 3.14-only Ansible workaround. Scope both removals to that interpreter.
Supported 3.11/3.12 retain installed safe loaders; absent LibYAML still falls
back safely, and 3.14 retains its original workaround.

A natural installed-backend regression fails first on Python 3.11 while all
48 existing safe-reader controls pass. The final 53-case reader/bootstrap
suite passes on 3.11, retaining malformed/unsafe-tag rejection, fresh nested
results, mapping validation, and original on-key handling. Four version
controls execute the actual unit bootstrap and verify collection-path and
temporary-name cleanup. The expanded existing coverage profile audits both
changed bootstraps: 91% combined; root 91%, unit 94%, with each owner's line
and branch floors passing unchanged 85/75 admission. It passes 192 cases
with one pre-existing canonical-checkout conditional skip, not a new waiver.
Ruff and strict typing pass. Rollback restores both bootstrap guards together;
this source repair does not close S98.4 or supply hosted/full-gate/deployment proof.

## One native owner for the repository fixture inventory (2026-10-13)

The real duration profile at 9f677f92 shows the same module-scoped repository
inventory being constructed on two workers: 7.06 and 6.62 setup seconds.
The existing combined source audit already parses each unique file once and
releases its AST. No parser, persistent cache or allocator is needed. Give
only its two consumers the same native pytest-source-inventory xdist_group.
All input discovery, plugin fixture names, collision/binding assertions,
negative controls and the rest of the two-worker schedule remain unchanged.

The grouping regression genuinely fails first. All 130 parser, fixture,
scheduling and promotion cases then pass on both installed Python versions.
An actual full-owner trace contains 1184 main cases plus fresh help, each
with exactly one START/FINISH, zero failure reports and no unfinished node.
Both inventory consumers execute on gw1/PID 29820; setup costs 7.65s once,
and the second consumer reuses the module fixture. Fresh help runs later in
PID 32156. The candidate whole-owner replay takes 41.1 observed seconds at
the original 90/60 bounds; it includes the separately tracked singleton
help change and is not an isolated grouping benchmark or hosted speed claim.

The maintained [loadgroup contract](https://pytest-xdist.readthedocs.io/en/stable/distribution.html)
guarantees same-group worker ownership while leaving unmarked tests normally
scheduled. The existing [session-fixture user report](https://github.com/pytest-dev/pytest-xdist/issues/271)
explains separate worker sessions, supporting measurement of repeated setup.
Rollback reverts the two marks and their regression together. No serving
process, ZDD action, release closure or full-gate requirement is changed.

## Fresh singleton without a redundant worker (2026-10-13)

Only the second existing test-files child changes to -n 0 with maxprocesses 1.
It still starts a separate interpreter after the complete two-worker cohort,
owns a distinct basetemp and Ansible temporary path, loads all installed
plugins/autouse fixtures, and runs the unchanged duplicate-help assertion.
It does not depend on worker_id, workerinput or PYTEST_XDIST_WORKER. Removing
the extra worker avoids a second interpreter and collection for one case;
it does not merge the help test into the preceding mutable-state session.

The existing real Make capture test fails first on the new serial expectation.
Its complete roster now also includes the already-required ledger concurrency
suite rather than the stale pre-addition roster. Two child PIDs, full/help
order, complete selections, limits, warning-fatal behavior and both failure
exits remain asserted. Existing native shared-file, exactly-once and fatal
warning controls still use the original one/two-worker matrix. Dependent
promotion pins conform to the new exact help flag and retain 90/60 bounds.

An isolated native comparison passes the exact help node in 0.79s serial
versus 1.75s with one worker. The complete candidate replay passes 1184 main
cases and help in 41.1 observed seconds; trace PID 32156 is distinct from
both cohort worker PIDs. No tests, plugins, warning policies, deadlines or
coverage requirements are disabled. These local receipts do not establish
hosted Python 3.12 improvement or authorize any backlog closure.

The official [distribution contract](https://pytest-xdist.readthedocs.io/en/stable/distribution.html)
supports native -n 0 and worker ceilings. The documented
[worker architecture](https://pytest-xdist.readthedocs.io/en/stable/how-it-works.html)
explains per-worker interpreters and collection. A long-lived
[2018 worker-startup report](https://github.com/pytest-dev/pytest-xdist/issues/346)
motivates measuring startup rather than blindly adding workers; its 88-core
case is not asserted to reproduce Gludd's two-worker issue. Rollback restores
the second child's worker flag and exact pins together. No serving runtime,
ZDD switch, installed image or release authority changes.

Final source acceptance includes all 169 parser/bootstrap, fixture, scheduling,
promotion and publication cases on both installed Python versions. The expanded
actual Python 3.14 owner passes 1188 main cases in 43.87 pytest seconds and
fresh help in 0.83s, 46.8s observed total under unchanged 90/60 bounds. Its
run-isolated trace has exactly 1189 starts/finishes, zero failure reports and
no unfinished node. This local source receipt remains separate from hosted
Python 3.12 acceptance. All 227 target contracts, 58 failure families, native
Make parity/help, Ruff, strict typing and task integrity pass; no task is ticked.

## Fixed initial-path collection throughput (2026-10-13)

Hosted SHA 768eaf6e759215c4bf7735930b917f69e9ab484c, Build 38477108604,
Python 3.12 job 115570429529 produced its first visible pytest header after
39.3 seconds. All 1,566 main nodes passed in 86.61 pytest seconds, but the
unchanged observer timed out at 90.5 seconds before the fresh help child.
The 3.11 lane passed the same nodes in 78.89 seconds and completed help.
The log confirms rfc3987 1.3.8 was installed; no optional-format failure or
failing node was observed. These hosted failures remain historical evidence.

Research used installed pytest 9.1.1, Python's existing
[cProfile](https://docs.python.org/3.14/library/profile.html) and importtime
tools rather than adding a collector or cache. A cold, collect-only native
profile kept all 1,566 nodes: 35.123 seconds total, with Package.collect
accounting for 28.531 cumulative seconds. It recorded 174,720 standard
pytest_collect_file calls, 172,529 pytest_ignore_collect calls and 699,787
stat calls. These are profiler call counts; generator resumptions are not
distinct package enumerations. A separate importtime observation finished
in 14.598 seconds and had no individual cumulative import above 0.5 seconds.
This identifies repeated parent/sibling collection work locally, not the
entire hosted 39.3-second interval as import overhead. Header timing can be
buffered. Profile data is diagnostic, not a gate or benchmark receipt.

The supported [pytest ignore-glob option](https://docs.pytest.org/en/stable/example/pythoncollection.html)
is scoped only to the fixed main admission's explicit file/node selectors:
--ignore-glob=tests/*. In the
[installed-version Package collector](https://github.com/pytest-dev/pytest/blob/9.1.1/src/_pytest/python.py),
explicit initial files and directories leading to explicit paths bypass
ignore hooks. The flag therefore avoids constructing never-selected sibling
collectors while retaining the native collectors, all original admitted
files/nodes, their parent packages, conftests, fixture lifecycles, assertion
rewriting and autoloaded plugins. Directory selectors are prohibited by the
new cheap owner control. The flag is absent from generic test-files, full
suite defaults and the sequential fresh help child. It is not deselection.

Two long-lived upstream user reports informed the controls. An
[August 2018 import-rewrite profile](https://github.com/pytest-dev/pytest/issues/3918)
shows why startup attribution must be measured; assertion rewriting remains
enabled here. A
[September 2020 package-collection discussion](https://github.com/pytest-dev/pytest/issues/7777)
describes duplicate parent/package work and package-scope fixture implications.
Those historical reports motivate verification, not a claim that their old
pytest implementations exactly reproduce the installed version. Whole-env
[uv bytecode compilation](https://docs.astral.sh/uv/reference/settings/#compile-bytecode)
was rejected: it scans all site-packages and ignores compilation errors,
and neither the profile nor the disk budget supports that larger mutation.

Artifact failing-first execution exposed the absent flag. After conforming
the new missing-path control to native xdist's nonzero/no-tests outcome,
all four behavioral controls passed in 10.85 seconds. They compare complete
expanded selected IDs/order/marks, plugin distributions and fixture signatures;
execute parent/package/plugin fixtures and package setup/teardown; retain an
explicit outside-tests file and exact class node; and prove warning, assertion
and missing-file failures cannot be hidden. Forty-eight unselected siblings
are constructed by the original collector but not by the filtered collector.
Only the cheap actual-owner/full-roster/registration control joins early
admission. All four native subprocess cases remain mandatory full-suite tests.

One serial native Python 3.14 comparison retained all 1,566 original main nodes
plus one fresh help node at identical source-content identity
738cc938da5c5ec275bce79554ca085cbd769a72dc1674f4c22b54a70bf246e6.
Original: 68.09 main + 0.98 help pytest seconds, 71.886 observed seconds.
Filtered: 63.95 main + 0.93 help, 67.913 observed seconds. Both exited zero
under 90-total/60-quiet limits with two main workers and a distinct later help
process. The observed difference is 3.973 seconds (5.5%), a single local
comparison, not a hosted improvement or a guaranteed cold-start saving.
Complete original node order/marks, plugin and fixture signatures match.

The prototype snapshot used an obsolete conftest-path map and wrote empty
path lists. Those raw receipts DO NOT prove conftest-path equality. Real
parent/package fixture execution and fixture-signature equality were proven.
The final tracked diagnostic fixture reads the actual _conftest_plugins owner,
and its portable regression positively requires both real conftest paths;
Root must replay that strengthened control after adoption. No historical
observation JSON was edited. This comparison also predates Root's 70ef3b8
ledger addendum adding 38 cases and the new one-case structural promotion;
it is not a final current-source roster replay. Final portable artifacts
passed Ruff and strict typing, but require their own genuine RED/GREEN replay.

Research receipts and immutable adoption patches live under
/tmp/gludd-v012-admission-cold-start; producer-owned native logs are
.gate-logs/v012-admission-cold-start-whole-roster-original-314.log and
.gate-logs/v012-admission-cold-start-whole-roster-proposed-314.log.
The observer owns its existing .gate-logs/observed evidence. No new runner,
daemon, allocator, persistent cache, install or environment is adopted.
The owner remains bounded at two concurrent workers; resource sampling is
diagnostic, not a global allocation lease. Root coordinates the unchanged
eight-worker ceiling. ZDD/serving processes, 85/75 coverage, full gate,
promotion and exact-SHA hosted obligations remain unchanged. Hosted successor
timing must come from the existing immutable ci-run-summary owner before
claiming hosted improvement. Rollback reverts the one flag, its additive
structural promotion and portable regression fixture together.

Canonical adoption reproduces one missing-flag failure before implementation.
The strengthened native controls now positively verify both conftest paths;
all 106 selection, scheduling, promotion and publication cases pass on Python
3.11 and 3.14. Seventeen additive conformance hunks preserve original assertions
while pinning the scoped flag, extra structural selector and failure family.
The diagnostic-only profile config/coverage_admission_cold_start.ini initially
exposes 75% aggregate branch coverage and a parent no-data warning. Its added
opt-in/absent-manager test executes both missing boundaries rather than exempting
the warning or lowering policy. All 11 controls then pass with 100% fixture
lines and branches, zero exclusions and unchanged 85/75 floors; native audit
.gate-logs/coverage-20261013T230959Z.json records that result. Ruff, strict typing,
233 target contracts, 69 failure families, Make parity and task registration pass.
Final current-source admission and hosted successor proof remain pending.

The expanded native Python 3.14 admission now passes all 1605 main cases in
64.24 seconds and the separate fresh help case in 0.93 seconds, 68.2 observed
seconds at unchanged 90/60 bounds. Its producer-owned receipt is
.gate-logs/v012-initial-path-current314.log. The final typed boundary replay
preserves the explicit None-return assertions through correctly typed callback
interfaces; all 11 controls and 100% fixture lines/branches pass again with no
coverage warning (.gate-logs/coverage-20261013T231244Z.json). Strict typing is
green on all four changed Python files. This is local source acceptance,
not successor hosted timing, a complete gate, deployment acceptance or closure.

## Native bounded collection reporting (2026-10-14)

Research selects the maintained `-o verbosity_test_cases=-2` override for the
existing shared `--co -q` collection invocation. Keep the inherited `-v` and
every other addopts setting: their global verbosity remains zero. The official
[fine-grained verbosity documentation](https://docs.pytest.org/en/stable/how-to/output.html#fine-grained-verbosity)
supports controlling item detail independently. Installed pytest 9.1.1's
[terminal owner](https://github.com/pytest-dev/pytest/blob/9.1.1/src/_pytest/terminal.py)
prints sorted per-file selected-item counts when this setting is below -1;
the initial collected/selected/deselected report and final native count summary
use the unchanged global verbosity. Collection errors still follow the original
error-reporting path, traceback policy and nonzero exit. Increasing global
quietness far enough to obtain file counts instead suppresses the final summary.

The long-lived [February 2022 collect-only user report](https://github.com/pytest-dev/pytest/issues/9704)
documents the distinction between exact IDs at `-q` and file counts at `-qq`,
including the summary trailer. It supports using the separate maintained setting
rather than treating quieter console output as an exact inventory format.
Compact stdout is not a node-ID artifact: the existing trace records only
`len(session.items)` at collection finish. Native `verbosity_test_cases=-1`
provides the exact selected IDs when an inventory diagnostic is required.
Acceptance must compare the full native selected inventory and preserve count,
deselection, warning/error and failure-exit behavior; file counts alone cannot
prove inventory equivalence. These are verified source semantics, not completed
behavioral acceptance or a measured collection/hosted speed improvement.

The change needs no collector, reporter, cache, environment, installer or daemon.
Keep the collection lock, full tests-directory selection, plugin loading,
observer limits, warning policy, and 85% aggregate/75% per-file coverage floors.
Serving processes and ZDD authority are unchanged. Rollback removes only this
shared invocation's output override and its associated controls; preserve all
historical logs. Full gate and exact-SHA hosted proof remain required for closure.

Two actual-Make routing controls fail first. Four native pytest comparisons
verify all 240 selected IDs, marks, fixture dependencies and parameter identities
byte-equivalently, plus native totals/deselection, import errors, fatal warnings,
missing-file errors, exit codes and existing trace counts. On the success
fixture, compact stdout is less than one third of the original output. These
test-owned snapshots are diagnostic assertions, not a production inventory or
proof cache. All 83 related cases pass on Python 3.11 and 3.14; typing, Ruff, 234 target
contracts and Make integrity pass. Whole-repository log-volume measurement and
successor hosted/full-gate/deployment proof remain pending; no speed gain is
inferred from the output reduction.

## One native shell for help text (2026-10-15)

The 6d79fcf hosted Python 3.12 admission passes its main cohort in 82.91 seconds
but reaches the unchanged 90.6-second observed deadline during the separate fresh
help invocation. Native Python 3.14 duration attribution identifies a 1.67-second
real help audit in the main cohort; the separate fresh dry-run call is 0.03 seconds.
Optimize that real help recipe, not the fresh-process isolation policy.

GNU Make documents a normally separate
[subshell for each recipe line](https://www.gnu.org/software/make/manual/html_node/Execution.html).
Use one help-local continued command: literal `printf '%s\n'` arguments before
and after the unchanged native index invocation, joined with `&&`. Do not enable
global [`.ONESHELL`](https://www.gnu.org/software/make/manual/html_node/One-Shell.html)
or expand `$(info)` during Make evaluation. A long-lived
[2017 echo-versus-info practitioner discussion](https://stackoverflow.com/questions/47367589/printing-in-makefiles-echo-vs-info)
also distinguishes shell execution from Make expansion and their output ordering.
The native index retains its exact argv, partial stdout and nonzero failure;
a failure cannot print the footer or become success through another printf.

Failing-first tests observe 501 original recipe-shell invocations. The replacement
uses one, retains all 500 static output lines, and handles percent signs, quotes,
Unicode and shell/Make metacharacters as literal fixture data. Independent native
`cmp` succeeds for all 88,323 bytes and 1,566 ordered output lines against the
retained original c478f186 help output, including provider-smoke's original
unindented, collapsed whitespace. All 470 original static inventory names match.
The help audit, target-contract reader and atomic target editor understand both
legacy echo and continued printf representations. Old/new duplicate checks remain
nonvacuous; native editor round-trips retain continuation and index-failure behavior.
Legacy editor assertions, atomic replacement/refusal and cleanup behavior remain;
its pre-existing Ruff/type diagnostics receive mechanical fixes without ignores.

Same-owner local Python 3.11 observation, one run per variant: original 1.448
seconds, replacement 0.086 seconds, a 1.362-second reduction for this help call.
Raw logs and immutable observer receipts are retained under
`/tmp/gludd-v012-help-c478-{before-1792050627537,after-1792051159088}*`;
the complete native inventory fixture and unchanged-floor coverage inputs are
`/tmp/gludd-v012-native-help-throughput-{inventory.py,coverage.ini}`.
This is not a whole-admission or hosted speed measurement: the additional regression
controls have their own execution cost. Fresh child selection, native Make argv,
30-second dry-run timeout, plugins, warning-fatal behavior, workers, 90/60/2850
bounds, 85/75 coverage and ZDD are unchanged. Root owns combined source review,
canonical gate and exact-successor-SHA hosted proof. Rollback reverts this isolated
recipe/consumer/test/documentation slice together, retaining historical receipts.

The complete existing help/deep-audit/editor/contract suites plus additive controls
pass 101 cases on the locked Python 3.11 environment. Native subprocess/branch
coverage measures 93% combined: help reader 100%, contract checker 97%, atomic
editor 89%; all three pass unchanged 85/75 admission. The first expanded coverage
run correctly refused the editor's previously untested CLI paths at 72%; additive
legacy-exit/atomic-refusal controls close that gap without excluding code. Ruff,
strict typing, all 235 native target contracts, duplicate-target and Make parity
checks pass. Receipts are diagnostic focused acceptance, not a full gate.

## Preserve the original admission boundary (2026-10-15)

The frozen f0a38e7 combined admission reports three failures and 1780 passes in
74.29 seconds: two original target-help audits and the native-format safe-plan
help assertion still assumed echo source syntax. Their representation-only
conformance reuses the existing inventory reader, retains descriptions/internal
entry detection, and changes neither the 70% canary nor orphan allowances or the
native-format contract. All three failures are reproduced before repair.

One expanded native all-failure batch covers 1024 cases across the retained
consumer inventory: 1021 pass and three additional echo-only help assertions fail
in 201.45 seconds. Their representation-only conformance preserves the Git
PHONY fallback, exactly-one promotion help entry and complete hygiene description
and delegation checks. The two pure TUI helpers run; actual persistent TUI/LLM
sessions are outside this source-regression batch, with no release-policy change.

Native collection identifies 49 help-audit cases: eight original cases plus 41
new exhaustive controls, correcting an inferred count of 42 new cases. Keep the
original eight-case audit byte-identical to the retained pre-H Q worktree and
move only those 41 new controls into `tests/unit/test_make_help_throughput.py`.
Every assertion stays mandatory in full-suite/focused acceptance; the original
admission selector, fresh help child, nodes, warning/plugins/workers and 90/60/2850
limits are unchanged. The new path is added inline to the existing S98.4 scope.
No production code, runner, cache, admission roster or promotion manifest changes.
This is a test-boundary/conformance repair, not a measured speed improvement or
full-gate/hosted/deployment acceptance; Root must measure the actual combined owner.

## Keep execution detail without redundant per-case reporting (2026-10-15)

Build 38775452145 on `e914eeeb646d919e79fff4d6fdd30d6905bda479`
passes Python 3.11 admission but times out on Python 3.12 at 90.5 seconds. Its
last test-call reports PASS at 100%; the terminal summary and separate fresh-help
child never finish. Call-phase progress is not a completed admission result.
The test configuration and `test-files` each add `-v`; admission's single `-q`
therefore still emits verbose per-node start and result lines.

Apply pytest's native `-o verbosity_test_cases=-2` only to the main admission
cohort. The [fine-grained verbosity option](https://docs.pytest.org/en/stable/how-to/output.html#fine-grained-verbosity)
separates execution progress from assertion detail. The pinned
[pytest 9.1.1 terminal implementation](https://github.com/pytest-dev/pytest/blob/9.1.1/src/_pytest/terminal.py)
uses compact result characters while retaining the unchanged global verbosity,
failure diagnostics and final summary. Do not replace `-q` with `-qq`, change
generic `test-files` output, or apply the override to the fresh-help child.

The longstanding [2022 output/inventory discussion](https://github.com/pytest-dev/pytest/issues/9704)
supports keeping console presentation separate from exact test identity; it does
not establish a runtime speedup. Existing xdist trace START/FINISH/REPORT events
remain the node-level diagnostic source. The existing observer counts output
bytes, including result characters without newlines, as activity.

Keep the complete ordered selector roster, real plugins and fixtures, two-worker
loadgroup ownership, isolated help process, fatal warnings, 90-second total and
60-second quiet limits. No runner, cache, environment, daemon, coverage exemption
or release authority is added. ZDD and 85/75 coverage remain unchanged. Rollback
removes only this cohort's output override and corresponding controls; retain
historical failure evidence. Native behavioral acceptance and complete hosted
timing, not reduced output volume alone, determine whether this clears the budget.

The actual-Make flag requirement fails before the change. Native old/new fixture
comparisons explicitly load the project configuration and retain global verbosity
1. Success output falls from 4,225 to 1,031 bytes; exact expanded nodes, marks,
plugins, fixture signatures and trace outcomes match. Assertion, warning, setup,
import and missing-node cases retain their native nonzero exits and diagnostics.
A separate real observer control retains all 20 flushed, newline-free bytes over
1.2 seconds without tripping its 0.4-second quiet limit. These are behavioral and
output-volume results, not a measured whole-admission or hosted speedup.

## Preserve a failed bounded admission while profiling (2026-10-18)

Candidate `33fd37fc929e66f3873b2a277faf3044309cb4f5` fails normal guarded
publication admission at 90.5 seconds. Its canonical Python 3.11 main child
passes all 1824 cases in 79.81 seconds and fresh help passes, but the second
separate review control is interrupted. Git transport never starts. Retain
`.gate-logs/observed/batch-push-runtime-admission/batch-push-runtime-admission-37974.log`;
a source approval, raw unbounded admission or focused pass cannot clear it.

The existing observer runs one diagnostic with native pytest `--durations=30`.
Its explicitly non-authoritative 180-second diagnostic cap does not alter the
actual 90-second publisher or 60-second quiet limit. It completes in 84.0
observed seconds: main 70.98 seconds, fresh help 0.93, final controls 8.03.
The largest reported setup is the already shared repository source inventory
at 9.08 seconds. Each final control spends about 3.6 seconds in its real call.
Keep the failed proof; a later faster run without a changed failing input is
not a repair or permission to reseal and retry expensive proof.

User reports provide profiling hypotheses, not a Gludd diagnosis. The
[longstanding 2019 large-suite discussion](https://github.com/pytest-dev/pytest/issues/5516)
reports collection costs associated with fixture matching. The
[August 2026 pytest 9.1.1 regression report](https://github.com/pytest-dev/pytest/issues/14942)
reproduces a slowdown with many class fixtures sharing names. Its
[merged September storage repair](https://github.com/pytest-dev/pytest/pull/14984)
also discusses compatibility with plugins using private fixture structures.
These reports support measuring actual call stacks before changing dependencies.
Do not infer that Gludd has that shape, patch pytest internals, suppress plugin
loading, or discard audit inputs from an upstream report alone.

Use the existing standard-library profiler and native owners to isolate actual
work. Preserve all selectors, assertions, plugin and fixture context, three
child invocations, worker bounds, fatal warnings, 90/60/2850 bounds, ZDD and
85/75 coverage. No new proof cache, runner or release authority is introduced.
A concrete performance repair, fresh review and bounded native acceptance must
precede a successor candidate; canonical full-gate and exact-SHA hosted proof
remain required. Diagnostic log:
`.gate-logs/observed/v012-admission-diagnostic/admission-durations-33fd37f-20261018.log`.

The fresh standard-library profile attributes 4.872 seconds to parsing and
4.696 seconds to declaration traversal across all 4106 repository sources and
12 delegated plugin modules. Entry-point discovery takes 0.043 seconds, so a
provider cache or dependency change would address the wrong measured input.
Keep the parser, all source paths and every audit consumer.

The existing declaration owner now visits statement-bearing lists through
stdlib `ast.iter_fields`, including exception-handler and match-case bodies.
Arguments, aliases, keywords and patterns cannot contain Python declarations;
their payloads no longer enter the pending statement walk. Field/list order,
nested declaration owners, late imports, plugin declarations, source locations,
freshness and release of each AST stay intact. No separate scanner is added.

One additive work-bound control fails first on the old argument traversal;
nine compound-statement/order/owner parity controls already pass there.
The complete 33-case file then passes through the original coverage profile at
96% combined coverage, with the unchanged 85/75 floors. Strict typing and Ruff
pass without suppressions. All original assertions remain unchanged.

One same-instrumentation local profile per variant reports declaration work
4.696 to 2.240 seconds and complete audit work 10.280 to 7.675 seconds.
The same 4106 source paths, 12 plugin modules and 41 external fixture names
produce zero collisions and zero unsupported bindings. These are instrumented
local owner measurements, not whole-admission, hosted or release acceptance.
Retain both profiles under `v012-inventory-cprofile`, including
`inventory-profile-33fd37f-20261018.log` and
`inventory-profile-statements-20261018.log`. The actual bounded publication
owner, independent successor review, full gate and exact-SHA CI remain pending.

## Batch only adjacent native Git metadata (2026-10-18)

The two unchanged native review controls call `repository_state_id` 63 times
in the measured tail profile. Preserve all calls and their independent
authority boundaries. Inside each call only, one native
`rev-parse --show-toplevel --show-object-format HEAD` replaces three adjacent
metadata processes. Existing index-stage and complete worktree-content reads
remain fresh. The digest version, object IDs, bookkeeping options, symlinks,
deletions, untracked files and submodule behavior remain unchanged.

The existing Git byte reader remains the sole command owner. Right-splitting
the fixed trailing fields preserves embedded newline/Unicode root paths.
Malformed or incomplete metadata fails before content reads; native Git failures
still propagate. No current-state result is cached, and no preparation,
authenticated source validation or native child boundary is removed.

The [Git manual](https://git-scm.com/docs/git-rev-parse) documents native
metadata and revision parsing. A
[2016 practitioner range discussion](https://stackoverflow.com/questions/38357020/get-range-of-commits-in-same-branch-using-git-rev-parse)
illustrates that revision expressions can expand to multiple object IDs.
This change accepts only fixed options and literal `HEAD`; named-ref verification
and untrusted revision handling are unchanged. The forum is context for that
boundary, not evidence of Gludd timing.

Four native SHA-1/SHA-256 worktree/index query-count cases fail first.
After the change, all 177 complete attestation, reviewed-input and receipt-builder
cases pass with warnings fatal. Focused native branch coverage is 95% with
unchanged 85/75 floors. New controls preserve known-content digest equivalence,
both bookkeeping/head options, source/index/HEAD mutations, malformed output,
root delimiters and native unborn-HEAD refusal. Every original assertion remains.
The standard subprocess import is explicitly re-exported for its existing typed
test consumer; the new refusal regex is raw without changing its pattern.

One same-instrumentation tail profile per variant observes 699 to 573
`subprocess.run` calls, with all 63 state checks, 30 cleanliness checks and
13 input preparations retained. Profiled test-body time changes from 7.860 to
6.749 seconds; these local diagnostic results are not whole-admission or hosted
throughput proof. Retain `review-tail-profile-nativepath-33fd37f-20261018.log`
and `review-tail-profile-batched-20261018.log` beneath
`.gate-logs/observed/v012-review-tail-cprofile/`. Actual bounded admission,
source review, complete canonical gate and exact-SHA hosted evidence remain
required before promotion. ZDD, cleanup authority and all release gates remain.

The cheap native query-count guard is promoted additively into the existing
first compatibility cohort. Its one exact selector expands all four native
SHA-1/SHA-256 worktree/index cases once; the full 34-case file is not added to
that bounded cohort. All 72 prior selectors and 96 prior families remain.
The native manifest now has 97 families with the same 21 phases, 2850-second
ceiling, 90/60 owner bounds and full-gate requirement. The shared literal roster
receives the identical selector at the same position.

Twelve new routing controls fail before that wiring. All 154 complete promotion,
scheduling and cold-start controls then pass with warnings fatal, including ten
mutations rejecting missing, partial, widened, duplicate, misplaced, wrongly
owned or over-budget routing. All 243 target contracts, duplicate/parity/help
checks, strict test typing and Ruff pass. These are focused routing results;
the complete bounded behavioral example and final release proof remain pending.

## Preserve validated Makefile bytes during composition (2026-10-18)

The reviewed candidate 51669b0 passes normal publication admission in 76.4
observed seconds and its 36-file focus passes 1330 cases with two existing skips.
Required Build 39097125106 nevertheless times out at the unchanged 90-second
boundary on both hosted Python versions. Its 1838-case main children pass in
78.32 seconds on 3.11 and 84.16 seconds on 3.12; the remaining children do not
finish. Molecule 39097125202 and presentation 39097125234 pass. These successful
workflows do not clear the Build failure. The owned local canonical gate is
stopped, with no survivors, before source repair; retain its interrupted log
`.gate-logs/gate-20261018042952-72c496e1.log` and all immutable candidate refs.

A complete diagnostic takes 90.8 seconds under an explicitly diagnostic-only
180-second cap. A separate standard-library cProfile run takes 106.4 seconds
under a diagnostic-only 240-second cap and retains all 1838 + 1 + 2 passes.
Neither clears the original 90/60 prerequisite. The latter run is retained in
`.gate-logs/observed/v012-cohort-cprofile/runtime-cprofile-nativepath-51669-layout-20261018.log`.
Worker profiles show repeated collection and native MCP topic sizing cost.
They measure the repaired Makefile reader at only 0.356 and 0.404 cumulative
seconds across 82 and 92 calls, respectively; its repair alone cannot establish
the required admission margin.

The existing Makefile reader validates each fragment, then rereads it while
composing. A real mutation after the validated read reproduces emission of an
unvalidated nested include. Retain each validated path/text pair only for that
public call and emit those same bytes. Both public readers open and validate
current files again on every invocation. Ordered includes, UTF-8/newline errors,
duplicate/nested include refusal and unsplit Makefiles keep their original
semantics. This is a per-file validated view, not an atomic snapshot of
independently changing sources. No persistent cache or new scanner is added.

Two new cases fail first and a path-reader control already passes. All 19
complete reader tests then pass, with native branch coverage at 95% under the
unchanged 85/75 floors. Both strict typing scopes pass. Every original assertion
is retained. The read-count contract reduces composition from 25 to 13 source
reads per call for the current twelve-fragment layout; no hosted timing benefit
is inferred from that count.

The long-lived [pytest collection discussion from 2017](https://github.com/pytest-dev/pytest/issues/2206)
and [package collection design discussion from 2020](https://github.com/pytest-dev/pytest/issues/7777)
describe costs and boundaries around filesystem hooks and package traversal.
The [native pytest 9.1.1 collection source](https://github.com/pytest-dev/pytest/blob/9.1.1/src/_pytest/main.py)
is the implementation reference. These are practitioner and upstream context,
not Gludd proof or permission to replace collection, suppress plugins, narrow
the roster or raise deadlines. Fresh bounded admission, independent source
review, the complete canonical gate and exact-SHA hosted evidence remain
required; all release tasks and promotion remain open.

## Size independent MCP topics with the native serializer (2026-10-18)

Whole-cohort worker profiling attributes 9.113 cumulative instrumented seconds
to two native MCP topic writes, including 170 topic dumps and 8.718 seconds in
chunk sizing. The original sizing algorithm serializes the growing mapping
repeatedly. Keep PyYAML 6.0.3 and the same SafeDumper, formatting options and
final per-shard serialization. Separate-document dump APIs do not implement
the required one-mapping-per-shard format.

Within the existing writer, exact built-in dict/list/scalar trees with no
shared containers can use additive native entry sizes. Inspect the value
graph once per invocation, using one container-identity set across all topics.
For this restricted subset, invoke the existing singleton topic dump once,
sum its existing splitlines count and fill the same greedy shard boundaries.
Serialize each final shard through the original unchanged native function.
No dump, size, AST, source or proof result is retained across calls.

Shared or recursive containers, custom mappings/types, altered native
representers or alias policy, and custom YAML path resolvers retain the
original complete-candidate algorithm. Native aliases remain enabled.
The [native representer source](https://github.com/yaml/pyyaml/blob/6.0.3/lib/yaml/representer.py)
explains its object-identity alias handling. The long-lived
[PyYAML user discussion opened in November 2017](https://github.com/yaml/pyyaml/issues/103)
documents requests and workarounds for suppressing aliases. Preserve those
semantics here through the native fallback. The discussion supplies context,
not evidence of an upstream defect causing Gludd timing.

Eight deterministic native call-budget controls fail before implementation;
46 original/parity/fallback controls already pass. After repair, all 137 cases
in the existing six-source coverage cohort pass. Aggregate native branch
coverage is 91%, the changed writer is 96%, and every measured file meets the
unchanged 85/75 floors. The complete 100 manifest/generator cases also pass
on installed Python 3.14, with warnings fatal; both strict typing scopes pass.
All original assertions remain. Additive controls compare exact shard and
manifest bytes, digests, boundaries, alias-sensitive packing, recursive values,
custom types/representers/mappings, fresh mutations and the actual live
generator corpus. No generated artifact is regenerated in the checkout.

For n independent topics and k output shards, the native topic-dump count is
n for planning and n+k for publication, with no growing private dict copies.
The focused generator writes take 0.38 and 0.23 seconds in the local repaired
run; the earlier two-worker diagnostic recorded 2.28 and 1.98 seconds.
Those different local invocations are not a controlled hosted comparison.
The unchanged bounded admission and exact-SHA hosted lanes remain necessary
before claiming that the original timeout prerequisite is repaired.

## Bound cyclic collection to each native source audit (2026-10-18)

A native diagnostic identifies repeated automatic cyclic-GC scans during the
complete source audit. With the same 4106 sources and 41 external fixture names,
the original observation takes 6.2775 seconds versus 3.8071 with per-source
suspension. Extending suspension across the entire inventory saves only another
0.0609 seconds and is rejected. Those same-process RSS peaks are not independent
memory measurements and do not establish memory parity.

The existing owner now reads each source before entering a small guarded helper.
Only ordinary GIL CPython suspends cyclic collection while native parsing,
declaration traversal and compact findings extraction run for that one source.
Its helper frame releases AST references before the wrapper restores the exact
entry enabled state in finally, including errors and BaseException cancellation.
Collection can resume between files. Other interpreters and free-threaded builds
retain the original behavior. No threshold, freeze, explicit collection, parent
links, result retention, inventory, plugin discovery or fixture ownership changes.

The [Python GC contract](https://docs.python.org/3.11/library/gc.html) distinguishes
cyclic collection from reference counting, and the
[standard-library timeit implementation](https://github.com/python/cpython/blob/3.11/Lib/timeit.py)
uses entry-state restoration. The long-lived
[temporary-GC context discussion](https://github.com/python/cpython/issues/75537)
and [nested disable/enable discussion](https://github.com/python/cpython/issues/85717)
motivate explicit state restoration; they do not prove this optimization safe or
fast for Gludd. The [free-threaded build documentation](https://docs.python.org/3.14/howto/free-threading-python.html)
documents Py_GIL_DISABLED and its different memory model, supporting the fallback.
The flag is interpreter-wide; the guarded region contains native static parsing
and extraction, not plugin execution or a whole pytest worker lifetime.

Seven new controls fail first with one passing disabled-input syntax control.
All 49 original/additive cases then pass on installed Python 3.11 and 3.14.
The existing native coverage profile measures 96% with unchanged 85/75 floors;
both strict typing scopes and Ruff pass. Controls preserve both findings and
fresh parsing, read errors outside the guard, exact True/False cleanup after
body mutation, SyntaxError, RuntimeError and KeyboardInterrupt. Real per-file
Module/ClassDef/FunctionDef weak references are dead before the next parse
without explicit collection; deliberate retention proves that check detects
a leak. The complete original assertions remain.

An additional independent-process diagnostic addresses the unresolved memory
question, using the actual complete inventory and tracking file-owned AST nodes.
Its sorted canonical finding and path digests match in both processes. Original
and guarded total audit-plus-cleanup times are 5.6044 and 3.8116 seconds; Darwin
peak RSS is 103333888 and 103481344 bytes. Both final AST reference counts and
diagnostic cleanup garbage counts are zero. Automatic collection counts change
from 13243/1203/102 to 5/0/0. These are bounded local observations, not a universal
memory guarantee or hosted throughput proof. An earlier diagnostic digest used
unordered frozenset repr; the retained canonical observations fix that diagnostic
serialization without changing audit output. Evidence is
/tmp/gludd-v012-ast-gc-memory-baseline-canonical.xml and
/tmp/gludd-v012-ast-gc-memory-guarded-canonical.xml.

The original 90/60 admission, full gate, exact-SHA hosted lanes, source review,
85/75 coverage, ZDD and promotion requirements remain. Rollback reverts this
isolated helper and its additive controls; it does not restore old observations.

## Add the cheap native repair guards to admission (2026-10-18)

The existing first compatibility cohort gains three exact selectors: both new
Makefile reader guards (three cases total) and the independent MCP topic dump
budget (eight cases). The already selected complete source-audit file owns all
sixteen GC controls without another selector. Four new manifest families record
those owners, preserving every prior selector and all 97 prior families in order.
The manifest now has 101 families, the same 21 phases and 2850-second ceiling.
The three child invocations, worker limits 2/1/1, fresh help/review context,
fatal warnings, 90/60 bounds and mandatory complete gate remain unchanged.

Five routing pins fail first. A complete replay additionally exposes the second
literal roster expectation; its update adds the same selectors and retains every
original assertion. All 171 promotion/scheduling/cold-start cases then pass,
including twelve new mutation controls for omitted, widened, partial, duplicate,
misplaced or wrongly owned selectors and changed budgets. Both strict test
typing scopes, Ruff, all 243 target contracts, duplicate/parity/help validation
and the native public promotion-check behavior pass.

A first observer incorrectly applies the runtime's 90-second cap to preflight
plus runtime together. It retains 1865 main passes in 71.64 seconds, then times
out before the remaining children. This stricter wrapper result is not a failure
of the canonical runtime-only budget and is not relabelled as success. Retain
.gate-logs/observed/v012-support-admission/support-routing-cb311da-20261018.log.

The correctly scoped native runtime observer then passes all 1865 main cases,
one fresh help case and both final review controls in 74.5 observed seconds
under the original 90/60 bounds. Pytest child times are 62.68, 0.88 and 7.21
seconds. Standard native duration reporting is enabled for attribution; no
selection, plugin, fixture, assertion or warning policy is changed. The source
inventory setup is 5.06 seconds and both final controls take about 3.1 seconds.
Retain .gate-logs/observed/v012-native-runtime-admission/native-runtime-support-cb311da-20261018.log.

This is bounded local acceptance. The direct public proof-admission producer,
independent source review, frozen combined focus, complete canonical gate and
exact-SHA hosted proof remain required. No release task is closed, no failed
candidate evidence is replaced and no promotion or publication follows yet.

## Native absolute-glob admission support

Build run 39100824559 at 191f588 passed the complete Python3.11 gate. Python3.12
passed all 1865 main-cohort cases in 80.39 seconds and the fresh help case in
2.10 seconds, then hit the existing 90-second observer limit during the final
two-case child's collection (90.6 seconds observed). That failure remains in the
native ledger and blocks final proof. No full local gate was started for this SHA.

The existing first and third children now pass
`--ignore-glob=$(CURDIR)/tests/*`. Pytest receives the same absolute pattern it
previously derived for each sibling, while Make derives it once for the current
invocation. The fresh-help child and generic test owner retain their original
arguments. Native [pytest9.1.1 ignore handling](https://github.com/pytest-dev/pytest/blob/9.1.1/src/_pytest/main.py)
still owns all decisions; [CPython's abspath](https://github.com/python/cpython/blob/3.12/Lib/posixpath.py)
avoids its repeated cwd/join branch for this already absolute input. No collected
package is bypassed. The original relative fixture exclusion remains native.

Serial native collection diagnostics on 191f588 selected the same 1865 node IDs
in identical order: relative glob 9.24 seconds (10.3 observed), absolute glob
7.03 seconds (8.1 observed). Logs remain under
`.gate-logs/observed/v012-collection-ignore-diagnostic/`. Collection-only timing
is diagnostic; it establishes neither the three-child 90-second proof nor a
hosted throughput improvement.

The actual-Make native regression first fails because the glob is relative.
Additive controls check fewer native cwd resolutions with identical ignore
decisions, a fresh glob for each invocation directory, and rejection of relative,
foreign-root, widened, omitted and duplicated patterns in both affected children.
Existing native package/parent/plugin fixtures, exact selectors, fatal warnings,
all three children and their worker counts retain their original assertions.
Earlier literal flag expectations are conformed only to this absolute spelling.

The long-lived [pytest2206 discussion](https://github.com/pytest-dev/pytest/issues/2206)
records collection traversal and hook overhead reported by cloud-custodian users.
[Pytest7777](https://github.com/pytest-dev/pytest/issues/7777) records package
collection and fixture-scope concerns. Those reports motivate native options and
lifecycle controls; they supply no performance proof here. `--pyargs` is rejected:
filesystem fallback retains traversal, dotted packages retain the costly parent,
and bare leaf imports lose package setup/scope semantics. Relocating the global
fixture exclusion into a scoped conftest is also not an exact CLI replacement.

Focused validation passes all 103 native collection/routing cases, including the
12 additive cwd/boundary controls. The unchanged three-child observer passes
1865 main cases, one help case and both reviewed controls in 72.7 seconds, under
its original 90/60 limits. Main execution is 60.75 seconds; help is 0.92 and the
reviewed tail 7.11. This local support proof does not clear the failed hosted
Python3.12 lane or authorize milestone completion.

## Provision the current managed Python 3.12 patch through existing owners

The 191f588 failure reports Ubuntu system CPython 3.12.3, pytest 9.1.1 and
Ansible 2.21.5. That observation does not establish interpreter causation or the
distribution package's security-backport state. Keep the matrix minors 3.11 and
3.12. The 3.12 leg now uses the existing pinned setup-uv action with uv 0.13.1
and an exact Python 3.12.15 request; the 3.11 action and installation body retain
their previous behavior behind a mutually exclusive condition.

[CPython 3.12.15](https://www.python.org/downloads/release/python-31215/) was
released on September 30, 2026. Official 3.12 releases are now source-only;
[uv's managed distributions](https://docs.astral.sh/uv/concepts/python-versions/)
provide the existing binary acquisition route.
[uv 0.13.1](https://github.com/astral-sh/uv/releases/tag/0.13.1) is the current
release checked on October 18. The
[pinned setup-uv input contract](https://github.com/astral-sh/setup-uv/blob/fac544c07dec837d0ccb6301d7b5580bf5edae39/action.yml)
supports these inputs. Environment activation stays disabled.

The existing atomic dependency-profile manager owns venv creation, every locked
overlay, pip check and promotion. Its exact 3.12.15 request is forwarded to every
native venv/sync command. Native
[only-managed preference](https://docs.astral.sh/uv/reference/environment/#uv_python_preference)
prevents fallback to the runner's system interpreter. Acquisition is neither
offline nor download-disabled; the existing global UV_NO_SYNC remains 1 and the
profile manager retains its own explicit mutation setting. No new installer,
global local-tool update, dependency override or environment replacement is added.

The [uv base-interpreter replacement discussion from 2025](https://github.com/astral-sh/uv/issues/16252)
records environment failures after interpreter replacement. The
[standalone build discussion from 2024](https://github.com/astral-sh/python-build-standalone/issues/275)
records build/distribution differences. These user reports support retaining
actual runtime/build identity and avoiding silent shared-runtime replacement;
neither predicts Gludd performance. The local read-only inventory finds managed
3.12.13 and uv 0.11.17, with no observed 3.12.15. Local contract checks therefore
cannot establish actual acquisition or execution of the newly pinned runtime.

The two workflow/routing guards fail first while the exact native profile plan
already passes. After implementation, all 428 cases in the nine-file workflow,
profile, promotion, selection and runtime-boundary cohort pass with fatal
warnings. Controls reject old/floating uv, a missing/old patch, system fallback,
extra activation, overlapping branches, profile bypass and disabled acquisition.
Every native profile command retains the exact request, inherited preference,
locked overlays and original cleanup/promotion assertions.

One additive manifest family routes the managed-runtime guard through the already
selected complete workflow file before fan-out. All 101 earlier families remain;
the manifest has 102. Existing raw flag expectations are conformed only to the
absolute native glob spelling. All three children, workers 2/1/1, 90/60 bounds,
21 phases, 2850-second ceiling, warning policy, ZDD and 85/75 coverage stay fixed.
Hosted acquisition, actual 3.12.15 identity, the unchanged bounded admission,
full exact-SHA gate and required workflows remain missing. No timeout repair,
throughput improvement or release completion is claimed from these source checks.

### Exact terminal diagnosis for 23d3424

Build run [39104090060](https://github.com/sandboxcom/gludd/actions/runs/39104090060)
on 23d3424064c84fe3193a5eeef685ac228f4dc68d is terminal failure.
Both required Python gate jobs pass, including actual managed Python3.12.15
execution; the native ledger resolves the previous runtime-admission family.
Seven test shards, every required platform/container/EE build and the separate
Molecule/Pages workflows pass. The remaining unit-3b failure is the unchanged
per-handler subagent audit at enforce-no-idle-monitoring.ts. Its typed fallback
lacks its own visible guard even though the public wrapper and shared handler
already call isSubagent(). The native original node reproduces one failure
in1.22s. Keep the failing owner and actual input, not another run-label diagnosis.

The repair adds the existing helper's real short circuit at the fallback
boundary and a native dependency-sentinel regression that cannot be masked by
the delegated helper's own guard. Both exact nodes join the first compatibility
child; all prior selectors, three children, workers2/1/1, warning policy,
absolute native globs, 90/60 bounds, 21 phases and2850-second ceiling remain.
The six targeted structural/runtime/routing controls fail before their owners
change. Existing full roster assertions are extended by exactly the two added
selectors and two families; omission/duplication/widening controls cover both.
No original assertion, depth exception, coverage floor or cleanup authority
is relaxed. The native plugin-manifest producer changes only that plugin's digest.
OpenCode must restart to load the changed entrypoint in an existing session;
source/runtime-harness acceptance does not establish a live-session restart.

The existing local gate .gate-logs/gate-20261018064002-4c64ffbc.log is
ABORTED through make gate-kill after hosted proof fails. The native producer
finishes at2026-10-18T08:10:47.545531+00:00; the cancellation reports no owned
survivors. Raw status/background/cancellation/CI observations remain under
/tmp/gludd-v012-failed-23d3424, and the original reviewed bundle and immutable
candidate refs remain unchanged. No terminal gate, final coverage, task closure,
master promotion or release publication is claimed for this candidate.

The following values come only from the existing immutable
make ci-run-summary RUN=39104090060 CI_RUN_SUMMARY_REPO=sandboxcom/gludd
CI_RUN_SUMMARY_VALIDATE_ONLY=0 CI_RUN_SUMMARY_SHARD_TIMINGS=1 owner.
The complete native stdout is retained in the historical directory above.
Its successful gate barrier is2026-10-18T06:41:00Z. Delay measures gate completion
to shard job start; test duration is the native Test step interval. All eight
shards are present. No interval is unknown in this report; the failed unit-3b
duration is explicitly censored. These diagnostics provide no gate/release
authority and do not establish a general throughput improvement.

| Shard | Exact job ID | Gate delay seconds | Test-step seconds | Native state | Censored |
| --- | --- | --- | --- | --- | --- |
| unit-1a1 | 117608539075 | 5133 | 364 | success | no |
| unit-1a2 | 117608539179 | 4786 | 608 | success | no |
| unit-1b | 117608539174 | 2958 | 2345 | success | no |
| unit-1d | 117608539082 | 3698 | 1044 | success | no |
| unit-2 | 117608539157 | 2 | 3657 | success | no |
| unit-3a | 117608539112 | 3 | 2916 | success | no |
| unit-3b | 117608539128 | 2558 | 2536 | failed | yes |
| other | 117608539099 | 2 | 2509 | success | no |
