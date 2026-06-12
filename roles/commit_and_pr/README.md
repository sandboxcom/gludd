# commit_and_pr role

Commits staged changes and optionally (with explicit operator opt-in) pushes
and creates a pull request.

## Security

Push and PR creation are **disabled by default**. This role will never push
to a remote or create a PR unless the operator explicitly sets `enable_push: true`
and/or `enable_pr: true`.

## Variables

| Variable | Default | Description |
|---|---|---|
| `repo_path` | `.` | Repository path |
| `commit_message` | `auto: agent commit` | Commit message |
| `enable_push` | `false` | **Must be true to push** |
| `enable_pr` | `false` | **Must be true to create PR** |
| `remote` | `origin` | Git remote name |
| `artifact_dir` | `/tmp/gludd-commit-pr` | Artifact output directory |
