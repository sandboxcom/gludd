# estimate_story

Assigns story points (Fibonacci: 1, 2, 3, 5, 8, 13, 21) from complexity heuristics calibrated by historical velocity from `facts.history`.

## Point Computation

1. Compute complexity score from: word count / 10, +2 if "integrat*", +2 if "secur*", +1 if "test*", +1 if "migrat*", +1 if backlog > 5
2. Map score to Fibonacci: 0-1 → 1pt, 2 → 2pt, 3 → 3pt, 4-5 → 5pt, 6-7 → 8pt, 8-10 → 13pt, >10 → 21pt
3. Historical context (total_runs, success_rate) is included in rationale

## Variables

| Variable | Default | Description |
|---|---|---|
| `story_id` | `""` | Fetch story from gludd_db (takes precedence) |
| `story_text` | inline story | Inline story text |
| `scale` | `fibonacci` | Point scale |
| `enable_model_call` | `false` | Use model for rationale |
| `artifact_dir` | `/tmp/gludd-estimate-story` | Output directory |

## Artifacts

- `estimate_story.json` — points (∈ scale), rationale, complexity_score, history_context
- `estimate_story.md` — markdown estimate card
