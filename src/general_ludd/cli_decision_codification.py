"""Bounded operator CLI for proposal-only decision-log analysis.

The command intentionally exposes only the authenticated analysis endpoint. It
does not accept approval, activation, lifecycle, evidence, artifact, or key
inputs, and it validates the daemon response before printing a safe projection.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import NoReturn, cast

import httpx
from pydantic import ValidationError

from general_ludd.replay.schema import ReplaySchemaError, decode_replay_json_object
from general_ludd.routers.decision_codification import (
    DecisionAnalysisRequest,
    DecisionAnalysisResponse,
)

MAX_ANALYSIS_RESPONSE_BYTES = 128 * 1024
_ANALYSIS_PATH = "/api/v1/decision-codification/analyze"
_REQUEST_TIMEOUT_SECONDS = 30.0


class _AnalysisCLIError(Exception):
    """Base class for errors with fixed, content-free CLI diagnostics."""


class _AnalysisHTTPStatusError(_AnalysisCLIError):
    """A bounded non-success HTTP status returned by the daemon."""

    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        super().__init__()


class _AnalysisResponseTooLarge(_AnalysisCLIError):
    """The daemon response exceeded the local output safety bound."""


class _InvalidAnalysisResponse(_AnalysisCLIError):
    """The daemon response did not match the digest-only wire contract."""


class _AnalysisUnavailable(_AnalysisCLIError):
    """The daemon request could not be completed safely."""


def _exit(message: str, *, code: int) -> NoReturn:
    print(f"Error: {message}", file=sys.stderr)
    raise SystemExit(code)


def _request_payload(args: argparse.Namespace) -> dict[str, object]:
    """Return the exact bounded request schema or raise ``ValidationError``."""
    request = DecisionAnalysisRequest.model_validate(
        {
            "project_id": args.project,
            "run_ids": args.run_ids,
            "training_recipe_digest": args.training_recipe_digest,
            "dependency_lock_digest": args.dependency_lock_digest,
            "created_at": args.created_at,
            "expires_at": args.expires_at,
            "maximum_use_count": args.maximum_use_count,
            "estimated_tokens_per_call": args.estimated_tokens_per_call,
        },
        strict=True,
    )
    return cast(dict[str, object], request.model_dump(mode="json"))


def _headers(project_id: str) -> dict[str, str]:
    """Build JSON headers, binding configured daemon auth to the exact project."""
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    psk = os.environ.get("GLUDD_AUTH_PSK", "").strip()
    if psk:
        headers["Authorization"] = f"Bearer {project_id}:{psk}"
    return headers


def _read_response(response: httpx.Response) -> bytes:
    """Read a response incrementally while enforcing a strict byte bound."""
    declared_length = response.headers.get("content-length")
    if declared_length is not None:
        try:
            parsed_length = int(declared_length)
        except ValueError:
            raise _InvalidAnalysisResponse from None
        if parsed_length < 0:
            raise _InvalidAnalysisResponse
        if parsed_length > MAX_ANALYSIS_RESPONSE_BYTES:
            raise _AnalysisResponseTooLarge

    chunks: list[bytes] = []
    received = 0
    for chunk in response.iter_bytes():
        received += len(chunk)
        if received > MAX_ANALYSIS_RESPONSE_BYTES:
            raise _AnalysisResponseTooLarge
        chunks.append(chunk)
    return b"".join(chunks)


def _validate_response(body: bytes) -> DecisionAnalysisResponse:
    """Validate the response as the strict digest/count/enum projection."""
    try:
        decode_replay_json_object(body)
        return DecisionAnalysisResponse.model_validate_json(body, strict=True)
    except (ReplaySchemaError, TypeError, ValueError, ValidationError):
        raise _InvalidAnalysisResponse from None


def _post_analysis(
    payload: dict[str, object],
    *,
    daemon_url: str,
) -> DecisionAnalysisResponse:
    """POST one bounded request through the maintained httpx client."""
    endpoint = daemon_url.rstrip("/") + _ANALYSIS_PATH
    project_id = payload["project_id"]
    if not isinstance(project_id, str):
        raise _AnalysisUnavailable

    try:
        with httpx.Client(
            timeout=httpx.Timeout(_REQUEST_TIMEOUT_SECONDS),
            follow_redirects=False,
            trust_env=False,
        ) as client, client.stream(
            "POST",
            endpoint,
            headers=_headers(project_id),
            json=payload,
        ) as response:
            status_code = response.status_code
            if type(status_code) is not int or not 100 <= status_code <= 599:
                raise _InvalidAnalysisResponse
            if not 200 <= status_code < 300:
                raise _AnalysisHTTPStatusError(status_code)
            body = _read_response(response)
    except _AnalysisCLIError:
        raise
    except Exception:
        raise _AnalysisUnavailable from None

    return _validate_response(body)


def _cmd_analyze(args: argparse.Namespace) -> None:
    """Analyze verified run bundles without exposing evidence or lifecycle actions."""
    try:
        payload = _request_payload(args)
    except (AttributeError, TypeError, ValueError, ValidationError):
        _exit("invalid decision analysis request", code=2)

    try:
        response = _post_analysis(payload, daemon_url=args.daemon_url)
    except _AnalysisHTTPStatusError as exc:
        _exit(f"decision analysis request failed (HTTP {exc.status_code})", code=1)
    except _AnalysisResponseTooLarge:
        _exit("decision analysis response too large", code=1)
    except _InvalidAnalysisResponse:
        _exit("invalid decision analysis response", code=1)
    except _AnalysisUnavailable:
        _exit("decision analysis request unavailable", code=1)

    safe_output = response.model_dump(mode="json")
    print(json.dumps(safe_output, sort_keys=True, separators=(",", ":")))


def add_decision_codification_subparser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    """Register the proposal-only ``decision-codification analyze`` command."""
    parser = subparsers.add_parser(
        "decision-codification",
        help="Analyze verified decision logs into digest-only candidate summaries.",
    )
    parser.set_defaults(func=None)
    commands = parser.add_subparsers(dest="decision_codification_command")

    analyze = commands.add_parser(
        "analyze",
        help="Request bounded proposal analysis; does not approve or activate candidates.",
    )
    analyze.add_argument("--project", required=True, help="Exact project identifier")
    analyze.add_argument(
        "--run-id",
        dest="run_ids",
        action="append",
        required=True,
        help="Verified run ID (repeat 1-256 times)",
    )
    analyze.add_argument("--training-recipe-digest", required=True, help="sha256: digest")
    analyze.add_argument("--dependency-lock-digest", required=True, help="sha256: digest")
    analyze.add_argument("--created-at", required=True, help="Timezone-aware ISO-8601 timestamp")
    analyze.add_argument("--expires-at", required=True, help="Timezone-aware ISO-8601 timestamp")
    analyze.add_argument(
        "--maximum-use-count",
        required=True,
        type=int,
        help="Candidate use bound (1-1000000)",
    )
    analyze.add_argument(
        "--estimated-tokens-per-call",
        type=int,
        default=0,
        help="Estimated token bound per call (0-10000000)",
    )
    analyze.add_argument("--daemon-url", default="http://localhost:8000")
    analyze.set_defaults(func=_cmd_analyze)


__all__ = [
    "MAX_ANALYSIS_RESPONSE_BYTES",
    "add_decision_codification_subparser",
]
