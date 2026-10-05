# AG.15 — Reflexion Loops Architecture

## Overview

Reflexion loops give agents the ability to self-critique their own outputs and
iteratively improve across multiple attempts. The pattern mirrors the cognitive
cycle of try → evaluate → reflect → retry, storing lessons learned in a
persistent memory for cross-episode improvement.

## Core Loop

```text
┌──────────┐   output   ┌──────────────┐   score   ┌──────────────┐
│  TRY     │──────────▶│  EVALUATE     │─────────▶│  REFLECT      │
│ (actor)  │           │  (critic)     │          │  (self-crit)  │
└──────────┘           └──────────────┘          └──────┬─────────┘
     ▲                                                  │
     └──────────────────────────────────────────────────┘
                        retry with feedback
```

Each episode produces an `EpisodeRecord` containing the actor's output, the
critic's evaluation, and a textual reflexion that steers the next attempt.

## Data Model

```text
ReflexionEpisode:
  - episode_id: str
  - task_description: str
  - actor_output: str
  - evaluation_score: float         # 0.0–1.0 quality score
  - reflexion_text: str             # what went wrong + how to improve
  - retry_count: int                # which attempt this is
  - created_at: datetime

ReflexionMemory:
  - episodes: list[ReflexionEpisode]
  - add(episode) → None
  - recent_feedback(n=3) → str      # concatenated reflexions for prompt
```

## Configurable Knobs

| Knob              | Default | Meaning                          |
|-------------------|---------|----------------------------------|
| max_retries       | 3       | Max attempts before giving up    |
| score_threshold   | 0.8     | Stop when evaluation >= this     |
| memory_window     | 5       | How many past reflexions to keep |

## Loop Algorithm

1. **Try** — actor generates a response for the task.
2. **Evaluate** — critic scores the output on correctness/completeness.
3. **Decide** — if score ≥ threshold or retries exhausted, stop.
4. **Reflect** — generate a textual critique explaining failures.
5. **Retry** — inject reflexion + prior feedback into the actor's context.

## Integration Points

- **LangGraph agent loop** — the ReflexionLoop wraps an existing agent graph
  as a parent graph with concurrency-control edges.
- **Prompt injection** — reflexion text is appended to the system prompt on
  retry attempts.
- **Memory store** — uses the gludd LangGraph `Store` API for durable
  cross-session reflexion memory.
- **Observability** — each episode records a structured log entry with
  episode_id, score, and retry_count for debugging.

## Future Extensions

- Multi-critic ensemble (normalize scores across evaluators)
- Actor-critic RL integration for tuning prompts automatically
- Reflexion-to-fewshot conversion (synthesize examples from successful episodes)
- Per-task reflexion templates for known failure modes
