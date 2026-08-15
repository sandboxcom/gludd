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

## Role rejection handling — corrective retry + model fallback

`collections/ansible_collections/general_ludd/agent/roles/local_game_gen/tasks/generate_and_verify.yml`
is a recursive generate → verify loop. Each invocation:

1. Selects the model for the attempt: `model_repo` on attempt 1 (or when
   `fallback_models` is empty), otherwise
   `fallback_models[min(_attempt - 2, len - 1)]`.
2. Selects the prompt for the attempt: `game_prompt` on attempt 1,
   `retry_prompt` (the corrective prompt) on later attempts.
3. Runs the generate-and-verify block: POST to
   `http://<server_host>:<server_port>/v1/completions` (timeout 300), write
   the extracted code to the artifact, then three verify steps — AST parse,
   module import, runtime lifecycle checks.
4. On any failure, the `rescue` records a REJECTED event naming the failing
   check (`AST parse`, `module import`, or `runtime checks` in
   `_verify_failed_check`), increments `_attempt`, and either re-includes
   this file with `_attempt < max_attempts` or fails the role hard
   (`ansible.builtin.fail`) when attempts are exhausted — bounded retry,
   no infinite loop.

Defaults live in the role's `defaults/main.yml`: `model_repo:
bartowski/Qwen2.5-0.5B-Instruct-GGUF`, `max_attempts: 1` (preserving the
original single-shot behavior), `fallback_models: []`, and a `retry_prompt`
that instructs the model to "CORRECT THE PREVIOUS FAILURES".

Pinned by `tests/unit/test_game_gen_rejection_retry.py`:
`TestRejectionEventSurfacing`, `TestCorrectivePromptRetry`,
`TestModelFallback`, `TestBoundedRetry`, plus the self-pin
`test_rejection_retry_test_count`.

## Running the live e2e

`tests/e2e/test_model_fit_loop_live.py` proves the full loop against a real
local model. It is skipped unless `GLUDD_LIVE_MODEL_E2E=1`; it downloads a
GGUF and starts `llama_cpp.server`, so it needs network access and the
`llama-cpp-python[server]` extra.

```text
GLUDD_LIVE_MODEL_E2E=1 make test TESTFILE=tests/e2e/test_model_fit_loop_live.py
```

- Model served: `GLUDD_LIVE_MODEL_REPO` (default
  `bartowski/Qwen2.5-0.5B-Instruct-GGUF`) and `GLUDD_LIVE_MODEL_FILE`
  (default `Qwen2.5-0.5B-Instruct-Q5_K_M.gguf`).
- Test: `test_live_generate_score_record_reassess` — serve the model, ask
  it "The capital of France is", score the completion, record a good and a
  bad profile into a real `ModelPerformanceRepository`, assert
  `router.select_model()` prefers the better-scored model, and assert the
  `quality` ranking agrees.
- Runtime bound: `pytest.mark.timeout(480)` — the test must finish within 8
  minutes. Server health poll budget is 300 seconds; graceful shutdown
  budget 30 seconds.

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
- **Single-server assumption.** The `local_game_gen` role and the live e2e
  assume exactly one local OpenAI-compatible server
  (`server_host:server_port`, default `127.0.0.1:9999` for the role; a free
  loopback port in the e2e) serving one model per run. There is no
  multi-model, multi-server concurrency in these paths.
- **Local models are opt-in.** `_local_model_available()` requires
  `GLUDD_ALLOW_LOCAL_MODEL_BASE_URLS=1` before a `LOCAL_MODEL_BASE_URL`
  pointing at localhost is treated as a local (non-cloud) source
  (`src/general_ludd/models/model_scoring.py:58`).
