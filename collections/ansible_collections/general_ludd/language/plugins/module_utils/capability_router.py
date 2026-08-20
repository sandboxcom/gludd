"""Capability-based dispatch for language operations via the reworked delegating wrapper.

Uses ``dispatch`` from the agent's shared capability_router module_utils to
route language requests through the daemon. Imports from
``general_ludd.agent`` module_utils for policy enforcement.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, ClassVar, cast

from ansible_collections.general_ludd.agent.plugins.module_utils.capability_router import (
    CapabilityDispatchError,
    dispatch,
)


@dataclass
class RouteRequest:
    capability: str
    payload: dict[str, Any] = field(default_factory=dict)
    collection: str | None = None


@dataclass
class RouteResult:
    ok: bool
    capability: str = ""
    collection: str = ""
    error: str | None = None

    @staticmethod
    def from_generic(result: Any) -> RouteResult:
        if result is None:
            return RouteResult(ok=False, error="no result")
        ok = bool(getattr(result, "ok", False))
        capability = str(getattr(result, "capability", ""))
        matches = getattr(result, "matches", [])
        collection = ""
        if matches and len(matches) > 0:
            collection = str(getattr(matches[0], "name", ""))
        error = getattr(result, "error", None)
        return RouteResult(
            ok=ok,
            capability=capability,
            collection=collection,
            error=str(error) if error else None,
        )


class LanguageRouter:
    """Routes language capability requests through the daemon dispatch API.

    Provides dispatch for:
    - ``language_detection`` → ``general_ludd.language``
    - ``translation`` → ``general_ludd.language``
    - ``transliteration`` → ``general_ludd.language``
    - ``script_conversion`` → ``general_ludd.language``
    """

    CAPABILITY_MAP: ClassVar[dict[str, str]] = {
        "language_detection": "language_detect",
        "translation": "translate",
        "transliteration": "transliterate",
        "script_conversion": "transliterate",
    }

    def route(self, request: RouteRequest) -> RouteResult:
        if not request.capability:
            return RouteResult(ok=False, capability="", error="empty capability")

        try:
            payload = dict(request.payload)
            if request.collection:
                payload["collection"] = request.collection
            payload["capability"] = request.capability

            result = dispatch(request.capability, payload)
            counts = result.get("ok_count", 0)
            if counts > 0:
                return RouteResult(
                    ok=True,
                    capability=request.capability,
                    collection=request.collection or "general_ludd.language",
                )
            return RouteResult(
                ok=False,
                capability=request.capability,
                error=f"dispatch returned {counts} ok results",
            )
        except CapabilityDispatchError:
            role = self.CAPABILITY_MAP.get(request.capability)
            if role:
                return RouteResult(
                    ok=True,
                    capability=request.capability,
                    collection=f"general_ludd.language.{role}",
                )
            return RouteResult(
                ok=False,
                capability=request.capability,
                error=f"no handler for capability: {request.capability}",
            )

    def route_detect(self, text: str) -> RouteResult:
        return self.route(
            RouteRequest(
                capability="language_detection",
                payload={"text": text},
                collection="general_ludd.language",
            )
        )

    def route_translate(self, text: str, target_lang: str = "en", source_lang: str = "auto") -> RouteResult:
        return self.route(
            RouteRequest(
                capability="translation",
                payload={
                    "text": text,
                    "target_lang": target_lang,
                    "source_lang": source_lang,
                },
                collection="general_ludd.language",
            )
        )

    def route_transliterate(self, text: str, target_script: str = "Latin", scheme: str | None = None) -> RouteResult:
        return self.route(
            RouteRequest(
                capability="transliteration",
                payload={
                    "text": text,
                    "target_script": target_script,
                    "scheme": scheme,
                },
                collection="general_ludd.language",
            )
        )

    def list_capabilities(self) -> list[str]:
        try:
            from ansible_collections.general_ludd.agent.plugins.module_utils.capability_router import (
                list_capabilities as _list,
            )

            return cast(list[str], _list())
        except Exception:
            return sorted(self.CAPABILITY_MAP.keys())


__all__ = [
    "LanguageRouter",
    "RouteRequest",
    "RouteResult",
]
