"""Verify that a remote ref matches an expected commit SHA.

Ported from the Makefile ``verify-remote`` target (``git ls-remote`` pattern).
Use ``verify_remote()`` as a standalone function, or call
``GitAutomation.verify_remote()`` for the same logic wired into the
control-plane class.
"""

from __future__ import annotations

import os
import subprocess

from general_ludd.git_automation.types import VerifyRemoteResult

_GIT_TIMEOUT_SECONDS = 60.0


def verify_remote(
    remote: str,
    branch: str,
    expected_sha: str,
    ssh_key_path: str | None = None,
    *,
    ref_type: str = "heads",
) -> VerifyRemoteResult:
    """Run ``git ls-remote`` and compare the returned SHA to ``expected_sha``.

    Args:
        remote: Remote name or URL (e.g. ``sandboxcom``, ``/path/to/bare.git``).
        branch: The branch or tag name (e.g. ``master``, ``v1.0.0``).
        expected_sha: The full commit SHA that the remote tip should match.
        ssh_key_path: Optional SSH private key path; if given, sets
            ``GIT_SSH_COMMAND`` so ``git ls-remote`` uses this key.
        ref_type: ``"heads"`` (branches) or ``"tags"`` (tags).

    Returns:
        A ``VerifyRemoteResult`` with ``status`` set to:
        - ``"VERIFIED"`` — ``remote_sha == expected_sha``
        - ``"MISMATCH"`` — SHA differs
        - ``"UNREACHABLE"`` — ``git ls-remote`` failed, timed out, or returned no ref
    """
    _reject_leading_dash(remote, kind="remote name")
    _reject_leading_dash(branch, kind="branch/ref name")
    _reject_leading_dash(expected_sha, kind="expected SHA")

    if ref_type not in ("heads", "tags"):
        raise ValueError(f"ref_type must be 'heads' or 'tags', got {ref_type!r}")

    ref = f"refs/{ref_type}/{branch}"

    env = {**os.environ}
    if ssh_key_path:
        key = os.path.abspath(os.path.expanduser(ssh_key_path))
        env["GIT_SSH_COMMAND"] = (
            f"ssh -i {key} -o StrictHostKeyChecking=accept-new"
        )

    try:
        result = subprocess.run(
            ["git", "ls-remote", "--", remote, ref],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_SECONDS,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return VerifyRemoteResult(
            status="UNREACHABLE",
            remote_sha="",
            expected_sha=expected_sha,
            remote=remote,
            ref=ref,
            message=f"git ls-remote timed out after {_GIT_TIMEOUT_SECONDS}s",
        )
    except OSError as exc:
        return VerifyRemoteResult(
            status="UNREACHABLE",
            remote_sha="",
            expected_sha=expected_sha,
            remote=remote,
            ref=ref,
            message=f"git ls-remote failed: {exc}",
        )

    if result.returncode != 0:
        return VerifyRemoteResult(
            status="UNREACHABLE",
            remote_sha="",
            expected_sha=expected_sha,
            remote=remote,
            ref=ref,
            message=(result.stderr or result.stdout or "git ls-remote failed").strip(),
        )

    output = result.stdout.strip()
    if not output:
        return VerifyRemoteResult(
            status="UNREACHABLE",
            remote_sha="",
            expected_sha=expected_sha,
            remote=remote,
            ref=ref,
            message=f"no ref {ref} found on remote {remote}",
        )

    parts = output.split()
    remote_sha = parts[0] if parts else ""

    if remote_sha == expected_sha:
        return VerifyRemoteResult(
            status="VERIFIED",
            remote_sha=remote_sha,
            expected_sha=expected_sha,
            remote=remote,
            ref=ref,
            message="",
        )

    return VerifyRemoteResult(
        status="MISMATCH",
        remote_sha=remote_sha,
        expected_sha=expected_sha,
        remote=remote,
        ref=ref,
        message=f"REMOTE MISMATCH: remote={remote_sha} expected={expected_sha}",
    )


def _reject_leading_dash(value: str, *, kind: str) -> None:
    """Reject a value that begins with ``-`` (option-injection guard)."""
    if value.startswith("-"):
        raise ValueError(
            f"refusing {kind} that begins with '-' "
            f"(would be parsed as a git option, not a value): {value!r}"
        )
