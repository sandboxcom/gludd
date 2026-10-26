# Code Intelligence Import Boundary

## Canonical parent coverage repair (2026-10-26 UTC)

The cold gate on the working candidate based at
82ec5170c173cfb52bb8bf3c6bbf7944cf3375e4 failed in
.gate-logs/gate-20261025114409-e53ab751.log. Coverage reset passed and all133
batches executed with zero resumed, disproving the earlier stale-fragment
explanation. The missing branch is the unknown-name AttributeError at line42;
TYPE_CHECKING was already excluded. Preserve that failed gate as failed evidence.

The seven original fresh-process controls exercised this behavior in children.
Their focused profile enables subprocess measurement; canonical
.coveragerc-greenlet does not. An additive in-process API control now measures
lazy resolution, cached canonical identity, discoverability and unknown-name
errors in the ordinary caller, restoring the original cache state afterward.
No production exclusion, coverage profile, assertion or floor changed.

Actual native reproduction ran all85 original cases successfully, then
make coverage-json THRESHOLD=85 SOURCE=src/general_ludd/code_intelligence/__init__.py
refused64.7058823529percent line and0percent branch. The retained numeric report
is .gate-logs/coverage-20261026T122738Z.json. After the additional control, all86
cases pass on both installed runtimes using the unchanged canonical profile.
The same native audit reports100percent line and100percent branch for this file:

- Python3.12 execution receipt:
  .gate-logs/observed/v012-native-export-coverage/v012-native-export-green312-20261026.json;
  numeric audit .gate-logs/coverage-20261026T123318Z.json.
- Python3.11 execution receipt:
  .gate-logs/observed/v012-native-export-coverage/v012-native-export-green311-20261026.json;
  numeric audit .gate-logs/coverage-20261026T123600Z.json.

The exact native child selects the complete import-boundary, init-files-deep and
package-reexports owners with -n0 and fatal warnings, --cov and
--cov-config=.coveragerc-greenlet, writing coverage.json. Its measurement-only
--cov-fail-under=0 precedes the separate unchanged85aggregate/75each-file LINE
and BRANCH audit above; the first child's exit0 alone is no admission.
An earlier dotted-module selector produced no report and is failed diagnostic
input, retained in v012-import-boundary-red-20261026; it supplies no coverage proof.

Independent delta review approved source only. The complete canonical gate,
global85/75 coverage, exact-SHA hosted CI, ZDD deployment and stable-release
artifact proofs remain required; no S98 task is closed by this focused repair.

The [firsthand Python forum discussion](https://discuss.python.org/t/module-getattr-invoked-twice-importlib-bug/25600)
was reread on2026-10-26: the original2023 report and2025 follow-up both describe
repeated from-import resolution. This supports retaining the cache-identity
assertions. It does not establish the cause of Gludd's measured coverage gap,
which comes from the native parent/child evidence above.

## Decision and release trigger (2026-10-21 UTC)

Importing the AST extractor no longer imports the codebase introspector and its
self-improvement/database dependencies. Only the CodebaseIntrospector re-export
uses standard PEP562 resolution. The other seven exports, exact eight-name
__all__, direct submodule imports, class identity and actual implementations
remain unchanged. Root owns source/config/docs on development, based on published
fe60a94589f4fb312d5c04bc5e97ab3ab2775195; the reviewer produced an external proposal.

At that exact published SHA, Build39550173309 fails both supported-runtime gates
in Known runtime and handoff regression admission. Python312 passes2067 cases in
75.77s and fresh HELP in1.81s, then times out124/90.6s during the original tail;
Python311 passes2067/76.12s plus HELP1/1.59s, then times out124/90.5s at the same
second tail node. Neither has an assertion failure. All eight canonical shard
timings remain unknown/censored because downstream jobs never schedule.
The earlier local publication replay79.9s does not establish hosted improvement.

The native normal-fixture profile attributes0.866s cumulative to the parser-reset
fixture's extractor import, through introspect, self_improve's initializer,
live_candidate_wiring and SQLAlchemy. Cumulative rows overlap. Five fresh,
serial interpreter pairs isolate the export boundary: median original0.424902s
versus proposal0.057810s; first real introspector use still costs about0.36s.
This is warm-filesystem diagnostic evidence, not an admission or hosted verdict.
Do not multiply isolated savings by worker/test counts.

## Preserve public behavior and failure visibility

The package's TYPE_CHECKING re-export preserves the actual public class type.
Runtime __getattr__ imports and publishes that same class only after success.
An import exception propagates and leaves no failed binding; a later access
retries normally. Python's own import lock handles simultaneous first consumers.
__dir__ includes declared exports before first use. Unknown names raise the
ordinary AttributeError without importing the heavy feature.

Reload retains a module dictionary. Root's added patch/restore control catches
the proposal retaining an old cached class:1FAIL/1.11s/native1.8s. Discard just
that cached export during package execution so reload followed by access binds
the current canonical class, including restoration. The seven eager imports
retain normal reload behavior.

Raw vars(package) does not contain the deferred class until access. Import side
effects and errors move to actual introspector use; star import still resolves
all exports. No daemon startup acquisition, dependency, lock, fixture, class proxy,
custom loader, observer, proof cache or background process is introduced.
The facts router imports the canonical submodule directly; its existing patch
seam remains intact.

The existing init-file orphan checker deliberately handles exact PEP562 exports
through its per-symbol allowlist. Add exactly this one justified symbol,
backed by real import/identity/error controls. Keep its checks and every other
entry unchanged; treating all TYPE_CHECKING imports as runtime exports would
weaken its intent.

## Retained source evidence

The initial five new controls retain4FAIL/1PASS3.69s/native4.8s against original
source, /tmp/gludd-v012-import-boundary-red.log/XML. Seven final controls cover
extractor isolation, all canonical exports/from/star imports, serialization and
construction, direct-import order, failure/retry, concurrent access, unknown
names and reload/patch/restore. Three complete owners pass85/5.38s/native6.4s
on311 and85/9.00s/native10.7s on312, zero skips/errors and fatal warnings;
/tmp/gludd-v012-import-boundary-final-311.log/XML and312 equivalent.

Whole changed-file coverage uses the complete real initializer and all three
owners. It passes85/8.43s/native10.1s with100% line and100% branch, retaining
85aggregate/75each for BOTH. Native report
.gate-logs/coverage-v012-code-intelligence-exports-final.json and audit
.gate-logs/coverage-20261021T060334Z.json bind the measured source. The initial
pre-reload84-case coverage remains separate historical evidence.

The earlier broader14-owner replay passes514 with2skips on BOTH runtimes
(31113.40s/native14.6s;31217.67s/native19.0s). The two skipped cold-start checks
expose a separate measurement defect: their helper cannot parse actual CPython
output. Preserve those results and repair the helper; never call that run
zero-skips or use missing timing as success. The package's changed owners pass
without those skips. After [repairing the measurement consumers](IMPORT_TIME_MEASUREMENT_INTEGRITY.md),
the complete15-owner current replay passes649 cases on31115.84s/native17.2s
and31218.96s/native20.4s, fatal warnings/zero skips/errors;
/tmp/gludd-v012-import-consumers-final-311.log/XML and312 equivalent. That owning
record retains the exact complete scope and all32 new false-green REDs.

Reproduce focused behavior with the installed canonical environment from SESSION:

~~~console
make test-files TESTFILES='tests/unit/test_code_intelligence_import_boundary.py tests/unit/test_init_files_deep.py tests/unit/test_package_reexports.py' PYTEST_ARGS='-q -W error -n 0'
make coverage-files COVERAGE_TESTFILES='tests/unit/test_code_intelligence_import_boundary.py tests/unit/test_init_files_deep.py tests/unit/test_package_reexports.py' COVERAGE_CONFIG=config/coverage_code_intelligence_exports.ini COVERAGE_REPORT=.gate-logs/coverage-code-intelligence-exports.json COVERAGE_AGGREGATE_MIN=85 COVERAGE_PER_FILE_MIN=75 OBSERVED_ROOT=.gate-logs/observed OBSERVED_HEARTBEAT_SECS=15 OBSERVED_QUIET_SECS=60 OBSERVED_MAX_SECS=90 OBSERVED_RETAIN_RUNS=20
~~~

Set BOTH outer UV_PROJECT_ENVIRONMENT and VIRTUAL_ENV to that installed runtime,
plus UV_NO_SYNC=1, UV_PYTHON=3.11 and UV_PYTHON_DOWNLOADS=never. Use the registered
312 sibling with UV_PYTHON=3.12.15 for the other supported runtime. Run from the
selected checkout root; the required PWD coverage interpolation rejects absence
and excludes deliberate modified fixture copies without omitting real source.

## Standard facility and firsthand research

[PEP562](https://peps.python.org/pep-0562/) and the
[Python311 module contract](https://docs.python.org/3.11/reference/datamodel.html#customizing-module-attribute-access)
support module __getattr__/__dir__. A
[firsthand April2023 double-resolution report](https://discuss.python.org/t/module-getattr-invoked-twice-importlib-bug/25600)
explains repeated from-import getter calls. Publishing the canonical successful
class avoids that repeated lookup while preserving errors.
[LazyLoader's documented restrictions](https://docs.python.org/3.11/library/importlib.html#importlib.util.LazyLoader)
make a global loader unnecessary for this one export. These sources support the
facility choice, not project acceptance or a timing claim.

Exact proposal, ten sample rows and executable probe remain under
/tmp/gludd-v012-import-reduction-{decision.md,proposal.patch,benchmark.json,
benchmark.xml,child.py,test.py}. The decision preserves compatibility limits and
the primary sources; the proposal does not constitute independent approval of
Root's implementation.

## Required receiving and release proof

The existing admission owner appends just the cheap import-graph node as family132
while preserving all131 prior identities/selectors/order and original90total/
60quiet,2/1/1 workers,
fatal warnings,21phases2850 and full gate. No fragile timing threshold replaces
that deterministic graph condition. Bind first actual runtime replay to the
native CI repair owner at one clean descendant SHA. Independent source review,
exact hosted scheduling measurement, full canonical gate, deployment acceptance,
global coverage, ZDD and guarded promotion remain separate and pending.
Disk97% still blocks final release proof; this source repair does not clear it.
