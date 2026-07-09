#!/usr/bin/env python3
"""Parse CI faillog output and categorize test failures by root cause.

Input: stdin (pipe from ``make ci-faillog RUN=<id>``)
Output: grouped failure categories with counts and first 3 examples each.

Categories:
  CAPLOG     — caplog propagation / fixture pollution issues
  IMPORT     — import errors / missing modules
  ASSERTION  — wrong assertions
  TIMEOUT    — pytest-timeout expirations
  OOM        — process killed by signal (SIGKILL, OOM killer)
  OTHER      — everything else
"""

import re
import sys
from collections import defaultdict
from typing import Optional


FAILED_LINE = re.compile(
    r"^FAILED\s+(?P<test_id>\S+)\s*[-—–]\s*(?P<reason>.*)$",
    re.IGNORECASE,
)

CAPLOG_PATTERNS = [
    re.compile(rf".*{p}", re.IGNORECASE)
    for p in [
        r"caplog",
        r"CaplogPropagationError",
        r"CaplogSetMultipleError",
        r"LogCaptureFixture",
        r"caplog_(?:empty|missing|propagat)",
        r"cannot set.*handler.*logger",
        r"already has a handler",
        r"propagat(?:e|ion).*(?:error|issue|problem)",
        r"logger.*propagat",
    ]
]

IMPORT_PATTERNS = [
    re.compile(rf".*{p}", re.IGNORECASE)
    for p in [
        r"ImportError",
        r"ModuleNotFoundError",
        r"No module named",
        r"cannot import",
        r"could not import",
        r"import.*error",
        r"unresolved import",
        r"relative import",
    ]
]

ASSERTION_PATTERNS = [
    re.compile(rf".*{p}", re.IGNORECASE)
    for p in [
        r"AssertionError",
        r"assert\s",
        r"assertion.*fail",
        r"expected.*got",
        r"Expected.*but got",
    ]
]

TIMEOUT_PATTERNS = [
    re.compile(rf".*{p}", re.IGNORECASE)
    for p in [
        r"TimeoutError",
        r"pytest.?timeout",
        r"TimeoutExpired",
        r"timed out",
        r"test.*timed? ?out",
        r"Took too long",
        r"timeout.*exceeded",
        r"Wall time exceeded",
    ]
]

OOM_PATTERNS = [
    re.compile(rf".*{p}", re.IGNORECASE)
    for p in [
        r"SIGKILL",
        r"SIGTERM",
        r"killed",
        r"Out.?of.?memory",
        r"OOM.{0,10}killer",
        r"memory.{0,10}error",
        r"Cannot allocate memory",
        r"exit code 137",
        r"signal\s+9",
        r"signal\s+15",
    ]
]

CATEGORY_DETECTORS = [
    ("CAPLOG", CAPLOG_PATTERNS),
    ("IMPORT", IMPORT_PATTERNS),
    ("TIMEOUT", TIMEOUT_PATTERNS),
    ("OOM", OOM_PATTERNS),
    ("ASSERTION", ASSERTION_PATTERNS),
]


class Failure:
    __slots__ = ("test_id", "reason", "category")

    def __init__(self, test_id: str, reason: str, category: str) -> None:
        self.test_id = test_id
        self.reason = reason
        self.category = category


def classify(reason: str) -> str:
    combined = reason.strip()
    for cat_name, patterns in CATEGORY_DETECTORS:
        for pat in patterns:
            if pat.match(combined):
                return cat_name
    return "OTHER"


def parse_stdin(lines: list[str]) -> tuple[list[Failure], set[str]]:
    failures: list[Failure] = []
    seen: set[str] = set()
    for line in lines:
        m = FAILED_LINE.match(line.strip())
        if not m:
            continue
        test_id = m.group("test_id")
        reason = (m.group("reason") or "").strip()
        if test_id in seen:
            continue
        seen.add(test_id)
        category = classify(reason)
        failures.append(Failure(test_id=test_id, reason=reason, category=category))
    return failures, seen


def maybe_report_errors(lines: list[str], failures: list[Failure]) -> None:
    error_lines = [
        l for l in lines if re.search(r"ERROR\s+", l) and "ERRORS" not in l
    ]
    if error_lines:
        sys.stderr.write(f"  found {len(error_lines)} ERROR line(s) "
                         f"(collection / fixture setup failures)\n")


def report(failures: list[Failure], seen_keys: set[str]) -> None:
    grouped: defaultdict[str, list[Failure]] = defaultdict(list)
    for f in failures:
        grouped[f.category].append(f)

    total = len(seen_keys)
    categorized = sum(len(v) for v in grouped.values())

    print(f"TOTAL FAILURES  : {total}")
    print(f"  Categorized   : {categorized}")
    if total != categorized:
        missing = total - categorized
        if missing == 1:
            print(f"  UNCATEGORIZED  : {missing} (duplicate FAILED line?)")
        else:
            print(f"  UNCATEGORIZED  : {missing}")
    print()

    for cat_name in ["CAPLOG", "IMPORT", "ASSERTION", "TIMEOUT", "OOM", "OTHER"]:
        items = grouped.get(cat_name, [])
        if not items:
            continue
        print(f"[{cat_name}]  {len(items)} failure(s)")
        shown = 0
        for i, f in enumerate(items):
            if shown >= 3:
                if len(items) > 3:
                    print(f"  ... and {len(items) - 3} more")
                break
            shown += 1
            reason = f.reason
            if reason and len(reason) > 120:
                reason = reason[:117] + "..."
            print(f"  [{i+1}] {f.test_id} — {reason}")
        print()

    if not failures:
        print("No categorized failures found.")


def main() -> None:
    lines = [l.rstrip("\n") for l in sys.stdin]
    classified, seen_keys = parse_stdin(lines)

    maybe_report_errors(lines, classified)
    report(classified, seen_keys)


if __name__ == "__main__":
    main()
