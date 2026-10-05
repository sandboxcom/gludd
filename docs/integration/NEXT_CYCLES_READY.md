# Next Cycles — Verified Paste-Ready (post batch-3a)

Status snapshot while batch-3a gates (commit pending). Every item below has had its
anchors re-verified against current code (post-`f0dc262`). Apply strictly one cycle
per `make ship` — never two gates concurrently. Order chosen lowest-risk first.

## Batch 3b (self-contained, no existing-test breakage beyond the 2 noted)

### variable_store Fix 2A — dispatch key collision
- `src/general_ludd/dispatch/variable_store.py` apply_results:
  - old: `safe_name = result.name.replace(".", "_").replace("-", "_")`
  - new: `safe_name = result.name.replace(".", "_DOT_").replace("-", "_DASH_")`
- Update 2 pinned tests in `tests/unit/test_variable_store.py`:
  - `"my_skill__ok"` → `"my_DASH_skill__ok"` (test_name_with_dash_is_normalised)
  - `"fs_read__ok"` → `"fs_DOT_read__ok"` (test_name_with_dot_is_normalised)
- Verified: no other src/template references the old dispatch__ key form.

### markdown_todo Fix 2B — `--&gt;` escape + dedup
- `src/general_ludd/issue_sources/markdown_todo.py` update_status:
  - old:
    ```text
    if comment:
        marker = f" <!--gludd:{comment}-->"
        if marker.strip() not in text:
            text = f"{text}{marker}"
    ```
  - new: escape `comment.replace("--&gt;", "--&gt;")`, build marker from the escaped
    comment, dedup on the FULL marker (`if marker not in text`).
- New test `tests/unit/test_markdown_todo.py` drafted (escape + double-call dedup).
  NOTE: reconcile the draft's dedup assertion with the final marker spacing.

## Cycle A — gated git workflow port (the user's portability ask)
- `types.py`: add `GatedCommitResult(success: bool, commit_sha: str|None=None,
  gate_returncode: int=0, message: str="")` after CloneResult. Anchor MATCHES.
- `repo.py`: add `gated_commit(files, message, gate_cmd)` after commit() (ends
  `return result.stdout.strip()`); add `gated_merge(source, target, gate_cmd,
  strategy="ff")` after merge_branch() return. Both: stage/checkout via _run_git,
  run gate_cmd via raw `subprocess.run(shell=False, env={**os.environ,
  **_NON_INTERACTIVE_GIT_ENV}, timeout=_GIT_TIMEOUT_SECONDS→124)`, commit/merge
  ONLY if returncode==0 else fail-closed GatedCommitResult. Imports os/subprocess/
  datetime ALL present. Anchors MATCH.
- `__init__.py`: add `CloneResult` (currently missing from __all__) AND
  `GatedCommitResult` to both the types import and __all__.
- Tests `tests/unit/test_gated_commit.py`: use real API — `get_current_commit()`,
  `create_branch(name)`, `init_repo(path=)`, `commit(msg)` all exist; copy the
  temp-repo fixture from `tests/unit/test_git.py` (git._run_git("init"/config/add/
  commit)). gate_cmd ["true"]/["false"] for pass/fail; assert HEAD advances or not.
- Then surface as a `validate_and_push` ansible op (role layout TBD — see role agent).

## Cycle 3 — is_path_within → is_join_within rename (4 files, atomic)
- `security/auth.py`: rename def, add `is_path_within = is_join_within` alias +
  module-docstring bullet. Anchor MATCHES (def at L114).
- `security/__init__.py`: import both names + both in __all__. Anchor MATCHES.
- `skills/fetcher.py`: import is_join_within (L11) + call site (L170). Anchor MATCHES.
  (Alias keeps any other importer working.)

## Cycle 4 — connector SSRF guard (real guard = `is_safe_endpoint`)
- `connectors/cassandra_stats.py` _build_default_executor (L67-87): call
  `is_safe_endpoint(self._jmx_url)` BEFORE reading the token; fail-closed.
- `connectors/clickhouse_stats.py` _build_default_executor (L82-92): call
  `is_safe_endpoint(self._url)` BEFORE reading the password; fail-closed.
- Guard lives in `connectors/base.py` as `is_safe_endpoint(url) -> bool`
  (loopback/link-local/RFC-1918/metadata/non-http denylist).

## Batch 4 (deferred, verified)
- Redirect guards: add `follow_redirects=False` to httpx.get in
  `connectors/pagerduty.py` (L110) and `connectors/opsgenie.py` (L126). Others
  (tempo/zipkin/newrelic/...) use urllib or injected transports — already safe.
- `issue_sources/base.py`: convert base_url to a validated property (setter re-runs
  the SSRF guard) — currently mutable post-init, bypassing _guard_base_url.
- Fix 6 secrets resolve() raise: BLOCKED — 3 callers depend on warn-and-None
  (`routers/slurm.py`, `infra/deployment.py`, `mcp/secrets.py`). Refactor those first.
- git_automation Fix 5 (_run_git routing of 9 methods + realpath jail): test-stub
  audit pending (Explore agents blocked on sandbox; needs main-thread make-grep).
- release Fix 1B (LICENSE-in-manifest assert): fixture audit pending.
