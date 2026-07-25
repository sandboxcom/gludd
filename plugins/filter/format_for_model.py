from __future__ import annotations

import json
from typing import Any


MODEL_FORMATS: dict[str, dict[str, Any]] = {
    "claude": {
        "search_results": "xml",
        "code_block_style": "xml_fenced",
        "preferred_separator": "\n\n",
        "formatting_weights": {
            "separator_token": 0.8,
            "code_block_style": 0.9,
            "structure_header": 0.9,
            "inline_references": 0.7,
            "truncation_style": 0.5,
        },
    },
    "gpt4": {
        "search_results": "markdown",
        "code_block_style": "markdown_fenced",
        "preferred_separator": "\n---\n",
        "formatting_weights": {
            "separator_token": 0.7,
            "code_block_style": 0.8,
            "structure_header": 0.6,
            "inline_references": 0.5,
            "truncation_style": 0.6,
        },
    },
    "deepseek": {
        "search_results": "json",
        "code_block_style": "json_structured",
        "preferred_separator": "\n",
        "formatting_weights": {
            "separator_token": 0.6,
            "code_block_style": 0.7,
            "structure_header": 0.5,
            "inline_references": 0.8,
            "truncation_style": 0.4,
        },
    },
    "qwen": {
        "search_results": "markdown",
        "code_block_style": "markdown_fenced",
        "preferred_separator": "\n\n",
        "formatting_weights": {
            "separator_token": 0.7,
            "code_block_style": 0.6,
            "structure_header": 0.8,
            "inline_references": 0.5,
            "truncation_style": 0.5,
        },
    },
    "llama": {
        "search_results": "plaintext",
        "code_block_style": "plaintext_indented",
        "preferred_separator": "\n\n",
        "formatting_weights": {
            "separator_token": 0.5,
            "code_block_style": 0.4,
            "structure_header": 0.7,
            "inline_references": 0.6,
            "truncation_style": 0.7,
        },
    },
    "glm": {
        "search_results": "structured",
        "code_block_style": "structured_separated",
        "preferred_separator": "\n\n",
        "formatting_weights": {
            "separator_token": 0.6,
            "code_block_style": 0.5,
            "structure_header": 0.7,
            "inline_references": 0.6,
            "truncation_style": 0.6,
        },
    },
}

DEFAULT_MODEL = "claude"


def _resolve_model(target_model: str) -> dict[str, Any]:
    key = target_model.lower().strip()
    if key in MODEL_FORMATS:
        return MODEL_FORMATS[key]
    for k in MODEL_FORMATS:
        if k in key:
            return MODEL_FORMATS[k]
    return MODEL_FORMATS[DEFAULT_MODEL]


def _weighted_choice(profile: dict[str, Any], aspect: str, preferred: Any, fallback: Any) -> Any:
    weight = profile.get("formatting_weights", {}).get(aspect, 0.5)
    return preferred if weight >= 0.5 else fallback


def format_search_results(results: list[dict[str, Any]], target_model: str) -> str:
    profile = _resolve_model(target_model)
    style = profile.get("search_results", "xml")
    weights = profile.get("formatting_weights", {})
    sep_weight = weights.get("separator_token", 0.5)
    sep = profile["preferred_separator"] if sep_weight >= 0.5 else "\n"

    if not results:
        if style == "xml":
            return "<search_results></search_results>"
        elif style == "json":
            return "[]"
        elif style == "markdown":
            return "### Search Results\n\n*No results found.*"
        else:
            return "Search Results:\n\nNo results found."

    if style == "xml":
        items = []
        for r in results:
            title = r.get("title", "")
            url = r.get("url", "")
            snippet = r.get("snippet", "") or r.get("content", "") or r.get("summary", "")
            items.append(
                f"<result><title>{title}</title><url>{url}</url><snippet>{snippet}</snippet></result>"
            )
        return f"<search_results>{sep.join(items)}</search_results>"

    elif style == "json":
        return json.dumps(results, ensure_ascii=False, indent=2)

    elif style == "markdown":
        lines = ["### Search Results", ""]
        for i, r in enumerate(results, 1):
            title = r.get("title", "")
            url = r.get("url", "")
            snippet = r.get("snippet", "") or r.get("content", "") or r.get("summary", "")
            lines.append(f"{i}. **{title}**")
            lines.append(f"   {url}")
            lines.append(f"   {snippet}")
            lines.append("")
        return "\n".join(lines).strip()

    elif style == "structured":
        lines = ["[Search Results]"]
        for r in results:
            title = r.get("title", "")
            url = r.get("url", "")
            snippet = r.get("snippet", "") or r.get("content", "") or r.get("summary", "")
            lines.append(f"TITLE: {title}")
            lines.append(f"URL: {url}")
            lines.append(f"SNIPPET: {snippet}")
            lines.append("---")
        return "\n".join(lines)

    else:
        lines = ["Search Results:"]
        for r in results:
            title = r.get("title", "")
            url = r.get("url", "")
            snippet = r.get("snippet", "") or r.get("content", "") or r.get("summary", "")
            lines.append(f"  {title}")
            lines.append(f"  {url}")
            lines.append(f"  {snippet}")
            lines.append("")
        return "\n".join(lines).strip()


def format_log_calls(logs: list[dict[str, Any]], target_model: str) -> str:
    profile = _resolve_model(target_model)
    weights = profile.get("formatting_weights", {})
    sep_weight = weights.get("separator_token", 0.5)
    sep = profile["preferred_separator"] if sep_weight >= 0.5 else "\n"

    if not logs:
        return "No log entries."

    code_style = _weighted_choice(profile, "code_block_style", profile.get("code_block_style", ""), "plain")

    entries: list[str] = []
    for entry in logs:
        tool = entry.get("tool", "unknown")
        args = entry.get("args", {})
        result = entry.get("result", "")
        ts = entry.get("timestamp", "")

        if code_style in ("xml_fenced", "markdown_fenced"):
            entries.append(f"<tool_call>\n  <name>{tool}</name>\n  <args>{json.dumps(args, ensure_ascii=False)}</args>\n  <result>{result}</result>\n  <timestamp>{ts}</timestamp>\n</tool_call>")
        elif code_style in ("json_structured",):
            entries.append(json.dumps({"tool": tool, "args": args, "result": str(result)[:500], "timestamp": str(ts)}, ensure_ascii=False))
        else:
            entries.append(f"[{ts}] {tool}({json.dumps(args, ensure_ascii=False)}) -> {str(result)[:500]}")

    return sep.join(entries)


def format_context_window(context: dict[str, Any], target_model: str, max_tokens: int | None = None) -> str:
    profile = _resolve_model(target_model)
    weights = profile.get("formatting_weights", {})
    sep_weight = weights.get("separator_token", 0.5)
    sep = profile["preferred_separator"] if sep_weight >= 0.5 else "\n"

    parts: list[str] = []

    agent_state = context.get("agent_state")
    if agent_state:
        parts.append(f"<agent_state>{json.dumps(agent_state, ensure_ascii=False, default=str)}</agent_state>")

    memory = context.get("memory")
    if memory:
        parts.append(f"<memory>{json.dumps(memory, ensure_ascii=False, default=str)}</memory>")

    tasks = context.get("tasks")
    if tasks:
        parts.append(f"<tasks>{json.dumps(tasks, ensure_ascii=False, default=str)}</tasks>")

    history = context.get("history")
    if history:
        parts.append(f"<history>{json.dumps(history, ensure_ascii=False, default=str)}</history>")

    output = sep.join(parts) if parts else "{}"

    if max_tokens is not None:
        truncation_style = _weighted_choice(profile, "truncation_style", "ellipsis", "hard")
        approx_chars = max_tokens * 4
        if len(output) > approx_chars:
            if truncation_style == "ellipsis":
                output = output[:approx_chars - 20] + "\n\n... [truncated]"
            else:
                output = output[:approx_chars]

    return output


def detect_format_misfire(formatted_output: str, target_model: str) -> float:
    if not formatted_output or not formatted_output.strip():
        return 1.0

    profile = _resolve_model(target_model)
    style = profile.get("search_results", "xml")
    indicators: list[float] = []

    if formatted_output.endswith("... [truncated]") or len(formatted_output) < 10:
        indicators.append(0.9)

    if style == "xml":
        if "<" not in formatted_output:
            indicators.append(0.7)
        if formatted_output.startswith("{") or formatted_output.startswith("["):
            indicators.append(0.5)
    elif style == "json":
        try:
            json.loads(formatted_output)
            indicators.append(0.0)
        except (json.JSONDecodeError, ValueError):
            indicators.append(0.4)
    elif style == "markdown":
        if "###" not in formatted_output and "**" not in formatted_output:
            indicators.append(0.3)

    if not indicators:
        return 0.0
    return sum(indicators) / len(indicators)


def format_for_model(data: Any, target_model: str, context_type: str = "search_results") -> str:
    profile = _resolve_model(target_model)
    weights = profile.get("formatting_weights", {})

    if context_type == "search_results":
        if isinstance(data, list):
            return format_search_results(data, target_model)
        return format_search_results([data] if isinstance(data, dict) else [], target_model)

    elif context_type == "log_calls":
        if isinstance(data, list):
            return format_log_calls(data, target_model)
        return format_log_calls([data] if isinstance(data, dict) else [], target_model)

    elif context_type == "model_context":
        if isinstance(data, dict):
            return format_context_window(data, target_model)
        return format_context_window({}, target_model)

    elif context_type == "error_trace":
        header_weight = weights.get("structure_header", 0.5)
        if isinstance(data, str):
            if header_weight >= 0.5:
                return f"<error>\n{data}\n</error>"
            return f"Error:\n{data}"
        if isinstance(data, dict):
            msg = data.get("message", data.get("error", str(data)))
            trace = data.get("traceback", data.get("trace", ""))
            parts = [f"Error: {msg}"]
            if trace:
                parts.append(f"Traceback:\n{trace}")
            return "\n\n".join(parts)
        return str(data)

    else:
        return str(data)


class FilterModule:
    def filters(self) -> dict[str, Any]:
        return {
            "format_for_model": format_for_model,
            "format_search_results": format_search_results,
            "format_log_calls": format_log_calls,
            "format_context_window": format_context_window,
            "detect_format_misfire": detect_format_misfire,
        }
