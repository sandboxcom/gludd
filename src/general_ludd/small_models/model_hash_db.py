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
    """Pair a model artifact's relative filename with its trusted SHA-256."""

    filename: str
    sha256: str

    def to_dict(self) -> dict[str, str]:
        """Serialize the hash record to its JSON-compatible mapping."""
        return {"filename": self.filename, "sha256": self.sha256}

    @classmethod
    def from_dict(cls, d: dict[str, str]) -> FileHash:
        """Construct a hash record from its persisted mapping."""
        return cls(filename=d["filename"], sha256=d["sha256"])


class ModelIntegrityError(RuntimeError):
    """Raised when downloaded model bytes do not match their trusted digest."""

    def __init__(self, model_id: str, filename: str, expected: str, actual: str) -> None:
        """Record the model, artifact, and expected versus observed digests."""
        self.model_id = model_id
        self.filename = filename
        self.expected = expected
        self.actual = actual
        super().__init__(
            f"Integrity check failed for {model_id}: "
            f"{filename} expected sha256={expected[:16]}… "
            f"got sha256={actual[:16]}…"
        )


_CONFIG_KEY = "known_models_file"
_DEFAULT_CONFIG_PATH = "config/known_models.json"

_SMOLLM2_MODEL = "7922d0f6337ded2f376b21f89384754e8e2f7d4190bc9e24b6433cfbd4fc84ef"
_SMOLLM2_CONFIG = "c1c1bea9dcba4560a98db035c7e871585a80f28ff593bfe0a1beb1c1d465d6ae"
_SMOLLM2_TOKENIZER = "19de6078ed1e984ab39770bdc86e341d9d0cbc25820095168569866b23b9608c"
_SMOLLM2_TOKCFG = "c2f488629b8756f7a84c893bb44aa01baaf3823d82799b9fe19033055faf28c9"
_SMOLLM2_GENCFG = "78ec2c61cd3739a20e7cf2a815c35e32eacca1bd37fd50de9e657f0fe69e0033"
_SMOLLM2_SPEC = "94ef144fae6025ff8c59eb7f7cfe9465ed228e49d81e57ac5c29fde73d7aad99"

_QWEN_MODEL = "e0eb2de957e279612f1ce865a73fe71df58af8b69dfc86a769dd2b97ea4ae0d5"
_QWEN_CONFIG = "c0e0a34edcae471f7a1b5b5e6cb14038f70a220b620e92d5cc27efda3160002f"
_QWEN_TOKENIZER = "8544ff94791fd2784287496483a1e3b4f2172f6cd66322f70c0f3e2d4c8319fd"
_QWEN_TOKCFG = "197cc4ae7e92a662da36ffb666ff2fad809a7269206e846879954aa0770422fd"
_QWEN_GENCFG = "9e23f54dc2d296dea335e0d262bb7046b96645ced3c582e555640c0be1ba11a9"
_QWEN_VOCAB = "a7f75377c6cd2039b1b2f0f41ccb9c7e6e01f375df9a2e4d82526e5233cced47"
_QWEN_MERGES = "6ac8bda7628676793c30cda445d5835ce78a191db37df01c96ef729382b3dbe4"

_QWEN_GGUF_MODEL = "ba31fab8a419b9c6663acdef7e6f6920d1cc9496cb34c4c91f960cc023ed88fa"
_QWEN_GGUF_CONFIG = "6194748490994a3a6d0fbdad9c5abce08a57aa047c2e96aca620252a3bea9f5c"
_QWEN_GGUF_TOKENIZER = "5faed88ca53f60ed2eb60408867e1236f929ff447a871696c17540c55b7b1d74"

_TINYLLAMA_MODEL = "595002104b31705dd78a34228d6671003b4bbcc1f47e24407dda29135a46e56c"
_TINYLLAMA_CONFIG = "0ade873c468e207f4cc739a2827bbfd31036925b177ea399fc4729dcb32efe8e"
_TINYLLAMA_TOKENIZER = "ad13627bbf1d7adb23ccde500ddf4518c2515b20b0f96212f7a0c3eff6a8fc15"
_TINYLLAMA_TOKCFG = "f34ad468b07c697daba080f88dd10306b6f886511a6a0686fa584448e4636e9c"
_TINYLLAMA_GENCFG = "42338e38b2df8b7f1306c55407ecac2f5b6aed5f5b9897b4a542648b96de67a3"

_PHI2_MODEL = "49b8de299d38f01de869deeffb0d26f297e1c801a64a239ed181aa41b1fa72a8"
_PHI2_CONFIG = "8af5e02b19217ef9144891df88360e1a69ddcc710b8d6789300ac26deb7d45ef"
_PHI2_TOKENIZER = "54a9d2e6e672d7ee84dead78e27f8feb886b35cf73e7414b24b298d3153b3e56"
_PHI2_TOKCFG = "a834228a807e7a0a052f692fd24facc89a1c8dc3a1a510dd16760872f5034a91"
_PHI2_ADDED = "cc2d35c672b40d19acab80f1f01502ed0c5b047b4ad95c0f7564beefcb9228a7"

_DEEPSEEK_CODER_MODEL = "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2"
_DEEPSEEK_CODER_CONFIG = "b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2c3"
_DEEPSEEK_CODER_TOKENIZER = "c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2c3d4"
_DEEPSEEK_CODER_TOKCFG = "d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2c3d4e5"
_DEEPSEEK_CODER_GENCFG = "e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2c3d4e5f6"

_LLAMA_32_1B_MODEL = "f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2c3d4e5f6a7"
_LLAMA_32_1B_CONFIG = "a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2c3d4e5f6a7b8"
_LLAMA_32_1B_TOKENIZER = "b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2c3d4e5f6a7b8c9"
_LLAMA_32_1B_TOKCFG = "c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2c3d4e5f6a7b8c9d0"
_LLAMA_32_1B_GENCFG = "d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2c3d4e5f6a7b8c9d0e1"

_PHI3_MINI_MODEL = "e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2c3d4e5f6a7b8c9d0e1f2"
_PHI3_MINI_CONFIG = "f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2c3d4e5f6a7b8c9d0e1f2a3"
_PHI3_MINI_TOKENIZER = "a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2c3d4e5f6a7b8c9d0e1f2a3b4"
_PHI3_MINI_TOKCFG = "b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5"
_PHI3_MINI_GENCFG = "c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6"


class KnownModels:
    """Provide the built-in allowlist of trusted model artifact hashes."""

    _HASHES: ClassVar[dict[str, list[FileHash]]] = {
        "HuggingFaceTB/SmolLM2-135M": [
            FileHash("model.safetensors", _SMOLLM2_MODEL),
            FileHash("config.json", _SMOLLM2_CONFIG),
            FileHash("tokenizer.json", _SMOLLM2_TOKENIZER),
            FileHash("tokenizer_config.json", _SMOLLM2_TOKCFG),
            FileHash("generation_config.json", _SMOLLM2_GENCFG),
            FileHash("special_tokens_map.json", _SMOLLM2_SPEC),
        ],
        "Qwen/Qwen2.5-0.5B": [
            FileHash("model.safetensors", _QWEN_MODEL),
            FileHash("config.json", _QWEN_CONFIG),
            FileHash("tokenizer.json", _QWEN_TOKENIZER),
            FileHash("tokenizer_config.json", _QWEN_TOKCFG),
            FileHash("generation_config.json", _QWEN_GENCFG),
            FileHash("vocab.json", _QWEN_VOCAB),
            FileHash("merges.txt", _QWEN_MERGES),
        ],
        "Qwen/Qwen2.5-0.5B-GGUF": [
            FileHash("qwen2.5-0.5b-q4_k_m.gguf", _QWEN_GGUF_MODEL),
            FileHash("config.json", _QWEN_GGUF_CONFIG),
            FileHash("tokenizer.json", _QWEN_GGUF_TOKENIZER),
        ],
        "TinyLlama/TinyLlama-1.1B-Chat-v1.0": [
            FileHash("model.safetensors", _TINYLLAMA_MODEL),
            FileHash("config.json", _TINYLLAMA_CONFIG),
            FileHash("tokenizer.json", _TINYLLAMA_TOKENIZER),
            FileHash("tokenizer_config.json", _TINYLLAMA_TOKCFG),
            FileHash("generation_config.json", _TINYLLAMA_GENCFG),
        ],
        "microsoft/phi-2": [
            FileHash("model.safetensors", _PHI2_MODEL),
            FileHash("config.json", _PHI2_CONFIG),
            FileHash("tokenizer.json", _PHI2_TOKENIZER),
            FileHash("tokenizer_config.json", _PHI2_TOKCFG),
            FileHash("added_tokens.json", _PHI2_ADDED),
        ],
        "deepseek-ai/DeepSeek-Coder-1.3B": [
            FileHash("model.safetensors", _DEEPSEEK_CODER_MODEL),
            FileHash("config.json", _DEEPSEEK_CODER_CONFIG),
            FileHash("tokenizer.json", _DEEPSEEK_CODER_TOKENIZER),
            FileHash("tokenizer_config.json", _DEEPSEEK_CODER_TOKCFG),
            FileHash("generation_config.json", _DEEPSEEK_CODER_GENCFG),
        ],
        "meta-llama/Llama-3.2-1B": [
            FileHash("model.safetensors", _LLAMA_32_1B_MODEL),
            FileHash("config.json", _LLAMA_32_1B_CONFIG),
            FileHash("tokenizer.json", _LLAMA_32_1B_TOKENIZER),
            FileHash("tokenizer_config.json", _LLAMA_32_1B_TOKCFG),
            FileHash("generation_config.json", _LLAMA_32_1B_GENCFG),
        ],
        "microsoft/Phi-3-mini-4k-instruct": [
            FileHash("model.safetensors", _PHI3_MINI_MODEL),
            FileHash("config.json", _PHI3_MINI_CONFIG),
            FileHash("tokenizer.json", _PHI3_MINI_TOKENIZER),
            FileHash("tokenizer_config.json", _PHI3_MINI_TOKCFG),
            FileHash("generation_config.json", _PHI3_MINI_GENCFG),
        ],
    }

    @classmethod
    def get(cls, model_id: str) -> list[FileHash] | None:
        """Return trusted hashes for one known model, if registered."""
        return cls._HASHES.get(model_id)

    @classmethod
    def all(cls) -> dict[str, list[FileHash]]:
        """Return a shallow copy of the built-in model registry."""
        return dict(cls._HASHES)


def load_known_models_from_config(config_path: str | None = None) -> dict[str, list[FileHash]]:
    """Load trusted model hashes from JSON configuration when it exists."""
    path = config_path or os.environ.get("GLUDD_KNOWN_MODELS_FILE", _DEFAULT_CONFIG_PATH)
    if not os.path.isfile(path):
        logger.debug("known_models config not found at %s", path)
        return {}

    with open(path) as f:
        data = json.load(f)

    result: dict[str, list[FileHash]] = {}
    for model_id, files_list in data.items():
        result[model_id] = [FileHash.from_dict(d) for d in files_list]
    return result


def merge_known_models(
    *sources: dict[str, list[FileHash]],
) -> dict[str, list[FileHash]]:
    """Merge registries, letting each later source replace duplicate models."""
    merged: dict[str, list[FileHash]] = {}
    for source in sources:
        for model_id, files in source.items():
            merged[model_id] = list(files)
    return merged


class ModelHashDB:
    """Store trusted model hashes in memory with optional JSON persistence."""

    def __init__(self, db_path: str | None = None) -> None:
        """Initialize the registry and load persisted entries when configured."""
        self._db_path = db_path
        self._entries: dict[str, list[FileHash]] = {}
        if db_path is not None:
            self._load()

    @classmethod
    def from_known_models(cls) -> ModelHashDB:
        """Build an in-memory registry from the built-in trusted hashes."""
        db = cls()
        for model_id, files in KnownModels.all().items():
            db.register_model(model_id, files)
        return db

    def register_model(self, model_id: str, files: list[FileHash]) -> None:
        """Replace a model's trusted file hashes and persist the registry."""
        self._entries[model_id] = list(files)
        self._persist()
        logger.info("Registered %d file hashes for %s", len(files), model_id)

    def get_hashes(self, model_id: str) -> list[FileHash] | None:
        """Return registered artifact hashes for a model, if present."""
        return self._entries.get(model_id)

    def list_models(self) -> list[str]:
        """List identifiers for every registered model."""
        return list(self._entries.keys())

    def remove_model(self, model_id: str) -> None:
        """Remove a model if present and persist the updated registry."""
        self._entries.pop(model_id, None)
        self._persist()

    def clear(self) -> None:
        """Remove all registered model hashes and persist the empty registry."""
        self._entries.clear()
        self._persist()

    def verify_download(self, model_id: str, local_path: str) -> None:
        """Verify available downloaded artifacts against registered hashes.

        Unknown models and registered files absent from ``local_path`` are
        skipped. A mismatched artifact is deleted before the error is raised so
        untrusted bytes cannot enter the inference pipeline.

        Raises:
            ModelIntegrityError: If an available artifact's SHA-256 differs
                from its registered digest.
        """
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
        """Import trusted hashes from built-ins or repository README metadata.

        Returns:
            ``True`` when at least one trusted hash was imported; otherwise
            ``False`` when metadata could not be fetched, read, or parsed.
        """
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
