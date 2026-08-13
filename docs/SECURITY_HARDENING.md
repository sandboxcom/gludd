# Security Hardening Reference

This document catalogues gludd's defenses against **subprocess / argv (option)
injection**, **SSRF**, **path traversal / confinement**, and
**self-modification**. It is a reference for where each guard lives and which
test exercises it; the source files themselves carry the authoritative
rationale in module docstrings.

gludd is a long-running daemon that runs many roles in parallel and shells out
to `git`, `ansible-galaxy`, `sbatch`/`sacct`/`scancel`, `uv`/`pip`, `patch`,
`gh`, MCP server runtimes, and local-inference engines. Almost every one of
those invocations takes at least one value that originates from caller input,
config, secrets, or LLM output. The hardening below treats all of those values
as untrusted.

---

## The argv-injection defense pattern

Every subprocess surface that accepts a caller/config/LLM-derived value applies
the same layered pattern. Defense-in-depth means **no single layer is the weak
link** — each surface validates even though the next layer would also catch it.

1. **Validate the caller/config value, fail closed.** A dedicated validator
   raises (or returns an error) *before* any subprocess is launched. A rejected
   value never reaches `subprocess`. The validators reject:
   - **a leading `-`** — `git`, `ansible-galaxy`, `pytest`, `sbatch`, `uv`/`pip`,
     etc. parse a leading-dash token as an **option**, not a positional. This is
     the core option-injection primitive (`--upload-pack=<cmd>`, `--exec=<cmd>`,
     `-r /etc/passwd`, `--index-url=...`).
   - **shell metacharacters and whitespace/control chars** — even though argv is
     always list-form (never `shell=True`), these are rejected as a config smell
     and defense in depth (some downstream tools re-shell their own args, and a
     value may later be interpolated into a `--wrap`/`#SBATCH` string).
   - **structural rules** for the specific token kind (a Slurm job id must be
     numeric, a galaxy name must be `namespace.name`, a package spec must be
     PEP 508-ish, a ref must not contain `..`/`.lock`/leading `/`).
2. **List-form argv, never `shell=True`.** Every `subprocess.run` /
   `create_subprocess_exec` / `Popen` passes a list, so the shell never parses
   the value.
3. **`--` end-of-options separator** before any caller-controlled positional, so
   even if validation ever loosened, the value can never be re-read as a flag.
4. **Bound + non-interactive.** Subprocesses carry timeouts; git carries
   `GIT_TERMINAL_PROMPT=0` / `GIT_ASKPASS=echo` so a credential prompt can never
   hang the daemon. Process-group kills (`start_new_session=True` + `killpg`)
   prevent recipe grandchildren from leaking on timeout.

---

## Surface → file → guard → test

### Subprocess / argv-injection surfaces

| Surface | File | Guard | Test |
| --- | --- | --- | --- |
| `git` (branch/push/merge/tag/worktree) | `src/general_ludd/git_automation/repo.py` | `_reject_leading_dash()` on every ref/remote/path; `--` before positionals; `_run_git` adds 60s timeout + non-interactive env | `tests/unit/test_git_automation.py` |
| `git clone` URL (RCE / option-injection) | `src/general_ludd/git_automation/repo.py` | `_reject_dangerous_clone_url()` — refuses leading `-`, `ext::`/`git::`/`fd::` transport helpers, embedded ssh `-o`/`ProxyCommand`, and (when `allow_local=False`) `file://`; `--` before url/target | `tests/unit/test_git_automation.py` |
| `git worktree add/remove/list` | `src/general_ludd/worktree/core.py` | `validate_branch_name()` (leading `-`, ref metachars, `..`, leading `/`, `.lock`) + `confine_worktree_path()` (realpath under allowed base); `build_worktree_*_argv()` emit list-form with `--`; reclaim fails closed | `tests/unit/test_worktree_core.py` |
| `git` history intel (`git -C <repo> ...`) | `src/general_ludd/code_intelligence/git_intel.py` | `_validate_token()` (leading `-` + metachars) on caller refs; repo realpath-confined; refs placed after `--` | `tests/unit/test_git_intel.py` |
| `git push` + `gh pr create` | `src/general_ludd/git_automation/pr_delivery.py` | `_validate_ref()` (leading `-` + `_SAFE_REF` charset) on branch/remote names, fail closed | `tests/unit/test_pr_delivery.py` |
| Slurm `sbatch`/`sacct`/`scancel` | `src/general_ludd/infra/slurm.py`, `src/general_ludd/routers/slurm.py` | `_require_job_id` (numeric), `_require_name`/`_require_time`/`_require_extra_arg` (no leading dash / newline that could inject a `#SBATCH` directive); `--` before script; `--jobs=<id>` binds id to flag; router maps invalid input to 422, unavailable Slurm to 503, and unexpected failures to a sanitized 500 | `tests/unit/test_slurm.py`, `tests/unit/test_slurm_deep.py`, `tests/unit/test_slurm_daemon_endpoints.py` |
| Local inference engines (vllm / llama.cpp / slurm `--wrap`) | `src/general_ludd/infra/local_inference.py` | `_validate_model`/`_validate_host`/`_validate_port`/`_validate_extra_args` reject leading-dash + shell metachars before argv (and before the `--wrap` shell string) | `tests/unit/test_local_inference.py` |
| `uv`/`pip` package install | `src/general_ludd/dependency/manager.py` | `_validate_package_spec()` (PEP 508-ish, no leading `-`, no metachars/whitespace/paths); `--` before the spec | `tests/unit/test_dependency_manager.py` |
| `ansible-galaxy` search/install | `src/general_ludd/ansible/galaxy.py` | `_validate_galaxy_type`/`_validate_name_spec`/`_validate_search_query` (namespace.name form, no leading `-`); `--` before positional | `tests/unit/test_galaxy.py` |
| MCP server subprocess launch | `src/general_ludd/mcp/transport.py` | `_validate_launch_command()` — exec-basename allowlist (`GLUDD_MCP_ALLOW_ANY_EXEC` opt-out), must resolve on PATH; `_validate_package_spec`/`_validate_package_runtime_args` for npx/uvx/bunx (leading-dash + metachar guard); minimal `_ENV_ALLOWLIST` env (no host secrets) | `tests/unit/test_mcp_transport.py` |
| LLM-supplied file writes + unified diffs (`patch`) | `src/general_ludd/execution/engine.py` | `_resolve_in_workspace()` realpath + `commonpath` jail; `_diff_target_paths()` validates BOTH `---` and `+++` header paths before invoking `patch -p1 -d <realpath-jail>` (list-form) | `tests/unit/test_execution_engine.py` |
| Config-supplied test command | `src/general_ludd/validation/runner.py` | `_validate_command()` rejects shell metachars (would be mis-split by `shlex`), runner-binary allowlist; `shell=False` | `tests/unit/test_validation_runner.py` |
| Feature-DB evidence `test:<node-id>` | `src/general_ludd/quality/feature_verifier.py` | `_validate_node_id()` (leading `-` + metachars, safe pytest node-id regex); fails closed `rc=1` without spawning | `tests/unit/test_feature_verifier.py` |
| Dogfood smoke-task → `ansible-playbook` | `src/general_ludd/dogfood/runner.py` | `_validate_task_name()` (no path sep / `..` / leading `-` / metachars) before `playbooks/<task>.yml` | `tests/unit/test_dogfood_runner.py` |
| A/B candidate execution | `src/general_ludd/abtest/runner.py` | crash-isolated fresh-interpreter child; success requires exit 0 **and** an unforgeable parent-generated `secrets.token_hex` nonce written to a parent-controlled result file; fails closed otherwise; bounded output | `tests/unit/test_abtest_runner.py` |
| `make test` (engine test gate) | `src/general_ludd/execution/engine.py` | `_run_tests()` runs in its own process group (`start_new_session=True`) and `os.killpg`s the whole group on a 120s timeout so no recipe grandchild leaks | `tests/unit/test_execution_engine.py` |

#### Practitioner evidence for A/B resource limits

- A long-lived [Python resource-limit discussion](https://stackoverflow.com/questions/60405540/soft-hard-limit-in-pythons-resource)
  explains that an unprivileged process can lower a hard limit but cannot raise it
  again. Gludd therefore applies hard limits only in its fresh A/B child process;
  direct in-process callers remain opt-in so one test cannot poison later work.

#### Practitioner evidence for Slurm/API error boundaries

- A long-lived [slurm-users report about slurmrestd authentication](https://lists.schedmd.com/pipermail/slurm-users/2023-June/010148.html)
  shows an operator receiving a generic HTTP 500 when the service works for the
  Slurm user but fails for an ordinary user. That ambiguity is why Gludd keeps
  caller errors (422), deployment/controller availability (503), and internal
  adapter failures (sanitized 500) distinct.
- A [FastAPI practitioner discussion on generic exception handling](https://github.com/fastapi/fastapi/discussions/9478)
  documents exceptions escaping tests unless the application deliberately
  installs or exercises the intended boundary. Gludd therefore catches adapter
  failures inside each Slurm route and tests the real HTTP response, while logs
  retain diagnostic detail that the response does not expose.
- CPython's long-running [subprocess shell-security discussion](https://github.com/python/cpython/issues/114539)
  includes a concrete report that `shell=True` does not make untrusted arguments
  safe. Gludd's adversarial detector consequently treats the argument order as
  irrelevant: it flags a supported `subprocess` call whenever both a
  user-controlled value and `shell=True` occur in the same call.

### SSRF guards

| Surface | File | Guard | Test |
| --- | --- | --- | --- |
| Outbound skill fetch | `src/general_ludd/security/auth.py` | `is_safe_fetch_url()` — https-only; `_host_is_blocked()` does a **literal, no-DNS** deny of loopback / link-local / RFC-1918 / reserved / multicast / cloud-metadata (`169.254.169.254`, `metadata.google.internal`, …) hosts; never resolves names so it can never block | `tests/unit/test_auth.py` |
| Model gateway `base_url` (config/secrets-supplied) | `src/general_ludd/models/gateway.py` | `_invoke_and_bill` calls `is_safe_fetch_url(base_url)` before handing it to the provider client; fails closed on loopback/private/metadata | `tests/unit/test_gateway.py` |
| Project clone `repo_url` (HTTP-supplied) | `src/general_ludd/git_automation/repo.py` (`clone(..., allow_local=False)`) | untrusted URLs are screened by `_reject_dangerous_clone_url` with `allow_local=False`, which additionally refuses `file://` local-filesystem disclosure | `tests/unit/test_git_automation.py` |
| Generic git clone hardening | `src/general_ludd/git_automation/repo.py` | bounded `timeout`, non-interactive env, `ext::`/`git::`/`fd::` + ssh-`ProxyCommand` refusal, `--` before url/path | `tests/unit/test_git_automation.py` |

### Auth / path confinement

| Surface | File | Guard | Test |
| --- | --- | --- | --- |
| PSK auth | `src/general_ludd/security/auth.py` | `verify_psk()` constant-time `hmac.compare_digest`; `require_auth_env()` reads the `GLUDD_REQUIRE_AUTH` fail-closed opt-in | `tests/unit/test_auth.py` |
| Path jail | `src/general_ludd/security/auth.py` | `is_path_within()` — `realpath` + `commonpath`; an absolute candidate replaces the base and is then caught; refuses `..`/symlink escapes | `tests/unit/test_auth.py` |
| Workspace write/patch jail | `src/general_ludd/execution/engine.py` | `_resolve_in_workspace()` (same realpath + commonpath contract, mirrors `is_path_within`) | `tests/unit/test_execution_engine.py` |

### Self-modification guards

| Surface | File | Guard | Test |
| --- | --- | --- | --- |
| Hot-reload source swap | `src/general_ludd/security/capability_lattice.py` | `check_self_modification()` — protected-path deny-list (`PROTECTED_FILE_STEMS`: guardrails/policy/permission/capability_lattice/fs_write_policy/enforce_make; `PROTECTED_PATH_SUBSTRINGS`: `/.opencode/`, `/.claude/`, capability/fs-write policy utils) is NEVER swappable regardless of role; a `collections/` write requires the `collections_self_modify` capability | `tests/unit/test_capability_lattice.py` |
| Tool-call dispatch | `src/general_ludd/security/capability_lattice.py` | `role_may_dispatch()` / `check_dispatch()` — **default-DENY** per-role `dispatch_kinds`; the `collection` kind additionally requires `collections_self_modify`; an unknown role gets the empty baseline (grants nothing) | `tests/unit/test_capability_lattice.py` |

> The daemon-side lattice has an Ansible-side twin, `module_utils/capability_policy.py`,
> which answers the same "may this role do this?" question inside managed-node
> module execution. Both are default-DENY and fail closed.

---

## Invariants

- **Fail closed.** A value that cannot be validated is *refused*; no subprocess
  is launched, no network call is made, no code is swapped. Unknown costs under
  a configured cap are refused (see `CONCURRENCY_MODEL.md`).
- **No `shell=True` anywhere on these paths.** argv is always list-form.
- **No host-secret leakage to children.** MCP subprocesses get a minimal env
  allowlist (`PATH`, `HOME`, `LANG`, `LC_ALL`, `TMPDIR`) plus only the server's
  own declared/resolved secrets — never `ANTHROPIC_API_KEY`/`GLUDD_PSK`/cloud
  creds.
- **No blocking I/O in security primitives.** `security/auth.py` performs no DNS,
  no socket binds, no sleeps; the SSRF host check is purely literal.
