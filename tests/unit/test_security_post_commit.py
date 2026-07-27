"""Post-commit security verification — no secrets in committed files.

Ensures that committed files do not contain patterns matching common
secret formats: API keys, tokens, passwords, and private keys.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

SECRET_PATTERNS: list[tuple[str, str]] = [
    (r"-----BEGIN\s+(?:RSA|DSA|EC|OPENSSH)\s+PRIVATE\s+KEY", "private key"),
    (r"(?:api|access|secret|auth)_?key\s*=\s*[\'\"][a-zA-Z0-9+/=]{32,}[\'\"]", "API/access key literal"),
    (r"(?:password|passwd|pwd)\s*=\s*[\'\"][^\'\"]{8,}[\'\"]", "hardcoded password"),
    (r"ghp_[a-zA-Z0-9]{36}", "GitHub personal access token"),
    (r"sk-[a-zA-Z0-9]{32,}", "OpenAI/Stripe secret key"),
    (r"eyJ[a-zA-Z0-9_-]{10,}\.[a-zA-Z0-9_-]{10,}\.[a-zA-Z0-9_-]{10,}", "JWT token"),
]


def _committed_files() -> list[Path]:
    result = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "ls-files", "-z"],
        capture_output=True,
        text=True,
        check=True,
    )
    return [REPO_ROOT / f for f in result.stdout.split("\0") if f]


def _readable_file(path: Path) -> bool:
    if not path.is_file():
        return False
    if path.stat().st_size > 1_000_000:
        return False
    suffixes = path.suffixes
    if not suffixes:
        return False
    ext = suffixes[-1].lower()
    return ext in {".py", ".yml", ".yaml", ".toml", ".json", ".cfg", ".ini", ".sh", ".ts", ".md"}


class TestNoSecretsInCommittedFiles:
    def test_no_private_keys_in_committed_files(self) -> None:
        failures: list[str] = []
        for path in _committed_files():
            if not _readable_file(path):
                continue
            try:
                content = path.read_text(encoding="utf-8", errors="replace")
            except (OSError, UnicodeDecodeError):
                continue
            for pattern, label in SECRET_PATTERNS:
                if re.search(pattern, content, re.IGNORECASE):
                    failures.append(f"{path}: {label}")
        assert not failures, f"Found {len(failures)} potential secret(s) in committed files:\n" + "\n".join(failures)

    def test_no_env_files_committed(self) -> None:
        committed_names = {c.name for c in _committed_files()}
        forbidden = {".env", ".env.local", ".env.production", "credentials.json", "secrets.yml"}
        found = committed_names & forbidden
        assert not found, f"Forbidden env/credential file(s) committed: {found}"
