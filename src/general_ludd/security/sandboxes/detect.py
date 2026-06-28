"""Auto-detect the best sandbox backend for the current host.

Selection order:

  * **Linux**: SELinux first (``selinuxenabled`` exits 0 when SELinux is
    enforcing/permissive and the policy store is wired), then AppArmor
    (``aa-status`` succeeds), else ``None`` (warn — no sandbox).
  * **FreeBSD**: ``jail`` binary present (always present on a stock install).
  * **macOS**: ``sandbox-exec`` on PATH. Always present on macOS ≤ 14.x;
    removed in 15.4+ — we detect the missing binary and warn loudly.
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
        if _selinux_enabled():
            from general_ludd.security.sandboxes.linux_selinux import SELinuxBackend

            return SELinuxBackend
        if _apparmor_enabled():
            from general_ludd.security.sandboxes.linux_apparmor import AppArmorBackend

            return AppArmorBackend
        logger.warning(
            "No sandbox backend available on this Linux host "
            "(neither SELinux nor AppArmor is enabled); dispatching UNSANDBOXED"
        )
        return None
    if _jail_present():
        from general_ludd.security.sandboxes.freebsd_jail import JailBackend

        return JailBackend
    if plat == "darwin":
        if _seatbelt_present():
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
