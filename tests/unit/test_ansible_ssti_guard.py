"""Validate extra_vars against Jinja2 SSTI injection in Ansible playbooks."""

from __future__ import annotations

import pytest

from general_ludd.execution.engine import validate_extra_vars_safe


class TestValidateExtraVarsSafe:
    def test_benign_vars_pass(self) -> None:
        validate_extra_vars_safe({"name": "hello", "count": 42, "flag": True})

    def test_empty_vars_pass(self) -> None:
        validate_extra_vars_safe({})

    def test_none_value_passes(self) -> None:
        validate_extra_vars_safe({"key": None})

    def test_normal_text_with_curly_braces_passes(self) -> None:
        validate_extra_vars_safe({"code": "function foo() { return x; }"})
        validate_extra_vars_safe({"json": '{"key": "value"}'})

    def test_double_curly_expression_blocked(self) -> None:
        with pytest.raises(ValueError, match="SSTI"):
            validate_extra_vars_safe({"x": "{{7*7}}"})

    def test_block_syntax_blocked(self) -> None:
        with pytest.raises(ValueError, match="SSTI"):
            validate_extra_vars_safe({"x": "{% import os %}"})

    def test_comment_syntax_blocked(self) -> None:
        with pytest.raises(ValueError, match="SSTI"):
            validate_extra_vars_safe({"x": "{# secret #}"})

    def test_nested_injection_in_nested_dict_blocked(self) -> None:
        with pytest.raises(ValueError, match="SSTI"):
            validate_extra_vars_safe({"outer": {"inner": "{{config}}"}})

    def test_injection_in_list_value_blocked(self) -> None:
        with pytest.raises(ValueError, match="SSTI"):
            validate_extra_vars_safe({"items": ["safe", "{{7*7}}"]})

    def test_allow_jinja2_flag_bypasses_validation(self) -> None:
        validate_extra_vars_safe({"x": "{{7*7}}"}, allow_jinja2_in_extravars=True)

    def test_lookup_injection_blocked(self) -> None:
        with pytest.raises(ValueError, match="SSTI"):
            validate_extra_vars_safe({"x": "{{ lookup('pipe', 'id') }}"})

    def test_deeply_nested_injection_blocked(self) -> None:
        with pytest.raises(ValueError, match="SSTI"):
            validate_extra_vars_safe(
                {"level1": {"level2": [{"level3": "{{7*7}}"}]}}
            )

    def test_mixed_benign_and_malicious_blocked(self) -> None:
        with pytest.raises(ValueError, match="SSTI"):
            validate_extra_vars_safe(
                {"safe": "hello", "danger": "{% if True %}yes{% endif %}"}
            )

    def test_multiline_injection_blocked(self) -> None:
        with pytest.raises(ValueError, match="SSTI"):
            validate_extra_vars_safe(
                {"x": "{% for i in range(10) %}\n{{ i }}\n{% endfor %}"}
            )
