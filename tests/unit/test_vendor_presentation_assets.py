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
