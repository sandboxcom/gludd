# Oversized Python module split plan

This plan turns the 11 production and maintenance Python violations in the
tracked-file line-limit ledger into independently mergeable refactors. It is a
design for extraction, not permission to change behavior. Every original module
path remains the compatibility facade, every new implementation file targets at
most 1,500 physical lines, and the facade plus every extracted file must remain
below 2,000 lines so routine maintenance has room below the 2,500-line gate.

The repository already provides the patterns needed for the work: event-loop
mixins, domain-specific `cli_*` registrars, FastAPI routers, repository classes,
dataclasses, runtime-checkable protocols, and explicit dependency factories.
The implementation should extend those seams with the standard library,
Pydantic, FastAPI, and pytest already in the lock. It must not add another
plugin framework, command framework, dependency-injection container, or HTTP
client.

## Automated compatibility boundary

The first implementation slice adds `scripts/check_python_split_compat.py`,
`config/python_split_public_api.json`, and focused tests. The checker captures
an **AST public-surface snapshot** before a split, without importing modules
that start daemons or touch state. For every top-level class, function,
constant, facade alias, and declared `__all__` member, the manifest records its
kind, qualified path, `inspect.signature` representation where applicable, and
whether object identity or a wrapper is required after extraction.

After a split, a fresh subprocess builds an **importlib identity matrix** for
the facade, package-level re-exports, and implementation modules. It proves
that imports work in both orders, that identity-required objects are the same
objects, and that wrappers retain **signature parity**. Dataclass fields,
Pydantic model fields, enum values, function defaults, coroutine status, and
the `gludd = general_ludd.cli:main` console entry point are compared explicitly.
The process also imports each affected package twice after clearing its module
keys, catching import-order dependencies rather than accepting a warm
interpreter result.

An AST-based **monkeypatch target inventory** scans existing test calls to
`patch`, `patch.object`, and `monkeypatch.setattr`. Every literal target rooted
at one of the 11 facade modules must still resolve. A target may be migrated to
an implementation module only in the same commit and only when the facade name
was never a supported import; public and widely used private targets stay as
facade wrappers or aliases. Wrappers read facade-owned dependency ports at call
time, so patching `general_ludd.cli._cmd_status`,
`general_ludd.models.gateway.default_token_tracker`, or
`scripts.agent_watchdog.FALSE_DONE_MAXOUT` continues to influence execution.

The checker also builds the affected import graph and rejects a reverse edge:
**no extracted module imports its facade**. Lower layers may import contracts,
schemas, and explicit protocol modules; facades import implementations. The
daemon router cycle is removed before daemon extraction by moving shared state
accessors into `daemon_components/state.py` and migrating routers to that
module, while `general_ludd.daemon` re-exports the old names.

The compatibility baseline is reviewed data, not an automatic allowlist.
Removing or changing a symbol requires an explicit manifest migration and a
consumer change in the same commit. The checker emits a deterministic diff and
fails closed on missing paths, duplicate symbols, unresolved patch targets,
signature drift, import cycles, or unreviewed manifest changes.

## Exact 11-module execution ledger

| Path | Extraction modules | Stable facade / API | State and cycle guard | Test families | Coverage contract | Rollback | Wave | Owner |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `src/general_ludd/pricing_intel/sources.py` | `pricing_intel/source_components/base.py`, `model_apis.py`, `cloud_compute.py`, `cache.py`, `registry.py` | Keep the module; re-export every source class, error, protocol, `staleness_text`, and `all_sources`; facade wrappers retain HTTP patch ports | Immutable price models cross boundaries; cache clock and HTTP factories are injected; cycle direction is components to models only | `make test-files TESTFILES='tests/unit/test_pricing_sources.py tests/unit/test_pricing_intel.py tests/unit/test_pricing_cache_and_fallback.py tests/unit/test_pricing_aws.py tests/unit/test_pricing_gcp.py' PYTEST_ARGS='-q -W error'` | Branch-aware >=85% aggregate and >=75% each extracted file; target >=90% | Revert the single source split commit and its compatibility manifest row | W1 | A |
| `scripts/test_hook_runtime.py` | `scripts/hook_runtime/runner.py`, `fixtures.py`, `cases_core.py`, `cases_scheduling.py`, `cases_safety.py`, `cases_lifecycle.py` | Keep the script as pytest entrypoint; re-export imported test callables and helpers so existing node IDs remain rooted at the facade | One fixture owns isolated state roots and cleanup; case modules depend on runner and fixtures only, preventing a collection cycle | `make test-hook-runtime` plus `make test-specific TESTFILE=tests/unit/test_runtime_test_coverage.py PYTEST_ARGS='-q -W error'` | Branch-aware >=85% aggregate and >=75% each extracted file; target >=90% | Revert the script split and restore the pre-split node-ID manifest | W1 | B |
| `src/general_ludd/db/repository.py` | `db/repositories/shared.py`, `todos.py`, `projects.py`, `messaging.py`, `operations.py`, `metrics.py`, `memory.py` | Keep the module; re-export all repository classes, `ConcurrencyError`, `InvalidTransitionError`, and `scoped_to`; retain `general_ludd.db` identities | Session and tenant scope are constructor or context inputs; aggregate modules never import the facade, blocking a repository cycle | `make test-files TESTFILES='tests/unit/test_repository.py tests/unit/test_repository_branches.py tests/unit/test_db_repository_coverage.py tests/unit/test_todo_repository_security.py tests/unit/test_memory_repository_deep.py' PYTEST_ARGS='-q -W error'` | Branch-aware >=85% aggregate and >=75% each extracted file; target >=90% | Revert the aggregate split commit; no schema or migration rollback is needed | W2 | A |
| `scripts/agent_watchdog.py` | `scripts/watchdog_components/types.py`, `state_store.py`, `lease.py`, `task_health.py`, `ci_health.py`, `enforcement.py`, `release.py`, `cli.py` | Keep the script as executable entrypoint; re-export public functions and tested constants; facade wrappers preserve monkeypatchable globals | A per-call `WatchdogContext` snapshots facade configuration; state writers remain atomic; components form a cycle-free types-to-services-to-orchestrator DAG | `make test-files TESTFILES='tests/unit/test_agent_watchdog.py tests/unit/test_gate_process_cleanup.py tests/unit/test_hooks_actually_fire.py tests/unit/test_task_watchdog.py' PYTEST_ARGS='-q -W error'` | Branch-aware >=85% aggregate and >=75% each extracted file; target >=90% | Revert the watchdog split commit and restore the original single-process entrypoint | W2 | B |
| `src/general_ludd/self_improve/codex_comparison.py` | `self_improve/codex_components/contracts.py`, `compact_protocol.py`, `proposal_merge.py`, `feedback.py`, `local_gateway.py` | Keep the module; re-export contracts, proposal types, codecs, comparison functions, feedback exchange, and `LocalProposalGateway` | Contract modules are the lowest layer and never import managed runner or runtime; immutable dataclasses own validation; no facade cycle | `make test-files TESTFILES='tests/unit/test_self_improve_codex_comparison.py tests/unit/test_self_improve_codex_span_protocol.py tests/unit/test_self_improve_prompt_plan.py tests/unit/test_managed_remote_schema.py' PYTEST_ARGS='-q -W error'` | Branch-aware >=85% aggregate and >=75% each extracted file; target >=95% for codecs | Revert the comparison split commit and its public-surface manifest update | W3 | A |
| `src/general_ludd/cli.py` | `cli_commands/parser.py`, `core.py`, `models.py`, `compute.py`, `platform.py`, `integrity.py`, `daemon_control.py`, `tui_views.py` | Keep `general_ludd.cli:main` as entrypoint; re-export `build_parser`, `main`, all tested `_cmd_*` and `_build_*` wrappers; reuse existing `cli_*` registrars | Parser receives a command registry instead of importing the facade; handlers receive `CliServices`; this removes callback cycles and preserves facade patch lookup | `make test-files TESTFILES='tests/unit/test_cli.py tests/unit/test_cli_parser_deep.py tests/unit/test_cli_execution_coverage.py tests/unit/test_tui_extracted_builders.py tests/unit/test_cli_decision_codification.py' PYTEST_ARGS='-q -W error'` | Branch-aware >=85% aggregate and >=75% each extracted file; target >=90% | Revert the CLI split commit and restore the parser cache compatibility fixture | W3 | B |
| `src/general_ludd/self_improve/managed_runner.py` | `self_improve/managed_components/contracts.py`, `approval.py`, `proposal_backends.py`, `candidate_routing.py`, `policy.py`, `execution.py`, `serialization.py` | Keep the module; re-export task, prompt, proposal, approval, result, adapter, and runner types; facade owns construction entrypoints | `contracts.py` depends only on schemas; policy and routing depend on contracts; execution composes them through protocols and never imports runtime, preventing a managed-runtime cycle | `make test-files TESTFILES='tests/unit/test_managed_self_improve_runner.py tests/unit/test_managed_self_improve_process.py tests/unit/test_managed_self_improve_private_policy.py tests/unit/test_self_improve_runner_model_lifecycle.py' PYTEST_ARGS='-q -W error'` | Branch-aware >=85% aggregate and >=75% each extracted file; target >=90% | Revert the managed-runner split commit and restore facade implementation bodies | W4 | A |
| `src/general_ludd/models/gateway.py` | `models/gateway_components/contracts.py`, `limits.py`, `routing.py`, `invocation.py`, `streaming.py`, `failover.py` | Keep the module; re-export constants, errors, `ModelProfile`, `ModelResponse`, and `ModelGateway`; mixin methods remain visible on the same class and facade dependency ports remain patchable | `ModelGateway` alone owns locks, semaphores, client lifecycle, and mutable registries; mixins receive state through self; contracts cannot import routing, avoiding a gateway cycle | `make test-files TESTFILES='tests/unit/test_models_gateway.py tests/unit/test_model_gateway_payload_limits.py tests/unit/test_model_gateway_stream_limits.py tests/unit/test_model_gateway_failover.py tests/security/test_gateway_concurrency_redteam.py' PYTEST_ARGS='-q -W error'` | Branch-aware >=85% aggregate and >=75% each extracted file; target >=90% | Revert the gateway split commit; close behavior and profile registries return with the facade | W4 | B |
| `src/general_ludd/self_improve/runtime.py` | `self_improve/runtime_components/make_runner.py`, `proposal_repair.py`, `prompt_context.py`, `candidate_evaluation.py`, `managed_assembly.py`, `benchmark_cli.py` | Keep the module as program entrypoint; re-export `MakeResult`, `MakeRunner`, proposal and prompt helpers, evaluation functions, builders, `run_benchmark`, and `main` | Components follow codex contracts to managed contracts to runtime orchestration; process ownership stays in make runner; no lower layer imports runtime, preventing a cycle | `make test-files TESTFILES='tests/unit/test_managed_self_improve_runtime.py tests/unit/test_self_improve_codex_runner.py tests/unit/test_self_improve_runtime_protocols.py tests/unit/test_self_improve_runtime_private_policy.py' PYTEST_ARGS='-q -W error'` | Branch-aware >=85% aggregate and >=75% each extracted file; target >=90% | Revert the runtime split commit and restore the original process and worktree orchestration | W5 | serial |
| `src/general_ludd/event_loop/loop.py` | `event_loop/tick_lifecycle.py`, `review_dispatch.py`, `compute_lifecycle.py`, `execution_dispatch.py`, `self_improve_lifecycle.py`, `decision_completion.py` | Keep the module; re-export `EventLoop`, phase constants, `_FileClaimConflict`, routing helper aliases, and tested dependency ports; extend existing mixins | The `EventLoop` instance remains the sole mutable owner; mixins use typed self protocols and never import the facade; phase order and repository lifetime prevent a scheduler cycle | `make test-files TESTFILES='tests/unit/test_event_loop_loop.py tests/unit/test_event_loop_tick.py tests/unit/test_event_loop_branches.py tests/unit/test_event_loop_execution_leases.py tests/integration/test_decision_codification_live_review.py tests/integration/test_full_pipeline_e2e.py' PYTEST_ARGS='-q -W error'` | Branch-aware >=85% aggregate and >=75% each extracted file; target >=90% | Revert the event-loop split commit; the facade restores method bodies without state migration | W6 | serial |
| `src/general_ludd/daemon.py` | `daemon_components/config.py`, `secrets.py`, `state.py`, `lifecycle.py`, `middleware.py`, `subsystems.py`, `app_factory.py` | Keep the module; re-export request models, public helpers, tested private helpers, `_daemon_state`, `_lifespan`, and `create_daemon_app`; preserve app-factory entrypoint | Move router-accessed state first; one state proxy owns mutable globals; routers import state components, never facade; lifecycle imports factories through protocols to break the current router cycle | `make test-files TESTFILES='tests/unit/test_daemon.py tests/unit/test_daemon_deep.py tests/unit/test_daemon_startup.py tests/unit/test_no_circular_imports.py tests/integration/test_daemon_lifespan.py tests/integration/test_daemon_core_integration.py' PYTEST_ARGS='-q -W error'` | Branch-aware >=85% aggregate and >=75% each extracted file; target >=90% | Revert the daemon split commit and router import migration together, restoring the original state proxy | W7 | serial |

## Disjoint implementation waves

Each path in the ledger is owned by one branch and lands through development.
There is **one oversized module per commit**; compatibility-manifest and focused
test changes for that module travel in the same commit. No two owners in a wave
touch the same source, test module, coverage configuration, or compatibility
manifest shard. The orchestrator merges owners sequentially and regenerates the
top-level compatibility digest after each merge.

### Wave W1: low-coupling characterization

- `src/general_ludd/pricing_intel/sources.py` (owner A) extracts provider
  implementations and registry composition. Its production consumers are the
  pricing catalog and provider tests.
- `scripts/test_hook_runtime.py` (owner B) extracts only pytest runner helpers
  and case families. It touches no application package.

This is the first recommended parallel coding wave because the owners share no
runtime imports and no test files. It proves the facade checker against both a
library module and a pytest entrypoint before riskier stateful splits.

### Wave W2: persistence and watchdog state

- `src/general_ludd/db/repository.py` (owner A) separates aggregate repositories
  without schema changes.
- `scripts/agent_watchdog.py` (owner B) separates process/state policy without
  touching application persistence.

This is the second recommended parallel coding wave. The branches share neither
state files nor imports; the watchdog continues using its own atomic JSON files
while repositories continue using SQLAlchemy sessions.

### Wave W3: user and model-planning boundaries

- Owner A splits `self_improve/codex_comparison.py` first because both later
  self-improvement modules import its contracts.
- Owner B splits `cli.py`, reusing the already extracted `cli_*` registrars.

### Wave W4: managed execution and model transport

- Owner A splits `self_improve/managed_runner.py` after the W3 comparison facade
  is stable.
- Owner B splits `models/gateway.py`. It cannot edit daemon or event-loop tests
  owned by later waves; cross-feature smoke tests run only after both merges.

### Waves W5 through W7: dependency-ordered serial work

1. W5 splits `self_improve/runtime.py` after managed-runner contracts land.
2. W6 splits `event_loop/loop.py` after repository and self-improvement facades
   are stable. Its broad monkeypatch inventory makes parallel edits unsafe.
3. W7 splits `daemon.py` last, after EventLoop and ModelGateway are stable and
   after router imports have moved to `daemon_components/state.py`.

## Module-specific implementation notes

### Pricing sources

`base.py` owns `PricingSource`, the authentication/data exceptions, shared JSON
validation, and `UnavailableModelPrices`. `model_apis.py` owns OpenRouter,
Anthropic, OpenAI, Hugging Face, Z.AI, and LiteLLM JSON sources.
`cloud_compute.py` owns RunPod, Lambda Labs, AWS, and GCP source pairs. `cache.py`
owns `CachedSource` and clock behavior; `registry.py` owns `all_sources` and
`staleness_text`. The facade supplies an HTTP client factory to provider
constructors, preserving existing `general_ludd.pricing_intel.sources.httpx`
patch behavior without making components import the facade.

### Hook runtime suite

`runner.py` owns `_run_ts` and TypeScript factory helpers. `fixtures.py` owns
isolated runtime roots, hermetic project creation, and cleanup. Case modules
align with plugin families rather than arbitrary line ranges. The facade imports
their test functions explicitly, not with wildcard imports, so the API manifest
can pin every node ID and pytest does not collect duplicate component paths.

### Repositories

`shared.py` owns tenant scoping, locked-error classification, and repository
errors. `todos.py` owns todo and task-return aggregates; `projects.py` owns
projects, relationships, features, and variable namespaces; `messaging.py` owns
audit events and agent messages; `operations.py` owns queues, human todos,
remediation, and Slurm jobs; `metrics.py` owns benchmarks, prompt profiles,
spend, role runs, and model performance; `memory.py` owns memory records. There
is no database migration because table models and transaction semantics do not
change.

### Agent watchdog

`types.py` holds TypedDicts, the state enum, and pure classifiers.
`state_store.py` implements bounded, atomic JSON/text access. `lease.py` owns
PID/start-time verified singleton ownership. `task_health.py` owns deadlines,
history, stall detection, and process termination. `ci_health.py` owns cached CI
and gate signals. `enforcement.py` owns streak, disengage, dispatch, liveness,
and continue-directive policy. `release.py` owns tag, secrets, and stale-release
checks. `cli.py` owns classification and the polling loop. A context built at
the facade boundary carries all paths, clocks, subprocess functions, and
monkeypatchable thresholds.

### Codex comparison

`contracts.py` owns proposal, span, manifest, reference, result, and sampling
identity dataclasses. `compact_protocol.py` owns strict schemas, batch codecs,
scope validation, and compact materialization. `proposal_merge.py` owns
preconditions and deterministic merge logic. `feedback.py` owns planner exchange,
comparison, safe diagnosis, and retry prompts. `local_gateway.py` owns the
optional llama-cpp adapter and grammar selection. None of these modules imports
managed runner or runtime; those higher layers consume only the facade or the
contracts module.

### CLI

`parser.py` builds the argparse graph from an explicit command registry and the
existing domain registrars. Remaining handlers group into core, model, compute,
platform, integrity, and daemon-control modules. `tui_views.py` receives the
pure Rich table builders; process lifecycle remains in daemon control. The
facade defines lightweight wrappers for every historically patched `_cmd_*` and
`_build_*` name, and the registry points at those wrappers so a facade patch is
observed at dispatch time.

### Managed runner

`contracts.py` owns `TaskSpec`, `PromptPlan`, generated proposal/result records,
and protocols. `approval.py` owns immutable approval identity and serialization.
`proposal_backends.py` owns local/remote decoding. `candidate_routing.py` owns
candidate planning and acquisition. `policy.py` owns privacy and execution
decisions. `execution.py` owns the runner state machine through mixins while the
facade creates the concrete `ManagedSelfImproveRunner`. `serialization.py` owns
strict JSON helpers. A runner instance remains the only owner of reservations,
leases, outcome adapters, and retry state.

### Model gateway

`contracts.py` owns all public errors, constants, protocols, `ModelProfile`, and
`ModelResponse`. `limits.py` owns request/response/cumulative budgets.
`routing.py` owns profiles, cost-aware selection, and runtime registrations.
`invocation.py` owns ordinary calls and billing. `streaming.py` owns sync/async
stream limits and cleanup. `failover.py` owns retry, semaphore, health, and
fallback traversal. Mixins never construct a second lock or registry; they use
the concrete facade instance. The facade's dependency-port object resolves
clock, sleep, tracker, and HTTP hooks per call so established patches still
work.

### Self-improvement runtime

`make_runner.py` owns bounded observable subprocess groups. `proposal_repair.py`
owns compact repair transitions. `prompt_context.py` owns repository excerpts
and prompt shards. `candidate_evaluation.py` owns apply, preflight, test,
inspection, commit, and cleanup. `managed_assembly.py` binds approved runners,
Azure wiring, and policies. `benchmark_cli.py` owns argument parsing and the
program entry point. The dependency DAG is codex contracts, managed contracts,
runtime components, then the runtime facade.

### Event loop

The existing review, handler, task-routing, runtime-helper, supervision, and
managed-dispatch modules remain authoritative. New mixins own tick/session
lifecycle, review claims, compute leases, execution dispatch, managed
self-improvement persistence, and completed-decision reconciliation. The
concrete `EventLoop` facade alone defines `__init__`, phase ordering, wake/stop,
and shared task registries. Typed self protocols document required attributes
without importing `EventLoop`. Widely patched functions such as `release_lease`,
`asyncio.to_thread`, `apply_rule_actions`, and generation invocation are
resolved through facade-owned ports rather than captured at mixin import time.

Forum evidence checked on 2026-10-06 reinforces both compatibility choices. A
[Python Help discussion about splitting a large class][python-mixin-thread]
recommends behavior-only mixins that do not initialize or own state; accordingly,
the extracted lifecycle mixins leave all mutable state and construction in
`EventLoop`. A [long-running pytest monkeypatch discussion][patch-lookup-thread]
documents that imported names stay bound in the namespace that uses them and
must be patched there. The facade therefore rebinds extracted method globals to
`general_ludd.event_loop.loop`, and the split characterization test pins that
legacy lookup seam instead of requiring downstream test rewrites.

[python-mixin-thread]: https://discuss.python.org/t/split-pyqt-project-in-many-files/11400
[patch-lookup-thread]: https://stackoverflow.com/questions/31306080/pytest-monkeypatch-isnt-working-on-imported-function/31746577

### Daemon

`config.py` owns startup overlays and model profiles; `secrets.py` owns resolver
selection; `state.py` owns the state proxy and lazy subsystems; `lifecycle.py`
owns resource acquisition and reverse-order teardown; `middleware.py` owns
public-path, CIDR, authentication, and statistics middleware; `subsystems.py`
owns MCP, STS, slow-operation, and self-update factories; `app_factory.py`
assembles routers and endpoints. Request Pydantic models may live with their
owning router after the facade first re-exports them. Router imports of daemon
internals are migrated to components before `app_factory.py` is introduced,
and `tests/unit/test_no_circular_imports.py` runs after every extraction step.

## Exact make-only validation sequence

Each module slice follows this sequence, substituting only its ledger test list
and explicit changed-file list:

1. Add the compatibility characterization first and prove it fails for the
   absent extraction or unresolved facade mapping with `make test-specific
   TESTFILE=tests/unit/test_python_split_compat.py PYTEST_ARGS='-q -W error'`.
2. Extract one behavior boundary, then run the ledger's exact `make test-files`
   command. For hook runtime, run `make test-hook-runtime` as its documented
   behavioral target.
3. Run `make lint-files FILES='explicit changed Python paths'` and
   `make typecheck-scope FILES='explicit changed production Python paths'
   MYPY_STRICT=1`.
4. Run `make coverage-files COVERAGE_TESTS='explicit focused tests'
   COVERAGE_SOURCE='explicit facade and component paths' COVERAGE_MIN=85
   COVERAGE_PER_FILE_MIN=75 COVERAGE_BRANCH=1 COVERAGE_FAIL_UNDER=85
   COVERAGE_CONFIG_FILE='config/coverage_python_split.ini'
   OBSERVED_ROOT='/tmp/gludd-python-split-coverage' OBSERVED_HEARTBEAT_SECS=10
   OBSERVED_QUIET_SECS=60 OBSERVED_MAX_SECS=900 OBSERVED_RETAIN_RUNS=3`.
5. Run `make test-files TESTFILES='tests/unit/test_python_split_compat.py
   tests/unit/test_no_circular_imports.py' PYTEST_ARGS='-q -W error'` and the
   module's adjacent package-level export tests.
6. Run `make check-file-line-limits
   FILE_LINE_LIMIT_POLICY=config/file_line_limits.json`, then commit only that
   complete module slice. Do not run a broad gate in a worker branch.

After each sequential merge, the orchestrator repeats compatibility, affected
cross-feature tests, and the line-limit check on the integration head. After W7,
it runs collection and the full exact-head gate under the project's serialized
resource policy. A failed slice is reverted before the next dependent wave;
coverage thresholds, patch-target checks, or import-cycle checks are never
waived to keep a split.
