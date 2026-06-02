"""Prompt scoring engine — evaluates prompt+model combos against benchmark tasks."""

from __future__ import annotations

import re
from typing import Any

from general_ludd.schemas.benchmark import (
    BenchmarkScores,
    TaskType,
)


class BenchmarkTask:
    def __init__(
        self,
        task_type: TaskType,
        description: str,
        prompt_instruction: str,
        expected_patterns: list[str] | None = None,
        forbidden_patterns: list[str] | None = None,
        max_tokens: int = 4096,
        timeout_seconds: float = 120.0,
    ) -> None:
        self.task_type = task_type
        self.description = description
        self.prompt_instruction = prompt_instruction
        self.expected_patterns = expected_patterns or []
        self.forbidden_patterns = forbidden_patterns or []
        self.max_tokens = max_tokens
        self.timeout_seconds = timeout_seconds


DEFAULT_BENCHMARK_TASKS: list[BenchmarkTask] = [
    BenchmarkTask(
        task_type=TaskType.BUG_FIX,
        description="Fix off-by-one error in list slicing",
        prompt_instruction=(
            "The function `get_last_n(items, n)` returns `items[:-n]` but should "
            "return `items[-n:]`. Fix the bug. Return only the corrected function."
        ),
        expected_patterns=[r"items\[-n:\]", r"items\[-n\]", r"def get_last_n"],
        forbidden_patterns=[r"items\[:-n\]"],
    ),
    BenchmarkTask(
        task_type=TaskType.FEATURE,
        description="Add retry decorator with exponential backoff",
        prompt_instruction=(
            "Write a Python decorator `retry(max_retries=3, base_delay=1.0)` that "
            "retries a function with exponential backoff on any exception. Include a "
            "docstring. Return only the decorator code."
        ),
        expected_patterns=[
            r"def retry",
            r"max_retries",
            r"base_delay",
            r"exponential",
            r"sleep",
            r"try.*except",
        ],
    ),
    BenchmarkTask(
        task_type=TaskType.TEST_WRITE,
        description="Write unit tests for a stack implementation",
        prompt_instruction=(
            "Given a `Stack` class with `push`, `pop`, `peek`, and `is_empty` methods, "
            "write comprehensive unit tests using pytest. Cover edge cases: empty stack "
            "pop, peek on empty stack, push-then-pop roundtrip."
        ),
        expected_patterns=[
            r"def test_",
            r"Stack",
            r"push",
            r"pop",
            r"peek",
            r"is_empty",
            r"pytest\.raises|raises",
        ],
    ),
    BenchmarkTask(
        task_type=TaskType.REFACTOR,
        description="Refactor nested conditionals to guard clauses",
        prompt_instruction=(
            "Refactor this function to use guard clauses instead of nested if/else:\n\n"
            "def process(data):\n"
            "    if data is not None:\n"
            "        if 'type' in data:\n"
            "            if data['type'] == 'A':\n"
            "                return handle_a(data)\n"
            "            else:\n"
            "                return handle_other(data)\n"
            "    return None\n\n"
            "Return only the refactored function."
        ),
        expected_patterns=[r"if data is None", r"return None", r"if .+ not in"],
        forbidden_patterns=[r"if data is not None.*:\s*if"],
    ),
    BenchmarkTask(
        task_type=TaskType.CODE_REVIEW,
        description="Review code for security vulnerabilities",
        prompt_instruction=(
            "Review this code for security issues and list them:\n\n"
            "def get_user(user_id):\n"
            "    query = f\"SELECT * FROM users WHERE id = {user_id}\"\n"
            "    return db.execute(query)\n\n"
            "Provide a numbered list of issues found."
        ),
        expected_patterns=[
            r"(?i)sql.?injection",
            r"(?i)parameterized|prepared|placeholder",
            r"1[.)]",
        ],
    ),
    BenchmarkTask(
        task_type=TaskType.DOCUMENTATION,
        description="Write docstrings for a module",
        prompt_instruction=(
            "Add Google-style docstrings to this function:\n\n"
            "def merge_configs(base, override):\n"
            "    result = base.copy()\n"
            "    for key, value in override.items():\n"
            '        if isinstance(value, dict) and key in result and isinstance(result[key], dict):\n'
            "            result[key] = merge_configs(result[key], value)\n"
            "        else:\n"
            "            result[key] = value\n"
            "    return result\n\n"
            "Return the function with docstring."
        ),
        expected_patterns=[r'"""', r"Args:|Arguments:", r"Returns:", r"dict"],
    ),
    BenchmarkTask(
        task_type=TaskType.DEBUGGING,
        description="Debug a race condition",
        prompt_instruction=(
            "This async function has a race condition. Identify and fix it:\n\n"
            "async def transfer(from_account, to_account, amount):\n"
            "    balance = await get_balance(from_account)\n"
            "    if balance >= amount:\n"
            "        await debit(from_account, amount)\n"
            "        await credit(to_account, amount)\n"
            "    return True\n\n"
            "Return the corrected function with explanation of the fix."
        ),
        expected_patterns=[
            r"(?i)race.?condition|lock|mutex|atomic|transaction",
        ],
    ),
    BenchmarkTask(
        task_type=TaskType.OPTIMIZATION,
        description="Optimize O(n^2) list operations",
        prompt_instruction=(
            "This function is O(n^2). Optimize it to O(n):\n\n"
            "def find_pairs(items, target):\n"
            "    pairs = []\n"
            "    for i, a in enumerate(items):\n"
            "        for j, b in enumerate(items):\n"
            "            if i != j and a + b == target:\n"
            "                pairs.append((a, b))\n"
            "    return pairs\n\n"
            "Return only the optimized function."
        ),
        expected_patterns=[r"dict|set|{}", r"for .+ in", r"target"],
        forbidden_patterns=[r"for .+ in items.*:\s*for .+ in items"],
    ),
    BenchmarkTask(
        task_type=TaskType.SECURITY_FIX,
        description="Fix command injection vulnerability",
        prompt_instruction=(
            "This code is vulnerable to command injection. Fix it:\n\n"
            'import os\n\ndef ping_host(host):\n'
            '    os.system(f"ping -c 1 {host}")\n\n'
            "Return the corrected code."
        ),
        expected_patterns=[
            r"subprocess",
            r"(?i)validate|sanitize|whitelist|allowlist",
        ],
        forbidden_patterns=[r"os\.system"],
    ),
    BenchmarkTask(
        task_type=TaskType.INTEGRATION,
        description="Implement API client with error handling",
        prompt_instruction=(
            "Write an async API client class `APIClient` with a `get` method that:\n"
            "1. Makes HTTP GET requests using httpx\n"
            "2. Retries on 5xx errors (max 3 retries)\n"
            "3. Raises a custom `APIError` on 4xx errors\n"
            "4. Returns parsed JSON on success\n"
            "Return the full class implementation."
        ),
        expected_patterns=[
            r"class APIClient",
            r"async def get",
            r"httpx",
            r"retry",
            r"(?i)5\d\d|server.?error",
            r"(?i)4\d\d|client.?error",
        ],
    ),
]


class PromptScoringEngine:
    def __init__(
        self,
        model_gateway: Any | None = None,
        benchmark_repo: Any | None = None,
    ) -> None:
        self._gateway = model_gateway
        self._repo = benchmark_repo
        self._tasks = list(DEFAULT_BENCHMARK_TASKS)

    def score_output(
        self,
        output: str,
        task: BenchmarkTask,
    ) -> BenchmarkScores:
        completion = self._score_completion(output, task)
        code_quality = self._score_code_quality(output)
        instruction = self._score_instruction_adherence(output, task)
        token_efficiency = self._score_token_efficiency(output)
        return BenchmarkScores(
            completion_score=completion,
            code_quality_score=code_quality,
            instruction_adherence_score=instruction,
            token_efficiency_score=token_efficiency,
        )

    def _score_completion(self, output: str, task: BenchmarkTask) -> float:
        if not output.strip():
            return 0.0
        if not task.expected_patterns:
            return 0.7
        matches = 0
        for pattern in task.expected_patterns:
            if re.search(pattern, output, re.DOTALL | re.IGNORECASE):
                matches += 1
        return min(1.0, matches / len(task.expected_patterns))

    def _score_code_quality(self, output: str) -> float:
        score = 0.3
        if re.search(r"def \w+", output):
            score += 0.15
        if re.search(r'""".*?"""', output, re.DOTALL) or re.search(r"'''.*?'''", output, re.DOTALL):
            score += 0.15
        if re.search(r"(?m)^\s*(import |from )", output):
            score += 0.1
        if re.search(r"try:", output) and re.search(r"except", output):
            score += 0.1
        if re.search(r"type.*:|->", output):
            score += 0.1
        if len(output.split("\n")) > 5:
            score += 0.1
        return min(1.0, score)

    def _score_instruction_adherence(self, output: str, task: BenchmarkTask) -> float:
        score = 0.5
        for pattern in task.forbidden_patterns:
            if re.search(pattern, output, re.DOTALL):
                score -= 0.3
        if "return only" in task.prompt_instruction.lower():
            has_explanation = any(
                w in output.lower()
                for w in ["here is", "here's", "i've", "explanation", "let me"]
            )
            if not has_explanation:
                score += 0.2
            else:
                score -= 0.1
        lines = output.strip().split("\n")
        if len(lines) > 0 and not output.strip().startswith("#"):
            score += 0.1
        return max(0.0, min(1.0, score))

    def _score_token_efficiency(self, output: str) -> float:
        if not output.strip():
            return 0.0
        code_lines = [
            line for line in output.split("\n")
            if line.strip() and not line.strip().startswith("#")
        ]
        if not code_lines:
            return 0.1
        total_chars = sum(len(line) for line in code_lines)
        avg_line_length = total_chars / len(code_lines)
        if avg_line_length < 30:
            return 0.9
        elif avg_line_length < 60:
            return 0.8
        elif avg_line_length < 100:
            return 0.6
        return 0.4

    def get_tasks(self) -> list[BenchmarkTask]:
        return list(self._tasks)

    def get_tasks_for_type(self, task_type: TaskType) -> list[BenchmarkTask]:
        return [t for t in self._tasks if t.task_type == task_type]
