---
{
  "name": "mp-improve-architecture",
  "description": "Find deepening opportunities in a codebase, informed by domain language and ADRs. Consolidate tightly-coupled modules. Adapted from mattpocock/skills.",
  "tags": [
    "mattpocock",
    "architecture",
    "refactoring",
    "engineering"
  ],
  "category": "engineering"
}
---

# Improve Codebase Architecture

Find deepening opportunities in a codebase.

Process:
1. Explore — read glossary/ADRs, walk codebase noting friction (shallow modules, leaked coupling, no locality, untested areas). Apply deletion test.
2. Present candidates with before/after diagrams, recommendation strength. Do NOT propose interfaces yet — ask user which to explore.
3. Grilling loop — walk design tree, update CONTEXT.md inline, offer ADRs sparingly.

Key principles: deletion test, interface is the test surface, one adapter = hypothetical seam, two adapters = real seam.
