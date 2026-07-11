"""C24 — Daemon/network defaults (TDD tests).

Implements the C24 requirement from AGENTIC_IMPLEMENTATION_SPEC.md:
- Daemon default bind 0.0.0.0 → 127.0.0.1 unless explicitly configured
- Compute allowed_cidr 0.0.0.0/0 default → require explicit CIDR
"""

from general_ludd.infra.compute import ComputeConfig, ComputeProvider, GPUType
from general_ludd.tui.keybindings import build_gunicorn_cmd

# ---------------------------------------------------------------------------
# Daemon bind: default must be loopback-only
# ---------------------------------------------------------------------------


def test_default_bind_is_localhost_keybindings() -> None:
    """TUI keybindings daemon host default is 127.0.0.1, not 0.0.0.0."""
    from general_ludd.tui.keybindings import _DAEMON_HOST_DEFAULT
    assert _DAEMON_HOST_DEFAULT == "127.0.0.1", (
        f"keybindings default host must be 127.0.0.1, got {_DAEMON_HOST_DEFAULT!r}"
    )
    assert _DAEMON_HOST_DEFAULT != "0.0.0.0", (
        "keybindings default host must NOT be world-open (0.0.0.0)"
    )


def test_default_bind_is_localhost_runner() -> None:
    """TUI runner daemon host fallback is 127.0.0.1, not 0.0.0.0."""
    from general_ludd.tui.runner import _DAEMON_HOST_DEFAULT
    assert _DAEMON_HOST_DEFAULT == "127.0.0.1", (
        f"runner default host must be 127.0.0.1, got {_DAEMON_HOST_DEFAULT!r}"
    )
    assert _DAEMON_HOST_DEFAULT != "0.0.0.0", (
        "runner default host must NOT be world-open (0.0.0.0)"
    )


def test_bind_override_to_public() -> None:
    """Explicit host=0.0.0.0 is honored for intentional public binding."""
    cmd = build_gunicorn_cmd(host="0.0.0.0", port=8000, workers=1)
    assert "--bind" in cmd
    bind_idx = cmd.index("--bind")
    bind_value = cmd[bind_idx + 1]
    host = bind_value.split(":")[0]
    assert host == "0.0.0.0", f"explicit host override must be respected, got {host!r}"


# ---------------------------------------------------------------------------
# allowed_cidr: default must not be world-open
# ---------------------------------------------------------------------------


def test_allowed_cidr_requires_explicit() -> None:
    """Default CIDR is not 0.0.0.0/0; world-open must be set explicitly."""
    cfg = ComputeConfig(provider=ComputeProvider.AWS, gpu_type=GPUType.T4)
    assert cfg.allowed_cidr == "127.0.0.1/32", (
        f"default allowed_cidr must be loopback-only, got {cfg.allowed_cidr!r}"
    )
    assert cfg.allowed_cidr != "0.0.0.0/0", (
        "default allowed_cidr must NOT be world-open (0.0.0.0/0)"
    )

    cfg_explicit = ComputeConfig(
        provider=ComputeProvider.AWS,
        gpu_type=GPUType.T4,
        allowed_cidr="10.0.0.0/8",
    )
    assert cfg_explicit.allowed_cidr == "10.0.0.0/8", (
        "explicit allowed_cidr must be respected"
    )
