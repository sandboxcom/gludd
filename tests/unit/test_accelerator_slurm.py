"""Tests for Slurm GRES accelerator normalization."""

from general_ludd.hardware.accelerator_slurm import parse_slurm_nodes


def test_slurm_parser_subtracts_observed_usage_without_sku_tables() -> None:
    """Available resources derive from scheduler state, not configured GPU keys."""
    resources = parse_slurm_nodes(
        [
            {
                "name": "worker-1",
                "state": "MIXED",
                "gres": "gpu:intel_max_1550:4,tpu:2",
                "gres_used": "gpu:intel_max_1550:1,tpu:2",
                "partitions": ["models"],
            }
        ]
    )

    assert [(item.kind.value, item.total_count, item.available_count) for item in resources] == [
        ("gpu", 4, 3),
        ("tpu", 2, 0),
    ]
    assert {item.model for item in resources} == {"intel_max_1550", "unspecified"}
