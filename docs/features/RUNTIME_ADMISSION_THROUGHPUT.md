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
