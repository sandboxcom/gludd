# Structural Python audit contracts

Gludd's structural tests protect importability, test ownership, and bounded module size without freezing repository
snapshots that become false as features are added.

## Package discovery

Every directory containing Python modules must be discoverable as either a regular package or a PEP 420 namespace
package. An `__init__.py` file is therefore required only when regular-package behavior is semantically necessary. Empty
`__init__.py` marker files are valid; non-empty package APIs must parse, import, document themselves, and keep their
declared exports consistent.

This distinction addresses a long-running source of user confusion documented in the Python community discussion
["__init__.py, PEP 420, and iter_modules confusion"](https://discuss.python.org/t/init-py-pep-420-and-iter-modules-confusion/9642).
The repository audit asks Python's import machinery whether an unmarked directory is a namespace package instead of
maintaining a hand-written directory allowlist.

## Test ownership and size growth

Large source modules are associated with tests through explicit static imports, including literal
`importlib.import_module(...)` calls. Test filenames are not required to mirror implementation filenames, so module
splits and feature-oriented test names do not create false missing-test failures. The ten largest modules must all have
an importing test.

The hard per-file ceiling is 2,500 lines. Aggregate growth is bounded by average source-file size rather than an
absolute repository-wide line snapshot, allowing the project to add cohesive modules while continuing to detect
monolithic growth.

## Pytest fixture lifecycle

Class containers in these audits use function-scoped fixtures because each fixture feeds one test. This avoids the
class-instance lifecycle ambiguity reported by users in
[pytest issue #10819](https://github.com/pytest-dev/pytest/issues/10819), which pytest 9.1 deprecated ahead of removal in
pytest 10.

Fixtures that intentionally share a value across a class keep `scope="class"`. If a fixture only returns data, put
`@staticmethod` immediately below `@pytest.fixture(scope="class")` and omit the instance parameter. If it sets class
attributes, use `@classmethod` with `cls`, as shown in the
[pytest migration instructions](https://docs.pytest.org/en/stable/deprecations.html#class-scoped-fixture-as-instance-method).
The fixture's lifetime and test cases stay the same; its callable no longer binds to a disposable test instance.

The v0.1.2 hosted `unit-1b` batch 029 exposed this contract in
`tests/unit/test_enhancement_ratio_plugin.py`: three keyword fixtures in `TestKeywordClassification` and
`TestWaveRatioSimulations` caused 34 setup errors under `-W error`. All three only return extracted keyword data, so
they use static methods while retaining their original class scope, classification inputs, parametrization, and
assertions. Fixture conformance does not change the enforcement plugin or its restart requirements.

The longstanding report in pytest issue #10819 dates to March 2023 and shows class fixture instance attributes missing
from an inherited test class. The June 2026 practitioner discussion
[SQLAlchemy #13391](https://github.com/sqlalchemy/sqlalchemy/discussions/13391) reports the same warning after adopting
pytest 9.1 and the need to migrate existing fixture methods. These reports explain why correct fixture binding matters
even when a return-only fixture previously appeared to work.

### Earlier detection

Pytest's [fixture resolver](https://docs.pytest.org/en/stable/_modules/_pytest/fixtures.html#resolve_fixture_function)
emits the instance-method deprecation during fixture setup. Collection and `--setup-plan` therefore do not prove this
binding is valid, and `--setup-only` executes actual fixtures. The current
[Ruff rule catalog](https://docs.astral.sh/ruff/rules/) and
[flake8-pytest-style rules](https://github.com/m-burst/flake8-pytest-style#description) have no rule for this binding;
`PT003` addresses redundant function scope instead.

The existing pytest source audit in `scripts/check_pytest_param_fixture_collisions.py` now exposes
`find_class_scoped_instance_fixtures`, reusing its import-alias and fixture-decorator recognition. The repository
invariant in `tests/unit/test_pytest_param_fixture_collisions.py` scans all Python test sources and reports unsupported
literal class-scoped declarations with their file, decorator line, class, and public fixture name. It accepts static
methods, class methods, module-level fixtures, and fixtures local to a test method. Conditional class declarations
remain methods. The binding scan also recognizes public `pytest_asyncio.fixture` aliases, while the existing plugin
fixture discovery and parameter-collision semantics remain unchanged.

The first repository scan found five additional instance fixtures in the materials, local-model role, and grinding
block tests. Each fixture only returns data, so static binding preserves its body, scope, and consumers. Shape tests
cover aliases, async declarations, supported bindings, and deterministic source locations without executing synthetic
fixtures. The binding invariant has no allowlist. Computed scopes remain the canonical runtime's responsibility.

Run the repository invariant through the existing `_pytest-runtime-compatibility-admission` owner before unit shards,
within its unchanged 90-second budget. The canonical warning-fatal replay remains required; the static scan does not
replace the full gate, coverage floors, or exact-SHA evidence.

### Bounded shared source evidence

Build `38291013073` at `7239008` times out the Python 3.11 admission owner at 90.1 seconds. Its two repository fixture
checks consume approximately 61 seconds independently parsing the same test tree. Both checks pass before the timeout;
the later interrupted interpreter-boundary node is not evidence of a separate hanging CLI.

`audit_pytest_sources` now parses each sorted unique source once and returns only immutable collision/binding findings.
The two existing repository assertions share that compact report through a module-scoped fixture, using pytest's
[documented fixture reuse](https://docs.pytest.org/en/stable/how-to/fixtures.html#scope-sharing-fixtures-across-classes-modules-packages-or-session).
The standard-library [AST traversal](https://docs.python.org/3.11/library/ast.html#ast.iter_child_nodes) visits declaration
and statement structure; expressions cannot contain import or function/class declarations, so large parameter values
need not be walked. Decorators and plugin declarations remain available for the original recognizers.

Alias collection precedes recognition, including imports after decorators or inside nested scopes. Nearest class/function
ownership remains intact through conditional blocks. Binding checks retain asyncio providers; collision/plugin fixture
discovery remains pytest-only. Installed plugin modules are likewise parsed once per visited module, retaining cycle
protection. The existing path/source helpers stay fresh adapters. No cross-invocation cache, file sampling, AST retention,
allowlist, fixture execution, warning suppression, or budget increase is introduced.

Four regression cases fail first: unique-path parse count and prior-tree weakref release; changed sources, added paths
and changed fixture names; late aliases/nested ownership/provider boundaries; and one parse per cyclic plugin module.
All 22 audit cases then pass at 96.03% focused coverage (97.47% line, 93.27% branch), above unchanged 85/75 floors.
The complete observed owner passes 349 cases in 29.49 seconds (30.3 observed), with all 28 families, 21 phases and
2850-second ceiling unchanged. This local acceptance does not prove hosted 3.11/3.12 timing or satisfy the canonical gate.

The November 2025 [pytest subclass report](https://github.com/pytest-dev/pytest/issues/13882) describes missing class-fixture
state in inherited tests. It reinforces preserving binding semantics while optimizing execution; it does not explain this
repository's measured scan cost. ZDD and rollback remain the administrative-audit contract below.

### v0.1.2 acceptance and lifecycle

The original ratio replay preserves 41 passing tests and 34 setup errors. After binding conformance, the affected
five-file cohort passes all 263 cases with warnings fatal. The audit has 18 passing cases and 95.67% combined coverage
(97.08% line and 93.40% branch), above the unchanged 85% aggregate and 75% per-file floors. Four new promotion tests
fail first; all 21 promotion cases then pass. The actual complete early owner passes 285 cases in 43.38 seconds
(44.1 observed), retaining its 90-second runtime and 60-second quiet bounds, one worker, and all 26 manifest families.

This is an administrative source audit, not a deployment or daemon. It acquires no serving endpoint and does not
restart a controller, preserving zero-downtime deployment (ZDD). Rollback restores the prior audit and routing from
their development commits; it cannot relabel focused acceptance as release readiness. Full canonical coverage,
the exact candidate gate, and required hosted success remain mandatory.

## Dependency graph semantics

The architectural graph keeps two views of imports:

- The complete static graph includes deferred imports and enforces package-layer boundaries.
- The initialization graph excludes function-local and `TYPE_CHECKING` imports and detects only cycles that can execute
  while modules load.

CLI independence is enforced between feature roots such as `cli_model` and `cli_payment`. Modules inside the
`cli_commands` package remain one feature, while `cli_commands.parser` is the declared composition root that wires the
independent features into one command tree.

The D-16 tool-call ceiling is owned by `general_ludd.dispatch.limits`, below HTTP and worker adapters. The router keeps
its public compatibility export, but event-loop, worker, and tool-loop code no longer depend upward on the presentation
layer. The job schema's denial-audit path deliberately delegates to the canonical fail-closed persistence redactor; that
single security edge is documented explicitly rather than weakening the general layer rule.
