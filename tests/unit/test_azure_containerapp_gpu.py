"""Right-sizing contracts for Azure Container Apps serverless GPU models."""

from __future__ import annotations

from dataclasses import asdict
from typing import cast

import pytest

from general_ludd.infra.azure_containerapp_gpu import (
    A100_PROFILE,
    T4_PROFILE,
    AzureContainerAppGPUProfile,
    AzureContainerAppGPUUnavailable,
    ModelServingRequirement,
    SizingTrace,
    select_smallest_sufficient_profile,
)

QWEN_REVISION = "7ae557604adf67be50417f59c2c2f167def9a775"


def _requirement(**overrides: object) -> ModelServingRequirement:
    values: dict[str, object] = {
        "model_id": "Qwen/Qwen2.5-0.5B-Instruct",
        "revision": QWEN_REVISION,
        "parameter_count": 494_032_768,
        "weight_bits": 16,
        "kv_cache_mib": 2_048,
        "runtime_overhead_mib": 3_072,
    }
    values.update(overrides)
    return ModelServingRequirement(
        model_id=cast(str, values["model_id"]),
        revision=cast(str, values["revision"]),
        parameter_count=cast(int, values["parameter_count"]),
        weight_bits=cast(int, values["weight_bits"]),
        kv_cache_mib=cast(int, values["kv_cache_mib"]),
        runtime_overhead_mib=cast(int, values["runtime_overhead_mib"]),
    )


def test_qwen_half_billion_is_right_sized_to_t4() -> None:
    selection = select_smallest_sufficient_profile(_requirement())

    assert selection.profile == T4_PROFILE
    assert selection.required_vram_mib < T4_PROFILE.usable_vram_mib
    assert selection.headroom_mib == (
        T4_PROFILE.usable_vram_mib - selection.required_vram_mib
    )


def test_bfloat16_seven_billion_requires_a100() -> None:
    selection = select_smallest_sufficient_profile(
        _requirement(
            model_id="example/seven-billion",
            parameter_count=7_000_000_000,
            kv_cache_mib=4_096,
        )
    )

    assert selection.profile == A100_PROFILE
    assert selection.required_vram_mib > T4_PROFILE.usable_vram_mib


def test_four_bit_seven_billion_stays_on_t4() -> None:
    selection = select_smallest_sufficient_profile(
        _requirement(
            model_id="example/seven-billion-4bit",
            parameter_count=7_000_000_000,
            weight_bits=4,
        )
    )

    assert selection.profile == T4_PROFILE


def test_selector_accepts_extensible_hardware_inventory_without_named_keys() -> None:
    future_profile = AzureContainerAppGPUProfile(
        name="inventory-profile",
        workload_profile_name="gpu-inventory-01",
        workload_profile_type="provider/Profile-vNext",
        gpu_vram_mib=48 * 1024,
        usable_vram_mib=44 * 1024,
        cpu_cores=16,
        memory_gib=128,
    )

    selection = select_smallest_sufficient_profile(
        _requirement(parameter_count=12_000_000_000),
        hardware_profiles=(future_profile,),
    )

    assert selection.profile is future_profile


def test_model_too_large_for_a100_fails_closed() -> None:
    with pytest.raises(AzureContainerAppGPUUnavailable, match="exceeds A100"):
        select_smallest_sufficient_profile(
            _requirement(parameter_count=80_000_000_000)
        )


def test_missing_exact_right_sized_profile_does_not_silently_oversize() -> None:
    with pytest.raises(AzureContainerAppGPUUnavailable, match="right-sized T4"):
        select_smallest_sufficient_profile(
            _requirement(),
            available_profile_types={A100_PROFILE.workload_profile_type},
        )


def test_missing_required_a100_does_not_underprovision() -> None:
    with pytest.raises(AzureContainerAppGPUUnavailable, match="required A100"):
        select_smallest_sufficient_profile(
            _requirement(parameter_count=7_000_000_000, kv_cache_mib=4_096),
            available_profile_types={T4_PROFILE.workload_profile_type},
        )


def test_profile_contracts_match_azure_serverless_gpu_shapes() -> None:
    assert asdict(T4_PROFILE) == {
        "name": "T4",
        "workload_profile_name": "gpu-t4",
        "workload_profile_type": "Consumption-GPU-NC8as-T4",
        "gpu_vram_mib": 16 * 1024,
        "usable_vram_mib": 14_745,
        "cpu_cores": 8,
        "memory_gib": 56,
    }
    assert asdict(A100_PROFILE) == {
        "name": "A100",
        "workload_profile_name": "gpu-a100",
        "workload_profile_type": "Consumption-GPU-NC24-A100",
        "gpu_vram_mib": 80 * 1024,
        "usable_vram_mib": 73_728,
        "cpu_cores": 24,
        "memory_gib": 220,
    }


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"model_id": ""}, "model_id"),
        ({"model_id": "private\nmodel"}, "model_id"),
        ({"revision": "main"}, "revision"),
        ({"revision": QWEN_REVISION.upper()}, "revision"),
        ({"parameter_count": 0}, "parameter_count"),
        ({"weight_bits": 3}, "weight_bits"),
        ({"kv_cache_mib": -1}, "kv_cache_mib"),
        ({"runtime_overhead_mib": -1}, "runtime_overhead_mib"),
    ],
)
def test_invalid_model_sizing_input_is_rejected(
    overrides: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        _requirement(**overrides)


def test_sizing_emits_content_free_started_and_selected_traces() -> None:
    traces: list[SizingTrace] = []

    selection = select_smallest_sufficient_profile(
        _requirement(),
        trace_sink=traces.append,
    )

    assert [trace.phase for trace in traces] == ["sizing_started", "sizing_selected"]
    assert traces[-1].required_vram_mib == selection.required_vram_mib
    assert traces[-1].profile_name == "T4"
    rendered = repr(traces)
    assert "Qwen" not in rendered
    assert QWEN_REVISION not in rendered


def test_refusal_is_traced_without_model_identity() -> None:
    traces: list[SizingTrace] = []

    with pytest.raises(AzureContainerAppGPUUnavailable, match="right-sized T4"):
        select_smallest_sufficient_profile(
            _requirement(),
            available_profile_types=set(),
            trace_sink=traces.append,
        )

    assert [trace.phase for trace in traces] == ["sizing_started", "sizing_refused"]
    assert traces[-1].reason == "profile_unavailable"
    assert "Qwen" not in repr(traces)


def test_trace_sink_failure_fails_closed() -> None:
    def broken_sink(_trace: SizingTrace) -> None:
        raise RuntimeError("sink unavailable")

    with pytest.raises(RuntimeError, match="sizing trace publication failed"):
        select_smallest_sufficient_profile(
            _requirement(),
            trace_sink=broken_sink,
        )
