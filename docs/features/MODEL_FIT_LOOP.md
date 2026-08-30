# Model fit loop

Status: implemented. The loop that selects a model per job type from recorded
outcomes, re-picks after each recorded outcome, and rejects misbehaving
generated code — with the `local_game_gen` role retrying on rejection.

## The loop in one sentence

Every model call writes an immutable outcome row into `model_call_logs`;
`ModelPerformanceRouter` picks the best (service, model) for the next job of
the same type from those rows; generated code passes through an acceptance
gate; and the `local_game_gen` role turns a rejection into a corrective-prompt
retry on the next fallback model, bounded by `max_attempts`.

## Outcome recording — the weight DB

`ModelPerformanceRepository` (`src/general_ludd/db/repository.py:2513`) is a
two-table store:

- `model_call_logs` — one immutable row per model invocation, written by
  `record_call()` (`src/general_ludd/db/repository.py:2540`) with
  `service`, `model_name`, `model_profile_id`, `task_type`, `success`,
  `cost_usd`, `duration_ms`, and token/error fields. A synchronous
  fail-soft variant `record_call_sync()` exists for `asyncio.to_thread`
  worker paths.
- `model_performance` — pre-aggregated per-profile stats, recomputed in
  batch by `refresh_recent_stats()` (`src/general_ludd/db/repository.py:2647`,
  rolling window, default 24 hours) for dashboard reads.

Router queries (`get_ranking`, `get_best_model`) group directly from
`model_call_logs` so selection always sees the latest recorded outcomes
without waiting for an aggregate refresh.

## Outcome-weighted selection per job type

`ModelPerformanceRouter.select_model(task_type, ...)`
(`src/general_ludd/models/performance_router.py:105`) resolves a pick in
three steps:

1. `repo.get_best_model(task_type, min_calls, prefer_cost)` — considers only
   (service, model) rows with `sample_count >= min_calls` (default 3). By
   default the highest success rate wins with average cost as tiebreak; with
   `prefer_cost` (the `cheapest` strategy) the lowest average cost wins with
   success as tiebreak (`src/general_ludd/db/repository.py:2881`).
2. If no model meets the sample minimum, `get_rankings()` computes a
   composite score per strategy (`src/general_ludd/models/performance_router.py:179`):
   min-max-normalized success rate, latency, and cost combined with the
   weights in `DEFAULT_STRATEGIES` (`src/general_ludd/models/performance_router.py:48`):
   `balanced` (0.5/0.25/0.25), `quality` (success only), `cheapest` (cost
   only), `fastest` (latency only). Strategies are set per task type with
   `set_strategy()`.
3. With no history at all, the pick falls back to the configured
   `default_fallback` (`openai/gpt-4o`) with `fallback: True` and reason
   `no_historical_data`.

The pick dict carries `service`, `model_name`, `score`, `strategy`,
`fallback`, and `reason` (`historical_best`, `strategy_ranked`,
`no_performance_repo`, or `no_historical_data`).

## Reassessment after outcomes are recorded

Selection is a pure read of recorded outcomes, so the next
`select_model()` for the same `task_type` reflects the latest rows. A
rejected outcome for the current pick lowers its success rate and can flip
the next selection to a different model. Outcomes are keyed per job type —
`bug_fix` history never influences a `feature` pick.

Pinned by `tests/unit/test_model_fit_loop.py` (real
`ModelPerformanceRepository` on an in-memory SQLite database, no mocks):

- `TestOutcomeWeightedSelection::test_good_outcome_beats_rejected_outcome`
- `TestOutcomeWeightedSelection::test_rejected_outcome_flips_the_pick`
- `TestOutcomeWeightedSelection::test_outcomes_are_keyed_per_job_type`
- `TestOutcomeWeightedSelection::test_reassessment_reflects_latest_outcomes`

## The acceptance engine

`src/general_ludd/game_gen/acceptance.py` is the gate that rejects
misbehaving model-generated game code. `accept_generated_code(path,
timeout_seconds=10.0)` (`acceptance.py:509`) is the file-level entry point:
static checks run in-process and never execute the code; the runtime
exercise runs in a subprocess killed on `TimeoutExpired`, so an infinite
loop at import or inside a method cannot hang the caller. `check_source()`
(`acceptance.py:375`) is the in-process variant using a `SIGALRM` timer
(default budget 5 seconds). There is also a CLI:

```text
python -m general_ludd.game_gen.acceptance /tmp/artifacts/snake.py
```

Checks, in order:

1. Content sanity — empty source, or more than 10% non-ASCII characters
   (`NON_ASCII_RATIO_LIMIT`), is rejected as junk.
2. Syntax — the module must parse.
3. Forbidden imports/calls — `FORBIDDEN_MODULES` (`os`, `sys`,
   `subprocess`, `socket`, `pickle`, `ctypes`, `shutil`, `multiprocessing`,
   `importlib`, `builtins`, `marshal`, `fcntl`, `pty`, `resource`, and
   `io` attribute calls), `FORBIDDEN_CALLS` (`eval`, `exec`, `compile`,
   `open`, `input`, `__import__`), `FORBIDDEN_ATTR_CALLS` (`os.system`,
   `os.popen`, `os.spawnl`, `os.spawnlp`, `os.spawnv`, `os.spawnvp`,
   `io.open`), and `__builtins__[...]` access. Any hit rejects the module
   without executing it.
4. Game class contract — the first class must define `__init__`, `start`,
   `tick`, `score`, `is_game_over`, `restart` (`REQUIRED_METHODS`).
5. Bounded runtime exercise — instantiate, `start()`, `score()` (must return
   a non-bool `int`), `is_game_over()` (must return `bool`), up to 5
   `tick("right")` calls, `restart()` — all under the hard wall-clock
   budget. Excessive output (over `MAX_OUTPUT_CHARS`, 4096 chars) and
   non-ASCII junk output also reject.

The result is a single `AcceptanceResult` (accepted + reasons +
game class name + output snippet + elapsed seconds). Pinned by
`tests/unit/test_generated_code_acceptance.py`:
`test_valid_game_class_is_accepted`, `test_syntax_error_is_rejected`,
`test_no_class_is_rejected`, `test_missing_required_methods_is_rejected`,
`test_dangerous_import_is_rejected`,
`test_import_time_hang_is_rejected_with_timeout`,
`test_junk_output_is_rejected`, `test_rejection_carries_all_reasons`.

## Role rejection handling — daemon-owned corrective retry

`collections/ansible_collections/general_ludd/agent/roles/local_game_gen/tasks/generate_and_verify.yml`
is a recursive generate → verify loop. Each invocation:

1. Selects the model for the attempt: `model_repo` on attempt 1 (or when
   `fallback_models` is empty), otherwise
   `fallback_models[min(_attempt - 2, len - 1)]`.
2. Selects the prompt for the attempt: `game_prompt` on attempt 1 and
   `retry_prompt` on later attempts.
3. Calls the typed `general_ludd.agent.gludd_local_model` module over the
   authenticated Gludd daemon API. The daemon owns model download, process
   launch, kernel-assigned/validated endpoint identity, diagnostics, and
   teardown; the collection never installs `llama-cpp-python`, invokes
   `nohup`, writes a PID file, or signals a process.
4. Consumes the daemon-issued `server_id`, writes the generated code, and
   runs acceptance, AST, import, and runtime checks through safe
   `command.argv` entries.
5. On failure, the rescue records the failed check, increments `_attempt`,
   asks the daemon to shut down the current server before acquiring a fallback,
   and either retries below `max_attempts` or fails closed. An outer
   `always` block requests daemon shutdown on success, failure, or
   cancellation.

Defaults live in the role's `defaults/main.yml`: the loopback daemon URL,
authenticated PSK input, bounded daemon/startup timeouts, model identity,
`max_attempts: 1`, `fallback_models: []`, and the corrective
`retry_prompt`.

Pinned by `tests/unit/test_game_gen_rejection_retry.py`,
`tests/unit/test_game_gen_local_pipeline.py`, and
`tests/unit/test_local_game_gen_daemon_lifecycle.py`. The real
`local_game_gen` Molecule scenario uses a random-port, manifest-owned mock
daemon and proves endpoint closure during handler cleanup and the
cleanup/destroy backstops.

## Practitioner and upstream evidence (2026-08-30)

- A llama-cpp-python user asked how to unload a model from GPU/RAM without
  exhausting memory when switching models
  ([issue #302](https://github.com/abetlen/llama-cpp-python/issues/302)).
- Another user reported server port behavior changing across invocations and
  the requested `--port` apparently being ignored
  ([issue #1359](https://github.com/abetlen/llama-cpp-python/issues/1359)).
- An Ansible user reproduced concurrent async-state races and described the
  detached supervisor as an orphaned process
  ([ansible/ansible #59306](https://github.com/ansible/ansible/issues/59306)).
- The upstream server documentation defines the server as a separately
  installed extra with CLI/environment configuration
  ([server documentation](https://github.com/abetlen/llama-cpp-python/blob/main/docs/server.md)).

These reports do not establish Gludd defects by themselves. They motivate the
tested boundary: one daemon owns model memory, process identity, endpoint
selection, readiness, logs, and shutdown; collection roles are authenticated
clients only.

## ZDD, rollback, and resources

The role publishes no successful game artifact until generation and validation
complete; rejected artifacts are removed before failure is surfaced. Daemon
model acquisition and readiness must return a stable model/server identity
before the role proceeds. Cleanup is idempotent and identity-bound, so a stale
or foreign process cannot be signaled. Rollback is the previous committed role
plus daemon API module; no package or process state is installed on the managed
host. Resource ceilings are `max_attempts`, `daemon_timeout`,
`server_startup_timeout`, `context_size`, `max_tokens`, and one active
server identity per role invocation.

## Running the live e2e

`tests/e2e/test_model_fit_loop_live.py` proves the loop against a real local
model through the managed lifecycle. It is skipped unless
`GLUDD_LIVE_MODEL_E2E=1` and needs the locked local-inference extra plus a
readable GGUF artifact.

```text
GLUDD_LIVE_MODEL_E2E=1 make test TESTFILE=tests/e2e/test_model_fit_loop_live.py
```

The test uses a bounded startup budget and a bounded graceful-shutdown budget;
normal completion, startup failure, and assertion failure all converge through
the same owner cleanup path.

## Known boundaries

- **Static capability priors are not learned.** `src/general_ludd/models/model_scoring.py`
  ships `_MODEL_CAPABILITIES` — a static per-task-type matrix
  (`bug_fix`, `feature`, `review`, `chat`, `generate`) of success,
  latency, and cost estimates per known model, with `_DEFAULT_CAPABILITY`
  for anything unknown. `score_model`/`rank_models`/`best_model` use these
  priors plus a `BudgetProfile`; they do not read `model_call_logs`. The
  learned path is the performance router, not the scoring matrix.
- **Small-model capability evidence is separate from the fit loop.**
  `src/general_ludd/small_models/` (evidence store, lm-eval harness, radar
  profiles, recommender) evaluates and records capability evidence for
  small models; it does not feed `ModelPerformanceRepository`.
- **Cold start needs samples.** `get_best_model` requires
  `sample_count >= min_calls` (default 3) per (service, model, task type)
  before historical selection engages; below that the router falls through
  to the strategy ranking or the configured fallback.
- **One role-owned server identity at a time.** The role can retry across
  fallback models, but it holds only one daemon-issued `server_id` at each
  step and shuts that identity down before the next serve request. The daemon,
  rather than the role, selects and owns the concrete process and endpoint.
- **Local models are opt-in.** `_local_model_available()` requires
  `GLUDD_ALLOW_LOCAL_MODEL_BASE_URLS=1` before a `LOCAL_MODEL_BASE_URL`
  pointing at localhost is treated as a local (non-cloud) source
  (`src/general_ludd/models/model_scoring.py:58`).
