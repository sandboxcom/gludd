# SPEC — Security Wave 1

Status: DRAFT / implementation-ready.
Audience: implementing engineer for the next release.
Scope: six defect clusters found by an authorized internal audit of this
repository. Every file:line claim below was **re-verified against the working
tree** at spec-authoring time (branch `development`). Items that could not be
re-confirmed from source are labelled **UNVERIFIED** and must be verified by the
implementer before any code is written — do not assume a prior ledger entry is
still true (several ledger items in this repo were later found FALSE).

---

## 0. Conventions

**Severity**: CRITICAL (remote priv-esc / auth bypass / RCE-adjacent) · HIGH
(secret disclosure / integrity bypass) · MEDIUM (DoS / correctness under
concurrency) · LOW (hardening).

**Effort**: S (≤ half day, one file) · M (1–2 days, a few files + tests) ·
L (multi-file, new subsystem or migration).

**Test rule**: every named test below MUST FAIL on the current tree (it asserts
the *secure* post-fix behavior, which does not hold today) and PASS after the
fix. Where a test file is marked `(new)` it does not exist yet. Bash in this
repo is `make`-only; run targeted tests with
`make test-iso TESTFILE='path::Class::test'`.

**Do NOT weaken to pass.** "Fix" means the control works, never that a check is
removed or a feature stubbed.

---

## 1. Landing order (respects file overlap)

Items 2 and 3 both touch `git_automation/` (`locking.py` + `repo.py`) and MUST
land as one PR to avoid a merge conflict on `repo.py`. Everything else is
file-disjoint and can land in parallel worktrees.

| Order | Item | Files (primary) | Rationale |
|------|------|-----------------|-----------|
| 1 | **Item 1** HMAC forgery | `integrity/scanner.py` (+ `routers/self_improve.py`) | Self-contained; highest integrity impact; no overlap. |
| 2 | **Items 2+3** git lock + git gaps | `git_automation/locking.py`, `git_automation/repo.py` | Same files — one PR. Item 2 (common-dir resolution) is a prerequisite for locking the newly-locked call sites in Item 3. |
| 3 | **Item 4** env leaks | `routers/stream.py`, `sandbox_exec/executor.py`, `renderers/runner.py`, `collections/.../module_utils/gludd.py` | Disjoint from 1–3. |
| 4 | **Item 5** per-capability authz | `security/`, `agents/registry.py`, routers, + DB migration | Largest; new middleware + ownership columns. Do last so it can build on a clean tree. |
| 5 | **Item 6** protected-path / self-improve / SSTI | `self_update/apply.py`, `execution/engine.py`, `routers/self_improve.py` | Overlaps `self_improve.py` with Item 1 — rebase Item 6's `self_improve.py` hunk after Item 1 lands. |

---

## 2. ITEM 1 — HMAC integrity signatures are forgeable WITHOUT the key

**Severity: HIGH. Effort: M.** Files: `src/general_ludd/integrity/scanner.py`
(all four sign/verify functions), caller `src/general_ludd/routers/self_improve.py:258`.

### 2.1 Verified facts (re-confirmed against source)

- `sign_change` (scanner.py:601-611): payload built as
  `"|".join([change.file_path, change.change_type, str(old_hash), str(new_hash), change.detected_at])`
  (lines 602-603). Unescaped join.
- `verify_signature` (scanner.py:614-636): same unescaped `"|".join(...)` of the
  same five fields (615-622). Uses `hmac.compare_digest` (good — keep).
- `sign_change_openbao` (scanner.py:645-683): payload
  `"|".join([path, signer, reason, str(old_hash), str(new_hash), ts])` (line 661).
  `signer` and `reason` are free text.
- `verify_openbao_signature` (scanner.py:686-711): mirrors 692-700, same
  unescaped join. Uses `hmac.compare_digest` (keep).
- Caller taint: `routers/self_improve.py:258` sets
  `change_type = str(spec.get("kind", "config"))` — a plain string pulled from
  the approval's `plan_artifact` JSON, **no allowlist / no delimiter rejection**
  (confirmed; `reason` at :255 is likewise free text).
- The anti-rollback high-water-mark machinery (`_hwm_mac`, `_write_mac_and_hwm`,
  `_read_hwm_value`, scanner.py:144-217) is a **separate, independent** HMAC
  scheme (`f"hwm:{counter}"` and `f"{counter}|{serialized}"`) and MUST be left
  untouched by this fix. `_get_integrity_key` / `IntegrityKeyError` are the key
  provider — unchanged.

### 2.2 Exploit walkthrough (delimiter-collision forgery)

The signed payload is the **concatenation of attacker-influenced fields with a
`|` separator that also occurs inside the fields**. Because the fields are not
escaped and not length-prefixed, two *different* field tuples serialize to the
*byte-identical* payload, so one valid signature verifies for both.

Concrete `sign_change` / `verify_signature` collision (five fields
`file_path | change_type | old_hash | new_hash | detected_at`):

```
Record A (legitimately signed):
    file_path   = "config"
    change_type = "app|modified"        # attacker-supplied via spec["kind"]
    old_hash="", new_hash="", detected_at="T"
  payload_A = "config|app|modified|||T"

Record B (forged — NOT signed, but verifies):
    file_path   = "config|app"          # a DIFFERENT file
    change_type = "modified"
    old_hash="", new_hash="", detected_at="T"
  payload_B = "config|app|modified|||T"     ==  payload_A
```

The signature minted for A (an approval the attacker legitimately obtained for
some innocuous `change_type`) is a valid signature for B, which names a
different `file_path`. Because `change_type` is caller-controlled free text
(self_improve.py:258), the attacker chooses the `|`-bearing value that shifts a
boundary. **No knowledge of `GL_INTEGRITY_KEY` is required.**

The OpenBao variant is strictly worse: `signer` and `reason` are *both* free
text inside the same delimiter-less join, so an attacker can slide bytes across
the `path | signer | reason` boundaries to **reassign who approved a change and
why** while keeping a signature valid — e.g. present `path="x", signer="root",
reason="ok"` as `path="x|root|ok..."`. Approval provenance is forgeable.

### 2.3 Fix — canonical JSON payload + scheme tags

Replace every `"|".join(...)` payload with a **canonical JSON serialization**
(sorted keys, no whitespace, explicit field names) prefixed with a **distinct
scheme tag** per function. The tag also prevents cross-scheme replay (an
`sign_change` signature can never validate under `verify_openbao_signature`).
Canonical JSON is unambiguous: field boundaries are the JSON structure, not an
in-band delimiter, so no field value can shift a boundary.

Add near the top of `scanner.py` (module scope):

```python
import json

_SCHEME_LOCAL = "gl-integrity-v1"
_SCHEME_OPENBAO = "gl-integrity-openbao-v1"


def _canonical_payload(scheme: str, fields: dict[str, object]) -> bytes:
    """Unambiguous signed-payload encoding.

    A leading scheme tag domain-separates the two signature families (so a
    signature minted by one can never verify under the other), and
    canonical JSON (sorted keys, no whitespace, ``ensure_ascii=False`` with a
    UTF-8 encode) makes field boundaries structural — a ``|`` (or any byte)
    inside a value can no longer collide two distinct field tuples onto one
    payload the way the old ``"|".join(...)`` did.
    """
    body = json.dumps(
        {"scheme": scheme, **{k: ("" if v is None else str(v)) for k, v in fields.items()}},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return body.encode("utf-8")
```

**`sign_change`** — before (602-605):

```python
    parts = [change.file_path, change.change_type, str(change.old_hash), str(change.new_hash), change.detected_at]
    payload = "|".join(parts)
    key = _get_integrity_key()
    sig = hmac.new(key.encode(), payload.encode(), hashlib.sha256).hexdigest()
```

after:

```python
    payload = _canonical_payload(_SCHEME_LOCAL, {
        "file_path": change.file_path,
        "change_type": change.change_type,
        "old_hash": change.old_hash,
        "new_hash": change.new_hash,
        "detected_at": change.detected_at,
    })
    key = _get_integrity_key()
    sig = hmac.new(key.encode(), payload, hashlib.sha256).hexdigest()
```

**`verify_signature`** — replace the `parts`/`"|".join` block (615-622) and the
`expected = hmac.new(...)` line (630) with the same
`_canonical_payload(_SCHEME_LOCAL, {...})` over `signed.get(...)` values; keep
the `IntegrityKeyError -> return False` guard (626-629) and the
`hmac.compare_digest` compare (636) exactly.

**`sign_change_openbao`** — before (661-663):

```python
    payload = "|".join([path, signer, reason, str(old_hash), str(new_hash), ts])
    key = _get_integrity_key()
    sig = hmac.new(key.encode(), payload.encode(), hashlib.sha256).hexdigest()
```

after: `_canonical_payload(_SCHEME_OPENBAO, {"path": path, "signer": signer,
"reason": reason, "old_hash": old_hash, "new_hash": new_hash, "timestamp": ts})`
then `hmac.new(key.encode(), payload, hashlib.sha256)`.

**`verify_openbao_signature`** — same substitution over the 692-700 `parts`;
keep the key-error guard (701-704) and `compare_digest` (711).

**Defense in depth at the caller** (`routers/self_improve.py:258`): reject a
`change_type`/`kind` and `reason` that would previously have been forgeable —
add a small allowlist / delimiter-and-control-char rejection so untrusted
`spec["kind"]` cannot carry structural bytes. This is secondary; the canonical
encoding is the real fix, but the caller guard documents intent and blocks
absurd values.

### 2.4 Tests (each FAILS today)

`tests/unit/integrity/test_signature_forgery.py` (new):

- `test_pipe_collision_signature_does_not_cross_verify` — sign a `ChangeRecord`
  with `change_type="app|modified", file_path="config"`, then attempt to verify
  that signature against a record with `file_path="config|app",
  change_type="modified"`; assert `verify_signature(...) is False`. (Today it
  returns `True`.)
- `test_openbao_signer_reassignment_rejected` — sign with
  `signer="alice", reason="ok"`, then verify a payload whose `path` absorbs the
  signer/reason bytes; assert `verify_openbao_signature(...) is False`.
- `test_cross_scheme_replay_rejected` — take a `sign_change` result, coerce its
  fields into an openbao-shaped dict with the same signature, assert
  `verify_openbao_signature(...) is False` (scheme tag domain separation).
- `test_roundtrip_still_verifies` — honest sign→verify still returns `True` for
  both families (regression guard).
- `test_hwm_rollback_guard_unchanged` — a HWM-tamper still raises
  `IntegrityStoreError` (proves 144-217 untouched).

---

## 3. ITEM 2 — git cross-process flock is a NO-OP inside worktrees

**Severity: MEDIUM (data-integrity under concurrency). Effort: M.**
File: `src/general_ludd/git_automation/locking.py`.

### 3.1 Verified facts

- `_git_dir(repo_path)` (locking.py:120-131): returns `<repo>/.git` **only if
  `os.path.isdir(...)`**. In a linked worktree `<repo>/.git` is a *file*
  (`gitdir: ...`), so `os.path.isdir` is False → returns `None`.
- `git_repo_lock` (233-281): when `_git_dir` returns `None` (272-275) it `yield`s
  holding **only the in-process `RLock`** — the cross-process `_file_lock` is
  skipped entirely.
- The in-process lock key is `_normalize(repo_path) = os.path.realpath(repo_path)`
  (86-96, 267) — the *worktree's own* path, not the shared repo. So two worktrees
  of the same repo get **different lock objects** even in one process.
- Each worktree agent is a **separate OS process**. Net effect: concurrent
  mutating git ops (commit/merge/tag/push) across worktree agents are
  **completely unserialized** today — the exact `.git/index.lock` / HEAD / commit
  -graph races the module was written (issue #63) to prevent.

### 3.2 Exploit / failure walkthrough

Two worktree agents (`wt-A`, `wt-B`) linked to one repo commit at the same
instant:

1. Both call a mutating git op. Each enters `git_repo_lock(<its own worktree>)`.
2. `_normalize` yields two different realpaths → two different `RLock`s → no
   in-process contention (and they're different processes anyway).
3. `_git_dir` sees a `.git` *file* in each worktree → returns `None` → `_file_lock`
   skipped in both.
4. Both run `git commit` against the **shared** object store / `HEAD` / `index`
   concurrently. Result: `fatal: Unable to create '.../index.lock': File exists`
   on the loser, or — worse — interleaved ref updates / a lost commit. The
   serialization guarantee the module advertises does not exist for worktrees,
   which is gludd's primary parallelism substrate.

### 3.3 Fix — resolve the git *common* dir

Anchor both the file lock and the in-process key on the **common git dir**
(shared across all worktrees of a repo), obtained via
`git rev-parse --git-common-dir`. Critically, resolve it with a **plain bounded
`subprocess.run`, NOT `_run_git`** — `_run_git` (repo.py:214) itself acquires
`git_repo_lock`, so calling it from *inside* lock acquisition (before the file
lock is held) would re-enter the lock machinery. (The RLock is re-entrant so it
would not deadlock, but locking.py is import-light and has no `GitAutomation`
instance; a direct subprocess is correct and dependency-free.)

Replace `_git_dir` (120-131):

```python
def _git_common_dir(repo_path: str) -> str | None:
    """Absolute path to the SHARED git dir for ``repo_path``.

    Works for a normal repo (``.git`` directory) AND a linked worktree (where
    ``.git`` is a file pointing into ``<main>/.git/worktrees/<name>`` and the
    shared object store / refs live in ``<main>/.git``). We must lock on the
    SHARED dir so every worktree of one repo serializes on the same lock file.

    Uses a bounded plain subprocess — NOT ``_run_git`` (which itself takes
    ``git_repo_lock`` and would re-enter this machinery mid-acquisition). Any
    failure (not a repo yet, git missing, timeout) returns ``None`` and the
    caller falls back to the in-process lock alone.
    """
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--git-common-dir"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=10.0,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    common = proc.stdout.strip()
    if not common:
        return None
    # `--git-common-dir` may be relative to cwd; make it absolute + real so the
    # lock-file path and the in-process key are stable across spellings.
    if not os.path.isabs(common):
        common = os.path.join(repo_path, common)
    common = os.path.realpath(common)
    return common if os.path.isdir(common) else None
```

Add `import subprocess` at the top of locking.py (currently absent).

Rewire `git_repo_lock` (267-280) so the key and the file lock both derive from
the common dir:

```python
    common_dir = _git_common_dir(repo_path)
    # Normalize the in-process key off the SHARED git dir so two worktrees of one
    # repo (different working-tree paths) map to the SAME in-process lock too.
    key = common_dir if common_dir is not None else _normalize(repo_path)
    inproc = _get_inprocess_lock(key)
    inproc.acquire()
    try:
        if common_dir is None:
            yield
        else:
            with _file_lock(common_dir, key, timeout=timeout, stale_after=stale_after):
                yield
    finally:
        inproc.release()
```

`_file_lock` already accepts an arbitrary dir + key and places
`gludd-git.lock` inside it — placing that file in the shared `.git` common dir
is exactly what we want; no change to `_file_lock` itself.

### 3.4 Tests (each FAILS today)

`tests/unit/git_automation/test_worktree_lock.py` (new):

- `test_common_dir_resolved_for_linked_worktree` — create a repo, add a linked
  worktree, assert `_git_common_dir(worktree_path)` equals
  `_git_common_dir(main_repo_path)` (both the shared `.git`). Today `_git_dir`
  returns `None` for the worktree.
- `test_two_worktrees_share_one_inprocess_lock` — assert
  `_get_inprocess_lock(key_for(wt_a)) is _get_inprocess_lock(key_for(wt_b))`
  after the common-dir keying. Today they differ.
- `test_concurrent_worktree_commits_serialized` — spawn two threads each doing a
  `git commit` in a different worktree under `git_repo_lock`; assert both
  succeed and produce two distinct commits with no `index.lock` error
  (integration-flavored; may live in `tests/integration/`). Today one racer
  fails.
- `test_file_lock_created_in_common_dir` — after acquiring the lock in a
  worktree, assert `gludd-git.lock` exists under the *main* repo's `.git`, not
  in the worktree.

---

## 4. ITEM 3 — related git gaps in `repo.py`

**Severity: MEDIUM (arg-injection LOW-MEDIUM; unlocked ops MEDIUM). Effort: M.**
File: `src/general_ludd/git_automation/repo.py`. **Land with Item 2** (same
files; the newly-locked call sites below use the Item-2 common-dir logic).

### 4.1 Verified facts

- `push_to_remote` (repo.py:979-1013): **no `git_repo_lock`**. Has a 60s timeout
  + non-interactive env (992-1000). Its comment (990-991) says it "cannot route
  through `_run_git`" because it takes an explicit `repo_path`. But
  `git_repo_lock(repo_path)` is a *free function* other methods already call with
  an explicit path — confirmed at repo.py:761 (`merge_branch`) and repo.py:887
  (`gated_merge`). So the "can't lock" claim is false; it simply isn't wrapped.
- `tag_release` (412-414) and `tag_checkpoint` (416-418): call `_run_git("tag",
  ...)` (so they DO get the lock + timeout via `_run_git`) but have **no
  `_reject_leading_dash`** and **no `--` separator**, unlike every sibling
  (`create_branch`:365, `push`:421-429, `merge_branch`:747-776, `remove_worktree`
  :687-696 all reject leading-dash and/or use `--`). A tag argument of `-d` /
  `--force` / `--cleanup=...` is parsed by `git tag` as an **option**, not a tag
  name (argument injection).
- Unlocked **and untimed** raw-`subprocess.run` git call sites (no
  `git_repo_lock`, no `timeout=`):
  - `init_repo` (326-342) — `git init` + two `git config`.
  - `create_worktree` (~617-646) — `git worktree add ... --` (has `--`, no lock,
    no timeout).
  - `remove_worktree` (686-704) — has `_reject_leading_dash` + `--`, no lock, no
    timeout.
  - `create_release_tag` (954-964) — `git tag -a <ts>` (tag is a timestamp, not
    attacker-controlled, but no lock/timeout and no `--`).
  - `create_checkpoint_tag` (966-977) — `git tag <tag>`, no lock/timeout/`--`.
  - `create_local_bare_mirror` (1015-1022) — `git clone --bare`, no lock/timeout.

### 4.2 Fix

**(a) Argument injection on tags** — add `_reject_leading_dash` + `--` end-of-
options to `tag_release` and `tag_checkpoint` (and, for consistency,
`create_release_tag` / `create_checkpoint_tag`):

```python
    def tag_release(self, tag: str) -> str:
        _reject_leading_dash(tag, kind="tag name")
        self._run_git("tag", "-a", "--", tag, "-m", f"Release {tag}")
        return tag

    def tag_checkpoint(self, tag: str) -> str:
        _reject_leading_dash(tag, kind="tag name")
        self._run_git("tag", "--", tag)
        return tag
```

Note: `git tag` accepts `--` before the tag name to end option parsing; verify
against the installed git in a quick manual check (`git tag -a -- <name> -m ...`)
since a few very old git builds differ — if `--` is rejected by `git tag -a`,
fall back to the `_reject_leading_dash` guard alone (which already closes the
injection). The `create_checkpoint_tag`/`create_release_tag` timestamp tags are
not attacker-controlled, so the guard there is defense-in-depth.

**(b) Missing locks + timeouts** — wrap each unlocked mutating call site in
`git_repo_lock(<the repo_path it targets>)` and add
`timeout=_GIT_TIMEOUT_SECONDS, env={**os.environ, **_NON_INTERACTIVE_GIT_ENV}`
to the raw `subprocess.run` calls (mirroring `push_to_remote`'s existing
timeout/env pattern at 992-1000). Concretely:

- `push_to_remote`: wrap the `subprocess.run` (992-1000) in
  `with git_repo_lock(repo_path):` and delete the misleading 990-991 comment.
- `init_repo`: the repo may not exist yet, so `git_repo_lock` will fall back to
  the in-process lock (common-dir unresolved) — still correct; add the lock +
  `timeout`/`env` to all three subprocess calls.
- `create_worktree`, `remove_worktree`, `create_release_tag`,
  `create_checkpoint_tag`, `create_local_bare_mirror`: wrap in
  `git_repo_lock(repo_path)` and add `timeout`/`env`. (`create_local_bare_mirror`
  reads `repo_path` and writes `mirror_path`; lock on `repo_path`, the source.)

### 4.3 Tests (each FAILS today)

`tests/unit/git_automation/test_repo_hardening.py` (new):

- `test_tag_release_rejects_leading_dash` — `tag_release("-d")` raises
  `ValueError`. Today it shells `git tag -a -d ...`.
- `test_tag_checkpoint_rejects_leading_dash` — `tag_checkpoint("--force")` raises.
- `test_push_to_remote_holds_repo_lock` — monkeypatch `git_repo_lock` with a
  spy CM; assert `push_to_remote` entered it. Today it never does.
- `test_init_repo_calls_have_timeout` / `test_worktree_ops_timeout` — patch
  `subprocess.run` and assert `timeout=` is passed on init/worktree/tag/mirror
  call sites. Today it is absent.
- `test_create_local_bare_mirror_locked` — spy CM asserts the source repo lock
  is taken.

---

## 5. ITEM 4 — environment-variable secret leaks to subprocess children

**Severity: HIGH (secret disclosure). Effort: S–M.**
Files: `routers/stream.py`, `sandbox_exec/executor.py`,
`renderers/runner.py`, `collections/.../module_utils/gludd.py`.

### 5.1 Verified facts (all CONFIRMED against source)

- **`routers/stream.py:81-87`** `_run_subprocess` does
  `subprocess.Popen(args, cwd=cwd, stdout=PIPE, stderr=PIPE)` with **no `env=`**
  → the `ansible-playbook` CLI child inherits the daemon's *entire* `os.environ`
  (ZAI_API_KEY, AWS_*, DATABASE_URL, GLUDD_AUTH_PSK — not just what a playbook needs).
  Reachable path: `POST /admin/stream/dispatch` (route registered stream.py:91-92,
  handler `admin_stream_dispatch` :100) → `_run_clone_sync` (:165) when
  `req.wait_for_completion=True` → builds `["ansible-playbook", "run-clone.yml"]`
  and calls `_run_subprocess` (:212-230).
- The allowlist scrub **exists but on a different path**:
  `_PLAYBOOK_ENV_ALLOWLIST` is defined in `ansible/core_runner.py:524` and
  enforced only inside `CoreAnsibleRunner` (an in-process `os.environ` swap around
  `PlaybookExecutor.run()`, core_runner.py:822-831). `stream.py`'s raw
  `ansible-playbook` CLI **bypasses `core_runner.py` entirely**, so the scrub
  never runs. Confirmed bypass.
- **`sandbox_exec/executor.py:12-19`** `execute` does
  `subprocess.run(shlex.split(command), cwd=workdir, capture_output=True,
  text=True, timeout=self.timeout)` — **no `env=`**. Latent.
- **`event_loop/loop.py:1812-1816`**: the only caller passes a **marker string**
  `f"dispatch:{todo_id}:{work_type}"` (no spaces) to `executor.execute`, which
  `shlex.split`s to a single non-existent argv → `FileNotFoundError`. So the
  executor leak is currently **inert** (not reachable with a real command) but is
  a live footgun the moment a real command is wired.
- **`collections/ansible_collections/general_ludd/agent/plugins/module_utils/gludd.py:146-154`**:
  `_headers()` does `psk = self._psk or os.environ.get("GLUDD_AUTH_PSK", "")` (line
  148) and, if set, adds `Authorization: Bearer <psk>` + `X-PSK`. Every
  `general_ludd.agent.*` module built on this base **scavenges the admin PSK from
  its inherited env** — converting the stream.py/renderer env leak into *live
  admin API calls*.
- **`renderers/runner.py:226-232`**: deliberately re-injects
  `extra_env["GLUDD_AUTH_PSK"] = os.environ["GLUDD_AUTH_PSK"]` (and `GLUDD_DAEMON_URL`)
  which `core_runner.py:818-821` merges in *after* the allowlist strip — an
  intentional opt-in so renderer playbooks can call back into the daemon. This is
  the mechanism by which the PSK legitimately reaches Ansible-dispatched agents
  (relevant to Item 5's scope) but it means renderer subprocesses hold the admin
  credential.

### 5.2 Fix

- **`stream.py` `_run_subprocess`**: pass an **allowlisted** `env`. Import (or
  re-export) `_PLAYBOOK_ENV_ALLOWLIST` from `ansible/core_runner.py` and build
  `env = {k: v for k, v in os.environ.items() if k in _PLAYBOOK_ENV_ALLOWLIST}`,
  then re-add only what the CLI genuinely needs (`PATH`, `HOME`,
  `ANSIBLE_COLLECTIONS_PATH`, and `GLUDD_AUTH_PSK`/`GLUDD_DAEMON_URL` *only if* the
  clone playbook must call back — decide per playbook, default deny). Pass
  `env=env` to `Popen`. Better still: route this dispatch through
  `CoreAnsibleRunner` so it inherits the same scrub as every other playbook
  invocation, eliminating the parallel CLI path. Prefer that if the clone
  playbook can run under ansible-runner.

  ```python
  def _run_subprocess(args, cwd, timeout):
      from general_ludd.ansible.core_runner import _PLAYBOOK_ENV_ALLOWLIST
      safe_env = {k: v for k, v in os.environ.items() if k in _PLAYBOOK_ENV_ALLOWLIST}
      # add only the non-secret vars the CLI needs to run
      for k in ("PATH", "HOME", "LANG", "ANSIBLE_COLLECTIONS_PATH"):
          if k in os.environ:
              safe_env[k] = os.environ[k]
      return subprocess.Popen(args, cwd=cwd, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, env=safe_env)
  ```

- **`sandbox_exec/executor.py`**: add an `env=` parameter defaulting to a minimal
  allowlisted env (never bare `os.environ`); pass it to `subprocess.run`. Since
  the call site is inert, this is preventative — fix it now so the leak can never
  activate. Keep the `timeout`.

- **`module_utils/gludd.py`**: leave the `self._psk or os.environ.get(...)`
  fallback intact (Ansible agents legitimately need it) BUT make it work only
  because Items 4-stream and 5 ensure the PSK reaches *only* intended playbooks.
  No code change strictly required here; document that this line is the
  amplifier and that the env-scoping upstream is the control.

- **`renderers/runner.py`**: no functional change — the PSK re-injection is
  intentional. Add a comment cross-referencing Item 5 (this is *why* Ansible
  agents are in the PSK trust scope).

### 5.3 Tests (each FAILS today)

- `tests/unit/routers/test_stream_env_scrub.py::test_run_subprocess_strips_secrets`
  (new) — set `ZAI_API_KEY` / `AWS_SECRET_ACCESS_KEY` in `os.environ`,
  monkeypatch `subprocess.Popen` to capture kwargs, call `_run_subprocess`,
  assert those keys are absent from the passed `env`. Today `env` is unset (child
  inherits everything).
- `tests/unit/sandbox_exec/test_executor_env.py::test_execute_does_not_inherit_secrets`
  (new) — same pattern on `SandboxExecutor.execute`.
- Optional integration: `test_admin_stream_dispatch_child_env_scrubbed` driving
  `POST /admin/stream/dispatch` with `wait_for_completion=True` and a stubbed
  Popen, asserting the captured env excludes secrets.

---

## 6. ITEM 5 — PSK-flat authorization (per-capability gate)

**Severity: CRITICAL. Effort: L.** Files: `routers/security.py`,
`routers/compute.py`, `routers/account.py`, `security/` (new middleware),
`agents/registry.py`, `permissions/`, + a DB migration for ownership columns.

### 6.1 Scope (CORRECTED — do not overstate)

The PSK trust boundary reaches **Ansible-dispatched agents only**:
`renderers/runner.py:226-232` re-injects `GLUDD_AUTH_PSK` past the allowlist scrub,
and `module_utils/gludd.py:148` scavenges it into `Authorization: Bearer`. It
does **NOT** reach:

- the **in-process dispatcher** (`agents/dispatcher.py:370` — `await
  self._executor(task)` is a same-process coroutine, no boundary, no PSK;
  CONFIRMED, zero PSK references in that file), or
- **MCP tool servers** (`mcp/transport.py:351-355` `_ENV_ALLOWLIST =
  ("PATH","HOME","LANG","LC_ALL","TMPDIR")`; PSK is never inherited — test-pinned
  by `tests/unit/test_mcp_transport_pins.py:66-71`; CONFIRMED).

Within the reachable scope, any PSK holder is omnipotent. All of the following
were re-confirmed:

| Tag | Handler | File:line | Gap |
|-----|---------|-----------|-----|
| A-PERMSPEC-SELF-EDIT | `PUT /admin/perm/spec/{agent_type}` | `routers/security.py:406-421` | Overwrites the on-disk spec for ANY agent_type (incl. `human-admin`); no ownership/role check. |
| A-STS-REVOKE-ANY | `POST /admin/sts/revoke` | `routers/security.py:344-355` | Revokes any `token_id`; no check caller is issuer/subject. |
| A-DEPLOY-DESTROY-ANY | `DELETE /admin/compute/destroy/{instance_id}` | `routers/compute.py:337-353` | Destroys any recorded instance; `may_destroy(role)`/`can_destroy(role)` exist (`permissions/tool_permissions.py:269-271`, `permissions/infra_access.py:42-48`) but are **never called** — dead authorization code. |
| A-ACCOUNT-ANY | `DELETE /api/account` | `routers/account.py:91-113` | Deletes any `user_id`'s data; only gate is `confirm=true` (fat-finger guard, not authz). |

**A-ESCALATION-SELF-APPROVE (CRITICAL) — scope CORRECTED after re-verify.**
The original ledger said the requester can self-approve with their own id. That
literal case is in fact **blocked**: `routers/security.py:587-599` returns 403
`self_approval_forbidden` when `human_reviewer == row["agent_id"]` (and 668-680
mirrors it for deny). **HOWEVER** the finding still stands in a stronger form:
`human_reviewer` is read straight from the request body
(`req.get("human_reviewer")`) as **unauthenticated free text** — no session, no
separate human credential, no roster cross-check. Since PSK is the only auth
boundary and the same PSK holder controls both the escalation *request* and its
*approval*, the requester bypasses the equality guard by submitting **any other
string** (`"human_reviewer": "totally-a-human"` or a second agent's id) and
`_resolve_human_todo_for_escalation(..., resolver=human_reviewer, ...)`
(security.py:642) mints a real STS token on that unverified word. It is
self-approval-with-an-alias. The same trust-the-string pattern recurs at
`security.py:508` (auto-approved rows) and `security.py:215`
(`_sync_escalation_from_human_todo`, `human_resolver`).

**Building blocks that already exist and are sound** (build ON these, do not
reinvent):
- `security/capability_lattice.py`: `capabilities_for(role)`,
  `role_may_dispatch(role, kind)`, `check_dispatch(role, kind)` (raises
  `CapabilityError`), `is_protected_path`, `check_self_modification`.
- `security/permissions.py`: `PermissionSpec.capability_for(resource)`,
  `.is_denied(resource, action, path=None)`; `PermissionSpecParser.is_subset` /
  `.intersection`; `PermissionSubject` enum (`AGENT`/`HUMAN`/`STS_TOKEN`).
- `security/sts.py`: `STSRegistry.issue/resolve/revoke/purge_expired`;
  `StsIssuer.issue/validate/record_use/revoke/list_active` — **subset-enforcing,
  TTL-clamping, correct**. Keep as-is.
- `agents/registry.py:43-55` `can_invoke` gates subagent dispatch via the
  per-agent `AgentPermission` matrix (glob `allowed_subagents`).

**NOT a usable exemplar:** `ornith/client.py:47-56,91-100` calls
`permission_spec.has_capability(...)` and `sts_registry.mint(...)` — **methods
that DO NOT EXIST** on the real classes (real API is `capability_for`/`is_denied`
and `issue`). `OrnithClient` is constructed only in
`tests/unit/test_ornith_client.py:95,106`; production uses
`OrnithMCPClientAdapter`, which does not call those. So the middleware wiring is
genuinely **new code** — do not copy `ornith/client.py`.

### 6.2 Fix — per-capability gate + ownership + authenticated approver

**(a) Ownership columns (DB migration).** Add an owner/principal column to the
resources these handlers mutate so authorization can be "caller owns target":
- permission specs: record the issuing principal (the spec is a file today —
  either move to a table with an `owner_principal` column, or maintain a sidecar
  ownership registry keyed by `agent_type`).
- STS tokens: `StsToken`/`STSClaim` already carry `issuer_agent_id`/`subject_agent_id`
  (verify in `security/sts.py`); expose them to the revoke handler.
- deployments: add `owner_principal` to the deployment record (compute manager).
- account data: the principal that owns `user_id`.

Follow the repo's alembic conventions (a real migration under
`.../migrations/` — the ledger has a history of migration drift, so add the
column AND backfill, and pin it in the alembic head).

**(b) Authenticated principal, not a request-body string.** Introduce a
`Principal` resolved by the auth middleware from the credential actually
presented (PSK → a named service principal; a future per-agent token → that
agent's id). Handlers receive the principal via a FastAPI dependency; they must
NEVER read the acting/approving identity from the request body. Concretely,
`human_reviewer`/`human_resolver` must be replaced by the authenticated
principal of a **distinct** human credential — a PSK-holder cannot assert it.
Until per-human credentials exist, at minimum require the approver principal to
differ from the requester principal *as authenticated* (not as a submitted
string), and gate approval behind a capability the requesting agent's spec does
not hold.

**(c) Per-capability gate over the lattice.** Add a small authorization helper
(new module, e.g. `security/authz.py`) that each mutating handler calls:

```python
def require_capability(principal: Principal, resource: str, action: str,
                       *, owner: str | None = None) -> None:
    """Raise PermissionDeniedError unless principal may perform action on
    resource. Uses PermissionSpec.is_denied over the principal's spec, AND —
    when `owner` is supplied — requires principal to be the owner (or hold an
    explicit admin capability). No request-body identity is ever consulted."""
```

Wire it into each handler:
- A-PERMSPEC-SELF-EDIT: `require_capability(p, f"perm-spec:{agent_type}",
  "write", owner=owner_of(agent_type))`.
- A-STS-REVOKE-ANY: `require_capability(p, f"sts:{token_id}", "revoke",
  owner=issuer_of(token_id))`.
- A-DEPLOY-DESTROY-ANY: call the **already-existing** `can_destroy(role)` /
  `may_destroy(role)` (wire the dead code) AND
  `require_capability(p, f"deployment:{instance_id}", "destroy",
  owner=owner_of(instance_id))`.
- A-ACCOUNT-ANY: `require_capability(p, f"account:{user_id}", "delete",
  owner=principal_for(user_id))`.
- A-ESCALATION-SELF-APPROVE: derive reviewer identity from the authenticated
  approver principal; reject if it equals the requester principal or lacks an
  `escalation:approve` capability.

Keep `StsIssuer`/`STSRegistry` unchanged — they already enforce subset+TTL, so
the minted token stays correctly scoped once the *approval* is authenticated.

### 6.3 Tests (each FAILS today)

`tests/unit/routers/test_authz_ownership.py` (new):
- `test_perm_spec_put_denies_non_owner` — a principal that does not own
  `agent_type` gets 403 from `PUT /admin/perm/spec/{agent_type}`.
- `test_sts_revoke_denies_non_issuer` — revoking a token you did not issue → 403.
- `test_compute_destroy_denies_without_capability` — `DELETE
  /admin/compute/destroy/{id}` denied for a role lacking `can_destroy`
  (proves the dead `may_destroy` is now wired).
- `test_account_delete_denies_foreign_user` — deleting another principal's
  `user_id` → 403.
- `test_escalation_approver_must_be_authenticated_distinct_principal` — an
  approval whose `human_reviewer` is an arbitrary body string (not an
  authenticated distinct human) is rejected; today the alias
  `"totally-a-human"` succeeds and mints a token.
- `test_escalation_alias_self_approval_rejected` — the requester principal
  cannot approve by submitting a different string.

Regression guards (must still PASS): the existing
`self_approval_forbidden` literal-match test, and the STS subset/TTL tests in
the sts suite.

---

## 7. ITEM 6 — engine denylist gap, self-improve bait-and-switch, SSTI unwrap

**Severity: HIGH (mixed). Effort: M.** Three CONFIRMED sub-items to fix; two
prior ledger claims were **REFUTED on re-verify** and are recorded here so the
implementer does not chase them. This section overlaps `routers/self_improve.py`
with Item 1 — rebase after Item 1 lands.

### 7.1 REFUTED on re-verify (do NOT implement — recorded for the record)

- **C-RELOAD symlink bypass — REFUTED (exploit does not exist).** The lexical
  fact is true: `security/capability_lattice.py:179-199` `is_protected_path`
  calls `is_denied_path(path)` with **no `workspace_root`**, and
  `path_canonicalizer.py:263-345` only does the realpath-resolve block when
  `workspace_root is not None` — so `is_protected_path` and
  `self_update/apply.py:124-139` (`_any_protected`) are purely lexical. BUT the
  inferred "symlink target gets written" does **not** hold: `apply.py`'s
  `apply_plan` is a decision/audit layer that **never writes** (only
  `POST /admin/self-update/plan` audit records). Every real write/reload path
  pre-resolves symlinks: `UpdateApplier.apply` writes the RESOLVED paths
  (`self_update/applier.py:356-362`), `AtomicSafeWriter._confine` resolves
  (`safe_writer.py:190-217`), and `reload/hot_reloader.py:258-259` does
  `os.path.realpath(live_path_str)` before `check_self_modification`. Regression
  tests already assert this (`test_hot_reload_toc.py:256`,
  `test_self_update_applier.py:683`, `test_self_improve_security.py:128`). No fix
  needed. (Optional hardening: give `is_protected_path` a `workspace_root` so its
  own return value is realpath-aware for defense in depth — low priority.)
- **Protected-path denylist missing AGENTS.md/CLAUDE.md/TASKS.md — REFUTED.**
  They ARE in the denylist: `path_canonicalizer.py:162-207`
  `CANONICAL_DENY_MARKERS` includes `"agents.md","claude.md","tasks.md",
  "bugs.md","session.md"` (case-insensitive match), echoed in
  `PROTECTED_PATH_SUBSTRINGS` (:71-82) and `PROTECTED_PATH_SEGMENTS` (:87-97).
  No fix needed.

### 7.2 CONFIRMED sub-item A — `execution/engine.py` write path ignores the denylist

**Severity: HIGH. Effort: S.**

Verified: `engine.py:889-893` `_write_file` and `engine.py:942-993`
`_apply_unified_diff` only call `_resolve_in_workspace` (`engine.py:864-887`),
which does realpath-based **workspace containment** (`os.path.commonpath`) and
nothing else. Neither consults `is_protected_path` / `is_denied_path` /
`capability_lattice` (grep of those symbols in `engine.py` = 0 hits). So a
write/diff targeting a protected file that lives **inside the workspace** — e.g.
`.claude/settings.json`, `AGENTS.md`, `guardrails.py`, an alembic migration —
passes unchallenged. Confinement is necessary but NOT sufficient; the denylist
that every *other* write path honors is skipped here.

**Fix:** before writing, run each target through the denylist (mirroring
`UpdateApplier`/`AtomicSafeWriter`). In `_write_file`:

```python
def _write_file(self, file_path: str, content: str) -> None:
    full_path = self._resolve_in_workspace(file_path)
    if is_protected_path(full_path) or is_denied_path(full_path, self.workspace_root):
        raise PermissionError(f"refusing write to protected/denied path: {file_path!r}")
    os.makedirs(os.path.dirname(full_path), exist_ok=True)
    with open(full_path, "w") as f:
        f.write(content)
```

Pass `workspace_root` to `is_denied_path` so its realpath-resolve branch runs
(closing the symlink-into-protected case for this path too). In
`_apply_unified_diff`, apply the same check to every `target` in the
`self._resolve_in_workspace(target)` loop (949-957) — refuse the whole diff if
any target is protected (fail-closed, consistent with the existing
escaping-target refusal). Import
`from general_ludd.security.capability_lattice import is_protected_path` and
`from general_ludd.security.path_canonicalizer import is_denied_path`.

**Tests (FAIL today)** — `tests/unit/execution/test_engine_denylist.py` (new):
- `test_write_file_refuses_protected_in_workspace` — `_write_file(".claude/settings.json", ...)`
  raises `PermissionError`. Today it writes.
- `test_apply_unified_diff_refuses_protected_target` — a diff touching `AGENTS.md`
  is rejected and no file is written.
- `test_write_file_allows_normal_workspace_file` — regression: an ordinary file
  still writes.

### 7.3 CONFIRMED sub-item B — self-improve bait-and-switch (C-SELFIMP)

**Severity: HIGH. Effort: S.**

Verified: `routers/self_improve.py:463-470` (non-config apply/execute handler)
reads `worktree_path = str(payload.get("worktree_path", ""))` from the **live
request body** and validates/applies against it, while the approved record's
`plan_artifact` — which stored the originally-enqueued `worktree_path` at enqueue
time (`self_improve.py:326-331`) — is never parsed in this branch. (The sibling
config-tier path `_apply_approved_config_change`, :206-247, is hardened: it uses
`spec = json.loads(todo.plan_artifact ...)` at :242.) So an operator approves a
record referencing worktree A, then re-POSTs with `worktree_path=B` and the
workflow validates/applies B — the approval is decoupled from what executes.

**Fix:** in the non-config branch, read `worktree_path` (and any other
execution-determining field) from the **enqueued/approved artifact**, never from
the live payload:

```python
approved_spec = json.loads(approval_todo.plan_artifact or "{}")
worktree_path = str(approved_spec.get("worktree_path", ""))
if not worktree_path:
    raise HTTPException(status_code=422, detail="approved record has no worktree_path")
# ignore any worktree_path in the live payload
```

If the live payload carries a `worktree_path` that disagrees with the approved
artifact, reject with 409 (tamper signal) rather than silently preferring one.

**Tests (FAIL today)** — `tests/unit/routers/test_self_improve_baitswitch.py` (new):
- `test_apply_uses_approved_worktree_not_payload` — enqueue+approve with
  `worktree_path=A`, POST apply with `worktree_path=B`; assert the workflow was
  invoked with A (patch `SelfImprovementWorkflow.validate_improvement` to
  capture its arg). Today it gets B.
- `test_apply_rejects_conflicting_worktree` — divergent payload path → 409.

### 7.4 CONFIRMED sub-item C — A-COLLECTION-HANDLER-UNWRAP (SSTI/RCE)

**Severity: HIGH (SSTI→RCE). Effort: S–M.**

Verified: `daemon_wiring.py:204-257` `_collection_handler` builds a transient
playbook where `task_args = dict(args)` (caller/model-supplied) is embedded
**verbatim as the module's inline argument dict**
(`{"name": ..., name: task_args}`, :232), `yaml.safe_dump`'d (:246), and run via
`runner_adapter.run_playbook`. Only the FQCN module *name* is validated
(`_FQCN_RE`, :215). There is **no `wrap_extravars`/`wrap_unsafe`** on this path.
`wrap_extravars` (`ansible/unsafe.py:63-72`) is applied ONLY to the `extravars`
channel (`ansible/core_runner.py:290-292`) — the inline task-args channel used
here bypasses it entirely. A Jinja-shaped value in `task_args` (e.g.
`{{ lookup('pipe','id') }}`) is re-evaluated by Ansible's task-arg templating at
run time → SSTI→RCE, the exact class `wrap_extravars` was built to stop.

**Fix:** route the caller-supplied task args through the same unsafe-wrapping
before they enter the generated playbook. Apply `wrap_unsafe` to every value in
`task_args` so Ansible marks them `!unsafe` and does not re-template them:

```python
from general_ludd.ansible.unsafe import wrap_unsafe
task_args = {k: wrap_unsafe(v) for k, v in dict(args).items()}
timeout = ...  # pop control keys BEFORE wrapping, or from the raw args
```

Pop the control keys (`_timeout`, `_hosts`) from the raw dict first, then wrap
the remainder. Verify `yaml.safe_dump` serializes the `AnsibleUnsafe`-wrapped
values as `!unsafe`-tagged scalars end-to-end (add a serialization assertion in
the test); if `safe_dump` drops the tag, pass the args via a wrapped
`extravars`/`vars` channel referenced by the task instead of inlining raw
strings. Keep the `_FQCN_RE` name validation.

**Implemented 2026-08-01.** The transient-playbook serializer now uses a
`SafeDumper` subclass that emits every caller-controlled string in task args
(and the caller-controlled hosts selector) as an explicit `!unsafe` scalar.
The regression test loads the generated file with an unsafe-tag-aware loader
and asserts both literal payload preservation and tag presence for lookup,
dunder, and arithmetic Jinja payloads. This closes the serialization gap noted
above: an in-memory unsafe proxy alone is insufficient if a YAML round trip
silently reduces it to an ordinary playbook string.

**Upstream/operator evidence.** This is a long-lived operational boundary, not
a Gludd-only convention:

- [Ansible's advanced YAML syntax documentation](https://docs.ansible.com/projects/ansible-core/devel/playbook_guide/playbooks_advanced_syntax.html#unsafe-or-raw-strings)
  says `!unsafe` prevents malicious Jinja evaluation and is more comprehensive
  than raw-block escaping, including for nested arrays and mappings.
- [Ansible's version 12 porting guide](https://docs.ansible.com/projects/ansible/latest/porting_guides/porting_guide_12.html#template-trust-model-inversion)
  records that the legacy trust-by-default model produced multiple RCE-class
  vulnerabilities when unsafe markers were not propagated. Gludd keeps the
  explicit tag for compatibility across both legacy and inverted trust models.
- A [2020 Ansible operator thread](https://forum.ansible.com/t/making-an-unsafe-variable-safe/33059)
  confirms that operators intentionally preserve the unsafe boundary rather
  than relying on a generic inverse `!safe` conversion; a later
  [operator discussion](https://forum.ansible.com/t/how-to-get-ansibleunsafetext-to-template/8831)
  reiterates that externally sourced values are marked unsafe by design.

**Tests (FAIL today)** — `tests/unit/test_collection_handler_ssti.py` (new):
- `test_task_args_jinja_is_neutralized` — pass
  `task_args={"cmd": "{{ lookup('pipe','id') }}"}`; assert the generated
  playbook file marks that value `!unsafe` (or is otherwise not re-templated).
  Today the raw Jinja string is written verbatim.
- `test_fqcn_name_still_validated` — regression: a non-FQCN module name is still
  rejected.

---

## 8. Verification ledger (what changed vs. the prior audit)

| Item | Prior claim | Re-verify verdict |
|------|-------------|-------------------|
| 1 | HMAC pipe-join forgeable | **CONFIRMED** (all 4 fns; caller taint at self_improve.py:258). |
| 2 | worktree flock no-op | **CONFIRMED** (`_git_dir` isdir check; separate processes unserialized). |
| 3 | push unlocked; tag arg-injection; init/worktree/mirror unlocked+untimed | **CONFIRMED** (repo.py 412-418, 979-1013, 326-342, 617-704, 954-977, 1015-1022). |
| 4 | stream.py + executor.py env leaks; module_utils PSK scavenge; renderer re-inject | **CONFIRMED** (executor leak inert per loop.py:1812-1816 marker string). |
| 5 | PSK-flat authz (5 handlers + escalation self-approve) | **CONFIRMED** for the 4 resource handlers; escalation **CONFIRMED-with-correction** (literal self-match IS blocked at security.py:587-599; the real gap is unauthenticated free-text reviewer / alias bypass). `may_destroy` is dead code. Dispatcher + MCP correctly OUT of PSK scope. `ornith/client.py` is broken test-only code, not an exemplar. |
| 6a | C-RELOAD symlink write | **REFUTED** — decision layer never writes; real write/reload paths pre-resolve symlinks. |
| 6b | AGENTS/CLAUDE/TASKS.md not in denylist | **REFUTED** — they ARE in `CANONICAL_DENY_MARKERS`. |
| 6c | engine.py write path skips denylist | **CONFIRMED** — confinement only. |
| 6d | self-improve bait-and-switch | **CONFIRMED** — non-config branch reads live payload worktree_path. |
| 6e | collection-handler SSTI unwrap | **CONFIRMED** — inline task_args bypass `wrap_extravars`. |
