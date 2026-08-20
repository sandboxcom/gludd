# Sandbox ZDD Lifecycle and SELinux Emission

Status: implemented for the beta4 sandbox hardening pass. Last reviewed:
2026-08-20.

## Safety Contract

Sandbox lifecycle operations fail closed and keep cleanup scoped to the exact
project namespace. A failed allocation cannot weaken isolation, and a cleanup
failure cannot authorize recursive deletion outside that namespace.

The host-state lifecycle is:

1. Resolve an existing project root and a canonical, absolute state base.
2. Reject `..`, caller-created symlink components, wrong ownership, and modes
   broader than `0700` before accepting the namespace.
3. Create the base and deterministic project namespace with owner-only modes.
4. If namespace allocation fails, remove only newly created, caller-owned,
   empty directories. A non-empty, replaced, symlinked, or differently owned
   path is preserved as failure evidence instead of being recursively deleted.
5. During normal teardown, reject every symlink in the target tree and verify
   canonical containment before calling `unlink` or `shutil.rmtree`.

Unsafe identifiers are converted to a readable slug plus a deterministic
BLAKE2 digest. This avoids traversal and collisions without preserving hostile
leading punctuation. Empty identifiers receive an `item-` prefix.

## Ornith Process Lifecycle

`ornith_sandboxed_run` owns one namespaced temporary directory through a context
manager. Environment overrides are merged into a copy of the parent environment,
and memory/CPU overrides are bound into the child-only pre-exec callback. The
parent process never changes its working directory.

The subprocess timeout contract retains partial standard output and error.
`subprocess.TimeoutExpired` may expose bytes even when `text=True`, so both byte
streams are decoded as UTF-8 with replacement rather than silently discarded.
The result remains fail-closed with return code `-1`, and context teardown still
runs before the result is returned.

An allocated path outside the state's project namespace is never cleaned by a
fallback `rmtree`. Cleanup raises, leaves the external path untouched, and keeps
the sandbox marked uncleaned so a controlled retry remains possible.

## Resource-Limit Translation

The PID value `0` is a boundary sentinel rather than a literal container limit:

- Docker arguments omit `--pids-limit` for `0`, preserving Docker's unlimited
  sentinel semantics.
- Converting that external value into `SandboxConfig` restores the internal
  default of 50 processes. This avoids turning an unlimited external sentinel
  into an accidentally unbounded process-backend policy.
- Positive values remain unchanged across both representations.

## Canonical SELinux Output

Type-enforcement and file-context lines are modeled as semantic sets, then
sorted before rendering. Equivalent capabilities therefore emit one rule even
when they arrive in a different order or repeat with different action-set order.
Distinct file paths retain distinct file-context labels, while their shared
type-enforcement permissions are emitted once. This makes policy artifacts
stable, reviewable, and safe to rebuild during zero-downtime replacement.

SELinux installation continues to stage policy artifacts in private project
state. Compiler or installer failure returns an unapplied handle and immediately
removes only that confined build directory. Release removes the installed module
when possible and then performs the same confined state cleanup.

## Upstream Practitioner Evidence

The following upstream user reports were reviewed on 2026-08-20. They motivate
the defensive contracts above; they do not imply that each upstream bug is
present in Gludd.

- CPython issue
  [#87597](https://github.com/python/cpython/issues/87597), opened 2021-03-08,
  reports that timeout output can remain bytes in text mode. Ornith normalizes
  both supported stream types rather than dropping the user's partial result.
- CPython issue
  [#79325](https://github.com/python/cpython/issues/79325), opened 2018-11-02,
  documents `TemporaryDirectory` cleanup failures for directories whose
  permissions prevent traversal. Gludd surfaces cleanup failure and retains the
  unclean state instead of declaring teardown complete.
- CPython issue
  [#65308](https://github.com/python/cpython/issues/65308), opened 2014-03-31,
  records traversal through absolute names, `..`, and symlinked files or
  directories. Gludd rejects those path forms before recursive cleanup.
- CPython issue
  [#142916](https://github.com/python/cpython/issues/142916), opened 2025-12-18,
  demonstrates a `Path.mkdir` time-of-check/time-of-use race when another actor
  removes a directory between checks. Gludd's allocation rollback therefore
  uses owner checks plus non-recursive `rmdir`; any changed or non-empty path is
  preserved rather than guessed safe.

## Regression Evidence

Focused tests pin zero-PID translation, deterministic state names, configured
symlink rejection, allocation rollback, timeout partial output, environment and
resource overrides, cleanup confinement, and canonical SELinux TE/FC rules.
Tests inject rollback and timeout failures without depending on a live SELinux
host or an unbounded subprocess.

