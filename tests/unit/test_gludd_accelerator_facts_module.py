from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest


class _Exit(Exception):
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload


class _Fail(Exception):
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload


def _inventory(location: str, count: int) -> dict[str, object]:
    return {
        "_status": 200,
        "schema_version": 1,
        "total_count": count,
        "available_count": count,
        "resources": [
            {
                "kind": "gpu",
                "location": location,
                "backend": "runtime",
                "model": "observed-model",
                "vendor": "observed-vendor",
                "resource_key": f"{location}:runtime:0",
                "total_count": count,
                "available_count": count,
                "memory_gb": 16.0,
                "source": "observed-source",
                "node": None,
                "partitions": [],
            }
        ],
    }


def _run(
    *,
    scope: str,
    responses: dict[str, dict[str, object]],
) -> tuple[dict[str, object], list[str]]:
    from ansible_collections.general_ludd.agent.plugins.modules import (
        gludd_accelerator_facts as subject,
    )

    calls: list[str] = []

    class FakeAnsibleModule:
        def __init__(self, **_kwargs: object) -> None:
            self.params = {
                "scope": scope,
                "daemon_url": "http://daemon:8000",
                "psk": "secret",
                "timeout": 30,
            }
            self.check_mode = True

        def exit_json(self, **payload: object) -> None:
            raise _Exit(payload)

        def fail_json(self, **payload: object) -> None:
            raise _Fail(payload)

    class FakeClient:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def get(self, path: str, params: object = None) -> dict[str, object]:
            del params
            calls.append(path)
            return responses[path]

    with (
        patch.object(subject, "AnsibleModule", FakeAnsibleModule),
        patch.object(subject, "GluddClient", FakeClient),
        pytest.raises(_Exit) as result,
    ):
        subject.main()
    return result.value.payload, calls


def test_all_scope_exposes_local_and_slurm_as_one_ansible_fact() -> None:
    payload, calls = _run(
        scope="all",
        responses={
            "/admin/hardware/accelerators": _inventory("local", 1),
            "/admin/slurm/hardware": _inventory("slurm", 4),
        },
    )
    facts = payload["ansible_facts"]["gludd_accelerators"]  # type: ignore[index]
    assert payload["changed"] is False
    assert facts["total_count"] == 5
    assert facts["available_count"] == 5
    assert len(facts["resources"]) == 2
    assert calls == ["/admin/hardware/accelerators", "/admin/slurm/hardware"]


@pytest.mark.parametrize(
    ("scope", "path"),
    [
        ("local", "/admin/hardware/accelerators"),
        ("slurm", "/admin/slurm/hardware"),
    ],
)
def test_single_scope_only_queries_requested_control_plane(scope: str, path: str) -> None:
    payload, calls = _run(scope=scope, responses={path: _inventory(scope, 2)})
    facts = payload["ansible_facts"]["gludd_accelerators"]  # type: ignore[index]
    assert facts["total_count"] == 2
    assert calls == [path]


@pytest.mark.parametrize(
    "response",
    [
        {"_status": 200, "_error": "transport failed"},
        {"_status": 503, "detail": "unavailable"},
        {"_status": 200, "schema_version": 2, "total_count": 0, "available_count": 0, "resources": []},
        {"_status": 200, "schema_version": 1, "total_count": -1, "available_count": 0, "resources": []},
        {"_status": 200, "schema_version": 1, "total_count": 0, "available_count": 1, "resources": []},
        {"_status": 200, "schema_version": 1, "total_count": 1, "available_count": 1},
        {"_status": 200, "schema_version": 1, "total_count": True, "available_count": 0, "resources": []},
    ],
)
def test_daemon_or_schema_failure_fails_closed_without_returning_psk(
    response: dict[str, object],
) -> None:
    from ansible_collections.general_ludd.agent.plugins.modules import (
        gludd_accelerator_facts as subject,
    )

    class FakeAnsibleModule:
        def __init__(self, **_kwargs: object) -> None:
            self.params = {
                "scope": "local",
                "daemon_url": "http://daemon:8000",
                "psk": "do-not-leak",
                "timeout": 30,
            }
            self.check_mode = False

        def exit_json(self, **payload: object) -> None:
            raise _Exit(payload)

        def fail_json(self, **payload: object) -> None:
            raise _Fail(payload)

    class FakeClient:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def get(self, _path: str, params: object = None) -> dict[str, object]:
            del params
            return response

    with (
        patch.object(subject, "AnsibleModule", FakeAnsibleModule),
        patch.object(subject, "GluddClient", FakeClient),
        pytest.raises(_Fail) as result,
    ):
        subject.main()
    assert "do-not-leak" not in str(result.value.payload)


def test_role_keeps_inventory_events_visible_without_exposing_the_psk() -> None:
    root = Path(__file__).resolve().parents[2]
    role_task = (
        root
        / "collections/ansible_collections/general_ludd/agent/roles"
        / "discover_accelerators/tasks/main.yml"
    ).read_text(encoding="utf-8")
    module = (
        root
        / "collections/ansible_collections/general_ludd/agent/plugins/modules"
        / "gludd_accelerator_facts.py"
    ).read_text(encoding="utf-8")

    assert "no_log:" not in role_task
    assert 'psk=dict(type="str", default="", no_log=True)' in module
