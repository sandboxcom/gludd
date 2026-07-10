"""Regression test for CRITICAL template-injection RCE in PromptRegistry.

``PromptRegistry`` previously wrapped a bare :class:`jinja2.Environment`.
The daemon auto-discovers project-local ``.gludd/templates/*.j2`` files and
those SHADOW the global templates by filename (see
``general_ludd.daemon`` template discovery / ``PromptRegistry.refresh``).
A malicious template committed to any target repo gludd is pointed at
(e.g. ``implementation.md.j2``) could therefore use a Server-Side Template
Injection (SSTI) payload such as
``{{ ''.__class__.__mro__[1].__subclasses__() }}`` to walk Python's live
object graph and ultimately achieve remote code execution on the daemon
host on the next dispatch.

The fix swaps the bare ``jinja2.Environment`` for
``jinja2.sandbox.SandboxedEnvironment``, which blocks attribute access to
unsafe/dunder attributes and unsafe callables while rendering ordinary
templates identically.

This test proves:
  1. An SSTI payload is neutralised (raises ``jinja2.exceptions.SecurityError``
     or renders to inert text) rather than executing/enumerating code.
  2. A normal ``{{ variable }}`` substitution still renders correctly, so the
     sandbox introduces no behavioural regression for legitimate templates.
"""

from __future__ import annotations

import pytest
from jinja2.exceptions import SecurityError, TemplateError

from general_ludd.prompts.registry import PromptRegistry


def test_ssti_payload_is_neutralised_not_executed() -> None:
    """A malicious template shadowing a real prompt template must not achieve RCE."""
    registry = PromptRegistry()
    payload = "{{ ''.__class__.__mro__[1].__subclasses__() }}"
    registry.register("implementation.md.j2", payload)

    try:
        result = registry.render("implementation.md.j2")
    except (SecurityError, TemplateError):
        # SandboxedEnvironment blocking the unsafe attribute access is the
        # expected, safe outcome.
        return

    # If it didn't raise, it must NOT have produced the actual subclass list
    # (which would prove the sandbox failed to block the attribute walk).
    assert "<class 'object'>" not in result
    assert "subclasses" not in result.lower()


def test_ssti_popen_payload_is_neutralised_not_executed() -> None:
    """A payload attempting to reach os.popen via cycler globals must not execute."""
    registry = PromptRegistry()
    payload = "{{ cycler.__init__.__globals__.os.popen('id').read() }}"
    registry.register("implementation.md.j2", payload)

    try:
        result = registry.render("implementation.md.j2", cycler=_FakeCycler())
    except (SecurityError, TemplateError):
        return

    # Must not contain shell-command output (e.g. "uid=" from `id`).
    assert "uid=" not in result


class _FakeCycler:
    """Stand-in object with an __init__ attribute, mirroring the classic SSTI gadget."""

    def __init__(self) -> None:
        pass


def test_normal_template_still_renders_correctly() -> None:
    """Sandboxing must not change output for ordinary, legitimate templates."""
    registry = PromptRegistry()
    registry.register("greeting.md.j2", "Hello {{ name }}, you have {{ count }} items.")

    result = registry.render("greeting.md.j2", name="Ada", count=3)

    assert result == "Hello Ada, you have 3 items."


def test_normal_conditional_and_loop_template_still_renders_correctly() -> None:
    """Common Jinja control-flow constructs must still work under the sandbox."""
    registry = PromptRegistry()
    template_text = (
        "{% for item in items %}{{ item }},{% endfor %}"
        "{% if flag %}done{% else %}pending{% endif %}"
    )
    registry.register("loop.md.j2", template_text)

    result = registry.render("loop.md.j2", items=["a", "b", "c"], flag=True)

    assert result == "a,b,c,done"
