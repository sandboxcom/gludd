# User Directives — Check Before Every Response

ALL directives are simultaneously active. New directives ADD, never replace.

## ACTIVE DIRECTIVES

1. **Subagent nesting depth 3** — Every Task dispatch must include "Depth: N/3. Max depth: 3." Subagents at depth 1 dispatch depth-2 subagents for sub-tasks. Depth-2 dispatch depth-3. Depth-3 must NOT dispatch further. Never dispatched depth 2+ — implement NOW.

2. **Exactly 10 subagents per wave** — Every message with task/agent/workflow dispatches must contain EXACTLY 10. Never 5, never 8. If fewer than 10 work items exist, fill with research/quality tasks.

3. **≥3 distinct topic areas per wave** — Never 10 agents all on one topic. Continuation + new feature + quality/research minimum.

4. **≥1 continuation slot per wave** — When TASKS.md has in_progress items, at least one slot must reference an existing task ID.

5. **Finish all specs before starting new ones** — Close existing in_progress items. Do NOT start new feature areas (radio, binary_re) when existing specs (SEC.1, MPL.2, travel wiring) still have remaining work.

6. **Travel expert in ansible collection** — NOT in src/general_ludd/. New experts follow this pattern. Core code for ansible-only experts is forbidden.

7. **Audit core code for unnecessary expert code** — Remove expert code from src/ that belongs in collections.

8. **Never text-only response with pending work** — Every response must include tool calls.

9. **Answer direct questions directly first** — "Yes" or "No" before explanation.

10. **SearXNG integration for travel** — Travel expert must reference SearXNG indexes for live data.

## COMPLETED DIRECTIVES

(None yet — all above still active)

## VERIFICATION

Before composing every response:
1. Read this file
2. Count: how many of the 10 directives does this response satisfy?
3. If < 8, recompose the response
