"""Observability router — make the connector layer reachable over HTTP (#72/#73).

Exposes a small, least-privilege HTTP surface over a
:class:`general_ludd.connectors.registry.ConnectorRegistry`:

- ``GET  /api/observe/sources`` — list every operator-registered source
  (metadata only: ``{name, kind, family}`` + a ``by_kind`` grouping). NEVER
  returns secrets or env-var names.
- ``GET  /api/observe/health``  — ``health()`` across every source (never
  raises; an unreachable backend is reported, not an exception).
- ``POST /api/observe/query``   — run ``query(spec)`` on ONE source addressed by
  its registered ``name``. The request body is ``{source, spec}``; there is no
  ``url`` field, so a caller can never steer the daemon at an arbitrary host.

Security posture (least-privilege + SSRF-safe)
----------------------------------------------
- These paths are **PSK-gated, NOT public** — exactly like ``/api/facts`` (the
  daemon's auth middleware applies; the integration pass must NOT add any of
  these paths to ``_PUBLIC_PATHS``). The router registers no public bypass.
- Egress targets are **only** the sources an operator registered via config.
  ``POST /api/observe/query`` selects a connector by its registered NAME; an
  unknown name is a ``404`` (no fallthrough to a fetch). This is the SSRF
  firewall — there is no request-supplied URL anywhere in the surface.
- Inputs are validated by a pydantic model; ``spec`` is a bounded dict that is
  handed to the connector's own ``query`` (each connector re-applies its own
  literal-host SSRF guard to its CONFIGURED backend URL).

Daemon hookup (do NOT apply this wave — integration pass owns it)
-----------------------------------------------------------------
``wire_observability(app, daemon_state, config)`` builds the registry from an
operator config list and registers this router in ONE call. The hookup in
``src/general_ludd/daemon.py`` (inside ``create_daemon_app`` / ``register_*``,
alongside ``facts.register(app, _daemon_state)`` near line 1082) is exactly two
lines::

    from general_ludd.routers.observe import wire_observability   # line 1
    wire_observability(app, _daemon_state, _connector_config)     # line 2

where ``_connector_config`` is the operator's list of connector entries (e.g.
loaded from ``config/connectors.yml``); pass ``[]`` to register the router with
no sources. ``register_all`` in ``routers/__init__.py`` would gain the same two
lines if hookup is centralized there instead. No edit to ``daemon.py`` /
``daemon_wiring.py`` / ``routers/__init__.py`` is made in this wave.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import asdict
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field, StrictFloat, StrictInt, StrictStr, field_validator, model_validator

from general_ludd.connectors.registry import ConnectorRegistry
from general_ludd.observe.facade import GluddObserve
from general_ludd.pricing_intel.catalog import PricingCatalog
from general_ludd.routers._runtime import StrictRuntimeRequest
from general_ludd.security.capability_guard import RequireCapability

logger = logging.getLogger(__name__)


class ObserveQueryRequest(BaseModel):
    """Body for ``POST /api/observe/query``.

    ``source`` is the *registered name* of an operator-configured connector —
    never a URL. ``spec`` is the bounded query payload handed to that
    connector's own ``query(spec)``. Unknown extra fields (e.g. a smuggled
    ``url``) are ignored by pydantic's default and never reach the connector.
    """

    source: str = Field(min_length=1, max_length=256)
    spec: dict[str, object] = Field(default_factory=dict)


class ObserveFacadeRequest(StrictRuntimeRequest):
    """Bounded cross-source facade request over registered connectors only."""

    operation: Literal["query_sources", "timeline", "correlate_incident", "topology"]
    role: StrictStr = Field(default="observe", min_length=1, max_length=128)
    seed: dict[StrictStr, object] = Field(default_factory=dict)
    kinds: list[StrictStr] = Field(
        default_factory=lambda: ["logs", "traces", "metrics", "events"],
        min_length=1,
        max_length=32,
    )
    by: StrictStr = Field(default="trace_id", pattern=r"^[A-Za-z_][A-Za-z0-9_.-]{0,127}$")
    window_s: StrictFloat = Field(default=300.0, ge=0.0, le=86_400.0)
    spec: dict[StrictStr, object] = Field(default_factory=dict)
    start: StrictFloat | None = None
    end: StrictFloat | None = None
    timeout_seconds: StrictInt = Field(default=30, ge=1, le=120)

    @field_validator("seed", "spec")
    @classmethod
    def _bound_mapping(cls, value: dict[str, object]) -> dict[str, object]:
        if len(value) > 256 or len(json.dumps(value, default=str)) > 65_536:
            raise ValueError("observability mapping exceeds the bounded payload size")
        return value

    @field_validator("kinds")
    @classmethod
    def _bound_kinds(cls, value: list[str]) -> list[str]:
        if any(not item or len(item) > 128 for item in value):
            raise ValueError("connector kinds must be non-empty and bounded")
        return value

    @model_validator(mode="after")
    def _validate_operation(self) -> ObserveFacadeRequest:
        if self.operation == "correlate_incident" and not self.seed:
            raise ValueError("correlate_incident requires a non-empty seed")
        if self.start is not None and self.end is not None and self.start > self.end:
            raise ValueError("start must not exceed end")
        return self


def _json_topology(topology: dict[str, dict[str, set[str]]]) -> dict[str, dict[str, list[str]]]:
    return {
        side: {name: sorted(values) for name, values in sorted(adjacency.items())}
        for side, adjacency in topology.items()
    }


def _get_registry(app: FastAPI) -> ConnectorRegistry | None:
    reg = getattr(app.state, "_connector_registry", None)
    return reg if isinstance(reg, ConnectorRegistry) else None


def _get_pricing_catalog(app: FastAPI) -> PricingCatalog | None:
    """Return the daemon-shared :class:`PricingCatalog`, or ``None``.

    The daemon stores the same instance the SpendLimiter uses on
    ``app.state._pricing_catalog`` (see ``daemon.py``). When no catalog is
    present the pricing endpoints degrade to empty results rather than
    erroring — a daemon running without pricing intel is a valid, safe state.
    """
    cat = getattr(app.state, "_pricing_catalog", None)
    return cat if isinstance(cat, PricingCatalog) else None


def register(app: FastAPI, _daemon_state: dict[str, object]) -> None:
    """Register the observe routes on ``app``.

    Reads the live :class:`ConnectorRegistry` from ``app.state._connector_registry``
    at request time (so it works whether the registry was wired before or after
    this call). When no registry is present every endpoint degrades to an empty
    result rather than erroring — a daemon with no connectors configured is a
    valid, safe state.
    """

    @app.post(
        "/api/observe/facade",
        dependencies=[Depends(RequireCapability(resource="api:observe", action="query"))],
    )
    async def observe_facade(req: ObserveFacadeRequest) -> dict[str, object]:
        reg = _get_registry(app)
        sources = (
            {name: source for name in reg.names() if (source := reg.get(name)) is not None}
            if reg is not None
            else {}
        )

        def _run() -> dict[str, object]:
            facade = GluddObserve(sources)
            if req.operation == "query_sources":
                result: dict[str, object] = {
                    "records": facade.query_sources(
                        req.kinds,
                        req.spec,
                        start=req.start,
                        end=req.end,
                    )
                }
            elif req.operation == "timeline":
                result = {
                    "records": facade.timeline(
                        req.kinds,
                        spec=req.spec,
                        start=req.start,
                        end=req.end,
                    )
                }
            elif req.operation == "correlate_incident":
                result = {
                    "groups": facade.correlate_incident(
                        req.seed,
                        kinds=req.kinds,
                        by=req.by,
                        window_s=req.window_s,
                        spec=req.spec,
                    )
                }
            else:
                result = {
                    "topology": _json_topology(
                        facade.topology(kinds=req.kinds, spec=req.spec)
                    )
                }
            result["errors"] = list(facade.errors)
            result["role"] = req.role
            if len(json.dumps(result, default=str)) > 2_097_152:
                raise HTTPException(status_code=413, detail="observability result exceeds response limit")
            return result

        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(_run),
                timeout=float(req.timeout_seconds),
            )
        except TimeoutError as exc:
            raise HTTPException(status_code=504, detail="observability operation timed out") from exc
        return {"result": result}

    @app.get("/api/observe/sources")
    async def observe_sources() -> dict[str, object]:
        """List operator-registered sources (metadata only — no secrets)."""
        reg = _get_registry(app)
        if reg is None:
            return {"sources": [], "by_kind": {}, "count": 0}
        sources = reg.list_sources()
        return {
            "sources": sources,
            "by_kind": reg.by_kind(),
            "count": len(sources),
        }

    @app.get("/api/observe/health")
    async def observe_health() -> dict[str, object]:
        """Probe ``health()`` across every registered source (never raises)."""
        reg = _get_registry(app)
        if reg is None:
            return {"health": {}, "count": 0}
        # health_all() probes each connector's health() serially and blocks on
        # network I/O. Offload to a worker thread so the event loop stays free.
        health = await asyncio.to_thread(reg.health_all)
        return {"health": health, "count": len(health)}

    @app.post("/api/observe/query")
    async def observe_query(req: ObserveQueryRequest) -> dict[str, object]:
        """Run ``query(spec)`` on the NAMED, operator-registered source.

        ``404`` when the name is not a registered source — there is no
        request-supplied URL, so this cannot be steered at an arbitrary host.
        """
        reg = _get_registry(app)
        if reg is None or reg.get(req.source) is None:
            raise HTTPException(
                status_code=404,
                detail=f"no registered observability source named {req.source!r}",
            )
        # query() invokes the connector's synchronous query(spec), which blocks
        # on network I/O. Offload to a worker thread so the event loop is free.
        records = await asyncio.to_thread(reg.query, req.source, req.spec)
        return {
            "source": req.source,
            "records": records,
            "count": len(records),
        }

    @app.get("/api/pricing")
    async def pricing_models(provider: str | None = None) -> dict[str, object]:
        """Return all model token prices from the :class:`PricingCatalog`.

        PSK-gated by the daemon middleware (NOT in ``_PUBLIC_PATHS``), exactly
        like the other ``/api/`` routes. An optional ``?provider=`` slug
        forwards to ``catalog.all_model_prices(provider=...)``. Degrades to
        ``{"prices": [], "count": 0}`` when no catalog is wired.
        """
        cat = _get_pricing_catalog(app)
        if cat is None:
            return {"prices": [], "count": 0}
        prices = cat.all_model_prices(provider=provider)
        return {
            "prices": [asdict(p) for p in prices],
            "count": len(prices),
        }

    @app.get("/api/pricing/compute")
    async def pricing_compute(provider: str | None = None) -> dict[str, object]:
        """Return all compute instance prices from the :class:`PricingCatalog`.

        PSK-gated by the daemon middleware (NOT in ``_PUBLIC_PATHS``), exactly
        like the other ``/api/`` routes. An optional ``?provider=`` slug
        forwards to ``catalog.all_compute_prices(provider=...)``. Degrades to
        ``{"prices": [], "count": 0}`` when no catalog is wired.
        """
        cat = _get_pricing_catalog(app)
        if cat is None:
            return {"prices": [], "count": 0}
        prices = cat.all_compute_prices(provider=provider)
        return {
            "prices": [asdict(p) for p in prices],
            "count": len(prices),
        }

    @app.get("/api/pricing/catalog")
    async def pricing_catalog(provider: str | None = None) -> dict[str, object]:
        """Return the WHOLE live :class:`PricingCatalog` as one JSON facet.

        This is the consolidated view the daemon's computed catalog was missing:
        model token prices + compute instance prices + provider billing
        semantics + the registered provider slugs, all in a single response with
        a ``counts`` summary. The split ``/api/pricing`` and
        ``/api/pricing/compute`` routes remain for callers that only want one
        slice. An optional ``?provider=`` slug narrows the model + compute
        lists (billing/providers are always the full set — they are cheap,
        static facts).

        PSK-gated by the daemon middleware (NOT in ``_PUBLIC_PATHS``), exactly
        like the other ``/api/`` routes. Degrades to a fully-empty catalog
        (all lists empty, all counts 0) when no catalog is wired, rather than
        erroring — a daemon running without pricing intel is a valid state.
        """
        cat = _get_pricing_catalog(app)
        if cat is None:
            return {
                "model_prices": [],
                "compute_prices": [],
                "billing": [],
                "providers": [],
                "counts": {
                    "model_prices": 0,
                    "compute_prices": 0,
                    "billing": 0,
                    "providers": 0,
                },
            }
        model_prices = cat.all_model_prices(provider=provider)
        compute_prices = cat.all_compute_prices(provider=provider)
        billing = cat.all_billing()
        providers = cat.provider_slugs()
        return {
            "model_prices": [asdict(p) for p in model_prices],
            "compute_prices": [asdict(p) for p in compute_prices],
            "billing": [asdict(b) for b in billing],
            "providers": providers,
            "counts": {
                "model_prices": len(model_prices),
                "compute_prices": len(compute_prices),
                "billing": len(billing),
                "providers": len(providers),
            },
        }

    @app.get("/api/pricing/info")
    async def pricing_model_info(provider: str | None = None) -> dict[str, object]:
        """Return ModelInfo records combining pricing with model metadata.

        PSK-gated by the daemon middleware. Optional ``?provider=`` filter.
        Degrades to empty results when no catalog is wired.
        """
        cat = _get_pricing_catalog(app)
        if cat is None:
            return {"models": [], "count": 0}
        infos = cat.all_model_info(provider=provider)
        return {
            "models": [asdict(i) for i in infos],
            "count": len(infos),
        }


def wire_observability(
    app: FastAPI,
    daemon_state: dict[str, object],
    config: list[dict[str, object]] | None,
    *,
    factories: dict[str, object] | None = None,
) -> ConnectorRegistry:
    """Build the connector registry from ``config`` and register the router.

    This is the single daemon hookup point: it constructs a
    :class:`ConnectorRegistry` from the operator's connector config list, stores
    it on ``app.state._connector_registry`` (so ``/api/facts`` or other consumers
    can reach it), and registers the observe routes — all in one call.

    Parameters
    ----------
    app:
        The FastAPI app to register routes on.
    daemon_state:
        The daemon's shared state dict (passed through to :func:`register`).
    config:
        The operator's list of connector config entries (each a dict with
        ``name`` / ``kind`` / a selector + ``*_env`` secret NAMES). ``None`` or
        ``[]`` registers the router with no sources.
    factories:
        Optional explicit ``{key: class}`` map for entries that select a
        connector via a ``factory`` key (used by tests / pre-imported classes).

    Returns the built registry (also stored on ``app.state``).
    """
    # Tear down a previous registry's sources (e.g. an MqttSource subscriber
    # thread) before replacing it, so a reload doesn't leak background resources.
    _old = getattr(app.state, "_connector_registry", None)
    if _old is not None and hasattr(_old, "close"):
        _old.close()
    registry = ConnectorRegistry.from_config(config, factories=factories)
    app.state._connector_registry = registry
    register(app, daemon_state)
    if registry.errors():
        logger.warning(
            "wire_observability: %d connector config entr(ies) skipped: %s",
            len(registry.errors()),
            [e.get("name") for e in registry.errors()],
        )
    logger.info(
        "wire_observability: registered observe router with %d source(s)",
        len(registry.names()),
    )
    return registry
