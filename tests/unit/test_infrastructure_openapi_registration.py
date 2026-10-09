"""Fail-closed native OpenAPI query contracts for infrastructure discovery."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import runpy
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
import yaml
from scripts.check_collection_python_boundary import scan_collections

ROOT = Path(__file__).resolve().parents[2]
COLLECTIONS = ROOT / "collections"
if str(COLLECTIONS) not in sys.path:
    sys.path.insert(0, str(COLLECTIONS))

from ansible_collections.general_ludd.infrastructure.plugins.action import (  # noqa: E402
    openapi_query as action_plugin,
)
from ansible_collections.general_ludd.infrastructure.plugins.action.openapi_query import (  # noqa: E402
    ActionModule,
    execute_action,
)
from ansible_collections.general_ludd.infrastructure.plugins.module_utils import (  # noqa: E402
    openapi_registration,
)
from ansible_collections.general_ludd.infrastructure.plugins.module_utils import (  # noqa: E402
    secure_fetch as secure_transport,
)
from ansible_collections.general_ludd.infrastructure.plugins.module_utils.secure_fetch import (  # noqa: E402
    FetchResult,
)
from ansible_collections.general_ludd.infrastructure.plugins.modules import (  # noqa: E402
    openapi_query as module_stub,
)


def _spec() -> dict[str, Any]:
    return {
        "openapi": "3.1.0",
        "info": {"title": "Inventory API", "version": "1.0.0"},
        "paths": {
            "/v1/services/{service_id}": {
                "parameters": [
                    {
                        "name": "service_id",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "string"},
                    }
                ],
                "get": {
                    "operationId": "getService",
                    "parameters": [
                        {
                            "$ref": "#/components/parameters/Region",
                        }
                    ],
                    "responses": {
                        "200": {
                            "description": "Service inventory",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "required": ["records"],
                                        "properties": {
                                            "records": {
                                                "type": "array",
                                                "items": {
                                                    "type": "object",
                                                    "required": ["id"],
                                                    "properties": {"id": {"type": "string"}},
                                                    "additionalProperties": False,
                                                },
                                            }
                                        },
                                        "additionalProperties": False,
                                    }
                                }
                            },
                        }
                    },
                },
            }
        },
        "components": {
            "parameters": {
                "Region": {
                    "name": "region",
                    "in": "query",
                    "required": True,
                    "schema": {"type": "string"},
                }
            }
        },
    }


def _write_spec(root: Path, spec: dict[str, Any] | None = None) -> str:
    content = json.dumps(spec or _spec(), sort_keys=True).encode()
    (root / "inventory.openapi.json").write_bytes(content)
    return hashlib.sha256(content).hexdigest()


def _args(root: Path, digest: str) -> dict[str, Any]:
    return {
        "root": str(root),
        "spec_path": "inventory.openapi.json",
        "spec_sha256": digest,
        "base_url": "https://api.example.test",
        "allowed_host": "api.example.test",
        "operation_id": "getService",
        "parameters": {"service_id": "svc/1", "region": "us east"},
        "secret_env": {"Authorization": "GLUDD_TEST_API_TOKEN"},
        "records_pointer": "/records",
        "timeout_seconds": 5.0,
        "max_response_bytes": 4096,
        "max_records": 2,
    }


def test_real_read_only_query_is_digest_bound_and_schema_validated(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    digest = _write_spec(tmp_path)
    monkeypatch.setenv("GLUDD_TEST_API_TOKEN", "Bearer secret-value")
    calls: list[dict[str, object]] = []

    def fetch(url: str, **kwargs: object) -> FetchResult:
        calls.append({"url": url, **kwargs})
        return FetchResult(
            url=url,
            status_code=200,
            headers={"content-type": "application/json"},
            content=b'{"records":[{"id":"svc-1"}]}',
        )

    result = execute_action(_args(tmp_path, digest), fetcher=fetch)

    assert result == {
        "changed": False,
        "executed": True,
        "method": "GET",
        "operation_id": "getService",
        "path": "/v1/services/{service_id}",
        "record_count": 1,
        "records": [{"id": "svc-1"}],
        "spec_sha256": digest,
        "status_code": 200,
        "validated": True,
    }
    assert calls[0]["url"] == "https://api.example.test/v1/services/svc%2F1?region=us+east"
    assert calls[0]["method"] == "GET"
    assert calls[0]["headers"] == {
        "Accept": "application/json",
        "Authorization": "Bearer secret-value",
    }
    policy = cast(secure_transport.FetchPolicy, calls[0]["policy"])
    assert policy.allowed_hosts == frozenset({"api.example.test"})
    assert policy.allowed_schemes == frozenset({"https"})
    assert policy.max_bytes == 4096
    assert policy.max_redirects == 0
    assert "secret-value" not in json.dumps(result)


@pytest.mark.parametrize(
    ("mutator", "message"),
    [
        (lambda args: {**args, "spec_sha256": "0" * 64}, "digest"),
        (lambda args: {**args, "spec_path": "../outside.json"}, "escapes root"),
        (lambda args: {**args, "base_url": "http://api.example.test"}, "HTTPS"),
        (lambda args: {**args, "base_url": "https://other.example.test"}, "allowed_host"),
        (lambda args: {**args, "operation_id": "missing"}, "operation_id"),
        (lambda args: {**args, "parameters": {}}, "required parameter"),
        (lambda args: {**args, "parameters": {"unknown": "x"}}, "unknown parameter"),
        (lambda args: {**args, "secret_env": {"Authorization": "not-valid"}}, "environment"),
        (lambda args: {**args, "timeout_seconds": 0}, "timeout"),
        (lambda args: {**args, "max_response_bytes": 4_194_305}, "response"),
        (lambda args: {**args, "max_records": 1001}, "record"),
        (lambda args: {**args, "uri": "https://evil.test"}, "unsupported argument"),
    ],
)
def test_unsafe_or_ambiguous_inputs_fail_before_transport(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutator: Any,
    message: str,
) -> None:
    digest = _write_spec(tmp_path)
    monkeypatch.setenv("GLUDD_TEST_API_TOKEN", "secret")
    calls: list[object] = []

    def fetch(*args: object, **kwargs: object) -> FetchResult:
        calls.append((args, kwargs))
        raise AssertionError("transport must not run")

    with pytest.raises(
        (openapi_registration.OpenAPIRegistrationError, TypeError, ValueError),
        match=message,
    ):
        execute_action(mutator(_args(tmp_path, digest)), fetcher=fetch)
    assert calls == []


def test_methods_specs_secrets_and_response_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec = _spec()
    operation = spec["paths"]["/v1/services/{service_id}"].pop("get")
    spec["paths"]["/v1/services/{service_id}"]["post"] = operation
    digest = _write_spec(tmp_path, spec)
    monkeypatch.setenv("GLUDD_TEST_API_TOKEN", "secret")
    with pytest.raises(openapi_registration.OpenAPIRegistrationError, match="GET or HEAD"):
        execute_action(_args(tmp_path, digest), fetcher=lambda *_args, **_kwargs: None)

    digest = _write_spec(tmp_path)
    monkeypatch.delenv("GLUDD_TEST_API_TOKEN")
    with pytest.raises(openapi_registration.OpenAPIRegistrationError, match="not set"):
        execute_action(_args(tmp_path, digest), fetcher=lambda *_args, **_kwargs: None)

    monkeypatch.setenv("GLUDD_TEST_API_TOKEN", "secret")

    def malformed(url: str, **_kwargs: object) -> FetchResult:
        return FetchResult(
            url=url,
            status_code=200,
            headers={"content-type": "application/json"},
            content=b'{"records":[{"wrong":true}]}',
        )

    with pytest.raises(openapi_registration.OpenAPIRegistrationError, match="response validation"):
        execute_action(_args(tmp_path, digest), fetcher=malformed)

    def excessive(url: str, **_kwargs: object) -> FetchResult:
        return FetchResult(
            url=url,
            status_code=200,
            headers={"content-type": "application/json"},
            content=b'{"records":[{"id":"1"},{"id":"2"},{"id":"3"}]}',
        )

    with pytest.raises(openapi_registration.OpenAPIRegistrationError, match="record limit"):
        execute_action(_args(tmp_path, digest), fetcher=excessive)


def test_validate_only_and_check_mode_do_not_open_transport(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    digest = _write_spec(tmp_path)
    monkeypatch.setenv("GLUDD_TEST_API_TOKEN", "secret")

    def forbidden(*_args: object, **_kwargs: object) -> FetchResult:
        raise AssertionError("validation-only mode must not use transport")

    result = execute_action(
        {**_args(tmp_path, digest), "validate_only": True},
        fetcher=forbidden,
    )
    assert result["validated"] is True
    assert result["executed"] is False
    assert result["changed"] is False

    check = execute_action(_args(tmp_path, digest), check_mode=True, fetcher=forbidden)
    assert check["validated"] is True
    assert check["executed"] is False
    assert check["check_mode"] is True


def test_action_translates_errors_and_module_bypass_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    digest = _write_spec(tmp_path)
    action = object.__new__(ActionModule)
    action._task = SimpleNamespace(args={**_args(tmp_path, digest), "spec_sha256": "0" * 64}, check_mode=False)
    monkeypatch.setattr(action_plugin._ActionBase, "run", lambda *_args, **_kwargs: {})
    result = action.run()
    assert result["failed"] is True
    assert result["changed"] is False

    class Module:
        def __init__(self, **kwargs: Any) -> None:
            assert kwargs["supports_check_mode"] is True
            assert kwargs["argument_spec"]["operation_id"]["required"] is True

        def fail_json(self, **kwargs: Any) -> None:
            raise RuntimeError(kwargs["msg"])

    monkeypatch.setattr(module_stub, "AnsibleModule", Module)
    with pytest.raises(RuntimeError, match="controller-side action plugin"):
        module_stub.main()
    monkeypatch.setattr("ansible.module_utils.basic.AnsibleModule", Module)
    with pytest.raises(RuntimeError, match="controller-side action plugin"):
        runpy.run_path(str(Path(module_stub.__file__)), run_name="__main__")


def test_role_is_native_read_only_namespaced_and_has_molecule_rollback() -> None:
    role = ROOT / "collections/ansible_collections/general_ludd/infrastructure/roles/auto_register_service"
    defaults = yaml.safe_load((role / "defaults/main.yml").read_text(encoding="utf-8"))
    tasks_text = (role / "tasks/main.yml").read_text(encoding="utf-8")
    readme = (role / "README.md").read_text(encoding="utf-8")
    scenario = ROOT / "molecule/playbooks/infrastructure_openapi_registration"
    molecule = yaml.safe_load((scenario / "molecule.yml").read_text(encoding="utf-8"))
    converge = (scenario / "default/converge.yml").read_text(encoding="utf-8")
    verify = (scenario / "default/verify.yml").read_text(encoding="utf-8")

    assert defaults
    assert all(name.startswith("auto_register_service_") for name in defaults)
    assert "general_ludd.infrastructure.openapi_query" in tasks_text
    for forbidden in ("template:", "lineinfile:", "blockinfile:", "shell:", "command:"):
        assert forbidden not in tasks_text
    assert "validate_only" in converge
    assert "spec_sha256" in verify
    assert molecule["provisioner"]["env"]["ANSIBLE_COLLECTIONS_SCAN_SYS_PATH"] == "false"
    assert "canary" in readme.lower()
    assert "drain" in readme.lower()
    assert "rollback" in readme.lower()


def test_dependencies_boundary_and_no_escape_hatches_are_pinned() -> None:
    collection = ROOT / "collections/ansible_collections/general_ludd/infrastructure"
    requirements = (ROOT / "config/ansible/requirements.txt").read_text(encoding="utf-8")
    controller = (ROOT / "requirements/profiles/ansible-controller/pyproject.toml").read_text(
        encoding="utf-8"
    )
    source = (collection / "plugins/module_utils/openapi_registration.py").read_text(encoding="utf-8")

    assert scan_collections(collection) == []
    assert "openapi-core==0.23.1" in requirements
    assert "jsonpointer==3.2.0" in requirements
    assert "openapi-core==0.23.1" in controller
    assert "jsonpointer==3.2.0" in controller
    assert "from openapi_core import Config, OpenAPI" in source
    assert "from jsonpointer import" in source
    assert "resolve_pointer" in source
    assert "plugins.module_utils.secure_fetch import" in source
    for forbidden in ("subprocess", "urlopen", "requests.", "socket.", "Thread", "retry"):
        assert forbidden not in source


def test_openapi_file_and_document_guards_cover_local_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    valid_digest = _write_spec(tmp_path)
    base = _args(tmp_path, valid_digest)

    relative_root = {**base, "root": "relative"}
    with pytest.raises(openapi_registration.OpenAPIRegistrationError, match="absolute"):
        execute_action(relative_root)

    root_file = tmp_path / "root-file"
    root_file.write_text("x", encoding="utf-8")
    with pytest.raises(openapi_registration.OpenAPIRegistrationError, match="directory"):
        execute_action({**base, "root": str(root_file)})

    missing = {**base, "spec_path": "missing.json"}
    with pytest.raises(openapi_registration.OpenAPIRegistrationError, match="unavailable"):
        execute_action(missing)

    linked = tmp_path / "linked.json"
    linked.symlink_to(tmp_path / "inventory.openapi.json")
    with pytest.raises(openapi_registration.OpenAPIRegistrationError, match="symlink"):
        execute_action({**base, "spec_path": "linked.json"})

    empty = tmp_path / "empty.json"
    empty.write_bytes(b"")
    with pytest.raises(openapi_registration.OpenAPIRegistrationError, match="between 1 byte"):
        execute_action({**base, "spec_path": "empty.json"})

    (tmp_path / "inventory.openapi.json").write_text("[]", encoding="utf-8")
    digest = hashlib.sha256(b"[]").hexdigest()
    with pytest.raises(openapi_registration.OpenAPIRegistrationError, match="must be an object"):
        execute_action({**base, "spec_sha256": digest})

    content = b'{"openapi":"3.0.3","info":{"title":"x","version":"1"},"paths":{}}'
    (tmp_path / "inventory.openapi.json").write_bytes(content)
    with pytest.raises(openapi_registration.OpenAPIRegistrationError, match=r"OpenAPI 3\.1"):
        execute_action({**base, "spec_sha256": hashlib.sha256(content).hexdigest()})

    content = b'{"openapi":"3.1.0"}'
    (tmp_path / "inventory.openapi.json").write_bytes(content)
    with pytest.raises(openapi_registration.OpenAPIRegistrationError, match="specification validation"):
        execute_action({**base, "spec_sha256": hashlib.sha256(content).hexdigest()})

    digest = _write_spec(tmp_path)
    monkeypatch.setattr(openapi_registration, "MAX_SPEC_NODES", 1)
    with pytest.raises(openapi_registration.OpenAPIRegistrationError, match="node limit"):
        execute_action({**base, "spec_sha256": digest})


def test_spec_read_uses_a_nofollow_stable_descriptor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    digest = _write_spec(tmp_path)

    def path_read_is_forbidden(_path: Path) -> bytes:
        raise AssertionError("contract reads must use a no-follow descriptor")

    monkeypatch.setattr(Path, "read_bytes", path_read_is_forbidden)
    result = execute_action(
        {
            **_args(tmp_path, digest),
            "secret_env": {},
            "validate_only": True,
        }
    )
    assert result["validated"] is True
    source = Path(openapi_registration.__file__).read_text(encoding="utf-8")
    assert "os.O_NOFOLLOW" in source
    assert "os.fstat" in source


def test_reference_operation_and_parameter_definition_guards(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base_spec = _spec()

    def rejected(spec: dict[str, Any], message: str) -> None:
        digest = _write_spec(tmp_path, spec)
        with pytest.raises(openapi_registration.OpenAPIRegistrationError, match=message):
            execute_action({**_args(tmp_path, digest), "secret_env": {}}, fetcher=lambda *_a, **_k: None)

    external = _spec()
    external["paths"] = {"/x": {"$ref": "https://example.test/path.json"}}
    rejected(external, "internal JSON Pointer")

    missing_ref = _spec()
    missing_ref["paths"] = {"/x": {"$ref": "#/components/missing"}}
    rejected(missing_ref, "cannot be resolved")

    bad_path = _spec()
    bad_path["paths"] = {"relative": base_spec["paths"]["/v1/services/{service_id}"]}
    rejected(bad_path, "absolute paths")
    with pytest.raises(openapi_registration.OpenAPIRegistrationError, match="absolute paths"):
        openapi_registration._find_operation(bad_path, "getService")

    bad_list = _spec()
    bad_list["paths"]["/v1/services/{service_id}"]["parameters"] = {}
    with pytest.raises(openapi_registration.OpenAPIRegistrationError, match="parameters must be a list"):
        openapi_registration._find_operation(bad_list, "getService")

    duplicate = _spec()
    duplicate["paths"]["/other"] = {
        "get": duplicate["paths"]["/v1/services/{service_id}"]["get"]
    }
    rejected(duplicate, "exactly one")
    with pytest.raises(openapi_registration.OpenAPIRegistrationError, match="exactly one"):
        openapi_registration._find_operation(duplicate, "getService")

    cookie = _spec()
    cookie["paths"]["/v1/services/{service_id}"]["get"]["parameters"].append(
        {"name": "session", "in": "cookie", "schema": {"type": "string"}}
    )
    digest = _write_spec(tmp_path, cookie)
    with pytest.raises(openapi_registration.OpenAPIRegistrationError, match="only path, query, and header"):
        execute_action({**_args(tmp_path, digest), "secret_env": {}, "parameters": {"service_id": "x", "region": "r"}})

    missing_responses = _spec()
    del missing_responses["paths"]["/v1/services/{service_id}"]["get"]["responses"]
    rejected(missing_responses, "response contract")

    non_json_response = _spec()
    non_json_response["paths"]["/v1/services/{service_id}"]["get"]["responses"] = {
        "200": {
            "description": "plain",
            "content": {"text/plain": {"schema": {"type": "string"}}},
        }
    }
    rejected(non_json_response, "JSON response schema")

    missing_definition = _spec()
    missing_definition["paths"]["/v1/services/{service_id}"]["get"]["parameters"] = [{}]
    with pytest.raises(openapi_registration.OpenAPIRegistrationError, match="include name and location"):
        openapi_registration._request_parts(
            openapi_registration._Operation("/x", "GET", ({},)),
            {},
        )

    digest = _write_spec(tmp_path)
    monkeypatch.setattr(openapi_registration, "MAX_OPERATIONS", 0)
    with pytest.raises(openapi_registration.OpenAPIRegistrationError, match="operation limit"):
        execute_action({**_args(tmp_path, digest), "secret_env": {}})


def test_parameter_secret_and_record_bounds_are_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    digest = _write_spec(tmp_path)
    base = {**_args(tmp_path, digest), "secret_env": {}}

    for value, message in [([], "mapping"), ({"service_id": [], "region": "r"}, "scalar")]:
        with pytest.raises((TypeError, openapi_registration.OpenAPIRegistrationError), match=message):
            execute_action({**base, "parameters": value})

    with pytest.raises(openapi_registration.OpenAPIRegistrationError, match="between 1 and 512"):
        execute_action({**base, "parameters": {"service_id": "", "region": "r"}})

    monkeypatch.setattr(openapi_registration, "MAX_PARAMETER_BYTES", 1)
    with pytest.raises(openapi_registration.OpenAPIRegistrationError, match="4096-byte"):
        execute_action(base)
    monkeypatch.setattr(openapi_registration, "MAX_PARAMETER_BYTES", 4096)

    malformed_secret_calls = [
        ({"Bad Header": "VALID_ENV"}, "header name"),
        ({"Authorization": "lowercase"}, "environment variable name"),
    ]
    for secret_env, message in malformed_secret_calls:
        with pytest.raises(openapi_registration.OpenAPIRegistrationError, match=message):
            execute_action({**base, "secret_env": secret_env})

    monkeypatch.setenv("EMPTY_SECRET", "")
    with pytest.raises(openapi_registration.OpenAPIRegistrationError, match="is invalid"):
        execute_action({**base, "secret_env": {"Authorization": "EMPTY_SECRET"}})

    response = FetchResult(
        url="https://api.example.test/x",
        status_code=200,
        headers={"content-type": "text/plain"},
        content=b"[]",
    )
    with pytest.raises(openapi_registration.OpenAPIRegistrationError, match="response validation"):
        execute_action(base, fetcher=lambda *_a, **_k: response)
    with pytest.raises(openapi_registration.OpenAPIRegistrationError, match="content type"):
        openapi_registration._decode_records(response, "", 10)

    response = FetchResult(
        url="https://api.example.test/x",
        status_code=200,
        headers={"content-type": "application/json"},
        content=b"not-json",
    )
    with pytest.raises(openapi_registration.OpenAPIRegistrationError, match="response validation"):
        execute_action(base, fetcher=lambda *_a, **_k: response)
    with pytest.raises(openapi_registration.OpenAPIRegistrationError, match="valid JSON"):
        openapi_registration._decode_records(response, "", 10)

    valid = FetchResult(
        url="https://api.example.test/x",
        status_code=200,
        headers={"content-type": "application/json"},
        content=b'{"records":[]}',
    )
    with pytest.raises(openapi_registration.OpenAPIRegistrationError, match="cannot be resolved"):
        execute_action({**base, "records_pointer": "/missing"}, fetcher=lambda *_a, **_k: valid)
    with pytest.raises(openapi_registration.OpenAPIRegistrationError, match="resolve to a list"):
        execute_action({**base, "records_pointer": ""}, fetcher=lambda *_a, **_k: valid)


def test_non_finite_and_ambiguous_headers_fail_before_transport(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    digest = _write_spec(tmp_path)
    base = {**_args(tmp_path, digest), "secret_env": {}}

    for value in (math.nan, math.inf, -math.inf):
        with pytest.raises(openapi_registration.OpenAPIRegistrationError, match="finite"):
            execute_action(
                {**base, "parameters": {"service_id": value, "region": "r"}},
            )

    monkeypatch.setenv("ONE", "one")
    monkeypatch.setenv("TWO", "two")
    for secret_env, message in (
        ({"Host": "ONE"}, "reserved"),
        ({"Authorization": "ONE", "authorization": "TWO"}, "case-insensitive"),
        ({"Accept": "ONE"}, "Accept"),
    ):
        with pytest.raises(openapi_registration.OpenAPIRegistrationError, match=message):
            execute_action({**base, "secret_env": secret_env})

    spec = _spec()
    operation = spec["paths"]["/v1/services/{service_id}"]["get"]
    operation["parameters"].append(
        {"name": "Authorization", "in": "header", "schema": {"type": "string"}}
    )
    digest = _write_spec(tmp_path, spec)
    with pytest.raises(openapi_registration.OpenAPIRegistrationError, match="secret header"):
        execute_action(
            {
                **_args(tmp_path, digest),
                "parameters": {
                    "service_id": "svc",
                    "region": "r",
                    "Authorization": "plain",
                },
                "secret_env": {"authorization": "ONE"},
            }
        )


def test_transport_policy_and_static_url_validation_are_strict() -> None:
    policy = secure_transport.FetchPolicy(allowed_hosts=frozenset({"api.example.com"}))
    assert secure_transport._validated_url("https://api.example.com/v1#fragment", policy) == (
        "https://api.example.com/v1",
        "api.example.com",
        443,
    )
    bad_policies: list[dict[str, Any]] = [
        {"allowed_hosts": frozenset()},
        {"allowed_hosts": frozenset({"*.example.com"})},
        {"allowed_hosts": frozenset({"api.example.com"}), "allowed_schemes": frozenset({"http"})},
        {"allowed_hosts": frozenset({"api.example.com"}), "max_bytes": 0},
        {"allowed_hosts": frozenset({"api.example.com"}), "timeout_seconds": 0},
        {"allowed_hosts": frozenset({"api.example.com"}), "max_redirects": 1},
    ]
    for kwargs in bad_policies:
        with pytest.raises(ValueError):
            secure_transport.FetchPolicy(**kwargs)

    for url in (
        "http://api.example.com",
        "https://user:pass@api.example.com",
        "https://other.example.com",
        "https://localhost",
        "https://127.0.0.1",
    ):
        with pytest.raises(secure_transport.SecureFetchError):
            secure_transport._validated_url(url, policy)


@pytest.mark.asyncio
async def test_transport_resolution_rejects_every_non_public_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loop = asyncio.get_running_loop()

    async def private(*_args: object, **_kwargs: object) -> list[tuple[Any, ...]]:
        return [(0, 0, 0, "", ("127.0.0.1", 443))]

    monkeypatch.setattr(loop, "getaddrinfo", private)
    with pytest.raises(secure_transport.SecureFetchError, match="non-public"):
        await secure_transport._resolve_public("api.example.com", 443, 1.0)

    async def empty(*_args: object, **_kwargs: object) -> list[tuple[Any, ...]]:
        return []

    monkeypatch.setattr(loop, "getaddrinfo", empty)
    with pytest.raises(secure_transport.SecureFetchError, match="no addresses"):
        await secure_transport._resolve_public("api.example.com", 443, 1.0)


class _FakeHTTPResponse:
    def __init__(
        self,
        *,
        status: int = 200,
        headers: dict[str, str] | None = None,
        chunks: list[bytes] | None = None,
    ) -> None:
        self.status_code = status
        self.headers = headers or {"content-type": "application/json"}
        self._chunks = chunks or [b"[]"]

    async def aiter_bytes(self) -> Any:
        for chunk in self._chunks:
            yield chunk


class _FakeStream:
    def __init__(self, response: _FakeHTTPResponse) -> None:
        self.response = response

    async def __aenter__(self) -> _FakeHTTPResponse:
        return self.response

    async def __aexit__(self, *_args: object) -> None:
        return None


class _FakeClient:
    response = _FakeHTTPResponse()

    def __init__(self, **kwargs: object) -> None:
        assert kwargs["follow_redirects"] is False
        assert kwargs["trust_env"] is False

    async def __aenter__(self) -> _FakeClient:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    def stream(self, method: str, url: str, *, headers: Any) -> _FakeStream:
        assert method in {"GET", "HEAD"}
        assert url.startswith("https://api.example.com")
        assert isinstance(headers, dict)
        return _FakeStream(self.response)


@pytest.mark.asyncio
async def test_secure_transport_success_redirect_and_byte_caps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def public(*_args: object, **_kwargs: object) -> str:
        return "93.184.216.34"

    monkeypatch.setattr(secure_transport, "_resolve_public", public)
    monkeypatch.setattr(secure_transport.safehttpx, "AsyncSecureTransport", lambda _ip: object())
    monkeypatch.setattr(secure_transport.httpx, "AsyncClient", _FakeClient)
    policy = secure_transport.FetchPolicy(
        allowed_hosts=frozenset({"api.example.com"}),
        max_bytes=8,
    )
    result = await secure_transport._secure_fetch_async(
        "https://api.example.com/v1",
        policy=policy,
        method="GET",
        headers={"Accept": "application/json"},
    )
    assert result.content == b"[]"

    _FakeClient.response = _FakeHTTPResponse(status=302)
    with pytest.raises(secure_transport.SecureFetchError, match="redirect"):
        await secure_transport._secure_fetch_async(
            "https://api.example.com/v1", policy=policy, method="GET", headers={}
        )

    _FakeClient.response = _FakeHTTPResponse(headers={"content-length": "9"})
    with pytest.raises(secure_transport.SecureFetchError, match="byte limit"):
        await secure_transport._secure_fetch_async(
            "https://api.example.com/v1", policy=policy, method="GET", headers={}
        )

    _FakeClient.response = _FakeHTTPResponse(chunks=[b"12345", b"6789"])
    with pytest.raises(secure_transport.SecureFetchError, match="byte limit"):
        await secure_transport._secure_fetch_async(
            "https://api.example.com/v1", policy=policy, method="GET", headers={}
        )
    with pytest.raises(ValueError, match="GET or HEAD"):
        await secure_transport._secure_fetch_async(
            "https://api.example.com/v1", policy=policy, method="POST", headers={}
        )


@pytest.mark.asyncio
async def test_transport_errors_are_bounded_and_redacted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def public(*_args: object, **_kwargs: object) -> str:
        return "93.184.216.34"

    class FailingClient:
        def __init__(self, **_kwargs: object) -> None:
            pass

        async def __aenter__(self) -> Any:
            raise secure_transport.httpx.ConnectError("sensitive upstream detail")

        async def __aexit__(self, *_args: object) -> None:
            return None

    monkeypatch.setattr(secure_transport, "_resolve_public", public)
    monkeypatch.setattr(secure_transport.safehttpx, "AsyncSecureTransport", lambda _ip: object())
    monkeypatch.setattr(secure_transport.httpx, "AsyncClient", FailingClient)
    policy = secure_transport.FetchPolicy(allowed_hosts=frozenset({"api.example.com"}))

    with pytest.raises(secure_transport.SecureFetchError, match="request failed") as failure:
        await secure_transport._secure_fetch_async(
            "https://api.example.com/private",
            policy=policy,
            method="GET",
            headers={"Authorization": "secret"},
        )
    assert "sensitive" not in str(failure.value)
    assert "secret" not in str(failure.value)


def test_sync_transport_adapter_and_invalid_fetch_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_async(*_args: object, **_kwargs: object) -> FetchResult:
        return FetchResult("https://api.example.com", 200, {}, b"")

    monkeypatch.setattr(secure_transport, "_secure_fetch_async", fake_async)
    policy = secure_transport.FetchPolicy(allowed_hosts=frozenset({"api.example.com"}))
    assert secure_transport.secure_fetch("https://api.example.com", policy=policy).status_code == 200

    digest = _write_spec(tmp_path)
    with pytest.raises(openapi_registration.OpenAPIRegistrationError, match="invalid result"):
        execute_action(
            {**_args(tmp_path, digest), "secret_env": {}},
            fetcher=lambda *_args, **_kwargs: object(),
        )


@pytest.mark.asyncio
async def test_transport_resolution_and_loop_failure_branches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loop = asyncio.get_running_loop()

    async def raises(*_args: object, **_kwargs: object) -> list[tuple[Any, ...]]:
        raise OSError("resolver unavailable")

    monkeypatch.setattr(loop, "getaddrinfo", raises)
    with pytest.raises(secure_transport.SecureFetchError, match="resolution failed"):
        await secure_transport._resolve_public("api.example.com", 443, 1.0)

    async def malformed(*_args: object, **_kwargs: object) -> list[tuple[Any, ...]]:
        return [(0, 0, 0, "", ("not-an-address", 443))]

    monkeypatch.setattr(loop, "getaddrinfo", malformed)
    with pytest.raises(secure_transport.SecureFetchError, match="invalid address"):
        await secure_transport._resolve_public("api.example.com", 443, 1.0)

    async def public(*_args: object, **_kwargs: object) -> list[tuple[Any, ...]]:
        return [(0, 0, 0, "", ("93.184.216.34", 443))]

    monkeypatch.setattr(loop, "getaddrinfo", public)
    assert await secure_transport._resolve_public("api.example.com", 443, 1.0) == "93.184.216.34"

    policy = secure_transport.FetchPolicy(allowed_hosts=frozenset({"api.example.com"}))
    with pytest.raises(RuntimeError, match="event loop"):
        secure_transport.secure_fetch("https://api.example.com", policy=policy)


def test_transport_url_parser_and_literal_host_branches() -> None:
    policy = secure_transport.FetchPolicy(allowed_hosts=frozenset({"api.example.com"}))
    with pytest.raises(secure_transport.SecureFetchError, match="invalid outbound URL"):
        secure_transport._validated_url("https://[broken", policy)

    local_policy = secure_transport.FetchPolicy(allowed_hosts=frozenset({"localhost"}))
    with pytest.raises(secure_transport.SecureFetchError, match="single-label"):
        secure_transport._validated_url("https://localhost", local_policy)

    literal_policy = secure_transport.FetchPolicy(allowed_hosts=frozenset({"127.0.0.1"}))
    with pytest.raises(secure_transport.SecureFetchError, match="globally routable"):
        secure_transport._validated_url("https://127.0.0.1", literal_policy)

    public_policy = secure_transport.FetchPolicy(allowed_hosts=frozenset({"8.8.8.8"}))
    assert secure_transport._validated_url("https://8.8.8.8:8443/x", public_policy)[2] == 8443


def test_remaining_openapi_guard_branches(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    digest = _write_spec(tmp_path)
    base = {**_args(tmp_path, digest), "secret_env": {}}

    with pytest.raises(openapi_registration.OpenAPIRegistrationError, match="non-empty absolute"):
        execute_action({**base, "root": ""})

    linked_root = tmp_path.parent / f"{tmp_path.name}-root-link"
    linked_root.symlink_to(tmp_path, target_is_directory=True)
    try:
        with pytest.raises(openapi_registration.OpenAPIRegistrationError, match="root must not be a symlink"):
            execute_action({**base, "root": str(linked_root)})
    finally:
        linked_root.unlink(missing_ok=True)

    outside = tmp_path.parent / f"{tmp_path.name}-outside.json"
    outside.write_bytes((tmp_path / "inventory.openapi.json").read_bytes())
    try:
        with pytest.raises(openapi_registration.OpenAPIRegistrationError, match="escapes root"):
            execute_action({**base, "spec_path": str(outside)})
    finally:
        outside.unlink(missing_ok=True)

    hardlink = tmp_path / "hardlink.json"
    hardlink.hardlink_to(tmp_path / "inventory.openapi.json")
    with pytest.raises(openapi_registration.OpenAPIRegistrationError, match="unlinked file"):
        execute_action({**base, "spec_path": "hardlink.json"})
    hardlink.unlink()

    malformed = b"openapi: [unterminated"
    (tmp_path / "inventory.openapi.json").write_bytes(malformed)
    with pytest.raises(openapi_registration.OpenAPIRegistrationError, match="valid JSON or YAML"):
        execute_action({**base, "spec_sha256": hashlib.sha256(malformed).hexdigest()})

    monkeypatch.setattr(openapi_registration, "MAX_SPEC_DEPTH", 0)
    with pytest.raises(openapi_registration.OpenAPIRegistrationError, match="nesting limit"):
        openapi_registration._bounded_tree({"nested": {}})
    monkeypatch.setattr(openapi_registration, "MAX_SPEC_DEPTH", 64)

    with pytest.raises(openapi_registration.OpenAPIRegistrationError, match="must be an object"):
        openapi_registration._resolve({}, [], "operation")
    with pytest.raises(openapi_registration.OpenAPIRegistrationError, match="resolve to an object"):
        openapi_registration._resolve({"value": []}, {"$ref": "#/value"}, "operation")
    with pytest.raises(openapi_registration.OpenAPIRegistrationError, match="define paths"):
        openapi_registration._find_operation({}, "operation")

    with pytest.raises(TypeError, match="must be strings"):
        openapi_registration._base_url(1, "api.example.test")
    with pytest.raises(openapi_registration.OpenAPIRegistrationError, match="credentials"):
        openapi_registration._base_url("https://user@api.example.test", "api.example.test")
    with pytest.raises(openapi_registration.OpenAPIRegistrationError, match="explicit DNS host"):
        openapi_registration._base_url("https://*.example.test", "*.example.test")

    assert openapi_registration._scalar(True, "flag") == "true"
    duplicate_parameters = openapi_registration._Operation(
        "/x",
        "GET",
        (
            {"name": "same", "in": "query"},
            {"name": "same", "in": "header"},
        ),
    )
    with pytest.raises(openapi_registration.OpenAPIRegistrationError, match="unique"):
        openapi_registration._request_parts(duplicate_parameters, {})

    monkeypatch.setattr(openapi_registration, "MAX_PARAMETERS", 0)
    with pytest.raises(openapi_registration.OpenAPIRegistrationError, match="32-parameter"):
        openapi_registration._request_parts(
            openapi_registration._Operation("/x", "GET", ({"name": "q", "in": "query"},)),
            {"q": "x"},
        )
    monkeypatch.setattr(openapi_registration, "MAX_PARAMETERS", 32)
    with pytest.raises(openapi_registration.OpenAPIRegistrationError, match="path parameters"):
        openapi_registration._request_parts(openapi_registration._Operation("/{id}", "GET", ()), {})

    with pytest.raises(TypeError, match="secret_env"):
        openapi_registration._secret_headers([])
    with pytest.raises(openapi_registration.OpenAPIRegistrationError, match="16-header"):
        openapi_registration._secret_headers({f"X-{index}": "ENV" for index in range(17)})

    empty_response = FetchResult("https://api.example.test", 204, {}, b"")
    assert openapi_registration._decode_records(empty_response, "", 1) == []
    with pytest.raises(openapi_registration.OpenAPIRegistrationError, match="bounded JSON Pointer"):
        openapi_registration._decode_records(
            FetchResult("https://api.example.test", 200, {"content-type": "application/json"}, b"[]"),
            "invalid",
            1,
        )

    with pytest.raises(TypeError, match="missing required argument"):
        openapi_registration.action_arguments({})
    for field, value, message in (
        ("timeout_seconds", True, "number"),
        ("max_response_bytes", True, "integer"),
        ("max_records", True, "integer"),
        ("validate_only", "yes", "boolean"),
    ):
        with pytest.raises(TypeError, match=message):
            execute_action({**base, field: value})

    digest = _write_spec(tmp_path)

    def transport_failure(*_args: object, **_kwargs: object) -> FetchResult:
        raise secure_transport.SecureFetchError("no route")

    with pytest.raises(openapi_registration.OpenAPIRegistrationError, match="bounded HTTPS query failed"):
        execute_action(
            {**_args(tmp_path, digest), "secret_env": {}},
            fetcher=transport_failure,
        )

