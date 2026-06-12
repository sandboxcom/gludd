# git_setup role

Initializes and configures a git repository for agent use.

## Variables

| Variable | Default | Description |
|---|---|---|
| `repo_path` | `.` | Path to the repository |
| `user_email` | `agent@harness.local` | Git user email |
| `user_name` | `Agentic Harness Agent` | Git user name |
| `init_if_missing` | `true` | Run `git init` if `.git` is absent |
