#!/usr/bin/env python3
"""Prompt evaluator for agent log analysis.

Parses agent conversation transcripts and CoT traces to evaluate prompt quality,
classify prompt types, identify waste patterns, and generate recommendations.
Supports A/B prompt variant comparison.

Usage:
    prompt_evaluator.py --input <log_file> --analysis-type all --output <json_path>
"""

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class PromptEntry:
    index: int = 0
    prompt_text: str = ""
    response_text: str = ""
    cot_trace: str = ""
    prompt_type: str = "unknown"
    scores: dict[str, float] = field(default_factory=dict)
    waste_flags: list[str] = field(default_factory=list)


def parse_agent_log(log_path: str) -> list[dict[str, Any]]:
    """Extract prompts, responses, and CoT traces from agent log files."""
    entries: list[dict[str, Any]] = []

    try:
        content = Path(log_path).read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        print(f"ERROR: log file not found: {log_path}", file=sys.stderr)
        return entries
    except OSError as exc:
        print(f"ERROR: cannot read log file {log_path}: {exc}", file=sys.stderr)
        return entries

    sections = content.split("---LOG_BOUNDARY---")

    prompt_section_patterns = [
        (r'prompt["\s:=]+(.+?)(?=response["\s:=]|$)', "prompt"),
        (r'user_msg["\s:=]+(.+?)(?=assistant["\s:=]|$)', "prompt"),
        (r'(?:^|\n)(?:User|Human|Instruction):\s*(.+?)(?=\n(?:Assistant|Agent|Response|Model):|\Z)', "prompt"),
    ]

    cot_patterns = [
        r'(?:cot_trace|chain_of_thought|reasoning)["\s:=]+(.+?)(?=response["\s:=]|$)',
        r'(?:^|\n)(?:CoT|Reasoning|Thinking):\s*(.+?)(?=\n(?:Response|Output|Action):|\Z)',
    ]

    entry_index = 0
    for section in sections:
        if not section.strip():
            continue

        prompt_text = ""
        response_text = ""
        cot_trace = ""

        for pattern, _label in prompt_section_patterns:
            m = re.search(pattern, section, re.DOTALL | re.IGNORECASE | re.MULTILINE)
            if m:
                prompt_text = m.group(1).strip()
                break

        resp_m = re.search(
            r'(?:response|assistant|model_output|agent_response)["\s:=]+(.+)',
            section, re.DOTALL | re.IGNORECASE,
        )
        if resp_m:
            response_text = resp_m.group(1).strip()

        for pattern in cot_patterns:
            cot_m = re.search(pattern, section, re.DOTALL | re.IGNORECASE | re.MULTILINE)
            if cot_m:
                cot_trace = cot_m.group(1).strip()
                break

        if prompt_text or response_text or cot_trace:
            entries.append({
                "index": entry_index,
                "prompt": prompt_text,
                "response": response_text,
                "cot_trace": cot_trace,
            })
            entry_index += 1

    return entries


def classify_prompt_type(prompt: str) -> str:
    """Classify a prompt into one of: planning, coding, research, debugging, unknown."""
    if not prompt:
        return "unknown"

    lower = prompt.lower()

    coding_keywords = [
        "write code", "implement", "refactor", "fix the bug", "add function",
        "create class", "write a test", "add endpoint", "add a route",
        "write the following", "generate code", "edit this file",
    ]
    for kw in coding_keywords:
        if kw in lower:
            return "coding"

    planning_keywords = [
        "plan", "architecture", "design", "roadmap", "sprint plan",
        "next steps", "what should we", "how should we approach",
        "break down", "task breakdown", "prioritize", "estimate",
    ]
    for kw in planning_keywords:
        if kw in lower:
            return "planning"

    research_keywords = [
        "research", "investigate", "find out", "what is", "how does",
        "explain", "explore", "survey", "audit", "review the code",
        "analyse", "compare", "what are the", "look into",
    ]
    for kw in research_keywords:
        if kw in lower:
            return "research"

    debug_keywords = [
        "debug", "why is", "error", "exception", "not working",
        "broken", "failing", "fix the", "crash", "traceback",
        "stack trace", "investigate the bug", "root cause",
    ]
    for kw in debug_keywords:
        if kw in lower:
            return "debugging"

    return "unknown"


def _count_words(text: str) -> int:
    return len(text.split())


def _estimate_tokens(text: str) -> int:
    return max(1, int(len(text.split()) * 1.3))


def score_prompt_quality(prompt: str, response: dict[str, Any]) -> dict[str, float]:
    """Score a prompt on conciseness, specificity, context utilisation, and output quality."""
    prompt_tokens = _estimate_tokens(prompt)
    response_text = response.get("response", "") if isinstance(response, dict) else str(response)
    response_tokens = _estimate_tokens(response_text)

    conciseness = max(0.0, min(1.0, 1.0 - (prompt_tokens / max(2000, prompt_tokens))))
    if prompt_tokens < 50:
        conciseness = min(1.0, conciseness + 0.2)

    specificity_indicators = [
        r'\b[a-zA-Z0-9_/\.]+\.py\b',
        r'\b[a-zA-Z0-9_/\.]+\.ts\b',
        r'\b[a-zA-Z0-9_/\.]+\.yml\b',
        r'`[^`]+`',
        r'line \d+',
        r'function \w+',
        r'class \w+',
        r'make \w+\S*',
        r'--\w+(?:=\S+)?',
        r'TAG=\S+',
        r'(?i)EXACTLY',
        r'(?i)specifically',
        r'(?i)return.*\d+.*lines',
        r'(?i)do NOT',
    ]
    specificity_hits = 0
    for pattern in specificity_indicators:
        if re.search(pattern, prompt):
            specificity_hits += 1
    specificity = min(1.0, specificity_hits / 5.0)

    context_waste_patterns = [
        r'you are (?:an?|the)\s',
        r'(?:you have access to|you can use)\s',
        r'(?:you are running|you are powered by)\s',
        r'(?:you must|you should)\s.*?(?:always|never)',
    ]
    wasted_tokens = 0
    for pattern in context_waste_patterns:
        for m in re.finditer(pattern, prompt):
            wasted_tokens += _estimate_tokens(m.group(0))
    context_utilization = max(0.0, 1.0 - (wasted_tokens / max(prompt_tokens, 1)))

    if prompt_tokens < 100:
        context_utilization = min(0.95, context_utilization)

    output_quality = 0.5
    if response_tokens > 0:
        specificity_in_response = response_tokens / max(1, _count_words(response_text))
        output_quality = min(1.0, specificity_in_response)
        if prompt_tokens > 300 and response_tokens < 50:
            output_quality *= 0.3
        if response_tokens > prompt_tokens * 0.8:
            output_quality *= 1.1

    return {
        "conciseness": round(conciseness, 3),
        "specificity": round(specificity, 3),
        "context_utilization": round(context_utilization, 3),
        "output_quality": round(min(1.0, output_quality), 3),
    }


def identify_waste_patterns(log_entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Identify common waste patterns across all log entries."""
    pattern_defs: list[tuple[str, str, str]] = [
        (
            "over_verbose_prompt",
            "Prompt is excessively long relative to the task",
            r'.{4000,}',
        ),
        (
            "repeated_known_facts",
            "Prompt repeats facts the model already knows (identity, capabilities, rules already in system prompt)",
            r'you are (?:an?|the)\s.*?(?:AI|assistant|agent|coding|language model)',
        ),
        (
            "overly_broad_request",
            "Ambiguous request without specific deliverables or constraints",
            r'(?i)(?:fix|improve|make|do)\s+(?:everything|all|the code|the project)',
        ),
        (
            "unnecessary_explanation",
            "Prompt asks the model to explain what it is doing rather than to do it",
            r'(?i)explain\s+(?:your|the)\s+(?:reasoning|thinking|approach|process|step)',
        ),
        (
            "missing_context",
            "Prompt references files, functions, or concepts without providing location or content",
            r'(?i)(?:fix|change|update|edit|modify)\s+(?:the|that|this|it)\s+(?:file|function|line|code|bug)',
        ),
        (
            "ambiguous_instruction",
            "Instruction uses vague terms without concrete criteria",
            r'(?i)(?:make it\s+(?:better|good|nice|clean|fast|right)|do it\s+(?:properly|correctly|well))',
        ),
        (
            "context_dump",
            "Prompt dumps large blocks of raw text/code without specifying what to do with it",
            r'```[\s\S]{800,}```',
        ),
    ]

    pattern_counts: dict[str, dict[str, Any]] = {}
    for name, desc, regex in pattern_defs:
        pattern_counts[name] = {
            "pattern": name,
            "description": desc,
            "frequency": 0,
            "examples": [],
        }

    for entry in log_entries:
        prompt = entry.get("prompt", "")
        for name, _desc, regex in pattern_defs:
            matches = re.findall(regex, prompt, re.IGNORECASE | re.MULTILINE)
            if matches:
                pattern_counts[name]["frequency"] += 1
                if len(pattern_counts[name]["examples"]) < 3:
                    exemplar = matches[0] if isinstance(matches[0], str) else str(matches[0])
                    if len(exemplar) > 120:
                        exemplar = exemplar[:117] + "..."
                    if exemplar not in pattern_counts[name]["examples"]:
                        pattern_counts[name]["examples"].append(exemplar)

    result = sorted(
        [v for v in pattern_counts.values() if v["frequency"] > 0],
        key=lambda x: x["frequency"],
        reverse=True,
    )
    return result


def compare_prompt_variants(
    variant_a: list[dict[str, Any]], variant_b: list[dict[str, Any]]
) -> dict[str, Any]:
    """Compare two prompt variants and determine which performed better."""
    def _avg_score(entries: list[dict[str, Any]]) -> float:
        if not entries:
            return 0.0
        scores: list[float] = []
        for e in entries:
            prompt = e.get("prompt", "")
            scores.append(sum(score_prompt_quality(prompt, e).values()) / 4.0)
        return sum(scores) / len(scores)

    a_score = _avg_score(variant_a)
    b_score = _avg_score(variant_b)

    a_waste = len(identify_waste_patterns(variant_a))
    b_waste = len(identify_waste_patterns(variant_b))

    a_total_tokens = sum(_estimate_tokens(e.get("prompt", "")) for e in variant_a)
    b_total_tokens = sum(_estimate_tokens(e.get("prompt", "")) for e in variant_b)

    a_type_counts = Counter(classify_prompt_type(e.get("prompt", "")) for e in variant_a)
    b_type_counts = Counter(classify_prompt_type(e.get("prompt", "")) for e in variant_b)

    diff = b_score - a_score
    if abs(diff) < 0.05:
        winner = "tie"
    elif diff > 0:
        winner = "variant_b"
    else:
        winner = "variant_a"

    return {
        "winner": winner,
        "variant_a_score": round(a_score, 3),
        "variant_b_score": round(b_score, 3),
        "variant_a_waste_patterns": a_waste,
        "variant_b_waste_patterns": b_waste,
        "variant_a_total_tokens": a_total_tokens,
        "variant_b_total_tokens": b_total_tokens,
        "variant_a_type_distribution": dict(a_type_counts),
        "variant_b_type_distribution": dict(b_type_counts),
        "delta": round(diff, 3),
    }


def generate_recommendations(analysis: dict[str, Any]) -> list[str]:
    """Generate actionable recommendations based on evaluation results."""
    recs: list[str] = []

    scores = analysis.get("quality_scores", {})
    if scores.get("avg_conciseness", 1.0) < 0.6:
        recs.append(
            "Reduce prompt verbosity: target 50-200 tokens for simple tasks, "
            "200-500 for complex multi-step tasks. Move context to system prompt."
        )
    if scores.get("avg_specificity", 1.0) < 0.5:
        recs.append(
            "Increase prompt specificity: include exact file paths, line numbers, "
            "function/class names, and concrete output format constraints."
        )
    if scores.get("avg_context_utilization", 1.0) < 0.7:
        recs.append(
            "Improve context utilization: avoid repeating facts the model already "
            "knows. Use examples instead of describing what to do in abstract."
        )

    waste = analysis.get("waste_patterns", [])
    waste_by_name = {wp["pattern"]: wp for wp in waste}

    if "over_verbose_prompt" in waste_by_name:
        recs.append(
            "Trim long prompts: use `be specific, not verbose` directive. "
            "Replace prose with structured constraints (bullet points, YAML, JSON schema)."
        )
    if "repeated_known_facts" in waste_by_name:
        recs.append(
            "Stop repeating system-level facts in every prompt. The model's system "
            "prompt already covers identity, rules, and capabilities. Only include "
            "task-specific context."
        )
    if "overly_broad_request" in waste_by_name:
        recs.append(
            "Replace broad requests with specific, scoped tasks. Instead of "
            "'fix the code', say 'fix the TypeError in src/foo.py:142 — param "
            "`user_id` expected int, got str'."
        )
    if "unnecessary_explanation" in waste_by_name:
        recs.append(
            "Ask the model to DO the work, not EXPLAIN it. Replace 'explain your "
            "reasoning' with 'return the result — no commentary' or set "
            "output_format: json."
        )
    if "missing_context" in waste_by_name:
        recs.append(
            "Always include context: file paths, relevant code snippets, error "
            "messages, and expected behavior. A prompt without context forces the "
            "model to guess."
        )
    if "ambiguous_instruction" in waste_by_name:
        recs.append(
            "Use concrete, measurable criteria. Replace 'make it better' with "
            "'reduce response time under 200ms' or 'achieve coverage >= 85%'."
        )
    if "context_dump" in waste_by_name:
        recs.append(
            "Don't dump raw data without instructions. For large code blocks, "
            "always specify: what to change, what to keep, the expected output format."
        )

    ab = analysis.get("ab_comparison", {})
    if ab and ab.get("winner") not in (None, "tie", "none"):
        winner = ab["winner"]
        delta = abs(ab.get("delta", 0))
        if delta > 0.1:
            recs.append(
                f"A/B test winner is {winner} (delta={delta:.3f}). Adopt the "
                f"winning prompt style for this category of task."
            )

    waste_threshold = 3
    if len(waste) >= waste_threshold:
        recs.append(
            f"Systemic prompt quality issue: {len(waste)} waste patterns detected. "
            "Consider implementing prompt templates with mandatory fields "
            "(objective, context, constraints, output_format) to enforce quality."
        )

    model_tuning_recs = {
        "coding": "For coding tasks: use direct instructions with exact file paths "
                   "and error messages. DeepSeek prefers imperative voice; "
                   "Claude prefers structured context with examples.",
        "planning": "For planning tasks: define scope explicitly. Provide the "
                    "current state (what exists) and the desired end state "
                    "(what should exist after). Use checklists for multi-step plans.",
        "research": "For research tasks: scope the investigation to N files or M "
                     "minutes. Ask for findings in a structured format "
                     "(table, bullet list with file:line references).",
        "debugging": "For debugging tasks: always include the full error trace, the "
                      "code block that produced it, and what was expected. Never "
                      "ask 'why is this broken?' without the error message.",
    }

    type_dist = analysis.get("prompt_types", {})
    dominant_type = max(type_dist, key=type_dist.get, default="")
    if dominant_type and dominant_type in model_tuning_recs:
        recs.append(model_tuning_recs[dominant_type])

    if len(recs) > 20:
        recs = recs[:20]

    return recs


def _compute_aggregate_scores(entries: list[dict[str, Any]]) -> dict[str, Any]:
    scores_list: list[dict[str, float]] = []
    for entry in entries:
        prompt = entry.get("prompt", "")
        scores_list.append(score_prompt_quality(prompt, entry))

    if not scores_list:
        return {
            "avg_conciseness": 0.0,
            "avg_specificity": 0.0,
            "avg_context_utilization": 0.0,
            "avg_output_quality": 0.0,
            "low_quality_count": 0,
        }

    n = len(scores_list)
    aggreg = {
        "avg_conciseness": round(sum(s["conciseness"] for s in scores_list) / n, 3),
        "avg_specificity": round(sum(s["specificity"] for s in scores_list) / n, 3),
        "avg_context_utilization": round(sum(s["context_utilization"] for s in scores_list) / n, 3),
        "avg_output_quality": round(sum(s["output_quality"] for s in scores_list) / n, 3),
        "low_quality_count": sum(
            1 for s in scores_list
            if sum(s.values()) / 4.0 < 0.5
        ),
    }
    return aggreg


def _classify_all(entries: list[dict[str, Any]]) -> dict[str, int]:
    type_counts: dict[str, int] = {}
    for entry in entries:
        ptype = classify_prompt_type(entry.get("prompt", ""))
        type_counts[ptype] = type_counts.get(ptype, 0) + 1
    return type_counts


def main() -> None:
    parser = argparse.ArgumentParser(description="Agent prompt evaluator")
    parser.add_argument("--input", required=True, help="Path to concatenated log file")
    parser.add_argument("--output", required=True, help="Path to output JSON")
    parser.add_argument(
        "--analysis-type", default="all",
        choices=["all", "prompt_quality", "cot_efficiency", "context_usage", "ab_comparison"],
    )
    parser.add_argument("--min-context-tokens", type=int, default=100)
    parser.add_argument("--max-recommendations", type=int, default=10)
    parser.add_argument("--output-format", default="json", choices=["json", "markdown", "text"])
    parser.add_argument("--ab-variant-a", default="")
    parser.add_argument("--ab-variant-b", default="")

    args = parser.parse_args()

    entries = parse_agent_log(args.input)

    if not entries:
        result: dict[str, Any] = {
            "total_prompts": 0,
            "prompt_types": {},
            "quality_scores": {},
            "waste_patterns": [],
            "recommendations": ["No log entries found to analyze."],
            "ab_comparison": {},
        }
    else:
        prompt_types = _classify_all(entries)
        quality_scores = _compute_aggregate_scores(entries)
        waste_patterns = identify_waste_patterns(entries)

        ab_comparison: dict[str, Any] = {}
        if args.ab_variant_a and args.ab_variant_b:
            variant_a = parse_agent_log(args.ab_variant_a)
            variant_b = parse_agent_log(args.ab_variant_b)
            ab_comparison = compare_prompt_variants(variant_a, variant_b)

        analysis: dict[str, Any] = {
            "quality_scores": quality_scores,
            "waste_patterns": waste_patterns,
            "prompt_types": prompt_types,
            "ab_comparison": ab_comparison,
        }
        recommendations = generate_recommendations(analysis)

        result = {
            "total_prompts": len(entries),
            "prompt_types": prompt_types,
            "quality_scores": quality_scores,
            "waste_patterns": waste_patterns,
            "recommendations": recommendations[:args.max_recommendations],
            "ab_comparison": ab_comparison,
        }

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, indent=2), encoding="utf-8")

    if args.output_format in ("text", "markdown"):
        print(f"Evaluation complete: {result['total_prompts']} prompts analysed")
        print(f"  Waste patterns: {len(result['waste_patterns'])}")
        print(f"  Recommendations: {len(result['recommendations'])}")


if __name__ == "__main__":
    main()
