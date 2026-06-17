"""Adversarial unit tests for the skill body renderer.

Target: src/general_ludd/skills/renderer.py

Covers: StrictUndefined error wrapping, the (non-)escaping behavior of
``autoescape=False``, partial/empty templates, and the security shape of
Jinja2 ``from_string`` on the template body (SSTI surface).
"""

from __future__ import annotations

import pytest

from general_ludd.skills.renderer import SkillRenderError, render_skill

# --------------------------------------------------------------------------
# Happy path
# --------------------------------------------------------------------------

def test_simple_substitution() -> None:
    assert render_skill("Hello {{ name }}", {"name": "world"}) == "Hello world"


def test_no_variables_plain_text_passes_through() -> None:
    assert render_skill("just text") == "just text"


def test_none_variables_is_allowed_for_var_free_template() -> None:
    assert render_skill("no vars here", None) == "no vars here"


def test_empty_body_renders_empty_string() -> None:
    assert render_skill("", {"x": 1}) == ""


def test_multiple_variables() -> None:
    out = render_skill("{{ a }}-{{ b }}-{{ a }}", {"a": "1", "b": "2"})
    assert out == "1-2-1"


def test_jinja_control_flow_partial_template() -> None:
    body = "{% for i in items %}{{ i }},{% endfor %}"
    assert render_skill(body, {"items": [1, 2, 3]}) == "1,2,3,"


def test_conditional_block() -> None:
    body = "{% if flag %}YES{% else %}NO{% endif %}"
    assert render_skill(body, {"flag": False}) == "NO"


def test_non_string_variable_values_are_stringified() -> None:
    assert render_skill("n={{ n }}", {"n": 42}) == "n=42"


def test_filters_work() -> None:
    assert render_skill("{{ name | upper }}", {"name": "abc"}) == "ABC"


# --------------------------------------------------------------------------
# StrictUndefined -> SkillRenderError
# --------------------------------------------------------------------------

def test_undefined_variable_raises_skill_render_error() -> None:
    with pytest.raises(SkillRenderError):
        render_skill("Hello {{ missing }}", {})


def test_undefined_variable_with_no_dict_raises() -> None:
    with pytest.raises(SkillRenderError):
        render_skill("{{ missing }}")


def test_skill_render_error_is_a_valueerror() -> None:
    # Documented subclassing: callers may catch ValueError.
    assert issubclass(SkillRenderError, ValueError)


def test_undefined_error_message_names_the_variable() -> None:
    with pytest.raises(SkillRenderError) as ei:
        render_skill("{{ secret_thing }}", {})
    assert "undefined variable" in str(ei.value).lower()


def test_undefined_attribute_access_raises() -> None:
    # StrictUndefined also fires on attribute access of a defined-but-None
    # path; here 'obj' is missing entirely.
    with pytest.raises(SkillRenderError):
        render_skill("{{ obj.attr }}", {})


def test_defined_var_missing_attribute_raises() -> None:
    with pytest.raises(SkillRenderError):
        render_skill("{{ d.nope }}", {"d": {}})


def test_partial_render_before_undefined_still_raises() -> None:
    # Even though 'a' is defined, the undefined 'b' must abort the whole render.
    with pytest.raises(SkillRenderError):
        render_skill("{{ a }} and {{ b }}", {"a": "x"})


# --------------------------------------------------------------------------
# autoescape=False — content is NOT HTML-escaped (documented behavior)
# --------------------------------------------------------------------------

def test_html_in_variable_is_not_escaped() -> None:
    # autoescape=False: special chars pass through verbatim. This is the
    # current behavior; for a prompt/skill body this is intentional, but the
    # test pins it so an accidental flip to autoescape=True is caught.
    out = render_skill("{{ v }}", {"v": "<b>&\"'</b>"})
    assert out == "<b>&\"'</b>"


def test_literal_braces_in_value_are_not_re_evaluated() -> None:
    # A variable VALUE that itself looks like Jinja syntax is treated as
    # data, NOT re-rendered. No second-pass template injection via values.
    out = render_skill("{{ v }}", {"v": "{{ 7 * 7 }}"})
    assert out == "{{ 7 * 7 }}"


def test_value_with_statement_syntax_is_inert() -> None:
    out = render_skill("X={{ v }}", {"v": "{% for x in y %}"})
    assert out == "X={% for x in y %}"


# --------------------------------------------------------------------------
# SSTI surface: the BODY is attacker-evaluable (from_string on body).
# These pin the current behavior so the trust boundary is explicit.
# --------------------------------------------------------------------------

def test_body_arithmetic_expression_is_evaluated() -> None:
    # The body is compiled+evaluated as a Jinja template. If body is ever
    # attacker-controlled this is an SSTI vector; the skill contract assumes
    # the BODY is trusted and only VALUES are untrusted.
    assert render_skill("{{ 6 * 7 }}", {}) == "42"


def test_body_cannot_access_python_builtins_via_value_objects() -> None:
    # FIXED: SandboxedEnvironment is now used. Dunder attribute traversal
    # (the classic SSTI gadget chain) is blocked and raises SkillRenderError.
    body = "{{ x.__class__.__name__ }}"
    with pytest.raises(SkillRenderError):
        render_skill(body, {"x": "s"})


def test_body_rendering_is_sandboxed() -> None:
    # FIXED: render_skill now uses SandboxedEnvironment, which blocks access
    # to dunder internals and raises SkillRenderError on any such attempt.
    with pytest.raises(SkillRenderError):
        render_skill("{{ x.__class__ }}", {"x": "s"})


# --------------------------------------------------------------------------
# Misc robustness
# --------------------------------------------------------------------------

def test_extra_unused_variables_are_ignored() -> None:
    assert render_skill("{{ a }}", {"a": "1", "unused": "2"}) == "1"


def test_whitespace_control_works() -> None:
    body = "{%- if True -%} v {%- endif -%}"
    assert render_skill(body, {}) == "v"


def test_repeated_render_is_pure_no_shared_state() -> None:
    assert render_skill("{{ a }}", {"a": "1"}) == "1"
    assert render_skill("{{ a }}", {"a": "2"}) == "2"


def test_variables_dict_is_not_mutated() -> None:
    v = {"a": "1"}
    render_skill("{{ a }}", v)
    assert v == {"a": "1"}
