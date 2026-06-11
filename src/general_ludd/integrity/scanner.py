"""File integrity monitoring — hash recording, change detection, OpenBao signing."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_INTEGRITY_KEY: str | None = None


def _get_integrity_key() -> str:
    global _INTEGRITY_KEY
    if _INTEGRITY_KEY is not None:
        return _INTEGRITY_KEY
    key = os.environ.get("GL_INTEGRITY_KEY")
    if key:
        _INTEGRITY_KEY = key
        return key
    _INTEGRITY_KEY = secrets.token_hex(32)
    logger.warning(
        "GL_INTEGRITY_KEY not set — using random per-process key. "
        "Signatures will not survive restarts. Set GL_INTEGRITY_KEY for "
        "persistent integrity verification."
    )
    return _INTEGRITY_KEY


@dataclass
class ChangeRecord:
    file_path: str
    change_type: str
    old_hash: str | None = None
    new_hash: str | None = None
    detected_at: str = ""
    approved: bool = False
    reason: str = ""
    signer: str = ""
    signature: str | None = None


class FileIntegrityScanner:
    def __init__(self, store_dir: str = "") -> None:
        if store_dir:
            self._store = Path(store_dir) / "integrity_db.json"
        else:
            home = os.path.expanduser("~")
            base = Path(home) / ".local" / "share" / "general-ludd" / "integrity"
            base.mkdir(parents=True, exist_ok=True)
            self._store = base / "integrity_db.json"

    def _hash_file(self, path: str) -> str:
        try:
            data = Path(path).read_bytes()
            return hashlib.sha256(data).hexdigest()
        except Exception:
            return ""

    def _load_hashes(self) -> dict[str, str]:
        if self._store.exists():
            try:
                return json.loads(self._store.read_text())
            except Exception:
                pass
        return {}

    def _save_hashes(self, hashes: dict[str, str]) -> None:
        self._store.parent.mkdir(parents=True, exist_ok=True)
        self._store.write_text(json.dumps(hashes, indent=2))

    def _is_vc_controlled(self, path: str) -> bool:
        current = Path(path).resolve()
        while current != current.parent:
            if (current / ".git").is_dir() or (current / ".svn").is_dir():
                return True
            current = current.parent
        return False

    def scan(self, watch_paths: list[str], exclude_patterns: list[str] | None = None) -> dict[str, Any]:
        exclude = [re.compile(p) for p in (exclude_patterns or [])]
        old_hashes = self._load_hashes()
        new_hashes: dict[str, str] = {}
        files: list[str] = []
        changes: list[dict[str, Any]] = []

        for wp in watch_paths:
            root = Path(wp).expanduser().resolve()
            if not root.exists():
                continue
            for dirpath, _dirnames, filenames in os.walk(str(root)):
                for fn in filenames:
                    fp = os.path.join(dirpath, fn)
                    if any(e.search(fp) for e in exclude):
                        continue
                    if self._is_vc_controlled(fp):
                        continue
                    new_hash = self._hash_file(fp)
                    if new_hash:
                        new_hashes[fp] = new_hash
                        files.append(fp)

        scanned = set(files)
        previously = set(old_hashes.keys())

        for fp in scanned - previously:
            changes.append({
                "type": "new",
                "file": fp,
                "new_hash": new_hashes.get(fp, ""),
                "old_hash": None,
                "detected_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "approved": False,
            })
        for fp in scanned & previously:
            if old_hashes[fp] != new_hashes.get(fp, ""):
                changes.append({
                    "type": "modified",
                    "file": fp,
                    "old_hash": old_hashes[fp],
                    "new_hash": new_hashes.get(fp, ""),
                    "detected_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "approved": False,
                })
        for fp in previously - scanned:
            changes.append({
                "type": "removed",
                "file": fp,
                "old_hash": old_hashes[fp],
                "new_hash": None,
                "detected_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "approved": False,
            })

        self._save_hashes(new_hashes)
        return {"scanned": len(files), "files": files, "changes": changes}


def sign_change(change: ChangeRecord, reason: str, signer: str) -> dict[str, Any]:
    parts = [change.file_path, change.change_type, str(change.old_hash), str(change.new_hash), change.detected_at]
    payload = "|".join(parts)
    key = _get_integrity_key()
    sig = hmac.new(key.encode(), payload.encode(), hashlib.sha256).hexdigest()
    result = asdict(change)
    result["approved"] = True
    result["reason"] = reason
    result["signer"] = signer
    result["signature"] = sig
    return result


def verify_signature(signed: dict[str, Any]) -> bool:
    parts = [
        signed.get("file_path", ""),
        signed.get("change_type", ""),
        signed.get("old_hash", ""),
        signed.get("new_hash", ""),
        signed.get("detected_at", ""),
    ]
    payload = "|".join(str(p) for p in parts)
    key = _get_integrity_key()
    expected = hmac.new(key.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return signed.get("signature") == expected


def sign_change_openbao(
    path: str, signer: str, reason: str, secrets_resolver: Any | None = None
) -> dict[str, Any]:
    payload = f"{path}|{signer}|{reason}|{time.time()}"
    key = _get_integrity_key()
    sig = hmac.new(key.encode(), payload.encode(), hashlib.sha256).hexdigest()
    result: dict[str, Any] = {
        "path": path,
        "signer": signer,
        "reason": reason,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "signature": sig,
        "backend": "openbao" if secrets_resolver else "local-hmac",
    }
    if secrets_resolver and hasattr(secrets_resolver, "write_secret"):
        try:
            secrets_resolver.write_secret(
                f"integrity/{path.replace('/', '_')}", {"signature": sig, "reason": reason}
            )
            result["backend"] = "openbao"
        except Exception:
            result["backend"] = "openbao-unavailable-fallback-hmac"
    return result
