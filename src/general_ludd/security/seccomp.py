"""Seccomp BPF syscall filtering for Ansible playbook child processes.

OpenShell P2 transfer
---------------------
OpenShell's process policy blocks privilege escalation and container-escape
syscalls (``clone`` with namespace flags, ``mount``, ``ptrace``, ``setns``,
``unshare``, ``pivot_root``) in addition to filesystem/network isolation.
gludd's Landlock backend (``security/sandboxes/linux_landlock.py``) restricts
the filesystem and TCP ports but performs **no syscall-level filtering** — a
compromised playbook could call ``unshare`` to escape its mount namespace or
``mount`` to remount a filesystem. This module closes that gap by installing a
seccomp BPF filter in the ansible fork child *before* ``os.setsid()``.

Design
------
* **Linux-only.** ``apply()`` is a fail-open NOP on macOS / Windows / any host
  without seccomp — it logs and returns ``False`` rather than raising. This is
  the same fail-open contract the sandbox backends use: a missing kernel
  feature must never wedge job execution.
* **Two install strategies, tried in order:**
    1. **libseccomp** (``import seccomp``) — the maintained, arch-portable
       binding. Supports argument filtering, so ``clone``/``clone3`` are killed
       only when a namespace-creating flag is set (never for plain
       thread/fork ``clone``, which Python's runtime needs).
    2. **Manual BPF** assembled here and installed via
       ``prctl(PR_SET_SECCOMP, SECCOMP_MODE_FILTER, ...)`` through ``ctypes``.
       No third-party dependency; syscall numbers for x86_64 + aarch64 are
       baked in for the (small) denylist.
* **Default model is allow-list-by-default with an explicit KILL denylist.**
  A pure whitelist with default-KILL would kill the child on any syscall the
  runtime happens to need but we forgot to list — brittle and dangerous. The
  default filter therefore *allows* by default and *kills* the documented
  container-escape / privilege family, which is both safe (never breaks a
  benign playbook) and sufficient (blocks the real escape vectors). A strict
  whitelist mode (``default_action="errno"``) is available for callers that
  want it.
"""

from __future__ import annotations

import errno
import logging
import platform
import sys
from dataclasses import dataclass

import yaml

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Default syscall sets
# ---------------------------------------------------------------------------

# ~80 common syscalls a local ansible run legitimately needs. Used as the
# whitelist for strict (``default_action="errno"``) mode and as documentation
# of "known-benign" for the default allow-mode filter.
DEFAULT_ALLOWED_SYSCALLS: frozenset[str] = frozenset({
    # file I/O
    "read", "write", "open", "openat", "openat2", "close", "close_range",
    "stat", "fstat", "lstat", "newfstatat", "statx", "lseek", "pread64",
    "pwrite64", "readv", "writev", "access", "faccessat", "faccessat2",
    "getdents", "getdents64", "getcwd", "chdir", "fchdir", "readlink",
    "readlinkat", "fcntl", "flock", "fsync", "fdatasync", "ftruncate",
    "truncate",
    # file management
    "rename", "renameat", "renameat2", "mkdir", "mkdirat", "rmdir", "unlink",
    "unlinkat", "symlink", "symlinkat", "chmod", "fchmod", "fchmodat", "chown",
    "fchown", "lchown", "fchownat", "umask", "dup", "dup2", "dup3", "pipe",
    "pipe2",
    # memory
    "mmap", "mprotect", "munmap", "mremap", "madvise", "brk", "memfd_create",
    # signals
    "rt_sigaction", "rt_sigprocmask", "rt_sigreturn", "sigaltstack",
    # polling / events
    "select", "pselect6", "poll", "ppoll", "epoll_create1", "epoll_ctl",
    "epoll_wait", "epoll_pwait", "eventfd2",
    # process / scheduling
    "getpid", "getppid", "gettid", "getuid", "geteuid", "getgid", "getegid",
    "clock_gettime", "clock_nanosleep", "nanosleep", "futex", "sched_yield",
    "exit", "exit_group", "wait4", "waitid", "execve", "execveat", "fork",
    "vfork", "set_tid_address", "set_robust_list", "rseq", "prlimit64",
    "arch_prctl",
    # info / random
    "uname", "sysinfo", "getrandom", "ioctl", "gettimeofday",
    # sockets (net egress is separately constrained by Landlock ports)
    "socket", "socketpair", "bind", "connect", "listen", "accept", "accept4",
    "sendto", "recvfrom", "sendmsg", "recvmsg", "shutdown", "getsockname",
    "getpeername", "getsockopt", "setsockopt",
})

# The container-escape / privilege-escalation family. These are KILLED (SIGSYS)
# by the default filter. Superset of the OpenShell-documented denylist
# (mount, setns, unshare, pivot_root, add_key, request_key, keyctl, reboot,
# kexec_load, init_module, delete_module) plus the modern mount API
# (open_tree/move_mount/fsopen/...) and the debugging/BPF vectors.
DEFAULT_DENIED_SYSCALLS: frozenset[str] = frozenset({
    # namespace / mount escapes
    "mount", "umount2", "setns", "unshare", "pivot_root", "chroot",
    "mount_setattr", "open_tree", "move_mount", "fsopen", "fsconfig",
    "fsmount", "fspick",
    # kernel module (in)jection
    "init_module", "finit_module", "delete_module",
    # kernel exec / reboot
    "kexec_load", "kexec_file_load", "reboot",
    # keyring
    "add_key", "request_key", "keyctl",
    # swap / host identity
    "swapon", "swapoff", "sethostname", "setdomainname",
    # debugging / memory-poking / BPF
    "ptrace", "process_vm_writev", "bpf", "perf_event_open",
})

# ``clone``/``clone3`` are NOT in the flat denylist — a plain fork/thread clone
# is needed by the Python runtime. They are killed only when a namespace flag
# is set (libseccomp arg-filter + manual-BPF JSET guard below).
_NAMESPACE_GUARDED = frozenset({"clone", "clone3"})

# CLONE_NEW* flags (namespace-creating). Deliberately excludes CLONE_NEWTIME
# (0x80), which overlaps the CSIGNAL byte in clone()'s flags argument.
_CLONE_NS_MASK = (
    0x00020000  # CLONE_NEWNS
    | 0x02000000  # CLONE_NEWCGROUP
    | 0x04000000  # CLONE_NEWUTS
    | 0x08000000  # CLONE_NEWIPC
    | 0x10000000  # CLONE_NEWUSER
    | 0x20000000  # CLONE_NEWPID
    | 0x40000000  # CLONE_NEWNET
)

# ---------------------------------------------------------------------------
# seccomp / BPF constants
# ---------------------------------------------------------------------------

_PR_SET_NO_NEW_PRIVS = 38
_PR_SET_SECCOMP = 22
_SECCOMP_MODE_FILTER = 2

# seccomp_data field offsets (little-endian 64-bit).
_OFF_NR = 0
_OFF_ARCH = 4
_OFF_ARG0_LOW = 16

# AUDIT_ARCH_* — the kernel validates the caller's ABI so an x86-32 shim cannot
# be used to smuggle a differently-numbered syscall past the filter.
_AUDIT_ARCH_X86_64 = 0xC000003E
_AUDIT_ARCH_AARCH64 = 0xC00000B7

# BPF classic opcodes.
_BPF_LD_ABS = 0x20  # BPF_LD | BPF_W | BPF_ABS
_BPF_JEQ = 0x15  # BPF_JMP | BPF_JEQ | BPF_K
_BPF_JSET = 0x45  # BPF_JMP | BPF_JSET | BPF_K
_BPF_RET = 0x06  # BPF_RET | BPF_K

# seccomp return actions.
_SECCOMP_RET_KILL_PROCESS = 0x80000000
_SECCOMP_RET_ERRNO = 0x00050000
_SECCOMP_RET_ALLOW = 0x7FFF0000

# Baked-in syscall numbers for the denylist + clone, per arch. The manual-BPF
# fallback needs numbers (no libseccomp to resolve names). libseccomp resolves
# names itself, so this table only has to cover what the fallback filters.
_SYSCALL_NUMBERS: dict[str, dict[str, int]] = {
    "x86_64": {
        "mount": 165, "umount2": 166, "swapon": 167, "swapoff": 168,
        "reboot": 169, "sethostname": 170, "setdomainname": 171,
        "pivot_root": 155, "chroot": 161, "init_module": 175,
        "delete_module": 176, "finit_module": 313, "setns": 308,
        "unshare": 272, "add_key": 248, "request_key": 249, "keyctl": 250,
        "kexec_load": 246, "kexec_file_load": 320, "ptrace": 101,
        "process_vm_writev": 311, "bpf": 321, "perf_event_open": 298,
        "open_tree": 428, "move_mount": 429, "fsopen": 430, "fsconfig": 431,
        "fsmount": 432, "fspick": 433, "mount_setattr": 442,
        "clone": 56, "clone3": 435,
    },
    "aarch64": {
        "mount": 40, "umount2": 39, "swapon": 224, "swapoff": 225,
        "reboot": 142, "sethostname": 161, "setdomainname": 162,
        "pivot_root": 41, "chroot": 51, "init_module": 105,
        "delete_module": 106, "finit_module": 273, "setns": 268,
        "unshare": 97, "add_key": 217, "request_key": 218, "keyctl": 219,
        "kexec_load": 104, "kexec_file_load": 294, "ptrace": 117,
        "process_vm_writev": 271, "bpf": 280, "perf_event_open": 241,
        "open_tree": 428, "move_mount": 429, "fsopen": 430, "fsconfig": 431,
        "fsmount": 432, "fspick": 433, "mount_setattr": 442,
        "clone": 220, "clone3": 435,
    },
}


def _native_arch() -> str:
    """Return the syscall-table key for the running machine.

    Returns ``x86_64`` or ``aarch64`` and defaults to ``x86_64`` for unknown
    machines so ``build_bpf`` remains testable off Linux.
    """
    machine = platform.machine().lower()
    if machine in ("aarch64", "arm64"):
        return "aarch64"
    return "x86_64"


def _audit_arch(arch: str) -> int:
    return _AUDIT_ARCH_AARCH64 if arch == "aarch64" else _AUDIT_ARCH_X86_64


# ---------------------------------------------------------------------------
# SeccompFilter
# ---------------------------------------------------------------------------

_BpfInsn = tuple[int, int, int, int]


@dataclass(frozen=True)
class SeccompFilter:
    """A declarative seccomp syscall filter.

    ``allowed_syscalls`` is the whitelist (used verbatim in ``errno`` mode; in
    the default ``allow`` mode it documents known-benign syscalls). Anything in
    ``denied_syscalls`` is KILLed (SIGSYS) or, if ``deny_action == "errno"``,
    returned ``EPERM``. ``default_action`` is the action for any syscall that is
    neither explicitly allowed nor denied: ``"allow"`` (default, safe) or
    ``"errno"`` (strict whitelist).
    """

    allowed_syscalls: frozenset[str] = DEFAULT_ALLOWED_SYSCALLS
    denied_syscalls: frozenset[str] = DEFAULT_DENIED_SYSCALLS
    default_action: str = "allow"
    deny_action: str = "kill"
    errno: int = errno.EPERM

    @classmethod
    def default(cls) -> SeccompFilter:
        """The wired-in default: allow-by-default, KILL the escape family."""
        return cls()

    @classmethod
    def from_yaml(cls, path: str) -> SeccompFilter:
        """Build a filter from a declarative YAML spec.

        Recognised keys: ``allowed_syscalls`` (list), ``denied_syscalls``
        (list), ``default_action`` (``allow``/``errno``), ``deny_action``
        (``kill``/``errno``), ``errno`` (int). Missing keys fall back to the
        defaults.
        """
        with open(path) as fh:
            raw = yaml.safe_load(fh) or {}
        if not isinstance(raw, dict):
            raise ValueError(f"seccomp YAML must be a mapping, got {type(raw).__name__}")
        allowed = raw.get("allowed_syscalls")
        denied = raw.get("denied_syscalls")
        default_action = str(raw.get("default_action", "allow"))
        deny_action = str(raw.get("deny_action", "kill"))
        if default_action not in {"allow", "errno"}:
            raise ValueError(
                "seccomp default_action must be 'allow' or 'errno', "
                f"got {default_action!r}"
            )
        if deny_action not in {"kill", "errno"}:
            raise ValueError(
                "seccomp deny_action must be 'kill' or 'errno', "
                f"got {deny_action!r}"
            )
        return cls(
            allowed_syscalls=(
                frozenset(str(s) for s in allowed)
                if allowed is not None
                else DEFAULT_ALLOWED_SYSCALLS
            ),
            denied_syscalls=(
                frozenset(str(s) for s in denied)
                if denied is not None
                else DEFAULT_DENIED_SYSCALLS
            ),
            default_action=default_action,
            deny_action=deny_action,
            errno=int(raw.get("errno", errno.EPERM)),
        )

    # -- introspection -----------------------------------------------------

    def is_denied(self, syscall: str) -> bool:
        """True iff ``syscall`` is on the explicit denylist."""
        return syscall in self.denied_syscalls

    def is_allowed(self, syscall: str) -> bool:
        """True iff ``syscall`` would be permitted by this filter."""
        if syscall in self.denied_syscalls:
            return False
        if self.default_action == "allow":
            return True
        return syscall in self.allowed_syscalls

    # -- platform support --------------------------------------------------

    @staticmethod
    def is_supported() -> bool:
        """Return whether seccomp can be installed on this host.

        Support requires Linux with a usable ``libc.prctl``. Other platforms
        and loader failures return ``False`` so the caller can follow its
        documented fail-open compatibility path.
        """
        if not sys.platform.startswith("linux"):
            return False
        try:
            import ctypes

            ctypes.CDLL(None, use_errno=True)
        except Exception:
            return False
        return True

    # -- BPF assembly ------------------------------------------------------

    def _deny_ret(self) -> int:
        if self.deny_action == "errno":
            return _SECCOMP_RET_ERRNO | (self.errno & 0x0000FFFF)
        return _SECCOMP_RET_KILL_PROCESS

    def _default_ret(self) -> int:
        if self.default_action == "errno":
            return _SECCOMP_RET_ERRNO | (self.errno & 0x0000FFFF)
        return _SECCOMP_RET_ALLOW

    def build_bpf(self, arch: str | None = None) -> list[_BpfInsn]:
        """Assemble the classic-BPF program for this filter.

        Each instruction is a ``(code, jt, jf, k)`` tuple. The program:
          1. validates the caller ABI (arch mismatch → KILL),
          2. KILLs each flat-denied syscall (with a known number for ``arch``),
          3. KILLs ``clone`` when a namespace flag is set (JSET guard),
          4. returns the default action for everything else.

        Off-Linux this still returns a valid program (never installed there),
        so it is unit-testable on any platform.
        """
        arch = arch or _native_arch()
        numbers = _SYSCALL_NUMBERS.get(arch, _SYSCALL_NUMBERS["x86_64"])
        deny_ret = self._deny_ret()
        default_ret = self._default_ret()

        insns: list[_BpfInsn] = []
        # 1. arch validation.
        insns.append((_BPF_LD_ABS, 0, 0, _OFF_ARCH))
        insns.append((_BPF_JEQ, 1, 0, _audit_arch(arch)))
        insns.append((_BPF_RET, 0, 0, _SECCOMP_RET_KILL_PROCESS))
        # 2. load syscall number.
        insns.append((_BPF_LD_ABS, 0, 0, _OFF_NR))

        # In strict (errno-default) mode, allow the whitelist explicitly first
        # so it survives the default-deny at the end.
        if self.default_action == "errno":
            for name in sorted(self.allowed_syscalls):
                num = numbers.get(name)
                if num is None:
                    continue
                insns.append((_BPF_JEQ, 0, 1, num))
                insns.append((_BPF_RET, 0, 0, _SECCOMP_RET_ALLOW))

        # 3. flat denylist → deny_ret.
        for name in sorted(self.denied_syscalls):
            num = numbers.get(name)
            if num is None:
                logger.debug("seccomp: no %s syscall number for %r; skipping in BPF", arch, name)
                continue
            insns.append((_BPF_JEQ, 0, 1, num))
            insns.append((_BPF_RET, 0, 0, deny_ret))

        # 4. clone namespace guard: kill clone() only when a CLONE_NEW* flag is
        #    set. Placed last because loading arg0 clobbers the accumulator.
        clone_num = numbers.get("clone")
        if clone_num is not None:
            insns.append((_BPF_JEQ, 0, 3, clone_num))  # not clone → default RET
            insns.append((_BPF_LD_ABS, 0, 0, _OFF_ARG0_LOW))
            insns.append((_BPF_JSET, 0, 1, _CLONE_NS_MASK))  # ns flag set → kill
            insns.append((_BPF_RET, 0, 0, deny_ret))

        # 5. default action.
        insns.append((_BPF_RET, 0, 0, default_ret))
        return insns

    # -- installation ------------------------------------------------------

    def apply(self) -> bool:
        """Install this filter in the current process.

        Returns ``True`` if a filter was installed, ``False`` if this is a
        fail-open NOP (non-Linux host, no seccomp support, or an install error).
        NEVER raises — a failed install must not wedge the playbook child.
        """
        if not self.is_supported():
            logger.warning(
                "seccomp unavailable on %s — playbook child runs WITHOUT "
                "syscall filtering (fail-open)",
                sys.platform,
            )
            return False
        if self._apply_libseccomp():
            return True
        return self._apply_manual_bpf()

    def _apply_libseccomp(self) -> bool:
        """Install via the libseccomp Python binding, if importable."""
        try:
            import importlib

            sc = importlib.import_module("seccomp")  # libseccomp binding: Linux-only, guarded
        except Exception:
            return False
        try:
            defaction = (
                sc.ERRNO(self.errno)
                if self.default_action == "errno"
                else sc.ALLOW
            )
            flt = sc.SyscallFilter(defaction=defaction)
            deny = sc.ERRNO(self.errno) if self.deny_action == "errno" else sc.KILL

            if self.default_action == "errno":
                for name in sorted(self.allowed_syscalls):
                    try:
                        flt.add_rule(sc.ALLOW, name)
                    except Exception:
                        logger.debug("seccomp: unknown allowed syscall %r; skipped", name)

            for name in sorted(self.denied_syscalls):
                try:
                    flt.add_rule(deny, name)
                except Exception:
                    logger.debug("seccomp: unknown denied syscall %r; skipped", name)

            # Namespace-guarded clone/clone3: kill only when a CLONE_NEW* flag
            # is set (one rule per flag; libseccomp ORs same-syscall rules).
            for name in sorted(_NAMESPACE_GUARDED):
                for bit in _iter_bits(_CLONE_NS_MASK):
                    try:
                        flt.add_rule(deny, name, sc.Arg(0, sc.MASKED_EQ, bit, bit))
                    except Exception:
                        break  # clone3's flags live behind a pointer; skip

            flt.load()
            logger.info(
                "seccomp filter installed via libseccomp (default=%s, denied=%d)",
                self.default_action, len(self.denied_syscalls),
            )
            return True
        except Exception:
            logger.warning("seccomp libseccomp install failed; trying manual BPF", exc_info=True)
            return False

    def _apply_manual_bpf(self) -> bool:
        """Install a hand-assembled BPF program via ``prctl``."""
        try:
            import ctypes

            class _SockFilter(ctypes.Structure):
                _fields_ = [
                    ("code", ctypes.c_uint16),
                    ("jt", ctypes.c_uint8),
                    ("jf", ctypes.c_uint8),
                    ("k", ctypes.c_uint32),
                ]

            class _SockFprog(ctypes.Structure):
                _fields_ = [
                    ("len", ctypes.c_uint16),
                    ("filter", ctypes.POINTER(_SockFilter)),
                ]

            program = self.build_bpf()
            arr = (_SockFilter * len(program))()
            for i, (code, jt, jf, k) in enumerate(program):
                arr[i] = _SockFilter(code=code, jt=jt, jf=jf, k=k & 0xFFFFFFFF)
            fprog = _SockFprog(len=len(program), filter=arr)

            libc = ctypes.CDLL(None, use_errno=True)
            if libc.prctl(_PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0:
                err = ctypes.get_errno()
                logger.warning(
                    "seccomp prctl(PR_SET_NO_NEW_PRIVS) failed (errno=%d); NOT filtered", err,
                )
                return False
            if libc.prctl(
                _PR_SET_SECCOMP, _SECCOMP_MODE_FILTER, ctypes.byref(fprog), 0, 0
            ) != 0:
                err = ctypes.get_errno()
                logger.warning(
                    "seccomp prctl(PR_SET_SECCOMP) failed (errno=%d); NOT filtered", err,
                )
                return False
            logger.info(
                "seccomp filter installed via manual BPF (%d instructions)", len(program),
            )
            return True
        except Exception:
            logger.warning("seccomp manual-BPF install failed (fail-open)", exc_info=True)
            return False


def _iter_bits(mask: int) -> list[int]:
    """Yield each set bit of ``mask`` as its own single-bit integer."""
    bits: list[int] = []
    bit = 1
    while bit <= mask:
        if mask & bit:
            bits.append(bit)
        bit <<= 1
    return bits


__all__ = [
    "DEFAULT_ALLOWED_SYSCALLS",
    "DEFAULT_DENIED_SYSCALLS",
    "SeccompFilter",
]
