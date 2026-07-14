from __future__ import annotations

import asyncio
import logging
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx

# Canonical SSRF predicate — the SINGLE source of truth shared by every guard
# in the codebase (auth, sanitize, connectors). Do NOT re-implement blocklists
# here; delegate so they can never drift apart.
from general_ludd.security.ssrf import is_url_blocked, resolve_and_pin

logger = logging.getLogger(__name__)


class SSRFBlockedError(ValueError):
    """Raised when a webhook URL targets a non-routable or internal address."""


def is_safe_fetch_url(url: str) -> bool:
    """SSRF guard for webhook URLs — http+https literal host deny.

    Returns ``True`` only when the URL is well-formed, uses ``http`` or ``https``
    scheme, and its LITERAL host is not a loopback / link-local / RFC-1918 /
    metadata target. Delegates the host/scheme decision to the canonical
    :func:`general_ludd.security.ssrf.is_url_blocked`. Performs NO DNS resolution
    and NO network I/O — safe to call on any hot path.

    This is the public SSRF gate for webhooks; ``register_webhook`` and
    ``_fire_webhook`` both funnel through it so the check can never drift.
    """
    if not url or not isinstance(url, str):
        return False
    return not is_url_blocked(url, scheme_allowlist=("http", "https"))


def _ensure_safe_webhook_url(url: str) -> None:
    """Raise :class:`SSRFBlockedError` if *url* must not be fetched.

    Delegates to :func:`is_safe_fetch_url` — the single SSRF decision point for
    webhooks — so registration-time and fire-time checks can never diverge.
    """
    if not is_safe_fetch_url(url):
        raise SSRFBlockedError(
            f"Webhook URL rejected by SSRF guard (internal/loopback/link-local/"
            f"metadata or bad scheme): {url!r}"
        )


# Secret key patterns — if any of these substrings appear in a payload key
# (case-insensitive), the key is stripped before the payload is forwarded to
# an external webhook endpoint (credential-exfil prevention).
_SECRET_PATTERNS: tuple[str, ...] = (
    "api_key",
    "token",
    "secret",
    "password",
    "credential",
    "authorization",
)


def _redact_payload(payload: dict[str, Any], _depth: int = 0) -> dict[str, Any]:
    """Return a redacted copy of *payload* with secret-looking keys removed.

    A key is considered sensitive when its lower-cased name contains any of
    the substrings listed in ``_SECRET_PATTERNS``.  All other keys are passed
    through unchanged.

    Recursion: dict values are redacted recursively; list values have each
    element redacted if it is a dict.  A depth cap of 10 prevents pathological
    recursion on deeply nested structures.
    """
    if _depth > 10:
        return payload
    result: dict[str, Any] = {}
    for k, v in payload.items():
        if any(pattern in k.lower() for pattern in _SECRET_PATTERNS):
            continue
        if isinstance(v, dict):
            result[k] = _redact_payload(v, _depth + 1)
        elif isinstance(v, list):
            result[k] = [
                _redact_payload(item, _depth + 1) if isinstance(item, dict) else item
                for item in v
            ]
        else:
            result[k] = v
    return result


@dataclass
class WebhookConfig:
    url: str
    headers: dict[str, str] = field(default_factory=dict, repr=False)
    retry_count: int = 1
    timeout_seconds: int = 10


@dataclass
class HookRegistration:
    hook_id: str
    event_name: str
    hook_type: str
    callback: Callable[..., Any] | None = None
    webhook_config: WebhookConfig | None = None
    priority: int = 100


class HookSystem:
    def __init__(self, event_bus: Any | None = None) -> None:
        self._hooks: dict[str, list[HookRegistration]] = {}
        self._next_cb_id = 0
        self._lock = threading.Lock()
        # Previously accepted but dropped on the floor. Store it so fire() can
        # mirror hook activity onto the event bus (a HookTriggeredEvent per
        # event fired), giving subscribers visibility into hook execution.
        self._event_bus = event_bus
        # SF-fire-webhook-sync: hold a strong reference to each in-flight webhook
        # Future. run_in_executor's Future is otherwise discarded, so it could be
        # garbage-collected mid-delivery and its exception swallowed. Tracking +
        # a done-callback keeps it alive and surfaces failures to operators.
        self._pending_webhooks: set[asyncio.Future[None]] = set()
        self._scheduled_webhooks: set[str] = set()

    def register_callback(
        self, event_name: str, callback: Callable[..., Any], priority: int = 100
    ) -> str:
        with self._lock:
            hook_id = f"hook-cb-{self._next_cb_id}"
            self._next_cb_id += 1
            reg = HookRegistration(
                hook_id=hook_id,
                event_name=event_name,
                hook_type="callback",
                callback=callback,
                priority=priority,
            )
            self._hooks.setdefault(event_name, []).append(reg)
            self._hooks[event_name].sort(key=lambda h: h.priority)
            return hook_id

    def register_webhook(
        self,
        event_name: str,
        url: str,
        headers: dict[str, str] | None = None,
        retry_count: int = 1,
        timeout_seconds: int = 10,
    ) -> str:
        # SSRF guard: reject internal/loopback/link-local/metadata URLs up front
        # so a bad target is never persisted in a HookRegistration.
        _ensure_safe_webhook_url(url)
        # D-34: clamp at registration — a caller-supplied retry_count must never
        # be stored verbatim so that fire() can't loop 10000x on a slow endpoint.
        clamped_retry = min(max(1, retry_count), 5)
        with self._lock:
            hook_id = f"hook-wh-{uuid.uuid4().hex[:8]}"
            config = WebhookConfig(
                url=url,
                headers=headers or {},
                retry_count=clamped_retry,
                timeout_seconds=timeout_seconds,
            )
            reg = HookRegistration(
                hook_id=hook_id,
                event_name=event_name,
                hook_type="webhook",
                webhook_config=config,
                priority=100,
            )
            self._hooks.setdefault(event_name, []).append(reg)
            return hook_id

    def unregister(self, hook_id: str) -> None:
        with self._lock:
            for event_name in list(self._hooks.keys()):
                self._hooks[event_name] = [
                    h for h in self._hooks[event_name] if h.hook_id != hook_id
                ]

    def fire(self, event_name: str, payload: dict[str, Any]) -> int:
        """Run every hook registered for ``event_name``.

        Returns the number of hooks that ran *successfully*. A failing hook
        does not block the others, but — unlike before — its failure is now
        surfaced at ERROR (not silently warned-and-counted-as-noise), and the
        aggregate failure count is logged so callers can detect partial
        delivery. If an event bus was supplied, a ``HookTriggeredEvent`` is
        published with the delivery tally.
        """
        with self._lock:
            hooks = list(self._hooks.get(event_name, []))
        count = 0
        failed = 0
        for hook in hooks:
            try:
                if hook.hook_type == "callback" and hook.callback is not None:
                    hook.callback(payload)
                    count += 1
                elif hook.hook_type == "webhook" and hook.webhook_config is not None:
                    with self._lock:
                        if hook.hook_id in self._scheduled_webhooks:
                            continue
                        self._scheduled_webhooks.add(hook.hook_id)
                    self._fire_webhook(hook.webhook_config, event_name, payload, hook.hook_id)
                    count += 1
            except Exception as exc:
                failed += 1
                logger.error(
                    "Hook %s failed for event %s: %s",
                    hook.hook_id,
                    event_name,
                    exc,
                    exc_info=True,
                )

        if failed:
            logger.error(
                "Hook event %s: %d/%d hook(s) failed",
                event_name,
                failed,
                len(hooks),
            )

        self._publish_hook_triggered(event_name, succeeded=count, failed=failed)
        return count

    def _publish_hook_triggered(self, event_name: str, *, succeeded: int, failed: int) -> None:
        """Mirror hook activity onto the event bus, if one was supplied.

        Best-effort: a misbehaving bus must never break hook delivery, so any
        error here is logged rather than propagated.
        """
        bus = self._event_bus
        if bus is None:
            return
        try:
            from general_ludd.events.types import HookTriggeredEvent

            event = HookTriggeredEvent(event_name=event_name)
            event.payload["succeeded"] = succeeded
            event.payload["failed"] = failed
            bus.publish(event)
        except Exception as exc:  # pragma: no cover - defensive
            logger.error(
                "Failed to publish HookTriggeredEvent for %s: %s",
                event_name,
                exc,
                exc_info=True,
            )

    def _fire_webhook(self, config: WebhookConfig, event_name: str, payload: dict[str, Any], hook_id: str) -> None:
        # Fix A: strip credential keys before forwarding.  Redaction happens
        # here (on the calling thread) so that the redacted body is captured by
        # the coroutine closure — no race on the original payload dict.
        body = {"event": event_name, "payload": _redact_payload(payload)}
        # Fix C: clamp retry_count so misconfigured values can't cause DoS.
        retry_count = min(max(1, config.retry_count), 5)

        # H.21 — DNS-resolving SSRF re-check at delivery time.
        # _ensure_safe_webhook_url runs a literal-host check at registration,
        # but a hostname can be re-bound to an internal IP between registration
        # and delivery (DNS rebinding). resolve_and_pin performs actual DNS
        # resolution and vets every resolved address, catching re-binds.
        from urllib.parse import urlsplit

        _ensure_safe_webhook_url(config.url)
        parts = urlsplit(config.url)
        host = parts.hostname
        if host:
            resolve_and_pin(host, port=(parts.port or 443), timeout=2.0)

        async def _do_post_async() -> None:
            """Retry loop using httpx.AsyncClient for native async I/O.

            Uses an async context manager so the client is properly closed
            even on error.  Unlike the prior sync httpx.post + run_in_executor
            workaround, this never consumes a thread-pool thread and cannot
            freeze the event loop.
            """
            # Defence-in-depth: re-check SSRF at fire time so a URL that was
            # somehow mutated between registration and delivery cannot reach
            # an internal address.
            if not is_safe_fetch_url(config.url):
                raise SSRFBlockedError(
                    f"Webhook URL rejected at fire time: {config.url!r}"
                )

            async with httpx.AsyncClient() as client:
                last_exc: Exception | None = None
                for attempt in range(retry_count):
                    try:
                        response = await client.post(
                            config.url,
                            json=body,
                            headers=config.headers,
                            timeout=config.timeout_seconds,
                            # Never auto-follow redirects: a 30x to an internal
                            # address would bypass the registration-time SSRF check.
                            follow_redirects=False,
                        )
                        response.raise_for_status()
                        return
                    except Exception as exc:
                        last_exc = exc
                        logger.warning(
                            "Webhook attempt %d/%d failed: %s",
                            attempt + 1,
                            retry_count,
                            exc,
                        )
                if last_exc is not None:
                    raise last_exc

        try:
            asyncio.get_running_loop()
            # fire() is sync and fire-and-forget, so we schedule the async
            # coroutine as a task on the running loop.  This yields native
            # async I/O (no thread-pool thread consumed) and, critically,
            # never freezes the event loop — the task yields to the loop
            # on every await.
            task = asyncio.ensure_future(_do_post_async())
            self._pending_webhooks.add(task)

            def _on_webhook_done(t: asyncio.Task[None]) -> None:
                self._pending_webhooks.discard(t)
                self._scheduled_webhooks.discard(hook_id)
                try:
                    t.result()
                except asyncio.CancelledError:
                    logger.warning("Webhook delivery task was cancelled")
                except Exception:
                    logger.warning(
                        "Webhook delivery failed after retries", exc_info=True
                    )

            task.add_done_callback(_on_webhook_done)
        except RuntimeError:
            # No running event loop — run the async coroutine synchronously
            # via asyncio.run (sync startup path, CLI, or unit tests without
            # an event loop).
            asyncio.run(_do_post_async())
            self._scheduled_webhooks.discard(hook_id)

    def list_hooks(self) -> list[HookRegistration]:
        result = []
        for hooks in self._hooks.values():
            result.extend(hooks)
        return result
