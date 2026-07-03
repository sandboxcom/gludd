"""AtomicSafeWriter — concrete ``SafeWriter`` for the self-update pipeline.

``UpdateApplier`` delegates the filesystem write out via the structural
``SafeWriter`` Protocol defined in ``self_update.applier``. This module
provides the production implementation.

Safety contract (mirrors the applier's fail-closed posture):

* **Atomic.** Each write goes to a temp file in the *same directory* as the
  target, then ``os.replace`` swaps it into place. On POSIX this is atomic;
  a reader never observes a partial file. The temp file is created with
  ``tempfile.mkstemp`` (mode 0600) and ``fsync``-ed before the swap.
* **Confined.** Every target is resolved against the configured
  ``workspace_root`` and must live beneath it. ``../`` traversal, absolute
  paths outside the root, and symlinks that escape the root are all refused
  with ``ValueError`` — the applier's own ``_first_escape`` check is a
  belt-and-braces backstop; this writer never trusts the caller.
* **Idempotent on parents.** Missing parent directories are created
  (``mkdir(parents=True, exist_ok=True)``).
* **Observable.** Returns the resolved absolute path of the file written, so
  the caller can record / log the exact on-disk location.
"""

from __future__ import annotations

import contextlib
import os
import tempfile
from collections.abc import Callable
from pathlib import Path

__all__ = ["AtomicSafeWriter"]


class AtomicSafeWriter:
    """Writes files atomically and confined to ``workspace_root``.

    Implements the ``SafeWriter`` Protocol from
    :mod:`general_ludd.self_update.applier`::

        class SafeWriter(Protocol):
            def write(self, path: str, content: str) -> str | None: ...

    The implementation returns the resolved written path (``str``) — a
    superset of the Protocol's contract (callers that ignore the return
    value are unaffected), so it is structurally assignable to ``SafeWriter``
    and passes the ``runtime_checkable`` ``isinstance`` check.
    """

    def __init__(
        self,
        workspace_root: Path | str,
        recorder: Callable[[str, bytes | None, str], None] | None = None,
    ) -> None:
        # Resolve once at construction so a later cwd change can't shift the
        # root under us. Symlinks in the root path are followed.
        self._workspace_root: Path = Path(workspace_root).resolve()
        # Optional change-export hook, invoked on a SUCCESSFUL, validated write
        # with ``(resolved_path, original_bytes_or_None, new_content)``. It is
        # called FAIL-SOFT (any exception is swallowed) so recording can NEVER
        # break or roll back the write itself. ``None`` disables recording.
        self._recorder: Callable[[str, bytes | None, str], None] | None = recorder

    @property
    def workspace_root(self) -> Path:
        """The resolved root every target must live beneath."""
        return self._workspace_root

    def write(
        self,
        path: str,
        content: str,
        validate: Callable[[str], bool] | None = None,
    ) -> str:
        """Atomically write ``content`` to ``path``, returning the resolved path.

        Parameters
        ----------
        path:
            Relative (anchored to ``workspace_root``) or absolute path of the
            target file. Must resolve *beneath* ``workspace_root``.
        content:
            Text content to write. Encoded as UTF-8.
        validate:
            Optional post-write validation hook. If provided, it is called with
            the resolved target path *after* the atomic swap. If it returns a
            falsy value OR raises, the write is **rolled back** — the file is
            restored to its exact prior bytes (or removed if it did not exist
            before) and a :class:`RuntimeError` is raised. This adds
            recoverability: a config change that breaks validation never leaves a
            broken file on disk. When ``validate`` is ``None`` (the default) the
            behaviour is unchanged, so existing callers are unaffected.

        Returns
        -------
        str
            The resolved absolute path of the file that was written.

        Raises
        ------
        ValueError
            If ``path`` resolves outside ``workspace_root`` (traversal,
            absolute escape, or symlink escape).
        OSError
            On any underlying filesystem error during the temp write/replace.
        RuntimeError
            If ``validate`` is provided and rejects (returns falsy) or raises;
            the on-disk file is rolled back to its prior state before raising.
        """
        target = self._confine(path)

        # Capture the prior bytes BEFORE the swap so we can roll back to the
        # exact previous content. ``None`` means the file did not exist (a fresh
        # create), in which case a rollback removes the newly-created file.
        original: bytes | None = target.read_bytes() if target.exists() else None

        # Same-directory temp so os.replace is an atomic rename on POSIX.
        # Prefix with the target name + "." so debugging a stale temp is easy.
        fd, tmp_name = tempfile.mkstemp(
            dir=str(target.parent),
            prefix=f".{target.name}.",
            suffix=".tmp",
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(content)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_name, target)
        except Exception:
            # Best-effort cleanup of the temp file on any failure path.
            with contextlib.suppress(OSError):
                os.unlink(tmp_name)
            raise

        if validate is not None:
            try:
                accepted = bool(validate(str(target)))
            except Exception as exc:
                # A raising validator is treated as a rejection — roll back and
                # surface the original failure as the cause.
                self._restore(target, original)
                raise RuntimeError(
                    "post-write validation failed, rolled back"
                ) from exc
            if not accepted:
                self._restore(target, original)
                raise RuntimeError("post-write validation failed, rolled back")

        # Change-export hook: record the (path, prior bytes, new content) AFTER
        # the write has fully succeeded and passed validation. FAIL-SOFT — any
        # exception from the recorder is swallowed so a recording failure can
        # never break the write or trigger a rollback of an already-good file.
        if self._recorder is not None:
            with contextlib.suppress(Exception):
                self._recorder(str(target), original, content)

        return str(target)

    def _restore(self, target: Path, original: bytes | None) -> None:
        """Roll ``target`` back to its prior state after a failed validation.

        * If the file existed before (``original`` is bytes), atomically swap the
          original bytes back into place (same temp-file + ``os.replace`` dance
          used by :meth:`write`, so the restore is itself crash-safe).
        * If the file did NOT exist before (``original`` is ``None``), the write
          created it — remove it so no partial/broken new file is left behind.
        """
        if original is None:
            with contextlib.suppress(OSError):
                target.unlink()
            return

        fd, tmp_name = tempfile.mkstemp(
            dir=str(target.parent),
            prefix=f".{target.name}.restore.",
            suffix=".tmp",
        )
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(original)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_name, target)
        except Exception:
            with contextlib.suppress(OSError):
                os.unlink(tmp_name)
            raise

    def _confine(self, path: str) -> Path:
        """Resolve ``path`` against the workspace root and verify confinement.

        - Relative ``path`` is anchored at ``workspace_root``.
        - Absolute ``path`` is accepted only if it already lives beneath root.
        - Symlinks are followed (via ``Path.resolve``) so a link inside root
          that points outside is detected and refused.

        Raises ``ValueError`` on any escape; returns the resolved ``Path``
        on success. Missing parent directories are NOT created here — that
        happens in :meth:`write` after confinement has been confirmed.
        """
        # Mirror the applier's anchoring: (root / path).resolve(). For an
        # absolute ``path``, the ``/`` operator returns ``path`` unchanged,
        # which the relative_to check then rejects unless already inside.
        resolved = (self._workspace_root / path).resolve()
        try:
            resolved.relative_to(self._workspace_root)
        except ValueError as exc:
            raise ValueError(
                f"refusing to write outside workspace root: {path!r} "
                f"resolves to {resolved}, not beneath {self._workspace_root}"
            ) from exc

        # Ensure parent exists so the mkstemp/replace can succeed. Done after
        # confinement so we never create dirs outside the root.
        resolved.parent.mkdir(parents=True, exist_ok=True)
        return resolved
