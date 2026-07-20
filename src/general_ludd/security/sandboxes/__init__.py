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

The canonical ``PermissionSpec`` / ``Capability`` types live in
``general_ludd.security.permissions`` (the parallel permissions task). We
re-export them here so callers can keep importing from one place. The
constraint shape used by the backends is ``cap.constraints: dict[str, Any]``
with keys like ``path_prefix``, ``allowed_hosts``, ``allowed_ports`` — see
:mod:`general_ludd.security.permissions` for the full vocabulary.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from general_ludd.security.permissions import Capability, PermissionSpec

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
    popen: object | None = None
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
    token: str = field(repr=False)
    applied: bool = True
    extra: dict[str, object] = field(default_factory=dict)


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
    capability: Capability | None = None


# ---------------------------------------------------------------------------
# Constraint helpers (work against the canonical dict-based constraints)
# ---------------------------------------------------------------------------


def constraint_value(cap: Capability, key: str) -> Any | None:
    """Return ``cap.constraints[key]`` or ``None`` (dict-based constraints)."""
    return cap.constraints.get(key) if isinstance(cap.constraints, dict) else None


def path_prefix(cap: Capability) -> str | None:
    """Return the ``path_prefix`` constraint value as a string or None."""
    v = constraint_value(cap, "path_prefix")
    if isinstance(v, str):
        return v
    return None


def allowed_hosts(cap: Capability) -> list[str]:
    """Return the ``allowed_hosts`` constraint as a list (may be empty)."""
    v = constraint_value(cap, "allowed_hosts")
    if isinstance(v, (list, tuple)):
        return [str(h) for h in v]
    if isinstance(v, str):
        return [v]
    parts = cap.resource.split(":")
    if len(parts) >= 3 and parts[0] == "net":
        return [parts[2]]
    return []


def allowed_ports(cap: Capability) -> list[int]:
    """Return the ``allowed_ports`` constraint as a list of ints (may be empty)."""
    v = constraint_value(cap, "allowed_ports")
    if isinstance(v, (list, tuple)):
        return [int(p) for p in v]
    if isinstance(v, int):
        return [v]
    parts = cap.resource.split(":")
    if len(parts) >= 4 and parts[0] == "net":
        try:
            return [int(parts[3])]
        except ValueError:
            return []
    return []


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
        """True iff this backend is usable on the current host."""
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
    def verify(spec: PermissionSpec, handle: SandboxHandle) -> list[Finding]:
        """Re-read OS state and report any divergence between ``spec`` and
        what the kernel is actually enforcing.
        """
        ...

    @staticmethod
    def release(handle: SandboxHandle) -> None:
        """Tear down the sandbox (unload profile, stop jail, revoke SID).

        Best-effort + fail-open.
        """
        ...


__all__ = [
    "Capability",
    "Finding",
    "PermissionSpec",
    "SandboxBackend",
    "SandboxHandle",
    "SandboxTarget",
    "allowed_hosts",
    "allowed_ports",
    "constraint_value",
    "path_prefix",
]
