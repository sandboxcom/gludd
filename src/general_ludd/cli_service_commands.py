"""CLI commands for service discovery and catalog operations.

`gludd service discover` — run a discovery pipeline via SearX.
`gludd service catalog` — print the current service catalog.
`gludd service show NAME` — show details for one service.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from argparse import _SubParsersAction
from typing import Any, cast

from general_ludd.connectors.searx import SearXConnector
from general_ludd.infra.service_catalog import ServiceCatalog
from general_ludd.service_discovery.pipeline import ServiceDiscoveryPipeline

logger = logging.getLogger(__name__)


def add_service_subparser(subparsers: _SubParsersAction[argparse.ArgumentParser]) -> None:
    service = subparsers.add_parser("service", help="Service discovery and catalog")
    svc_sub = service.add_subparsers(dest="service_command")

    discover = svc_sub.add_parser("discover", help="Run service discovery via SearX")
    discover.add_argument(
        "--searx-url",
        default=os.environ.get("GLUDD_SEARX_URL", "http://localhost:8888"),
    )
    discover.add_argument("--catalog-path", default=".gludd/service_catalog.yml")
    discover.add_argument("--term", action="append", dest="terms", default=None)

    catalog = svc_sub.add_parser("catalog", help="Print the current service catalog")
    catalog.add_argument("--path", default=".gludd/service_catalog.yml")

    show = svc_sub.add_parser("show", help="Show details for one service")
    show.add_argument("name", help="Service name")
    show.add_argument("--path", default=".gludd/service_catalog.yml")


def _cmd_discover(args: argparse.Namespace) -> int:
    try:
        SearXConnector({"base_url": args.searx_url})
    except Exception as exc:
        logger.error("Failed to create SearXConnector: %s", exc)
        return 1

    ServiceCatalog(args.catalog_path)
    pipeline = ServiceDiscoveryPipeline(
        searx_url=args.searx_url,
        catalog_path=args.catalog_path,
        search_terms=args.terms,
    )
    report = pipeline.run_discovery_pipeline()

    print("Discovery report")
    print(f"  New:       {len(report.new_services)}")
    print(f"  Retired:   {len(report.retired_services)}")
    print(f"  Changed:   {len(report.changed_services)}")
    print(f"  Total:     {report.total_discovered}")
    print(f"  Errors:    {len(report.errors)}")

    for svc in report.new_services:
        print(f"  NEW: {svc}")
    for err in report.errors:
        print(f"  ERROR: {err}")

    return 0


def _cmd_catalog(args: argparse.Namespace) -> int:
    catalog = ServiceCatalog(args.path)
    services = sorted(catalog.services.values(), key=lambda s: s.name)
    for svc in services:
        print(f"{svc.name:40s} {svc.status:10s} {svc.url}")
    print(f"\n{len(services)} services in catalog ({args.path})")
    return 0


def _cmd_show(args: argparse.Namespace) -> int:
    catalog = ServiceCatalog(args.path)
    svc = catalog.services.get(args.name)
    if svc is None:
        print(f"Service not found: {args.name}", file=sys.stderr)
        return 1
    print(f"Name:        {svc.name}")
    print(f"URL:         {svc.url}")
    print(f"Status:      {svc.status}")
    print(f"Description: {svc.description or 'N/A'}")
    print(f"API Docs:    {svc.api_docs_url or 'N/A'}")
    print(f"Pricing:     {svc.pricing_url or 'N/A'}")
    print(f"Source:      {svc.source_engine or 'N/A'}")
    print(f"Discovered:  {svc.discovered_at}")
    print(f"Last seen:   {svc.last_seen}")
    return 0


_SERVICE_DISPATCH: dict[str, Any] = {
    "discover": _cmd_discover,
    "catalog": _cmd_catalog,
    "show": _cmd_show,
}


def main() -> int:
    args = sys.argv[1:]
    handler = _SERVICE_DISPATCH.get(args[0] if args else "")
    if handler is None:
        return 1
    return cast(int, handler(args))


if __name__ == "__main__":
    sys.exit(main())
