from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx

logger = logging.getLogger(__name__)


@dataclass
class WebhookConfig:
    url: str
    headers: dict[str, str] = field(default_factory=dict)
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

    def register_callback(
        self, event_name: str, callback: Callable[..., Any], priority: int = 100
    ) -> str:
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
        hook_id = f"hook-wh-{uuid.uuid4().hex[:8]}"
        config = WebhookConfig(
            url=url,
            headers=headers or {},
            retry_count=retry_count,
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
        for event_name in list(self._hooks.keys()):
            self._hooks[event_name] = [
                h for h in self._hooks[event_name] if h.hook_id != hook_id
            ]

    def fire(self, event_name: str, payload: dict[str, Any]) -> int:
        hooks = self._hooks.get(event_name, [])
        count = 0
        for hook in hooks:
            try:
                if hook.hook_type == "callback" and hook.callback is not None:
                    hook.callback(payload)
                    count += 1
                elif hook.hook_type == "webhook" and hook.webhook_config is not None:
                    self._fire_webhook(hook.webhook_config, event_name, payload)
                    count += 1
            except Exception as exc:
                logger.warning("Hook %s error for event %s: %s", hook.hook_id, event_name, exc)
        return count

    def _fire_webhook(self, config: WebhookConfig, event_name: str, payload: dict[str, Any]) -> None:
        body = {"event": event_name, "payload": payload}
        last_exc: Exception | None = None
        for attempt in range(config.retry_count):
            try:
                httpx.post(
                    config.url,
                    json=body,
                    headers=config.headers,
                    timeout=config.timeout_seconds,
                )
                return
            except Exception as exc:
                last_exc = exc
                logger.warning("Webhook attempt %d/%d failed: %s", attempt + 1, config.retry_count, exc)
        if last_exc is not None:
            raise last_exc

    def list_hooks(self) -> list[HookRegistration]:
        result = []
        for hooks in self._hooks.values():
            result.extend(hooks)
        return result
