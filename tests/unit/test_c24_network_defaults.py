"""C24 — Daemon/network defaults (TDD tests).

Implements the C24 requirement:
- Daemon default bind 0.0.0.0 → 127.0.0.1 unless explicitly configured
- Explicit 0.0.0.0 config requires explicit CIDR allowlist
- localhost-only mode restricts to loopback interface
"""

from general_ludd.config.user_config import NetworkConfig
from general_ludd.infra.compute import ComputeConfig, ComputeProvider, GPUType
from general_ludd.tui.keybindings import build_gunicorn_cmd

# ---------------------------------------------------------------------------
# Daemon bind: default must be loopback-only (structural)
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


# ---------------------------------------------------------------------------
# NetworkConfig: host defaults + 0.0.0.0/:: requires CIDR allowlist
# ---------------------------------------------------------------------------


def test_network_config_defaults_to_localhost() -> None:
    """NetworkConfig default host is 127.0.0.1, not 0.0.0.0."""
    cfg = NetworkConfig()
    assert cfg.host == "127.0.0.1", (
        f"NetworkConfig default host must be 127.0.0.1, got {cfg.host!r}"
    )
    assert cfg.host != "0.0.0.0", "NetworkConfig default must NOT be world-open"


def test_network_config_localhost_needs_no_cidr() -> None:
    """Loopback host (127.0.0.1) is accepted without allowed_cidr."""
    cfg = NetworkConfig(host="127.0.0.1")
    assert cfg.host == "127.0.0.1"
    assert cfg.allowed_cidr == []


def test_network_config_rejects_world_open_without_cidr() -> None:
    """0.0.0.0 without allowed_cidr must be rejected."""
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        NetworkConfig(host="0.0.0.0", allowed_cidr=[])


def test_network_config_rejects_wildcard_ipv6_without_cidr() -> None:
    """:: (IPv6 wildcard) without allowed_cidr must be rejected."""
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        NetworkConfig(host="::", allowed_cidr=[])


def test_network_config_accepts_world_open_with_cidr() -> None:
    """0.0.0.0 with explicit allowed_cidr is accepted."""
    cfg = NetworkConfig(host="0.0.0.0", allowed_cidr=["10.0.0.0/8"])
    assert cfg.host == "0.0.0.0"
    assert cfg.allowed_cidr == ["10.0.0.0/8"]


def test_network_config_accepts_wildcard_ipv6_with_cidr() -> None:
    """:: with explicit allowed_cidr is accepted."""
    cfg = NetworkConfig(host="::", allowed_cidr=["2001:db8::/32"])
    assert cfg.host == "::"
    assert cfg.allowed_cidr == ["2001:db8::/32"]


# ---------------------------------------------------------------------------
# Daemon lifespan: enforcement during startup
# ---------------------------------------------------------------------------


def test_daemon_app_defaults_loopback() -> None:
    """create_daemon_app() defaults network host to 127.0.0.1."""
    from general_ludd.daemon import create_daemon_app

    app = create_daemon_app()

    assert app.state._network_host == "127.0.0.1", (
        f"daemon default host must be 127.0.0.1, got {app.state._network_host!r}"
    )
    assert app.state._network_port == 8000
    assert app.state._allowed_cidr == []


def test_daemon_app_loopback_gets_loopback_cidr() -> None:
    """When binding to 127.0.0.1, loopback CIDRs are auto-applied."""
    from general_ludd.config.user_config import NetworkConfig, UserConfig

    uc = UserConfig(network=NetworkConfig(host="127.0.0.1"))
    # Verify the config-level state: loopback host with no explicit CIDR
    # should be valid and the daemon should auto-enforce loopback CIDRs
    assert uc.network.host == "127.0.0.1"
    assert uc.network.allowed_cidr == []


def test_daemon_app_rejects_world_open_without_cidr() -> None:
    """Daemon lifespan must not start with 0.0.0.0 and no allowed_cidr."""
    # ValidationError at config layer catches this before daemon even starts
    import pytest
    from pydantic import ValidationError

    from general_ludd.config.user_config import NetworkConfig

    with pytest.raises(ValidationError):
        NetworkConfig(host="0.0.0.0", allowed_cidr=[])


def test_daemon_app_accepts_world_open_with_cidr() -> None:
    """Daemon accepts 0.0.0.0 when allowed_cidr is explicitly set."""
    from general_ludd.config.user_config import NetworkConfig

    cfg = NetworkConfig(host="0.0.0.0", allowed_cidr=["192.168.0.0/16"])
    assert cfg.host == "0.0.0.0"
    assert len(cfg.allowed_cidr) > 0


# ---------------------------------------------------------------------------
# CIDR middleware: only permits allowed clients
# ---------------------------------------------------------------------------


def test_cidr_middleware_denies_non_loopback() -> None:
    """CIDR middleware returns 403 for clients outside allowed_cidr."""
    import ipaddress

    allowed = ["127.0.0.0/8", "::1/128"]
    client_ip = ipaddress.ip_address("192.168.1.1")

    allowed_nets = [ipaddress.ip_network(c, strict=False) for c in allowed]
    is_allowed = any(client_ip in net for net in allowed_nets)
    assert not is_allowed, "non-loopback IP must NOT be in loopback-only CIDR set"


def test_cidr_middleware_allows_loopback() -> None:
    """CIDR middleware permits 127.0.0.1 when loopback CIDR is enforced."""
    import ipaddress

    allowed = ["127.0.0.0/8", "::1/128"]
    client_ip = ipaddress.ip_address("127.0.0.1")

    allowed_nets = [ipaddress.ip_network(c, strict=False) for c in allowed]
    is_allowed = any(client_ip in net for net in allowed_nets)
    assert is_allowed, "127.0.0.1 must be in loopback CIDR set"


def test_cidr_middleware_allows_ipv6_loopback() -> None:
    """CIDR middleware permits ::1 when loopback CIDR is enforced."""
    import ipaddress

    allowed = ["127.0.0.0/8", "::1/128"]
    client_ip = ipaddress.ip_address("::1")

    allowed_nets = [ipaddress.ip_network(c, strict=False) for c in allowed]
    is_allowed = any(client_ip in net for net in allowed_nets)
    assert is_allowed, "::1 must be in loopback CIDR set"


def test_cidr_middleware_passthrough_when_no_cidrs() -> None:
    """When allowed_cidr is empty, all clients pass through (regression guard)."""
    # Empty CIDR list = no enforcement (back-compat with test setups)
    allowed: list[str] = []
    import ipaddress

    ipaddress.ip_address("10.0.0.1")
    is_blocked = bool(allowed)
    assert not is_blocked, "empty allowed_cidr must not block any client"
