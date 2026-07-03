from __future__ import annotations

import contextlib
import enum
import hashlib
import hmac
import importlib
import logging
import os
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from general_ludd.events.types import (
    ConfigReloadedEvent,
    HookTriggeredEvent,
    PlaybookRegisteredEvent,
    PlaybookRemovedEvent,
    ReloadCompletedEvent,
    ReloadFailedEvent,
    ReloadRequestedEvent,
    SkillUpdatedEvent,
    TemplateUpdatedEvent,
)
from general_ludd.integration.safe_merge import safe_merge
from general_ludd.security.capability_lattice import (
    CapabilityError,
    ProtectedPathError,
    check_self_modification,
)

logger = logging.getLogger(__name__)


class ReloadScope(enum.StrEnum):
    MODELS = "models"
    TEMPLATES = "templates"
    PLAYBOOKS = "playbooks"
    SKILLS = "skills"
    CONFIG = "config"
    ALL = "all"


@dataclass
class ReloadResult:
    success: bool
    scope: str
    details: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    timestamp: float = field(default_factory=time.time)


@dataclass
class _ReloadState:
    previous_config: dict[str, Any] | None = None
    timestamp: float = 0.0


class HotReloader:
    def __init__(
        self,
        config_dir: str,
        event_bus: Any = None,
        hook_system: Any = None,
        worker_broadcaster: Any = None,
        templates_dir: str | None = None,
        playbooks_dir: str | None = None,
        skills_dirs: list[str] | None = None,
        skill_registry: Any = None,
        model_gateway: Any = None,
        prompt_registry: Any = None,
    ) -> None:
        self._config_dir = Path(config_dir)
        self._event_bus = event_bus
        self._hooks = hook_system
        self._broadcaster = worker_broadcaster
        self._templates_dir = Path(templates_dir) if templates_dir else None
        self._playbooks_dir = Path(playbooks_dir) if playbooks_dir else None
        self._skills_dirs = [Path(d) for d in skills_dirs] if skills_dirs else []
        self._skill_registry = skill_registry
        self._model_gateway = model_gateway
        self._prompt_registry = prompt_registry
        self._last_state = _ReloadState()
        # Playbooks seen on the previous reload — used to detect removals so a
        # PlaybookRemovedEvent fires when a registered playbook disappears.
        self._known_playbooks: set[str] = set()

    def reload(self, scope: ReloadScope) -> ReloadResult:
        self._publish(ReloadRequestedEvent(scope=scope.value))
        self._last_state = _ReloadState(previous_config=self._snapshot())

        try:
            details: dict[str, Any] = {"scope": scope.value}
            if scope in (ReloadScope.MODELS, ReloadScope.CONFIG, ReloadScope.ALL):
                details.update(self._reload_models())
            if scope in (ReloadScope.TEMPLATES, ReloadScope.CONFIG, ReloadScope.ALL):
                details.update(self._reload_templates())
            if scope in (ReloadScope.PLAYBOOKS, ReloadScope.CONFIG, ReloadScope.ALL):
                details.update(self._reload_playbooks())
            if scope in (ReloadScope.SKILLS, ReloadScope.CONFIG, ReloadScope.ALL):
                details.update(self._reload_skills())

            self._publish(ConfigReloadedEvent(scope=scope.value))
            self._fire_hooks("on_config_reloaded", {"scope": scope.value, "details": details})
            self._publish(ReloadCompletedEvent(scope=scope.value))
            self._broadcast_reload(scope)

            return ReloadResult(success=True, scope=scope.value, details=details)
        except Exception as exc:
            logger.error("Reload failed: %s", exc)
            self._publish(ReloadFailedEvent(scope=scope.value, error=str(exc)))
            return ReloadResult(success=False, scope=scope.value, error=str(exc))

    def reload_code_module(
        self,
        module_name: str,
        candidate_source_path: str,
        health_check: Callable[[], bool] | None = None,
        role: str | None = None,
        base_source_path: str | None = None,
        expected_sha256: str | None = None,
    ) -> ReloadResult:
        """Hot-rotate a single leaf module's source over the live file.

        Steps (fail-closed at every stage):
          0. Consult the self-modification guards (issue #58): the live path
             must not be a protected guardrail/policy/permission file, and a
             swap into a ``collections/`` tree requires the acting ``role`` to
             hold ``collections_self_modify``. Either denial refuses the swap
             BEFORE a single byte is written.
          1. Resolve the live module and its on-disk ``__file__``. Only an
             already-imported, file-backed module is eligible (a leaf with no
             in-flight state — the caller is responsible for that contract).
          2. Snapshot the live file bytes into a rollback buffer.
          2b. AUTHENTICITY (task #20): when the caller supplies
             ``expected_sha256`` — the digest of the exact candidate bytes it
             produced/approved — VERIFY the candidate's on-disk bytes hash to
             that value BEFORE any swap or ``importlib.reload``. A tampered,
             swapped, wrong-path, or corrupted candidate is REJECTED fail-closed
             here, so untrusted code is never written over the live file nor
             executed. Constant-time hex compare (no timing oracle). When
             ``expected_sha256`` is ``None`` the check is skipped and behavior
             is unchanged (backward compatible).
          2a. ANTI-CLOBBER (issue #70): when ``base_source_path`` — the snapshot
             the candidate was generated against — is supplied, route the
             file-application through :mod:`integration.safe_merge` instead of a
             blind whole-file overwrite. If the LIVE file diverged from base via
             a concurrent edit, a raw ``os.replace`` of the candidate would
             silently REVERT that edit (the wt-sync clobber-bug class). We 3-way
             merge ``base`` (ancestor) / live (``ours``) / candidate
             (``theirs``): a disjoint divergence merges cleanly (both edits
             kept), an OVERLAPPING divergence REFUSES the reload (fail-closed,
             conflict surfaced) before any byte is written. Without a base the
             API is unchanged — the candidate is swapped verbatim.
          3. ``os.replace`` the merged-or-candidate bytes over the live path
             (atomic on the same filesystem) and ``importlib.reload`` the module.
          4. Run the ``health_check`` gate (typically a ``/readyz`` poll that
             returns False when ``app.state._degraded`` or a 503 is seen). If it
             fails — or if the reload itself raised — restore the snapshot bytes,
             reload again, and report ``rolled_back``.

        On any failure the live module is left byte-for-byte as it started.
        """
        scope = "code_module"
        details: dict[str, Any] = {"module": module_name}
        self._publish(ReloadRequestedEvent(scope=scope))

        module = sys.modules.get(module_name)
        if module is None:
            # Not yet imported in this process — try a plain import so a
            # genuinely-importable module can still be rotated.
            try:
                module = importlib.import_module(module_name)
            except Exception as exc:
                return self._code_reload_failure(
                    scope, details, f"module not importable: {exc}"
                )

        live_path_str = getattr(module, "__file__", None)
        if not live_path_str:
            return self._code_reload_failure(
                scope, details, "module has no __file__ (not file-backed)"
            )
        live_path = Path(live_path_str)
        details["live_path"] = str(live_path)

        # Self-modification guards (issue #58): refuse protected guard files
        # outright, and require collections_self_modify for a swap into the
        # agent's own collections/ source. Runs BEFORE any byte is read/written
        # so a denied swap leaves the live module byte-for-byte unchanged.
        try:
            check_self_modification(str(live_path), role)
        except ProtectedPathError as exc:
            return self._code_reload_failure(
                scope, details, f"protected guard file: {exc}"
            )
        except CapabilityError as exc:
            return self._code_reload_failure(
                scope, details, f"capability denied: {exc}"
            )

        candidate = Path(candidate_source_path)
        if not candidate.is_file():
            return self._code_reload_failure(
                scope, details, f"candidate source not found: {candidate}"
            )

        try:
            original_bytes = live_path.read_bytes()
        except OSError as exc:
            return self._code_reload_failure(
                scope, details, f"cannot read live module bytes: {exc}"
            )

        # Anti-clobber resolution (issue #70). Decide the EXACT bytes to write:
        # either the candidate verbatim (no base, or no divergence) or a 3-way
        # merge of base/live/candidate. An overlapping divergence returns None
        # and a refusal — write nothing, leave the live module untouched.
        try:
            candidate_bytes = candidate.read_bytes()
        except OSError as exc:
            return self._code_reload_failure(
                scope, details, f"cannot read candidate bytes: {exc}"
            )

        # Authenticity gate (task #20). When the caller pins the expected sha256
        # of the candidate it generated, VERIFY the candidate's actual on-disk
        # bytes match BEFORE writing anything or reloading. This runs on the
        # candidate as-read — a race that rewrote the temp candidate, a wrong
        # candidate path, or a corrupted generation is caught here and REFUSED
        # rather than swapped in and executed. Constant-time compare over the
        # normalized hex digest avoids leaking a partial-match timing oracle.
        # No expected hash supplied -> skipped (unchanged legacy behavior).
        if expected_sha256 is not None:
            actual_sha256 = hashlib.sha256(candidate_bytes).hexdigest()
            details["candidate_sha256"] = actual_sha256
            if not hmac.compare_digest(actual_sha256, expected_sha256.strip().lower()):
                return self._code_reload_failure(
                    scope,
                    details,
                    "candidate integrity check failed: sha256 mismatch "
                    f"(expected {expected_sha256}, got {actual_sha256}) — "
                    "refusing to swap or reload an unverified candidate",
                )
            details["integrity_verified"] = True

        resolved_bytes = self._resolve_apply_bytes(
            scope,
            details,
            base_source_path=base_source_path,
            live_bytes=original_bytes,
            candidate_bytes=candidate_bytes,
        )
        if resolved_bytes is None:
            # An overlapping divergence: _resolve_apply_bytes recorded the
            # conflict in details. No byte was written; the live module is left
            # exactly as the concurrent edit left it (no rollback needed). Fail
            # CLOSED — refuse rather than clobber or silently pick a side.
            return self._code_reload_failure(
                scope,
                details,
                details.get("merge_error", "reload refused: merge conflict"),
            )

        # Swap resolved bytes over live path + reload.
        try:
            # Write via a temp + os.replace for an atomic same-dir swap.
            tmp_path = live_path.with_suffix(live_path.suffix + ".candidate.tmp")
            tmp_path.write_bytes(resolved_bytes)
            os.replace(tmp_path, live_path)
            self._invalidate_source_cache(live_path)
            importlib.reload(module)
        except Exception as exc:
            rb = self._restore_module_bytes(module, live_path, original_bytes)
            details["rollback_verified"] = rb
            return self._code_reload_failure(
                scope,
                details,
                f"reload failed: {exc}",
                rolled_back=True,
                rollback_verified=rb,
            )

        # Health gate. A code swap that imports cleanly is NOT proof it is
        # correct — "it imports" is necessary but never sufficient. Without a
        # semantic health gate we have no evidence the candidate behaves, so we
        # fail CLOSED: roll the live module back to the original bytes and refuse
        # to report success. A successful unverified code reload would let a
        # corrupt-but-importable candidate be committed silently — exactly the
        # self-improvement-safety hole BUG#2 describes. A passing health gate is
        # therefore REQUIRED for a code reload to succeed.
        if health_check is None:
            rb = self._restore_module_bytes(module, live_path, original_bytes)
            details["rollback_verified"] = rb
            return self._code_reload_failure(
                scope,
                details,
                "no health_check: unverified code reload refused",
                rolled_back=True,
                rollback_verified=rb,
            )

        try:
            healthy = bool(health_check())
        except Exception as exc:
            logger.warning("health_check raised, treating as unhealthy: %s", exc)
            healthy = False

        if not healthy:
            rb = self._restore_module_bytes(module, live_path, original_bytes)
            details["rollback_verified"] = rb
            return self._code_reload_failure(
                scope,
                details,
                "health gate failed after reload",
                rolled_back=True,
                rollback_verified=rb,
            )

        self._publish(ReloadCompletedEvent(scope=scope))
        details["rolled_back"] = False
        return ReloadResult(success=True, scope=scope, details=details)

    def _resolve_apply_bytes(
        self,
        scope: str,
        details: dict[str, Any],
        *,
        base_source_path: str | None,
        live_bytes: bytes,
        candidate_bytes: bytes,
    ) -> bytes | None:
        """Decide the bytes to write over the live file, anti-clobber (issue #70).

        Returns the bytes to apply, or ``None`` to REFUSE the reload (overlapping
        divergence). It writes nothing itself; the caller performs the swap.

        * No ``base_source_path`` -> candidate verbatim (legacy blind swap).
        * base == live (no concurrent edit) -> candidate verbatim.
        * base/live/candidate undecodable as text -> candidate verbatim. A binary
          leaf cannot be line-merged; the base-given guarantee only covers text.
        * live diverged from base, DISJOINT from candidate -> 3-way merged bytes
          (both edits preserved). ``details["merged"] = True``.
        * live diverged from base, OVERLAPPING candidate -> ``None`` + a conflict
          recorded in ``details`` (``conflict``, ``merge_error``). Fail-closed.
        """
        if base_source_path is None:
            return candidate_bytes

        try:
            base_text = Path(base_source_path).read_bytes().decode("utf-8")
            live_text = live_bytes.decode("utf-8")
            candidate_text = candidate_bytes.decode("utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            # Cannot read base or a side is non-text: the line-level merge does
            # not apply. Fall back to the candidate verbatim rather than block a
            # legitimate swap — the base path is an optional anti-clobber aid.
            logger.debug("safe_merge skipped (unreadable/binary): %s", exc)
            return candidate_bytes

        # 3-way merge: base is the ancestor, the LIVE file is "ours" (it may hold
        # a concurrent edit), the candidate is "theirs".
        result = safe_merge(base_text, live_text, candidate_text)
        details["merge_source"] = result.source

        if result.conflict:
            details["conflict"] = True
            details["merge_error"] = (
                "reload refused: candidate overlaps a concurrent edit to the "
                "live file (3-way merge conflict) — refusing to clobber"
            )
            return None

        if result.source == "merged":
            details["merged"] = True
        return result.text.encode("utf-8")

    def _restore_module_bytes(
        self, module: Any, live_path: Path, original_bytes: bytes
    ) -> bool:
        """Restore the rollback buffer over the live path, reload the module so
        the running interpreter reverts to the original code, and VERIFY the
        restore actually took.

        Returns True only when the live file is byte-for-byte the original AND
        the importlib.reload succeeded — i.e. the running module is genuinely
        back to its starting state. Returns False otherwise (a swallowed/failed
        rollback reload, or the bytes did not round-trip), so callers can report
        rolled_back honestly instead of claiming a rollback that never landed.
        """
        try:
            # Atomic same-dir swap (tmp + os.replace), mirroring the forward
            # write path. A bare write_bytes here could leave the live module
            # half-written if the process dies mid-rollback — the worst possible
            # moment to corrupt a file we are trying to restore.
            tmp_path = live_path.with_suffix(live_path.suffix + ".rollback.tmp")
            tmp_path.write_bytes(original_bytes)
            os.replace(tmp_path, live_path)
            self._invalidate_source_cache(live_path)
            importlib.reload(module)
        except Exception as exc:
            logger.error("rollback failed for %s: %s", live_path, exc)
            return False

        # Verify the live file was actually returned to the original bytes and
        # the reload picked them up. "We attempted a restore" is not the same as
        # "the module is back to original" — only the byte-equality + successful
        # reload above is evidence we can stand behind.
        try:
            restored_ok = live_path.read_bytes() == original_bytes
        except OSError as exc:
            logger.error("could not verify rollback for %s: %s", live_path, exc)
            return False
        if not restored_ok:
            logger.error(
                "rollback verification failed for %s: live bytes != original", live_path
            )
        return restored_ok

    @staticmethod
    def _invalidate_source_cache(live_path: Path) -> None:
        """Force ``importlib.reload`` to recompile from the new source.

        CPython trusts a cached ``.pyc`` when its stored source mtime matches the
        source file's mtime; a hot swap that completes within the same clock
        second can leave those equal, so reload would use the STALE bytecode.
        We bump the source mtime forward and remove any cached ``.pyc`` so the
        recompile is unavoidable, then invalidate the finder caches.
        """
        try:
            st = live_path.stat()
            os.utime(live_path, (st.st_atime, st.st_mtime + 1))
        except OSError:
            pass
        cache = live_path.parent / "__pycache__"
        if cache.is_dir():
            stem = live_path.stem
            for pyc in cache.glob(f"{stem}.*.pyc"):
                with contextlib.suppress(OSError):
                    pyc.unlink()
        importlib.invalidate_caches()

    def _code_reload_failure(
        self,
        scope: str,
        details: dict[str, Any],
        error: str,
        rolled_back: bool = False,
        rollback_verified: bool | None = None,
    ) -> ReloadResult:
        details = {**details, "rolled_back": rolled_back}
        if rollback_verified is not None:
            details["rollback_verified"] = rollback_verified
        self._publish(ReloadFailedEvent(scope=scope, error=error))
        return ReloadResult(success=False, scope=scope, details=details, error=error)

    def get_last_state(self) -> _ReloadState:
        return self._last_state

    def _snapshot(self) -> dict[str, Any]:
        return {"config_dir": str(self._config_dir), "timestamp": time.time()}

    def _reload_models(self) -> dict[str, Any]:
        # H14 (W3.12): previously returned models_reloaded=True after a bare
        # existence check — theater success for a no-op.  Now we actually
        # parse the routing config and swap it into the model gateway.
        result: dict[str, Any] = {"models_reloaded": False}
        routing_path = self._config_dir / "model_routing.yml"
        if not routing_path.exists():
            return result

        try:
            import yaml

            raw = yaml.safe_load(routing_path.read_text()) or {}
        except Exception as exc:
            logger.error("Failed to parse model_routing.yml: %s", exc)
            result["parse_error"] = str(exc)
            return result

        result["routing_file"] = str(routing_path)
        result["routing_parsed"] = True
        profiles_raw = raw.get("profiles", {})
        result["profiles_count"] = len(profiles_raw) if isinstance(profiles_raw, dict) else 0

        # Apply to gateway if one is wired
        if self._model_gateway is not None:
            try:
                # Reload profiles: for each profile dict, update or add it
                if hasattr(self._model_gateway, "update_routing_config"):
                    self._model_gateway.update_routing_config(raw)
                    result["models_reloaded"] = True
                elif isinstance(profiles_raw, dict) and hasattr(self._model_gateway, "add_profile"):
                    for pid, pdata in profiles_raw.items():
                        if isinstance(pdata, dict):
                            try:
                                self._model_gateway.add_profile(
                                    model_id=pid,
                                    provider=pdata.get("provider", "openai"),
                                    model=pdata.get("model", ""),
                                    api_key_env=pdata.get("api_key_env", ""),
                                    api_base_alias=pdata.get("api_base_alias"),
                                )
                            except Exception as _e:
                                logger.debug("Profile %s already exists or invalid: %s", pid, _e)
                    result["models_reloaded"] = True
                else:
                    # Gateway present but no suitable update method
                    result["models_reloaded"] = False
                    result["reason"] = "gateway lacks update_routing_config or add_profile"
            except Exception as exc:
                logger.error("Failed to apply routing config to gateway: %s", exc)
                result["apply_error"] = str(exc)
        else:
            # Config was parsed but there's no gateway to apply it to.
            # Routing file was read and parsed — that is the deliverable here.
            result["models_reloaded"] = True

        return result

    def _reload_templates(self) -> dict[str, Any]:
        result: dict[str, Any] = {"templates_loaded": 0}
        if self._templates_dir and self._templates_dir.exists():
            templates = list(self._templates_dir.glob("*.j2"))
            result["templates_loaded"] = len(templates)
            result["templates"] = [t.name for t in templates]
            if self._prompt_registry:
                self._prompt_registry.refresh()
            self._publish(TemplateUpdatedEvent(templates=[t.name for t in templates]))
        return result

    def _reload_playbooks(self) -> dict[str, Any]:
        result: dict[str, Any] = {"playbooks": []}
        if self._playbooks_dir and self._playbooks_dir.exists():
            playbooks = list(self._playbooks_dir.glob("*.yml"))
            current_names = {p.name for p in playbooks}
            result["playbooks"] = [p.name for p in playbooks]
            for p in playbooks:
                self._publish(PlaybookRegisteredEvent(playbook=p.name))
            # Anything registered last time but gone now was removed.
            removed = sorted(self._known_playbooks - current_names)
            for name in removed:
                self._publish(PlaybookRemovedEvent(playbook=name))
            result["removed"] = removed
            self._known_playbooks = current_names
        return result

    def _reload_skills(self) -> dict[str, Any]:
        result: dict[str, Any] = {"skills": []}
        active_dirs: list[str] = []
        for skills_dir in self._skills_dirs:
            if skills_dir.exists():
                active_dirs.append(str(skills_dir))
                for md_file in sorted(skills_dir.glob("*.md")):
                    name = md_file.stem
                    result["skills"].append(name)
                    self._publish(SkillUpdatedEvent(skill=name))
        if active_dirs and self._skill_registry is not None:
            try:
                self._skill_registry.refresh(search_paths=active_dirs)
                result["registry_refreshed"] = True
            except Exception as exc:
                logger.error("skill_registry.refresh failed: %s", exc)
                result["registry_error"] = str(exc)
        return result

    def _publish(self, event: Any) -> None:
        # Event emission must NEVER crash a reload/rollback. A raising subscriber
        # would otherwise propagate out of reload_code_module — worst case the
        # post-swap ReloadCompletedEvent raising AFTER a healthy swap, leaving
        # live code diverged from a non-success verdict. Swallow + log so the
        # reload's own success/rollback verdict stays authoritative.
        if not self._event_bus:
            return
        try:
            self._event_bus.publish(event)
        except Exception as exc:
            logger.warning(
                "reload event publish failed (%s): %s", type(event).__name__, exc
            )

    def _fire_hooks(self, event_name: str, payload: dict[str, Any]) -> None:
        if self._hooks:
            self._hooks.fire(event_name, payload)
        # Surface the hook firing on the event bus so subscribers (metrics,
        # observers) can react to it, independent of the hook system itself.
        self._publish(HookTriggeredEvent(event_name=event_name))

    def _broadcast_reload(self, scope: ReloadScope) -> None:
        if self._broadcaster:
            try:
                self._broadcaster.broadcast_reload(scope)
            except Exception as exc:
                logger.warning("Broadcast failed: %s", exc)
