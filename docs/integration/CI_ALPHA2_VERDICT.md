# CI Gate Verdict — v0.1.0-alpha.2

**Generated:** 2026-06-18
**Verdict: FAILED**

---

## Run Summary

| Run ID | Trigger | Branch | Conclusion | Duration | URL |
|---|---|---|---|---|---|
| 27795703529 | push | master (049be99 / 7516aaf) | **failure** | 36m6s | https://github.com/sandboxcom/gludd/actions/runs/27795703529 |
| 27795704202 | push (tag) | v0.1.0-alpha.2 | **failure** | 32m55s | https://github.com/sandboxcom/gludd/actions/runs/27795704202 |

Both runs are **completed**. Neither is in-progress.

---

## Per-Job Conclusions

### Tag run 27795704202 (v0.1.0-alpha.2)

| Job | Conclusion |
|---|---|
| version | success |
| gate (3.12) | **failure** — step: Gate |
| gate (3.11) | **failure** — step: Gate |
| linux | skipped |
| windows | skipped |
| macos | skipped |
| termux | skipped |
| molecule | skipped |
| release | skipped |

### Master push run 27795703529

| Job | Conclusion |
|---|---|
| version | success |
| gate (3.12) | **failure** — step: Gate |
| gate (3.11) | **failure** — step: Gate |
| molecule | skipped |
| windows | skipped |
| macos | skipped |
| termux | skipped |
| linux | skipped |
| release | skipped |

Downstream artifact-build and release jobs were **skipped** because the gate jobs failed. The `release` job never ran on either run.

---

## GitHub Release Status

`gh release view v0.1.0-alpha.2` returned empty output (JSON parse error on empty body). The release job was skipped on both runs. **No GitHub Release for v0.1.0-alpha.2 is published.** Zero artifacts exist on run 27795704202.

---

## Failing Tests (both Python versions, identical failure set)

Total: **105 failed**, 11256 passed, 31 skipped — on both 3.11 and 3.12.

### Failure clusters (real bugs, not flaky)

**1. `general_ludd` not installed in CI venv (import-level failure — root cause for ~70 tests)**

```text
test_tui_subprocess.py::TestTUIE2E::test_tui_shows_version_in_output
  ModuleNotFoundError: No module named 'general_ludd'
  (subprocess spawning .venv/bin/python3 -m general_ludd.cli fails at CI)
```
All `SystemExit: 1` failures in `test_cli_execution_coverage.py`, `test_benchmark_cli.py`, `test_slurm_cli.py`, `test_worktree_endpoint_cli.py`, and `test_selftest.py` trace back to the CLI crashing on import. The module is present in the venv for the test-process itself (11256 tests pass) but the subprocess path used by those tests fails — likely a `uv run` vs direct-python invocation mismatch in CI.

**2. `test_readme_status_gate.py` — RecursionError + normalize bug (6 tests)**

```text
TestNormalize::test_lowercases: assert 'v0.1.0-alpha.2' == '0.1.0-alpha.2'
TestMainExitCodes::* — RecursionError: maximum recursion depth exceeded
```
`normalize()` is not stripping the `v` prefix it should strip; the `main()` function recurses infinitely (likely calls itself instead of a helper).

**3. `test_connector_proc_sys.py::test_confined_explicit_path_allowed` — CI-environment path (1 test)**

```text
FileNotFoundError: /sys/devices/LNXSYSTM:00/.../net/eth0/statistics/rx_bytes
```
Test hard-codes a specific sysfs ACPI path that exists on one CI runner's hardware but not another. Needs mocking or dynamic path discovery. This is real (not flaky) but environment-specific.

**4. `test_gate_concurrency.py::test_second_invocation_rejected_when_lock_held` (1 test)**

```text
AssertionError: Rejected invocation must NOT leave a basetemp dir on disk. Found: {'/tmp/gludd-gate-test-xnqacsh1'}
```
`scripts/run_gate.sh` is not cleaning up its basetemp dir when a lock-rejected invocation exits. Race condition or missing `rm -rf` in the rejection path.

**5. `test_tui_e2e.py::test_tui_main_screen_renders` — missing `rich._emoji_codes` (1 test)**

```text
ModuleNotFoundError: No module named 'rich._emoji_codes'
```
A private `rich` module used in the TUI code changed its internal structure. Either pin `rich` to a specific version or import through the public API.

**6. `test_cli_e2e.py` — mock/patch issues (~31 tests)**

```text
test_add_success: AssertionError: Expected 'json' to have been called.
test_*_with_project: TypeError: 'NoneType' object is not subscriptable
test_list_*/test_log_level_*/etc.: assert 1 == 0
```
The mock targets in the e2e CLI tests are patching the wrong path after a refactor, causing patched functions to never be called and return `None`.

**7. `test_self_improve_harness.py::test_integrity_log_uses_handle_connection_error` (1 test)**

```text
AttributeError: 'NoneType' object has no attribute 'get'
```
Mock is returning `None` where a dict is expected; the harness function changed its return contract.

---

## Flaky vs Real Assessment

All 105 failures are **real bugs**, not flaky:
- The failure set is identical between the two independent runs (tag run and master push) and identical between 3.11 and 3.12.
- The `test_connector_proc_sys` path failure is environment-specific but reproducible across all CI runner instances (not timing-dependent).
- No timeout or asyncio teardown failures are in the set.

---

## CI Greenness Context

Last 20 completed runs: **4 GREEN, 16 not-green = 20%** (as of 2026-06-18).

---

## Fix-Forward Priority

1. **Highest: `general_ludd` subprocess import failure** — diagnose why `python3 -m general_ludd.cli` fails in the subprocess path but not in the pytest process itself. Likely the test code spawns the raw venv python without `uv run` or without `-e` install active for subprocess children.
2. **`test_readme_status_gate.py`** — fix `normalize()` to strip leading `v` and fix the infinite recursion in `main()`.
3. **`test_gate_concurrency.py`** — add `rm -rf "$BASETEMP"` in `run_gate.sh`'s lock-rejection exit path.
4. **`test_cli_e2e.py` mock targets** — audit patch paths after CLI refactor.
5. **`rich._emoji_codes`** — pin or replace with public API.
6. **`test_connector_proc_sys.py`** — mock the sysfs path rather than using real hardware paths.
