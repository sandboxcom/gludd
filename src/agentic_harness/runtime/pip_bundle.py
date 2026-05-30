"""Pip bundle builder for release artifacts."""

from __future__ import annotations

import hashlib
import os
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel


class BundleManifest(BaseModel):
    version: str
    commit: str
    timestamp: str
    files: list[str]
    checksums: dict[str, str]


@dataclass
class BundleResult:
    bundle_path: str
    wheel_path: str
    sdist_path: str
    manifest_path: str
    checksum_path: str
    success: bool


class PipBundleBuilder:
    def build(self, output_dir: str, version: str) -> BundleResult:
        os.makedirs(output_dir, exist_ok=True)

        build_result = subprocess.run(
            ["uv", "build", "--out-dir", output_dir],
            capture_output=True,
            text=True,
            timeout=120,
        )

        if build_result.returncode != 0:
            return BundleResult(
                bundle_path=output_dir,
                wheel_path="",
                sdist_path="",
                manifest_path="",
                checksum_path="",
                success=False,
            )

        try:
            files_in_dir = os.listdir(output_dir)
        except OSError:
            files_in_dir = []

        wheel_path = ""
        sdist_path = ""
        for f in files_in_dir:
            if f.endswith(".whl"):
                wheel_path = os.path.join(output_dir, f)
            elif f.endswith(".tar.gz"):
                sdist_path = os.path.join(output_dir, f)

        checksums: dict[str, str] = {}
        for fname in files_in_dir:
            fpath = os.path.join(output_dir, fname)
            if os.path.isfile(fpath):
                h = hashlib.sha256(Path(fpath).read_bytes()).hexdigest()
                checksums[fname] = f"sha256:{h}"

        try:
            commit_result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            commit = commit_result.stdout.strip() if commit_result.returncode == 0 else "unknown"
        except (FileNotFoundError, subprocess.TimeoutExpired):
            commit = "unknown"

        manifest = BundleManifest(
            version=version,
            commit=commit,
            timestamp=datetime.now(UTC).isoformat(),
            files=files_in_dir,
            checksums=checksums,
        )

        manifest_path = os.path.join(output_dir, "MANIFEST.json")
        Path(manifest_path).write_text(manifest.model_dump_json(indent=2))

        checksum_lines = [f"{v}  {k}" for k, v in checksums.items()]
        checksum_path = os.path.join(output_dir, "CHECKSUMS.sha256")
        Path(checksum_path).write_text("\n".join(checksum_lines) + "\n")

        return BundleResult(
            bundle_path=output_dir,
            wheel_path=wheel_path,
            sdist_path=sdist_path,
            manifest_path=manifest_path,
            checksum_path=checksum_path,
            success=True,
        )
