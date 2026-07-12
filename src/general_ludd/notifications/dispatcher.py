from __future__ import annotations

import logging
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)

BACKEND_NAMES = frozenset({"slack", "stdout", "webhook"})
PRIORITY_LEVELS: dict[str, int] = {"low": 0, "medium": 1, "high": 2, "urgent": 3}

FALLBACK_NOTIFICATION_CONFIG: dict[str, Any] = {
    "enabled": False,
    "backends": {"stdout": {}},
    "min_priority": "high",
}

NOTIFICATION_TEMPLATE = (
    "[gludd] {priority} human-todo #{id}: {title}\n"
    "  Category: {category}\n"
    "  Agent: {agent_id}\n"
    "  {body}"
)


@runtime_checkable
class HttpTransport(Protocol):
    def post(
        self,
        url: str,
        *,
        headers: dict[str, str],
        data: dict[str, object] | None = ...,
        json: dict[str, object] | None = ...,
        timeout: float = ...,
    ) -> Any: ...


class NotificationDispatcher:
    def __init__(
        self,
        config: dict[str, Any],
        *,
        slack_sources: dict[str, Any] | None = None,
        transport: HttpTransport | None = None,
        env: dict[str, str] | None = None,
    ) -> None:
        self._enabled = config.get("enabled", FALLBACK_NOTIFICATION_CONFIG["enabled"])
        self._backends: dict[str, dict[str, Any]] = config.get(
            "backends", FALLBACK_NOTIFICATION_CONFIG["backends"]
        )
        self._min_priority = config.get(
            "min_priority", FALLBACK_NOTIFICATION_CONFIG["min_priority"]
        )
        self._min_priority_val = PRIORITY_LEVELS.get(self._min_priority, 2)
        self._slack_sources = slack_sources or {}
        self._transport = transport
        self._env = env

    def _format_message(self, todo: dict[str, object]) -> str:
        return NOTIFICATION_TEMPLATE.format(
            id=todo.get("id", "?"),
            title=todo.get("title", "untitled"),
            priority=str(todo.get("priority", "medium")),
            category=str(todo.get("category", "unknown")),
            agent_id=str(todo.get("agent_id", "unknown")),
            body=str(todo.get("body", "")),
        )

    def _priority_meets_threshold(self, priority: str) -> bool:
        return PRIORITY_LEVELS.get(priority, 0) >= self._min_priority_val

    def _dispatch_stdout(self, message: str) -> dict[str, object]:
        print(message)
        return {"ok": True, "backend": "stdout"}

    def _dispatch_slack(self, message: str, backend_config: dict[str, Any]) -> dict[str, object]:
        try:
            source_name = backend_config.get("source", "slack")
            source = self._slack_sources.get(source_name)
            if source is None:
                return {"ok": False, "backend": "slack", "error": f"slack source {source_name!r} not found"}
            result: dict[str, object] = source.send_notification(message)
            return result
        except Exception as exc:
            logger.warning("slack notification dispatch failed: %s", exc)
            return {"ok": False, "backend": "slack", "error": str(exc)}

    def _dispatch_webhook(self, message: str, backend_config: dict[str, Any]) -> dict[str, object]:
        url = backend_config.get("url")
        if not url:
            return {"ok": False, "backend": "webhook", "error": "webhook backend requires url"}
        headers = backend_config.get("headers", {})
        timeout = float(backend_config.get("timeout", 10))
        if self._transport is None:
            return {"ok": False, "backend": "webhook", "error": "no HTTP transport available"}
        try:
            resp = self._transport.post(
                url,
                headers={"Content-Type": "application/json", **headers},
                json={"text": message},
                timeout=timeout,
            )
            status = getattr(resp, "status_code", None)
            if status is not None and 200 <= status < 300:
                return {"ok": True, "backend": "webhook", "status_code": status}
            return {"ok": False, "backend": "webhook", "status_code": status, "error": f"HTTP {status}"}
        except Exception as exc:
            logger.warning("webhook notification dispatch failed: %s", exc)
            return {"ok": False, "backend": "webhook", "error": str(exc)}

    def dispatch(self, todo: dict[str, object]) -> dict[str, object]:
        if not self._enabled:
            return {"ok": False, "reason": "notifications disabled"}

        priority = str(todo.get("priority", "medium"))
        if not self._priority_meets_threshold(priority):
            return {"ok": False, "reason": f"priority {priority!r} below min_priority {self._min_priority!r}"}

        message = self._format_message(todo)

        results: dict[str, object] = {}
        for backend_name, backend_config in self._backends.items():
            handler = getattr(self, f"_dispatch_{backend_name}", None)
            if handler is None:
                logger.warning("unknown notification backend: %s", backend_name)
                results[backend_name] = {"ok": False, "error": f"unknown backend {backend_name!r}"}
                continue
            try:
                if backend_name == "stdout":
                    results[backend_name] = handler(message)
                else:
                    results[backend_name] = handler(message, backend_config)
            except Exception as exc:
                logger.warning("notification dispatch to %s failed: %s", backend_name, exc)
                results[backend_name] = {"ok": False, "backend": backend_name, "error": str(exc)}

        return {"ok": any(r.get("ok") for r in results.values() if isinstance(r, dict)), "results": results}

    def test(self) -> dict[str, object]:
        return self.dispatch(
            {
                "id": "test-1",
                "title": "Test notification",
                "body": "This is a test notification from gludd.",
                "category": "blocker",
                "priority": "urgent",
                "agent_id": "notification-dispatcher",
            }
        )
