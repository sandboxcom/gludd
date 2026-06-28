#!/usr/bin/env python3
"""Threshold + hysteresis tests for token_window_monitor.decide_floor."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from token_window_monitor import FLOOR_NORMAL, FLOOR_THROTTLE, decide_floor  # noqa: E402

B = 1000  # budget for the test; watermarks are 95% / 90%.

# (spend, current_floor, expected_floor, description)
CASES = [
    (950, FLOOR_NORMAL, FLOOR_THROTTLE, "exactly 95% -> throttle to 1"),
    (999, FLOOR_NORMAL, FLOOR_THROTTLE, "99.9% -> throttle"),
    (1000, FLOOR_THROTTLE, FLOOR_THROTTLE, "100% stays throttled"),
    (1200, FLOOR_NORMAL, FLOOR_THROTTLE, "over budget -> throttle"),
    (899, FLOOR_THROTTLE, FLOOR_NORMAL, "<90% from throttle -> restore (window reset)"),
    (0, FLOOR_THROTTLE, FLOOR_NORMAL, "reset to 0 -> restore"),
    (889, FLOOR_NORMAL, FLOOR_NORMAL, "88.9% -> normal"),
    (920, FLOOR_NORMAL, FLOOR_NORMAL, "92% hold (was normal)"),
    (920, FLOOR_THROTTLE, FLOOR_THROTTLE, "92% hold (was throttled) -- hysteresis"),
    (940, FLOOR_THROTTLE, FLOOR_THROTTLE, "94% still throttled (hold in band)"),
    (949, FLOOR_THROTTLE, FLOOR_THROTTLE, "94.9% hold throttled"),
]

fail = 0
for spend, cur, exp, desc in CASES:
    got = decide_floor(spend, B, cur)
    ok = got == exp
    if not ok:
        fail += 1
    print(f"[{'PASS' if ok else 'FAIL'}] spend={spend:<5} cur={cur} -> {got} (exp {exp})  # {desc}")

# budget<=0 must hold the current floor (avoid div-by-zero / bad signal).
hold = decide_floor(500, 0, 5)
ok = hold == 5
if not ok:
    fail += 1
print(f"[{'PASS' if ok else 'FAIL'}] budget<=0 holds current floor -> {hold} (exp 5)")

total = len(CASES) + 1
print(f"--- {total - fail} passed, {fail} failed ---")
sys.exit(0 if fail == 0 else 1)
