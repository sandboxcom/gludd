"""Git automation module for repository management.

git subprocess calls can route through ansible-runner (invoking the
``general_ludd.agent.git_automation`` role) instead of ``subprocess.run``
directly.  SSRF validation and path-safety checks ALWAYS run in Python
(hard-gated BEFORE ansible is invoked).
"""

from __future__ import annotations

import fnmatch
import json
import logging
import os
import re
import subprocess
import tempfile
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from general_ludd.git_automation.locking import git_repo_lock
from general_ludd.git_automation.types import (
    CloneResult,
    GatedCommitResult,
    GitStateResult,
    InitResult,
    MergeResult,
    PushResult,
    VerifyRemoteResult,
    WorktreeInfo,
    WorktreeResult,
)
from general_ludd.git_automation.verify_remote import verify_remote as _verify_remote_fn
from general_ludd.security.ssrf import resolved_host_is_blocked
from general_ludd.security.state import SecureStateError, project_state

logger = logging.getLogger(__name__)

# ── ansible-runner availability ──────────────────────────────────────────────
try:
    import ansible_runner
    _HAS_ANSIBLE_RUNNER = True
except ImportError:
    ansible_runner = None
    _HAS_ANSIBLE_RUNNER = False

# Path to the role directory relative to this project root.  We resolve it at
# import time so the role is discoverable regardless of CWD.
_ROLE_DIR = (
    Path(__file__).resolve().parent.parent.parent.parent
    / "collections" / "ansible_collections" / "general_ludd" / "agent"
    / "roles" / "git_automation"
)
_COLLECTIONS_ROOT = (
    Path(__file__).resolve().parent.parent.parent.parent / "collections"
)

# Bound EVERY git subprocess so a slow/unreachable remote or a credential prompt
# can never hang the caller forever (the daemon awaits commit/push inside a tick).
_GIT_TIMEOUT_SECONDS = 60.0

# Non-interactive git environment: GIT_TERMINAL_PROMPT=0 makes git fail instead
# of blocking on a username/password TTY prompt; GIT_ASKPASS=echo neutralises any
# credential-helper prompt (mirrors what clone() already sets).
#
# Automated repositories are frequently ephemeral. Disable automatic GC and
# maintenance for every subprocess so a successful porcelain command cannot
# detach a pack writer that outlives the call and races teardown of the repo.
# Explicit maintenance commands remain available to callers when desired.
_NON_INTERACTIVE_GIT_ENV = {
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_ASKPASS": "echo",
    "GIT_CONFIG_COUNT": "2",
    "GIT_CONFIG_KEY_0": "gc.auto",
    "GIT_CONFIG_VALUE_0": "0",
    "GIT_CONFIG_KEY_1": "maintenance.auto",
    "GIT_CONFIG_VALUE_1": "false",
}

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

    # ── ansible-runner delegation ────────────────────────────────────────

    _COLLECTIONS_ROOT: Path | None = None

    def _invoke_role(self, op: str, **extravars: Any) -> dict[str, Any]:
        """Run the ``general_ludd.agent.git_automation`` role via ansible-runner.

        This is the ansible-delegation path: the Python class retains SSRF
        validation + path-safety checks (hard-gated), but delegates the actual
        ``git`` subprocess calls to the ansible role's task files.

        Returns the full ansible-runner result dict so callers can inspect
        ``status``, ``rc``, and ``events``.  Falls back to a failed-dict if
        ansible-runner is not installed.
        """
        if not _HAS_ANSIBLE_RUNNER:
            return {"status": "failed", "rc": 1, "error": "ansible-runner not installed"}

        role_path = _ROLE_DIR
        if not role_path.is_dir():
            return {"status": "failed", "rc": 1, "error": f"role directory not found: {role_path}"}

        extravars.setdefault("repo_path", self.repo_path)
        extravars.setdefault("git_op", op)
        extravars["ansible_connection"] = "local"

        playbook = [
            {
                "hosts": "localhost",
                "gather_facts": False,
                "tasks": [
                    {
                        "name": f"Invoke git_automation role ({op})",
                        "ansible.builtin.include_role": {
                            "name": str(role_path),
                            "apply": {"delegate_to": "localhost"},
                        },
                    },
                ],
            }
        ]
        playbook_yaml = (
            "["
            + ",".join(json.dumps(p) for p in playbook)
            + "]"
        )

        with tempfile.TemporaryDirectory(prefix="gludd-git-role-") as tmpdir:
            private_dir = Path(tmpdir)
            playbook_file = private_dir / "playbook.yml"
            playbook_file.write_text(playbook_yaml, encoding="utf-8")
            inventory_file = private_dir / "inventory"
            inventory_file.write_text("localhost ansible_connection=local\n", encoding="utf-8")
            env_dir = private_dir / "env"
            env_dir.mkdir()
            (env_dir / "extravars").write_text(
                json.dumps(extravars), encoding="utf-8"
            )

            try:
                runner_obj = ansible_runner.run(
                    private_data_dir=str(private_dir),
                    playbook=str(playbook_file),
                    inventory=str(inventory_file),
                    envvars={"ANSIBLE_COLLECTIONS_PATH": str(_COLLECTIONS_ROOT)},
                    quiet=True,
                )
            except Exception as exc:
                return {"status": "failed", "rc": 1, "error": f"ansible-runner error: {exc}"}

            rc = int(getattr(runner_obj, "rc", 1) or 0)
            raw_status = getattr(runner_obj, "status", None)
            status = str(raw_status).strip() if raw_status else "failed"
            return {"status": status, "rc": rc, "events": []}

    def init_repo(self, path: str | None = None) -> InitResult:
        target = path or self.repo_path
        git_dir = os.path.join(target, ".git")
        created = not os.path.isdir(git_dir)
        self._run_git("init", _cwd=target)
        for cmd in (
            ("config", "user.email", "agent@harness.local"),
            ("config", "user.name", "Agentic Harness Agent"),
        ):
            self._run_git(*cmd, check=False, _cwd=target)
        # A repository without a first commit cannot support stash/reset
        # workflows (git refuses ``stash push`` on an unborn branch).  Seed a
        # deliberately empty root commit for newly-created automation repos;
        # callers can still add their first real commit normally.
        if created:
            self._run_git(
                "commit", "--allow-empty", "-m", "Initialize repository",
                check=False, _cwd=target,
            )
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

    def _git_stdout_or_empty(self, *args: str) -> str:
        result = self._run_git(*args, check=False)
        if result.returncode != 0:
            return ""
        return result.stdout.strip()

    @staticmethod
    def _state_status_lines(status_output: str) -> list[str]:
        return [line for line in status_output.splitlines() if line.strip()]

    @staticmethod
    def _state_staged_count(lines: list[str]) -> int:
        return sum(1 for line in lines if line[:2] != "??" and line[:1] not in {"", " "})

    @staticmethod
    def _state_untracked_count(lines: list[str]) -> int:
        return sum(1 for line in lines if line.startswith("??"))

    @staticmethod
    def _state_remote_head(output: str) -> str:
        rows = output.splitlines()
        if not rows:
            return ""
        parts = rows[0].split()
        return parts[0] if parts else ""



    _DEFAULT_PRESERVE_BRANCH_PATTERNS = ("main-dirty-preserve-*", "preserve-*")
    _DEFAULT_RECONCILED_PRESERVE_HEAD_FILE = "config/reconciled_preserved_heads.txt"

    @staticmethod
    def _state_short_branch(ref: str) -> str:
        prefix = "refs/heads/"
        return ref[len(prefix):] if ref.startswith(prefix) else (ref or "DETACHED")

    @staticmethod
    def _state_is_protected_trunk_branch(branch: str) -> bool:
        return branch in {"development", "main", "master"}

    @staticmethod
    def _state_branch_matches(branch: str, patterns: Sequence[str]) -> bool:
        return any(fnmatch.fnmatchcase(branch, pattern) for pattern in patterns)

    @staticmethod
    def _state_branch_entries(ref_output: str) -> list[dict[str, str]]:
        entries: list[dict[str, str]] = []
        for line in ref_output.splitlines():
            parts = line.strip().split(maxsplit=1)
            if len(parts) == 2:
                entries.append({"branch": parts[0], "head": parts[1]})
        return entries

    @classmethod
    def _state_worktree_entries(cls, porcelain_output: str) -> list[dict[str, str]]:
        entries: list[dict[str, str]] = []
        current: dict[str, str] = {}
        for line in porcelain_output.splitlines():
            if not line.strip():
                if current.get("path"):
                    entries.append(current)
                current = {}
                continue
            if line.startswith("worktree "):
                current["path"] = line.removeprefix("worktree ")
            elif line.startswith("HEAD "):
                current["head"] = line.removeprefix("HEAD ")
            elif line.startswith("branch "):
                current["branch"] = cls._state_short_branch(line.removeprefix("branch "))
        if current.get("path"):
            entries.append(current)
        return entries

    def _state_unintegrated_worktrees(self, target_ref: str = "HEAD") -> list[dict[str, object]]:
        current_path = self._run_git("rev-parse", "--show-toplevel").stdout.strip()
        target_head = self._run_git("rev-parse", "--verify", target_ref).stdout.strip()
        worktree_output = self._git_stdout_or_empty("worktree", "list", "--porcelain")
        unintegrated: list[dict[str, object]] = []
        for entry in self._state_worktree_entries(worktree_output):
            path = entry.get("path", "")
            if not path or path == current_path:
                continue
            branch = entry.get("branch", "DETACHED")
            head = entry.get("head") or self._run_git("rev-parse", "--verify", "HEAD", _cwd=path).stdout.strip()
            status_result = self._run_git(
                "status",
                "--porcelain=v1",
                "--untracked-files=all",
                _cwd=path,
            )
            status = self._state_status_lines(status_result.stdout)
            reasons: list[str] = []
            if status:
                reasons.append("dirty")
            if (
                head
                and target_head
                and self._master_is_ancestor_of_development(head, target_head) is False
                and not self._state_is_protected_trunk_branch(branch)
            ):
                reasons.append("head_not_merged")
            if reasons:
                unintegrated.append(
                    {
                        "path": path,
                        "branch": branch,
                        "head": head,
                        "dirty_count": len(status),
                        "status": status[:25],
                        "reasons": reasons,
                    }
                )
        return unintegrated

    @staticmethod
    def _state_protected_branch_names(entries: Sequence[dict[str, str]]) -> list[str]:
        return [entry["branch"] for entry in entries if GitAutomation._state_is_protected_trunk_branch(entry["branch"])]


    def _state_branch_unique_commits(
        self,
        branch: str,
        target_head: str,
        exclude_branches: Sequence[str] = (),
    ) -> list[str]:
        args = [
            "rev-list",
            "--cherry-pick",
            "--right-only",
            "--no-merges",
            f"{target_head}...{branch}",
        ]
        for excluded in exclude_branches:
            args.append(f"^{excluded}")
        output = self._git_stdout_or_empty(*args)
        return [line.strip() for line in output.splitlines() if line.strip()]

    @staticmethod
    def _state_reconciled_preserve_head_tokens(text: str) -> set[str]:
        heads: set[str] = set()
        for raw_line in text.splitlines():
            line = raw_line.split("#", 1)[0].strip()
            if line:
                heads.add(line.split()[0])
        return heads

    def _state_load_reconciled_preserve_heads(
        self,
        head_file: str = _DEFAULT_RECONCILED_PRESERVE_HEAD_FILE,
        explicit_heads: Sequence[str] = (),
    ) -> set[str]:
        heads = {head.strip() for head in explicit_heads if head.strip()}
        if not head_file:
            return heads

        raw_path = Path(head_file)
        candidates: list[Path] = [raw_path] if raw_path.is_absolute() else []
        if not raw_path.is_absolute():
            candidates.append(Path(self.repo_path) / raw_path)
            candidates.append(Path.cwd() / raw_path)
        seen: set[Path] = set()
        for candidate in candidates:
            if candidate in seen:
                continue
            seen.add(candidate)
            try:
                heads.update(self._state_reconciled_preserve_head_tokens(candidate.read_text(encoding="utf-8")))
                return heads
            except FileNotFoundError:
                continue

        if not raw_path.is_absolute():
            repo_root = self._git_stdout_or_empty("rev-parse", "--show-toplevel")

            repo_candidate = Path(repo_root) / raw_path if repo_root else None
            if repo_candidate is not None and repo_candidate not in seen and repo_candidate.exists():
                heads.update(
                    self._state_reconciled_preserve_head_tokens(repo_candidate.read_text(encoding="utf-8"))
                )
        return heads


    def _state_unintegrated_branches(
        self,
        target_ref: str = "HEAD",
        branch_patterns: Sequence[str] = _DEFAULT_PRESERVE_BRANCH_PATTERNS,
        reconciled_preserve_heads: Sequence[str] = (),
    ) -> list[dict[str, object]]:
        reconciled_heads = {head.strip() for head in reconciled_preserve_heads if head.strip()}
        current_branch = self._git_stdout_or_empty("branch", "--show-current") or "DETACHED"
        target_head = self._run_git("rev-parse", "--verify", target_ref).stdout.strip()
        ref_output = self._git_stdout_or_empty(
            "for-each-ref",
            "--format=%(refname:short) %(objectname)",
            "refs/heads",
        )
        entries = self._state_branch_entries(ref_output)
        protected_branches = self._state_protected_branch_names(entries)
        unintegrated: list[dict[str, object]] = []
        for entry in entries:
            branch = entry["branch"]
            head = entry["head"]
            if (
                branch == current_branch
                or self._state_is_protected_trunk_branch(branch)
                or not self._state_branch_matches(branch, branch_patterns)
                or head in reconciled_heads
            ):
                continue
            unique_commits = self._state_branch_unique_commits(
                branch,
                target_head,
                protected_branches,
            )
            if unique_commits:
                unintegrated.append(
                    {
                        "branch": branch,
                        "head": head,
                        "unique_count": len(unique_commits),
                        "commits": unique_commits[:25],
                        "reasons": ["preserved_branch_not_reconciled"],
                    }
                )
        return unintegrated

    def _master_is_ancestor_of_development(
        self,
        master_head: str,
        development_head: str,
    ) -> bool | None:
        if not master_head or not development_head:
            return None
        result = self._run_git(
            "merge-base",
            "--is-ancestor",
            master_head,
            development_head,
            check=False,
        )
        if result.returncode == 0:
            return True
        if result.returncode == 1:
            return False
        raise subprocess.CalledProcessError(
            result.returncode,
            result.args,
            output=result.stdout,
            stderr=result.stderr,
        )



    def workflow_state(
        self,
        *,
        remote: str = "sandboxcom",
        ref: str = "",
        gha_head_sha: str = "",
        worktree_target_ref: str = "HEAD",
        preserve_branch_patterns: Sequence[str] = _DEFAULT_PRESERVE_BRANCH_PATTERNS,
        reconciled_preserve_heads: Sequence[str] = (),
        reconciled_preserve_head_file: str = _DEFAULT_RECONCILED_PRESERVE_HEAD_FILE,
        assert_clean: bool = False,
        assert_no_feature_on_master: bool = False,
        assert_merge_ready: bool = False,
        assert_remote_head: bool = False,
        assert_gha_matches_local: bool = False,
        assert_no_unintegrated_worktrees: bool = False,
        assert_no_unintegrated_branches: bool = False,
    ) -> GitStateResult:
        branch = self._git_stdout_or_empty("branch", "--show-current") or "DETACHED"
        head = self._run_git("rev-parse", "--verify", "HEAD").stdout.strip()
        status_result = self._run_git(
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
        )
        status = self._state_status_lines(status_result.stdout)
        remote_name = remote or "sandboxcom"
        remote_branch = ref or branch
        remote_ref = f"refs/heads/{remote_branch}"
        remote_head = self._state_remote_head(
            self._git_stdout_or_empty("ls-remote", remote_name, remote_ref)
        )
        master_head = self._git_stdout_or_empty("rev-parse", "--verify", "master")
        development_head = self._git_stdout_or_empty("rev-parse", "--verify", "development")
        master_in_development = self._master_is_ancestor_of_development(
            master_head,
            development_head,
        )
        staged_count = self._state_staged_count(status)
        untracked_count = self._state_untracked_count(status)
        dirty_count = len(status)
        unintegrated_worktrees = (
            self._state_unintegrated_worktrees(worktree_target_ref)
            if assert_no_unintegrated_worktrees
            else []
        )
        branch_patterns = tuple(preserve_branch_patterns) or self._DEFAULT_PRESERVE_BRANCH_PATTERNS
        loaded_reconciled_heads = (
            self._state_load_reconciled_preserve_heads(
                reconciled_preserve_head_file,
                reconciled_preserve_heads,
            )
            if assert_no_unintegrated_branches or reconciled_preserve_heads
            else set()
        )
        unintegrated_branches = (
            self._state_unintegrated_branches(
                worktree_target_ref,
                branch_patterns,
                tuple(loaded_reconciled_heads),
            )
            if assert_no_unintegrated_branches
            else []
        )
        errors: list[str] = []
        if assert_clean and dirty_count:
            errors.append(
                f"{dirty_count} dirty path(s) make local test evidence "
                "unreproducible in GHA"
            )
        if assert_no_feature_on_master and branch == "master" and dirty_count:
            errors.append(
                "feature or guardrail edits are present on master; "
                "use development or a release-sync worktree"
            )
        if assert_merge_ready:
            if master_in_development is None:
                errors.append(
                    "cannot prove master is contained in development; "
                    "both branches must exist before release merge"
                )
            elif not master_in_development:
                errors.append(
                    "master has commits not contained in development; "
                    "repair topology before release merge, do not cherry-pick"
                )
        if assert_remote_head:
            if not remote_head:
                errors.append(f"remote branch {remote_name}/{remote_ref} does not exist")
            elif remote_head != head:
                errors.append(
                    f"remote {remote_name}/{remote_ref} is {remote_head}, "
                    f"not local HEAD {head}"
                )
        if assert_gha_matches_local:
            if not gha_head_sha:
                errors.append(
                    "latest GHA head SHA was not provided; "
                    "cannot prove CI is testing this commit"
                )
            elif gha_head_sha != head:
                errors.append(f"latest GHA head {gha_head_sha} does not match local HEAD {head}")
        if assert_no_unintegrated_worktrees and unintegrated_worktrees:
            paths = ", ".join(
                str(item.get("path", "<unknown>"))
                for item in unintegrated_worktrees[:5]
            )
            errors.append(
                f"{len(unintegrated_worktrees)} sibling worktree(s) "
                f"contain unintegrated changes: {paths}"
            )
        if assert_no_unintegrated_branches and unintegrated_branches:
            branches = ", ".join(
                str(item.get("branch", "<unknown>"))
                for item in unintegrated_branches[:5]
            )
            errors.append(
                f"{len(unintegrated_branches)} preserved branch(es) "
                f"contain unreconciled patches: {branches}"
            )
        return GitStateResult(
            success=not errors,
            branch=branch,
            head=head,
            dirty_count=dirty_count,
            staged_count=staged_count,
            untracked_count=untracked_count,
            status=status,
            remote=remote_name,
            remote_ref=remote_ref,
            remote_head=remote_head,
            master_head=master_head,
            development_head=development_head,

            master_is_ancestor_of_development=master_in_development,
            gha_head_sha=gha_head_sha,
            reconciled_preserve_heads=sorted(loaded_reconciled_heads),
            unintegrated_worktrees=unintegrated_worktrees,
            unintegrated_branches=unintegrated_branches,
            errors=errors,
        )

    def create_branch(self, name: str, *, use_ansible: bool = False) -> str:
        _reject_leading_dash(name, kind="branch name")
        if use_ansible:
            result = self._invoke_role(
                "branch", branch_op="create", branch_name=name,
            )
            if result.get("status") != "successful":
                raise ValueError(
                    f"failed to create branch {name!r}: {result.get('error', 'ansible-runner error')}"
                )
            return name
        existing = [
            line.strip()
            for line in self._run_git("branch", "--format=%(refname:short)")
            .stdout.splitlines()
        ]
        if any(line == name for line in existing):
            raise ValueError(
                f"branch {name!r} already exists; refusing to overwrite"
            )
        # `--` ends option parsing so the name is unambiguously the new branch.
        self._run_git("checkout", "-b", name, "--")
        return name

    def list_branches(self) -> list[str]:
        result = self._run_git("branch", "--format=%(refname:short)")
        return [line.strip() for line in result.stdout.splitlines() if line.strip()]

    def delete_branch(self, name: str) -> bool:
        """Delete ``name``, safely leaving it first when it is checked out.

        Git refuses to delete the current branch.  For the symmetric
        ``create_branch``/``delete_branch`` lifecycle, move to an existing
        protected trunk before deleting a checked-out feature branch.  If no
        such trunk exists, fail closed without detaching HEAD or deleting a
        different ref.
        """
        _reject_leading_dash(name, kind="branch name")
        try:
            if self.current_branch() == name:
                branches = set(self.list_branches())
                trunk = next(
                    (
                        candidate
                        for candidate in ("development", "main", "master")
                        if candidate != name and candidate in branches
                    ),
                    None,
                )
                if trunk is None:
                    return False
                self._run_git("checkout", trunk, "--")
            self._run_git("branch", "-D", "--", name)
            return True
        except subprocess.CalledProcessError:
            return False

    def commit(self, message: str, *, use_ansible: bool = False) -> str:
        """Stage all changes and commit; return the new commit SHA.

        When ``use_ansible=True``, delegates to the ``git_automation`` ansible
        role instead of running ``git`` via subprocess directly.
        """
        if use_ansible:
            ansible_result = self._invoke_role(
                "commit", commit_message=message,
            )
            if ansible_result.get("status") != "successful":
                raise subprocess.CalledProcessError(
                    returncode=ansible_result.get("rc", 1),
                    cmd=["ansible_runner", "git_automation", "commit"],
                    output="",
                    stderr=ansible_result.get("error", "commit failed via ansible-runner"),
                )
            sha_result = self._run_git("rev-parse", "HEAD")
            return sha_result.stdout.strip()
        self._run_git("add", "-A")
        self._run_git("commit", "-m", message)
        result = self._run_git("rev-parse", "HEAD")
        return result.stdout.strip()

    def tag_release(self, tag: str) -> str:
        _reject_leading_dash(tag, kind="tag name")
        self._run_git("tag", "-a", "-m", f"Release {tag}", "--", tag)
        return tag

    def tag_checkpoint(self, tag: str) -> str:
        _reject_leading_dash(tag, kind="tag name")
        self._run_git("tag", "--", tag)
        return tag

    def push(self, remote: str = "origin", branch: str = "main", *, use_ansible: bool = False) -> bool:
        _reject_leading_dash(remote, kind="remote name")
        _reject_leading_dash(branch, kind="branch name")
        if use_ansible:
            result = self._invoke_role(
                "push", push_remote=remote, push_branch=branch,
            )
            return result.get("status") == "successful"
        try:
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
        use_ansible: bool = False,
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
        # --- pre-flight safety checks (fail BEFORE any execution) ---
        try:
            _reject_clone_url(url, allow_local=allow_local)
        except ValueError as exc:
            return CloneResult(
                path=os.path.abspath(target_dir),
                url=url,
                success=False,
                message=str(exc),
            )

        # --- ansible-runner delegation path ---
        if use_ansible:
            ansible_result = self._invoke_role(
                "clone", clone_url=url, target_dir=target_dir,
                git_clone_timeout=int(timeout),
            )
            return CloneResult(
                path=os.path.abspath(target_dir),
                url=url,
                success=ansible_result.get("status") == "successful",
                message=ansible_result.get("error", ""),
            )

        # --- subprocess path (default) ---
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
            # `-b <branch>` then `--` then the path positional: the path can
            # never be reinterpreted as an option.
            self._run_git(
                "worktree",
                "add",
                "-b",
                branch_name,
                "--",
                worktree_path,
                "HEAD",
                _cwd=repo_path,
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

        Worktrees are expected to live beside the repo (or under it), or in a
        gludd-owned temporary root. A path containing ``..`` is refused so a
        traversal value cannot plant a worktree in an arbitrary location.
        """
        # A `..` component is the traversal primitive that escapes the intended
        # area; refuse it outright (a legitimate worktree path never needs one).
        parts = worktree_path.replace("\\", "/").split("/")
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
        if GitAutomation._is_gludd_temp_worktree_path(target, project_root=repo_abs):
            return
        raise ValueError(
            f"refusing worktree path that escapes the repo parent: {worktree_path!r}"
        )

    @staticmethod
    def _is_gludd_temp_worktree_path(
        path: str,
        *,
        project_root: str | None = None,
    ) -> bool:
        real_target = os.path.realpath(path)
        try:
            state = project_state(project_root=project_root, create=False)
            root = os.path.realpath(state.path("worktrees"))
            return os.path.commonpath([root, real_target]) == root
        except (OSError, ValueError, SecureStateError):
            return False

    def remove_worktree(self, repo_path: str, worktree_path: str) -> bool:
        _reject_leading_dash(worktree_path, kind="worktree path")
        try:
            # `--force`: a worktree being torn down by the orchestrator
            # legitimately contains untracked/modified content. The caller has
            # already decided the worktree is done; --force discards only
            # uncommitted state (the branch is retained).
            self._run_git(
                "worktree",
                "remove",
                "--force",
                "--",
                worktree_path,
                _cwd=repo_path,
            )
            return True
        except subprocess.CalledProcessError:
            return False

    def list_worktrees(self, repo_path: str) -> list[WorktreeInfo]:
        result = self._run_git("worktree", "list", "--porcelain", _cwd=repo_path)
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

    def merge_branch(
        self, repo_path: str, source: str, target: str,
        strategy: str = "ff", *, use_ansible: bool = False,
    ) -> MergeResult:
        _reject_leading_dash(source, kind="merge source ref")
        _reject_leading_dash(target, kind="merge target ref")

        if use_ansible:
            ansible_result = self._invoke_role(
                "merge", merge_source=source, merge_target=target,
                merge_strategy=strategy, repo_path=repo_path,
            )
            return MergeResult(
                success=ansible_result.get("status") == "successful",
                strategy=strategy,
                message=ansible_result.get("error", ""),
            )

        with git_repo_lock(repo_path):
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
            # Fail-closed on merge failure (C.17): ``check=True`` makes
            # ``_run_git`` raise ``CalledProcessError`` on a non-zero exit, which we
            # translate into a failure result — structurally impossible to
            # accidentally skip the error check and return success=True.
            try:
                result = self._run_git(*merge_args, check=True, _cwd=repo_path)
            except subprocess.CalledProcessError as exc:
                conflicts = []
                stderr = (exc.stderr or "").strip()
                stdout = getattr(exc, "output", "") or ""
                combined = stderr or stdout
                if "CONFLICT" in combined:
                    conflicts = [source]
                return MergeResult(
                    success=False,
                    strategy=strategy,
                    message=stderr or str(exc),
                    conflicts=conflicts,
                )
            if strategy == "squash":
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
        # Hold the per-repo lock for the entire sequence (C.17, #63).
        with git_repo_lock(self.repo_path):
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
            # Fail-closed on merge failure (C.17): check=True so a non-zero
            # exit always raises — structurally impossible to accidentally skip
            # the error check and proceed past a failed merge.
            try:
                self._run_git(*merge_args, check=True)
            except subprocess.CalledProcessError as exc:
                self._run_git("merge", "--abort", check=False)
                self._run_git("reset", "--hard", pre_sha, check=False)
                return GatedCommitResult(
                    success=False,
                    gate_returncode=exc.returncode,
                    message=(exc.stderr or "merge failed").strip(),
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
        _reject_leading_dash(tag, kind="tag name")
        self._run_git("tag", "-a", "-m", f"Release {tag}", "--", tag, _cwd=repo_path)
        return tag

    def create_checkpoint_tag(self, repo_path: str, todo_id: str, sha: str) -> str:
        ts = datetime.now(tz=UTC).strftime("%Y%m%d%H%M%S")
        short_sha = sha[:7]
        tag = f"agent/{todo_id}/{ts}/{short_sha}"
        _reject_leading_dash(tag, kind="tag name")
        self._run_git("tag", "--", tag, _cwd=repo_path)
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
            with git_repo_lock(repo_path):
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

    def verify_remote(
        self,
        remote: str,
        branch: str,
        expected_sha: str,
        ssh_key_path: str | None = None,
        *,
        ref_type: str = "heads",
    ) -> VerifyRemoteResult:
        """Verify the remote ref tip matches ``expected_sha`` via ``git ls-remote``.

        Ported from the Makefile ``verify-remote`` target.
        """
        return _verify_remote_fn(
            remote=remote,
            branch=branch,
            expected_sha=expected_sha,
            ssh_key_path=ssh_key_path,
            ref_type=ref_type,
        )

    # ── staging / mutation operations ──────────────────────────────────

    def stash(self, message: str = "") -> bool:
        """Push working-tree changes onto the stash stack. Returns True if
        anything was stashed, False if the tree was already clean."""
        # Keep the staged index intact while shelving worktree edits.  This
        # mirrors the Makefile workflow used before commits and preserves
        # newly staged files (which ``git stash push`` otherwise removes).
        args = ["stash", "push", "--keep-index"]
        if message:
            args.extend(["-m", message])
        try:
            result = self._run_git(*args, check=True)
            return "No local changes to save" not in result.stdout
        except subprocess.CalledProcessError:
            return False

    def stash_pop(self) -> bool:
        try:
            self._run_git("stash", "pop", check=True)
            return True
        except subprocess.CalledProcessError:
            return False

    def reset_mixed(self) -> None:
        self._run_git("reset", check=True)

    def reset_soft(self, *, ref: str) -> None:
        _reject_leading_dash(ref, kind="reset ref")
        # ``--`` terminates options and starts a pathspec; refs such as
        # ``HEAD~1`` must remain positional revision arguments.
        self._run_git("reset", "--soft", ref, check=True)

    def reset_hard(self, *, ref: str) -> None:
        _reject_leading_dash(ref, kind="reset ref")
        self._run_git("reset", "--hard", ref, check=True)

    def add(self, files: Sequence[str]) -> None:
        if not files:
            return
        self._run_git("add", "--", *files, check=True)

    def add_all(self) -> None:
        self._run_git("add", "-A", check=True)

    def rm(self, files: Sequence[str]) -> None:
        if not files:
            return
        self._run_git("rm", "-r", "--", *files, check=True)

    def rm_cached(self, files: Sequence[str]) -> None:
        if not files:
            return
        self._run_git("rm", "--cached", "--", *files, check=True)

    def mv(self, old: str, new: str) -> None:
        _reject_leading_dash(old, kind="mv source path")
        _reject_leading_dash(new, kind="mv destination path")
        os.makedirs(os.path.dirname(os.path.join(self.repo_path, new)) or self.repo_path, exist_ok=True)
        self._run_git("mv", "--", old, new, check=True)

    def ls_tracked(self) -> list[str]:
        result = self._git_stdout_or_empty("ls-files")
        if not result:
            return []
        return [line.strip() for line in result.splitlines() if line.strip()]

    def restore(self, path: str) -> bool:
        _reject_leading_dash(path, kind="restore path")
        try:
            self._run_git("restore", "--", path, check=True)
            return True
        except subprocess.CalledProcessError:
            return False

    def create_local_bare_mirror(self, repo_path: str, mirror_path: str) -> str:
        with git_repo_lock(repo_path):
            subprocess.run(
                ["git", "clone", "--bare", "--", repo_path, mirror_path],
                capture_output=True,
                text=True,
                check=True,
                timeout=_GIT_TIMEOUT_SECONDS,
                env={**os.environ, **_NON_INTERACTIVE_GIT_ENV},
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
