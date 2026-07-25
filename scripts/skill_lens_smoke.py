#!/usr/bin/env python3
"""Smoke-test the skill-lens → SkillContextProvider pipeline.

Runs lens() on sample queries and reports token savings.
Exits 0 on success, 1 if no skills were matched (indicates broken wiring).
"""

from __future__ import annotations

import sys

from general_ludd.agents.skill_context import SkillContextProvider

QUERIES = [
    "debug an asyncio deadlock",
    "write a goroutine worker pool",
    "add type annotations with mypy",
    "write isolated unit tests",
    "build a guardrail with hooks",
    "create a reveal.js presentation",
    "bootstrap a new enforcement plugin",
    "run a background test suite",
]

provider = SkillContextProvider()
total = 0
any_matched = False

for q in QUERIES:
    ctx = provider.provide(q)
    if ctx.skills_used:
        any_matched = True
        print(f"  Query: {q!r}")
        print(f"    Skills: {ctx.skills_used}")
        print(f"    Tokens saved: ~{ctx.token_savings}")
        total += ctx.token_savings
    else:
        print(f"  Query: {q!r} -> no skills matched")

print()
print(f"Total token savings across queries: ~{total}")

if not any_matched:
    print("ERROR: No skills matched any query — SkillContextProvider wiring may be broken.")
    sys.exit(1)

print("skill-lens smoke: PASS")
