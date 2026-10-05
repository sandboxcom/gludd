# Log Prompt Evaluator — Automated Prompt Quality Analysis

**FQCN:** `general_ludd.agent.log_prompt_evaluator`

Automated, model-driven analysis of agent prompts and CoT traces extracted from
agent logs. Scores prompt quality along five axes (conciseness, specificity,
clarity, context utilization, output quality), identifies context waste patterns,
and generates actionable improvement recommendations. Supports A/B comparison of
prompt variants. REPORT-ONLY — never mutates the repo.

## 1. What It Measures

The role ingests conversation transcripts and CoT trace logs, then evaluates
each prompt across these dimensions:

| Dimension | What it measures | Example of a good prompt |
|---|---|---|
| **Conciseness** (0.0–1.0) | Verbosity ratio: instruction tokens vs. total tokens. Penalizes filler words, repetition, over-explanation. | "Fix the NPE in `auth.validate()`. Root cause is null `session_id` from stale cookie. Return ≤5 lines." |
| **Specificity** (0.0–1.0) | Presence of file paths, line numbers, expected output shapes, constraints. Rewards concrete references. | "Add `tenant_id` filter to `src/general_ludd/db/repository.py:342`. Match the pattern at line 280. New test in `tests/unit/test_repository.py`." |
| **Clarity** (0.0–1.0) | Readability, actionability, absence of ambiguity. Penalizes contradictory instructions, undefined terms, vague directives. | "Delete the cache key `session:{id}` when `logout()` is called in `auth.py:115`." |
| **Context Utilization** (0.0–1.0) | Fraction of provided context actually referenced by the agent. Low scores mean context was read but never used — waste. | A prompt that references 3 of 5 provided file snippets gets 0.6; one that references none gets 0.0. |
| **Output Quality** (0.0–1.0) | Task completion rate, tool accuracy (correct tool choice), steps-per-task (fewer = more efficient). Derived from post-hoc task outcomes in the same log. | 3 tasks completed with 4 tool calls total = high output quality; 1 task completed after 15 tool calls = low. |

**Aggregate score**: weighted average across all five axes, configurable via
`score_weights` (default equal weight). Scores below `verbose_score_threshold`
(0.6) are flagged for review.

## 2. Analysis Modules

Set `analysis_type` to run one or all modules:

| `analysis_type` | Description |
|---|---|
| `prompt_quality` | Score each prompt across all five dimensions. Flag prompts below threshold. |
| `cot_efficiency` | Analyze CoT reasoning depth, tool call selection patterns, iteration count, dead-end reasoning paths. Detect excessive tool calls from vague prompts. |
| `context_usage` | Measure context utilization: how much of the system prompt and provided files the agent actually referenced. Identify wasted context that could be trimmed. |
| `ab_comparison` | Compare two prompt variants (A/B) across all dimensions. Statistical significance test on score differences. Requires `ab_test_mode: true` and both `variant_a_source` / `variant_b_source`. |
| `all` (default) | Run all modules. |

### CoT Efficiency — What It Detects

The CoT efficiency module analyzes the chain-of-thought trace for:

- **Excessive tool calls** — when a vague prompt causes the agent to explore
  instead of act. Example: "fix the bug" triggers 8 read/file-search calls
  before the first edit; a better prompt would include the file and line.
- **Dead-end reasoning** — when the agent explores a path, then abandons it.
  Multiple abandoned paths per task = prompt is too open-ended.
- **Iteration spirals** — when the agent makes a small edit, tests, re-edits,
  re-tests, 5+ times. The prompt didn't specify enough.
- **Wrong tool selection** — when the agent uses grep when read would suffice,
  or Task (dispatch) for a single-file read. Indicates missing tool guidance
  in the prompt.

## 3. Usage

### Quick Start — Score All Prompts in a Log

```yaml
- hosts: localhost
  gather_facts: true
  roles:
    - role: general_ludd.agent.log_prompt_evaluator
      vars:
        enable_model_call: true
        model_profile: "sonnet"
        log_source: "/tmp/gludd-agent-logs/subagent-*.log"
        analysis_type: "all"
```

### A/B Comparison of Two Prompt Variants

```yaml
- hosts: localhost
  gather_facts: true
  roles:
    - role: general_ludd.agent.log_prompt_evaluator
      vars:
        enable_model_call: true
        model_profile: "sonnet"
        analysis_type: "ab_comparison"
        ab_test_mode: true
        variant_a_source: "/tmp/ab-test/variant-a-logs/"
        variant_b_source: "/tmp/ab-test/variant-b-logs/"
```

### Targeted CoT Efficiency Audit

```yaml
- hosts: localhost
  gather_facts: true
  roles:
    - role: general_ludd.agent.log_prompt_evaluator
      vars:
        enable_model_call: true
        model_profile: "sonnet"
        analysis_type: "cot_efficiency"
        log_source: "/tmp/gludd-agent-logs/subagent-*.log"
```

## 4. Inputs

| Variable | Default | Description |
|---|---|---|
| `enable_model_call` | `false` | Must be `true` to invoke model analysis |
| `model_profile` | `sonnet` | Model profile for analysis calls |
| `log_source` | `/tmp/gludd-plugin-loaded.log` | Conversation log or directory of logs |
| `analysis_type` | `all` | `prompt_quality`, `cot_efficiency`, `context_usage`, `ab_comparison`, `all` |
| `min_context_tokens` | `100` | Skip prompts shorter than this (not meaningful to evaluate) |
| `max_recommendations` | `10` | Max recommendations per report |
| `verbose_score_threshold` | `0.6` | Prompts scoring below this get flagged |
| `waste_pattern_threshold` | `0.3` | Fraction of prompts exhibiting waste before flagging the whole analysis |
| `score_weights` | equal | Per-dimension weight overrides: `{conciseness: 0.3, specificity: 0.2, ...}` |
| `ab_test_mode` | `false` | Enable A/B comparison mode |
| `variant_a_source` | `""` | Log/transcript path for variant A |
| `variant_b_source` | `""` | Log/transcript path for variant B |
| `output_format` | `json` | `json`, `markdown`, `text` |
| `artifact_dir` | `/tmp/gludd-prompt-evaluator` | Output directory |

## 5. Outputs

| File | Format | Description |
|---|---|---|
| `prompt_eval_report.json` | JSON | Structured scores, flagged prompts, recommendations |
| `prompt_eval_report.md` | Markdown | Human-readable summary with score tables and recommendations |
| `prompt_eval_cot.log` | Plain text | Chain-of-thought: every analysis model call's prompt + response |
| `prompt_eval_raw_corpus.txt` | Plain text | Full extracted prompts fed to the model (for audit) |

### JSON Report Structure

Each report contains:

```json
{
  "analysis_type": "prompt_quality",
  "source": "/tmp/gludd-agent-logs/subagent-1.log",
  "prompts_evaluated": 45,
  "flagged_count": 7,
  "aggregate_score": 0.72,
  "scores_by_dimension": {
    "conciseness": 0.68,
    "specificity": 0.71,
    "clarity": 0.83,
    "context_utilization": 0.61,
    "output_quality": 0.76
  },
  "flagged_prompts": [
    {
      "prompt_id": "subagent-1__prompt_3",
      "text_preview": "fix the bug in the database code",
      "scores": {
        "conciseness": 0.9,
        "specificity": 0.15,
        "clarity": 0.3,
        "context_utilization": 0.0,
        "output_quality": 0.0
      },
      "aggregate_score": 0.27,
      "waste_pattern": "vague_no_filepath",
      "recommendation": "Add the file path and specific function name. Example: 'Fix the NPE in src/general_ludd/db/repository.py:342 — null session_id from stale cookie.'"
    }
  ],
  "waste_patterns": {
    "vague_no_filepath": 3,
    "overly_broad_directive": 2,
    "missing_output_constraint": 1,
    "contradictory_instructions": 1
  },
  "recommendations": [
    "3 of 45 prompts (6.7%) lack file paths — add `src/<module>/<file>.py:<line>` to each prompt.",
    "2 prompts have contradictory instructions (both 'keep it short' and 'be thorough') — pick one.",
    "Context utilization is 0.61 — 39% of system prompt tokens are never referenced. Consider trimming."
  ],
  "ab_comparison": null
}
```

### A/B Comparison Output (additional fields)

When `ab_test_mode` is true, the report includes:

```json
{
  "ab_comparison": {
    "variant_a_source": "/tmp/ab-test/variant-a-logs/",
    "variant_b_source": "/tmp/ab-test/variant-b-logs/",
    "variant_a_score": 0.68,
    "variant_b_score": 0.79,
    "delta": 0.11,
    "winner": "B",
    "significant": true,
    "dimension_deltas": {
      "conciseness": 0.02,
      "specificity": 0.23,
      "clarity": 0.05,
      "context_utilization": 0.18,
      "output_quality": 0.09
    },
    "key_difference": "Variant B adds file paths and line numbers — specificity improved by 0.23."
  }
}
```

## 6. Recommendation Types

The evaluator generates these categories of recommendations:

| Recommendation pattern | Trigger | Example output |
|---|---|---|
| `vague_no_filepath` | Prompt has <10% file-path references, score <0.4 on specificity | "Add file paths to 3 of 45 prompts. Use `src/module/file.py:NNN` format." |
| `overly_broad_directive` | Prompt triggers >8 tool calls per task, low output quality | "This prompt triggered 12 read calls before first edit — add the file path and the expected fix approach." |
| `missing_output_constraint` | No 'return ≤N lines' or output shape specified | "Add output constraints: 'Return ≤5 bullet points' or 'Return a JSON object with {key, value}'." |
| `contradictory_instructions` | Prompt contains both 'be thorough' and 'be concise' or equivalent | "Choose one directing principle. 'Be concise but thorough' contradicts itself." |
| `undefined_term` | Prompt uses terms the agent has no context for (tool names, project jargon) | "'Use the gludd_patch module' — the agent has no context for this term. Define it or remove it." |
| `wasted_context` | >40% of system prompt tokens never referenced | "The system prompt is 8,200 tokens but only 4,100 are referenced. Trim unused sections." |
| `tool_call_loop` | Repeated calls to the same tool with similar inputs | "3 consecutive `grep` calls for the same pattern. Collapse into one with broader scope." |
| `ab_test_winner` | A/B comparison shows significant difference | "Variant B outperforms A by 0.11 (p<0.05). Key: B's file-path references." |
| `prompt_too_long_for_task` | Prompt tokens >5× task complexity, low context utilization | "This 2,400-token prompt describes a single-function fix. Cut to ≤500 tokens." |
| `missing_tool_guidance` | Agent used wrong tool 3+ times in one task | "Agent used `grep` (read-only) when `read` would give the full file. Add: 'Use read for file inspection, grep for pattern search only.'" |

## 7. Integration

### Run Weekly to Track Prompt Quality Trends

Store prompt evaluation reports in a time-series directory. Compare scores
week-over-week to detect prompt quality regressions:

```bash
# /etc/cron.d/gludd-prompt-eval
SHELL=/bin/bash
0 6 * * MON gludd cd /opt/gludd && .venv/bin/ansible-playbook \
  -e "psk=$(cat /etc/gludd/psk)" \
  -e "log_source=/var/log/gludd" \
  -e "artifact_dir=/var/log/gludd/prompt-eval/$(date +%Y-W%V)" \
  -e "enable_model_call=true" \
  -e "analysis_type=all" \
  playbooks/prompt_eval.yml
```

Track trends with a companion script:

```bash
#!/usr/bin/env bash
# scripts/prompt_quality_trend.sh — compare this week's scores to last week's
THIS_WEEK=$(ls -d /var/log/gludd/prompt-eval/$(date +%Y-W%V)/prompt_eval_report.json 2>/dev/null)
LAST_WEEK=$(ls -dt /var/log/gludd/prompt-eval/*/prompt_eval_report.json 2>/dev/null | head -2 | tail -1)

if [ -n "$THIS_WEEK" ] && [ -n "$LAST_WEEK" ]; then
  THIS_SCORE=$(jq '.aggregate_score' "$THIS_WEEK")
  LAST_SCORE=$(jq '.aggregate_score' "$LAST_WEEK")
  DELTA=$(python3 -c "print(round($THIS_SCORE - $LAST_SCORE, 3))")
  echo "Prompt quality: $LAST_SCORE → $THIS_SCORE (Δ $DELTA)"
  if (( $(echo "$DELTA < -0.05" | bc -l) )); then
    echo "WARNING: Prompt quality regression > 0.05. Review flagged prompts."
  fi
fi
```

### Wire into CI to Catch Prompt Regressions

Add a prompt quality gate to your CI pipeline. The role runs as a validation
step — if the aggregate score drops below `ci_regression_threshold` (default
0.60), the CI step exits non-zero:

```yaml
# .github/workflows/prompt-quality.yml
name: Prompt Quality Gate
on:
  pull_request:
    paths:
      - 'AGENTS.md'
      - 'collections/ansible_collections/general_ludd/**/roles/*/tasks/*.yml'
      - 'src/general_ludd/ornith/training_repo.py'

jobs:
  prompt-quality:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Run prompt evaluator
        run: |
          ansible-playbook \
            -e "psk=${{ secrets.GLUDD_AUTH_PSK }}" \
            -e "ci_regression_threshold=0.60" \
            -e "enable_model_call=true" \
            -e "log_source=/tmp/gludd-ci-prompt-logs" \
            -e "baseline_report=ci/prompt_quality_baseline.json" \
            playbooks/prompt_eval.yml
      - name: Fail on regression
        if: failure()
        run: |
          echo "Prompt quality regression detected"
          echo "Review flagged prompts in artifacts."
```

### Daemon Event Loop Integration

The prompt evaluator can be called from the event loop for continuous monitoring:

```python
# In the gludd event loop tick handler
import subprocess
import json
from pathlib import Path

def evaluate_prompt_quality(psk: str, log_source: str) -> dict:
    result = subprocess.run(
        [
            "ansible-playbook",
            "-e", f"psk={psk}",
            "-e", f"log_source={log_source}",
            "-e", "enable_model_call=true",
            "-e", "analysis_type=all",
            "playbooks/prompt_eval.yml",
        ],
        capture_output=True,
        text=True,
        timeout=300,
        cwd="/opt/gludd",
    )

    report_path = Path("/tmp/gludd-prompt-evaluator/prompt_eval_report.json")
    if report_path.exists():
        report = json.loads(report_path.read_text())
        return {
            "aggregate_score": report.get("aggregate_score", 0.0),
            "flagged_count": report.get("flagged_count", 0),
            "recommendations": report.get("recommendations", []),
        }
    return {"status": "no_report", "rc": result.returncode}
```

### Integration with the Self-Improve Router

The gludd self-improve router (`src/general_ludd/routers/self_improve.py`)
can consume prompt evaluation reports to automatically apply low-risk
recommendations (add file paths, tighten output constraints) and flag
high-risk ones (rewrite entire prompt, rethink system prompt) for human review.

```python
# src/general_ludd/routers/self_improve.py
from pathlib import Path
import json

def apply_prompt_improvements(report_path: str) -> list[str]:
    """Read a prompt_eval_report.json and apply low-risk improvements."""
    report = json.loads(Path(report_path).read_text())
    applied = []

    for rec in report.get("recommendations", []):
        if "add file paths" in rec.lower():
            # Low-risk: inject file paths into agent prompt templates
            apply_file_path_injection()
            applied.append("file_path_injection")
        elif "trim unused sections" in rec.lower():
            # Medium-risk: flag for review, don't auto-apply
            flag_for_human_review(rec)
            applied.append("flagged_for_review")

    return applied
```

## 8. Metrics Reference

### Token Counts

| Metric | Description | Source |
|---|---|---|
| `prompt_tokens_total` | Sum of all prompt tokens across evaluated conversations | Conversation log parsing |
| `prompt_tokens_avg` | Mean prompt tokens per conversation | Computed |
| `prompt_tokens_p95` | 95th percentile prompt token count | Computed |
| `system_prompt_tokens` | System prompt token count (from metadata) | Conversation metadata |
| `wasted_context_tokens` | System prompt tokens never referenced by agent | CoT trace analysis |
| `context_utilization_pct` | (referenced_tokens / total_context_tokens) × 100 | CoT trace analysis |

### Task Completion Rate

| Metric | Description |
|---|---|
| `tasks_total` | Total tasks dispatched in logs |
| `tasks_completed` | Tasks with a successful completion marker |
| `tasks_completion_rate` | `tasks_completed / tasks_total` |
| `tasks_failed` | Tasks with an error or abandonment marker |
| `tasks_timeout` | Tasks exceeding the 5-minute deadline |

### Steps Per Task

| Metric | Description |
|---|---|
| `steps_total` | Total tool calls across all tasks |
| `steps_per_task_avg` | Mean tool calls per task |
| `steps_per_task_p95` | 95th percentile tool calls per task |
| `tool_call_accuracy` | Fraction of tool calls that were the right tool for the job |

### Tool Accuracy

| Metric | Description |
|---|---|
| `tool_accuracy_read` | Agent used `read` (not `grep`) when the task was "read file X" |
| `tool_accuracy_edit` | Agent used `edit` (not `write`) when modifying existing files |
| `tool_accuracy_dispatch` | Agent dispatched a subagent (not inline grind) when task was multi-step |

## 9. Configuration — All Options

```yaml
- hosts: localhost
  gather_facts: true
  roles:
    - role: general_ludd.agent.log_prompt_evaluator
      vars:
        enable_model_call: true
        model_profile: "sonnet"

        # Log source
        log_source: "/var/log/gludd/agent-*.log"

        # Analysis scope
        analysis_type: "all"
        min_context_tokens: 100
        max_recommendations: 10

        # Thresholds
        verbose_score_threshold: 0.6
        waste_pattern_threshold: 0.3
        ci_regression_threshold: 0.60

        # Score weights (must sum to 1.0)
        score_weights:
          conciseness: 0.20
          specificity: 0.25
          clarity: 0.20
          context_utilization: 0.15
          output_quality: 0.20

        # A/B test mode
        ab_test_mode: true
        variant_a_source: "/tmp/ab-test/variant-a/"
        variant_b_source: "/tmp/ab-test/variant-b/"

        # Output
        output_format: "json"
        artifact_dir: "/tmp/gludd-prompt-evaluator"

        # Baseline (for CI regression comparison)
        baseline_report: "/var/log/gludd/prompt-eval/baseline.json"

        # CoT logging
        log_evaluator_cot: true
        log_evaluator_cot_path: "/tmp/gludd-prompt-evaluator/prompt_eval_cot.log"

        # Destructive ops guard
        enable_git_push: false
```

## 10. Relationship to log_analyzer

| Aspect | `log_analyzer` | `log_prompt_evaluator` |
|---|---|---|
| **FQCN** | `general_ludd.operations.log_analyzer` | `general_ludd.agent.log_prompt_evaluator` |
| **Scope** | System-wide logs: daemon, agents, CI, systemd, SearX | Agent prompts and CoT traces only |
| **Analysis type** | Error clustering, anomaly detection, behavior analysis, performance regression | Prompt quality scoring, context waste detection, A/B comparison |
| **Output focus** | System health + error remediation | Prompt quality + improvement recommendations |
| **Typical run frequency** | Daily cron or real-time event loop | Weekly trend tracking or CI gate on PR |
| **Degrades gracefully?** | Yes — runs read-only, no mutations | Yes — REPORT-ONLY, never mutates |
| **Mutates repo?** | Never | Never |

They complement each other: `log_analyzer` tells you *what went wrong in the
system*; `log_prompt_evaluator` tells you *how to write better prompts to
prevent things from going wrong in the first place*.

---

*Generated by general_ludd.agent.log_prompt_evaluator — REPORT-ONLY, no repo mutations.*
