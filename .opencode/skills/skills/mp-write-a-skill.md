---
{
  "name": "mp-write-a-skill",
  "description": "Create new agent skills with proper structure, progressive disclosure, and bundled resources. Adapted from mattpocock/skills.",
  "tags": [
    "mattpocock",
    "skills",
    "authoring",
    "productivity"
  ],
  "category": "productivity"
}
---

# Write a Skill

Create new agent skills with proper structure.

Structure: skill-name/SKILL.md (required), REFERENCE.md, EXAMPLES.md, scripts/ (optional).

Description requirements: Max 1024 chars, third person, first sentence = what it does, second sentence = 'Use when [triggers]'. The description is the ONLY thing the agent sees when deciding which skill to load.

Split files when SKILL.md exceeds 100 lines. Add scripts for deterministic operations.

