import json
import tempfile
from pathlib import Path

from general_ludd.log_analysis.prompt_evaluator import (
    ab_compare,
    analyze_cot_quality,
    classify_prompt,
    detect_context_waste,
    extract_prompts,
    generate_report,
    measure_prompt_efficiency,
    parse_conversation_log,
    recommend_improvements,
)

SAMPLE_LOG_XML = """<user>
Write a function that calculates fibonacci numbers.
<cot>
I need a fibonacci function. The iterative approach is O(n) and avoids recursion limits.
I considered recursion but it's slower and hits the stack. I'll use a loop instead.
</cot>
</user>
<assistant>
Here's the implementation:
```python
def fib(n):
    if n <= 1:
        return n
    a, b = 0, 1
    for _ in range(n - 1):
        a, b = b, a + b
    return a
```
<tool_call>
{"name": "write", "args": {"path": "fib.py", "content": "def fib(n): ..."}}
</tool_call>
The function is complete. ✅
</assistant>
<user>
Now add error handling for negative inputs.
</user>
<assistant>
<tool_call>
{"name": "edit", "args": {"path": "fib.py", "old": "def fib(n):", "new": "def fib(n):\\n    if n<0: raise ValueError"}}

</tool_call>
Error handling added and tests pass.
</assistant>"""


SAMPLE_LOG_FALLBACK = """User: Write a fibonacci function
Assistant: Here is the implementation using iteration.
User: Add tests
Assistant: Tests added and passing ✓
User: Why did the previous approach fail?
Assistant: The recursive approach hit recursion limits with n>1000.
"""


class TestParseConversationLog:
    def test_parse_xml(self):
        entries = parse_conversation_log(SAMPLE_LOG_XML)
        assert len(entries) == 4
        assert entries[0]["role"] == "user"
        assert "fibonacci" in entries[0]["content"]
        assert len(entries[0]["cot"]) > 0
        assert entries[0]["tokens"] > 0

    def test_parse_xml_tool_calls(self):
        entries = parse_conversation_log(SAMPLE_LOG_XML)
        assistant_entries = [e for e in entries if e["role"] == "assistant"]
        assert len(assistant_entries) == 2
        assert len(assistant_entries[0]["tool_calls"]) == 1
        assert assistant_entries[0]["tool_calls"][0]["name"] == "write"

    def test_parse_from_file(self):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".log", delete=False
        ) as f:
            f.write(SAMPLE_LOG_XML)
            f.flush()
            entries = parse_conversation_log(f.name)
        Path(f.name).unlink()
        assert len(entries) == 4

    def test_parse_empty(self):
        entries = parse_conversation_log("/nonexistent/path.log")
        assert entries == []

    def test_parse_fallback(self):
        entries = parse_conversation_log(SAMPLE_LOG_FALLBACK)
        assert len(entries) >= 2
        roles = {e["role"] for e in entries}
        assert "user" in roles
        assert "assistant" in roles

    def test_extract_prompts(self):
        conversation = parse_conversation_log(SAMPLE_LOG_XML)
        prompts = extract_prompts(conversation)
        assert len(prompts) == 4
        assert all(p["role"] in ("user", "assistant", "system") for p in prompts)


class TestClassifyPrompt:
    def test_classify_planning(self):
        assert classify_prompt("Design a new architecture for the event loop") == "planning"
        assert classify_prompt("What is our strategy for handling errors?") == "planning"
        assert classify_prompt("Create a roadmap for the next sprint") == "planning"

    def test_classify_coding(self):
        assert classify_prompt("Write a function that sorts arrays") == "coding"
        assert classify_prompt("Implement the new endpoint for user login") == "coding"
        assert classify_prompt("Add type annotations to all functions") == "coding"

    def test_classify_research(self):
        assert classify_prompt("Research available OSS solutions for logging") == "research"
        assert classify_prompt("Audit the codebase for security gaps") == "research"
        assert classify_prompt("Search the repo for usage of deprecated APIs") == "research"

    def test_classify_debugging(self):
        assert classify_prompt("Debug the null pointer exception in the worker") == "debugging"
        assert classify_prompt("Fix the crash in the event loop") == "debugging"
        assert classify_prompt("Why does the test fail with TypeError?") == "debugging"

    def test_classify_configuration(self):
        assert classify_prompt("Configure the CI pipeline for PR checks") == "configuration"
        assert classify_prompt("Set up the Docker container for deployment") == "configuration"
        assert classify_prompt("Install dependencies and bootstrap the project") == "configuration"

    def test_classify_other(self):
        assert classify_prompt("Hello, how are you?") == "other"
        assert classify_prompt("thanks") == "other"


class TestMeasurePromptEfficiency:
    def test_completed_task(self):
        prompt = "Write a fibonacci function"
        response = {
            "content": "Here is the function. Tests pass ✅",
            "tool_calls": [{"name": "write", "args": {}}],
        }
        result = measure_prompt_efficiency(prompt, response)
        assert result["task_completed"] is True
        assert result["tokens_in"] > 0
        assert result["tokens_out"] > 0
        assert result["tools_called"] == 1
        assert result["steps_taken"] >= 1

    def test_incomplete_task(self):
        prompt = "Write a fibonacci function"
        response = {
            "content": "I am still working on it",
            "tool_calls": [],
        }
        result = measure_prompt_efficiency(prompt, response)
        assert result["task_completed"] is False

    def test_error_counting(self):
        prompt = "Fix the bug"
        response = {
            "content": "Error: permission denied. Exception occurred. Failed.",
            "tool_calls": [],
        }
        result = measure_prompt_efficiency(prompt, response)
        assert result["errors"] >= 2


class TestDetectContextWaste:
    def test_finds_repeated_facts(self):
        base = "The event loop processes tasks in FIFO order"
        conversation = [
            {"role": "user", "content": f"{base}. That is the design.", "tokens": 10},
            {"role": "assistant", "content": f"Got it. {base}. Will comply.", "tokens": 15},
            {"role": "user", "content": f"As noted before, {base}. Please remember.", "tokens": 12},
        ]
        findings = detect_context_waste(conversation)
        repeated = [f for f in findings if f["type"] == "repeated_fact"]
        assert len(repeated) > 0

    def test_different_content_no_waste(self):
        conversation = [
            {"role": "user", "content": "Write a function.", "tokens": 5},
            {"role": "assistant", "content": "Here is the code.", "tokens": 5},
        ]
        findings = detect_context_waste(conversation)
        repeated = [f for f in findings if f["type"] == "repeated_fact"]
        assert len(repeated) == 0

    def test_overly_broad_detected(self):
        conversation = [
            {
                "role": "user",
                "content": "Implement the entire system with all features and all tests and all docs. "
                + "Also set up CI and deploy to production.",
                "tokens": 500,
            },
            {"role": "assistant", "content": "ok", "tokens": 1},
        ]
        findings = detect_context_waste(conversation)
        broad = [f for f in findings if f["type"] == "overly_broad_request"]
        assert len(broad) == 1


class TestAnalyzeCotQuality:
    def test_empty_cot(self):
        result = analyze_cot_quality("")
        assert result["reasoning_depth"] == 0
        assert result["score"] == 0

    def test_deep_reasoning(self):
        cot = (
            "I chose the iterative approach because it avoids recursion limits. "
            "However, the recursive version is more readable. "
            "Therefore, trade-off: performance vs readability. "
            "Given that the input size is unbounded, I will use iteration. "
            "Evidence: benchmarks show 10x speedup. "
            "Alternative considered: caching with functools.lru_cache."
        )
        result = analyze_cot_quality(cot)
        assert result["reasoning_depth"] >= 2
        assert result["decision_quality"] >= 1

    def test_dead_ends_detected(self):
        cot = (
            "First I tried approach A but it was wrong. "
            "Then I gave up on approach B and started over. "
            "The third attempt also hit a dead end. "
            "Finally I abandoned that and pivoted to approach D."
        )
        result = analyze_cot_quality(cot)
        assert result["dead_ends"] >= 2


class TestRecommendImprovements:
    def test_generates_useful_output(self):
        analysis = {
            "classification": "coding",
            "cot_quality": {
                "reasoning_depth": 1,
                "decision_quality": 2,
                "dead_ends": 4,
                "score": 3,
            },
            "efficiency": {
                "tokens_in": 2000,
                "tokens_out": 500,
                "task_completed": False,
                "steps_taken": 10,
                "errors": 5,
            },
            "context_waste": [
                {"type": "repeated_fact"},
                {"type": "repeated_fact"},
                {"type": "repeated_fact"},
                {"type": "repeated_fact"},
            ],
        }
        recs = recommend_improvements(analysis)
        assert len(recs) > 0
        assert any("reasoning" in r.lower() for r in recs)
        assert any("error" in r.lower() for r in recs)
        assert any("context waste" in r.lower() for r in recs)

    def test_good_prompt_no_recommendations_other_than_default(self):
        analysis = {
            "classification": "coding",
            "cot_quality": {
                "reasoning_depth": 8,
                "decision_quality": 8,
                "dead_ends": 1,
                "score": 8,
            },
            "efficiency": {
                "tokens_in": 200,
                "task_completed": True,
                "steps_taken": 2,
                "errors": 0,
            },
            "context_waste": [],
        }
        recs = recommend_improvements(analysis)
        assert any("well-structured" in r.lower() for r in recs)


class TestAbCompare:
    def test_picks_better_variant(self):
        variant_a = [
            {"role": "user", "content": "Write a fib function", "tokens": 6},
            {
                "role": "assistant",
                "content": "done",
                "tokens": 1,
                "tool_calls": [{"name": "write"}],
            },
        ]
        variant_b = [
            {"role": "user", "content": "Write fib", "tokens": 3},
            {
                "role": "assistant",
                "content": "done",
                "tokens": 1,
                "tool_calls": [{"name": "write"}],
            },
        ]
        result = ab_compare(variant_a, variant_b)
        assert result["winner"] in ("A", "B", "tie")
        assert "a_metrics" in result
        assert "b_metrics" in result
        assert len(result["recommendation"]) > 0

    def test_tie_when_equal(self):
        conv = [
            {"role": "user", "content": "hello", "tokens": 3},
            {
                "role": "assistant",
                "content": "hi",
                "tokens": 1,
                "tool_calls": [],
            },
        ]
        result = ab_compare(conv, conv)
        assert result["winner"] == "tie"
        assert "equivalent" in result["recommendation"].lower()


class TestGenerateReport:
    def test_markdown_report(self):
        analyses = [
            {
                "prompt_id": "Test Prompt 1",
                "classification": "coding",
                "efficiency": {
                    "tokens_in": 100,
                    "tokens_out": 50,
                    "task_completed": True,
                    "steps_taken": 3,
                    "errors": 0,
                },
                "cot_quality": {
                    "reasoning_depth": 7,
                    "decision_quality": 8,
                    "dead_ends": 1,
                    "score": 8,
                },
                "context_waste": [],
                "recommendations": ["Add error handling instructions"],
            },
        ]
        report = generate_report(analyses, format="markdown")
        assert "# Prompt Evaluation Report" in report
        assert "Test Prompt 1" in report
        assert "coding" in report
        assert "Add error handling instructions" in report

    def test_json_report(self):
        analyses = [{"prompt_id": "A", "classification": "planning"}]
        report = generate_report(analyses, format="json")
        data = json.loads(report)
        assert data[0]["prompt_id"] == "A"


class TestFullPipeline:
    def test_end_to_end(self):
        conversation = parse_conversation_log(SAMPLE_LOG_XML)
        prompts = extract_prompts(conversation)
        user_prompts = [p for p in prompts if p["role"] == "user"]
        assert len(user_prompts) == 2

        for prompt in user_prompts:
            classification = classify_prompt(prompt["content"])
            assert classification in (
                "planning",
                "coding",
                "research",
                "debugging",
                "configuration",
                "other",
            )

        efficiency = measure_prompt_efficiency(
            user_prompts[0]["content"],
            conversation[1],
        )
        assert efficiency["tokens_in"] > 0

        waste = detect_context_waste(conversation)
        assert isinstance(waste, list)

        cot = conversation[0].get("cot", "")
        cot_quality = analyze_cot_quality(cot)
        assert cot_quality["score"] >= 0

        analysis = {
            "classification": classification,
            "efficiency": efficiency,
            "cot_quality": cot_quality,
            "context_waste": waste,
        }
        recs = recommend_improvements(analysis)
        assert isinstance(recs, list)

        report = generate_report([analysis])
        assert len(report) > 0
