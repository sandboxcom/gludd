"""Build bounded command plans for governance lookup roles."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from ansible.errors import AnsibleFilterError
from ansible.module_utils.parsing.convert_bool import boolean

_MAX_ARGUMENT_LENGTH = 4096
_MAX_VALUE_LENGTH = 256
_MAX_ARGUMENTS = 16


@dataclass(frozen=True)
class _ValueArgument:
    flag: str
    variable: str
    required: bool = False
    exact_length: int | None = None
    allowed: tuple[str, ...] = ()
    omit_when_flags: tuple[str, ...] = ()


@dataclass(frozen=True)
class _FlagArgument:
    flag: str
    variable: str


@dataclass(frozen=True)
class _Profile:
    filename: str
    values: tuple[_ValueArgument, ...]
    flags: tuple[_FlagArgument, ...] = ()
    require_any: tuple[str, ...] = ()


_PROFILES = {
    "civic_service_finder": _Profile(
        "civic_services.py",
        values=(
            _ValueArgument("--country", "country", exact_length=2),
            _ValueArgument("--category", "category"),
        ),
        flags=(
            _FlagArgument("--list-countries", "list_countries"),
            _FlagArgument("--list-categories", "list_categories"),
        ),
        require_any=("country", "list_countries", "list_categories"),
    ),
    "conflicts_treaties_lookup": _Profile(
        "conflicts_treaties.py",
        values=(
            _ValueArgument("--country", "country", required=True, exact_length=2),
            _ValueArgument(
                "--scope",
                "scope",
                required=True,
                allowed=("conflicts", "treaties", "both"),
            ),
        ),
    ),
    "decision_maker_lookup": _Profile(
        "decision_makers.py",
        values=(
            _ValueArgument("--country", "country", exact_length=2),
            _ValueArgument("--branch", "branch"),
        ),
        flags=(_FlagArgument("--list-countries", "list_countries"),),
        require_any=("country", "list_countries"),
    ),
    "info_classification_check": _Profile(
        "info_classification.py",
        values=(
            _ValueArgument(
                "--scheme",
                "scheme",
                required=True,
                allowed=("government", "data"),
            ),
            _ValueArgument("--level", "level"),
        ),
        flags=(_FlagArgument("--list-schemes", "list_schemes"),),
    ),
    "lookup_governing_body": _Profile(
        "governing_bodies.py",
        values=(
            _ValueArgument("--country", "country", exact_length=2),
            _ValueArgument("--type", "type"),
        ),
        flags=(_FlagArgument("--list-countries", "list_countries"),),
        require_any=("country", "list_countries"),
    ),
    "navigate_borders": _Profile(
        "borders.py",
        values=(
            _ValueArgument(
                "--country",
                "country",
                exact_length=2,
                omit_when_flags=("list_countries",),
            ),
        ),
        flags=(_FlagArgument("--list-countries", "list_countries"),),
        require_any=("country", "list_countries"),
    ),
    "tax_currency_info": _Profile(
        "tax_currency.py",
        values=(
            _ValueArgument(
                "--country",
                "country",
                exact_length=2,
                omit_when_flags=("list_countries",),
            ),
        ),
        flags=(_FlagArgument("--list-countries", "list_countries"),),
        require_any=("country", "list_countries"),
    ),
}


def _role_variable(
    variables: Mapping[str, Any],
    profile: str,
    suffix: str,
) -> Any:
    return variables.get(f"{profile}_{suffix}")


def _enabled(value: Any) -> bool:
    try:
        return bool(boolean(value, strict=False))
    except (TypeError, ValueError):
        return False


def _bounded_text(value: Any, *, label: str, maximum: int) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise AnsibleFilterError(f"{label} must be a non-empty bounded string")
    return value


def _has_required_value(
    profile: str,
    suffix: str,
    variables: Mapping[str, Any],
    flag_variables: set[str],
) -> bool:
    value = _role_variable(variables, profile, suffix)
    if suffix in flag_variables:
        return _enabled(value)
    return isinstance(value, str) and bool(value)


def build_governance_lookup_plan(
    profile: str,
    variables: Mapping[str, Any],
) -> dict[str, Any]:
    """Return one validated, shell-free command plan for a public role."""
    spec = _PROFILES.get(profile)
    if spec is None:
        raise AnsibleFilterError(f"unsupported governance lookup profile: {profile}")
    if not _enabled(_role_variable(variables, profile, "enabled")):
        raise AnsibleFilterError(f"{profile} must be explicitly enabled")

    output_dir = _bounded_text(
        _role_variable(variables, profile, "output_dir"),
        label=f"{profile} output directory",
        maximum=_MAX_ARGUMENT_LENGTH,
    )
    interpreter = _bounded_text(
        variables.get("ansible_python_interpreter", "python3"),
        label="Ansible Python interpreter",
        maximum=_MAX_ARGUMENT_LENGTH,
    )
    flag_variables = {item.variable for item in spec.flags}
    if spec.require_any and not any(
        _has_required_value(profile, suffix, variables, flag_variables)
        for suffix in spec.require_any
    ):
        raise AnsibleFilterError(f"{profile} requires a lookup value or list mode")

    argv = [interpreter, f"{output_dir}/{spec.filename}"]
    for argument in spec.values:
        if any(
            _enabled(_role_variable(variables, profile, flag))
            for flag in argument.omit_when_flags
        ):
            continue
        raw = _role_variable(variables, profile, argument.variable)
        if raw is None or raw == "":
            if argument.required:
                raise AnsibleFilterError(
                    f"{profile} requires {argument.variable}"
                )
            continue
        value = _bounded_text(
            raw,
            label=f"{profile} {argument.variable}",
            maximum=_MAX_VALUE_LENGTH,
        )
        if argument.exact_length is not None and len(value) != argument.exact_length:
            raise AnsibleFilterError(
                f"{profile} {argument.variable} has an invalid length"
            )
        if argument.allowed and value not in argument.allowed:
            raise AnsibleFilterError(
                f"{profile} {argument.variable} is not allowlisted"
            )
        argv.extend((argument.flag, value))

    for flag_argument in spec.flags:
        if _enabled(_role_variable(variables, profile, flag_argument.variable)):
            argv.append(flag_argument.flag)
    if len(argv) > _MAX_ARGUMENTS or any(len(item) > _MAX_ARGUMENT_LENGTH for item in argv):
        raise AnsibleFilterError(f"{profile} command exceeds resource bounds")
    return {
        "argv": argv,
        "filename": spec.filename,
        "output_dir": output_dir,
        "output_filename": f"{profile}.json",
        "result_fact": f"{profile}_result",
    }


class FilterModule:
    """Expose governance lookup planning to Ansible."""

    def filters(self) -> dict[str, Callable[..., dict[str, Any]]]:
        return {"governance_lookup_plan": build_governance_lookup_plan}
