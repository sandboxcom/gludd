"""Contracts for constructor-excluded dataclass fields with derived state."""

from __future__ import annotations

import dataclasses
import inspect

import pytest

from general_ludd.algorithms.pake import OPAQUEConfig
from general_ludd.experiments.ab_engine import TrafficSplitter
from general_ludd.network.stream_mux import StreamState


def test_opaque_hash_has_a_safe_noninjectable_default() -> None:
    hash_field = next(
        item for item in dataclasses.fields(OPAQUEConfig) if item.name == "hash_name"
    )
    constructor = inspect.signature(OPAQUEConfig)

    assert hash_field.init is False
    assert hash_field.default is not dataclasses.MISSING
    assert hash_field.default == "sha256"
    assert "hash_name" not in constructor.parameters
    with pytest.raises(TypeError, match="hash_name"):
        constructor.bind(hash_name="md5")

    replaced = dataclasses.replace(OPAQUEConfig(curve="P-256"), curve="P-384")
    assert replaced.curve == "P-384"
    assert replaced.hash_name == "sha384"


def test_traffic_boundaries_have_a_per_instance_default() -> None:
    cumulative_field = next(
        item
        for item in dataclasses.fields(TrafficSplitter)
        if item.name == "_cumulative"
    )

    assert cumulative_field.init is False
    assert cumulative_field.default_factory is not dataclasses.MISSING
    assert cumulative_field.default_factory is list

    left = TrafficSplitter({"control": 0.5, "treatment": 0.5})
    right = TrafficSplitter({"control": 0.5, "treatment": 0.5})
    assert left._cumulative is not right._cumulative
    assert left._cumulative == [("control", 0.5), ("treatment", 1.0)]


def test_stream_flow_windows_have_independent_sized_defaults() -> None:
    flow_fields = {
        item.name: item
        for item in dataclasses.fields(StreamState)
        if item.name in {"_send_flow", "_recv_flow"}
    }

    assert set(flow_fields) == {"_send_flow", "_recv_flow"}
    assert all(item.init is False for item in flow_fields.values())
    assert all(
        item.default_factory is not dataclasses.MISSING for item in flow_fields.values()
    )

    left = StreamState(stream_id=1, send_window=64, recv_window=32)
    right = StreamState(stream_id=3, send_window=64, recv_window=32)
    assert left._send_flow is not right._send_flow
    assert left._recv_flow is not right._recv_flow
    assert left._send_flow.available() == 64
    assert left._recv_flow.available() == 32
