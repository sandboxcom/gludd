# sprint_board_report

Board state grouped by status from `gludd_facts.todos` and `gludd_facts.work`. Composes `report_status` data — does not duplicate health-classification logic.

## Board Columns

| Column | Source |
|---|---|
| `todo` | items with status backlog/todo/pending + backlog_size |
| `in_progress` | items with status in_progress/active/running + active_jobs |
| `review` | items with status review/pr_open/needs_review |
| `done` | items with status done/completed/closed + done total |

## Variables

| Variable | Default | Description |
|---|---|---|
| `sprint_name` | `Sprint-1` | Sprint name for board header |
| `artifact_dir` | `/tmp/gludd-sprint-board-report` | Output directory |
| `daemon_url` | `http://localhost:8000` | Daemon base URL |

## Artifacts

- `sprint_board_report.json` — columns{status:[items]}, counts
- `sprint_board_report.md` — markdown kanban board
