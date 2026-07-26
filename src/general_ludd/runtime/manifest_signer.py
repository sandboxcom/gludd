"""Bundle manifest signing and verification via SSH key.

Signs MANIFEST.json with ``ssh-keygen -Y sign`` and verifies with
``ssh-keygen -Y verify`` using an allowed-signers file. Follows the
same SSH-signing protocol that git uses for signed commits/tags.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

_MANIFEST_NAMESPACE = "file"
_SIGNER_IDENTITY = "release-bundle"
_BUFFER = 4096


@dataclass
class SignResult:
    success: bool
    sig_path: str
    errors: list[str] = field(default_factory=list)


@dataclass
class VerifyResult:
    success: bool
    identity: str
    errors: list[str] = field(default_factory=list)


class ManifestSigner:
    def __init__(
        self,
        private_key_path: str | None = None,
        allowed_signers_path: str | None = None,
    ) -> None:
        self._private_key = private_key_path or os.environ.get(
            "GLUDD_SIGNING_KEY", os.path.expanduser("~/.ssh/id_ed25519")
        )
        self._allowed_signers = allowed_signers_path or os.environ.get(
            "GLUDD_ALLOWED_SIGNERS", os.path.expanduser("~/.ssh/allowed_signers")
        )

    @property
    def private_key_path(self) -> str:
        """Return the configured private-key path without exposing key material."""
        return self._private_key

    def sign(self, manifest_path: str) -> SignResult:
        errors: list[str] = []
        sig_path = f"{manifest_path}.sig"

        if not Path(self._private_key).exists():
            errors.append(f"Signing key not found: {self._private_key}")
            return SignResult(success=False, sig_path=sig_path, errors=errors)

        if not Path(manifest_path).exists():
            errors.append(f"Manifest not found: {manifest_path}")
            return SignResult(success=False, sig_path=sig_path, errors=errors)

        try:
            with open(manifest_path, "rb") as fh:
                result = subprocess.run(
                    [
                        "ssh-keygen",
                        "-Y", "sign",
                        "-f", self._private_key,
                        "-n", _MANIFEST_NAMESPACE,
                    ],
                    stdin=fh,
                    capture_output=True,
                    timeout=15,
                )
        except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
            errors.append(f"ssh-keygen sign failed: {exc}")
            return SignResult(success=False, sig_path=sig_path, errors=errors)

        if result.returncode != 0:
            stderr = result.stderr.decode("utf-8", errors="replace").strip()
            errors.append(f"ssh-keygen sign exited {result.returncode}: {stderr}")
            return SignResult(success=False, sig_path=sig_path, errors=errors)

        signature = result.stdout.encode("utf-8") if isinstance(result.stdout, str) else result.stdout
        Path(sig_path).write_bytes(signature)
        return SignResult(success=True, sig_path=sig_path, errors=errors)

    def verify(self, manifest_path: str, sig_path: str) -> VerifyResult:
        errors: list[str] = []

        if not Path(self._allowed_signers).exists():
            errors.append(f"Allowed signers file not found: {self._allowed_signers}")
            return VerifyResult(success=False, identity="", errors=errors)

        if not Path(manifest_path).exists():
            errors.append(f"Manifest not found: {manifest_path}")
            return VerifyResult(success=False, identity="", errors=errors)

        if not Path(sig_path).exists():
            errors.append(f"Signature file not found: {sig_path}")
            return VerifyResult(success=False, identity="", errors=errors)

        try:
            with open(manifest_path, "rb") as fh:
                result = subprocess.run(
                    [
                        "ssh-keygen",
                        "-Y", "verify",
                        "-f", self._allowed_signers,
                        "-I", _SIGNER_IDENTITY,
                        "-n", _MANIFEST_NAMESPACE,
                        "-s", sig_path,
                    ],
                    stdin=fh,
                    capture_output=True,
                    timeout=15,
                )
        except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
            errors.append(f"ssh-keygen verify failed: {exc}")
            return VerifyResult(success=False, identity="", errors=errors)

        if result.returncode != 0:
            stderr = result.stderr.decode("utf-8", errors="replace").strip()
            errors.append(
                f"ssh-keygen verify exited {result.returncode}: {stderr}"
            )
            return VerifyResult(success=False, identity="", errors=errors)

        identity = result.stdout.decode("utf-8", errors="replace").strip()
        return VerifyResult(success=True, identity=identity, errors=errors)

    @staticmethod
    def make_allowed_signers(
        identity: str,
        public_key: str,
        allowed_signers_path: str,
    ) -> None:
        line = f"{identity} {public_key}\n"
        Path(allowed_signers_path).parent.mkdir(parents=True, exist_ok=True)
        existing: set[str] = set()
        fpath = Path(allowed_signers_path)
        if fpath.exists():
            for entry in fpath.read_text().splitlines():
                existing.add(entry.strip())
        if line.strip() in existing:
            return
        with open(fpath, "a") as fh:
            fh.write(line)
