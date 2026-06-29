"""Auto-detect the best sandbox backend for the current host.

Selection order (informed by the permission/sandbox research survey —
Linux per-task sandboxing is best served by Landlock + bubblewrap, which are
per-process / unprivileged; AppArmor + SELinux are demoted to host-level
defense-in-depth):

  * **Linux**:
      1. **Landlock** (kernel ≥ 6.7, pylandlock importable) — per-process,
         unprivileged, fine-grained. The best fit for per-agent sandboxing.
      2. **bubblewrap** (``bwrap`` binary present) — namespace-isolated
         subprocess jail; secondary when Landlock is unavailable.
      3. **AppArmor** (``aa-status`` succeeds) — system-wide; demoted to
         defense-in-depth.
      4. **SELinux** (``getenforce`` Enforcing) — system-wide; demoted to
         defense-in-depth.
      5. ``None`` (warn — dispatch UNSANDBOXED).
  * **FreeBSD**: ``jail`` binary present (always present on a stock install).
  * **macOS**: ``sandbox-exec`` on PATH AND macOS < 15.4 (sandbox-exec is
    DEPRECATED in 15.4+ with no replacement; on those hosts ``auto()``
    returns ``None`` and warns loudly).
  * **Windows**: ``pywin32`` importable AND ``CreateAppContainerProfile`` is
    resolvable in ``win32``, else ``None``.

Returns a :class:`SandboxBackend` Protocol implementation or ``None`` if no
backend is usable on this host. The caller is responsible for warning +
dispatching with a "no sandbox" notice when this returns ``None``.
"""

from __future__ import annotations

import logging
import shutil
import sys

from general_ludd.security.sandboxes import SandboxBackend

logger = logging.getLogger(__name__)


def _landlock_available() -> bool:
    """True iff pylandlock is importable AND the kernel reports a non-zero ABI.

    Landlock requires kernel ≥ 5.13 (filesystem) and ≥ 6.7 (network). We probe
    ``Ruleset().abi`` rather than uname so we read the actual LSM state.
    """
    try:
        from landlock import Ruleset  # type: ignore[import-not-found]
        return Ruleset().abi > 0
    except Exception:
        return False


def _bubblewrap_present() -> bool:
    """True iff the ``bwrap`` binary is on PATH (Linux)."""
    return shutil.which("bwrap") is not None


def _selinux_enabled() -> bool:
    """True iff SELinux is enabled + the policy toolchain is present."""
    try:
        import selinux  # type: ignore[import-not-found]
    except Exception:
        # python3-libselinux not installed — still check the userland toolchain
        # so an AppArmor-only host doesn't falsely look SELinux-capable.
        pass
    else:
        try:
            if selinux.is_selinux_enabled():  # pyright: ignore[reportPossiblyUnboundVariable]
                return shutil.which("checkmodule") is not None
        except Exception:
            pass
    # Fallback: ``selinuxenabled`` exits 0 iff enabled.
    if shutil.which("selinuxenabled") is None:
        return False
    import subprocess
    try:
        rc = subprocess.run(
            ["selinuxenabled"], check=False, capture_output=True, timeout=2,
        ).returncode
    except Exception:
        return False
    if rc != 0:
        return False
    return shutil.which("checkmodule") is not None


def _apparmor_enabled() -> bool:
    """True iff AppArmor is loaded + ``apparmor_parser`` is present."""
    if shutil.which("apparmor_parser") is None:
        return False
    if shutil.which("aa-status") is None:
        return False
    import subprocess
    try:
        rc = subprocess.run(
            ["aa-status"], check=False, capture_output=True, timeout=2,
        ).returncode
    except Exception:
        return False
    return rc == 0


def _jail_present() -> bool:
    """True iff FreeBSD ``jail`` is on PATH."""
    return sys.platform.startswith("freebsd") and shutil.which("jail") is not None


def _seatbelt_present() -> bool:
    """True iff macOS ``sandbox-exec`` is on PATH (gone in 15.4+)."""
    return sys.platform == "darwin" and shutil.which("sandbox-exec") is not None


def _appcontainer_present() -> bool:
    """True iff Windows + pywin32 + AppContainer API is usable."""
    if not sys.platform.startswith("win"):
        return False
    try:
        import win32  # type: ignore[import-not-found]  # noqa: F401
    except Exception:
        return False
    return True


def auto() -> SandboxBackend | None:
    """Pick the right backend for the host, or ``None`` (with a warning) if
    none is usable."""
    plat = sys.platform
    if plat.startswith("linux"):
        # 1. Landlock — per-process, unprivileged (preferred for per-agent).
        if _landlock_available():
            from general_ludd.security.sandboxes.linux_landlock import LandlockBackend

            return LandlockBackend
        # 2. bubblewrap — namespace jail (secondary).
        if _bubblewrap_present():
            from general_ludd.security.sandboxes.linux_bubblewrap import BubblewrapBackend

            return BubblewrapBackend
        # 3. AppArmor — system-wide, demoted to defense-in-depth.
        if _apparmor_enabled():
            from general_ludd.security.sandboxes.linux_apparmor import AppArmorBackend

            logger.warning(
                "AppArmor selected as backend — this is system-wide "
                "defense-in-depth, NOT a per-process sandbox. Consider "
                "installing pylandlock (pip install general-ludd-agent[sandbox]) "
                "or bubblewrap (apt install bubblewrap) for per-agent isolation."
            )
            return AppArmorBackend
        # 4. SELinux — system-wide, demoted to defense-in-depth.
        if _selinux_enabled():
            from general_ludd.security.sandboxes.linux_selinux import SELinuxBackend

            logger.warning(
                "SELinux selected as backend — this is system-wide "
                "defense-in-depth, NOT a per-process sandbox. Consider "
                "installing pylandlock (pip install general-ludd-agent[sandbox]) "
                "or bubblewrap (apt install bubblewrap) for per-agent isolation."
            )
            return SELinuxBackend
        logger.warning(
            "No sandbox backend available on this Linux host "
            "(neither Landlock nor bubblewrap nor AppArmor nor SELinux); "
            "dispatching UNSANDBOXED. Install pylandlock "
            "(pip install general-ludd-agent[sandbox]) or bubblewrap "
            "(apt install bubblewrap) for per-agent isolation."
        )
        return None
    if _jail_present():
        from general_ludd.security.sandboxes.freebsd_jail import JailBackend

        return JailBackend
    if plat == "darwin":
        if _seatbelt_present():
            # Even if the binary is present, refuse on macOS 15.4+ (deprecated).
            try:
                from general_ludd.security.sandboxes.macos_seatbelt import (
                    _is_deprecated_host,
                )
                deprecated = _is_deprecated_host()
            except Exception:
                deprecated = False
            if deprecated:
                import platform
                logger.error(
                    "sandbox-exec present but DEPRECATED on macOS %s (>= 15.4); "
                    "dispatching UNSANDBOXED. Run untrusted agent work in a "
                    "Linux VM (Tart/Lima).", platform.mac_ver()[0],
                )
                return None
            from general_ludd.security.sandboxes.macos_seatbelt import SeatbeltBackend

            return SeatbeltBackend
        import platform
        logger.warning(
            "sandbox-exec is NOT present on this macOS host (removed in 15.4+; "
            "current=%s); dispatching UNSANDBOXED. Apple has marked Seatbelt "
            "deprecated — there is no supported replacement for arbitrary "
            "sandbox profiles; document this risk for your deployment.",
            platform.mac_ver()[0],
        )
        return None
    if plat.startswith("win"):
        if _appcontainer_present():
            from general_ludd.security.sandboxes.windows_appcontainer import (
                AppContainerBackend,
            )

            return AppContainerBackend
        logger.warning(
            "AppContainer backend unavailable on Windows "
            "(pywin32 not installed or AppContainer API absent); "
            "dispatching UNSANDBOXED"
        )
        return None
    logger.warning("No sandbox backend implemented for platform %r", plat)
    return None


__all__ = ["auto"]
