# Local-SLM context compaction with a self-improving eval loop

**Status:** package landed (`src/general_ludd/compaction/`, tests in
`tests/unit/test_compaction.py`); integration is opt-in and staged (see §6). The
whole package runs and tests **fully offline** — the model call is a plain
injected callable and the fidelity judge defaults to an offline check.

## 1. Problem

Every work item we hand the main LLM ships a context: the message history, tool
results, and recalled memory. That context grows every tool-loop turn, and most
of it is irrelevant to the *current* work item. We pay for those tokens twice —
in money and in the model's attention budget — and the excess actively degrades
answer quality (lost-in-the-middle).

We want to **compact the context sent per work item** so that:

1. it is materially smaller (fewer tokens), and
2. the information *that specific work item needs* still survives, and
3. the compaction is cheap — ideally done by a **small model running locally**,
   not a paid frontier call, and never blocking on the network.

And critically: we want the choice of compaction strategy to be a **measured,
self-correcting claim** — "this beats every other mechanism we have" backed by
an eval score on a real corpus, not an assertion. That is what this package
provides.

### Why a *local* SLM

A summarizer that runs on a paid frontier API to save frontier tokens is
robbing Peter to pay Paul, and it adds a network hop and a failure mode to the
hot context path. A small model (0.5B–3B params) quantized to 4-bit runs on the
operator's own machine via gludd's existing llama.cpp / OpenAI-compatible local
path, at effectively zero marginal cost and low latency. Summarization is a
*compression* task, not a reasoning task — it is exactly where small models are
strong and where the frontier model is overkill.

## 2. Local SLM recommendations for the `compactor` role

These are general recommendations for the compactor slot. All run through
gludd's local-inference path (an OpenAI-compatible endpoint served by
llama.cpp / vLLM / Ollama), so switching models is a config change, not a code
change. Sizes assume 4-bit GGUF (`Q4_K_M`) unless noted.

| Model | License | Params | Why / when |
|---|---|---|---|
| **Qwen2.5-1.5B-Instruct** (**default**) | Apache-2.0 | 1.5B | Best quality-per-byte in the class; strong instruction following and structure retention. `Q4_K_M` GGUF is ~1 GB RAM. **Recommended default compactor.** |
| Qwen2.5-0.5B-Instruct | Apache-2.0 | 0.5B | When RAM/latency is tight but you still want Qwen's fidelity. |
| **SmolLM2-360M-Instruct** | Apache-2.0 | 360M | **Ultra-fast** tier — sub-100ms on CPU, ~250 MB. Use when compaction latency must be invisible and the context is not too subtle. |
| SmolLM2-1.7B-Instruct | Apache-2.0 | 1.7B | A fast, permissive alternative to Qwen2.5-1.5B. |
| Llama-3.2-1B / 3B-Instruct | Llama Community | 1B / 3B | Excellent quality; only if the Llama license is acceptable for the deployment. |
| Gemma-2-2B-it | Gemma terms | 2B | Strong 2B option; check license fit. |
| Phi-3.5-mini-instruct | MIT | 3.8B | MIT-licensed, strong reasoning if you want a slightly larger compactor and have the RAM. |
| FLAN-T5 (base/large) | Apache-2.0 | 250M–780M | Pure *summarization* seq2seq — good if you only ever want extractive/abstractive summary and never chat-style steering. |
| LLMLingua-2 | MIT | (compressor) | Not a chat model — a **token-level prompt compressor**. Complementary: run it *after* the SLM summary to squeeze the residual, or as an alternative baseline in the arena. |

**Recommendation:**

- **Default:** `Qwen2.5-1.5B-Instruct` @ `Q4_K_M` via the local llama.cpp path.
  Best fidelity/size trade-off, Apache-2.0, no license friction.
- **Ultra-fast tier:** `SmolLM2-360M-Instruct` when compaction must be
  effectively free in latency.
- **Graceful offline fallback:** if no local model is served (or the call
  fails), the `SLMCompactor` degrades to a deterministic model-free extractive
  summary (head+tail) — compaction never crashes the context path (§4).

These recommendations are from general knowledge and are not a substitute for
running the arena (§5) against your own corpus, which is the authority on which
model+strategy actually wins.

## 3. Architecture

Everything is dependency-injected. A "compactor" is anything satisfying the
`Compactor` protocol; the SLM call is a plain `summarize_fn(goal, text) -> str`.

### 3.1 Core types (`base.py`)

- **`ContextMessage`** — reused verbatim from `agents/context.py`
  (`role`, `content`, `token_estimate`, `is_system`, `timestamp`). Using the same
  record the hibernation snapshotter and `ContextCompactor` already use means a
  compactor drops straight into the existing context path.
- **`CompactionRequest`** — `messages`, plus:
  - `goal` — the **current work item's objective**. This is the steering signal:
    a work-item-aware compactor keeps goal-relevant facts and drops the rest.
  - `target_tokens` — soft output budget (`None` = strategy's discretion).
  - `preserve_recent` — always keep at least this many recent non-system
    messages verbatim (recency is the cheapest, safest signal).
- **`CompactionResult`** — the shrunken `messages` + metrics (`method`,
  `original_tokens`, `compacted_tokens`, `dropped_messages`) and derived
  `ratio` (compacted/original) and `tokens_saved`.
- **`estimate_tokens` / `messages_tokens`** — the same `len // 4` heuristic as
  `ContextCompactor`, one shared definition so ratios are **apples-to-apples**
  across every strategy.
- **`Compactor`** (runtime-checkable `Protocol`) — `name: str` +
  `compact(request) -> CompactionResult`. Implementations MUST be **pure w.r.t.
  the request** (no hidden state) so the arena can evaluate several side by side
  deterministically.

### 3.2 Baselines (`baselines.py`) — the mechanisms the SLM must beat

- **`NoOpCompactor`** (`noop`) — returns context unchanged. The **fidelity
  ceiling / compression floor** — the control.
- **`TruncationCompactor`** (`truncate`) — keep all system messages + the most
  recent `preserve_recent` (or as many as fit under `target_tokens`), drop the
  rest. The honest, cheapest baseline: preserves recency, blindly discards older
  content (and any fact that lived there).
- **`ContextCompactorAdapter`** (`context_compactor`) — wraps the **existing**
  in-tree `agents.context.ContextCompactor` so today's mechanism is a
  first-class competitor. It summarizes old messages into one `[prior context]`
  system message; with an injected `summary_fn` (e.g. an SLM) it produces a real
  abstractive summary instead of the default 500-char truncation. Threshold is
  forced to `0.0` so it always actually compacts in the arena.

### 3.3 The SLM compactor (`slm.py`)

`SLMCompactor` is the work-item-aware strategy:

- Keep every system message and the most-recent `preserve_recent` messages
  **verbatim**; replace the older middle with a single
  `[prior context — compacted for goal]` system message produced by the SLM.
- The model call is the injected `summarize_fn(goal, text)`. The prompt
  (`_SYSTEM_PROMPT`) instructs the model to keep decisions, facts, file paths,
  identifiers, errors, and open questions relevant to `GOAL`, drop chit-chat,
  and preserve exact names/numbers.
- **Fail-soft, always.** If `summarize_fn` is `None`, raises, or returns
  empty/non-string, the compactor falls back to a deterministic **extractive**
  summary (`head + "...[trimmed]..." + tail` of the old text). Head+tail keeps
  both the earliest setup and the latest state — more faithful than raw
  head-truncation. Compaction sits on the hot context path and **must never
  crash it**.
- Honors `target_tokens` by trimming the summary to the residual budget.

### 3.4 The evaluation harness (`evaluate.py`) — compression × fidelity

A good compactor cuts tokens *without losing what the work item needs*, so each
candidate is scored on two axes over a corpus of `EvalSample`s:

- **compression** = `1 - mean_ratio` (how much smaller it got).
- **fidelity** = fraction of `Probe`s retained. A `Probe` names `expected`
  substrings — exact file paths / ids / numbers / decisions — that must still be
  recoverable from the compacted context.

The default **judge** is an offline keyword-retention check (every expected
substring appears in the compacted text). A stricter semantic judge (`judge_fn`,
e.g. an LLM judge) can be injected without changing anything else.

```text
score = fidelity_weight * mean_fidelity + compression_weight * (1 - mean_ratio)
        (defaults: 0.7 fidelity, 0.3 compression)
```

Fidelity is weighted higher on purpose: a drop-everything strategy tanks
fidelity, a no-op tanks compression, and the winner is the one that balances
both. Because `score` is what the arena maximizes, **"beats every other
mechanism" is a measured number, not an assertion.**

### 3.5 The champion/challenger arena (`arena.py`)

`SelfImprovingCompactor` makes the "best of breed" claim self-correcting:

1. Hold a **champion** compactor (the one actually deployed).
2. `generate_candidates(summarize_fn)` builds a diverse pool — noop ceiling,
   truncation, the existing `ContextCompactor` (with and, if a summarizer is
   present, without an SLM `summary_fn`), and `SLMCompactor` at a couple of
   `preserve_recent` depths. With `summarize_fn=None` the SLM entries use their
   offline fallback, so the pool still runs with no model.
3. `run_arena(candidates, corpus, incumbent=...)` evaluates every candidate,
   sorts a leaderboard best-score-first, and — if an `incumbent` is named —
   promotes a challenger **only if it beats the incumbent by `min_improvement`**.
   A **gated, no-regression swap** mirroring gludd's `self_improve` gate + abtest
   philosophy.
4. `SelfImprovingCompactor.improve(corpus)` runs a round and promotes (logged)
   only on a strict, gated win; `.compact(request)` just delegates to the
   current champion, so it *is* a `Compactor` you can deploy directly.

Because the champion is always the arena's top scorer on the corpus, the
deployed compactor is **by construction the best of the evaluated pool**. The
test `test_self_improving_champion_beats_all_others` asserts exactly this: after
`improve()`, `champion_score == max(leaderboard scores)`.

## 4. Fail-soft & offline guarantees (why this is safe to sit on the hot path)

- No model? `summarize_fn=None` → deterministic extractive fallback.
- Model errors/times out/returns junk? Caught → same extractive fallback,
  logged at WARNING. `make_slm_summarize_fn` itself never raises — on any
  gateway error it returns `""`, which trips the fallback.
- Empty context or "nothing old to summarize"? Pass-through, `dropped=0`.
- Every strategy is pure and deterministic given a request, so arena rounds are
  reproducible and unit-tests need no network (they inject a fake summarizer).

## 5. The self-improvement + testing loop

### How "beats any other mechanism" is measured

The arena is the referee. On each round it scores the incumbent, all baselines,
and all challengers on the **same corpus with the same judge**, and only the
strict, gated winner is deployed. The claim degrades gracefully to the truth on
the day a baseline is actually better — you cannot ship a regression, and you
can read the leaderboard to see *by how much* the champion leads.

The headline unit test (`test_self_improving_promotes_better_champion`) uses a
corpus where the goal-relevant fact (`config/secrets.yml`, retry cap `42`,
`postgres`) lives in an **old** message that recency-truncation throws away:
truncation compresses but fails the probes; a faithful SLM summary compresses
**and** keeps the facts, wins the score, and gets promoted from the `truncate`
champion. `test_lossy_slm_scores_below_good_slm` proves the score punishes a
model that compresses but drops facts — fidelity is not free.

### Building a real eval corpus from gludd run history

The synthetic corpus in the tests is a proof of mechanism. The *real* signal
comes from gludd's own history:

1. Take completed task runs and their returns (task `goal`/objective +
   final result + the context the agent actually saw). The hibernation
   snapshots (`agents/hibernation.py`, `messages: list[ContextMessage]`) and the
   run/return records are the raw material — same `ContextMessage` type the
   compactor consumes, so no adaptation is needed.
2. For each, derive `Probe`s from the facts the task's return actually depended
   on — the file paths it edited, the ids/numbers it quoted, the decision it
   recorded. Those are the things a compaction MUST NOT lose.
3. Grow this corpus over time; re-run `improve()` periodically. As the corpus
   gets more representative of real work, the promoted champion tracks what
   actually helps real work items. Optionally swap the offline keyword judge for
   an LLM `judge_fn` for a stricter semantic fidelity check on the promotion
   gate.

This is the loop: **real returns → probes → corpus → arena → gated promotion →
better deployed compactor**, repeat.

## 6. Integration seam (opt-in, beside `ContextCompactor` + hibernation)

The compaction package is now **daemon-wired** — integrated into the hot
context path at three points:

| Wiring point | Location | What it does |
|---|---|---|
| Compactor construction | `daemon.py:1452-1473` | Builds `SelfImprovingCompactor` via `build_self_improving_compactor()`, wires champion + `CompactionMetrics` onto `app.state` |
| Admin status endpoint | `daemon.py:2754-2768` | Exposes `GET /admin/compaction/eval-status` returning champion name, metrics, and wired flag |
| Generation path consumer | `worker/app.py:157-173` | Consumes compaction config on the generation path via EventLoop config dict |

`ContextCompactorAdapter` keeps the current mechanism as a competitor rather
than replacing it — a safe migration: prove the SLM strategy wins in the arena
before it ever touches production context.

The **hibernation snapshotter** (`agents/hibernation.py`, see
`docs/design/AGENT_HIBERNATION.md`) is orthogonal and complementary:
hibernation offloads an *idle* agent's whole context to disk to reclaim RAM;
compaction shrinks the *active* context sent to the LLM for a work item. A
natural future pairing is to compact-then-snapshot (smaller snapshots) — noted
in the hibernation doc as a future optimization.

**Improvement loop (periodic, offline):**
Periodically (offline / on a schedule) `.improve(corpus)` is called against the
run-history corpus to consider promotions. Promotions are gated and logged.

## 7. Wiring: the `compactor` model profile

gludd defines one profile **per file** under `config/model_profiles/*.yml`
(loaded by `load_model_profiles()`, `daemon.py:458` — globs `*.yml`, skips
`_`-prefixed and `enabled: false`); `config/model_routing.yml` only maps roles
to profile ids. There is already a `config/model_profiles/llamacpp_example.yml`
(and `vllm_example.yml`) — the compactor profile is a copy of that pattern.

**Important:** `ModelProfile` (`models/gateway.py:75`) has **no literal
`base_url`/`api_key` field**. The endpoint and credential are indirected as
**env-var alias names** via `api_base_alias` / `credential_alias`, resolved
through gludd's secrets/alias layer (and gated by the SSRF egress guard). For a
local server you set e.g. `LLAMACPP_BASE_URL=http://127.0.0.1:8080/v1` in the
environment and reference the *alias name* in the profile.

Add a small, local, **unmetered** profile — `config/model_profiles/compactor.yml`:

```yaml
# config/model_profiles/compactor.yml
model_profile_id: compactor
role_names: [compactor]
provider: openai                 # NOTE: `provider` is an unvalidated free-form label (any string works); the REAL client selector is provider_package + provider_class_hint below
provider_package: langchain_openai
provider_class_hint: ChatOpenAI
model_name: qwen2.5-1.5b-instruct-q4_k_m   # or smollm2-360m-instruct for ultra-fast
api_base_alias: LLAMACPP_BASE_URL   # env-var NAME; export LLAMACPP_BASE_URL=http://127.0.0.1:8080/v1
credential_alias: null              # keyless: local servers ignore the api_key; gateway skips injection
context_window: 8192
max_input_tokens: 6144
max_output_tokens: 512
cost_per_input_token: 0.0
cost_per_output_token: 0.0
api_metered: false               # free local inference — off the run-budget meter
run_budget_usd: 0.0
enabled: true
resource_profile: ai_heavy
roles: [compactor]
latency_class: fast
quality_class: medium
fallback_profiles: []
probe_enabled: false
```

Then route the role at it in `config/model_routing.yml`:

```yaml
role_routing:
  # ...existing roles...
  compactor: compactor
```

Wire the summarizer:

```python
from general_ludd.compaction import (
    make_slm_summarize_fn, build_self_improving_compactor,
)

summarize_fn = make_slm_summarize_fn(gateway, "compactor")   # -> summarize_fn(goal, text)
compactor    = build_self_improving_compactor(summarize_fn, champion_name="truncate")

result = compactor.compact(CompactionRequest(
    messages=work_item_context, goal=work_item.objective,
    target_tokens=6000, preserve_recent=4,
))
send_to_main_llm(result.messages)
```

`make_slm_summarize_fn` calls `gateway.call_model("compactor", messages=[...],
requested_max_output_tokens=512)` and returns `ModelResponse.content`; on any
error it returns `""` and the compactor's fail-soft extractive path engages —
so a missing or down local model degrades quietly rather than breaking the
context path.

## 8. File map

| File | Contents |
|---|---|
| `compaction/base.py` | `CompactionRequest`/`CompactionResult`, `Compactor` protocol, token helpers |
| `compaction/baselines.py` | `NoOpCompactor`, `TruncationCompactor`, `ContextCompactorAdapter` |
| `compaction/slm.py` | `SLMCompactor`, `make_slm_summarize_fn` |
| `compaction/evaluate.py` | `Probe`, `EvalSample`, `CompactionMetrics`, `evaluate` |
| `compaction/arena.py` | `SelfImprovingCompactor`, `run_arena`, `generate_candidates`, `build_self_improving_compactor` |
| `tests/unit/test_compaction.py` | offline tests incl. the "champion beats all others" guarantee |
