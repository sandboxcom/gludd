# Orchestration Policy — Parallel vs. Serial Work

> **Audience:** any orchestrator or coding agent working on this repo.
> **Status:** normative — this supersedes ad-hoc judgment calls. When in doubt, apply the decision checklist in Section 4.

---

## 1. The Core Rule (read this first)

**Work is SERIAL only if it mutates the single shared `master` working tree or competes for the one gate/commit/push slot. Everything else is PARALLEL — fan it out to an isolated git worktree.**

This repo's tooling makes parallel-safe implementation cheap: `isolation:"worktree"` spins a
full copy of the tree on a throwaway branch. Four independent feature worktrees have run
concurrently in this repo and all passed. The failure mode we have actually suffered is
the opposite: serializing work that was independent, letting idle time accumulate while a
merge or CI run "finished."

---

## 2. True Blockers — Serialize These (One at a Time, Main Checkout Only)

Only these operations genuinely require exclusive access to the shared `master` tree:

**Merging branches into `master`**
Git merges one branch at a time. If two branches both edited the same shared registration
files (`daemon.py`, `routers/facts.py`, `db/models.py`, `db/repository.py`), the second
merge will conflict. Resolve conflicts sequentially on the main checkout.

**Running `make gate` / `make git-commit` / any push**
One `.gate-status` file, one commit chain, one remote. These are strictly serial — only one
gate run is valid at a time on the main checkout.

**Resolving conflicts in shared registration files**
`daemon.py`, `routers/facts.py`, `db/models.py`, and `db/repository.py` are touched by
nearly every feature. Conflict resolution on these files is a fan-in step: it belongs on
the main checkout, done after each merge, not inside worktrees.

**That's it.** Everything else can run concurrently.

---

## 3. False Blockers — Parallelize These (Do NOT Wait)

These are NOT blockers. Treating them as blockers is the anti-pattern to fix.

**Independent greenfield modules or features**
If two features touch different files (or are purely additive new files), they are
independent. Spin up one `isolation:"worktree"` agent per feature and run them
concurrently. Proven safe: four worktree agents ran in parallel in this repo and all
passed gate independently.

**Additive new-file work**
New Ansible modules, new roles, new molecule scenarios, new docs pages, new tests
that build on already-merged code — these do not conflict with anything. They can
fan out to worktrees at will.

**CI observation**
A CI run executes on the commit that was already pushed. Watching it does not block
local work. Local work does not block it. Never serialize behind "wait for CI." Fan out
independent implementation work while CI runs. Check CI results at the next natural
integration point.

**Reading / research / design / planning**
These never touch the working tree. Run them concurrently with everything else.

**A currently-running merge on a different branch**
A merge that has not yet landed on `master` is not a prerequisite for independent
greenfield work. The two branches will eventually fan in to `master` sequentially — that
is expected. Begin new branches before old ones merge.

---

## 4. The Decision Checklist — Apply Before Ever "Waiting"

Before pausing or serializing any work, answer all three questions:

```
a) Does this task mutate the shared master working tree RIGHT NOW?
b) Does it need the one gate / commit / push slot RIGHT NOW?
c) Does it depend on code that has NOT YET been merged to master?
```

If all answers are NO — it is NOT a blocker. Spin up a worktree agent and run it
concurrently. Do not wait. Do not ask for permission.

If any answer is YES — that specific task must wait for that specific dependency to
resolve. While it waits, fan out everything else.

---

## 5. Mechanics That Make Parallel Work Safe

These are hard-won rules from this repo's history. They are not optional.

**Fan out to worktrees; fan in on the main checkout.**
Implementation belongs in isolated worktree agents. Integration (merge, gate, commit)
belongs on the main checkout. Never merge from inside a worktree.

**The Bash shell can strand you in a worktree.**
When a worktree agent finishes, the shell's cwd may still point at the worktree path.
Read/Edit/Write tools operate on whatever path you give them — they will not warn you
that you are editing a non-master file. For any integration git operation, ALWAYS target
the main checkout explicitly:

```
make -C /Users/shawnwilson/gludd git-merge MSG='feature/my-feature'
```

Verify `make git-status` output shows the correct branch before merging.

**Worktree agents commit to their own branch and never push.**
An agent in a worktree commits, then exits. The main checkout's orchestrator pulls the
branch in and merges it. Agents do not push; they do not merge to master themselves.

**Cap concurrent worktree agents at a sane number.**
In practice, four concurrent agents have been demonstrated safe. Do not spawn a dozen;
merge overhead grows. A reasonable cap is 4–6 for this repo.

**Never let an agent spawn its own agents.**
Agents are leaf workers. Nesting agents creates untracked parallel trees that cannot be
fan-in'd cleanly. All spawning is the orchestrator's responsibility.

**Additive-only shared files: use `.gitattributes` `merge=union`.**
Branches that only ADD lines to the same shared file (e.g., import registrations,
router mounts) can be configured to merge automatically without conflicts using
`merge=union` in `.gitattributes`. Reserve manual conflict resolution for files where
lines are modified or deleted by multiple branches.

---

## 6. Anti-Pattern Log — Mistakes Not to Repeat

These are concrete failures from this repo's history. They are recorded here so they
are never repeated.

**"Serialized 4 independent feature implementations."**
Four features (dynamic dispatch, feature DB, self-improvement, docs refresh) were
implemented one at a time, waiting for each to merge before starting the next. All four
were greenfield — different files, no shared state. They could have run in parallel.
Cost: 3x wall-clock time. Correct approach: fan all four to worktrees simultaneously.

**"Waited idle for CI instead of fanning out independent greenfield work."**
A CI run was kicked off and the session sat idle until it completed. Local independent
features that did not depend on the CI commit were not started. Correct approach: once
CI is observing an already-pushed commit, spin up the next batch of worktree agents
immediately.

**"Treated an additive new-file task as blocked by an in-progress merge."**
A new Ansible role was deferred because a feature branch was mid-merge. The role
touched no overlapping files. Correct approach: start the role in a new worktree
immediately; it will be one more branch to fan in after the current merge completes.

**"Merged from inside a worktree, corrupting integration state."**
A worktree agent that should have stopped at commit instead tried to merge to master
from the worktree path. The main checkout was left behind. Correct approach: worktree
agents stop at commit; the orchestrator on the main checkout does all merging.

**"Checked CI status as a prerequisite before beginning unrelated local work."**
CI status is observable but not blocking. The question "is CI green?" only matters at
the point of the next push. It does not gate local development. Correct approach:
develop and commit locally; check CI before the next push, not before the next feature.

---

## Summary Table

| Situation | Serial or Parallel? | Who does it? |
|---|---|---|
| Merge branch → master | Serial (one at a time) | Orchestrator, main checkout |
| `make gate` / commit / push | Serial | Orchestrator, main checkout |
| Conflict resolution (shared registration files) | Serial | Orchestrator, main checkout |
| Independent greenfield feature | **Parallel** | Worktree agent (one per feature) |
| New Ansible role / molecule scenario | **Parallel** | Worktree agent |
| New docs / tests (additive) | **Parallel** | Worktree agent |
| CI observation | **Non-blocking** | Checked at next push, not before |
| Research / design / reading | **Parallel** | Any agent |
