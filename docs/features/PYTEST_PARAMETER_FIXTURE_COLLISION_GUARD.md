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

## Measured declaration-classification receiving (2026-10-30 UTC)

The initial, pre-format receiving snapshot is at parent
`cf87c943b8c5f2cf60e4172ad57c82852ad4a7a7`, before a new normal checkpoint.
The approved script is
`a4a289ea8c02a3655dde0aed841945a19cc759821db1ef31d980c5fec1342422`;
the conformed new regression owner is
`9ee3b8e941dfc9b967de57d491e18e8755da64719836c06180410673af1a91cf`.
In that pre-format snapshot all1085 original test lines remain byte-exact;
41 original module statements,43 qualified functions and96 original assertions
remain. The recorded spy style/typing conformance affects only the new controls.
The later formatter and its current receiving are recorded separately below.

Actual Build40736678390 attempt1 at that published parent fails the3.11 runtime
owner at124/90.537s. Its5147 trace events contain2567 complete protocol pairs
without failed reports, issues or unfinished tests, but Main controller3021 has
no session finish, UV-return or final join. The latest worker's session hook
finishes at+89.804s; a hook is not an OS-process exit. The3.12 runtime passes0
in76.570s and its whole gate passes; that does not certify the failed Build or
eight downstream shards. Molecule40736774119 passes all6 jobs;
Pages40736777934 passes3 validation jobs with deployment skipped.

Before the guard change, the retained one-node profile uses the actual Root Make entrypoint,
installed3.11, original fixture payload and run-watched45/30. It passes1/6.67s,
native8.154s. Its generated profile
`.gate-logs/test-files-cprofile/profile-29772-4ecafb9e0096c42f` has5 events,
one matching controller29772 protocol pair and no issues, unfinished tests or
failed reports. The profile records14468756 calls/6.599s: the one repository
audit is5.385s cumulative,4204 fresh source parses remain, and declaration
traversal is1.583s cumulative/0.954s self across4216 calls. Cumulative rows overlap;
this sample establishes neither hosted CPU cause nor forecast deadline fit.

The source change combines four negative declaration-type checks into one local
union guard. Original positive classification branches, subclass acceptance,
custom fields, field/list/LIFO order and owner context remain. Every input is
still freshly parsed; no cache, path/field filter, skipped statement, parser/GC
change, plugin disabling or runtime bound change is introduced. Two native-AST
work-count cases use1/256 statements plus a Pass subclass; a separate positive
control preserves custom Module fields and Import/AnnAssign/FunctionDef/ClassDef
subclasses. This is a counted-work contract, not a timing assertion.

| Native scope | Actual outcome | Proof boundary |
| --- | --- | --- |
| New tests-only RED, installed3.11 | 2FAIL/1PASS0.64s; native2.029s | 11events/3pairs/2failed reports/0issues |
| First proposed GREEN test bytes | 3PASS0.56s; native1.925s | Subsequent Ruff SIM102 and spy union-typing failures remain recorded |
| Corrected new spy, installed3.11 | 3PASS0.61s; native1.966s | 9events/3pairs/0issues/unfinished/failures |
| Pre-format unchanged profile, installed3.11 | 62PASS6.98s; native8.882s | Native85aggregate/75every-file line AND branch audit passes |
| Pre-format unchanged profile, installed3.12 | 62PASS8.10s; native10.839s | Same native line AND branch floors pass |
| Same original-node profile after source repair | 1PASS6.58s; native7.926s | 5events/1controller pair/0issues; no speed/deadline-fit claim |

The first sandbox RED has2FAIL/0.74s but native125/2.113s; the corrected sandbox
GREEN has3PASS/0.58s but native125/about1.9s. These process-permission failures
remain distinct from the separately admitted native RED/GREEN above. They do
not supply successful wrapper evidence. The first GREEN's new spy alone was
conformed to Ruff and strict typing by combining its owned predicates and
using a narrowing builtin tuple check; no old assertion or production semantic
was changed to obtain a pass. Ruff, both configured strict source/test scopes,
environment-write guard, registration2 and whole-worktree10896-file line check
pass for the corrected two code paths.

The complete pre-format owner on both runtimes is
`tests/unit/test_pytest_param_fixture_collisions.py`;
its unchanged `config/coverage_pytest_fixture_audit.ini` measures only the
production auditor. Native receipt:
`.gate-logs/observed/coverage-files/20261030T001434Z-45884-49c1b9ef.json`.
Published raw report:
`.gate-logs/coverage-cf87-declaration-classification311-20261029.json`.
The actual audit covers224/229 statements (97.8%) and111/118 branches (94.1%);
there are5 missing statements and7 missing branches. These are separate line
and branch measures; the tool's combined96.5% display does not replace either
unchanged floor. The3.12 native receipt is
`.gate-logs/observed/coverage-files/20261030T001613Z-47691-53eb084d.json`.
Its published raw report is
`.gate-logs/coverage-cf87-declaration-classification312-20261029.json`;
native audit `.gate-logs/coverage-20261030T001623Z.json` passes the same
224/229 statement and111/118 branch counts. The3.11 native audit is
`.gate-logs/coverage-20261030T001443Z.json`. Both exact sessions and native
statuses are terminal and verified; these are whole-owner focus, not full-gate proofs.


The pre-format changed-input profile at a4/9ee uses the same original node and
actual Root Make/installed3.11
entrypoint, explicit runtime carriers, ID= and45/30 bounds. Exact terminal receipt:
`.gate-logs/observed/v012-declaration-repaired-fixture-profile/v012-declaration-profile311-a4a289e-20261029.json`.
It is passed0/native7.926s, with all UV/cleanup statuses0. Its native leaf is
`.gate-logs/test-files-cprofile/profile-50870-d01220babe09bdec`:
5 events, one matching controller50870/PPID50869 protocol pair, session exit0
and no failed reports, issues or unfinished nodes. The generated profile run ID
is separate from the enclosing observer identity.

Actual pstats records13826596 total/13756441 primitive calls in6.513s, versus
14468756/14398721 in6.599s before repair. Both samples retain4204 source-findings
and parsed-source calls,4231 native ast.parse calls and4216 declaration walks.
Declaration traversal is1.564s cumulative/0.934s self versus1.583/0.954;
audit_pytest_sources is5.350s cumulative versus5.385.
Builtin isinstance calls are7033140 versus7680297. This records reduced
classification work; cumulative profile rows overlap, instrumented durations
are nearly the same, and two samples establish no latency improvement,
hosted CPU cause, original90/60 deadline fit or release proof.

Verified postprofile SHA256s:
trace `47e6f8163ed2535ebb2d204dfde5533454cda66fa901e43ab53bea391a841289`;
pstats `bbd19e8e9f9e308d47053a019dcb905aa42def4abc0b6b4380e17664c648f6b4`;
report `87aa115834a7f9ff8ce8ce47b09f00786cd865bdb02b8f11870320e3e3a8f770`.

The [firsthand CPython123373 discussion](https://github.com/python/cpython/issues/123373)
and earlier pytest fixture-shadowing reports above remain context for preserving
native field/order and fixture semantics. They establish neither this hosted
timeout's cause nor a speed gain from this change. All previous source samples,
declined proposals and failed receipts remain at their original scope.


### Final format-conforming source and receiving

A later native format-check prerequisite refuses both owned code files, including
legacy layout. Applying the actual project formatter changes source bytes to
`778bfbf39d95a00f8ac5b3863a4fa0b87e3be34361ac9384b7ab1f95a205f16b`
for the391-line production script and
`278eeadcf1f5344c9354a41153ce72c4f36a8ca1daee7e0d5cb5b92ac1859c78`
for the1260-line test owner. Entire module ASTs are equal to a4/9ee, respectively,
as verified by the native
`/tmp/gludd-v012-classification-formatted-identity-20261029.mk` owner and private
source rebinding. All original41 module statements,43 functions and96 assertions
remain semantically exact. The original1085-line byte-prefix claim belongs to
the pre-format snapshot; final formatting also normalizes old layout.

Current Ruff format/check, script docstrings and configured strict source/test
scopes on both runtimes pass. Root's two-code-path staged-content guard passes0
in6.080s:
`.gate-logs/observed/v012-declaration-staged-content/v012-declaration-code2-content-778bfbf-20261029.json`.
It changes no source bytes; its EOF/whitespace/secrets/line/zero-new-clone checks
cover the two code paths, not these later document edits.

The final formatted files receive the same complete owner and unchanged
`config/coverage_pytest_fixture_audit.ini` exactly once on each runtime:

| Current formatted owner | Actual terminal | Native trace and coverage |
| --- | --- | --- |
| Installed3.11 | 62PASS7.51s; native9.518s | 127events/62pairs/0issues/unfinished/failures |
| Installed3.12 | 62PASS7.79s; native10.679s | 127events/62pairs/0issues/unfinished/failures |

Both native audits pass unchanged85aggregate/75every-file line AND branch:
224/229 statements (97.8%) and111/118 branches (94.1%), with no file below either
floor. Exact coverage-files receipts are
`.gate-logs/observed/coverage-files/20261030T002538Z-55962-71b604c1.json` and
`.gate-logs/observed/coverage-files/20261030T002621Z-57105-b412fb4e.json`.
Published raw reports are
`.gate-logs/coverage-declaration-formatted311-20261029.json` and
`.gate-logs/coverage-declaration-formatted312-20261029.json`;
native audits are `.gate-logs/coverage-20261030T002547Z.json` and
`.gate-logs/coverage-20261030T002631Z.json`. Exact sessions and statuses are
terminal and verified. These new receipts bind formatted bytes; the earlier
a4/9ee receipts remain historical. Neither original-node profile is repeated or
reattributed to the formatted input. The two recorded profile samples supply
no measured latency improvement, hosted CPU cause or90/60 deadline-fit proof.

Next: review the final two code/two document postimages, then normal checkpoint/fresh
collection and a clean-source original90/60 repair alone before guarded
publication and exact-source hosted observation. Existing E/M/C/N final objects,
focus132/128 HOLD,333 combined, canonical21600 gate, global85/75 line AND branch
on both runtimes, canonical push, master ZDD and fresh signed artifacts/release
proof remain pending. All328 task states and seven open S98 criteria remain;
this receiving paragraph closes none.
