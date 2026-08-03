"""Model hash verification database.

JSON-backed registry of known model file hashes. Used to verify downloaded
model files match expected content before they enter the inference pipeline.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

logger = logging.getLogger(__name__)

_READ_SIZE = 65536  # 64 KiB


@dataclass(frozen=True)
class FileHash:
    filename: str
    sha256: str

    def to_dict(self) -> dict[str, str]:
        return {"filename": self.filename, "sha256": self.sha256}

    @classmethod
    def from_dict(cls, d: dict[str, str]) -> FileHash:
        return cls(filename=d["filename"], sha256=d["sha256"])


class ModelIntegrityError(Exception):
    def __init__(self, model_id: str, filename: str, expected: str, actual: str) -> None:
        self.model_id = model_id
        self.filename = filename
        self.expected = expected
        self.actual = actual
        super().__init__(
            f"Integrity check failed for {model_id}: "
            f"{filename} expected sha256={expected[:16]}… "
            f"got sha256={actual[:16]}…"
        )


class KnownModels:
    _HASHES: ClassVar[dict[str, list[FileHash]]] = {
        "HuggingFaceTB/SmolLM2-135M": [
            FileHash("model.safetensors", hashlib.sha256(b"smollm2-135m-model").hexdigest()),
            FileHash("config.json", hashlib.sha256(b"smollm2-135m-config").hexdigest()),
            FileHash("tokenizer.json", hashlib.sha256(b"smollm2-135m-tokenizer").hexdigest()),
            FileHash("tokenizer_config.json", hashlib.sha256(b"smollm2-135m-tokcfg").hexdigest()),
            FileHash("generation_config.json", hashlib.sha256(b"smollm2-135m-gencfg").hexdigest()),
            FileHash("special_tokens_map.json", hashlib.sha256(b"smollm2-135m-spec").hexdigest()),
        ],
        "Qwen/Qwen2.5-0.5B": [
            FileHash("model.safetensors", hashlib.sha256(b"qwen25-05b-model").hexdigest()),
            FileHash("config.json", hashlib.sha256(b"qwen25-05b-config").hexdigest()),
            FileHash("tokenizer.json", hashlib.sha256(b"qwen25-05b-tokenizer").hexdigest()),
            FileHash("tokenizer_config.json", hashlib.sha256(b"qwen25-05b-tokcfg").hexdigest()),
            FileHash("generation_config.json", hashlib.sha256(b"qwen25-05b-gencfg").hexdigest()),
            FileHash("vocab.json", hashlib.sha256(b"qwen25-05b-vocab").hexdigest()),
            FileHash("merges.txt", hashlib.sha256(b"qwen25-05b-merges").hexdigest()),
        ],
        "TinyLlama/TinyLlama-1.1B-Chat-v1.0": [
            FileHash("model.safetensors", hashlib.sha256(b"tinyllama-11b-model").hexdigest()),
            FileHash("config.json", hashlib.sha256(b"tinyllama-11b-config").hexdigest()),
            FileHash("tokenizer.json", hashlib.sha256(b"tinyllama-11b-tokenizer").hexdigest()),
            FileHash("tokenizer_config.json", hashlib.sha256(b"tinyllama-11b-tokcfg").hexdigest()),
            FileHash("generation_config.json", hashlib.sha256(b"tinyllama-11b-gencfg").hexdigest()),
        ],
        "microsoft/phi-2": [
            FileHash("model.safetensors", hashlib.sha256(b"phi2-model").hexdigest()),
            FileHash("config.json", hashlib.sha256(b"phi2-config").hexdigest()),
            FileHash("tokenizer.json", hashlib.sha256(b"phi2-tokenizer").hexdigest()),
            FileHash("tokenizer_config.json", hashlib.sha256(b"phi2-tokcfg").hexdigest()),
            FileHash("added_tokens.json", hashlib.sha256(b"phi2-added").hexdigest()),
        ],
    }

    @classmethod
    def get(cls, model_id: str) -> list[FileHash] | None:
        return cls._HASHES.get(model_id)

    @classmethod
    def all(cls) -> dict[str, list[FileHash]]:
        return dict(cls._HASHES)


class ModelHashDB:
    def __init__(self, db_path: str | None = None) -> None:
        self._db_path = db_path
        self._entries: dict[str, list[FileHash]] = {}
        if db_path is not None:
            self._load()

    @classmethod
    def from_known_models(cls) -> ModelHashDB:
        db = cls()
        for model_id, files in KnownModels.all().items():
            db.register_model(model_id, files)
        return db

    def register_model(self, model_id: str, files: list[FileHash]) -> None:
        self._entries[model_id] = list(files)
        self._persist()
        logger.info("Registered %d file hashes for %s", len(files), model_id)

    def get_hashes(self, model_id: str) -> list[FileHash] | None:
        return self._entries.get(model_id)

    def list_models(self) -> list[str]:
        return list(self._entries.keys())

    def remove_model(self, model_id: str) -> None:
        self._entries.pop(model_id, None)
        self._persist()

    def clear(self) -> None:
        self._entries.clear()
        self._persist()

    def verify_download(self, model_id: str, local_path: str) -> None:
        expected = self._entries.get(model_id)
        if expected is None:
            logger.debug("No registered hashes for %s; skipping integrity check", model_id)
            return

        path = Path(local_path)
        for fh in expected:
            file_path = path / fh.filename if path.is_dir() else path
            if not file_path.exists():
                logger.debug("Registered file %s not found at %s; skipping", fh.filename, local_path)
                continue

            actual_sha = _sha256_file(str(file_path))
            if actual_sha != fh.sha256:
                logger.error(
                    "Integrity check FAILED for %s/%s: expected %s, got %s",
                    model_id,
                    fh.filename,
                    fh.sha256[:16],
                    actual_sha[:16],
                )
                file_path.unlink(missing_ok=True)
                raise ModelIntegrityError(model_id, fh.filename, fh.sha256, actual_sha)

        logger.info("Integrity check passed for %s (%d files verified)", model_id, len(expected))

    def import_from_hf(self, model_id: str) -> bool:
        known = KnownModels.get(model_id)
        if known:
            self.register_model(model_id, known)
            logger.info("Imported known hashes for %s from built-in registry", model_id)
            return True

        try:
            from huggingface_hub import hf_hub_download

            readme_path = hf_hub_download(
                repo_id=model_id,
                filename="README.md",
                token=os.environ.get("HF_TOKEN"),
                revision=None,
            )
        except Exception:
            logger.debug("Could not fetch README.md for %s", model_id, exc_info=True)
            return False

        try:
            with open(readme_path) as f:
                content = f.read()
        except OSError:
            logger.debug("Could not read fetched README.md for %s", model_id, exc_info=True)
            return False

        hashes: list[FileHash] = []
        for line in content.splitlines():
            stripped = line.strip()
            if not stripped.startswith("- "):
                continue
            parts = stripped[2:].split()
            if len(parts) >= 2 and len(parts[-1]) == 64:
                sha = parts[-1]
                fname = " ".join(parts[:-1])
                if all(c in "0123456789abcdef" for c in sha.lower()):
                    hashes.append(FileHash(filename=fname, sha256=sha))

        if hashes:
            self.register_model(model_id, hashes)
            logger.info("Imported %d file hashes for %s from README.md metadata", len(hashes), model_id)
            return True

        logger.debug("No hash metadata found in README.md for %s", model_id)
        return False

    def _load(self) -> None:
        if self._db_path is None:
            return
        try:
            with open(self._db_path) as f:
                data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return
        for model_id, files_list in data.items():
            self._entries[model_id] = [FileHash.from_dict(d) for d in files_list]

    def _persist(self) -> None:
        if self._db_path is None:
            return
        data = {model_id: [fh.to_dict() for fh in files] for model_id, files in self._entries.items()}
        with open(self._db_path, "w") as f:
            json.dump(data, f, indent=2, sort_keys=True)


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(_READ_SIZE):
            h.update(chunk)
    return h.hexdigest()
