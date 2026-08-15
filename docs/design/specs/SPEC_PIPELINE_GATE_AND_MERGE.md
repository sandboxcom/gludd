# SPEC — Pipeline (#77) Gate & Merge Closure (S3 / S4 / S5)

Status: DRAFT — implementation-ready. Author sweep: 2026-07-14.
Scope: close the three verified defects in the 3-lane pipeline (`#77`):

- **S3** — production gate is hardcoded `return True` (fake-green).
- **S4** — anti-clobber merge is mathematically unreachable (silent data loss).
- **S5** — the lanes have no production input (no producer creates a worktree).

Companion to `docs/design/STUB_CLOSURE_SPEC.md` (§S3–S5) and
`docs/CLAUDE.md` ("Feature flags that are NOT safe to turn on → `pipeline.enabled`").
Every claim below was re-verified against the code on 2026-07-14; the exact
reads are in §1 so a reviewer can re-confirm before writing a line.

---

## 0. Recommendation up front (read this first)

1. **Keep `pipeline.enabled` default-OFF and mark the feature EXPERIMENTAL in
   docs.** `PipelineConfig.enabled` already defaults to `False`
   (`pipeline/state.py:61`; asserted by
   `tests/integration/test_pipeline_controller_e2e.py:597-599`). Do not change
   the default in this work.

2. **Fix order is S4 → S3 → then the producer (S5). Do NOT wire the feed first.**
   Justification from the evidence: S5 proves the lanes have **zero production
   input today** — `CompletedUnit(` is constructed **zero times in `src/`**
   (verified §1.3), so the fake-green gate (S3) and the unreachable
   clobber-protection (S4) are *armed but never fired*. The moment a producer
   (S5) starts feeding real `CompletedUnit`s, both latent defects become live
   data-loss / false-green bugs. Therefore the two armed hazards must be
   disarmed **before** the feed is built. Within that, S4 first because its
   field addition (`CompletedUnit.base_sha`) is the plumbing that both S3's
   revert path and S5's producer must populate — designing S3 or S5 before the
   carrier exists would force a rework.

3. The blast radius of shipping S4→S3 alone is **nil in production** (nothing
   feeds the lanes), so they can land incrementally behind the OFF flag with
   only test churn. The producer (S5) is the first change that makes the feature
   observable; it must not land until S4+S3 are green.

---

## 1. Verify first (re-confirm every claim before editing)

Bash is make-only in this repo; use Read/Grep, not shell. Exact anchors:

### 1.1 S3 — fake-green gate
- Read `src/general_ludd/daemon.py:763-775` — `_gate_green()` is `return True`
  with a comment ("A stricter gate callable can be injected by an operator
  before start()") and is passed as the 4th positional to `PipelineController`
  at `daemon.py:773`.
- Read `src/general_ludd/pipeline/lanes.py:401-430` — `GateLane.step()` awaits
  `self._gate_fn()`; on `green` it increments `total_gates_green`
  (`lanes.py:412`) and logs `"GateLane: GREEN — committed snapshot covering %d
  unit(s)"` (`lanes.py:420-423`). Confirm there is **no repo write and no
  revert** in this method — the gate only clears `merged_awaiting_gate` ids.
- Read `src/general_ludd/pipeline/daemon_adapters.py:196-204` — merged file text
  is `open(path,"w")`-written to the **live repo** inside `_merge_sync` (the
  IntegrateLane), which runs *before* and *independently of* the GateLane. This
  is the ordering hazard: durable writes happen at merge time, the (fake) gate
  runs later.

### 1.2 S4 — unreachable clobber protection
- Read `src/general_ludd/pipeline/daemon_adapters.py:161-204`. The 3-way call is
  `daemon_adapters.py:184`: `result = safe_merge(repo_text, repo_text, wt_text)`
  — `repo_text` is passed as **both** `base_text` and `ours_text`.
- Read `src/general_ludd/integration/safe_merge.py:94-107`. With
  `base_text == ours_text`, `ours_changed = (ours_text != base_text)` is always
  `False` (`safe_merge.py:94`), so the function takes the
  `theirs_changed and not ours_changed → source="theirs"` branch
  (`safe_merge.py:101-102`) whenever the worktree differs, and `source="base"`
  when it does not. **`result.conflict` can never be `True`.** Therefore the
  `if result.conflict:` / "REFUSING clobber" branch at
  `daemon_adapters.py:185-193` is dead code, and a concurrent repo edit is
  silently overwritten by the worktree ("theirs").
- Read the test that blesses it: `tests/unit/test_pipeline_daemon_adapters.py:57-78`.
  `test_conflict_refuses_clobber_and_preserves` — its comment (lines 63-72)
  literally states *"with base==ours that never conflicts … Therefore: verify
  the adapter takes the worktree edit cleanly"* and asserts `outcome.merged is
  True` + that the worktree edit is taken (line 77-78). The test name promises
  the opposite of what it verifies.

### 1.3 S5 — no production input
- Grep `report_completed`: exactly one hit in `src/` — its definition at
  `src/general_ludd/pipeline/controller.py:101`. (All other hits are in tests.)
- Grep `CompletedUnit(`: **zero constructions in `src/`**; the only `src`
  references are the import at `pipeline/daemon_adapters.py:32`, the type
  annotations in `pipeline/lanes.py`, and the dataclass definition at
  `pipeline/state.py:82-94`. All constructions are in tests.
- Read `daemon.py:2124-2140` (pipeline `.start()`) and `daemon.py:2293-2301`
  (pipeline `.stop()`) — the daemon only starts/stops the controller; it never
  calls `report_completed`.
- Read the dispatch path: `pipeline/daemon_adapters.py:44-96` (`make_dispatch_fn`
  → `dispatcher.dispatch_one`) and the daemon executor
  `daemon.py:2056-2108` (`_gateway_executor`) — it `return result.content`
  (`daemon.py:2101`), a **model-response string**, and never creates a worktree.
  So nothing produces the `CompletedUnit` the IntegrateLane needs.

### 1.4 The fork-point value does not exist anywhere
- Grep `base_sha`, `base_commit`, `fork_point`, `parent_sha`, `merge_base` across
  the repo — the only hits are an unrelated SWE-bench dataset field. There is no
  recorded fork point to reuse.
- Read `src/general_ludd/git_automation/types.py:23-28` — `WorktreeInfo.commit`
  is populated by the porcelain parse at
  `src/general_ludd/git_automation/repo.py:721-722` (`HEAD ` line of
  `git worktree list --porcelain`), i.e. the worktree's **current HEAD at scan
  time**, NOT a fork point. It cannot be reused as the merge base.

### 1.5 The dispatch types are too thin to carry it
- Read `src/general_ludd/agents/types.py:42-53` (`AgentTask`) — no worktree path,
  no SHA.
- Read `src/general_ludd/agents/dispatcher.py:33-40` (`AgentTaskResult`) — fields
  are `task_id, agent_name, status, output, artifacts, duration_seconds`. No
  worktree path, no SHA.

### 1.6 Production worktree creation never goes through Python
- Read `src/general_ludd/git_automation/repo.py:625-653` (`create_worktree`) — it
  shells `git worktree add -b <branch> -- <worktree_path> HEAD` (line 640), so
  the fork point **is** the repo HEAD at creation time, but the method records no
  SHA. Grep confirms `create_worktree` has **zero `src/` callers** (all callers
  are tests).
- The real production path is the Makefile `agent-worktree` target, asserted by
  `tests/unit/test_agent_worktree_targets.py:63-84`: the recipe must contain
  `git worktree add`, print `WORKTREE_PATH=<path>`, place worktrees under
  `/tmp/gludd-worktrees/`, and take `$(BRANCH)` — it records **no base SHA**. So
  whichever path the producer adopts must be *taught* to capture the base SHA;
  there is no existing carrier for it.
- There are in fact **two independent, both-partly-dead** Python worktree-add
  code paths, neither wired into production: (a) `GitAutomation.create_worktree`
  (`repo.py:625`) shells `subprocess.run(["git","worktree","add",…])` inline
  (`repo.py:637-645`); (b) `build_worktree_add_argv` (`worktree/core.py:92`)
  builds an argv but has **zero `src/` callers** (all callers are
  `tests/unit/test_worktree_core_hardening.py`). `create_worktree` does not call
  `build_worktree_add_argv`. Pick (a) for the producer (§4.3) — it is the one
  that actually runs `git`.
- A **stale design doc already anticipates this field**:
  `docs/design/pipeline_controller.md:235` lists `base_commit: str  # ancestor
  for the 3-way merge` — described as planned but never implemented. Treat that
  doc as drift (per `docs/CLAUDE.md`): the field does not exist in code; this
  spec is the implementation of record.

### 1.7 A real gate exists and can be wired (S3)
- Read `src/general_ludd/quality/project_gate.py:35-208` — `run_project_gate(
  workspace, checks=("lint","test"), *, required=None, timeout_s=None,
  profile=None) -> dict` runs each declared check via `ProjectCommandRunner` and
  returns a report whose top-level `"passed": bool` is the gate verdict
  (`project_gate.py:197-208`); fail-closed on missing `project.yml`
  (`:74-87`) and on undeclared required checks (`:113-130, :168-192`).
- Read `src/general_ludd/quality/gate.py` `QualityGateChecker.enforce` — note the
  S20 fail-open default `all(g.get("passed", True) …)` at `gate.py:79`; do **not**
  route the pipeline through `enforce()` until S20's default is flipped to
  `False`. Prefer `run_project_gate`, whose verdict is already fail-closed.
- Read `src/general_ludd/git_automation/repo.py:873-952` (`gated_merge`) for the
  **proven rollback primitive** this spec reuses: it captures `pre_sha`
  (`repo.py:890`), applies the merge, runs the gate command, and on
  failure/timeout does `git reset --hard <pre_sha>` (`repo.py:911, 938, 943`) —
  fail-closed, "a failed gate leaves target exactly at pre_sha".

---

## 2. S4 fix — make the anti-clobber merge reachable

### 2.1 Schema change: `CompletedUnit.base_sha`

`src/general_ludd/pipeline/state.py:82-94`, before:

```python
@dataclass
class CompletedUnit:
    unit_id: str
    worktree_path: str
    branch: str | None = None
```

After:

```python
@dataclass
class CompletedUnit:
    unit_id: str
    worktree_path: str
    branch: str | None = None
    # Fork-point commit the worktree was branched from (the merge BASE).
    # Recorded by the producer at `git worktree add … HEAD` time (§4). An
    # EMPTY value means the fork point is unknown → the merge FAILS CLOSED
    # (refused) rather than clobbering, so a producer that forgets to record
    # it cannot silently re-introduce the S4 data-loss bug.
    base_sha: str = ""
```

Rationale for `default=""` (not required-positional): 33 existing test
constructions omit it; a required field would break collection before the tests
can be rewritten. The **fail-closed-on-empty** rule in §2.3 is what makes the
default safe — an empty `base_sha` refuses the merge, it does not fall back to
the broken `base==ours` behavior.

### 2.2 New helper: materialise base content from the fork point

Add to `src/general_ludd/pipeline/daemon_adapters.py` (near `_read`, ~line 99):

```python
def _git_show(repo_path: str, base_sha: str, relpath: str) -> str | None:
    """Return the content of ``relpath`` at commit ``base_sha`` (the fork point).

    Runs ``git show <base_sha>:<relpath>`` under the repo. Returns None when the
    path did not exist at the fork point (a NEW file added on the worktree
    branch) or on any git error — callers distinguish "no base" (new file, safe
    to take) from "base differs" (potential clobber).
    """
    import subprocess

    if not base_sha:
        return None
    try:
        out = subprocess.run(
            ["git", "-C", repo_path, "show", f"{base_sha}:{relpath}"],
            capture_output=True, text=True, timeout=15, check=False,
        )
    except Exception:  # pragma: no cover - git absent / odd repo
        return None
    if out.returncode != 0:
        return None
    return out.stdout
```

### 2.3 Corrected merge call

`src/general_ludd/pipeline/daemon_adapters.py:161-204`, the per-file loop.
Before (`:171-194`):

```python
        with git_repo_lock(repo_path):
            for rel in files:
                repo_file = os.path.join(repo_path, rel)
                wt_file = os.path.join(unit.worktree_path, rel)
                repo_text = _read(repo_file)
                wt_text = _read(wt_file)
                if wt_text is None:
                    continue
                if repo_text is None:
                    merged_texts[repo_file] = wt_text
                    continue
                # base == repo (current repo state). theirs == worktree.
                result = safe_merge(repo_text, repo_text, wt_text)
                if result.conflict:
                    logger.warning(
                        "pipeline merge: REFUSING clobber on %s for unit %s",
                        rel, unit.unit_id,
                    )
                    return MergeOutcome(
                        unit_id=unit.unit_id, merged=False, clobber_refused=True,
                        detail=f"conflict:{rel}",
                    )
                merged_texts[repo_file] = result.text
```

After:

```python
        with git_repo_lock(repo_path):
            for rel in files:
                repo_file = os.path.join(repo_path, rel)
                wt_file = os.path.join(unit.worktree_path, rel)
                repo_text = _read(repo_file)
                wt_text = _read(wt_file)
                if wt_text is None:
                    # Worktree file vanished — nothing to merge.
                    continue

                # FAIL CLOSED when the fork point is unknown: without a base we
                # cannot tell a divergent repo edit from an intended change, so
                # refuse rather than clobber (this is the S4 bug's root: base
                # must NOT be repo_text).
                if not unit.base_sha:
                    logger.warning(
                        "pipeline merge: no base_sha for unit %s — REFUSING "
                        "(cannot establish fork point)", unit.unit_id,
                    )
                    return MergeOutcome(
                        unit_id=unit.unit_id, merged=False, clobber_refused=True,
                        detail="no_base_sha",
                    )

                base_text = _git_show(repo_path, unit.base_sha, rel)
                if base_text is None:
                    # File did not exist at the fork point → new file on the
                    # branch. Only safe to take if the repo also lacks it;
                    # otherwise two independent adds → conflict, refuse.
                    if repo_text is None:
                        merged_texts[repo_file] = wt_text
                        continue
                    if repo_text == wt_text:
                        merged_texts[repo_file] = wt_text
                        continue
                    logger.warning(
                        "pipeline merge: REFUSING clobber on new-file %s for "
                        "unit %s (both repo and worktree added it)",
                        rel, unit.unit_id,
                    )
                    return MergeOutcome(
                        unit_id=unit.unit_id, merged=False, clobber_refused=True,
                        detail=f"conflict:{rel}",
                    )

                # If the repo file was deleted since the fork point, treat repo
                # side as the base's absence: refuse a modify/delete race.
                repo_side = repo_text if repo_text is not None else base_text
                # base = fork point, ours = current repo, theirs = worktree.
                result = safe_merge(base_text, repo_side, wt_text)
                if result.conflict:
                    logger.warning(
                        "pipeline merge: REFUSING clobber on %s for unit %s",
                        rel, unit.unit_id,
                    )
                    return MergeOutcome(
                        unit_id=unit.unit_id, merged=False, clobber_refused=True,
                        detail=f"conflict:{rel}",
                    )
                merged_texts[repo_file] = result.text
```

Key correctness points, mapped to `safe_merge` semantics
(`integration/safe_merge.py:94-107`):
- `base = fork-point content`, `ours = current repo content`,
  `theirs = worktree content`. Now `ours_changed` is `True` **iff the repo moved
  since the fork point** — exactly the concurrent-edit case S4 must detect.
- Worktree-only change → `source="theirs"` (clean, intended).
- Repo-only change → `source="ours"` (the worktree didn't touch it; keep repo).
- Both changed the same region differently → `conflict=True` → **refused**
  (`clobber_refused=True`). This branch is now reachable.
- Update the docstring at `daemon_adapters.py:113-129` (it currently documents
  the wrong "BASE for the merge is the repo's current content" behavior).

---

## 3. S3 fix — wire a real gate + close the ordering hazard

### 3.1 The ordering hazard, precisely

Today the durable repo write happens in the **IntegrateLane** merge adapter
(`daemon_adapters.py:196-200`, `open(path,"w")`), and the **GateLane** runs later
on a debounced, coalesced snapshot of merged unit-ids (`lanes.py:385-430`) with
**no revert path**. So even with a real gate, a RED verdict leaves already-merged
files in the live repo. The gate must be able to *undo* the writes its snapshot
covers.

### 3.2 Chosen solution: revert path via commit boundaries (option b), not per-merge pre-gate (option a)

**Recommendation: (b) add a revert path to the gate, implemented by making each
merge a git commit and resetting `--hard` to the last-green SHA on RED.**

Reasoning for (b) over (a):
- The debounced, single-flight, coalescing gate ("a burst of merges coalesces
  into one gate run", `lanes.py:349-358`) is a **core design goal** — the gate is
  expensive (`run_project_gate` shells lint+test). Option (a) — run the gate
  synchronously inside each merge *before* writing — destroys coalescing and runs
  the gate once per unit (N× the cost) and serialises merge behind gate.
- Option (b) reuses an **already-proven, tested rollback primitive**:
  `GitAutomation.gated_merge` captures `pre_sha` and `git reset --hard <pre_sha>`
  on gate failure (`repo.py:890, 911, 938, 943`). We mirror exactly that.
- Making each merged unit a commit also gives the pipeline real provenance (one
  commit per unit) instead of an uncommitted working-tree smear that is
  impossible to attribute or revert selectively.

### 3.3 Merge lane: commit each unit, record the SHA

In `_merge_sync` (`daemon_adapters.py:196-204`), after the clean writes, stage
and commit inside the same `git_repo_lock`, and capture the resulting HEAD so the
gate can bound its revert. Before:

```python
            # All files merged cleanly — commit the writes inside the lock.
            for path, text in merged_texts.items():
                os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
                with open(path, "w", encoding="utf-8", newline="") as fh:
                    fh.write(text)

        do_reclaim(unit.worktree_path)
        return MergeOutcome(unit_id=unit.unit_id, merged=True, detail="merged")
```

After:

```python
            for path, text in merged_texts.items():
                os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
                with open(path, "w", encoding="utf-8", newline="") as fh:
                    fh.write(text)
            # Commit this unit so the gate can revert it by SHA on RED (mirrors
            # GitAutomation.gated_merge rollback). Best-effort: a repo without a
            # committer identity still merges, it just isn't independently
            # revertable — logged, not fatal.
            commit_sha = _commit_all(repo_path, f"pipeline: merge unit {unit.unit_id}")

        do_reclaim(unit.worktree_path)
        return MergeOutcome(
            unit_id=unit.unit_id, merged=True, detail="merged",
            commit_sha=commit_sha,
        )
```

Add `_commit_all(repo_path, message) -> str | None` (git `add -A` + `commit -m`
+ `rev-parse HEAD`, all `check=False`, returns the new SHA or None). Add
`commit_sha: str | None = None` to `MergeOutcome`
(`pipeline/state.py:97-114`) and carry it onto `merged_awaiting_gate`.

### 3.4 State change: `merged_awaiting_gate` carries units, not bare ids

Today `LaneState.merged_awaiting_gate: list[str]` (`state.py:141`) holds bare
unit-ids, so the gate cannot know which commit to reset to. Change it to hold a
small record `(unit_id, commit_sha)` (a frozen `GatedUnit` dataclass, or reuse
`CompletedUnit` extended with `commit_sha`). This is the second half of the S5
test churn note ("`merged_awaiting_gate` carries units instead of bare ids").
`IntegrateLane.step()` (`lanes.py:296-305`) appends the record; `status()`
(`controller.py:192`) and the GateLane snapshot (`lanes.py:396`) read `.unit_id`.

### 3.5 Gate lane: capture pre-gate HEAD, reset on RED

In `GateLane.step()` (`lanes.py:391-430`):
- At snapshot time (`lanes.py:396-399`), also capture `self._pre_gate_sha` =
  `git rev-parse HEAD` (via an injected `head_provider()` callable so the lane
  stays unit-testable, same pattern as the injected `clock`).
- On GREEN (`lanes.py:411-423`): unchanged (commits stay).
- On RED (`lanes.py:424-429`): call an injected `revert_fn(pre_gate_sha)` that
  does `git reset --hard <pre_gate_sha>` under `git_repo_lock`, dropping every
  commit the failed snapshot covered, and requeue the covered units for a later
  re-merge (or route to a human todo after N failures, mirroring the clobber
  retry cap at `lanes.py:308-325`). Update the log line at `lanes.py:425-429`
  from "will re-gate after debounce" to reflect the revert.

The `revert_fn` / `head_provider` are new injected callables on `GateLane`
(constructor `lanes.py:360-375`), wired by the daemon adapter (§3.6). Keep them
optional (default no-op) so existing lane unit tests that inject a fake `gate_fn`
still pass without a repo.

### 3.6 Wire the real gate in the daemon

`src/general_ludd/daemon.py:763-775`. Before:

```python
    async def _gate_green() -> bool:
        # Conservative default: the in-process pipeline does not run the full
        # ~16-min suite on the event loop. A stricter gate callable can be
        # injected by an operator before start(); the lane treats True as green.
        return True

    return PipelineController(
        cfg,
        make_dispatch_fn(dispatcher),
        make_merge_fn(repo_path),
        _gate_green,
        disk_ok=make_disk_ok(repo_path),
    )
```

After:

```python
    from general_ludd.quality.project_gate import run_project_gate

    gate_checks = tuple(getattr(pipeline_cfg, "gate_checks", ("lint", "test")))

    async def _real_gate() -> bool:
        # Run the target project's declared gate OFF the event loop (it shells
        # lint/test). Fail-closed: any error or a missing project.yml yields a
        # report with passed=False (project_gate.py:74-87).
        report = await asyncio.to_thread(
            run_project_gate, repo_path, gate_checks,
        )
        green = bool(report.get("passed", False))
        if not green:
            logger.warning(
                "pipeline gate RED: %s", report.get("overall", "FAIL"),
            )
        return green

    return PipelineController(
        cfg,
        make_dispatch_fn(dispatcher),
        make_merge_fn(repo_path),
        _real_gate,
        disk_ok=make_disk_ok(repo_path),
    )
```

Plus thread the `head_provider` + `revert_fn` (git `rev-parse` / `reset --hard`
under `git_repo_lock(repo_path)`) into the `GateLane` via the controller
constructor. Add `gate_checks` and (optional) `gate_debounce_s` to the
`pipeline` config block in `config/user_config.py` and `docs/CONFIG_REFERENCE.md`.

> NOTE: `run_project_gate` requires a `project.yml` in `repo_path`. For the
> gludd repo itself the self-hosting gate lives in `quality/preflight.py`
> (hardcoded REPO_ROOT + `uv run ruff`/`mypy`/coverage). The daemon merges into
> its own cwd, so the injected gate must match the target: if `project.yml` is
> absent, `run_project_gate` fails closed (correct — better RED than fake
> GREEN). Document that operating the pipeline over gludd-on-gludd requires a
> `project.yml` or a `preflight`-backed gate callable.

---

## 4. S5 — the missing worktree-creating producer

### 4.1 What it must record

For each dispatched unit the producer must, **at worktree-creation time**,
record two values that no current carrier holds (§1.5/§1.6):

- `worktree_path` — the absolute path `git worktree add` created.
- `base_sha` — the repo HEAD **captured immediately before** `git worktree add …
  HEAD`, i.e. the fork point. This is the `CompletedUnit.base_sha` of §2.

### 4.2 Which carrier

Two options; **recommend the out-of-band producer hold (option ii)**:

- **(i) Extend `AgentTaskResult`** with `worktree_path` + `base_sha`
  (`dispatcher.py:33-40`) and have the executor create the worktree. Rejected:
  the executor is `_gateway_executor` (`daemon.py:2056`), which returns a plain
  `str` (`ExecutorFn = Callable[[AgentTask], Coroutine[..., str]]`,
  `dispatcher.py:43`); widening the executor contract to return a worktree
  touches every executor and the noop path. It also conflates "run a model call"
  with "own a git worktree".

- **(ii) A dedicated `PipelineProducer`** that owns the worktree lifecycle
  out-of-band and calls `controller.report_completed`. **Recommended.** Shape:

  ```python
  class PipelineProducer:
      def __init__(self, controller, dispatcher, repo_path, git=None):
          self._git = git or GitAutomation(repo_path)
          ...

      async def run_unit(self, unit_id: str) -> None:
          # 1. Capture the fork point BEFORE creating the worktree.
          base_sha = self._git.get_current_commit()          # repo.py:465
          wt_path = f"/tmp/gludd-worktrees/{unit_id}"
          branch = self._git.generate_branch_name(unit_id, "pipeline")  # repo.py:1028
          res = self._git.create_worktree(self._repo, branch, wt_path)  # repo.py:625
          if not res.success:
              ...  # roll back to pending, log
              return
          # 2. Dispatch the agent INTO the worktree (cwd = wt_path).
          await self._dispatcher.dispatch_one(self._build_task(unit_id, wt_path))
          # 3. On completion, feed the lane with the recorded provenance.
          await self._controller.report_completed(
              CompletedUnit(unit_id=unit_id, worktree_path=wt_path,
                            branch=branch, base_sha=base_sha),
          )
  ```

  This replaces / wraps the current `make_dispatch_fn` so `DispatchLane` drives
  `PipelineProducer.run_unit` instead of a bare model call. The producer is the
  single place that both creates the worktree and records `base_sha`, so §2's
  fail-closed-on-empty rule is always satisfied on the real path.

### 4.3 Why the dead Python worktree API forces this

`GitAutomation.create_worktree` (`repo.py:625-653`) has **zero `src/` callers**
and the real production worktree path is the Makefile `agent-worktree` target,
which records no base SHA (§1.6). So the producer cannot "just call the existing
function and get a base_sha for free" — whichever path it adopts must be taught
to capture HEAD. Recommendation: adopt the Python `create_worktree` (it already
exists, is tested, and enforces path-traversal safety at `repo.py:655-684`), and
capture `get_current_commit()` in the producer immediately before the call. If
the team prefers the Makefile path for parity with manual worktrees, the target
must be extended to print `BASE_SHA=<sha>` alongside `WORKTREE_PATH=` and the
producer must parse it — but the Python path is simpler and already unit-tested.

> The agent MUST run with `cwd=wt_path` (or `GIT_WORK_TREE`) so its edits land in
> the worktree, not the shared checkout. Note the known trap
> (`docs/CLAUDE.md`): `git_repo_lock` is a **no-op inside a worktree** (`.git` is
> a file there), so cross-process serialisation of the *worktree's own* commits
> is absent — the merge back into the main repo is what must hold the lock, and
> `make_merge_fn` already wraps the merge in `git_repo_lock(repo_path)` on the
> **main** repo (`daemon_adapters.py:170`), which is correct.

---

## 5. Tests that enshrine the broken behavior (must be rewritten)

Every construction below omits `base_sha`; once §2 lands they must pass a real
fork-point SHA (or the merge fails closed) and, where they assert merge outcomes,
must be re-pointed at the corrected semantics.

### 5.1 The bug-blessing test — invert it
- `tests/unit/test_pipeline_daemon_adapters.py:57-78`
  `test_conflict_refuses_clobber_and_preserves` — **rewrite completely**. Delete
  the "base==ours never conflicts" comment (lines 63-72). New body: set up a
  real fork point (a base commit), a divergent repo edit AND a divergent
  worktree edit on the same region, pass `CompletedUnit(..., base_sha=<fork>)`,
  and assert `outcome.merged is False and outcome.clobber_refused is True` and
  that the repo file is **unchanged** (not clobbered). See §6.
- Same file, the other `CompletedUnit(` sites that assert clean merges must gain
  a `base_sha`: `:52` (`test`… one-sided change — still merges), `:76`, `:91`
  (new file), `:104` (empty changeset). For the one-sided and new-file cases the
  base is the pre-edit content / absent, so they stay `merged=True` but now via
  the corrected 3-way, not the `base==ours` shortcut.

### 5.2 State / structural constructors (add `base_sha`)
- `tests/unit/test_pipeline_state.py:56` and `:60` (`TestCompletedUnit`).
- `tests/unit/test_pipeline_state_structural.py:63, :69, :143, :148, :153`
  (`TestCompletedUnit` + the `worktree_count` / `snapshot_heartbeat` builders).
  Also update `TestMergeOutcome` (`:73-89`) once `MergeOutcome.commit_sha`
  (§3.3) is added, and `test_default_state` (`:119-132`) if `merged_awaiting_gate`
  changes element type (§3.4).

### 5.3 Controller tests
- `tests/unit/test_pipeline_controller.py:93-101` (`test_report_completed_enqueues_unit`,
  constructs `CompletedUnit` at `:98`).
- `tests/unit/test_pipeline_controller.py:170-186`
  (`test_status_with_awaiting_merge`, constructs at `:174-175`).
- `tests/unit/test_pipeline_lanes.py` — constructs `CompletedUnit` at `:130,
  :384, :399, :419, :439, :637, :638` (and drives `report_completed` at `:637,
  :638`); all need `base_sha=`. The `TestGateLane` cases (class at `:466`) inject
  a bare boolean `gate_fn` and assert `total_gates_green` directly (`:508` ==1,
  `:523` ==0) — these must additionally cover the new RED-revert path (§3.5): add
  a fake `revert_fn` spy and assert it is called on a RED verdict and NOT on
  GREEN. The `TestIntegrateLane` cases (`:381`) use inline `MergeOutcome`-
  returning fakes, so they are unaffected by the `safe_merge` fix but must gain
  `MergeOutcome.commit_sha` awareness once §3.3 lands.

### 5.4 The "e2e" tests that validate nothing (TWO files)
- `tests/integration/test_pipeline_controller_e2e.py` — drives the controller
  with **in-process fake closures** (`dispatch`/`merge`/`gate` at `:70-89`,
  `:143-159`, etc.) and hand-built `CompletedUnit`s; it never exercises
  `make_dispatch_fn`, `make_merge_fn`, `run_project_gate`, or a real worktree, so
  it validates **nothing about the real daemon path**. `report_completed` sites:
  `:167, :168, :237, :274, :277, :278, :358, :384, :385, :525, :526, :579-580`;
  `CompletedUnit` constructions also at the list-comp `:412`. `gate_fn` is always
  an inline `return True` (or a `[False, True]` sequence at `:219`); `merge_fn`
  always returns `MergeOutcome(..., merged=True)` — even the "clobber" test
  (`:550`) uses a hand-rolled refuse-then-accept fake (`:557-566`), NOT
  `safe_merge`.
- `tests/e2e/test_pipeline_controller_e2e.py` — a **second, distinct** e2e file
  (same defect shape): `gate_fn` always `return True`, `merge_fn` always returns
  `merged=True`; never wires the real adapters. Classes `TestPipelineConfigGating`
  (`:48`), `TestPipelineControllerLifecycle` (`:123`), `TestLanesE2E` (`:220`).
  Update its `CompletedUnit`/`MergeOutcome` type-annotated fakes as needed.
- Required changes for both: (a) add `base_sha=` to every construction; (b) add a
  **new** true end-to-end test (§6.5) that wires the real adapters over a scratch
  git repo so the "e2e" name is finally earned. Consider consolidating the two
  redundant fake-driven e2e files into one during this work.

---

## 6. New tests proving a real conflict is now DETECTED and refused

### 6.1 Unit — reachable clobber refusal (`test_pipeline_daemon_adapters.py`)
```python
def make base commit B: f.txt = "a\nb\nc\n"; commit → base_sha
repo working tree: edit line2 → "a\nREPO\nc\n"        (ours diverged)
worktree tree:     edit line2 → "a\nWORKTREE\nc\n"    (theirs diverged, same region)
outcome = await make_merge_fn(repo, changed_files=lambda u:["f.txt"])(
    CompletedUnit("u1", wt, base_sha=base_sha))
assert outcome.merged is False
assert outcome.clobber_refused is True
assert (repo/"f.txt").read_text() == "a\nREPO\nc\n"   # NOT clobbered
```

### 6.2 Unit — disjoint edits still merge cleanly (regression guard)
Base `l1\nl2\nl3\n`; repo edits l1, worktree edits l3 → `safe_merge` returns a
clean 3-way (`source="merged"`), `outcome.merged is True`, and the repo file
contains **both** edits. Proves the fix does not over-refuse.

### 6.3 Unit — fail-closed on empty `base_sha`
`CompletedUnit("u1", wt)` (no `base_sha`) with a differing worktree file →
`outcome.clobber_refused is True`, `outcome.detail == "no_base_sha"`, repo file
unchanged. Proves a forgetful producer cannot re-introduce S4.

### 6.4 Gate — RED reverts the merged commits
Wire `GateLane` (or the controller) over a scratch git repo with a real
`revert_fn`. Merge one unit (commit lands), force the gate `red`, assert the repo
HEAD is reset to the pre-gate SHA and the working tree matches pre-merge content.
Proves S3's ordering hazard is closed.

### 6.5 True e2e (`tests/integration`)
Init a scratch git repo + a trivial `project.yml` whose `lint`/`test` commands
are controllable (e.g. `sh -c 'exit 0'` vs `exit 1`). Drive the **real** adapters
(`make_dispatch_fn` via a stub dispatcher that writes a file in a real worktree,
`make_merge_fn`, `run_project_gate`) through `report_completed`. Assert: green
path commits the file; red path leaves the repo at the pre-merge SHA; a
concurrent repo edit on the same line is refused.

---

## 7. Landing order, risk, rollback

### 7.1 Order
1. **S4a** — add `CompletedUnit.base_sha` + `_git_show` + corrected `safe_merge`
   call + fail-closed-on-empty (§2). Rewrite `test_pipeline_daemon_adapters.py`
   (§5.1) and add §6.1–6.3. No production behavior change (nothing feeds lanes).
2. **S3a** — `MergeOutcome.commit_sha` + `_commit_all` + `merged_awaiting_gate`
   carries units (§3.3-3.4); wire `run_project_gate` + `head_provider`/`revert_fn`
   (§3.5-3.6). Update state/controller/lane tests (§5.2-5.3) and add §6.4.
3. **S5** — `PipelineProducer` (§4) and DispatchLane wiring; rewrite the e2e
   test (§5.4) and add §6.5. **Only now is the feature observable.** Flag stays
   OFF by default; flip to ON only behind an explicit operator opt-in with a
   `project.yml`-backed gate.

Steps 1–2 land behind the OFF flag with test-only churn; step 3 is the first
change a user could enable.

### 7.2 Risk
- **False refusals** (over-conservative merge) — mitigated by §6.2. Acceptable
  posture: a refused merge preserves the worktree (`lanes.py:306-318`), so work
  is never lost; a wrong clobber is unrecoverable, so bias to refuse.
- **Gate cost / event-loop stall** — `run_project_gate` shells lint+test;
  always via `asyncio.to_thread` (§3.6) and only in the already-off-lock GateLane.
  The debounce (`gate_debounce_s`, default 30s) bounds frequency.
- **Commit identity** — `_commit_all` needs a git user; best-effort (logs, not
  fatal) so a mis-configured repo degrades to "not independently revertable"
  rather than crashing the lane.
- **Worktree lock no-op** (`docs/CLAUDE.md` trap) — the *merge-back* holds
  `git_repo_lock` on the main repo (`daemon_adapters.py:170`), which is the path
  that matters; documented in §4.3.

### 7.3 Rollback
Every change is gated by `pipeline.enabled` (default OFF), so the operational
rollback is "leave the flag off" — identical to today's safe state. Code
rollback is a straight revert of the three commits; the added
`CompletedUnit.base_sha` / `MergeOutcome.commit_sha` fields are defaulted, so
reverting the producer (S5) alone leaves S3/S4 intact and harmless. If the real
gate proves too slow in the field, revert only §3.6's `_real_gate` wiring back to
a conservative injected callable **without** reverting the S4 merge fix — the two
are independent.

---

## 8. Verification checklist (evidence required before marking done)

- [ ] `safe_merge` is called with three DISTINCT arguments at
      `daemon_adapters.py` (base=fork point, ours=repo, theirs=worktree).
      | evidence: read the edited line.
- [ ] `test_conflict_refuses_clobber_and_preserves` now asserts
      `clobber_refused is True` + repo unchanged. | evidence: `make test-iso
      TESTFILE=tests/unit/test_pipeline_daemon_adapters.py::…::test_conflict_refuses_clobber_and_preserves`.
- [ ] New §6.1/§6.3 tests pass. | evidence: test count.
- [ ] `daemon.py` no longer contains `async def _gate_green(): return True`;
      the gate is `run_project_gate`-backed. | evidence: read the edited block.
- [ ] GateLane reverts on RED (§6.4 passes). | evidence: test output.
- [ ] `pipeline.enabled` default is still `False`. | evidence:
      `test_default_config_is_disabled` green + `state.py:61`.
- [ ] `make gate-async` green on the exact SHA before any release note claims
      the pipeline is fixed. | evidence: `.gate-status` + run id.
```text
```
