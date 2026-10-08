"""BDD: reduced ordered binary decision diagram with apply, restrict, compose, satcount.

Represents Boolean functions over {x0, ..., xn-1}.  Variable ordering is fixed
at construction time.  Nodes are immutable interned via a unique table so
structural identity (`is`) implies semantic equivalence.

Pure-Python, stdlib only.
"""

from __future__ import annotations

from collections.abc import Callable

_TERMINAL_FALSE = 0
_TERMINAL_TRUE = 1


class BDDNode:
    """A BDD node: variable index, low child (var=0), high child (var=1).

    Terminal nodes are represented as the integers 0 (FALSE) and 1 (TRUE).
    """

    __slots__ = ("_hash", "high", "low", "var")

    def __init__(self, var: int, low: object, high: object) -> None:
        """Initialize a ``BDDNode`` instance."""
        self.var = var
        self.low = low
        self.high = high
        self._hash = hash((var, id_of(low), id_of(high)))

    def __hash__(self) -> int:
        """Return the stable hash."""
        return self._hash

    def __eq__(self, other: object) -> bool:
        """Compare this instance with another value."""
        if not isinstance(other, BDDNode):
            return NotImplemented
        return self.var == other.var and self.low is other.low and self.high is other.high

    def __repr__(self) -> str:
        """Return a developer-readable representation."""
        return f"BDDNode(var={self.var}, low={_repr(self.low)}, high={_repr(self.high)})"


def _repr(x: object) -> str:
    if x == 0:
        return "0"
    if x == 1:
        return "1"
    if isinstance(x, BDDNode):
        return f"<node var={x.var}>"
    return f"<node {x}>"


def id_of(x: object) -> int:
    """Execute ``id_of``."""
    if x == 0:
        return -1
    if x == 1:
        return -2
    if isinstance(x, BDDNode):
        return id(x)
    return hash(x)


class BDD:
    """Manager for reduced ordered BDDs over n variables.

    Variables are indexed 0..nvar-1.  The internal node table guarantees
    canonicity: two BDDs representing the same function share the same
    root node (or terminal).
    """

    def __init__(self, nvar: int) -> None:
        """Initialize a ``BDD`` instance."""
        self.nvar = nvar
        self.unique: dict[tuple[int, object, object], BDDNode] = {}
        self._apply_cache: dict[tuple[str, object, object], object] = {}
        self._restrict_cache: dict[tuple[object, int, int], object] = {}
        self._compose_cache: dict[tuple[object, int, object], object] = {}

    # ── builders ───────────────────────────────────────────────────────────

    def terminal(self, value: bool) -> object:
        """Execute ``terminal``."""
        return _TERMINAL_TRUE if value else _TERMINAL_FALSE

    def var(self, i: int) -> object:
        """Execute ``var``."""
        if not 0 <= i < self.nvar:
            raise IndexError(f"variable {i} out of range [0, {self.nvar})")
        return self._make(i, _TERMINAL_FALSE, _TERMINAL_TRUE)

    def _make(self, var: int, low: object, high: object) -> object:
        if low is high:
            return low
        key = (var, low, high)
        node = self.unique.get(key)
        if node is None:
            node = BDDNode(var, low, high)
            self.unique[key] = node
        return node

    def from_expression(self, f: Callable[[tuple[int, ...]], bool]) -> object:
        """Execute ``from_expression``."""
        expr_vars: list[int] = []
        return self._build_expr(f, expr_vars, 0)

    def _build_expr(self, f: Callable[[tuple[int, ...]], bool], expr_vars: list[int], depth: int) -> object:
        if depth >= self.nvar:
            return _TERMINAL_TRUE if f(tuple(expr_vars)) else _TERMINAL_FALSE
        expr_vars.append(0)
        low = self._build_expr(f, expr_vars, depth + 1)
        expr_vars[-1] = 1
        high = self._build_expr(f, expr_vars, depth + 1)
        expr_vars.pop()
        return self._make(depth, low, high)

    # ── apply ──────────────────────────────────────────────────────────────

    def apply(self, op: str, u: object, v: object) -> object:
        """Apply the value."""
        key = (op, u, v)
        cached = self._apply_cache.get(key)
        if cached is not None:
            return cached

        if op in ("and", "or", "xor"):
            result = self._apply_bool(op, u, v)
        elif op == "ite":
            result = self._apply_ite(u, v)
        else:
            raise ValueError(f"unknown op: {op}")
        self._apply_cache[key] = result
        return result

    def _apply_bool(self, op: str, u: object, v: object) -> object:
        if u in (0, 1) and v in (0, 1):
            if op == "and":
                return _TERMINAL_TRUE if u == 1 and v == 1 else _TERMINAL_FALSE
            if op == "or":
                return _TERMINAL_TRUE if u == 1 or v == 1 else _TERMINAL_FALSE
            if op == "xor":
                return _TERMINAL_TRUE if (u == 1) != (v == 1) else _TERMINAL_FALSE
            return _TERMINAL_FALSE

        if isinstance(u, BDDNode) and isinstance(v, BDDNode):
            if u.var == v.var:
                low = self._apply_bool(op, u.low, v.low)
                high = self._apply_bool(op, u.high, v.high)
                return self._make(u.var, low, high)
            elif u.var < v.var:
                low = self._apply_bool(op, u.low, v)
                high = self._apply_bool(op, u.high, v)
                return self._make(u.var, low, high)
            else:
                low = self._apply_bool(op, u, v.low)
                high = self._apply_bool(op, u, v.high)
                return self._make(v.var, low, high)

        if u in (0, 1):
            if op == "and":
                return _TERMINAL_FALSE if u == 0 else v
            if op == "or":
                return _TERMINAL_TRUE if u == 1 else v
            if op == "xor":
                return v if u == 0 else self.not_(v)
            return _TERMINAL_FALSE

        if op == "and":
            return _TERMINAL_FALSE if v == 0 else u
        if op == "or":
            return _TERMINAL_TRUE if v == 1 else u
        if op == "xor":
            return u if v == 0 else self.not_(u)
        return _TERMINAL_FALSE

    def _apply_ite(self, c: object, v: object) -> object:
        pass  # unused internally, overloaded elsewhere via ite()
        raise NotImplementedError

    def ite(self, i: object, t: object, e: object) -> object:
        """Execute ``ite``."""
        key = ("ite", i, t)
        key2 = ("ite", key, e)
        cached = self._apply_cache.get(key2)
        if cached is not None:
            return cached

        result = self._ite(i, t, e)
        self._apply_cache[key2] = result
        return result

    def _ite(self, i: object, t: object, e: object) -> object:
        if i == 1:
            return t
        if i == 0:
            return e
        if t is e:
            return t
        if t == 1 and e == 0:
            return i

        assert isinstance(i, BDDNode)

        tv = t.var if isinstance(t, BDDNode) else self.nvar
        ev = e.var if isinstance(e, BDDNode) else self.nvar
        top = min(i.var, tv, ev)

        low_i = i.low if isinstance(i, BDDNode) and i.var == top else i
        high_i = i.high if isinstance(i, BDDNode) and i.var == top else i
        low_t = t.low if isinstance(t, BDDNode) and t.var == top else t
        high_t = t.high if isinstance(t, BDDNode) and t.var == top else t
        low_e = e.low if isinstance(e, BDDNode) and e.var == top else e
        high_e = e.high if isinstance(e, BDDNode) and e.var == top else e

        low = self._ite(low_i, low_t, low_e)
        high = self._ite(high_i, high_t, high_e)
        return self._make(top, low, high)

    def and_(self, u: object, v: object) -> object:
        """Execute ``and_``."""
        return self.apply("and", u, v)

    def or_(self, u: object, v: object) -> object:
        """Execute ``or_``."""
        return self.apply("or", u, v)

    def xor(self, u: object, v: object) -> object:
        """Execute ``xor``."""
        return self.apply("xor", u, v)

    def not_(self, u: object) -> object:
        """Execute ``not_``."""
        if u == 1:
            return _TERMINAL_FALSE
        if u == 0:
            return _TERMINAL_TRUE
        assert isinstance(u, BDDNode)
        return self._not(u)

    def _not(self, u: BDDNode) -> object:
        key = ("not", u, None)
        cached = self._apply_cache.get(key)
        if cached is not None:
            return cached
        low = self._not_low(u.low)
        high = self._not_low(u.high)
        result = self._make(u.var, low, high)
        self._apply_cache[key] = result
        return result

    def _not_low(self, x: object) -> object:
        if x == 1:
            return _TERMINAL_FALSE
        if x == 0:
            return _TERMINAL_TRUE
        assert isinstance(x, BDDNode)
        return self._not(x)

    # ── restrict ───────────────────────────────────────────────────────────

    def restrict(self, u: object, var: int, value: int) -> object:
        """Restrict the value."""
        assert value in (0, 1)
        key = (u, var, value)
        cached = self._restrict_cache.get(key)
        if cached is not None:
            return cached
        result = self._restrict(u, var, value)
        self._restrict_cache[key] = result
        return result

    def _restrict(self, u: object, var: int, value: int) -> object:
        if u in (0, 1):
            return u
        assert isinstance(u, BDDNode)
        if u.var > var:
            return u
        if u.var == var:
            return u.high if value == 1 else u.low
        low = self._restrict(u.low, var, value)
        high = self._restrict(u.high, var, value)
        return self._make(u.var, low, high)

    # ── compose ────────────────────────────────────────────────────────────

    def compose(self, u: object, var: int, v: object) -> object:
        """Compose the value."""
        key = (u, var, v)
        cached = self._compose_cache.get(key)
        if cached is not None:
            return cached
        result = self._compose(u, var, v)
        self._compose_cache[key] = result
        return result

    def _compose(self, u: object, var: int, v: object) -> object:
        if u in (0, 1):
            return u
        assert isinstance(u, BDDNode)
        if u.var > var:
            return u
        if u.var == var:
            return self.ite(v, u.high, u.low)
        low = self._compose(u.low, var, v)
        high = self._compose(u.high, var, v)
        return self._make(u.var, low, high)

    # ── satcount ───────────────────────────────────────────────────────────

    def satcount(self, u: object) -> int:
        """Execute ``satcount``."""
        result: int = self._satcount(u, {})
        return result

    def _satcount(self, u: object, memo: dict[object, int]) -> int:
        if u == 0:
            return 0
        if u == 1:
            return 1 << self.nvar
        assert isinstance(u, BDDNode)
        cached = memo.get(u)
        if cached is not None:
            return cached
        left = self._satcount(u.low, memo)
        right = self._satcount(u.high, memo)
        shift = self.nvar - u.var - 1
        total = (left + right) >> 1 if shift == 0 else (left + right) // 2
        memo[u] = total
        return total

    def satcount_with_vars(self, u: object, active_vars: int) -> int:
        """Execute ``satcount_with_vars``."""
        return self._satcount_vars(u, active_vars, {})

    def _satcount_vars(self, u: object, active_vars: int, memo: dict[object, int]) -> int:
        if u == 0:
            return 0
        if u == 1:
            bits = active_vars.bit_count() if hasattr(active_vars, "bit_count") else bin(active_vars).count("1")
            return 1 << bits
        assert isinstance(u, BDDNode)
        if not (active_vars & (1 << u.var)):
            return self._satcount_vars(u.low, active_vars, memo) * 2
        cached = memo.get(u)
        if cached is not None:
            return cached
        low = self._satcount_vars(u.low, active_vars, memo)
        high = self._satcount_vars(u.high, active_vars, memo)
        total = low // 2 + high // 2
        memo[u] = total
        return total

    def any_sat(self, u: object) -> dict[int, int] | None:
        """Execute ``any_sat``."""
        return self._any_sat(u, {})

    def _any_sat(self, u: object, assign: dict[int, int]) -> dict[int, int] | None:
        if u == 0:
            return None
        if u == 1:
            return assign.copy()
        assert isinstance(u, BDDNode)
        assign[u.var] = 0
        result = self._any_sat(u.low, assign)
        if result is not None:
            return result
        assign[u.var] = 1
        return self._any_sat(u.high, assign)

    def is_tautology(self, u: object) -> bool:
        """Return whether is tautology."""
        return self.satcount(self.not_(u)) == 0

    def is_satisfiable(self, u: object) -> bool:
        """Return whether is satisfiable."""
        return self.any_sat(u) is not None

    def equals(self, u: object, v: object) -> bool:
        """Return whether equals."""
        return u is v

    # ── stats ──────────────────────────────────────────────────────────────

    def node_count(self, u: object) -> int:
        """Execute ``node_count``."""
        visited: set[object] = set()
        self._collect_nodes(u, visited)
        return len(visited) - (1 if 0 in visited else 0) - (1 if 1 in visited else 0)

    def _collect_nodes(self, u: object, visited: set[object]) -> None:
        if u in (0, 1) or u in visited:
            return
        visited.add(u)
        assert isinstance(u, BDDNode)
        self._collect_nodes(u.low, visited)
        self._collect_nodes(u.high, visited)

    def unique_count(self) -> int:
        """Execute ``unique_count``."""
        return len(self.unique)
