from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path


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
            "GLUDD_SIGNING_KEY",
            os.path.expanduser("~/.ssh/id_ed25519"),
        )
        self._allowed_signers = allowed_signers_path or os.environ.get(
            "GLUDD_ALLOWED_SIGNERS",
            os.path.expanduser("~/.ssh/allowed_signers"),
        )

    def sign(self, manifest_path: str) -> SignResult:
        sig_path = f"{manifest_path}.sig"
        errors: list[str] = []

        if not Path(self._private_key).exists():
            errors.append(f"Private key not found: {self._private_key}")
            return SignResult(success=False, sig_path=sig_path, errors=errors)

        if not Path(manifest_path).exists():
            errors.append(f"Manifest not found: {manifest_path}")
            return SignResult(success=False, sig_path=sig_path, errors=errors)

        try:
            completed = subprocess.run(
                [
                    "ssh-keygen",
                    "-Y", "sign",
                    "-f", self._private_key,
                    "-n", "file",
                    manifest_path,
                ],
                capture_output=True,
                timeout=30,
            )
        except subprocess.TimeoutExpired as exc:
            errors.append(f"ssh-keygen sign timed out: {exc}")
            return SignResult(success=False, sig_path=sig_path, errors=errors)
        except FileNotFoundError:
            errors.append("ssh-keygen not found or failed")
            return SignResult(success=False, sig_path=sig_path, errors=errors)

        if completed.returncode != 0:
            stderr_msg = completed.stderr.decode(errors="replace").strip()
            errors.append(
                f"ssh-keygen sign exited {completed.returncode}: {stderr_msg}"
            )
            return SignResult(success=False, sig_path=sig_path, errors=errors)

        Path(sig_path).write_bytes(completed.stdout)
        return SignResult(success=True, sig_path=sig_path, errors=[])

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
            completed = subprocess.run(
                [
                    "ssh-keygen",
                    "-Y", "verify",
                    "-f", self._allowed_signers,
                    "-n", "file",
                    "-s", sig_path,
                    manifest_path,
                ],
                capture_output=True,
                timeout=30,
            )
        except subprocess.TimeoutExpired as exc:
            errors.append(f"ssh-keygen verify timed out: {exc}")
            return VerifyResult(success=False, identity="", errors=errors)
        except FileNotFoundError:
            errors.append("ssh-keygen not found or failed")
            return VerifyResult(success=False, identity="", errors=errors)

        if completed.returncode != 0:
            stderr_msg = completed.stderr.decode(errors="replace").strip()
            errors.append(
                f"ssh-keygen verify exited {completed.returncode}: {stderr_msg}"
            )
            return VerifyResult(success=False, identity="", errors=errors)

        identity = completed.stdout.decode(errors="replace").strip()
        return VerifyResult(success=True, identity=identity, errors=[])

    @staticmethod
    def make_allowed_signers(identity: str, public_key: str, path: str) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        entry = f"{identity} {public_key}"

        existing: set[str] = set()
        if target.exists():
            for line in target.read_text().splitlines():
                stripped = line.strip()
                if stripped:
                    existing.add(stripped)

        if entry in existing:
            return

        with target.open("a") as fh:
            if existing:
                fh.write("\n")
            fh.write(f"{entry}\n")
