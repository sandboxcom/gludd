# Pytest Parameter/Fixture Collision Guard

## Problem

Pytest treats a directly parametrized argument as a fixture override. That is
normally useful, but it is hazardous when an installed plugin already owns the
same fixture name. Gludd hit this with `pytest-base-url`: its session-scoped URL
verification fixture depends on the plugin's `base_url` fixture, while two test
modules independently used `base_url` as an ordinary function-scoped parameter.
The complete test shard therefore failed at setup with `ScopeMismatch`, even
though each test's application logic was valid.

The same repository scan found latent collisions with the plugin-provided
`base_url` and Playwright's `browser` fixture. Those parameters now use the
intent-revealing names `candidate_url` and `browser_engine`.

## Mechanical Prevention

`scripts/check_pytest_param_fixture_collisions.py` discovers installed pytest
plugins through the standard `pytest11` entry-point group. It reads their Python
source without importing plugin internals, follows literal `pytest_plugins`
declarations, and extracts fixtures declared through public `pytest.fixture`
decorators. An AST scan then compares those names with literal arguments to
`pytest.mark.parametrize` throughout `tests/`.

This keeps the rule generic as plugins change: fixture names come from the
installed plugin set rather than a Gludd-maintained `base_url` denylist. The
scanner supports comma-separated strings, tuple/list argument declarations,
pytest import aliases, fixture decorator aliases, and explicit fixture names.
Dynamic parameter declarations remain pytest's responsibility because a static
checker cannot resolve them safely.

## Community Evidence

- [pytest issue #11350](https://github.com/pytest-dev/pytest/issues/11350)
  reports that shadowed fixtures can hide dependency parameters and defer the
  resulting failure until setup.
- [pytest issue #13754](https://github.com/pytest-dev/pytest/issues/13754)
  documents a user encountering `ScopeMismatch` where parametrization and
  fixture scopes interact unexpectedly.
- [pytest issue #14248](https://github.com/pytest-dev/pytest/issues/14248)
  traces a related fixture-closure regression to direct parametrization
  shadowing fixture dependencies.
- The [pytest-base-url project documentation](https://github.com/pytest-dev/pytest-base-url)
  confirms that `base_url` is part of the plugin's public fixture interface,
  which is why it must not be reused as an unrelated direct parameter.

These reports span multiple pytest releases and show that parameter/fixture
shadowing is a persistent class of collection and setup failure, not a
repository-specific naming accident.

## Regression Proof

`tests/unit/test_pytest_param_fixture_collisions.py` proves both original
single-argument collision shapes against synthetic source, validates aliased
public fixture declarations, and scans the complete repository against the
fixtures exposed by the installed CI plugin set.


## Field-value traversal without field-pair generators

The retained ca91f33743014d6667646757f34616457a81b987 diagnostic profile records
4184 declaration traversals at2.459 seconds cumulative/1.254 self. Its process-wide
ast.iter_fields count includes other consumers. Cumulative scopes overlap, and
these values are not a predicted saving or original admission proof. The native
profile is preserved at /tmp/gludd-v012-main-hotpath-profile-ca91f33/report.txt;
the original90-second admission remains failed.

The existing declaration walker now consumes a private field-values iterator:
stdlib map(getattr, repeat(node), node._fields, repeat(None)). It reads ALL current
fields once and in order, including custom or programmatically modified native
fields. Missing attributes yield None, which the existing list consumer ignores;
AttributeError follows the same default behavior and other exceptions propagate.
The pending stack, list/wrapper handling, LIFO order, nearest owner, public source
entrypoints, fresh parse and exact GC restoration/tree release are unchanged.
No grammar pruning, schema table, authenticity flag, cache, global memoization,
new dependency or alternate runner is introduced.

Eight additive controls cover deterministic iterator reduction, lazy once/in-order
attribute access, missing/property AttributeError behavior, non-AttributeError
propagation, native/custom fields and fresh calls/tree release. The representative
import/function/plugin input emitted23 Python field-pair tuples before the change
and emits none afterward; its real findings remain. The original bounded-visit
test redirects only its instrumentation seam/type/patch target, preserving all
visited-set/cardinality/owner/order/finding assertions. GC tests and signatures
are unchanged.

The failing-first subset is5FAIL/3PASS1.39s. The complete owner passes57 cases on
canonical CPython3.11 (final5.37s) and existing private CPython3.12.15 (6.72s), with
fatal warnings and serial execution. The unchanged complete-file coverage config
passes57 cases/8.01s,96% combined coverage,97.7% lines/94.0% branches under the
original85aggregate/75per-file line/branch floors. A new test's Module constructor
was made explicit for strict typing; final whole-owner acceptance includes that
fixture correction, without a production change after the coverage receipt.

Commands, whole-owner roster, RED/green JUnits and actual native logs/status/trace/
coverage/audit are retained in /tmp/gludd-v012-ast-field-traversal-evidence.
These are focused compatibility and operation-count proofs. They establish no
whole-admission timing gain, full gate, hosted success, release closure or change
to90/60, three children/workers2-1-1,21phases2850, ZDD or cleanup/promotion policy.

## Bind native declaration types once per traversal (2026-10-21 UTC)

Source commit 76c6d53cbf2ffb0388024221fb99d154213186c0 descends from
2e9d6d4b0f90e5526728174b495d33cb02c843df. Independent review approves
SOURCE ONLY in /tmp/gludd-v012-runtime-budget-type-binding-source-review.md.
The immutable two-file patch preserves all 57 original cases and assertions.

The original 2e9 repair passes main 2023/75.96s, help 1/1.00s and tail 11/8.52s,
but its enclosing owner still times out with status 124 after 90.57s; Make
returns 2 and issues no repair receipt. The last lock assertion also passes.
The [process-boundary record](PIPELINE_STATUS_PROCESS_BOUNDARY.md) retains the
native raw failure. Passing components do not clear that failed outer result.

The existing public test-files-cprofile owner then measures the exact current
2023-case, two-worker main roster: 65.21s, observed 68.1s. This is an unchanged
base diagnostic with a separate 180/60 bound, not a successor timing or original
90/60 clearance. Controller waits and overlapping worker wall spans are not
worker CPU. Existing test-xdist-trace-summary pairs all 2023 protocols without
issues; the source-inventory node remains the largest at 5.211319s.

A complete 57-case owner profile identifies repeated declaration-class lookups
inside the existing walker. The repair binds five native type values locally
once per invocation. Each subsequent invocation resolves them afresh. Parsing,
every current field, statement/wrapper handling, LIFO order, nearest owner,
findings, exceptions, GC restoration and tree release remain unchanged.
No parser, schema table, scan-result cache, dependency, runner or observer is added.

Two additive controls compare 1 and 256 pass statements while retaining real
imports, functions, plugin values, owner order and body lengths. On unchanged
source both fail in 1.10s: Import lookup counts grow from 8 to 263 and
FunctionDef from 15 to 525. The successor resolves each tracked declaration
class once in both cases. Original native/custom/modified-field, one-visit,
failure, GC and fresh-scan controls remain mandatory.

| Local profiled measurement | Original 57 cases | Final 59 cases |
|---|---:|---:|
| Pytest seconds | 8.27 | 7.82 |
| Observed owner seconds | 9.2 | 8.8 |
| Whole audit cumulative seconds | 6.043 | 5.669 |
| Declaration traversal cumulative/self seconds | 1.760/1.127 | 1.558/0.940 |
| Native parse cumulative seconds | 3.207 | 3.101 |

Cumulative rows overlap. Parse time also varies, so these observations do not
attribute the whole elapsed difference to the lookup repair. The deterministic
operation bound is distinct evidence. No successor main-roster profile or
unchanged 90-second retry is substituted for the actual repair owner.

Source whole-file coverage passes 59 cases in 7.59s, observed 9.1s:
97.8% line and 94.0% branch under unchanged config/coverage_pytest_fixture_audit.ini
and both 85% aggregate/75% each-file floors. Source audit is
.gate-logs/coverage-20261021T020602Z.json. Normal source commit freshly collects
127838/127862, 24 deselected, zero errors, 43.28s/observed 48.5s.

Root receives the exact source into development 1bcca8b through the existing
inactive gate guard and no-commit merge, with no conflicts. The complete receiving
59-case coverage owner passes in 8.70s, observed 10.8s, at the same 97.8%/94.0%.
Native audit .gate-logs/coverage-20261021T021309Z.json and full report
.gate-logs/coverage-runtime-budget-ast-types-receiving.json retain this separate
measurement. Python 3.12.15 independently passes all 59 cases in 7.09s;
JUnit is /tmp/gludd-v012-ast-types-receiving-py312.xml. Ruff and both complete
canonical strict scopes pass. A combined strict invocation reports the existing
flat/package module collision; separate source and test scopes retain strict
checking without import, configuration or suppression changes.

The exact receiving coverage and profiling owners are:

~~~console
make coverage-files COVERAGE_TESTFILES=tests/unit/test_pytest_param_fixture_collisions.py COVERAGE_CONFIG=config/coverage_pytest_fixture_audit.ini COVERAGE_REPORT=.gate-logs/coverage-runtime-budget-ast-types-receiving.json COVERAGE_AGGREGATE_MIN=85 COVERAGE_PER_FILE_MIN=75 OBSERVED_ROOT=.gate-logs/observed OBSERVED_HEARTBEAT_SECS=15 OBSERVED_QUIET_SECS=60 OBSERVED_MAX_SECS=300 OBSERVED_RETAIN_RUNS=20 UV_PROJECT_ENVIRONMENT=/Users/shawnwilson/tmp/gludd-resources/gludd-a24d2f0bddee-toolchain/ci-shards/python-3.11 VIRTUAL_ENV=/Users/shawnwilson/tmp/gludd-resources/gludd-a24d2f0bddee-toolchain/ci-shards/python-3.11 UV_NO_SYNC=1 UV_PYTHON=3.11 UV_PYTHON_DOWNLOADS=never
make test-files-cprofile TESTFILES=tests/unit/test_pytest_param_fixture_collisions.py NPROC=1 GLUDD_XDIST_WORKERS=1 PYTEST_ARGS='-q -W error -n 0' UV_PROJECT_ENVIRONMENT=/Users/shawnwilson/tmp/gludd-resources/gludd-a24d2f0bddee-toolchain/ci-shards/python-3.11 VIRTUAL_ENV=/Users/shawnwilson/tmp/gludd-resources/gludd-a24d2f0bddee-toolchain/ci-shards/python-3.11 UV_NO_SYNC=1 UV_PYTHON=3.11 UV_PYTHON_DOWNLOADS=never
make typecheck-scope FILES=scripts/check_pytest_param_fixture_collisions.py UV_PROJECT_ENVIRONMENT=/Users/shawnwilson/tmp/gludd-resources/gludd-a24d2f0bddee-toolchain/ci-shards/python-3.11 VIRTUAL_ENV=/Users/shawnwilson/tmp/gludd-resources/gludd-a24d2f0bddee-toolchain/ci-shards/python-3.11 UV_NO_SYNC=1 UV_PYTHON=3.11 UV_PYTHON_DOWNLOADS=never
make typecheck-scope FILES=tests/unit/test_pytest_param_fixture_collisions.py UV_PROJECT_ENVIRONMENT=/Users/shawnwilson/tmp/gludd-resources/gludd-a24d2f0bddee-toolchain/ci-shards/python-3.11 VIRTUAL_ENV=/Users/shawnwilson/tmp/gludd-resources/gludd-a24d2f0bddee-toolchain/ci-shards/python-3.11 UV_NO_SYNC=1 UV_PYTHON=3.11 UV_PYTHON_DOWNLOADS=never
~~~

The profile command above is diagnostic. Its actual source runs use public
run-watched at 180/60 with explicit progress/retention/runtime inputs, as retained
in /tmp/gludd-v012-runtime-budget-2e9d6d4-report.md. That report preserves exact
run IDs, both native profile directories, the original failed owner, all commands,
setup side effects and resource limitations. No timing improvement is claimed
for hosted scheduling or the original enclosing admission.

The [official AST field contract](https://docs.python.org/3.11/library/ast.html#ast.iter_fields)
and [firsthand CPython discussion #123373](https://github.com/python/cpython/issues/123373),
opened 2024-08-27, were rechecked. The reporter discusses ast.walk's queue-based
traversal and the documented unspecified ordering. This supports retaining the
explicit field/owner/LIFO walker; it establishes no Gludd speedup or timeout cause.
Supporting research is /tmp/gludd-v012-ast-native-traversal-research.txt.

The first redundant TASKS proof paragraph reaches 2501 lines and fails the
unchanged less-than-2500 guard. Complete new detail stays in this owning feature;
TASKS retains a short index link and every original checkbox/acceptance condition.
The labeled manual native failure copy is
/tmp/gludd-v012-ast-types-task-line-limit-red.txt. No line limit is raised.

Clean receiving merge6161e102eee998bf94407457ad3dde5d0e4ad459 freshly collects
127838/127862,24deselected,zero errors55.40s/native62.0s. Independent SOURCE ONLY
approval is /tmp/gludd-v012-candidate-6161e10-source-review.md. Its actual
ci-failure-repair replay passes2025main/60.57s,help1/0.91s,tail11/8.34s and
outer74.272s/rc0 through public run-watched at unchanged90/60 and2/1/1 workers.
The two additive audit controls account for2023→2025 without changing selectors.
The native owner records exactly family
cf5b089df8ad5c0921ed70ee9d01916f40baeacd98f086caf110f7f139404596.
Raw .gate-logs/observed/v012-runtime-family-repair/v012-runtime-family-6161e10-20261021.log
and native observed-status retain terminal passed/active:false/exit0.

The separate normal batch-push checks the served pinned EE manifest, replays
2025main/58.38s,help1/0.89s,tail11/8.56s in72.3s and passes ordinary collection
and push guards before13-commit development publication. Native verify-remote
returns VERIFIED development@6161e102eee998bf94407457ad3dde5d0e4ad459.
Immutable Build39533219987 at that exact SHA now records both311/312gate jobs
PASS and all eight shards scheduled; running/queued durations remain censored.
These are measurements of their respective owners, not a causal attribution of
the whole elapsed difference to type binding or a final hosted-success claim.
Capacity clearance, complete gate, exact-SHA all-workflow/deployment proof and
normal promotion remain separate. All seven task closures remain open.

## Bounded current source-inventory proposal declined (2026-10-21)

At exact5b25b9f163784163e6edbb931946eed6adcdec37, the existing public
test-files-cprofile owner profiles only the original repository-parameter
inventory case on installed CPython3.12.15 under run-watched90/60. It passes1
in8.60s, observed9.594s. The process-local profile records one shared audit,
4157_source_findings calls, native ast.parse3.486s cumulative, declaration
traversal1.914s cumulative/0.964self and alias discovery0.034s cumulative.
Cumulative rows overlap; instrumentation and other process activity are included.

No new redundant scan or safely removable cost is demonstrated. Root explicitly
declines the additional integration and retains the concrete decision/artifacts.
The current native parser, statement-only LIFO/owner walker, live fields,
per-file tree/GC ownership and fresh providers remain unchanged. No additional
source/test patch or invented performance RED is justified by this sample.

The [stdlib AST contract](https://docs.python.org/3.12/library/ast.html#ast.parse)
already provides the native AST-only parser. The existing
[firsthand2024 CPython123373 report](https://github.com/python/cpython/issues/123373)
also explains why a queue-based generic walk is not a substitute for the
project's explicit order/context contract.

Decision/raw evidence: /tmp/gludd-v012-source-inventory-enhancement-decision.md,
/tmp/gludd-v012-source-inventory-profile-5b25.txt,
/tmp/gludd-v012-source-inventory-profile-5b25.log and matching XML.
The initial external observed-root refusal is retained separately as manual
namespace evidence; the corrected command uses .gate-logs/observed without
changing ownership policy. Original31290.5s failure, missing repair receipt,
full-gate/exact-SHA hosted/deployment proof and task closures remain unchanged.
This diagnostic supplies no runtime speed attribution or release authority.
