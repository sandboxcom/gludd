#!/usr/bin/env python3
"""validate_scenarios — cross-reference scenarios against real-world usage patterns.

Usage:
    python validate_scenarios.py --scenarios-file <json> --output <json>
    python validate_scenarios.py --scenarios-file <json> --output <json> --mock
    python validate_scenarios.py --scenarios-file <json> --output <json> --daemon-url <url> --psk <key>

Reads scenario JSON, computes confidence scores per scenario using heuristic
keyword matching (mock mode) or a daemon ResearcherAgent call. Outputs a
validated_scenarios.json artifact.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

PATTERN_VALIDATION: dict[str, dict[str, float]] = {
    "crud_lifecycle": {
        "create": 0.85,
        "read": 0.90,
        "update": 0.80,
        "delete": 0.85,
        "api": 0.75,
    },
    "auth_flow": {
        "login": 0.90,
        "token": 0.85,
        "auth": 0.80,
        "session": 0.80,
        "oauth": 0.75,
    },
    "timeout_handling": {
        "timeout": 0.80,
        "retry": 0.85,
        "backoff": 0.75,
        "circuit": 0.70,
    },
    "concurrent_edits": {
        "lock": 0.80,
        "mutex": 0.75,
        "atomic": 0.70,
        "concurrent": 0.85,
        "transaction": 0.80,
    },
    "daemon_restart": {
        "init": 0.85,
        "startup": 0.85,
        "shutdown": 0.80,
        "restart": 0.90,
        "reload": 0.75,
    },
}


def _heuristic_confidence(scenario_name: str, scenario_desc: str) -> float:
    """Compute a heuristic confidence score from scenario keyword matching."""
    keywords = PATTERN_VALIDATION.get(scenario_name, {})
    if not keywords:
        return 0.4
    text = f"{scenario_name} {scenario_desc}".lower()
    scores = [weight for kw, weight in keywords.items() if kw in text]
    if not scores:
        return 0.3
    return round(sum(scores) / len(scores), 2)


def _extract_confidence_from_report(report: dict[str, Any]) -> float:
    """Extract a confidence score (0.0-1.0) from a ResearchReport dict.

    Uses the report's ``confidence_overall`` when findings are present,
    otherwise returns 0.0 so the caller falls back to heuristic scoring.
    """
    findings = report.get("findings", [])
    if not findings:
        return 0.0
    overall = report.get("confidence_overall", 0.0)
    if isinstance(overall, (int, float)) and overall > 0.0:
        return min(1.0, max(0.0, float(overall)))
    return 0.0


def _source_urls_from_report(report: dict[str, Any]) -> list[str]:
    """Collect source URLs from a ResearchReport's findings' citations."""
    urls: list[str] = []
    for finding in report.get("findings", []):
        for citation in finding.get("citations", []):
            url = citation.get("url", "")
            if url and url not in urls:
                urls.append(url)
    return urls


class DaemonResearchClient:
    """HTTP client for the daemon POST /api/research/validate endpoint."""

    def __init__(
        self,
        daemon_url: str = "http://localhost:8000",
        psk: str = "",
        timeout: float = 30.0,
    ) -> None:
        self._base = daemon_url.rstrip("/")
        self._psk = psk
        self._timeout = timeout

    def _headers(self) -> dict[str, str]:
        h: dict[str, str] = {"Content-Type": "application/json"}
        if self._psk:
            h["X-PSK"] = self._psk
        return h

    def validate_queries(
        self,
        queries: list[str],
        *,
        categories: list[str] | None = None,
        time_range: str = "year",
        max_results: int = 10,
    ) -> dict[str, Any]:
        """POST /api/research/validate and return the JSON response body.

        On any error (network, timeout, non-200) returns an empty result dict
        so callers always get a valid dict to introspect.
        """
        import urllib.error
        import urllib.request

        selected_categories = categories or ["general", "it"]
        payload = json.dumps({
            "queries": queries,
            "categories": selected_categories,
            "time_range": time_range,
            "max_results": max_results,
        }).encode("utf-8")

        url = f"{self._base}/api/research/validate"
        req = urllib.request.Request(
            url,
            data=payload,
            headers=self._headers(),
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                if resp.status != 200:
                    logger.warning(
                        "daemon returned %d, falling back to heuristic",
                        resp.status,
                    )
                    return {"reports": [], "query_count": 0, "findings_count": 0, "searx_available": False}
                body = resp.read().decode("utf-8")
                decoded: object = json.loads(body)
                if not isinstance(decoded, dict):
                    raise ValueError("daemon response must be a JSON object")
                return {str(key): value for key, value in decoded.items()}
        except urllib.error.URLError as e:
            logger.warning("daemon unreachable (%s), falling back to heuristic", e)
            return {"reports": [], "query_count": 0, "findings_count": 0, "searx_available": False}
        except Exception:
            logger.exception("unexpected error calling daemon")
            return {"reports": [], "query_count": 0, "findings_count": 0, "searx_available": False}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate E2E test scenarios against real-world usage patterns"
    )
    parser.add_argument("--scenarios-file", required=True, help="Path to scenarios.json")
    parser.add_argument("--output", required=True, help="Path for validated_scenarios.json")
    parser.add_argument("--confidence-threshold", type=float, default=0.4, help="Minimum confidence to keep a scenario")
    parser.add_argument("--mock", action="store_true", default=False, help="Use heuristic scoring (mock mode)")
    parser.add_argument("--daemon-url", default="http://localhost:8000", help="Daemon URL for live research")
    parser.add_argument(
        "--psk",
        default=os.environ.get("GLUDD_DAEMON_PSK", ""),
        help="Pre-shared key for daemon auth (defaults to GLUDD_DAEMON_PSK)",
    )
    parser.add_argument("--research-categories", default="general,it", help="Comma-separated research categories")
    parser.add_argument("--research-time-range", default="year", help="Time range filter")
    parser.add_argument("--max-results", type=int, default=10, help="Max search results per query")

    args = parser.parse_args()

    with open(args.scenarios_file) as f:
        data = json.load(f)

    scenarios = data.get("scenarios", [])
    valid: list[dict[str, Any]] = []
    discarded: list[dict[str, Any]] = []
    queries: list[str] = []

    for scenario in scenarios:
        name = scenario.get("name", "unknown")
        desc = scenario.get("description", "")
        targets = " ".join(scenario.get("coverage_targets", []))

        q = f'how is "{name}" tested in production e2e test patterns {targets}'
        queries.append(q)

    if not args.mock:
        categories = [c.strip() for c in args.research_categories.split(",") if c.strip()]
        client = DaemonResearchClient(
            daemon_url=args.daemon_url,
            psk=args.psk,
        )
        result = client.validate_queries(
            queries,
            categories=categories,
            time_range=args.research_time_range,
            max_results=args.max_results,
        )
        reports = result.get("reports", [])
    else:
        reports = []

    for idx, scenario in enumerate(scenarios):
        name = scenario.get("name", "unknown")
        desc = scenario.get("description", "")

        conf = _heuristic_confidence(name, desc)
        source_urls: list[str] = []

        if not args.mock and idx < len(reports):
            report = reports[idx]
            research_conf = _extract_confidence_from_report(report)
            if research_conf > 0.0:
                conf = research_conf
            source_urls = _source_urls_from_report(report)

        entry = dict(scenario)
        entry["confidence"] = conf
        entry["source_urls"] = source_urls

        if conf >= args.confidence_threshold:
            valid.append(entry)
        else:
            discarded.append({
                "name": name,
                "confidence": conf,
                "reason": f"confidence {conf} below threshold {args.confidence_threshold}",
            })

    output = {
        "module": data.get("module", "unknown"),
        "path": data.get("path", ""),
        "valid": valid,
        "discarded": discarded,
        "valid_count": len(valid),
        "discarded_count": len(discarded),
        "research_queries": queries,
        "confidence_threshold": args.confidence_threshold,
        "status": "completed",
    }

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2)

    print(json.dumps({"valid_count": len(valid), "discarded_count": len(discarded), "output": str(out_path)}))


if __name__ == "__main__":
    main()
