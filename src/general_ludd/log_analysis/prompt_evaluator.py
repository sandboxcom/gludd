import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

_INLINE_ROLE_TAG = re.compile(r"<(?:user|assistant|system)>", re.IGNORECASE)
_INLINE_ROLE_PREFIX = re.compile(
    r"^(?:User|Assistant|System|Human|AI)\s*[:>]",
    re.IGNORECASE,
)


def parse_conversation_log(log_path: str | Path) -> list[dict[str, Any]]:
    source = str(log_path)
    is_inline = (
        not isinstance(log_path, Path)
        and (
            "\n" in source
            or _INLINE_ROLE_TAG.search(source) is not None
            or _INLINE_ROLE_PREFIX.search(source) is not None
        )
    )
    if is_inline:
        raw = source
    else:
        path = Path(log_path)
        try:
            if not path.is_file():
                return []
            raw = path.read_text(encoding="utf-8")
        except OSError:
            return []
    entries: list[dict[str, Any]] = []
    role_pattern = re.compile(
        r"<(user|assistant|system)>(.*?)</\1>",
        re.DOTALL,
    )

    for match in role_pattern.finditer(raw):
        role = match.group(1)
        content = match.group(2).strip()

        entry: dict[str, Any] = {
            "role": role,
            "content": content,
            "tool_calls": [],
            "cot": "",
            "timestamp": None,
            "tokens": 0,
        }

        tool_match = re.findall(
            r"<tool_call>(.*?)</tool_call>",
            content,
            re.DOTALL,
        )
        for tc in tool_match:
            parsed = _try_parse_json(tc.strip())
            entry["tool_calls"].append(parsed if parsed else {"raw": tc.strip()})

        cot_match = re.search(
            r"<cot>(.*?)</cot>",
            content,
            re.DOTALL | re.IGNORECASE,
        )
        if cot_match:
            entry["cot"] = cot_match.group(1).strip()

        ts_match = re.search(
            r'[tT]imestamp["\s:=]+["\']?([\dT:\.\-\+Z]+)',
            content,
        )
        if ts_match:
            entry["timestamp"] = ts_match.group(1)

        entry["tokens"] = _estimate_tokens(content)
        entries.append(entry)

    if not entries:
        entries = _parse_fallback(raw)

    return entries


def _try_parse_json(text: str) -> dict[str, Any] | None:
    try:
        result: Any = json.loads(text)
        return result if isinstance(result, dict) else None
    except (json.JSONDecodeError, TypeError):
        pass
    return None


def _estimate_tokens(text: str) -> int:
    return len(re.findall(r"\b\w+\b", text))


def _parse_fallback(raw: str) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    lines = raw.splitlines()
    current_role = "unknown"
    current_lines: list[str] = []

    for line in lines:
        role_detect = re.match(
            r"^(User|Assistant|System|Human|AI|USER|ASSISTANT|SYSTEM)\s*[:>]\s*(.*)",
            line,
        )
        if role_detect:
            if current_lines:
                entries.append(_build_fallback_entry(current_role, "\n".join(current_lines)))
            role_raw = role_detect.group(1).lower()
            current_role = {"human": "user", "ai": "assistant"}.get(role_raw, role_raw)
            current_lines = [role_detect.group(2)]
        else:
            current_lines.append(line)

    if current_lines:
        entries.append(_build_fallback_entry(current_role, "\n".join(current_lines)))

    return entries


def _build_fallback_entry(role: str, content: str) -> dict[str, Any]:
    return {
        "role": role,
        "content": content,
        "tool_calls": [],
        "cot": "",
        "timestamp": None,
        "tokens": _estimate_tokens(content),
    }


def extract_prompts(conversation: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        entry
        for entry in conversation
        if entry.get("role") in ("user", "assistant", "system")
    ]


def classify_prompt(prompt_text: str) -> str:
    text = prompt_text.lower().strip()

    markers: list[tuple[str, list[str]]] = [
        (
            "planning",
            [
                r"\bplan[s]?\b",
                r"\barchitect",
                r"\bdesign\b",
                r"\bapproach\b",
                r"\bstrat(egy|egies)\b",
                r"\bblueprint",
                r"\broadmap",
                r"\bhow\s+(should|would|to|do|can)\b",
                r"\brefactor(ing)?\s+(plan|strategy)",
                r"\bdraft\b.*\bplan\b",
                r"\bnext\s+steps?\b",
            ],
        ),
        (
            "coding",
            [
                r"\bwrite\b.*\b(code|function|class|module|script)\b",
                r"\bimplement\b",
                r"\bcreate\b.*\b(file|test|endpoint|route)\b",
                r"\badd\b.*\b(feature|function|method|class)\b",
                r"\bbuild\b",
                r"\bmodify\b.*\b(file|code|function)\b",
                r"\bpatch\b",
                r"\bfix\b.*\b(bug|issue|error|test)\b",
                r"\btype\b.*\bannotation",
                r"\brefactor\b(?!\s+(plan|strategy))",
                r"\bcommit\b.*\b(code|changes?)\b",
                r"\bpush\b.*\b(branch|commit|code)\b",
            ],
        ),
        (
            "research",
            [
                r"\bresearch",
                r"\bsurvey\b",
                r"\baudit\b",
                r"\bdocument\b",
                r"\bgrep\b.*\bfor\b",
                r"\bsearch\b.*\b(codebase|repo|files?)\b",
                r"\blocate\b.*\b(usage|import|class|function)\b",
                r"\bcheck\b.*\b(exists?|present|available)\b",
                r"\bunderstand\b.*\b(code|how|what)\b",
                r"\binvestigate",
                r"\bexplore\b",
                r"\banaly[sz]e\b(?!\s+log)",
                r"\bevaluate\b(?!\s+log)",
            ],
        ),
        (
            "debugging",
            [
                r"\bdebug\b",
                r"\bbi[sz]arre",
                r"\bfix\b.*\b(crash|error|failure|broken)\b",
                r"\btroubleshoot",
                r"\bdiagnos[ei]\b",
                r"\bstack\s*trace\b",
                r"\bexception\b",
                r"\bregression\b",
                r"\bunexpected\b",
                r"\bwhy\b.*\b(fail|error|crash|not\s+work)\b",
                r"\breproduce\b.*\b(bug|issue|error)\b",
            ],
        ),
        (
            "configuration",
            [
                r"\bconfig\b",
                r"\benv\b.*\b(var|variable)\b",
                r"\bsetting\b",
                r"\bdeploy\b",
                r"\binstall\b",
                r"\bsetup\b",
                r"\bbootstrap\b",
                r"\bmigration\b",
                r"\bschema\b.*\b(chang|alter|migration)\b",
                r"\binit\b",
                r"\bdocker\b",
                r"\bcontainer",
                r"\bci\b.*\b(pipeline|config|workflow)\b",
            ],
        ),
    ]

    scores: dict[str, int] = defaultdict(int)

    for category, patterns in markers:
        for pat in patterns:
            if re.search(pat, text):
                scores[category] += 1

    if not scores:
        return "other"

    return max(scores, key=lambda k: scores[k])


def measure_prompt_efficiency(prompt: str, response: dict[str, Any]) -> dict[str, Any]:
    tokens_in = _estimate_tokens(prompt)
    tokens_out = _estimate_tokens(str(response.get("content", "")))
    tools_called = len(response.get("tool_calls", []))

    task_completed = False
    response_text = str(response.get("content", "")).lower()
    tool_results = _extract_tool_results(response)

    completion_markers = [
        r"\b(done|complete[d]?|finished|resolved|implemented)\b",
        r"\b(pass(ed|ing)?|green|success)\b.*\b(test|gate|check)\b",
        r"\[x\]",
        r"✅",
    ]
    for marker in completion_markers:
        if re.search(marker, response_text) or re.search(marker, str(tool_results)):
            task_completed = True
            break

    errors = _count_errors(response_text, tool_results)

    steps_taken = tools_called + len(
        re.findall(r"\bstep\b", response_text)
    )

    content_objects = response.get("content", [])
    if isinstance(content_objects, list):
        tool_count_from_content = sum(
            1 for item in content_objects if isinstance(item, dict) and item.get("type") == "tool_use"
        )
        steps_taken = max(steps_taken, tool_count_from_content)

    return {
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "task_completed": task_completed,
        "steps_taken": steps_taken,
        "tools_called": tools_called,
        "errors": errors,
    }


def _extract_tool_results(response: dict[str, Any]) -> str:
    parts: list[str] = []
    tool_results_list = response.get("tool_results", [])
    if isinstance(tool_results_list, list):
        for tr in tool_results_list:
            if isinstance(tr, dict):
                parts.append(str(tr.get("output", "")))
            else:
                parts.append(str(tr))
    return "\n".join(parts)


def _count_errors(response_text: str, tool_results: str) -> int:
    combined = f"{response_text}\n{tool_results}"
    error_patterns = [
        r"\b(error|exception|traceback|fail(ed|ure)?|crash)\b",
        r"\bdenied\b",
        r"\bpermission\b.*\b(denied|error)\b",
        r"\btype\s*error\b",
        r"\bsyntax\s*error\b",
    ]
    count = 0
    for pat in error_patterns:
        count += len(re.findall(pat, combined, re.IGNORECASE))
    return count


def detect_context_waste(conversation: list[dict[str, Any]]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []


    seen_sentences: dict[str, int] = defaultdict(int)
    for entry in conversation:
        sentences = re.split(r"[.!?]+", entry.get("content", ""))
        for sentence in sentences:
            normalized = " ".join(sentence.lower().split())[:80]
            if len(normalized) > 20:
                seen_sentences[normalized] += 1

    for sentence_text, count in seen_sentences.items():
        if count >= 2:
            findings.append({
                "type": "repeated_fact",
                "detail": f"'{sentence_text[:60]}...' appears {count} times",
                "severity": "medium" if count >= 4 else "low",
            })

    total_tokens = sum(e.get("tokens", 0) for e in conversation)
    user_prompts = [e for e in conversation if e.get("role") == "user"]
    assistant_messages = [e for e in conversation if e.get("role") == "assistant"]

    if user_prompts:
        avg_user_tokens = total_tokens / len(user_prompts)
        if avg_user_tokens > 500:
            findings.append({
                "type": "overly_broad_request",
                "detail": f"Average user prompt is {avg_user_tokens:.0f} tokens — consider narrowing",
                "severity": "medium",
            })

    token_ratio = 0.0
    if assistant_messages and user_prompts:
        assistant_tokens = sum(e.get("tokens", 0) for e in assistant_messages)
        user_tokens = sum(e.get("tokens", 0) for e in user_prompts)
        if user_tokens > 0:
            token_ratio = assistant_tokens / user_tokens
            if token_ratio > 10:
                findings.append({
                    "type": "high_response_overhead",
                    "detail": f"Assistant:user token ratio is {token_ratio:.1f}:1 — verbose responses waste context",
                    "severity": "high",
                })

    unused_details: list[dict[str, Any]] = []  # unused, kept for type-safety
    for entry in assistant_messages:
        content = entry.get("content", "")
        references = re.findall(r"`([^`]+)`", content)
        urls = re.findall(r"https?://[^\s]+", content)
        code_refs = re.findall(r"(\w+\.\w+)[\s:,]", content)

        unused_details.append({
            "refs": len(references),
            "urls": len(urls),
            "code_refs": len(code_refs),
        })

    return findings


def analyze_cot_quality(cot_text: str) -> dict[str, Any]:
    if not cot_text or not cot_text.strip():
        return {
            "reasoning_depth": 0,
            "decision_quality": 0,
            "dead_ends": 0,
            "iteration_efficiency": 0,
            "score": 0,
        }

    depth_indicators = [
        r"\b(because|therefore|thus|hence|consequently)\b",
        r"\b(if.*then|when.*then)\b",
        r"\b(alternative|however|on\s+the\s+other\s+hand)\b",
        r"\b(pros?\s*(and|&)\s*cons?|trade[-\s]?offs?)\b",
        r"\b(assum(e|ption)|given\s+that)\b",
        r"\b(evidence|test\s+result|observation)\b",
    ]
    depth_score = 0
    for pat in depth_indicators:
        depth_score += len(re.findall(pat, cot_text, re.IGNORECASE))

    reasoning_depth = min(10, depth_score)

    decision_markers = [
        r"\b(chose|chosen|selected|decided|opted|picked)\b",
        r"\b(best|optimal|prefer(able|red))\b",
        r"\b(should|must|will|going\s+to)\b.*\b(because|since|as)\b",
        r"\bclear\b.*\b(choice|winner|option|path)\b",
    ]
    decision_score = 0
    for pat in decision_markers:
        decision_score += len(re.findall(pat, cot_text, re.IGNORECASE))

    decision_quality = min(10, decision_score)

    dead_end_patterns = [
        r"\b(dead[-\s]?end|abandoned|gave\s+up|scrapped)\b",
        r"\b(back\s+to\s+drawing\s+board|start\s+over)\b",
        r"\b(redirected|pivoted|changed\s+(course|direction|approach))\b",
        r"\b(wrong|incorrect|mistake|error)\b.*\b(assumption|approach|direction)\b",
    ]
    dead_ends = 0
    for pat in dead_end_patterns:
        dead_ends += len(re.findall(pat, cot_text, re.IGNORECASE))

    iteration_patterns = [
        r"\b(try|attempt|iteration|round)\b.*\b(\d+|one|two|three|first|second|third)\b",
        r"\b(again|retry|re[-\s]?attempt)\b",
        r"\b(another|different|alternate)\b.*\b(approach|method|way|strategy)\b",
    ]
    iteration_count = 0
    for pat in iteration_patterns:
        iteration_count += len(re.findall(pat, cot_text, re.IGNORECASE))

    iteration_efficiency = max(0, min(10, 10 - iteration_count))

    score = int(
        reasoning_depth * 0.30
        + decision_quality * 0.35
        + (10 - dead_ends) * 0.20
        + iteration_efficiency * 0.15
    )

    return {
        "reasoning_depth": reasoning_depth,
        "decision_quality": decision_quality,
        "dead_ends": dead_ends,
        "iteration_efficiency": iteration_efficiency,
        "score": score,
    }


def recommend_improvements(analysis: dict[str, Any]) -> list[str]:
    recommendations: list[str] = []

    cot_quality = analysis.get("cot_quality", {})
    if isinstance(cot_quality, dict):
        if cot_quality.get("reasoning_depth", 0) < 3:
            recommendations.append(
                "Deepen reasoning: include trade-off analysis and evidence when making decisions"
            )
        if cot_quality.get("decision_quality", 0) < 3:
            recommendations.append(
                "Improve decision clarity: state chosen option and WHY it was selected explicitly"
            )
        if cot_quality.get("dead_ends", 0) > 2:
            recommendations.append(
                "Reduce dead-ends: validate assumptions early before committing to an approach"
            )
        if cot_quality.get("score", 0) < 5:
            recommendations.append(
                "Overall CoT quality is low — review reasoning patterns for gaps"
            )

    efficiency = analysis.get("efficiency", {})
    if isinstance(efficiency, dict):
        if efficiency.get("tokens_in", 0) > 1000:
            recommendations.append(
                "Prompt is very large (>1000 tokens) — reduce scope, remove redundant context"
            )
        if not efficiency.get("task_completed") and efficiency.get("steps_taken", 0) > 5:
            recommendations.append(
                "Task incomplete despite many steps — add explicit acceptance criteria to the prompt"
            )
        if efficiency.get("errors", 0) > 2:
            recommendations.append(
                "High error count — add error-handling instructions to the prompt or split complex tasks"
            )

    waste = analysis.get("context_waste", [])
    if isinstance(waste, list) and len(waste) > 3:
        recommendations.append(
            "Significant context waste detected — clean up repeated facts, narrow requests, reduce verbosity"
        )

    classification = analysis.get("classification", "")
    if classification == "debugging" and cot_quality.get("dead_ends", 0) > 3:
        recommendations.append(
            "Debugging prompt with many dead-ends — add the specific error message and reproduction steps"
        )
    if classification == "research":
        recommendations.append(
            "Research prompt — specify exact search scope (paths, patterns) to reduce context waste"
        )

    if not recommendations:
        recommendations.append("Prompt looks well-structured — no specific improvements identified")

    return recommendations


def ab_compare(
    variant_a: list[dict[str, Any]],
    variant_b: list[dict[str, Any]],
) -> dict[str, Any]:
    a_metrics = _compute_variant_metrics(variant_a)
    b_metrics = _compute_variant_metrics(variant_b)

    a_score = _score_variant(a_metrics)
    b_score = _score_variant(b_metrics)

    if a_score > b_score:
        winner = "A"
        reason = f"Variant A scored {a_score:.1f} vs B's {b_score:.1f}"
    elif b_score > a_score:
        winner = "B"
        reason = f"Variant B scored {b_score:.1f} vs A's {a_score:.1f}"
    else:
        winner = "tie"
        reason = f"Both variants scored {a_score:.1f}"

    recommendation = ""
    if winner == "A":
        if a_metrics["task_completion_rate"] < 0.5:
            recommendation = "Variant A wins but completion rate is low — further optimization needed"
        else:
            recommendation = "Adopt Variant A"
    elif winner == "B":
        if b_metrics["task_completion_rate"] < 0.5:
            recommendation = "Variant B wins but completion rate is low — further optimization needed"
        else:
            recommendation = "Adopt Variant B"
    else:
        recommendation = "Variants are equivalent — choose the simpler/shorter one"

    return {
        "winner": winner,
        "reason": reason,
        "a_metrics": a_metrics,
        "b_metrics": b_metrics,
        "recommendation": recommendation,
    }


def _compute_variant_metrics(conversation: list[dict[str, Any]]) -> dict[str, Any]:
    total_tokens_in = 0
    total_tokens_out = 0
    tasks_completed = 0
    total_errors = 0
    total_tools = 0
    task_count = 0

    for entry in conversation:
        if entry.get("role") == "user":
            total_tokens_in += entry.get("tokens", 0)
            task_count += 1
        elif entry.get("role") == "assistant":
            tokens_out = entry.get("tokens", 0)
            total_tokens_out += tokens_out
            content = entry.get("content", "")
            content_str = content if isinstance(content, str) else str(content)
            total_errors += _count_errors(content_str, "")
            total_tools += len(entry.get("tool_calls", []))

            has_completion = False
            for marker in [r"\bdone\b", r"\bcomplete\b", r"\bpass\b", r"\[x\]", r"✅"]:
                if re.search(marker, content_str.lower()):
                    has_completion = True
                    break
            if has_completion:
                tasks_completed += 1

    total_tasks = max(task_count, 1)
    task_completion_rate = tasks_completed / total_tasks

    return {
        "total_tokens_in": total_tokens_in,
        "total_tokens_out": total_tokens_out,
        "tokens_per_task": total_tokens_in / total_tasks,
        "task_completion_rate": task_completion_rate,
        "tasks_completed": tasks_completed,
        "total_tasks": total_tasks,
        "total_errors": total_errors,
        "total_tools": total_tools,
        "total_steps": total_tools,
    }


def _score_variant(metrics: dict[str, Any]) -> float:
    tcr = float(metrics["task_completion_rate"])
    tpt = float(metrics["tokens_per_task"])
    te = float(metrics["total_errors"])
    return tcr * 40.0 + (1.0 / max(1.0, tpt / 100.0)) * 30.0 + (10.0 - min(10.0, te)) * 3.0


def generate_report(analyses: list[dict[str, Any]], format: str = "markdown") -> str:
    if format != "markdown":
        return json.dumps(analyses, indent=2, default=str)

    lines: list[str] = []
    lines.append("# Prompt Evaluation Report")
    lines.append("")

    for idx, analysis in enumerate(analyses):
        prompt_id = analysis.get("prompt_id", f"Analysis #{idx + 1}")
        lines.append(f"## {prompt_id}")
        lines.append("")

        classification = analysis.get("classification", "unknown")
        lines.append(f"- **Classification:** {classification}")

        efficiency = analysis.get("efficiency", {})
        if isinstance(efficiency, dict):
            lines.append(f"- **Tokens In:** {efficiency.get('tokens_in', 'N/A')}")
            lines.append(f"- **Tokens Out:** {efficiency.get('tokens_out', 'N/A')}")
            lines.append(f"- **Task Completed:** {efficiency.get('task_completed', False)}")
            lines.append(f"- **Steps:** {efficiency.get('steps_taken', 0)}")
            lines.append(f"- **Errors:** {efficiency.get('errors', 0)}")

        cot_quality = analysis.get("cot_quality", {})
        if isinstance(cot_quality, dict):
            lines.append(f"- **Reasoning Depth:** {cot_quality.get('reasoning_depth', 0)}/10")
            lines.append(f"- **Decision Quality:** {cot_quality.get('decision_quality', 0)}/10")
            lines.append(f"- **Dead Ends:** {cot_quality.get('dead_ends', 0)}")
            lines.append(f"- **CoT Score:** {cot_quality.get('score', 0)}/10")

        waste = analysis.get("context_waste", [])
        if waste:
            lines.append(f"- **Context Waste Items:** {len(waste)}")

        recommendations = analysis.get("recommendations", [])
        if recommendations:
            lines.append("")
            lines.append("### Recommendations")
            for rec in recommendations:
                lines.append(f"- {rec}")

        lines.append("")

    return "\n".join(lines)
