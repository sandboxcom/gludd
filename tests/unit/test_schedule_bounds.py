"""Regression tests for /api/schedule input-size caps (DoS hardening).

Uncapped ``items``, ``depends_on``, and ``resources`` lists on
``ScheduleRequest``/``WorkItemRequest`` allowed topological-work amplification.
Caps: items<=1000, depends_on<=100, resources<=100 — all return 422 on excess.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from general_ludd.routers.schedule import ScheduleRequest, WorkItemRequest, register


def _item(id: str = "x", depends_on: list[str] | None = None, resources: list[str] | None = None) -> dict:
    result: dict = {"id": id}
    if depends_on is not None:
        result["depends_on"] = depends_on
    if resources is not None:
        result["resources"] = resources
    return result


class TestScheduleRequestItemsCap:
    def test_exactly_at_limit_is_accepted(self):
        payload = {"items": [_item(id=str(i)) for i in range(1000)]}
        req = ScheduleRequest.model_validate(payload)
        assert len(req.items) == 1000

    def test_one_over_limit_raises_validation_error(self):
        payload = {"items": [_item(id=str(i)) for i in range(1001)]}
        with pytest.raises(ValidationError):
            ScheduleRequest.model_validate(payload)

    def test_empty_list_is_accepted(self):
        req = ScheduleRequest.model_validate({"items": []})
        assert req.items == []


class TestWorkItemDependsOnCap:
    def test_exactly_at_limit_is_accepted(self):
        item = WorkItemRequest.model_validate(
            {"id": "task", "depends_on": [str(i) for i in range(100)]}
        )
        assert len(item.depends_on) == 100

    def test_one_over_limit_raises_validation_error(self):
        with pytest.raises(ValidationError):
            WorkItemRequest.model_validate(
                {"id": "task", "depends_on": [str(i) for i in range(101)]}
            )


class TestWorkItemResourcesCap:
    def test_exactly_at_limit_is_accepted(self):
        item = WorkItemRequest.model_validate(
            {"id": "task", "resources": [f"res-{i}" for i in range(100)]}
        )
        assert len(item.resources) == 100

    def test_one_over_limit_raises_validation_error(self):
        with pytest.raises(ValidationError):
            WorkItemRequest.model_validate(
                {"id": "task", "resources": [f"res-{i}" for i in range(101)]}
            )


def test_schedule_endpoint_returns_ordered_batches_and_records_plan() -> None:
    app = FastAPI()
    register(app, {})
    client = TestClient(app)

    response = client.post(
        "/api/schedule",
        json={
            "items": [
                {"id": "prepare", "resources": ["gpu"]},
                {"id": "deploy", "depends_on": ["prepare"]},
            ]
        },
    )

    assert response.status_code == 200
    assert response.json() == {"batches": [["prepare"], ["deploy"]]}
    assert app.state._schedule_last_plan["batches"] == [["prepare"], ["deploy"]]
