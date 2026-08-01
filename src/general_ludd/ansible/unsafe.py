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

import math
from dataclasses import dataclass
from typing import Any

import yaml
from yaml.tokens import (
    AliasToken,
    AnchorToken,
    BlockEndToken,
    BlockMappingStartToken,
    BlockSequenceStartToken,
    DirectiveToken,
    FlowMappingEndToken,
    FlowMappingStartToken,
    FlowSequenceEndToken,
    FlowSequenceStartToken,
    ScalarToken,
    TagToken,
)

try:
    from ansible.utils.unsafe_proxy import wrap_var as _wrap_var

    _HAS_WRAP_VAR = True
except Exception:  # pragma: no cover - ansible not installed / API moved
    _HAS_WRAP_VAR = False

    def _wrap_var(value: Any) -> Any:
        return value


class ExtraVarsValidationError(ValueError):
    """Raised when untrusted Ansible extra-vars violate the strict schema."""


@dataclass(frozen=True, slots=True)
class ExtraVarsLimits:
    """Resource bounds for parsing and validating one extra-vars payload."""

    max_depth: int = 32
    max_items: int = 10_000
    max_string_bytes: int = 1_048_576
    max_bytes_value: int = 1_048_576
    max_total_bytes: int = 4_194_304

    def __post_init__(self) -> None:
        for name in (
            "max_depth",
            "max_items",
            "max_string_bytes",
            "max_bytes_value",
            "max_total_bytes",
        ):
            value = getattr(self, name)
            if type(value) is not int or value < 1:
                raise ValueError(f"{name} must be a positive integer")


DEFAULT_EXTRAVARS_LIMITS = ExtraVarsLimits()


def _scan_yaml_operators(payload: str, limits: ExtraVarsLimits) -> None:
    """Reject dangerous YAML features and excessive nesting before loading."""

    depth = -1
    starts = (
        BlockMappingStartToken,
        BlockSequenceStartToken,
        FlowMappingStartToken,
        FlowSequenceStartToken,
    )
    ends = (BlockEndToken, FlowMappingEndToken, FlowSequenceEndToken)
    try:
        for token in yaml.scan(payload):
            if isinstance(token, starts):
                depth += 1
                if depth > limits.max_depth:
                    raise ExtraVarsValidationError(
                        "extra-vars YAML depth exceeds limit before construction"
                    )
            elif isinstance(token, ends):
                depth -= 1
            if isinstance(token, TagToken):
                raise ExtraVarsValidationError("extra-vars YAML tags are forbidden")
            if isinstance(token, (AnchorToken, AliasToken)):
                raise ExtraVarsValidationError("extra-vars YAML anchors and aliases are forbidden")
            if isinstance(token, DirectiveToken):
                raise ExtraVarsValidationError("extra-vars YAML directives are forbidden")
            if isinstance(token, ScalarToken) and token.style is None and token.value == "<<":
                raise ExtraVarsValidationError("extra-vars YAML merge operator is forbidden")
    except ExtraVarsValidationError:
        raise
    except yaml.YAMLError as exc:
        raise ExtraVarsValidationError("invalid extra-vars YAML") from exc


def validate_extravars(
    extravars: object,
    *,
    limits: ExtraVarsLimits = DEFAULT_EXTRAVARS_LIMITS,
) -> dict[str, Any]:
    """Validate and return a JSON-like, resource-bounded extra-vars mapping.

    Only exact built-in mapping/list/scalar types are accepted. This prevents
    custom constructors, lazy mappings and scalar subclasses from running code
    while Ansible or PyYAML traverses the payload. Container reuse is rejected
    because it represents either a cycle or YAML alias semantics.
    """

    item_count = 0
    total_bytes = 0
    seen_containers: set[int] = set()

    def add_bytes(size: int, path: str) -> None:
        nonlocal total_bytes
        total_bytes += size
        if total_bytes > limits.max_total_bytes:
            raise ExtraVarsValidationError(
                f"extra-vars total bytes exceed limit at {path}"
            )

    def visit(value: object, path: str, depth: int) -> Any:
        nonlocal item_count
        if depth > limits.max_depth:
            raise ExtraVarsValidationError(f"extra-vars depth exceeds limit at {path}")

        value_type = type(value)
        if value is None:
            return value
        if isinstance(value, bool) and value_type is bool:
            return value
        if isinstance(value, int) and value_type is int:
            return value
        if isinstance(value, float) and value_type is float:
            if not math.isfinite(value):
                raise ExtraVarsValidationError(
                    f"extra-vars numbers must be finite at {path}"
                )
            return value
        if isinstance(value, str) and value_type is str:
            encoded_size = len(value.encode("utf-8"))
            if encoded_size > limits.max_string_bytes:
                raise ExtraVarsValidationError(
                    f"extra-vars string exceeds byte limit at {path}"
                )
            add_bytes(encoded_size, path)
            return value
        if isinstance(value, bytes) and value_type is bytes:
            if len(value) > limits.max_bytes_value:
                raise ExtraVarsValidationError(
                    f"extra-vars byte string exceeds limit at {path}"
                )
            add_bytes(len(value), path)
            return value

        if not isinstance(value, (dict, list)) or value_type not in (dict, list):
            raise ExtraVarsValidationError(
                f"unsupported extra-vars structure at {path}: {value_type.__name__}"
            )

        identity = id(value)
        if identity in seen_containers:
            raise ExtraVarsValidationError(
                f"extra-vars alias or cycle is forbidden at {path}"
            )
        seen_containers.add(identity)

        item_count += len(value)
        if item_count > limits.max_items:
            raise ExtraVarsValidationError(f"extra-vars items exceed limit at {path}")

        if isinstance(value, list):
            return [visit(item, f"{path}[{index}]", depth + 1) for index, item in enumerate(value)]

        normalized: dict[str, Any] = {}
        for key, child in value.items():
            if type(key) is not str:
                raise ExtraVarsValidationError(
                    f"extra-vars mapping key must be a string at {path}"
                )
            key_size = len(key.encode("utf-8"))
            if key_size > limits.max_string_bytes:
                raise ExtraVarsValidationError(
                    f"extra-vars string key exceeds byte limit at {path}"
                )
            add_bytes(key_size, path)
            if key == "<<" or key.startswith(("!", "&", "*", "%")):
                raise ExtraVarsValidationError(
                    f"extra-vars YAML operator key is forbidden at {path}"
                )
            child_path = f"{path}.{key}" if path else key
            normalized[key] = visit(child, child_path, depth + 1)
        return normalized

    result = visit(extravars, "$", 0)
    if type(result) is not dict:
        raise ExtraVarsValidationError("extra-vars root must be a mapping")
    return result


def parse_extravars(
    payload: object,
    *,
    limits: ExtraVarsLimits = DEFAULT_EXTRAVARS_LIMITS,
) -> dict[str, Any]:
    """Parse strict YAML/JSON or validate an already-decoded extra-vars map."""

    if type(payload) is dict:
        return validate_extravars(payload, limits=limits)
    if type(payload) is bytes:
        if len(payload) > limits.max_total_bytes:
            raise ExtraVarsValidationError("extra-vars raw bytes exceed total bytes limit")
        try:
            raw = payload.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise ExtraVarsValidationError("extra-vars bytes must be valid UTF-8") from exc
    elif type(payload) is str:
        raw = payload
        if len(raw.encode("utf-8")) > limits.max_total_bytes:
            raise ExtraVarsValidationError("extra-vars raw string exceeds total bytes limit")
    else:
        raise ExtraVarsValidationError(
            f"unsupported extra-vars structure at $: {type(payload).__name__}"
        )

    _scan_yaml_operators(raw, limits)
    try:
        decoded = yaml.safe_load(raw)
    except ExtraVarsValidationError:
        raise
    except yaml.YAMLError as exc:
        raise ExtraVarsValidationError("invalid extra-vars YAML") from exc
    return validate_extravars(decoded, limits=limits)


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
    validated = validate_extravars(extravars)
    return {k: wrap_unsafe(v) for k, v in validated.items()}


def has_wrap_var() -> bool:
    """Whether a real ansible ``wrap_var`` backs :func:`wrap_unsafe`."""
    return _HAS_WRAP_VAR
