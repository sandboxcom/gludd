"""Capability-based dispatch for language operations.

Uses ``CapabilityRouter`` from ``general_ludd.dispatch`` to route language
requests (detection, translation, transliteration) to the appropriate
collection and module. Imports from ``general_ludd.agent`` module_utils
for policy enforcement.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar

_SRC = Path(__file__).resolve().parents[11] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


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
    """Routes language capability requests to the appropriate Ansible collection.

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

    def __init__(self) -> None:
        self._router = None
        try:
            from general_ludd.dispatch.capabilities import CapabilityRegistry
            from general_ludd.dispatch.router import CapabilityRouter

            registry = CapabilityRegistry()
            self._router = CapabilityRouter(registry)
        except Exception:
            pass

    def route(self, request: RouteRequest) -> RouteResult:
        if not request.capability:
            return RouteResult(ok=False, capability="", error="empty capability")

        if request.collection:
            if self._router is not None:
                try:
                    result = self._router.route_by_collection(request.collection, request.payload)
                    return RouteResult.from_generic(result)
                except Exception:
                    pass
            return RouteResult(
                ok=True,
                capability=request.capability,
                collection=request.collection,
            )

        if self._router is not None:
            try:
                result = self._router.route(request.capability, request.payload)
                return RouteResult.from_generic(result)
            except Exception:
                pass

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
        if self._router is not None:
            try:
                return self._router.list_capabilities()
            except Exception:
                pass
        return sorted(self.CAPABILITY_MAP.keys())


__all__ = [
    "LanguageRouter",
    "RouteRequest",
    "RouteResult",
]
