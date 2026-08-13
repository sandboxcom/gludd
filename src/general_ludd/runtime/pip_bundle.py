"""Pip bundle builder for release artifacts."""

from __future__ import annotations

import hashlib
import os
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, field_validator

from general_ludd.runtime.manifest_signer import ManifestSigner

# Characters that must never appear in an output dir. Even though the subprocess
# calls use argv list-form (no shell), these are rejected as defense-in-depth so
# a future refactor to a shell form cannot reintroduce an injection sink, and so
# NUL/newline can never corrupt the argv or a written manifest path.
_FORBIDDEN_DIR_CHARS = set(";|&`$<>\n\r\t\x00")


def _validate_output_dir(output_dir: str) -> str:
    """Validate a caller/config-derived output dir before it reaches argv.

    ``output_dir`` flows in untrusted (release_orchestrator -> make
    release-validate). The build interpolates it into
    ``uv build --out-dir <output_dir>`` and runs ``os.makedirs`` on it. Reject:

      * empty/whitespace-only values,
      * leading-dash values (``--out-dir=...``, ``-rf``) — argv/flag smuggling
        into ``uv``,
      * shell metacharacters / NUL / newline (defense-in-depth),
      * ``..`` path-traversal components (escape the intended artifacts root).

    Returns the stripped dir on success; raises ``ValueError`` (fail closed)
    otherwise — before any subprocess spawn or directory creation.
    """
    if not isinstance(output_dir, str):
        raise ValueError("output_dir must be a string")
    candidate = output_dir.strip()
    if not candidate:
        raise ValueError("output_dir must not be empty")
    if candidate.startswith("-"):
        raise ValueError(f"output_dir must not start with '-' (flag smuggling): {output_dir!r}")
    bad = _FORBIDDEN_DIR_CHARS & set(candidate)
    if bad:
        raise ValueError(
            f"output_dir contains forbidden characters {sorted(bad)!r}: {output_dir!r}"
        )
    parts = candidate.replace("\\", "/").split("/")
    if ".." in parts:
        raise ValueError(f"output_dir must not contain '..' traversal: {output_dir!r}")
    return candidate


class BundleManifest(BaseModel):
    version: str
    commit: str
    timestamp: str
    files: list[str]
    checksums: dict[str, str]

    @field_validator("version", mode="before")
    @classmethod
    def _strip_and_require(cls, v: str) -> str:
        if isinstance(v, str):
            v = v.strip()
        if not v:
            raise ValueError("version must not be empty")
        return v


@dataclass
class BundleResult:
    bundle_path: str
    wheel_path: str
    sdist_path: str
    manifest_path: str
    checksum_path: str
    success: bool
    sig_path: str = ""
    signature_valid: bool = False


class PipBundleBuilder:
    def __init__(self, signer: ManifestSigner | None = None) -> None:
        self._signer = signer or ManifestSigner()

    def build(self, output_dir: str, version: str) -> BundleResult:
        # Fail closed on injection-y dirs BEFORE any makedirs/subprocess spawn.
        output_dir = _validate_output_dir(output_dir)
        os.makedirs(output_dir, exist_ok=True)

        # Metadata is regenerated below.  Leaving a prior manifest, checksum
        # list, or signature in place would cause the new manifest to hash
        # those stale bytes and then overwrite them, invalidating its own
        # integrity record.
        for name in ("MANIFEST.json", "MANIFEST.json.sig", "CHECKSUMS.sha256"):
            generated_path = Path(output_dir, name)
            if generated_path.exists():
                generated_path.unlink()

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

        try:
            sig_result = self._signer.sign(manifest_path)
            sig_path = sig_result.sig_path if sig_result.success else ""
            signature_valid = sig_result.success
        except Exception:
            sig_path = ""
            signature_valid = False

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
            sig_path=sig_path,
            signature_valid=signature_valid,
        )
