"""Ansible templating exposed for skills and prompts.

Two distinct rendering paths live here, and the distinction is a security
boundary — do not blur it:

* :meth:`AnsibleTemplater.render` is the **trusted-only** path. It wraps
  ``CoreAnsibleRunner.render_template`` -> ansible's ``Templar.template()``,
  which exposes the FULL Ansible plugin surface (filters, tests, and lookups).
  A lookup such as ``{{ lookup('pipe', 'id') }}`` executes a shell. Only ever
  feed this path templates that the operator authored.

* :meth:`AnsibleTemplater.render_sandboxed` is the **untrusted** path. It
  renders the body in a ``jinja2.sandbox.SandboxedEnvironment`` with
  ``StrictUndefined`` and an empty global namespace (no ``lookup``/``range``/
  plugin surface), and every variable value is marked Ansible-unsafe so a
  payload smuggled in via a *variable value* renders literally instead of being
  re-evaluated. It fails CLOSED: any ``SecurityError`` / undefined / syntax
  error raises :class:`TemplateRenderError` rather than leaking a traceback.

The network-exposed ``POST /admin/ansible/render`` endpoint MUST use the
sandboxed path; the trusted path is reserved for operator-authored prompts and
skills running in-process.
"""

from __future__ import annotations

from typing import Any

from general_ludd.ansible.core_runner import CoreAnsibleRunner
from general_ludd.ansible.unsafe import wrap_unsafe


class TemplateRenderError(Exception):
    """Raised when a sandboxed render is rejected (security / undefined / syntax).

    Carries only a short, non-sensitive reason — never a Jinja traceback — so
    the API can fail closed with an HTTP 400 and no internal detail leak.
    """


class AnsibleTemplater:
    def __init__(self, extra_vars: dict[str, Any] | None = None) -> None:
        self._extra_vars = extra_vars or {}
        self._runner = CoreAnsibleRunner()

    def render(self, template: str, **kwargs: Any) -> str:
        """TRUSTED-ONLY full-Templar render (full lookup/plugin surface).

        Never call this on an attacker-controlled template body — use
        :meth:`render_sandboxed` for that.
        """
        merged = dict(self._extra_vars)
        merged.update(kwargs)
        return self._runner.render_template(template, variables=merged)

    def render_sandboxed(self, template: str, **kwargs: Any) -> str:
        """Render an UNTRUSTED template body, failing closed on any abuse.

        - No ``lookup``/plugin surface (SandboxedEnvironment + empty globals).
        - ``StrictUndefined`` so an unknown name is rejected, not silently "".
        - Dunder / attribute-escape attempts raise ``SecurityError`` -> 400.
        - Every variable value is wrapped Ansible-unsafe so a payload smuggled
          through a *value* (e.g. ``{"x": "{{ 7*7 }}"}``) is emitted literally.
        """
        from jinja2 import StrictUndefined
        from jinja2.exceptions import SecurityError, TemplateError
        from jinja2.sandbox import SandboxedEnvironment

        merged = dict(self._extra_vars)
        merged.update(kwargs)
        # Wrap values unsafe: a value that itself contains Jinja must render as
        # a literal string, never be re-evaluated.
        safe_vars = {k: wrap_unsafe(v) for k, v in merged.items()}

        env = SandboxedEnvironment(undefined=StrictUndefined, autoescape=False)
        # Strip the global namespace so range/dict/lipsum/cycler and any other
        # callable cannot be reached from inside the template.
        env.globals.clear()

        try:
            compiled = env.from_string(template)
            return compiled.render(safe_vars)
        except SecurityError as exc:
            raise TemplateRenderError(f"template rejected: {exc.__class__.__name__}") from exc
        except TemplateError as exc:
            # Undefined, syntax, type errors during render — fail closed.
            raise TemplateRenderError(f"template rejected: {exc.__class__.__name__}") from exc
        except Exception as exc:  # pragma: no cover - defensive catch-all
            raise TemplateRenderError(f"template rejected: {exc.__class__.__name__}") from exc

    def resolve_fact(self, fact_name: str, host: str = "localhost") -> Any:
        return self._runner.resolve_variable(fact_name, host=host)
