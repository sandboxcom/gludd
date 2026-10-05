#!/usr/bin/env python3
"""
check_dispatch_diversity.py

Pre-dispatch checker: validates a voluntary dispatch wave for bounded,
productive ownership. Reads TASKS.md for in-progress task IDs, cross-references
the dispatch prompts, and enforces one-to-three dispatches, topic diversity for
multi-prompt waves, and a continuation when in-progress work exists.

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
MAX_DISPATCHES = 3
MIN_MULTI_WAVE_TOPICS = 2


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
        print("Usage: check_dispatch_diversity.py <dispatch-wave.json> [tasks-md-path]", file=sys.stderr)
        return 2

    wave_file = Path(sys.argv[1])
    prompts = load_prompts(wave_file)
    if prompts is None:
        return 2

    if len(sys.argv) >= 3:
        tasks_path = Path(sys.argv[2])
    else:
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

    if n_prompts < 1 or n_prompts > MAX_DISPATCHES:
        violations.append(
            f"DISPATCH COUNT: {n_prompts} "
            f"(requires at least 1 and at most {MAX_DISPATCHES})"
        )

    num_distinct = len(topic_counts)
    if n_prompts > 1 and num_distinct < MIN_MULTI_WAVE_TOPICS:
        violations.append(
            f"TOPIC DIVERSITY: {num_distinct} distinct topics "
            f"(requires >={MIN_MULTI_WAVE_TOPICS} for a multi-prompt wave). "
            f"Topics found: {', '.join(sorted(topic_counts.keys()))}"
        )

    if n_prompts > 1 and topic_counts:
        newest_topic = topic_counts.most_common(1)[0][0]
        newest_count = topic_counts[newest_topic]
        if newest_count == n_prompts:
            violations.append(
                f"SLOT CONCENTRATION: '{newest_topic}' has {newest_count}/{n_prompts} "
                "slots and consumes the entire multi-prompt wave; "
                "use at least two topics"
            )

    if in_progress_ids and continuations < 1:
        violations.append(
            "NO CONTINUATIONS: 0 slots reference an in-progress TASKS.md item. "
            f"Known in-progress IDs: {sorted(in_progress_ids)}"
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
