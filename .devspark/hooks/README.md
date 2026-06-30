# DevSpark Optional Hooks

## pre-commit-review-isolation.ps1

Guards PR review artifact commit discipline by blocking commits that stage both:

- `.documentation/specs/pr-review/pr-*.md`
- any other path

### Option 1: Native git hook

1. Copy this script into `.git/hooks/pre-commit` (or invoke it from that file).
2. Ensure PowerShell is available (`pwsh`).
3. Example `.git/hooks/pre-commit` shim:

```bash
#!/usr/bin/env bash
pwsh -File .devspark/hooks/pre-commit-review-isolation.ps1
```

### Option 2: pre-commit framework

Invoke `pwsh -File .devspark/hooks/pre-commit-review-isolation.ps1` from your pre-commit framework config as a local hook.

If the hook blocks a commit, split staged changes into:

1. code-fix commit(s)
2. review-file-only commit
