# Self-Improvement Task-Shape Evidence

## Decision

Local-model self-improvement evidence is scoped to an exact existing task shape:

```text
(model_profile_id, TaskType, DEFAULT_TASK_CONTRACTS task_kind)
```

No new task taxonomy is introduced. The coarse dimension is the existing
`TaskType` enum and the capability dimension is an existing default small-model
task contract. A legacy record without `task_type` remains auditable in the
JSON store but is excluded from self-improvement model selection.

The planner classifies an objective against the existing canonical TaskType
descriptions with the repository's deterministic `HashEmbedder`. It then gives
the recommender a read-only evidence view containing only the inferred TaskType
and contract-backed task kinds. Outcomes persist that same TaskType and bind it
into the evidence digest.

## Representative-case bound

Evidence selection keeps the newest record for each exact model and task shape.
It then visits TaskType values in declaration order, one shape per type per
round. At most ten records are returned because ten TaskType values exist. This
has three useful properties:

1. Repeated evidence for one feature-coding case cannot crowd out a bug-fix or
   documentation case.
2. Evidence belonging to different models is never collapsed.
3. Input order and process restart do not change the selected sample.

Malformed, invented-task-kind, unknown-task-type, and legacy-unscoped records do
not enter the representative sample.

## Operational evidence

The upstream
[lm-evaluation-harness task guide](https://github.com/EleutherAI/lm-evaluation-harness/blob/main/docs/task_guide.md)
treats task configuration, version, prompt rendering, and grouped task sets as
part of reproducible evaluation identity. That supports preserving Gludd's
existing task dimensions instead of reducing every coding result to one score.

A long-lived Hugging Face forum question asks for
[individual task performance by model](https://discuss.huggingface.co/t/more-insight-into-benchmarks-leaderboard-individual-task-performance-by-model/43560)
rather than only aggregate leaderboard results. A later practitioner benchmark
of
[local coding models by task family and repeated runs](https://discuss.huggingface.co/t/benchmark-6-local-ollama-models-for-code-gen-delegation-with-variance-analysis/175579)
reported that a single-shot ordering and a repeated-run ordering differed, and
that an unrelated task family affected the aggregate result. These reports are
not treated as proof of Gludd behavior; they motivate the fail-closed,
task-shape-specific regression tests.

## Verification

`tests/unit/test_self_improve_task_diversity.py` pins:

- all existing TaskType canonical descriptions;
- exact store queries and legacy evidence exclusion;
- bounded, deterministic, model-preserving representative selection;
- planner selection using only the inferred exact shape; and
- failure history isolation between feature and bug-fix tasks sharing the
  `coding` contract.

The focused branch-coverage gate includes the planner, diversity selector, and
evidence store and requires at least 85 percent aggregate branch coverage and
75 percent for every measured file.
