"""Central helper for marking untrusted values as Ansible-unsafe.

Untrusted data that flows into a playbook's extra-vars (e.g. a model's
``model_response``, an operator-supplied template body, anything an attacker
can influence) must NEVER be re-templated by Ansible. If a value containing
``{{ lookup('pipe', 'id') }}`` reaches a ``shell``/``command``/``template``
task and Ansible re-evaluates it as Jinja, that lookup runs as a shell — a
templating-injection -> RCE.

Ansible's defence is to mark such values "unsafe" so the templating engine
treats them as literal strings and refuses to evaluate embedded Jinja.

This module spans two ansible-core templating models:

* **ansible-core >= 2.19** uses an *opt-in trusted-templating* model. Plain
  strings are NOT templated; only values explicitly trusted via
  ``ansible.template.trust_as_template`` are. ``wrap_var`` still exists and
  returns an ``AnsibleUnsafe`` wrapper, and crucially an unsafe-wrapped value
  is never trusted — so wrapping remains the correct, defensive marker.
* **ansible-core < 2.19** uses the legacy *trust-by-default* model where
  ``wrap_var`` -> ``AnsibleUnsafeText``/``AnsibleUnsafeBytes`` is the only thing
  that stops re-templating.

In both cases ``wrap_var`` is the right call; this module simply provides a
single import site, recurses through containers, and degrades to an identity
function when ansible is not installed (so non-ansible unit tests still run).
"""

from __future__ import annotations

from typing import Any

try:
    from ansible.utils.unsafe_proxy import wrap_var as _wrap_var

    _HAS_WRAP_VAR = True
except Exception:  # pragma: no cover - ansible not installed / API moved
    _HAS_WRAP_VAR = False

    def _wrap_var(value: Any) -> Any:
        return value


def wrap_unsafe(value: Any) -> Any:
    """Mark a single value (and, recursively, its contents) Ansible-unsafe.

    ``wrap_var`` already recurses through dict/list/tuple, but we recurse first
    so the behaviour is identical whether or not ansible is installed and so a
    missing/partial ``wrap_var`` still wraps every leaf. Strings, bytes and
    containers are wrapped; scalars that cannot carry Jinja (int/float/bool/None)
    are returned unchanged.
    """
    if isinstance(value, dict):
        return {k: wrap_unsafe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        wrapped = [wrap_unsafe(v) for v in value]
        return type(value)(wrapped) if isinstance(value, tuple) else wrapped
    if isinstance(value, (str, bytes)):
        return _wrap_var(value)
    return value


def wrap_extravars(extravars: dict[str, Any] | None) -> dict[str, Any] | None:
    """Wrap every value in an extra-vars mapping as Ansible-unsafe.

    Keys are left as-is (they are not templated); only the values — which may
    carry attacker-influenced Jinja — are wrapped. Returns ``None`` unchanged so
    callers can pass through an absent mapping.
    """
    if extravars is None:
        return None
    return {k: wrap_unsafe(v) for k, v in extravars.items()}


def has_wrap_var() -> bool:
    """Whether a real ansible ``wrap_var`` backs :func:`wrap_unsafe`."""
    return _HAS_WRAP_VAR
