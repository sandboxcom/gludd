# triage_issue role

Turn an inbound issue/todo into a structured plan.

Gathers live system facts via `gludd_facts`, runs a model agent to classify
the issue (severity, complexity, work type, next action), then sends the
plan to the appropriate downstream role via `gludd_message`.

Skips automatically when `issue_body` is empty and the backlog is also empty.

## Key variables

| Variable | Default | Notes |
|---|---|---|
| `issue_id` | `""` | Issue identifier |
| `issue_title` | `""` | Issue title |
| `issue_body` | `""` | Issue body (required for triage) |
| `triage_handoff_recipient` | `"implement_change"` | Role to hand off plan to |
| `enable_git_push` | `false` | Push DISABLED by default |

## Example

```yaml
- name: Triage a GitHub issue
  ansible.builtin.include_role:
    name: general_ludd.agent.triage_issue
  vars:
    issue_id: "GH-123"
    issue_title: "Pagination is broken for large result sets"
    issue_body: "When fetching more than 100 items the API returns 500..."
    triage_handoff_recipient: "implement_change"
```
