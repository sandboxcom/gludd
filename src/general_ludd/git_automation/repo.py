"""Git automation module for repository management."""

from __future__ import annotations

import logging
import os
import re
import subprocess
import uuid
from datetime import UTC, datetime
from urllib.parse import urlsplit

from general_ludd.git_automation.locking import git_repo_lock
from general_ludd.git_automation.types import (
    CloneResult,
    GatedCommitResult,
    InitResult,
    MergeResult,
    PushResult,
    WorktreeInfo,
    WorktreeResult,
)
from general_ludd.security.ssrf import resolved_host_is_blocked

logger = logging.getLogger(__name__)

# Bound EVERY git subprocess so a slow/unreachable remote or a credential prompt
# can never hang the caller forever (the daemon awaits commit/push inside a tick).
_GIT_TIMEOUT_SECONDS = 60.0

# Non-interactive git environment: GIT_TERMINAL_PROMPT=0 makes git fail instead
# of blocking on a username/password TTY prompt; GIT_ASKPASS=echo neutralises any
# credential-helper prompt (mirrors what clone() already sets).
_NON_INTERACTIVE_GIT_ENV = {"GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "echo"}

_FORCE_PUSH_PATTERN = re.compile(
    r"\s+(-f\s+|--force\b|--force-with-lease\b)"
)


def _reject_leading_dash(value: str, *, kind: str) -> str:
    """Reject a ref/path value that begins with ``-``.

    A leading-dash value (e.g. ``--upload-pack=...``, ``--exec=...``,
    ``--receive-pack=...``, ``--no-verify``) would otherwise be parsed by git
    as an OPTION rather than a ref/path positional. We refuse it before exec.
    Callers must additionally place a ``--`` end-of-options separator before
    the positional so a value can never be reinterpreted as an option.
    """
    if value.startswith("-"):
        raise ValueError(
            f"refusing {kind} that begins with '-' (would be parsed as a git "
            f"option, not a ref/path): {value!r}"
        )
    return value


# Only these transports may be cloned. file:// and ext:: (and any other local /
# smart-transport scheme) are excluded: ext::sh -c '<cmd>' is arbitrary command
# execution at clone time (RCE), and file:// lets a request read/copy arbitrary
# local paths. ssh/git/https(/http) all go over a network socket to a host we can
# vet for SSRF below.
_ALLOWED_REPO_SCHEMES = frozenset({"https", "http", "git", "ssh"})

def _host_is_blocked(host: str) -> bool:
    """True if ``host`` is (or resolves to) an address we refuse to clone from.

    Blocks loopback, link-local (incl. 169.254.169.254 / 100.100.100.200
    cloud-metadata), RFC-1918 / unique-local private ranges, CGNAT/TEST-NET
    ranges, and unspecified/reserved addresses. Delegates entirely to the
    canonical :func:`general_ludd.security.ssrf.resolved_host_is_blocked`,
    which resolves a non-literal hostname to its A/AAAA records (bounded,
    fail-closed) and vets EVERY resolved address, so a name that points at an
    internal IP cannot smuggle past a literal check — matching this
    function's prior DNS-resolving behavior. This also closes gaps the old
    local blocklist had: the ``not is_global`` catch for CGNAT/TEST-NET
    ranges, the Alibaba metadata IP, and the ``metadata.goog``/
    ``instance-data``/``ip6-*`` name aliases.
    """
    return resolved_host_is_blocked(host)


def reject_unsafe_repo_url(url: str) -> str:
    """Validate ``url`` is a safe remote to ``git clone``; return it or raise.

    Defends the unauthenticated workspace-materialization path against:
      * RCE via git smart transports — ``ext::sh -c ...`` / ``transport::addr``
        run arbitrary commands at clone time. Any ``::`` is refused outright.
      * RCE/local-file exfiltration via ``file://`` (and bare local clones).
      * SSRF — an http(s)/git/ssh URL whose host is loopback, link-local
        (169.254.169.254 metadata), or an RFC-1918 private address.
      * Option injection — a leading-dash URL parsed by git as a flag.

    Callers must STILL place a ``--`` end-of-options separator before the URL.
    """
    if not isinstance(url, str) or not url.strip():
        raise ValueError("refusing empty repo url")
    raw = url.strip()
    _reject_leading_dash(raw, kind="repo url")
    # `::` is git's smart-transport separator (ext::, transport::addr). It never
    # appears in a legitimate http/ssh/git URL and is the primary RCE primitive.
    if "::" in raw:
        raise ValueError(f"refusing repo url containing '::' (git smart transport / RCE): {url!r}")

    parts = urlsplit(raw)
    scheme = parts.scheme.lower()

    # scp-like syntax (git@github.com:org/repo) has no '://' scheme. Accept only
    # the user@host:path shape and SSRF-check its host; anything else is refused.
    if not scheme:
        m = re.match(r"^[A-Za-z0-9._%+-]+@([A-Za-z0-9._-]+):", raw)
        if not m:
            raise ValueError(f"refusing repo url with no recognized transport scheme: {url!r}")
        host = m.group(1)
        if _host_is_blocked(host):
            raise ValueError(f"refusing repo url whose host is internal/blocked (SSRF): {url!r}")
        return raw

    if scheme not in _ALLOWED_REPO_SCHEMES:
        raise ValueError(
            f"refusing repo url scheme {scheme!r} (allowed: "
            f"{sorted(_ALLOWED_REPO_SCHEMES)}): {url!r}"
        )
    host = parts.hostname or ""
    if not host or _host_is_blocked(host):
        raise ValueError(f"refusing repo url whose host is internal/blocked (SSRF): {url!r}")
    return raw


_PROXYCOMMAND_RE = re.compile(r"[- ]o\s*ProxyCommand\s*=", re.IGNORECASE)


def _reject_clone_url(url: str, *, allow_local: bool = True) -> str:
    """Pre-flight safety check for ``clone()``; raises ``ValueError`` if unsafe.

    This is a defence-in-depth layer ABOVE ``reject_unsafe_repo_url``. It
    catches the unconditional RCE / option-injection primitives WITHOUT relying
    on the scheme allowlist or SSRF check (which requires parsing):

    * Leading-dash: would be parsed by git as a CLI option (option injection).
    * ``::`` separator: git smart transports (``ext::``, ``git::``, ``fd::``)
      run arbitrary commands or open file-descriptors at clone time (RCE).
    * ``-oProxyCommand=`` / ``ProxyCommand=`` patterns in any part of the URL:
      ssh passes these through to the ProxyCommand and executes them (RCE).
    * ``file://`` (and ``FILE://``) when ``allow_local=False``: local filesystem
      disclosure — a caller can clone any local repo the process can read.

    Returns the URL unchanged when all checks pass.
    """
    if not isinstance(url, str) or not url.strip():
        raise ValueError("refusing empty clone url")
    raw = url.strip()

    # Leading dash: option injection unconditionally.
    if raw.startswith("-"):
        raise ValueError(
            f"refusing clone url beginning with '-' (option injection): {url!r}"
        )

    # '::' is git's smart-transport separator; ext::, git::, fd:: are RCE.
    if "::" in raw:
        raise ValueError(
            f"refusing clone url containing '::' (git smart transport / RCE): {url!r}"
        )

    # ProxyCommand injection via ssh options embedded in the URL.
    if _PROXYCOMMAND_RE.search(raw):
        raise ValueError(
            f"refusing clone url containing ProxyCommand pattern (RCE): {url!r}"
        )

    # file:// is local-filesystem disclosure; reject when caller signals untrusted.
    if not allow_local and raw.lower().startswith("file:"):
        raise ValueError(
            f"refusing file:// clone url (local filesystem disclosure): {url!r}"
        )

    return raw


class GitAutomation:
    def __init__(self, repo_path: str = ".") -> None:
        self.repo_path = repo_path

    def _run_git(
        self, *args: str, check: bool = True, _cwd: str | None = None
    ) -> subprocess.CompletedProcess[str]:
        cwd = _cwd if _cwd is not None else self.repo_path
        env = {**os.environ, **_NON_INTERACTIVE_GIT_ENV}
        # Serialize every git invocation per-repo (#63) so concurrent roles/threads
        # cannot collide on .git/index.lock. The lock is re-entrant, so nested
        # _run_git calls in one thread (e.g. add -> commit -> rev-parse) acquire it
        # cheaply without self-deadlocking.
        with git_repo_lock(cwd):
            try:
                return subprocess.run(
                    ["git", *args],
                    cwd=cwd,
                    capture_output=True,
                    text=True,
                    check=check,
                    timeout=_GIT_TIMEOUT_SECONDS,
                    env=env,
                )
            except subprocess.TimeoutExpired as exc:
                # A hung git (unreachable remote / credential prompt) must surface as a
                # CLEAN failure, never as a daemon-stalling crash. We translate the
                # timeout into a CalledProcessError so every existing caller that
                # already handles git failure (commit/push catch CalledProcessError,
                # is_repo() catches it) fails closed instead of propagating a hang.
                logger.error("git %s timed out after %ss", args[0] if args else "?", _GIT_TIMEOUT_SECONDS)
                raise subprocess.CalledProcessError(
                    returncode=124,
                    cmd=["git", *args],
                    output=exc.stdout if isinstance(exc.stdout, str) else "",
                    stderr=(
                        exc.stderr if isinstance(exc.stderr, str)
                        else f"git timed out after {_GIT_TIMEOUT_SECONDS}s"
                    ),
                ) from exc

    def init_repo(self, path: str | None = None) -> InitResult:
        target = path or self.repo_path
        git_dir = os.path.join(target, ".git")
        created = not os.path.isdir(git_dir)
        subprocess.run(
            ["git", "init"],
            cwd=target,
            capture_output=True,
            text=True,
            check=True,
        )
        for cmd in (
            ["git", "config", "user.email", "agent@harness.local"],
            ["git", "config", "user.name", "Agentic Harness Agent"],
        ):
            subprocess.run(cmd, cwd=target, capture_output=True, text=True, check=False)
        return InitResult(path=target, created=created, message="initialized" if created else "already exists")

    def is_repo(self) -> bool:
        try:
            self._run_git("rev-parse", "--git-dir")
            return True
        except (subprocess.CalledProcessError, FileNotFoundError, OSError):
            return False

    def current_branch(self) -> str:
        """Return the current branch name, or ``'unknown'`` on any failure.

        Uses ``rev-parse --abbrev-ref HEAD`` (same command the engine used
        inline before delegation). Failures — timeout, not-a-repo, detached
        HEAD — all surface as ``'unknown'`` so callers never see an exception.
        """
        try:
            result = self._run_git("rev-parse", "--abbrev-ref", "HEAD")
            return result.stdout.strip()
        except Exception:
            return "unknown"

    def create_branch(self, name: str) -> str:
        _reject_leading_dash(name, kind="branch name")
        # `--` ends option parsing so the name is unambiguously the new branch.
        self._run_git("checkout", "-b", name, "--")
        return name

    def commit(self, message: str) -> str:
        self._run_git("add", "-A")
        self._run_git("commit", "-m", message)
        result = self._run_git("rev-parse", "HEAD")
        return result.stdout.strip()

    def tag_release(self, tag: str) -> str:
        self._run_git("tag", "-a", tag, "-m", f"Release {tag}")
        return tag

    def tag_checkpoint(self, tag: str) -> str:
        self._run_git("tag", tag)
        return tag

    def push(self, remote: str = "origin", branch: str = "main") -> bool:
        _reject_leading_dash(remote, kind="remote name")
        _reject_leading_dash(branch, kind="branch name")
        try:
            # `--` separates the remote/refspec positionals from any options.
            self._run_git("push", remote, "--", branch)
            return True
        except subprocess.CalledProcessError:
            logger.error("Push failed")
            return False

    def commit_and_push(
        self, message: str, remote: str = "origin", branch: str = "main"
    ) -> str:
        """Commit all changes and push to remote; return the new commit SHA.

        A push failure is logged via ``push`` returning ``False`` but does NOT
        raise — the local commit SHA is still returned so the caller (the
        self-improvement hot-reload path) can reload from the local commit.
        The remote will be reconciled on a later push.
        """
        sha = self.commit(message)
        self.push(remote=remote, branch=branch)
        return sha

    def remote_url(self, remote: str = "origin") -> str:
        """Return the configured URL for ``remote`` (default origin).

        Best-effort read: returns ``""`` on any failure (not a repo, no such
        remote, git missing, timeout) rather than raising, so callers can use
        it for detection without try/except noise.
        """
        _reject_leading_dash(remote, kind="remote name")
        proc = self._run_git("remote", "get-url", remote, check=False)
        if proc.returncode != 0:
            return ""
        return proc.stdout.strip()

    def reject_force_push(self) -> bool:
        return False

    def get_current_commit(self) -> str:
        result = self._run_git("rev-parse", "HEAD")
        return result.stdout.strip()

    def lines_changed_in_commit(self, ref: str = "HEAD") -> int:
        """Sum added+deleted lines introduced by the commit at ``ref`` (default HEAD).

        Runs ``git show --numstat --format= <ref>`` and sums the added+deleted
        columns across all files, skipping binary rows (``-`` markers). Used by
        the accounting ledger to record a per-commit lines-of-code delta right
        after :meth:`commit`. Fail-safe: any error (not a repo, no such ref, git
        missing, timeout) returns 0 and never raises — a LOC count must never
        abort a commit/push flow.
        """
        _reject_leading_dash(ref, kind="commit ref")
        try:
            proc = self._run_git("show", "--numstat", "--format=", ref, check=False)
        except (subprocess.CalledProcessError, FileNotFoundError, OSError):
            return 0
        if proc.returncode != 0:
            return 0
        total = 0
        for line in proc.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) < 3:
                continue
            added, deleted = parts[0], parts[1]
            # Binary files report "-" for added/deleted; skip them.
            if added == "-" or deleted == "-":
                continue
            try:
                total += int(added) + int(deleted)
            except ValueError:
                continue
        return total

    def changed_files(self) -> list[str]:
        """Return the repo-relative paths with uncommitted changes (porcelain).

        Runs ``git status --porcelain`` and parses the path column of each entry,
        so the result is the set of files this worktree is about to commit —
        added, modified, deleted, or renamed (the post-rename path is returned).
        Used by the event loop's git-delivery path to discover a todo's affected
        files at commit time (they are unknown earlier — the model has not yet
        produced a diff at dispatch). Fail-safe: any error (not a repo, git
        missing, timeout) returns ``[]`` and never raises — file-claim
        coordination must never abort a commit/push flow.
        """
        try:
            proc = self._run_git("status", "--porcelain", check=False)
        except (subprocess.CalledProcessError, FileNotFoundError, OSError):
            return []
        if proc.returncode != 0:
            return []
        paths: list[str] = []
        for line in proc.stdout.splitlines():
            # Porcelain v1 lines are "XY <path>" (XY = 2 status chars + a space).
            # Strip those 3 leading chars; a too-short line is skipped.
            if len(line) < 4:
                continue
            entry = line[3:].strip()
            if not entry:
                continue
            # Renames/copies report "old -> new"; keep the destination path.
            if " -> " in entry:
                entry = entry.split(" -> ", 1)[1].strip()
            # git quotes paths with special chars in double quotes; unwrap them.
            if len(entry) >= 2 and entry[0] == '"' and entry[-1] == '"':
                entry = entry[1:-1]
            if entry:
                paths.append(entry)
        return paths

    def clone(
        self,
        url: str,
        target_dir: str,
        timeout: float = 120.0,
        *,
        allow_local: bool = True,
    ) -> CloneResult:
        """Clone ``url`` into ``target_dir``.

        Idempotent: if ``target_dir`` already contains a git checkout, this is a
        no-op success (we never re-clone over existing work). Failures (bad URL,
        unreachable remote, timeout) return ``success=False`` rather than raising,
        so callers can fail closed without try/except noise. A bounded ``timeout``
        and a non-interactive environment guarantee this never blocks the daemon
        (e.g. on a credential prompt for a private/unreachable remote).

        ``allow_local`` controls whether ``file://`` URLs are permitted (default
        True for intra-repo tooling; set False for untrusted caller-supplied
        URLs to prevent local filesystem disclosure).

        SECURITY: Unconditionally rejects URLs that are arbitrary-command-execution
        / option-injection primitives before ``subprocess.run`` is ever called:
          * ``::``-containing URLs (git smart transports: ``ext::``, ``git::``,
            ``fd::`` run shell commands or open file descriptors at clone time).
          * Leading-dash URLs (parsed as git options, not as a remote address).
          * ``-o``/``ProxyCommand`` in ssh URLs (ssh executes the proxy command).
          * ``file://`` when ``allow_local=False``.
        """
        # --- pre-flight safety checks (fail BEFORE forking subprocess) ---
        try:
            _reject_clone_url(url, allow_local=allow_local)
        except ValueError as exc:
            return CloneResult(
                path=os.path.abspath(target_dir),
                url=url,
                success=False,
                message=str(exc),
            )

        target = os.path.abspath(target_dir)
        if os.path.isdir(os.path.join(target, ".git")):
            return CloneResult(
                path=target, url=url, success=True, already_present=True,
                message="checkout already present",
            )
        parent = os.path.dirname(target) or "."
        os.makedirs(parent, exist_ok=True)
        env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "echo"}
        try:
            # `--` ends option parsing so a leading-dash url can never be parsed
            # as a git option (defense in depth; reject_unsafe_repo_url already
            # refuses one upstream).
            result = subprocess.run(
                ["git", "clone", "--", url, target],
                capture_output=True,
                text=True,
                timeout=timeout,
                env=env,
            )
        except subprocess.TimeoutExpired:
            return CloneResult(
                path=target, url=url, success=False,
                message=f"clone timed out after {timeout}s",
            )
        if result.returncode != 0:
            return CloneResult(
                path=target, url=url, success=False,
                message=result.stderr.strip() or result.stdout.strip() or "clone failed",
            )
        return CloneResult(path=target, url=url, success=True)

    def create_worktree(self, repo_path: str, branch_name: str, worktree_path: str) -> WorktreeResult:
        # Fail closed on dash-leading branch/path (would be parsed as a git
        # option) and on a path that escapes the repo parent via `..`.
        try:
            _reject_leading_dash(branch_name, kind="branch name")
            _reject_leading_dash(worktree_path, kind="worktree path")
            self._reject_escaping_path(repo_path, worktree_path)
        except ValueError as exc:
            return WorktreeResult(
                path=worktree_path, branch=branch_name, success=False, message=str(exc),
            )
        try:
            subprocess.run(
                # `-b <branch>` then `--` then the path positional: the path can
                # never be reinterpreted as an option.
                ["git", "worktree", "add", "-b", branch_name, "--", worktree_path, "HEAD"],
                cwd=repo_path,
                capture_output=True,
                text=True,
                check=True,
            )
            return WorktreeResult(path=worktree_path, branch=branch_name, success=True)
        except subprocess.CalledProcessError as exc:
            return WorktreeResult(
                path=worktree_path,
                branch=branch_name,
                success=False,
                message=exc.stderr.strip() if exc.stderr else str(exc),
            )

    @staticmethod
    def _reject_escaping_path(repo_path: str, worktree_path: str) -> None:
        """Reject a worktree path that escapes the repo's parent directory.

        Worktrees are expected to live beside the repo (or under it). A path
        containing ``..`` that resolves above the repo parent is refused so a
        traversal value cannot plant a worktree in an arbitrary location.
        """
        # A `..` component is the traversal primitive that escapes the intended
        # area; refuse it outright (a legitimate worktree path never needs one).
        norm = os.path.normpath(worktree_path)
        parts = norm.replace("\\", "/").split("/")
        if ".." in parts:
            raise ValueError(
                f"refusing worktree path containing '..' traversal: {worktree_path!r}"
            )
        repo_abs = os.path.abspath(repo_path)
        parent = os.path.dirname(repo_abs) or os.sep
        if os.path.isabs(worktree_path):
            target = os.path.abspath(worktree_path)
        else:
            target = os.path.abspath(os.path.join(repo_abs, worktree_path))
        # Allowed roots: the repo itself or its immediate parent directory.
        for root in (repo_abs, parent):
            root_prefix = root.rstrip(os.sep) + os.sep
            if target == root or target.startswith(root_prefix):
                return
        raise ValueError(
            f"refusing worktree path that escapes the repo parent: {worktree_path!r}"
        )

    def remove_worktree(self, repo_path: str, worktree_path: str) -> bool:
        _reject_leading_dash(worktree_path, kind="worktree path")
        try:
            subprocess.run(
                # `--force`: a worktree being torn down by the orchestrator
                # legitimately contains untracked/modified content — the
                # gludd_worktree module plants an audit marker inside the
                # worktree, and an agent worktree typically has WIP. The
                # caller has already decided the worktree is done; --force
                # discards only uncommitted state (the branch is retained).
                ["git", "worktree", "remove", "--force", "--", worktree_path],
                cwd=repo_path,
                capture_output=True,
                text=True,
                check=True,
            )
            return True
        except subprocess.CalledProcessError:
            return False

    def list_worktrees(self, repo_path: str) -> list[WorktreeInfo]:
        result = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            check=True,
        )
        worktrees: list[WorktreeInfo] = []
        current: dict[str, str] = {}
        for line in result.stdout.splitlines():
            if line.startswith("worktree "):
                current["path"] = line[len("worktree "):]
            elif line.startswith("branch "):
                current["branch"] = line[len("branch "):]
            elif line.startswith("HEAD "):
                current["commit"] = line[len("HEAD "):]
            elif line == "":
                if "path" in current:
                    worktrees.append(
                        WorktreeInfo(
                            path=current.get("path", ""),
                            branch=current.get("branch", ""),
                            commit=current.get("commit", ""),
                        )
                    )
                current = {}
        if "path" in current:
            worktrees.append(
                WorktreeInfo(
                    path=current.get("path", ""),
                    branch=current.get("branch", ""),
                    commit=current.get("commit", ""),
                )
            )
        return worktrees

    def merge_branch(self, repo_path: str, source: str, target: str, strategy: str = "ff") -> MergeResult:
        _reject_leading_dash(source, kind="merge source ref")
        _reject_leading_dash(target, kind="merge target ref")
        # `git checkout <branch> --` ends option parsing with `--` AFTER the
        # branch (the only checkout form that both switches branches and is
        # option-safe; `git checkout -- <x>` would treat <x> as a pathspec).
        # `target` is also already rejected if it leads with `-`.
        self._run_git("checkout", target, "--", _cwd=repo_path)
        # Options first, then `--`, then the source ref positional, so a
        # leading-dash source could never be parsed as a merge option.
        merge_args = ["merge"]
        if strategy == "ff":
            merge_args.append("--ff-only")
        elif strategy == "no-ff":
            merge_args.extend(["--no-ff", "-m", f"Merge {source} into {target}"])
        elif strategy == "squash":
            merge_args.append("--squash")
        merge_args.extend(["--", source])
        result = self._run_git(*merge_args, check=False, _cwd=repo_path)
        if result.returncode != 0:
            conflicts = []
            if "CONFLICT" in result.stdout or "CONFLICT" in result.stderr:
                conflicts = [source]
            return MergeResult(success=False, strategy=strategy, message=result.stderr.strip(), conflicts=conflicts)
        if strategy == "squash":
            # Fail-closed on squash commit error (finding #12): ``check=True`` makes
            # ``_run_git`` raise ``CalledProcessError`` on a non-zero exit, which we
            # translate into a failure result. The returncode fallback below catches
            # the case where the caller mocks ``subprocess.run`` (a mock does not
            # implement subprocess's check logic), so BOTH the real-execution and
            # the mocked paths are fail-closed — a failed squash commit can never
            # surface as ``success=True``.
            try:
                squash_result = self._run_git(
                    "commit", "-m", f"Merge {source} into {target} (squash)",
                    check=True, _cwd=repo_path,
                )
            except subprocess.CalledProcessError as exc:
                return MergeResult(
                    success=False,
                    strategy=strategy,
                    message=(exc.stderr or "").strip() or "squash commit failed",
                    conflicts=[],
                )
            if squash_result.returncode != 0:
                return MergeResult(
                    success=False,
                    strategy=strategy,
                    message=(squash_result.stderr or squash_result.stdout).strip(),
                    conflicts=[],
                )
        return MergeResult(success=True, strategy=strategy, message=result.stdout.strip())

    def gated_commit(
        self, files: list[str], message: str, gate_cmd: list[str]
    ) -> GatedCommitResult:
        """Stage ``files``, run ``gate_cmd``, and commit ONLY if the gate passes.

        The git operations go through :meth:`_run_git` (timeout + non-interactive
        env); the caller-supplied ``gate_cmd`` (e.g. ``["make", "gate"]``) runs via
        a raw ``subprocess.run`` with the same non-interactive env + timeout so a
        misbehaving gate cannot hang the caller. Fail-closed: every path returns a
        :class:`GatedCommitResult`, and no commit happens unless the gate returned 0
        (this is the portable primitive behind the non-blocking gated git workflow —
        the gate is NOT a rubber stamp).
        """
        try:
            for f in files:
                self._run_git("add", "--", f)
        except subprocess.CalledProcessError as exc:
            return GatedCommitResult(
                success=False,
                gate_returncode=exc.returncode,
                message=f"failed to stage files: {(exc.stderr or '').strip()}",
            )
        try:
            gate = subprocess.run(
                gate_cmd,
                cwd=self.repo_path,
                capture_output=True,
                text=True,
                check=False,
                timeout=_GIT_TIMEOUT_SECONDS,
                env={**os.environ, **_NON_INTERACTIVE_GIT_ENV},
            )
        except subprocess.TimeoutExpired:
            return GatedCommitResult(
                success=False, gate_returncode=124, message="gate command timed out"
            )
        if gate.returncode != 0:
            return GatedCommitResult(
                success=False,
                gate_returncode=gate.returncode,
                message=(gate.stderr or gate.stdout or "gate command failed").strip(),
            )
        try:
            self._run_git("commit", "-m", message)
            sha = self._run_git("rev-parse", "HEAD").stdout.strip()
        except subprocess.CalledProcessError as exc:
            return GatedCommitResult(
                success=False,
                gate_returncode=0,
                message=f"commit failed after gate passed: {(exc.stderr or '').strip()}",
            )
        return GatedCommitResult(
            success=True, commit_sha=sha, gate_returncode=0, message="committed"
        )

    def gated_merge(
        self, source: str, target: str, gate_cmd: list[str], strategy: str = "ff"
    ) -> GatedCommitResult:
        """Merge ``source`` into ``target`` and keep it ONLY if ``gate_cmd`` passes.

        The gate validates the MERGED tree, so the merge is applied first and then
        rolled back on gate failure/timeout. Rollback uses ``git reset --hard`` to
        the captured pre-merge HEAD — a completed fast-forward merge leaves no
        merge-in-progress for ``git merge --abort`` to undo, so abort alone would be
        fail-OPEN. Fail-closed: a failed gate leaves ``target`` exactly at pre_sha.
        """
        _reject_leading_dash(source, kind="merge source ref")
        _reject_leading_dash(target, kind="merge target ref")
        try:
            self._run_git("checkout", target, "--")
            pre_sha = self._run_git("rev-parse", "HEAD").stdout.strip()
        except subprocess.CalledProcessError as exc:
            return GatedCommitResult(
                success=False,
                message=f"failed to checkout target {target!r}: {(exc.stderr or '').strip()}",
            )
        merge_args = ["merge"]
        if strategy == "ff":
            merge_args.append("--ff-only")
        elif strategy == "no-ff":
            merge_args.extend(["--no-ff", "-m", f"Merge {source} into {target}"])
        elif strategy == "squash":
            merge_args.append("--squash")
        merge_args.extend(["--", source])
        merge = self._run_git(*merge_args, check=False)
        if merge.returncode != 0:
            # Conflict / non-ff: clean up any in-progress merge and restore HEAD.
            self._run_git("merge", "--abort", check=False)
            self._run_git("reset", "--hard", pre_sha, check=False)
            return GatedCommitResult(
                success=False,
                gate_returncode=merge.returncode,
                message=(merge.stderr or "merge failed").strip(),
            )
        if strategy == "squash":
            try:
                self._run_git("commit", "-m", f"Merge {source} into {target} (squash)", check=True)
            except subprocess.CalledProcessError as exc:
                self._run_git("reset", "--hard", pre_sha, check=False)
                return GatedCommitResult(
                    success=False,
                    message=(exc.stderr or "").strip() or "squash commit failed (rolled back)",
                )
        # The merge is applied (HEAD moved). Gate the merged tree; roll back on fail.
        try:
            gate = subprocess.run(
                gate_cmd,
                cwd=self.repo_path,
                capture_output=True,
                text=True,
                check=False,
                timeout=_GIT_TIMEOUT_SECONDS,
                env={**os.environ, **_NON_INTERACTIVE_GIT_ENV},
            )
        except subprocess.TimeoutExpired:
            self._run_git("reset", "--hard", pre_sha, check=False)
            return GatedCommitResult(
                success=False, gate_returncode=124, message="gate command timed out (rolled back)"
            )
        if gate.returncode != 0:
            self._run_git("reset", "--hard", pre_sha, check=False)
            return GatedCommitResult(
                success=False,
                gate_returncode=gate.returncode,
                message=(gate.stderr or gate.stdout or "gate command failed").strip() + " (rolled back)",
            )
        sha = self._run_git("rev-parse", "HEAD").stdout.strip()
        return GatedCommitResult(
            success=True, commit_sha=sha, gate_returncode=0, message="merged"
        )

    def create_release_tag(self, repo_path: str, fmt: str = "YYYYMMDDHHMMSS") -> str:
        now = datetime.now(tz=UTC)
        tag = now.strftime("%Y%m%d%H%M%S")
        subprocess.run(
            ["git", "tag", "-a", tag, "-m", f"Release {tag}"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            check=True,
        )
        return tag

    def create_checkpoint_tag(self, repo_path: str, todo_id: str, sha: str) -> str:
        ts = datetime.now(tz=UTC).strftime("%Y%m%d%H%M%S")
        short_sha = sha[:7]
        tag = f"agent/{todo_id}/{ts}/{short_sha}"
        subprocess.run(
            ["git", "tag", tag],
            cwd=repo_path,
            capture_output=True,
            text=True,
            check=True,
        )
        return tag

    def push_to_remote(self, repo_path: str, remote: str = "origin", branch: str | None = None) -> PushResult:
        _reject_leading_dash(remote, kind="remote name")
        if branch:
            _reject_leading_dash(branch, kind="branch name")
        # `--` ends option parsing before the refspec positional.
        args = ["git", "push", remote, "--"]
        if branch:
            args.append(branch)
        # GA-1 (real gap): push does network I/O. Without a timeout it can hang
        # the daemon indefinitely on an unresponsive remote — the exact failure
        # `_run_git` was built to prevent. Mirror its timeout + non-interactive
        # env (this method takes an explicit repo_path, so it cannot route
        # through `_run_git`, which is pinned to self.repo_path).
        try:
            result = subprocess.run(
                args,
                cwd=repo_path,
                capture_output=True,
                text=True,
                timeout=_GIT_TIMEOUT_SECONDS,
                env={**os.environ, **_NON_INTERACTIVE_GIT_ENV},
            )
        except subprocess.TimeoutExpired:
            return PushResult(
                success=False,
                remote=remote,
                branch=branch or "",
                message=f"push timed out after {_GIT_TIMEOUT_SECONDS}s",
            )
        return PushResult(
            success=result.returncode == 0,
            remote=remote,
            branch=branch or "",
            message=result.stderr.strip() if result.stderr else result.stdout.strip(),
        )

    def create_local_bare_mirror(self, repo_path: str, mirror_path: str) -> str:
        subprocess.run(
            ["git", "clone", "--bare", repo_path, mirror_path],
            capture_output=True,
            text=True,
            check=True,
        )
        return mirror_path

    @staticmethod
    def is_force_push(command: str) -> bool:
        return bool(_FORCE_PUSH_PATTERN.search(command))

    @staticmethod
    def generate_branch_name(todo_id: str, slug: str) -> str:
        ts = datetime.now(tz=UTC).strftime("%Y%m%d%H%M%S")
        uid = uuid.uuid4().hex[:8]
        return f"agent/TODO-{todo_id}/{slug}-{ts}-{uid}"
