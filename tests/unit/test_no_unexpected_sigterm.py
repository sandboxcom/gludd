from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TEST_ROOT = ROOT / "tests"
INTENTIONAL_SIGTERM_TESTS = {
    Path("tests/unit/test_writer_child.py"),
    Path("tests/unit/test_ci_shards_parallel.py"),
    # Exercises the guarded shard runner SIGTERM trap without killing pytest.
    Path("tests/unit/test_ci_shard_summary_runner.py"),
    Path("tests/unit/test_task_watchdog.py"),
    Path("tests/unit/test_background_test_runner.py"),
    Path("tests/unit/test_local_inference.py"),
    Path("tests/unit/test_local_inference_hardening.py"),
    Path("tests/unit/test_process_registry.py"),
    Path("tests/unit/test_registry_seal.py"),
    Path("tests/unit/test_gate_process_cleanup.py"),
}
DIRECT_SIGTERM_PATTERNS = (
    re.compile(r"\.terminate\("),
    re.compile(r"\.send_signal\(\s*signal\.SIGTERM"),
    re.compile(r"os\.kill(?:pg)?\([^\n]*SIGTERM"),
)
IGNORED_TEXT = ("assert", "mock", "#", "Finding", "line ")


def test_direct_sigterm_usage_is_limited_to_signal_intent_tests() -> None:
    offenders: list[str] = []
    for path in sorted(TEST_ROOT.rglob("test_*.py")):
        rel = path.relative_to(ROOT)
        text = path.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), start=1):
            stripped = line.strip()
            if any(token in stripped for token in IGNORED_TEXT):
                continue
            if not any(pattern.search(stripped) for pattern in DIRECT_SIGTERM_PATTERNS):
                continue
            if rel not in INTENTIONAL_SIGTERM_TESTS:
                offenders.append(f"{rel}:{lineno}: {stripped}")
    assert not offenders, "Unexpected direct SIGTERM usage in tests:" + chr(10) + chr(10).join(offenders)
