"""Linux bubblewrap (``bwrap``) backend — namespace-isolation sandbox.

bubblewrap (``bwrap``) is an unprivileged sandbox runner that uses Linux
namespaces (mount, pid, net, ipc, uts, user) to launch a subprocess inside a
restricted environment. Unlike Landlock (which restricts the current process
in-place), bubblewrap FORKS the target into a new namespace — so it is the
correct backend for the "run an agent subprocess in a jail" model.

This backend is SECONDARY on Linux: where Landlock is available (kernel 6.7+,
pylandlock importable) Landlock is preferred because it is in-process and has
finer-grained filesystem access control. bubblewrap is the next-best option
when Landlock is unavailable, before falling back to AppArmor / SELinux
(system-wide) or no sandbox.

Network egress
--------------

bubblewrap alone does NOT do hostname filtering. ``--share-net`` shares the
host netns with the agent subprocess; if the spec lists ``allowed_hosts`` this
backend logs a loud warning and the operator MUST pair bubblewrap with seccomp
or an iptables/nft rule scoped to the agent's network namespace. ``bwrap``
CAN run with ``--unshare-net`` to deny all networking; this backend uses that
mode when the spec has zero net capabilities.
"""

from __future__ import annotations

import logging
import shlex
import shutil
from typing import cast

from general_ludd.security.sandboxes import (
    Capability,
    Finding,
    PermissionSpec,
    SandboxHandle,
    SandboxTarget,
    allowed_hosts,
    path_prefix,
)

logger = logging.getLogger(__name__)


def _is_file_family(cap: Capability) -> bool:
    return cap.resource.startswith("file:")


def _is_net_family(cap: Capability) -> bool:
    return cap.resource.startswith("net:")


def render_argv(spec: PermissionSpec, target: SandboxTarget, cmd: list[str] | None = None) -> list[str]:
    """Render the ``bwrap`` argv for ``spec``.

    ``cmd`` is the agent command to launch INSIDE the namespace (defaults to
    ``["/bin/sh"]``). The returned argv is suitable for ``subprocess.Popen``.
    """
    argv: list[str] = ["bwrap"]
    # Read-only OS essentials.
    argv += ["--ro-bind", "/usr", "/usr"]
    argv += ["--ro-bind", "/lib", "/lib"]
    argv += ["--ro-bind", "/lib64", "/lib64"]
    argv += ["--symlink", "usr/lib", "lib"]
    argv += ["--symlink", "usr/lib64", "lib64"]
    argv += ["--dir", "/tmp"]
    argv += ["--proc", "/proc"]
    argv += ["--dev", "/dev"]
    argv += ["--ro-bind", "/etc/resolv.conf", "/etc/resolv.conf"]
    # Bind agent-allowed paths read-write.
    seen: set[str] = set()
    for cap in spec.capabilities:
        if _is_file_family(cap):
            p = path_prefix(cap)
            if p and p not in seen:
                argv += ["--bind", p, p]
                seen.add(p)
    # Chroot into target.directory if specified.
    if target.directory:
        # bwrap has no direct "chroot" but --chdir works after the binds land.
        argv += ["--chdir", target.directory]
    # Namespace isolation.
    argv += ["--unshare-all"]
    # Network: share-net if any net cap, else fully unshare (no net).
    has_net = any(_is_net_family(c) for c in spec.capabilities)
    argv += ["--share-net"] if has_net else ["--unshare-net"]
    argv += ["--die-with-parent"]
    argv += ["--"]
    argv += (cmd or ["/bin/sh"])
    return argv


class BubblewrapBackend:
    name = "bubblewrap"

    @staticmethod
    def available() -> bool:
        return shutil.which("bwrap") is not None

    @staticmethod
    def apply(spec: PermissionSpec, target: SandboxTarget) -> SandboxHandle:
        ruleset_name = f"gludd-{spec.agent_type}"
        if shutil.which("bwrap") is None:
            logger.warning(
                "bubblewrap apply skipped: bwrap binary not on PATH "
                "(apt install bubblewrap / brew install bubblewrap) — UNSANDBOXED"
            )
            return SandboxHandle(
                backend="bubblewrap", token=ruleset_name, applied=False,
                extra={"reason": "bwrap binary absent"},
            )
        # Surface net-by-hostname constraints bubblewrap cannot enforce.
        net_hosts: list[str] = []
        for cap in spec.capabilities:
            if _is_net_family(cap):
                hosts = allowed_hosts(cap)
                if hosts:
                    net_hosts.extend(hosts)
        if net_hosts:
            logger.warning(
                "bubblewrap does NOT enforce hostname egress allow-list %r; "
                "pair bwrap --share-net with seccomp or nft/iptables rules "
                "scoped to the agent netns", net_hosts,
            )
        try:
            argv = render_argv(spec, target)
            argv_str = " ".join(shlex.quote(a) for a in argv)
            logger.info(
                "bubblewrap argv rendered for %s (%d tokens): %s",
                ruleset_name, len(argv), argv_str,
            )
            # We do NOT launch the agent here — the caller launches its
            # subprocess with this argv as the prefix. Record the argv so the
            # dispatch site can prepend it to the real agent command.
            return SandboxHandle(
                backend="bubblewrap", token=ruleset_name, applied=True,
                extra={
                    "argv": argv,
                    "argv_str": argv_str,
                    "unhandled_net_hosts": net_hosts,
                },
            )
        except Exception as exc:
            logger.error(
                "bubblewrap apply failed for %s — dispatching UNSANDBOXED: %s",
                ruleset_name, exc, exc_info=True,
            )
            return SandboxHandle(
                backend="bubblewrap", token=ruleset_name, applied=False,
                extra={"reason": str(exc)},
            )

    @staticmethod
    def verify(spec: PermissionSpec, handle: SandboxHandle) -> list[Finding]:
        findings: list[Finding] = []
        if not handle.applied:
            findings.append(Finding(
                severity="warn",
                message=(
                    f"apply() reported applied=False (reason="
                    f"{handle.extra.get('reason', 'unknown')}); bubblewrap advisory"
                ),
                capability=None,
            ))
            return findings
        # Structural check: argv must contain --unshare-all and --die-with-parent.
        argv = cast(list[str], handle.extra.get("argv")) or []
        if "--unshare-all" not in argv:
            findings.append(Finding(
                severity="fail",
                message="bubblewrap argv missing --unshare-all (namespace isolation broken)",
                capability=None,
            ))
        else:
            findings.append(Finding(
                severity="ok",
                message="bubblewrap argv includes --unshare-all",
                capability=None,
            ))
        if "--die-with-parent" not in argv:
            findings.append(Finding(
                severity="warn",
                message="bubblewrap argv missing --die-with-parent (orphan risk)",
                capability=None,
            ))
        # Probe write outside bind mounts — only meaningful from inside the
        # namespace. We can only structurally assert the bind list here.
        bound = [a for i, a in enumerate(argv) if i > 0 and argv[i - 1] == "--bind"]
        expected = {
            path_prefix(c)
            for c in spec.capabilities
            if _is_file_family(c) and path_prefix(c)
        }
        missing = expected - set(bound)
        if missing:
            findings.append(Finding(
                severity="fail",
                message=f"bubblewrap argv missing --bind for paths: {sorted(p for p in missing if p is not None)}",
                capability=None,
            ))
        else:
            findings.append(Finding(
                severity="ok",
                message=f"bubblewrap bind mounts present for {sorted(p for p in expected if p is not None)}",
                capability=None,
            ))
        unhandled = handle.extra.get("unhandled_net_hosts") or []
        if unhandled:
            findings.append(Finding(
                severity="warn",
                message=(
                    f"net hostname constraints {unhandled!r} NOT enforced by "
                    f"bubblewrap (needs seccomp/nft)"
                ),
                capability=None,
            ))
        return findings

    @staticmethod
    def release(handle: SandboxHandle) -> None:
        # bubblewrap namespaces die with the child process (--die-with-parent).
        # release() is a no-op for audit symmetry.
        logger.debug("bubblewrap release(%s) — child process owns the namespace", handle.token)


__all__ = ["BubblewrapBackend", "render_argv"]
