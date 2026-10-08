"""Behavioral structure for Lua and C Wireshark dissector generation."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
ROLE = (
    ROOT
    / "collections/ansible_collections/general_ludd/networking/roles/networking"
)


def _tasks() -> list[dict[str, Any]]:
    loaded = yaml.safe_load((ROLE / "tasks/dissector_create.yml").read_text(encoding="utf-8"))
    assert isinstance(loaded, list)
    return loaded


def test_c_dissector_is_rendered_instead_of_reporting_placeholder_success() -> None:
    source = (ROLE / "tasks/dissector_create.yml").read_text(encoding="utf-8")
    tasks = _tasks()
    generation = next(task for task in tasks if task["name"] == "Generate Wireshark dissector")
    block = generation["block"]

    assert "placeholder" not in source.casefold()
    assert "rescue" not in generation
    c_task = next(task for task in block if task["name"] == "Generate C dissector from template")
    assert c_task["ansible.builtin.template"]["src"] == "dissector_template.c.j2"
    assert c_task["ansible.builtin.template"]["dest"] == "{{ _dc_output_file }}"
    assert c_task["when"] == ["networking__dissector_language == 'c'"]


def test_c_template_registers_fields_parser_and_bounded_port() -> None:
    template = (ROLE / "templates/dissector_template.c.j2").read_text(encoding="utf-8")

    for required in (
        "proto_register_protocol",
        "proto_register_field_array",
        "create_dissector_handle",
        "dissector_add_uint_with_preference",
        "tvb_captured_length",
        "networking__dissector_fields",
        "networking__dissector_port",
    ):
        assert required in template
    assert "system(" not in template
    assert "popen(" not in template


def test_artifact_does_not_change_only_because_wall_clock_advanced() -> None:
    source = (ROLE / "tasks/dissector_create.yml").read_text(encoding="utf-8")

    assert "lookup('pipe'" not in source
    assert "_dc_timestamp" not in source
