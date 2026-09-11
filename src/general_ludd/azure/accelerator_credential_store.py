"""Protected, versioned storage for irreplaceable Azure controller credentials.

Static client secrets are durable operator artifacts, not temporary runtime
state.  This store confines them to one owner-private persistent directory,
keeps every installed generation, activates by atomic hard-link replacement,
and can restore a missing current link from its non-secret active manifest.
There is intentionally no deletion or pruning API.
"""

from __future__ import annotations

import json
import os
import re
import stat
import tempfile
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Final

from filelock import FileLock

from general_ludd.azure.accelerator_credentials import (
    AzureAcceleratorCredentialError,
    AzureAcceleratorCredentials,
    load_azure_accelerator_credentials,
    parse_azure_accelerator_credentials,
    read_azure_accelerator_credential_payload,
)
from general_ludd.security.state import SecureStateError, secure_directory

CREDENTIAL_HOME_ENV: Final = "GLUDD_CREDENTIAL_HOME"
DEFAULT_CREDENTIAL_NAME: Final = "azure-accelerator-auth.json"
_GENERATION_NAME: Final = re.compile(r"^[0-9a-f]{32}\.json$")
_CURRENT_NAME: Final = re.compile(
    r"^azure-accelerator-auth(?:-[A-Za-z0-9][A-Za-z0-9._-]{0,63})?\.json$"
)
_SCHEMA_VERSION: Final = 1
_LOCK_TIMEOUT_SECONDS: Final = 10


class AzureCredentialArtifactError(AzureAcceleratorCredentialError):
    """Report a fixed-context protected-artifact failure without secret data."""


class AzureCredentialArtifactState(StrEnum):
    """Content-free durable credential-artifact transitions."""

    STAGED = "staged"
    ACTIVATED = "activated"
    RESTORED = "restored"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class AzureCredentialArtifactEvent:
    """One secret-free, auditable protected-artifact transition."""

    state: AzureCredentialArtifactState
    generation_id: str
    source: str = "azure-accelerator-credential-store"


@dataclass(frozen=True, slots=True)
class AzureCredentialArtifactReceipt:
    """Paths and identity for one immutable installed generation."""

    generation_id: str
    generation_path: Path
    current_path: Path


def _discard_trace(_event: AzureCredentialArtifactEvent) -> None:
    return None


def _absolute(path: str | os.PathLike[str]) -> Path:
    return Path(os.path.abspath(os.path.expanduser(os.fspath(path))))


def default_azure_accelerator_credential_home(
    environment: Mapping[str, str] | None = None,
) -> Path:
    """Return the persistent credential root (explicit override, then XDG)."""
    values = os.environ if environment is None else environment
    override = values.get(CREDENTIAL_HOME_ENV)
    if override:
        return _absolute(override)
    data_home = values.get("XDG_DATA_HOME")
    base = _absolute(data_home) if data_home else Path.home() / ".local" / "share"
    return base / "general-ludd" / "credentials"


def _ephemeral_roots() -> tuple[Path, ...]:
    candidates = {
        _absolute(tempfile.gettempdir()),
        _absolute("/tmp"),
        _absolute("/var/tmp"),
        _absolute("/run"),
    }
    for name in ("TMPDIR", "TEMP", "TMP", "XDG_RUNTIME_DIR"):
        value = os.environ.get(name)
        if value:
            candidates.add(_absolute(value))
    return tuple(sorted(candidates, key=os.fspath))


def _within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _inside_worktree(path: Path) -> bool:
    return any((parent / ".git").exists() for parent in (path, *path.parents))


def _reject_unsafe_root(root: Path) -> None:
    if not root.is_absolute() or root == Path(root.anchor):
        raise AzureCredentialArtifactError(
            "credential artifact requires a durable credential root"
        )
    if any(_within(root, candidate) for candidate in _ephemeral_roots()):
        raise AzureCredentialArtifactError(
            "credential artifact requires a durable credential root"
        )
    if _inside_worktree(root):
        raise AzureCredentialArtifactError(
            "credential artifact requires a durable credential root"
        )


def durable_azure_accelerator_credential_path(
    path: str | os.PathLike[str] | None = None,
) -> Path:
    """Validate and return one path confined to its protected credential root."""
    if path is None or not os.fspath(path):
        root = default_azure_accelerator_credential_home()
        target = root / DEFAULT_CREDENTIAL_NAME
    else:
        target = _absolute(path)
        root = target.parent
    _reject_unsafe_root(root)
    if _CURRENT_NAME.fullmatch(target.name) is None:
        raise AzureCredentialArtifactError("credential artifact name is invalid")
    return target


class AzureAcceleratorCredentialStore:
    """Persist immutable credentials and one recoverable active hard link."""

    def __init__(
        self,
        *,
        root: str | os.PathLike[str] | None = None,
        current_name: str = DEFAULT_CREDENTIAL_NAME,
        trace_sink: Callable[[AzureCredentialArtifactEvent], None] = _discard_trace,
    ) -> None:
        """Open one protected root without accepting cache or temporary paths."""
        selected_root = (
            default_azure_accelerator_credential_home()
            if root is None
            else _absolute(root)
        )
        _reject_unsafe_root(selected_root)
        if _CURRENT_NAME.fullmatch(current_name) is None:
            raise AzureCredentialArtifactError("credential artifact name is invalid")
        if not callable(trace_sink):
            raise AzureCredentialArtifactError("credential trace sink must be callable")
        try:
            self._root = secure_directory(selected_root)
            self._generations = secure_directory(self._root / "generations")
        except SecureStateError as exc:
            raise AzureCredentialArtifactError(
                "credential artifact root is not owner-private"
            ) from exc
        self._current_name = current_name
        self._trace_sink = trace_sink
        self._lock = FileLock(
            str(self._root / ".credential-store.lock"),
            timeout=_LOCK_TIMEOUT_SECONDS,
            mode=0o600,
        )

    @property
    def current_path(self) -> Path:
        """Return the recoverable active credential link."""
        return self._root / self._current_name

    @property
    def audit_path(self) -> Path:
        """Return the content-free append-only lifecycle audit path."""
        return self._root / "artifact-events.jsonl"

    @property
    def manifest_path(self) -> Path:
        """Return the non-secret active-generation manifest path."""
        return self._root / f"{self._current_name}.active.json"

    def generation_paths(self) -> tuple[Path, ...]:
        """Return every immutable generation without reading secret content."""
        generations: list[Path] = []
        for path in sorted(self._generations.iterdir(), key=lambda item: item.name):
            if _GENERATION_NAME.fullmatch(path.name) is None:
                continue
            try:
                info = path.lstat()
            except OSError:
                continue
            if stat.S_ISREG(info.st_mode):
                generations.append(path)
        return tuple(generations)

    @staticmethod
    def _write_all(descriptor: int, payload: bytes) -> None:
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("short credential artifact write")
            view = view[written:]

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        descriptor = os.open(path, flags)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _write_exclusive(self, path: Path, payload: bytes) -> None:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags, 0o600)
        try:
            self._write_all(descriptor, payload)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _atomic_write(self, target: Path, payload: bytes) -> None:
        temporary = self._root / f".{target.name}.{uuid.uuid4().hex}.tmp"
        try:
            self._write_exclusive(temporary, payload)
            os.replace(temporary, target)
            self._fsync_directory(self._root)
        finally:
            temporary.unlink(missing_ok=True)

    def _append_audit(self, event: AzureCredentialArtifactEvent) -> None:
        record = json.dumps(
            {
                "generation_id": event.generation_id,
                "schema_version": _SCHEMA_VERSION,
                "source": event.source,
                "state": event.state,
                "timestamp_ns": time.time_ns(),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode() + b"\n"
        flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT
        flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(self.audit_path, flags, 0o600)
            try:
                info = os.fstat(descriptor)
                if not stat.S_ISREG(info.st_mode):
                    raise OSError("audit target is not regular")
                if hasattr(os, "getuid") and info.st_uid != os.getuid():
                    raise OSError("audit target has wrong owner")
                os.fchmod(descriptor, 0o600)
                self._write_all(descriptor, record)
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        except OSError as exc:
            raise AzureCredentialArtifactError(
                "credential artifact audit failed"
            ) from exc

    def _emit(self, state: AzureCredentialArtifactState, generation_id: str) -> None:
        event = AzureCredentialArtifactEvent(state=state, generation_id=generation_id)
        self._append_audit(event)
        try:
            self._trace_sink(event)
        except Exception:
            return

    def _generation_for_current(self) -> Path | None:
        try:
            current = self.current_path.stat()
        except OSError:
            return None
        for generation in self.generation_paths():
            try:
                candidate = generation.stat()
            except OSError:
                continue
            if (current.st_dev, current.st_ino) == (candidate.st_dev, candidate.st_ino):
                return generation
        return None

    def _archive_legacy_current(self, expected_subscription_id: str) -> Path | None:
        if not self.current_path.exists():
            return None
        managed = self._generation_for_current()
        if managed is not None:
            return managed
        load_azure_accelerator_credentials(
            self.current_path,
            expected_subscription_id=expected_subscription_id,
        )
        generation = self._generations / f"{uuid.uuid4().hex}.json"
        try:
            os.link(self.current_path, generation, follow_symlinks=False)
            self._fsync_directory(self._generations)
        except OSError as exc:
            raise AzureCredentialArtifactError(
                "legacy credential archival failed"
            ) from exc
        return generation

    def _write_manifest(self, generation_id: str) -> None:
        payload = json.dumps(
            {"generation_id": generation_id, "schema_version": _SCHEMA_VERSION},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        self._atomic_write(self.manifest_path, payload)

    def _read_manifest(self) -> str:
        try:
            raw = read_azure_accelerator_credential_payload(self.manifest_path)
            payload = json.loads(raw)
            generation_id = payload.get("generation_id")
            if (
                set(payload) != {"generation_id", "schema_version"}
                or payload.get("schema_version") != _SCHEMA_VERSION
                or not isinstance(generation_id, str)
                or _GENERATION_NAME.fullmatch(f"{generation_id}.json") is None
            ):
                raise ValueError
        except (
            AzureAcceleratorCredentialError,
            OSError,
            ValueError,
            TypeError,
            AttributeError,
            json.JSONDecodeError,
        ):
            raise AzureCredentialArtifactError(
                "active credential manifest is unavailable"
            ) from None
        return generation_id

    def _activate(self, generation: Path) -> None:
        temporary = self._root / f".{self._current_name}.{uuid.uuid4().hex}.link"
        try:
            os.link(generation, temporary, follow_symlinks=False)
            os.replace(temporary, self.current_path)
            self._fsync_directory(self._root)
        except OSError as exc:
            raise AzureCredentialArtifactError(
                "credential artifact activation failed"
            ) from exc
        finally:
            temporary.unlink(missing_ok=True)

    def install(
        self,
        payload: bytes,
        *,
        expected_subscription_id: str,
    ) -> AzureCredentialArtifactReceipt:
        """Validate, preserve, and atomically activate one new generation."""
        try:
            parse_azure_accelerator_credentials(
                payload,
                expected_subscription_id=expected_subscription_id,
            )
        except AzureAcceleratorCredentialError as exc:
            raise AzureCredentialArtifactError(str(exc)) from None

        with self._lock:
            self._archive_legacy_current(expected_subscription_id)
            generation_id = uuid.uuid4().hex
            generation = self._generations / f"{generation_id}.json"
            try:
                self._write_exclusive(generation, payload)
                self._fsync_directory(self._generations)
                self._emit(AzureCredentialArtifactState.STAGED, generation_id)
                self._activate(generation)
                self._write_manifest(generation_id)
            except AzureCredentialArtifactError:
                if generation.exists():
                    self._emit(AzureCredentialArtifactState.FAILED, generation_id)
                raise
            self._emit(AzureCredentialArtifactState.ACTIVATED, generation_id)
            return AzureCredentialArtifactReceipt(
                generation_id=generation_id,
                generation_path=generation,
                current_path=self.current_path,
            )

    def load_current(
        self,
        *,
        expected_subscription_id: str,
    ) -> AzureAcceleratorCredentials:
        """Load current credentials, restoring only the manifest-selected link."""
        with self._lock:
            if self.current_path.exists():
                credentials = load_azure_accelerator_credentials(
                    self.current_path,
                    expected_subscription_id=expected_subscription_id,
                )
                generation = self._archive_legacy_current(expected_subscription_id)
                if generation is not None:
                    self._write_manifest(generation.stem)
                return credentials

            generation_id = self._read_manifest()
            generation = self._generations / f"{generation_id}.json"
            load_azure_accelerator_credentials(
                generation,
                expected_subscription_id=expected_subscription_id,
            )
            self._activate(generation)
            self._emit(AzureCredentialArtifactState.RESTORED, generation_id)
            return load_azure_accelerator_credentials(
                self.current_path,
                expected_subscription_id=expected_subscription_id,
            )


def load_durable_azure_accelerator_credentials(
    path: str | os.PathLike[str],
    *,
    expected_subscription_id: str,
) -> AzureAcceleratorCredentials:
    """Load one protected path with automatic active-link restoration."""
    target = durable_azure_accelerator_credential_path(path)
    store = AzureAcceleratorCredentialStore(
        root=target.parent,
        current_name=target.name,
    )
    return store.load_current(expected_subscription_id=expected_subscription_id)


def load_preserved_azure_accelerator_credentials(
    path: str | os.PathLike[str],
    *,
    expected_subscription_id: str,
) -> AzureAcceleratorCredentials:
    """Load a managed generation with recovery, or one legacy private file.

    The presence of store metadata makes recovery mandatory and fail-closed.
    A legacy operator file remains readable so callers can import it without
    silently moving or deleting the original artifact.
    """
    target = _absolute(path)
    manifest = target.parent / f"{target.name}.active.json"
    generations = target.parent / "generations"
    managed = _CURRENT_NAME.fullmatch(target.name) is not None and (
        os.path.lexists(manifest) or os.path.lexists(generations)
    )
    if managed:
        return load_durable_azure_accelerator_credentials(
            target,
            expected_subscription_id=expected_subscription_id,
        )
    return load_azure_accelerator_credentials(
        target,
        expected_subscription_id=expected_subscription_id,
    )


__all__ = [
    "CREDENTIAL_HOME_ENV",
    "DEFAULT_CREDENTIAL_NAME",
    "AzureAcceleratorCredentialStore",
    "AzureCredentialArtifactError",
    "AzureCredentialArtifactEvent",
    "AzureCredentialArtifactReceipt",
    "AzureCredentialArtifactState",
    "default_azure_accelerator_credential_home",
    "durable_azure_accelerator_credential_path",
    "load_durable_azure_accelerator_credentials",
    "load_preserved_azure_accelerator_credentials",
]
