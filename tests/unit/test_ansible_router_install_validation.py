"""Fail-closed contracts for the administrative Galaxy install endpoint."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from general_ludd.routers import ansible as ansible_router


def _client() -> TestClient:
    app = FastAPI()
    ansible_router.register(app, {})
    return TestClient(app)


@pytest.mark.parametrize(
    "payload",
    ({}, {"name": 7}, {"name": "community.general", "type": 7}),
)
def test_install_rejects_missing_or_non_string_fields_before_dispatch(
    payload: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called = False

    def forbidden_install(_name: str, _kind: str) -> dict[str, object]:
        nonlocal called
        called = True
        return {}

    monkeypatch.setattr(ansible_router, "install_galaxy", forbidden_install)

    response = _client().post("/admin/ansible/install", json=payload)

    assert response.status_code == 422
    assert called is False


def test_install_maps_domain_validation_failure_to_client_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def invalid_install(_name: str, _kind: str) -> dict[str, object]:
        raise ValueError("unsupported galaxy type")

    monkeypatch.setattr(ansible_router, "install_galaxy", invalid_install)

    response = _client().post(
        "/admin/ansible/install",
        json={"name": "community.general", "type": "invalid"},
    )

    assert response.status_code == 422
    assert response.json() == {"detail": "unsupported galaxy type"}
