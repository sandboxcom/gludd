"""Task-general method-only protocols for injected execution adapters."""

from __future__ import annotations

from typing import ParamSpec, Protocol, TypeVar, runtime_checkable

_Parameters = ParamSpec("_Parameters")
_ResultT_co = TypeVar("_ResultT_co", covariant=True)
_AdapterT = TypeVar("_AdapterT")


@runtime_checkable
class ObservableRunnerProtocol(Protocol[_ResultT_co]):
    """Run one named operation with bounded, observable execution evidence."""

    def run_observable(
        self,
        operation: str,
        parameters: dict[str, str],
        *,
        timeout: float,
    ) -> _ResultT_co:
        """Run one bounded operation and return its domain-owned result."""
        ...


@runtime_checkable
class CallableAdapterProtocol(Protocol[_Parameters, _ResultT_co]):
    """Task-neutral callable seam refined by capability-owned static protocols."""

    def __call__(
        self,
        *args: _Parameters.args,
        **kwargs: _Parameters.kwargs,
    ) -> _ResultT_co:
        """Evaluate one capability-owned request."""
        ...


def require_runtime_adapter(
    candidate: _AdapterT,
    protocol: object,
    *,
    boundary: str,
) -> _AdapterT:
    """Return a structurally admitted adapter or fail before domain work starts."""
    if not boundary.strip():
        raise ValueError("runtime adapter boundary must be non-empty")
    if not isinstance(protocol, type):
        raise TypeError("runtime adapter protocol must be a class")
    if not isinstance(candidate, protocol):
        raise TypeError(f"{boundary} does not satisfy its runtime protocol")
    return candidate


__all__ = [
    "CallableAdapterProtocol",
    "ObservableRunnerProtocol",
    "require_runtime_adapter",
]
