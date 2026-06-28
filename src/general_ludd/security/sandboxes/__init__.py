"""OS-level sandboxing backends that enforce a ``PermissionSpec`` at the system
level.

A ``SandboxBackend`` is selected by :func:`general_ludd.security.sandboxes.detect.auto`
based on the host OS and available kernel features, then used to:

  1. **apply**    — translate the spec into an OS-native artifact (AppArmor
                    profile, SELinux Type-Enforcement module, FreeBSD jail,
                    macOS ``sandbox.d`` profile, or Windows AppContainer SID)
                    and load/attach it to the target.
  2. **verify**   — re-read the OS state and report a list of
                    :class:`Finding` describing any divergence between the
                    spec and what the kernel is actually enforcing.
  3. **release**  — tear down the sandbox (unload profile, stop jail, revoke
                    SID) so the host is returned to its pre-apply state.

Every backend FAILS OPEN: if loading the sandbox raises (missing tool,
permission denied, unsupported kernel), the backend logs loudly and returns a
``SandboxHandle`` whose ``applied`` flag is ``False``. The daemon continues
to dispatch the agent with a "no sandbox" warning rather than wedging. The
``verify`` step is the trust anchor — applying without verifying is theater.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

# ---------------------------------------------------------------------------
# PermissionSpec shim
# ---------------------------------------------------------------------------
# The canonical schema lives in ``general_ludd.security.permissions`` (the
# parallel permissions task). When that module is present we re-export its
# types so callers can keep importing from here. When it is absent (the
# permissions task has not landed yet) we fall back to local definitions that
# match the documented shape so the sandboxes layer is independently usable
# and testable today. The two must agree on the field names below; if they
# ever diverge the test ``test_permission_spec_shim_is_consistent`` will fail.
# ---------------------------------------------------------------------------
try:
    from general_ludd.security.permissions import (  # type: ignore[attr-defined]
        Capability as Capability,
    )
    from general_ludd.security.permissions import (
        Constraint as Constraint,
    )
    from general_ludd.security.permissions import (
        PermissionSpec as PermissionSpec,
    )
except Exception:  # pragma: no cover - exercised only when permissions.py lands
    @dataclass(frozen=True)
    class Constraint:
        """A single constraint on a capability (e.g. path prefix, peer host).

        ``kind`` is a small closed vocabulary: ``"path_prefix"``, ``"host"``,
        ``"port"``, ``"proto"``. ``value`` is the constraint payload (a string
        for path/host/proto, an int for port).
        """

        kind: str
        value: str | int

    @dataclass(frozen=True)
    class Capability:
        """A granted capability: do ``actions`` on ``resource`` under
        ``constraints``.

        ``resource`` is one of ``"fs"``, ``"net"``, ``"process"``, ``"ipc"``,
        ``"sys"`` (the closed resource vocabulary). ``actions`` is the set of
        verbs (``"read"``, ``"write"``, ``"connect"``, ...). ``constraints``
        narrows the resource — e.g. ``fs`` + ``read`` + ``path_prefix=/tmp/gludd``.
        """

        resource: str
        actions: frozenset[str] = field(default_factory=frozenset)
        constraints: tuple[Constraint, ...] = field(default_factory=tuple)

        def has_constraint(self, kind: str) -> bool:
            return any(c.kind == kind for c in self.constraints)

        def constraint_value(self, kind: str) -> str | int | None:
            for c in self.constraints:
                if c.kind == kind:
                    return c.value
            return None

    @dataclass(frozen=True)
    class PermissionSpec:
        """A full permission specification: granted capabilities + explicit
        denies.

        ``agent_id`` scopes the sandbox identity (AppArmor profile name,
        SELinux domain, jail hostname, AppContainer SID). ``denied`` is a list
        of :class:`Capability` the agent must NEVER be able to exercise; the
        sandbox backend turns each into a ``deny`` rule that wins over any
        ``allow``.
        """

        agent_id: str
        capabilities: tuple[Capability, ...] = field(default_factory=tuple)
        denied: tuple[Capability, ...] = field(default_factory=tuple)


# ---------------------------------------------------------------------------
# Target / Handle / Finding
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SandboxTarget:
    """What is being sandboxed.

    Exactly one of ``pid`` / ``popen`` / ``directory`` / ``service`` should be
    populated; backends pick whichever is meaningful for their OS. ``pid`` and
    ``popen`` are for subprocess targets; ``directory`` is a chroot/jail root;
    ``service`` is a systemd unit name (Linux only).
    """

    pid: int | None = None
    popen: Any | None = None  # subprocess.Popen
    directory: str | None = None
    service: str | None = None


@dataclass
class SandboxHandle:
    """Opaque per-backend token returned by ``apply``.

    ``backend`` is the backend name (``"apparmor"``, ``"selinux"``, ``"jail"``,
    ``"seatbelt"``, ``"appcontainer"``). ``token`` is the backend-specific
    identifier (profile name, jail id, container SID, ...). ``applied`` is
    False when the backend fail-opened (sandbox NOT actually enforced); callers
    MUST log + dispatch with a "no sandbox" warning in that case.
    """

    backend: str
    token: str
    applied: bool = True
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Finding:
    """A single line of the ``verify`` report.

    ``severity`` is ``"ok"`` (spec == actual), ``"warn"`` (drift that does not
    break the security model), or ``"fail"`` (spec NOT enforced). ``capability``
    is the :class:`Capability` the finding refers to (or ``None`` for a
    whole-spec finding like "profile not loaded").
    """

    severity: str
    message: str
    capability: Any | None = None


# ---------------------------------------------------------------------------
# Backend Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class SandboxBackend(Protocol):
    """Interface every OS-level sandbox backend implements.

    Implementations MUST be safe to import on any OS (lazy-import their
    OS-specific modules inside ``apply`` / ``verify``) and MUST fail open.
    """

    name: str

    @staticmethod
    def available() -> bool:
        """True iff this backend is usable on the current host.

        Implementations probe for the kernel feature AND the userland tool
        (``apparmor_parser``, ``checkmodule``, ``jail``, ``sandbox-exec``,
        ``pywin32``). Cheap — no side effects, no exceptions.
        """
        ...

    @staticmethod
    def apply(spec: PermissionSpec, target: SandboxTarget) -> SandboxHandle:
        """Translate ``spec`` into an OS-native artifact and attach it to
        ``target``. Returns an opaque :class:`SandboxHandle`.

        Fail-open contract: if loading the sandbox raises, log loudly and
        return a handle whose ``applied`` is ``False`` rather than propagating.
        """
        ...

    @staticmethod
    def verify(
        spec: PermissionSpec, handle: SandboxHandle
    ) -> list[Finding]:
        """Re-read OS state and report any divergence between ``spec`` and
        what the kernel is actually enforcing.

        This is the trust anchor: applying without verifying is theater.
        """
        ...

    @staticmethod
    def release(handle: SandboxHandle) -> None:
        """Tear down the sandbox (unload profile, stop jail, revoke SID).

        Best-effort + fail-open: a release failure is logged but does not
        propagate (the daemon must keep running even if cleanup is partial).
        """
        ...


__all__ = [
    "Capability",
    "Constraint",
    "Finding",
    "PermissionSpec",
    "SandboxBackend",
    "SandboxHandle",
    "SandboxTarget",
]
