"""Tests for pinned, self-contained presentation browser assets."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from scripts import vendor_presentation_assets as vendor


def _payload(url: str) -> bytes:
    """Return stable synthetic bytes for one pinned URL."""
    return f"owned:{url}\n".encode()


def test_refresh_and_validate_exact_vendor_tree(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Refresh writes an attributed exact manifest that validates offline."""
    destination = tmp_path / "vendor"
    monkeypatch.setattr(vendor, "_download", _payload)

    vendor.refresh_assets(destination)
    vendor.validate_assets(destination)

    manifest = json.loads((destination / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["schema"] == "gludd-presentation-vendor/v1"
    assert len(manifest["assets"]) == len(vendor.ASSETS)
    for entry in manifest["assets"]:
        asset = destination / entry["destination"]
        license_path = destination / entry["license"]
        assert entry["sha256"] == hashlib.sha256(asset.read_bytes()).hexdigest()
        assert entry["license_sha256"] == hashlib.sha256(license_path.read_bytes()).hexdigest()

    previous = destination.with_name("vendor.previous")
    previous.mkdir()
    (previous / "stale").write_text("old", encoding="utf-8")
    vendor.refresh_assets(destination)
    vendor.validate_assets(destination)
    assert not previous.exists()


def test_validate_rejects_asset_tamper(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A changed browser bundle cannot enter the Pages upload directory."""
    destination = tmp_path / "vendor"
    monkeypatch.setattr(vendor, "_download", _payload)
    vendor.refresh_assets(destination)
    first = vendor.ASSETS[0]
    (destination / first.destination).write_text("tampered", encoding="utf-8")

    with pytest.raises(RuntimeError, match="digest mismatch"):
        vendor.validate_assets(destination)


def test_validate_rejects_unexpected_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Unattributed executable content cannot silently join the artifact."""
    destination = tmp_path / "vendor"
    monkeypatch.setattr(vendor, "_download", _payload)
    vendor.refresh_assets(destination)
    (destination / "surprise.js").write_text("alert(1)", encoding="utf-8")

    with pytest.raises(RuntimeError, match="missing or unexpected"):
        vendor.validate_assets(destination)


def test_validate_only_cli_never_downloads(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The default CI path is offline and cannot mutate committed assets."""
    destination = tmp_path / "vendor"
    monkeypatch.setattr(vendor, "_download", _payload)
    vendor.refresh_assets(destination)
    monkeypatch.setattr(vendor, "VENDOR", destination)

    def forbidden_download(url: str) -> bytes:
        raise AssertionError(f"unexpected network call: {url}")

    monkeypatch.setattr(vendor, "_download", forbidden_download)
    monkeypatch.setattr("sys.argv", ["vendor_presentation_assets.py", "--validate-only"])

    vendor.main()


@pytest.mark.parametrize(
    ("package", "version"),
    (
        ("reveal.js", "5.1.0"),
        ("reveal.js-mermaid-plugin", "11.15.0"),
        ("ace-builds", "1.44.0"),
    ),
)
def test_inventory_uses_exact_versions(package: str, version: str) -> None:
    """No presentation dependency may resolve a floating version."""
    matching = [asset for asset in vendor.ASSETS if asset.package == package]
    assert matching
    assert {asset.version for asset in matching} == {version}
    assert all(f"@{version}/" in asset.source for asset in matching)


class _Response:
    """Minimal bounded urllib response used without network access."""

    def __init__(self, payload: bytes, status: int = 200) -> None:
        self.payload = payload
        self.status = status
        self.read_bound: int | None = None

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self, bound: int) -> bytes:
        self.read_bound = bound
        return self.payload


def test_download_is_bounded_and_strips_source_map_directive(monkeypatch: pytest.MonkeyPatch) -> None:
    """Acquisition enforces HTTP/content bounds and removes network sourcemaps."""
    response = _Response(b"code\n//# sourceMappingURL=remote.map\n")
    monkeypatch.setattr(vendor.urllib.request, "urlopen", lambda *args, **kwargs: response)
    payload = vendor._download("https://example.invalid/pinned.js")
    assert b"source map omitted: remote.map" in payload
    assert response.read_bound == 12 * 1024 * 1024 + 1

    monkeypatch.setattr(
        vendor.urllib.request,
        "urlopen",
        lambda *args, **kwargs: _Response(b"failure", status=503),
    )
    with pytest.raises(RuntimeError, match="HTTP 503"):
        vendor._download("https://example.invalid/pinned.js")

    monkeypatch.setattr(
        vendor.urllib.request,
        "urlopen",
        lambda *args, **kwargs: _Response(b""),
    )
    with pytest.raises(RuntimeError, match="empty or exceeds"):
        vendor._download("https://example.invalid/pinned.js")


def test_validate_rejects_each_manifest_trust_boundary(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Every manifest field is required and bound to committed bytes."""
    destination = tmp_path / "vendor"
    monkeypatch.setattr(vendor, "_download", _payload)
    vendor.refresh_assets(destination)
    manifest_path = destination / "manifest.json"
    original = json.loads(manifest_path.read_text(encoding="utf-8"))

    manifest_path.write_text("not-json", encoding="utf-8")
    with pytest.raises(RuntimeError, match="missing or invalid"):
        vendor.validate_assets(destination)

    def write(payload: dict[str, object]) -> None:
        manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    malformed = dict(original)
    malformed["schema"] = "wrong"
    write(malformed)
    with pytest.raises(RuntimeError, match="schema is invalid"):
        vendor.validate_assets(destination)

    malformed = dict(original)
    malformed["assets"] = []
    write(malformed)
    with pytest.raises(RuntimeError, match="inventory is incomplete"):
        vendor.validate_assets(destination)

    malformed = dict(original)
    malformed["assets"] = [None] * len(vendor.ASSETS)
    write(malformed)
    with pytest.raises(RuntimeError, match="entry is invalid"):
        vendor.validate_assets(destination)

    malformed = json.loads(json.dumps(original))
    malformed["assets"][0]["version"] = "floating"
    write(malformed)
    with pytest.raises(RuntimeError, match="unexpected or duplicate"):
        vendor.validate_assets(destination)

    write(original)
    first = vendor.ASSETS[0]
    (destination / first.destination).unlink()
    with pytest.raises(RuntimeError, match="file is missing"):
        vendor.validate_assets(destination)

    vendor.refresh_assets(destination)
    original = json.loads(manifest_path.read_text(encoding="utf-8"))
    malformed = json.loads(json.dumps(original))
    malformed["assets"][0]["license"] = "untrusted/LICENSE"
    write(malformed)
    with pytest.raises(RuntimeError, match="license path mismatch"):
        vendor.validate_assets(destination)

    malformed = json.loads(json.dumps(original))
    malformed["assets"][0]["license_sha256"] = "0" * 64
    write(malformed)
    with pytest.raises(RuntimeError, match="license digest mismatch"):
        vendor.validate_assets(destination)


def test_refresh_restores_previous_tree_if_atomic_swap_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A failed final rename leaves the previously validated vendor tree intact."""
    destination = tmp_path / "vendor"
    monkeypatch.setattr(vendor, "_download", _payload)
    vendor.refresh_assets(destination)
    original_replace = vendor.os.replace
    calls = 0

    def fail_second_replace(source: Path, target: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("synthetic swap failure")
        original_replace(source, target)

    monkeypatch.setattr(vendor.os, "replace", fail_second_replace)
    with pytest.raises(OSError, match="synthetic swap failure"):
        vendor.refresh_assets(destination)
    vendor.validate_assets(destination)
    assert calls == 3


def test_refresh_cli_delegates_only_after_explicit_flag(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The mutating CLI branch invokes refresh for the configured destination."""
    destination = tmp_path / "vendor"
    calls: list[Path] = []
    monkeypatch.setattr(vendor, "VENDOR", destination)
    monkeypatch.setattr(vendor, "refresh_assets", calls.append)
    monkeypatch.setattr("sys.argv", ["vendor_presentation_assets.py", "--refresh"])
    vendor.main()
    assert calls == [destination]
