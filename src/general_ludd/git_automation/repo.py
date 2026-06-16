"""Git automation module for repository management."""

from __future__ import annotations

import logging
import os
import re
import subprocess
from datetime import UTC, datetime

from general_ludd.git_automation.locking import git_repo_lock
from general_ludd.git_automation.types import (
    CloneResult,
    InitResult,
    MergeResult,
    PushResult,
    WorktreeInfo,
    WorktreeResult,
)

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


# git "transport helper" URL schemes that make `git clone` run an ARBITRARY
# command (ext::/git::/fd::). `ext::sh -c ...` is a documented remote-code-exec
# primitive; fd:: and git:: are the same class. We refuse them unconditionally —
# a legitimate https/ssh/git clone never needs them.
_DANGEROUS_CLONE_SCHEMES = ("ext::", "git::", "fd::")


def _reject_dangerous_clone_url(url: str, *, allow_local: bool = True) -> str:
    """Reject a clone URL that is a code-exec / local-disclosure primitive.

    Defense-in-depth at the git wrapper itself (a higher layer may also validate,
    but this layer must never be the weak link). UNCONDITIONALLY rejected (these
    have no legitimate https/ssh/git clone use — they are pure RCE / option
    injection vectors):

    * a value beginning with ``-`` -> git would parse it as an OPTION, not a url
      (option injection, e.g. ``--upload-pack=<cmd>``).
    * ``ext::``/``git::``/``fd::`` transport helpers -> arbitrary command exec.
    * any url carrying an embedded ssh ``-o``/``ProxyCommand`` option ->
      ssh would run an arbitrary command via ProxyCommand.

    Conditionally rejected:

    * ``file://`` -> local filesystem disclosure (clone any local repo). Allowed
      by default (the product materializes trusted local-repo projects via this
      wrapper); pass ``allow_local=False`` for an UNTRUSTED, caller-supplied url
      (the projects router's HTTP-supplied ``repo_url``) to refuse it.

    Returns the stripped url when safe; raises ``ValueError`` otherwise.
    """
    stripped = url.strip()
    if stripped.startswith("-"):
        raise ValueError(
            f"refusing clone url that begins with '-' (option injection, would "
            f"be parsed as a git flag): {url!r}"
        )
    lowered = stripped.lower()
    for scheme in _DANGEROUS_CLONE_SCHEMES:
        if lowered.startswith(scheme):
            raise ValueError(
                f"refusing clone url using the {scheme!r} transport helper "
                f"(arbitrary command execution): {url!r}"
            )
    if lowered.startswith("file://") and not allow_local:
        raise ValueError(
            f"refusing file:// clone url (local filesystem disclosure); pass "
            f"allow_local=True to clone a trusted local repo: {url!r}"
        )
    # An embedded ssh option (-o / ProxyCommand=) anywhere in the url would be
    # handed to ssh and can run an arbitrary command via ProxyCommand. The token
    # appears as `-o`, `-oProxyCommand=...`, or a standalone `ProxyCommand=`.
    if "proxycommand" in lowered or "-o" in stripped.split():
        raise ValueError(
            f"refusing clone url embedding an ssh -o/ProxyCommand option "
            f"(arbitrary command execution): {url!r}"
        )
    return stripped


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


class GitAutomation:
    def __init__(self, repo_path: str = ".") -> None:
        self.repo_path = repo_path

    def _run_git(self, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        env = {**os.environ, **_NON_INTERACTIVE_GIT_ENV}
        # SERIALIZATION (issue #63): gludd runs roles in parallel and many call
        # git against the same repo concurrently, racing on .git/index.lock, on
        # HEAD, and on commits. Every git invocation flows through this choke
        # point, so we hold the per-repo lock for the FULL duration of the
        # subprocess (and the timeout translation below). The lock is re-entrant,
        # so nested/sequential _run_git calls on the same repo from one thread
        # (e.g. commit() doing add+commit+rev-parse) never self-deadlock. The
        # leading-dash guards, clone hardening, and timeout-to-CalledProcessError
        # behavior are unchanged — only the serialization is added.
        with git_repo_lock(self.repo_path):
            return self._run_git_locked(args, check=check, env=env)

    def _run_git_locked(
        self, args: tuple[str, ...], *, check: bool, env: dict[str, str]
    ) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                ["git", *args],
                cwd=self.repo_path,
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

    def reject_force_push(self) -> bool:
        return False

    def get_current_commit(self) -> str:
        result = self._run_git("rev-parse", "HEAD")
        return result.stdout.strip()

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

        Defense-in-depth (#64): the url is screened HERE — even if a higher layer
        validates, this wrapper UNCONDITIONALLY refuses ``ext::``/``git::``/``fd::``
        transport helpers and embedded ssh ``-o``/``ProxyCommand`` (arbitrary
        command exec) and any url beginning with ``-`` (option injection). For an
        UNTRUSTED caller-supplied url, pass ``allow_local=False`` to also refuse
        ``file://`` (local filesystem disclosure). A rejected url returns
        ``success=False`` and NEVER launches git. The argv also inserts ``--``
        before the positional url/path so a url can never be reparsed as a flag.
        """
        try:
            safe_url = _reject_dangerous_clone_url(url, allow_local=allow_local)
        except ValueError as exc:
            return CloneResult(path=os.path.abspath(target_dir), url=url, success=False, message=str(exc))
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
            result = subprocess.run(
                # `--` ends option parsing so neither the url nor the target path
                # can ever be reinterpreted as a git option.
                ["git", "clone", "--", safe_url, target],
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
                ["git", "worktree", "remove", "--", worktree_path],
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
        subprocess.run(
            ["git", "checkout", target, "--"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            check=True,
        )
        # Options first, then `--`, then the source ref positional, so a
        # leading-dash source could never be parsed as a merge option.
        merge_args = ["git", "merge"]
        if strategy == "ff":
            merge_args.append("--ff-only")
        elif strategy == "no-ff":
            merge_args.extend(["--no-ff", "-m", f"Merge {source} into {target}"])
        elif strategy == "squash":
            merge_args.append("--squash")
        merge_args.extend(["--", source])
        result = subprocess.run(
            merge_args,
            cwd=repo_path,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            conflicts = []
            if "CONFLICT" in result.stdout or "CONFLICT" in result.stderr:
                conflicts = [source]
            return MergeResult(success=False, strategy=strategy, message=result.stderr.strip(), conflicts=conflicts)
        if strategy == "squash":
            subprocess.run(
                ["git", "commit", "-m", f"Merge {source} into {target} (squash)"],
                cwd=repo_path,
                capture_output=True,
                text=True,
                check=False,
            )
        return MergeResult(success=True, strategy=strategy, message=result.stdout.strip())

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
        result = subprocess.run(
            args,
            cwd=repo_path,
            capture_output=True,
            text=True,
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
        return f"agent/TODO-{todo_id}/{slug}-{ts}"
