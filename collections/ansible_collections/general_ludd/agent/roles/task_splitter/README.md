# task_splitter

Analyze a task/prompt and recommend whether to split it into parallel subtasks
for subagent dispatch.

## FQCN

`general_ludd.agent.task_splitter`

## Example

```yaml
- hosts: localhost
  vars:
    task_description: "Add dark mode support across the codebase"
    task_context: "The app currently uses hardcoded color values. TASKS.md lists 3 views that need theming."
    max_subtasks: 5
  roles:
    - role: general_ludd.agent.task_splitter
```

## Inputs

See `defaults/main.yml` for the full variable list with defaults.

Core inputs:
- `task_description` (required): the prompt/task text to analyze
- `task_context`: optional background from TASKS.md, spec, or prior work
- `max_subtasks`: max subtasks to propose (default 7)
- `min_cost_benefit_ratio`: min estimated cost saving ratio to recommend splitting (default 2.0)

## Output Artifacts

- `{{ artifact_dir }}/task_splitter.json` — structured result with `should_split`, `subtasks`, `research_needs`, `rationale`
- `{{ artifact_dir }}/task_splitter.md` — markdown listing of subtasks (only written when `should_split` is true)
