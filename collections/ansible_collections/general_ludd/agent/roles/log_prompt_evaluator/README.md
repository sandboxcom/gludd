# log_prompt_evaluator

Analyses agent conversation logs (prompts, responses, CoT traces) to evaluate
prompt quality, identify waste patterns, and generate actionable recommendations.

## Analysis modules

| Module | What it evaluates |
|--------|-------------------|
| `prompt_quality` | Conciseness, specificity, context utilisation, output quality |
| `cot_efficiency` | Reasoning depth, tool selection accuracy, iteration count, dead ends |
| `context_usage` | How much of available context is actually used, wasted context patterns |
| `ab_comparison` | Compare two prompt variants — which performed better |

## Prompt quality metrics

| Metric | Measure | Target |
|--------|---------|--------|
| Token count | Number of tokens in the prompt | 50-200 for simple tasks, 200-500 for complex |
| Instruction clarity | Presence of concrete file paths, line numbers, function names | ≥3 specificity indicators per prompt |
| Context inclusion | Whether necessary context (error messages, code snippets, expected behaviour) is present | No `missing_context` flags |
| Example quality | Concrete, minimal examples vs. abstract descriptions | Examples present for non-trivial tasks |

## CoT analysis

- **Reasoning depth**: number of reasoning steps before tool use (target: 2-5)
- **Tool selection accuracy**: correct tool chosen first attempt vs. retries (target: >80%)
- **Iteration count**: tool-use rounds to task completion (target: <5)
- **Dead ends**: tool calls that produced no useful result or were immediately retracted

## Context usage

### Patterns that reduce context (good)
- Be specific — "fix the TypeError in `src/foo.py:142`" not "fix the code"
- Provide exact file paths and line numbers
- Use concrete examples, not abstract descriptions
- Set output format constraints ("return JSON with fields: x, y, z")
- Reference by function/class name, not by vague description

### Patterns that waste context (bad)
- Repeating system-level facts the model already knows
- Overly broad requests ("improve everything")
- Asking for unnecessary explanation ("explain your reasoning")
- Ambiguous instructions without measurable criteria
- Dumping raw data/code without specifying what to do with it

## A/B comparison methodology

1. Change ONE variable between variants (prompt length, specificity level, format)
2. Run each variant on the same or equivalent tasks
3. Measure: task completion time, token count, success rate, output quality score
4. Winner is the variant with higher aggregate quality score (delta > 0.05)

## Model-specific tuning

| Model | Preferred style |
|-------|----------------|
| DeepSeek | Direct, imperative instructions. Minimal preamble. Explicit constraints. |
| Claude | Structured context. Task description → relevant files → expected output → constraints. |
| GPT-4 | Balanced. Few-shot examples help. Clear format constraints (JSON schema). |
| Haiku | Short, single-sentence instructions. One task per prompt. |

## Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `log_source` | `/tmp/gludd-plugin-loaded.log` | Path or glob for agent log files |
| `analysis_type` | `all` | Analysis module(s) to run |
| `output_format` | `json` | Output format (json, markdown, text) |
| `min_context_tokens` | `100` | Minimum tokens for context-utilization scoring |
| `max_recommendations` | `10` | Maximum recommendations to generate |
| `verbose_score_threshold` | `0.6` | Quality score below which prompts are flagged |
| `ab_test_mode` | `false` | Enable A/B comparison |
| `variant_a_source` | `""` | Log path for variant A |
| `variant_b_source` | `""` | Log path for variant B |

## Artifacts

`prompt_evaluation.json`:
```json
{
  "total_prompts": 42,
  "prompt_types": {"coding": 20, "research": 12, "planning": 8, "debugging": 2},
  "quality_scores": {
    "avg_conciseness": 0.72,
    "avg_specificity": 0.58,
    "avg_context_utilization": 0.64,
    "avg_output_quality": 0.71,
    "low_quality_count": 3
  },
  "waste_patterns": [
    {"pattern": "over_verbose_prompt", "frequency": 15, "description": "...", "examples": ["..."]}
  ],
  "recommendations": [
    "Increase prompt specificity: include exact file paths...",
    "Trim long prompts: use structured constraints..."
  ],
  "ab_comparison": {}
}
```

## Report-only

This role is REPORT-ONLY — it never mutates the repository or pushes changes.
All output goes to `{{ artifact_dir }}`.
