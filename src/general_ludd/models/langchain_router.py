from __future__ import annotations

from collections.abc import Callable
from typing import cast

from langchain_core.runnables import Runnable, RunnableBranch
from langchain_core.runnables.base import RunnableLambda


class LangChainModelRouter:
    """Model router backed by LangChain's RunnableBranch for conditional routing.

    Resolves an input dict to a model runnable (or profile identifier) by
    evaluating route conditions in insertion order.  The first condition that
    returns ``True`` selects its associated runnable; if no conditions match
    the *default* runnable (when set) is returned, otherwise ``None``.

    The route conditions mirror the existing ModelRouter resolution logic:
    role name, quality_class, latency_class, etc.
    """

    def __init__(self) -> None:
        self._conditions: list[Callable[[dict[str, object]], bool]] = []
        self._runnables: list[object] = []
        self._default_runnable: object = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def add_route(
        self,
        condition_fn: Callable[[dict[str, object]], bool],
        model_runnable: object,
    ) -> None:
        """Register a route: when *condition_fn* evaluates to ``True`` on the
        resolver input, *model_runnable* is returned by :meth:`resolve`."""
        self._conditions.append(condition_fn)
        self._runnables.append(model_runnable)

    def set_default(self, model_runnable: object) -> None:
        """Set the catch-all runnable returned when no route condition matches."""
        self._default_runnable = model_runnable

    def resolve(self, input_dict: dict[str, object]) -> object:
        """Resolve *input_dict* to the first matching model runnable.

        Returns the runnable associated with the first satisfied condition,
        or the default runnable.  Returns ``None`` when there is no default
        and no route condition matches.
        """
        branches = self._build_branches()
        return branches.invoke(input_dict)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _build_branches(self) -> RunnableBranch[object, object]:
        """Assemble a :class:`RunnableBranch` from the current route table.

        Each row becomes a ``(condition_fn, RunnableLambda)`` branch whose
        RunnableLambda simply returns the registered model runnable.  The
        branch evaluates conditions in insertion order and invokes the
        RunnableLambda for the first match, producing the model runnable
        as the result.

        A dummy always-false condition is prepended when no routes are
        registered so the :class:`RunnableBranch` minimum-arguments
        invariant holds (at least one pair + the default).
        """
        def _passthrough_factory(value: object) -> object:
            def _fn(_input: object) -> object:
                return value

            return RunnableLambda(_fn)

        conditions: list[tuple[object, object]] = []
        for cond, run in zip(self._conditions, self._runnables, strict=True):
            conditions.append((cond, _passthrough_factory(run)))

        if not conditions:
            conditions.append((lambda _: False, RunnableLambda(lambda _: None)))

        default = (
            _passthrough_factory(self._default_runnable)
            if self._default_runnable is not None
            else RunnableLambda(lambda _: None)
        )

        return RunnableBranch(
            *cast(
                "list[tuple[Callable[[object], bool], Runnable[object, object]]]",
                conditions,
            ),
            cast("Runnable[object, object]", default),
        )
