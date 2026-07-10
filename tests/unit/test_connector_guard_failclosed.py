"""Fail-closed guards on the grafana_oncall and proc_sys connectors.

These tests pin two security regressions:

* ``grafana_oncall._guard_ssrf`` must FAIL CLOSED when DNS resolution itself
  fails. Previously a ``socket.getaddrinfo`` ``OSError`` was swallowed with a
  bare ``return`` (fail-open): an unresolvable host was silently accepted,
  which could mask an internal / rebinding target. It now raises ``ValueError``.

* ``proc_sys._is_confined`` must resolve symlinks (via ``os.path.realpath``)
  and require BOTH the lexical normpath AND the real path to live under an
  allowed root. This defeats a magic-symlink escape (e.g. a symlink under
  ``/proc`` whose target is ``/etc/shadow``).
"""

from __future__ import annotations

import os
import socket

import pytest

from general_ludd.connectors import proc_sys
from general_ludd.connectors.grafana_oncall import GrafanaOnCallSource, _guard_ssrf


# --------------------------------------------------------------------------- #
# grafana_oncall — DNS-failure fail-closed
# --------------------------------------------------------------------------- #
def test_guard_ssrf_fails_closed_when_resolution_raises(monkeypatch):
    """An unresolvable host must be REFUSED, not silently allowed."""

    def _boom(*_args, **_kwargs):
        raise socket.gaierror("name or service not known")

    monkeypatch.setattr(socket, "getaddrinfo", _boom)

    with pytest.raises(ValueError, match="could not be resolved"):
        _guard_ssrf("https://does-not-resolve.example.com", allow_private=False)


def test_source_construction_fails_closed_on_unresolvable_host(monkeypatch):
    """The fail-closed guard fires through the public constructor too."""

    def _boom(*_args, **_kwargs):
        raise OSError("resolution failed")

    monkeypatch.setattr(socket, "getaddrinfo", _boom)

    with pytest.raises(ValueError, match="could not be resolved"):
        GrafanaOnCallSource({"base_url": "https://nope.example.com"})


def test_guard_ssrf_allow_private_bypasses_resolution(monkeypatch):
    """allow_private short-circuits before any name resolution is attempted."""

    def _boom(*_args, **_kwargs):  # pragma: no cover - must never be called
        raise AssertionError("getaddrinfo must not be called when allow_private")

    monkeypatch.setattr(socket, "getaddrinfo", _boom)

    # No raise: allow_private returns immediately, resolution never runs.
    _guard_ssrf("https://anything.internal", allow_private=True)


def test_guard_ssrf_public_host_still_allowed(monkeypatch):
    """A host that resolves to a public address is accepted (no raise)."""

    def _public(*_args, **_kwargs):
        # getaddrinfo-shaped tuple: (family, type, proto, canonname, sockaddr)
        return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("93.184.216.34", 0))]

    monkeypatch.setattr(socket, "getaddrinfo", _public)

    _guard_ssrf("https://public.example.com", allow_private=False)


def test_guard_ssrf_private_resolution_still_rejected(monkeypatch):
    """Sanity: a host resolving to a private address is rejected as before."""

    def _private(*_args, **_kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("10.0.0.5", 0))]

    monkeypatch.setattr(socket, "getaddrinfo", _private)

    with pytest.raises(ValueError, match="non-public"):
        _guard_ssrf("https://internal.example.com", allow_private=False)


@pytest.mark.parametrize("host", ["metadata", "instance-data", "ip6-localhost"])
def test_guard_ssrf_rejects_metadata_alias_names_without_resolution(monkeypatch, host):
    """Literal metadata/loopback-alias names are refused via host_is_blocked
    before any DNS resolution -- closes a gap the connector's own 4-suffix
    name blocklist used to have (it never recognized these names at all).
    """

    def _boom(*_args, **_kwargs):  # pragma: no cover - must never be called
        raise AssertionError("getaddrinfo must not be called for a blocked literal name")

    monkeypatch.setattr(socket, "getaddrinfo", _boom)

    with pytest.raises(ValueError):
        _guard_ssrf(f"https://{host}", allow_private=False)


def test_guard_ssrf_resolved_cgnat_address_rejected(monkeypatch):
    """A hostname resolving into the 100.64.0.0/10 CGNAT range is rejected.

    is_private is False for this range in Python's ipaddress module, so the
    OLD local flag set (missing `not is_global`) would NOT have caught it;
    delegating classification to the canonical _ip_addr_is_blocked closes
    this gap.
    """

    def _cgnat(*_args, **_kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("100.70.1.1", 0))]

    monkeypatch.setattr(socket, "getaddrinfo", _cgnat)

    with pytest.raises(ValueError, match="non-public"):
        _guard_ssrf("https://cgnat-internal.example.com", allow_private=False)


# --------------------------------------------------------------------------- #
# proc_sys — symlink-resolving confinement
# --------------------------------------------------------------------------- #
def test_symlink_escape_under_allowed_root_is_rejected(tmp_path, monkeypatch):
    """A real on-disk symlink under the allowed root pointing OUTSIDE is refused."""
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "shadow"
    secret.write_text("root:!:::::::\n")

    # Symlink that lives inside the allowed root but targets a file outside it.
    evil_link = allowed / "escape"
    os.symlink(secret, evil_link)

    monkeypatch.setattr(proc_sys, "_ALLOWED_ROOTS", (str(allowed),))

    # normpath stays under the allowed root, but realpath escapes -> refused.
    assert proc_sys._is_confined(str(evil_link)) is False


def test_genuine_in_root_path_is_allowed(tmp_path, monkeypatch):
    """A real path that resolves to a location inside the root is confined-OK."""
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    real_file = allowed / "metric"
    real_file.write_text("42\n")

    monkeypatch.setattr(proc_sys, "_ALLOWED_ROOTS", (str(allowed),))

    assert proc_sys._is_confined(str(real_file)) is True
    # The root itself is also confined.
    assert proc_sys._is_confined(str(allowed)) is True


def test_in_root_symlink_to_in_root_target_is_allowed(tmp_path, monkeypatch):
    """A symlink whose target is ALSO inside the root stays confined."""
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    target = allowed / "real"
    target.write_text("ok\n")
    link = allowed / "link"
    os.symlink(target, link)

    monkeypatch.setattr(proc_sys, "_ALLOWED_ROOTS", (str(allowed),))

    assert proc_sys._is_confined(str(link)) is True


def test_dotdot_traversal_still_rejected(tmp_path, monkeypatch):
    """``..`` escaping the root is refused at the lexical-normpath stage."""
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    monkeypatch.setattr(proc_sys, "_ALLOWED_ROOTS", (str(allowed),))

    traversal = str(allowed / ".." / "etc" / "shadow")
    assert proc_sys._is_confined(traversal) is False


def test_absolute_path_outside_root_still_rejected(monkeypatch, tmp_path):
    """An absolute path outside any allowed root is refused."""
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    monkeypatch.setattr(proc_sys, "_ALLOWED_ROOTS", (str(allowed),))

    assert proc_sys._is_confined("/etc/shadow") is False


def test_relative_path_rejected(monkeypatch, tmp_path):
    """A non-absolute path is refused outright."""
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    monkeypatch.setattr(proc_sys, "_ALLOWED_ROOTS", (str(allowed),))

    assert proc_sys._is_confined("relative/path") is False


def test_read_passes_resolved_path_to_reader(tmp_path, monkeypatch):
    """``_read`` hands the reader the realpath of the confined normpath."""
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    target = allowed / "real"
    target.write_text("ok\n")
    link = allowed / "link"
    os.symlink(target, link)

    monkeypatch.setattr(proc_sys, "_ALLOWED_ROOTS", (str(allowed),))

    seen: list[str] = []

    def _reader(path: str) -> str:
        seen.append(path)
        return "content"

    source = proc_sys.ProcSysSource(reader=_reader)
    source._read(str(link))

    # The reader observed the fully resolved real target, not the symlink.
    assert seen == [os.path.realpath(str(target))]


# --------------------------------------------------------------------------- #
# gcp_asset_inventory — single-label (dot-less) internal hostname rejected
# --------------------------------------------------------------------------- #
def test_gcp_single_label_host_is_internal():
    """A dot-less hostname like ``internal`` cannot be a public FQDN -> internal."""
    from general_ludd.connectors.gcp_asset_inventory import _host_is_internal

    assert _host_is_internal("internal") is True
    assert _host_is_internal("metadata") is True  # already on the denylist
    # a genuine public FQDN is NOT internal
    assert _host_is_internal("cloudasset.googleapis.com") is False


def test_gcp_validate_endpoint_rejects_single_label_host():
    """The construction-time guard refuses a single-label endpoint host."""
    from general_ludd.connectors.gcp_asset_inventory import _validate_endpoint

    with pytest.raises(ValueError):
        _validate_endpoint("https://internal/")
    # a public FQDN endpoint is accepted
    assert (
        _validate_endpoint("https://cloudasset.googleapis.com")
        == "https://cloudasset.googleapis.com"
    )
