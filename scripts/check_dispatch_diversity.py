#!/usr/bin/env python3
"""
check_dispatch_diversity.py

Pre-dispatch checker: validates a dispatch wave for diversity invariants.
Reads TASKS.md for in_progress task IDs, cross-references the dispatch prompts,
and enforces: exactly 10 dispatches, >=3 distinct topics, <=50% slots to any
one topic, >=1 continuation slot.

Usage:
    python3 scripts/check_dispatch_diversity.py /tmp/dispatch-wave.json

Exit codes:
    0   Wave passes all diversity checks.
    1   Invariant violation — see stderr for guidance.
    2   I/O error (fail-open).
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

ID_PATTERN = re.compile(r"(?:^|\s)([A-Z]{1,4}[\d]*[-.]\d+(?:\.\d+)*)(?:\s|$|\.|—)")
COMMON_WORDS: frozenset[str] = frozenset(
    {
        "fix",
        "add",
        "read",
        "write",
        "test",
        "check",
        "run",
        "make",
        "implement",
        "create",
        "the",
        "and",
        "for",
        "with",
        "that",
        "this",
        "from",
        "all",
        "any",
        "new",
        "use",
        "need",
        "file",
        "code",
        "should",
        "must",
        "also",
        "issue",
    }
)


def read_tasks_file(tasks_path: Path) -> str:
    try:
        return tasks_path.read_text(encoding="utf-8")
    except OSError:
        return ""


def extract_in_progress_ids(tasks_text: str) -> set[str]:
    ids: set[str] = set()
    for line in tasks_text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("- [ ]"):
            continue
        if "status: in_progress" not in line:
            continue
        for tid in ID_PATTERN.findall(stripped):
            ids.add(tid)
    return ids


def load_prompts(path: Path) -> list[str] | None:
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
        if not isinstance(data, list):
            print(f"ERROR: {path} does not contain a JSON array of strings", file=sys.stderr)
            return None
        if not all(isinstance(p, str) for p in data):
            print(f"ERROR: {path} contains non-string entries", file=sys.stderr)
            return None
        return data
    except FileNotFoundError:
        print(f"ERROR: file not found: {path}", file=sys.stderr)
        return None
    except (json.JSONDecodeError, ValueError) as e:
        print(f"ERROR: invalid JSON in {path}: {e}", file=sys.stderr)
        return None
    except OSError as e:
        print(f"ERROR: I/O error reading {path}: {e}", file=sys.stderr)
        return None


def extract_topic(prompt: str) -> str:
    tid_match = ID_PATTERN.search(prompt)
    if tid_match:
        return tid_match.group(1)

    words = prompt.lower().split()
    for w in words:
        if w.isalpha() and w not in COMMON_WORDS and len(w) > 2:
            return w
    return prompt[:40]


def classify_prompt(prompt: str, in_progress_ids: set[str]) -> str:
    prompt_ids = set(ID_PATTERN.findall(prompt))
    if prompt_ids & in_progress_ids:
        return "continuation"
    return "new"


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: check_dispatch_diversity.py <dispatch-wave.json>", file=sys.stderr)
        return 2

    wave_file = Path(sys.argv[1])
    prompts = load_prompts(wave_file)
    if prompts is None:
        return 2

    repo_root = Path(__file__).resolve().parent.parent
    tasks_path = repo_root / "TASKS.md"
    tasks_text = read_tasks_file(tasks_path)
    in_progress_ids = extract_in_progress_ids(tasks_text)

    n_prompts = len(prompts)
    classifications = [classify_prompt(p, in_progress_ids) for p in prompts]
    continuations = classifications.count("continuation")
    topics = [extract_topic(p) for p in prompts]
    topic_counts = Counter(topics)

    violations: list[str] = []

    if n_prompts != 10:
        violations.append(f"DISAPTCH COUNT: {n_prompts} (requires exactly 10)")

    num_distinct = len(topic_counts)
    if num_distinct < 3:
        violations.append(
            f"TOPIC DIVERSITY: {num_distinct} distinct topics (requires >=3). "
            f"Topics found: {', '.join(sorted(topic_counts.keys()))}"
        )

    if topic_counts:
        newest_topic = topic_counts.most_common(1)[0][0]
        newest_count = topic_counts[newest_topic]
        if newest_count > n_prompts / 2:
            violations.append(
                f"SLOT CONCENTRATION: '{newest_topic}' has {newest_count}/{n_prompts} "
                f"slots ({newest_count / n_prompts:.0%}), exceeds 50% maximum"
            )

    if continuations < 1:
        violations.append(
            "NO CONTINUATIONS: 0 slots reference an in-progress TASKS.md item. "
            f"Known in-progress IDs: {sorted(in_progress_ids) if in_progress_ids else '(none)'}"
        )

    if not violations:
        print(
            f"check-dispatch-diversity: PASS — {n_prompts} dispatches, "
            f"{num_distinct} topics, {continuations} continuation(s), "
            f"max topic concentration {topic_counts.most_common(1)[0][1]}/{n_prompts}"
            if topic_counts
            else f"check-dispatch-diversity: PASS — {n_prompts} dispatches, {continuations} continuation(s)"
        )
        return 0

    print("check-dispatch-diversity: FAILED", file=sys.stderr)
    for v in violations:
        print(f"  - {v}", file=sys.stderr)
    print(f"\n  Summary: {n_prompts} total, {num_distinct} topics, {continuations} continuation(s).", file=sys.stderr)
    if topic_counts:
        print(f"  Topics: {dict(topic_counts.most_common())}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
