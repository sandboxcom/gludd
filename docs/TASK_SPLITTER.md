# Task Splitter

Analyze a complex agent task and recommend whether to split it into parallel
subtasks for subagent dispatch. Part of the `general_ludd.agent` collection.

## FQCN

`general_ludd.agent.task_splitter`

## Purpose

When an orchestrator receives a task that touches multiple files or subsystems,
the task_splitter role evaluates whether the task can be decomposed into
independent parallel subtasks. This enables the orchestrator to fan out work
across subagents instead of grinding through it serially.

## Usage

```yaml
- hosts: localhost
  vars:
    task_description: "Add dark mode support across the codebase"
    task_context: |
      The app currently uses hardcoded color values.
      TASKS.md lists 3 views that need theming.
    max_subtasks: 5
    min_cost_benefit_ratio: 2.0
  roles:
    - role: general_ludd.agent.task_splitter
```

## Inputs

| Variable | Required | Default | Description |
|---|---|---|---|
| `task_description` | yes | `""` | The prompt/task text to analyze |
| `task_context` | no | `""` | Background from TASKS.md, spec, or prior work |
| `max_subtasks` | no | `7` | Max subtasks to propose |
| `min_cost_benefit_ratio` | no | `2.0` | Minimum estimated cost saving ratio to recommend splitting |
| `model_profile` | no | `""` | Explicit model profile (overrides `route_task_type`) |
| `route_task_type` | no | `"analysis"` | Adaptive routing when `model_profile` is empty |
| `daemon_url` | no | `"http://localhost:8000"` | Daemon endpoint for model calls |
| `psk` | no | `""` | Pre-shared key for daemon auth |
| `artifact_dir` | no | `/tmp/harness-task-splitter` | Output directory for artifacts |

## Output Artifact

### `task_splitter_result.json`

Always written to `{{ artifact_dir }}`. Structured result:

```json
{
  "task_description": "Add dark mode support across the codebase",
  "task_context": "The app currently uses hardcoded color values...",
  "should_split": true,
  "cost_benefit_ratio": 3.5,
  "reasoning": "Task exceeds complexity threshold",
  "subtasks": [
    {"title": "Research: Add dark mode support...", "description": "...", "expected_duration": "2-4 minutes"},
    {"title": "Implement: Add dark mode support...", "description": "...", "expected_duration": "3-6 minutes"},
    {"title": "Test: Add dark mode support...", "description": "...", "expected_duration": "2-4 minutes"}
  ],
  "generated_at": "2026-07-12T14:30:00Z",
  "role_version": "1.0.0"
}
```

## Decision Logic

The role calls `gludd_model_call` (the daemon's model gateway) with a reasoning
model that:

1. Analyzes the task description against context
2. Identifies independent work units (disjoint files, no shared state)
3. Estimates effort per subtask
4. Computes cost-benefit ratio = (serial effort) / (parallel effort)
5. Recommends split when ratio >= `min_cost_benefit_ratio`

## Implementation — Ansible Role ONLY

The `general_ludd.agent.task_splitter` role is the **sole implementation**.
There is no standalone Python module, no CLI subcommand, and no dispatch wiring.
The role is the canonical interface; all callers invoke it via FQCN.

### How it works

1. The role receives `task_description` and optional `task_context` as ansible vars.
2. It calls `gludd_model_call` (the daemon's model gateway) with a system prompt
   instructing the LLM to analyze the task for decomposability.
3. The LLM responds with a structured JSON payload (`should_split`, `subtasks`,
   `research_needs`, `rationale`, `cost_benefit_ratio`).
4. The role parses the JSON response, validates required fields, and writes the
   artifact to `{{ artifact_dir }}/task_splitter_result.json`.
5. Callers read the artifact file to determine whether to fan out the task.

### Invoking from a playbook

```yaml
- hosts: localhost
  vars:
    task_description: "Add dark mode support across the codebase"
    task_context: |
      The app currently uses hardcoded color values.
      TASKS.md lists 3 views that need theming.
  roles:
    - role: general_ludd.agent.task_splitter
```

### Invoking from the agent orchestrator

```yaml
- name: Analyze task for parallel decomposition
  ansible.builtin.include_role:
    name: general_ludd.agent.task_splitter
  vars:
    task_description: "{{ pending_task.description }}"
    task_context: "{{ pending_task.context | default('') }}"
    max_subtasks: 7
    daemon_url: "{{ harness_daemon_url }}"
    psk: "{{ harness_psk }}"
    artifact_dir: "/tmp/harness-task-splitter"
```

After the role completes, read `{{ artifact_dir }}/task_splitter_result.json`.
If `should_split` is `true`, fan out the `subtasks` list; otherwise dispatch
the task as a single unit.
