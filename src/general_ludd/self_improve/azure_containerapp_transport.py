"""Bounded HTTP transport and response validation for one Container App model."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from typing import Final, Protocol, cast

import httpx

from general_ludd.self_improve.azure_backend import AzureCandidateResponse
from general_ludd.self_improve.azure_containerapp_transport_types import (
    ContainerAppBackendAccounting,
    ContainerAppBackendTrace,
    ContainerAppResponseFailure,
    ContainerAppTraceEvent,
)
from general_ludd.self_improve.model_candidates import (
    AzureContainerAppCandidateIdentity,
    BackendFailure,
    BackendInfrastructureError,
    BackendPolicyError,
    BackendPolicyFailure,
)

MAX_DISCOVERY_BYTES: Final = 1_048_576
MAX_RESPONSE_BYTES: Final = 16_777_216
MAX_PROVIDER_TOKENS: Final = 100_000_000
_MAX_PROVIDER_ERROR_BYTES: Final = 16_384
_VLLM_ERROR_FIELDS: Final = frozenset(
    {"object", "message", "type", "param", "code"}
)
_VLLM_ERROR_PARAMETERS: Final = frozenset(
    {None, "input_tokens", "max_completion_tokens", "max_tokens"}
)


class HTTPClient(Protocol):
    """Minimum closed HTTP surface owned by the backend."""

    def get(
        self,
        path: str,
        *,
        timeout: float,
        follow_redirects: bool,
    ) -> object:
        """Issue one bounded GET request without redirects."""
        ...

    def post(
        self,
        path: str,
        *,
        json: dict[str, object],
        timeout: float,
        follow_redirects: bool,
    ) -> object:
        """Issue one bounded JSON POST request without redirects."""
        ...

    def close(self) -> None:
        """Close resources owned by this HTTP client."""
        ...


class _HTTPResponse(Protocol):
    status_code: int
    headers: Mapping[str, str]
    content: bytes


class _DuplicateJSONField(ValueError):
    pass


class _IdentityDrift(ValueError):
    pass


class ContainerAppTransportResponseError(BackendInfrastructureError):
    """Censored transport error carrying only fixed response diagnostics."""

    def __init__(
        self,
        failure: BackendFailure,
        response_failure: ContainerAppResponseFailure,
        *,
        http_status: int = 0,
    ) -> None:
        """Record only fixed failure classes and a bounded HTTP status."""
        super().__init__(failure)
        self.response_failure = response_failure
        self.http_status = http_status


def _strict_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJSONField
        result[key] = value
    return result


def discard_trace(_trace: ContainerAppBackendTrace) -> None:
    """Accept one trace when callers intentionally disable persistence."""
    return None


def emit_trace(
    trace_sink: Callable[[ContainerAppBackendTrace], None],
    trace: ContainerAppBackendTrace,
) -> None:
    """Emit one trace while censoring sink-specific failure details."""
    try:
        trace_sink(trace)
    except Exception:
        raise BackendInfrastructureError(BackendFailure.INTERNAL) from None


def emit_failure(
    trace_sink: Callable[[ContainerAppBackendTrace], None],
    trace: ContainerAppBackendTrace,
) -> None:
    """Best-effort terminal trace that cannot mask the provider failure."""
    try:
        emit_trace(trace_sink, trace)
    except BackendInfrastructureError:
        return


_STATUS_FAILURES: Final[dict[int, BackendFailure]] = {
    401: BackendFailure.AUTHENTICATION,
    403: BackendFailure.AUTHORIZATION,
    404: BackendFailure.NOT_FOUND,
    408: BackendFailure.TIMEOUT,
    409: BackendFailure.UNAVAILABLE,
    429: BackendFailure.RATE_LIMITED,
}


def _failure_for_exception(error: BaseException) -> BackendFailure:
    if isinstance(error, BackendInfrastructureError):
        return error.failure
    if isinstance(error, (TimeoutError, httpx.TimeoutException)):
        return BackendFailure.TIMEOUT
    if isinstance(error, httpx.HTTPError):
        return BackendFailure.TRANSPORT
    return BackendFailure.INTERNAL


def _status_failure(status_code: object) -> BackendFailure | None:
    if isinstance(status_code, bool) or not isinstance(status_code, int):
        return BackendFailure.INVALID_RESPONSE
    if status_code == 200:
        return None
    if status_code in _STATUS_FAILURES:
        return _STATUS_FAILURES[status_code]
    if status_code >= 500:
        return BackendFailure.UNAVAILABLE
    return BackendFailure.INVALID_RESPONSE


def _vllm_error_mapping(payload: object) -> Mapping[str, object] | None:
    """Return only the exact bounded vLLM error envelope shape."""
    if not isinstance(payload, Mapping):
        return None
    candidate: object = payload
    if set(payload) == {"error"}:
        candidate = payload.get("error")
    if not isinstance(candidate, Mapping):
        return None
    keys = set(candidate)
    if (
        not {"message", "type", "code"}.issubset(keys)
        or not keys.issubset(_VLLM_ERROR_FIELDS)
        or candidate.get("object") not in {None, "error"}
        or candidate.get("type") != "BadRequestError"
        or candidate.get("code") != 400
        or candidate.get("param") not in _VLLM_ERROR_PARAMETERS
    ):
        return None
    return candidate


def _response_failure_for_status(
    response: _HTTPResponse,
    status_code: int,
    *,
    maximum_bytes: int,
) -> ContainerAppResponseFailure:
    """Classify a recognized error without retaining provider-controlled text."""
    if status_code != 400:
        return ContainerAppResponseFailure.HTTP_STATUS
    try:
        content_type = response.headers.get("content-type", "")
        raw = response.content
        if (
            not isinstance(content_type, str)
            or content_type.partition(";")[0].strip().casefold()
            != "application/json"
            or not isinstance(raw, bytes)
            or len(raw) > min(maximum_bytes, _MAX_PROVIDER_ERROR_BYTES)
        ):
            return ContainerAppResponseFailure.HTTP_STATUS
        payload = json.loads(raw.decode("utf-8"), object_pairs_hook=_strict_object)
        error = _vllm_error_mapping(payload)
        if error is None:
            return ContainerAppResponseFailure.HTTP_STATUS
        message = error.get("message")
        if not isinstance(message, str):
            return ContainerAppResponseFailure.HTTP_STATUS
        normalized = message.casefold()
        if (
            "maximum context length" in normalized
            and "tokens" in normalized
            and (
                "requested" in normalized
                or "request has" in normalized
                or "request contains" in normalized
                or error.get("param")
                in {"input_tokens", "max_completion_tokens", "max_tokens"}
            )
        ):
            return ContainerAppResponseFailure.CONTEXT_WINDOW_EXCEEDED
        if "chat template" in normalized and any(
            marker in normalized
            for marker in ("does not define", "not defined", "provide a chat template")
        ):
            return ContainerAppResponseFailure.CHAT_TEMPLATE_UNAVAILABLE
        return ContainerAppResponseFailure.PROVIDER_BAD_REQUEST
    except Exception:
        return ContainerAppResponseFailure.HTTP_STATUS
    return ContainerAppResponseFailure.HTTP_STATUS


def response_json(response: object, *, maximum_bytes: int) -> object:
    """Decode one bounded, duplicate-free JSON response."""
    try:
        typed_response = cast(_HTTPResponse, response)
        status_code = typed_response.status_code
        failure = _status_failure(status_code)
        if failure is not None:
            response_failure = (
                _response_failure_for_status(
                    typed_response,
                    status_code,
                    maximum_bytes=maximum_bytes,
                )
                if isinstance(status_code, int) and not isinstance(status_code, bool)
                else ContainerAppResponseFailure.HTTP_STATUS
            )
            raise ContainerAppTransportResponseError(
                failure,
                response_failure,
                http_status=(
                    status_code
                    if isinstance(status_code, int) and not isinstance(status_code, bool)
                    else 0
                ),
            )
        content_type = typed_response.headers.get("content-type", "")
        if not isinstance(content_type, str):
            raise ContainerAppTransportResponseError(
                BackendFailure.INVALID_RESPONSE,
                ContainerAppResponseFailure.CONTENT_TYPE,
            )
        if content_type.partition(";")[0].strip().casefold() != "application/json":
            raise ContainerAppTransportResponseError(
                BackendFailure.INVALID_RESPONSE,
                ContainerAppResponseFailure.CONTENT_TYPE,
            )
        raw = typed_response.content
        if not isinstance(raw, bytes) or len(raw) > maximum_bytes:
            raise ContainerAppTransportResponseError(
                BackendFailure.INVALID_RESPONSE,
                ContainerAppResponseFailure.RESPONSE_BODY,
            )
        try:
            return json.loads(raw.decode("utf-8"), object_pairs_hook=_strict_object)
        except Exception:
            raise ContainerAppTransportResponseError(
                BackendFailure.INVALID_RESPONSE,
                ContainerAppResponseFailure.JSON_BODY,
            ) from None
    except BackendInfrastructureError:
        raise
    except Exception:
        raise ContainerAppTransportResponseError(
            BackendFailure.INVALID_RESPONSE,
            ContainerAppResponseFailure.RESPONSE_BODY,
        ) from None


def request_json(
    operation: Callable[..., object],
    path: str,
    *,
    timeout_seconds: float,
    maximum_bytes: int,
    payload: dict[str, object] | None = None,
) -> object:
    """Invoke one non-redirecting HTTP operation and decode its JSON."""
    try:
        response = (
            operation(path, timeout=timeout_seconds, follow_redirects=False)
            if payload is None
            else operation(
                path,
                json=payload,
                timeout=timeout_seconds,
                follow_redirects=False,
            )
        )
        return response_json(response, maximum_bytes=maximum_bytes)
    except BackendInfrastructureError:
        raise
    except Exception as error:
        raise BackendInfrastructureError(_failure_for_exception(error)) from None


def response_text(
    response: object,
    *,
    maximum_bytes: int,
    content_types: frozenset[str],
) -> str:
    """Decode one bounded UTF-8 response without retaining provider text."""
    try:
        typed_response = cast(_HTTPResponse, response)
        status_code = typed_response.status_code
        failure = _status_failure(status_code)
        if failure is not None:
            raise ContainerAppTransportResponseError(
                failure,
                ContainerAppResponseFailure.HTTP_STATUS,
                http_status=(
                    status_code
                    if isinstance(status_code, int) and not isinstance(status_code, bool)
                    else 0
                ),
            )
        content_type = typed_response.headers.get("content-type", "")
        if (
            not isinstance(content_type, str)
            or content_type.partition(";")[0].strip().casefold() not in content_types
        ):
            raise ContainerAppTransportResponseError(
                BackendFailure.INVALID_RESPONSE,
                ContainerAppResponseFailure.CONTENT_TYPE,
            )
        raw = typed_response.content
        if not isinstance(raw, bytes) or len(raw) > maximum_bytes:
            raise ContainerAppTransportResponseError(
                BackendFailure.INVALID_RESPONSE,
                ContainerAppResponseFailure.RESPONSE_BODY,
            )
        return raw.decode("utf-8")
    except BackendInfrastructureError:
        raise
    except Exception:
        raise ContainerAppTransportResponseError(
            BackendFailure.INVALID_RESPONSE,
            ContainerAppResponseFailure.RESPONSE_BODY,
        ) from None


def request_text(
    operation: Callable[..., object],
    path: str,
    *,
    timeout_seconds: float,
    maximum_bytes: int,
    content_types: frozenset[str],
) -> str:
    """Invoke one non-redirecting HTTP operation and decode bounded text."""
    try:
        response = operation(
            path,
            timeout=timeout_seconds,
            follow_redirects=False,
        )
        return response_text(
            response,
            maximum_bytes=maximum_bytes,
            content_types=content_types,
        )
    except BackendInfrastructureError:
        raise
    except Exception as error:
        raise BackendInfrastructureError(_failure_for_exception(error)) from None


def _validate_model_inventory(
    payload: object,
    identity: AzureContainerAppCandidateIdentity,
) -> None:
    if not isinstance(payload, Mapping) or payload.get("object") != "list":
        raise _IdentityDrift
    values = payload.get("data")
    if (
        not isinstance(values, Sequence)
        or isinstance(values, (str, bytes))
        or len(values) != 1
    ):
        raise _IdentityDrift
    model = values[0]
    if (
        not isinstance(model, Mapping)
        or model.get("object") != "model"
        or model.get("id") != identity.model_name
    ):
        raise _IdentityDrift


def probe_model(
    client: HTTPClient,
    identity: AzureContainerAppCandidateIdentity,
    timeout_seconds: float,
    trace_sink: Callable[[ContainerAppBackendTrace], None],
    *,
    initial: bool,
) -> None:
    """Verify the endpoint still serves exactly the bound model identity."""
    known_digest = None if initial else identity.identity_digest
    emit_trace(
        trace_sink,
        ContainerAppBackendTrace(
            ContainerAppTraceEvent.DISCOVERY_STARTED,
            candidate_digest=known_digest,
        ),
    )
    try:
        payload = request_json(
            client.get,
            "/v1/models",
            timeout_seconds=timeout_seconds,
            maximum_bytes=MAX_DISCOVERY_BYTES,
        )
        _validate_model_inventory(payload, identity)
    except _IdentityDrift:
        if initial:
            failure = BackendFailure.INVALID_RESPONSE
            emit_failure(
                trace_sink,
                ContainerAppBackendTrace(
                    ContainerAppTraceEvent.DISCOVERY_FAILED,
                    failure=failure,
                ),
            )
            raise BackendInfrastructureError(failure) from None
        emit_trace(
            trace_sink,
            ContainerAppBackendTrace(
                ContainerAppTraceEvent.IDENTITY_DRIFT,
                candidate_digest=identity.identity_digest,
            ),
        )
        raise BackendPolicyError(BackendPolicyFailure.IDENTITY_DRIFT) from None
    except BackendInfrastructureError as error:
        emit_failure(
            trace_sink,
            ContainerAppBackendTrace(
                ContainerAppTraceEvent.DISCOVERY_FAILED,
                candidate_digest=known_digest,
                failure=error.failure,
            ),
        )
        raise
    emit_trace(
        trace_sink,
        ContainerAppBackendTrace(
            ContainerAppTraceEvent.DISCOVERY_SUCCEEDED,
            candidate_digest=identity.identity_digest,
        ),
    )


def _token_count(value: object) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 0 <= value <= MAX_PROVIDER_TOKENS
    ):
        raise ValueError
    return value


def validated_chat_response(
    payload: object,
    identity: AzureContainerAppCandidateIdentity,
    *,
    max_output_tokens: int,
) -> AzureCandidateResponse:
    """Validate one exact OpenAI-compatible non-streaming response."""
    if (
        not isinstance(payload, Mapping)
        or payload.get("object") != "chat.completion"
        or payload.get("model") != identity.model_name
    ):
        raise ValueError
    choices = payload.get("choices")
    if not isinstance(choices, list) or len(choices) != 1:
        raise ValueError
    choice = choices[0]
    if not isinstance(choice, Mapping) or choice.get("index") != 0:
        raise ValueError
    if choice.get("finish_reason") not in {"stop", "length"}:
        raise ValueError
    message = choice.get("message")
    if not isinstance(message, Mapping) or message.get("role") != "assistant":
        raise ValueError
    text = message.get("content")
    if (
        not isinstance(text, str)
        or not text
        or len(text.encode("utf-8")) > MAX_RESPONSE_BYTES
    ):
        raise ValueError
    usage = payload.get("usage")
    if not isinstance(usage, Mapping):
        raise ValueError
    input_tokens = _token_count(usage.get("prompt_tokens"))
    output_tokens = _token_count(usage.get("completion_tokens"))
    total_tokens = _token_count(usage.get("total_tokens"))
    if output_tokens > max_output_tokens or input_tokens + output_tokens != total_tokens:
        raise ValueError
    return AzureCandidateResponse(
        text=text,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
    )


def new_httpx_client(identity: AzureContainerAppCandidateIdentity) -> HTTPClient:
    """Build the sole bounded HTTP client owned by a candidate backend."""
    return cast(
        HTTPClient,
        httpx.Client(
            base_url=identity.endpoint,
            follow_redirects=False,
            timeout=httpx.Timeout(30.0),
            limits=httpx.Limits(max_connections=1, max_keepalive_connections=1),
            trust_env=False,
            headers={"accept": "application/json", "content-type": "application/json"},
        ),
    )


__all__ = (
    "MAX_PROVIDER_TOKENS",
    "MAX_RESPONSE_BYTES",
    "ContainerAppBackendAccounting",
    "ContainerAppBackendTrace",
    "ContainerAppResponseFailure",
    "ContainerAppTraceEvent",
    "ContainerAppTransportResponseError",
    "HTTPClient",
    "discard_trace",
    "emit_failure",
    "emit_trace",
    "new_httpx_client",
    "probe_model",
    "request_json",
    "request_text",
    "response_text",
    "validated_chat_response",
)
