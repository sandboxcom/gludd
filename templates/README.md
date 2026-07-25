# Prompt Templates

This directory contains the Jinja2 templates (`*.j2`) used to render system
prompts for the agent harness. Each template is selected by the event loop
when a todo is dispatched; the rendered text becomes the system prompt the
model sees for that turn.

## How templates connect to prompt profiles

A **prompt profile** is the string identifier carried on a todo
(`todo.prompt_profile`) — e.g. `"implementation.md.j2"` or a custom name.
When the event loop dispatches a todo it calls `_resolve_prompt_text_static`
in `src/general_ludd/event_loop/loop.py:193`, which resolves that string to
rendered text in this order:

1. **Project-local override.** If `project_templates_dir` is set and a file
   with the same name exists there, it is rendered in a sandboxed Jinja2
   environment. Drop a file at `.general-ludd/templates/<name>.j2` in your
   project root to override any bundled template.
2. **Registry.** Otherwise the bundled `PromptRegistry` renders the profile
   from this directory.
3. **Fallback.** If both fail, no prompt text is attached and a warning is
   logged.

Work-type → template mapping is fixed in
`src/general_ludd/quality/preflight.py:77` (`check_templates`):

| Work type    | Template                    |
|--------------|-----------------------------|
| code         | `implementation.md.j2`      |
| test         | `test_creation.md.j2`       |
| review       | `code_review.md.j2`         |
| docs         | `documentation.md.j2`       |
| analysis     | `gap_analysis.md.j2`        |
| audit        | `log_audit.md.j2`           |
| prompt       | `prompt_eval.md.j2`         |
| dependency   | `dependency_update.md.j2`   |
| refactor     | `implementation.md.j2` (alias of `code`) |
| self_improve | `self_improvement.md.j2`    |
| return_review| `return_review.md.j2`       |

## Template reference

Every template below `{% include 'base_harness_aware.md.j2' %}` as its
first line — that base block provides the harness contract, the
`TaskDecision` JSON schema, the evidence-prefix grammar (`test:`, `file:`,
`commit:`, `artifact:`, `role:`, `module:`, `molecule:`), and the make-target
table. The variables listed below are the per-template context variables
beyond those the base block consumes (the base block takes none).

| Template | Purpose | Context variables |
|----------|---------|-------------------|
| `base_harness_aware.md.j2` | Base system prompt. Establishes the harness contract, the `TaskDecision` JSON schema, the evidence-prefix grammar, and the available `make` targets. Included by every other template. | _(none — consumed via `{% include %})`_ |
| `code_review.md.j2` | Review a worker task return and emit a `TaskDecision` (complete / needs_more_work / failed / blocked / manual_hold / ignore_duplicate). | `return_id`, `task_return_json`, `candidate_todos_json` |
| `implementation.md.j2` | Implement a code change following TDD, then record evidence. Used for both `code` and `refactor` work types. | `todo_title`, `todo_description`, `work_type`, `queue`, `priority` |
| `test_creation.md.j2` | Create unit / integration / e2e / Molecule tests for a change. Enforces AAA structure, naming, and coverage thresholds. | `todo_title`, `todo_description`, `work_type`, `queue`, `priority` |
| `documentation.md.j2` | Write or update markdown documentation for a feature or change. | `todo_title`, `todo_description`, `work_type`, `queue` |
| `gap_analysis.md.j2` | Compare sprint requirements against code/tests/playbooks/prompts and file high-confidence gap todos. | `todo_title`, `todo_description`, `work_type`, `queue` |
| `log_audit.md.j2` | Audit logs for anomalies, retries, stuck todos, and costly prompts. Redacts secrets before any model-based pass. | `todo_title`, `todo_description`, `work_type`, `queue` |
| `prompt_eval.md.j2` | Run prompt-variant benchmarks (pass-rate, cost, latency, schema validity) and promote winners. | `todo_title`, `todo_description`, `work_type`, `queue` |
| `dependency_update.md.j2` | Update a project dependency through the tested workflow (lockfile, sync, compatibility check, validation). | `todo_title`, `todo_description`, `work_type`, `queue` |
| `self_improvement.md.j2` | Implement a self-improvement to the harness itself through the TDD + research + dogfood + staged-reload pipeline. | `todo_title`, `todo_description`, `work_type`, `queue` |
| `return_review.md.j2` | Rich reviewer prompt: renders the task return, candidate todos, artifacts, and optional conversation history as JSON blocks before asking for a `TaskDecision`. | `task_return`, `candidate_todos`, `artifacts`, `conversation_context` (optional) |

### Notes on context variables

- All per-template variables are optional. Each template uses
  `{{ var | default('...') }}`, so a missing variable degrades to a sane
  literal rather than raising `UndefinedError`.
- The renderer uses `jinja2.sandbox.SandboxedEnvironment` with
  `autoescape=True` (see `loop.py:208`), so unsafe attribute access and
  unescaped HTML are blocked.
- The `return_review.md.j2` variables are real Python objects passed through
  `tojson(indent=2)`; the other templates expect string-shaped values.

## Creating a custom template

1. **Pick the file name.** It must end in `.j2` (markdown-flavored names
   like `my_review.md.j2` are conventional). The filename IS the
   `prompt_profile` string a todo references.
2. **Start with the base include.** The first line should be
   `{% include 'base_harness_aware.md.j2' %}` so the harness contract,
   `TaskDecision` schema, and evidence grammar are present. Templates that
   omit this block produce model output the reviewer loop cannot ingest.
3. **Declare the task and instructions** in plain markdown. Reference
   per-todo fields with `{{ todo_title | default('...') }}` style — guard
   every variable with a default.
4. **End with the output contract.** Remind the model to return only a
   valid `TaskDecision` JSON document. The base block already specifies
   the schema; the per-template instruction restates the requirement.
5. **Place the file.** Bundled templates live in this directory
   (`templates/prompts/`). Per-project overrides live at
   `<project_root>/.general-ludd/templates/<name>.j2` and shadow the
   bundled name.
6. **Register the work-type mapping (optional).** If you want a work type
   to dispatch your template by default, extend the `expected` dict in
   `src/general_ludd/quality/preflight.py:77` (`check_templates`) and any
   routing logic that consumes it. Otherwise dispatch the template
   explicitly by setting `prompt_profile` on the todo.
7. **Validate.** Run `make preflight` — `check_templates` reports any
   expected work type whose template is missing from this directory.

### Minimal custom template

```jinja2
{% include 'base_harness_aware.md.j2' %}

Task:
{{ todo_title | default('Custom task') }}

Description:
{{ todo_description | default('No description provided.') }}

Instructions:
1. Do the work described above.
2. Record evidence using the prefix grammar from the base context.
3. Return only a valid TaskDecision JSON document.
```

Save as `.general-ludd/templates/custom_task.md.j2` in your project and
set `prompt_profile: custom_task.md.j2` on the todo (or extend the
work-type map in `preflight.py`).
