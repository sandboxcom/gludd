---
{
  "name": "mp-handoff",
  "description": "Compact the current conversation into a handoff document so another agent can continue the work. Adapted from mattpocock/skills.",
  "tags": [
    "mattpocock",
    "handoff",
    "session",
    "productivity"
  ],
  "category": "productivity"
}
---

# Handoff

Write a handoff document summarizing the conversation for a fresh agent.

- Save to OS temp directory (not workspace)
- Include suggested skills section
- Don't duplicate content in other artifacts (PRDs, plans, ADRs, issues, commits) — reference by path/URL
- Redact sensitive info
- Tailor to what the next session will focus on
