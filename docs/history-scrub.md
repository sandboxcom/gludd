# History Scrub — SSH Key Removal

## Context

The private key `sandboxcom_github_rsa` and its public counterpart `sandboxcom_github_rsa.pub`
were committed to the repository at the project root. The key comment leaks
`shawnwilson@Mac.localdomain`. These files are in git history and must be purged.

## Pre-Scrub (Operator)

1. **Rotate the key immediately** on the `sandboxcom/gludd` GitHub repo:
   - Settings → Deploy keys → Delete the key matching `sandboxcom_github_rsa.pub`
   - Generate a new deploy key: `ssh-keygen -t ed25519 -f ~/.ssh/sandboxcom_github_ed25519`
   - Add the new public key to the repo

2. **Verify no active deployments** use the compromised key.

## Scrub Commands (Operator — run from repo root)

### Option A: git-filter-repo (recommended)

```bash
# Install: pip install git-filter-repo
# OR: brew install git-filter-repo

git filter-repo \
  --path sandboxcom_github_rsa \
  --path sandboxcom_github_rsa.pub \
  --invert-paths \
  --force
```

### Option B: BFG Repo-Cleaner

```bash
# Download from https://rtyley.github.io/bfg-repo-cleaner/

java -jar bfg.jar --delete-files sandboxcom_github_rsa .
java -jar bfg.jar --delete-files sandboxcom_github_rsa.pub .
git reflog expire --expire=now --all
git gc --prune=now --aggressive
```

## Post-Scrub

1. **Force push to the mirror**:
   ```bash
   # WARNING: This rewrites public history. All collaborators must re-clone.
   git push --force --all origin
   git push --force --tags origin
   ```

2. **Verify the keys are gone**:
   ```bash
   git log --all --full-history -- sandboxcom_github_rsa
   git log --all --full-history -- sandboxcom_github_rsa.pub
   # Should return nothing
   ```

3. **Update Makefile**: After the scrub, change `git-remote-sandboxcom` target to use
   the new key path (already externalized via `SSH_KEY` variable).

4. **Notify all collaborators** to re-clone the repository.

## Agent Actions (completed)

- [x] `git rm --cached` applied to both key files (`make untrack`)
- [x] `.gitignore` entries confirmed: `sandboxcom_github_rsa` and `sandboxcom_github_rsa.pub`
- [x] Makefile targets updated to use `SSH_KEY ?= ~/.ssh/sandboxcom_github_rsa` (external path)
- [ ] Operator: run the scrub commands above
- [ ] Operator: force push to mirror
