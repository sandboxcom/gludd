"""Security boundary tests for strict Ansible extra-vars parsing."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from general_ludd.ansible.runner import AnsibleRunnerAdapter
from general_ludd.ansible.templating import AnsibleTemplater, TemplateRenderError
from general_ludd.ansible.unsafe import (
    ExtraVarsLimits,
    ExtraVarsValidationError,
    parse_extravars,
    validate_extravars,
    wrap_extravars,
)


def test_safe_scalar_list_and_map_values_round_trip() -> None:
    raw = {
        "name": "gludd",
        "enabled": True,
        "count": 3,
        "ratio": 0.5,
        "nothing": None,
        "payload": b"safe-bytes",
        "nested": {"regions": ["eastus", "westus2"]},
    }

    assert validate_extravars(raw) == raw
    assert parse_extravars(raw) == raw


@pytest.mark.parametrize(
    "raw",
    [
        "!!python/object/apply:os.system ['id']",
        "value: &shared [one, two]\ncopy: *shared",
        "base: &base {enabled: true}\nmerged: {<<: *base}",
        "%TAG ! tag:example.invalid,2026:\n---\nvalue: !thing payload",
    ],
)
def test_raw_yaml_tags_anchors_aliases_and_merge_operators_are_denied(raw: str) -> None:
    with pytest.raises(ExtraVarsValidationError, match="YAML"):
        parse_extravars(raw)


def test_safe_yaml_and_json_objects_parse_to_plain_types() -> None:
    assert parse_extravars('{"name": "gludd", "workers": 2}') == {
        "name": "gludd",
        "workers": 2,
    }


def test_raw_depth_limit_is_enforced_before_object_construction() -> None:
    raw = "value: " + "[" * 4 + "0" + "]" * 4

    with (
        patch("general_ludd.ansible.unsafe.yaml.safe_load") as safe_load,
        pytest.raises(ExtraVarsValidationError, match="depth"),
    ):
        parse_extravars(raw, limits=ExtraVarsLimits(max_depth=3))

    safe_load.assert_not_called()


@pytest.mark.parametrize("raw", [b"\xff", "[not a mapping]", "unterminated: ["])
def test_invalid_encoding_syntax_and_non_mapping_roots_are_denied(
    raw: str | bytes,
) -> None:
    with pytest.raises(ExtraVarsValidationError):
        parse_extravars(raw)
    assert parse_extravars("name: gludd\nregions:\n  - eastus\n  - westus2\n") == {
        "name": "gludd",
        "regions": ["eastus", "westus2"],
    }


@dataclass
class _UnknownStructure:
    command: str


class _TruthinessTrap(dict[str, object]):
    def __bool__(self) -> bool:
        raise AssertionError("untrusted mapping truthiness must not execute")


@pytest.mark.parametrize(
    "value",
    [
        {"unknown": _UnknownStructure("id")},
        {"tuple": ("not", "a", "list")},
        {"set": {"not", "a", "list"}},
        {1: "non-string mapping key"},
    ],
)
def test_unknown_structures_and_non_string_keys_are_denied(value: object) -> None:
    with pytest.raises(ExtraVarsValidationError):
        validate_extravars(value)


def test_depth_item_string_byte_and_total_byte_limits_are_enforced() -> None:
    with pytest.raises(ExtraVarsValidationError, match="depth"):
        validate_extravars(
            {"a": {"b": {"c": "too deep"}}},
            limits=ExtraVarsLimits(max_depth=2),
        )
    with pytest.raises(ExtraVarsValidationError, match="items"):
        validate_extravars(
            {"items": [1, 2, 3]},
            limits=ExtraVarsLimits(max_items=3),
        )
    with pytest.raises(ExtraVarsValidationError, match="string"):
        validate_extravars(
            {"text": "four"},
            limits=ExtraVarsLimits(max_string_bytes=3),
        )
    with pytest.raises(ExtraVarsValidationError, match="byte string"):
        validate_extravars(
            {"blob": b"four"},
            limits=ExtraVarsLimits(max_bytes_value=3),
        )
    with pytest.raises(ExtraVarsValidationError, match="total bytes"):
        validate_extravars(
            {"one": "123", "two": "456"},
            limits=ExtraVarsLimits(max_total_bytes=5),
        )


def test_cycles_and_shared_container_aliases_are_denied() -> None:
    cyclic: dict[str, object] = {}
    cyclic["self"] = cyclic
    with pytest.raises(ExtraVarsValidationError, match=r"alias|cycle"):
        validate_extravars(cyclic)

    shared = ["value"]
    with pytest.raises(ExtraVarsValidationError, match=r"alias|cycle"):
        validate_extravars({"first": shared, "second": shared})


def test_wrapping_rejects_invalid_input_before_ansible_receives_it() -> None:
    with pytest.raises(ExtraVarsValidationError, match="unsupported"):
        wrap_extravars({"payload": object()})


def test_write_vars_validates_before_creating_files(tmp_path: Path) -> None:
    adapter = AnsibleRunnerAdapter(private_data_dir=str(tmp_path))

    with pytest.raises(ExtraVarsValidationError, match="unsupported"):
        adapter.write_vars("JOB-STRICT", {"payload": object()})

    assert not (tmp_path / "JOB-STRICT").exists()


def test_adapter_does_not_execute_untrusted_mapping_truthiness(tmp_path: Path) -> None:
    adapter = AnsibleRunnerAdapter(private_data_dir=str(tmp_path))
    core_runner = MagicMock()
    core_runner.run_playbook.side_effect = ExtraVarsValidationError(
        "unsupported extra-vars structure"
    )
    adapter._core_runner = core_runner

    result = adapter.run_playbook("noop.yml", extravars=_TruthinessTrap(value=1))

    assert result["status"] == "failed"
    assert "unsupported extra-vars structure" in result["error"]
    core_runner.run_playbook.assert_called_once()


def test_templater_rejects_invalid_extra_vars_before_rendering() -> None:
    templater = AnsibleTemplater(extra_vars={"payload": object()})
    with pytest.raises(ExtraVarsValidationError, match="unsupported"):
        templater.render("{{ payload }}")
    with pytest.raises(TemplateRenderError, match="ExtraVarsValidationError"):
        templater.render_sandboxed("{{ payload }}")

    kwargs_templater = AnsibleTemplater()
    with pytest.raises(ExtraVarsValidationError, match="unsupported"):
        kwargs_templater.render("{{ payload }}", payload=object())
    with pytest.raises(TemplateRenderError, match="ExtraVarsValidationError"):
        kwargs_templater.render_sandboxed("{{ payload }}", payload=object())


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_numbers_are_denied(value: float) -> None:
    with pytest.raises(ExtraVarsValidationError, match="finite"):
        validate_extravars({"value": value})
