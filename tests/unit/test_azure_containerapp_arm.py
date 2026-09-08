"""Fixed-origin ARM transport security contracts for Container Apps preflight."""

from __future__ import annotations

import json
from collections.abc import Iterator

import httpx
import pytest

from general_ludd.infra.azure_containerapp_arm import (
    ENVIRONMENT_PREFLIGHT_API_VERSION,
    AzureContainerAppARMError,
    HttpxARMJSONTransport,
    HttpxContainerAppARMTransport,
    HttpxContainerAppEnvironmentLifecycleTransport,
)

SUBSCRIPTION_ID = "11111111-2222-3333-4444-555555555555"
TOKEN = "opaque-bearer-token"
PROFILES_PATH = (
    f"/subscriptions/{SUBSCRIPTION_ID}/providers/Microsoft.App/locations/eastus/"
    "availableManagedEnvironmentsWorkloadProfileTypes?api-version=2025-07-01"
)
RESOURCE_ROOT = (
    f"/subscriptions/{SUBSCRIPTION_ID}/resourceGroups/gludd-models-eastus/"
    "providers/Microsoft.App/managedEnvironments/gludd-gpu-environment"
)
ENVIRONMENT_PATHS = (
    f"{RESOURCE_ROOT}?api-version={ENVIRONMENT_PREFLIGHT_API_VERSION}",
    f"{RESOURCE_ROOT}/usages?api-version={ENVIRONMENT_PREFLIGHT_API_VERSION}",
    f"{RESOURCE_ROOT}/workloadProfileStates?api-version={ENVIRONMENT_PREFLIGHT_API_VERSION}",
)
LIFECYCLE_ENVIRONMENT_PATH = f"{RESOURCE_ROOT}?api-version=2025-07-01"
APP_RESOURCE_ID = (
    f"/subscriptions/{SUBSCRIPTION_ID}/resourceGroups/gludd-models-eastus/"
    "providers/Microsoft.App/containerApps/gludd-vllm-proof-abc123"
)
APP_PATH = f"{APP_RESOURCE_ID}?api-version=2025-01-01"
APP_LIST_PATH = (
    f"/subscriptions/{SUBSCRIPTION_ID}/resourceGroups/gludd-models-eastus/"
    "providers/Microsoft.App/containerApps?api-version=2025-01-01"
)


def _client(handler: httpx.MockTransport) -> httpx.Client:
    return httpx.Client(
        base_url="https://management.azure.com",
        transport=handler,
        follow_redirects=True,
    )


def _subject(client: httpx.Client) -> HttpxARMJSONTransport:
    return HttpxARMJSONTransport(
        subscription_id=SUBSCRIPTION_ID,
        resource_group="gludd-models-eastus",
        environment_name="gludd-gpu-environment",
        client=client,
    )


@pytest.mark.parametrize("path", ENVIRONMENT_PATHS)
def test_transport_gets_only_the_fixed_arm_origin_and_exact_path(path: str) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"value": []})

    subject = _subject(_client(httpx.MockTransport(handler)))

    result = subject.get_json(path, TOKEN)

    assert result == {"value": []}
    assert len(requests) == 1
    assert str(requests[0].url) == f"https://management.azure.com{path}"
    assert requests[0].method == "GET"
    assert requests[0].headers["authorization"] == f"Bearer {TOKEN}"


def test_transport_does_not_follow_redirects_even_if_client_default_does() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            302,
            headers={"location": "https://attacker.invalid/capture"},
        )

    subject = _subject(_client(httpx.MockTransport(handler)))

    with pytest.raises(AzureContainerAppARMError, match="status 302"):
        subject.get_json(ENVIRONMENT_PATHS[0], TOKEN)

    assert len(requests) == 1


@pytest.mark.parametrize(
    "path",
    [
        "https://attacker.invalid/capture",
        f"//attacker.invalid/subscriptions/{SUBSCRIPTION_ID}",
        PROFILES_PATH,
        ENVIRONMENT_PATHS[0].replace("gludd-gpu-environment", "another-environment"),
        PROFILES_PATH.replace("Microsoft.App", "Microsoft.Authorization"),
        PROFILES_PATH.replace("availableManagedEnvironmentsWorkloadProfileTypes", "usages/delete"),
        PROFILES_PATH.replace("2025-07-01", "2099-01-01"),
        PROFILES_PATH + "#fragment",
        PROFILES_PATH + "%0d%0aX-Evil:true",
    ],
)
def test_transport_rejects_every_path_outside_named_environment_reads(path: str) -> None:
    called = False

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, json={})

    subject = _subject(_client(httpx.MockTransport(handler)))

    with pytest.raises(ValueError, match="approved read-only ARM path"):
        subject.get_json(path, TOKEN)

    assert called is False


@pytest.mark.parametrize(
    "token",
    ["", "line\nbreak", pytest.param("x" * 8193, id="oversized")],
)
def test_invalid_bearer_token_shape_is_rejected_before_network(token: str) -> None:
    subject = _subject(
        _client(
            httpx.MockTransport(
                lambda _request: pytest.fail("network must not be reached")
            )
        )
    )

    with pytest.raises(ValueError, match="bearer token"):
        subject.get_json(ENVIRONMENT_PATHS[0], token)


def test_provider_error_body_and_token_are_never_rendered() -> None:
    body = f"private provider detail {TOKEN} {SUBSCRIPTION_ID}"
    subject = _subject(
        _client(
            httpx.MockTransport(lambda _request: httpx.Response(403, text=body))
        )
    )

    with pytest.raises(AzureContainerAppARMError) as captured:
        subject.get_json(ENVIRONMENT_PATHS[0], TOKEN)

    rendered = repr(captured.value)
    assert captured.value.status_code == 403
    assert "status 403" in rendered
    assert body not in rendered
    assert TOKEN not in rendered
    assert SUBSCRIPTION_ID not in rendered


@pytest.mark.parametrize(
    ("headers", "body", "message"),
    [
        ({"content-type": "text/html"}, b"{}", "JSON content type"),
        ({"content-type": "application/json", "content-length": "1048577"}, b"{}", "too large"),
        ({"content-type": "application/json"}, b"{", "valid JSON"),
        ({"content-type": "application/json"}, b'\xff', "valid UTF-8 JSON"),
        (
            {"content-type": "application/json"},
            b'{"value":[],"value":[]}',
            "duplicate JSON field",
        ),
    ],
)
def test_untrusted_response_shape_is_bounded_and_unambiguous(
    headers: dict[str, str],
    body: bytes,
    message: str,
) -> None:
    subject = _subject(
        _client(
            httpx.MockTransport(
                lambda _request: httpx.Response(200, headers=headers, content=body)
            )
        )
    )

    with pytest.raises(AzureContainerAppARMError, match=message):
        subject.get_json(ENVIRONMENT_PATHS[0], TOKEN)


def test_streamed_body_is_limited_even_without_content_length() -> None:
    class OversizedStream(httpx.SyncByteStream):
        def __iter__(self) -> Iterator[bytes]:
            yield b"{" + b"x" * 700_000
            yield b"x" * 700_000

    subject = _subject(
        _client(
            httpx.MockTransport(
                lambda _request: httpx.Response(
                    200,
                    headers={"content-type": "application/json"},
                    stream=OversizedStream(),
                )
            )
        )
    )

    with pytest.raises(AzureContainerAppARMError, match="too large"):
        subject.get_json(ENVIRONMENT_PATHS[0], TOKEN)


def test_transport_accepts_json_content_type_with_charset() -> None:
    body = json.dumps({"value": [{"name": "safe"}]}).encode()
    subject = _subject(
        _client(
            httpx.MockTransport(
                lambda _request: httpx.Response(
                    200,
                    headers={"content-type": "application/json; charset=utf-8"},
                    content=body,
                )
            )
        )
    )

    assert subject.get_json(ENVIRONMENT_PATHS[0], TOKEN) == {
        "value": [{"name": "safe"}]
    }


def test_close_does_not_close_an_injected_client() -> None:
    client = _client(
        httpx.MockTransport(lambda _request: httpx.Response(200, json={}))
    )
    subject = _subject(client)

    subject.close()

    assert client.is_closed is False
    client.close()


def _app_subject(client: httpx.Client) -> HttpxContainerAppARMTransport:
    return HttpxContainerAppARMTransport(
        subscription_id=SUBSCRIPTION_ID,
        resource_group="gludd-models-eastus",
        app_name="gludd-vllm-proof-abc123",
        client=client,
    )


def test_app_transport_reads_only_one_exact_container_app() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"id": APP_RESOURCE_ID})

    subject = _app_subject(_client(httpx.MockTransport(handler)))

    assert subject.get_json(TOKEN) == {"id": APP_RESOURCE_ID}
    assert len(requests) == 1
    assert requests[0].method == "GET"
    assert str(requests[0].url) == f"https://management.azure.com{APP_PATH}"


def test_app_transport_represents_exact_404_as_absent() -> None:
    subject = _app_subject(
        _client(httpx.MockTransport(lambda _request: httpx.Response(404)))
    )

    assert subject.get_json(TOKEN) is None


@pytest.mark.parametrize(
    ("status", "expected"),
    [(302, "status 302"), (403, "status 403"), (500, "status 500")],
)
def test_app_transport_censors_every_non_absence_failure(
    status: int,
    expected: str,
) -> None:
    private_body = f"private {TOKEN} {SUBSCRIPTION_ID}"
    subject = _app_subject(
        _client(
            httpx.MockTransport(
                lambda _request: httpx.Response(status, text=private_body)
            )
        )
    )

    with pytest.raises(AzureContainerAppARMError, match=expected) as captured:
        subject.get_json(TOKEN)

    assert private_body not in repr(captured.value)
    assert TOKEN not in repr(captured.value)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("subscription_id", "not-a-uuid"),
        ("resource_group", "../other"),
        ("app_name", "other/app"),
    ],
)
def test_app_transport_rejects_ambiguous_resource_identifiers(
    field: str,
    value: str,
) -> None:
    values = {
        "subscription_id": SUBSCRIPTION_ID,
        "resource_group": "gludd-models-eastus",
        "app_name": "gludd-vllm-proof-abc123",
    }
    values[field] = value

    with pytest.raises(ValueError):
        HttpxContainerAppARMTransport(**values)


def _lifecycle_subject(
    client: httpx.Client,
) -> HttpxContainerAppEnvironmentLifecycleTransport:
    return HttpxContainerAppEnvironmentLifecycleTransport(
        subscription_id=SUBSCRIPTION_ID,
        resource_group="gludd-models-eastus",
        environment_name="gludd-gpu-environment",
        client=client,
    )


def test_lifecycle_transport_reads_only_environment_and_group_app_inventory() -> None:
    requests: list[httpx.Request] = []
    second_id = APP_RESOURCE_ID.replace("abc123", "def456")
    foreign_environment = RESOURCE_ROOT.replace("gludd-gpu-environment", "foreign")

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/containerApps"):
            return httpx.Response(
                200,
                json={
                    "value": [
                        {
                            "id": second_id,
                            "properties": {"managedEnvironmentId": RESOURCE_ROOT.upper()},
                        },
                        {
                            "id": APP_RESOURCE_ID,
                            "properties": {"managedEnvironmentId": RESOURCE_ROOT},
                        },
                        {
                            "id": APP_RESOURCE_ID.replace("abc123", "foreign"),
                            "properties": {"managedEnvironmentId": foreign_environment},
                        },
                    ]
                },
            )
        return httpx.Response(200, json={"id": RESOURCE_ROOT})

    subject = _lifecycle_subject(_client(httpx.MockTransport(handler)))

    assert subject.get_environment(TOKEN) == {"id": RESOURCE_ROOT}
    assert subject.list_environment_app_ids(TOKEN) == tuple(
        sorted((APP_RESOURCE_ID, second_id))
    )
    assert [request.method for request in requests] == ["GET", "GET"]
    assert [str(request.url) for request in requests] == [
        f"https://management.azure.com{LIFECYCLE_ENVIRONMENT_PATH}",
        f"https://management.azure.com{APP_LIST_PATH}",
    ]
    assert all(request.headers["authorization"] == f"Bearer {TOKEN}" for request in requests)


def test_lifecycle_transport_represents_only_exact_environment_404_as_absent() -> None:
    subject = _lifecycle_subject(
        _client(httpx.MockTransport(lambda _request: httpx.Response(404)))
    )

    assert subject.get_environment(TOKEN) is None
    with pytest.raises(AzureContainerAppARMError, match="status 404"):
        subject.list_environment_app_ids(TOKEN)


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {},
        {"value": {}},
        {"value": [], "nextLink": "https://management.azure.com/next"},
        {"value": [{"id": APP_RESOURCE_ID}]},
        {
            "value": [
                {
                    "id": APP_RESOURCE_ID,
                    "properties": {"managedEnvironmentId": 7},
                }
            ]
        },
        {
            "value": [
                {
                    "id": "/subscriptions/other/containerApps/foreign",
                    "properties": {"managedEnvironmentId": RESOURCE_ROOT},
                }
            ]
        },
        {
            "value": [
                {
                    "id": APP_RESOURCE_ID,
                    "properties": {"managedEnvironmentId": RESOURCE_ROOT},
                },
                {
                    "id": APP_RESOURCE_ID.upper(),
                    "properties": {"managedEnvironmentId": RESOURCE_ROOT},
                },
            ]
        },
    ],
    ids=(
        "not-object",
        "missing-value",
        "value-not-list",
        "pagination",
        "missing-properties",
        "environment-not-string",
        "foreign-id",
        "duplicate-id",
    ),
)
def test_lifecycle_inventory_fails_closed_on_incomplete_or_ambiguous_truth(
    payload: object,
) -> None:
    subject = _lifecycle_subject(
        _client(httpx.MockTransport(lambda _request: httpx.Response(200, json=payload)))
    )

    with pytest.raises(AzureContainerAppARMError, match="inventory"):
        subject.list_environment_app_ids(TOKEN)


@pytest.mark.parametrize("operation", ["environment", "inventory"])
def test_lifecycle_transport_never_follows_redirects_or_renders_private_body(
    operation: str,
) -> None:
    requests: list[httpx.Request] = []
    private = f"private {TOKEN} {SUBSCRIPTION_ID}"

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            302,
            text=private,
            headers={"location": "https://attacker.invalid/capture"},
        )

    subject = _lifecycle_subject(_client(httpx.MockTransport(handler)))
    call = (
        subject.get_environment
        if operation == "environment"
        else subject.list_environment_app_ids
    )

    with pytest.raises(AzureContainerAppARMError) as captured:
        call(TOKEN)

    assert len(requests) == 1
    assert TOKEN not in repr(captured.value)
    assert private not in repr(captured.value)


@pytest.mark.parametrize(
    "token",
    ["", "line\nbreak", pytest.param("x" * 8193, id="oversized")],
)
def test_lifecycle_transport_rejects_invalid_tokens_before_network(token: str) -> None:
    subject = _lifecycle_subject(
        _client(
            httpx.MockTransport(
                lambda _request: pytest.fail("network must not be reached")
            )
        )
    )

    with pytest.raises(ValueError, match="bearer token"):
        subject.get_environment(token)
    with pytest.raises(ValueError, match="bearer token"):
        subject.list_environment_app_ids(token)
