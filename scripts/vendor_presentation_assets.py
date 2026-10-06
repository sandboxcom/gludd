#!/usr/bin/env python3
"""Acquire pinned browser assets for the self-contained presentation."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import tempfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENDOR = ROOT / "docs" / "presentation" / "deck" / "vendor"


@dataclass(frozen=True)
class Asset:
    """One exact upstream file copied into the Pages artifact."""

    package: str
    version: str
    source: str
    destination: str
    license_source: str
    license_destination: str


def _cdn(package: str, version: str, source_path: str) -> str:
    return f"https://cdn.jsdelivr.net/npm/{package}@{version}/{source_path}"


REVEAL_LICENSE = _cdn("reveal.js", "5.1.0", "LICENSE")
MERMAID_LICENSE = _cdn("reveal.js-mermaid-plugin", "11.15.0", "LICENSE")
ACE_LICENSE = _cdn("ace-builds", "1.44.0", "LICENSE")

ASSETS = (
    Asset(
        "reveal.js", "5.1.0", _cdn("reveal.js", "5.1.0", "dist/reveal.css"),
        "reveal/reveal.css", REVEAL_LICENSE, "reveal/LICENSE",
    ),
    Asset(
        "reveal.js", "5.1.0", _cdn("reveal.js", "5.1.0", "dist/theme/black.css"),
        "reveal/theme/black.css", REVEAL_LICENSE, "reveal/LICENSE",
    ),
    Asset(
        "reveal.js", "5.1.0", _cdn("reveal.js", "5.1.0", "dist/reveal.js"),
        "reveal/reveal.js", REVEAL_LICENSE, "reveal/LICENSE",
    ),
    Asset(
        "reveal.js", "5.1.0", _cdn("reveal.js", "5.1.0", "plugin/highlight/highlight.js"),
        "reveal/plugin/highlight/highlight.js", REVEAL_LICENSE, "reveal/LICENSE",
    ),
    Asset(
        "reveal.js", "5.1.0", _cdn("reveal.js", "5.1.0", "plugin/notes/notes.js"),
        "reveal/plugin/notes/notes.js", REVEAL_LICENSE, "reveal/LICENSE",
    ),
    Asset(
        "reveal.js-mermaid-plugin", "11.15.0",
        _cdn("reveal.js-mermaid-plugin", "11.15.0", "plugin/mermaid/mermaid.js"),
        "mermaid/mermaid.js", MERMAID_LICENSE, "mermaid/LICENSE",
    ),
    Asset(
        "ace-builds", "1.44.0", _cdn("ace-builds", "1.44.0", "src-min-noconflict/ace.js"),
        "ace/ace.js", ACE_LICENSE, "ace/LICENSE",
    ),
    Asset(
        "ace-builds", "1.44.0", _cdn("ace-builds", "1.44.0", "src-min-noconflict/theme-tomorrow_night.js"),
        "ace/theme-tomorrow_night.js", ACE_LICENSE, "ace/LICENSE",
    ),
    Asset(
        "ace-builds", "1.44.0", _cdn("ace-builds", "1.44.0", "src-min-noconflict/mode-css.js"),
        "ace/mode-css.js", ACE_LICENSE, "ace/LICENSE",
    ),
    Asset(
        "ace-builds", "1.44.0", _cdn("ace-builds", "1.44.0", "src-min-noconflict/mode-html.js"),
        "ace/mode-html.js", ACE_LICENSE, "ace/LICENSE",
    ),
    Asset(
        "ace-builds", "1.44.0", _cdn("ace-builds", "1.44.0", "src-min-noconflict/mode-javascript.js"),
        "ace/mode-javascript.js", ACE_LICENSE, "ace/LICENSE",
    ),
    Asset(
        "ace-builds", "1.44.0", _cdn("ace-builds", "1.44.0", "src-min-noconflict/mode-json.js"),
        "ace/mode-json.js", ACE_LICENSE, "ace/LICENSE",
    ),
    Asset(
        "ace-builds", "1.44.0", _cdn("ace-builds", "1.44.0", "src-min-noconflict/mode-markdown.js"),
        "ace/mode-markdown.js", ACE_LICENSE, "ace/LICENSE",
    ),
    Asset(
        "ace-builds", "1.44.0", _cdn("ace-builds", "1.44.0", "src-min-noconflict/mode-python.js"),
        "ace/mode-python.js", ACE_LICENSE, "ace/LICENSE",
    ),
    Asset(
        "ace-builds", "1.44.0", _cdn("ace-builds", "1.44.0", "src-min-noconflict/mode-sh.js"),
        "ace/mode-sh.js", ACE_LICENSE, "ace/LICENSE",
    ),
    Asset(
        "ace-builds", "1.44.0", _cdn("ace-builds", "1.44.0", "src-min-noconflict/mode-yaml.js"),
        "ace/mode-yaml.js", ACE_LICENSE, "ace/LICENSE",
    ),
)


def _download(url: str) -> bytes:
    """Download one HTTPS resource with a bounded request."""
    request = urllib.request.Request(url, headers={"User-Agent": "gludd-presentation-vendor/1"})
    with urllib.request.urlopen(request, timeout=30) as response:
        if response.status != 200:
            raise RuntimeError(f"asset download returned HTTP {response.status}")
        payload = response.read(12 * 1024 * 1024 + 1)
    if not payload or len(payload) > 12 * 1024 * 1024:
        raise RuntimeError("asset is empty or exceeds the 12 MiB acquisition bound")
    return payload.replace(b"//# sourceMappingURL=", b"//# source map omitted: ")


def _expected_files() -> set[str]:
    """Return the exact owned files below the vendor directory."""
    files = {asset.destination for asset in ASSETS}
    files.update(asset.license_destination for asset in ASSETS)
    files.add("manifest.json")
    return files


def validate_assets(destination: Path = VENDOR) -> None:
    """Fail closed unless the vendor tree exactly matches its manifest."""
    try:
        manifest = json.loads((destination / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("presentation vendor manifest is missing or invalid") from exc
    if manifest.get("schema") != "gludd-presentation-vendor/v1":
        raise RuntimeError("presentation vendor manifest schema is invalid")
    entries = manifest.get("assets")
    if not isinstance(entries, list) or len(entries) != len(ASSETS):
        raise RuntimeError("presentation vendor manifest inventory is incomplete")
    expected = {(asset.package, asset.version, asset.source, asset.destination): asset for asset in ASSETS}
    seen: set[tuple[str, str, str, str]] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise RuntimeError("presentation vendor manifest entry is invalid")
        key = (
            str(entry.get("package", "")),
            str(entry.get("version", "")),
            str(entry.get("source", "")),
            str(entry.get("destination", "")),
        )
        asset = expected.get(key)
        if asset is None or key in seen:
            raise RuntimeError("presentation vendor manifest has an unexpected or duplicate asset")
        seen.add(key)
        try:
            asset_digest = hashlib.sha256((destination / asset.destination).read_bytes()).hexdigest()
            license_digest = hashlib.sha256((destination / asset.license_destination).read_bytes()).hexdigest()
        except OSError as exc:
            raise RuntimeError("presentation vendor file is missing") from exc
        if entry.get("sha256") != asset_digest:
            raise RuntimeError("presentation vendor asset digest mismatch")
        if entry.get("license") != asset.license_destination:
            raise RuntimeError("presentation vendor license path mismatch")
        if entry.get("license_sha256") != license_digest:
            raise RuntimeError("presentation vendor license digest mismatch")
    if seen != set(expected):
        raise RuntimeError("presentation vendor manifest is incomplete")
    actual = {
        path.relative_to(destination).as_posix()
        for path in destination.rglob("*")
        if path.is_file()
    }
    if actual != _expected_files():
        raise RuntimeError("presentation vendor tree has missing or unexpected files")


def refresh_assets(destination: Path = VENDOR) -> None:
    """Acquire assets into a sibling temporary tree and replace atomically."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=".gludd-presentation-vendor-",
        dir=destination.parent,
    ) as temporary:
        output = Path(temporary) / "vendor"
        manifest_assets: list[dict[str, str]] = []
        licenses: dict[tuple[str, str], bytes] = {}
        for asset in ASSETS:
            payload = _download(asset.source)
            asset_path = output / asset.destination
            asset_path.parent.mkdir(parents=True, exist_ok=True)
            asset_path.write_bytes(payload)
            license_key = (asset.license_source, asset.license_destination)
            if license_key not in licenses:
                licenses[license_key] = _download(asset.license_source)
            manifest_assets.append(
                {
                    "package": asset.package,
                    "version": asset.version,
                    "source": asset.source,
                    "sha256": hashlib.sha256(payload).hexdigest(),
                    "destination": asset.destination,
                    "license": asset.license_destination,
                    "license_sha256": hashlib.sha256(licenses[license_key]).hexdigest(),
                }
            )
        for (_, license_destination), payload in licenses.items():
            path = output / license_destination
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
        manifest = {
            "schema": "gludd-presentation-vendor/v1",
            "assets": manifest_assets,
        }
        (output / "manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        validate_assets(output)
        backup = destination.with_name(destination.name + ".previous")
        if backup.exists():
            shutil.rmtree(backup)
        if destination.exists():
            os.replace(destination, backup)
        try:
            os.replace(output, destination)
        except OSError:
            if backup.exists() and not destination.exists():
                os.replace(backup, destination)
            raise
        if backup.exists():
            shutil.rmtree(backup)
    validate_assets(destination)


def main() -> None:
    """Validate by default; refresh only after explicit operator intent."""
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--validate-only", action="store_true", help="verify without network or writes")
    mode.add_argument("--refresh", action="store_true", help="download and atomically replace pinned assets")
    args = parser.parse_args()
    if args.refresh:
        refresh_assets(VENDOR)
        print(f"Vendored {len(ASSETS)} pinned presentation assets into {VENDOR}")
    else:
        validate_assets(VENDOR)
        print(f"Validated {len(ASSETS)} pinned presentation assets in {VENDOR}")


if __name__ == "__main__":
    main()
