"""Agent-environment hibernation: dehydrate idle-waiting agents, rehydrate on resume.

When an agent parks waiting on a child N levels deep in a recursion tree, its
in-RAM environment — dominated by conversation context (message history) — can
be serialized to disk (*dehydrated*) so the resident footprint shrinks to a tiny
handle of a few hundred bytes.  Just before control returns to that agent, the
snapshot is *rehydrated*.  This lets deep recursion proceed without every idle
ancestor pinning its full context in RAM: at any instant only the active frontier
of the tree is resident, and dormant ancestors cost disk, not memory.

The blocking-wait seam this targets is
:meth:`general_ludd.agents.dispatcher.AgentDispatcher.dispatch_many` — where a
parent's coroutine (and the context captured in its frame) stays alive while its
children run.  An executor that recursively dispatches children wraps that await
in :meth:`HibernationController.parked` to release its context for the duration.

Design constraints (repo policy — see abtest/runner.py, response_cache.py notes):
  - Serialization is pydantic-validated JSON, **never pickle**.  A tampered file
    must not execute code or inject unvalidated state on rehydrate.
  - Every snapshot file is jailed under a base directory (path-traversal safe)
    and carries a SHA-256 integrity checksum verified against the trusted
    in-RAM handle on hydrate (tamper/corruption -> reject).
  - Blocking disk I/O is offloadable off the event loop via ``asyncio.to_thread``
    wrappers, mirroring the ``daemon_wiring._write_playbook`` convention.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    pass

from pydantic import BaseModel, Field

from general_ludd.agents.context import ContextMessage

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 2


class TokenCreds(BaseModel):
    """Return type for :meth:`TokenReviver.revive`."""

    role_id: str
    secret_id: str


class TokenReviver(Protocol):
    """Protocol for STS token renewal providers."""

    async def revive(self, task_id: str) -> TokenCreds:
        """Return fresh credentials for the task being resumed."""
        ...


# Characters permitted in a snapshot filename stem.  Anything else (``/``,
# ``..``, control chars) is collapsed to ``_`` so a hostile task_id such as
# ``../../etc/passwd`` cannot steer the write outside the jail.
_UNSAFE_ID = re.compile(r"[^A-Za-z0-9._-]")


def default_hibernation_dir() -> Path:
    """Resolve the default snapshot directory (env override -> XDG -> default).

    Mirrors ``db.session.get_default_db_path``: an explicit
    ``GLUDD_HIBERNATION_DIR`` wins, else ``$XDG_DATA_HOME`` (fallback
    ``~/.local/share``) ``/general-ludd/hibernation``.
    """
    override = os.environ.get("GLUDD_HIBERNATION_DIR")
    if override:
        return Path(override)
    xdg = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return Path(xdg) / "general-ludd" / "hibernation"


def _load_hibernate_mac_key(base_dir: str) -> bytes | None:
    """Load a durable MAC key from ``<base_dir>/secrets/hibernate_mac.key``.

    Fail-closed pattern (mirrors :meth:`PauseStore._load_or_create_key`):
      - Keyfile exists + secure -> read it.
      - Keyfile missing + no prior evidence -> mint a fresh one.
      - Keyfile missing/corrupt + prior evidence -> fail closed (IntegrityError).
      - Keyfile insecure (group/world readable) -> fail closed.
      - OSError + prior evidence -> fail closed.
      - OSError + no prior evidence -> degrade gracefully (return None).
    """
    secrets_dir = Path(base_dir).resolve() / "secrets"
    key_path = secrets_dir / "hibernate_mac.key"
    mac_path = Path(base_dir).resolve() / "hibernate_state.json.mac"
    keyed_marker = Path(base_dir).resolve() / ".hibernate_keyed"
    has_prior_evidence = keyed_marker.exists() or mac_path.exists()

    try:
        if key_path.exists():
            st = os.stat(key_path)
            mode = st.st_mode & 0o777
            if mode & 0o077:
                raise IntegrityError(
                    f"hibernate MAC keyfile {key_path} is group/world accessible "
                    f"(mode {oct(mode)}); refusing to use it (fail closed)."
                )
            getuid = getattr(os, "getuid", None)
            if getuid is not None and st.st_uid != getuid():
                raise IntegrityError(
                    f"hibernate MAC keyfile {key_path} is owned by uid "
                    f"{st.st_uid}, not the current user {getuid()}; refusing "
                    "(fail closed)."
                )
            data = key_path.read_bytes()
            if data:
                if not keyed_marker.exists():
                    with contextlib.suppress(OSError):
                        tmp = keyed_marker.with_name(keyed_marker.name + ".tmp")
                        tmp.write_text("keyed\n", encoding="utf-8")
                        with contextlib.suppress(OSError):
                            os.chmod(tmp, 0o600)
                        tmp.replace(keyed_marker)
                return data
            if has_prior_evidence:
                raise IntegrityError(
                    f"hibernate MAC keyfile {key_path} is empty/corrupt "
                    "but prior signed state exists; refusing to re-mint "
                    "(fail closed)."
                )
            logger.warning(
                "HibernateStore: empty MAC keyfile at %s; regenerating.",
                key_path,
            )
        elif has_prior_evidence:
            raise IntegrityError(
                f"hibernate MAC keyfile {key_path} is missing but a "
                "prior keyed/signed state exists; refusing to mint a "
                "replacement key (fail closed)."
            )
        secrets_dir.mkdir(parents=True, exist_ok=True)
        with contextlib.suppress(OSError):
            os.chmod(secrets_dir, 0o700)
        key = secrets.token_bytes(32)
        tmp = key_path.with_name(key_path.name + ".tmp")
        tmp.write_bytes(key)
        with contextlib.suppress(OSError):
            os.chmod(tmp, 0o600)
        tmp.replace(key_path)
        if not keyed_marker.exists():
            with contextlib.suppress(OSError):
                marker_tmp = keyed_marker.with_name(keyed_marker.name + ".tmp")
                marker_tmp.write_text("keyed\n", encoding="utf-8")
                with contextlib.suppress(OSError):
                    os.chmod(marker_tmp, 0o600)
                marker_tmp.replace(keyed_marker)
        return key
    except IntegrityError:
        raise
    except OSError as exc:
        if has_prior_evidence:
            raise IntegrityError(
                "HibernateStore: could not load the durable MAC key for a "
                f"previously-keyed store ({exc}); refusing to operate "
                "without integrity verification (fail closed)."
            ) from exc
        logger.warning(
            "HibernateStore: could not establish a durable MAC key (%s); "
            "operating in DEGRADED mode without integrity verification.",
            exc,
        )
        return None


def messages_from_dicts(
    raw: list[dict[str, object]],
) -> list[ContextMessage]:
    """Convert the gateway/tool-loop's untyped message dicts to ``ContextMessage``.

    The live ``ModelGateway.call_model`` / ``ToolCallLoop`` path threads plain
    ``{"role", "content", ...}`` dicts and, on ``ModelResponse``, a
    non-serializable LangChain ``raw_response``.  A faithful, rehydratable
    snapshot must capture context as :class:`ContextMessage` and discard the
    unserializable bits — this adapter is that bridge.  ``token_estimate`` is
    filled with the same ``len//4`` heuristic ``ContextCompactor`` uses.
    """
    out: list[ContextMessage] = []
    for msg in raw:
        role = str(msg.get("role", "user"))
        # Only None/absent collapse to ""; a genuine falsy value (0, False)
        # keeps its string form.  ``... or ""`` would wrongly eat ``0`` -> "".
        raw_content = msg.get("content")
        content = "" if raw_content is None else str(raw_content)
        raw_ts = msg.get("timestamp", 0.0) or 0.0
        try:
            timestamp = float(raw_ts) if isinstance(raw_ts, (int, float, str)) else 0.0
        except (ValueError, TypeError):
            timestamp = 0.0
        out.append(
            ContextMessage(
                role=role,
                content=content,
                token_estimate=len(content) // 4,
                is_system=(role == "system"),
                timestamp=timestamp,
            )
        )
    return out


class HibernationError(RuntimeError):
    """Raised when a snapshot cannot be safely written or restored."""


class HibernationIntegrityError(HibernationError):
    """Raised when a snapshot fails its checksum (tampered or corrupted)."""


# Compatibility alias retained for callers of the pre-beta public contract.
IntegrityError = HibernationIntegrityError


class DispatchState(BaseModel):
    """Resumable state of a single dispatch invocation (B3.1.5).

    Captured just before the model is called and updated per tool-loop
    iteration, then embedded in :class:`AgentEnvironmentSnapshot` so a
    checkpoint round-trips through the existing integrity-checked JSON
    envelope without a second file format. Lives here (rather than in
    ``dispatch_checkpoint.py``) because embedding it in the snapshot
    requires the class be importable from this module without a cycle.
    """

    model_config = {"strict": True}

    todo_id: str
    resolved_model_profile: str | None = None
    resolved_prompt_profile: str | None = None
    prompt_text: str = ""
    phase_marker: str = "pre_model"
    tool_iterations: int = 0
    accumulated_messages: list[ContextMessage] = Field(default_factory=list)
    lease_holder_id: str | None = None


class AgentEnvironmentSnapshot(BaseModel):
    """Serializable snapshot of a parked agent's environment.

    The heavy field is :attr:`messages` (the conversation history).  Everything
    else is small metadata needed to resume the agent exactly where it parked —
    including :attr:`depth`, its position in the recursion stack, which the
    controller uses to decide whether dehydration is worthwhile.

    B3.1.5 added :attr:`dispatch_state` (``schema_version=2``): the resumable
    bits of a single dispatch invocation, written at three lifecycle
    boundaries (pre-model, per-tool-iter, clear-on-persist) so a writer crash
    does not abandon in-flight work. v1 snapshots hydrate with
    ``dispatch_state=None`` (backward compat — they predate the field).
    """

    model_config = {"strict": True}

    task_id: str
    agent_name: str
    parent_task_id: str | None = None
    invoker_name: str = ""
    depth: int = 0
    workspace_path: str = ""
    model_profile: str | None = None
    prompt_profile: str | None = None
    messages: list[ContextMessage] = Field(default_factory=list)
    scratch: dict[str, str] = Field(default_factory=dict)
    created_at: float = 0.0
    schema_version: int = SCHEMA_VERSION
    # Embedded dispatch-lifecycle checkpoint (B3.1.5). v1 snapshots (pre-2.0)
    # lack this field and hydrate as None — ``list_interrupted`` skips those
    # because they are deep-recursion hibernation files, not dispatch
    # checkpoints.
    dispatch_state: DispatchState | None = None


class HibernationHandle(BaseModel):
    """Tiny in-RAM reference to a dehydrated snapshot on disk.

    Holding this instead of the full :class:`AgentEnvironmentSnapshot` is what
    reclaims memory: it is a few hundred bytes regardless of how large the
    dehydrated context was.  Its :attr:`checksum` is the *trusted* copy used to
    detect on-disk tampering at hydrate time.
    """

    model_config = {"strict": True}

    task_id: str
    path: str
    checksum: str
    size_bytes: int
    depth: int = 0


class HibernationStore:
    """Persists agent-environment snapshots as integrity-checked JSON on disk.

    All files live directly under *base_dir* (typically the session scratch
    directory).  The store never uses pickle; it round-trips through
    :meth:`AgentEnvironmentSnapshot.model_dump_json` / ``model_validate_json``.

    Integrity is a **keyed** HMAC-SHA256 over the payload, using a random key
    generated per store instance and held only in RAM.  A tampered on-disk file
    therefore cannot be re-signed by an attacker who lacks the key — integrity
    holds even independently of the trusted in-RAM handle.  Because the key is
    ephemeral, snapshots are scoped to the lifetime of THIS store (in-process
    hibernation for deep recursion); they are intentionally not portable across
    a process restart.  A durable variant would key the MAC from ``secrets/``.
    """

    def __init__(
        self,
        base_dir: str | Path | None = None,
        mac_key: bytes | None = None,
    ) -> None:
        """Initialize an owner-only snapshot store with a signing key."""
        self._base = Path(
            base_dir if base_dir is not None else default_hibernation_dir()
        ).resolve()
        self._base.mkdir(parents=True, exist_ok=True)
        with contextlib.suppress(OSError):
            os.chmod(self._base, 0o700)
        self._mac_key = mac_key if mac_key is not None else secrets.token_bytes(32)

    @property
    def base_dir(self) -> Path:
        """Return the resolved directory that contains snapshot files."""
        return self._base

    def _path_for(self, task_id: str) -> Path:
        stem = _UNSAFE_ID.sub("_", task_id).strip("._") or "unnamed"
        digest = hashlib.sha256(task_id.encode("utf-8")).hexdigest()[:12]
        candidate = (self._base / f"{stem}-{digest}.snapshot.json").resolve()
        # Jail: the resolved candidate must live *directly* under base.  This
        # catches both a sanitizer miss and a base_dir that is itself a symlink.
        if candidate.parent != self._base:
            raise HibernationError(
                f"refusing snapshot path that escapes base dir: {task_id!r}"
            )
        return candidate

    def _checksum(self, payload: str) -> str:
        return hmac.new(
            self._mac_key, payload.encode("utf-8"), hashlib.sha256
        ).hexdigest()

    def dehydrate(self, snap: AgentEnvironmentSnapshot) -> HibernationHandle:
        """Serialize *snap* to disk and return a lightweight handle.

        The write is atomic (temp file + ``replace``) so a crash mid-write can
        never leave a half-written snapshot that would fail to hydrate.
        """
        payload = snap.model_dump_json()
        checksum = self._checksum(payload)
        envelope = json.dumps(
            {
                "schema_version": SCHEMA_VERSION,
                "checksum": checksum,
                "payload": payload,
            }
        )
        path = self._path_for(snap.task_id)
        tmp = path.with_name(path.name + ".tmp")
        try:
            tmp.write_text(envelope, encoding="utf-8")
            # Owner-only file (0o600) BEFORE the atomic swap, so the snapshot is
            # never briefly world-readable — a snapshot can carry sensitive
            # context (prompts, tool output).
            with contextlib.suppress(OSError):
                os.chmod(tmp, 0o600)
            tmp.replace(path)
        except OSError as exc:
            with contextlib.suppress(OSError):
                tmp.unlink()
            raise HibernationError(f"failed to write snapshot: {exc}") from exc
        logger.debug(
            "dehydrated %s (depth=%d, %d msg, %d bytes)",
            snap.task_id,
            snap.depth,
            len(snap.messages),
            len(envelope),
        )
        return HibernationHandle(
            task_id=snap.task_id,
            path=str(path),
            checksum=checksum,
            size_bytes=len(envelope),
            depth=snap.depth,
        )

    def hydrate(self, handle: HibernationHandle) -> AgentEnvironmentSnapshot:
        """Read, integrity-check, and validate the snapshot for *handle*.

        Raises :class:`IntegrityError` if the on-disk payload does not match the
        checksum carried in the trusted in-RAM *handle* — defeating an attacker
        who rewrites both the payload and the envelope's self-declared checksum.
        """
        path = Path(handle.path)
        if path.resolve().parent != self._base:
            raise HibernationError(
                f"refusing to hydrate path outside base dir: {handle.path!r}"
            )
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise HibernationError(
                f"snapshot for {handle.task_id!r} unreadable: {exc}"
            ) from exc
        try:
            envelope = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise IntegrityError(
                f"snapshot envelope for {handle.task_id!r} is not valid JSON"
            ) from exc
        if not isinstance(envelope, dict):
            raise IntegrityError(f"malformed snapshot envelope for {handle.task_id!r}")
        payload = envelope.get("payload")
        stored = envelope.get("checksum")
        if not isinstance(payload, str) or not isinstance(stored, str):
            raise IntegrityError(f"malformed snapshot envelope for {handle.task_id!r}")
        actual = self._checksum(payload)
        # Compare against BOTH the envelope's self-declared MAC (catches a naive
        # edit) AND the trusted in-RAM handle MAC (catches a re-signed one).
        # Constant-time compare: the MAC is keyed, so a plain ``!=`` would be a
        # timing oracle on the key.
        if not hmac.compare_digest(actual, stored) or not hmac.compare_digest(
            actual, handle.checksum
        ):
            raise IntegrityError(
                f"checksum mismatch for {handle.task_id!r}: snapshot was "
                "tampered with or corrupted"
            )
        return AgentEnvironmentSnapshot.model_validate_json(payload)

    def discard(self, handle: HibernationHandle) -> None:
        """Remove the on-disk snapshot; a missing file is not an error."""
        with contextlib.suppress(OSError):
            Path(handle.path).unlink()

    async def dehydrate_async(
        self, snap: AgentEnvironmentSnapshot
    ) -> HibernationHandle:
        """Serialize a snapshot without blocking the event loop."""
        return await asyncio.to_thread(self.dehydrate, snap)

    async def hydrate_async(
        self, handle: HibernationHandle
    ) -> AgentEnvironmentSnapshot:
        """Restore a snapshot without blocking the event loop."""
        return await asyncio.to_thread(self.hydrate, handle)

    async def discard_async(self, handle: HibernationHandle) -> None:
        """Discard a snapshot without blocking the event loop."""
        await asyncio.to_thread(self.discard, handle)


class ParkedEnv:
    """Handle returned by :meth:`HibernationController.parked`.

    While the ``async with`` block runs, a *dehydrated* env keeps only the tiny
    :class:`HibernationHandle` alive — the caller is free to drop its own
    reference to the context.  On block exit the env is rehydrated and exposed
    via :attr:`snapshot`.

    When *token_reviver* is provided, ``_resume()`` calls
    ``token_reviver.revive()`` after rehydration and stores the fresh STS
    credentials in ``snapshot.scratch`` (keys ``sts_role_id`` and
    ``sts_secret_id``) for injection into the revived agent.
    """

    def __init__(
        self,
        store: HibernationStore,
        original: AgentEnvironmentSnapshot,
        handle: HibernationHandle | None,
        token_reviver: TokenReviver | None = None,
    ) -> None:
        """Track either a resident snapshot or its dehydrated handle."""
        self._store = store
        # When dehydrated we deliberately drop the strong reference to the
        # original so it can be garbage-collected — that is the whole point.
        self._original: AgentEnvironmentSnapshot | None = (
            None if handle is not None else original
        )
        self._handle = handle
        self._token_reviver = token_reviver
        self.snapshot: AgentEnvironmentSnapshot | None = None

    @property
    def dehydrated(self) -> bool:
        """Return whether the environment currently resides on disk."""
        return self._handle is not None

    @property
    def handle(self) -> HibernationHandle | None:
        """Return the on-disk snapshot handle, if one exists."""
        return self._handle

    async def _resume(self) -> None:
        if self._handle is None:
            self.snapshot = self._original
            return
        try:
            self.snapshot = await self._store.hydrate_async(self._handle)
            if self._token_reviver is not None and self.snapshot is not None:
                await self._revive_token(self.snapshot)
        finally:
            # Always remove the on-disk snapshot, even if hydrate failed (e.g.
            # IntegrityError) — a parked file must never leak on the floor.
            await self._store.discard_async(self._handle)

    async def _revive_token(self, snapshot: AgentEnvironmentSnapshot) -> None:
        """Mint a fresh STS secret_id and store it in ``snapshot.scratch``."""
        if self._token_reviver is None:
            return
        try:
            creds = await self._token_reviver.revive(snapshot.task_id)
            snapshot.scratch["sts_role_id"] = creds.role_id
            snapshot.scratch["sts_secret_id"] = creds.secret_id
        except Exception:
            logger.exception(
                "Failed to revive STS token for agent=%s", snapshot.task_id
            )


class HibernationController:
    """Policy for *when* to dehydrate a parked agent and the resume plumbing.

    An env is worth dehydrating only when it is both deep enough in the
    recursion stack (so it will stay dormant for a while) and heavy enough (so
    the reclaimed RAM justifies the disk round-trip).  Shallow or near-empty
    envs are left resident — dehydrating them would cost more than it saves.

    When *token_reviver* is provided, the controller calls
    ``token_reviver.revive()`` on every rehydration to mint a fresh STS
    secret_id for the revived agent (Phase P3).
    """

    def __init__(
        self,
        store: HibernationStore,
        *,
        min_depth: int = 3,
        min_context_messages: int = 8,
        clock: Callable[[], float] | None = None,
        token_reviver: TokenReviver | None = None,
    ) -> None:
        """Configure thresholds and optional token renewal for parked agents."""
        self._store = store
        self._min_depth = min_depth
        self._min_context_messages = min_context_messages
        self._clock: Callable[[], float] = clock if clock is not None else time.monotonic
        self._paused_projects: set[str] = set()
        self._token_reviver = token_reviver

    def pause_project(self, project_id: str) -> None:
        """Mark a project as paused so its dispatch is gated."""
        self._paused_projects.add(project_id)

    def resume_project(self, project_id: str) -> None:
        """Resume a previously paused project."""
        self._paused_projects.discard(project_id)

    def is_paused(self, scope: str, identifier: str) -> bool:
        """Return True when *identifier* of *scope* is currently paused.

        At present only ``"project"`` scope is supported; a paused project
        blocks dispatch of tasks carrying its ``project_id``.
        """
        if scope == "project":
            return identifier in self._paused_projects
        return False

    def should_dehydrate(self, snap: AgentEnvironmentSnapshot) -> bool:
        """Return whether a snapshot meets the depth and context thresholds."""
        return (
            snap.depth >= self._min_depth
            and len(snap.messages) >= self._min_context_messages
        )

    @asynccontextmanager
    async def parked(
        self, snap: AgentEnvironmentSnapshot
    ) -> AsyncIterator[ParkedEnv]:
        """Dehydrate *snap* (if policy warrants) for the duration of the block.

        Usage — wrap the await where an agent waits on its children::

            async with controller.parked(env) as parked:
                # `env` may now be dropped; if parked.dehydrated the context
                # is on disk and this frame holds only a tiny handle.
                results = await dispatcher.dispatch_many(children)
            resumed = parked.snapshot   # rehydrated env, ready to continue
        """
        handle: HibernationHandle | None = None
        if self.should_dehydrate(snap):
            handle = await self._store.dehydrate_async(snap)
        parked = ParkedEnv(self._store, snap, handle,
                           token_reviver=self._token_reviver)
        if handle is not None:
            # Release THIS generator frame's last strong reference to the heavy
            # snapshot (ParkedEnv already dropped its own).  Without this ``del``
            # the suspended frame pins ``snap`` — including its full message
            # list — across the whole ``async with`` block and no memory is
            # reclaimed.  This line is the actual reclaim.
            del snap
        try:
            yield parked
        finally:
            await parked._resume()
